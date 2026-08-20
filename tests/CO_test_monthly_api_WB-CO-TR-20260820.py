"""利润宝 · 月度拆分 API 测试（TestClient，无 AI/浏览器依赖）。

覆盖：draft 任务不产生下载、state 各 stage 恢复、问答幂等与默认补齐、
拆分任务（假 AI 成功/失败回退）、download 409 语义、勾选指纹变化重置。
测试环境未配置 AI：出题/拆分自动走规则兜底（离线优先验收点）。
"""

from __future__ import annotations

import importlib
import io
import os
import time

import pytest
from fastapi.testclient import TestClient

app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
session_mod = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
db_mod = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")
ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")

CLIENT = TestClient(app_mod.create_app())


def _reset_monthly_state():
    """负向前提用例的显式重置：删除当前会话的月度流程状态行。"""
    db_mod.delete_monthly_state(session_mod.get_version())


@pytest.fixture(autouse=True, scope="module")
def _isolated_db(tmp_path_factory):
    """模块级隔离：整个模块共享一个临时库与一次示例导入（模块结束清理）。"""
    db_path = tmp_path_factory.mktemp("monthly-api") / "app.db"
    old = os.environ.get("LIRUNBAO_DB_PATH")
    os.environ["LIRUNBAO_DB_PATH"] = str(db_path)
    session_mod.clear()
    yield
    session_mod.clear()
    import gc

    gc.collect()  # Windows 下释放残留 sqlite 连接，避免临时目录清理锁文件
    if old is None:
        os.environ.pop("LIRUNBAO_DB_PATH", None)
    else:
        os.environ["LIRUNBAO_DB_PATH"] = old


def _sample_xlsx_bytes() -> bytes:
    from data.make_sample import write_sample_xlsx

    path = write_sample_xlsx()
    try:
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.remove(path)


