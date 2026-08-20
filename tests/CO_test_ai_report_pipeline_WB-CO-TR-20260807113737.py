"""Task 5B：完整财报后台流水线契约测试。"""

from __future__ import annotations

import importlib
import dataclasses
import json
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import ai_engine as ai_engine_mod
from core import parser
from data.make_sample import build_sample_data


db_mod = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")
session_mod = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")


def _pipeline_mod():
    return importlib.import_module(
        "web_backend.CO_ai_report_pipeline_WB-CO-TR-20260807113737"
    )


def _job_mod():
    return importlib.import_module(
        "web_backend.CO_ai_report_job_WB-CO-TR-20260807113737"
    )


def _wait_terminal(job_id: str, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = _job_mod().get_job(job_id)
        if job and job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not reach terminal state")


@pytest.fixture(autouse=True)
def _isolated_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LIRUNBAO_DB_PATH", str(tmp_path / "app.db"))
    session_mod._data = None
    session_mod._ocr_texts = []
    session_mod._source_files = []
    session_mod._session_version = ""
    yield
    session_mod._data = None
    session_mod._ocr_texts = []
    session_mod._source_files = []
    session_mod._session_version = ""


def _seed_session(tmp_path: Path, *, company: str = "快照企业") -> tuple[str, str]:
    source_path = tmp_path / "2024-report.pdf"
    source_path.write_bytes(b"%PDF-snapshot")
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = company
    data.years = [2022, 2023, 2024]
    source = {
        "path": str(source_path.resolve()),
        "name": source_path.name,
        "sha256": "a" * 64,
        "size": source_path.stat().st_size,
        "report_year": 2024,
        "page_count": 1,
    }
    session_mod.replace(data, ["private OCR marker"], [source])
    return session_mod.get_version(), str(source_path.resolve())


def _pipeline_result(markdown: str = "# result") -> SimpleNamespace:
    return SimpleNamespace(
        markdown=markdown,
        report_type="ai_full",
        model="test-model",
        attempted_model="test-model",
        fallback=False,
        fallback_reason_code="",
        page_coverage={"2024-report.pdf": (1, 1)},
        blank_pages={"2024-report.pdf": ()},
        conflict_count=2,
    )


class _CompletionResponse:
    def __init__(self, finish_reason: str):
        self.finish_reason = finish_reason

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "model": "test-model",
            "choices": [
                {
                    "finish_reason": self.finish_reason,
                    "message": {"content": "partial"},
                }
            ],
            "usage": {},
        }


@pytest.mark.parametrize("reason", ["length", "content_filter", "unknown", ""])
def test_chat_result_exposes_structured_incomplete_reason(monkeypatch, reason):
    """Replacing the structured exception with a plain error breaks retry routing."""
    assert hasattr(ai_engine_mod, "AICompletionError")
    engine = ai_engine_mod.AIEngine("https://example.invalid", "test-key", "test-model")
    monkeypatch.setattr(
        "requests.post", lambda *args, **kwargs: _CompletionResponse(reason)
    )

    with pytest.raises(ai_engine_mod.AICompletionError) as caught:
        engine.chat_result("input")

    assert caught.value.finish_reason == reason
    assert caught.value.code == "AI_COMPLETION_INCOMPLETE"


def test_chat_result_classifies_transport_failure_without_exposing_detail(monkeypatch):
    """A raw transport exception must not become an unstructured pipeline error."""
    assert hasattr(ai_engine_mod, "AIRequestError")
    engine = ai_engine_mod.AIEngine("https://example.invalid", "test-key", "test-model")

    def fail(*args, **kwargs):
        raise TimeoutError("private upstream detail")

    monkeypatch.setattr("requests.post", fail)
    with pytest.raises(ai_engine_mod.AIRequestError) as caught:
        engine.chat_result("input")

    assert caught.value.code == "AI_TIMEOUT"
    assert caught.value.retryable is True
    assert "private upstream detail" not in str(caught.value)


def test_capture_job_freezes_private_session_snapshot(tmp_path):
    """Reading the live session later must not change the job's captured inputs."""
    version, source_path = _seed_session(tmp_path)

    job_id, created = db_mod.capture_session_and_create_job(
        "job-snapshot", version, "owner-current"
    )
    snapshot = db_mod.load_job_input_snapshot(job_id)
    public = db_mod.get_job(job_id)

    assert created is True
    assert snapshot.session_version == version
    assert snapshot.company_name == "快照企业"
    assert snapshot.years == (2022, 2023, 2024)
    assert snapshot.sources[0].path == source_path
    assert json.loads(snapshot.financial_data_json)["company_name"] == "快照企业"
    serialized_public = json.dumps(public, ensure_ascii=False)
    assert source_path not in serialized_public
    assert "private OCR marker" not in serialized_public
    assert "snapshot_json" not in public
    assert "owner_token" not in public


def test_twenty_concurrent_captures_return_one_active_job(tmp_path):
    """Removing the database unique gate creates duplicate workers under concurrent starts."""
    version, _ = _seed_session(tmp_path)

    def capture(index: int) -> tuple[str, bool]:
        return db_mod.capture_session_and_create_job(
            f"job-{index}", version, "owner-current"
        )

    with ThreadPoolExecutor(max_workers=20) as pool:
        captures = list(pool.map(capture, range(20)))

    assert len({job_id for job_id, _ in captures}) == 1
    assert sum(created for _, created in captures) == 1


def test_twenty_real_concurrent_starts_reuse_job_even_if_worker_finishes_fast(
    tmp_path, monkeypatch
):
    """Admission idempotency must survive a worker completing between callers."""
    version, _ = _seed_session(tmp_path)
    job_mod = _job_mod()
    pipeline = _pipeline_mod()
    result = pipeline.PipelineResult(
        markdown="# fast",
        report_type="ai_full",
        model="test-model",
        attempted_model="test-model",
        fallback=False,
        fallback_reason_code="",
        page_coverage={"2024-report.pdf": (1, 1)},
        blank_pages={"2024-report.pdf": ()},
        conflict_count=0,
    )
    monkeypatch.setattr(job_mod, "_run_pipeline", lambda *args, **kwargs: result)
    entered = threading.Barrier(20)

    def start(_: int) -> str:
        entered.wait(timeout=2)
        return job_mod.start_job(version)

    with ThreadPoolExecutor(max_workers=20) as pool:
        job_ids = list(pool.map(start, range(20)))

    assert len(set(job_ids)) == 1
    assert _wait_terminal(job_ids[0])["status"] == "completed"


def test_atomic_report_commit_persists_metadata_and_completed_job(tmp_path):
    """Splitting report insert and completed update permits a half-committed result."""
    version, _ = _seed_session(tmp_path)
    job_id, _ = db_mod.capture_session_and_create_job(
        "job-complete", version, "owner-current"
    )
    assert db_mod.start_job(job_id)

    outcome = db_mod.commit_report_and_complete_job(
        job_id, version, "快照企业 跨年合并报告", _pipeline_result()
    )

    assert outcome.status == "completed"
    job = db_mod.get_job(job_id)
    assert job["status"] == "completed"
    assert job["report_type"] == "ai_full"
    assert job["report_id"] == outcome.report_id
    report = db_mod.get_report(outcome.report_id)
    assert report["job_id"] == job_id
    assert report["session_version"] == version
    assert report["report_type"] == "ai_full"
    assert report["model"] == "test-model"
    assert report["fallback"] == 0
    assert json.loads(report["blank_pages_json"]) == {"2024-report.pdf": []}
    assert report["conflict_count"] == 2


