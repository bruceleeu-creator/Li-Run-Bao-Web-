"""利润宝 · 月度拆分引擎单元测试。

覆盖：尾差归位、恒等校验（PBT）、0 元行、规则形状表驱动、AI 权重容错、
题库结构、数字白名单告警。全部确定性，不依赖 AI 与数据库。
"""

from __future__ import annotations

import importlib
import random

import pytest

m = importlib.import_module("core.CO_monthly_split_WB-CO-TR-20260820")


def _row(row, subject, name, annual):
    return {"row": row, "subject": subject, "expense_name": name, "annual": annual}


# ── 权重与金额数学 ──────────────────────────────────────────────────────

def test_distribute_tail_diff_single_yuan():
    """尾差归位（设计文档 §2.3）：diff 整体加到 argmax 月，Σ月精确等于年度。

    100,001 = 8333×12 + 5 → 均匀权重下恰有一个月为 8338（base+5），
    其余 11 个月为 8333；annual=25 时尾差恰为 1 → 仅一月多 1 元。
    """
    months = m.distribute(100001)
    assert sum(months) == 100001
    base = 100001 // 12  # 8333
    off = [v for v in months if v != base]
    assert len(off) == 1 and off[0] == base + 5  # 尾差 5 整体归位到一个月
    assert all(v == base for v in months[1:])    # 均匀权重 argmax 平局取首月

    months25 = m.distribute(25)
    assert sum(months25) == 25
    assert months25[0] == 3 and all(v == 2 for v in months25[1:])  # 仅一月多 1 元


def test_distribute_zero_annual_all_zero():
    """0 元行 12 个月全 0。"""
    assert m.distribute(0) == [0.0] * 12
    assert m.distribute(0, [0.5] * 12) == [0.0] * 12


def test_verify_random_pbt():
    """PBT：随机 100 行 × 随机权重，恒等校验全部通过（固定种子）。"""
    rng = random.Random(42)
    rows = []
    for i in range(100):
        annual = rng.choice([0, 1, 99, 12_345.67, 222_632_373.93, 7])
        weights = [rng.random() for _ in range(12)]
        rows.append(m.MonthlyRow(i + 1, "测试科目", "测试费用", annual, m.distribute(annual, weights)))
    checks = m.verify(rows)
    assert checks["row_failures"] == 0
    assert checks["total_gap"] == 0


