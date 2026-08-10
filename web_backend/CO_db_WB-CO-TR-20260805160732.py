"""利润宝 · SQLite 持久化层。

保存会话（FinancialData + OCR 文本）与 AI 报告，服务重启/前端刷新后可从
数据库恢复，根治「切换工作区/刷新后数据丢失」问题（Web 重构设计文档 §5）。

DB 文件位于 web_backend/workspaces/app.db（workspaces 已在 .gitignore）。
使用标准库 sqlite3，无新增依赖。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "workspaces" / "app.db"
_lock = threading.Lock()
_START_DEDUPE_SECONDS = 2.0


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def get_db_path() -> Path:
    """返回会话数据库路径；测试进程可通过环境变量隔离持久化。"""
    configured = os.environ.get("LIRUNBAO_DB_PATH", "").strip()
    if configured:
        return Path(configured).resolve()
    workspace = os.environ.get("LIRUNBAO_WORKSPACE_PATH", "").strip()
    return (Path(workspace).resolve() / "app.db") if workspace else _DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
    """为已有安装平滑补齐会话快照字段。"""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    """创建表（幂等）。"""
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS session (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                company_name TEXT DEFAULT '',
                industry TEXT DEFAULT '制造业',
                years TEXT DEFAULT '[]',
                indicators TEXT DEFAULT '[]',
                data_json TEXT DEFAULT '{}',
                ocr_texts TEXT DEFAULT '[]',
                source_files TEXT DEFAULT '[]',
                saved_previews TEXT DEFAULT '[]',
                session_version TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS ai_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS pdf_page_cache (
                file_sha256 TEXT NOT NULL,
                page_no INTEGER NOT NULL,
                total_pages INTEGER NOT NULL,
                method TEXT NOT NULL,
                status TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (file_sha256, page_no)
            );
            CREATE TABLE IF NOT EXISTS ai_report_jobs (
                id TEXT PRIMARY KEY,
                session_version TEXT NOT NULL,
                status TEXT NOT NULL,
                stage TEXT DEFAULT '',
                current INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0,
                message TEXT DEFAULT '',
                markdown TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                started_at TEXT DEFAULT '',
                finished_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS workspace_retirements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_path TEXT NOT NULL,
                batch_path TEXT NOT NULL UNIQUE,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS diagnosis_results (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                session_version TEXT NOT NULL DEFAULT '',
                company_name TEXT DEFAULT '',
                industry TEXT DEFAULT '',
                industry_fallback INTEGER DEFAULT 0,
                vat_estimate_note TEXT DEFAULT '',
                ai_used INTEGER DEFAULT 0,
                findings_json TEXT DEFAULT '[]',
                created_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS interaction_sessions (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                session_version TEXT NOT NULL DEFAULT '',
                state TEXT DEFAULT '',
                decisions_json TEXT DEFAULT '[]',
                strategy_notes_json TEXT DEFAULT '[]',
                draft2_json TEXT DEFAULT '[]',
                feasibility_score REAL DEFAULT 100.0,
                feasibility_breakdown_json TEXT DEFAULT '[]',
                ai_fallback_message TEXT DEFAULT '',
                user_confirmed INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT ''
            );
            """
        )
        _ensure_column(conn, "session", "source_files", "TEXT DEFAULT '[]'")
        _ensure_column(conn, "session", "saved_previews", "TEXT DEFAULT '[]'")
        _ensure_column(conn, "session", "session_version", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_reports", "job_id", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_reports", "session_version", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_reports", "model", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_reports", "attempted_model", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_reports", "report_type", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_reports", "fallback", "INTEGER DEFAULT 0")
        _ensure_column(conn, "ai_reports", "fallback_reason_code", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_reports", "page_coverage_json", "TEXT DEFAULT '{}'")
        _ensure_column(conn, "ai_reports", "blank_pages_json", "TEXT DEFAULT '{}'")
        _ensure_column(conn, "ai_reports", "conflict_count", "INTEGER DEFAULT 0")
        _ensure_column(conn, "ai_report_jobs", "snapshot_json", "TEXT DEFAULT '{}'")
        _ensure_column(conn, "ai_report_jobs", "owner_token", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_report_jobs", "input_digest", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_report_jobs", "error_code", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_report_jobs", "report_id", "INTEGER DEFAULT 0")
        _ensure_column(conn, "ai_report_jobs", "report_type", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_report_jobs", "model", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_report_jobs", "attempted_model", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_report_jobs", "fallback", "INTEGER DEFAULT 0")
        _ensure_column(conn, "ai_report_jobs", "fallback_reason_code", "TEXT DEFAULT ''")
        _ensure_column(conn, "ai_report_jobs", "page_coverage_json", "TEXT DEFAULT '{}'")
        _ensure_column(conn, "ai_report_jobs", "blank_pages_json", "TEXT DEFAULT '{}'")
        _ensure_column(conn, "ai_report_jobs", "conflict_count", "INTEGER DEFAULT 0")
        _ensure_column(conn, "ai_report_jobs", "dedupe_until", "REAL DEFAULT 0")
        duplicate_versions = conn.execute(
            """
            SELECT session_version FROM ai_report_jobs
            WHERE status IN ('queued', 'running')
            GROUP BY session_version HAVING COUNT(*) > 1
            """
        ).fetchall()
        for duplicate in duplicate_versions:
            active_rows = conn.execute(
                """
                SELECT id FROM ai_report_jobs
                WHERE session_version = ? AND status IN ('queued', 'running')
                ORDER BY created_at DESC, rowid DESC
                """,
                (duplicate["session_version"],),
            ).fetchall()
            stale_ids = [row["id"] for row in active_rows[1:]]
            if stale_ids:
                placeholders = ",".join("?" for _ in stale_ids)
                conn.execute(
                    f"""
                    UPDATE ai_report_jobs
                    SET status = 'failed', finished_at = ?,
                        error_code = 'MIGRATION_DUPLICATE_ACTIVE',
                        error = '历史重复任务已安全终止'
                    WHERE id IN ({placeholders})
                    """,
                    (_now(), *stale_ids),
                )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_report_jobs_active_version
            ON ai_report_jobs(session_version)
            WHERE status IN ('queued', 'running')
            """
        )


# ── 完整 PDF 页面缓存 ─────────────────────────────────────────────────

def save_cached_pages(file_hash: str, pages: list[dict]) -> None:
    """原子写入某一 PDF 的完整逐页读取结果。

    先清除该文件的旧缓存再批量插入，连接上下文保证两步同属一个事务；读取端
    仍会校验页集完整性，因此传入不完整页集也绝不会被复用。
    """
    init_db()
    rows = [
        (
            file_hash,
            int(page["page_no"]),
            int(page["total_pages"]),
            str(page["method"]),
            str(page["status"]),
            str(page["text"]),
        )
        for page in pages
    ]
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM pdf_page_cache WHERE file_sha256 = ?", (file_hash,))
        conn.executemany(
            """
            INSERT INTO pdf_page_cache (
                file_sha256, page_no, total_pages, method, status, text
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def load_cached_pages(file_hash: str) -> list[dict]:
    """读取完整有效的缓存页集；缺页、重复或页数不一致时返回空列表。"""
    init_db()
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT page_no, total_pages, method, status, text
            FROM pdf_page_cache
            WHERE file_sha256 = ?
            ORDER BY page_no
            """,
            (file_hash,),
        ).fetchall()
    if not rows:
        return []
    total_pages = rows[0]["total_pages"]
    if total_pages <= 0 or len(rows) != total_pages:
        return []
    if any(row["total_pages"] != total_pages for row in rows):
        return []
    if [row["page_no"] for row in rows] != list(range(1, total_pages + 1)):
        return []
    return [dict(row) for row in rows]


# ── 会话持久化 ─────────────────────────────────────────────────────────

def save_session(
    session_data: dict,
    ocr_texts: list,
    source_files: list | None = None,
    saved_previews: list | None = None,
    session_version: str = "",
) -> None:
    """写入当前会话（单行 id=1）。session_data 为可 JSON 序列化的 dict。"""
    init_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO session (
                id, company_name, industry, years, indicators, data_json, ocr_texts,
                source_files, saved_previews, session_version, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                company_name=excluded.company_name, industry=excluded.industry,
                years=excluded.years, indicators=excluded.indicators,
                data_json=excluded.data_json, ocr_texts=excluded.ocr_texts,
                source_files=excluded.source_files, saved_previews=excluded.saved_previews,
                session_version=excluded.session_version,
                updated_at=excluded.updated_at
            """,
            (
                session_data.get("company_name", ""),
                session_data.get("industry", "制造业"),
                json.dumps(session_data.get("years", []), ensure_ascii=False),
                json.dumps(session_data.get("indicators", []), ensure_ascii=False),
                json.dumps(session_data.get("data_json", {}), ensure_ascii=False),
                json.dumps(ocr_texts, ensure_ascii=False),
                json.dumps(source_files or [], ensure_ascii=False),
                json.dumps(saved_previews or [], ensure_ascii=False),
                session_version,
                "",
            ),
        )


def load_session() -> dict | None:
    """读取当前会话；无则返回 None。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM session WHERE id = 1").fetchone()
    if row is None:
        return None
    return {
        "company_name": row["company_name"],
        "industry": row["industry"],
        "years": json.loads(row["years"] or "[]"),
        "indicators": json.loads(row["indicators"] or "[]"),
        "data_json": json.loads(row["data_json"] or "{}"),
        "ocr_texts": json.loads(row["ocr_texts"] or "[]"),
        "source_files": json.loads(row["source_files"] or "[]"),
        "saved_previews": json.loads(row["saved_previews"] or "[]"),
        "session_version": row["session_version"] or "",
    }


def clear_session_db() -> None:
    """清空会话表。"""
    init_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM session WHERE id = 1")


# ── 诊断结果持久化（绑定 session_version，重导入自动失效）────────────────

def save_diagnosis(
    session_version: str,
    company_name: str,
    industry: str,
    industry_fallback: bool,
    vat_estimate_note: str,
    ai_used: bool,
    findings: list,
) -> None:
    """写入当前诊断结果（单行 id=1）。findings 为可 JSON 序列化的列表。"""
    init_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO diagnosis_results (
                id, session_version, company_name, industry, industry_fallback,
                vat_estimate_note, ai_used, findings_json, created_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                session_version=excluded.session_version,
                company_name=excluded.company_name,
                industry=excluded.industry,
                industry_fallback=excluded.industry_fallback,
                vat_estimate_note=excluded.vat_estimate_note,
                ai_used=excluded.ai_used,
                findings_json=excluded.findings_json,
                created_at=excluded.created_at
            """,
            (
                session_version or "",
                company_name or "",
                industry or "",
                int(bool(industry_fallback)),
                vat_estimate_note or "",
                int(bool(ai_used)),
                json.dumps(findings, ensure_ascii=False),
                _now(),
            ),
        )


def load_diagnosis() -> dict | None:
    """读取当前诊断结果；无则返回 None。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM diagnosis_results WHERE id = 1").fetchone()
    if row is None:
        return None
    return {
        "session_version": row["session_version"] or "",
        "company_name": row["company_name"] or "",
        "industry": row["industry"] or "",
        "industry_fallback": bool(row["industry_fallback"]),
        "vat_estimate_note": row["vat_estimate_note"] or "",
        "ai_used": bool(row["ai_used"]),
        "findings": json.loads(row["findings_json"] or "[]"),
        "created_at": row["created_at"] or "",
    }


def clear_diagnosis_db() -> None:
    """清空诊断结果表。"""
    init_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM diagnosis_results WHERE id = 1")


# ── 互动会话持久化（绑定 session_version）──────────────────────────────

def save_interaction_session(
    session_version: str,
    state: str,
    decisions: list,
    strategy_notes: list,
    draft2: list,
    feasibility_score: float,
    feasibility_breakdown: list,
    ai_fallback_message: str,
    user_confirmed: bool,
) -> None:
    """写入当前互动会话（单行 id=1）。"""
    init_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO interaction_sessions (
                id, session_version, state, decisions_json, strategy_notes_json,
                draft2_json, feasibility_score, feasibility_breakdown_json,
                ai_fallback_message, user_confirmed, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                session_version=excluded.session_version,
                state=excluded.state,
                decisions_json=excluded.decisions_json,
                strategy_notes_json=excluded.strategy_notes_json,
                draft2_json=excluded.draft2_json,
                feasibility_score=excluded.feasibility_score,
                feasibility_breakdown_json=excluded.feasibility_breakdown_json,
                ai_fallback_message=excluded.ai_fallback_message,
                user_confirmed=excluded.user_confirmed,
                updated_at=excluded.updated_at
            """,
            (
                session_version or "",
                state or "",
                json.dumps(decisions, ensure_ascii=False),
                json.dumps(strategy_notes, ensure_ascii=False),
                json.dumps(draft2, ensure_ascii=False),
                float(feasibility_score or 0.0),
                json.dumps(feasibility_breakdown, ensure_ascii=False),
                ai_fallback_message or "",
                int(bool(user_confirmed)),
                _now(),
            ),
        )


def load_interaction_session() -> dict | None:
    """读取当前互动会话；无则返回 None。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM interaction_sessions WHERE id = 1").fetchone()
    if row is None:
        return None
    return {
        "session_version": row["session_version"] or "",
        "state": row["state"] or "",
        "decisions": json.loads(row["decisions_json"] or "[]"),
        "strategy_notes": json.loads(row["strategy_notes_json"] or "[]"),
        "draft2": json.loads(row["draft2_json"] or "[]"),
        "feasibility_score": row["feasibility_score"] or 100.0,
        "feasibility_breakdown": json.loads(row["feasibility_breakdown_json"] or "[]"),
        "ai_fallback_message": row["ai_fallback_message"] or "",
        "user_confirmed": bool(row["user_confirmed"]),
        "updated_at": row["updated_at"] or "",
    }


def clear_interaction_db() -> None:
    """清空互动会话表。"""
    init_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM interaction_sessions WHERE id = 1")


# ── 上传工作区退休队列 ────────────────────────────────────────────────

def queue_workspace_retirement(
    workspace_path: str, batch_path: str, reason: str
) -> int:
    """登记一个待回收批次；同一路径重复登记时复用原记录。"""
    init_db()
    now = _now()
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO workspace_retirements (
                workspace_path, batch_path, reason, status, attempts,
                last_error, created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', 0, '', ?, ?)
            ON CONFLICT(batch_path) DO UPDATE SET
                workspace_path=excluded.workspace_path,
                reason=excluded.reason,
                updated_at=excluded.updated_at
            """,
            (workspace_path, batch_path, reason, now, now),
        )
        row = conn.execute(
            "SELECT id FROM workspace_retirements WHERE batch_path = ?", (batch_path,)
        ).fetchone()
    return int(row["id"])