def test_atomic_report_commit_rolls_back_when_job_update_fails(tmp_path, monkeypatch):
    """A failed terminal update must roll back the report inserted in the same transaction."""
    version, _ = _seed_session(tmp_path)
    job_id, _ = db_mod.capture_session_and_create_job(
        "job-rollback", version, "owner-current"
    )
    assert db_mod.start_job(job_id)

    def fail_complete(*args, **kwargs):
        raise sqlite3.OperationalError("injected update failure")

    monkeypatch.setattr(db_mod, "_complete_job_in_transaction", fail_complete)
    with pytest.raises(sqlite3.OperationalError, match="injected update failure"):
        db_mod.commit_report_and_complete_job(
            job_id, version, "title", _pipeline_result()
        )

    assert db_mod.list_reports_for_job(job_id) == []
    assert db_mod.get_job(job_id)["status"] == "running"


def test_atomic_report_commit_rejects_changed_database_session(tmp_path):
    """Comparing only an in-memory version allows a stale report to commit."""
    version, _ = _seed_session(tmp_path)
    job_id, _ = db_mod.capture_session_and_create_job(
        "job-stale", version, "owner-current"
    )
    assert db_mod.start_job(job_id)
    _seed_session(tmp_path, company="替换企业")

    outcome = db_mod.commit_report_and_complete_job(
        job_id, version, "title", _pipeline_result()
    )

    assert outcome.status == "session_changed"
    assert db_mod.list_reports_for_job(job_id) == []
    assert db_mod.get_job(job_id)["status"] == "running"


def test_orphan_recovery_fails_only_previous_owner_jobs(tmp_path):
    """Leaving old-owner jobs active after restart permanently blocks new work."""
    version, _ = _seed_session(tmp_path)
    old_job, _ = db_mod.capture_session_and_create_job(
        "job-old-owner", version, "owner-old"
    )
    assert db_mod.start_job(old_job)

    # A caller cannot manufacture proof that the previous process is dead merely
    # by presenting a different owner token.
    assert db_mod.recover_orphaned_jobs("owner-current") == 0
    assert db_mod.get_job(old_job)["status"] == "running"

    # The job module calls this only after it holds the exclusive OS process
    # lease, which is the proof required by the database boundary.
    assert (
        db_mod.recover_orphaned_jobs("owner-current", lease_verified=True) == 1
    )

    recovered = db_mod.get_job(old_job)
    assert recovered["status"] == "failed"
    assert recovered["error_code"] == "PROCESS_RESTARTED"
    assert "owner-old" not in json.dumps(recovered, ensure_ascii=False)


class _MemoryPageCache:
    def __init__(self, cached=None):
        self.cached = dict(cached or {})
        self.saved: dict[str, list[dict]] = {}

    def load_cached_pages(self, file_hash):
        return [dict(page) for page in self.cached.get(file_hash, [])]

    def save_cached_pages(self, file_hash, pages):
        self.saved[file_hash] = [dict(page) for page in pages]


def _source_for(path: Path, year: int, pages: int):
    pipeline = _pipeline_mod()
    return pipeline.SourceFileSnapshot(
        path=str(path.resolve()),
        name=path.name,
        sha256=importlib.import_module("hashlib").sha256(path.read_bytes()).hexdigest(),
        size=path.stat().st_size,
        report_year=year,
        page_count=pages,
    )


def test_source_loading_replays_cache_and_fresh_pages_as_one_monotonic_sequence(
    tmp_path,
):
    """Resetting progress per file makes 30/43/49-page work appear to move backwards."""
    pipeline = _pipeline_mod()
    first = tmp_path / "2023.pdf"
    second = tmp_path / "2024.pdf"
    first.write_bytes(b"%PDF-first")
    second.write_bytes(b"%PDF-second")
    source_a = _source_for(first, 2023, 2)
    source_b = _source_for(second, 2024, 3)
    cached_a = [
        {"page_no": n, "total_pages": 2, "method": "text", "status": "ok", "text": f"A{n}"}
        for n in (1, 2)
    ]
    cache = _MemoryPageCache({source_a.sha256: cached_a})

    class FakeReader:
        PDFPageRecord = importlib.import_module(
            "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
        ).PDFPageRecord

        @staticmethod
        def file_sha256(path):
            return importlib.import_module("hashlib").sha256(Path(path).read_bytes()).hexdigest()

        @classmethod
        def extract_all_pages(cls, path, on_progress=None):
            records = [cls.PDFPageRecord(n, 3, "text", f"B{n}", "ok") for n in (1, 2, 3)]
            for n in (1, 2, 3):
                on_progress(n, 3)
            return records

    updates = []
    deps = pipeline.PipelineDependencies(
        reader=FakeReader,
        ai=object(),
        db=cache,
        engine_factory=lambda: None,
        workspace_root=tmp_path,
    )
    snapshot = pipeline.JobInputSnapshot(
        session_version="v",
        company_name="企业",
        industry="制造业",
        years=(2023, 2024),
        financial_data_json="{}",
        sources=(source_b, source_a),
    )

    loaded = pipeline.load_sources_pages(snapshot, updates.append, deps)

    assert [update.current for update in updates] == [1, 2, 3, 4, 5]
    assert all(update.total == 5 for update in updates)
    assert list(loaded.pages) == ["2023.pdf", "2024.pdf"]
    assert loaded.page_coverage == {"2023.pdf": (2, 2), "2024.pdf": (3, 3)}


@pytest.mark.parametrize(
    "reported_progress",
    [((1, 99),), ((1, 2), (1, 2))],
)
def test_reader_progress_requires_exact_local_total_and_monotonic_pages(
    tmp_path, reported_progress
):
    pipeline = _pipeline_mod()
    pdf = tmp_path / "2024.pdf"
    pdf.write_bytes(b"%PDF-progress")
    source = _source_for(pdf, 2024, 2)
    base_reader = importlib.import_module(
        "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
    )

    class BadProgressReader:
        PDFPageRecord = base_reader.PDFPageRecord
        file_sha256 = staticmethod(base_reader.file_sha256)

        @classmethod
        def extract_all_pages(cls, path, on_progress=None):
            for current, total in reported_progress:
                on_progress(current, total)
            return [
                cls.PDFPageRecord(n, 2, "text", f"page-{n}", "ok")
                for n in (1, 2)
            ]

    cache = _MemoryPageCache()
    deps = pipeline.PipelineDependencies(
        reader=BadProgressReader,
        ai=object(),
        db=cache,
        engine_factory=lambda: None,
        workspace_root=tmp_path,
    )

    with pytest.raises(pipeline.PipelineError) as caught:
        pipeline.load_or_extract_pages(source, lambda *args: None, deps)

    assert caught.value.code == "PROGRESS_INVALID"
    assert source.sha256 not in cache.saved


def test_invalid_cached_page_state_is_miss_and_invalid_fresh_state_is_rejected(tmp_path):
    pipeline = _pipeline_mod()
    pdf = tmp_path / "2024.pdf"
    pdf.write_bytes(b"%PDF-state")
    source = _source_for(pdf, 2024, 1)
    cache = _MemoryPageCache(
        {
            source.sha256: [
                {"page_no": 1, "total_pages": 1, "method": "none", "status": "ok", "text": ""}
            ]
        }
    )
    base_reader = importlib.import_module(
        "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
    )

    class InvalidFreshReader:
        PDFPageRecord = base_reader.PDFPageRecord
        file_sha256 = staticmethod(base_reader.file_sha256)

        @classmethod
        def extract_all_pages(cls, path, on_progress=None):
            on_progress(1, 1)
            return [cls.PDFPageRecord(1, 1, "text", "invented", "blank")]

    deps = pipeline.PipelineDependencies(
        reader=InvalidFreshReader,
        ai=object(),
        db=cache,
        engine_factory=lambda: None,
        workspace_root=tmp_path,
    )

    with pytest.raises(pipeline.PipelineError) as caught:
        pipeline.load_or_extract_pages(source, lambda *args: None, deps)

    assert caught.value.code == "PAGE_STATE_INVALID"
    assert source.sha256 not in cache.saved


