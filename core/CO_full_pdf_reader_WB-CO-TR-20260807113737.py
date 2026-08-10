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
    """按需构造并在线程间共享 RapidOCR 引擎。"""
    global _ocr_engine
    if _ocr_engine is _OCR_UNINITIALIZED:
        with _ocr_engine_lock:
            if _ocr_engine is _OCR_UNINITIALIZED:
                from rapidocr_onnxruntime import RapidOCR

                _ocr_engine = RapidOCR()
    return _ocr_engine


def _extract_ocr_from_document(document: Any, page_index: int, engine: Any) -> str:
    """渲染已打开文档的一页，并复用调用方传入的 OCR 引擎。"""
    import numpy as np

    image = document[page_index].render(scale=1.5).to_pil()
    result, _ = engine(np.array(image.convert("RGB")))
    if not result:
        return ""
    return "\n".join(item[1] for item in result if item and len(item) > 1 and item[1]).strip()


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
                        ocr_failed = True
                    else:
                        try:
                            ocr_text = _extract_ocr_from_document(
                                render_document, page_index, ocr_engine
                            )
                        except Exception:
                            ocr_text = ""
                            ocr_failed = True
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