def list_workspace_retirements() -> list[dict]:
    """返回全部待回收/失败记录，供重试、诊断和测试审计。"""
    init_db()
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, workspace_path, batch_path, reason, status, attempts,
                   last_error, created_at, updated_at
            FROM workspace_retirements
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def fail_workspace_retirement(retirement_id: int, error: str) -> None:
    """记录一次失败尝试，保留记录供后续重试。"""
    init_db()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE workspace_retirements
            SET status = 'failed', attempts = attempts + 1,
                last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (error, _now(), retirement_id),
        )


def delete_workspace_retirement(retirement_id: int) -> None:
    """批次已删除或已不存在时移除退休记录。"""
    init_db()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM workspace_retirements WHERE id = ?", (retirement_id,))


# ── AI 报告管理 ───────────────────────────────────────────────────────

_TERMINAL_STATUSES = {"completed", "failed"}


def save_report(
    kind: str,
    title: str,
    content: str,
    job_id: str = "",
    session_version: str = "",
    model: str = "",
    page_coverage: dict | None = None,
    conflict_count: int = 0,
    report_type: str = "",
    attempted_model: str = "",
    fallback: bool = False,
    fallback_reason_code: str = "",
    blank_pages: dict | None = None,
) -> int:
    """保存 AI 报告，返回 id。job_id 关联后台任务，便于按任务追溯报告。"""
    init_db()
    with _lock, _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ai_reports (
                kind, title, content, created_at, job_id, session_version, model,
                attempted_model, report_type, fallback, fallback_reason_code,
                page_coverage_json, blank_pages_json, conflict_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                title,
                content,
                _now(),
                job_id or "",
                session_version or "",
                model or "",
                attempted_model or "",
                report_type or "",
                int(bool(fallback)),
                fallback_reason_code or "",
                json.dumps(page_coverage or {}, ensure_ascii=False),
                json.dumps(blank_pages or {}, ensure_ascii=False),
                int(conflict_count or 0),
            ),
        )
        return cur.lastrowid