def test_database_progress_is_bounded_monotonic_and_total_is_immutable(tmp_path):
    version, _ = _seed_session(tmp_path)
    job_id, _ = db_mod.capture_session_and_create_job(
        "job-progress-rules", version, "owner-current"
    )
    assert db_mod.start_job(job_id)
    assert db_mod.update_job(job_id, current=2, total=10)
    for current, total in ((1, 10), (11, 10), (3, 9), (-1, 10)):
        assert not db_mod.update_job(job_id, current=current, total=total)

    assert db_mod.get_job(job_id)["progress"] == {"current": 2, "total": 10}


def test_init_db_repairs_legacy_duplicate_active_jobs_before_unique_index(
    tmp_path, monkeypatch
):
    legacy = tmp_path / "legacy-duplicates.db"
    monkeypatch.setenv("LIRUNBAO_DB_PATH", str(legacy))
    with sqlite3.connect(legacy) as conn:
        conn.execute(
            """
            CREATE TABLE ai_report_jobs (
                id TEXT PRIMARY KEY, session_version TEXT NOT NULL,
                status TEXT NOT NULL, stage TEXT DEFAULT '', current INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0, message TEXT DEFAULT '', markdown TEXT DEFAULT '',
                error TEXT DEFAULT '', created_at TEXT DEFAULT '', started_at TEXT DEFAULT '',
                finished_at TEXT DEFAULT ''
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO ai_report_jobs
            (id, session_version, status, created_at)
            VALUES (?, 'same-version', ?, ?)
            """,
            [
                ("old-queued", "queued", "2026-01-01T00:00:00"),
                ("new-running", "running", "2026-01-02T00:00:00"),
            ],
        )

    db_mod.init_db()

    with sqlite3.connect(legacy) as conn:
        rows = conn.execute(
            "SELECT id, status, error_code FROM ai_report_jobs ORDER BY id"
        ).fetchall()
        indexes = conn.execute("PRAGMA index_list(ai_report_jobs)").fetchall()
    assert rows == [
        ("new-running", "running", ""),
        ("old-queued", "failed", "MIGRATION_DUPLICATE_ACTIVE"),
    ]
    assert any(row[1] == "uq_ai_report_jobs_active_version" for row in indexes)


def test_failed_cache_is_reextracted_and_fresh_failed_page_is_rejected(tmp_path):
    """Treating a cached failed page as success silently creates incomplete reports."""
    pipeline = _pipeline_mod()
    pdf = tmp_path / "2024.pdf"
    pdf.write_bytes(b"%PDF-failed")
    source = _source_for(pdf, 2024, 1)
    cache = _MemoryPageCache(
        {
            source.sha256: [
                {"page_no": 1, "total_pages": 1, "method": "none", "status": "failed", "text": ""}
            ]
        }
    )
    base_reader = importlib.import_module(
        "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
    )

    class FailedReader:
        PDFPageRecord = base_reader.PDFPageRecord
        file_sha256 = staticmethod(base_reader.file_sha256)

        @classmethod
        def extract_all_pages(cls, path, on_progress=None):
            on_progress(1, 1)
            return [cls.PDFPageRecord(1, 1, "none", "", "failed")]

    deps = pipeline.PipelineDependencies(
        reader=FailedReader,
        ai=object(),
        db=cache,
        engine_factory=lambda: None,
        workspace_root=tmp_path,
    )
    with pytest.raises(pipeline.PipelineError) as caught:
        pipeline.load_or_extract_pages(source, lambda *args: None, deps)

    assert caught.value.code == "PAGE_EXTRACTION_FAILED"
    assert caught.value.source_file == "2024.pdf"
    assert caught.value.page_range == (1, 1)
    assert source.sha256 not in cache.saved


def test_blank_page_is_covered_and_recorded_without_invented_text(tmp_path):
    """Dropping blank pages breaks complete page coverage; treating them as text invents facts."""
    pipeline = _pipeline_mod()
    pdf = tmp_path / "2024.pdf"
    pdf.write_bytes(b"%PDF-blank")
    source = _source_for(pdf, 2024, 1)
    base_reader = importlib.import_module(
        "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
    )

    class BlankReader:
        PDFPageRecord = base_reader.PDFPageRecord
        file_sha256 = staticmethod(base_reader.file_sha256)

        @classmethod
        def extract_all_pages(cls, path, on_progress=None):
            on_progress(1, 1)
            return [cls.PDFPageRecord(1, 1, "none", "", "blank")]

    deps = pipeline.PipelineDependencies(
        reader=BlankReader,
        ai=object(),
        db=_MemoryPageCache(),
        engine_factory=lambda: None,
        workspace_root=tmp_path,
    )
    snapshot = pipeline.JobInputSnapshot(
        "v", "企业", "制造业", (2024,), "{}", (source,)
    )

    loaded = pipeline.load_sources_pages(snapshot, lambda update: None, deps)

    assert loaded.page_coverage == {"2024.pdf": (1, 1)}
    assert loaded.blank_pages == {"2024.pdf": (1,)}
    assert loaded.pages["2024.pdf"][0].text == ""


def test_length_split_uses_page_midpoints_and_not_exception_message():
    """Matching localized error text breaks split retries when wording changes."""
    pipeline = _pipeline_mod()
    base_reader = importlib.import_module(
        "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
    )
    pages = tuple(
        base_reader.PDFPageRecord(n, 8, "text", f"page-{n}", "ok")
        for n in range(1, 9)
    )
    calls = []

    class FakeAI:
        @staticmethod
        def extract_chunk_facts(engine, source, chunk):
            calls.append((chunk.start_page, chunk.end_page))
            if len(calls) == 1:
                raise ai_engine_mod.AICompletionError("length", "totally changed wording")
            return {
                "source_file": source["name"],
                "report_year": source["report_year"],
                "page_range": [chunk.start_page, chunk.end_page],
                "_total_pages": 8,
                "_finish_reason": "stop",
                "metrics": {},
                "evidence": [],
            }

    facts = pipeline.extract_pages_with_split_retry(
        object(), {"name": "eight.pdf", "report_year": 2024}, pages,
        ai_module=FakeAI, reader_module=base_reader,
    )

    assert calls == [(1, 8), (1, 4), (5, 8)]
    assert [fact["page_range"] for fact in facts] == [[1, 4], [5, 8]]


def test_single_page_length_is_integrity_failure():
    """A single truncated page cannot be split safely and must never fall back as completed."""
    pipeline = _pipeline_mod()
    base_reader = importlib.import_module(
        "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
    )
    page = (base_reader.PDFPageRecord(1, 1, "text", "page", "ok"),)

    class AlwaysLength:
        @staticmethod
        def extract_chunk_facts(*args, **kwargs):
            raise ai_engine_mod.AICompletionError("length", "changed")

    with pytest.raises(pipeline.PipelineError) as caught:
        pipeline.extract_pages_with_split_retry(
            object(), {"name": "one.pdf", "report_year": 2024}, page,
            ai_module=AlwaysLength, reader_module=base_reader,
        )
    assert caught.value.code == "CHUNK_TRUNCATED_UNSPLITTABLE"


