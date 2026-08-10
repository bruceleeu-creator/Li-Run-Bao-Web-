"""利润宝 · 扫描件 PDF 的 DeepSeek 解析模块。

扫描件审计报告（图片 + 劣质文本层）无法用 pdfplumber 表格提取，
本模块把 PDF 逐页取文本（文本层不足时回退 RapidOCR），整份送
DeepSeek 全权理解、纠错并输出统一 JSON，再由本模块映射进 FinancialData。

数据安全：本模块仅在显式配置 AI（base_url/api_key/model）时调用，
把整份 PDF 文本上传至第三方模型；未配置时不触网。
"""

from __future__ import annotations

import json
import importlib
import os
import re
import urllib.request
from typing import Optional

from core import parser as parser_mod
from core.models import FinancialData, normalize_account_name

full_pdf_reader = importlib.import_module(
    "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
)


# 文本层「过少/乱码」判定：非空白字符数低于该值时回退 OCR
_TEXT_TOO_SHORT = 40
# 基本中文判定：页文本层若无任何中文字符，多为扫描件/失败提取，回退 OCR
_GARBLE_RE = re.compile(r"[一-鿿]")
# 子集化字体乱码字形：数字被替换为带圈数字（⒛22→2022）、字母被替换为
# 罗马数字（Ⅱ→n）、标点替换为偏旁（灬丿扌）等，含这些字形即判定乱码回退 OCR
_GARBLE_GLYPH_RE = re.compile(
    r"[⒛⒈⒉⒊⒋⒌⒍⒎⒏⒐⒑ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ灬丿扌]"
)


def _looks_garbled(text: str) -> bool:
    """委托 core.parser 的统一乱码判定，预览/解析/报告三处口径一致。"""
    return parser_mod._looks_garbled(text)


def _page_text_layer(path: str, page_index: int) -> str:
    """取单页文本层（pdfplumber）。失败/为空返回空串。"""
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            page = pdf.pages[page_index]
            return (page.extract_text() or "").strip()
    except Exception:
        return ""


def _ocr_page(path: str, page_index: int) -> str:
    """渲染页面并用 RapidOCR 识别文字（扫描件回退）。"""
    import pypdfium2 as pdfium
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    pdf = pdfium.PdfDocument(path)
    try:
        pil = pdf[page_index].render(scale=1.5).to_pil()
    finally:
        pdf.close()
    result, _ = RapidOCR()(np.array(pil.convert("RGB")))
    if not result:
        return ""
    return "\n".join(item[1] for item in result if item and len(item) > 1 and item[1])


def extract_pdf_pages_text(path: str, max_pages: int = 200) -> list[str]:
    """逐页提取完整文本，供 DeepSeek 解析。

    ``max_pages`` 为兼容既有调用而保留，但完整报告流程不再截断页数。返回
    类型维持 ``list[str]``，每项仍带页码及 OCR 标记。
    """
    del max_pages
    return [
        full_pdf_reader._format_page(page)
        for page in full_pdf_reader.extract_all_pages(path)
    ]


def _deepseek_chat(
    user_text: str,
    system_prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int = 16_384,
    timeout: int = 120,
) -> str:
    """调用 DeepSeek /v1/chat/completions，返回 content。失败抛异常。"""
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": max_tokens,
        # 解析任务要求直接输出 JSON：禁用推理，避免 reasoning 占满 max_tokens
        # 导致正文 content 为 0（finish_reason=length）
        "thinking": {"type": "disabled"},
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choice = data["choices"][0]
    finish_reason = choice.get("finish_reason")
    if finish_reason != "stop":
        reason_messages = {
            "length": "DeepSeek 输出被截断（达到输出上限），请提高 max_tokens 后重试。",
            "content_filter": "DeepSeek 输出因内容过滤而未完成，请调整提示后重试。",
            "insufficient_system_resource": "DeepSeek 系统资源不足，未生成完整输出，请稍后重试。",
            "tool_calls": "DeepSeek 返回工具调用，无法作为财报 JSON 解析。",
        }
        message = reason_messages.get(
            finish_reason,
            f"DeepSeek 未正常完成输出（finish_reason={finish_reason or '缺失'}）。",
        )
        raise parser_mod.ParserError(message)
    content = choice["message"]["content"]
    return content.strip()