def list_reports_for_job(job_id: str) -> list[dict]:
    """按任务 id 列出已保存报告（不含 content）。"""
    init_db()
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, kind, title, created_at, job_id, session_version, model,
                   attempted_model, report_type, fallback, fallback_reason_code,
                   page_coverage_json, blank_pages_json, conflict_count
            FROM ai_reports
            WHERE job_id = ?
            ORDER BY id DESC
            """,
            (job_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_reports() -> list:
    """列出所有 AI 报告（不含 content，用于列表展示）。"""
    init_db()
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, title, created_at FROM ai_reports ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_report(report_id: int) -> dict | None:
    """按 id 读取报告详情。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM ai_reports WHERE id = ?", (report_id,)).fetchone()
    return dict(row) if row else None


def delete_report(report_id: int) -> bool:
    """删除报告，返回是否删除了行。"""
    init_db()
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM ai_reports WHERE id = ?", (report_id,))
        return cur.rowcount > 0


# ── 后台报告任务 ──────────────────────────────────────────────────────

_JOB_COLUMNS = (
    "id, session_version, status, stage, current, total, message, "
    "markdown, error, error_code, report_id, report_type, model, attempted_model, "
    "fallback, fallback_reason_code, page_coverage_json, blank_pages_json, "
    "conflict_count, created_at, started_at, finished_at"
)


