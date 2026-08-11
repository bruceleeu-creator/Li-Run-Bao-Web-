"""利润宝 · 行业基准库测试（WB 行业基准数据库 v1.0）。"""

import pytest

from core import industry as ind


def test_reference_db_has_22_doc_industries():
    """文档共 22 个行业条目，四大指标字段齐全。"""
    assert len(ind.INDUSTRY_REFERENCE_DB) == 22
    for name, item in ind.INDUSTRY_REFERENCE_DB.items():
        assert set(item) == {"vat_tax_rate", "income_tax_rate", "gross_margin", "net_margin"}
        for key, fields in item.items():
            for field in ("median", "min", "max", "low_warn", "high_warn", "budget_min", "budget_max"):
                assert field in fields, f"{name}/{key} 缺少 {field}"


def test_single_doc_industry_values():
    """建筑业/医药直接使用文档数值。"""
    b = ind.INDUSTRY_REFERENCE_DB["建筑业"]
    assert b["vat_tax_rate"] == pytest.approx(
        {"median": 3.0, "min": 2.5, "max": 4.0, "low_warn": 2.0, "high_warn": 7.0,
         "budget_min": 3.0, "budget_max": 3.6}
    )
    assert b["gross_margin"]["min"] == 10.0 and b["gross_margin"]["max"] == 13.0
    assert b["net_margin"]["low_warn"] == 1.0 and b["net_margin"]["high_warn"] == 5.0

    m = ind.INDUSTRY_REFERENCE_DB["医药制造业"]
    assert m["vat_tax_rate"]["median"] == 8.5
    assert m["vat_tax_rate"]["low_warn"] == 6.0 and m["vat_tax_rate"]["high_warn"] == 18.0


def test_app_industries_have_full_four_indicator_fields():
    """应用层 10 个行业都带四大指标与预警线/预算区间。"""
    for name in ind.list_industries():
        bench, fallback = ind.get_benchmark(name)
        assert fallback is False
        for key in ("vat_tax_rate", "income_tax_rate", "gross_margin", "net_margin"):
            item = bench[key]
            assert item["median"] > 0
            assert item["low_warn"] < item["high_warn"]
            assert item["budget_min"] <= item["budget_max"]


def test_wholesale_retail_aggregate():
    """批发零售业由商业批发+商业零售聚合：中枢取均值，区间取覆盖范围。"""
    bench, _ = ind.get_benchmark("批发零售业")
    vat = bench["vat_tax_rate"]
    assert vat["median"] == pytest.approx(1.7)
    assert vat["min"] == 0.7 and vat["max"] == 3.5
    assert vat["low_warn"] == 0.6 and vat["high_warn"] == 6.0


def test_unknown_industry_falls_back_to_manufacturing():
    bench, fallback = ind.get_benchmark("量子计算")
    assert fallback is True
    assert bench is ind.get_benchmark("制造业")[0]
    assert bench["vat_tax_rate"]["median"] == 3.5


def test_wb_section7_fee_band_and_cit_hub():
    """§七：费用率≈毛利−净利；所得税贡献率=税负中枢（建筑 1.5%）。"""
    assert ind.resolve_industry_key("装饰工程") == "建筑业"
    assert ind.resolve_industry_key("建筑业") == "建筑业"
    cit = ind.get_income_tax_contribution_rate("建筑业", mode="hub")
    assert abs(cit - 0.015) < 1e-6
    band = ind.get_period_expense_ratio_band("建筑业")
    # 建筑毛利 10–13、净利 1.5–3 → 费用约 8–12%
    assert band["min"] >= 0.07
    assert band["max"] <= 0.15
    assert band["min"] < band["median"] <= band["max"]
    mfg = ind.get_period_expense_ratio_band("制造业")
    assert mfg["min"] >= 0.12  # 叙述 15–20 与 GM-NM 并集
    assert mfg["max"] >= 0.18


def test_apply_wb_top_rates_overwrites_template_default():
    from core import budget as bk

    plan = bk.make_empty_plan(company_name="某某装饰", industry="建筑业", year=2024)
    plan.top_inputs.budget_revenue = 100_000_000
    plan.top_inputs.budget_cost = 80_000_000  # 毛利率 20%
    plan.top_inputs.income_tax_rate = 0.05
    assert abs(plan.top_inputs.company_contribution_rate - 0.003) < 1e-9
    notes = ind.apply_wb_top_rates_to_plan(plan)
    assert abs(plan.top_inputs.industry_contribution_rate - 0.015) < 1e-6
    # 小微 5% + 中枢 1.5% 会炸 E7 → 倒推税率升至高新默认 15%（不再抬到 25%）
    assert plan.top_inputs.income_tax_rate == 0.15
    bk.compute_all(plan)
    assert plan.top_computed.expense_budget_cap > 0
    assert any("E3" in n or "E4" in n for n in notes)
