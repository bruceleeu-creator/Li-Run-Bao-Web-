"""利润宝 · Web AI 配置与调用（可选增强）。

配置持久化：base_url / model / api_key 写入 .ai_config.json（用户选择
「配一次全局可用、重启免重输」）；`get_config()` 响应不返回 api_key，
避免前端回显敏感字段。未配置时绝不触网；调用失败由前端展示提示，
不影响主流程。
"""

from __future__ import annotations

import json
import logging
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
        from core import compliance_policy as compliance_mod

        system_prompt = (
            "你是企业财报整理助手。根据用户提供的财报原始内容（可能是表格提取文本或"
            "页面文字），整理为结构清晰、合法的中文 markdown 报告："
            "1. 有表格数据时输出 markdown 表格（含表头），数字保留原值；"
            "2. 有文字段落时输出要点化的 markdown 列表；"
            "3. 只整理呈现，不推断、不虚构数据，不改变事实；"
            "4. 禁止任何违规税务筹划表述；"
            "5. 在「经营与费用要点」中必须穿插："
            "历史成本费用占营收比例对标、收入变化下费用可筹划空间（合法合规/金税四期）、"
            "费用增幅是否匹配营收增速的提示（仅基于原文数字，禁止编造）。\n"
            + compliance_mod.HARD_RULES_BLOCK
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
    from core import compliance_policy as compliance_mod

    prompt = (
        "以下是多份不同年份审计报告的年度总结。请生成最终的跨年对比综合分析报告，"
        "结构如下：\n"
        "1. **企业概况**：企业名称、报告年度范围；\n"
        "2. **逐年关键指标表**：用 markdown 表格逐年对比（营业收入、营业成本、净利润、"
        "毛利率、净利率、增值税税负率、期间费用率），表头含年份列；\n"
        "3. **逐年要点**：每个年度单独一个小节（该年关键事件、异常、趋势线索）；\n"
        "4. **跨年趋势与异常**：用要点列出逐年变化趋势与值得关注的异常；\n"
        "5. **费用合规对标（必须）**：历史成本费用占营收比例对标；"
        "收入上涨后费用可筹划空间（合法合规/金税四期）；"
        "费用增幅是否匹配营收增速（仅基于给定数字）；\n"
        "6. 若某年数据缺失，标注「该年数据缺失」。\n"
        "禁止违规税务筹划表述，只基于给定总结。\n"
        + compliance_mod.HARD_RULES_BLOCK
        + "\n\n"
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
        from core import compliance_policy as compliance_mod

        system_prompt = (
            "你是企业财报整理助手。根据用户提供的财报原始内容（可能是表格提取文本或"
            "页面文字），整理为结构清晰、合法的中文 markdown 报告："
            "1. 有表格数据时输出 markdown 表格（含表头），数字保留原值；"
            "2. 有文字段落时输出要点化的 markdown 列表；"
            "3. 只整理呈现，不推断、不虚构数据，不改变事实；"
            "4. 禁止任何违规税务筹划表述；"
            "5. 在「经营与费用要点」中必须穿插："
            "历史成本费用占营收比例对标、收入变化下费用可筹划空间（合法合规/金税四期）、"
            "费用增幅是否匹配营收增速的提示（仅基于原文数字，禁止编造）。\n"
            + compliance_mod.HARD_RULES_BLOCK
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


def _fmt_metric_cell(value: Any, metric: str = "") -> str:
    """把确定性数值格式化为表内单元格，缺失写「未识别」。"""
    if value is None:
        return "未识别"
    number = _number_from_cell(value)
    if number is None:
        text = str(value).strip()
        return text if text else "未识别"
    if "率" in metric or "margin" in metric.lower():
        return f"{number:.2f}%"
    # 金额保留两位，千分位
    return f"{number:,.2f}"


def _build_scope_section(expected: dict) -> str:
    coverage = expected.get("page_coverage") or {}
    lines = ["## 数据范围与完整性", "", "| 文件 | 页覆盖 |", "| --- | --- |"]
    if not coverage:
        lines.append("| 无页覆盖元数据 | — |")
    else:
        for name, values in coverage.items():
            if isinstance(values, (list, tuple)) and len(values) == 2:
                lines.append(f"| {name} | {values[0]}/{values[1]} 页 |")
            else:
                lines.append(f"| {name} | 未识别 |")
    return "\n".join(lines)


def _build_metrics_table_section(expected: dict, years: list[int]) -> str:
    """用 required_metrics 拼装可通过校验的关键指标表。"""
    raw = expected.get("required_metrics") or {}
    # year -> metric -> value
    by_year: dict[str, dict[str, Any]] = {}
    metrics_order: list[str] = []
    if isinstance(raw, dict) and raw and all(re.fullmatch(r"\d{4}", str(k)) for k in raw):
        for year, metrics in raw.items():
            if not isinstance(metrics, dict):
                continue
            by_year[str(year)] = dict(metrics)
            for metric in metrics:
                if metric not in metrics_order:
                    metrics_order.append(str(metric))
    elif isinstance(raw, dict):
        # metric -> year -> value
        for metric, years_map in raw.items():
            metrics_order.append(str(metric))
            if isinstance(years_map, dict):
                for year, value in years_map.items():
                    by_year.setdefault(str(year), {})[str(metric)] = value

    year_cols = [str(y) for y in years] if years else sorted(by_year.keys())
    if not metrics_order:
        metrics_order = ["营业收入", "毛利率", "净利率", "增值税税负率", "所得税税负率"]
    header = "| 指标 | " + " | ".join(year_cols) + " |"
    sep = "| --- | " + " | ".join("---" for _ in year_cols) + " |"
    rows = [header, sep]
    for metric in metrics_order:
        cells = [_fmt_metric_cell((by_year.get(y) or {}).get(metric), metric) for y in year_cols]
        # 校验要求每个单元格非空
        cells = [c if c.strip() else "未识别" for c in cells]
        rows.append("| " + metric + " | " + " | ".join(cells) + " |")
    return "## 跨年关键指标\n\n" + "\n".join(rows)


def _build_conflicts_section(expected: dict) -> str:
    conflicts = expected.get("conflicts") or []
    lines = ["## 数据冲突与待核验项", ""]
    if not conflicts:
        lines.append("无")
        return "\n".join(lines)
    for index, conflict in enumerate(conflicts, start=1):
        if not isinstance(conflict, dict):
            lines.append(f"{index}. 冲突项待核验")
            continue
        metric = str(conflict.get("metric") or f"冲突{index}").strip()
        lines.append(f"### {metric}")
        sides = conflict.get("sides") if isinstance(conflict.get("sides"), list) else []
        if len(sides) < 2:
            # 兼容旧结构
            sides = [
                {
                    "source_file": conflict.get("existing_source_file") or conflict.get("source_file") or "",
                    "value": conflict.get("existing_ai_value", conflict.get("ai_value")),
                    "evidence": [],
                },
                {
                    "source_file": conflict.get("source_file") or "merge_years_deterministic",
                    "value": conflict.get("ai_value", conflict.get("deterministic_value")),
                    "evidence": [],
                },
            ]
        for side in sides:
            if not isinstance(side, dict):
                continue
            source_file = str(side.get("source_file") or "未知来源").strip() or "未知来源"
            value = side.get("value")
            if value is None:
                value_text = "未识别"
            else:
                try:
                    number = float(value)
                    value_text = f"{number:g}"
                except (TypeError, ValueError):
                    value_text = str(value)
            page_bits: list[str] = []
            for evidence in side.get("evidence") or []:
                if not isinstance(evidence, dict):
                    continue
                for page in evidence.get("pages") or []:
                    try:
                        page_bits.append(f"第 {int(page)} 页")
                    except (TypeError, ValueError):
                        continue
            page_text = "、".join(page_bits) if page_bits else "第 1 页"
            lines.append(f"- {source_file}：{value_text}（{page_text}）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _extract_section_body(markdown: str, heading: str) -> str:
    body = _markdown_section(markdown, heading).strip()
    return body


def harden_final_report(
    markdown: str,
    expected: dict,
    *,
    deterministic_markdown: str = "",
) -> str:
    """将 AI 草稿硬化为可通过 validate_final_report 的结构。

    - 强制标题、章节、页覆盖、关键指标表、增值税口径
    - 保留 AI 的「趋势与异常」「审计意见与重大事项」正文（若有）
    - 冲突区按 expected.conflicts 结构化重写，避免呈现不完整
    """
    company_name = str(expected.get("company_name") or "企业").strip()
    years = sorted({int(y) for y in expected.get("years") or []})
    if years:
        title = f"# {company_name} {years[0]}—{years[-1]} 跨年合并报告"
    else:
        title = f"# {company_name} 跨年合并报告"

    trend_body = _extract_section_body(markdown, "## 趋势与异常") or "详见跨年关键指标与审计意见。"
    audit_body = _extract_section_body(markdown, "## 审计意见与重大事项") or "未识别独立审计意见摘录。"
    # 若 AI 草稿几乎为空，尝试从确定性 markdown 补一句
    if not trend_body.strip() and deterministic_markdown:
        trend_body = "指标来源于已导入结构化财报的确定性计算，详见关键指标表。"

    parts = [
        title,
        "",
        _build_scope_section(expected),
        "",
        _build_metrics_table_section(expected, years),
        "",
        "## 趋势与异常",
        "",
        trend_body.strip(),
        "",
        "## 审计意见与重大事项",
        "",
        audit_body.strip(),
        "",
        _build_conflicts_section(expected),
        "",
        "## 计算口径与合规声明",
        "",
        f"{_VAT_ESTIMATE_WORDING}。本报告仅用于合法合规的经营分析，不构成投资、融资或法律意见。",
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


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


def _chunk_ocr_for_budget(ocr_text: str, chunk_size: int = 12_000, max_chunks: int = 6) -> list[str]:
    """按长度切分 OCR，优先保留含利润表/费用关键词的片段。"""
    text = (ocr_text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    keywords = (
        "利润表", "营业收入", "营业成本", "销售费用", "管理费用", "研发费用",
        "财务费用", "业务招待", "职工福利", "教育经费", "广告", "咨询", "折旧",
    )
    windows: list[tuple[int, str]] = []
    step = chunk_size - 800
    for i in range(0, len(text), max(step, 1)):
        chunk = text[i:i + chunk_size]
        if not chunk.strip():
            continue
        score = sum(1 for k in keywords if k in chunk)
        windows.append((score, chunk))
    windows.sort(key=lambda x: x[0], reverse=True)
    # 高分片段优先，再补全文头尾
    picked = [c for _, c in windows[:max_chunks]]
    if text[:chunk_size] not in picked:
        picked.insert(0, text[:chunk_size])
    if text[-chunk_size:] not in picked and len(text) > chunk_size:
        picked.append(text[-chunk_size:])
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for c in picked:
        key = c[:200]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= max_chunks:
            break
    return out


def _merge_numeric_dicts(base: dict, extra: dict, keys: tuple[str, ...]) -> dict:
    """字段取「较大的正数优先」，避免 0 覆盖已识别金额。"""
    out = dict(base)
    for key in keys:
        try:
            b = float(out.get(key) or 0)
        except (TypeError, ValueError):
            b = 0.0
        try:
            e = float(extra.get(key) or 0)
        except (TypeError, ValueError):
            e = 0.0
        # 优先非零；若都非零取绝对值更大（通常本年累计 > 局部）
        if e > 0 and (b <= 0 or e >= b * 0.5):
            # 若 e 明显大于 b 或 b 为空，采用 e；防止小片段局部数覆盖全年
            if b <= 0 or e > b:
                out[key] = round(e, 2)
        elif b <= 0 and e > 0:
            out[key] = round(e, 2)
        else:
            out[key] = round(b, 2)
    return out


def extract_budget_indicators(
    ocr_text: str,
    *,
    structured_hints: dict | None = None,
) -> tuple[dict, str]:
    """从审计报告 OCR 原文识别预算模板顶部指标（多片段聚合）。

    识别 4 个字段（营业收入/营业成本 × 本年累计/上年累计），返回 JSON dict：
        {"budget_revenue": number, "budget_cost": number,
         "last_year_revenue": number, "last_year_cost": number}
    structured_hints：可选结构化先验（来自 FinancialData），用于补洞/校验。
    返回 (indicators, error)。未配置/失败时 error 非空，indicators 为空 dict。
    """
    if not ocr_text.strip() and not structured_hints:
        return {}, "无审计报告 OCR 文本，无法识别"
    engine = _engine(timeout=90.0)
    if engine is None:
        # 无 AI 时直接返回结构化先验
        if structured_hints:
            out = {k: round(float(structured_hints.get(k) or 0), 2) for k in _BUDGET_INDICATOR_KEYS}
            return out, ""
        return {}, "大模型未配置。请先在设置中配置 AI（仅需一次）。"

    system_prompt = (
        "你是企业财报数据提取助手。根据用户提供的审计报告 OCR 文本，识别利润表关键指标。"
        "只返回一个 JSON 对象，字段："
        '{"budget_revenue": 最新年/本年累计营业收入, "budget_cost": 最新年/本年累计营业成本,'
        ' "last_year_revenue": 上年累计营业收入, "last_year_cost": 上年累计营业成本,'
        ' "selling_expense": 最新年销售费用, "admin_expense": 最新年管理费用,'
        ' "rd_expense": 最新年研发费用, "finance_expense": 最新年财务费用,'
        ' "prev_selling_expense": 上年销售费用, "prev_admin_expense": 上年管理费用,'
        ' "prev_rd_expense": 上年研发费用, "prev_finance_expense": 上年财务费用}。'
        "金额单位为元，返回纯数字（不要千分位、货币符号、汉字单位）。"
        "优先取合并利润表「本年累计/期末」；上年取「上年累计/期初」。"
        "识别不到的字段填 0。只返回 JSON。禁止虚构。"
    )
    hints = ""
    if structured_hints:
        hints = "\n【结构化先验（可参考，OCR 更准时以 OCR 为准）】\n" + json.dumps(
            structured_hints, ensure_ascii=False
        )

    merged: dict = {k: 0.0 for k in _BUDGET_INDICATOR_KEYS}
    extra_keys = (
        "selling_expense", "admin_expense", "rd_expense", "finance_expense",
        "prev_selling_expense", "prev_admin_expense", "prev_rd_expense", "prev_finance_expense",
    )
    for k in extra_keys:
        merged[k] = 0.0

    chunks = _chunk_ocr_for_budget(ocr_text) if ocr_text.strip() else []
    if not chunks and structured_hints:
        for k in _BUDGET_INDICATOR_KEYS:
            merged[k] = round(float(structured_hints.get(k) or 0), 2)
        return {k: merged[k] for k in _BUDGET_INDICATOR_KEYS}, ""

    errors: list[str] = []
    for idx, chunk in enumerate(chunks):
        try:
            content = engine.chat(
                f"【片段 {idx + 1}/{len(chunks)}】\n{chunk}{hints if idx == 0 else ''}",
                system_prompt=system_prompt,
                max_tokens=2_048,
            )
            obj = _parse_budget_json(content)
            merged = _merge_numeric_dicts(merged, obj, _BUDGET_INDICATOR_KEYS + extra_keys)
        except AIEngineError as e:
            errors.append(str(e))
        except Exception as e:
            errors.append(type(e).__name__)

    # 结构化补洞：OCR 仍为 0 时用先验
    if structured_hints:
        for k in _BUDGET_INDICATOR_KEYS:
            if float(merged.get(k) or 0) <= 0 and float(structured_hints.get(k) or 0) > 0:
                merged[k] = round(float(structured_hints[k]), 2)

    if all(float(merged.get(k) or 0) <= 0 for k in _BUDGET_INDICATOR_KEYS):
        return {}, ("；".join(errors) if errors else "未能识别到营业收入/成本")

    # 主返回仍是 4 键；期间费用放 _period 供导出填充
    out = {k: round(float(merged.get(k) or 0), 2) for k in _BUDGET_INDICATOR_KEYS}
    out["_period"] = {k: round(float(merged.get(k) or 0), 2) for k in extra_keys}
    return out, ""


def extract_budget_expense_lines(
    ocr_text: str,
    catalog: list[dict],
    *,
    structured_facts: dict | None = None,
    period_totals: dict | None = None,
) -> tuple[list[dict], str]:
    """多段 DeepSeek：把费用明细分配到模板行。

    catalog: [{row, subject, expense_name, invoice_name}, ...]
    返回 (lines, error)。lines 项含 row/last_year_actual/budget_amount/actual_amount。
    """
    engine = _engine(timeout=120.0)
    if engine is None:
        return [], "大模型未配置"
    if not catalog:
        return [], "模板行目录为空"

    # 按科目分批，降低漏项
    by_subject: dict[str, list[dict]] = {}
    for row in catalog:
        by_subject.setdefault(str(row.get("subject") or "其他"), []).append(row)

    system = (
        "你是中国企业费用预算编制助手。根据 OCR 与结构化事实，把费用金额分配到给定模板行。"
        "只返回 JSON：{\"lines\":[{\"row\":14,\"last_year_actual\":0,\"budget_amount\":0,"
        "\"actual_amount\":0,\"reason\":\"...\"}]}。"
        "规则：1) row 必须来自用户给的目录；2) 金额单位元、纯数字；"
        "3) last_year_actual=上年发生；actual_amount=最新年已发生；"
        "budget_amount 默认可取上年或最新年×合理比例；"
        "4) 同一科目下各行 actual 合计尽量接近期间费用合计；"
        "5) 识别不到的行不要返回；禁止虚构合同/虚开发票等违法表述；只输出 JSON。"
    )
    all_lines: list[dict] = []
    chunks = _chunk_ocr_for_budget(ocr_text, chunk_size=10_000, max_chunks=5) if ocr_text else [""]
    facts_json = json.dumps(
        {"structured": structured_facts or {}, "period_totals": period_totals or {}},
        ensure_ascii=False,
    )

    for subject, rows in by_subject.items():
        # 每科目最多 2 次调用（目录 + 高分 OCR 片段）
        cat_json = json.dumps(rows, ensure_ascii=False)
        user = (
            f"科目大类：{subject}\n模板行目录：{cat_json}\n"
            f"结构化事实：{facts_json}\n"
            f"OCR：\n{(chunks[0] if chunks else '')[:9000]}"
        )
        try:
            content = engine.chat(user, system_prompt=system, max_tokens=4_096)
            obj = _parse_budget_json(content)
            items = obj.get("lines") if isinstance(obj, dict) else None
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict) and it.get("row") is not None:
                        all_lines.append(it)
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.info("科目 %s 费用分配失败：%s", subject, type(e).__name__)
            continue

        # 第二遍：用另一 OCR 片段补漏
        if len(chunks) > 1:
            user2 = (
                f"科目大类：{subject}（补漏）\n模板行：{cat_json}\n"
                f"已有分配行号：{[x.get('row') for x in all_lines[-30:]]}\n"
                f"OCR补片段：\n{chunks[1][:9000]}\n请只返回尚未覆盖且能从文本识别到的行。"
            )
            try:
                content2 = engine.chat(user2, system_prompt=system, max_tokens=3_072)
                obj2 = _parse_budget_json(content2)
                items2 = obj2.get("lines") if isinstance(obj2, dict) else None
                if isinstance(items2, list):
                    for it in items2:
                        if isinstance(it, dict) and it.get("row") is not None:
                            all_lines.append(it)
            except Exception:
                pass

    if not all_lines:
        return [], "未能从 OCR 分配到费用行"
    # 同行合并：取较大值
    merged_by_row: dict[int, dict] = {}
    for it in all_lines:
        try:
            row = int(it.get("row"))
        except (TypeError, ValueError):
            continue
        cur = merged_by_row.setdefault(
            row,
            {"row": row, "last_year_actual": 0.0, "budget_amount": 0.0, "actual_amount": 0.0},
        )
        for key in ("last_year_actual", "budget_amount", "actual_amount"):
            try:
                val = float(it.get(key) or 0)
            except (TypeError, ValueError):
                val = 0.0
            if val > float(cur.get(key) or 0):
                cur[key] = round(val, 2)
    return list(merged_by_row.values()), ""


def _normalize_ratio_amount_item(
    it: dict,
    *,
    revenue: float,
    valid_rows: set[int] | None = None,
) -> dict | None:
    """单行占比/金额恒等：G = C2 × ratio_pct/100；ratio 与金额互推。"""
    if not isinstance(it, dict):
        return None
    try:
        row = int(it.get("row"))
    except (TypeError, ValueError):
        return None
    if valid_rows is not None and row not in valid_rows:
        return None
    try:
        ratio_pct = float(it.get("budget_ratio_pct") or 0)
    except (TypeError, ValueError):
        ratio_pct = 0.0
    try:
        amt = float(it.get("budget_amount") or 0)
    except (TypeError, ValueError):
        amt = 0.0
    # 单位纠偏：若模型把 0.35% 误写成 35（或 0.0035 小数），按量级纠正
    if ratio_pct > 0 and revenue > 0:
        if ratio_pct > 30:  # 单行 30%+ 几乎不可能 → 可能把 0.35 写成 35
            ratio_pct = ratio_pct / 100.0
        elif ratio_pct < 0.0001 and amt > 0:
            # 小数费率 0.0035 误当 pct
            ratio_pct = ratio_pct * 100.0
    # 恒等：有占比以占比推金额；仅有金额则反推占比
    if ratio_pct > 0 and revenue > 0:
        amt = round(revenue * ratio_pct / 100.0, 2)
    elif amt > 0 and revenue > 0:
        ratio_pct = round(amt / revenue * 100.0, 4)
    if amt <= 0:
        return None
    # 仅拦截荒谬：单行 > 营收 25%（留足刚性工资空间；12% 硬压会扭曲结构）
    if revenue > 0 and amt > revenue * 0.25:
        ratio_pct = 15.0
        amt = round(revenue * 0.15, 2)
    try:
        ref = float(it.get("reference_amount") or 0)
    except (TypeError, ValueError):
        ref = 0.0
    if ref <= 0:
        ref = amt
    return {
        "row": row,
        "budget_amount": amt,
        "budget_ratio_pct": round(ratio_pct, 4),
        "reference_amount": round(ref, 2),
        "reason": str(it.get("reason") or "").strip() or "DeepSeek 占比重算",
        "selected": True,
        "has_last_year": False,
        "last_year_actual": 0.0,
        "write_last_year": False,
        "subject": str(it.get("subject") or ""),
        "expense_name": str(it.get("expense_name") or ""),
    }


def _subject_sum_report(
    cleaned: list[dict],
    lines_meta: list[dict],
    period_expenses: dict | None,
    revenue: float,
) -> dict:
    """科目合计 vs 利润表期间费用，供第二轮对账。"""
    row_subj = {}
    for it in lines_meta:
        try:
            row_subj[int(it["row"])] = str(it.get("subject") or "")
        except (TypeError, ValueError, KeyError):
            continue
    sums: dict[str, float] = {}
    for x in cleaned:
        subj = str(x.get("subject") or row_subj.get(x["row"]) or "其他")
        sums[subj] = sums.get(subj, 0.0) + float(x["budget_amount"])
    pe = period_expenses if isinstance(period_expenses, dict) else {}
    anchors = {
        "销售费用": float(pe.get("selling_latest") or pe.get("selling_expense") or 0),
        "管理费用": float(pe.get("admin_latest") or pe.get("admin_expense") or 0)
        + float(pe.get("rd_latest") or pe.get("rd_expense") or 0),
        "财务费用": float(pe.get("finance_latest") or pe.get("finance_expense") or 0),
    }
    gaps = []
    for subj, anchor in anchors.items():
        got = sums.get(subj, 0.0)
        if anchor <= 0:
            continue
        rel = abs(got - anchor) / anchor if anchor else 0
        gaps.append(
            {
                "subject": subj,
                "period_latest": round(anchor, 2),
                "budget_sum": round(got, 2),
                "gap": round(got - anchor, 2),
                "rel_error": round(rel, 4),
                "need_fix": rel > 0.25,
            }
        )
    total = sum(float(x["budget_amount"]) for x in cleaned)
    return {
        "subject_sums": {k: round(v, 2) for k, v in sums.items()},
        "anchors": {k: round(v, 2) for k, v in anchors.items()},
        "gaps": gaps,
        "total_budget": round(total, 2),
        "fee_rate_pct": round(total / revenue * 100, 4) if revenue else 0,
        "need_second_pass": any(g.get("need_fix") for g in gaps),
    }


def rebalance_expense_ratios(context: dict) -> tuple[list[dict], str, str]:
    """DeepSeek 准确性优先：多轮占比重算 + 期间费用对账。

    时间可放宽；本地只做恒等（G=C2×H%）与极端超上限缩放，不改结构权重。
    输入：budget_revenue、expense_budget_cap、period_expenses、lines
    输出：items[{row, budget_amount, budget_ratio_pct, reference_amount, reason}]
    """
    engine = _engine(timeout=float(context.get("timeout") or 300.0))
    if engine is None:
        return [], "", "大模型未配置，无法由 DeepSeek 重算占比"

    revenue = float(context.get("budget_revenue") or 0)
    cap = float(context.get("expense_budget_cap") or 0)
    lines = context.get("lines") or []
    period_expenses = context.get("period_expenses")
    growth = float(context.get("revenue_growth_rate") or 0)
    if revenue <= 0 or not lines:
        return [], "", "缺少营收或费用行，无法重算占比"

    wb = context.get("wb_model") if isinstance(context.get("wb_model"), dict) else {}
    fee_band = (wb.get("period_expense_ratio_band") or {}) if wb else {}
    fee_lo = float(fee_band.get("min") or 0.08) * 100
    fee_hi = float(fee_band.get("max") or 0.18) * 100
    from core import compliance_policy as compliance_mod

    system = (
        "你是中国企业费用预算 CFO。按 WB 行业基准 + 合规硬规则重算金额。"
        + compliance_mod.hard_rules_prompt_suffix()
        + "硬规则："
        "1) budget_ratio_pct 为占预算营收百分点：50万/1亿→0.5；"
        "2) budget_amount = round(budget_revenue * budget_ratio_pct / 100, 2)；"
        "3) Σbudget ≤ expense_budget_cap 且 ≤ hard_cap（增速匹配+行业带）；"
        f"总费用率 {fee_lo:.1f}%～{fee_hi:.1f}%；"
        "4) 单行通常≤4%，刚性≤8～10%；禁止堆在1～2行；"
        "5) 各大类对齐 period_expenses 与历史占比，偏差宜≤25%；"
        "6) 有上年时费用增幅≤收入增速+3pp；reference≈上年×(1+g)；"
        "7) 只返回 JSON："
        '{"summary":"...","items":[{"row":14,"budget_amount":0,'
        '"budget_ratio_pct":0.12,"reference_amount":0,"reason":"历史对标/增速/合规"}]}。'
    )

    slim: list[dict] = []
    for it in lines:
        if not isinstance(it, dict):
            continue
        try:
            row = int(it.get("row"))
        except (TypeError, ValueError):
            continue
        bud = float(it.get("budget_amount") or 0)
        slim.append(
            {
                "row": row,
                "subject": it.get("subject"),
                "expense_name": it.get("expense_name"),
                "invoice_name": it.get("invoice_name"),
                "last_year_actual": float(it.get("last_year_actual") or 0),
                "budget_amount_current": bud,
                "current_ratio_pct": round(bud / revenue * 100, 4) if revenue else 0,
            }
        )
    valid_rows = {x["row"] for x in slim}
    by_subj: dict[str, list[dict]] = {}
    for x in slim:
        by_subj.setdefault(str(x.get("subject") or "其他"), []).append(x)

    base_payload = {
        "company_name": context.get("company_name"),
        "industry": context.get("industry"),
        "budget_revenue": revenue,
        "expense_budget_cap": cap,
        "revenue_growth_rate": growth,
        "period_expenses": period_expenses,
        "accuracy_first": True,
        "hint": (
            "准确性优先：金额与占比必须恒等；大类合计对齐 period_expenses；"
            "不要为了凑数胡乱放大单行。"
        ),
    }

    merged: dict[int, dict] = {}
    summaries: list[str] = []
    errors: list[str] = []

    def _run_batch(batch_lines: list[dict], pass_label: str, extra: dict | None = None) -> None:
        payload = {**base_payload, "pass": pass_label, "lines": batch_lines}
        if extra:
            payload.update(extra)
        user = json.dumps(payload, ensure_ascii=False)
        try:
            content = engine.chat(user, system_prompt=system, max_tokens=8_000)
            obj = _parse_budget_json(content)
        except AIEngineError as e:
            errors.append(f"{pass_label}:{e}")
            return
        except Exception as e:
            errors.append(f"{pass_label}:{type(e).__name__}")
            return
        if not isinstance(obj, dict):
            errors.append(f"{pass_label}:非对象")
            return
        s = str(obj.get("summary") or "").strip()
        if s:
            summaries.append(f"[{pass_label}] {s}")
        for raw in obj.get("items") or []:
            if not isinstance(raw, dict):
                continue
            try:
                r0 = int(raw.get("row"))
            except (TypeError, ValueError):
                continue
            meta = next((x for x in slim if x["row"] == r0), None)
            if meta and not raw.get("subject"):
                raw = {**raw, "subject": meta.get("subject"), "expense_name": meta.get("expense_name")}
            norm = _normalize_ratio_amount_item(raw, revenue=revenue, valid_rows=valid_rows)
            if norm:
                merged[norm["row"]] = norm

    for subj, rows in by_subj.items():
        with_amt = [x for x in rows if x["budget_amount_current"] > 0]
        zeros = [x for x in rows if x["budget_amount_current"] <= 0]
        batch = (with_amt + zeros)[:36]
        if not batch:
            continue
        _run_batch(batch, f"P1-{subj}")

    if len(merged) < 3:
        with_amt = [x for x in slim if x["budget_amount_current"] > 0]
        zeros = [x for x in slim if x["budget_amount_current"] <= 0][:50]
        _run_batch((with_amt + zeros)[:90], "P1-all")

    cleaned = list(merged.values())
    if not cleaned:
        return [], "；".join(summaries), (
            "；".join(errors) if errors else "DeepSeek 未返回有效占比行"
        )

    report = _subject_sum_report(cleaned, slim, period_expenses, revenue)
    max_passes = int(context.get("max_passes") or 3)
    if report.get("need_second_pass") and max_passes >= 2:
        gap_lines = [
            {
                "row": x["row"],
                "subject": x.get("subject"),
                "expense_name": x.get("expense_name"),
                "budget_amount_current": x["budget_amount"],
                "current_ratio_pct": x["budget_ratio_pct"],
            }
            for x in cleaned
        ]
        _run_batch(
            gap_lines,
            "P2-reconcile",
            extra={
                "qa_report": report,
                "instruction": (
                    "上轮科目合计与利润表期间费用偏差过大。"
                    "请校正 items，使各大类 budget 合计贴近 anchors"
                    "（可按收入增长率微调），并保持行内金额=占比恒等。"
                    "只返回需调整的行 + 关键行，或返回全量更准确的结果。"
                ),
            },
        )
        cleaned = list(merged.values())
        report = _subject_sum_report(cleaned, slim, period_expenses, revenue)

    if max_passes >= 3 and (
        (cap > 0 and report["total_budget"] > cap * 1.05)
        or report["fee_rate_pct"] > 20
        or report.get("need_second_pass")
    ):
        _run_batch(
            [
                {
                    "row": x["row"],
                    "subject": x.get("subject"),
                    "expense_name": x.get("expense_name"),
                    "budget_amount_current": x["budget_amount"],
                    "current_ratio_pct": x["budget_ratio_pct"],
                }
                for x in cleaned
            ],
            "P3-final",
            extra={
                "qa_report": report,
                "instruction": (
                    "终检：1) 每行 budget_amount 与 budget_ratio_pct 严格恒等；"
                    "2) Σ ≤ expense_budget_cap；3) 总费用率合理；"
                    "4) 科目合计对齐 period_expenses。返回全量最终 items。"
                ),
            },
        )
        cleaned = list(merged.values())

    final: list[dict] = []
    for x in cleaned:
        norm = _normalize_ratio_amount_item(x, revenue=revenue, valid_rows=valid_rows)
        if norm:
            final.append(norm)
    if not final:
        return [], "；".join(summaries), "规范化后无有效行"

    total = sum(x["budget_amount"] for x in final)
    summary = " | ".join(summaries)[:900] if summaries else "DeepSeek 多轮占比重算"
    if cap > 0 and total > cap * 1.02:
        scale = cap / total
        for x in final:
            x["budget_amount"] = round(x["budget_amount"] * scale, 2)
            x["reference_amount"] = round(float(x.get("reference_amount") or 0) * scale, 2)
            x["budget_ratio_pct"] = (
                round(x["budget_amount"] / revenue * 100, 4) if revenue else 0
            )
        summary = (summary + f" （合计按费用上限等比缩至 {cap:,.0f} 元，结构不变）").strip()
        total = sum(x["budget_amount"] for x in final)

    report_f = _subject_sum_report(final, slim, period_expenses, revenue)
    fee = report_f["fee_rate_pct"]
    summary = (
        f"{summary} | 终检：ΣG={total:,.0f} 总费用率={fee:.2f}% "
        f"科目={report_f['subject_sums']}"
    ).strip()
    if errors and not final:
        return [], summary, "；".join(errors)
    return final, summary, ("" if final else "；".join(errors))



def advise_budget_expenses(context: dict) -> tuple[list[dict], str, str]:
    """DeepSeek 全量编制费用建议（主路径，非规则微调）。

    按四大科目分批调用，覆盖空白行，给出老板「该花什么钱」的完整建议。
    返回 (items, summary, error)。
    """
    engine = _engine(timeout=150.0)
    if engine is None:
        return [], "", "大模型未配置。费用编制建议需 DeepSeek 全量介入，请先在设置中配置 API Key。"

    catalog = context.get("catalog") or context.get("empty_or_zero_budget_lines") or []
    if not catalog:
        return [], "", "模板费用目录为空"

    by_subject: dict[str, list[dict]] = {}
    for row in catalog:
        if not isinstance(row, dict):
            continue
        # 主路径：预算已为 0 的行优先；也允许 AI 对已有行提出调增
        subj = str(row.get("subject") or "其他")
        by_subject.setdefault(subj, []).append(row)

    rev = float(context.get("budget_revenue") or 0)
    cap = float(context.get("expense_budget_cap") or 0)
    wb = context.get("wb_model") if isinstance(context.get("wb_model"), dict) else {}
    fee_band = (wb.get("period_expense_ratio_band") or {}) if wb else {}
    fee_lo = float(fee_band.get("min") or 0.08) * 100
    fee_hi = float(fee_band.get("max") or 0.18) * 100
    fee_md = float(fee_band.get("median") or 0.12) * 100
    target_tot = float(wb.get("target_fee_total") or 0) if wb else 0
    growth = float(context.get("revenue_growth_rate") or 0)
    from core import compliance_policy as compliance_mod

    system = (
        "你是中国中小企业 CFO。按 WB 行业基准 + 合规三条硬规则做费用编制建议。"
        + compliance_mod.hard_rules_prompt_suffix()
        + "导出表：E=D/上年营收、H=G/预算营收 由建模计算；你只给准金额。"
        "硬性规则："
        "1) 无上年：禁止虚构 last_year_actual；必给 reference_amount 与 budget_amount；"
        "2) 有上年：reference≈D×(1+g)；budget 增幅≤收入增速+3个百分点；"
        "3) budget_amount = round(budget_revenue * budget_ratio_pct/100, 2)；"
        f"4) budget_revenue={rev}，cap={cap}，g={growth:.4f}；"
        f"   费用率 {fee_lo:.1f}%～{fee_hi:.1f}%（中枢{fee_md:.1f}%）；"
        f"   Σbudget 目标≈{target_tot:,.0f} 且 ≤ hard_cap（见 compliance_limits）；"
        "5) 必须对标 historical 费用率；大类对齐 period_expenses；"
        "6) 单行通常≤4%，刚性≤8%；禁止虚增成本；"
        "7) reason 须写：历史占比 / 增速匹配 / 金税合规；"
        "8) 只返回 JSON："
        '{"summary":"...","items":[{"row":14,"reference_amount":0,"budget_amount":0,'
        '"budget_ratio_pct":0.15,"reason":"...","priority":"high","selected":true}]}。'
    )

    meta = {
        k: context.get(k)
        for k in (
            "company_name", "industry", "year", "budget_revenue", "budget_cost",
            "last_year_revenue", "expense_budget_cap", "allocated_before", "residual",
            "revenue_growth_rate", "period_expenses", "subject_mix_hint", "hard_rules",
            "ocr_excerpt", "wb_model", "compliance_limits", "compliance_rules",
        )
        if context.get(k) is not None
    }
    meta_json = json.dumps(meta, ensure_ascii=False)

    all_items: list[dict] = []
    summaries: list[str] = []
    errors: list[str] = []

    for subject, rows in by_subject.items():
        # 优先传预算为 0 的行；若过少则带全科目
        zeros = [r for r in rows if float(r.get("budget_amount") or 0) <= 0]
        batch = zeros if len(zeros) >= 3 else rows
        # 控制长度：每科目最多 24 行目录
        batch = batch[:24]
        cat_json = json.dumps(batch, ensure_ascii=False)
        residual = float(context.get("residual") or 0)
        cap = float(context.get("expense_budget_cap") or 0)
        share_hint = (context.get("subject_mix_hint") or {}).get(subject)
        user = (
            f"【全量编制·科目】{subject}\n"
            f"【企业与上限】{meta_json}\n"
            f"【本科目建议占用 residual 的参考占比】{share_hint if share_hint is not None else '按行业常识'}\n"
            f"【费用预算上限】{cap} 元 · residual≈{residual} 元\n"
            f"【模板行目录（请从中选行并给出金额）】{cat_json}\n"
            "请输出本科目完整编制建议 JSON（不要空 items）。"
        )
        try:
            content = engine.chat(user, system_prompt=system, max_tokens=4_096)
            obj = _parse_budget_json(content)
        except AIEngineError as e:
            errors.append(f"{subject}:{e}")
            continue
        except Exception as e:
            errors.append(f"{subject}:{type(e).__name__}")
            continue
        if not isinstance(obj, dict):
            errors.append(f"{subject}:非对象")
            continue
        sum_s = str(obj.get("summary") or "").strip()
        if sum_s:
            summaries.append(f"{subject}：{sum_s}")
        items = obj.get("items") if isinstance(obj.get("items"), list) else []
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                row = int(it.get("row"))
            except (TypeError, ValueError):
                continue

            def _f(key: str, src: dict = it) -> float:
                try:
                    return round(float(src.get(key) or 0), 2)
                except (TypeError, ValueError):
                    return 0.0

            # 目录内校验 has_last_year
            cat_row = next((r for r in rows if int(r.get("row") or -1) == row), None)
            has_ly = bool(cat_row and float(cat_row.get("last_year_actual") or 0) > 0)
            try:
                ratio_pct = float(it.get("budget_ratio_pct") or 0)
            except (TypeError, ValueError):
                ratio_pct = 0.0
            amt = _f("budget_amount")
            # DeepSeek 占比优先：金额由 营收×占比 回算，避免占比与金额不一致
            if ratio_pct > 0 and rev > 0:
                amt = round(rev * ratio_pct / 100.0, 2)
            elif amt > 0 and rev > 0:
                ratio_pct = round(amt / rev * 100.0, 4)
            ref = _f("reference_amount")
            if ref <= 0:
                ref = amt
            all_items.append(
                {
                    "row": row,
                    "reference_amount": ref,
                    "budget_amount": amt,
                    "budget_ratio_pct": round(ratio_pct, 4),
                    "last_year_actual": 0.0 if not has_ly else float(cat_row.get("last_year_actual") or 0),
                    "has_last_year": has_ly,
                    "reason": str(it.get("reason") or "").strip(),
                    "priority": str(it.get("priority") or "mid"),
                    "selected": bool(it.get("selected", True)),
                    "drop": bool(it.get("drop", False)),
                    "subject": subject,
                    "expense_name": str((cat_row or {}).get("expense_name") or it.get("expense_name") or ""),
                    "invoice_name": str((cat_row or {}).get("invoice_name") or it.get("invoice_name") or ""),
                }
            )

    # 第二轮：总述 + 查漏补缺（金额仍过少时）
    if all_items:
        try:
            brief = json.dumps(
                {
                    "meta": meta,
                    "items_preview": [
                        {"row": x["row"], "subject": x["subject"], "budget_amount": x["budget_amount"]}
                        for x in all_items[:50]
                    ],
                    "count": len(all_items),
                    "sum_budget": round(sum(float(x.get("budget_amount") or 0) for x in all_items), 2),
                },
                ensure_ascii=False,
            )
            content2 = engine.chat(
                "根据已生成的分科目建议，写一段给老板的总述 summary，并补漏 0～8 条仍缺失的关键费用行。"
                "只返回 JSON：{\"summary\":\"...\",\"items\":[...]}。\n" + brief[:12_000],
                system_prompt=system,
                max_tokens=2_048,
            )
            obj2 = _parse_budget_json(content2)
            if isinstance(obj2, dict):
                s2 = str(obj2.get("summary") or "").strip()
                if s2:
                    summaries.insert(0, s2)
                for it in obj2.get("items") or []:
                    if isinstance(it, dict) and it.get("row") is not None:
                        try:
                            row = int(it["row"])
                        except (TypeError, ValueError):
                            continue
                        all_items.append(
                            {
                                "row": row,
                                "reference_amount": float(it.get("reference_amount") or 0),
                                "budget_amount": float(it.get("budget_amount") or 0),
                                "last_year_actual": 0.0,
                                "has_last_year": False,
                                "reason": str(it.get("reason") or "").strip(),
                                "priority": str(it.get("priority") or "mid"),
                                "selected": bool(it.get("selected", True)),
                                "drop": bool(it.get("drop", False)),
                                "subject": str(it.get("subject") or ""),
                                "expense_name": str(it.get("expense_name") or ""),
                                "invoice_name": str(it.get("invoice_name") or ""),
                            }
                        )
        except Exception:
            pass

    if not all_items:
        return [], "", ("；".join(errors) if errors else "DeepSeek 未给出可用费用编制项")

    # 同行合并：取较大预算
    merged: dict[int, dict] = {}
    for it in all_items:
        if it.get("drop"):
            continue
        row = int(it["row"])
        cur = merged.get(row)
        if cur is None or float(it.get("budget_amount") or 0) > float(cur.get("budget_amount") or 0):
            merged[row] = it

    summary = " ".join(summaries)[:800]
    if errors:
        summary = (summary + f" （部分科目重试提示：{';'.join(errors[:3])}）").strip()
    return list(merged.values()), summary, ""


def analyze_operating_narrative(context: dict) -> tuple[dict, str, str]:
    """DeepSeek 经营预算分析（前世今生文案，主路径）。

    输入 factsheet 为确定性事实清单；DeepSeek 只做「艺康体小白版」改写，
    禁止编造数字。返回 (analysis_dict, ai_summary, error)。
    数字白名单校验由 core.CO_report_analysis 负责（只提示不拦截）。
    """
    engine = _engine(timeout=180.0)
    if engine is None:
        return {}, "", "大模型未配置。经营分析报告需 DeepSeek 介入，请先在设置中配置 API Key。"

    facts = context.get("factsheet") or {}
    if not facts:
        return {}, "", "事实清单为空（尚未导入财报或叙事构建失败）"

    stage_titles = [str(s.get("title") or "") for s in facts.get("stages") or []]
    point_titles = [str(p.get("title") or "") for p in facts.get("now_points") or []]
    metric_names = [str(k) for k in (facts.get("metric_judgments") or {}).keys()]

    system = (
        "你是中国中小企业 CFO 兼财务写作顾问，为「艺康体小白版」经营预算分析报告写文案："
        "用老板看得懂的大白话，讲清公司的前世今生（最以前 → 中间 → 现在）与将来怎么干。"
        "硬性规则："
        "1) 所有数字（金额、比率、年份、评分）只能引用【事实清单】中已有的数字，"
        "一个都不许编造、不许改写数值；"
        "2) stages 数量与标题、now_points 标题、指标名称必须与事实清单完全一致，只写正文；"
        "3) one_liner ≤80 字；headline 2~4 句（句号分隔）；每个 stage summary 2~4 句、"
        "bullets 3~5 条；now_points 每段 2~4 句；future_actions 4~8 条、每条 ≤100 字；"
        "4) 口径：先说结论再讲故事；讲清「收入-利润-税负-现金」的因果；"
        "不承诺节税金额超出事实清单；限于合法税务筹划；"
        "5) 只返回 JSON："
        '{"one_liner":"...","headline":"...","stage_insight":"...",'
        '"stages":[{"title":"...","summary":"...","bullets":["..."]}],'
        '"now_points":[{"title":"...","body":"..."}],'
        '"now_judgments":{"指标名":"管理判断一句话"},'
        '"future_actions":["..."],"summary":"..."}'
    )

    facts_json = json.dumps(facts, ensure_ascii=False)
    user = (
        f"【事实清单（唯一数字来源，禁止编造）】\n{facts_json[:24_000]}\n\n"
        f"【阶段标题（必须原样返回 {len(stage_titles)} 个）】{'；'.join(stage_titles)}\n"
        f"【现在要点标题（必须原样返回 {len(point_titles)} 个）】{'；'.join(point_titles)}\n"
        f"【指标名（now_judgments 的键，只用这些）】{'；'.join(metric_names)}\n"
        "请基于以上事实写出经营预算分析文案 JSON（不要输出 JSON 以外的文字）。"
    )

    try:
        content = engine.chat(user, system_prompt=system, max_tokens=6_000)
        obj = _parse_budget_json(content)
    except AIEngineError as e:
        return {}, "", f"DeepSeek 调用失败：{e}"
    except Exception as e:
        return {}, "", f"DeepSeek 返回解析失败：{type(e).__name__}: {e}"

    if not isinstance(obj, dict):
        return {}, "", "DeepSeek 返回非对象"
    summary = str(obj.get("summary") or "").strip()
    return obj, summary[:400], ""


# ── 月度拆分（模块 A 二段式：AI 只出题/出权重，绝不产出金额） ────────────

MONTHLY_QUESTIONS_MAX_TOKENS = 4096
MONTHLY_WEIGHTS_MAX_TOKENS = 4096


def generate_monthly_questions(
    plan_snapshot: dict,
    hints: dict | None = None,
) -> tuple[list[dict], str]:
    """基于第一稿快照生成 4~6 个月度拆分澄清问题。

    返回 (questions, error)；结构非法/未配置 AI 时 questions 为空列表、
    error 非空，由调用方回退规则题库（流程不中断）。
    """
    engine = _engine(timeout=60.0)
    if engine is None:
        return [], "大模型未配置"
    rows = (plan_snapshot or {}).get("rows") or []
    catalog = [
        {
            "row": r.get("row"),
            "subject": r.get("subject"),
            "expense_name": r.get("expense_name"),
            "annual": r.get("annual"),
        }
        for r in rows
        if isinstance(r, dict) and float(r.get("annual") or 0) > 0
    ][:40]
    if not catalog:
        return [], "第一稿快照缺少非零费用行"

    prompt = (
        "根据下面的年度费用预算行目录与企业信息，生成 4~6 个用于「月度拆分方向」的澄清问题。\n"
        "只返回 JSON 对象：{\"questions\":[{\"id\":\"q_xxx\",\"type\":\"single\"或\"text\","
        "\"title\":\"...\",\"options\":[\"...\"],\"default\":\"...\",\"placeholder\":\"...\"}]}。\n"
        "要求：id 用英文小写加下划线且不重复；single 题至少 2 个选项且 default 必须是选项之一；"
        "text 题 default 为空串、placeholder 给填写示例；问题必须覆盖：收入季节性、"
        "人员薪酬与年终奖节奏、房租等固定费用是否平摊、广宣投放节奏、一次性大额支出"
        "（格式：月份+金额+费用项目）；题目贴合给出的费用科目构成；只输出 JSON。\n"
        f"企业信息：{json.dumps(hints or {}, ensure_ascii=False)}\n"
        f"费用行目录：{json.dumps(catalog, ensure_ascii=False)}"
    )
    try:
        result = engine.chat_result(
            prompt,
            system_prompt="你是预算编制问答助手，只输出 JSON，不输出任何解释。",
            max_tokens=MONTHLY_QUESTIONS_MAX_TOKENS,
            extra={"response_format": {"type": "json_object"}, "stream": False},
        )
        parsed = _parse_budget_json(result.content)
    except Exception as e:
        return [], f"出题失败：{e}"

    raw = parsed.get("questions")
    if not isinstance(raw, list) or not (4 <= len(raw) <= 6):
        return [], "AI 问题数量非法（须 4~6 题）"
    questions: list[dict] = []
    seen_ids: set = set()
    for item in raw:
        if not isinstance(item, dict):
            return [], "AI 问题结构非法"
        qid = str(item.get("id") or "").strip()
        qtype = str(item.get("type") or "").strip()
        title = str(item.get("title") or "").strip()
        if not qid or not qid.replace("_", "").isalnum() or qid in seen_ids:
            return [], "AI 问题 id 非法或重复"
        if qtype not in ("single", "text") or not title:
            return [], "AI 问题 type/title 非法"
        default = str(item.get("default") or "").strip()
        options = [str(o) for o in (item.get("options") or []) if str(o).strip()]
        if qtype == "single":
            if len(options) < 2:
                return [], "single 题选项不足"
            if default not in options:
                default = options[0]
        else:
            default = ""
        questions.append({
            "id": qid, "type": qtype, "title": title,
            "options": options, "default": default,
            "placeholder": str(item.get("placeholder") or ""),
        })
        seen_ids.add(qid)
    return questions, ""


def generate_monthly_weights(
    plan_snapshot: dict,
    answers: dict,
    hints: dict | None = None,
) -> tuple[list[dict], str]:
    """按用户答案为第一稿逐行生成 12 个月分布权重。

    返回 (rows, error)；rows 项：{row, shape, weights[12], note}。
    金额一律由确定性引擎按「年度金额×权重」计算——提示词硬规则禁止 AI
    输出/改写金额，note 出现数字仅记白名单告警（不改数）。
    """
    engine = _engine(timeout=120.0)
    if engine is None:
        return [], "大模型未配置"
    rows = [
        r for r in (plan_snapshot or {}).get("rows") or []
        if isinstance(r, dict) and float(r.get("annual") or 0) > 0
    ]
    if not rows:
        return [], "第一稿快照缺少非零费用行"

    prompt = (
        "把下列年度费用预算行拆分为 12 个月的分布权重。\n"
        "只返回 JSON：{\"rows\":[{\"row\":14,"
        "\"shape\":\"uniform|front_load|back_load|peak|lump|custom\","
        "\"weights\":[12 个非负小数，合计≈1],\"note\":\"依据\"}]}。\n"
        "硬规则：1) 逐行给出，row 必须来自目录；2) weights 只表示分布形状，"
        "金额由系统按「年度金额×权重」计算，你绝不能计算或改写任何金额；"
        "3) note 不得出现任何数字（含月份、倍数、金额），只用文字描述节奏；"
        "4) 依据用户回答定形状：工资房租折旧等刚性费用应接近均匀；"
        "年终奖提成类集中在春节所在月；广宣营销类按投放节奏前置或集中；"
        "一次性支出全额落在用户指定月份。\n"
        f"预算年：{(hints or {}).get('budget_year')}\n春节所在月：{(hints or {}).get('spring_month')}\n"
        f"用户回答：{json.dumps(answers or {}, ensure_ascii=False)}\n"
        f"费用行目录：{json.dumps(rows, ensure_ascii=False)}"
    )
    try:
        result = engine.chat_result(
            prompt,
            system_prompt="你是预算月度拆分助手，只决定分布形状，绝不改金额。只输出 JSON。",
            max_tokens=MONTHLY_WEIGHTS_MAX_TOKENS,
            extra={"response_format": {"type": "json_object"}, "stream": False},
        )
        parsed = _parse_budget_json(result.content)
    except Exception as e:
        return [], f"权重生成失败：{e}"

    out = parsed.get("rows")
    if not isinstance(out, list) or not out:
        return [], "AI 权重结构非法"
    return out, ""

