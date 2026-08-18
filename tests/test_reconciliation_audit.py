"""勾稽 / 科目归一 / E3 合成 / 金标（艺康）回归。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import budget as budget_mod
from core import industry as ind_mod
from core import reconciliation as recon
from core.models import FinancialData

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "demo_output" / "cases" / "audit_3years" / "gold_yikang.json"


def _data_from_gold() -> FinancialData:
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    data = FinancialData(
        company_name=gold["company_name"],
        industry=gold["industry"],
        years=sorted(int(y) for y in gold["by_year"].keys()),
    )
    for y_str, accounts in gold["by_year"].items():
        y = int(y_str)
        for acc, val in accounts.items():
            if acc == "notes":
                continue
            data.income_statement.setdefault(acc, {})[y] = float(val)
    data.parsed_meta = {
        "matched_cells": 30,
        "text_layer": True,
        "income_tax_nominal_rate": gold.get("income_tax_nominal_rate", 0.15),
    }
    return data


def test_gold_file_exists():
    assert GOLD.is_file()
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    assert gold["company_name"].startswith("云南艺康")
    assert set(gold["by_year"]) == {"2022", "2023", "2024"}


def test_r2_net_income_reconciles_on_gold():
    data = _data_from_gold()
    rec = recon.reconcile_income_statement(data)
    assert rec["ok"] is True
    assert rec["hard_fail"] is False
    r2 = [c for c in rec["checks"] if str(c["id"]).startswith("R2")]
    assert len(r2) == 3
    assert all(c["status"] == "pass" for c in r2)


def test_negative_tax_excluded_from_cit_average():
    data = _data_from_gold()
    hist = recon.historical_cit_rates(data)
    # 2022 负所得税应排除
    y2022 = next(s for s in hist["series"] if s["year"] == 2022)
    assert y2022["excluded"] is True
    assert hist["latest_valid"] is not None
    # 2024 约 1.75%
    assert abs(hist["latest_valid"] - 4967859.45 / 283347223.63) < 1e-6


def test_e3_synthesis_max_hub_latest():
    data = _data_from_gold()
    syn = recon.synthesize_company_contribution(data, "建筑业")
    hub = ind_mod.get_income_tax_contribution_rate("建筑业", mode="hub")
    assert abs(hub - 0.015) < 1e-6
    # max(1.5%, ~1.75%) ≈ 1.75%
    assert syn["company_contribution_rate"] >= hub - 1e-9
    assert abs(syn["company_contribution_rate"] - syn["latest_valid"]) < 1e-6


def test_normalize_research_expense_alias():
    data = FinancialData(company_name="测", industry="建筑业", years=[2022])
    data.income_statement["研究费用"] = {2022: 100.0}
    data.income_statement["管理费用"] = {2022: 500.0}
    notes = recon.normalize_subjects(data)
    assert "研发费用" in data.income_statement
    assert data.income_statement["研发费用"][2022] == 100.0
    assert "研究费用" not in data.income_statement
    assert any("研发" in n for n in notes)


def test_enrich_writes_data_quality():
    data = _data_from_gold()
    recon.enrich_financial_data(data, industry="建筑业")
    dq = data.parsed_meta["data_quality"]
    assert dq["confidence"] in ("high", "medium", "low")
    assert "reconciliation" in data.parsed_meta
    assert data.parsed_meta["cit_synthesis"]["company_contribution_rate"] > 0
    anoms = data.parsed_meta["expense_anomalies"]["anomalies"]
    # 财务费用跳变 + 销售近零
    types = {a["type"] for a in anoms}
    assert "near_zero_selling" in types
    assert "fee_ratio_jump" in types


def test_apply_historical_contribution_to_plan():
    data = _data_from_gold()
    plan = budget_mod.make_empty_plan(
        company_name=data.company_name, industry="建筑业", year=2024
    )
    plan.top_inputs.budget_revenue = 283_347_223.63
    plan.top_inputs.budget_cost = 226_005_610.20
    assert plan.top_inputs.income_tax_rate == 0.15
    notes = ind_mod.apply_wb_top_rates_to_plan(plan)
    notes2 = ind_mod.apply_historical_contribution_to_plan(plan, data)
    assert plan.top_inputs.company_contribution_rate >= 0.015
    # 应贴近 2024 实缴/营收
    assert plan.top_inputs.company_contribution_rate >= 0.017
    assert notes or notes2


def test_compliance_winsor_on_volatile_growth():
    from core import compliance_policy as cp

    data = _data_from_gold()
    hist = cp.historical_fee_ratios(data)
    # 2023→2024 或 2022→2023 至少一年 |增速|>30% 会标 volatile（取决于 latest/prev）
    lim = cp.budget_amount_limits(data, "建筑业")
    assert "fee_growth_mode" in lim
    assert "robust_subject_ratios" in lim
    assert lim.get("near_zero_selling") is True


def test_robust_finance_ratio_drops_spike():
    data = _data_from_gold()
    r = recon.robust_subject_ratio(data, "财务费用", drop_max=True)
    assert r is not None
    # 去掉 2023 高点后，中位应远低于 5.7%
    assert r < 0.04
