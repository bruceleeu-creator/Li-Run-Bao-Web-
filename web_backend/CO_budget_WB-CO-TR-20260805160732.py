"""利润宝 · 模板工作台预算 API 路由。

从已导入的会话数据（FinancialData）自动提取审计报告指标，
生成模板工作台的预算计划（TopInputs 顶部输入）。
优先用 AI 从 OCR 原文识别（更准），未配置/失败回退结构化映射。
复用 core/budget.py 的 make_empty_plan，不重写预算逻辑。

另：导出后「费用编制建议」问答（规则算法 + DeepSeek）与应用回写导出。
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import budget as budget_mod

session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
budget_export = importlib.import_module("core.CO_budget_export_WB-CO-TR-20260810")
budget_advice = importlib.import_module("core.CO_budget_advice_WB-CO-TR-20260810")
interaction = importlib.import_module("web_backend.CO_interaction_WB-CO-TR-20260805160732")

router = APIRouter(prefix="/api/budget", tags=["budget"])

_EXPORT_DIR = Path(__file__).resolve().parent / "workspaces" / "exports"
_last_advice: dict[str, Any] = {}


def _amount(data, account: str, year: int) -> float:
    """从 FinancialData 取某科目某年金额，缺失返回 0.0。"""
    vals = (data.income_statement.get(account) or {})
    val = vals.get(year)
    return float(val) if val else 0.0


def _fill_top_inputs(ti, values: dict) -> None:
    """把识别/提取的 4 个金额字段填入 TopInputs。"""
    ti.budget_revenue = float(values.get("budget_revenue", 0.0) or 0.0)
    ti.budget_cost = float(values.get("budget_cost", 0.0) or 0.0)
    ti.last_year_revenue = float(values.get("last_year_revenue", 0.0) or 0.0)
    ti.last_year_cost = float(values.get("last_year_cost", 0.0) or 0.0)


def _fallback_values(data, latest_year: int, prev_year: int) -> dict:
    """结构化回退：从 FinancialData 直接映射 4 字段。"""
    return {
        "budget_revenue": _amount(data, "营业收入", latest_year),
        "budget_cost": _amount(data, "营业成本", latest_year),
        "last_year_revenue": _amount(data, "营业收入", prev_year),
        "last_year_cost": _amount(data, "营业成本", prev_year),
    }


@router.post("/from-session")
def budget_from_session() -> dict:
    """从会话数据生成预算计划。

    优先用 AI 从审计报告 OCR 原文识别 4 个顶部指标；AI 未配置或识别失败时
    回退结构化映射。响应 method 标记 "ai" 或 "fallback"。
    """
    data = session.get_data()
    if data is None:
        raise HTTPException(status_code=400, detail="尚未导入财报，请先导入后再打开模板工作台")

    years = sorted(data.years)
    latest_year = years[-1] if years else 0
    prev_year = years[-2] if len(years) >= 2 else latest_year

    plan = budget_mod.make_empty_plan(
        company_name=data.company_name,
        industry=data.industry,
        year=latest_year,
    )
    ti = plan.top_inputs

    # 优先 AI：从 OCR 原文识别（即使 years 为空也可识别本年/上年指标）
    method = "fallback"
    if latest_year:
        source_note = (
            f"结构化提取 · {latest_year} 年（本年累计）；上年同期取自 {prev_year}"
        )
    else:
        source_note = "结构化提取（无年份数据）"
    ocr_texts = session.get_ocr_texts()
    if ocr_texts:
        ai_indicators, ai_error = ai_mod.extract_budget_indicators("\n".join(ocr_texts))
        if ai_indicators:
            _fill_top_inputs(ti, ai_indicators)
            method = "ai"
            if latest_year:
                source_note = (
                    f"AI 识别 · {latest_year} 年（本年累计）；上年同期取自 {prev_year}"
                )
            else:
                source_note = "AI 识别 · 年份由 AI 从审计报告判断"
            source_note = (
                f"AI 识别 · {latest_year} 年（本年累计）；上年同期取自 {prev_year}"
            )
        else:
            # AI 未配置或识别失败 → 回退结构化
            _fill_top_inputs(ti, _fallback_values(data, latest_year, prev_year))
            if ai_error and "未配置" not in ai_error:
                source_note = f"AI 识别失败（{ai_error}），已回退结构化提取"
    else:
        _fill_top_inputs(ti, _fallback_values(data, latest_year, prev_year))

    # E2/E3/E4：统一读 pipeline PolicySnapshot（会话导入时已写入 parsed_meta）
    try:
        from core import pipeline as pipeline_mod

        policy = pipeline_mod.policy_from_data(data)
        rate_notes = pipeline_mod.apply_policy_to_plan(plan, policy)
        if rate_notes:
            source_note = source_note + " · " + "；".join(rate_notes)
    except Exception:
        pass

    dq = (getattr(data, "parsed_meta", None) or {}).get("data_quality") or {}
    policy_dict = (getattr(data, "parsed_meta", None) or {}).get("policy") or {}

    return {
        "plan": {
            "company_name": plan.company_name,
            "industry": plan.industry,
            "year": plan.year,
            "top_inputs": {
                "budget_revenue": ti.budget_revenue,
                "budget_cost": ti.budget_cost,
                "last_year_revenue": ti.last_year_revenue,
                "last_year_cost": ti.last_year_cost,
                "industry_contribution_rate": ti.industry_contribution_rate,
                "company_contribution_rate": ti.company_contribution_rate,
                "income_tax_rate": ti.income_tax_rate,
            },
        },
        "source_note": source_note,
        "method": method,
        "data_quality": dq,
        "policy": policy_dict,
    }


# ── 费用编制建议（导出费用预算三表后的问答补零）──────────────────────────


class AdviceRequest(BaseModel):
    use_ai: bool = True
    """必须为 True：费用编制建议由 DeepSeek 全量介入。False 时返回 400。"""


class AdviceApplyItem(BaseModel):
    row: int
    reference_amount: float = 0.0
    budget_amount: float = 0.0
    has_last_year: bool = False
    last_year_actual: float = 0.0
    selected: bool = True
    write_last_year: bool = False
    subject: str = ""
    expense_name: str = ""
    invoice_name: str = ""
    reason: str = ""


class AdviceApplyRequest(BaseModel):
    items: list[AdviceApplyItem] = Field(default_factory=list)


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip()) or "企业"
    return cleaned[:40]


def _interactive_sess():
    try:
        with interaction._lock:
            if (
                interaction._sess is not None
                and interaction._sess_session_version == session.get_version()
            ):
                return interaction._sess
    except Exception:
        pass
    return None


def _build_session_plan():
    data = session.get_data()
    if data is None:
        raise HTTPException(status_code=400, detail="尚未导入财报，请先导入后再做费用编制建议")
    plan, meta = budget_export.build_budget_plan_from_session(data)
    return data, plan, meta


@router.post("/advice")
def generate_budget_advice(body: AdviceRequest = AdviceRequest()) -> dict:
    """生成费用编制建议：DeepSeek 全量介入（必选）。

    硬规则仍校验：上年实际 D 缺失时不虚构 D，只写参考金额 F、预算 G、占比 H。
    未配置 AI 时直接 503，不再用规则顶替主路径。
    """
    global _last_advice
    if body.use_ai is False:
        raise HTTPException(
            status_code=400,
            detail="费用编制建议已改为 DeepSeek 全量介入，请勿关闭 use_ai",
        )

    data, plan, meta = _build_session_plan()
    # WB 顶栏贡献率入模后再快照（E3/E7 与行业中枢一致）
    try:
        from core import industry as ind_mod

        ind_mod.apply_wb_top_rates_to_plan(plan, force=False)
        from core import budget as budget_mod

        budget_mod.compute_all(plan)
    except Exception:
        pass
    ctx = budget_advice.plan_snapshot(plan, data=data)

    years = sorted(data.years or [])
    latest = years[-1] if years else 0
    prev = years[-2] if len(years) >= 2 else latest

    def _amt(acc: str, y: int) -> float:
        v = (data.income_statement.get(acc) or {}).get(y)
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    ctx["period_expenses"] = {
        "selling_latest": _amt("销售费用", latest),
        "admin_latest": _amt("管理费用", latest),
        "rd_latest": _amt("研发费用", latest),
        "finance_latest": _amt("财务费用", latest),
        "selling_prev": _amt("销售费用", prev),
        "admin_prev": _amt("管理费用", prev),
        "rd_prev": _amt("研发费用", prev),
        "finance_prev": _amt("财务费用", prev),
    }
    # OCR 摘要帮助 DeepSeek 理解业务（截断）
    ocr_texts = list(session.get_ocr_texts() or [])
    if ocr_texts:
        ctx["ocr_excerpt"] = "\n\n".join(ocr_texts)[:6000]

    items, summary, ai_error = ai_mod.advise_budget_expenses(ctx)
    if not items:
        raise HTTPException(
            status_code=503,
            detail=ai_error or "DeepSeek 未返回编制建议，请检查 AI 配置后重试",
        )

    result = budget_advice.build_from_ai_items(plan, items, ai_summary=summary)
    if ai_error:
        result.algorithm_notes.append(f"DeepSeek 提示：{ai_error}")

    # 第二轮：DeepSeek 全量占比重算（替换本地缩放算法）
    rebalance_ctx = {
        "company_name": result.company_name,
        "industry": result.industry,
        "budget_revenue": result.budget_revenue,
        "expense_budget_cap": result.expense_budget_cap,
        "revenue_growth_rate": plan.top_computed.revenue_growth_rate if plan.top_computed else 0,
        "period_expenses": ctx.get("period_expenses"),
        "wb_model": ctx.get("wb_model"),
        "lines": [
            {
                "row": s.row,
                "subject": s.subject,
                "expense_name": s.expense_name,
                "invoice_name": s.invoice_name,
                "last_year_actual": s.last_year_actual,
                "budget_amount": s.budget_amount,
            }
            for s in result.suggestions
            if s.selected
        ],
    }
    # 附带计划中已有金额行，避免只优化建议、漏掉规则已填行
    seen_rows = {s.row for s in result.suggestions}
    for l in plan.lines:
        if l.row in seen_rows:
            continue
        if float(l.budget_amount or 0) > 0 or float(l.last_year_actual or 0) > 0:
            rebalance_ctx["lines"].append(
                {
                    "row": l.row,
                    "subject": l.subject,
                    "expense_name": l.expense_name,
                    "invoice_name": l.invoice_name,
                    "last_year_actual": float(l.last_year_actual or 0),
                    "budget_amount": float(l.budget_amount or 0),
                }
            )
    rebalance_ctx["timeout"] = 300
    rebalance_ctx["max_passes"] = 3
    rb_items, rb_summary, rb_err = ai_mod.rebalance_expense_ratios(rebalance_ctx)
    if rb_items:
        result = budget_advice.build_from_ai_items(
            plan,
            rb_items,
            ai_summary=(rb_summary or summary or "")[:800],
        )
        result.algorithm_notes.append(
            "占比已由 DeepSeek 多轮对账重算（准确性优先：H=G÷C2，科目对齐期间费用）"
        )
        if rb_summary:
            result.ai_summary = rb_summary
        result.ai_used = True
        # 建议层：期间费用规模对齐 + QA，避免 UI 上也出现 0.0x% 过低占比
        try:
            tmp_plan = plan
            budget_advice.apply_advice_to_plan(tmp_plan, rb_items, optimize_ratios=False)
            for n in budget_advice.align_budget_to_period_level(
                tmp_plan, period_expenses=ctx.get("period_expenses")
            ):
                result.algorithm_notes.append(n)
            qa = budget_advice.finalize_accuracy_qa(
                tmp_plan, period_expenses=ctx.get("period_expenses")
            )
            result.algorithm_notes.extend(qa.get("notes") or [])
            by_row = {l.row: l for l in tmp_plan.lines}
            c2_ui = float(result.budget_revenue or 0)
            for s in result.suggestions:
                line = by_row.get(s.row)
                if line is None:
                    continue
                s.budget_amount = float(line.budget_amount or 0)
                s.reference_amount = float(line.reference_amount or 0)
                s.budget_ratio = float(line.budget_expense_ratio or 0)
                s.budget_ratio_pct = round(s.budget_ratio * 100, 6)
            # 同步刷新勾选合计说明
            sel_sum = sum(s.budget_amount for s in result.suggestions if s.selected)
            if c2_ui > 0 and sel_sum > 0:
                result.algorithm_notes.append(
                    f"建议勾选合计 {sel_sum:,.0f} 元 · 占营收 {sel_sum/c2_ui:.2%}"
                )
        except Exception:
            pass
    elif rb_err:
        result.algorithm_notes.append(f"DeepSeek 占比重算未成功：{rb_err}（保留编制建议原占比）")

    payload = result.to_dict()
    payload["plan_meta"] = {
        "top_method": meta.get("top_method"),
        "line_method": meta.get("line_method"),
        "filled_lines": meta.get("filled_lines"),
        "notes": (meta.get("notes") or [])[-6:],
    }
    payload["ai_error"] = ""
    payload["mode"] = "deepseek_full"
    _last_advice = {
        "session_version": session.get_version(),
        "payload": payload,
    }
    return payload


@router.get("/advice/last")
def last_budget_advice() -> dict:
    """返回最近一次编制建议（同会话）；无则 404。"""
    if not _last_advice or _last_advice.get("session_version") != session.get_version():
        raise HTTPException(status_code=404, detail="尚无编制建议，请先生成")
    return _last_advice["payload"]


@router.post("/advice/apply")
def apply_budget_advice(body: AdviceApplyRequest) -> FileResponse:
    """应用选中的编制建议，写出「费用预算三表_编制建议版」并下载。"""
    data, plan, _meta = _build_session_plan()
    items = [it.model_dump() for it in body.items] if body.items else []
    if not items and _last_advice.get("session_version") == session.get_version():
        items = (_last_advice.get("payload") or {}).get("suggestions") or []
    if not items:
        raise HTTPException(status_code=400, detail="没有可应用的建议项")

    budget_advice.apply_advice_to_plan(plan, items)
    budget_mod.compute_all(plan)

    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    years = sorted(data.years or [])
    yspan = f"{years[0]}-{years[-1]}" if len(years) > 1 else (str(years[0]) if years else "")
    filename = f"{_safe_name(data.company_name)}{yspan}费用预算三表_编制建议版.xlsx"
    path = _EXPORT_DIR / filename

    from core import budget_template as tpl_mod

    out = tpl_mod.write_template(plan, str(path), session=_interactive_sess())
    ascii_name = "budget_advice.xlsx"
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
        )
    }
    return FileResponse(
        path=out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
        headers=headers,
    )