def test_normalize_weights_rules():
    w = m.normalize_weights([-1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
    assert abs(sum(w) - 1.0) < 1e-12 and w[0] == 0.0
    assert m.normalize_weights([0] * 12) == [1.0 / 12] * 12  # 全零回退均匀
    with pytest.raises(ValueError):
        m.normalize_weights([float("nan")] + [1.0] * 11)
    with pytest.raises(ValueError):
        m.normalize_weights([1.0] * 11)
    with pytest.raises(ValueError):
        m.normalize_weights([1.0] * 12 + [1.0])


def test_ai_weights_valid_threshold():
    assert m.ai_weights_valid([1 / 12] * 12)
    assert not m.ai_weights_valid([-0.1] + [0.1] * 11)          # 负权重
    assert not m.ai_weights_valid([0.2] * 12)                     # Σ=2.4 偏离 1 超过 0.05
    assert not m.ai_weights_valid([1.0] + [None] * 11)            # 非数值
    assert not m.ai_weights_valid([1.0] * 11)                     # 长度不足


# ── 规则形状（表驱动） ──────────────────────────────────────────────────

def test_rule_rigid_uniform():
    plan = [_row(1, "管理费用", "管理人员工资", 120000), _row(2, "销售费用", "广告宣传费", 120000)]
    result = m.rule_split(plan, {}, budget_year=2026)
    r1 = result.rows[0]
    assert r1.shape == "uniform" and r1.months == [10000.0] * 12
    assert result.mode == "rule" and result.checks["row_failures"] == 0


def test_rule_peak_at_spring_month():
    plan = [_row(5, "管理费用", "年终奖", 130000)]
    result = m.rule_split(plan, {"q_bonus": "年终奖2~3个月工资"}, budget_year=2026)
    r = result.rows[0]
    assert r.shape == "peak"
    spring = m.spring_festival_month(2026)  # 2026 春节在 2 月
    assert spring == 2
    assert r.months[spring - 1] == max(r.months)
    # 其余 11 个月均摊
    others = [v for i, v in enumerate(r.months) if i != spring - 1]
    assert len(set(others)) == 1
    assert sum(r.months) == 130000


def test_rule_campaign_front_load():
    plan = [_row(9, "销售费用", "广宣费", 120000)]
    result = m.rule_split(
        plan,
        {"q_season": "下半年旺", "q_campaign": "旺季前1~2个月前置投放"},
        budget_year=2026,
    )
    r = result.rows[0]
    assert r.shape == "front_load"
    # 窗口 7/8 月权重 1.8、其余 1.0 → 7/8 月金额严格高于其他月
    assert r.months[6] > r.months[8] and r.months[7] > r.months[8]
    assert sum(r.months) == 120000


def test_rule_lump_full_month():
    plan = [_row(14, "管理费用", "装修费", 500000)]
    result = m.rule_split(plan, {"q_lump": "3月 50万 装修费"}, budget_year=2026)
    r = result.rows[0]
    assert r.shape == "lump"
    assert r.months[2] == 500000 and sum(r.months[2:]) == 500000
    assert all(v == 0 for i, v in enumerate(r.months) if i != 2)


def test_rule_lump_over_budget_warns_not_change():
    """声明金额超年度 → 告警但年度值不变、恒等仍成立。"""
    plan = [_row(14, "管理费用", "装修费", 300000)]
    result = m.rule_split(plan, {"q_lump": "3月 50万 装修费"}, budget_year=2026)
    assert any("超过年度预算" in w for w in result.warnings)
    assert result.rows[0].annual == 300000
    assert sum(result.rows[0].months) == 300000


def test_rule_lump_no_match_warns():
    plan = [_row(14, "管理费用", "装修费", 300000)]
    result = m.rule_split(plan, {"q_lump": "5月 10万 展会"}, budget_year=2026)
    assert any("未匹配到预算行" in w for w in result.warnings)
    assert result.rows[0].shape == "uniform"


def test_rule_rigid_not_flat_answer_warns():
    plan = [_row(1, "管理费用", "房租", 120000)]
    result = m.rule_split(plan, {"q_fixed": "否（不平摊）"}, budget_year=2026)
    assert any("不平摊" in w for w in result.warnings)
    assert result.rows[0].months == [10000.0] * 12  # 仍按均摊


def test_spring_festival_month_mapping():
    assert m.spring_festival_month(2025) == 1
    assert m.spring_festival_month(2026) == 2
    assert m.spring_festival_month(2030) == 2
    assert m.spring_festival_month(0) == 2          # 未知年份兜底
    assert m.spring_festival_month(2050) in (1, 2)  # 超表取最近


# ── AI 权重合并 ────────────────────────────────────────────────────────

def _plan(n=5):
    return [_row(i + 1, "管理费用", f"费用{i + 1}", 10000 * (i + 1)) for i in range(n)]


def test_merge_ai_missing_rows_fallback_uniform():
    plan = _plan(5)
    ai_rows = [
        {"row": 1, "shape": "uniform", "weights": [1 / 12] * 12, "note": "均匀"},
        {"row": 2, "shape": "front_load", "weights": [0.2] * 5 + [0.0] * 7, "note": "前置"},
    ]
    result = m.merge_ai_weights(plan, ai_rows)
    assert result.mode == "ai"
    fallback = [r for r in result.rows if r.row in (3, 4, 5)]
    assert len(fallback) == 3 and all(r.shape == "uniform" for r in fallback)
    assert sum(1 for w in result.warnings if "未给出有效权重" in w) == 3


def test_merge_ai_invalid_weights_fallback():
    plan = _plan(2)
    ai_rows = [
        {"row": 1, "shape": "custom", "weights": [-0.5] + [0.14] * 11, "note": ""},
        {"row": 2, "shape": "custom", "weights": [0.5] * 12, "note": ""},
    ]
    result = m.merge_ai_weights(plan, ai_rows)
    assert all(r.shape == "uniform" for r in result.rows)
    assert len([w for w in result.warnings if "未给出有效权重" in w]) == 2


def test_merge_ai_note_digit_whitelist_warning():
    plan = _plan(1)
    ai_rows = [{"row": 1, "shape": "peak", "weights": [1 / 12] * 12, "note": "春节月2月集中"}]
    result = m.merge_ai_weights(plan, ai_rows)
    assert any("含数字" in w for w in result.warnings)
    assert result.rows[0].annual == 10000  # 数字绝不被 AI 改写
    assert sum(result.rows[0].months) == 10000


def test_merge_ai_identity_always_holds():
    rng = random.Random(7)
    plan = _plan(20)
    ai_rows = []
    for i in range(15):  # 只覆盖一部分行
        weights = [rng.random() for _ in range(12)]
        ai_rows.append({"row": i + 1, "shape": "custom", "weights": weights, "note": ""})
    result = m.merge_ai_weights(plan, ai_rows)
    checks = m.verify(result.rows)
    assert checks["row_failures"] == 0 and checks["total_gap"] == 0
    assert result.grand_total == sum(round(float(p["annual"])) for p in plan)


def test_ai_weights_coverage():
    plan = _plan(4)
    full = [{"row": i + 1, "weights": [1 / 12] * 12} for i in range(4)]
    assert m.ai_weights_coverage(plan, full) == (4, 4)
    partial = full[:3]
    assert m.ai_weights_coverage(plan, partial) == (4, 3)
    assert m.ai_weights_coverage(plan, []) == (4, 0)


# ── 规则题库 ───────────────────────────────────────────────────────────

def test_rule_questions_structure():
    questions = m.build_rule_questions()
    assert 4 <= len(questions) <= 6
    ids = [q["id"] for q in questions]
    assert len(set(ids)) == len(ids)
    for q in questions:
        assert q["type"] in ("single", "text")
        assert q["title"]
        if q["type"] == "single":
            assert len(q["options"]) >= 2
            assert q["default"] in q["options"]  # 一路默认可走通
        else:
            assert q["default"] == ""
    blob = "".join(q["title"] for q in questions)
    for kw in ("季节", "薪酬", "年终奖", "平摊", "投放", "一次性"):
        assert kw in blob, f"题库缺少覆盖：{kw}"


def test_rule_split_full_flow_identity():
    """模拟完整题面答案 → 恒等 + 关键形状落位。"""
    plan = [
        _row(1, "管理费用", "工资", 240000),
        _row(2, "管理费用", "年终奖", 60000),
        _row(3, "销售费用", "广告宣传费", 120000),
        _row(4, "管理费用", "咨询费", 0),
        _row(5, "管理费用", "装修费", 360000),
    ]
    answers = {
        "q_season": "下半年旺",
        "q_bonus": "年终奖2~3个月工资",
        "q_fixed": "是，按月平摊",
        "q_campaign": "旺季前1~2个月前置投放",
        "q_lump": "3月 30万 装修费",
        "q_start": "1月",
    }
    result = m.rule_split(plan, answers, budget_year=2026)
    by_row = {r.row: r for r in result.rows}
    assert by_row[1].shape == "uniform"
    assert by_row[2].shape == "peak"
    assert by_row[3].shape == "front_load"
    assert by_row[4].months == [0.0] * 12
    assert by_row[5].shape == "lump" and by_row[5].months[2] == 360000
    checks = m.verify(result.rows)
    assert checks["row_failures"] == 0 and checks["total_gap"] == 0
    assert result.grand_total == 240000 + 60000 + 120000 + 0 + 360000
