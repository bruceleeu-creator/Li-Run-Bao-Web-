"""利润宝 · 互动决策确定性与防跳项测试（T6.6-P4，v32 契约重写）。

v32 起 start_session 按互动队列出题（高→中→低、同级金额降序），低价值发现
自动暂维持不逐题问（auto_decisions 不进题队列、不扣落地性）。状态机严格按
队列推进、不允许跳项，因此"顺序无关性"体现为跨运行确定性：

- 同一份诊断结果，多次独立全 A/全 B/全 C/混合提交，最终状态、落地性、
  总预计节税与决策记录集合完全一致
- 题队列必须取自 start_session 之后的 sess.interactive_ids（不含 auto 项）
- 状态机不允许"跳项"或"漏项"（提交非 current 的 id 返回 None 且不推进）

出题顺序契约（高→低、首题=最高严重度）由 tests/test_interactive.py 的
test_interaction_order_high_to_low 覆盖，本文件不重复断言。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import parser as pr
from core import diagnostic as diag
from core import interactive as iv
from data import make_sample


@pytest.fixture
def base_data_and_diagnosis():
    """构造样例数据与诊断结果（基准，所有运行共用）。"""
    raw = make_sample.build_sample_data()
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    return data, result


def _run_decisions(data, result, option="A", option_fn=None):
    """按会话题队列逐项提交决策，返回最终 session。

    题队列取自 start_session 之后的 sess.interactive_ids（低价值 auto 项
    不在其中）；逐项断言 current_finding 与队列一致，防跳项/漏项。
    option_fn(i, finding_id) 优先于固定 option，用于混合选项场景。
    """
    sess = iv.start_session(data, result)
    for i, finding_id in enumerate(sess.interactive_ids):
        assert sess.current_finding is not None, "题队列未完成但 current_finding 为空"
        assert sess.current_finding.id == finding_id, (
            f"状态机跳项：期望 {finding_id}，实际 current_finding={sess.current_finding.id}"
        )
        opt = option_fn(i, finding_id) if option_fn else option
        iv.submit_decision(sess, finding_id, opt)
    return sess


def test_all_a_decisions_idempotent(base_data_and_diagnosis):
    """全选 A：两轮独立运行最终态一致，全 A 落地性 100%，起始题为队列首项。"""
    data, result = base_data_and_diagnosis
    sess1 = _run_decisions(data, result, "A")
    sess2 = _run_decisions(data, result, "A")
    # 新会话起始题必须等于互动队列首项（防起始错位）
    fresh = iv.start_session(data, result)
    assert fresh.current_finding is not None
    assert fresh.current_finding.id == fresh.interactive_ids[0], (
        "状态机起始 current_finding 应为互动队列首项"
    )
    # 全选 A 应进入 CONFIRMATION，落地性 100%（auto 决策不扣分）
    assert sess1.state == iv.STATE_CONFIRMATION
    assert sess1.feasibility_score == 100.0
    assert sess2.state == sess1.state
    assert sess2.feasibility_score == sess1.feasibility_score


def test_all_a_same_total_saving_across_runs(base_data_and_diagnosis):
    """两次独立运行全选 A：总预计节税与决策记录集合应一致且覆盖全部互动题。"""
    data, result = base_data_and_diagnosis
    sess1 = _run_decisions(data, result, "A")
    sess2 = _run_decisions(data, result, "A")
    assert sess1.total_est_saving == sess2.total_est_saving
    assert sess1.feasibility_score == sess2.feasibility_score
    # 决策记录集合一致（Decision 字段为 option_label）
    set1 = {(d.finding_id, d.option_label) for d in sess1.decisions}
    set2 = {(d.finding_id, d.option_label) for d in sess2.decisions}
    assert set1 == set2
    # 决策集合必须覆盖全部互动题（防止提交被静默拒绝导致空泛通过）
    assert len(set1) == len(sess1.interactive_ids)


def test_state_machine_rejects_skipping(base_data_and_diagnosis):
    """状态机不允许跳项：提交非 current_finding 的 id 应返回 None 且不推进状态。"""
    data, result = base_data_and_diagnosis
    sess = iv.start_session(data, result)
    if len(sess.interactive_ids) < 2:
        pytest.skip("互动题数不足 2，无法测试跳项")
    current_id = sess.current_finding.id
    # 找一个非 current 的互动题 id
    other_id = next(fid for fid in sess.interactive_ids if fid != current_id)
    # 提交非 current 的 id 应返回 None（状态机不跳项）
    result_decision = iv.submit_decision(sess, other_id, "A")
    assert result_decision is None, "跳项提交应返回 None"
    # 状态未推进：current_finding 仍是原值
    assert sess.current_finding is not None
    assert sess.current_finding.id == current_id, "跳项提交后 current_finding 不应推进"
    # 决策记录为空
    assert len(sess.decisions) == 0


def test_state_machine_no_skip_after_partial_submission(base_data_and_diagnosis):
    """部分提交后，current_finding 仍按互动队列推进，不允许跳到后续未决项。"""
    data, result = base_data_and_diagnosis
    sess = iv.start_session(data, result)
    queue = list(sess.interactive_ids)
    if len(queue) < 3:
        pytest.skip("互动题数不足 3，无法测试部分跳项")
    # 提交第一个
    iv.submit_decision(sess, queue[0], "A")
    # current_finding 应为第二个
    assert sess.current_finding is not None
    assert sess.current_finding.id == queue[1], (
        f"提交第一项后 current_finding 应为 {queue[1]}，实际 {sess.current_finding.id}"
    )
    # 尝试提交第三个（跳过第二个）应返回 None
    result_decision = iv.submit_decision(sess, queue[2], "A")
    assert result_decision is None, "跳项提交应返回 None"
    # current_finding 仍是第二个
    assert sess.current_finding is not None
    assert sess.current_finding.id == queue[1]


def test_all_b_decisions_deterministic(base_data_and_diagnosis):
    """全选 B：两次独立运行的落地性与节税合计应一致。"""
    data, result = base_data_and_diagnosis
    sess1 = _run_decisions(data, result, "B")
    sess2 = _run_decisions(data, result, "B")
    assert sess1.feasibility_score == sess2.feasibility_score
    assert sess1.total_est_saving == sess2.total_est_saving


def test_all_c_decisions_deterministic(base_data_and_diagnosis):
    """全选 C：两次独立运行的落地性应一致，且应低于全选 A。"""
    data, result = base_data_and_diagnosis
    sess_c1 = _run_decisions(data, result, "C")
    sess_c2 = _run_decisions(data, result, "C")
    assert sess_c1.feasibility_score == sess_c2.feasibility_score
    # 全选 A 应落地性更高（全 C：高严重度逐项扣分，auto 项不扣）
    sess_a = _run_decisions(data, result, "A")
    assert sess_a.feasibility_score >= sess_c1.feasibility_score


def test_mixed_options_deterministic(base_data_and_diagnosis):
    """混合选项 A/B/C 交替：两次独立运行的最终态与节税合计应一致。"""
    data, result = base_data_and_diagnosis
    options_cycle = ["A", "B", "C"]

    def pick(i, fid):
        return options_cycle[i % len(options_cycle)]

    sess1 = _run_decisions(data, result, option_fn=pick)
    sess2 = _run_decisions(data, result, option_fn=pick)
    assert sess1.feasibility_score == sess2.feasibility_score
    assert sess1.total_est_saving == sess2.total_est_saving
    set1 = {(d.finding_id, d.option_label) for d in sess1.decisions}
    set2 = {(d.finding_id, d.option_label) for d in sess2.decisions}
    assert set1 == set2
    # 决策集合必须覆盖全部互动题（防止提交被静默拒绝导致空泛通过）
    assert len(set1) == len(sess1.interactive_ids)
    assert sess1.state == iv.STATE_CONFIRMATION