def test_final_generation_retries_once_with_higher_tokens():
    """Appending a truncated response or repeating 16384 cannot produce a complete report."""
    pipeline = _pipeline_mod()
    calls = []

    class FakeAI:
        @staticmethod
        def generate_final_report_result(engine, payload, max_tokens):
            calls.append(max_tokens)
            if len(calls) == 1:
                raise ai_engine_mod.AICompletionError("length", "changed")
            return ai_engine_mod.AIChatResult("complete", "stop", 1, 2, "model-x")

    result = pipeline.generate_final_with_retry(object(), {}, FakeAI)

    assert result.content == "complete"
    assert calls == [16384, 32768]


def test_unconfigured_ai_returns_honest_rules_report_without_network(tmp_path):
    """Labeling an offline deterministic report as AI-full misrepresents its provenance."""
    pipeline = _pipeline_mod()
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "规则企业"
    snapshot = pipeline.JobInputSnapshot(
        session_version="v",
        company_name="规则企业",
        industry="制造业",
        years=tuple(data.years),
        financial_data_json=json.dumps(dataclasses.asdict(data), ensure_ascii=False),
        sources=(),
    )
    deps = pipeline.PipelineDependencies.default(
        engine_factory=lambda: (_ for _ in ()).throw(AssertionError("network/config access")),
        workspace_root=tmp_path,
    )
    deps = dataclasses.replace(deps, engine_factory=lambda: None)

    result = pipeline.run_report_pipeline(snapshot, lambda update: None, deps)

    assert result.report_type == "rules_quick"
    assert result.fallback is True
    assert result.fallback_reason_code == "AI_NOT_CONFIGURED"
    assert result.model == ""
    assert "规则版快速报告" in result.markdown
    assert "AI 完整读取" not in result.markdown
    assert "DeepSeek 生成" not in result.markdown


def test_fourth_length_on_one_branch_fails_without_calling_sibling_branches():
    """Allowing a fourth split violates the bounded retry contract and can explode calls."""
    pipeline = _pipeline_mod()
    base_reader = importlib.import_module(
        "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
    )
    pages = tuple(
        base_reader.PDFPageRecord(n, 16, "text", f"page-{n}", "ok")
        for n in range(1, 17)
    )
    calls = []

    class AlwaysLength:
        @staticmethod
        def extract_chunk_facts(engine, source, chunk):
            calls.append((chunk.start_page, chunk.end_page))
            raise ai_engine_mod.AICompletionError("length", "changed")

    with pytest.raises(pipeline.PipelineError) as caught:
        pipeline.extract_pages_with_split_retry(
            object(), {"name": "sixteen.pdf", "report_year": 2024}, pages,
            ai_module=AlwaysLength, reader_module=base_reader,
        )

    assert caught.value.code == "CHUNK_TRUNCATION_EXHAUSTED"
    assert calls == [(1, 16), (1, 8), (1, 4), (1, 2)]


def test_content_filter_does_not_trigger_page_split():
    """Only length is recoverable by splitting; filtering is an integrity stop."""
    pipeline = _pipeline_mod()
    base_reader = importlib.import_module(
        "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
    )
    pages = tuple(
        base_reader.PDFPageRecord(n, 2, "text", f"page-{n}", "ok")
        for n in (1, 2)
    )
    calls = []

    class Filtered:
        @staticmethod
        def extract_chunk_facts(*args, **kwargs):
            calls.append(1)
            raise ai_engine_mod.AICompletionError("content_filter", "changed")

    with pytest.raises(pipeline.PipelineError) as caught:
        pipeline.extract_pages_with_split_retry(
            object(), {"name": "two.pdf", "report_year": 2024}, pages,
            ai_module=Filtered, reader_module=base_reader,
        )
    assert caught.value.code == "AI_COMPLETION_INCOMPLETE"
    assert calls == [1]


def test_second_final_length_fails_instead_of_rules_fallback():
    """Final truncation is an integrity failure, not an availability fallback."""
    pipeline = _pipeline_mod()
    calls = []

    class AlwaysLength:
        @staticmethod
        def generate_final_report_result(engine, payload, max_tokens):
            calls.append(max_tokens)
            raise ai_engine_mod.AICompletionError("length", "changed")

    with pytest.raises(pipeline.PipelineError) as caught:
        pipeline.generate_final_with_retry(object(), {}, AlwaysLength)
    assert caught.value.code == "FINAL_REPORT_TRUNCATED"
    assert calls == [16384, 32768]


def _valid_full_markdown(
    company: str,
    years: list[int],
    coverage: dict[str, tuple[int, int]],
    required_metrics: dict | None = None,
) -> str:
    metric_rows = [
        "| " + metric + " | " + " | ".join(
            str((required_metrics or {}).get(str(year), {}).get(metric, 1))
            for year in years
        ) + " |"
        for metric in (
            "营业收入",
            "毛利率",
            "净利率",
            "增值税税负率",
            "所得税税负率",
            "综合税负率",
        )
    ]
    headings = [
        "## 数据范围与完整性",
        *[f"- {name}: {value[0]}/{value[1]} 页" for name, value in coverage.items()],
        "## 跨年关键指标",
        "| 指标 | " + " | ".join(str(year) for year in years) + " |",
        "| --- | " + " | ".join("---" for _ in years) + " |",
        *metric_rows,
        "## 趋势与异常",
        "、".join(str(year) for year in years),
        "## 审计意见与重大事项",
        "未识别",
        "## 数据冲突与待核验项",
        "无",
        "## 计算口径与合规声明",
        "增值税税负率为估算值（基于税金及附加反推）。",
    ]
    return "\n".join(
        [f"# {company} {years[0]}—{years[-1]} 跨年合并报告", *headings]
    )


def test_pipeline_composes_real_ai_stages_without_replacing_coordinator(tmp_path):
    """A placeholder coordinator can pass job tests while never calling Tasks 2-4."""
    pipeline = _pipeline_mod()
    real_ai = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    base_reader = importlib.import_module(
        "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
    )
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "全链企业"
    source_items = []
    for year in sorted(data.years):
        pdf = tmp_path / f"{year}.pdf"
        pdf.write_bytes(f"%PDF-full-chain-{year}".encode())
        source_items.append(_source_for(pdf, year, 2))
    sources = tuple(source_items)
    coverage = {source.name: (2, 2) for source in sources}
    matrix = real_ai.deterministic_metric_matrix(real_ai.merge_years_deterministic(data))
    final_markdown = _valid_full_markdown(
        data.company_name, sorted(data.years), coverage, matrix
    )
    calls = []

    class Reader:
        PDFPageRecord = base_reader.PDFPageRecord
        PDFTextChunk = base_reader.PDFTextChunk
        file_sha256 = staticmethod(base_reader.file_sha256)
        chunk_pages = staticmethod(base_reader.chunk_pages)
        _format_page = staticmethod(base_reader._format_page)

        @classmethod
        def extract_all_pages(cls, path, on_progress=None):
            pages = [
                cls.PDFPageRecord(n, 2, "text", f"财务事实 {n}" * 20, "ok")
                for n in (1, 2)
            ]
            for n in (1, 2):
                on_progress(n, 2)
            return pages

    class Engine:
        model = "model-full"

        def chat_result(self, prompt, system_prompt="", max_tokens=0, extra=None):
            calls.append(max_tokens)
            if extra and extra.get("response_format"):
                content = json.dumps(
                    {
                        "source_file": "ignored",
                        "report_year": 1900,
                        "page_range": [99, 99],
                        "metrics": {},
                        "audit_opinion": None,
                        "major_events": None,
                        "evidence": None,
                    },
                    ensure_ascii=False,
                )
                return ai_engine_mod.AIChatResult(content, "stop", 1, 1, self.model)
            return ai_engine_mod.AIChatResult(
                final_markdown, "stop", 1, 1, self.model
            )

    snapshot = pipeline.JobInputSnapshot(
        "v",
        data.company_name,
        data.industry,
        tuple(data.years),
        json.dumps(dataclasses.asdict(data), ensure_ascii=False),
        sources,
    )
    updates = []
    result = pipeline.run_report_pipeline(
        snapshot,
        updates.append,
        pipeline.PipelineDependencies(
            Reader, real_ai, _MemoryPageCache(), Engine, tmp_path
        ),
    )

    assert result.report_type == "ai_full"
    assert result.model == "model-full"
    assert result.page_coverage == coverage
    assert result.fallback is False
    assert calls[-1] == 16384
    assert [update.current for update in updates] == sorted(
        update.current for update in updates
    )
    assert updates[-1].stage == "validate"