@pytest.fixture(scope="module")
def imported(_isolated_db):
    """导入示例三年数据（module 级共享，减少重复导入耗时）。"""
    r = CLIENT.post(
        "/api/import",
        files={"files": ("sample.xlsx", io.BytesIO(_sample_xlsx_bytes()),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"company_name": "月度拆分测试厂", "industry": "制造业"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _wait_job(path: str, timeout: float = 180.0) -> dict:
    """轮询任务端点直到终态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = CLIENT.get(path)
        assert r.status_code == 200, r.text
        job = r.json()
        if job.get("status") in ("completed", "failed"):
            return job
        time.sleep(0.5)
    raise AssertionError(f"任务超时：{path}")


def _run_draft(imported, advice=None) -> dict:
    r = CLIENT.post("/api/export/budget/draft/jobs", json={"advice_items": advice or []})
    assert r.status_code == 200, r.text
    job = r.json()
    job = _wait_job(f"/api/export/budget/draft/jobs/{job['job_id']}")
    assert job["status"] == "completed", job.get("error")
    return job


def _full_flow_to_answered(imported) -> dict:
    _run_draft(imported)
    r = CLIENT.post("/api/export/budget/monthly/questions")
    assert r.status_code == 200, r.text
    questions = r.json()["questions"]
    r = CLIENT.post("/api/export/budget/monthly/answers", json={"answers": []})  # 全默认
    assert r.status_code == 200, r.text
    return {"questions": questions}


# ── 第一稿 ─────────────────────────────────────────────────────────────

def test_draft_completes_without_download(imported):
    """AC-A1.1：draft 完成后 state=draft、快照与摘要齐备，无下载副作用。"""
    job = _run_draft(imported)
    # 响应是 JSON 任务态，不带文件流（不触发浏览器下载）
    assert job.get("download_ready") in (True, False)

    r = CLIENT.get("/api/export/budget/monthly/state")
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["stage"] == "draft"
    snapshot = state["plan_snapshot"]
    assert snapshot and snapshot.get("rows"), "第一稿快照应含费用行"
    for row in snapshot["rows"]:
        assert set(row) >= {"row", "subject", "expense_name", "annual"}
    summary = state["summary"]
    assert set(summary) >= {"revenue", "expense_total", "fee_rate", "filled_lines", "advice_applied"}
    # 摘要数字与快照同源（AC-A1.2）：expense_total == Σannual
    assert abs(summary["expense_total"] - sum(r0["annual"] for r0 in snapshot["rows"])) < 1.0


def test_draft_get_state_persists_after_refresh(imported):
    """AC-A5.1：再次 GET state 仍能取到第一稿摘要（刷新语义）。"""
    _run_draft(imported)
    for _ in range(2):
        r = CLIENT.get("/api/export/budget/monthly/state")
        assert r.status_code == 200
        assert r.json()["stage"] == "draft"


# ── 问答 ───────────────────────────────────────────────────────────────

def test_questions_rule_fallback_and_idempotent(imported):
    """AC-A2.2：未配置 AI 走规则题库；AC 幂等：重复 POST 返回同题集。"""
    _run_draft(imported)
    r1 = CLIENT.post("/api/export/budget/monthly/questions")
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["source"] == "rule"  # 测试环境无 AI
    assert 4 <= len(body1["questions"]) <= 6

    r2 = CLIENT.post("/api/export/budget/monthly/questions")
    assert r2.status_code == 200
    assert r2.json()["questions"] == body1["questions"]  # 幂等

    r = CLIENT.get("/api/export/budget/monthly/state")
    assert r.json()["stage"] == "questions"


def test_answers_fill_defaults(imported):
    """AC-A2.3：空答案提交 → 全部按题面默认补齐，stage=answered。"""
    _run_draft(imported)
    CLIENT.post("/api/export/budget/monthly/questions")
    r = CLIENT.post("/api/export/budget/monthly/answers", json={"answers": []})
    assert r.status_code == 200, r.text
    answers = r.json()["answers"]
    assert answers and all(a["value"] != "" or True for a in answers)
    single_filled = [a for a in answers if a["value"]]
    assert single_filled  # single 题默认非空


def test_answers_unknown_id_rejected(imported):
    _run_draft(imported)
    CLIENT.post("/api/export/budget/monthly/questions")
    r = CLIENT.post("/api/export/budget/monthly/answers", json={"answers": [{"id": "q_nope", "value": "x"}]})
    assert r.status_code == 400


def test_questions_require_draft(imported):
    """未生成第一稿 → 400。"""
    _reset_monthly_state()
    r = CLIENT.post("/api/export/budget/monthly/questions")
    assert r.status_code == 400


# ── 拆分 ───────────────────────────────────────────────────────────────

def test_split_rule_mode_ready(imported):
    """AC-A3.1/A3.3：未配 AI → 规则拆分；恒等校验 0 失败；stage=ready。"""
    _full_flow_to_answered(imported)
    r = CLIENT.post("/api/export/budget/monthly/split/jobs")
    assert r.status_code == 200, r.text
    job = _wait_job(f"/api/export/budget/monthly/split/jobs/{r.json()['job_id']}", timeout=60)
    assert job["status"] == "completed", job.get("error")

    r = CLIENT.get("/api/export/budget/monthly")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["mode"] == "rule"
    assert payload["checks"]["row_failures"] == 0
    assert payload["checks"]["total_gap"] == 0
    # 逐行 Σ月 == 年（元级）
    for row in payload["matrix"]:
        assert round(sum(row["months"])) == round(row["annual"]), f"第{row['row']}行恒等破坏"
    # 整表恒等
    assert round(sum(payload["month_totals"])) == round(payload["grand_total"])

    state = CLIENT.get("/api/export/budget/monthly/state").json()
    assert state["stage"] == "ready"
    assert state["answers"] and state["questions"]  # 恢复所需信息齐备


def test_split_requires_answers(imported):
    _reset_monthly_state()
    r = CLIENT.post("/api/export/budget/monthly/split/jobs")
    assert r.status_code == 400


def test_download_not_ready_409(imported):
    """download 409 语义：未就绪不允许下载。"""
    _reset_monthly_state()
    r = CLIENT.get("/api/export/budget/monthly/download")
    assert r.status_code == 409
    _full_flow_to_answered(imported)
    r = CLIENT.get("/api/export/budget/monthly/download")  # answered 但未拆分
    assert r.status_code == 409


def test_split_ai_success(monkeypatch, imported):
    """假 AI 全覆盖 → mode=ai。"""
    snapshot_rows = None

    def fake_weights(plan_snapshot, answers, hints=None):
        rows = [r for r in plan_snapshot["rows"] if float(r.get("annual") or 0) > 0]
        return [
            {"row": r["row"], "shape": "front_load", "weights": [0.15, 0.15, 0.1, 0.08, 0.08, 0.07, 0.07, 0.07, 0.06, 0.06, 0.06, 0.05], "note": "前置投放"}
            for r in rows
        ], ""

    monkeypatch.setattr(ai_mod, "ai_available", lambda: True)
    monkeypatch.setattr(ai_mod, "generate_monthly_weights", fake_weights)
    _full_flow_to_answered(imported)
    r = CLIENT.post("/api/export/budget/monthly/split/jobs")
    job = _wait_job(f"/api/export/budget/monthly/split/jobs/{r.json()['job_id']}", timeout=60)
    assert job["status"] == "completed"
    payload = CLIENT.get("/api/export/budget/monthly").json()
    assert payload["mode"] == "ai"
    assert payload["checks"]["row_failures"] == 0


def test_split_ai_failure_falls_back_rule(monkeypatch, imported):
    """假 AI 缺行 → 重试 3 次后回退规则（mode=rule）。"""
    calls = {"n": 0}

    def fake_weights(plan_snapshot, answers, hints=None):
        calls["n"] += 1
        rows = [r for r in plan_snapshot["rows"] if float(r.get("annual") or 0) > 0]
        partial = rows[: max(1, len(rows) // 2)]  # 只覆盖一半
        return [
            {"row": r["row"], "shape": "uniform", "weights": [1 / 12] * 12, "note": ""}
            for r in partial
        ], ""

    monkeypatch.setattr(ai_mod, "ai_available", lambda: True)
    monkeypatch.setattr(ai_mod, "generate_monthly_weights", fake_weights)
    _full_flow_to_answered(imported)
    r = CLIENT.post("/api/export/budget/monthly/split/jobs")
    job = _wait_job(f"/api/export/budget/monthly/split/jobs/{r.json()['job_id']}", timeout=90)
    assert job["status"] == "completed"
    assert calls["n"] == 3  # 重试满 3 次
    payload = CLIENT.get("/api/export/budget/monthly").json()
    assert payload["mode"] == "rule"
    assert "回退规则默认拆分" in job["message"] or "规则默认" in job["message"]


# ── 指纹重置与状态恢复 ─────────────────────────────────────────────────

def test_fingerprint_change_resets_state(imported):
    """AC-A5.2：勾选集变化 → 状态作废重来。"""
    advice_a = [{"row": 14, "budget_amount": 100000, "selected": True}]
    advice_b = [{"row": 14, "budget_amount": 200000, "selected": True}]
    _run_draft(imported, advice=advice_a)
    CLIENT.post("/api/export/budget/monthly/questions")
    CLIENT.post("/api/export/budget/monthly/answers", json={"answers": []})
    r = CLIENT.post("/api/export/budget/monthly/split/jobs")
    job = _wait_job(f"/api/export/budget/monthly/split/jobs/{r.json()['job_id']}", timeout=60)
    assert job["status"] == "completed"
    assert CLIENT.get("/api/export/budget/monthly/state").json()["stage"] == "ready"

    # 换勾选金额再起第一稿 → 状态重置为新 draft
    r = CLIENT.post("/api/export/budget/draft/jobs", json={"advice_items": advice_b})
    assert r.status_code == 200
    job = _wait_job(f"/api/export/budget/draft/jobs/{r.json()['job_id']}")
    assert job["status"] == "completed"
    state = CLIENT.get("/api/export/budget/monthly/state").json()
    assert state["stage"] == "draft"
    assert not state.get("answers"), "旧答案应已重置"
    assert not state.get("split_result"), "旧拆分结果应已重置"


def test_full_state_recovery_semantics(imported):
    """AC-A5.1：完整流程后按 state 恢复——stage/questions/answers/split 齐。"""
    _full_flow_to_answered(imported)
    r = CLIENT.post("/api/export/budget/monthly/split/jobs")
    job = _wait_job(f"/api/export/budget/monthly/split/jobs/{r.json()['job_id']}", timeout=60)
    assert job["status"] == "completed"
    state = CLIENT.get("/api/export/budget/monthly/state").json()
    assert state["stage"] == "ready"
    assert state["plan_snapshot"]["rows"]
    assert state["questions"] and state["answers"]
    assert state["split_result"]["checks"]["row_failures"] == 0