def _row_to_job(row: sqlite3.Row) -> dict:
    job = dict(row)
    job["job_id"] = job.pop("id")
    job["progress"] = {"current": int(job.pop("current") or 0), "total": int(job.pop("total") or 0)}
    job["fallback"] = bool(job.get("fallback"))
    job["page_coverage"] = json.loads(job.pop("page_coverage_json") or "{}")
    job["blank_pages"] = json.loads(job.pop("blank_pages_json") or "{}")
    return job


class JobCaptureError(ValueError):
    """创建 job 时当前会话不满足冻结条件。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CommitOutcome:
    status: str
    report_id: int | None = None


def _canonical_snapshot(row: sqlite3.Row) -> str:
    data = json.loads(row["data_json"] or "{}")
    sources = json.loads(row["source_files"] or "[]")
    snapshot = {
        "session_version": row["session_version"] or "",
        "company_name": row["company_name"] or "",
        "industry": row["industry"] or "",
        "years": sorted({int(year) for year in json.loads(row["years"] or "[]")}),
        "financial_data_json": json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        "sources": [
            {
                "path": str(source.get("path") or ""),
                "name": str(source.get("name") or source.get("source_file") or ""),
                "sha256": str(source.get("sha256") or ""),
                "size": int(source.get("size") or 0),
                "report_year": int(source.get("report_year") or 0),
                "page_count": int(source.get("page_count") or 0),
            }
            for source in sources
            if isinstance(source, dict)
        ],
    }
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot_digest(snapshot_json: str) -> str:
    return hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()


def capture_session_and_create_job(
    job_id: str,
    expected_session_version: str | None,
    owner_token: str,
) -> tuple[str, bool]:
    """在一个写事务内冻结 SQLite 当前会话并创建或复用 active job。"""
    init_db()
    with _lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM session WHERE id = 1").fetchone()
        if row is None or not (row["session_version"] or ""):
            raise JobCaptureError("EMPTY_SESSION", "尚未导入财报，请先导入后再生成报告")
        current_version = row["session_version"] or ""
        if expected_session_version and expected_session_version != current_version:
            raise JobCaptureError("SESSION_CHANGED", "会话已变化，请重新发起报告任务")
        active = conn.execute(
            """
            SELECT id FROM ai_report_jobs
            WHERE session_version = ? AND status IN ('queued', 'running')
            ORDER BY created_at DESC LIMIT 1
            """,
            (current_version,),
        ).fetchone()
        if active is not None:
            return str(active["id"]), False
        recent = conn.execute(
            """
            SELECT id FROM ai_report_jobs
            WHERE session_version = ? AND status = 'completed' AND dedupe_until >= ?
            ORDER BY dedupe_until DESC LIMIT 1
            """,
            (current_version, time.time()),
        ).fetchone()
        if recent is not None:
            return str(recent["id"]), False
        snapshot_json = _canonical_snapshot(row)
        input_digest = _snapshot_digest(snapshot_json)
        conn.execute(
            """
            INSERT INTO ai_report_jobs (
                id, session_version, status, stage, current, total, message,
                markdown, error, created_at, started_at, finished_at,
                snapshot_json, owner_token, input_digest, dedupe_until
            ) VALUES (?, ?, 'queued', '', 0, 0, '', '', '', ?, '', '', ?, ?, ?, ?)
            """,
            (
                job_id,
                current_version,
                _now(),
                snapshot_json,
                owner_token,
                input_digest,
                time.time() + _START_DEDUPE_SECONDS,
            ),
        )
    return job_id, True


def load_job_input_snapshot(job_id: str):
    """读取 worker 私有冻结输入；该数据永不进入公开 job SELECT。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT snapshot_json FROM ai_report_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if row is None:
        raise KeyError(job_id)
    raw = json.loads(row["snapshot_json"] or "{}")
    if not raw:
        raise JobCaptureError("SNAPSHOT_MISSING", "任务输入快照缺失")
    pipeline = __import__(
        "web_backend.CO_ai_report_pipeline_WB-CO-TR-20260807113737",
        fromlist=["JobInputSnapshot"],
    )
    sources = tuple(pipeline.SourceFileSnapshot(**source) for source in raw["sources"])
    return pipeline.JobInputSnapshot(
        session_version=raw["session_version"],
        company_name=raw["company_name"],
        industry=raw["industry"],
        years=tuple(raw["years"]),
        financial_data_json=raw["financial_data_json"],
        sources=sources,
    )


