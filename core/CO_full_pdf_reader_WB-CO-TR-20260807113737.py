"""利润宝 · 完整 PDF 逐页读取。

该模块独立于 Web 层：对每一页保留完整文本和提取状态，供 AI 报告流程
按页分块。它不复用导入预览的页数或字符上限。
"""

from __future__ import annotations

import hashlib
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal


CHUNK_MAX_CHARS = 24_000
EXTRACT_MAX_TOKENS = 16_384
FINAL_MAX_TOKENS = 16_384
MAX_TRUNCATION_RETRIES = 3

_TEXT_TOO_SHORT = 40
_CJK_RE = re.compile(r"[一-鿿]")
_GARBLE_GLYPH_RE = re.compile(
    r"[⒛⒈⒉⒊⒋⒌⒍⒎⒏⒐⒑ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ灬丿扌]"
)
_OCR_UNINITIALIZED = object()
_OCR_UNAVAILABLE = object()
_ocr_engine: Any = _OCR_UNINITIALIZED
_ocr_engine_lock = threading.Lock()
# 正式解析渲染倍率：1.5（约 108 DPI）下财报小字号数字像素信息不足；
# 3.0（约 216 DPI）兼顾精度与速度，低置信度页再升档重扫。
_OCR_RENDER_SCALE = 3.0
_OCR_RESCAN_SCALE = 4.0
_OCR_RESCAN_CONFIDENCE = 0.85


@dataclass(frozen=True)
class PDFPageRecord:
    """一页 PDF 的完整读取结果，页码从 1 开始。"""

    page_no: int
    total_pages: int
    method: Literal["text", "ocr", "none"]
    text: str
    status: Literal["ok", "blank", "failed"]


@dataclass(frozen=True)
class PDFTextChunk:
    """由完整页组成、可直接交给模型的一段文本。"""

    start_page: int
    end_page: int
    total_pages: int
    text: str