_SYSTEM_PROMPT = (
    "你是资深财务数据分析师。用户提供的是扫描版审计报告的逐页文本（含 OCR 文本层，"
    "可能存在乱码、缺小数点、字符错位、单位丢失等问题）。"
    "请根据财务常识和上下文，把整份报告解析为准确的财务数据。"
    "只输出一个 JSON 对象，不要输出其他任何文字。JSON 结构：\n"
    "{\n"
    '  "company_name": "公司全称",\n'
    '  "report_year": 2022,\n'
    '  "income_statement": {"科目名": {"本年": 数值或null, "上年": 数值或null}, ...},\n'
    '  "balance_sheet": {"科目名": {"期末": 数值或null, "期初": 数值或null}, ...}\n'
    "}\n"
    "要求：\n"
    "1. 金额单位一律换算为元（文本中出现的 万元/亿元 需换算）；\n"
    "2. 数字用 float，不要逗号、不要货币符号；\n"
    "3. 科目名使用规范名：营业收入、营业成本、税金及附加、销售费用、管理费用、"
    "研发费用、财务费用、利润总额、所得税费用、净利润、资产总额、负债总额、"
    "所有者权益、应收账款、存货、固定资产；\n"
    "4. 识别不到的科目填 null，绝不虚构；\n"
    "5. 若文本同时包含利润表和资产负债表，两个对象都填；\n"
    "6. 涉及违规税务筹划的表述禁止出现。"
)


def parse_pdf_with_deepseek(
    path: str,
    api_key: str,
    base_url: str,
    model: str,
    company_name: str = "",
    industry: str = "制造业",
    max_pages: int = 200,
) -> FinancialData:
    """把扫描件 PDF 整份送 DeepSeek 解析为 FinancialData。"""
    pages_text = extract_pdf_pages_text(path, max_pages=max_pages)
    user_text = "\n\n".join(pages_text)
    raw = _deepseek_chat(user_text, _SYSTEM_PROMPT, api_key, base_url, model)
    return _map_deepseek_result(
        raw, company_name=company_name, industry=industry, path=path
    )


def _extract_json_object(content: str) -> dict:
    """从模型输出截取 JSON 对象。"""
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise parser_mod.ParserError(f"DeepSeek 未返回 JSON：{content[:120]}")
    try:
        obj = json.loads(content[start:end + 1])
    except Exception as e:
        raise parser_mod.ParserError(f"DeepSeek JSON 解析失败：{e}") from e
    if not isinstance(obj, dict):
        raise parser_mod.ParserError(f"DeepSeek 未返回对象：{content[:120]}")
    return obj


def _num(value, default: Optional[float] = None) -> Optional[float]:
    """把模型值转 float；空/异常返回 default。"""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[,\s元万]", "", str(value))
    try:
        return float(s)
    except ValueError:
        return default


def _fill_table(
    obj: dict,
    kind: str,
    year: int,
    year_keys: tuple[str, ...],
) -> dict[str, dict[int, Optional[float]]]:
    """把模型输出的 {科目: {年: 值}} 映射为 {科目: {年: 值}}。

    只保留「本年/期末」作为独立年份（report_year）的值；「上年/期初」是
    对比参考，不生成 year-1 的独立年份（避免 2022 报告混入 2021）。
    """
    current_keys = {"本年", "本期", "期末", "年末"}
    table: dict[str, dict[int, Optional[float]]] = {}
    src = obj.get(kind) or {}
    for raw_name, col in src.items():
        if not isinstance(col, dict):
            continue
        acc = normalize_account_name(str(raw_name))
        if not acc:
            continue
        table.setdefault(acc, {})
        for yk in year_keys:
            if yk not in current_keys:
                continue  # 上年/期初等对比列不生成独立年份
            val = _num(col.get(yk))
            if val is None:
                continue
            table[acc][year] = val
    return table


def _map_deepseek_result(
    content: str,
    company_name: str = "",
    industry: str = "制造业",
    path: str = "",
) -> FinancialData:
    """把 DeepSeek 返回的 JSON 映射为 FinancialData。"""
    obj = _extract_json_object(content)
    company = obj.get("company_name") or company_name or os.path.basename(path)
    year_raw = obj.get("report_year")
    year = None
    if isinstance(year_raw, (int, float)):
        year = int(year_raw)
    # 文件名年份优先（2024年审计报告.pdf → 2024），避免模型误判年份
    year = _infer_year_from_name(path) or year
    years = [year] if year else []

    income = _fill_table(obj, "income_statement", year, ("本年", "上年"))
    balance = _fill_table(obj, "balance_sheet", year, ("期末", "期初"))
    warnings: list[str] = []
    if not income:
        warnings.append("未解析到利润表科目")
    if not balance:
        warnings.append("未解析到资产负债表科目")
    data = FinancialData(
        company_name=str(company),
        industry=industry or "制造业",
        years=years,
        income_statement=income,
        balance_sheet=balance,
        account_balances={},
        parsed_meta={
            "source": "deepseek",
            "warnings": warnings,
            "report_year": year,
        },
    )
    return data


def _infer_year_from_name(path: str) -> Optional[int]:
    """从文件名推断报告年份（如「2022年审计报告.pdf」→ 2022）。"""
    m = re.search(r"(20\d{2})", os.path.basename(path) or "")
    return int(m.group(1)) if m else None
