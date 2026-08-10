"""利润宝 · finance.py 单元测试（S1-S4 验证范围）。"""
from core import finance as fin


def test_vat_tax_rate_estimate():
    # 税金及附加=12000, 营收=1_000_000 => 估算增值税=100000 => 税负率=10%
    rate, note = fin.vat_tax_rate(12_000.0, 1_000_000.0)
    assert abs(rate - 10.0) < 1e-9
    assert note == fin.VAT_ESTIMATE_NOTE
    assert "估算" in note


def test_vat_tax_rate_zero_revenue():
    rate, note = fin.vat_tax_rate(12_000.0, 0.0)
    assert rate == 0.0
    assert note == fin.BASE_MISSING_NOTE


def test_income_tax_rate():
    rate, _ = fin.income_tax_rate(50_000.0, 1_000_000.0)
    assert abs(rate - 5.0) < 1e-9


def test_composite_tax_rate():
    rate, _ = fin.composite_tax_rate(12_000.0, 50_000.0, 1_000_000.0)
    assert abs(rate - 6.2) < 1e-9


def test_gross_margin():
    rate, _ = fin.gross_margin(1_000_000.0, 700_000.0)
    assert abs(rate - 30.0) < 1e-9


def test_expense_ratio_zero_revenue():
    rate, note = fin.expense_ratio(100.0, 0.0)
    assert rate == 0.0
    assert note == fin.BASE_MISSING_NOTE


def test_growth_rate_no_base():
    val, note = fin.growth_rate(120.0, 0.0)
    assert val == 0.0
    assert note == "无同比基数"


def test_growth_rate_positive():
    val, _ = fin.growth_rate(110.0, 100.0)
    assert abs(val - 10.0) < 1e-9


def test_value_add_estimate():
    # (目标 - 当前) × 税率
    assert fin.value_add_estimate(0.0, 500_000.0, 0.25) == 125_000.0
