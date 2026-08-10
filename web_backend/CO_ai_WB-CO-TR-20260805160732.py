"""利润宝 · Web AI 配置与调用（可选增强）。

配置持久化：base_url / model / api_key 写入 .ai_config.json（用户选择
「配一次全局可用、重启免重输」）；`get_config()` 响应不返回 api_key，
避免前端回显敏感字段。未配置时绝不触网；调用失败由前端展示提示，
不影响主流程。
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import importlib
from pathlib import Path
from typing import Any

from core.ai_engine import AIChatResult, AIEngine, AIEngineError

_full_pdf_reader = importlib.import_module(
    "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
)
PDFTextChunk = _full_pdf_reader.PDFTextChunk
EXTRACT_MAX_TOKENS = _full_pdf_reader.EXTRACT_MAX_TOKENS
FINAL_MAX_TOKENS = _full_pdf_reader.FINAL_MAX_TOKENS

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_AI_CONFIG_FILE = _ROOT / ".ai_config.json"
# 官方默认值：Base URL / 模型留空时自动回退，用户只需填 API Key
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

_lock = threading.Lock()
# 内存态：base_url / model / api_key（均持久化到 .ai_config.json）
_state: dict = {"base_url": "", "model": "", "api_key": ""}


def _get_ai_config_path() -> Path:
    """返回 AI 配置路径；测试进程可通过环境变量隔离本地密钥配置。"""
    configured = os.environ.get("LIRUNBAO_AI_CONFIG_PATH", "").strip()
    return Path(configured).resolve() if configured else _DEFAULT_AI_CONFIG_FILE


def _load_persisted() -> dict:
    """读取持久化配置（base_url/model/api_key），供启动恢复。"""
    path = _get_ai_config_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        return {
            "base_url": str(cfg.get("base_url", "")),
            "model": str(cfg.get("model", "")),
            "api_key": str(cfg.get("api_key", "")),
        }
    except Exception:
        return {}


def _ensure_loaded() -> None:
    """首次访问前从磁盘恢复配置（进程重启后 base_url/model/api_key 仍在）。"""
    with _lock:
        if _state.get("_loaded"):
            return
        _state.update(_load_persisted())
        _state["_loaded"] = True


def get_config() -> dict:
    """返回前端可读配置（不含 api_key）。"""
    _ensure_loaded()
    with _lock:
        return {
            "base_url": _state["base_url"],
            "model": _state["model"],
            "configured": bool(_state["base_url"] and _state["api_key"] and _state["model"]),
        }


def get_credentials() -> dict:
    """返回 DeepSeek 调用所需的完整凭据（含 api_key，仅后端内部使用）。"""
    _ensure_loaded()
    with _lock:
        return {
            "base_url": _state["base_url"],
            "model": _state["model"],
            "api_key": _state["api_key"],
        }


def save_config(base_url: str, model: str, api_key: str) -> dict:
    """保存配置：base_url/model/api_key 均持久化到 .ai_config.json。

    Base URL / 模型留空时自动回退官方默认值（用户只需填 API Key）。
    """
    _ensure_loaded()
    with _lock:
        _state["base_url"] = (base_url or "").strip() or DEFAULT_BASE_URL
        _state["model"] = (model or "").strip() or DEFAULT_MODEL
        if api_key:
            _state["api_key"] = (api_key or "").strip()
        persisted = {
            "base_url": _state["base_url"],
            "model": _state["model"],
            "api_key": _state["api_key"],
        }
        try:
            path = _get_ai_config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(persisted, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, str(path))
        except OSError:
            pass  # 持久化失败不阻塞内存配置
    cfg = get_config()
    if not cfg["configured"]:
        # 明确告知缺失项，避免前端把「保存成功但未配置」误当成正常结果
        missing = [
            label
            for label, value in (
                ("Base URL", _state["base_url"]),
                ("模型", _state["model"]),
                ("API Key", _state["api_key"]),
            )
            if not value
        ]
        cfg["error"] = "配置未完成，请填写：" + "、".join(missing)
    return cfg


def clear_config() -> dict:
    """清空配置并恢复离线。"""
    _ensure_loaded()
    with _lock:
        _state["base_url"] = ""
        _state["model"] = ""
        _state["api_key"] = ""
        try:
            path = _get_ai_config_path()
            if path.exists():
                path.unlink()
        except OSError:
            pass
    return get_config()


def _engine(timeout: float = 8.0) -> AIEngine | None:
    """构造 AIEngine（可用时）。timeout 可覆盖默认超时（大文本生成需更长）。"""
    _ensure_loaded()
    with _lock:
        base = _state["base_url"]
        key = _state["api_key"]
        model = _state["model"]
    if not (base and key and model):
        return None
    return AIEngine(base_url=base, api_key=key, model=model, timeout=timeout)


def ai_available() -> bool:
    return _engine() is not None


def summarize_for_markdown(content: str) -> tuple[str, str]:
    """把预览内容整理为 markdown 表格/文字（多阶段：按文件拆分 → 逐份提炼 → 合并）。

    避免一次性把多年 PDF 全部文本塞给模型导致输出截断：
    - 阶段一：按「文件：」拆分，每份独立提炼关键数据（max_tokens 8,192）
    - 阶段二：合并各份提炼结果，生成跨文件整理报告（max_tokens 16,384）

    返回 (markdown, error)。未配置/失败时 error 非空，markdown 为空。
    """
    engine = _engine(timeout=180.0)  # 多阶段耗时更长
    if engine is None:
        return "", "大模型未配置。请先在设置中配置 AI（仅需一次）。"
    chunks = _extract_ocr_chunks(content)
    if len(chunks) <= 1:
        # 单份文件/单段文本：直接整理（输入不大，单次调用足够）
        system_prompt = (
            "你是企业财报整理助手。根据用户提供的财报原始内容（可能是表格提取文本或"
            "页面文字），整理为结构清晰、合法的中文 markdown 报告："
            "1. 有表格数据时输出 markdown 表格（含表头），数字保留原值；"
            "2. 有文字段落时输出要点化的 markdown 列表；"
            "3. 只整理呈现，不推断、不虚构数据，不改变事实；"
            "4. 禁止任何违规税务筹划表述。"
        )
        try:
            return engine.chat(content, system_prompt=system_prompt, max_tokens=16_384), ""
        except AIEngineError as e:
            return "", str(e)
    try:
        extracts = []
        for chunk in chunks:
            extracts.append(_stage_extract(engine, chunk))
        final = _stage_merge(engine, extracts)
    except AIEngineError as e:
        return "", str(e)
    return final, ""


# 单次喂给模型的文本上限（字符）：超过则按页/按段拆分，避免输入超限
_CHUNK_TEXT_LIMIT = 8000


def _extract_ocr_chunks(ocr_text: str) -> list[dict]:
    """把拼接的 OCR 文本按文件标题拆成多份：返回 [{name, text}]。

    兼容「【文件】」与「文件：」两种前缀（前端 onSummarize 用「文件：」）。
    """
    chunks: list[dict] = []
    current_name = ""
    current: list[str] = []
    for line in (ocr_text or "").splitlines():
        if line.startswith("【文件") or line.startswith("文件："):
            if current:
                chunks.append({"name": current_name, "text": "\n".join(current)})
                current = []
            current_name = line.replace("【文件", "").replace("】", "").replace("文件：", "").strip()
        current.append(line)
    if current:
        chunks.append({"name": current_name, "text": "\n".join(current)})
    return chunks


def _split_long_text(text: str, limit: int = _CHUNK_TEXT_LIMIT) -> list[str]:
    """超长文本按「[第 N 页]」页边界切段；无页边界时按字符硬切。"""
    if len(text) <= limit:
        return [text]
    page_blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("[第 ") and current:
            page_blocks.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        page_blocks.append("\n".join(current))
    # 按页块重新聚合成 ≤limit 的段
    segments: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for block in page_blocks:
        if buf_len + len(block) > limit and buf:
            segments.append("\n".join(buf))
            buf = []
            buf_len = 0
        buf.append(block)
        buf_len += len(block)
    if buf:
        segments.append("\n".join(buf))
    # 极端情况：单页超过 limit，硬切
    final: list[str] = []
    for seg in segments:
        while len(seg) > limit:
            final.append(seg[:limit])
            seg = seg[limit:]
        final.append(seg)
    return [s for s in final if s.strip()]


def _stage_extract(engine, chunk: str, name: str = "") -> str:
    """第一阶段：让 DeepSeek 从单段文本提炼关键财务数据（精简）。"""
    header = f"文件：{name}\n" if name else ""
    prompt = (
        "以下是企业审计报告的片段文本（可能含乱码）。请提炼其中的关键财务数据，"
        "输出精简的 markdown：\n"
        "1. 报告年份/文件名（如有）；\n"
        "2. 出现的关键指标：营业收入、营业成本、税金及附加、销售费用、管理费用、研发费用、"
        "财务费用、利润总额、所得税费用、净利润、毛利率、净利率（如有）；\n"
        "3. 2-4 条要点（异常、重大事项、趋势线索）。\n"
        "只输出片段中出现的内容，数字保留原值（注意纠正缺小数点/错位），不要虚构，"
        "没有的指标不要编造。\n\n"
        + header + chunk
    )
    return engine.chat(prompt, system_prompt="", max_tokens=4_096)


def _merge_file_extracts(engine, name: str, extracts: list[str]) -> str:
    """把同一份文件的多段提炼结果合并成该文件的年度总结。"""
    joined = "\n\n---\n\n".join(f"[片段{i+1}]\n{e}" for i, e in enumerate(extracts))
    prompt = (
        f"以下是文件「{name}」（某一年审计报告）各片段的关键数据提炼。"
        "请合并成该年度的精简总结 markdown：\n"
        "1. 汇总一份关键指标表（营业收入、营业成本、净利润、毛利率、净利率等，如有）；\n"
        "2. 合并重复信息，去除冗余；\n"
        "3. 3-5 条年度经营要点；\n"
        "4. 数字保留原值，不要虚构，缺失标注「未识别」。\n\n"
        + joined
    )
    return engine.chat(prompt, system_prompt="", max_tokens=8_192)


def _stage_merge(engine, extracts: list[dict]) -> str:
    """最终阶段：把各年度总结合并成跨年对比综合分析。

    extracts: [{name, summary}]，每项是某一年度的总结。
    """
    joined = "\n\n---\n\n".join(
        f"[{e['name'] or f'第{i+1}年'}年度总结]\n{e['summary']}"
        for i, e in enumerate(extracts)
    )
    prompt = (
        "以下是多份不同年份审计报告的年度总结。请生成最终的跨年对比综合分析报告，"
        "结构如下：\n"
        "1. **企业概况**：企业名称、报告年度范围；\n"
        "2. **逐年关键指标表**：用 markdown 表格逐年对比（营业收入、营业成本、净利润、"
        "毛利率、净利率、增值税税负率），表头含年份列；\n"
        "3. **逐年要点**：每个年度单独一个小节（该年关键事件、异常、趋势线索）；\n"
        "4. **跨年趋势与异常**：用要点列出逐年变化趋势与值得关注的异常；\n"
        "5. 若某年数据缺失，标注「该年数据缺失」。\n"
        "禁止违规税务筹划表述，只基于给定总结。\n\n"
        + joined
    )
    return engine.chat(prompt, system_prompt="", max_tokens=16_384)


def summarize_multi_stage(
    content: str,
    timeout: float = 180.0,
) -> tuple[str, str]:
    """多文件 AI 整理（三级分治，防截断）：

    文件 →（超长按页分段）→ 每段提炼 → 文件级年度总结 → 跨年综合分析。

    返回 (markdown, error)。未配置/失败时 error 非空。
    """
    if not content.strip():
        return "", "内容为空"
    engine = _engine(timeout=timeout)
    if engine is None:
        return "", "大模型未配置。请先在设置中配置 AI（仅需一次）。"

    chunks = _extract_ocr_chunks(content)
    try:
        year_summaries: list[dict] = []
        for chunk in chunks:
            name = chunk["name"]
            segments = _split_long_text(chunk["text"])
            if len(segments) == 1:
                # 单段：直接提炼（即该文件的年度总结雏形）
                summary = _stage_extract(engine, segments[0], name=name)
                year_summaries.append({"name": name, "summary": summary})
            else:
                extracts = [_stage_extract(engine, seg, name=name) for seg in segments]
                summary = _merge_file_extracts(engine, name, extracts)
                year_summaries.append({"name": name, "summary": summary})
        if len(year_summaries) <= 1:
            # 单份文件：年度总结即最终结果
            return year_summaries[0]["summary"], ""
        final = _stage_merge(engine, year_summaries)
    except AIEngineError as e:
        return "", str(e)
    return final, ""


def summarize_for_markdown(content: str) -> tuple[str, str]:
    """预览区「AI 整理」：多文件走三级分治，单文件直接整理。"""
    if not content.strip():
        return "", "内容为空"
    engine = _engine(timeout=180.0)
    if engine is None:
        return "", "大模型未配置。请先在设置中配置 AI（仅需一次）。"
    chunks = _extract_ocr_chunks(content)
    if len(chunks) <= 1:
        # 单份文件/单段文本：输入量可控，单次整理即可
        system_prompt = (
            "你是企业财报整理助手。根据用户提供的财报原始内容（可能是表格提取文本或"
            "页面文字），整理为结构清晰、合法的中文 markdown 报告："
            "1. 有表格数据时输出 markdown 表格（含表头），数字保留原值；"
            "2. 有文字段落时输出要点化的 markdown 列表；"
            "3. 只整理呈现，不推断、不虚构数据，不改变事实；"
            "4. 禁止任何违规税务筹划表述。"
        )
        try:
            return engine.chat(content, system_prompt=system_prompt, max_tokens=16_384), ""
        except AIEngineError as e:
            return "", str(e)
    return summarize_multi_stage(content)


def summarize_years_for_markdown(
    ocr_text: str = "",
    structured: str = "",
) -> tuple[str, str]:
    """多阶段整理多份审计报告：文件 → 页段提炼 → 年度总结 → 跨年对比。"""
    if not ocr_text.strip():
        return "", "无审计报告 OCR 文本"
    return summarize_multi_stage(ocr_text)


def merge_years_deterministic(data) -> str:
    """基于已解析指标生成确定性「三年对比」markdown（离线回退，不触网）。

    数据源为 session 的 FinancialData，口径与 Tk 基线一致（core.finance
    compute_year_indicators）。仅作整理呈现，不推断、不虚构。
    """
    import importlib

    fin_mod = importlib.import_module("core.finance")
    years = sorted(data.years or [])
    if not years:
        return ""
    key_labels = [
        "营业收入", "毛利率", "净利率",
        "增值税税负率", "所得税税负率", "综合税负率",
    ]
    # 年份 → {key: {value,note,estimate}}，复用导入侧同一口径
    rows = []
    for y in years:
        ind = fin_mod.compute_year_indicators(data, y)
        row = {}
        for k in key_labels:
            v = ind.get(k)
            row[k] = v["value"] if isinstance(v, dict) else v
        rows.append((y, row))

    def fmt(v, pct=False):
        if v is None:
            return "—"
        f = float(v)
        return f"{f:.2f}%" if pct else f"{f:,.2f}"

    lines = [
        f"## 跨年合并报告（{data.company_name or '企业'} · {data.industry or ''}）",
        "",
        "> 数据来源：已导入的财务报表结构化指标；增值税税负率为估算值（基于税金及附加反推）。",
        "",
    ]
    # 表头
    header = "| 指标 | " + " | ".join(str(y) for y in years) + " |"
    sep = "| --- |" + " | ".join("---" for _ in years) + " |"
    lines.append(header)
    lines.append(sep)
    pct_keys = {"毛利率", "净利率", "增值税税负率", "所得税税负率", "综合税负率"}
    for key in key_labels:
        cells = []
        for _, row in rows:
            val = row.get(key)
            cells.append(fmt(val, pct=(key in pct_keys)))
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


_FACT_KEYS = (
    "source_file",
    "report_year",
    "page_range",
    "metrics",
    "audit_opinion",
    "major_events",
    "evidence",
)

_FINAL_HEADINGS = (
    "## 数据范围与完整性",
    "## 跨年关键指标",
    "## 趋势与异常",
    "## 审计意见与重大事项",
    "## 数据冲突与待核验项",
    "## 计算口径与合规声明",
)

_VAT_ESTIMATE_WORDING = "增值税税负率为估算值（基于税金及附加反推）"

_EVIDENCE_INPUT_KEYS = {
    "metric",
    "pages",
    "text",
    "source_file",
    "report_year",
    "page_range",
}


def _reject_json_constant(value: str) -> None:
    raise AIEngineError(f"AI 分段事实 metrics 不能包含非有限数：{value}")


def _parse_exact_fact_json(content: str) -> dict[str, Any]:
    """解析分段事实协议；缺失键为 None，额外键直接拒绝。"""
    try:
        obj = json.loads(
            (content or "").strip(),
            parse_constant=_reject_json_constant,
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise AIEngineError(f"AI 分段事实不是有效 JSON 对象：{exc}") from exc
    if not isinstance(obj, dict):
        raise AIEngineError("AI 分段事实不是 JSON 对象")
    unexpected = sorted(set(obj) - set(_FACT_KEYS))
    if unexpected:
        raise AIEngineError("AI 分段事实 JSON 字段不符合约定：" + "、".join(unexpected))

    fact = {key: obj.get(key) for key in _FACT_KEYS}
    if fact["metrics"] is not None and not isinstance(fact["metrics"], dict):
        raise AIEngineError("AI 分段事实 metrics 必须是对象或 null")
    for metric, value in (fact["metrics"] or {}).items():
        if not isinstance(metric, str) or not metric.strip():
            raise AIEngineError("AI 分段事实 metrics 的指标名必须是非空字符串")
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise AIEngineError(f"AI 分段事实 metrics.{metric} 必须是有限数字或 null")
    if fact["audit_opinion"] is not None and not isinstance(fact["audit_opinion"], str):
        raise AIEngineError("AI 分段事实 audit_opinion 必须是字符串或 null")
    if fact["major_events"] is not None:
        if not isinstance(fact["major_events"], list) or any(
            not isinstance(item, str) or not item.strip()
            for item in fact["major_events"]
        ):
            raise AIEngineError("AI 分段事实 major_events 必须是非空字符串数组或 null")
    if fact["evidence"] is not None:
        if not isinstance(fact["evidence"], list):
            raise AIEngineError("AI 分段事实 evidence 必须是对象数组或 null")
        for item in fact["evidence"]:
            if not isinstance(item, dict) or set(item) - _EVIDENCE_INPUT_KEYS:
                raise AIEngineError("AI 分段事实 evidence 元素字段不符合约定")
            pages = item.get("pages")
            if not isinstance(pages, list) or not pages or any(
                isinstance(page, bool) or not isinstance(page, int)
                for page in pages
            ):
                raise AIEngineError("AI 分段事实 evidence.pages 必须是非空整数数组")
            for key in ("metric", "text"):
                if item.get(key) is not None and (
                    not isinstance(item[key], str) or not item[key].strip()
                ):
                    raise AIEngineError(f"AI 分段事实 evidence.{key} 必须是非空字符串或 null")
    return fact


def _trusted_evidence(
    evidence: list[dict] | None,
    source_file: str,
    report_year: Any,
    page_range: list[int],
) -> list[dict] | None:
    if evidence is None:
        return None
    start_page, end_page = page_range
    trusted: list[dict] = []
    for item in evidence:
        pages = item["pages"]
        if any(page < start_page or page > end_page for page in pages):
            raise AIEngineError("AI 分段事实 evidence 页码超出可信 chunk 范围")
        normalized = {
            key: item[key]
            for key in ("metric", "pages", "text")
            if item.get(key) is not None
        }
        normalized.update(
            {
                "source_file": source_file,
                "report_year": report_year,
                "page_range": list(page_range),
            }
        )
        trusted.append(normalized)
    return trusted


def extract_chunk_facts(engine, source: dict, chunk: PDFTextChunk) -> dict:
    """从单个文件的单一连续页段提取严格 JSON 事实。"""
    source_file = str(source.get("name") or source.get("source_file") or "").strip()
    if not source_file:
        raise AIEngineError("分段提取缺少来源文件名")
    company_name = str(source.get("company_name") or "未知企业").strip()
    report_year = source.get("report_year")
    page_range = [int(chunk.start_page), int(chunk.end_page)]
    schema = ", ".join(_FACT_KEYS)
    prompt = (
        "从下面这一段审计报告原文提取事实。只返回一个 JSON 对象，不要 Markdown。\n"
        f"企业：{company_name}\n"
        f"来源文件：{source_file}\n"
        f"报告年度：{report_year if report_year is not None else '未知'}\n"
        f"页范围：{chunk.start_page}-{chunk.end_page} / {chunk.total_pages}\n"
        f"JSON 必须且只能使用这些键：{schema}。\n"
        "metrics 为指标到数值的对象；audit_opinion 为字符串；major_events 为数组；"
        "evidence 每项只能含 metric、pages、text，其中 pages 是当前页段内的非空整数页码数组。"
        "识别不到的字段使用 null，绝不能用 0 代替缺失值。\n"
        "source_file、report_year、page_range 必须原样复述上述元数据。\n\n"
        f"原文：\n{chunk.text}"
    )
    result = engine.chat_result(
        prompt,
        system_prompt="你是企业审计报告事实提取助手，只提取原文明确存在的事实。",
        max_tokens=EXTRACT_MAX_TOKENS,
        extra={"response_format": {"type": "json_object"}, "stream": False},
    )
    fact = _parse_exact_fact_json(result.content)
    # 来源元数据以调用方的不可变页段为准，绝不采用模型回显的其他文件信息。
    fact["source_file"] = source_file
    fact["report_year"] = report_year
    fact["page_range"] = page_range
    fact["evidence"] = _trusted_evidence(
        fact["evidence"], source_file, report_year, page_range
    )
    fact["_total_pages"] = int(chunk.total_pages)
    fact["_finish_reason"] = result.finish_reason
    return fact


def _fact_page_range(fact: dict) -> tuple[int, int]:
    page_range = fact.get("page_range")
    if (
        not isinstance(page_range, (list, tuple))
        or len(page_range) != 2
        or isinstance(page_range[0], bool)
        or isinstance(page_range[1], bool)
    ):
        raise AIEngineError("分段事实页范围无效")
    try:
        start, end = int(page_range[0]), int(page_range[1])
    except (TypeError, ValueError) as exc:
        raise AIEngineError("分段事实页范围无效") from exc
    if start < 1 or end < start:
        raise AIEngineError("分段事实页范围无效")
    return start, end


def merge_document_facts(source: dict, facts: list[dict]) -> dict:
    """合并同一文件事实，并强制页段从第一页连续覆盖到末页。"""
    if not facts:
        raise AIEngineError("文档缺少分段事实")
    source_file = str(source.get("name") or source.get("source_file") or "").strip()
    report_year = source.get("report_year")
    ordered = sorted(facts, key=lambda item: _fact_page_range(item)[0])
    expected_total_raw = source.get("page_count") or ordered[0].get("_total_pages")
    try:
        expected_total = int(expected_total_raw)
    except (TypeError, ValueError) as exc:
        raise AIEngineError("文档缺少总页数") from exc
    if expected_total < 1:
        raise AIEngineError("文档总页数无效")

    next_page = 1
    metrics: dict[str, Any] = {}
    evidence: list[Any] = []
    audit_opinions: list[str] = []
    major_events: list[Any] = []
    conflicts: list[dict] = []
    metric_sides: dict[str, dict] = {}

    for fact in ordered:
        if fact.get("source_file") != source_file or fact.get("report_year") != report_year:
            raise AIEngineError("分段事实来源与文档元数据不一致")
        if fact.get("_finish_reason") != "stop":
            raise AIEngineError("分段事实未正常完成")
        try:
            fact_total = int(fact.get("_total_pages"))
        except (TypeError, ValueError) as exc:
            raise AIEngineError("分段事实缺少总页数") from exc
        if fact_total != expected_total:
            raise AIEngineError("分段事实总页数不一致")
        start, end = _fact_page_range(fact)
        if start != next_page or end > expected_total:
            raise AIEngineError("文档页覆盖不连续，存在缺失或重复页段")
        next_page = end + 1

        fact_metrics = fact.get("metrics")
        if fact_metrics is not None and not isinstance(fact_metrics, dict):
            raise AIEngineError("分段事实 metrics 必须是对象或 null")
        fact_evidence = fact.get("evidence") or []
        for metric, value in (fact_metrics or {}).items():
            side = {
                "kind": "ai",
                "source_file": source_file,
                "value": value,
                "evidence": [
                    item
                    for item in fact_evidence
                    if item.get("metric") in (None, metric)
                ],
            }
            if metric not in metrics or metrics[metric] is None:
                metrics[metric] = value
                metric_sides[metric] = side
            elif value is not None and value != metrics[metric]:
                conflict = next((item for item in conflicts if item.get("metric") == metric), None)
                if conflict is None:
                    conflict = {
                        "source_file": source_file,
                        "report_year": report_year,
                        "metric": metric,
                        "values": [metrics[metric]],
                        "sides": [metric_sides[metric]],
                    }
                    conflicts.append(conflict)
                if value not in conflict["values"]:
                    conflict["values"].append(value)
                    conflict["sides"].append(side)

        opinion = fact.get("audit_opinion")
        if opinion and opinion not in audit_opinions:
            audit_opinions.append(opinion)
        for event in fact.get("major_events") or []:
            if event not in major_events:
                major_events.append(event)
        evidence.extend(fact_evidence)

    if next_page != expected_total + 1:
        raise AIEngineError("文档页覆盖不连续，存在缺失页段")
    return {
        "source_file": source_file,
        "report_year": report_year,
        "page_range": [1, expected_total],
        "page_coverage": [expected_total, expected_total],
        "metrics": metrics,
        "audit_opinion": audit_opinions[0] if len(audit_opinions) == 1 else (audit_opinions or None),
        "major_events": major_events or None,
        "evidence": evidence,
        "conflicts": conflicts,
    }


def _markdown_table_rows(markdown: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw_line in (markdown or "").splitlines():
        line = raw_line.strip()
        if line.startswith("|") and line.endswith("|"):
            rows.append([cell.strip() for cell in line[1:-1].split("|")])
    return rows


def _number_from_cell(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {"—", "-", "--", "N/A", "null", "None"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace("%", "").replace("元", "").strip()
    multiplier = 1.0
    if text.endswith("亿"):
        text, multiplier = text[:-1], 100_000_000.0
    elif text.endswith("万"):
        text, multiplier = text[:-1], 10_000.0
    try:
        number = float(text) * multiplier
    except ValueError:
        return None
    return -number if negative else number


def _parse_deterministic_table(markdown: str) -> dict[str, dict[str, float | None]]:
    rows = _markdown_table_rows(markdown)
    header_index = next(
        (index for index, row in enumerate(rows) if row and row[0] == "指标"),
        None,
    )
    if header_index is None:
        return {}
    header = rows[header_index]
    years = [cell for cell in header[1:] if re.fullmatch(r"\d{4}", cell)]
    if not years:
        return {}
    result = {year: {} for year in years}
    for row in rows[header_index + 1:]:
        if not row or re.fullmatch(r":?-{3,}:?", row[0] or ""):
            continue
        if len(row) < len(years) + 1:
            continue
        metric = row[0]
        for year, cell in zip(years, row[1:]):
            value = _number_from_cell(cell)
            result[year][metric] = value
    return result


def deterministic_metric_matrix(markdown: str) -> dict[str, dict[str, float | None]]:
    """公开确定性指标矩阵，保留明确的缺失值状态。"""
    return _parse_deterministic_table(markdown)


def _values_conflict(left: Any, right: Any) -> bool:
    left_number = _number_from_cell(left)
    right_number = _number_from_cell(right)
    if left_number is None or right_number is None:
        return left != right
    tolerance = max(1e-9, abs(right_number) * 1e-9)
    return abs(left_number - right_number) > tolerance


def reconcile_with_deterministic(documents: list[dict], deterministic: str) -> dict:
    """以确定性财务表覆盖重叠数值，同时保留 AI 叙事事实和冲突。"""
    years: dict[str, dict[str, Any]] = {}
    audit_opinions: dict[str, list[dict]] = {}
    major_events: dict[str, list[dict]] = {}
    page_coverage: dict[str, list[int]] = {}
    conflicts: list[dict] = []
    metric_sides: dict[str, dict[str, dict]] = {}

    for document in documents:
        year_value = document.get("report_year")
        if year_value is None:
            continue
        year = str(year_value)
        source_file = str(document.get("source_file") or "")
        year_metrics = years.setdefault(year, {})
        year_sides = metric_sides.setdefault(year, {})
        for metric, value in (document.get("metrics") or {}).items():
            evidence = [
                item
                for item in document.get("evidence") or []
                if item.get("metric") in (None, metric)
            ]
            side = {
                "kind": "ai",
                "source_file": source_file,
                "value": value,
                "evidence": evidence,
            }
            if (
                metric in year_metrics
                and year_metrics[metric] is not None
                and value is not None
                and _values_conflict(year_metrics[metric], value)
            ):
                conflicts.append(
                    {
                        "source_file": source_file,
                        "existing_source_file": year_sides[metric]["source_file"],
                        "report_year": year_value,
                        "metric": metric,
                        "ai_value": value,
                        "existing_ai_value": year_metrics[metric],
                        "sides": [year_sides[metric], side],
                    }
                )
            elif metric not in year_metrics or year_metrics[metric] is None:
                year_metrics[metric] = value
                year_sides[metric] = side
        conflicts.extend(document.get("conflicts") or [])

        opinion = document.get("audit_opinion")
        opinions = opinion if isinstance(opinion, list) else ([opinion] if opinion else [])
        for value in opinions:
            audit_opinions.setdefault(year, []).append({"source_file": source_file, "value": value})
        for value in document.get("major_events") or []:
            major_events.setdefault(year, []).append({"source_file": source_file, "value": value})
        if source_file and document.get("page_coverage"):
            page_coverage[source_file] = list(document["page_coverage"])

    for year, deterministic_metrics in _parse_deterministic_table(deterministic).items():
        year_metrics = years.setdefault(year, {})
        for metric, deterministic_value in deterministic_metrics.items():
            if metric in year_metrics and year_metrics[metric] is not None and _values_conflict(
                year_metrics[metric], deterministic_value
            ):
                ai_side = metric_sides.get(year, {}).get(
                    metric,
                    {
                        "kind": "ai",
                        "source_file": "",
                        "value": year_metrics[metric],
                        "evidence": [],
                    },
                )
                deterministic_side = {
                    "kind": "deterministic",
                    "source_file": "merge_years_deterministic",
                    "value": deterministic_value,
                    "evidence": [
                        {
                            "source_file": "merge_years_deterministic",
                            "report_year": int(year),
                            "metric": metric,
                        }
                    ],
                }
                conflicts.append(
                    {
                        "source_file": ai_side["source_file"],
                        "deterministic_source_file": deterministic_side["source_file"],
                        "report_year": int(year),
                        "metric": metric,
                        "ai_value": year_metrics[metric],
                        "deterministic_value": deterministic_value,
                        "sides": [ai_side, deterministic_side],
                    }
                )
            year_metrics[metric] = deterministic_value

    return {
        "years": years,
        "audit_opinions": audit_opinions,
        "major_events": major_events,
        "page_coverage": page_coverage,
        "conflicts": conflicts,
        "documents": documents,
    }


def _payload_years(payload: dict) -> list[int]:
    raw_years = payload.get("years")
    if isinstance(raw_years, dict):
        raw_years = raw_years.keys()
    if not raw_years:
        raw_years = (payload.get("years_data") or {}).keys()
    years: list[int] = []
    for value in raw_years or []:
        try:
            years.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(years))


def generate_final_report_result(
    engine, payload: dict, max_tokens: int = FINAL_MAX_TOKENS
) -> AIChatResult:
    """用已对账载荷生成固定结构的最终 Markdown，并保留模型元数据。"""
    company_name = str(payload.get("company_name") or "企业").strip()
    years = _payload_years(payload)
    year_span = f"{years[0]}—{years[-1]}" if years else "年度未知"
    title = f"# {company_name} {year_span} 跨年合并报告"
    coverage = payload.get("page_coverage") or {}
    coverage_lines = [
        f"- {name}: {values[0]}/{values[1]} 页"
        for name, values in coverage.items()
        if isinstance(values, (list, tuple)) and len(values) == 2
    ]
    prompt = (
        "根据下方已经完成确定性对账的数据生成中文 Markdown 最终报告。不得改写企业名称、"
        "年度、来源文件、页覆盖或已对账数值；缺失值写“未识别”，不得写成 0。\n"
        "必须依次使用以下标题，不能增删或改名：\n"
        f"{title}\n"
        + "\n".join(_FINAL_HEADINGS)
        + "\n跨年关键指标必须使用首尾都有竖线且列数一致的 Markdown 表格。\n"
        "数据范围章节必须逐文件写明页覆盖：\n"
        + ("\n".join(coverage_lines) if coverage_lines else "- 无页覆盖元数据")
        + "\n计算口径与合规声明必须原样包含："
        + _VAT_ESTIMATE_WORDING
        + "。所有分析仅基于给定事实并遵循合法合规边界。\n\n"
        "已对账数据：\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )
    result = engine.chat_result(
        prompt,
        system_prompt="你是企业财务报告编制助手，只能使用用户提供的已对账事实。",
        max_tokens=max_tokens,
        extra={"stream": False},
    )
    if result.finish_reason != "stop":
        raise AIEngineError("AI 最终报告未正常完成")
    return result


def generate_final_report(engine, payload: dict) -> str:
    """兼容旧调用：以默认 token 上限返回最终 Markdown 字符串。"""
    return generate_final_report_result(engine, payload, FINAL_MAX_TOKENS).content


def validate_reconciled_payload(payload: dict, expected: dict) -> list[str]:
    """最终生成前验证身份、年度、来源覆盖和冲突来源完整性。"""
    errors: list[str] = []
    if str(payload.get("company_name") or "") != str(expected.get("company_name") or ""):
        errors.append("企业名称不一致")
    expected_years = sorted({int(year) for year in expected.get("years") or []})
    if _payload_years(payload) != expected_years:
        errors.append("年度集合不一致")

    payload_years = payload.get("years") or {}
    required = expected.get("required_metrics") or {}
    if isinstance(required, dict):
        for year, metrics in required.items():
            actual_metrics = payload_years.get(str(year), {}) if isinstance(payload_years, dict) else {}
            if not isinstance(metrics, dict):
                continue
            for metric, expected_value in metrics.items():
                if not isinstance(actual_metrics, dict) or metric not in actual_metrics:
                    errors.append(f"缺少确定性指标状态：{metric} {year}")
                    continue
                actual_value = actual_metrics[metric]
                if (expected_value is None and actual_value is not None) or (
                    expected_value is not None
                    and (actual_value is None or _values_conflict(actual_value, expected_value))
                ):
                    errors.append(f"确定性指标值不一致：{metric} {year}")

    expected_coverage = {
        str(name): list(values)
        for name, values in (expected.get("page_coverage") or {}).items()
    }
    actual_coverage = {
        str(name): list(values)
        for name, values in (payload.get("page_coverage") or {}).items()
    }
    if actual_coverage != expected_coverage:
        errors.append("文件页覆盖不完整")

    documents = payload.get("documents")
    if not isinstance(documents, list):
        errors.append("文档事实集合缺失")
    else:
        names = [str(document.get("source_file") or "") for document in documents]
        if len(names) != len(set(names)) or set(names) != set(expected_coverage):
            errors.append("文档来源集合不一致")

    for conflict in payload.get("conflicts") or []:
        sides = conflict.get("sides") if isinstance(conflict, dict) else None
        if not isinstance(sides, list) or len(sides) < 2:
            errors.append("冲突来源不完整")
            break
    return errors


def build_rules_report(data, reason_code: str) -> str:
    """只用结构化会话数据生成诚实标识的规则版快速报告。"""
    deterministic = merge_years_deterministic(data)
    years = sorted(data.years or [])
    span = f"{years[0]}—{years[-1]}" if years else "年度未知"
    return "\n".join(
        [
            f"# {data.company_name or '企业'} {span} 规则版快速报告",
            "",
            "> 本报告仅依据已导入的结构化财务指标生成，不代表完整逐页 AI 审阅结果。",
            f"> 回退原因代码：{reason_code}",
            "",
            "## 结构化指标",
            deterministic,
            "",
            "## 计算口径与合规声明",
            _VAT_ESTIMATE_WORDING + "。本报告仅用于合法合规的经营分析。",
        ]
    )


def validate_rules_report(markdown: str, expected: dict) -> list[str]:
    """规则版使用独立门禁，禁止冒充完整 AI 报告。"""
    errors: list[str] = []
    if "规则版快速报告" not in (markdown or ""):
        errors.append("缺少规则版标识")
    company = str(expected.get("company_name") or "")
    if company and company not in (markdown or ""):
        errors.append("企业名称不一致")
    for year in expected.get("years") or []:
        if not re.search(rf"(?<!\d){int(year)}(?!\d)", markdown or ""):
            errors.append(f"缺少年度：{year}")
    if "AI 完整读取" in (markdown or "") or "DeepSeek 生成" in (markdown or ""):
        errors.append("规则版包含 AI 完整报告声明")
    if _VAT_ESTIMATE_WORDING not in (markdown or ""):
        errors.append("缺少增值税税负率估算口径")
    return errors


def _markdown_section(markdown: str, heading: str) -> str:
    lines = (markdown or "").splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == heading) + 1
    except StopIteration:
        return ""
    end = next(
        (
            index
            for index in range(start, len(lines))
            if lines[index].strip().startswith("## ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _parse_key_metrics_table(markdown: str) -> tuple[list[str], dict[str, list[str]], bool]:
    """只解析“跨年关键指标”章节内的第一张 Markdown 表。"""
    section = _markdown_section(markdown, "## 跨年关键指标")
    section_lines = section.splitlines()
    first_pipe = next(
        (index for index, line in enumerate(section_lines) if "|" in line),
        None,
    )
    if first_pipe is None:
        return [], {}, False

    table_lines: list[str] = []
    for raw_line in section_lines[first_pipe:]:
        line = raw_line.strip()
        if not line:
            if table_lines:
                break
            continue
        if "|" not in line:
            break
        table_lines.append(line)
    if len(table_lines) < 3:
        return [], {}, False

    structurally_valid = True
    parsed_rows: list[list[str]] = []
    for line in table_lines:
        if not line.startswith("|") or not line.endswith("|") or line == "|":
            structurally_valid = False
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        parsed_rows.append(cells)

    column_count = len(parsed_rows[0])
    if column_count < 2 or any(len(row) != column_count for row in parsed_rows):
        structurally_valid = False
    header = parsed_rows[0]
    if not header or header[0] != "指标" or any(not cell for cell in header):
        structurally_valid = False
    separator = parsed_rows[1]
    if len(separator) != column_count or any(
        not re.fullmatch(r":?-{3,}:?", cell) for cell in separator
    ):
        structurally_valid = False

    data_rows = parsed_rows[2:]
    if not data_rows:
        structurally_valid = False
    metrics: dict[str, list[str]] = {}
    for row in data_rows:
        if len(row) != column_count or not row or not row[0] or any(not cell for cell in row[1:]):
            structurally_valid = False
        if row and row[0]:
            if row[0] in metrics:
                structurally_valid = False
            metrics[row[0]] = row[1:]

    years = header[1:] if len(header) > 1 else []
    if any(not re.fullmatch(r"\d{4}", year) for year in years):
        structurally_valid = False
    return years, metrics, structurally_valid


def _required_metric_matrix(expected: dict, years: list[int]) -> dict[str, list[int]]:
    raw = expected.get("required_metrics")
    if raw is None:
        raw = expected.get("years_data")
    if raw is None and isinstance(expected.get("years"), dict):
        raw = expected["years"]
    if not raw:
        return {}
    if isinstance(raw, (list, tuple, set)):
        return {str(metric): list(years) for metric in raw}
    if not isinstance(raw, dict):
        return {}

    # 同时接受“年度 -> 指标对象”和“指标 -> 年度集合/对象”两种矩阵方向。
    if raw and all(re.fullmatch(r"\d{4}", str(key)) for key in raw):
        matrix: dict[str, list[int]] = {}
        for year, metrics in raw.items():
            if not isinstance(metrics, dict):
                continue
            for metric in metrics:
                matrix.setdefault(str(metric), []).append(int(year))
        return {metric: sorted(set(values)) for metric, values in matrix.items()}

    matrix = {}
    for metric, required_years in raw.items():
        if required_years is None:
            values = list(years)
        elif isinstance(required_years, dict):
            values = list(required_years)
        elif isinstance(required_years, (list, tuple, set)):
            values = list(required_years)
        else:
            values = list(years)
        normalized: list[int] = []
        for value in values:
            try:
                normalized.append(int(value))
            except (TypeError, ValueError):
                continue
        matrix[str(metric)] = sorted(set(normalized))
    return matrix


def validate_final_report(markdown: str, expected: dict) -> list[str]:
    """汇总最终报告的身份、范围、结构与合规口径错误。"""
    errors: list[str] = []
    company_name = str(expected.get("company_name") or "").strip()
    years = sorted({int(year) for year in expected.get("years") or []})
    first_title = next(
        (line.strip() for line in (markdown or "").splitlines() if line.strip().startswith("# ")),
        "",
    )
    title_match = re.fullmatch(
        r"#\s+(.+?)\s+(\d{4})—(\d{4})\s+跨年合并报告",
        first_title,
    )
    if not title_match or title_match.group(1) != company_name:
        errors.append("企业名称不一致")
    if years:
        expected_title = f"# {company_name} {years[0]}—{years[-1]} 跨年合并报告"
        if first_title != expected_title:
            errors.append("报告标题不符合约定")

    body_lines = (markdown or "").splitlines()
    if first_title:
        title_index = next(
            (index for index, line in enumerate(body_lines) if line.strip() == first_title),
            None,
        )
        if title_index is not None:
            body_lines.pop(title_index)
    scope_start = next(
        (
            index
            for index, line in enumerate(body_lines)
            if line.strip() == "## 数据范围与完整性"
        ),
        None,
    )
    if scope_start is not None:
        scope_end = next(
            (
                index
                for index in range(scope_start + 1, len(body_lines))
                if body_lines[index].strip().startswith("## ")
            ),
            len(body_lines),
        )
        del body_lines[scope_start:scope_end]
    body_markdown = "\n".join(body_lines)
    missing_years = [
        str(year)
        for year in years
        if not re.search(rf"(?<!\d){year}(?!\d)", body_markdown)
    ]
    if missing_years:
        errors.append("缺少年度：" + "、".join(missing_years))

    missing_headings = [heading for heading in _FINAL_HEADINGS if heading not in (markdown or "")]
    if missing_headings:
        errors.append("缺少章节：" + "、".join(heading[3:] for heading in missing_headings))
    table_years, metric_rows, table_valid = _parse_key_metrics_table(markdown)
    if not table_valid:
        errors.append("跨年关键指标表结构无效")
        errors.append("Markdown 表格未闭合")
    missing_table_years = [str(year) for year in years if str(year) not in table_years]
    if missing_table_years:
        errors.append("关键指标表缺少年度：" + "、".join(missing_table_years))

    header_indexes = {year: index for index, year in enumerate(table_years)}
    for metric, required_years in _required_metric_matrix(expected, years).items():
        row = metric_rows.get(metric)
        if row is None:
            errors.append(f"缺少关键指标：{metric}")
        for year in required_years:
            index = header_indexes.get(str(year))
            if row is None or index is None or index >= len(row) or not row[index].strip():
                errors.append(f"关键指标缺少年度值：{metric} {year}")

    raw_required = expected.get("required_metrics") or {}
    if isinstance(raw_required, dict) and raw_required and all(
        re.fullmatch(r"\d{4}", str(key)) for key in raw_required
    ):
        explicit_missing = {"—", "-", "--", "N/A", "null", "None", "未识别"}
        for year, metrics in raw_required.items():
            if not isinstance(metrics, dict):
                continue
            index = header_indexes.get(str(year))
            for metric, expected_value in metrics.items():
                row = metric_rows.get(str(metric))
                if row is None or index is None or index >= len(row):
                    continue
                cell = row[index].strip()
                if expected_value is None:
                    if cell not in explicit_missing:
                        errors.append(f"关键指标缺失状态不一致：{metric} {year}")
                else:
                    actual_value = _number_from_cell(cell)
                    if actual_value is None or _values_conflict(actual_value, expected_value):
                        errors.append(f"关键指标值不一致：{metric} {year}")

    for source_file, coverage in (expected.get("page_coverage") or {}).items():
        if not isinstance(coverage, (list, tuple)) or len(coverage) != 2:
            errors.append(f"页覆盖元数据无效：{source_file}")
            continue
        covered, total = coverage
        pattern = rf"(?m)^.*{re.escape(str(source_file))}.*{re.escape(str(covered))}\s*/\s*{re.escape(str(total))}.*$"
        if not re.search(pattern, markdown or ""):
            errors.append(f"缺少页覆盖：{source_file} {covered}/{total}")

    conflict_section = _markdown_section(markdown, "## 数据冲突与待核验项")
    for index, conflict in enumerate(expected.get("conflicts") or [], start=1):
        if not isinstance(conflict, dict):
            errors.append(f"冲突呈现不完整：{index}")
            continue
        required_tokens: list[str] = []
        metric = str(conflict.get("metric") or "").strip()
        if metric:
            required_tokens.append(metric)
        sides = conflict.get("sides")
        complete = isinstance(sides, list) and len(sides) >= 2
        for side in sides if isinstance(sides, list) else []:
            if not isinstance(side, dict):
                complete = False
                continue
            source_file = str(side.get("source_file") or "").strip()
            if source_file:
                required_tokens.append(source_file)
            else:
                complete = False
            value = side.get("value")
            if value is not None:
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    required_tokens.append(str(value))
                else:
                    candidates = {str(value), f"{number:g}", f"{number:,.2f}"}
                    if not any(candidate in conflict_section for candidate in candidates):
                        complete = False
            for evidence in side.get("evidence") or []:
                for page in evidence.get("pages") or [] if isinstance(evidence, dict) else []:
                    if not re.search(rf"第\s*{int(page)}\s*页", conflict_section):
                        complete = False
        if not complete or any(token not in conflict_section for token in required_tokens):
            errors.append(f"冲突呈现不完整：{metric or index}")
    if _VAT_ESTIMATE_WORDING not in (markdown or ""):
        errors.append("缺少增值税税负率估算口径")
    return errors


# 预算模板顶部需要识别的指标键
_BUDGET_INDICATOR_KEYS = ("budget_revenue", "budget_cost", "last_year_revenue", "last_year_cost")


def _parse_budget_json(content: str) -> dict:
    """解析 AI 返回的预算指标 JSON 对象；失败抛 AIEngineError。

    复用 _parse_options_json 的「截取 JSON 片段 + json.loads」套路（改为 dict）。
    """
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise AIEngineError(f"AI 返回非 JSON 对象：{content[:120]}")
    try:
        obj = json.loads(content[start:end + 1])
    except Exception as e:
        raise AIEngineError(f"AI 返回 JSON 解析失败：{e}") from e
    if not isinstance(obj, dict):
        raise AIEngineError(f"AI 返回非对象：{content[:120]}")
    return obj


def extract_budget_indicators(ocr_text: str) -> tuple[dict, str]:
    """从审计报告 OCR 原文识别预算模板顶部指标。

    识别 4 个字段（营业收入/营业成本 × 本年累计/上年累计），返回 JSON dict：
        {"budget_revenue": number, "budget_cost": number,
         "last_year_revenue": number, "last_year_cost": number}
    返回 (indicators, error)。未配置/失败时 error 非空，indicators 为空 dict。
    """
    if not ocr_text.strip():
        return {}, "无审计报告 OCR 文本，无法识别"
    engine = _engine(timeout=60.0)  # OCR 文本较长，识别需更长时间
    if engine is None:
        return {}, "大模型未配置。请先在设置中配置 AI（仅需一次）。"
    system_prompt = (
        "你是企业财报数据提取助手。根据用户提供的审计报告 OCR 提取文本，识别利润表中的关键指标。"
        "只需返回一个 JSON 对象，包含："
        '{"budget_revenue": 本年累计营业收入, "budget_cost": 本年累计营业成本,'
        ' "last_year_revenue": 上年累计营业收入, "last_year_cost": 上年累计营业成本}。'
        "金额单位为元，返回数字（不要千分位逗号、不要货币符号）。"
        "若某项识别不到，填 0。只返回 JSON，不要其他文字。"
        "禁止虚构数据；识别不到就填 0。"
    )
    try:
        content = engine.chat(ocr_text[:6000], system_prompt=system_prompt, max_tokens=4_096)
        obj = _parse_budget_json(content)
        # 只取 4 个目标键，缺失填 0
        out: dict = {}
        for key in _BUDGET_INDICATOR_KEYS:
            val = obj.get(key)
            try:
                out[key] = round(float(val), 2) if val else 0.0
            except (TypeError, ValueError):
                out[key] = 0.0
        return out, ""
    except AIEngineError as e:
        return {}, str(e)
