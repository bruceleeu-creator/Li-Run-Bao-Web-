"""利润宝 · A/B/C 互动 API 路由。

端点：
- POST /api/interaction/start    启动互动会话（无诊断自动先诊断）
- GET  /api/interaction/state    读取当前互动状态
- POST /api/interaction/decide   提交对当前发现的 A/B/C 决策
- POST /api/interaction/confirm  用户确认，进入 FINAL 解锁导出

互动会话绑定 session_version；重新导入后旧会话自动失效（409 提示重来）。
AI 为可选增强：draft2 生成后尝试增强操作细节，失败静默回退规则文本。
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core import diagnostic as diag_mod
from core import interactive as iv_mod

# 模块文件名含智能体标识连字符，须用 importlib 加载
session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
db = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")
ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")

router = APIRouter(prefix="/api/interaction", tags=["interaction"])
logger = logging.getLogger(__name__)
# RLock：_require_session/_ensure_session 存在嵌套获取场景，须可重入
_lock = threading.RLock()

# 内存中的互动会话（与诊断结果共同构成可恢复状态）
_sess: Optional[iv_mod.Session] = None
_sess_session_version = ""


class DecideIn(BaseModel):
    finding_id: str
    option_label: str
    strategy_note: str = ""


class ConfirmIn(BaseModel):
    user_confirmed: bool = True


# ── 序列化 ────────────────────────────────────────────────────────────

def _option_to_dict(opt) -> dict:
    return {
        "label": opt.label,
        "name": opt.name,
        "description": opt.description,
        "target_value": opt.target_value,
        "tax_rate": opt.tax_rate,
        "est_saving": opt.est_saving,
        "cost_saving": opt.cost_saving,
        "tax_saving": opt.tax_saving,
        "tax_impact": opt.tax_impact,
        "feasibility": opt.feasibility,
        "risk_level": opt.risk_level,
        "action_note": opt.action_note,
        "deduction_rate": getattr(opt, "deduction_rate", 0.0),
    }


def _finding_to_dict(finding) -> dict:
    return {
        "id": finding.id,
        "title": finding.title,
        "category": finding.category,
        "severity": finding.severity,
        "fact": finding.fact,
        "benchmark": finding.benchmark,
        "suggestion": finding.suggestion,
        "current_value": finding.current_value,
        "target_value": finding.target_value,
        "unit": finding.unit,
        "status": finding.status,
        "options": [_option_to_dict(o) for o in finding.options],
    }


def _decision_to_dict(d) -> dict:
    return {
        "finding_id": d.finding_id,
        "finding_title": d.finding_title,
        "option_label": d.option_label,
        "option_name": d.option_name,
        "current_value": d.current_value,
        "target_value": d.target_value,
        "est_saving": d.est_saving,
        "risk_level": d.risk_level,
        "strategy_note": d.strategy_note,
        "trend": d.trend,
        "change_amount": d.change_amount,
        "change_pct": d.change_pct,
        "action_detail": d.action_detail,
        "cautions": d.cautions,
    }


def _draft2_entry_to_dict(e) -> dict:
    return {
        "finding_id": e.finding_id,
        "finding_title": e.finding_title,
        "option_label": e.option_label,
        "option_name": e.option_name,
        "trend": e.trend,
        "current_value": e.current_value,
        "target_value": e.target_value,
        "change_amount": e.change_amount,
        "change_pct": e.change_pct,
        "est_saving": e.est_saving,
        "action_detail": e.action_detail,
        "cautions": e.cautions,
        "risk_level": e.risk_level,
        "cost_saving": e.cost_saving,
        "tax_saving": e.tax_saving,
        "tax_impact": e.tax_impact,
        "tax_rate": e.tax_rate,
        "deduction_rate": getattr(e, "deduction_rate", 0.0),
    }


def _state_payload() -> dict:
    global _sess
    sess = _sess
    if sess is None:
        return {"state": "IDLE", "current_finding": None, "decisions": [], "draft2": []}
    current = sess.current_finding
    return {
        "state": sess.state,
        "current_index": sess.current_finding_index if current else None,
        "total": len(sess.diagnosis.findings),
        "current_finding": _finding_to_dict(current) if current else None,
        "decisions": [_decision_to_dict(d) for d in sess.decisions],
        "draft2": [_draft2_entry_to_dict(e) for e in sess.draft2],
        "feasibility_score": sess.feasibility_score,
        "feasibility_breakdown": list(sess.feasibility_breakdown),
        "is_export_unlocked": sess.is_export_unlocked,
        "user_confirmed": sess.user_confirmed,
        "ai_fallback_message": sess.ai_fallback_message,
    }


# ── 恢复与持久化 ──────────────────────────────────────────────────────

def _diagnosis_from_db(data) -> Optional[diag_mod.DiagnosisResult]:
    """从已保存诊断 JSON 重建 DiagnosisResult（必须与当前会话版本匹配）。"""
    stored = db.load_diagnosis()
    if stored is None or stored.get("session_version") != session.get_version():
        return None
    findings = []
    for f in stored.get("findings", []):
        options = []
        for o in f.get("options", []):
            options.append(diag_mod.Option(
                label=o.get("label", ""),
                name=o.get("name", ""),
                description=o.get("description", ""),
                target_value=float(o.get("target_value", 0.0) or 0.0),
                tax_rate=float(o.get("tax_rate", 0.0) or 0.0),
                est_saving=float(o.get("est_saving", 0.0) or 0.0),
                cost_saving=float(o.get("cost_saving", 0.0) or 0.0),
                tax_saving=float(o.get("tax_saving", 0.0) or 0.0),
                tax_impact=float(o.get("tax_impact", 0.0) or 0.0),
                feasibility=str(o.get("feasibility", "中")),
                risk_level=str(o.get("risk_level", "低")),
                action_note=o.get("action_note", ""),
                deduction_rate=float(o.get("deduction_rate", 0.0) or 0.0),
            ))
        findings.append(diag_mod.Finding(
            id=f.get("id", ""),
            title=f.get("title", ""),
            category=f.get("category", ""),
            severity=f.get("severity", "中"),
            fact=f.get("fact", ""),
            benchmark=f.get("benchmark", ""),
            suggestion=f.get("suggestion", ""),
            current_value=float(f.get("current_value", 0.0) or 0.0),
            target_value=float(f.get("target_value", 0.0) or 0.0),
            unit=f.get("unit", "%"),
            status=f.get("status", "pending"),
            options=options,
        ))
    return diag_mod.DiagnosisResult(
        company_name=stored.get("company_name", ""),
        industry=stored.get("industry", ""),
        industry_fallback=stored.get("industry_fallback", False),
        years=list(data.years or []),
        findings=findings,
        vat_estimate_note=stored.get("vat_estimate_note", ""),
    )


def _restore_session(data) -> None:
    """从 DB 重建互动会话：重建诊断 → 重放已保存决策 → 恢复确认态。"""
    global _sess, _sess_session_version
    version = session.get_version()
    diagnosis = _diagnosis_from_db(data)
    if diagnosis is None:
        return
    stored = db.load_interaction_session()
    if stored is None or stored.get("session_version") != version:
        return
    sess = iv_mod.start_session(data, diagnosis)
    for dec in stored.get("decisions", []):
        finding_id = dec.get("finding_id", "")
        option_label = dec.get("option_label", "")
        strategy_note = dec.get("strategy_note", "")
        if sess.state != iv_mod.STATE_FINDING_LOOP:
            break
        iv_mod.submit_decision(sess, finding_id, option_label, strategy_note=strategy_note)
    # 恢复确认态：全部决策重放后处于 CONFIRMATION
    if stored.get("user_confirmed") and sess.state == iv_mod.STATE_CONFIRMATION:
        iv_mod.confirm(sess, user_confirmed=True)
    _sess = sess
    _sess_session_version = version


def _db_has_live_session() -> bool:
    """DB 中是否存在与当前 session_version 匹配的互动记录。"""
    stored = db.load_interaction_session()
    return stored is not None and stored.get("session_version") == session.get_version()


def _ensure_session(data) -> Optional[iv_mod.Session]:
    """确保当前内存会话存在且与 session_version + DB 记录一致。"""
    global _sess, _sess_session_version
    version = session.get_version()
    with _lock:
        if _sess is not None:
            if _sess_session_version == version and _db_has_live_session():
                return _sess
            # 旧会话（重新导入后，即使版本未变但 DB 已清空）失效：丢弃，重新恢复或启动
            _sess = None
            _sess_session_version = ""
        _restore_session(data)
        if _sess is not None and _sess_session_version == version:
            return _sess
        # 无持久化会话：全新启动
        diagnosis = _diagnosis_from_db(data)
        if diagnosis is None:
            return None
        sess = iv_mod.start_session(data, diagnosis)
        _sess = sess
        _sess_session_version = version
        _persist(sess)
        return sess


def _persist(sess: iv_mod.Session) -> None:
    db.save_interaction_session(
        session_version=_sess_session_version,
        state=sess.state,
        decisions=[_decision_to_dict(d) for d in sess.decisions],
        strategy_notes=list(sess.strategy_notes),
        draft2=[_draft2_entry_to_dict(e) for e in sess.draft2],
        feasibility_score=sess.feasibility_score,
        feasibility_breakdown=list(sess.feasibility_breakdown),
        ai_fallback_message=sess.ai_fallback_message,
        user_confirmed=sess.user_confirmed,
    )


def _require_session() -> iv_mod.Session:
    data = session.get_data()
    if data is None:
        raise HTTPException(status_code=400, detail="尚未导入财报，请先完成财报导入")
    with _lock:
        sess = _ensure_session(data)
    if sess is None:
        raise HTTPException(
            status_code=409,
            detail="诊断结果与当前会话不一致，请先重新生成诊断",
        )
    return sess


def _try_ai_enhance_draft2(sess: iv_mod.Session) -> None:
    """draft2 生成后尝试 AI 增强操作细节；失败静默回退（由 try_ai_enhance 保证）。"""
    engine = ai_mod._engine(timeout=30.0)
    if engine is None or not engine.is_available():
        return
    try:
        iv_mod.try_ai_enhance(sess, engine)
    except Exception as e:
        logger.info("互动 AI 增强失败（%s），保持规则文本", type(e).__name__)


# ── 端点 ──────────────────────────────────────────────────────────────

@router.post("/start")
def start_interaction() -> dict:
    """启动互动会话；无已保存诊断时先执行诊断。"""
    global _sess, _sess_session_version
    data = session.get_data()
    if data is None:
        raise HTTPException(status_code=400, detail="尚未导入财报，请先完成财报导入")
    with _lock:
        # 旧会话失效判定：版本不匹配或 DB 记录已被清空（重新导入相同数据时版本可能不变）
        if _sess is not None and not (
            _sess_session_version == session.get_version() and _db_has_live_session()
        ):
            _sess = None
            _sess_session_version = ""
        if _sess is None:
            _restore_session(data)
        if _sess is not None:
            return _state_payload()
        # 无诊断：自动执行诊断（规则 + AI 增强）
        diag_mod_route = importlib.import_module("web_backend.CO_diagnosis_WB-CO-TR-20260805160732")
        diag_mod_route._run_diagnosis(ai=True)
        diagnosis = _diagnosis_from_db(data)
        if diagnosis is None:
            raise HTTPException(status_code=500, detail="诊断结果不可用，请稍后重试")
        _sess = iv_mod.start_session(data, diagnosis)
        _sess_session_version = session.get_version()
        _persist(_sess)
        return _state_payload()


@router.get("/state")
def get_interaction_state() -> dict:
    """读取当前互动状态；未导入或未启动时返回 IDLE。"""
    global _sess, _sess_session_version
    data = session.get_data()
    if data is None:
        return {"state": "IDLE", "current_finding": None, "decisions": [], "draft2": []}
    with _lock:
        # 会话已替换（重新导入，即使版本未变但 DB 已清空）：旧内存会话失效
        if _sess is not None and not _db_has_live_session():
            _sess = None
            _sess_session_version = ""
        elif _sess is not None and _sess_session_version != session.get_version():
            _sess = None
            _sess_session_version = ""
        if _sess is None:
            _restore_session(data)
        if _sess is None:
            return {"state": "IDLE", "current_finding": None, "decisions": [], "draft2": []}
        return _state_payload()


@router.post("/decide")
def submit_decision(body: DecideIn) -> dict:
    """提交对当前发现的 A/B/C 决策；推进到下一条或进入第二稿。"""
    sess = _require_session()
    with _lock:
        if sess.state != iv_mod.STATE_FINDING_LOOP:
            raise HTTPException(status_code=400, detail="当前不在决策轮次（请刷新状态）")
        current = sess.current_finding
        if current is None:
            raise HTTPException(status_code=400, detail="没有待决策的发现")
        if body.finding_id != current.id:
            raise HTTPException(
                status_code=400,
                detail=f"请先处理当前发现：{current.title}",
            )
        decision = iv_mod.submit_decision(
            sess, body.finding_id, body.option_label, strategy_note=body.strategy_note,
        )
        if decision is None:
            raise HTTPException(status_code=400, detail="决策无效：选项不存在或状态不允许")
        # 全部处理完进入 DRAFT2 → CONFIRMATION；尝试 AI 增强第二稿
        if sess.state == iv_mod.STATE_CONFIRMATION:
            _try_ai_enhance_draft2(sess)
        _persist(sess)
        return _state_payload()


@router.post("/confirm")
def confirm_interaction(body: ConfirmIn) -> dict:
    """用户确认（或自动满足落地性阈值）后进入 FINAL，解锁导出。"""
    sess = _require_session()
    with _lock:
        state = iv_mod.confirm(sess, user_confirmed=body.user_confirmed)
        _persist(sess)
        return _state_payload()