def test_request_failure_after_partial_fact_discards_ai_and_returns_rules(tmp_path):
    """Availability fallback must not retain facts from a partially successful AI attempt."""
    pipeline = _pipeline_mod()
    real_ai = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    base_reader = importlib.import_module(
        "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
    )
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "回退企业"
    source_items = []
    for year in sorted(data.years):
        pdf = tmp_path / f"{year}.pdf"
        pdf.write_bytes(f"%PDF-fallback-{year}".encode())
        source_items.append(_source_for(pdf, year, 2))
    sources = tuple(source_items)

    class Reader:
        PDFPageRecord = base_reader.PDFPageRecord
        PDFTextChunk = base_reader.PDFTextChunk
        file_sha256 = staticmethod(base_reader.file_sha256)
        _format_page = staticmethod(base_reader._format_page)

        @classmethod
        def extract_all_pages(cls, path, on_progress=None):
            pages = [
                cls.PDFPageRecord(n, 2, "text", f"page-{n}", "ok")
                for n in (1, 2)
            ]
            for n in (1, 2):
                on_progress(n, 2)
            return pages

        @staticmethod
        def chunk_pages(pages):
            return [
                base_reader.PDFTextChunk(page.page_no, page.page_no, 2, page.text)
                for page in pages
            ]

    class Engine:
        model = "attempted-model"

        def __init__(self):
            self.calls = 0

        def chat_result(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise ai_engine_mod.AIRequestError(
                    "AI_CONNECTION_FAILED", "safe", retryable=True
                )
            content = json.dumps(
                {
                    "source_file": "ignored",
                    "report_year": 1900,
                    "page_range": [99, 99],
                    "metrics": {"partial secret metric": 9},
                    "audit_opinion": None,
                    "major_events": None,
                    "evidence": None,
                }
            )
            return ai_engine_mod.AIChatResult(content, "stop", 1, 1, self.model)

    engine = Engine()
    snapshot = pipeline.JobInputSnapshot(
        "v", data.company_name, data.industry, tuple(data.years),
        json.dumps(dataclasses.asdict(data), ensure_ascii=False), sources,
    )
    result = pipeline.run_report_pipeline(
        snapshot,
        lambda update: None,
        pipeline.PipelineDependencies(
            Reader, real_ai, _MemoryPageCache(), lambda: engine, tmp_path
        ),
    )

    assert result.report_type == "rules_quick"
    assert result.fallback_reason_code == "AI_CONNECTION_FAILED"
    assert result.attempted_model == "attempted-model"
    assert "partial secret metric" not in result.markdown


def test_runner_passes_frozen_snapshot_and_atomically_commits_result(tmp_path, monkeypatch):
    """Passing the public job row instead of the frozen input breaks the pipeline boundary."""
    version, source_path = _seed_session(tmp_path)
    pipeline = _pipeline_mod()
    seen = []

    def run(job_id, snapshot, update):
        seen.append(snapshot)
        update(pipeline.ProgressUpdate("validate", 1, 1, "完成"))
        return pipeline.PipelineResult(
            "# frozen result", "rules_quick", "", "", True,
            "AI_NOT_CONFIGURED", {}, {}, 0,
        )

    monkeypatch.setattr(_job_mod(), "_run_pipeline", run)
    job_id = _job_mod().start_job(version)
    final = _wait_terminal(job_id)

    assert seen[0].company_name == "快照企业"
    assert seen[0].sources[0].path == source_path
    assert final["status"] == "completed"
    assert final["report_type"] == "rules_quick"
    assert len(db_mod.list_reports_for_job(job_id)) == 1


def test_runner_turns_rejected_progress_write_into_safe_terminal_failure(
    tmp_path, monkeypatch
):
    version, _ = _seed_session(tmp_path)
    pipeline = _pipeline_mod()

    def run(job_id, snapshot, update):
        update(pipeline.ProgressUpdate("read", 2, 1, "invalid"))
        return pipeline.PipelineResult(
            "# must not save", "rules_quick", "", "", True,
            "AI_NOT_CONFIGURED", {}, {}, 0,
        )

    monkeypatch.setattr(_job_mod(), "_run_pipeline", run)
    job_id = _job_mod().start_job(version)
    final = _wait_terminal(job_id)

    assert final["status"] == "failed"
    assert final["error_code"] == "PROGRESS_INVALID"
    assert db_mod.list_reports_for_job(job_id) == []


def test_unknown_worker_exception_is_not_rendered_into_logs_or_job(
    tmp_path, monkeypatch, caplog
):
    version, _ = _seed_session(tmp_path)
    job_mod = _job_mod()
    marker = f"secret-key-at-{tmp_path}"

    def explode(*args, **kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(job_mod, "_run_pipeline", explode)
    caplog.set_level("ERROR")
    job_id = job_mod.start_job(version)
    final = _wait_terminal(job_id)

    assert final["error_code"] == "INTERNAL_PIPELINE_ERROR"
    serialized = json.dumps(final, ensure_ascii=False)
    assert marker not in serialized
    assert marker not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_thread_start_failure_fails_job_and_drains_retirements_once(
    tmp_path, monkeypatch
):
    version, _ = _seed_session(tmp_path)
    job_mod = _job_mod()
    lifecycle = importlib.import_module(
        "web_backend.CO_import_WB-CO-TR-20260805160732"
    )
    drained = []
    monkeypatch.setattr(job_mod, "_new_job_id", lambda: "job-thread-start-failed")
    monkeypatch.setattr(lifecycle, "retry_workspace_cleanup", lambda: drained.append(True))
    monkeypatch.setattr(
        job_mod.threading.Thread,
        "start",
        lambda self: (_ for _ in ()).throw(RuntimeError("private thread marker")),
    )

    with pytest.raises(RuntimeError):
        job_mod.start_job(version)

    final = db_mod.get_job("job-thread-start-failed")
    assert final["status"] == "failed"
    assert final["error_code"] == "WORKER_START_FAILED"
    assert drained == [True]


def test_runner_session_change_fails_with_stable_code_and_no_report(tmp_path, monkeypatch):
    """A live session replacement during work must lose the final transaction race safely."""
    version, _ = _seed_session(tmp_path)
    pipeline = _pipeline_mod()
    entered = threading.Event()
    release = threading.Event()

    def run(job_id, snapshot, update):
        entered.set()
        assert release.wait(timeout=2)
        return pipeline.PipelineResult(
            "# stale", "rules_quick", "", "", True,
            "AI_NOT_CONFIGURED", {}, {}, 0,
        )

    monkeypatch.setattr(_job_mod(), "_run_pipeline", run)
    job_id = _job_mod().start_job(version)
    assert entered.wait(timeout=2)
    _seed_session(tmp_path, company="新企业")
    release.set()
    final = _wait_terminal(job_id)

    assert final["status"] == "failed"
    assert final["error_code"] == "SESSION_CHANGED"
    assert db_mod.list_reports_for_job(job_id) == []


def test_runner_unknown_exception_is_sanitized(tmp_path, monkeypatch):
    """Persisting str(exc) can leak paths, OCR text, prompts, or credentials via GET job."""
    version, source_path = _seed_session(tmp_path)
    marker = "fake-key-private-ocr-marker"

    def fail(*args, **kwargs):
        raise RuntimeError(f"{source_path} {marker}")

    monkeypatch.setattr(_job_mod(), "_run_pipeline", fail)
    job_id = _job_mod().start_job(version)
    final = _wait_terminal(job_id)
    serialized = json.dumps(final, ensure_ascii=False)

    assert final["status"] == "failed"
    assert final["error_code"] == "INTERNAL_PIPELINE_ERROR"
    assert source_path not in serialized
    assert marker not in serialized


def test_start_route_rejects_empty_session_without_creating_job(tmp_path):
    """An empty-version job has no immutable inputs and must not enter queued state."""
    app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
    from fastapi.testclient import TestClient

    with TestClient(app_mod.create_app()) as client:
        response = client.post("/api/ai/years-summary/jobs")

    assert response.status_code == 400
    assert db_mod.has_active_jobs() is False


def test_reconciled_payload_requires_explicit_deterministic_value_or_missing_state():
    """Dropping a deterministic metric before final generation hides structured facts."""
    real_ai = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    payload = {
        "company_name": "企业",
        "years": {"2024": {"营业收入": 1.0}},
        "page_coverage": {"2024.pdf": [1, 1]},
        "documents": [{"source_file": "2024.pdf"}],
        "conflicts": [],
    }
    expected = {
        "company_name": "企业",
        "years": [2024],
        "page_coverage": {"2024.pdf": [1, 1]},
        "required_metrics": {"2024": {"营业收入": 1.0, "毛利率": None}},
    }

    errors = real_ai.validate_reconciled_payload(payload, expected)

    assert any("确定性指标" in error for error in errors)


def test_pipeline_rejects_duplicate_source_year_before_reading(tmp_path):
    """Two source files claiming the same year make deterministic document identity ambiguous."""
    pipeline = _pipeline_mod()
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "重复年度企业"
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(b"%PDF-a")
    second.write_bytes(b"%PDF-b")
    sources = (
        _source_for(first, max(data.years), 1),
        _source_for(second, max(data.years), 1),
    )
    snapshot = pipeline.JobInputSnapshot(
        "v", data.company_name, data.industry, tuple(data.years),
        json.dumps(dataclasses.asdict(data), ensure_ascii=False), sources,
    )
    deps = pipeline.PipelineDependencies.default(
        engine_factory=lambda: SimpleNamespace(model="configured"),
        workspace_root=tmp_path,
    )

    with pytest.raises(pipeline.PipelineError) as caught:
        pipeline.run_report_pipeline(snapshot, lambda update: None, deps)

    assert caught.value.code == "SOURCE_METADATA_INVALID"


def test_job_continue_gate_detects_database_session_change(tmp_path):
    """Waiting until final commit wastes a long AI run after the session is already stale."""
    version, _ = _seed_session(tmp_path)
    job_id, _ = db_mod.capture_session_and_create_job(
        "job-progress-gate", version, "owner-current"
    )
    assert db_mod.start_job(job_id)
    assert db_mod.job_continue_status(job_id, version) == "ok"

    _seed_session(tmp_path, company="changed")

    assert db_mod.job_continue_status(job_id, version) == "session_changed"


def test_configured_ai_with_incomplete_source_years_is_hard_failure_without_reading(tmp_path):
    """An AI-full report must not be produced when only one of three annual sources exists."""
    pipeline = _pipeline_mod()
    real_ai = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "缺来源企业"
    pdf = tmp_path / "latest.pdf"
    pdf.write_bytes(b"%PDF-latest")
    source = _source_for(pdf, max(data.years), 1)

    class MustNotRead:
        def __getattr__(self, name):
            raise AssertionError(f"reader should not be used: {name}")

    snapshot = pipeline.JobInputSnapshot(
        "v", data.company_name, data.industry, tuple(data.years),
        json.dumps(dataclasses.asdict(data), ensure_ascii=False), (source,)
    )
    with pytest.raises(pipeline.PipelineError) as caught:
        pipeline.run_report_pipeline(
            snapshot,
            lambda update: None,
            pipeline.PipelineDependencies(
                MustNotRead(), real_ai, _MemoryPageCache(),
                lambda: SimpleNamespace(model="configured"), tmp_path,
            ),
        )

    assert caught.value.code == "SOURCE_FILES_INCOMPLETE"


def test_session_version_digest_covers_industry_data_and_all_source_metadata(tmp_path):
    """Omitting any frozen input from the version lets a stale job appear current."""
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "摘要企业"
    source = {
        "path": str((tmp_path / "2024.pdf").resolve()),
        "name": "2024.pdf",
        "sha256": "a" * 64,
        "size": 10,
        "report_year": 2024,
        "page_count": 7,
    }
    baseline = session_mod._version_for(data, [source])

    changed_industry = parser.parse_financial_dict(dataclasses.asdict(data))
    changed_industry.industry = "软件业"
    changed_data = parser.parse_financial_dict(dataclasses.asdict(data))
    changed_data.income_statement["营业收入"][changed_data.years[-1]] = 999.0

    assert session_mod._version_for(changed_industry, [source]) != baseline
    assert session_mod._version_for(changed_data, [source]) != baseline
    for field, value in (
        ("path", str((tmp_path / "other.pdf").resolve())),
        ("name", "other.pdf"),
        ("size", 11),
        ("report_year", 2023),
        ("page_count", 8),
    ):
        modified = dict(source)
        modified[field] = value
        assert session_mod._version_for(data, [modified]) != baseline


def test_atomic_commit_rejects_content_change_even_when_version_column_is_unchanged(
    tmp_path,
):
    """The final transaction must compare the complete captured input, not one version field."""
    version, _ = _seed_session(tmp_path)
    job_id, _ = db_mod.capture_session_and_create_job(
        "job-digest-stale", version, "owner-current"
    )
    assert db_mod.start_job(job_id)
    with sqlite3.connect(db_mod.get_db_path()) as conn:
        conn.execute(
            "UPDATE session SET industry = ? WHERE id = 1",
            ("软件业",),
        )

    outcome = db_mod.commit_report_and_complete_job(
        job_id, version, "title", _pipeline_result()
    )

    assert outcome.status == "session_changed"
    assert db_mod.list_reports_for_job(job_id) == []


@pytest.mark.parametrize(
    ("actual", "expected"),
    [(999999.0, 100.0), (0.0, None)],
)
def test_reconciled_payload_rejects_wrong_deterministic_value_or_fake_zero(
    actual, expected
):
    """A present key is insufficient: its value and explicit missing state are immutable."""
    real_ai = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    payload = {
        "company_name": "企业",
        "years": {"2024": {"营业收入": actual}},
        "page_coverage": {"2024.pdf": [1, 1]},
        "documents": [{"source_file": "2024.pdf"}],
        "conflicts": [],
    }
    errors = real_ai.validate_reconciled_payload(
        payload,
        {
            "company_name": "企业",
            "years": [2024],
            "page_coverage": {"2024.pdf": [1, 1]},
            "required_metrics": {"2024": {"营业收入": expected}},
        },
    )
    assert any("确定性指标值" in error for error in errors)


def test_final_report_rejects_wrong_numeric_value_and_none_rendered_as_zero():
    """Non-empty table cells must still equal deterministic values and preserve missing as missing."""
    real_ai = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    markdown = "\n".join(
        [
            "# 企业 2024—2024 跨年合并报告",
            "## 数据范围与完整性",
            "- 2024.pdf: 1/1 页",
            "## 跨年关键指标",
            "| 指标 | 2024 |",
            "| --- | --- |",
            "| 营业收入 | 999999 |",
            "| 毛利率 | 0 |",
            "## 趋势与异常",
            "2024",
            "## 审计意见与重大事项",
            "未识别",
            "## 数据冲突与待核验项",
            "无",
            "## 计算口径与合规声明",
            "增值税税负率为估算值（基于税金及附加反推）。",
        ]
    )
    errors = real_ai.validate_final_report(
        markdown,
        {
            "company_name": "企业",
            "years": [2024],
            "page_coverage": {"2024.pdf": [1, 1]},
            "required_metrics": {"2024": {"营业收入": 100.0, "毛利率": None}},
        },
    )
    assert "关键指标值不一致：营业收入 2024" in errors
    assert "关键指标缺失状态不一致：毛利率 2024" in errors


def test_final_report_requires_conflict_values_sources_and_evidence_pages():
    """A conflict count without both sides and evidence in the conflict section is not auditable."""
    real_ai = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    markdown = _valid_full_markdown("企业", [2024], {"a.pdf": (1, 1), "b.pdf": (1, 1)})
    conflict = {
        "metric": "营业收入",
        "sides": [
            {
                "source_file": "a.pdf",
                "value": 100.0,
                "evidence": [{"pages": [1]}],
            },
            {
                "source_file": "b.pdf",
                "value": 200.0,
                "evidence": [{"pages": [2]}],
            },
        ],
    }
    errors = real_ai.validate_final_report(
        markdown,
        {
            "company_name": "企业",
            "years": [2024],
            "page_coverage": {"a.pdf": [1, 1], "b.pdf": [1, 1]},
            "conflicts": [conflict],
        },
    )
    assert any("冲突呈现不完整" in error for error in errors)


def test_runner_rejects_bare_string_pipeline_result():
    """A placeholder string must never be upgraded into a formal ai_full report."""
    pipeline = _pipeline_mod()
    with pytest.raises(pipeline.PipelineError) as caught:
        _job_mod()._coerce_pipeline_result("# unvalidated placeholder")
    assert caught.value.code == "PIPELINE_RESULT_INVALID"


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(
            markdown="# x", report_type="ai_full", model="", attempted_model="m",
            fallback=False, fallback_reason_code="", page_coverage={}, blank_pages={},
            conflict_count=0,
        ),
        SimpleNamespace(
            markdown="# x", report_type="rules_quick", model="m", attempted_model="m",
            fallback=False, fallback_reason_code="", page_coverage={}, blank_pages={},
            conflict_count=0,
        ),
    ],
)
def test_atomic_commit_rejects_invalid_result_metadata_combinations(
    tmp_path, result
):
    """DB commit is the last defense against mislabeled AI/rules results."""
    version, _ = _seed_session(tmp_path)
    job_id, _ = db_mod.capture_session_and_create_job(
        f"job-invalid-result-{result.report_type}", version, "owner-current"
    )
    assert db_mod.start_job(job_id)
    outcome = db_mod.commit_report_and_complete_job(job_id, version, "title", result)
    assert outcome.status == "invalid_result"
    assert db_mod.list_reports_for_job(job_id) == []