def file_sha256(path: str | Path) -> str:
    """计算文件内容的 SHA-256，不把整份大 PDF 读入内存。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _looks_garbled(text: str) -> bool:
    """委托 core.parser 的统一乱码判定，预览/解析/报告三处口径一致。"""
    from .parser import _looks_garbled as _detect

    return _detect(text)


def _text_needs_ocr(text: str) -> bool:
    return len(text) < _TEXT_TOO_SHORT or not _CJK_RE.search(text) or _looks_garbled(text)


@contextmanager
def _open_pdf_documents(path: str | Path) -> Iterator[tuple[Any, Any]]:
    """一次读取中共享文本层和渲染文档，并在退出时统一关闭。"""
    import pdfplumber
    import pypdfium2 as pdfium

    with pdfplumber.open(str(path)) as text_document:
        render_document = pdfium.PdfDocument(str(path))
        try:
            yield text_document, render_document
        finally:
            render_document.close()


def _extract_text_from_document(document: Any, page_index: int) -> str:
    return (document.pages[page_index].extract_text() or "").strip()


def _get_ocr_engine() -> Any:
    """委托 core.parser 的共享 OCR 引擎（全项目单例，含降级与模型覆盖逻辑）。"""
    global _ocr_engine
    if _ocr_engine is _OCR_UNINITIALIZED:
        with _ocr_engine_lock:
            if _ocr_engine is _OCR_UNINITIALIZED:
                from core import parser as parser_mod

                _ocr_engine = parser_mod._get_ocr_engine()
    return _ocr_engine


def _extract_ocr_from_document(
    document: Any, page_index: int, engine: Any, scale: float = _OCR_RENDER_SCALE
) -> tuple[str, float]:
    """渲染已打开文档的一页并 OCR，返回 (文本, 平均置信度)。

    平均置信度供调用方判断是否需要更高分辨率重扫；识别为空时置信度为 0。
    """
    import numpy as np

    from core import parser as parser_mod

    image = document[page_index].render(scale=scale).to_pil()
    result = engine(np.array(image.convert("RGB")))
    items = parser_mod.normalize_ocr_result(result)
    text = "\n".join(text for _, text, _ in items if text).strip()
    confidence = (
        sum(conf for _, _, conf in items) / len(items) if items else 0.0
    )
    return text, confidence


def extract_all_pages(
    path: str | Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[PDFPageRecord]:
    """读取 PDF 的每一页，文本层质量不足时逐页回退 OCR。

    无论该页提取是否成功，返回值均包含它的记录；进度回调也会在每页完成后
    调用一次。这里刻意不存在预览用途的页数、字符数截断。
    """
    records: list[PDFPageRecord] = []
    with _open_pdf_documents(path) as (text_document, render_document):
        total_pages = len(render_document)
        ocr_engine: Any = _OCR_UNINITIALIZED
        for page_index in range(total_pages):
            try:
                text = _extract_text_from_document(text_document, page_index)
                if text and not _text_needs_ocr(text):
                    record = PDFPageRecord(page_index + 1, total_pages, "text", text, "ok")
                else:
                    ocr_failed = False
                    if ocr_engine is _OCR_UNINITIALIZED:
                        try:
                            ocr_engine = _get_ocr_engine()
                        except Exception:
                            ocr_engine = _OCR_UNAVAILABLE
                    if ocr_engine is _OCR_UNAVAILABLE:
                        ocr_text = ""
                        ocr_confidence = 0.0
                        ocr_failed = True
                    else:
                        try:
                            ocr_text, ocr_confidence = _extract_ocr_from_document(
                                render_document, page_index, ocr_engine
                            )
                        except Exception:
                            ocr_text, ocr_confidence = "", 0.0
                            ocr_failed = True
                    if ocr_text and ocr_confidence < _OCR_RESCAN_CONFIDENCE:
                        # 财报数字对识别精度敏感：低置信度页用更高分辨率重扫
                        # 一次，仅在重扫结果确实更好时采用，避免无谓抖动。
                        try:
                            retry_text, retry_confidence = _extract_ocr_from_document(
                                render_document,
                                page_index,
                                ocr_engine,
                                scale=_OCR_RESCAN_SCALE,
                            )
                            if retry_text and retry_confidence > ocr_confidence:
                                ocr_text, ocr_confidence = retry_text, retry_confidence
                        except Exception:
                            pass
                    if ocr_text:
                        record = PDFPageRecord(page_index + 1, total_pages, "ocr", ocr_text, "ok")
                    elif text:
                        record = PDFPageRecord(page_index + 1, total_pages, "text", text, "failed")
                    elif ocr_failed:
                        record = PDFPageRecord(page_index + 1, total_pages, "none", "", "failed")
                    else:
                        record = PDFPageRecord(page_index + 1, total_pages, "none", "", "blank")
            except Exception:
                record = PDFPageRecord(page_index + 1, total_pages, "none", "", "failed")
            records.append(record)
            if on_progress is not None:
                on_progress(page_index + 1, total_pages)
    return records


def _format_page(page: PDFPageRecord) -> str:
    prefix = "[OCR] " if page.method == "ocr" and page.text else ""
    return f"[第{page.page_no}页]\n{prefix}{page.text}"


def chunk_pages(
    pages: list[PDFPageRecord], max_chars: int = CHUNK_MAX_CHARS
) -> list[PDFTextChunk]:
    """按页边界分组，不截断任何单页文本。"""
    if max_chars <= 0:
        raise ValueError("max_chars 必须为正数")
    if not pages:
        return []

    chunks: list[PDFTextChunk] = []
    current_pages: list[PDFPageRecord] = []
    current_texts: list[str] = []
    current_size = 0

    def flush() -> None:
        if not current_pages:
            return
        chunks.append(
            PDFTextChunk(
                start_page=current_pages[0].page_no,
                end_page=current_pages[-1].page_no,
                total_pages=current_pages[0].total_pages,
                text="\n\n".join(current_texts),
            )
        )

    for page in pages:
        formatted = _format_page(page)
        addition = len(formatted) + (2 if current_texts else 0)
        if current_pages and current_size + addition > max_chars:
            flush()
            current_pages = []
            current_texts = []
            current_size = 0
        current_pages.append(page)
        current_texts.append(formatted)
        current_size += len(formatted) + (2 if len(current_texts) > 1 else 0)
    flush()
    return chunks
