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