@pytest.mark.parametrize(
    ("code", "retryable", "allowed"),
    [
        ("AI_TIMEOUT", True, True),
        ("AI_CONNECTION_FAILED", True, True),
        ("AI_HTTP_ERROR", True, True),
        ("JSON_SCHEMA_BUG", False, False),
        ("AI_RESPONSE_INVALID", False, False),
        ("AI_REQUEST_FAILED", True, False),
    ],
)
def test_fallback_uses_explicit_request_error_whitelist(code, retryable, allowed):
    """Arbitrary AIRequestError codes must not silently complete as rules reports."""
    error = ai_engine_mod.AIRequestError(code, "safe", retryable)
    assert _pipeline_mod().is_request_fallback_allowed(error) is allowed


def test_configured_ai_with_no_sources_is_integrity_failure(tmp_path):
    """Missing source files must not complete once an AI-full run was requested."""
    pipeline = _pipeline_mod()
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "缺来源企业"
    snapshot = pipeline.JobInputSnapshot(
        "v", data.company_name, data.industry, tuple(data.years),
        json.dumps(dataclasses.asdict(data), ensure_ascii=False), (),
    )
    deps = pipeline.PipelineDependencies.default(
        engine_factory=lambda: SimpleNamespace(model="configured"),
        workspace_root=tmp_path,
    )
    with pytest.raises(pipeline.PipelineError) as caught:
        pipeline.run_report_pipeline(snapshot, lambda update: None, deps)
    assert caught.value.code == "SOURCE_FILES_UNAVAILABLE"


