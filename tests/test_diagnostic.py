"""利润宝 · diagnostic.py 单元测试（S5）。"""
import os
import sys

import pytest

from core import parser as pr
from core import diagnostic as diag
from core import finance as fin
from data import make_sample

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope="module")
def sample_data():
    """用样例 JSON 构造 FinancialData。"""
    raw = make_sample.build_sample_data()
    return pr.parse_financial_dict(raw)


def test_diagnose_identifies_four_findings(sample_data):
    """样例数据应识别出 4 类典型异常。"""
    result = diag.diagnose(sample_data)
    finding_ids = {f.id for f in result.findings}
    assert "RD_MISSING" in finding_ids
    assert "ENTERTAIN_EXCESS" in finding_ids
    assert "CONSULTING_HIGH" in finding_ids
    assert "VAT_LOW" in finding_ids


def test_sample_includes_new_benchmark_findings(sample_data):
    """WB 基准 v1.0 落地后，样例还应触发所得税偏低与净利率偏低。"""
    result = diag.diagnose(sample_data)
    by_id = {f.id: f for f in result.findings}
    assert "INCOME_TAX_LOW" in by_id
    assert "NET_MARGIN_LOW" in by_id
    # VAT 偏低目标值改为行业经验中枢（3.5%），不再用旧的区间下限
    assert by_id["VAT_LOW"].target_value == 3.5


def test_vat_high_triggered_above_2_5x_median():
    """增值税税负率高于中枢×2.5 时触发偏高提示。"""
    raw = {
        "company_name": "高税负公司",
        "industry": "制造业",
        "years": [2023],
        "income_statement": {
            "营业收入": {2023: 10_000_000},
            "营业成本": {2023: 7_000_000},
            "税金及附加": {2023: 240_000},  # 估算 VAT = 240000/0.12/1000万 = 20%
            "所得税费用": {2023: 250_000},
            "净利润": {2023: 750_000},
        },
        "balance_sheet": {},
        "account_balances": {},
    }
    result = diag.diagnose(pr.parse_financial_dict(raw))
    ids = {f.id for f in result.findings}
    assert "VAT_HIGH" in ids
    high = next(f for f in result.findings if f.id == "VAT_HIGH")
    assert high.severity == diag.SEVERITY_LOW
    assert len(high.options) == 3


def test_severity_assignment(sample_data):
    """严重度正确：研发缺失/咨询偏高/VAT偏低=高；招待费超限=中。"""
    result = diag.diagnose(sample_data)
    by_id = {f.id: f for f in result.findings}
    assert by_id["RD_MISSING"].severity == diag.SEVERITY_HIGH
    assert by_id["ENTERTAIN_EXCESS"].severity == diag.SEVERITY_MEDIUM
    assert by_id["CONSULTING_HIGH"].severity == diag.SEVERITY_HIGH
    assert by_id["VAT_LOW"].severity == diag.SEVERITY_HIGH


def test_each_finding_has_three_options(sample_data):
    """每条发现含 A/B/C 三个可量化选项。"""
    result = diag.diagnose(sample_data)
    for f in result.findings:
        labels = {o.label for o in f.options}
        assert labels == {"A", "B", "C"}, f"{f.id} 选项标签应为 A/B/C"
        for o in f.options:
            assert o.name, f"{f.id}/{o.label} 缺少名称"
            assert o.description, f"{f.id}/{o.label} 缺少描述"
            assert o.target_value >= 0, f"{f.id}/{o.label} 目标值应 ≥ 0"
            assert o.risk_level in (diag.RISK_HIGH, diag.RISK_MEDIUM, diag.RISK_LOW)


def test_finding_contains_fact_benchmark_suggestion(sample_data):
    """每条发现含事实、行业对标、建议。"""
    result = diag.diagnose(sample_data)
    for f in result.findings:
        assert f.fact, f"{f.id} 缺少事实"
        assert f.benchmark, f"{f.id} 缺少行业对标"
        assert f.suggestion, f"{f.id} 缺少建议"
        assert f.current_value >= 0


def test_vat_estimate_note_significant(sample_data):
    """诊断结果须显著标注增值税税负率为估算口径。"""
    result = diag.diagnose(sample_data)
    assert "估算" in result.vat_estimate_note
    vat_finding = next((f for f in result.findings if f.id == "VAT_LOW"), None)
    assert vat_finding is not None
    assert "估算" in vat_finding.fact or "估算" in vat_finding.benchmark


def test_rd_missing_quantitative_options(sample_data):
    """研发费用缺失选项含可量化目标值与预计节税。"""
    result = diag.diagnose(sample_data)
    rd = next(f for f in result.findings if f.id == "RD_MISSING")
    # A 选项目标值应接近营收 5%
    revenue = sample_data.income_statement["营业收入"][2023]
    opt_a = next(o for o in rd.options if o.label == "A")
    assert abs(opt_a.target_value - revenue * 0.05) < 1.0
    assert opt_a.est_saving > 0


def test_entertainment_excess_options(sample_data):
    """招待费超限选项的目标值小于当前值（A/B）；C 维持。"""
    result = diag.diagnose(sample_data)
    ent = next(f for f in result.findings if f.id == "ENTERTAIN_EXCESS")
    cur = ent.current_value
    opt_a = next(o for o in ent.options if o.label == "A")
    opt_b = next(o for o in ent.options if o.label == "B")
    opt_c = next(o for o in ent.options if o.label == "C")
    assert opt_a.target_value < cur
    assert opt_b.target_value < cur
    assert opt_c.target_value == cur


def test_no_findings_for_clean_data():
    """对正常数据不产生误报。"""
    raw = {
        "company_name": "正常公司",
        "industry": "制造业",
        "years": [2023],
        "income_statement": {
            "营业收入": {2023: 10_000_000},
            "营业成本": {2023: 7_000_000},
            "税金及附加": {2023: 36_000},  # 估算 VAT 税负率 = 36000/0.12/1000万 = 3% → 正常
            "管理费用": {2023: 800_000},
            "研发费用": {2023: 500_000},  # 5% → 正常
            "所得税费用": {2023: 250_000},
            "净利润": {2023: 750_000},
        },
        "balance_sheet": {},
        "account_balances": {
            "业务招待费": 30_000,  # 营收 0.3% → 正常
            "咨询服务费": 100_000,  # 营收 1% → 正常
        },
    }
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    assert len(result.findings) == 0


def test_industry_fallback_to_manufacturing():
    """未知行业回退制造业并标注。"""
    raw = {
        "company_name": "未知行业公司",
        "industry": "量子计算",
        "years": [2023],
        "income_statement": {"营业收入": {2023: 0}},
        "balance_sheet": {},
        "account_balances": {},
    }
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    assert result.industry_fallback is True
    assert result.industry == "量子计算"  # 保留原行业名，仅回退基准
