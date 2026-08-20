"""利润宝扫描财报核心字段识别。

本模块只负责 PDF 页面定位、坐标化 OCR 行重建和核心字段候选值生成。
置信度核对与人工确认由上层会话处理，AI 不直接消费本模块的 OCR 原文。
"""

from __future__ import annotations

import os
import re
from typing import Optional

from core import parser


CORE_FIELDS = (
    "营业收入",
    "营业成本",
    "税金及附加",
    "销售费用",
    "管理费用",
    "研发费用",
    "财务费用",
    "利润总额",
    "所得税费用",
    "净利润",
    "资产总额",
    "负债总额",
)

FIELD_ALIASES = {
    "营业收入": ("营业总收入", "其中营业收入", "营业收入"),
    "营业成本": ("营业总成本", "其中营业成本", "营业成本"),
    "税金及附加": ("税金及附加", "营业税金及附加"),
    "销售费用": ("销售费用",),
    "管理费用": ("管理费用",),
    "研发费用": ("研发费用",),
    "财务费用": ("财务费用",),
    "利润总额": ("利润总额",),
    "所得税费用": ("所得税费用",),
    "净利润": ("净利润",),
    "资产总额": ("资产总计", "资产总额"),
    "负债总额": ("负债合计", "负债总计", "负债总额"),
}


def _box_left(box) -> float:
    return min(float(point[0]) for point in box)


def _box_center_y(box) -> float:
    return sum(float(point[1]) for point in box) / len(box)


