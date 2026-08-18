"""利润宝 · Web AI 配置与整理 API 测试。

覆盖：配置保存/清空、key 不落盘、未配置离线回退、AI 整理端点（mock）。
"""

from __future__ import annotations

import importlib
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")

CLIENT = TestClient(app_mod.create_app())

AI_CONFIG = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "api_key": "sk-test-key-not-real",
}


@pytest.fixture(scope="module", autouse=True)
def _close_module_client():
    yield
    CLIENT.close()
    importlib.import_module(
        "web_backend.CO_ai_report_job_WB-CO-TR-20260807113737"
    ).release_process_lease(force=True)


@pytest.fixture(autouse=True)
def _clean_ai():
    ai_mod.clear_config()
    yield
    ai_mod.clear_config()


@pytest.fixture(autouse=True)
def _clean_session():
    session_mod.clear()
    yield
    session_mod.clear()


def test_ai_config_default_offline():
    r = CLIENT.get("/api/ai/config")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert "api_key" not in body


def test_save_config_returns_without_key():
    r = CLIENT.post("/api/ai/config", json=AI_CONFIG)
    assert r.status_code == 200
    body = r.json()
    assert body["base_url"] == "https://api.deepseek.com"
    assert body["model"] == "deepseek-chat"
    assert body["configured"] is True
    assert "api_key" not in body