def test_malformed_ai_response_is_non_retryable_protocol_error(monkeypatch):
    """Protocol/schema bugs must not be mislabeled as network availability failures."""
    engine = ai_engine_mod.AIEngine("https://example.invalid", "test-key", "test-model")

    class MalformedResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {}

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: MalformedResponse())
    with pytest.raises(ai_engine_mod.AIRequestError) as caught:
        engine.chat_result("input")
    assert caught.value.code == "AI_RESPONSE_INVALID"
    assert caught.value.retryable is False


def test_legacy_sync_years_summary_redirects_to_job_and_never_saves_untyped_report(
    tmp_path,
):
    """The legacy endpoint must use the same frozen job and typed report boundary."""
    _seed_session(tmp_path)
    app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
    from fastapi.testclient import TestClient

    with TestClient(app_mod.create_app()) as client:
        response = client.post("/api/ai/years-summary")
    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    final = _wait_terminal(body["job_id"])
    assert final["report_type"] == "rules_quick"
    reports = db_mod.list_reports_for_job(body["job_id"])
    assert reports and reports[0]["report_type"] == "rules_quick"


def test_live_old_process_lock_prevents_recovery_and_source_drain(tmp_path, monkeypatch):
    """A new process cannot fail old jobs or delete sources while the old process lock is live."""
    job_mod = _job_mod()
    import_mod = importlib.import_module(
        "web_backend.CO_import_WB-CO-TR-20260805160732"
    )
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    batch = workspace / "import-old"
    batch.mkdir(parents=True)
    source_path = batch / "old.pdf"
    source_path.write_bytes(b"old")
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "旧进程企业"
    source = {
        "path": str(source_path.resolve()),
        "name": "old.pdf",
        "sha256": importlib.import_module("hashlib").sha256(b"old").hexdigest(),
        "size": 3,
        "report_year": max(data.years),
        "page_count": 1,
    }
    session_mod.replace(data, [], [source])
    old_version = session_mod.get_version()
    job_id, _ = db_mod.capture_session_and_create_job(
        "job-live-old", old_version, "owner-old"
    )
    assert db_mod.start_job(job_id)
    newer = parser.parse_financial_dict(build_sample_data())
    newer.company_name = "新进程企业"
    session_mod.replace(newer, [], [])
    db_mod.queue_workspace_retirement(str(workspace), str(batch), "session_replaced")

    job_mod.release_process_lease(force=True)
    lock_path = job_mod.get_process_lock_path()
    child_code = (
        "import pathlib, sys\n"
        "p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True)\n"
        "f=p.open('a+')\n"
        "try:\n"
        "    import fcntl\n"
        "    fcntl.flock(f.fileno(), fcntl.LOCK_EX)\n"
        "except ImportError:\n"
        "    import msvcrt\n"
        "    f.seek(0)\n"
        "    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\n"
        "print('ready', flush=True)\n"
        "sys.stdin.read()"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        assert job_mod.acquire_process_lease() is False
        assert job_mod.recover_orphaned_jobs() == 0
        assert import_mod.retry_workspace_cleanup() == 0
        assert db_mod.get_job(job_id)["status"] == "running"
        assert batch.exists()
    finally:
        child.stdin.close()
        child.wait(timeout=3)

    assert job_mod.acquire_process_lease() is True
    try:
        assert job_mod.recover_orphaned_jobs() == 1
        assert import_mod.retry_workspace_cleanup() == 1
        assert not batch.exists()
    finally:
        job_mod.release_process_lease(force=True)


def _probe_process_lock(lock_path: Path) -> str:
    """Return the result of a separate process's non-blocking lock attempt."""
    code = (
        "import pathlib, sys\n"
        "p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True)\n"
        "f=p.open('a+')\n"
        "try:\n"
        "    import fcntl\n"
        "except ImportError:\n"
        "    import msvcrt\n"
        "    f.seek(0)\n"
        "    try:\n"
        "        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\n"
        "    except OSError:\n"
        "        print('blocked')\n"
        "    else:\n"
        "        print('acquired')\n"
        "else:\n"
        "    try:\n"
        "        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    except BlockingIOError:\n"
        "        print('blocked')\n"
        "    else:\n"
        "        print('acquired')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, str(lock_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    return completed.stdout.strip()


def test_worker_cannot_release_its_own_process_lease(tmp_path):
    """A worker is still alive until its target returns, even inside its final cleanup."""
    job_mod = _job_mod()
    assert job_mod.acquire_process_lease() is True
    checked = threading.Event()
    release_worker = threading.Event()
    observed = {}

    def attempts_early_release():
        observed["released"] = job_mod.release_process_lease(force=True)
        observed["second_process"] = _probe_process_lock(
            job_mod.get_process_lock_path()
        )
        checked.set()
        assert release_worker.wait(timeout=2)

    worker = threading.Thread(target=attempts_early_release)
    with job_mod._lock:
        job_mod._threads_by_job["job-self-release-probe"] = worker
    worker.start()
    try:
        assert checked.wait(timeout=2)
        assert observed == {"released": False, "second_process": "blocked"}
    finally:
        release_worker.set()
        worker.join(timeout=2)
        with job_mod._lock:
            job_mod._threads_by_job.pop("job-self-release-probe", None)
        job_mod.release_process_lease(force=True)


def test_lifespan_shutdown_timeout_keeps_lease_and_source_until_worker_stops(
    tmp_path, monkeypatch
):
    """Releasing the lease after a bounded join timeout lets recovery delete live input."""
    job_mod = _job_mod()
    import_mod = importlib.import_module(
        "web_backend.CO_import_WB-CO-TR-20260805160732"
    )
    app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
    from fastapi.testclient import TestClient

    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("LIRUNBAO_WORKSPACE_PATH", str(workspace))
    batch = workspace / "import-shutdown"
    batch.mkdir(parents=True)
    source_path = batch / "shutdown.pdf"
    source_path.write_bytes(b"shutdown-source")
    data = parser.parse_financial_dict(build_sample_data())
    data.company_name = "关停企业"
    source = {
        "path": str(source_path.resolve()),
        "name": source_path.name,
        "sha256": importlib.import_module("hashlib").sha256(
            source_path.read_bytes()
        ).hexdigest(),
        "size": source_path.stat().st_size,
        "report_year": max(data.years),
        "page_count": 1,
    }
    session_mod.replace(data, [], [source])
    version = session_mod.get_version()
    entered = threading.Event()
    release = threading.Event()

    def blocked_pipeline(job_id, snapshot, update):
        entered.set()
        assert release.wait(timeout=3)
        return _pipeline_mod().PipelineResult(
            markdown="# stopped",
            report_type="rules_quick",
            model="",
            attempted_model="",
            fallback=True,
            fallback_reason_code="AI_NOT_CONFIGURED",
            page_coverage={},
            blank_pages={},
            conflict_count=0,
        )

    monkeypatch.setattr(job_mod, "_run_pipeline", blocked_pipeline)
    monkeypatch.setattr(job_mod, "_SHUTDOWN_WAIT_SECONDS", 0.05, raising=False)

    client = TestClient(app_mod.create_app())
    client.__enter__()
    try:
        response = client.post("/api/ai/years-summary/jobs")
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        assert entered.wait(timeout=2)

        replacement = parser.parse_financial_dict(build_sample_data())
        replacement.company_name = "新会话"
        session_mod.replace(replacement, [], [])
        db_mod.queue_workspace_retirement(
            str(workspace), str(batch), "session_replaced"
        )

        shutdown = threading.Thread(target=client.__exit__, args=(None, None, None))
        shutdown.start()
        shutdown.join(timeout=1)
        assert not shutdown.is_alive(), "shutdown must use a bounded wait"
        assert job_mod._threads_by_job[job_id].is_alive()
        assert _probe_process_lock(job_mod.get_process_lock_path()) == "blocked"
        assert import_mod.retry_workspace_cleanup() == 0
        assert batch.exists()

        with pytest.raises(job_mod.ProcessLeaseError):
            job_mod.start_job(session_mod.get_version())
    finally:
        release.set()

    final = _wait_terminal(job_id)
    assert final["status"] == "failed"
    deadline = time.monotonic() + 2
    while (batch.exists() or job_mod.owns_process_lease()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not batch.exists()
    assert _probe_process_lock(job_mod.get_process_lock_path()) == "acquired"


@pytest.mark.parametrize("worker_error", [False, True])
def test_lifespan_shutdown_waits_for_finishing_worker_before_unlock(
    tmp_path, monkeypatch, worker_error
):
    """Normal and exceptional workers that finish within the bound release safely."""
    job_mod = _job_mod()
    app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
    from fastapi.testclient import TestClient

    version, _ = _seed_session(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def finishing_pipeline(job_id, snapshot, update):
        entered.set()
        assert release.wait(timeout=2)
        if worker_error:
            raise RuntimeError("private shutdown error")
        return _pipeline_mod().PipelineResult(
            markdown="# complete",
            report_type="rules_quick",
            model="",
            attempted_model="",
            fallback=True,
            fallback_reason_code="AI_NOT_CONFIGURED",
            page_coverage={},
            blank_pages={},
            conflict_count=0,
        )

    monkeypatch.setattr(job_mod, "_run_pipeline", finishing_pipeline)
    monkeypatch.setattr(job_mod, "_SHUTDOWN_WAIT_SECONDS", 1.0, raising=False)
    client = TestClient(app_mod.create_app())
    client.__enter__()
    try:
        job_id = job_mod.start_job(version)
        assert entered.wait(timeout=2)
        closer = threading.Thread(target=client.__exit__, args=(None, None, None))
        closer.start()
        time.sleep(0.05)
        assert closer.is_alive()
        assert _probe_process_lock(job_mod.get_process_lock_path()) == "blocked"
        release.set()
        closer.join(timeout=2)
        assert not closer.is_alive()
    finally:
        release.set()

    expected = "failed" if worker_error else "completed"
    assert _wait_terminal(job_id)["status"] == expected
    assert _probe_process_lock(job_mod.get_process_lock_path()) == "acquired"
