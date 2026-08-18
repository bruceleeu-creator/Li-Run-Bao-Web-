import importlib
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data.make_sample import build_sample_data
from core import parser
from core.ai_engine import AIChatResult, AIEngine, AIEngineError


app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
import_mod = importlib.import_module("web_backend.CO_import_WB-CO-TR-20260805160732")
session_mod = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
db_mod = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")
ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
reader = importlib.import_module("core.CO_full_pdf_reader_WB-CO-TR-20260807113737")
deepseek_parser = importlib.import_module("core.CO_deepseek_parse_WB-CO-TR-20260806140818")


def _job_module():
    """惰性引用任务 5 job 模块：当前实现缺失时让单个测试失败而非收集中断。"""
    return importlib.import_module("web_backend.CO_ai_report_job_WB-CO-TR-20260807113737")


def _rules_job_result(markdown: str):
    pipeline = importlib.import_module(
        "web_backend.CO_ai_report_pipeline_WB-CO-TR-20260807113737"
    )
    return pipeline.PipelineResult(
        markdown=markdown,
        report_type="rules_quick",
        model="",
        attempted_model="",
        fallback=True,
        fallback_reason_code="AI_NOT_CONFIGURED",
        page_coverage={},
        blank_pages={},
        conflict_count=0,
    )


@pytest.fixture
def client():
    session_mod.clear()
    with TestClient(app_mod.create_app()) as value:
        yield value
    session_mod.clear()


def test_import_persists_source_file_snapshot(client, tmp_path, monkeypatch):
    pdf = tmp_path / "2022年审计报告.pdf"
    pdf.write_bytes(b"%PDF-test")
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "云南艺康"
    monkeypatch.setattr(import_mod, "_parse_one", lambda *args, **kwargs: data)
    monkeypatch.setattr(
        import_mod,
        "_safe_preview",
        lambda path: {"name": Path(path).name, "notes": ["共 7 页"]},
    )
    response = client.post(
        "/api/import",
        files={"files": (pdf.name, pdf.read_bytes(), "application/pdf")},
        data={"company_name": "云南艺康", "industry": "制造业"},
    )
    assert response.status_code == 200
    source = session_mod.get_source_files()[0]
    assert source == {
        "path": str(Path(source["path"]).resolve()),
        "name": pdf.name,
        "sha256": hashlib.sha256(b"%PDF-test").hexdigest(),
        "size": len(b"%PDF-test"),
        "report_year": 2023,
        "page_count": 7,
    }
    assert Path(source["path"]).exists()
    version = session_mod.get_version()
    assert version
    persisted = db_mod.load_session()
    assert persisted is not None
    assert persisted["source_files"] == [source]
    assert persisted["session_version"] == version
    session_mod._data = None
    session_mod._ocr_texts = []
    session_mod._source_files = []
    session_mod._session_version = ""
    session_mod.restore_from_db()
    assert session_mod.get_source_files() == [source]
    assert session_mod.get_version() == version


def test_test_database_path_is_not_production_db():
    assert db_mod.get_db_path() != Path("web_backend/workspaces/app.db").resolve()


def test_test_workspace_path_is_not_production_workspace():
    test_state = Path(os.environ["LIRUNBAO_DB_PATH"]).parent
    expected = (test_state / "workspaces").resolve()
    production = (Path(import_mod.__file__).resolve().parent / "workspaces").resolve()

    assert import_mod._workdir().resolve() == expected
    assert import_mod._workdir().resolve() != production


def test_workdir_reads_runtime_override_on_every_call(tmp_path, monkeypatch):
    first = tmp_path / "first-workspace"
    second = tmp_path / "second-workspace"

    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(first))
    assert import_mod._workdir().resolve() == first.resolve()
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(second))
    assert import_mod._workdir().resolve() == second.resolve()


def test_db_path_follows_runtime_workspace_when_db_override_is_absent(
    tmp_path, monkeypatch
):
    first = tmp_path / "first-workspace"
    second = tmp_path / "second-workspace"
    monkeypatch.delenv("LIRUNBAO_DB_PATH", raising=False)

    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(first))
    assert db_mod.get_db_path() == (first / "app.db").resolve()
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(second))
    assert db_mod.get_db_path() == (second / "app.db").resolve()


def test_workdir_empty_override_uses_default_workspace(monkeypatch):
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", "   ")
    production = Path(import_mod.__file__).resolve().parent / "workspaces"

    assert import_mod._workdir().resolve() == production.resolve()


def test_test_storage_paths_ignore_external_overrides():
    test_state = Path(os.environ["LIRUNBAO_DB_PATH"]).parent

    assert db_mod.get_db_path() == (test_state / "app.db").resolve()
    assert ai_mod._get_ai_config_path() == (test_state / ".ai_config.json").resolve()
    assert import_mod._workdir().resolve() == (test_state / "workspaces").resolve()
    assert os.environ["LIRUNBAO_DB_PATH"] == str(test_state / "app.db")
    assert os.environ["LIRUNBAO_AI_CONFIG_PATH"] == str(test_state / ".ai_config.json")
    assert os.environ["LIRUNBAO_WORKSPACE_PATH"] == str(test_state / "workspaces")


def test_test_state_is_removed_when_child_process_exits():
    project_root = Path(__file__).resolve().parents[1]
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import tests; print(tests._TEST_STATE, flush=True)",
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    child_state = Path(child.stdout.strip())

    assert child_state.name.startswith("lirunbao-tests-")
    assert not child_state.exists()


def test_source_files_hashes_in_chunks_without_path_read_bytes(tmp_path, monkeypatch):
    content = (b"annual-report-block" * 70_000) + b"tail"
    source = tmp_path / "2024-report.pdf"
    source.write_bytes(content)
    original_open = Path.open
    read_sizes = []

    class TrackedReader:
        def __init__(self, raw):
            self.raw = raw

        def __enter__(self):
            self.raw.__enter__()
            return self

        def __exit__(self, exc_type, exc, traceback):
            return self.raw.__exit__(exc_type, exc, traceback)

        def read(self, size=-1):
            read_sizes.append(size)
            return self.raw.read(size)

    def track_binary_open(path, *args, **kwargs):
        raw = original_open(path, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        return TrackedReader(raw) if path == source and mode == "rb" else raw

    def reject_read_bytes(_path):
        raise AssertionError("source hashing must not load the whole file with read_bytes()")

    monkeypatch.setattr(Path, "open", track_binary_open)
    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)

    snapshot = import_mod._source_files([source], [{}], {str(source): [2024]})[0]

    assert snapshot["sha256"] == hashlib.sha256(content).hexdigest()
    assert snapshot["size"] == len(content)
    assert len(read_sizes) >= 3
    assert set(read_sizes) == {1024 * 1024}


def test_import_removes_new_batch_after_parse_failure(client, tmp_path, monkeypatch):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    monkeypatch.setattr(
        import_mod,
        "_parse_one",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(parser.ParserError("bad report")),
    )

    response = client.post(
        "/api/import",
        files={"files": ("broken.pdf", b"broken", "application/pdf")},
    )

    assert response.status_code == 400
    assert list(workspace.glob("import-*")) == []


def test_import_removes_partial_batch_after_upload_failure(client, tmp_path, monkeypatch):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))

    def fail_upload(_upload, batch):
        (batch / "partial-upload").write_bytes(b"partial")
        raise import_mod.HTTPException(status_code=500, detail="upload failed")

    monkeypatch.setattr(import_mod, "_save_upload", fail_upload)

    response = client.post(
        "/api/import",
        files={"files": ("broken.pdf", b"broken", "application/pdf")},
    )

    assert response.status_code == 500
    assert list(workspace.glob("import-*")) == []


@pytest.mark.parametrize("failing_phase", ["preview", "snapshot"])
def test_import_removes_new_batch_after_late_phase_failure(
    client, tmp_path, monkeypatch, failing_phase
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    data = parser.parse_financial_dict(build_sample_data())
    monkeypatch.setattr(import_mod, "_parse_one", lambda *_args, **_kwargs: data)
    if failing_phase == "preview":
        monkeypatch.setattr(
            import_mod,
            "_safe_preview",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                import_mod.HTTPException(status_code=500, detail="preview failed")
            ),
        )
    else:
        monkeypatch.setattr(import_mod, "_safe_preview", lambda path: {"name": Path(path).name, "notes": []})
        monkeypatch.setattr(
            import_mod,
            "_source_files",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                import_mod.HTTPException(status_code=500, detail="snapshot failed")
            ),
        )

    response = client.post(
        "/api/import",
        files={"files": ("report.pdf", b"report", "application/pdf")},
    )

    assert response.status_code == 500
    assert list(workspace.glob("import-*")) == []


@pytest.mark.parametrize("failing_phase", ["upload", "parse", "preview", "snapshot", "persistence"])
def test_failed_import_cleanup_failure_is_tracked_and_retryable(
    client, tmp_path, monkeypatch, failing_phase
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    data = parser.parse_financial_dict(build_sample_data())
    monkeypatch.setattr(import_mod, "_parse_one", lambda *_args, **_kwargs: data)
    monkeypatch.setattr(
        import_mod,
        "_safe_preview",
        lambda path: {"name": Path(path).name, "notes": []},
    )
    if failing_phase == "upload":
        def fail_upload(_upload, batch):
            (batch / "partial").write_bytes(b"partial")
            raise import_mod.HTTPException(status_code=500, detail="upload failed")

        monkeypatch.setattr(import_mod, "_save_upload", fail_upload)
    elif failing_phase == "parse":
        monkeypatch.setattr(
            import_mod,
            "_parse_one",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(parser.ParserError("parse failed")),
        )
    elif failing_phase == "preview":
        monkeypatch.setattr(
            import_mod,
            "_safe_preview",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                import_mod.HTTPException(status_code=500, detail="preview failed")
            ),
        )
    elif failing_phase == "snapshot":
        monkeypatch.setattr(
            import_mod,
            "_source_files",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                import_mod.HTTPException(status_code=500, detail="snapshot failed")
            ),
        )
    else:
        monkeypatch.setattr(
            db_mod,
            "save_session",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )
    original_remove = import_mod._remove_managed_import_batch
    monkeypatch.setattr(import_mod, "_remove_managed_import_batch", lambda *_args: False)

    response = client.post(
        "/api/import",
        files={"files": ("report.pdf", b"report", "application/pdf")},
    )

    assert response.status_code == 500
    assert "清理" in response.json()["detail"]
    assert hasattr(db_mod, "list_workspace_retirements")
    records = [
        item
        for item in db_mod.list_workspace_retirements()
        if Path(item["workspace_path"]) == workspace.resolve()
    ]
    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert records[0]["attempts"] == 1
    batch = Path(records[0]["batch_path"])
    assert batch.exists()

    monkeypatch.setattr(import_mod, "_remove_managed_import_batch", original_remove)
    assert import_mod.retry_workspace_cleanup() == 1
    assert not batch.exists()
    assert [
        item
        for item in db_mod.list_workspace_retirements()
        if Path(item["workspace_path"]) == workspace.resolve()
    ] == []


