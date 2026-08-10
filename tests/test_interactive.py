"""利润宝 · interactive.py 单元测试（S5）。"""
import os
import sys

import pytest

from core import parser as pr
from core import diagnostic as diag
from core import interactive as iv
from core import ai_engine
from data import make_sample

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope="module")
def sample_session():
    """构造样例会话（4 条发现）。"""
    raw = make_sample.build_sample_data()
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    sess = iv.start_session(data, result)
    return sess


def test_state_machine_initial_state(sample_session):
    """启动后进入 FINDING_LOOP。"""
    assert sample_session.state == iv.STATE_FINDING_LOOP


def test_state_machine_progression_all_a(sample_session):
    """连续选 A 可推进到 DRAFT2 → CONFIRMATION → FINAL；落地性=100%。"""
    sess = sample_session
    findings = list(sess.diagnosis.findings)
    for f in findings:
        iv.submit_decision(sess, f.id, "A", strategy_note=f"对{f.title}选A")
    assert sess.state == iv.STATE_CONFIRMATION
    assert sess.feasibility_score == 100.0
    iv.confirm(sess, user_confirmed=False)
    assert sess.state == iv.STATE_FINAL
    assert sess.is_export_unlocked is True


def test_default_all_a_feasibility_100():
    """默认全选 A 时落地性 = 100%。"""
    raw = make_sample.build_sample_data()
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    sess = iv.start_session(data, result)
    for f in result.findings:
        iv.submit_decision(sess, f.id, "A")
    assert sess.feasibility_score == 100.0


def test_high_risk_c_decreases_feasibility():
    """全选 C（高风险）落地性 < 95%。"""
    raw = make_sample.build_sample_data()
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    sess = iv.start_session(data, result)
    for f in result.findings:
        iv.submit_decision(sess, f.id, "C")
    # 4 条 C 高风险 → 100 - 4*12 = 52
    assert sess.feasibility_score < iv.FEASIBILITY_THRESHOLD
    assert sess.is_export_unlocked is False


def test_low_feasibility_blocks_export_until_confirm():
    """低于阈值且未确认不得解锁最终导出；确认后解锁。"""
    raw = make_sample.build_sample_data()
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    sess = iv.start_session(data, result)
    for f in result.findings:
        iv.submit_decision(sess, f.id, "C")
    assert sess.state == iv.STATE_CONFIRMATION
    assert sess.is_export_unlocked is False
    iv.confirm(sess, user_confirmed=True)
    assert sess.state == iv.STATE_FINAL
    assert sess.is_export_unlocked is True


def test_strategy_note_recorded(sample_session):
    """战略意图被记录到 session.strategy_notes。"""
    sess = sample_session
    # 已通过 test_state_machine_progression_all_a 记录了战略意图
    assert len(sess.strategy_notes) > 0
    assert any("研发" in note or "招待" in note or "咨询" in note or "税负" in note
               or "A" in note for note in sess.strategy_notes)


def test_draft2_has_four_elements(sample_session):
    """第二稿四要素齐全：环比同比 / 当前值→目标值→变动幅度→预计节税 / 操作细节 / 注意事项。"""
    sess = sample_session
    # 已通过 test_state_machine_progression_all_a 生成第二稿
    assert sess.draft2, "第二稿为空"
    for entry in sess.draft2:
        assert entry.trend, f"{entry.finding_id} 缺环比同比"
        assert entry.current_value is not None, f"{entry.finding_id} 缺当前值"
        assert entry.target_value is not None, f"{entry.finding_id} 缺目标值"
        assert entry.change_amount is not None, f"{entry.finding_id} 缺变动幅度"
        assert entry.est_saving is not None, f"{entry.finding_id} 缺预计节税"
        assert entry.action_detail, f"{entry.finding_id} 缺操作细节"
        assert entry.cautions, f"{entry.finding_id} 缺注意事项"
        # 注意事项含合规声明
        assert "合法税务筹划" in entry.cautions or "严禁" in entry.cautions


def test_offline_state_machine_reaches_final_without_ai():
    """未配置 AI 时状态机可达 FINAL。"""
    raw = make_sample.build_sample_data()
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    sess = iv.start_session(data, result)
    ai = ai_engine.AIEngine()  # 未配置
    assert ai.is_available() is False
    for f in result.findings:
        iv.submit_decision(sess, f.id, "A")
    iv.try_ai_enhance(sess, ai)
    assert "未配置" in sess.ai_fallback_message or "不可用" in sess.ai_fallback_message
    iv.confirm(sess, user_confirmed=True)
    assert sess.state == iv.STATE_FINAL


def test_ai_fallback_on_failure():
    """AI 配置但调用失败时回退规则引擎并展示提示。"""
    raw = make_sample.build_sample_data()
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    sess = iv.start_session(data, result)
    for f in result.findings:
        iv.submit_decision(sess, f.id, "A")
    # 配置一个不存在的端点，触发失败
    ai = ai_engine.AIEngine(
        base_url="http://127.0.0.1:39999",
        api_key="test-key",
        model="test-model",
        timeout=1.0,
    )
    assert ai.is_available() is True
    ok = iv.try_ai_enhance(sess, ai)
    assert ok is False
    assert "失败" in sess.ai_fallback_message or "回退" in sess.ai_fallback_message


def test_no_findings_skips_to_draft2():
    """无发现时直接进入 DRAFT2。"""
    raw = {
        "company_name": "正常公司",
        "industry": "制造业",
        "years": [2023],
        "income_statement": {
            "营业收入": {2023: 10_000_000},
            "营业成本": {2023: 7_000_000},
            "税金及附加": {2023: 36_000},
            "研发费用": {2023: 500_000},
            "所得税费用": {2023: 250_000},
            "净利润": {2023: 750_000},
        },
        "balance_sheet": {},
        "account_balances": {"业务招待费": 30_000, "咨询服务费": 100_000},
    }
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    assert len(result.findings) == 0
    sess = iv.start_session(data, result)
    assert sess.state == iv.STATE_DRAFT2 or sess.state == iv.STATE_CONFIRMATION


def test_total_est_saving_calculation(sample_session):
    """总预计节税 = 各决策节税之和。"""
    sess = sample_session
    if not sess.decisions:
        pytest.skip("无决策记录")
    expected = round(sum(d.est_saving for d in sess.decisions), 2)
    assert sess.total_est_saving == expected


def test_feasibility_summary_explainable(sample_session):
    """落地性可解释。"""
    sess = sample_session
    summary = iv.feasibility_summary(sess)
    assert "落地性评分" in summary
    assert "阈值" in summary or "确认" in summary
