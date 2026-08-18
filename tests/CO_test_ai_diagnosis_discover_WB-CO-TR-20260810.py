"""DeepSeek 诊断补充发现 / 互动出题解析单测（无外网）。"""

from __future__ import annotations

import os
import sys

import pytest

from core.ai_engine import AIEngine, AIEngineError
from core.diagnostic import Finding, Option
from core.models import FinancialData

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _sample_data() -> FinancialData:
    return FinancialData(
        company_name="测试装饰公司",
        industry="建筑业",
        years=[2023, 2024],
        income_statement={
            "营业收入": {2023: 100_000_000, 2024: 80_000_000},
            "营业成本": {2023: 80_000_000, 2024: 60_000_000},
            "税金及附加": {2023: 200_000, 2024: 160_000},
            "管理费用": {2023: 5_000_000, 2024: 6_000_000},
            "研发费用": {2023: 0, 2024: 0},
            "净利润": {2023: 3_000_000, 2024: 4_000_000},
            "所得税费用": {2023: 500_000, 2024: 600_000},
        },
        balance_sheet={
            "资产总额": {2023: 50_000_000, 2024: 55_000_000},
            "负债总额": {2023: 30_000_000, 2024: 28_000_000},
            "应收账款": {2023: 20_000_000, 2024: 35_000_000},
        },
        account_balances={},
    )


def test_compact_financial_context_contains_years():
    ctx = AIEngine.compact_financial_context(_sample_data())
    assert "测试装饰公司" in ctx
    assert "2023" in ctx and "2024" in ctx
    assert "营业收入" in ctx
    assert "毛利率" in ctx


def test_parse_options_json_abc():
    content = """[
      {"label":"A","name":"积极","description":"马上做","target_value":10,"est_saving":1000,"feasibility":"中","risk_level":"低","action_note":"执行"},
      {"label":"B","name":"平衡","description":"分步","target_value":8,"est_saving":500,"feasibility":"高","risk_level":"低"},
      {"label":"C","name":"暂维持","description":"观察","target_value":5,"est_saving":0,"feasibility":"高","risk_level":"高"}
    ]"""
    finding = Finding(
        id="X", title="t", category="成本费用结构", severity="中",
        fact="f", benchmark="b", suggestion="s",
    )
    opts = AIEngine._parse_options_json(content, finding)
    assert [o.label for o in opts] == ["A", "B", "C"]
    assert opts[0].est_saving == 1000


def test_parse_findings_payload_object():
    content = """
    {"findings":[
      {"id":"AI_AR_PRESSURE","title":"应收过高","category":"成本费用结构","severity":"高",
       "fact":"应收占收入高","benchmark":"管理经验","suggestion":"抓回款",
       "current_value":70,"target_value":40,"unit":"%",
       "options":[
         {"label":"A","name":"清单清收","description":"每周跟进","target_value":40,"est_saving":0,"feasibility":"中","risk_level":"低"},
         {"label":"B","name":"分批","description":"先大户","target_value":50,"est_saving":0,"feasibility":"高","risk_level":"低"},
         {"label":"C","name":"暂维持","description":"观察","target_value":70,"est_saving":0,"feasibility":"高","risk_level":"高"}
       ]}
    ]}
    """
    items = AIEngine._parse_findings_payload(content)
    assert len(items) == 1
    f = AIEngine._finding_from_dict(items[0], default_id="AI_AR_PRESSURE")
    assert f is not None
    assert f.id == "AI_AR_PRESSURE"
    assert len(f.options) == 3


def test_discover_findings_merges_mock(monkeypatch):
    """mock _chat 返回补充发现，确保不与已有 id 冲突。"""
    engine = AIEngine(base_url="https://x", api_key="k", model="m")
    payload = """
    {"findings":[
      {"id":"AI_CASH","title":"利润现金背离","category":"成本费用结构","severity":"高",
       "fact":"利润升应收升","benchmark":"经营质量","suggestion":"抓回款",
       "current_value":1,"target_value":0,"unit":"%",
       "options":[
         {"label":"A","name":"A1","description":"d","target_value":0,"est_saving":0,"feasibility":"中","risk_level":"低"},
         {"label":"B","name":"B1","description":"d","target_value":0,"est_saving":0,"feasibility":"高","risk_level":"低"},
         {"label":"C","name":"C1","description":"d","target_value":0,"est_saving":0,"feasibility":"高","risk_level":"中"}
       ]},
      {"id":"RD_MISSING","title":"重复的研发缺失","category":"成本费用结构","severity":"高",
       "fact":"应被去重","benchmark":"x","suggestion":"y",
       "current_value":0,"target_value":5,"unit":"%",
       "options":[
         {"label":"A","name":"A","description":"d","target_value":0,"est_saving":0,"feasibility":"中","risk_level":"低"},
         {"label":"B","name":"B","description":"d","target_value":0,"est_saving":0,"feasibility":"高","risk_level":"低"},
         {"label":"C","name":"C","description":"d","target_value":0,"est_saving":0,"feasibility":"高","risk_level":"中"}
       ]}
    ]}
    """
    monkeypatch.setattr(engine, "_chat", lambda messages, max_tokens=400: payload)
    existing = [
        Finding(
            id="RD_MISSING", title="研发费用缺失（该有没的）",
            category="成本费用结构", severity="高",
            fact="0", benchmark="b", suggestion="s",
            options=[
                Option(label="A", name="a", description="d", target_value=1),
                Option(label="B", name="b", description="d", target_value=1),
                Option(label="C", name="c", description="d", target_value=0),
            ],
        )
    ]
    extra = engine.discover_findings(_sample_data(), existing=existing)
    assert len(extra) == 1
    assert extra[0].id == "AI_CASH"
    assert all(o.label in ("A", "B", "C") for o in extra[0].options)


def test_discover_findings_raises_on_bad_json(monkeypatch):
    engine = AIEngine(base_url="https://x", api_key="k", model="m")
    monkeypatch.setattr(engine, "_chat", lambda messages, max_tokens=400: "not-json")
    with pytest.raises(AIEngineError):
        engine.discover_findings(_sample_data(), existing=[])