def test_failed_import_deletes_batch_even_when_retirement_database_is_unavailable(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    monkeypatch.setattr(
        import_mod,
        "_parse_one",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(parser.ParserError("parse failed")),
    )
    monkeypatch.setattr(
        db_mod,
        "queue_workspace_retirement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("database unavailable")),
    )

    response = client.post(
        "/api/import",
        files={"files": ("report.pdf", b"report", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "parse failed"
    assert list(workspace.glob("import-*")) == []


def test_failed_import_double_cleanup_failure_has_durable_fallback_marker(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    monkeypatch.setattr(
        import_mod,
        "_parse_one",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(parser.ParserError("parse failed")),
    )
    original_remove = import_mod._remove_managed_import_batch
    original_queue = db_mod.queue_workspace_retirement
    monkeypatch.setattr(import_mod, "_remove_managed_import_batch", lambda *_args: False)
    monkeypatch.setattr(
        db_mod,
        "queue_workspace_retirement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("database unavailable")),
    )

    response = client.post(
        "/api/import",
        files={"files": ("report.pdf", b"report", "application/pdf")},
    )

    assert response.status_code == 500
    assert "cleanup_id=fallback-" in response.json()["detail"]
    batches = list(workspace.glob("import-*"))
    markers = list(workspace.glob(".cleanup-pending-*.json"))
    assert len(batches) == 1
    assert len(markers) == 1

    monkeypatch.setattr(import_mod, "_remove_managed_import_batch", original_remove)
    monkeypatch.setattr(db_mod, "queue_workspace_retirement", original_queue)
    assert import_mod.retry_workspace_cleanup() == 1
    assert not batches[0].exists()
    assert not markers[0].exists()


def _write_fallback_marker(
    workspace: Path, marker_id: str, batch: Path, **overrides
) -> Path:
    marker = workspace / f".cleanup-pending-fallback-{marker_id}.json"
    payload = {
        "cleanup_id": f"fallback-{marker_id}",
        "workspace_path": str(workspace.resolve()),
        "batch_path": str(batch.resolve()),
        "reason": "failed_import_rollback",
        "last_error": "cleanup failed",
        "queue_error": "database unavailable",
    }
    payload.update(overrides)
    marker.write_text(json.dumps(payload), encoding="utf-8")
    return marker


def test_fallback_marker_never_deletes_sqlite_current_batch(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    current_source = workspace / "import-current" / "stored" / "current.pdf"
    current_source.parent.mkdir(parents=True)
    current_source.write_bytes(b"current")
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    data = parser.parse_financial_dict(build_sample_data())
    source_files = [
        {
            "path": str(current_source),
            "name": current_source.name,
            "sha256": hashlib.sha256(b"current").hexdigest(),
        }
    ]
    session_mod.replace(data, [], source_files)
    marker = _write_fallback_marker(
        workspace, "forged-current", current_source.parents[1]
    )
    # 删除内存副本，证明保护集合只能来自 SQLite 当前 source_files。
    session_mod._source_files = []

    assert import_mod.retry_workspace_cleanup() == 0

    assert current_source.read_bytes() == b"current"
    assert marker.exists()
    assert db_mod.load_session()["source_files"] == source_files
    session_mod._source_files = source_files


def test_fallback_marker_waits_for_queued_job_lease(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    leased_source = workspace / "import-leased" / "stored" / "leased.pdf"
    leased_source.parent.mkdir(parents=True)
    leased_source.write_bytes(b"leased")
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    marker = _write_fallback_marker(workspace, "leased", leased_source.parents[1])
    job_id = f"job-fallback-lease-{time.time_ns()}"
    db_mod.create_job(job_id, f"leased-version-{time.time_ns()}")

    assert import_mod.retry_workspace_cleanup() == 0
    assert leased_source.exists()
    assert marker.exists()

    assert _job_module().fail_job(job_id, "cancelled") is True
    assert not leased_source.exists()
    assert not marker.exists()


def test_fallback_marker_validation_isolated_and_safe(
    client, tmp_path, monkeypatch, caplog
):
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    retired_source = workspace / "import-retired" / "stored" / "old.pdf"
    retired_source.parent.mkdir(parents=True)
    retired_source.write_bytes(b"old")
    first = _write_fallback_marker(workspace, "retired-a", retired_source.parents[1])
    duplicate = _write_fallback_marker(
        workspace, "retired-b", retired_source.parents[1]
    )
    corrupt = workspace / ".cleanup-pending-fallback-corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    schema_invalid = workspace / ".cleanup-pending-fallback-schema.json"
    schema_invalid.write_text(
        json.dumps(
            {
                "cleanup_id": "fallback-schema",
                "workspace_path": str(workspace),
                "batch_path": [],
            }
        ),
        encoding="utf-8",
    )
    external_source = tmp_path / "outside" / "import-external" / "outside.pdf"
    external_source.parent.mkdir(parents=True)
    external_source.write_bytes(b"outside")
    external = _write_fallback_marker(
        workspace, "external", external_source.parents[1]
    )
    forged_workspace = _write_fallback_marker(
        workspace,
        "wrong-workspace",
        retired_source.parents[1],
        workspace_path=str(tmp_path / "other-workspace"),
    )

    with caplog.at_level("ERROR", logger=import_mod.__name__):
        removed = import_mod.retry_workspace_cleanup()

    assert removed == 1
    assert not retired_source.exists()
    assert not first.exists()
    assert not duplicate.exists()
    assert external_source.read_bytes() == b"outside"
    assert corrupt.exists()
    assert schema_invalid.exists()
    assert external.exists()
    assert forged_workspace.exists()
    assert caplog.text.count("工作区兜底清理重试失败") >= 4


def test_clear_database_failure_keeps_memory_sqlite_and_source_consistent(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    monkeypatch.setattr(
        import_mod,
        "_parse_one",
        lambda *_args, **_kwargs: parser.parse_financial_dict(build_sample_data()),
    )
    monkeypatch.setattr(
        import_mod,
        "_safe_preview",
        lambda path: {"name": Path(path).name, "notes": []},
    )
    assert client.post(
        "/api/import",
        files={"files": ("old.pdf", b"old", "application/pdf")},
    ).status_code == 200
    old_sources = session_mod.get_source_files()
    old_path = Path(old_sources[0]["path"])
    original_clear = db_mod.clear_session_db
    monkeypatch.setattr(
        db_mod,
        "clear_session_db",
        lambda: (_ for _ in ()).throw(OSError("database unavailable")),
    )

    with TestClient(app_mod.create_app(), raise_server_exceptions=False) as non_raising_client:
        response = non_raising_client.post("/api/session/clear")

    assert response.status_code == 500
    assert session_mod.get_source_files() == old_sources
    assert db_mod.load_session()["source_files"] == old_sources
    assert old_path.exists()

    monkeypatch.setattr(db_mod, "clear_session_db", original_clear)
    # 模拟进程内缓存丢失；退休保护必须以 SQLite 的当前 source_files 为事实源。
    session_mod._source_files = []
    assert import_mod.retry_workspace_cleanup() == 0
    assert old_path.exists()
    session_mod._source_files = old_sources


def test_committed_replace_returns_success_when_retirement_scan_database_fails(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))

    def parsed(_path, company_name, _industry):
        data = parser.parse_financial_dict(build_sample_data())
        data.company_name = company_name
        return data

    monkeypatch.setattr(import_mod, "_parse_one", parsed)
    monkeypatch.setattr(
        import_mod,
        "_safe_preview",
        lambda path: {"name": Path(path).name, "notes": []},
    )
    assert client.post(
        "/api/import",
        files={"files": ("old.pdf", b"old", "application/pdf")},
        data={"company_name": "旧会话"},
    ).status_code == 200
    old_batch = Path(session_mod.get_source_files()[0]["path"]).parents[1]
    original_list = db_mod.list_workspace_retirements
    monkeypatch.setattr(
        db_mod,
        "list_workspace_retirements",
        lambda: (_ for _ in ()).throw(OSError("retirement database unavailable")),
    )

    response = client.post(
        "/api/import",
        files={"files": ("new.pdf", b"new", "application/pdf")},
        data={"company_name": "新会话"},
    )

    assert response.status_code == 200
    assert response.json()["summary"]["company_name"] == "新会话"
    assert session_mod.get_data().company_name == "新会话"
    assert db_mod.load_session()["company_name"] == "新会话"
    assert old_batch.exists()
    monkeypatch.setattr(db_mod, "list_workspace_retirements", original_list)
    assert import_mod.retry_workspace_cleanup() == 1
    assert not old_batch.exists()


def test_legal_dotdot_workspace_alias_rolls_back_failed_import(
    client, tmp_path, monkeypatch
):
    (tmp_path / "alias").mkdir()
    workspace_alias = tmp_path / "alias" / ".." / "workspaces"
    workspace = workspace_alias.resolve()
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace_alias))
    monkeypatch.setattr(
        import_mod,
        "_parse_one",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(parser.ParserError("parse failed")),
    )

    response = client.post(
        "/api/import",
        files={"files": ("report.pdf", b"report", "application/pdf")},
    )

    assert response.status_code == 400
    assert list(workspace.glob("import-*")) == []


def test_successful_reimport_removes_old_batch_and_keeps_new_snapshot(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    monkeypatch.setattr(
        import_mod,
        "_parse_one",
        lambda *_args, **_kwargs: parser.parse_financial_dict(build_sample_data()),
    )
    monkeypatch.setattr(import_mod, "_safe_preview", lambda path: {"name": Path(path).name, "notes": []})

    first = client.post(
        "/api/import",
        files={"files": ("old.pdf", b"old", "application/pdf")},
    )
    assert first.status_code == 200
    old_source = session_mod.get_source_files()[0]
    old_batch = Path(old_source["path"]).parents[1]

    second = client.post(
        "/api/import",
        files={"files": ("new.pdf", b"new", "application/pdf")},
    )
    assert second.status_code == 200
    new_source = session_mod.get_source_files()[0]
    new_path = Path(new_source["path"])
    new_batch = new_path.parents[1]

    assert not old_batch.exists()
    assert new_batch.exists()
    assert new_path.exists()
    assert session_mod.get_source_files() == [new_source]


def test_persistence_failure_removes_new_batch_but_preserves_old_session_files(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    monkeypatch.setattr(
        import_mod,
        "_parse_one",
        lambda *_args, **_kwargs: parser.parse_financial_dict(build_sample_data()),
    )
    monkeypatch.setattr(import_mod, "_safe_preview", lambda path: {"name": Path(path).name, "notes": []})
    first = client.post(
        "/api/import",
        files={"files": ("old.pdf", b"old", "application/pdf")},
    )
    assert first.status_code == 200
    old_sources = session_mod.get_source_files()
    old_path = Path(old_sources[0]["path"])
    old_batch = old_path.parents[1]
    monkeypatch.setattr(
        db_mod,
        "save_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with TestClient(app_mod.create_app(), raise_server_exceptions=False) as non_raising_client:
        response = non_raising_client.post(
            "/api/import",
            files={"files": ("new.pdf", b"new", "application/pdf")},
        )

    assert response.status_code == 500
    assert session_mod.get_source_files() == old_sources
    assert old_batch.exists()
    assert old_path.exists()
    assert list(workspace.glob("import-*")) == [old_batch]


def test_old_batch_cleanup_failure_does_not_rollback_new_session(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    monkeypatch.setattr(
        import_mod,
        "_parse_one",
        lambda *_args, **_kwargs: parser.parse_financial_dict(build_sample_data()),
    )
    monkeypatch.setattr(import_mod, "_safe_preview", lambda path: {"name": Path(path).name, "notes": []})
    first = client.post(
        "/api/import",
        files={"files": ("old.pdf", b"old", "application/pdf")},
    )
    assert first.status_code == 200
    old_batch = Path(session_mod.get_source_files()[0]["path"]).parents[1]
    original_remove = import_mod._remove_managed_import_batch

    def fail_old_cleanup(batch, active_workspace):
        if batch == old_batch.resolve():
            raise OSError("cleanup denied")
        return original_remove(batch, active_workspace)

    monkeypatch.setattr(import_mod, "_remove_managed_import_batch", fail_old_cleanup)

    response = client.post(
        "/api/import",
        files={"files": ("new.pdf", b"new", "application/pdf")},
    )
    new_path = Path(session_mod.get_source_files()[0]["path"])

    assert response.status_code == 200
    assert response.json()["summary"]["company_name"] == session_mod.get_data().company_name
    assert old_batch.exists()
    assert new_path.exists()
    assert new_path.parents[1] != old_batch


def test_reimport_cleanup_ignores_external_root_and_non_batch_paths(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    managed_file = workspace / "import-old" / "stored" / "old.pdf"
    external_file = tmp_path / "external" / "import-external" / "outside.pdf"
    root_pdf = workspace / "root.pdf"
    root_db = workspace / "app.db"
    unmatched_file = workspace / "archive-old" / "stored" / "keep.pdf"
    for path in (managed_file, external_file, root_pdf, root_db, unmatched_file):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode("utf-8"))
    old_data = parser.parse_financial_dict(build_sample_data())
    old_sources = [
        {"path": str(path), "name": path.name, "sha256": "old"}
        for path in (managed_file, external_file, root_pdf, root_db, unmatched_file)
    ]
    session_mod.replace(old_data, [], old_sources)
    monkeypatch.setattr(
        import_mod,
        "_parse_one",
        lambda *_args, **_kwargs: parser.parse_financial_dict(build_sample_data()),
    )
    monkeypatch.setattr(import_mod, "_safe_preview", lambda path: {"name": Path(path).name, "notes": []})

    response = client.post(
        "/api/import",
        files={"files": ("new.pdf", b"new", "application/pdf")},
    )

    assert response.status_code == 200
    assert not managed_file.parent.parent.exists()
    assert external_file.exists()
    assert root_pdf.exists()
    assert root_db.exists()
    assert unmatched_file.exists()
    assert Path(session_mod.get_source_files()[0]["path"]).exists()


def test_cleanup_rejects_top_level_symlink_embedded_symlink_and_dotdot(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    external = tmp_path / "external"
    external.mkdir()
    external_file = external / "keep.pdf"
    external_file.write_bytes(b"outside")
    workspace.mkdir()
    top_link = workspace / "import-link"
    top_link.symlink_to(external, target_is_directory=True)
    embedded_batch = workspace / "import-embedded"
    embedded_source = embedded_batch / "stored" / "old.pdf"
    embedded_source.parent.mkdir(parents=True)
    embedded_source.write_bytes(b"old")
    (embedded_batch / "external-link").symlink_to(external, target_is_directory=True)
    traversal_batch = workspace / "import-traversal"
    traversal_batch.mkdir()
    traversal_source = traversal_batch / ".." / ".." / "external" / "keep.pdf"
    dotdot_batch_alias = workspace / "nested" / ".." / "import-embedded"
    assert import_mod._validated_import_batch(dotdot_batch_alias, workspace) is None
    old_data = parser.parse_financial_dict(build_sample_data())
    session_mod.replace(
        old_data,
        [],
        [
            {"path": str(top_link / "keep.pdf"), "name": "top.pdf", "sha256": "top"},
            {"path": str(embedded_source), "name": "embedded.pdf", "sha256": "embedded"},
            {"path": str(traversal_source), "name": "traversal.pdf", "sha256": "traversal"},
        ],
    )
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    monkeypatch.setattr(
        import_mod,
        "_parse_one",
        lambda *_args, **_kwargs: parser.parse_financial_dict(build_sample_data()),
    )
    monkeypatch.setattr(
        import_mod,
        "_safe_preview",
        lambda path: {"name": Path(path).name, "notes": []},
    )

    response = client.post(
        "/api/import",
        files={"files": ("new.pdf", b"new", "application/pdf")},
    )

    assert response.status_code == 200
    assert top_link.is_symlink()
    assert traversal_batch.exists()
    assert not embedded_batch.exists()
    assert external_file.read_bytes() == b"outside"


@pytest.mark.parametrize("endpoint", ["/api/import/sample", "/api/session/clear"])
def test_sample_and_clear_immediately_retire_old_batch_without_active_job(
    client, tmp_path, monkeypatch, endpoint
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    monkeypatch.setattr(
        import_mod,
        "_parse_one",
        lambda *_args, **_kwargs: parser.parse_financial_dict(build_sample_data()),
    )
    monkeypatch.setattr(
        import_mod,
        "_safe_preview",
        lambda path: {"name": Path(path).name, "notes": []},
    )
    imported = client.post(
        "/api/import",
        files={"files": ("old.pdf", b"old", "application/pdf")},
    )
    assert imported.status_code == 200
    old_batch = Path(session_mod.get_source_files()[0]["path"]).parents[1]

    replaced = client.post(endpoint)

    assert replaced.status_code == 200
    assert not old_batch.exists()


@pytest.mark.parametrize("preview_raises", [False, True])
def test_preview_always_removes_temporary_uploads(
    client, tmp_path, monkeypatch, preview_raises
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    if preview_raises:
        monkeypatch.setattr(
            import_mod,
            "_safe_preview",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                import_mod.HTTPException(status_code=500, detail="preview failed")
            ),
        )
    else:
        monkeypatch.setattr(
            import_mod,
            "_safe_preview",
            lambda path: {"name": Path(path).name, "notes": []},
        )

    response = client.post(
        "/api/preview",
        files={"files": ("preview.pdf", b"preview", "application/pdf")},
    )

    assert response.status_code == (500 if preview_raises else 200)
    assert list(workspace.iterdir()) == []


def test_import_keeps_same_basename_sources_distinct(client, monkeypatch):
    data = parser.parse_financial_dict(build_sample_data())
    data.years = [2022]
    monkeypatch.setattr(import_mod, "_parse_one", lambda *args, **kwargs: data)
    monkeypatch.setattr(import_mod, "_safe_preview", lambda path: {"name": Path(path).name, "notes": []})
    response = client.post(
        "/api/import",
        files=[
            ("files", ("2022/审计报告.pdf", b"first", "application/pdf")),
            ("files", ("2023/审计报告.pdf", b"second", "application/pdf")),
        ],
        data={"company_name": "云南艺康", "industry": "制造业"},
    )
    assert response.status_code == 200
    sources = session_mod.get_source_files()
    assert [source["name"] for source in sources] == ["审计报告.pdf", "审计报告.pdf"]
    assert sources[0]["path"] != sources[1]["path"]
    assert Path(sources[0]["path"]).read_bytes() == b"first"
    assert Path(sources[1]["path"]).read_bytes() == b"second"


def test_import_keeps_page_counts_with_same_basename(client, monkeypatch):
    data = parser.parse_financial_dict(build_sample_data())
    data.years = [2022]
    previews = iter([
        {"name": "审计报告.pdf", "notes": ["共 2 页"]},
        {"name": "审计报告.pdf", "notes": ["共 9 页"]},
    ])
    monkeypatch.setattr(import_mod, "_parse_one", lambda *args, **kwargs: data)
    monkeypatch.setattr(import_mod, "_safe_preview", lambda path: next(previews))
    response = client.post(
        "/api/import",
        files=[
            ("files", ("2022/审计报告.pdf", b"first", "application/pdf")),
            ("files", ("2023/审计报告.pdf", b"second", "application/pdf")),
        ],
    )
    assert response.status_code == 200
    assert [source["page_count"] for source in session_mod.get_source_files()] == [2, 9]


def test_import_prefers_parsed_year_and_uses_unambiguous_filename_fallback(client, monkeypatch):
    def parse_one(path, *_args):
        data = parser.parse_financial_dict(build_sample_data())
        data.years = [2023] if "2021-2022" in path else []
        return data

    monkeypatch.setattr(import_mod, "_parse_one", parse_one)
    monkeypatch.setattr(import_mod, "_safe_preview", lambda path: {"name": Path(path).name, "notes": []})
    parsed = client.post(
        "/api/import",
        files={"files": ("2021-2022审计报告.pdf", b"parsed-year", "application/pdf")},
    )
    assert parsed.status_code == 200
    assert session_mod.get_source_files()[0]["report_year"] == 2023
    fallback = client.post(
        "/api/import",
        files={"files": ("2024年审计报告.pdf", b"filename-year", "application/pdf")},
    )
    assert fallback.status_code == 200
    assert session_mod.get_source_files()[0]["report_year"] == 2024


def test_replace_does_not_publish_memory_when_persistence_fails(client, monkeypatch):
    old = parser.parse_financial_dict(build_sample_data())
    old.company_name = "旧会话"
    old_source = [{"path": "/tmp/old.pdf", "name": "old.pdf", "sha256": "old", "size": 1, "report_year": 2022, "page_count": 1}]
    session_mod.replace(old, ["旧 OCR"], old_source)
    persisted = db_mod.load_session()
    newer = parser.parse_financial_dict(build_sample_data())
    newer.company_name = "新会话"
    monkeypatch.setattr(db_mod, "save_session", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        session_mod.replace(newer, ["新 OCR"], [])
    assert session_mod.get_data().company_name == "旧会话"
    assert session_mod.get_ocr_texts() == ["旧 OCR"]
    assert session_mod.get_source_files() == old_source
    assert db_mod.load_session() == persisted


def test_restore_does_not_overwrite_newer_replace(client, monkeypatch):
    old = parser.parse_financial_dict(build_sample_data())
    old.company_name = "旧会话"
    session_mod.replace(old, [], [])
    session_mod._data = None
    session_mod._ocr_texts = []
    session_mod._source_files = []
    session_mod._session_version = ""
    started = threading.Event()
    continue_restore = threading.Event()
    original_parse = session_mod.parser_mod.parse_financial_dict

    def paused_parse(raw):
        started.set()
        assert continue_restore.wait(timeout=2)
        return original_parse(raw)

    monkeypatch.setattr(session_mod.parser_mod, "parse_financial_dict", paused_parse)
    worker = threading.Thread(target=session_mod.restore_from_db)
    worker.start()
    assert started.wait(timeout=2)
    newer = original_parse(build_sample_data())
    newer.company_name = "新会话"
    session_mod.replace(newer, [], [])
    continue_restore.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert session_mod.get_data().company_name == "新会话"


def test_restore_parse_error_does_not_clear_newer_replace(client, monkeypatch):
    old = parser.parse_financial_dict(build_sample_data())
    old.company_name = "旧会话"
    session_mod.replace(old, [], [])
    session_mod._data = None
    session_mod._ocr_texts = []
    session_mod._source_files = []
    session_mod._session_version = ""
    started = threading.Event()
    continue_restore = threading.Event()
    original_parse = session_mod.parser_mod.parse_financial_dict

    def paused_failure(_raw):
        started.set()
        assert continue_restore.wait(timeout=2)
        raise ValueError("旧记录损坏")

    monkeypatch.setattr(session_mod.parser_mod, "parse_financial_dict", paused_failure)
    worker = threading.Thread(target=session_mod.restore_from_db)
    worker.start()
    assert started.wait(timeout=2)
    newer = original_parse(build_sample_data())
    newer.company_name = "新会话"
    session_mod.replace(newer, [], [])
    continue_restore.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert session_mod.get_data().company_name == "新会话"


def test_init_db_migrates_legacy_session_without_losing_id_one(tmp_path, monkeypatch):
    legacy_path = tmp_path / "legacy.db"
    monkeypatch.setenv("LIRUNBAO_DB_PATH", str(legacy_path))
    with sqlite3.connect(legacy_path) as conn:
        conn.execute(
            """
            CREATE TABLE session (
                id INTEGER PRIMARY KEY CHECK (id = 1), company_name TEXT DEFAULT '',
                industry TEXT DEFAULT '制造业', years TEXT DEFAULT '[]', indicators TEXT DEFAULT '[]',
                data_json TEXT DEFAULT '{}', ocr_texts TEXT DEFAULT '[]', updated_at TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            "INSERT INTO session VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            ("历史企业", "制造业", "[2022]", "[]", "{}", '["OCR"]', "legacy"),
        )
    db_mod.init_db()
    loaded = db_mod.load_session()
    assert loaded == {
        "company_name": "历史企业",
        "industry": "制造业",
        "years": [2022],
        "indicators": [],
        "data_json": {},
        "ocr_texts": ["OCR"],
        "source_files": [],
        "saved_previews": [],
        "session_version": "",
    }
    with sqlite3.connect(legacy_path) as conn:
        assert conn.execute("SELECT updated_at FROM session WHERE id = 1").fetchone()[0] == "legacy"


def test_sample_replaces_source_snapshot_with_distinct_version(client, monkeypatch):
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "导入企业"
    monkeypatch.setattr(import_mod, "_parse_one", lambda *args, **kwargs: data)
    monkeypatch.setattr(import_mod, "_safe_preview", lambda path: {"name": Path(path).name, "notes": []})
    response = client.post(
        "/api/import",
        files={"files": ("2022年审计报告.pdf", b"source", "application/pdf")},
    )
    assert response.status_code == 200
    imported_version = session_mod.get_version()
    sample = client.post("/api/import/sample")
    assert sample.status_code == 200
    assert session_mod.get_source_files() == []
    assert session_mod.get_version()
    assert session_mod.get_version() != imported_version


def test_extract_all_pages_has_no_preview_limit(monkeypatch):
    """完整读取不能沿用预览页数或 600 字符的截断限制。"""
    monkeypatch.setattr(
        reader,
        "_open_pdf_documents",
        lambda path: nullcontext((object(), _FakePdfiumDocument(30))),
    )
    monkeypatch.setattr(
        reader, "_extract_text_from_document", lambda document, i: "财务数据 2024" * 400
    )

    pages = reader.extract_all_pages("2022.pdf")

    assert len(pages) == 30
    assert all(len(page.text) > 600 for page in pages)
    assert [page.page_no for page in pages] == list(range(1, 31))
    assert all(page.total_pages == 30 for page in pages)
    assert all(page.status == "ok" and page.method == "text" for page in pages)


def test_extract_all_pages_records_failed_page(monkeypatch):
    """单页文本层与 OCR 都失败时，结果仍保留该页的失败记录。"""
    monkeypatch.setattr(
        reader,
        "_open_pdf_documents",
        lambda path: nullcontext((object(), _FakePdfiumDocument(2))),
    )
    monkeypatch.setattr(
        reader,
        "_extract_text_from_document",
        lambda document, i: "" if i == 1 else "有效文本 2024" * 10,
    )
    monkeypatch.setattr(reader, "_get_ocr_engine", lambda: object())
    monkeypatch.setattr(
        reader,
        "_extract_ocr_from_document",
        lambda document, i, engine: (_ for _ in ()).throw(RuntimeError("OCR failed")),
    )

    pages = reader.extract_all_pages("scan.pdf")

    assert pages[1].status == "failed"
    assert pages[1].page_no == 2
    assert pages[1].method == "none"
    assert pages[1].text == ""


def test_extract_all_pages_reports_progress_for_each_page(monkeypatch):
    """进度回调必须逐页执行，使长报告能够显示真实处理进度。"""
    monkeypatch.setattr(
        reader,
        "_open_pdf_documents",
        lambda path: nullcontext((object(), _FakePdfiumDocument(3))),
    )
    monkeypatch.setattr(
        reader, "_extract_text_from_document", lambda document, i: "财务数据 2024" * 30
    )
    progress: list[tuple[int, int]] = []

    reader.extract_all_pages("2024.pdf", on_progress=lambda current, total: progress.append((current, total)))

    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_chunk_pages_keeps_page_boundaries_and_full_text():
    """分块只能在页边界断开，且不得丢弃任一页正文。"""
    pages = [
        reader.PDFPageRecord(1, 3, "text", "甲" * 10, "ok"),
        reader.PDFPageRecord(2, 3, "text", "乙" * 10, "ok"),
        reader.PDFPageRecord(3, 3, "text", "丙" * 10, "ok"),
    ]

    chunks = reader.chunk_pages(pages, max_chars=35)

    assert [(chunk.start_page, chunk.end_page, chunk.total_pages) for chunk in chunks] == [
        (1, 2, 3),
        (3, 3, 3),
    ]
    assert "甲" * 10 in chunks[0].text
    assert "乙" * 10 in chunks[0].text
    assert "丙" * 10 in chunks[1].text


def test_file_sha256_is_content_stable(tmp_path):
    """相同文件内容应产生可用于缓存复用的稳定哈希。"""
    pdf = tmp_path / "annual-report.pdf"
    pdf.write_bytes(b"full-report-content")

    assert reader.file_sha256(pdf) == hashlib.sha256(b"full-report-content").hexdigest()


def test_page_cache_reuses_only_complete_page_set(tmp_path, monkeypatch):
    """缓存缺页时必须视为失效，避免把不完整报告交给 AI。"""
    db_path = tmp_path / "page-cache.db"
    monkeypatch.setenv("LIRUNBAO_DB_PATH", str(db_path))
    page_hash = "a" * 64
    pages = [
        {"page_no": 1, "total_pages": 2, "method": "text", "status": "ok", "text": "第一页"},
        {"page_no": 2, "total_pages": 2, "method": "ocr", "status": "ok", "text": "第二页"},
    ]

    db_mod.save_cached_pages(page_hash, pages)
    assert db_mod.load_cached_pages(page_hash) == pages

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM pdf_page_cache WHERE file_sha256 = ? AND page_no = 2", (page_hash,)
        )
    assert db_mod.load_cached_pages(page_hash) == []


def test_deepseek_page_text_adapter_reads_every_reader_page(monkeypatch):
    """旧 max_pages 参数不能重新引入整份 AI 解析的页数截断。"""
    records = [
        reader.PDFPageRecord(1, 2, "text", "第一页", "ok"),
        reader.PDFPageRecord(2, 2, "ocr", "第二页", "ok"),
    ]
    monkeypatch.setattr(
        deepseek_parser,
        "full_pdf_reader",
        type("Reader", (), {"extract_all_pages": lambda path: records, "_format_page": reader._format_page}),
        raising=False,
    )

    assert deepseek_parser.extract_pdf_pages_text("full.pdf", max_pages=1) == [
        "[第1页]\n第一页",
        "[第2页]\n[OCR] 第二页",
    ]


class _FakePdfiumDocument:
    def __init__(self, total_pages: int):
        self.total_pages = total_pages

    def __len__(self):
        return self.total_pages


def test_extract_all_pages_marks_low_quality_text_failed_when_ocr_is_empty(monkeypatch):
    """已知低质量文本在 OCR 没有结果时不能伪装为成功页。"""
    monkeypatch.setattr(
        reader,
        "_open_pdf_documents",
        lambda path: nullcontext((object(), _FakePdfiumDocument(1))),
    )
    monkeypatch.setattr(reader, "_extract_text_from_document", lambda doc, i: "文本过短")
    monkeypatch.setattr(reader, "_get_ocr_engine", lambda: object())
    monkeypatch.setattr(reader, "_extract_ocr_from_document", lambda doc, i, engine: "")

    page = reader.extract_all_pages("low-quality.pdf")[0]

    assert page == reader.PDFPageRecord(1, 1, "text", "文本过短", "failed")


def test_extract_all_pages_marks_low_quality_text_failed_when_ocr_raises(monkeypatch):
    """OCR 异常也须将低质量文本记录为失败，同时保留可审计的原文。"""
    monkeypatch.setattr(
        reader,
        "_open_pdf_documents",
        lambda path: nullcontext((object(), _FakePdfiumDocument(1))),
    )
    monkeypatch.setattr(reader, "_extract_text_from_document", lambda doc, i: "文本过短")
    monkeypatch.setattr(reader, "_get_ocr_engine", lambda: object())
    monkeypatch.setattr(
        reader,
        "_extract_ocr_from_document",
        lambda doc, i, engine: (_ for _ in ()).throw(RuntimeError("OCR unavailable")),
    )

    page = reader.extract_all_pages("ocr-error.pdf")[0]

    assert page == reader.PDFPageRecord(1, 1, "text", "文本过短", "failed")


def test_extract_all_pages_marks_true_blank_page_blank(monkeypatch):
    """已成功读取、文本层和 OCR 都为空的页面应与提取失败区分。"""
    monkeypatch.setattr(
        reader,
        "_open_pdf_documents",
        lambda path: nullcontext((object(), _FakePdfiumDocument(1))),
    )
    monkeypatch.setattr(reader, "_extract_text_from_document", lambda doc, i: "")
    monkeypatch.setattr(reader, "_get_ocr_engine", lambda: object())
    monkeypatch.setattr(reader, "_extract_ocr_from_document", lambda doc, i, engine: "")

    page = reader.extract_all_pages("blank.pdf")[0]

    assert page == reader.PDFPageRecord(1, 1, "none", "", "blank")


def test_extract_all_pages_reuses_open_documents_and_single_ocr_engine(monkeypatch):
    """一次长报告读取只打开一组文档，并在多页 OCR 间复用同一引擎。"""
    events: list[str] = []
    engine_calls: list[object] = []

    class Documents:
        def __enter__(self):
            events.append("open")
            return object(), _FakePdfiumDocument(2)

        def __exit__(self, exc_type, exc, traceback):
            events.append("close")
            return False

    engine = object()
    monkeypatch.setattr(reader, "_open_pdf_documents", lambda path: Documents())
    monkeypatch.setattr(reader, "_extract_text_from_document", lambda doc, i: "文本过短")
    monkeypatch.setattr(reader, "_get_ocr_engine", lambda: engine_calls.append(engine) or engine)
    monkeypatch.setattr(reader, "_extract_ocr_from_document", lambda doc, i, active: f"OCR 第{i + 1}页")

    pages = reader.extract_all_pages("two-page-scan.pdf")

    assert events == ["open", "close"]
    assert engine_calls == [engine]
    assert [page.method for page in pages] == ["ocr", "ocr"]


def test_save_cached_pages_rolls_back_delete_when_batch_insert_fails(tmp_path, monkeypatch):
    """批量插入失败后，事务必须保留同一文件原有的完整缓存。"""
    db_path = tmp_path / "transaction.db"
    monkeypatch.setenv("LIRUNBAO_DB_PATH", str(db_path))
    page_hash = "b" * 64
    original_pages = [
        {"page_no": 1, "total_pages": 1, "method": "text", "status": "ok", "text": "原缓存"}
    ]
    db_mod.save_cached_pages(page_hash, original_pages)
    raw_conn = sqlite3.connect(db_path)
    raw_conn.row_factory = sqlite3.Row

    class FailingBatchConnection:
        def __enter__(self):
            raw_conn.__enter__()
            return self

        def __exit__(self, exc_type, exc, traceback):
            return raw_conn.__exit__(exc_type, exc, traceback)

        def execute(self, *args, **kwargs):
            return raw_conn.execute(*args, **kwargs)

        def executemany(self, *args, **kwargs):
            raise sqlite3.OperationalError("insert failed")

    monkeypatch.setattr(db_mod, "init_db", lambda: None)
    monkeypatch.setattr(db_mod, "_connect", lambda: FailingBatchConnection())

    with pytest.raises(sqlite3.OperationalError, match="insert failed"):
        db_mod.save_cached_pages(page_hash, original_pages)

    assert db_mod.load_cached_pages(page_hash) == original_pages
    raw_conn.close()


class _ChatCompletionResponse:
    """与 OpenAI 兼容 chat completion 响应保持一致的请求替身。"""

    def __init__(self, finish_reason: str, message: dict | None = None):
        self._finish_reason = finish_reason
        self._message = message or {"role": "assistant", "content": "完成"}

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1_722_900_000,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": self._finish_reason,
                    "message": self._message,
                    "logprobs": None,
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
            "system_fingerprint": "fp-test",
        }


def test_chat_result_exposes_stop_metadata(monkeypatch):
    """缺失结果元数据时应失败，防止调用方无法判定回答是否完整。"""
    engine = AIEngine("https://api.deepseek.com", "test-key", "deepseek-v4-flash")
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: _ChatCompletionResponse("stop"))

    result = engine.chat_result("输入", max_tokens=100)

    assert result.content == "完成"
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 2
    assert result.model == "deepseek-v4-flash"
    assert engine.chat("输入", max_tokens=100) == "完成"


@pytest.mark.parametrize(
    ("finish_reason", "error_message"),
    [
        ("length", "输出被截断"),
        ("content_filter", "内容过滤"),
        ("insufficient_system_resource", "系统资源不足"),
        ("tool_calls", "工具调用"),
    ],
)
def test_chat_result_rejects_incomplete_completion_reasons(
    monkeypatch, finish_reason, error_message
):
    """移除完成原因检查会让被截断或未完成的文本被错误采纳。"""
    engine = AIEngine("https://api.deepseek.com", "test-key", "deepseek-v4-flash")
    monkeypatch.setattr(
        "requests.post", lambda *args, **kwargs: _ChatCompletionResponse(finish_reason)
    )

    with pytest.raises(AIEngineError, match=error_message):
        engine.chat_result("输入", max_tokens=100)


def test_chat_result_accepts_tool_calls_only_when_requested(monkeypatch):
    """工具调用是请求工具时的有效协议分支，普通文本请求则必须拒绝。"""
    engine = AIEngine("https://api.deepseek.com", "test-key", "deepseek-v4-flash")
    tool_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-test",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }
        ],
    }
    monkeypatch.setattr(
        "requests.post", lambda *args, **kwargs: _ChatCompletionResponse("tool_calls", tool_message)
    )

    result = engine.chat_result(
        "输入",
        max_tokens=100,
        extra={"tools": [{"type": "function", "function": {"name": "lookup"}}]},
    )

    assert result.content == ""
    assert result.finish_reason == "tool_calls"


def test_deepseek_parser_rejects_non_stop_completion(monkeypatch):
    """解析器不得把输出上限导致的不完整 JSON 继续交给财报映射。"""
    class UrlResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(_ChatCompletionResponse("length").json()).encode("utf-8")

    monkeypatch.setattr(deepseek_parser.urllib.request, "urlopen", lambda *args, **kwargs: UrlResponse())

    with pytest.raises(parser.ParserError, match="输出被截断"):
        deepseek_parser._deepseek_chat("输入", "系统", "test-key", "https://api.deepseek.com", "deepseek-v4-flash")


class _JSONEngine:
    """记录分段提取请求，并返回完整 OpenAI 兼容结果。"""

    def __init__(self, content: str, finish_reason: str = "stop"):
        self.content = content
        self.finish_reason = finish_reason
        self.calls: list[dict] = []

    def chat_result(self, user_prompt, **kwargs):
        self.calls.append({"user_prompt": user_prompt, **kwargs})
        return AIChatResult(
            content=self.content,
            finish_reason=self.finish_reason,
            prompt_tokens=100,
            completion_tokens=30,
            model="deepseek-v4-flash",
        )


def test_chunks_from_different_files_never_mix():
    """模型回显其他来源时，分段事实仍必须绑定调用方传入的文件与页段。"""
    engine = _JSONEngine(
        json.dumps(
            {
                "source_file": "2024年审计报告.pdf",
                "report_year": 2024,
                "page_range": [20, 25],
                "metrics": {},
                "audit_opinion": "",
                "major_events": [],
                "evidence": [],
            },
            ensure_ascii=False,
        )
    )
    source = {"name": "2022年审计报告.pdf", "report_year": 2022, "company_name": "云南艺康"}
    chunk = reader.PDFTextChunk(start_page=1, end_page=6, total_pages=30, text="审计报告文本")

    facts = ai_mod.extract_chunk_facts(engine, source, chunk)

    assert facts["source_file"] == "2022年审计报告.pdf"
    assert facts["report_year"] == 2022
    assert facts["page_range"] == [1, 6]
    assert facts["_total_pages"] == 30
    assert facts["_finish_reason"] == "stop"
    call = engine.calls[0]
    assert "云南艺康" in call["user_prompt"]
    assert "2022年审计报告.pdf" in call["user_prompt"]
    assert "1-6 / 30" in call["user_prompt"]
    assert call["max_tokens"] == reader.EXTRACT_MAX_TOKENS
    assert call["extra"] == {"response_format": {"type": "json_object"}, "stream": False}


def test_chunk_extraction_uses_none_for_absent_json_fields():
    """缺失事实不得被改写成零、空列表或其他貌似真实的默认值。"""
    engine = _JSONEngine(
        '{"source_file":"2022.pdf","report_year":2022,"page_range":[1,1],'
        '"metrics":{"营业收入":null}}'
    )
    source = {"name": "2022.pdf", "report_year": 2022}
    chunk = reader.PDFTextChunk(1, 1, 1, "文本")

    facts = ai_mod.extract_chunk_facts(engine, source, chunk)

    assert facts["metrics"] == {"营业收入": None}
    assert facts["audit_opinion"] is None
    assert facts["major_events"] is None
    assert facts["evidence"] is None


def test_chunk_extraction_rejects_non_exact_json_shape():
    """新增未约定键时应拒绝结果，避免模型悄然改变分段事实协议。"""
    engine = _JSONEngine(
        '{"source_file":"2022.pdf","report_year":2022,"page_range":[1,1],'
        '"metrics":{},"audit_opinion":null,"major_events":null,"evidence":null,'
        '"unexpected":"value"}'
    )
    chunk = reader.PDFTextChunk(1, 1, 1, "文本")

    with pytest.raises(AIEngineError, match="JSON 字段不符合约定"):
        ai_mod.extract_chunk_facts(engine, {"name": "2022.pdf", "report_year": 2022}, chunk)


@pytest.mark.parametrize(
    "invalid_value",
    ["true", "NaN", "Infinity", '"100"', "[]", "{}"],
)
def test_chunk_extraction_rejects_non_finite_or_non_numeric_metric_values(invalid_value):
    """指标值只能是 null 或有限数字，不能让 JSON 宽松值进入财务对账。"""
    engine = _JSONEngine(
        '{"source_file":"2022.pdf","report_year":2022,"page_range":[1,1],'
        f'"metrics":{{"营业收入":{invalid_value}}},'
        '"audit_opinion":null,"major_events":null,"evidence":null}'
    )

    with pytest.raises(AIEngineError, match="metrics"):
        ai_mod.extract_chunk_facts(
            engine,
            {"name": "2022.pdf", "report_year": 2022},
            reader.PDFTextChunk(1, 1, 1, "文本"),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("major_events", '[{"event":"新增生产线"}]'),
        ("evidence", '[{"pages":"1"}]'),
    ],
)
def test_chunk_extraction_rejects_invalid_nested_fact_shapes(field, value):
    """事件必须是字符串，证据必须是带整数页列表的对象。"""
    fields = {
        "metrics": "{}",
        "audit_opinion": "null",
        "major_events": "null",
        "evidence": "null",
    }
    fields[field] = value
    content = (
        '{"source_file":"2022.pdf","report_year":2022,"page_range":[2,3],'
        + ",".join(f'"{key}":{item}' for key, item in fields.items())
        + "}"
    )

    with pytest.raises(AIEngineError, match=field):
        ai_mod.extract_chunk_facts(
            _JSONEngine(content),
            {"name": "2022.pdf", "report_year": 2022},
            reader.PDFTextChunk(2, 3, 5, "文本"),
        )


def test_chunk_evidence_uses_trusted_source_metadata():
    """证据中的模型来源回显必须被当前可信文件、年度和页段覆盖。"""
    engine = _JSONEngine(
        '{"source_file":"other.pdf","report_year":2024,"page_range":[40,41],'
        '"metrics":{"营业收入":100},"audit_opinion":null,"major_events":null,'
        '"evidence":[{"metric":"营业收入","pages":[2],"text":"收入 100",'
        '"source_file":"other.pdf","report_year":2024,"page_range":[40,41]}]}'
    )

    fact = ai_mod.extract_chunk_facts(
        engine,
        {"name": "2022.pdf", "report_year": 2022},
        reader.PDFTextChunk(2, 3, 5, "文本"),
    )

    assert fact["evidence"] == [
        {
            "metric": "营业收入",
            "pages": [2],
            "text": "收入 100",
            "source_file": "2022.pdf",
            "report_year": 2022,
            "page_range": [2, 3],
        }
    ]


def test_chunk_evidence_rejects_pages_outside_trusted_chunk():
    """证据页码超出当前 chunk 时必须拒绝，不能借用其他页段。"""
    engine = _JSONEngine(
        '{"source_file":"2022.pdf","report_year":2022,"page_range":[2,3],'
        '"metrics":{},"audit_opinion":null,"major_events":null,'
        '"evidence":[{"pages":[1,3]}]}'
    )

    with pytest.raises(AIEngineError, match="evidence.*页码"):
        ai_mod.extract_chunk_facts(
            engine,
            {"name": "2022.pdf", "report_year": 2022},
            reader.PDFTextChunk(2, 3, 5, "文本"),
        )


def test_document_merge_requires_continuous_complete_page_coverage():
    """页段缺页或重叠时，单文件结果不能进入跨年合并。"""
    source = {"name": "2022.pdf", "report_year": 2022, "page_count": 6}
    facts = [
        {
            "source_file": "2022.pdf",
            "report_year": 2022,
            "page_range": [1, 2],
            "metrics": {},
            "audit_opinion": None,
            "major_events": None,
            "evidence": None,
            "_total_pages": 6,
            "_finish_reason": "stop",
        },
        {
            "source_file": "2022.pdf",
            "report_year": 2022,
            "page_range": [2, 6],
            "metrics": {},
            "audit_opinion": None,
            "major_events": None,
            "evidence": None,
            "_total_pages": 6,
            "_finish_reason": "stop",
        },
    ]

    with pytest.raises(AIEngineError, match="页覆盖不连续"):
        ai_mod.merge_document_facts(source, facts)


def test_document_merge_preserves_evidence_and_fact_conflicts():
    """完整文件合并保留逐页证据，分段金额冲突留待确定性对账。"""
    source = {"name": "2022.pdf", "report_year": 2022, "page_count": 4}
    shared = {
        "source_file": "2022.pdf",
        "report_year": 2022,
        "audit_opinion": None,
        "major_events": None,
        "_total_pages": 4,
        "_finish_reason": "stop",
    }
    document = ai_mod.merge_document_facts(
        source,
        [
            {**shared, "page_range": [1, 2], "metrics": {"营业收入": 100.0}, "evidence": [{"pages": [1]}]},
            {**shared, "page_range": [3, 4], "metrics": {"营业收入": 120.0}, "evidence": [{"pages": [4]}]},
        ],
    )

    assert document["page_coverage"] == [4, 4]
    assert document["evidence"] == [{"pages": [1]}, {"pages": [4]}]
    assert document["conflicts"][0]["metric"] == "营业收入"
    assert document["conflicts"][0]["values"] == [100.0, 120.0]


def test_document_merge_rejects_non_stop_chunk():
    """任一页段没有正常结束时，即使页覆盖完整也不得形成文档事实。"""
    fact = {
        "source_file": "2022.pdf",
        "report_year": 2022,
        "page_range": [1, 4],
        "metrics": {},
        "audit_opinion": None,
        "major_events": None,
        "evidence": None,
        "_total_pages": 4,
        "_finish_reason": "length",
    }

    with pytest.raises(AIEngineError, match="未正常完成"):
        ai_mod.merge_document_facts(
            {"name": "2022.pdf", "report_year": 2022, "page_count": 4},
            [fact],
        )


def test_reconciliation_prefers_deterministic_amount():
    """同年度同指标重叠时，结构化计算值覆盖 AI 值并留下冲突记录。"""
    deterministic = "| 指标 | 2022 |\n| --- | --- |\n| 营业收入 | 223,000,000.00 |"

    result = ai_mod.reconcile_with_deterministic(
        [{"source_file": "2022.pdf", "report_year": 2022, "metrics": {"营业收入": 999.0}}],
        deterministic,
    )

    assert result["years"]["2022"]["营业收入"] == 223_000_000.0
    assert result["conflicts"][0]["metric"] == "营业收入"
    assert result["conflicts"][0]["ai_value"] == 999.0
    assert result["conflicts"][0]["deterministic_value"] == 223_000_000.0


def test_reconciliation_keeps_ai_only_narrative_facts():
    """确定性表没有的审计意见和重大事项必须继续随年度结果传递。"""
    result = ai_mod.reconcile_with_deterministic(
        [
            {
                "source_file": "2022.pdf",
                "report_year": 2022,
                "metrics": {"营业收入": 999.0},
                "audit_opinion": "标准无保留意见",
                "major_events": ["新增生产线"],
            }
        ],
        "| 指标 | 2022 |\n| --- | --- |\n| 营业收入 | 1,000.00 |",
    )

    assert result["audit_opinions"]["2022"] == [{"source_file": "2022.pdf", "value": "标准无保留意见"}]
    assert result["major_events"]["2022"] == [{"source_file": "2022.pdf", "value": "新增生产线"}]


def test_ai_to_ai_conflict_records_both_sources_and_evidence():
    """同年度两份 AI 来源冲突时，不能丢失先到值的文件和证据。"""
    first_evidence = [{"metric": "营业收入", "pages": [2], "text": "收入 100"}]
    second_evidence = [{"metric": "营业收入", "pages": [3], "text": "收入 120"}]

    result = ai_mod.reconcile_with_deterministic(
        [
            {
                "source_file": "2022-A.pdf",
                "report_year": 2022,
                "metrics": {"营业收入": 100.0},
                "evidence": first_evidence,
            },
            {
                "source_file": "2022-B.pdf",
                "report_year": 2022,
                "metrics": {"营业收入": 120.0},
                "evidence": second_evidence,
            },
        ],
        "",
    )

    sides = result["conflicts"][0]["sides"]
    assert [side["source_file"] for side in sides] == ["2022-A.pdf", "2022-B.pdf"]
    assert [side["evidence"] for side in sides] == [first_evidence, second_evidence]


def test_reconciliation_does_not_treat_missing_ai_value_as_conflict():
    """先到的缺失值应由后续有限数值补全，而不是生成虚假冲突。"""
    result = ai_mod.reconcile_with_deterministic(
        [
            {"source_file": "2022-A.pdf", "report_year": 2022, "metrics": {"营业收入": None}},
            {"source_file": "2022-B.pdf", "report_year": 2022, "metrics": {"营业收入": 100.0}},
        ],
        "",
    )

    assert result["years"]["2022"]["营业收入"] == 100.0
    assert result["conflicts"] == []


def test_ai_to_deterministic_conflict_records_both_sources_and_evidence():
    """确定性覆盖 AI 值时，冲突双方仍需保留各自来源证据。"""
    ai_evidence = [{"metric": "营业收入", "pages": [2], "text": "收入 999"}]

    result = ai_mod.reconcile_with_deterministic(
        [
            {
                "source_file": "2022.pdf",
                "report_year": 2022,
                "metrics": {"营业收入": 999.0},
                "evidence": ai_evidence,
            }
        ],
        "| 指标 | 2022 |\n| --- | --- |\n| 营业收入 | 1,000.00 |",
    )

    sides = result["conflicts"][0]["sides"]
    assert sides[0] == {
        "kind": "ai",
        "source_file": "2022.pdf",
        "value": 999.0,
        "evidence": ai_evidence,
    }
    assert sides[1]["kind"] == "deterministic"
    assert sides[1]["source_file"] == "merge_years_deterministic"
    assert sides[1]["evidence"][0]["metric"] == "营业收入"


def test_generate_final_report_requests_fixed_sections_and_full_output():
    """最终生成必须复用完整输出上限，并把身份、页覆盖和固定章节写入提示。"""
    engine = _JSONEngine("# 云南艺康 2022—2024 跨年合并报告")
    payload = {
        "company_name": "云南艺康",
        "years": [2022, 2023, 2024],
        "page_coverage": {"2022.pdf": [30, 30]},
        "years_data": {"2022": {"营业收入": 1.0}},
    }

    markdown = ai_mod.generate_final_report(engine, payload)

    assert markdown == "# 云南艺康 2022—2024 跨年合并报告"
    call = engine.calls[0]
    assert call["max_tokens"] == reader.FINAL_MAX_TOKENS
    assert call["extra"] == {"stream": False}
    assert "# 云南艺康 2022—2024 跨年合并报告" in call["user_prompt"]
    assert "## 数据范围与完整性" in call["user_prompt"]
    assert "2022.pdf" in call["user_prompt"] and "30/30" in call["user_prompt"]
    assert "增值税税负率为估算值（基于税金及附加反推）" in call["user_prompt"]


def test_validator_rejects_wrong_identity_and_missing_year():
    """校验器应一次返回企业身份和所有缺失年度，不在首错处中止。"""
    errors = ai_mod.validate_final_report(
        "# 示例制造有限公司\n## 2022 年",
        {
            "company_name": "云南艺康",
            "years": [2022, 2023, 2024],
            "page_coverage": {
                "2022年审计报告.pdf": [30, 30],
                "2023年审计报告.pdf": [43, 43],
                "2024年审计报告.pdf": [49, 49],
            },
        },
    )

    assert "企业名称不一致" in errors
    assert "缺少年度：2023、2024" in errors
    assert "缺少增值税税负率估算口径" in errors
    assert "缺少页覆盖：2022年审计报告.pdf 30/30" in errors
    assert "缺少页覆盖：2023年审计报告.pdf 43/43" in errors
    assert "缺少页覆盖：2024年审计报告.pdf 49/49" in errors


def test_validator_accepts_complete_report_and_rejects_open_table():
    """固定章节、逐文件页覆盖、完整表格和估算口径齐全时才可通过。"""
    expected = {
        "company_name": "云南艺康",
        "years": [2022, 2023, 2024],
        "page_coverage": {"2022.pdf": [30, 30], "2023.pdf": [43, 43], "2024.pdf": [49, 49]},
    }
    markdown = """# 云南艺康 2022—2024 跨年合并报告
## 数据范围与完整性
| 文件 | 页覆盖 |
| --- | --- |
| 2022.pdf | 30/30 页 |
| 2023.pdf | 43/43 页 |
| 2024.pdf | 49/49 页 |
## 跨年关键指标
| 指标 | 2022 | 2023 | 2024 |
| --- | --- | --- | --- |
| 营业收入 | 1 | 2 | 3 |
## 趋势与异常
无
## 审计意见与重大事项
无
## 数据冲突与待核验项
无
## 计算口径与合规声明
增值税税负率为估算值（基于税金及附加反推）。
"""

    assert ai_mod.validate_final_report(markdown, expected) == []
    broken = markdown.replace("| 营业收入 | 1 | 2 | 3 |", "| 营业收入 | 1 | 2 | 3")
    assert "Markdown 表格未闭合" in ai_mod.validate_final_report(broken, expected)


def test_validator_rejects_wrong_title_year_span_even_when_body_has_all_years():
    """正文偶然出现全部年度，不能掩盖主标题年度跨度错误。"""
    expected = {
        "company_name": "云南艺康",
        "years": [2022, 2023, 2024],
        "page_coverage": {"2022.pdf": [1, 1]},
    }
    markdown = """# 云南艺康 2021—2024 跨年合并报告
## 数据范围与完整性
| 文件 | 页覆盖 |
| --- | --- |
| 2022.pdf | 1/1 页 |
## 跨年关键指标
| 指标 | 2022 | 2023 | 2024 |
| --- | --- | --- | --- |
| 营业收入 | 1 | 2 | 3 |
## 趋势与异常
无
## 审计意见与重大事项
无
## 数据冲突与待核验项
无
## 计算口径与合规声明
增值税税负率为估算值（基于税金及附加反推）。
"""

    assert "报告标题不符合约定" in ai_mod.validate_final_report(markdown, expected)


def _report_with_key_metrics_table(table: str) -> str:
    return f"""# 云南艺康 2022—2024 跨年合并报告
## 数据范围与完整性
| 文件 | 页覆盖 |
| --- | --- |
| 2022.pdf | 1/1 页 |
## 跨年关键指标
{table}
## 趋势与异常
无
## 审计意见与重大事项
无
## 数据冲突与待核验项
无
## 计算口径与合规声明
增值税税负率为估算值（基于税金及附加反推）。
"""


def test_validator_does_not_count_title_years_as_body_coverage():
    """正确主标题中的起止年不能替代正文或关键指标表的年度覆盖。"""
    markdown = _report_with_key_metrics_table(
        "| 指标 | 2023 |\n| --- | --- |\n| 营业收入 | 2 |"
    )
    errors = ai_mod.validate_final_report(
        markdown,
        {
            "company_name": "云南艺康",
            "years": [2022, 2023, 2024],
            "page_coverage": {"2022.pdf": [1, 1]},
        },
    )

    assert "缺少年度：2022、2024" in errors
    assert "关键指标表缺少年度：2022、2024" in errors


def test_validator_returns_all_missing_required_metric_cells():
    """必需指标矩阵的整行缺失和单年度空值必须同时返回。"""
    markdown = _report_with_key_metrics_table(
        "| 指标 | 2022 | 2023 | 2024 |\n"
        "| --- | --- | --- | --- |\n"
        "| 营业收入 | 1 |  | 3 |"
    )
    errors = ai_mod.validate_final_report(
        markdown,
        {
            "company_name": "云南艺康",
            "years": [2022, 2023, 2024],
            "page_coverage": {"2022.pdf": [1, 1]},
            "required_metrics": {
                "营业收入": [2022, 2023, 2024],
                "净利润": [2022, 2023, 2024],
            },
        },
    )

    assert "关键指标缺少年度值：营业收入 2023" in errors
    assert "缺少关键指标：净利润" in errors
    assert "关键指标缺少年度值：净利润 2022" in errors
    assert "关键指标缺少年度值：净利润 2023" in errors
    assert "关键指标缺少年度值：净利润 2024" in errors


def test_validator_derives_required_metrics_from_reconciled_years_payload():
    """对账结果直接作为 expected 时，years 内的指标仍必须进入校验矩阵。"""
    markdown = _report_with_key_metrics_table(
        "| 指标 | 2022 | 2023 | 2024 |\n"
        "| --- | --- | --- | --- |\n"
        "| 净利润 | 1 | 2 | 3 |"
    )

    errors = ai_mod.validate_final_report(
        markdown,
        {
            "company_name": "云南艺康",
            "years": {
                "2022": {"营业收入": 100.0},
                "2023": {"营业收入": 110.0},
                "2024": {"营业收入": 120.0},
            },
            "page_coverage": {"2022.pdf": [1, 1]},
        },
    )

    assert "缺少关键指标：营业收入" in errors
    assert "关键指标缺少年度值：营业收入 2022" in errors
    assert "关键指标缺少年度值：营业收入 2023" in errors
    assert "关键指标缺少年度值：营业收入 2024" in errors


@pytest.mark.parametrize(
    "table",
    [
        "| 指标 | 2022 | 2023 | 2024 |\n| --- | --- | --- | --- |\n营业收入 | 1 | 2 | 3 |",
        "| 指标 | 2022 | 2023 | 2024 |\n| --- | --- | --- | --- |\n|",
        "| 指标 | 2022 | 2023 | 2024 |\n| --- | --- | --- | --- |",
    ],
    ids=["missing-left-boundary", "isolated-pipe", "empty-table"],
)
def test_validator_rejects_malformed_key_metrics_table(table):
    """关键指标表缺边界、孤立管道或没有数据行时均不可通过。"""
    errors = ai_mod.validate_final_report(
        _report_with_key_metrics_table(table),
        {
            "company_name": "云南艺康",
            "years": [2022, 2023, 2024],
            "page_coverage": {"2022.pdf": [1, 1]},
        },
    )

    assert "跨年关键指标表结构无效" in errors


# ── 任务 5：后台报告任务生命周期与持久化 ─────────────────────────────


def _wait_until_terminal(client, job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/ai/years-summary/jobs/{job_id}").json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish")


def _wait_job_terminal(job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = _job_module().get_job(job_id)
        if snapshot is not None and snapshot["status"] in {"completed", "failed"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish")


def test_concurrent_imports_publish_one_consistent_session_without_orphan(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    parse_barrier = threading.Barrier(2)

    def blocked_parse(_path, company_name, _industry):
        parse_barrier.wait(timeout=2)
        data = parser.parse_financial_dict(build_sample_data())
        data.company_name = company_name
        return data

    monkeypatch.setattr(import_mod, "_parse_one", blocked_parse)
    monkeypatch.setattr(
        import_mod,
        "_safe_preview",
        lambda path: {"name": Path(path).name, "notes": []},
    )
    start_barrier = threading.Barrier(3)
    responses = {}

    def send_import(company):
        start_barrier.wait(timeout=2)
        responses[company] = client.post(
            "/api/import",
            files={"files": (f"{company}.pdf", company.encode(), "application/pdf")},
            data={"company_name": company, "industry": "制造业"},
        )

    workers = [
        threading.Thread(target=send_import, args=(company,))
        for company in ("并发甲", "并发乙")
    ]
    for worker in workers:
        worker.start()
    start_barrier.wait(timeout=2)
    for worker in workers:
        worker.join(timeout=3)

    assert all(not worker.is_alive() for worker in workers)
    assert sorted(response.status_code for response in responses.values()) == [200, 409]
    winner, response = next(
        (company, response)
        for company, response in responses.items()
        if response.status_code == 200
    )
    assert response.json()["summary"]["company_name"] == winner
    assert session_mod.get_data().company_name == winner
    source = Path(session_mod.get_source_files()[0]["path"])
    assert source.read_bytes() == winner.encode()
    assert list(workspace.glob("import-*")) == [source.parents[1]]


def test_concurrent_import_conflict_cleans_loser_when_retirement_database_fails(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    parse_barrier = threading.Barrier(2)

    def blocked_parse(_path, company_name, _industry):
        parse_barrier.wait(timeout=2)
        data = parser.parse_financial_dict(build_sample_data())
        data.company_name = company_name
        return data

    monkeypatch.setattr(import_mod, "_parse_one", blocked_parse)
    monkeypatch.setattr(
        import_mod,
        "_safe_preview",
        lambda path: {"name": Path(path).name, "notes": []},
    )
    monkeypatch.setattr(
        db_mod,
        "queue_workspace_retirement",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("database unavailable")),
    )
    start = threading.Barrier(3)
    responses = {}

    def send(company):
        start.wait(timeout=2)
        responses[company] = client.post(
            "/api/import",
            files={"files": (f"{company}.pdf", company.encode(), "application/pdf")},
            data={"company_name": company},
        )

    workers = [threading.Thread(target=send, args=(name,)) for name in ("甲", "乙")]
    for worker in workers:
        worker.start()
    start.wait(timeout=2)
    for worker in workers:
        worker.join(timeout=3)

    assert all(not worker.is_alive() for worker in workers)
    assert sorted(response.status_code for response in responses.values()) == [200, 409]
    source = Path(session_mod.get_source_files()[0]["path"])
    assert source.exists()
    assert list(workspace.glob("import-*")) == [source.parents[1]]


@pytest.mark.parametrize("replacement", ["import", "sample", "clear"])
def test_active_job_defers_retirement_until_worker_terminal(
    client, tmp_path, monkeypatch, replacement
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))

    def parsed(_path, company_name, _industry):
        data = parser.parse_financial_dict(build_sample_data())
        data.company_name = company_name
        return data

    monkeypatch.setattr(import_mod, "_parse_one", parsed)
    monkeypatch.setattr(
        import_mod,
        "_safe_preview",
        lambda path: {"name": Path(path).name, "notes": []},
    )
    imported = client.post(
        "/api/import",
        files={"files": ("old.pdf", b"old", "application/pdf")},
        data={"company_name": f"旧会话-{replacement}", "industry": "制造业"},
    )
    assert imported.status_code == 200
    old_path = Path(session_mod.get_source_files()[0]["path"])
    old_batch = old_path.parents[1]
    entered = threading.Event()
    release = threading.Event()
    consumer_saw_source_exists = []

    def blocked_pipeline(job_id, snapshot, update):
        entered.set()
        assert release.wait(timeout=2)
        consumer_saw_source_exists.append(old_path.exists())
        return _rules_job_result("# 云南艺康 2022—2024 跨年合并报告")

    monkeypatch.setattr(_job_module(), "_run_pipeline", blocked_pipeline)
    job_id = _job_module().start_job(session_mod.get_version())
    assert entered.wait(timeout=2)

    if replacement == "import":
        replaced = client.post(
            "/api/import",
            files={"files": ("new.pdf", b"new", "application/pdf")},
            data={"company_name": "新会话", "industry": "制造业"},
        )
    elif replacement == "sample":
        replaced = client.post("/api/import/sample")
    else:
        replaced = client.post("/api/session/clear")

    try:
        assert replaced.status_code == 200
        assert old_batch.exists()
    finally:
        release.set()
    assert _wait_job_terminal(job_id)["status"] == "failed"
    deadline = time.monotonic() + 2
    while old_batch.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert consumer_saw_source_exists == [True]
    assert not old_batch.exists()


def test_queued_job_defers_all_retirement_until_failed(
    client, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))

    def parsed(_path, company_name, _industry):
        data = parser.parse_financial_dict(build_sample_data())
        data.company_name = company_name
        return data

    monkeypatch.setattr(import_mod, "_parse_one", parsed)
    monkeypatch.setattr(
        import_mod,
        "_safe_preview",
        lambda path: {"name": Path(path).name, "notes": []},
    )
    imported = client.post(
        "/api/import",
        files={"files": ("old.pdf", b"old", "application/pdf")},
        data={"company_name": "排队旧会话", "industry": "制造业"},
    )
    assert imported.status_code == 200
    old_batch = Path(session_mod.get_source_files()[0]["path"]).parents[1]
    queued_id = f"job-queued-{time.time_ns()}"
    db_mod.create_job(queued_id, session_mod.get_version())

    replaced = client.post(
        "/api/import",
        files={"files": ("new.pdf", b"new", "application/pdf")},
        data={"company_name": "排队新会话", "industry": "制造业"},
    )

    assert replaced.status_code == 200
    assert old_batch.exists()
    assert _job_module().fail_job(queued_id, "cancelled") is True
    assert not old_batch.exists()


def _prepare_job_session(company_name="云南艺康"):
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = company_name
    nonce = str(time.time_ns())
    session_mod.replace(
        data,
        [],
        [
            {
                "path": f"/test-fixture/{nonce}.pdf",
                "name": f"{nonce}.pdf",
                "sha256": hashlib.sha256(nonce.encode()).hexdigest(),
                "size": 1,
                "report_year": max(data.years),
                "page_count": 1,
            }
        ],
    )
    return session_mod.get_version()


def test_job_start_rejects_stale_requested_version_under_lifecycle_guard(
    client, monkeypatch
):
    old_data = parser.parse_financial_dict(build_sample_data())
    old_data.company_name = "旧会话"
    session_mod.replace(old_data, [], [])
    stale_version = session_mod.get_version()
    new_data = parser.parse_financial_dict(build_sample_data())
    new_data.company_name = "新会话"
    session_mod.replace(new_data, [], [])
    with pytest.raises(db_mod.JobCaptureError) as caught:
        _job_module().start_job(stale_version)
    assert caught.value.code == "SESSION_CHANGED"
    assert db_mod.get_active_job(session_mod.get_version()) is None


def test_report_job_starts_and_reports_persistent_status(client, monkeypatch):
    """启动任务应返回 job_id 与 queued/running，且能跨请求读取持久化状态。"""
    _prepare_job_session()
    def success_pipeline(job_id, snapshot, update):
        update(stage="ocr", current=122, total=122, message="122 页读取完成")
        return _rules_job_result("# 云南艺康 2022—2024 跨年合并报告")

    monkeypatch.setattr(_job_module(), "_run_pipeline", success_pipeline)
    started = client.post("/api/ai/years-summary/jobs").json()
    assert started["job_id"]
    assert started["status"] in {"queued", "running"}
    final = _wait_until_terminal(client, started["job_id"])
    assert final["status"] == "completed"
    assert final["progress"] == {"current": 122, "total": 122}


def test_job_start_returns_job_id_with_terminal_progress(client, monkeypatch):
    """直接启动应返回 job_id，持久化快照最终 completed 并保留进度。"""
    _prepare_job_session()
    def success_pipeline(job_id, snapshot, update):
        update(stage="ocr", current=122, total=122, message="122 页读取完成")
        return _rules_job_result("# 云南艺康 2022—2024 跨年合并报告")

    monkeypatch.setattr(_job_module(), "_run_pipeline", success_pipeline)
    job_id = _job_module().start_job(session_mod.get_version())
    assert job_id
    final = _wait_job_terminal(job_id)
    assert final["status"] == "completed"
    assert final["progress"] == {"current": 122, "total": 122}


def test_report_job_get_active_returns_in_progress_job(client, monkeypatch):
    """active 查询应返回当前 session_version 正在进行的任务。"""
    _prepare_job_session()
    entered = threading.Event()
    release = threading.Event()

    def blocked_pipeline(job_id, snapshot, update):
        entered.set()
        assert release.wait(timeout=1)
        return _rules_job_result("# 云南艺康 2022—2024 跨年合并报告")

    monkeypatch.setattr(_job_module(), "_run_pipeline", blocked_pipeline)
    started = client.post("/api/ai/years-summary/jobs").json()
    assert entered.wait(timeout=1)

    active = client.get("/api/ai/years-summary/jobs/active").json()
    assert active["job"] is not None
    assert active["job"]["job_id"] == started["job_id"]
    assert active["job"]["session_version"] == session_mod.get_version()
    assert active["job"]["status"] in {"queued", "running"}
    release.set()
    final = _wait_until_terminal(client, started["job_id"])
    assert final["status"] == "completed"


def test_job_get_active_returns_current_session_in_progress_job(client, monkeypatch):
    """直接 get_active_job 应返回当前 session_version 的进行中任务。"""
    _prepare_job_session()
    entered = threading.Event()
    release = threading.Event()

    def blocked_pipeline(job_id, snapshot, update):
        entered.set()
        assert release.wait(timeout=1)
        return _rules_job_result("# 云南艺康 2022—2024 跨年合并报告")

    monkeypatch.setattr(_job_module(), "_run_pipeline", blocked_pipeline)
    version = session_mod.get_version()
    job_id = _job_module().start_job(version)
    assert entered.wait(timeout=1)

    active = _job_module().get_active_job(version)
    assert active is not None
    assert active["job_id"] == job_id
    assert active["session_version"] == version
    assert active["status"] in {"queued", "running"}
    release.set()
    assert _wait_job_terminal(job_id)["status"] == "completed"


def test_report_job_duplicate_start_returns_same_active_job(client, monkeypatch):
    """同一 session_version 重复启动应返回同一个进行中任务，而不是重复执行。"""
    _prepare_job_session()
    entered = threading.Event()
    release = threading.Event()

    def blocked_pipeline(job_id, snapshot, update):
        entered.set()
        assert release.wait(timeout=1)
        return _rules_job_result("# 云南艺康 2022—2024 跨年合并报告")

    monkeypatch.setattr(_job_module(), "_run_pipeline", blocked_pipeline)
    first = client.post("/api/ai/years-summary/jobs").json()
    assert entered.wait(timeout=1)

    second = client.post("/api/ai/years-summary/jobs").json()
    assert second["job_id"] == first["job_id"]
    assert second["status"] in {"queued", "running"}
    release.set()
    assert _wait_until_terminal(client, first["job_id"])["status"] == "completed"


def test_job_duplicate_start_returns_same_active_job(client, monkeypatch):
    """同一 session_version 重复直接启动应返回同一个进行中任务。"""
    _prepare_job_session()
    entered = threading.Event()
    release = threading.Event()

    def blocked_pipeline(job_id, snapshot, update):
        entered.set()
        assert release.wait(timeout=1)
        return _rules_job_result("# 云南艺康 2022—2024 跨年合并报告")

    monkeypatch.setattr(_job_module(), "_run_pipeline", blocked_pipeline)
    version = session_mod.get_version()
    first = _job_module().start_job(version)
    assert entered.wait(timeout=1)

    second = _job_module().start_job(version)
    assert second == first
    assert _job_module().get_active_job(version)["job_id"] == first
    release.set()
    assert _wait_job_terminal(first)["status"] == "completed"


def test_report_job_session_change_prevents_save(client, monkeypatch):
    """任务运行期间会话版本变化，最终保存前必须失败且不写正式报告。"""
    _prepare_job_session()
    entered = threading.Event()
    release = threading.Event()

    def blocked_pipeline(job_id, snapshot, update):
        entered.set()
        assert release.wait(timeout=1)
        return _rules_job_result("# 云南艺康 2022—2024 跨年合并报告")

    monkeypatch.setattr(_job_module(), "_run_pipeline", blocked_pipeline)
    job_id = client.post("/api/ai/years-summary/jobs").json()["job_id"]
    assert entered.wait(timeout=1)

    other = parser.parse_financial_dict(build_sample_data())
    other.company_name = "其他企业"
    session_mod.replace(other, [], [])
    release.set()

    final = _wait_until_terminal(client, job_id)
    assert final["status"] == "failed"
    assert "会话已变化" in final["error"]
    assert db_mod.list_reports_for_job(job_id) == []


def test_report_job_pipeline_validation_failure_does_not_save(client, monkeypatch):
    """pipeline 的结构化最终校验错误必须 failed 且 ai_reports 不新增。"""
    before = len(db_mod.list_reports())
    db_mod.init_db()

    # 建立会话数据，使保存前校验真正被调用；校验函数显式返回错误。
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "云南艺康"
    session_mod.replace(data, [], [])
    def invalid_pipeline(job_id, snapshot, update):
        pipeline = importlib.import_module(
            "web_backend.CO_ai_report_pipeline_WB-CO-TR-20260807113737"
        )
        raise pipeline.PipelineError(
            "FINAL_REPORT_INVALID", "最终报告校验失败，未保存", stage="validate"
        )

    monkeypatch.setattr(_job_module(), "_run_pipeline", invalid_pipeline)
    started = client.post("/api/ai/years-summary/jobs").json()
    final = _wait_until_terminal(client, started["job_id"])
    assert final["status"] == "failed"
    assert "校验" in final["error"]
    assert final["error_code"] == "FINAL_REPORT_INVALID"
    assert len(db_mod.list_reports()) == before


def test_report_job_rejects_invalid_state_transition(client, monkeypatch):
    """queued→running→completed|failed 之外的转换必须被拒绝。"""
    _prepare_job_session()

    def blocking_pipeline(job_id, snapshot, update):
        time.sleep(0.2)
        return _rules_job_result("# 云南艺康 2021—2023 跨年合并报告")

    monkeypatch.setattr(_job_module(), "_run_pipeline", blocking_pipeline)
    job_id = client.post("/api/ai/years-summary/jobs").json()["job_id"]
    snapshot = client.get(f"/api/ai/years-summary/jobs/{job_id}").json()

    assert _job_module().fail_job(job_id, "error") is True
    assert snapshot["status"] in {"queued", "running"}
    assert _job_module().fail_job(job_id, "再次失败") is False
    assert _job_module().complete_job(job_id, "# 云南艺康 2022—2024 跨年合并报告") is False
    assert _job_module().get_job(job_id)["status"] == "failed"


def test_job_rejects_invalid_state_transition(client, monkeypatch):
    """queued→running→completed|failed 之外的转换必须被拒绝。"""
    _prepare_job_session()
    def blocking_pipeline(job_id, snapshot, update):
        time.sleep(0.2)
        return _rules_job_result("# 云南艺康 2022—2024 跨年合并报告")

    monkeypatch.setattr(_job_module(), "_run_pipeline", blocking_pipeline)
    job_id = _job_module().start_job(session_mod.get_version())
    snapshot = _job_module().get_job(job_id)
    assert snapshot["status"] in {"queued", "running"}

    assert _job_module().fail_job(job_id, "error") is True
    assert _job_module().fail_job(job_id, "再次失败") is False
    assert _job_module().complete_job(job_id, "# 云南艺康 2022—2024 跨年合并报告") is False
    assert _job_module().get_job(job_id)["status"] == "failed"


REAL_PDF_WORKSPACES = Path(__file__).resolve().parents[1] / "web_backend" / "workspaces"
REAL_PDF_SPECS = (
    ("2022年审计报告.pdf", 2022, 30),
    ("2023年审计报告.pdf", 2023, 43),
    ("2024年审计报告.pdf", 2024, 49),
)


@pytest.fixture(scope="session")
def real_pdf_records():
    """每个正式样本只完整读取一次，记录真实耗时且不写项目缓存。"""
    extracted = {}
    for name, _year, _expected_pages in REAL_PDF_SPECS:
        started = time.perf_counter()
        pages = tuple(reader.extract_all_pages(REAL_PDF_WORKSPACES / name))
        extracted[name] = {
            "pages": pages,
            "seconds": time.perf_counter() - started,
        }
        print(
            f"REAL_PDF {name}: pages={len(pages)} "
            f"ocr={sum(page.method == 'ocr' for page in pages)} "
            f"failed={sum(page.status == 'failed' for page in pages)} "
            f"seconds={extracted[name]['seconds']:.2f}",
            flush=True,
        )
    return extracted


@pytest.mark.real_pdf
@pytest.mark.parametrize(
    ("name", "expected_pages"),
    [(name, expected_pages) for name, _year, expected_pages in REAL_PDF_SPECS],
)
def test_real_pdf_full_page_coverage(real_pdf_records, name, expected_pages):
    pages = real_pdf_records[name]["pages"]

    assert len(pages) == expected_pages
    assert [page.page_no for page in pages] == list(range(1, expected_pages + 1))
    assert {page.total_pages for page in pages} == {expected_pages}
    assert [page.page_no for page in pages if page.status == "failed"] == []


def _shift_sample_to_real_years():
    raw = copy.deepcopy(build_sample_data())
    raw["company_name"] = "云南艺康"
    raw["years"] = [2022, 2023, 2024]
    for section in ("income_statement", "balance_sheet", "account_balances"):
        for metric, values in raw[section].items():
            if isinstance(values, dict):
                raw[section][metric] = {
                    int(year) + 1: value for year, value in values.items()
                }
    return parser.parse_financial_dict(raw)


TASK7_FIXED_MARKDOWN = """# 云南艺康 2022—2024 跨年合并报告
## 数据范围与完整性
- 2022年审计报告.pdf: 30/30 页
- 2023年审计报告.pdf: 43/43 页
- 2024年审计报告.pdf: 49/49 页
## 跨年关键指标
| 指标 | 2022 | 2023 | 2024 |
| --- | --- | --- | --- |
| 营业收入 | 12000000.0 | 13500000.0 | 15200000.0 |
| 毛利率 | 20.0 | 22.0 | 21.51 |
| 净利率 | 1.5 | 2.22 | 2.43 |
| 增值税税负率 | 1.94 | 1.91 | 1.92 |
| 所得税税负率 | 0.5 | 0.74 | 0.81 |
| 综合税负率 | 0.73 | 0.97 | 1.04 |
## 趋势与异常
2022、2023、2024
## 审计意见与重大事项
未识别
## 数据冲突与待核验项
无
## 计算口径与合规声明
增值税税负率为估算值（基于税金及附加反推）。"""

TASK7_EXPECTED_COVERAGE = {
    "2022年审计报告.pdf": (30, 30),
    "2023年审计报告.pdf": (43, 43),
    "2024年审计报告.pdf": (49, 49),
}


class _Task7ReplayCache:
    def __init__(self, records_by_hash):
        self.records_by_hash = records_by_hash
        self.hits = 0

    def load_cached_pages(self, file_hash):
        rows = self.records_by_hash.get(file_hash, [])
        if rows:
            self.hits += 1
        return [dict(row) for row in rows]

    def save_cached_pages(self, file_hash, pages):
        raise AssertionError("real-record replay must not rewrite page cache")


def _task7_run_job(
    case_root,
    monkeypatch,
    real_pdf_records,
    *,
    final_markdown=TASK7_FIXED_MARKDOWN,
    missing_total_pages=None,
    inject_commit_failure=False,
):
    pipeline = importlib.import_module(
        "web_backend.CO_ai_report_pipeline_WB-CO-TR-20260807113737"
    )
    real_ai = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    job = _job_module()
    job.release_process_lease(force=True)
    workspace = case_root / "workspaces"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("LIRUNBAO_DB_PATH", str(case_root / "app.db"))
    monkeypatch.setenv("LIRUNBAO_AI_CONFIG_PATH", str(case_root / ".ai_config.json"))
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))

    source_files = []
    cache_rows = {}
    for name, year, expected_pages in REAL_PDF_SPECS:
        source = REAL_PDF_WORKSPACES / name
        replay = workspace / name
        shutil.copy2(source, replay)
        assert replay.stat().st_ino != source.stat().st_ino
        assert replay.stat().st_size == source.stat().st_size
        assert reader.file_sha256(replay) == reader.file_sha256(source)
        file_hash = reader.file_sha256(replay)
        source_files.append(
            {
                "path": str(replay.resolve()),
                "name": name,
                "sha256": file_hash,
                "size": replay.stat().st_size,
                "report_year": year,
                "page_count": expected_pages,
            }
        )
        cache_rows[file_hash] = [
            {
                "page_no": page.page_no,
                "total_pages": page.total_pages,
                "method": page.method,
                "status": page.status,
                "text": page.text,
            }
            for page in real_pdf_records[name]["pages"]
        ]

    data = _shift_sample_to_real_years()
    session_mod.replace(data, [], source_files)
    expected_version = session_mod.get_version()
    cache = _Task7ReplayCache(cache_rows)

    class ReplayReader:
        PDFPageRecord = reader.PDFPageRecord
        PDFTextChunk = reader.PDFTextChunk
        file_sha256 = staticmethod(reader.file_sha256)
        _format_page = staticmethod(reader._format_page)

        @staticmethod
        def extract_all_pages(*_args, **_kwargs):
            raise AssertionError("job must replay the approved real page records")

        @staticmethod
        def chunk_pages(pages):
            chunks = reader.chunk_pages(pages)
            if pages[0].total_pages == missing_total_pages:
                assert len(chunks) > 1
                return chunks[:-1]
            return chunks

    class FakeEngine:
        model = "mock-v4-flash"

        def chat_result(self, prompt, system_prompt="", max_tokens=0, extra=None):
            if extra and extra.get("response_format"):
                content = json.dumps(
                    {
                        "source_file": "ignored",
                        "report_year": 1900,
                        "page_range": [1, 1],
                        "metrics": {},
                        "audit_opinion": None,
                        "major_events": None,
                        "evidence": None,
                    },
                    ensure_ascii=False,
                )
            else:
                content = final_markdown
            return AIChatResult(content, "stop", 1, 1, self.model)

    deps = pipeline.PipelineDependencies(
        ReplayReader, real_ai, cache, FakeEngine, workspace
    )

    def run_real_pipeline(_job_id, snapshot, update):
        assert snapshot.session_version == expected_version
        assert snapshot.company_name == "云南艺康"
        assert snapshot.years == (2022, 2023, 2024)
        return pipeline.run_report_pipeline(
            snapshot, lambda progress: update(progress), deps
        )

    monkeypatch.setattr(job, "_run_pipeline", run_real_pipeline)
    if inject_commit_failure:
        monkeypatch.setattr(
            db_mod,
            "_complete_job_in_transaction",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                sqlite3.OperationalError("injected task7 commit failure")
            ),
        )

    job_id = job.start_job(expected_version)
    final = _wait_job_terminal(job_id, timeout=5.0)
    reports = db_mod.list_reports_for_job(job_id)
    job.release_process_lease(force=True)
    shutil.rmtree(workspace)
    assert not workspace.exists()
    return final, reports, cache


@pytest.mark.real_pdf
def test_real_records_replay_completes_through_worker_and_atomic_commit(
    real_pdf_records, tmp_path, monkeypatch
):
    final, reports, cache = _task7_run_job(
        tmp_path, monkeypatch, real_pdf_records
    )

    assert final["status"] == "completed"
    assert final["progress"] == {"current": 122, "total": 122}
    assert final["report_type"] == "ai_full"
    assert final["model"] == "mock-v4-flash"
    assert len(reports) == 1
    report = reports[0]
    assert report["job_id"] == final["job_id"]
    assert report["title"] == "云南艺康 跨年合并报告"
    assert report["report_type"] == "ai_full"
    assert report["model"] == "mock-v4-flash"
    assert json.loads(report["page_coverage_json"]) == {
        name: [done, total]
        for name, (done, total) in TASK7_EXPECTED_COVERAGE.items()
    }
    detail = db_mod.get_report(report["id"])
    assert detail["content"] == TASK7_FIXED_MARKDOWN
    assert detail["content"].startswith("# 云南艺康 2022—2024 跨年合并报告")
    assert "| 营业收入 | 12000000.0 | 13500000.0 | 15200000.0 |" in detail["content"]
    assert all(
        line.endswith("|")
        for line in detail["content"].splitlines()
        if line.startswith("|")
    )
    assert cache.hits == 3


@pytest.mark.real_pdf
@pytest.mark.parametrize("missing_total_pages", [30, 43, 49])
def test_each_missing_real_file_tail_chunk_fails_worker_without_report(
    real_pdf_records, tmp_path, monkeypatch, missing_total_pages
):
    final, reports, _cache = _task7_run_job(
        tmp_path, monkeypatch, real_pdf_records,
        missing_total_pages=missing_total_pages,
    )

    assert final["status"] == "failed"
    assert final["error_code"] == "AI_FACT_INVALID"
    assert reports == []
    assert db_mod.list_reports() == []


@pytest.mark.real_pdf
def test_real_record_job_commit_fault_rolls_back_report_and_fails_worker(
    real_pdf_records, tmp_path, monkeypatch
):
    final, reports, _cache = _task7_run_job(
        tmp_path, monkeypatch, real_pdf_records, inject_commit_failure=True
    )

    assert final["status"] == "failed"
    assert final["error_code"] == "INTERNAL_PIPELINE_ERROR"
    assert reports == []
    assert db_mod.list_reports() == []


@pytest.mark.real_pdf
def test_literal_metric_mutation_is_rejected_by_real_record_job(
    real_pdf_records, tmp_path, monkeypatch
):
    mutated = TASK7_FIXED_MARKDOWN.replace(
        "| 营业收入 | 12000000.0 | 13500000.0 | 15200000.0 |",
        "| 营业收入 | 12000001.0 | 13500000.0 | 15200000.0 |",
    )
    assert mutated != TASK7_FIXED_MARKDOWN

    final, reports, _cache = _task7_run_job(
        tmp_path, monkeypatch, real_pdf_records, final_markdown=mutated
    )

    assert final["status"] == "failed"
    assert final["error_code"] == "FINAL_REPORT_INVALID"
    assert reports == []
    assert db_mod.list_reports() == []


def _task7_synthetic_records():
    return {
        name: {
            "pages": tuple(
                reader.PDFPageRecord(
                    page_no,
                    expected_pages,
                    "text",
                    f"隔离回放页 {page_no} " * 10,
                    "ok",
                )
                for page_no in range(1, expected_pages + 1)
            ),
            "seconds": 0.0,
        }
        for name, _year, expected_pages in REAL_PDF_SPECS
    }


def test_task7_job_workspace_uses_independent_pdf_copies(tmp_path, monkeypatch):
    synthetic_records = _task7_synthetic_records()

    final, reports, _cache = _task7_run_job(
        tmp_path, monkeypatch, synthetic_records
    )

    assert final["status"] == "completed"
    assert len(reports) == 1


def test_task7_literal_mutation_has_zero_reports_with_safe_replay(
    tmp_path, monkeypatch
):
    mutated = TASK7_FIXED_MARKDOWN.replace(
        "| 营业收入 | 12000000.0 | 13500000.0 | 15200000.0 |",
        "| 营业收入 | 12000001.0 | 13500000.0 | 15200000.0 |",
    )

    final, reports, _cache = _task7_run_job(
        tmp_path,
        monkeypatch,
        _task7_synthetic_records(),
        final_markdown=mutated,
    )

    assert final["status"] == "failed"
    assert final["error_code"] == "FINAL_REPORT_INVALID"
    assert reports == []
    assert db_mod.list_reports() == []
