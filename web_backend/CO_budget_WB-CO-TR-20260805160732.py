"""利润宝 · 模板工作台预算 API 路由。

从已导入的会话数据（FinancialData）自动提取审计报告指标，
生成模板工作台的预算计划（TopInputs 顶部输入）。
优先用 AI 从 OCR 原文识别（更准），未配置/失败回退结构化映射。
复用 core/budget.py 的 make_empty_plan，不重写预算逻辑。
"""

from __future__ import annotations

import importlib

from fastapi import APIRouter, HTTPException

from core import budget as budget_mod

session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")

router = APIRouter(prefix="/api/budget", tags=["budget"])


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
    }
