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


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """列存在性检查：pragma_table_info 表值函数支持参数绑定，无动态 SQL。"""
    return (
        conn.execute(
            "SELECT 1 FROM pragma_table_info(?) WHERE name = ? LIMIT 1",
            (table, column),
        ).fetchone()
        is not None
    )


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """为已有安装平滑补齐缺失列。

    所有 ALTER 均为内联字面量 SQL（表名/列名/DDL 为固定迁移清单，
    与旧版 _ensure_column 的 f-string 拼接行为等价，但无动态构造）。
    """
    if not _has_column(conn, "session", "source_files"):
        conn.execute("ALTER TABLE session ADD COLUMN source_files TEXT DEFAULT '[]'")
    if not _has_column(conn, "session", "saved_previews"):
        conn.execute("ALTER TABLE session ADD COLUMN saved_previews TEXT DEFAULT '[]'")
    if not _has_column(conn, "session", "session_version"):
        conn.execute("ALTER TABLE session ADD COLUMN session_version TEXT DEFAULT ''")
    if not _has_column(conn, "ai_reports", "job_id"):
        conn.execute("ALTER TABLE ai_reports ADD COLUMN job_id TEXT DEFAULT ''")
    if not _has_column(conn, "ai_reports", "session_version"):
        conn.execute("ALTER TABLE ai_reports ADD COLUMN session_version TEXT DEFAULT ''")
    if not _has_column(conn, "ai_reports", "model"):
        conn.execute("ALTER TABLE ai_reports ADD COLUMN model TEXT DEFAULT ''")
    if not _has_column(conn, "ai_reports", "attempted_model"):
        conn.execute("ALTER TABLE ai_reports ADD COLUMN attempted_model TEXT DEFAULT ''")
    if not _has_column(conn, "ai_reports", "report_type"):
        conn.execute("ALTER TABLE ai_reports ADD COLUMN report_type TEXT DEFAULT ''")
    if not _has_column(conn, "ai_reports", "fallback"):
        conn.execute("ALTER TABLE ai_reports ADD COLUMN fallback INTEGER DEFAULT 0")
    if not _has_column(conn, "ai_reports", "fallback_reason_code"):
        conn.execute("ALTER TABLE ai_reports ADD COLUMN fallback_reason_code TEXT DEFAULT ''")
    if not _has_column(conn, "ai_reports", "page_coverage_json"):
        conn.execute("ALTER TABLE ai_reports ADD COLUMN page_coverage_json TEXT DEFAULT '{}'")
    if not _has_column(conn, "ai_reports", "blank_pages_json"):
        conn.execute("ALTER TABLE ai_reports ADD COLUMN blank_pages_json TEXT DEFAULT '{}'")
    if not _has_column(conn, "ai_reports", "conflict_count"):
        conn.execute("ALTER TABLE ai_reports ADD COLUMN conflict_count INTEGER DEFAULT 0")
    if not _has_column(conn, "ai_report_jobs", "snapshot_json"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN snapshot_json TEXT DEFAULT '{}'")
    if not _has_column(conn, "ai_report_jobs", "owner_token"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN owner_token TEXT DEFAULT ''")
    if not _has_column(conn, "ai_report_jobs", "input_digest"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN input_digest TEXT DEFAULT ''")
    if not _has_column(conn, "ai_report_jobs", "error_code"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN error_code TEXT DEFAULT ''")
    if not _has_column(conn, "ai_report_jobs", "report_id"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN report_id INTEGER DEFAULT 0")
    if not _has_column(conn, "ai_report_jobs", "report_type"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN report_type TEXT DEFAULT ''")
    if not _has_column(conn, "ai_report_jobs", "model"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN model TEXT DEFAULT ''")
    if not _has_column(conn, "ai_report_jobs", "attempted_model"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN attempted_model TEXT DEFAULT ''")
    if not _has_column(conn, "ai_report_jobs", "fallback"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN fallback INTEGER DEFAULT 0")
    if not _has_column(conn, "ai_report_jobs", "fallback_reason_code"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN fallback_reason_code TEXT DEFAULT ''")
    if not _has_column(conn, "ai_report_jobs", "page_coverage_json"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN page_coverage_json TEXT DEFAULT '{}'")
    if not _has_column(conn, "ai_report_jobs", "blank_pages_json"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN blank_pages_json TEXT DEFAULT '{}'")
    if not _has_column(conn, "ai_report_jobs", "conflict_count"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN conflict_count INTEGER DEFAULT 0")
    if not _has_column(conn, "ai_report_jobs", "dedupe_until"):
        conn.execute("ALTER TABLE ai_report_jobs ADD COLUMN dedupe_until REAL DEFAULT 0")
    if not _has_column(conn, "import_history", "is_active"):
        conn.execute("ALTER TABLE import_history ADD COLUMN is_active INTEGER DEFAULT 0")
    if not _has_column(conn, "import_history", "diagnosis_json"):
        conn.execute("ALTER TABLE import_history ADD COLUMN diagnosis_json TEXT DEFAULT ''")
    if not _has_column(conn, "import_history", "interaction_json"):
        conn.execute("ALTER TABLE import_history ADD COLUMN interaction_json TEXT DEFAULT ''")


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
            CREATE TABLE IF NOT EXISTS import_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT '',
                company_name TEXT NOT NULL DEFAULT '',
                industry TEXT NOT NULL DEFAULT '',
                years TEXT NOT NULL DEFAULT '[]',
                file_count INTEGER NOT NULL DEFAULT 0,
                session_version TEXT NOT NULL DEFAULT '',
                data_json TEXT NOT NULL DEFAULT '{}',
                ocr_texts TEXT NOT NULL DEFAULT '[]',
                source_files TEXT NOT NULL DEFAULT '[]',
                saved_previews TEXT NOT NULL DEFAULT '[]',
                is_active INTEGER NOT NULL DEFAULT 0,
                diagnosis_json TEXT NOT NULL DEFAULT '',
                interaction_json TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS monthly_budget_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_version TEXT NOT NULL UNIQUE,
                stage TEXT NOT NULL,
                advice_fingerprint TEXT NOT NULL DEFAULT '',
                draft_job_id TEXT DEFAULT '',
                draft_path TEXT DEFAULT '',
                plan_snapshot TEXT DEFAULT '',
                draft_meta TEXT DEFAULT '',
                question_source TEXT DEFAULT '',
                questions TEXT DEFAULT '',
                answers TEXT DEFAULT '',
                split_job_id TEXT DEFAULT '',
                split_mode TEXT DEFAULT '',
                split_result TEXT DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0
            );
            """
        )
        _ensure_columns(conn)
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


# ── 导入历史（每次导入存档一份快照，供「导入记录卡片」快速载入）──────────
# 上限：最多保留最近 50 条，超出自动淘汰最旧记录。

_IMPORT_HISTORY_LIMIT = 50


def _history_fingerprint(data_json: dict, source_files: list) -> str:
    """导入内容指纹：数据 JSON + 文件名/sha256（忽略工作区随机路径）。

    parsed_meta 中含 import-{uuid} 工作区路径等易变字段，参与哈希前剥离；
    与最近一条指纹相同视为重复导入，不再新增卡片。
    """
    payload = json.loads(json.dumps(data_json or {}, ensure_ascii=False))
    meta = payload.get("parsed_meta")
    if isinstance(meta, dict):
        for volatile_key in ("excel_path", "source_path", "pdf_path"):
            meta.pop(volatile_key, None)
    files = sorted(
        (str(f.get("name") or ""), str(f.get("sha256") or ""))
        for f in source_files or []
        if isinstance(f, dict)
    )
    canonical = json.dumps(
        {"data": payload, "files": files},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def save_import_history(
    company_name: str,
    industry: str,
    years: list,
    file_count: int,
    session_version: str,
    data_json: dict,
    ocr_texts: list,
    source_files: list,
    saved_previews: list,
) -> int | None:
    """追加一条导入历史；与最近一条内容指纹相同则跳过（去重）。

    返回新记录 id；重复导入同一批数据时返回 None。
    session_version 含工作区随机路径、不适合做去重键，故用内容指纹。
    """
    insert_sql = "INSERT INTO import_history (created_at, company_name, industry, years, file_count, session_version, data_json, ocr_texts, source_files, saved_previews) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    init_db()
    with _lock, _connect() as conn:
        latest = conn.execute(
            "SELECT id, data_json, source_files FROM import_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest is not None:
            try:
                prev = _history_fingerprint(
                    json.loads(latest["data_json"] or "{}"),
                    json.loads(latest["source_files"] or "[]"),
                )
            except Exception:
                prev = ""
            if prev == _history_fingerprint(data_json, source_files):
                return None
        cursor = conn.execute(
            insert_sql,
            (
                _now(),
                company_name or "",
                industry or "",
                json.dumps(years or [], ensure_ascii=False),
                int(file_count or 0),
                session_version or "",
                json.dumps(data_json or {}, ensure_ascii=False),
                json.dumps(ocr_texts or [], ensure_ascii=False),
                json.dumps(source_files or [], ensure_ascii=False),
                json.dumps(saved_previews or [], ensure_ascii=False),
            ),
        )
        new_id = int(cursor.lastrowid or 0)
        conn.execute(
            "DELETE FROM import_history WHERE id NOT IN (SELECT id FROM import_history ORDER BY id DESC LIMIT ?)",
            (_IMPORT_HISTORY_LIMIT,),
        )
        return new_id


def list_import_history(limit: int = 50) -> list[dict]:
    """导入历史轻量列表（不含 data_json 等大字段），新→旧。"""
    list_sql = "SELECT id, created_at, company_name, industry, years, file_count, session_version FROM import_history ORDER BY id DESC LIMIT ?"
    init_db()
    with _lock, _connect() as conn:
        rows = conn.execute(list_sql, (int(limit or 50),)).fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "id": row["id"],
                "created_at": row["created_at"] or "",
                "company_name": row["company_name"] or "",
                "industry": row["industry"] or "",
                "years": json.loads(row["years"] or "[]"),
                "file_count": int(row["file_count"] or 0),
                "session_version": row["session_version"] or "",
            }
        )
    return out


def get_import_history_entry(entry_id: int) -> dict | None:
    """读取单条导入历史完整快照（含 data_json / ocr / sources / previews）。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM import_history WHERE id = ?", (int(entry_id),)
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"] or "",
        "company_name": row["company_name"] or "",
        "industry": row["industry"] or "",
        "years": json.loads(row["years"] or "[]"),
        "file_count": int(row["file_count"] or 0),
        "session_version": row["session_version"] or "",
        "data_json": json.loads(row["data_json"] or "{}"),
        "ocr_texts": json.loads(row["ocr_texts"] or "[]"),
        "source_files": json.loads(row["source_files"] or "[]"),
        "saved_previews": json.loads(row["saved_previews"] or "[]"),
        "is_active": bool(row["is_active"]),
        "diagnosis_snapshot": _parse_progress_snapshot(row["diagnosis_json"]),
        "interaction_snapshot": _parse_progress_snapshot(row["interaction_json"]),
    }


def _parse_progress_snapshot(raw) -> dict | None:
    """把历史行里的进度快照 JSON 安全解析为 dict；空/损坏返回 None。"""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def delete_import_history(entry_id: int) -> bool:
    """删除一条导入历史。"""
    init_db()
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM import_history WHERE id = ?", (int(entry_id),)
        )
        return cursor.rowcount > 0


def set_active_import_history(entry_id: int | None = None) -> None:
    """标记当前会话对应的激活历史行。

    - entry_id=None：激活最新一行（新导入去重复用旧行时使用）
    - entry_id=0：清除全部激活（会话清空时使用）
    - 其他：激活指定行（历史载入时使用）
    """
    init_db()
    with _lock, _connect() as conn:
        conn.execute("UPDATE import_history SET is_active = 0")
        if entry_id == 0:
            return
        if entry_id is None:
            conn.execute(
                "UPDATE import_history SET is_active = 1 WHERE id = (SELECT id FROM import_history ORDER BY id DESC LIMIT 1)"
            )
        else:
            conn.execute(
                "UPDATE import_history SET is_active = 1 WHERE id = ?", (int(entry_id),)
            )


def snapshot_case_progress() -> None:
    """把当前诊断/互动进度快照进激活的导入历史行（无激活行则跳过）。

    供「点击导入记录卡片完整载入案例」使用：诊断/互动每次保存后同步快照，
    载入时据此恢复，各页面状态随载入联动。存解析后的载荷（findings 为
    列表而非 *_json 字符串），与 load 路由的恢复读取口径一致。
    """
    init_db()
    with _lock, _connect() as conn:
        active = conn.execute(
            "SELECT id FROM import_history WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if active is None:
            return
        diag = conn.execute("SELECT * FROM diagnosis_results WHERE id = 1").fetchone()
        inter = conn.execute("SELECT * FROM interaction_sessions WHERE id = 1").fetchone()

        diag_payload = None
        if diag is not None:
            diag_payload = {
                "company_name": diag["company_name"] or "",
                "industry": diag["industry"] or "",
                "industry_fallback": bool(diag["industry_fallback"]),
                "vat_estimate_note": diag["vat_estimate_note"] or "",
                "ai_used": bool(diag["ai_used"]),
                "findings": json.loads(diag["findings_json"] or "[]"),
            }
        inter_payload = None
        if inter is not None:
            inter_payload = {
                "state": inter["state"] or "",
                "decisions": json.loads(inter["decisions_json"] or "[]"),
                "strategy_notes": json.loads(inter["strategy_notes_json"] or "[]"),
                "draft2": json.loads(inter["draft2_json"] or "[]"),
                "feasibility_score": float(inter["feasibility_score"] or 100.0),
                "feasibility_breakdown": json.loads(inter["feasibility_breakdown_json"] or "[]"),
                "ai_fallback_message": inter["ai_fallback_message"] or "",
                "user_confirmed": bool(inter["user_confirmed"]),
            }
        conn.execute(
            "UPDATE import_history SET diagnosis_json = ?, interaction_json = ? WHERE id = ?",
            (
                json.dumps(diag_payload, ensure_ascii=False) if diag_payload else "",
                json.dumps(inter_payload, ensure_ascii=False) if inter_payload else "",
                active["id"],
            ),
        )


def find_report_for_version(session_version: str, kind: str = "years_summary") -> int | None:
    """查某会话版本最新一份指定类型的 AI 报告 id（无则 None）。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT id FROM ai_reports WHERE session_version = ? AND kind = ? ORDER BY id DESC LIMIT 1",
            (session_version or "", str(kind)),
        ).fetchone()
    return int(row["id"]) if row is not None else None


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
    """列出所有 AI 报告（不含 content，用于列表展示）。

    session_version 供前端反查「该报告属于哪个导入案例」，
    点击报告记录时可完整载入对应案例。
    """
    init_db()
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, title, created_at, session_version FROM ai_reports ORDER BY id DESC"
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


# ── 月度拆分流程状态（模块 A 二段式，每 session_version 一行） ────────────
# stage：draft|questions|answered|splitting|ready|failed|skipped
# plan_snapshot/questions/answers/split_result/draft_meta 存 JSON 字符串。

_MONTHLY_STATE_COLUMNS = (
    "stage", "advice_fingerprint", "draft_job_id", "draft_path",
    "plan_snapshot", "draft_meta", "question_source", "questions",
    "answers", "split_job_id", "split_mode", "split_result",
)
_MONTHLY_STATE_JSON_COLUMNS = {
    "plan_snapshot", "draft_meta", "questions", "answers", "split_result",
}


def upsert_monthly_state(session_version: str, **fields) -> None:
    """按 session_version UPSERT 月度流程状态行。

    JSON 列自动序列化；首次插入必须带 stage 与 advice_fingerprint
    （表上 NOT NULL），否则抛 ValueError。
    """
    cols = []
    params: list = []
    for key, value in fields.items():
        if key not in _MONTHLY_STATE_COLUMNS:
            continue
        if key in _MONTHLY_STATE_JSON_COLUMNS and value is not None and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        cols.append(key)
        params.append(value)
    if not cols:
        return
    version = str(session_version or "")
    init_db()
    now = time.time()
    with _lock, _connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM monthly_budget_state WHERE session_version = ?", (version,)
        ).fetchone()
        if existing is None and ("stage" not in cols or "advice_fingerprint" not in cols):
            raise ValueError("首次写入月度状态必须包含 stage 与 advice_fingerprint")
        colnames = ", ".join(cols)
        qs = ", ".join("?" for _ in cols)
        sets = ", ".join(f"{c} = excluded.{c}" for c in cols)
        conn.execute(
            f"""
            INSERT INTO monthly_budget_state (
                session_version, {colnames}, created_at, updated_at
            ) VALUES (?, {qs}, ?, ?)
            ON CONFLICT(session_version) DO UPDATE SET {sets}, updated_at = excluded.updated_at
            """,
            (version, *params, now, now),
        )


def get_monthly_state(session_version: str) -> dict | None:
    """读取月度流程状态；JSON 列解析为对象，无记录返回 None。"""
    init_db()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM monthly_budget_state WHERE session_version = ?",
            (str(session_version or ""),),
        ).fetchone()
    if row is None:
        return None
    state = dict(row)
    for col in _MONTHLY_STATE_JSON_COLUMNS:
        raw = state.get(col) or ""
        state[col] = json.loads(raw) if raw else None
    return state


def delete_monthly_state(session_version: str) -> bool:
    """删除月度流程状态行（勾选指纹变化/重新导入时重置）。"""
    init_db()
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM monthly_budget_state WHERE session_version = ?",
            (str(session_version or ""),),
        )
        return cursor.rowcount > 0
