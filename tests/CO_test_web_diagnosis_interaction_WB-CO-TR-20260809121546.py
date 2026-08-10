"""利润宝 · 诊断 / 互动 / 行业推荐 API 回归测试。

验证：规则诊断、AI 增强回退、持久化与 session_version 绑定、互动状态机
走通（FINDING_LOOP → DRAFT2 → CONFIRMATION → FINAL）、行业推荐双路径。
全部使用 TestClient，不依赖浏览器；AI 默认未配置，走规则路径。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
session_mod = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
db_mod = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")
interaction_mod = importlib.import_module("web_backend.CO_interaction_WB-CO-TR-20260805160732")

CLIENT = TestClient(app_mod.create_app())


@pytest.fixture(autouse=True)
def _reset_session():
    session_mod.clear()
    # 清空互动模块内存态，保证测试相互独立
    interaction_mod._sess = None
    interaction_mod._sess_session_version = ""
    yield
    session_mod.clear()
    interaction_mod._sess = None
    interaction_mod._sess_session_version = ""


def _load_sample() -> dict:
    """载入示例数据，返回导入响应。"""
    r = CLIENT.post("/api/import/sample")
    assert r.status_code == 200, r.text
    return r.json()


# ── 诊断 API ─────────────────────────────────────────────────────────

def test_diagnosis_requires_import():
    """未导入财报时诊断应提示先导入。"""
    r = CLIENT.post("/api/diagnosis/run")
    assert r.status_code == 400
    assert "导入" in r.json()["detail"]


def test_diagnosis_run_rule_path():
    """导入样例后诊断返回 4 类发现，每条含 A/B/C 三个选项；AI 未配置时 ai_used=false。"""
    _load_sample()
    r = CLIENT.post("/api/diagnosis/run")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company_name"] == "示例制造有限公司"
    assert body["industry"] == "制造业"
    assert body["industry_fallback"] is False
    assert body["ai_used"] is False
    assert body["vat_estimate_note"]
    ids = {f["id"] for f in body["findings"]}
    assert {"RD_MISSING", "ENTERTAIN_EXCESS", "CONSULTING_HIGH", "VAT_LOW"} <= ids
    for f in body["findings"]:
        labels = {o["label"] for o in f["options"]}
        assert labels == {"A", "B", "C"}, f"{f['id']} 应有 A/B/C 选项"
        for o in f["options"]:
            assert o["name"]
            assert o["feasibility"]
            assert o["risk_level"]


def test_diagnosis_get_returns_saved():
    """GET /api/diagnosis 返回已保存结果。"""
    _load_sample()
    r = CLIENT.post("/api/diagnosis/run")
    assert r.status_code == 200
    r2 = CLIENT.get("/api/diagnosis")
    assert r2.status_code == 200
    body = r2.json()
    assert body["diagnosis"] is not None
    assert len(body["diagnosis"]["findings"]) >= 4


def test_diagnosis_invalidated_after_reimport():
    """重新导入后旧诊断失效，GET 返回 null。"""
    _load_sample()
    CLIENT.post("/api/diagnosis/run")
    _load_sample()  # 重新导入会替换会话版本
    r = CLIENT.get("/api/diagnosis")
    assert r.status_code == 200
    assert r.json()["diagnosis"] is None


def test_diagnosis_clear():
    """POST /api/diagnosis/clear 清空已保存诊断。"""
    _load_sample()
    CLIENT.post("/api/diagnosis/run")
    r = CLIENT.post("/api/diagnosis/clear")
    assert r.status_code == 200
    assert r.json()["cleared"] is True
    r2 = CLIENT.get("/api/diagnosis")
    assert r2.json()["diagnosis"] is None


# ── 互动 API ─────────────────────────────────────────────────────────

def test_interaction_full_flow():
    """完整走通：start → 逐条 decide → DRAFT2 → confirm → FINAL 解锁导出。"""
    _load_sample()
    r = CLIENT.post("/api/interaction/start")
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["state"] == "FINDING_LOOP"
    assert state["current_finding"] is not None
    assert state["total"] >= 4

    findings_ids = []
    current = state["current_finding"]
    while current is not None:
        fid = current["id"]
        findings_ids.append(fid)
        r = CLIENT.post(
            "/api/interaction/decide",
            json={"finding_id": fid, "option_label": "A", "strategy_note": "测试选择A"},
        )
        assert r.status_code == 200, r.text
        state = r.json()
        current = state["current_finding"]
        # 决策推进必须严格按当前发现
        if current is not None:
            assert current["id"] not in findings_ids, "状态机不应跳项"

    assert state["state"] == "CONFIRMATION"
    assert len(state["decisions"]) >= 4
    assert len(state["draft2"]) >= 4
    for e in state["draft2"]:
        assert e["finding_title"]
        assert e["trend"]
        assert e["action_detail"]
        assert e["cautions"]

    r = CLIENT.post("/api/interaction/confirm", json={"user_confirmed": True})
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["state"] == "FINAL"
    assert state["is_export_unlocked"] is True


def test_interaction_requires_import():
    """未导入时启动互动应提示。"""
    r = CLIENT.post("/api/interaction/start")
    assert r.status_code == 400
    assert "导入" in r.json()["detail"]


def test_interaction_rejects_wrong_finding():
    """decide 提交非当前发现应被拒绝（不跳项）。"""
    _load_sample()
    CLIENT.post("/api/interaction/start")
    state = CLIENT.get("/api/interaction/state").json()
    current = state["current_finding"]
    wrong_id = "NONEXISTENT"
    r = CLIENT.post(
        "/api/interaction/decide",
        json={"finding_id": wrong_id, "option_label": "A"},
    )
    assert r.status_code == 400
    assert "请先处理当前发现" in r.json()["detail"]


def test_interaction_state_persists_across_requests():
    """decide 后 state 持久化，重新请求仍保持进度。"""
    _load_sample()
    CLIENT.post("/api/interaction/start")
    state = CLIENT.get("/api/interaction/state").json()
    fid = state["current_finding"]["id"]
    CLIENT.post("/api/interaction/decide", json={"finding_id": fid, "option_label": "B"})
    r = CLIENT.get("/api/interaction/state")
    state = r.json()
    assert len(state["decisions"]) == 1
    assert state["decisions"][0]["option_label"] == "B"


# ── 行业推荐 ─────────────────────────────────────────────────────────

def test_industry_recommend_rule():
    """AI 未配置时行业推荐走规则路径。"""
    r = CLIENT.post(
        "/api/industries/recommend",
        json={"company_name": "某某建筑工程有限公司", "overview": ""},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["industry"] == "建筑业"
    assert body["source"] == "rule"
    assert body["reason"]


def test_industry_recommend_default_fallback():
    """无关键词时回退默认行业。"""
    r = CLIENT.post(
        "/api/industries/recommend",
        json={"company_name": "无名企业", "overview": ""},
    )
    assert r.status_code == 200
    assert r.json()["industry"] == "制造业"


def test_industry_list_has_desc():
    """行业列表包含说明字段。"""
    r = CLIENT.get("/api/industries")
    assert r.status_code == 200
    body = r.json()
    items = {item["name"]: item for item in body["industries"]}
    assert items["制造业"]["desc"]
    assert len(body["names"]) >= 10


# ── 企业名称与行业 AI/规则识别 ──────────────────────────────────────

def test_identify_company_rule_path():
    """AI 未配置时从文件名识别企业名称与行业（规则路径）。"""
    r = CLIENT.post(
        "/api/import/identify",
        json={"files": [{"name": "云南艺康装饰工程有限公司 2023年度审计报告.pdf", "text": ""}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["company_name"] == "云南艺康装饰工程有限公司"
    assert body["industry"] == "建筑业"
    assert body["source"] == "rule"
    assert body["fallback"] is False
    assert body["reason"]


def test_identify_company_mixed_files():
    """多个文件时优先使用含企业名的文件。"""
    r = CLIENT.post(
        "/api/import/identify",
        json={"files": [
            {"name": "2022年审计报告.pdf", "text": ""},
            {"name": "2023年审计报告.pdf", "text": ""},
            {"name": "深圳市某某医药有限公司2022年度财务报告.pdf", "text": ""},
        ]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["company_name"] == "深圳市某某医药有限公司"
    assert body["industry"] == "医药制造业"


def test_identify_company_year_only_fallback():
    """纯年份文件名无法提取企业名时返回空。"""
    r = CLIENT.post(
        "/api/import/identify",
        json={"files": [{"name": "2022年审计报告.pdf", "text": ""}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["company_name"] == ""
    assert body["industry"] == "制造业"  # 行业回退默认
