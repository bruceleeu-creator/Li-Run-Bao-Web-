"""利润宝 · 第一轮诊断 API 路由。

端点：
- POST /api/diagnosis/run    执行规则诊断 + 可选 AI 增强 A/B/C 选项，结果持久化
- GET  /api/diagnosis        读取已保存诊断（绑定 session_version，不匹配返回 null）
- POST /api/diagnosis/clear  清空诊断结果（重新导入时由前端调用）

诊断结果绑定当前会话版本；重新导入后旧诊断自动失效，绝不展示旧数据。
AI 为可选增强：未配置或调用失败时静默回退规则引擎选项，不阻塞主流程。
"""

from __future__ import annotations

import importlib
import logging

from fastapi import APIRouter, HTTPException

from core import diagnostic as diag_mod

# 模块文件名含智能体标识连字符，须用 importlib 加载
session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
db = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")
ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")

router = APIRouter(prefix="/api/diagnosis", tags=["diagnosis"])
logger = logging.getLogger(__name__)


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
        "deduction_rate": opt.deduction_rate,
    }


def _finding_to_dict(finding, ai_enhanced: bool = False) -> dict:
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
        "ai_enhanced": ai_enhanced,
        "options": [_option_to_dict(o) for o in finding.options],
    }


def _diagnosis_payload(result, ai_used: bool, enhanced_ids: set | None = None) -> dict:
    enhanced_ids = enhanced_ids or set()
    return {
        "company_name": result.company_name,
        "industry": result.industry,
        "industry_fallback": result.industry_fallback,
        "vat_estimate_note": result.vat_estimate_note,
        "ai_used": ai_used,
        "years": list(result.years),
        "findings": [
            _finding_to_dict(f, ai_enhanced=f.id in enhanced_ids) for f in result.findings
        ],
    }


def _require_data():
    """无会话数据时返回 400，并给出明确提示。"""
    data = session.get_data()
    if data is None:
        raise HTTPException(status_code=400, detail="尚未导入财报，请先完成财报导入")
    return data


def _run_diagnosis(ai: bool = True) -> dict:
    """执行规则诊断；AI 可用且 ai=True 时逐条增强 A/B/C 选项（失败回退规则）。"""
    data = _require_data()
    result = diag_mod.diagnose(data)
    ai_used = False
    enhanced_ids: set[str] = set()

    engine = None
    if ai:
        engine = ai_mod._engine(timeout=30.0)

    if engine is not None and engine.is_available():
        ai_used = True
        for finding in result.findings:
            try:
                options = engine.generate_options(finding)
                if options and len(options) == 3:
                    finding.options = options
                    enhanced_ids.add(finding.id)
            except Exception as e:
                # 单条增强失败：保留规则选项，不阻塞整体诊断
                logger.info(
                    "诊断 AI 增强失败（%s），保留规则选项：%s", type(e).__name__, finding.id
                )

    payload = _diagnosis_payload(result, ai_used, enhanced_ids)

    db.save_diagnosis(
        session_version=session.get_version(),
        company_name=result.company_name,
        industry=result.industry,
        industry_fallback=result.industry_fallback,
        vat_estimate_note=result.vat_estimate_note,
        ai_used=ai_used,
        findings=payload["findings"],
    )
    return payload


@router.post("/run")
def run_diagnosis() -> dict:
    """执行第一轮诊断（规则引擎 + 可选 AI 增强），保存并返回结果。"""
    return _run_diagnosis(ai=True)


@router.get("")
def get_diagnosis() -> dict:
    """读取已保存诊断；无结果或 session_version 不匹配返回 {diagnosis: null}。"""
    stored = db.load_diagnosis()
    if stored is None:
        return {"diagnosis": None}
    if stored.get("session_version") != session.get_version():
        return {"diagnosis": None}
    # years 未持久化在诊断表：从当前会话数据读取（诊断与会话绑定，年份一致）
    data = session.get_data()
    years = list(data.years) if data is not None else []
    payload = {
        "company_name": stored["company_name"],
        "industry": stored["industry"],
        "industry_fallback": stored["industry_fallback"],
        "vat_estimate_note": stored["vat_estimate_note"],
        "ai_used": stored["ai_used"],
        "years": years,
        "findings": stored["findings"],
    }
    return {"diagnosis": payload}


@router.post("/clear")
def clear_diagnosis() -> dict:
    """清空已保存诊断（重新导入时调用，避免旧结果被复用）。"""
    db.clear_diagnosis_db()
    return {"cleared": True}
