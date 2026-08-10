"""利润宝 · 三 Sheet 预算模板导出填充（DeepSeek 可选增强）。

产出与 `budget_3sheet_WB-CO-TR-20260726.xlsx` 同构的工作簿：
1. 费用预算表（A1:J100，公式列 E/F/H/J 保留）
2. 行业企业所得税贡献率参考
3. 诊断与行动清单

流程：
- 规则：从 FinancialData 提取营收/成本，科目余额表/费用科目映射到 84 行
- AI（可选）：DeepSeek 识别顶部 4 指标 + 将费用金额分配到模板行
- 写出：core.budget_template.write_template（禁止覆盖原模板）
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional, Sequence

from . import budget as budget_mod
from . import budget_template as tpl_mod
from .models import FinancialData

logger = logging.getLogger(__name__)

# 项目内标准样例模板（与用户提供的格式一致）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_TEMPLATE = (
    PROJECT_ROOT / "demo_output" / "budget_3sheet_WB-CO-TR-20260726.xlsx"
)

# 科目余额表 → 模板费用名称关键词（确定性映射，可复核）
_LEDGER_TO_EXPENSE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("业务招待费", ["业务招待费"]),
    ("广告宣传费", ["广宣费", "广告"]),
    ("咨询服务费", ["咨询", "中介机构服务费"]),
    ("福利费", ["职工福利费"]),
    ("教育经费", ["职工教育经费"]),
    ("研发费用", ["研发费用"]),
    ("折旧", ["摊销折旧费", "折旧费"]),
]

_INCOME_TO_SUBJECT: list[tuple[str, str]] = [
    ("销售费用", "销售费用"),
    ("管理费用", "管理费用"),
    ("财务费用", "财务费用"),
    ("研发费用", "管理费用"),  # 模板中研发在管理费用大类下
]


def _amount(data: FinancialData, statement: str, account: str, year: int) -> float:
    if statement == "income":
        table = data.income_statement
    elif statement == "balance":
        table = data.balance_sheet
    else:
        table = data.account_balances
    val = (table.get(account) or {}).get(year)
    try:
        return float(val or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fill_top_from_data(plan: budget_mod.BudgetPlan, data: FinancialData) -> str:
    years = sorted(data.years or [])
    latest = years[-1] if years else 0
    prev = years[-2] if len(years) >= 2 else latest
    ti = plan.top_inputs
    ti.budget_revenue = _amount(data, "income", "营业收入", latest)
    ti.budget_cost = _amount(data, "income", "营业成本", latest)
    ti.last_year_revenue = _amount(data, "income", "营业收入", prev)
    ti.last_year_cost = _amount(data, "income", "营业成本", prev)
    plan.year = latest
    plan.company_name = data.company_name or plan.company_name
    plan.industry = data.industry or plan.industry
    return f"结构化提取 · 预算年 {latest}，上年 {prev}"


def apply_top_indicators(plan: budget_mod.BudgetPlan, indicators: dict) -> None:
    """把识别结果写入顶部 TopInputs（供 Web 层 AI 回调使用）。"""
    ti = plan.top_inputs
    ti.budget_revenue = float(indicators.get("budget_revenue", 0) or 0)
    ti.budget_cost = float(indicators.get("budget_cost", 0) or 0)
    ti.last_year_revenue = float(indicators.get("last_year_revenue", 0) or 0)
    ti.last_year_cost = float(indicators.get("last_year_cost", 0) or 0)


def _match_lines(plan: budget_mod.BudgetPlan, keywords: list[str]) -> list[budget_mod.ExpenseLine]:
    hits = []
    for line in plan.lines:
        blob = f"{line.expense_name}{line.invoice_name}{line.subject}"
        if any(k in blob for k in keywords):
            hits.append(line)
    return hits


def _distribute(amount: float, lines: list[budget_mod.ExpenseLine], field: str) -> None:
    """把金额均分到匹配行（确定性，可复核）。"""
    if amount <= 0 or not lines:
        return
    each = round(amount / len(lines), 2)
    # 尾差放第一行
    for i, line in enumerate(lines):
        val = each if i < len(lines) - 1 else round(amount - each * (len(lines) - 1), 2)
        setattr(line, field, float(getattr(line, field, 0.0) or 0.0) + val)


def _fill_lines_from_data(plan: budget_mod.BudgetPlan, data: FinancialData) -> str:
    """用利润表期间费用 + 科目余额表确定性灌入 D/G/I。"""
    years = sorted(data.years or [])
    latest = years[-1] if years else 0
    prev = years[-2] if len(years) >= 2 else latest
    notes = []

    # 1) 科目余额表精确项
    for ledger_name, keywords in _LEDGER_TO_EXPENSE_KEYWORDS:
        prev_amt = _amount(data, "ledger", ledger_name, prev)
        latest_amt = _amount(data, "ledger", ledger_name, latest)
        lines = _match_lines(plan, keywords)
        if not lines:
            continue
        if prev_amt > 0:
            _distribute(prev_amt, lines, "last_year_actual")
            _distribute(prev_amt, lines, "budget_amount")  # 默认预算=上年
        if latest_amt > 0:
            _distribute(latest_amt, lines, "actual_amount")
            if prev_amt <= 0:
                _distribute(latest_amt, lines, "budget_amount")
        if prev_amt or latest_amt:
            notes.append(f"{ledger_name}→{len(lines)}行")

    # 2) 利润表期间费用：保证大类合计被灌入（余额表不足时补差额到科目代表行）
    for income_acc, subject in _INCOME_TO_SUBJECT:
        prev_amt = _amount(data, "income", income_acc, prev)
        latest_amt = _amount(data, "income", income_acc, latest)
        if income_acc == "研发费用":
            subject_lines = _match_lines(plan, ["研发费用"]) or [
                l for l in plan.lines if l.subject == subject
            ][:1]
        else:
            subject_lines = [l for l in plan.lines if l.subject == subject]
        if not subject_lines:
            continue
        already_prev = sum(float(l.last_year_actual or 0) for l in subject_lines)
        already_act = sum(float(l.actual_amount or 0) for l in subject_lines)
        # 代表行：优先已有非零，否则首行
        primary = next(
            (l for l in subject_lines if (l.last_year_actual or l.actual_amount or l.budget_amount)),
            subject_lines[0],
        )
        # 补足上年
        if prev_amt > already_prev + 1:
            gap = prev_amt - already_prev
            primary.last_year_actual = float(primary.last_year_actual or 0) + gap
            if float(primary.budget_amount or 0) <= 0:
                primary.budget_amount = float(primary.last_year_actual or 0)
            notes.append(f"{income_acc}上年补{gap:,.0f}→R{primary.row}")
        # 补足最新年实际
        if latest_amt > already_act + 1:
            gap = latest_amt - already_act
            primary.actual_amount = float(primary.actual_amount or 0) + gap
            if float(primary.budget_amount or 0) <= 0:
                primary.budget_amount = float(primary.actual_amount or 0)
            notes.append(f"{income_acc}本年补{gap:,.0f}→R{primary.row}")
        # 若完全空白且利润表有数，整笔落入代表行
        if prev_amt > 0 and already_prev <= 0 and float(primary.last_year_actual or 0) <= 0:
            primary.last_year_actual = prev_amt
            primary.budget_amount = prev_amt
            notes.append(f"{income_acc}上年整笔→R{primary.row}")
        if latest_amt > 0 and already_act <= 0 and float(primary.actual_amount or 0) <= 0:
            primary.actual_amount = latest_amt
            if float(primary.budget_amount or 0) <= 0:
                primary.budget_amount = latest_amt
            notes.append(f"{income_acc}本年整笔→R{primary.row}")

    return "规则映射 · " + ("；".join(notes) if notes else "无费用明细可映射")


def apply_period_totals_to_plan(plan: budget_mod.BudgetPlan, period: dict) -> str:
    """用 AI 提取的期间费用合计，补齐代表行（当明细仍空时）。"""
    if not period:
        return ""
    mapping = [
        ("selling_expense", "prev_selling_expense", "销售费用", ["销售费用"]),
        ("admin_expense", "prev_admin_expense", "管理费用", ["管理费用"]),
        ("rd_expense", "prev_rd_expense", "管理费用", ["研发费用"]),
        ("finance_expense", "prev_finance_expense", "财务费用", ["财务费用"]),
    ]
    notes = []
    for act_key, prev_key, subject, keywords in mapping:
        try:
            act = float(period.get(act_key) or 0)
            prev = float(period.get(prev_key) or 0)
        except (TypeError, ValueError):
            act, prev = 0.0, 0.0
        lines = _match_lines(plan, keywords) or [l for l in plan.lines if l.subject == subject]
        if not lines:
            continue
        primary = lines[0]
        if prev > 0 and sum(float(l.last_year_actual or 0) for l in lines) <= 0:
            primary.last_year_actual = prev
            primary.budget_amount = prev
            notes.append(f"AI期间{prev_key}→R{primary.row}")
        if act > 0 and sum(float(l.actual_amount or 0) for l in lines) <= 0:
            primary.actual_amount = act
            if float(primary.budget_amount or 0) <= 0:
                primary.budget_amount = act
            notes.append(f"AI期间{act_key}→R{primary.row}")
    return "；".join(notes)


def apply_line_allocations(plan: budget_mod.BudgetPlan, items: Sequence[dict]) -> int:
    """应用 AI/外部返回的行分配 JSON 列表，返回成功写入行数。"""
    by_row = {l.row: l for l in plan.lines}
    applied = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            row = int(item.get("row"))
        except (TypeError, ValueError):
            continue
        line = by_row.get(row)
        if line is None:
            continue
        for field, key in (
            ("last_year_actual", "last_year_actual"),
            ("budget_amount", "budget_amount"),
            ("actual_amount", "actual_amount"),
        ):
            raw = item.get(key)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                m = re.search(r"[-+]?\d+(?:\.\d+)?", str(raw).replace(",", ""))
                val = float(m.group(0)) if m else None
            if val is None or val < 0:
                continue
            setattr(line, field, val)
        applied += 1
    return applied


def build_ai_allocation_prompt(data: FinancialData, plan: budget_mod.BudgetPlan) -> tuple[str, str]:
    """构造 DeepSeek 费用分配提示词 (system, user)。"""
    years = sorted(data.years or [])
    latest = years[-1] if years else 0
    prev = years[-2] if len(years) >= 2 else latest
    facts = {
        "years": years,
        "income": {
            acc: {str(y): _amount(data, "income", acc, y) for y in years}
            for acc in ["营业收入", "营业成本", "销售费用", "管理费用", "研发费用", "财务费用"]
        },
        "ledger": {
            acc: {str(y): _amount(data, "ledger", acc, y) for y in years}
            for acc in ["业务招待费", "广告宣传费", "咨询服务费", "福利费", "教育经费", "研发费用", "折旧"]
        },
    }
    catalog = [
        {
            "row": l.row,
            "subject": l.subject,
            "expense_name": l.expense_name,
            "invoice_name": l.invoice_name,
        }
        for l in plan.lines
    ]
    system = (
        "你是中国企业费用预算编制助手。只能使用用户提供的金额事实，"
        "把费用分配到模板行。所有建议必须合法合规。"
        "返回 JSON 对象：{\"lines\":[{\"row\":14,\"last_year_actual\":0,"
        "\"budget_amount\":0,\"actual_amount\":0},...]}。"
        "row 必须是模板已有行号；金额单位元；不要虚构不存在的费用。"
        "预算年费用可按上年×(1+收入增长率)估算；actual 用最新年已发生口径。"
        "只输出 JSON。"
    )
    user = (
        f"企业：{data.company_name} 行业：{data.industry}\n"
        f"预算年≈{latest} 上年≈{prev}\n"
        f"财务事实：{json.dumps(facts, ensure_ascii=False)}\n"
        f"模板行目录（前40行，共{len(catalog)}行）："
        f"{json.dumps(catalog[:40], ensure_ascii=False)}\n"
        "请输出需要填数的行（可只返回非零行，最多 40 条）。"
    )
    return system, user


def finalize_plan(plan: budget_mod.BudgetPlan) -> budget_mod.BudgetPlan:
    """compute + validate，必要时修正非法字段。"""
    budget_mod.compute_all(plan)
    ok, errors = budget_mod.validate_plan(plan)
    if not ok:
        if plan.top_inputs.income_tax_rate not in budget_mod.INCOME_TAX_RATE_CHOICES:
            plan.top_inputs.income_tax_rate = budget_mod.DEFAULT_INCOME_TAX_RATE
        for l in plan.lines:
            l.last_year_actual = max(0.0, float(l.last_year_actual or 0))
            l.budget_amount = max(0.0, float(l.budget_amount or 0))
            l.actual_amount = max(0.0, float(l.actual_amount or 0))
        for field in (
            "budget_revenue", "budget_cost", "last_year_revenue", "last_year_cost"
        ):
            v = getattr(plan.top_inputs, field)
            if v < 0:
                setattr(plan.top_inputs, field, 0.0)
        budget_mod.compute_all(plan)
        ok, errors = budget_mod.validate_plan(plan)
        if not ok:
            raise tpl_mod.TemplateError(
                "预算计划校验未通过：\n  - " + "\n  - ".join(errors)
            )
    if CANONICAL_TEMPLATE.exists():
        plan.source_path = str(CANONICAL_TEMPLATE.resolve())
    return plan


def build_budget_plan_from_session(
    data: FinancialData,
    ocr_texts: Optional[Sequence[str]] = None,
    *,
    top_indicators: Optional[dict] = None,
    line_allocations: Optional[Sequence[dict]] = None,
    period_totals: Optional[dict] = None,
) -> tuple[budget_mod.BudgetPlan, dict]:
    """组装可导出的 BudgetPlan。

    top_indicators / line_allocations / period_totals 由 Web 层 DeepSeek 结果注入；
    未提供时纯规则填充（core 不反向依赖 web_backend）。
    """
    plan = budget_mod.make_empty_plan(
        company_name=data.company_name,
        industry=data.industry or "制造业",
        year=(sorted(data.years)[-1] if data.years else 0),
    )
    meta = {"top_method": "rule", "line_method": "rule", "notes": []}

    if top_indicators:
        # 剥离内部字段
        clean = {k: v for k, v in top_indicators.items() if not str(k).startswith("_")}
        apply_top_indicators(plan, clean)
        meta["top_method"] = "ai"
        meta["notes"].append("AI 识别 · 顶部营收/成本（DeepSeek 多片段）")
        period_totals = period_totals or top_indicators.get("_period")
    else:
        meta["notes"].append(_fill_top_from_data(plan, data))

    # 先 AI 明细（更准），再规则补差额，避免 AI 空行被规则整笔覆盖细节
    if line_allocations:
        n = apply_line_allocations(plan, line_allocations)
        if n > 0:
            meta["line_method"] = "ai"
            meta["notes"].append(f"AI 分配 · 写入 {n} 行费用明细（DeepSeek）")
    meta["notes"].append(_fill_lines_from_data(plan, data))
    if period_totals:
        note = apply_period_totals_to_plan(plan, period_totals)
        if note:
            meta["notes"].append("期间合计补齐 · " + note)

    finalize_plan(plan)
    filled = sum(
        1
        for l in plan.lines
        if (l.last_year_actual or 0) > 0 or (l.budget_amount or 0) > 0 or (l.actual_amount or 0) > 0
    )
    meta["filled_lines"] = filled
    meta["notes"].append(f"非空费用行 {filled}/84")
    return plan, meta


def export_budget_3sheet(
    data: FinancialData,
    path: str,
    *,
    ocr_texts: Optional[Sequence[str]] = None,
    interactive_session=None,
    top_indicators: Optional[dict] = None,
    line_allocations: Optional[Sequence[dict]] = None,
    period_totals: Optional[dict] = None,
) -> tuple[str, dict]:
    """写出三 Sheet 预算 Excel，返回 (path, meta)。"""
    plan, meta = build_budget_plan_from_session(
        data,
        ocr_texts=ocr_texts,
        top_indicators=top_indicators,
        line_allocations=line_allocations,
        period_totals=period_totals,
    )
    out = tpl_mod.write_template(plan, path, session=interactive_session)
    meta["path"] = out
    meta["sheets"] = ["费用预算表", "行业企业所得税贡献率参考", "诊断与行动清单"]
    return out, meta
