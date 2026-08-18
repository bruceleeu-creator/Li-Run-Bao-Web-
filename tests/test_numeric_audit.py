"""利润宝 · 数字质检引擎（core/numeric_audit）单元测试。

覆盖：恒等式全式、资产负债恒等、小数点错位归因（历史 22263237393 事故类型）、
逐年跳变、业务合理性、OCR 原文字面质检、管线集成与 require_confirm 联动。
全部确定性断言，可反算复核。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

num_audit = importlib.import_module("core.numeric_audit")
session_mod = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")

CLIENT = TestClient(app_mod.create_app())


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LIRUNBAO_DB_PATH", str(tmp_path / "app.db"))
    session_mod.clear()
    yield
    session_mod.clear()


def _sample_data():
    from data.make_sample import build_sample_data
    from core.parser import parse_financial_dict

    return parse_financial_dict(build_sample_data())


def _findings_by_check(report, check):
    return [f for f in report["findings"] if f["check"] == check]


# ── 干净数据 ───────────────────────────────────────────────────────────

def test_clean_sample_passes_with_high_score():
    report = num_audit.audit_numbers(_sample_data())
    assert report["engine"] == "numeric_audit"
    highs = [f for f in report["findings"] if f["severity"] == "high"]
    mediums = [f for f in report["findings"] if f["severity"] == "medium"]
    assert not highs, [f["message"] for f in highs]
    assert not mediums, [f["message"] for f in mediums]
    assert report["score"] >= 90
    assert report["grade"] == "高"
    # 三年 × 2 条恒等式
    ids = {i["id"] for i in report["identities"]}
    assert {"NA-PBT-2021", "NA-PBT-2022", "NA-PBT-2023",
            "NA-BAL-2021", "NA-BAL-2022", "NA-BAL-2023"} <= ids


# ── 小数点错位归因（核心场景）─────────────────────────────────────────

def test_scale_error_attributed_with_suggestion():
    """营收漏两位小数（×100）→ 高风险 + ×0.01 修正建议。"""
    data = _sample_data()
    # 2023 营收 15,200,000 → 1,520,000,000（模拟 OCR/解析漏小数点）
    data.income_statement["营业收入"][2023] = 15_200_000 * 100
    report = num_audit.audit_numbers(data)
    scale = [f for f in report["findings"] if f["check"] == "小数点/量级错位归因"]
    assert scale, "应给出错位归因"
    f = scale[0]
    assert f["subject"] == "营业收入"
    assert f["expected"] == pytest.approx(15_200_000, abs=0.01)
    assert "小数点" in f["suggestion"]
    assert report["grade"] in ("中", "低")


def test_balance_identity_break_is_high():
    data = _sample_data()
    data.balance_sheet["资产总额"][2022] += 500_000  # 无法用 10^k 闭合的缺口
    report = num_audit.audit_numbers(data)
    bal = [f for f in report["findings"] if f["check"] == "资产负债恒等式"]
    assert bal and bal[0]["severity"] == "high"
    assert bal[0]["gap"] == pytest.approx(500_000, abs=0.01)


def test_pbt_identity_medium_when_gap_large_but_not_attributable():
    data = _sample_data()
    # 利润总额抬高 300 万（占营收 ~20%，非 10^k 错位）
    data.income_statement["利润总额"][2023] += 3_000_000
    report = num_audit.audit_numbers(data)
    pbt = [f for f in report["findings"] if f["check"] == "利润表恒等式"]
    assert pbt and pbt[0]["severity"] == "medium"
    # 样本自身有 10 万杂项残差（2% 容差内），抬高 300 万后总残差 310 万
    assert pbt[0]["gap"] == pytest.approx(3_100_000, abs=1.0)


# ── 跳变与合理性 ───────────────────────────────────────────────────────

def test_year_jump_flagged():
    data = _sample_data()
    data.income_statement["营业成本"][2022] *= 3  # +200% 跳变
    report = num_audit.audit_numbers(data)
    jumps = _findings_by_check(report, "逐年跳变")
    assert any(f["subject"] == "营业成本" and f["year"] == 2022 for f in jumps)


def test_plausibility_negative_revenue_high():
    data = _sample_data()
    data.income_statement["营业收入"][2021] = -12_000_000
    report = num_audit.audit_numbers(data)
    neg = [f for f in report["findings"] if f["check"] == "数值合理性"]
    assert neg and all(f["severity"] == "high" for f in neg)


def test_plausibility_gross_margin_out_of_band():
    data = _sample_data()
    data.income_statement["营业成本"][2023] = 20_000_000  # 成本>营收 → 毛利率 < -30%
    report = num_audit.audit_numbers(data)
    gm = _findings_by_check(report, "毛利率合理性")
    assert gm, "毛利率超区间应报告"


# ── OCR 原文字面层 ─────────────────────────────────────────────────────

def test_ocr_literal_scan_flags_suspicious_numbers():
    data = _sample_data()
    ocr = [
        "2023 年营业收入 2O2三类似串列 1,234.5678.90 元；"
        "资产总额为 ９８７６５４３.２１ 元；"
        "净利润 2226323739301（粘连数字）"
    ]
    report = num_audit.audit_numbers(data, ocr_texts=ocr)
    ocr_findings = _findings_by_check(report, "OCR 数字字面质检")
    kinds = {f["message"].split("发现")[1].split("：")[0] for f in ocr_findings}
    assert any("形近字母" in k for k in kinds), kinds
    assert any("全角" in k for k in kinds), kinds
    assert any("粘连" in k for k in kinds), kinds
    # 上下文提示存在
    ctx = [f for f in report["findings"] if f["check"] == "数字来源风险"]
    assert ctx, "OCR 来源应给出整体风险提示"


def test_no_ocr_text_no_ocr_findings():
    report = num_audit.audit_numbers(_sample_data(), ocr_texts=[])
    assert not _findings_by_check(report, "OCR 数字字面质检")
    assert not _findings_by_check(report, "数字来源风险")


# ── 管线集成 ───────────────────────────────────────────────────────────

def test_pipeline_response_includes_numeric_audit():
    r = CLIENT.post("/api/import/sample")
    assert r.status_code == 200, r.text
    body = r.json()
    audit = body.get("numeric_audit") or {}
    assert audit.get("engine") == "numeric_audit"
    assert audit["score"] >= 90  # 示例数据应高评分
    assert body["data_quality"].get("numeric_grade") == "高"
    assert body["data_quality"].get("require_confirm") is False


def test_session_and_history_carry_numeric_audit():
    assert CLIENT.post("/api/import/sample").status_code == 200
    s = CLIENT.get("/api/session").json()
    assert s["numeric_audit"].get("engine") == "numeric_audit"

    entries = CLIENT.get("/api/import/history").json()["history"]
    assert entries
    loaded = CLIENT.post(f"/api/import/history/{entries[0]['id']}/load").json()
    assert loaded["numeric_audit"].get("engine") == "numeric_audit"
    assert loaded["numeric_audit"]["score"] >= 90
