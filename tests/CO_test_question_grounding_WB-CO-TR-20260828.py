"""出题锚定校验（question grounding）单测：AI 只出题、锚定由确定性引擎把关。

背景（2026-08-28）：借鉴开源法律咨询项目问诊方法论（DISC-LawLLM 三段论、
Intelligent Legal Assistant 澄清问题设计、律所 intake 问卷原则），
要求 AI 题目必须引用本案例数字/科目；未锚定的题目由确定性守卫拦截并回退规则引擎。
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

from core.ai_engine import (
    AIEngine,
    AIEngineError,
    _case_number_anchors,
    _text_is_grounded,
)
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


# ── 锚点提取与判定 ───────────────────────────────────────────────


def test_anchors_contain_amounts_years_and_conversions():
    anchors = _case_number_anchors(_sample_data())
    assert 80_000_000 in anchors          # 科目金额原值
    assert 2023 in anchors and 2024 in anchors  # 年份
    assert 8000.0 in anchors              # 8000 万（元→万元换算）
    assert 5.0 in anchors or 5.5 in anchors      # 资产总额 5500 万的亿元换算舍入
    assert _case_number_anchors(None) == set()


def test_text_grounded_by_amount_formats():
    data = _sample_data()
    assert _text_is_grounded("管理费用 2024=6,000,000 元，占营收 7.5%", data)
    assert _text_is_grounded("应收账款高达 3500万，需关注回款", data)  # 万元叙述
    assert _text_is_grounded("营收 0.8亿 与上年 1亿 相比下滑", data)   # 亿元/亿叙述
    assert _text_is_grounded("净利润率约 5%，与行业接近", data)        # 指标值锚点


def test_text_grounded_by_account_name():
    assert _text_is_grounded("应收账款增长较快，请确认回款政策", _sample_data())
    assert not _text_is_grounded("咨询费偏高需四流合一", _sample_data())  # 科目不存在


def test_text_ungrounded_generic():
    data = _sample_data()
    assert not _text_is_grounded("企业税负较高，建议优化业务模式", data)
    assert not _text_is_grounded("请关注金税四期合规风险", data)
    assert not _text_is_grounded("", data)


def test_text_grounded_lenient_without_data():
    assert _text_is_grounded("任意通用文本", None)


def test_text_grounded_by_finding_values():
    data = _sample_data()
    finding = Finding(
        id="X", title="t", category="税负率", severity="中",
        fact="f", benchmark="b", suggestion="s",
        current_value=43.7, target_value=30.0,
    )
    assert _text_is_grounded("当前 43.7%，建议压降至 30%", data, finding=finding)


# ── generate_options 锚定守卫 ────────────────────────────────────

_OPTIONS_GROUNDED = """[
  {"label":"A","name":"压降管理费用","description":"管理费用从 6,000,000 元压降至 5,000,000 元","target_value":5000000,"est_saving":0,"feasibility":"中","risk_level":"低","action_note":"按预算执行"},
  {"label":"B","name":"分阶段压降","description":"先降至 5,500,000 元再观察","target_value":5500000,"est_saving":0,"feasibility":"高","risk_level":"低"},
  {"label":"C","name":"暂维持","description":"维持 6,000,000 元并加强审批","target_value":6000000,"est_saving":0,"feasibility":"高","risk_level":"中"}
]"""

_OPTIONS_GENERIC = """[
  {"label":"A","name":"积极优化","description":"全面优化费用结构提升效益","target_value":1,"est_saving":0,"feasibility":"中","risk_level":"低"},
  {"label":"B","name":"平衡推进","description":"分阶段推进优化","target_value":1,"est_saving":0,"feasibility":"高","risk_level":"低"},
  {"label":"C","name":"暂维持","description":"维持现状关注风险","target_value":1,"est_saving":0,"feasibility":"高","risk_level":"中"}
]"""


def _finding() -> Finding:
    return Finding(
        id="MGMT_HIGH", title="管理费用偏高", category="成本费用结构", severity="中",
        fact="管理费用 2024=6,000,000 元，占营收 7.5%", benchmark="行业 3%~5%",
        suggestion="压降管理费用", current_value=7.5, target_value=5.0, unit="%",
        options=[
            Option(label="A", name="a", description="d", target_value=1),
            Option(label="B", name="b", description="d", target_value=1),
            Option(label="C", name="c", description="d", target_value=1),
        ],
    )


def test_generate_options_ungrounded_raises(monkeypatch):
    engine = AIEngine(base_url="https://x", api_key="k", model="m")
    monkeypatch.setattr(engine, "_chat", lambda messages, max_tokens=400: _OPTIONS_GENERIC)
    with pytest.raises(AIEngineError, match="未锚定"):
        engine.generate_options(_finding(), data=_sample_data())


def test_generate_options_grounded_passes(monkeypatch):
    engine = AIEngine(base_url="https://x", api_key="k", model="m")
    monkeypatch.setattr(engine, "_chat", lambda messages, max_tokens=400: _OPTIONS_GROUNDED)
    options = engine.generate_options(_finding(), data=_sample_data())
    assert [o.label for o in options] == ["A", "B", "C"]


def test_generate_options_without_data_skips_gate(monkeypatch):
    engine = AIEngine(base_url="https://x", api_key="k", model="m")
    monkeypatch.setattr(engine, "_chat", lambda messages, max_tokens=400: _OPTIONS_GENERIC)
    options = engine.generate_options(_finding())
    assert len(options) == 3


# ── discover_findings 锚定丢弃 ──────────────────────────────────


def test_discover_drops_ungrounded_keeps_grounded(monkeypatch):
    engine = AIEngine(base_url="https://x", api_key="k", model="m")
    payload = """
    {"findings":[
      {"id":"AI_AR","title":"应收占比高","category":"成本费用结构","severity":"高",
       "fact":"应收账款 2024=35,000,000 元，占营收 43.75%",
       "benchmark":"经营质量","suggestion":"抓回款",
       "current_value":43.75,"target_value":30,"unit":"%",
       "options":[
         {"label":"A","name":"A1","description":"清单化清收 35,000,000 元应收","target_value":30,"est_saving":0,"feasibility":"中","risk_level":"低"},
         {"label":"B","name":"B1","description":"先清大额应收","target_value":35,"est_saving":0,"feasibility":"高","risk_level":"低"},
         {"label":"C","name":"C1","description":"维持并计提坏账准备","target_value":44,"est_saving":0,"feasibility":"高","risk_level":"中"}
       ]},
      {"id":"AI_GENERIC","title":"合规意识待提升","category":"真实性风险","severity":"中",
       "fact":"企业整体合规意识有待提升，建议加强培训",
       "benchmark":"管理经验","suggestion":"加强培训",
       "current_value":0,"target_value":0,"unit":"%",
       "options":[
         {"label":"A","name":"A","description":"开展培训","target_value":0,"est_saving":0,"feasibility":"中","risk_level":"低"},
         {"label":"B","name":"B","description":"制度建设","target_value":0,"est_saving":0,"feasibility":"高","risk_level":"低"},
         {"label":"C","name":"C","description":"暂缓","target_value":0,"est_saving":0,"feasibility":"高","risk_level":"中"}
       ]}
    ]}
    """
    monkeypatch.setattr(engine, "_chat", lambda messages, max_tokens=400: payload)
    extra = engine.discover_findings(_sample_data(), existing=[])
    assert [f.id for f in extra] == ["AI_AR"]


# ── enrich_interaction_question 锚定守卫 ─────────────────────────

_GROUNDED_OPTIONS = [
    Option(label="A", name="压降", description="降至 5,000,000 元", target_value=5_000_000),
    Option(label="B", name="分阶段", description="先降至 5,500,000 元", target_value=5_500_000),
    Option(label="C", name="维持", description="维持 6,000,000 元", target_value=6_000_000),
]


def test_enrich_ungrounded_keeps_original(monkeypatch):
    engine = AIEngine(base_url="https://x", api_key="k", model="m")
    monkeypatch.setattr(
        engine, "generate_options",
        lambda finding, data=None, prior_decisions=None, strategy_notes=None: list(_GROUNDED_OPTIONS),
    )
    monkeypatch.setattr(
        engine, "_chat",
        lambda messages, max_tokens=400: (
            '{"question_title":"费用优化","plain_fact":"贵司费用偏高需要优化",'
            '"why_it_matters":"不管会增加成本","suggestion_prompt":"请选择"}'
        ),
    )
    finding = _finding()
    with pytest.raises(AIEngineError, match="未锚定"):
        engine.enrich_interaction_question(finding, data=_sample_data())
    # 抛错发生在赋值前：原题面保持不变
    assert finding.title == "管理费用偏高"
    assert "6,000,000" in finding.fact


def test_enrich_grounded_applies(monkeypatch):
    engine = AIEngine(base_url="https://x", api_key="k", model="m")
    monkeypatch.setattr(
        engine, "generate_options",
        lambda finding, data=None, prior_decisions=None, strategy_notes=None: list(_GROUNDED_OPTIONS),
    )
    monkeypatch.setattr(
        engine, "_chat",
        lambda messages, max_tokens=400: (
            '{"question_title":"管理费用决策","plain_fact":"2024 年管理费用 6,000,000 元占营收 7.5%",'
            '"why_it_matters":"高于行业 3%~5% 区间","suggestion_prompt":"请选择处理方式"}'
        ),
    )
    finding = _finding()
    enriched = engine.enrich_interaction_question(finding, data=_sample_data())
    assert enriched.title == "管理费用决策"
    assert "6,000,000" in enriched.fact


# ── 月度拆分问题锚定校验（web_backend.CO_ai） ────────────────────


class _FakeResult:
    def __init__(self, content: str):
        self.content = content


class _FakeEngine:
    def __init__(self, content: str):
        self._content = content

    def chat_result(self, *args, **kwargs):
        return _FakeResult(self._content)


def _monthly_ai_mod():
    return importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")


def _plan_snapshot() -> dict:
    return {
        "rows": [
            {"row": 1, "subject": "管理费用", "expense_name": "工资薪金", "annual": 1_200_000},
            {"row": 2, "subject": "销售费用", "expense_name": "广宣费", "annual": 800_000},
            {"row": 3, "subject": "管理费用", "expense_name": "房租物业", "annual": 360_000},
        ]
    }


def test_monthly_questions_grounded(monkeypatch):
    ai_mod = _monthly_ai_mod()
    content = (
        '{"questions":['
        '{"id":"q_salary","type":"single","title":"工资薪金 1,200,000 元是否按月平摊？","options":["按月平摊","年底集中"],"default":"按月平摊"},'
        '{"id":"q_ad","type":"single","title":"广宣费 800,000 元的投放节奏？","options":["旺季集中","全年平摊"],"default":"旺季集中"},'
        '{"id":"q_rent","type":"single","title":"房租物业 360,000 元支付节奏？","options":["按月","按季"],"default":"按月"},'
        '{"id":"q_bonus","type":"text","title":"工资薪金中年终奖发放月份与金额？","default":"","placeholder":"如 2 月 200,000 元"}'
        "]}"
    )
    monkeypatch.setattr(ai_mod, "_engine", lambda timeout=60.0: _FakeEngine(content))
    questions, error = ai_mod.generate_monthly_questions(_plan_snapshot())
    assert error == ""
    assert len(questions) == 4


def test_monthly_questions_ungrounded_dropped(monkeypatch):
    ai_mod = _monthly_ai_mod()
    content = (
        '{"questions":['
        '{"id":"q_salary","type":"single","title":"工资薪金 1,200,000 元是否按月平摊？","options":["按月平摊","年底集中"],"default":"按月平摊"},'
        '{"id":"q_generic1","type":"single","title":"公司整体预算管理水平如何？","options":["好","一般"],"default":"好"},'
        '{"id":"q_generic2","type":"single","title":"是否希望控制总体税负？","options":["是","否"],"default":"是"},'
        '{"id":"q_generic3","type":"text","title":"对未来经营有何规划？","default":"","placeholder":"描述"}'
        "]}"
    )
    monkeypatch.setattr(ai_mod, "_engine", lambda timeout=60.0: _FakeEngine(content))
    questions, error = ai_mod.generate_monthly_questions(_plan_snapshot())
    assert questions == []
    assert "未锚定" in error  # 有效题不足 4 → 整体回退规则题库