def create_job(job_id: str, session_version: str) -> None:
    """新建 queued 任务。同版本已有 queued/running 时拒绝创建。"""
    init_db()
    with _lock, _connect() as conn:
        active = conn.execute(
            """
            SELECT id FROM ai_report_jobs
            WHERE session_version = ? AND status IN ('queued', 'running')
            """,
            (session_version,),
        ).fetchone()
        if active is not None:
            raise ValueError("该会话已有进行中的报告任务")
        conn.execute(
            """
            INSERT INTO ai_report_jobs (
                id, session_version, status, stage, current, total, message,
                markdown, error, created_at, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                session_version,
                "queued",
                "",
                0,
                0,
                "",
                "",
                "",
                _now(),
                "",
                "",
            ),
        )


def _transition(
    job_id: str,
    new_status: str,
    *,
    markdown: str = "",
    error: str = "",
    stage: str | None = None,
    current: int | None = None,
    total: int | None = None,
    message: str | None = None,
) -> bool:
    """把任务从 queued/running 迁移到目标状态；终态不可再变。"""
    if new_status not in {"queued", "running", "completed", "failed"}:
        raise ValueError(f"非法任务状态：{new_status}")
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT status FROM ai_report_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return False
        if new_status == "queued":
            return row["status"] == "queued"
        if new_status == "running":
            allowed = row["status"] == "queued"
        else:
            allowed = row["status"] in {"queued", "running"}
        if not allowed:
            return False
        sets = ["status = ?"]
        params: list = [new_status]
        if new_status == "running" and not row["status"] == "running":
            sets.append("started_at = ?")
            params.append(_now())
        if new_status in _TERMINAL_STATUSES:
            sets.append("finished_at = ?")
            params.append(_now())
        if new_status == "completed":
            sets.append("markdown = ?")
            params.append(markdown)
        if new_status == "failed":
            sets.append("error = ?")
            params.append(error)
        for column, value in (
            ("stage", stage),
            ("current", current),
            ("total", total),
            ("message", message),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        params.append(job_id)
        conn.execute(f"UPDATE ai_report_jobs SET {', '.join(sets)} WHERE id = ?", params)
    return True


def update_job(
    job_id: str,
    *,
    stage: str | None = None,
    current: int | None = None,
    total: int | None = None,
    message: str | None = None,
) -> bool:
    """进行中任务更新进度；终态任务不可更新。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT status, current, total FROM ai_report_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or row["status"] in _TERMINAL_STATUSES:
            return False
        stored_current = int(row["current"] or 0)
        stored_total = int(row["total"] or 0)
        next_current = stored_current if current is None else int(current)
        next_total = stored_total if total is None else int(total)
        if (
            next_current < stored_current
            or next_current < 0
            or next_total < 0
            or (stored_total > 0 and next_total != stored_total)
            or (next_total == 0 and next_current != 0)
            or (next_total > 0 and next_current > next_total)
        ):
            return False
        sets: list[str] = []
        params: list = []
        for column, value in (
            ("stage", stage),
            ("current", current),
            ("total", total),
            ("message", message),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        if not sets:
            return True
        params.append(job_id)
        conn.execute(f"UPDATE ai_report_jobs SET {', '.join(sets)} WHERE id = ?", params)
    return True


def start_job(job_id: str) -> bool:
    """queued → running。"""
    return _transition(job_id, "running")


def complete_job(job_id: str, markdown: str) -> bool:
    """running/queued → completed。"""
    return _transition(job_id, "completed", markdown=markdown)


def fail_job(job_id: str, error: str) -> bool:
    """running/queued → failed。"""
    return _transition(job_id, "failed", error=error)


def get_job(job_id: str) -> dict | None:
    """读取任务快照；不存在返回 None。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM ai_report_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return _row_to_job(row) if row else None


def get_active_job(session_version: str) -> dict | None:
    """返回该会话版本的进行中任务（queued/running）；无则 None。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            f"SELECT {_JOB_COLUMNS} FROM ai_report_jobs "
            "WHERE session_version = ? AND status IN ('queued', 'running') "
            "ORDER BY created_at DESC LIMIT 1",
            (session_version,),
        ).fetchone()
    return _row_to_job(row) if row else None


def has_active_jobs() -> bool:
    """任意会话存在 queued/running 任务时返回 True。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM ai_report_jobs "
            "WHERE status IN ('queued', 'running') LIMIT 1"
        ).fetchone()
    return row is not None


def job_continue_status(job_id: str, expected_session_version: str) -> str:
    """阶段边界门禁：确认 job 仍 running 且 SQLite 当前会话未变化。"""
    init_db()
    with _lock, _connect() as conn:
        job = conn.execute(
            "SELECT status, session_version, input_digest FROM ai_report_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if (
            job is None
            or job["status"] != "running"
            or job["session_version"] != expected_session_version
        ):
            return "job_stopped"
        current = conn.execute(
            "SELECT * FROM session WHERE id = 1"
        ).fetchone()
    if (
        current is None
        or current["session_version"] != expected_session_version
        or not job["input_digest"]
        or _snapshot_digest(_canonical_snapshot(current)) != job["input_digest"]
    ):
        return "session_changed"
    return "ok"


def active_job_source_paths() -> set[str]:
    """返回 queued/running job 私有快照中的来源路径，供工作区租约保护。"""
    init_db()
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT snapshot_json FROM ai_report_jobs "
            "WHERE status IN ('queued', 'running')"
        ).fetchall()
    paths: set[str] = set()
    for row in rows:
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        for source in snapshot.get("sources") or []:
            path = source.get("path") if isinstance(source, dict) else None
            if path:
                paths.add(str(path))
    return paths


def _result_value(result, name: str, default=None):
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _result_metadata_valid(result) -> bool:
    report_type = str(_result_value(result, "report_type", ""))
    markdown = str(_result_value(result, "markdown", ""))
    model = str(_result_value(result, "model", ""))
    fallback = bool(_result_value(result, "fallback", False))
    reason = str(_result_value(result, "fallback_reason_code", ""))
    coverage = _result_value(result, "page_coverage", {}) or {}
    blanks = _result_value(result, "blank_pages", {}) or {}
    if not markdown.strip() or not isinstance(coverage, dict) or not isinstance(blanks, dict):
        return False
    if report_type == "ai_full":
        if fallback or not model or reason or not coverage:
            return False
        return all(
            isinstance(value, (list, tuple))
            and len(value) == 2
            and int(value[0]) > 0
            and int(value[0]) == int(value[1])
            for value in coverage.values()
        )
    if report_type == "rules_quick":
        return fallback and not model and bool(reason)
    return False


def _complete_job_in_transaction(
    conn: sqlite3.Connection,
    job_id: str,
    expected_session_version: str,
    report_id: int,
    result,
) -> None:
    """原子提交的条件终态更新；独立函数便于 fault injection。"""
    page_coverage = _result_value(result, "page_coverage", {}) or {}
    blank_pages = _result_value(result, "blank_pages", {}) or {}
    cursor = conn.execute(
        """
        UPDATE ai_report_jobs
        SET status = 'completed', finished_at = ?, markdown = ?, report_id = ?,
            report_type = ?, model = ?, attempted_model = ?, fallback = ?,
            fallback_reason_code = ?, page_coverage_json = ?, blank_pages_json = ?,
            conflict_count = ?, error = '', error_code = ''
        WHERE id = ? AND status = 'running' AND session_version = ?
        """,
        (
            _now(),
            str(_result_value(result, "markdown", "")),
            int(report_id),
            str(_result_value(result, "report_type", "")),
            str(_result_value(result, "model", "")),
            str(_result_value(result, "attempted_model", "")),
            int(bool(_result_value(result, "fallback", False))),
            str(_result_value(result, "fallback_reason_code", "")),
            json.dumps(page_coverage, ensure_ascii=False),
            json.dumps(blank_pages, ensure_ascii=False),
            int(_result_value(result, "conflict_count", 0) or 0),
            job_id,
            expected_session_version,
        ),
    )
    if cursor.rowcount != 1:
        raise sqlite3.IntegrityError("job terminal update rejected")


def commit_report_and_complete_job(
    job_id: str,
    expected_session_version: str,
    title: str,
    result,
) -> CommitOutcome:
    """同一 BEGIN IMMEDIATE 事务内写报告并将 running job 标为完成。"""
    if not _result_metadata_valid(result):
        return CommitOutcome("invalid_result")
    init_db()
    with _lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job = conn.execute(
            "SELECT status, session_version, input_digest FROM ai_report_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if (
            job is None
            or job["status"] != "running"
            or job["session_version"] != expected_session_version
        ):
            return CommitOutcome("invalid_state")
        current = conn.execute(
            "SELECT * FROM session WHERE id = 1"
        ).fetchone()
        if (
            current is None
            or current["session_version"] != expected_session_version
            or not job["input_digest"]
            or _snapshot_digest(_canonical_snapshot(current)) != job["input_digest"]
        ):
            return CommitOutcome("session_changed")

        page_coverage = _result_value(result, "page_coverage", {}) or {}
        blank_pages = _result_value(result, "blank_pages", {}) or {}
        cursor = conn.execute(
            """
            INSERT INTO ai_reports (
                kind, title, content, created_at, job_id, session_version, model,
                attempted_model, report_type, fallback, fallback_reason_code,
                page_coverage_json, blank_pages_json, conflict_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "years_summary",
                title,
                str(_result_value(result, "markdown", "")),
                _now(),
                job_id,
                expected_session_version,
                str(_result_value(result, "model", "")),
                str(_result_value(result, "attempted_model", "")),
                str(_result_value(result, "report_type", "")),
                int(bool(_result_value(result, "fallback", False))),
                str(_result_value(result, "fallback_reason_code", "")),
                json.dumps(page_coverage, ensure_ascii=False),
                json.dumps(blank_pages, ensure_ascii=False),
                int(_result_value(result, "conflict_count", 0) or 0),
            ),
        )
        report_id = int(cursor.lastrowid)
        _complete_job_in_transaction(
            conn, job_id, expected_session_version, report_id, result
        )
    return CommitOutcome("completed", report_id)


def fail_job_safe(job_id: str, code: str, public_message: str) -> bool:
    """仅保存结构化安全 code/message，不接受内部异常文本。"""
    init_db()
    with _lock, _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE ai_report_jobs
            SET status = 'failed', finished_at = ?, error_code = ?, error = ?
            WHERE id = ? AND status IN ('queued', 'running')
            """,
            (_now(), code, public_message, job_id),
        )
    return cursor.rowcount == 1


def recover_orphaned_jobs(
    current_owner_token: str, *, lease_verified: bool = False
) -> int:
    """启动时把其他进程 owner 的 queued/running job 安全终态化。"""
    if not lease_verified:
        return 0
    init_db()
    with _lock, _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE ai_report_jobs
            SET status = 'failed', finished_at = ?, error_code = 'PROCESS_RESTARTED',
                error = '应用已重启，请重新发起报告任务'
            WHERE status IN ('queued', 'running')
              AND COALESCE(owner_token, '') <> ?
            """,
            (_now(), current_owner_token),
        )
    return int(cursor.rowcount)
