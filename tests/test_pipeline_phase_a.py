"""Phase A：通用管线门面 + 导入响应契约。"""
from __future__ import annotations

import json
from pathlib import Path

from core import budget as budget_mod
from core import pipeline as pipe
from core.models import FinancialData

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "demo_output" / "cases" / "audit_3years" / "gold_yikang.json"


def _gold_data() -> FinancialData:
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
    data.parsed_meta = {"matched_cells": 30, "text_layer": True}
    return data


def test_assemble_bundle_writes_policy_and_quality():
    data = _gold_data()
    bundle = pipe.run_case_pipeline_from_data(
        data,
        options=pipe.PipelineOptions(
            company_name=data.company_name,
            industry="建筑业",
            case_id="audit_3years",
            income_tax_nominal_rate=0.15,
        ),
    )
    assert bundle.case_id == "audit_3years"
    assert bundle.policy.e4_income_tax_rate == 0.15
    assert bundle.policy.e3_company_contribution >= 0.015
    assert bundle.policy.industry_key == "建筑业"
    assert "policy" in (bundle.financial_data.parsed_meta or {})
    assert bundle.quality.get("confidence") in ("high", "medium", "low")


def test_import_response_keys_contract():
    data = _gold_data()
    bundle = pipe.run_case_pipeline_from_data(
        data,
        options=pipe.PipelineOptions(industry="建筑业", case_id=None),
    )
    resp = bundle.to_import_response(
        indicators=[],
        previews=[],
        summary={"matched": 10},
    )
    missing = pipe.IMPORT_RESPONSE_KEYS - set(resp.keys())
    assert not missing, f"缺少键 {missing}"
    assert resp["case_id"] is None
    assert "e3" in resp["policy"]
    assert "e4" in resp["policy"]


def test_apply_policy_to_plan():
    data = _gold_data()
    bundle = pipe.run_case_pipeline_from_data(
        data, options=pipe.PipelineOptions(industry="建筑业")
    )
    plan = budget_mod.make_empty_plan(
        company_name="测", industry="制造业", year=2024
    )
    notes = pipe.apply_policy_to_plan(plan, bundle.policy)
    assert plan.industry == "建筑业"
    assert plan.top_inputs.income_tax_rate == 0.15
    assert plan.top_inputs.company_contribution_rate >= 0.015
    assert notes


def test_policy_from_data_roundtrip():
    data = _gold_data()
    bundle = pipe.run_case_pipeline_from_data(
        data, options=pipe.PipelineOptions(industry="建筑业")
    )
    restored = pipe.policy_from_data(bundle.financial_data)
    assert abs(restored.e3_company_contribution - bundle.policy.e3_company_contribution) < 1e-9
    assert restored.e4_income_tax_rate == bundle.policy.e4_income_tax_rate


def test_resolve_cit_rate_and_stamp_findings():
    data = _gold_data()
    bundle = pipe.run_case_pipeline_from_data(
        data, options=pipe.PipelineOptions(industry="建筑业", income_tax_nominal_rate=0.15)
    )
    assert pipe.resolve_cit_rate(bundle.financial_data) == 0.15
    from core.diagnostic import Option, Finding

    f = Finding(
        id="T",
        title="t",
        category="税负率",
        severity="低",
        fact="f",
        benchmark="b",
        suggestion="s",
        options=[Option(label="A", name="n", description="d", target_value=1.0, tax_rate=0.25)],
    )
    pipe.stamp_findings_tax_rate([f], bundle.financial_data)
    assert f.options[0].tax_rate == 0.15


def test_sample_like_synthetic_other_company():
    """非艺康合成数据也能走同一管线（任意案例）。"""
    data = FinancialData(company_name="某某制造有限公司", industry="制造业", years=[2023, 2024])
    data.income_statement = {
        "营业收入": {2023: 10_000_000, 2024: 12_000_000},
        "营业成本": {2023: 7_000_000, 2024: 8_000_000},
        "销售费用": {2023: 200_000, 2024: 250_000},
        "管理费用": {2023: 500_000, 2024: 550_000},
        "研发费用": {2023: 100_000, 2024: 120_000},
        "财务费用": {2023: 50_000, 2024: 40_000},
        "利润总额": {2023: 1_000_000, 2024: 1_200_000},
        "所得税费用": {2023: 150_000, 2024: 180_000},
        "净利润": {2023: 850_000, 2024: 1_020_000},
    }
    data.parsed_meta = {"matched_cells": 20, "text_layer": True}
    bundle = pipe.run_case_pipeline_from_data(
        data,
        options=pipe.PipelineOptions(
            company_name="某某制造有限公司",
            industry="制造业",
            case_id=None,
        ),
    )
    assert bundle.policy.industry_key == "制造业"
    assert bundle.policy.e4_income_tax_rate == 0.15
    # 所得税/营收 180k/12M = 1.5%
    assert abs(bundle.policy.e3_company_contribution - 0.015) < 1e-4 or (
        bundle.policy.e3_company_contribution >= 0.015
    )
    resp = bundle.to_import_response(summary={})
    assert pipe.IMPORT_RESPONSE_KEYS <= set(resp.keys())
