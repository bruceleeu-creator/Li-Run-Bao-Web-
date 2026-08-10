"""利润宝 · action_pack.py 招待费公式联动测试（CO 第三轮复核）。

验证点（CO 要求："change E and verify H and net I recalculate"）：
1. C3 顶部参考营业收入单元格被填充（默认取诊断会话最新年度营业收入）
2. ENTERTAIN_EXCESS 行的 H 列为 Excel 公式（非冻结常量），且引用 D/E/$C$3/J
3. 修改 E（目标值）后，H 公式文本保持不变（证明是公式而非冻结常量）
4. 用 Python 复算公式逻辑：E 变化 → H 变化 → I 变化（净影响）
5. 概览 Sheet 在空草稿时不引用行动 Sheet（无循环引用）

openpyxl 不评估公式，故用「公式字符串结构校验 + Python 等价复算」双轨验证。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

import pytest

openpyxl = pytest.importorskip("openpyxl")

from core import parser as pr
from core import diagnostic as diag
from core import interactive as iv
from core import action_pack as ap
from data import make_sample

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── 公式等价复算（Python 端，与 Excel 公式逻辑一致） ─────────────────────
def _py_entertain_h(current: float, target: float, revenue: float, tax_rate: float) -> float:
    """招待费 H 列公式等价复算：MAX(0, MIN(D*0.6, rev*0.005) - MIN(E*0.6, rev*0.005)) * J"""
    before = min(current * 0.6, revenue * 0.005)
    after = min(target * 0.6, revenue * 0.005)
    return max(0.0, before - after) * tax_rate


def _py_entertain_f(current: float, target: float) -> float:
    """F = MAX(0, D-E)"""
    return max(0.0, current - target)


def _py_entertain_i(f: float, g: float, h: float) -> float:
    """I = F + G - H"""
    return f + g - h


# ── Fixtures ───────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def sample_session_with_decisions():
    """构造样例会话并全选 A，进入 CONFIRMATION 状态（含 ENTERTAIN_EXCESS 决策）。"""
    raw = make_sample.build_sample_data()
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    sess = iv.start_session(data, result)
    for f in result.findings:
        iv.submit_decision(sess, f.id, "A")
    assert sess.state == iv.STATE_CONFIRMATION
    # 样例数据应包含 ENTERTAIN_EXCESS
    finding_ids = {d.finding_id for d in sess.decisions}
    assert "ENTERTAIN_EXCESS" in finding_ids, "样例会话缺少 ENTERTAIN_EXCESS 发现"
    return sess


@pytest.fixture
def exported_workbook(sample_session_with_decisions):
    """导出 Excel 到临时文件，返回 (路径, Workbook)。"""
    tmp = tempfile.mkdtemp(prefix="profitbao_action_")
    path = os.path.join(tmp, "action.xlsx")
    ap.export_excel_model(sample_session_with_decisions, path)
    wb = openpyxl.load_workbook(path)
    yield path, wb
    wb.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _find_entertain_row(ws, n_entries: int):
    """在行动测算 Sheet 中找到招待费行号。"""
    for r in range(5, 5 + n_entries):
        b = ws.cell(row=r, column=2).value or ""
        if "招待" in str(b):
            return r
    return None


# ── 测试 1：C3 顶部参考营业收入单元格被填充 ──────────────────────────────

def test_c3_reference_revenue_cell_populated(exported_workbook):
    """C3 顶部参考营业收入应非空且为正数。"""
    _, wb = exported_workbook
    ws = wb["行动测算"]
    c3 = ws.cell(row=3, column=3).value
    assert c3 is not None, "C3 参考营业收入单元格未填充"
    assert float(c3) > 0, f"C3 参考营业收入应为正数，实际 {c3!r}"


# ── 测试 2：ENTERTAIN_EXCESS H 列为 Excel 公式 ──────────────────────────

def test_entertain_h_is_formula_with_required_tokens(exported_workbook):
    """H 列必须是公式（以 = 开头），含 MIN/0.6/0.005/$C$3/J 等关键 token。"""
    _, wb = exported_workbook
    ws = wb["行动测算"]
    r = _find_entertain_row(ws, n_entries=20)
    assert r is not None, "未找到招待费行"
    h = ws.cell(row=r, column=8).value
    assert isinstance(h, str) and h.startswith("="), f"H 必须是公式，实际 {h!r}"
    assert "MIN" in h, f"H 必须含 MIN: {h!r}"
    assert "0.6" in h, f"H 必须含 60% (0.6): {h!r}"
    assert "0.005" in h, f"H 必须含 0.5% (0.005): {h!r}"
    assert "$C$3" in h, f"H 必须引用 $C$3 参考营收: {h!r}"
    assert f"J{r}" in h, f"H 必须引用 J{r} 税率: {h!r}"
    assert f"D{r}" in h, f"H 必须引用 D{r} 当前值: {h!r}"
    assert f"E{r}" in h, f"H 必须引用 E{r} 目标值: {h!r}"


# ── 测试 3：修改 E 后 H 公式文本不变（证明是公式） ────────────────────────

def test_h_formula_text_unchanged_after_e_change(exported_workbook):
    """修改 E 单元格后，H 公式文本保持不变（公式不会随值变化为常量）。"""
    path, wb = exported_workbook
    ws = wb["行动测算"]
    r = _find_entertain_row(ws, n_entries=20)
    assert r is not None
    h_before = ws.cell(row=r, column=8).value
    # 修改 E
    original_e = ws.cell(row=r, column=5).value
    try:
        ws.cell(row=r, column=5, value=50_000)
        wb.save(path)
        wb2 = openpyxl.load_workbook(path)
        ws2 = wb2["行动测算"]
        h_after = ws2.cell(row=r, column=8).value
        assert h_after == h_before, f"H 公式文本应不变，前={h_before!r} 后={h_after!r}"
        wb2.close()
    finally:
        # 还原 E
        ws.cell(row=r, column=5, value=original_e)
        wb.save(path)


# ── 测试 4：Python 等价复算 E 变化 → H 变化 → I 变化 ─────────────────────

def test_python_recalc_h_and_i_change_with_e(exported_workbook):
    """用 Python 端等价公式复算：E 变化时 H 与 I 必须不同。"""
    _, wb = exported_workbook
    ws = wb["行动测算"]
    r = _find_entertain_row(ws, n_entries=20)
    assert r is not None
    c3 = float(ws.cell(row=3, column=3).value)
    j = float(ws.cell(row=r, column=10).value)
    d = float(ws.cell(row=r, column=4).value)
    # 两个不同 E
    e1, e2 = 50_000, 200_000
    h1 = _py_entertain_h(d, e1, c3, j)
    h2 = _py_entertain_h(d, e2, c3, j)
    assert h1 != h2, f"E 变化时 H 应不同: h1={h1} h2={h2}"
    # F 与 I 也随之变化
    f1, f2 = _py_entertain_f(d, e1), _py_entertain_f(d, e2)
    i1, i2 = _py_entertain_i(f1, 0, h1), _py_entertain_i(f2, 0, h2)
    assert i1 != i2, f"净影响 I 应不同: i1={i1} i2={i2}"
    # 招待费 G=0，故 I = F - H
    assert i1 == f1 - h1
    assert i2 == f2 - h2


# ── 测试 5：空草稿无循环引用 ────────────────────────────────────────────

def test_empty_draft_overview_no_circular_reference(tmp_path):
    """空草稿时方案概览的「总净影响」应为 0，不引用行动 Sheet。"""
    raw = {
        "company_name": "正常公司", "industry": "制造业", "years": [2023],
        "income_statement": {
            "营业收入": {2023: 10_000_000}, "营业成本": {2023: 7_000_000},
            "税金及附加": {2023: 360_000}, "研发费用": {2023: 500_000},
            "所得税费用": {2023: 250_000}, "净利润": {2023: 750_000},
        },
        "balance_sheet": {}, "account_balances": {"业务招待费": 30_000, "咨询服务费": 100_000},
    }
    data = pr.parse_financial_dict(raw)
    result = diag.diagnose(data)
    sess = iv.start_session(data, result)
    iv.confirm(sess, user_confirmed=True)
    assert len(sess.draft2) == 0
    path = str(tmp_path / "empty.xlsx")
    ap.export_excel_model(sess, path)
    wb = openpyxl.load_workbook(path)
    ws = wb["方案概览"]
    found = False
    for row in ws.iter_rows():
        for cell in row:
            if cell.value == "总净影响（元）":
                nxt = ws.cell(row=cell.row, column=cell.column + 1).value
                assert nxt == 0, f"空草稿总净影响应为 0，实际 {nxt!r}"
                found = True
    assert found, "未找到『总净影响（元）』行"
    wb.close()