def rebuild_ocr_rows(items: list, y_tolerance: float | None = None) -> list[dict]:
    """按文字框纵坐标聚类为行，再按横坐标排序为列。

    y_tolerance 缺省自适应：取本页文字框高度中位数的 0.8 倍（钳制在
    8~28px），替代固定 14px——不同扫描件的行高与渲染倍率差异大，固定值
    容易把相邻两行并成一行导致金额串列。显式传值时仍按传入值执行。
    """
    normalized = []
    for item in items or []:
        if not item or len(item) < 2 or not str(item[1]).strip():
            continue
        box = item[0]
        confidence = float(item[2]) if len(item) > 2 else 0.0
        normalized.append({
            "box": box,
            "text": str(item[1]).strip(),
            "confidence": confidence,
            "x": _box_left(box),
            "y": _box_center_y(box),
        })
    normalized.sort(key=lambda entry: (entry["y"], entry["x"]))

    if y_tolerance is None and normalized:
        heights = sorted(
            max(float(point[1]) for point in entry["box"])
            - min(float(point[1]) for point in entry["box"])
            for entry in normalized
        )
        median_height = heights[len(heights) // 2]
        y_tolerance = min(28.0, max(8.0, median_height * 0.8))
    elif y_tolerance is None:
        y_tolerance = 14.0

    grouped: list[list[dict]] = []
    for entry in normalized:
        if not grouped:
            grouped.append([entry])
            continue
        group_y = sum(part["y"] for part in grouped[-1]) / len(grouped[-1])
        if abs(entry["y"] - group_y) <= y_tolerance:
            grouped[-1].append(entry)
        else:
            grouped.append([entry])

    rows = []
    for group in grouped:
        group.sort(key=lambda entry: entry["x"])
        rows.append({
            "texts": [entry["text"] for entry in group],
            "xs": [entry["x"] for entry in group],
            "confidences": [entry["confidence"] for entry in group],
            "boxes": [entry["box"] for entry in group],
        })
    return rows


def locate_statement_pages(page_texts: list[str]) -> dict[str, list[int]]:
    """从逐页文本中定位真正的利润表和资产负债表页面。"""
    located = {"income": [], "balance": []}
    for page_number, raw in enumerate(page_texts, start=1):
        text = re.sub(r"\s+", "", raw or "")
        if (
            "利润表" in text
            and sum(keyword in text for keyword in ("营业收入", "营业成本", "净利润")) >= 2
        ):
            located["income"].append(page_number)
        if (
            "资产负债表" in text
            and sum(keyword in text for keyword in ("资产总计", "资产总额", "负债合计", "负债总计")) >= 2
        ):
            located["balance"].append(page_number)
    return located


def _normalized_label(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")


def _match_field(texts: list[str], statement_kind: str) -> Optional[str]:
    combined = _normalized_label("".join(texts))
    allowed = CORE_FIELDS[:10] if statement_kind == "income" else CORE_FIELDS[10:]
    for field in allowed:
        for alias in FIELD_ALIASES[field]:
            if _normalized_label(alias) in combined:
                return field
    return None


def _header_columns(rows: list[dict], report_year: int) -> list[tuple[int, float]]:
    current = ("本年", "本期", "年末", "期末")
    previous = ("上年", "上期", "年初", "期初")
    for row in rows[:12]:
        columns: list[tuple[int, float]] = []
        for text, x in zip(row.get("texts", []), row.get("xs", [])):
            compact = _normalized_label(text)
            explicit = re.search(r"(?:19|20)\d{2}", compact)
            if explicit:
                columns.append((int(explicit.group(0)), float(x)))
            elif any(marker in compact for marker in current):
                columns.append((report_year, float(x)))
            elif any(marker in compact for marker in previous):
                columns.append((report_year - 1, float(x)))
        if len(columns) >= 2:
            return columns
    return []


def clean_ocr_amount(raw: str) -> Optional[float]:
    """Repair only punctuation-only OCR corruption in an otherwise numeric cell."""
    text = re.sub(r"\s+", "", str(raw or ""))
    if not re.fullmatch(r"[+-]?\d[\d.,]*", text):
        return None
    parsed = parser.clean_number(text)
    if parsed is not None:
        return parsed
    sign = -1.0 if text.startswith("-") else 1.0
    unsigned = text.lstrip("+-")
    separators = [index for index, char in enumerate(unsigned) if char in ".,"]
    if not separators:
        return None
    last = separators[-1]
    fractional_digits = len(unsigned) - last - 1
    decimal = unsigned[last] if fractional_digits in {1, 2} else ""
    integral = re.sub(r"[.,]", "", unsigned[:last] if decimal else unsigned)
    fraction = unsigned[last + 1 :] if decimal else ""
    if not integral.isdigit() or (decimal and not fraction.isdigit()):
        return None
    normalized = integral + ("." + fraction if decimal else "")
    return sign * float(normalized)


def _amount_cells(row: dict) -> list[dict]:
    amounts = []
    for text, x, confidence in zip(
        row.get("texts", []), row.get("xs", []), row.get("confidences", [])
    ):
        value = clean_ocr_amount(text)
        if value is not None and re.search(r"\d", str(text)):
            amounts.append({
                "raw": str(text),
                "value": value,
                "x": float(x),
                "confidence": float(confidence),
            })
    return amounts


def extract_statement_candidates(
    rows: list[dict],
    report_year: int,
    statement_kind: str,
    source_file: str,
    source_page: int,
) -> list[dict]:
    """从坐标化报表行提取核心科目，并按表头横坐标映射年度。"""
    columns = _header_columns(rows, report_year)
    if not columns:
        return []

    candidates = []
    for row in rows:
        field = _match_field(row.get("texts", []), statement_kind)
        if field is None:
            continue
        amounts = _amount_cells(row)
        if not amounts:
            continue
        used: set[int] = set()
        for year, header_x in columns:
            choices = [
                (idx, amount) for idx, amount in enumerate(amounts) if idx not in used
            ]
            if not choices:
                break
            idx, amount = min(choices, key=lambda pair: abs(pair[1]["x"] - header_x))
            used.add(idx)
            candidates.append({
                "field": field,
                "year": year,
                "value": amount["value"],
                "source_file": os.path.basename(source_file),
                "source_page": source_page,
                "raw_label": " ".join(row.get("texts", [])),
                "raw_value": amount["raw"],
                "confidence": amount["confidence"],
                "status": "trusted" if amount["confidence"] >= 0.9 else "review",
                "issues": [],
            })
    return candidates


def render_pdf_page(path: str, page_index: int, scale: float = 3.0):
    """渲染单个 PDF 页面供内部 OCR 使用，不返回给前端。

    3.0 倍（约 216 DPI）保障财报小字号金额的像素信息；低质量页可在调用方
    提高到 4.0 重扫。
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        return pdf[page_index].render(scale=scale).to_pil()
    finally:
        pdf.close()


def run_ocr(image) -> list:
    """执行 OCR 并返回 [文字框, 文本, 置信度] 三元组列表。

    复用 core.parser 的共享引擎（避免每页重复构造），结果经
    normalize_ocr_result 归一，屏蔽 rapidocr 版本差异。
    """
    import numpy as np

    engine = parser._get_ocr_engine()
    if engine is None:
        return []
    return parser.normalize_ocr_result(engine(np.array(image.convert("RGB"))))
