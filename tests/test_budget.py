"""利润宝 · 模板版预算计算引擎单元测试（T6.1 + T6.2）。

覆盖：
- 顶部公式零值保护（C4/C5/C7/C9/E5/E6/E7/E8/E9/G100）
- 明细公式（E/F/H/J）与 R90 工会经费补齐
- 比例精度 6 位（0.003 不被截断）
- 空白态零错误
- E8=I98 联动
- 执行状态判定（正常/临界/超支/待补录）
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import budget as bk


# ── 顶部公式 ────────────────────────────────────────────────────────────

def test_top_zero_inputs_zero_outputs():
    """所有顶部输入为 0 时，计算结果全为 0（零值保护）。"""
    ti = bk.TopInputs()
    tc = bk.compute_top(ti)
    assert tc.gross_profit == 0.0
    assert tc.gross_margin == 0.0
    assert tc.revenue_growth_rate == 0.0
    assert tc.last_year_gross_margin == 0.0
    assert tc.income_tax_budget == 0.0
    assert tc.profit_total_budget == 0.0
    assert tc.expense_budget_cap == 0.0


def test_top_formulas_basic():
    """正常输入：C4/C5/C7/C9/E5/E6/E7 公式正确。"""
    ti = bk.TopInputs(
        budget_revenue=10_000_000,        # C2
        budget_cost=6_000_000,            # C3
        last_year_revenue=9_000_000,      # C6
        last_year_cost=5_500_000,         # C8
        company_contribution_rate=0.003,  # E3
        income_tax_rate=0.05,             # E4
    )
    tc = bk.compute_top(ti)
    assert tc.gross_profit == 4_000_000             # C4 = C2 - C3
    assert tc.gross_margin == pytest.approx(0.4)    # C5 = 4M / 10M
    assert tc.revenue_growth_rate == pytest.approx((10 - 9) / 9, abs=1e-6)  # C7
    assert tc.last_year_gross_margin == pytest.approx((9 - 5.5) / 9, abs=1e-6)  # C9
    assert tc.income_tax_budget == 30_000           # E5 = 0.003 × 10M
    assert tc.profit_total_budget == 600_000        # E6 = 30000 / 0.05
    assert tc.expense_budget_cap == 3_400_000       # E7 = 4M - 600K


def test_ratio_precision_six_digits():
    """比例精度 6 位：0.003 不被截断为 0.0。"""
    ti = bk.TopInputs(budget_revenue=1_000_000, budget_cost=600_000,
                      industry_contribution_rate=0.003)
    tc = bk.compute_top(ti)
    # 0.003 应该被保留为 0.003 而非 0.0
    assert ti.industry_contribution_rate == 0.003
    assert tc.gross_margin == pytest.approx(0.4, abs=1e-6)


# ── 明细公式 ────────────────────────────────────────────────────────────

def test_compute_line_zero_safe():
    """行计算零值保护：D/G/I 全 0 时不崩溃，状态=待补录。"""
    ti = bk.TopInputs(budget_revenue=1_000_000, last_year_revenue=900_000)
    line = bk.ExpenseLine(row=14, subject="管理费用", expense_name="办公费", invoice_name="办公用品")
    bk.compute_line(line, ti)
    assert line.last_year_expense_ratio == 0.0
    assert line.reference_amount == 0.0
    assert line.budget_expense_ratio == 0.0
    assert line.diff == 0.0
    assert line.exec_status == bk.EXEC_STATUS_PENDING


def test_compute_line_formulas_basic():
    """正常行：E=D/C6, F=D×(1+C7), H=G/C2, J=G-I，状态判定正确。"""
    ti = bk.TopInputs(
        budget_revenue=10_000_000, last_year_revenue=8_000_000, last_year_cost=5_000_000,
        budget_cost=6_000_000,
    )
    line = bk.ExpenseLine(
        row=14, subject="管理费用", expense_name="办公费", invoice_name="办公用品",
        last_year_actual=80_000,    # D
        budget_amount=100_000,      # G
        actual_amount=90_000,       # I
    )
    bk.compute_line(line, ti)
    c7 = bk.compute_top(ti).revenue_growth_rate
    assert line.last_year_expense_ratio == pytest.approx(80_000 / 8_000_000, abs=1e-6)
    assert line.reference_amount == pytest.approx(80_000 * (1 + c7), abs=0.01)
    assert line.budget_expense_ratio == pytest.approx(100_000 / 10_000_000, abs=1e-6)
    assert line.diff == 10_000  # G - I = 100K - 90K
    # 执行率 90% → 临界
    assert line.exec_status == bk.EXEC_STATUS_CRITICAL


def test_union_fee_r90_formulas_filled():
    """R90 工会经费：原模板缺 F/H/J 公式，本引擎统一补齐。"""
    plan = bk.make_empty_plan()
    plan.top_inputs.budget_revenue = 1_000_000
    plan.top_inputs.last_year_revenue = 900_000
    r90 = next((l for l in plan.lines if l.row == 90), None)
    assert r90 is not None
    assert r90.is_union_fee is True
    r90.last_year_actual = 5_000
    r90.budget_amount = 6_000
    r90.actual_amount = 6_500
    bk.compute_all(plan)
    # F/H/J 必须已计算（非 None，非 0 当输入非 0）
    assert r90.reference_amount > 0
    assert r90.budget_expense_ratio > 0
    assert r90.diff == 6_000 - 6_500  # J = G - I


# ── 整体计算与联动 ─────────────────────────────────────────────────────

def test_compute_all_e8_links_i98():
    """E8 = I98：顶部实际已发生费用 = 明细 I 列合计。"""
    plan = bk.make_empty_plan()
    plan.top_inputs.budget_revenue = 1_000_000
    plan.top_inputs.last_year_revenue = 900_000
    plan.top_inputs.budget_cost = 600_000
    plan.top_inputs.income_tax_rate = 0.05
    plan.top_inputs.company_contribution_rate = 0.003
    # 给三行 I 列赋值
    plan.lines[0].actual_amount = 10_000
    plan.lines[1].actual_amount = 20_000
    plan.lines[2].actual_amount = 30_000
    bk.compute_all(plan)
    i98 = sum(l.actual_amount for l in plan.lines)
    assert plan.top_computed.actual_expense_total == i98
    # E9 = E7 - E8
    assert plan.top_computed.expense_diff == plan.top_computed.expense_budget_cap - i98
    # G100 = E7 - G98
    g98 = sum(l.budget_amount for l in plan.lines)
    assert plan.top_computed.unallocated_balance == plan.top_computed.expense_budget_cap - g98


def test_compute_all_blank_plan_no_error():
    """空白预算：compute_all 不崩溃，所有合计为 0。"""
    plan = bk.make_empty_plan()
    bk.compute_all(plan)
    assert plan.allocated_total == 0.0
    assert plan.actual_total == 0.0
    assert plan.last_year_total == 0.0
    assert plan.diff_total == 0.0
    assert plan.top_computed.actual_expense_total == 0.0
    assert plan.over_budget_count == 0
    assert plan.critical_count == 0
    assert plan.pending_count == 84  # 全部待补录


# ── 执行状态判定 ────────────────────────────────────────────────────────

def test_exec_status_thresholds():
    """执行率阈值：<80% 正常；80-100% 临界；>100% 超支；G=I=0 待补录。"""
    ti = bk.TopInputs(budget_revenue=1_000_000, last_year_revenue=900_000)
    cases = [
        (100, 50, bk.EXEC_STATUS_NORMAL),     # 50% < 80%
        (100, 80, bk.EXEC_STATUS_CRITICAL),   # 80% 临界
        (100, 100, bk.EXEC_STATUS_CRITICAL),  # 100% 临界
        (100, 120, bk.EXEC_STATUS_OVER),      # 120% 超支
        (0, 0, bk.EXEC_STATUS_PENDING),       # 待补录
    ]
    for g, i, expected in cases:
        line = bk.ExpenseLine(row=14, subject="管理费用", expense_name="x", invoice_name="y",
                              last_year_actual=0, budget_amount=g, actual_amount=i)
        bk.compute_line(line, ti)
        assert line.exec_status == expected, f"G={g} I={i} 应为 {expected}，实际 {line.exec_status}"


# ── 筛选与校验 ──────────────────────────────────────────────────────────

def test_filter_lines_by_status():
    """按状态筛选明细行。"""
    plan = bk.make_empty_plan()
    plan.top_inputs.budget_revenue = 1_000_000
    plan.top_inputs.last_year_revenue = 900_000
    # 制造一条超支
    plan.lines[0].budget_amount = 100
    plan.lines[0].actual_amount = 200
    bk.compute_all(plan)
    over = bk.filter_lines(plan, status=bk.EXEC_STATUS_OVER)
    assert len(over) == 1
    assert over[0].exec_status == bk.EXEC_STATUS_OVER


def test_filter_lines_by_keyword():
    """按关键词筛选：科目/费用名称/发票名称任一匹配。"""
    plan = bk.make_empty_plan()
    results = bk.filter_lines(plan, keyword="招待")
    assert all("招待" in l.expense_name or "招待" in l.invoice_name or "招待" in l.subject
               for l in results)
    assert len(results) >= 1


def test_validate_plan_84_rows():
    """validate_plan：行号必须连续 14-97。"""
    plan = bk.make_empty_plan()
    ok, errors = bk.validate_plan(plan)
    assert ok, f"应有 84 行连续，错误：{errors}"


def test_validate_plan_rejects_invalid_tax_rate():
    """validate_plan：E4 所得税率必须为 0.05/0.15/0.25 之一。"""
    plan = bk.make_empty_plan()
    plan.top_inputs.income_tax_rate = 0.30  # 非法
    ok, errors = bk.validate_plan(plan)
    assert not ok
    assert any("E4" in e for e in errors)
