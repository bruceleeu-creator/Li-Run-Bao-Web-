"""利润宝 · 导入历史 API 回归测试（卡片快速载入）。

覆盖：导入自动存档、重复导入去重、列表/载入/删除契约、载入后诊断可用、
记录不存在 404。全部使用 TestClient，不依赖浏览器与 AI。
"""

from __future__ import annotations

import io
import importlib

import pytest
from fastapi.testclient import TestClient

app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
import_mod = importlib.import_module("web_backend.CO_import_WB-CO-TR-20260805160732")
session_mod = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
workspace_db = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")

CLIENT = TestClient(app_mod.create_app())


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """每个测试独立临时库（get_db_path 动态读 env，无需重载模块）。"""
    monkeypatch.setenv("LIRUNBAO_DB_PATH", str(tmp_path / "app.db"))
    session_mod.clear()
    yield
    session_mod.clear()


def _sample_xlsx_bytes() -> bytes:
    import os

    from data.make_sample import write_sample_xlsx

    path = write_sample_xlsx()
    try:
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.remove(path)


def _xlsx():
    return {
        "files": (
            "sample.xlsx",
            io.BytesIO(_sample_xlsx_bytes()),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }


def _import_sample(company: str = "历史测试厂") -> dict:
    r = CLIENT.post(
        "/api/import",
        files=_xlsx(),
        data={"company_name": company, "industry": "制造业"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_import_creates_history_entry():
    """导入成功后自动存档一条导入历史（含轻量元数据）。"""
    _import_sample()
    r = CLIENT.get("/api/import/history")
    assert r.status_code == 200, r.text
    entries = r.json()["history"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["company_name"] == "历史测试厂"
    assert entry["industry"] == "制造业"
    assert entry["years"] == [2021, 2022, 2023]
    assert entry["file_count"] == 1
    assert entry["session_version"]


def test_reimport_same_data_dedupes_history():
    """同一批数据重复导入：session_version 相同，不新增历史卡片。"""
    _import_sample()
    _import_sample()
    entries = CLIENT.get("/api/import/history").json()["history"]
    assert len(entries) == 1


def test_load_history_restores_session():
    """载入历史记录：会话恢复、响应契约与 /import 一致、可直接诊断。"""
    imported = _import_sample()
    version = imported["summary"]  # 会话在；再取 version
    entries = CLIENT.get("/api/import/history").json()["history"]
    entry_id = entries[0]["id"]

    # 换成另一份数据（示例），再从历史载入第一份
    r = CLIENT.post("/api/import/sample")
    assert r.status_code == 200, r.text
    assert CLIENT.get("/api/import/history").json()["history"][0]["company_name"] != "历史测试厂"

    r = CLIENT.post(f"/api/import/history/{entry_id}/load")
    assert r.status_code == 200, r.text
    body = r.json()
    # 与 /import 同键集合（前端复用同一处理逻辑）
    for key in ("summary", "indicators", "years", "previews", "data_quality", "policy"):
        assert key in body, f"缺少契约字段 {key}"
    assert body["summary"]["company_name"] == "历史测试厂"
    assert body["years"] == [2021, 2022, 2023]
    assert body["indicators"], "载入后应带回按年指标"

    # 载入后诊断链路立即可用（无需重新导入）
    r = CLIENT.post("/api/diagnosis/run")
    assert r.status_code == 200, r.text
    assert r.json()["findings"], "载入的历史会话应可直接诊断"

    # 会话摘要同步恢复
    r = CLIENT.get("/api/session")
    assert r.status_code == 200, r.text
    assert r.json()["session"]["company_name"] == "历史测试厂"


def test_load_missing_history_returns_404():
    r = CLIENT.post("/api/import/history/999999/load")
    assert r.status_code == 404


def test_delete_history_entry():
    _import_sample()
    entries = CLIENT.get("/api/import/history").json()["history"]
    entry_id = entries[0]["id"]

    r = CLIENT.delete(f"/api/import/history/{entry_id}")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == entry_id

    # 删除后不可再载入
    assert CLIENT.post(f"/api/import/history/{entry_id}/load").status_code == 404
    # 再删除一次返回 404
    assert CLIENT.delete(f"/api/import/history/{entry_id}").status_code == 404
    assert CLIENT.get("/api/import/history").json()["history"] == []


def test_load_history_does_not_duplicate_cards():
    """从历史载入属于同一 session_version：不产生新的历史卡片。"""
    _import_sample()
    entries = CLIENT.get("/api/import/history").json()["history"]
    CLIENT.post("/api/import/sample")  # 换数据（新增一条）
    assert len(CLIENT.get("/api/import/history").json()["history"]) == 2
    CLIENT.post(f"/api/import/history/{entries[0]['id']}/load")
    # 载入旧记录不新增卡片（仍是 2 条，最新一条是示例）
    assert len(CLIENT.get("/api/import/history").json()["history"]) == 2


# ── 完整案例载入：诊断/互动进度与已生成报告一并恢复 ────────────────────

def _first_history_id() -> int:
    entries = CLIENT.get("/api/import/history").json()["history"]
    assert entries, "应至少存在一条导入记录"
    return entries[0]["id"]


def _decide_all_findings():
    """循环决策所有发现直到 DRAFT2/CONFIRMATION/FINAL。"""
    assert CLIENT.post("/api/diagnosis/run").status_code == 200
    CLIENT.post("/api/interaction/start")
    for _ in range(30):
        st = CLIENT.get("/api/interaction/state").json()
        if st["state"] in ("DRAFT2", "CONFIRMATION", "FINAL") or not st.get("current_finding"):
            return st
        r = CLIENT.post(
            "/api/interaction/decide",
            json={"finding_id": st["current_finding"]["id"], "option_label": "A"},
        )
        assert r.status_code == 200, r.text
    return CLIENT.get("/api/interaction/state").json()


def test_load_restores_diagnosis_and_interaction_progress():
    """案例 A 做完诊断+互动 → 切到案例 B → 载回 A：进度完整恢复。"""
    _import_sample(company="进度案例厂")
    assert CLIENT.post("/api/diagnosis/run").status_code == 200
    r = CLIENT.post("/api/interaction/start")
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["state"] == "FINDING_LOOP"
    finding_id = state["current_finding"]["id"]
    r = CLIENT.post(
        "/api/interaction/decide",
        json={"finding_id": finding_id, "option_label": "A"},
    )
    assert r.status_code == 200, r.text
    entry_id = _first_history_id()

    # 切到示例（另一案例，session.replace 清掉当前诊断/互动）
    assert CLIENT.post("/api/import/sample").status_code == 200
    assert CLIENT.get("/api/diagnosis").json()["diagnosis"] is None

    # 载回进度案例：诊断完成；互动只做了 1/N 条决策 → 仍在进行中（未到第二稿）
    r = CLIENT.post(f"/api/import/history/{entry_id}/load")
    assert r.status_code == 200, r.text
    restored = r.json()["restored"]
    assert restored["diagnosis_done"] is True
    assert restored["interaction_done"] is False  # FINDING_LOOP 进行中
    assert restored["export_unlocked"] is False

    # 诊断结果与互动决策原样恢复（不重跑）
    diag = CLIENT.get("/api/diagnosis").json()["diagnosis"]
    assert diag and diag["findings"], "载入后应直接带回已保存诊断"
    st = CLIENT.get("/api/interaction/state").json()
    assert st["decisions"], "载入后互动决策应恢复"
    decided = {d["finding_id"] for d in st["decisions"]}
    assert finding_id in decided


def test_load_restores_unlocked_state_after_confirm():
    """案例做到确认解锁后载回：export_unlocked 应恢复为 True。"""
    _import_sample(company="解锁案例厂")
    st = _decide_all_findings()
    if st["state"] == "DRAFT2":
        CLIENT.post("/api/interaction/confirm", json={"user_confirmed": True})
    entry_id = _first_history_id()
    assert CLIENT.post("/api/import/sample").status_code == 200

    r = CLIENT.post(f"/api/import/history/{entry_id}/load")
    assert r.status_code == 200, r.text
    assert r.json()["restored"]["export_unlocked"] is True
    assert CLIENT.get("/api/interaction/state").json()["is_export_unlocked"] is True


def test_load_returns_saved_report_id():
    """该版本已生成过 AI 合并报告：载入带回 report_id（前端直接展示）。"""
    _import_sample(company="报告案例厂")
    version = workspace_db.load_session().get("session_version") or ""
    report_id = workspace_db.save_report(
        "years_summary", "跨年合并报告（测试）", "# 测试报告内容",
        session_version=version,
    )
    entry_id = _first_history_id()

    r = CLIENT.post(f"/api/import/history/{entry_id}/load")
    assert r.status_code == 200, r.text
    assert r.json()["restored"]["report_id"] == report_id
    detail = CLIENT.get(f"/api/ai/reports/{report_id}").json()
    assert "测试报告内容" in detail["content"]
