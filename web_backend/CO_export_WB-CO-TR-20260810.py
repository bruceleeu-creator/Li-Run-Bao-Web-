"""利润宝 · 导出 API（艺康体 Word / PDF / Excel / 三 Sheet 预算模板）。

工作流：
1. 读取当前会话财报 + 诊断 + 互动决策
2. 重建 interactive.Session
3. 调用 core.report / core.action_pack / core.budget_template 生成文件
4. 以附件下载返回

端点：
- POST /api/export/word    经营业绩分析与建议（.docx，艺康体小白版）
- POST /api/export/pdf     同结构 PDF
- POST /api/export/excel   成本优化测算模型（行动包）
- POST /api/export/budget  三 Sheet 费用预算模板（DeepSeek 可选填充）
- GET  /api/export/status  是否可导出 + 预览摘要

经营预算分析链路（与费用编制建议同一模式：先 DeepSeek 分析 → 再导出填入）：
- POST /api/export/analysis       DeepSeek 写「前世今生」文案（数字白名单校验）
- GET  /api/export/analysis/last  最近一次分析（同会话）
Word/PDF 导出时自动把最近分析合并进叙事模板（表格/折线图不动）。
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import diagnostic as diag_mod
from core import interactive as iv_mod
from core import report as report_mod
from core import action_pack as ap_mod

session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
db = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")
interaction = importlib.import_module("web_backend.CO_interaction_WB-CO-TR-20260805160732")
budget_export = importlib.import_module("core.CO_budget_export_WB-CO-TR-20260810")
ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
report_analysis = importlib.import_module("core.CO_report_analysis_WB-CO-TR-20260818")
from core.narrative import build_stage_narrative  # noqa: E402

router = APIRouter(prefix="/api/export", tags=["export"])
logger = logging.getLogger(__name__)

_EXPORT_DIR = Path(__file__).resolve().parent / "workspaces" / "exports"

# 最近一次经营预算分析（内存态，按会话版本匹配；与 CO_budget._last_advice 同模式）
_last_analysis: dict = {}


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip()) or "企业"
    return cleaned[:40]


def _diagnosis_from_db(data) -> Optional[diag_mod.DiagnosisResult]:
    """复用互动模块的重建逻辑（保持一致）。"""
    return interaction._diagnosis_from_db(data)


def _build_session(require_final: bool = False) -> iv_mod.Session:
    """从持久化层重建可导出的 Session。

    require_final=True 时：若诊断已在但互动未完成，自动 fast-forward 解锁
   （旧实例升级后不必重新做诊断/互动）。
    """
    data = session.get_data()
    if data is None:
        raise HTTPException(status_code=400, detail="尚未导入财报，请先完成财报导入")

    diagnosis = _diagnosis_from_db(data)
    if diagnosis is None:
        # 无诊断时仍可用规则引擎即时诊断（仅数据，不写库）
        diagnosis = diag_mod.diagnose(data)

    # 优先使用内存互动会话
    live = None
    try:
        with interaction._lock:
            if (
                interaction._sess is not None
                and interaction._sess_session_version == session.get_version()
            ):
                live = interaction._sess
    except Exception:
        live = None

    if live is not None:
        sess = live
    else:
        sess = iv_mod.start_session(data, diagnosis)
        stored = db.load_interaction_session()
        if stored and stored.get("session_version") == session.get_version():
            for dec in stored.get("decisions", []):
                if sess.state != iv_mod.STATE_FINDING_LOOP:
                    break
                iv_mod.submit_decision(
                    sess,
                    dec.get("finding_id", ""),
                    dec.get("option_label", ""),
                    strategy_note=dec.get("strategy_note", ""),
                )
            if stored.get("user_confirmed") and sess.state == iv_mod.STATE_CONFIRMATION:
                iv_mod.confirm(sess, user_confirmed=True)
            # 恢复 draft2 若已在 CONFIRMATION/FINAL
            if stored.get("draft2") and not sess.draft2 and sess.state in (
                iv_mod.STATE_CONFIRMATION,
                iv_mod.STATE_FINAL,
                iv_mod.STATE_DRAFT2,
            ):
                # draft2 已由 submit_decision 生成；若 decisions 空则忽略
                pass

    if require_final and not sess.is_export_unlocked:
        # 自动补全互动（秒级），避免旧会话卡死在导出页
        try:
            interaction.fast_forward_interaction(
                interaction.FastForwardIn(
                    option_label="A",
                    strategy_note="导出前自动补全（保留已有诊断）",
                    confirm=True,
                )
            )
            # 重新取会话
            with interaction._lock:
                if interaction._sess is not None:
                    sess = interaction._sess
        except Exception as e:
            raise HTTPException(
                status_code=409,
                detail=(
                    "导出未解锁：请先完成 A/B/C 互动并确认第二稿"
                    f"（自动补全失败：{type(e).__name__}）"
                ),
            ) from e
        if not sess.is_export_unlocked:
            raise HTTPException(
                status_code=409,
                detail="导出未解锁：请先到总览点击「一键补全互动并解锁导出」",
            )
    return sess


def _output_path(suffix: str, company: str, years) -> Path:
    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    yspan = ""
    if years:
        ys = sorted(years)
        yspan = f"{ys[0]}-{ys[-1]}" if len(ys) > 1 else str(ys[0])
    name = f"{_safe_filename(company)}{yspan}经营业绩分析与建议{suffix}"
    return _EXPORT_DIR / name


def _file_response(path: Path, download_name: str, media: str) -> FileResponse:
    # RFC 5987 中文文件名
    encoded = quote(download_name)
    return FileResponse(
        path=str(path),
        media_type=media,
        filename=download_name,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
        },
    )


@router.get("/status")
def export_status() -> dict:
    """导出前置条件与预览摘要。"""
    data = session.get_data()
    if data is None:
        return {
            "ready": False,
            "unlocked": False,
            "reason": "尚未导入财报",
            "company_name": "",
            "years": [],
            "findings": 0,
            "decisions": 0,
        }
    try:
        sess = _build_session(require_final=False)
    except HTTPException as e:
        return {
            "ready": False,
            "unlocked": False,
            "reason": e.detail,
            "company_name": data.company_name,
            "years": list(data.years),
            "findings": 0,
            "decisions": 0,
        }
    unlocked = sess.is_export_unlocked
    dq = session.get_data_quality() if hasattr(session, "get_data_quality") else {}
    policy = session.get_policy() if hasattr(session, "get_policy") else {}
    require_confirm = bool((dq or {}).get("require_confirm") or (dq or {}).get("confidence") == "low")
    analysis_ready = (
        _last_analysis.get("session_version") == session.get_version()
        and report_analysis.has_analysis_content((_last_analysis or {}).get("payload") or {})
    )
    return {
        "ready": True,
        "unlocked": unlocked,
        "reason": "" if unlocked else "请先完成互动并确认第二稿",
        "company_name": sess.data.company_name,
        "years": list(sess.data.years),
        "findings": len(sess.diagnosis.findings),
        "decisions": len(sess.decisions),
        "feasibility_score": sess.feasibility_score,
        "state": sess.state,
        "total_est_saving": sess.total_est_saving,
        "data_quality": dq or {},
        "policy": policy or {},
        "require_confirm": require_confirm,
        "analysis_ready": analysis_ready,
    }


# ── 经营预算分析（前世今生 · DeepSeek 先行，Word/PDF 后导） ──────────────


@router.post("/analysis")
def generate_report_analysis() -> dict:
    """DeepSeek 经营预算分析：把确定性事实清单改写为小白版文案。

    数字白名单校验：文案中出现事实清单外的数字时返回 number_warnings
    （只提示，绝不静默改数）。结果存内存，Word/PDF 导出自动合并。
    """
    global _last_analysis
    sess = _build_session(require_final=False)
    facts = report_analysis.build_analysis_factsheet(sess.data, sess.diagnosis, sess.decisions)
    obj, summary, ai_error = ai_mod.analyze_operating_narrative({"factsheet": facts})
    if not obj:
        raise HTTPException(
            status_code=503,
            detail=ai_error or "DeepSeek 未返回经营预算分析，请检查 AI 配置后重试",
        )
    payload = report_analysis.normalize_analysis_payload(obj)
    if not report_analysis.has_analysis_content(payload):
        raise HTTPException(
            status_code=503,
            detail="DeepSeek 返回的经营预算分析为空，请重试",
        )
    if summary and not payload.get("ai_summary"):
        payload["ai_summary"] = summary
    payload["company_name"] = facts.get("company_name") or sess.data.company_name
    payload["years"] = list(sess.data.years or [])
    payload["number_warnings"] = report_analysis.validate_analysis_numbers(payload, facts)
    payload["mode"] = "deepseek"
    _last_analysis = {
        "session_version": session.get_version(),
        "payload": payload,
    }
    return payload


@router.get("/analysis/last")
def last_report_analysis() -> dict:
    """最近一次经营预算分析（同会话）；无则 404。"""
    if (
        not _last_analysis
        or _last_analysis.get("session_version") != session.get_version()
    ):
        raise HTTPException(status_code=404, detail="尚无经营预算分析，请先生成")
    return _last_analysis["payload"]


def _merged_narrative(sess: iv_mod.Session) -> Optional[object]:
    """最近一次 DeepSeek 经营预算分析（同会话）合并进规则叙事；无则 None。

    只覆盖文本字段；跨年表/指标表/折线图等数字内容不受影响。
    """
    stored = (_last_analysis or {}).get("payload") or {}
    if (
        _last_analysis.get("session_version") != session.get_version()
        or not report_analysis.has_analysis_content(stored)
    ):
        return None
    try:
        base = build_stage_narrative(sess.data, sess.diagnosis, sess.decisions)
        return report_analysis.merge_narrative(base, stored)
    except Exception:
        logger.exception("经营预算分析合并失败，回退规则叙事")
        return None


@router.post("/word")
def export_word() -> FileResponse:
    """导出艺康体 Word 经营业绩分析与建议（含 DeepSeek 经营预算分析文案）。"""
    sess = _build_session(require_final=True)
    path = _output_path(".docx", sess.data.company_name, sess.data.years)
    try:
        report_mod.export_word(
            sess, str(path), ai_fallback=sess.ai_fallback_message, narrative=_merged_narrative(sess)
        )
    except report_mod.ReportError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("Word 导出失败")
        raise HTTPException(status_code=500, detail=f"Word 导出失败：{type(e).__name__}") from e
    return _file_response(
        path,
        path.name,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.post("/pdf")
def export_pdf() -> FileResponse:
    """导出 PDF 报告（与 Word 同一份内容）。"""
    sess = _build_session(require_final=True)
    path = _output_path(".pdf", sess.data.company_name, sess.data.years)
    try:
        report_mod.export_pdf(
            sess, str(path), ai_fallback=sess.ai_fallback_message, narrative=_merged_narrative(sess)
        )
    except report_mod.ReportError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("PDF 导出失败")
        raise HTTPException(status_code=500, detail=f"PDF 导出失败：{type(e).__name__}") from e
    return _file_response(path, path.name, "application/pdf")


@router.post("/excel")
def export_excel() -> FileResponse:
    """导出成本优化测算模型 Excel。"""
    sess = _build_session(require_final=True)
    yspan = ""
    if sess.data.years:
        ys = sorted(sess.data.years)
        yspan = f"{ys[0]}-{ys[-1]}" if len(ys) > 1 else str(ys[0])
    name = f"{_safe_filename(sess.data.company_name)}{yspan}成本优化测算模型.xlsx"
    path = _EXPORT_DIR / name
    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        ap_mod.export_excel_model(sess, str(path))
    except ap_mod.ActionPackError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("Excel 导出失败")
        raise HTTPException(status_code=500, detail=f"Excel 导出失败：{type(e).__name__}") from e
    return _file_response(
        path,
        path.name,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


budget_job_mod = importlib.import_module("web_backend.CO_budget_export_job_WB-CO-TR-20260810")


class BudgetExportJobIn(BaseModel):
    """启动预算三表任务。建议先走费用编制建议，再把勾选项传入自动填表。"""
    advice_items: list[dict] = Field(default_factory=list)


@router.post("/budget/jobs")
def start_budget_export_job(body: BudgetExportJobIn = BudgetExportJobIn()) -> dict:
    """异步启动费用预算三表：DeepSeek 提取 + 可选编制建议自动填入。"""
    try:
        return budget_job_mod.start_budget_export_job(advice_items=body.advice_items or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("启动预算导出任务失败")
        raise HTTPException(status_code=500, detail=f"启动失败：{type(e).__name__}") from e


@router.get("/budget/jobs/active")
def active_budget_export_job() -> dict:
    job = budget_job_mod.get_active_budget_export_job()
    return {"job": job}


@router.get("/budget/jobs/{job_id}")
def get_budget_export_job(job_id: str) -> dict:
    job = budget_job_mod.get_budget_export_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.get("/budget/jobs/{job_id}/download")
def download_budget_export_job(job_id: str) -> FileResponse:
    path = budget_job_mod.get_budget_export_path(job_id)
    if path is None:
        raise HTTPException(status_code=409, detail="文件未就绪或任务未完成")
    job = budget_job_mod.get_budget_export_job(job_id) or {}
    name = job.get("filename") or path.name
    return _file_response(
        path,
        name,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/budget")
def export_budget_template() -> FileResponse:
    """同步导出（兼容旧前端）：内部走同一套填充逻辑，可能较慢。"""
    # 启动异步任务并阻塞等待完成（最多 8 分钟）
    try:
        started = budget_job_mod.start_budget_export_job()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    job_id = started["job_id"]
    deadline = time.time() + 480
    while time.time() < deadline:
        job = budget_job_mod.get_budget_export_job(job_id)
        if not job:
            break
        if job["status"] == "completed":
            path = budget_job_mod.get_budget_export_path(job_id)
            if path is None:
                raise HTTPException(status_code=500, detail="任务完成但文件丢失")
            return _file_response(
                path,
                job.get("filename") or path.name,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        if job["status"] == "failed":
            raise HTTPException(status_code=500, detail=job.get("error") or "预算导出失败")
        time.sleep(0.8)
    raise HTTPException(status_code=504, detail="预算三表生成超时，请改用异步任务接口重试")
