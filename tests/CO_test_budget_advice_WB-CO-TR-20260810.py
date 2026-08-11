"""费用编制建议算法：无上年只写 F/G/H，不虚构 D。"""

import importlib

from core import budget as budget_mod


def _advice():
    return importlib.import_module("core.CO_budget_advice_WB-CO-TR-20260810")


def test_rule_advice_no_fake_last_year():
    adv = _advice()
    plan = budget_mod.make_empty_plan(company_name="测试公司", industry="建筑业", year=2024)
    plan.top_inputs.budget_revenue = 10_000_000
    plan.top_inputs.budget_cost = 7_000_000
    plan.top_inputs.last_year_revenue = 9_000_000
    plan.top_inputs.last_year_cost = 6_500_000
    plan.top_inputs.company_contribution_rate = 0.02
    plan.top_inputs.income_tax_rate = 0.25
    result = adv.build_rule_advice(plan)
    assert result.zero_lines >= 50
    assert result.expense_budget_cap > 0
    assert len(result.suggestions) > 0
    for s in result.suggestions:
        if not s.has_last_year:
            assert s.last_year_actual == 0
            assert s.write_last_year is False
            assert s.reference_amount > 0 or s.budget_amount > 0
            assert s.budget_amount >= 0


def test_apply_advice_preserves_zero_d():
    adv = _advice()
    plan = budget_mod.make_empty_plan(company_name="测试", industry="制造业", year=2024)
    plan.top_inputs.budget_revenue = 5_000_000
    plan.top_inputs.budget_cost = 3_000_000
    plan.top_inputs.last_year_revenue = 4_000_000
    plan.top_inputs.last_year_cost = 2_800_000
    items = [
        {
            "row": 14,
            "selected": True,
            "has_last_year": False,
            "last_year_actual": 0,
            "reference_amount": 50_000,
            "budget_amount": 50_000,
            "write_last_year": False,
        }
    ]
    adv.apply_advice_to_plan(plan, items)
    line = next(l for l in plan.lines if l.row == 14)
    assert line.last_year_actual == 0
    assert line.budget_amount == 50_000
    assert line.reference_amount == 50_000
    assert line.budget_expense_ratio > 0


def test_build_from_ai_items_primary():
    adv = _advice()
    plan = budget_mod.make_empty_plan(company_name="AI主路径", industry="建筑业", year=2024)
    plan.top_inputs.budget_revenue = 8_000_000
    plan.top_inputs.budget_cost = 5_500_000
    plan.top_inputs.last_year_revenue = 7_000_000
    plan.top_inputs.last_year_cost = 5_000_000
    ai_items = [
        {
            "row": 14,
            "budget_amount": 80_000,
            "reference_amount": 80_000,
            "reason": "拓客广宣",
            "priority": "high",
            "selected": True,
        },
        {
            "row": 32,
            "budget_amount": 120_000,
            "reference_amount": 120_000,
            "reason": "管理人工",
            "priority": "high",
        },
    ]
    result = adv.build_from_ai_items(plan, ai_items, ai_summary="DeepSeek 全量")
    assert result.ai_used is True
    assert len(result.suggestions) == 2
    assert all(s.source == "ai" for s in result.suggestions)
    assert all(s.last_year_actual == 0 for s in result.suggestions)
    assert "DeepSeek 全量介入" in " ".join(result.algorithm_notes)


def test_ensure_all_required_fields_filled_columns():
    """有 D 必有 F/G；有 G 且无 D 时必有 F=G。"""
    adv = _advice()
    plan = budget_mod.make_empty_plan(company_name="补全", industry="制造业", year=2024)
    plan.top_inputs.budget_revenue = 10_000_000
    plan.top_inputs.budget_cost = 7_000_000
    plan.top_inputs.last_year_revenue = 9_000_000
    plan.top_inputs.last_year_cost = 6_500_000
    # 用不同科目、避免大类拆分互相影响：利息 / 违约金
    line_d = next(l for l in plan.lines if l.row == 92)  # 财务-利息
    line_g = next(l for l in plan.lines if l.row == 96)  # 营业外-违约金
    line_d.last_year_actual = 100_000
    line_d.budget_amount = 0
    line_d.reference_amount = 0
    line_g.budget_amount = 50_000
    line_g.reference_amount = 0
    line_g.last_year_actual = 0
    notes = adv.ensure_all_required_fields_filled(plan)
    assert line_d.budget_amount > 0
    assert line_d.reference_amount > 0
    assert line_g.reference_amount > 0
    assert abs(line_g.reference_amount - line_g.budget_amount) < 0.02
    assert any("补全" in n or "列联动" in n for n in notes)


def test_tiny_ratio_not_zeroed_by_engine():
    """千元费用 / 数亿营收：E 不得被精度抹成 0。"""
    plan = budget_mod.make_empty_plan(company_name="小额占比", industry="建筑业", year=2024)
    plan.top_inputs.budget_revenue = 372_364_436.57
    plan.top_inputs.budget_cost = 295_000_000
    plan.top_inputs.last_year_revenue = 397_746_460.76
    plan.top_inputs.last_year_cost = 350_000_000
    line = next(l for l in plan.lines if l.row == 50)
    line.last_year_actual = 3_049.69
    line.budget_amount = 643.46
    budget_mod.compute_all(plan)
    assert line.last_year_expense_ratio > 0
    assert line.last_year_expense_ratio > 1e-8
    assert line.budget_expense_ratio > 0
    # 展示不应是 0%
    e_show = budget_mod.format_ratio_pct(line.last_year_expense_ratio)
    h_show = budget_mod.format_ratio_pct(line.budget_expense_ratio)
    assert e_show != "0%"
    assert "0.000" in e_show or float(e_show.rstrip("%")) > 0
    assert h_show != "0%"