def test_save_config_missing_api_key_reports_reason():
    """只填 Base URL/模型、不填 API Key 时，必须明确告知缺失项而不是静默成功。"""
    r = CLIENT.post(
        "/api/ai/config",
        json={"base_url": "https://api.deepseek.com", "model": "deepseek-chat", "api_key": ""},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["error"], "缺少 API Key 时必须返回可展示的错误原因"
    assert "API Key" in body["error"]
    assert "api_key" not in body


def test_api_key_persisted_to_disk():
    """配置持久化：base_url/model/api_key 均写入 .ai_config.json（重启免重输）。"""
    import json
    CLIENT.post("/api/ai/config", json=AI_CONFIG)
    cfg_file = ai_mod._get_ai_config_path()
    assert cfg_file == Path(os.environ["LIRUNBAO_AI_CONFIG_PATH"]).resolve()
    assert cfg_file != Path(".").resolve() / ".ai_config.json"
    assert cfg_file.exists(), "配置应持久化 base_url/model/api_key"
    with open(cfg_file, "r", encoding="utf-8") as fh:
        disk = json.load(fh)
    assert disk["base_url"] == AI_CONFIG["base_url"]
    assert disk["model"] == AI_CONFIG["model"]
    assert disk["api_key"] == AI_CONFIG["api_key"]


def test_config_restored_after_process_restart():
    """模拟进程重启：持久化后清空内存 state，get_config 仍恢复为已配置。

    覆盖真实故障：保存配置后后端重启，base_url/model/api_key 从文件恢复，
    前端不再误报「大模型未配置」。
    """
    CLIENT.post("/api/ai/config", json=AI_CONFIG)
    assert CLIENT.get("/api/ai/config").json()["configured"] is True
    # 模拟重启：内存 state 清空（_loaded 标志也清掉）
    ai_mod._state = {"base_url": "", "model": "", "api_key": ""}
    body = CLIENT.get("/api/ai/config").json()
    assert body["configured"] is True, "重启后应恢复已配置状态"
    assert body["base_url"] == AI_CONFIG["base_url"]
    # get_credentials 也应恢复 api_key（供 DeepSeek 调用）
    creds = ai_mod.get_credentials()
    assert creds["api_key"] == AI_CONFIG["api_key"]


def test_summarize_offline_returns_400():
    r = CLIENT.post("/api/ai/summarize", json={"content": "营业收入 1000 万元"})
    assert r.status_code == 400
    assert "未配置" in r.json()["detail"]


def test_summarize_empty_content_400():
    CLIENT.post("/api/ai/config", json=AI_CONFIG)
    r = CLIENT.post("/api/ai/summarize", json={"content": "  "})
    assert r.status_code == 400


def test_clear_config_restores_offline():
    CLIENT.post("/api/ai/config", json=AI_CONFIG)
    assert CLIENT.get("/api/ai/config").json()["configured"] is True
    r = CLIENT.post("/api/ai/clear")
    assert r.status_code == 200
    assert r.json()["configured"] is False
    assert CLIENT.get("/api/ai/config").json()["configured"] is False


def test_summarize_with_mocked_engine(monkeypatch):
    """配置后调用 AI 整理（mock 引擎，不触网）。"""

    class FakeEngine:
        def is_available(self):
            return True

        def chat(self, *args, **kwargs):
            return "| 科目 | 金额 |\n|---|---|\n| 营业收入 | 1000万 |"

    monkeypatch.setattr(ai_mod, "_engine", lambda **kw: FakeEngine())
    r = CLIENT.post("/api/ai/summarize", json={"content": "营业收入 1000万"})
    assert r.status_code == 200
    assert "| 科目 | 金额 |" in r.json()["markdown"]


def test_extract_budget_indicators_parses_json(monkeypatch):
    """AI 返回 JSON 对象时正确解析 4 个预算指标。"""

    class FakeEngine:
        def is_available(self):
            return True

        def chat(self, *args, **kwargs):
            return (
                '{"budget_revenue": 15200000, "budget_cost": 11930000,'
                ' "last_year_revenue": 13500000, "last_year_cost": 10530000}'
            )

    monkeypatch.setattr(ai_mod, "_engine", lambda **kw: FakeEngine())
    ind, err = ai_mod.extract_budget_indicators("利润表 OCR 文本 营业收入 15200000")
    assert err == ""
    assert ind["budget_revenue"] == 15200000.0
    assert ind["budget_cost"] == 11930000.0
    assert ind["last_year_revenue"] == 13500000.0
    assert ind["last_year_cost"] == 10530000.0


def test_extract_budget_indicators_missing_key_fills_zero(monkeypatch):
    """AI 返回缺字段时填 0。"""

    class FakeEngine:
        def is_available(self):
            return True

        def chat(self, *args, **kwargs):
            return '{"budget_revenue": 1000}'

    monkeypatch.setattr(ai_mod, "_engine", lambda **kw: FakeEngine())
    ind, err = ai_mod.extract_budget_indicators("text")
    assert err == ""
    assert ind["budget_revenue"] == 1000.0
    assert ind["budget_cost"] == 0.0


def test_extract_budget_indicators_offline():
    """未配置 AI 时返回 error，不抛。"""
    ind, err = ai_mod.extract_budget_indicators("text")
    assert ind == {}
    assert "未配置" in err


def test_extract_budget_indicators_empty_text():
    ind, err = ai_mod.extract_budget_indicators("   ")
    assert ind == {}
    assert "OCR" in err


# ── 总览 AI 合并报告 ──────────────────────────────────────────────────

session_mod = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")


def _prepare_unique_sample() -> None:
    assert CLIENT.post("/api/import/sample").status_code == 200
    data = session_mod.get_data()
    data.company_name = f"同步端点契约-{time.time_ns()}"
    session_mod.replace(data, session_mod.get_ocr_texts(), session_mod.get_source_files())


def _start_legacy_job() -> dict:
    response = CLIENT.post("/api/ai/years-summary")
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job = CLIENT.get(f"/api/ai/years-summary/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("legacy job did not reach terminal state")


def test_years_summary_no_session():
    """未导入财报时返回 400。"""
    r = CLIENT.post("/api/ai/years-summary")
    assert r.status_code == 400
    assert "导入" in r.json()["detail"]


def test_years_summary_no_ocr_deterministic():
    """有会话数据但无 OCR 文本时，走确定性回退生成三年对比，不报 OCR 错误。"""
    _prepare_unique_sample()
    job = _start_legacy_job()
    assert job["status"] == "completed"
    assert job["report_type"] == "rules_quick"
    assert "跨年合并报告" in job["markdown"]


def test_years_summary_offline_fallback():
    """未配置 AI 但有已解析指标时，离线确定性生成三年对比，不报错。"""
    _prepare_unique_sample()
    job = _start_legacy_job()
    assert job["status"] == "completed"
    assert job["fallback"] is True
    assert job["fallback_reason_code"] == "AI_NOT_CONFIGURED"
    assert "2021" in job["markdown"] and "2023" in job["markdown"]
    assert "营业收入" in job["markdown"]
    assert "估算值" in job["markdown"]


def test_years_summary_legacy_route_cannot_bypass_frozen_sources(monkeypatch):
    """旧端点也必须经过后台流水线的完整来源门禁。"""

    class FakeEngine:
        def is_available(self):
            return True

        def chat(self, *args, **kwargs):
            return "| 年份 | 营业收入 |\n|---|---|\n| 2022 | 2.2亿 |"

    monkeypatch.setattr(ai_mod, "_engine", lambda **kw: FakeEngine())
    _prepare_unique_sample()
    session_mod.set_ocr_texts(["[第 1 页 OCR] 营业收入 2.2亿"])
    job = _start_legacy_job()
    assert job["status"] == "failed"
    assert job["error_code"] == "SOURCE_FILES_UNAVAILABLE"
    assert job["markdown"] == ""


# ── AI 报告持久化与管理 ──────────────────────────────────────────────

db_mod = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")


def test_years_summary_saves_report():
    """years-summary 生成后自动保存到报告库。"""
    db_mod.init_db()
    before = len(db_mod.list_reports())

    _prepare_unique_sample()
    session_mod.set_ocr_texts(["[第 1 页 OCR] 营业收入 2.2亿"])
    job = _start_legacy_job()
    assert job["status"] == "completed"
    assert job["report_type"] == "rules_quick"
    assert job["report_id"] > 0
    after = db_mod.list_reports()
    assert len(after) == before + 1
    # 清理测试污染
    db_mod.delete_report(after[0]["id"])


def test_list_get_delete_report():
    """报告列表/查看/删除闭环。"""
    rid = db_mod.save_report("test", "测试报告", "内容")
    reports = CLIENT.get("/api/ai/reports").json()["reports"]
    assert any(r["id"] == rid for r in reports)
    detail = CLIENT.get(f"/api/ai/reports/{rid}").json()
    assert detail["content"] == "内容"
    r = CLIENT.delete(f"/api/ai/reports/{rid}")
    assert r.status_code == 200
    assert CLIENT.get(f"/api/ai/reports/{rid}").status_code == 404
