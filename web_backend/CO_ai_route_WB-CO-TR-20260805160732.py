"""利润宝 · AI 整理预览 API 路由。

端点：
- GET  /api/ai/config    读取配置状态（不含 key，附脱敏提示）
- POST /api/ai/config    保存配置（key 仅内存，永不落盘）
- POST /api/ai/clear     清空配置恢复离线（含 base_url/model）
- POST /api/ai/key/clear 仅清除内存 Key（页面关闭 sendBeacon 调用）
- POST /api/ai/keepalive 前端心跳：延长内存 Key 存活（TTL 兜底配套）
- POST /api/ai/summarize 把预览内容整理为 markdown（自动保存到报告库）
- GET  /api/ai-reports   列出已保存的 AI 报告
- GET  /api/ai-reports/{id}  查看报告详情
- DELETE /api/ai-reports/{id} 删除报告
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# 模块文件名含智能体标识连字符，须用 importlib 加载
ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
db = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")
job_mod = importlib.import_module("web_backend.CO_ai_report_job_WB-CO-TR-20260807113737")


@asynccontextmanager
async def _ai_lifespan(_app):
    """单进程启动恢复旧 job；关停时等 worker 停止后才释放租约。"""
    acquired = job_mod.acquire_process_lease()
    try:
        if acquired:
            recovered = job_mod.recover_orphaned_jobs()
            if recovered:
                lifecycle = importlib.import_module(
                    "web_backend.CO_import_WB-CO-TR-20260805160732"
                )
                lifecycle.retry_workspace_cleanup()
        yield
    finally:
        if acquired:
            job_mod.shutdown_process_lifecycle()


router = APIRouter(prefix="/api/ai", tags=["ai"], lifespan=_ai_lifespan)


class AIConfigIn(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str = ""


class SummarizeIn(BaseModel):
    content: str


def _report_title(default: str) -> str:
    """从会话企业名生成报告标题。"""
    session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
    data = session.get_data()
    company = data.company_name if data and data.company_name else ""
    return f"{company} {default}" if company else default


@router.get("/config")
def get_config() -> dict:
    return ai_mod.get_config()


@router.post("/config")
def save_config(body: AIConfigIn) -> dict:
    cfg = ai_mod.save_config(body.base_url, body.model, body.api_key)
    return cfg


@router.post("/clear")
def clear_config() -> dict:
    return ai_mod.clear_config()


@router.post("/key/clear")
def clear_api_key() -> dict:
    """页面关闭时 sendBeacon 调用：仅清除内存 Key，保留 base_url/model。"""
    return ai_mod.clear_api_key()


@router.post("/keepalive")
def keepalive() -> dict:
    """前端心跳（页面打开期间每分钟一次）：延长内存 Key 存活。"""
    return ai_mod.keepalive()


@router.post("/summarize")
def summarize(body: SummarizeIn) -> dict:
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="内容为空")
    markdown, error = ai_mod.summarize_for_markdown(body.content)
    if error:
        raise HTTPException(status_code=400, detail=error)
    db.save_report("summarize", _report_title("财报整理"), markdown)
    return {"markdown": markdown, "saved": True}


@router.post("/years-summary", status_code=202)
def years_summary() -> dict:
    """旧同步入口改为启动同一冻结后台 job，不再直接保存未标识报告。"""
    session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
    version = session.get_version()
    if not version:
        raise HTTPException(status_code=400, detail="尚未导入财报，请先导入后再生成总览")
    try:
        job_id = job_mod.start_job(version)
    except db.JobCaptureError as exc:
        status_code = 409 if exc.code == "SESSION_CHANGED" else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    snapshot = job_mod.get_job(job_id)
    return {"job_id": job_id, "status": snapshot["status"]}


@router.post("/years-summary/jobs")
def start_years_summary_job() -> dict:
    """启动（或复用）当前会话的跨年合并报告后台任务，返回持久化任务快照。"""
    session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
    version = session.get_version()
    if not version:
        raise HTTPException(status_code=400, detail="尚未导入财报，请先导入后再生成报告")
    try:
        job_id = job_mod.start_job(version)
    except db.JobCaptureError as exc:
        status_code = 409 if exc.code == "SESSION_CHANGED" else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    snapshot = job_mod.get_job(job_id)
    return {"job_id": snapshot["job_id"], "status": snapshot["status"]}


@router.get("/years-summary/jobs/active")
def active_years_summary_job() -> dict:
    """返回当前会话版本进行中的后台任务；无则 job 为 null。"""
    session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
    return {"job": job_mod.get_active_job(session.get_version())}


@router.get("/years-summary/jobs/{job_id}")
def get_years_summary_job(job_id: str) -> dict:
    """读取指定后台任务快照。"""
    snapshot = job_mod.get_job(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return snapshot


# ── AI 报告管理 ───────────────────────────────────────────────────────

@router.get("/reports")
def list_reports() -> dict:
    """列出已保存的 AI 报告（不含内容）。"""
    return {"reports": db.list_reports()}


@router.get("/reports/{report_id}")
def get_report(report_id: int) -> dict:
    """查看报告详情。"""
    report = db.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    return report


@router.delete("/reports/{report_id}")
def delete_report(report_id: int) -> dict:
    """删除报告。"""
    if not db.delete_report(report_id):
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"deleted": report_id}