def test_align_budget_to_period_raises_undershoot():
    """DeepSeek 压低总费用率时，应按期间费用上调，H=G/C2 同步变大。"""
    adv = _advice()
    plan = budget_mod.make_empty_plan(company_name="期间对齐", industry="建筑业", year=2024)
    plan.top_inputs.budget_revenue = 100_000_000
    plan.top_inputs.budget_cost = 70_000_000
    plan.top_inputs.last_year_revenue = 95_000_000
    plan.top_inputs.last_year_cost = 68_000_000
    plan.top_inputs.company_contribution_rate = 0.003
    plan.top_inputs.income_tax_rate = 0.25
    # 人为压低：总费用率约 3%
    for i, l in enumerate(plan.lines[:10]):
        l.budget_amount = 300_000
        l.reference_amount = 300_000
    budget_mod.compute_all(plan)
    before = plan.allocated_total
    before_rate = before / 100_000_000
    notes = adv.align_budget_to_period_level(
        plan,
        period_expenses={
            "selling_latest": 4_000_000,
            "admin_latest": 6_000_000,
            "rd_latest": 0,
            "finance_latest": 1_000_000,
        },
    )
    after = plan.allocated_total
    after_rate = after / 100_000_000
    assert after > before
    # 期间合计 1100 万 ≈ 11% 营收，应对齐上去
    assert after_rate > 0.08
    assert after_rate < 0.20
    for l in plan.lines:
        if l.budget_amount > 0:
            assert abs(l.budget_expense_ratio - l.budget_amount / 100_000_000) < 1e-5
    assert any("上调" in n or "对齐" in n for n in notes)


def test_finalize_accuracy_qa_h_equals_g_over_c2():
    """QA：H 必须 = G/C2，有 G 必有 F。"""
    adv = _advice()
    plan = budget_mod.make_empty_plan(company_name="QA", industry="建筑业", year=2024)
    plan.top_inputs.budget_revenue = 10_000_000
    plan.top_inputs.budget_cost = 7_000_000
    plan.top_inputs.last_year_revenue = 9_000_000
    plan.top_inputs.last_year_cost = 6_500_000
    line = next(l for l in plan.lines if l.row == 14)
    line.budget_amount = 100_000
    line.reference_amount = 0
    line.last_year_actual = 0
    line.budget_expense_ratio = 0.5  # 故意错误
    qa = adv.finalize_accuracy_qa(
        plan,
        period_expenses={"selling_latest": 100_000, "admin_latest": 0, "finance_latest": 0},
    )
    assert qa["identity_ok"] is True
    assert abs(line.budget_expense_ratio - 0.01) < 1e-6
    assert line.reference_amount > 0
    assert qa["c2"] == 10_000_000
    assert any("QA" in n for n in qa["notes"])


def test_normalize_ratio_amount_identity():
    """DeepSeek 行规范化：占比推金额恒等。"""
    import importlib

    ai = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    item = ai._normalize_ratio_amount_item(
        {"row": 14, "budget_ratio_pct": 0.5, "budget_amount": 999},
        revenue=10_000_000,
        valid_rows={14},
    )
    assert item is not None
    assert item["budget_amount"] == 50_000.0
    assert abs(item["budget_ratio_pct"] - 0.5) < 1e-6


def test_optimize_expense_ratios_scales_over_cap():
    """费用合计远超上限时，整体缩放并重算占比。"""
    adv = _advice()
    plan = budget_mod.make_empty_plan(company_name="占比优化", industry="建筑业", year=2024)
    # 毛利=300万，贡献率/税率默认 → E7 通常远小于 2000 万
    plan.top_inputs.budget_revenue = 10_000_000
    plan.top_inputs.budget_cost = 7_000_000
    plan.top_inputs.last_year_revenue = 9_000_000
    plan.top_inputs.last_year_cost = 6_500_000
    plan.top_inputs.company_contribution_rate = 0.02
    plan.top_inputs.income_tax_rate = 0.25
    # 人为堆高：合计 2000 万
    for i, l in enumerate(plan.lines[:20]):
        l.budget_amount = 1_000_000
        l.reference_amount = 1_000_000
        l.last_year_actual = 0
    budget_mod.compute_all(plan)
    before = plan.allocated_total
    notes = adv.optimize_expense_ratios(plan)
    after = plan.allocated_total
    assert after < before
    assert after <= plan.top_computed.expense_budget_cap * 1.01 + 1
    # 每行占比 = G/C2
    for l in plan.lines:
        if l.budget_amount > 0:
            expect = l.budget_amount / 10_000_000
            assert abs(l.budget_expense_ratio - expect) < 1e-5
    assert any("占比优化" in n or "缩放" in n for n in notes)
