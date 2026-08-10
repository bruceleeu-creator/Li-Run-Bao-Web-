"""利润宝 · 费用预算三表异步生成任务。

阶段：prepare → extract_top → extract_lines → write → completed|failed
DeepSeek 多片段提取顶部指标 + 分科目费用行，再规则补差额，最后写出标准三 Sheet。
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
budget_export = importlib.import_module("core.CO_budget_export_WB-CO-TR-20260810")
interaction = importlib.import_module("web_backend.CO_interaction_WB-CO-TR-20260805160732")

_lock = threading.Lock()
_jobs: dict[str, "BudgetJob"] = {}
_EXPORT_DIR = Path(__file__).resolve().parent / "workspaces" / "exports"


@dataclass
class BudgetJob:
    job_id: str
    session_version: str
    status: str = "queued"  # queued|running|completed|failed
    stage: str = "prepare"
    progress: int = 0  # 0-100
    message: str = ""
    error: str = ""
    path: str = ""
    filename: str = ""
    meta: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "filename": self.filename,
            "download_ready": self.status == "completed" and bool(self.path),
            "meta": {
                "top_method": self.meta.get("top_method"),
                "line_method": self.meta.get("line_method"),
                "filled_lines": self.meta.get("filled_lines"),
                "notes": self.meta.get("notes", [])[-8:],
            },
        }


def _safe_name(name: str) -> str:
    import re
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip()) or "企业"
    return cleaned[:40]


def _update(job: BudgetJob, **kwargs) -> None:
    with _lock:
        for k, v in kwargs.items():
            setattr(job, k, v)


def _structured_hints(data) -> dict:
    years = sorted(data.years or [])
    latest = years[-1] if years else 0
    prev = years[-2] if len(years) >= 2 else latest

    def amt(acc: str, y: int) -> float:
        v = (data.income_statement.get(acc) or {}).get(y)
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "budget_revenue": amt("营业收入", latest),
        "budget_cost": amt("营业成本", latest),
        "last_year_revenue": amt("营业收入", prev),
        "last_year_cost": amt("营业成本", prev),
        "selling_expense": amt("销售费用", latest),
        "admin_expense": amt("管理费用", latest),
        "rd_expense": amt("研发费用", latest),
        "finance_expense": amt("财务费用", latest),
        "prev_selling_expense": amt("销售费用", prev),
        "prev_admin_expense": amt("管理费用", prev),
        "prev_rd_expense": amt("研发费用", prev),
        "prev_finance_expense": amt("财务费用", prev),
        "years": years,
        "latest_year": latest,
        "prev_year": prev,
    }


def _run_job(job_id: str) -> None:
    job = _jobs.get(job_id)
    if job is None:
        return
    try:
        _update(job, status="running", stage="prepare", progress=5, message="准备会话与模板…")
        data = session.get_data()
        if data is None or job.session_version != session.get_version():
            raise RuntimeError("会话已变化或未导入财报")

        ocr_texts = list(session.get_ocr_texts() or [])
        ocr_blob = "\n\n".join(ocr_texts)
        hints = _structured_hints(data)

        # 互动会话（第三 Sheet）
        interactive_sess = None
        try:
            with interaction._lock:
                if (
                    interaction._sess is not None
                    and interaction._sess_session_version == job.session_version
                ):
                    interactive_sess = interaction._sess
        except Exception:
            interactive_sess = None

        _update(job, stage="extract_top", progress=20, message="DeepSeek 提取顶部营收/成本（多片段）…")
        top_indicators, top_err = ai_mod.extract_budget_indicators(
            ocr_blob, structured_hints=hints
        )
        period = None
        if top_indicators:
            period = top_indicators.get("_period") or {
                k: hints.get(k, 0)
                for k in (
                    "selling_expense", "admin_expense", "rd_expense", "finance_expense",
                    "prev_selling_expense", "prev_admin_expense", "prev_rd_expense",
                    "prev_finance_expense",
                )
            }
            _update(
                job,
                progress=40,
                message=f"顶部指标完成（营收 {top_indicators.get('budget_revenue', 0):,.0f}）",
            )
        else:
            top_indicators = {k: hints[k] for k in (
                "budget_revenue", "budget_cost", "last_year_revenue", "last_year_cost"
            )}
            period = {
                k: hints.get(k, 0)
                for k in (
                    "selling_expense", "admin_expense", "rd_expense", "finance_expense",
                    "prev_selling_expense", "prev_admin_expense", "prev_rd_expense",
                    "prev_finance_expense",
                )
            }
            _update(
                job,
                progress=40,
                message=f"顶部指标回退结构化（{top_err or '无 OCR/AI'}）",
            )

        _update(job, stage="extract_lines", progress=50, message="DeepSeek 分科目分配费用行…")
        plan_stub, _ = budget_export.build_budget_plan_from_session(data)
        catalog = [
            {
                "row": l.row,
                "subject": l.subject,
                "expense_name": l.expense_name,
                "invoice_name": l.invoice_name,
            }
            for l in plan_stub.lines
        ]
        line_allocations, line_err = ai_mod.extract_budget_expense_lines(
            ocr_blob,
            catalog,
            structured_facts=hints,
            period_totals=period,
        )
        if line_allocations:
            _update(
                job,
                progress=75,
                message=f"费用行 AI 分配 {len(line_allocations)} 条，正在写表…",
            )
        else:
            _update(
                job,
                progress=75,
                message=f"费用行 AI 未命中（{line_err or '空'}），规则补齐后写表…",
            )

        _update(job, stage="write", progress=85, message="写出费用预算三表…")
        _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        years = sorted(data.years or [])
        yspan = f"{years[0]}-{years[-1]}" if len(years) > 1 else (str(years[0]) if years else "")
        filename = f"{_safe_name(data.company_name)}{yspan}费用预算三表.xlsx"
        path = _EXPORT_DIR / filename

        out_path, meta = budget_export.export_budget_3sheet(
            data,
            str(path),
            ocr_texts=ocr_texts,
            interactive_session=interactive_sess,
            top_indicators=top_indicators,
            line_allocations=line_allocations or None,
            period_totals=period,
        )
        _update(
            job,
            status="completed",
            stage="completed",
            progress=100,
            message=f"完成：非空费用行 {meta.get('filled_lines', 0)}/84",
            path=out_path,
            filename=filename,
            meta=meta,
            finished_at=time.time(),
        )
    except Exception as e:
        logger.exception("预算三表异步任务失败 job=%s", job_id)
        _update(
            job,
            status="failed",
            stage="failed",
            progress=100,
            error=str(e) or type(e).__name__,
            message="生成失败",
            finished_at=time.time(),
        )


def start_budget_export_job() -> dict:
    data = session.get_data()
    if data is None:
        raise ValueError("尚未导入财报")
    version = session.get_version()
    job_id = f"budget-{uuid.uuid4().hex[:12]}"
    job = BudgetJob(job_id=job_id, session_version=version, message="已排队")
    with _lock:
        # 取消同会话旧 running 任务标记（新任务覆盖）
        for old in list(_jobs.values()):
            if (
                old.session_version == version
                and old.status in ("queued", "running")
                and old.job_id != job_id
            ):
                old.status = "failed"
                old.error = "已被新的预算导出任务替代"
                old.message = "已取消"
        _jobs[job_id] = job

    t = threading.Thread(target=_run_job, args=(job_id,), name=f"budget-export-{job_id}", daemon=True)
    t.start()
    return job.to_dict()


def get_budget_export_job(job_id: str) -> Optional[dict]:
    job = _jobs.get(job_id)
    return job.to_dict() if job else None


def get_active_budget_export_job() -> Optional[dict]:
    version = session.get_version()
    with _lock:
        candidates = [
            j
            for j in _jobs.values()
            if j.session_version == version and j.status in ("queued", "running", "completed")
        ]
    if not candidates:
        return None
    # 优先 running，否则最新
    running = [j for j in candidates if j.status == "running"]
    pick = max(running or candidates, key=lambda j: j.created_at)
    return pick.to_dict()


def get_budget_export_path(job_id: str) -> Optional[Path]:
    job = _jobs.get(job_id)
    if not job or job.status != "completed" or not job.path:
        return None
    p = Path(job.path)
    return p if p.exists() else None
