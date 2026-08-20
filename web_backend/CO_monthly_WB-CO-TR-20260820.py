"""利润宝 · 月度拆分流程路由（模块 A 二段式）。

流程状态机（SQLite monthly_budget_state，每 session_version 一行）：
  draft（第一稿生成中/就绪）→ questions（二轮提问）→ answered →
  splitting → ready（可导出含月度 Sheet）；failed / skipped 为旁路终态。

数字安全底线：AI 只出题/出权重，金额一律由 core.CO_monthly_split
确定性引擎计算并恒等校验（Σ月=年，校验不过不出结果）。
现有 POST /api/export/budget/jobs + /download 保留不动 = 跳过拆分导出旧版。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import threading
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
db = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")
ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
budget_job_mod = importlib.import_module("web_backend.CO_budget_export_job_WB-CO-TR-20260810")
monthly = importlib.import_module("core.CO_monthly_split_WB-CO-TR-20260820")

router = APIRouter(prefix="/api/export/budget", tags=["monthly-budget"])

_lock = threading.Lock()
_split_jobs: dict = {}  # 拆分任务内存态（结果持久化在 monthly_budget_state）
_DRAFT_WATCH_TIMEOUT = 900.0
_AI_SPLIT_ATTEMPTS = 3


class DraftJobIn(BaseModel):
    advice_items: list = []


class MonthlyAnswersIn(BaseModel):
    answers: list = []


# ── 工具 ────────────────────────────────────────────────────────────────

def _advice_fingerprint(advice_items) -> str:
    """勾选指纹：sha256(排序后的 {row, budget_amount} 列表)。

    对勾选顺序不敏感、对金额敏感；空勾选也有指纹（空串场景）。
    """
    pairs = []
    for it in advice_items or []:
        if not isinstance(it, dict) or not it.get("selected", True):
            continue
        try:
            pairs.append((int(it.get("row") or 0), round(float(it.get("budget_amount") or 0), 2)))
        except (TypeError, ValueError):
            continue
    pairs.sort()
    canonical = json.dumps(
        [{"row": r, "budget_amount": a} for r, a in pairs],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_version() -> str:
    data = session.get_data()
    if data is None:
        raise HTTPException(status_code=400, detail="尚未导入财报，请先导入")
    return session.get_version()


def _state_or_none(version: str) -> Optional[dict]:
    return db.get_monthly_state(version)


def _summary_from_state(state: dict) -> dict:
    meta = (state or {}).get("draft_meta") or {}
    return {
        "revenue": float(meta.get("revenue") or 0),
        "expense_total": float(meta.get("expense_total") or 0),
        "fee_rate": float(meta.get("fee_rate") or 0),
        "filled_lines": int(meta.get("filled_lines") or 0),
        "advice_applied": int(meta.get("advice_applied") or 0),
    }


def _budget_hints() -> dict:
    data = session.get_data()
    years = sorted(getattr(data, "years", None) or [])
    latest = years[-1] if years else 0
    return {
        "company_name": getattr(data, "company_name", "") or "",
        "industry": getattr(data, "industry", "") or "",
        "years": years,
        "budget_year": latest,
        "spring_month": monthly.spring_festival_month(latest),
    }


# ── 第一稿任务（复用现有预算导出管线，不触发下载） ────────────────────────

def _finalize_draft(job_id: str, version: str, fingerprint: str) -> bool:
    """任务终态时把快照/摘要写入 monthly_budget_state；未终态返回 False。

    守护线程与 GET 端点都会调用（惰性补写）——前端看到任务 completed 的
    那一刻，state 必然已含完整快照，消除轮询竞态。
    """
    full = budget_job_mod.get_budget_export_full(job_id)
    if full is None or full["status"] not in ("completed", "failed"):
        return False
    # 竞态防护（双重）：
    # ① 状态行已被更新的任务接管（指纹重置/重开第一稿）→ 丢弃迟到结果；
    # ② 流程已推进过 draft 阶段（questions/answered/splitting/ready）→
    #    迟到的 finalize 绝不把状态机拉回 draft（只进不退）。
    current = db.get_monthly_state(version)
    if current and current.get("draft_job_id") and current.get("draft_job_id") != job_id:
        return True
    if current and str(current.get("stage") or "") in ("questions", "answered", "splitting", "ready"):
        return True
    if full["status"] == "completed":
        meta = full.get("meta") or {}
        snapshot = {
            "rows": meta.get("plan_rows") or [],
            "top_summary": meta.get("top_summary") or {},
            "filled_lines": meta.get("filled_lines"),
            "advice_selected": meta.get("advice_selected"),
            "fee_rate": meta.get("fee_rate"),
            "notes": (meta.get("notes") or [])[-6:],
        }
        top = snapshot["top_summary"]
        summary = {
            "revenue": float(top.get("budget_revenue") or 0),
            "expense_total": float(meta.get("advice_budget_sum") or 0),
            "fee_rate": float(meta.get("fee_rate") or 0),
            "filled_lines": int(meta.get("filled_lines") or 0),
            "advice_applied": int(meta.get("advice_selected") or 0),
        }
        try:
            db.upsert_monthly_state(
                version,
                stage="draft",
                advice_fingerprint=fingerprint,
                draft_job_id=job_id,
                draft_path=str(full.get("path") or ""),
                plan_snapshot=snapshot,
                draft_meta=summary,
            )
        except Exception:
            logger.exception("月度状态写入失败 job=%s", job_id)
        return True
    try:
        db.upsert_monthly_state(
            version, stage="failed", advice_fingerprint=fingerprint, draft_job_id=job_id
        )
    except Exception:
        logger.exception("月度状态失败态写入失败 job=%s", job_id)
    return True


def _ensure_draft_finalized(version: str, state: Optional[dict]) -> dict:
    """GET 语义兜底：任务已完成但快照尚未落库时，同步补写后重读。"""
    if not state or not state.get("draft_job_id"):
        return state or {}
    if state.get("plan_snapshot") or state.get("stage") == "failed":
        return state
    full = budget_job_mod.get_budget_export_full(state["draft_job_id"])
    if full is None or full["status"] != "completed":
        return state
    _finalize_draft(state["draft_job_id"], version, state.get("advice_fingerprint") or "")
    refreshed = db.get_monthly_state(version)
    return refreshed or state


def _watch_draft(job_id: str, version: str, fingerprint: str) -> None:
    """守护线程：等第一稿任务终态落库（GET 端点另有惰性补写兜底）。"""
    deadline = time.time() + _DRAFT_WATCH_TIMEOUT
    while time.time() < deadline:
        if _finalize_draft(job_id, version, fingerprint):
            return
        time.sleep(0.5)


@router.post("/draft/jobs")
def start_budget_draft(body: DraftJobIn = DraftJobIn()) -> dict:
    """启动第一稿任务：复用现有管线，产出 xlsx 与行快照，不触发下载。"""
    version = _require_version()
    fingerprint = _advice_fingerprint(body.advice_items)
    state = _state_or_none(version)
    if state and state.get("advice_fingerprint") and state["advice_fingerprint"] != fingerprint:
        # 勾选集变化：第一稿与拆分状态作废，重置
        db.delete_monthly_state(version)
        state = None
    if state and state.get("draft_job_id"):
        job = budget_job_mod.get_budget_export_job(state["draft_job_id"])
        if job and job.get("status") in ("queued", "running", "completed"):
            return {
                **job,
                "monthly_stage": state.get("stage"),
                "summary": _summary_from_state(state),
                "advice_fingerprint": state.get("advice_fingerprint"),
            }
    try:
        job = budget_job_mod.start_budget_export_job(advice_items=body.advice_items or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    job_id = job.get("job_id") or ""
    db.upsert_monthly_state(
        version,
        stage="draft",
        advice_fingerprint=fingerprint,
        draft_job_id=job_id,
        question_source="",
        questions=None,
        answers=None,
        split_job_id="",
        split_mode="",
        split_result=None,
    )
    t = threading.Thread(
        target=_watch_draft, args=(job_id, version, fingerprint),
        name=f"monthly-draft-{job_id}", daemon=True,
    )
    t.start()
    return {**job, "monthly_stage": "draft", "summary": None, "advice_fingerprint": fingerprint}


@router.get("/draft/jobs/active")
def active_budget_draft() -> dict:
    version = _require_version()
    state = _ensure_draft_finalized(version, _state_or_none(version))
    job = budget_job_mod.get_active_budget_export_job()
    if job is None:
        return {"job": None, "monthly_stage": (state or {}).get("stage") or "none"}
    return {
        **job,
        "monthly_stage": (state or {}).get("stage") or "draft",
        "summary": _summary_from_state(state or {}),
    }


@router.get("/draft/jobs/{job_id}")
def get_budget_draft(job_id: str) -> dict:
    version = _require_version()
    job = budget_job_mod.get_budget_export_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    state = _ensure_draft_finalized(version, _state_or_none(version))
    return {
        **job,
        "monthly_stage": (state or {}).get("stage") if state else "none",
        "summary": _summary_from_state(state or {}),
    }


# ── 月度流程状态 / 问答 ────────────────────────────────────────────────

@router.get("/monthly/state")
def monthly_state() -> dict:
    version = _require_version()
    state = _ensure_draft_finalized(version, _state_or_none(version))
    if not state:
        return {"stage": "none", "session_version": version}
    state["summary"] = _summary_from_state(state)
    state["session_version"] = version
    return state


@router.post("/monthly/questions")
def generate_questions() -> dict:
    """生成并持久化问题集（AI 优先，失败回退规则题库）；幂等。"""
    version = _require_version()
    state = _state_or_none(version)
    if not state or not state.get("plan_snapshot"):
        raise HTTPException(status_code=400, detail="请先生成预算第一稿")
    # 幂等：已有未作答问题集直接返回
    if state.get("questions") and not state.get("answers"):
        return {"questions": state["questions"], "source": state.get("question_source") or "rule"}
    if state.get("questions") and state.get("stage") in ("answered", "splitting", "ready"):
        return {"questions": state["questions"], "source": state.get("question_source") or "rule"}

    snapshot = state["plan_snapshot"]
    hints = _budget_hints()
    questions, err = ai_mod.generate_monthly_questions(snapshot, hints)
    if questions:
        source = "ai"
    else:
        questions = monthly.build_rule_questions()
        source = "rule"
        if err and "未配置" not in err:
            logger.warning("月度出题回退规则题库：%s", err)
    db.upsert_monthly_state(
        version, questions=questions, question_source=source, stage="questions"
    )
    return {"questions": questions, "source": source}


@router.get("/monthly/questions")
def read_questions() -> dict:
    version = _require_version()
    state = _state_or_none(version)
    if not state or not state.get("questions"):
        raise HTTPException(status_code=404, detail="尚未生成问题集")
    return {"questions": state["questions"], "source": state.get("question_source") or "rule"}


@router.post("/monthly/answers")
def submit_answers(body: MonthlyAnswersIn = MonthlyAnswersIn()) -> dict:
    """提交答案；缺失项自动用题面默认值补齐（一路回车也能走通）。"""
    version = _require_version()
    state = _state_or_none(version)
    if not state or not state.get("questions"):
        raise HTTPException(status_code=400, detail="尚未生成问题集")
    questions = state["questions"]
    by_id = {q.get("id"): q for q in questions}
    incoming = {}
    for item in body.answers or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "")
        if qid not in by_id:
            raise HTTPException(status_code=400, detail=f"未知问题 id：{qid}")
        incoming[qid] = str(item.get("value") or "").strip()
    filled = []
    for q in questions:
        qid = q["id"]
        value = incoming.get(qid, "")
        if not value:
            value = str(q.get("default") or "")
        elif q.get("type") == "single" and q.get("options") and value not in q["options"]:
            value = str(q.get("default") or "")  # 单选容错：非法值回落默认
        filled.append({"id": qid, "value": value})
    db.upsert_monthly_state(version, answers=filled, stage="answered")
    return {"answers": filled, "stage": "answered"}


# ── 月度拆分任务 ────────────────────────────────────────────────────────

def _split_job_dict(job_id: str) -> dict:
    with _lock:
        job = _split_jobs.get(job_id)
        return dict(job) if job else None


def _update_split_job(job_id: str, **kwargs) -> None:
    with _lock:
        job = _split_jobs.get(job_id)
        if job is not None:
            job.update(kwargs)


def _run_split(job_id: str, version: str) -> None:
    try:
        _update_split_job(job_id, status="running", progress=10, message="读取第一稿快照与答案…")
        state = db.get_monthly_state(version)
        snapshot = (state or {}).get("plan_snapshot") or {}
        plan_rows = snapshot.get("rows") or []
        answers_map = {a.get("id"): a.get("value") for a in (state.get("answers") or [])}
        hints = _budget_hints()

        result = None
        mode = "rule"
        if ai_mod.ai_available():
            needed = -1
            for attempt in range(1, _AI_SPLIT_ATTEMPTS + 1):
                _update_split_job(
                    job_id, progress=20 + attempt * 15,
                    message=f"AI 生成月度权重（第 {attempt}/{_AI_SPLIT_ATTEMPTS} 次）…",
                )
                ai_rows, err = ai_mod.generate_monthly_weights(snapshot, answers_map, hints)
                needed, covered = monthly.ai_weights_coverage(plan_rows, ai_rows)
                if ai_rows and needed > 0 and covered == needed:
                    candidate = monthly.merge_ai_weights(plan_rows, ai_rows)
                    checks = monthly.verify(candidate.rows)
                    if checks["row_failures"] == 0 and checks["total_gap"] == 0:
                        result = candidate
                        mode = "ai"
                        break
                _update_split_job(
                    job_id,
                    message=f"AI 权重未全覆盖或未过校验（{covered}/{needed} 行，{err or '重试'}）…",
                )
            if result is None:
                _update_split_job(job_id, progress=75, message="AI 拆分未通过，回退规则默认拆分…")
        else:
            _update_split_job(job_id, progress=40, message="未配置 AI，使用规则默认拆分…")

        if result is None:
            result = monthly.rule_split(plan_rows, answers_map, budget_year=hints.get("budget_year") or 0)
            mode = "rule"

        # 竞态防护：已被更新的拆分任务接管时丢弃本结果（重新拆分覆盖）
        latest = db.get_monthly_state(version)
        if latest and latest.get("split_job_id") and latest.get("split_job_id") != job_id:
            _update_split_job(job_id, status="failed", progress=100,
                              error="已被新的拆分任务替代", message="已取消", finished_at=time.time())
            return

        payload = result.to_payload()
        payload["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        payload["budget_year"] = hints.get("budget_year")
        db.upsert_monthly_state(
            version,
            stage="ready",
            split_job_id=job_id,
            split_mode=mode,
            split_result=payload,
        )
        warn_n = len(payload.get("warnings") or [])
        _update_split_job(
            job_id, status="completed", progress=100,
            message=f"拆分完成（{ 'AI' if mode == 'ai' else '规则默认' }拆分"
                    + (f"，{warn_n} 条提示" if warn_n else "") + "）",
            finished_at=time.time(),
        )
    except ArithmeticError as e:
        logger.exception("月度拆分恒等校验失败 job=%s", job_id)
        try:
            db.upsert_monthly_state(version, stage="failed", split_job_id=job_id)
        except Exception:
            pass
        _update_split_job(job_id, status="failed", progress=100, error=str(e), message="拆分失败：恒等校验未通过", finished_at=time.time())
    except Exception as e:
        logger.exception("月度拆分任务失败 job=%s", job_id)
        try:
            db.upsert_monthly_state(version, stage="failed", split_job_id=job_id)
        except Exception:
            pass
        _update_split_job(job_id, status="failed", progress=100, error=str(e) or type(e).__name__, message="拆分失败", finished_at=time.time())


@router.post("/monthly/split/jobs")
def start_monthly_split() -> dict:
    version = _require_version()
    state = _state_or_none(version)
    if not state or not state.get("plan_snapshot"):
        raise HTTPException(status_code=400, detail="请先生成预算第一稿")
    if not state.get("answers"):
        raise HTTPException(status_code=400, detail="请先完成月度拆分问答")
    if state.get("stage") not in ("answered", "ready"):
        raise HTTPException(status_code=400, detail=f"当前阶段不允许拆分（{state.get('stage')}）")

    job_id = f"monthly-{uuid.uuid4().hex[:12]}"
    with _lock:
        # 同会话旧拆分任务作废（重新拆分覆盖）
        for old_id, old in list(_split_jobs.items()):
            if old.get("session_version") == version and old.get("status") in ("queued", "running"):
                old["status"] = "failed"
                old["error"] = "已被新的拆分任务替代"
        _split_jobs[job_id] = {
            "job_id": job_id,
            "session_version": version,
            "status": "queued",
            "progress": 0,
            "message": "已排队",
            "error": "",
            "created_at": time.time(),
            "finished_at": 0.0,
        }
    db.upsert_monthly_state(version, stage="splitting", split_job_id=job_id)
    t = threading.Thread(target=_run_split, args=(job_id, version), name=f"monthly-split-{job_id}", daemon=True)
    t.start()
    return _split_job_dict(job_id)


@router.get("/monthly/split/jobs/{job_id}")
def get_monthly_split_job(job_id: str) -> dict:
    job = _split_job_dict(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.get("/monthly")
def latest_monthly_result() -> dict:
    version = _require_version()
    state = _state_or_none(version)
    if not state or not state.get("split_result"):
        raise HTTPException(status_code=404, detail="尚无月度拆分结果")
    payload = dict(state["split_result"])
    payload["stage"] = state.get("stage")
    return payload


@router.get("/monthly/download")
def download_monthly_budget():
    """下载最终文件：第一稿 xlsx 追加「月度执行计划」Sheet 后另存。"""
    version = _require_version()
    state = _state_or_none(version)
    if not state or state.get("stage") != "ready" or not state.get("split_result"):
        raise HTTPException(status_code=409, detail="月度拆分尚未就绪，不能下载")
    draft_path = state.get("draft_path") or ""
    if not draft_path:
        raise HTTPException(status_code=409, detail="第一稿文件路径缺失，请重新生成")
    import os

    if not os.path.exists(draft_path):
        raise HTTPException(status_code=409, detail="第一稿文件已不存在，请重新生成")
    budget_export = importlib.import_module("core.CO_budget_export_WB-CO-TR-20260810")
    payload = state["split_result"]
    mode = payload.get("mode") or state.get("split_mode") or "rule"
    try:
        out_path = budget_export.append_monthly_sheet(
            draft_path, payload, mode=mode
        )
    except AttributeError:
        raise HTTPException(status_code=501, detail="月度 Sheet 写入尚未就绪（P2 交付）")
    except Exception as e:
        logger.exception("月度 Sheet 写入失败")
        raise HTTPException(status_code=500, detail=f"月度 Sheet 写入失败：{e}")
    stem = str(state.get("draft_path") or "budget").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if stem.lower().endswith(".xlsx"):
        stem = stem[:-5]
    download_name = f"{stem}（含月度拆分）.xlsx"
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=download_name,
    )
