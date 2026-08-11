"""费用预算 / 诊断 / 导入 AI 必须严格执行的合规筹划规则（金税四期方向）。

规则来源：业务方交付要求 + WB 行业基准数据库。
所有 DeepSeek 提示词与本地金额对齐应引用本模块，禁止各处各写一套。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import industry as ind_mod
from .models import FinancialData

# ── 三条硬规则（对外展示 / 注入 prompt 原样使用）──────────────────────
HARD_RULES_LIST: List[str] = [
    "调取企业历史成本费用占营收比例数据进行对标分析；",
    "结合现行财税政策合规要求、金税四期监控与预警逻辑，评估本年度收入上涨后，"
    "企业成本费用可筹划范围；",
    "测算成本费用合规上调合理比例，保证成本增幅匹配营收增速、数值处于行业正常区间，"
    "杜绝涉税风险。",
]

HARD_RULES_BLOCK = (
    "【必须严格执行的费用/成本合规筹划规则】\n"
    + "\n".join(f"{i}. {r}" for i, r in enumerate(HARD_RULES_LIST, 1))
)

# 预算金额限制：费用增速不得超过收入增速 + 缓冲百分点（绝对百分点）
FEE_GROWTH_BUFFER_PP = 0.03  # 3 个百分点
# 营收 |增速| 超过该值时，费用增速帽改用 winsorize 增速（与 reconciliation 一致）
REV_GROWTH_WINSOR = 0.30
# 单行费用率硬顶（占营收）
MAX_SINGLE_LINE_RATIO = 0.08
MAX_SINGLE_LINE_RATIO_RIGID = 0.12  # 工资/社保/房租


def hard_rules_prompt_suffix() -> str:
    """拼进 system prompt 的强制段。"""
    return (
        HARD_RULES_BLOCK
        + "\n执行要求：费用/成本预算金额必须有「历史占比对标」与「增速匹配」依据；"
        "禁止费用增速显著超过收入增速（可允许缓冲 +3 个百分点）；"
        "总费用率与分科目费用率须落在行业正常区间与企业历史合理带；"
        "任何建议须可在金税四期风险扫描下自洽，禁止虚增成本、隐匿收入、违法筹划。"
    )


def severity_sort_key(severity: str) -> int:
    """诊断排序：低=0（上，绿）→ 中=1（中，橙）→ 高=2（下，红）。"""
    return {"低": 0, "中": 1, "高": 2}.get(str(severity or "").strip(), 9)


def sort_findings_by_severity(findings: list) -> list:
    """就地/返回：按风险从低到高。"""
    return sorted(findings, key=lambda f: severity_sort_key(getattr(f, "severity", "") or (f.get("severity") if isinstance(f, dict) else "")))


def historical_fee_ratios(data: FinancialData) -> Dict[str, Any]:
    """历史成本费用占营收比例（按年、分销管研财）。"""
    years = sorted(data.years or [])
    out_years: Dict[str, dict] = {}
    for y in years:
        rev = _amt(data, "营业收入", y)
        if rev <= 0:
            continue
        sell = _amt(data, "销售费用", y)
        admin = _amt(data, "管理费用", y)
        rd = _amt(data, "研发费用", y)
        fin = _amt(data, "财务费用", y)
        cost = _amt(data, "营业成本", y)
        period = sell + admin + rd + fin
        out_years[str(y)] = {
            "revenue": rev,
            "cost": cost,
            "selling": sell,
            "admin": admin,
            "rd": rd,
            "finance": fin,
            "period_expense": period,
            "cost_ratio": round(cost / rev, 6),
            "period_expense_ratio": round(period / rev, 6),
            "selling_ratio": round(sell / rev, 6),
            "admin_ratio": round(admin / rev, 6),
            "rd_ratio": round(rd / rev, 6),
            "finance_ratio": round(fin / rev, 6),
        }
    latest = years[-1] if years else 0
    prev = years[-2] if len(years) >= 2 else latest
    rev_l = _amt(data, "营业收入", latest)
    rev_p = _amt(data, "营业收入", prev)
    rev_growth = ((rev_l - rev_p) / rev_p) if rev_p > 0 else 0.0
    # |增速|>30%：费用增速帽按 winsorize 后增速计算（降权暴涨暴跌）
    winsor = max(-REV_GROWTH_WINSOR, min(REV_GROWTH_WINSOR, rev_growth))
    volatile = abs(rev_growth) > REV_GROWTH_WINSOR + 1e-12
    fee_base_growth = winsor if volatile else rev_growth
    return {
        "by_year": out_years,
        "latest_year": latest,
        "prev_year": prev,
        "revenue_growth_rate": round(rev_growth, 6),
        "revenue_growth_winsorized": round(winsor, 6),
        "revenue_volatile": volatile,
        "fee_growth_cap": round(fee_base_growth + FEE_GROWTH_BUFFER_PP, 6),
        "fee_growth_mode": "winsor_band_priority" if volatile else "raw_plus_buffer",
    }


def budget_amount_limits(data: FinancialData, industry: str = "") -> Dict[str, Any]:
    """预算金额上限约束（给 DeepSeek 与本地校验共用）。"""
    hist = historical_fee_ratios(data)
    ind = industry or data.industry or ""
    band = ind_mod.get_period_expense_ratio_band(ind)
    years = sorted(data.years or [])
    latest = years[-1] if years else 0
    rev = _amt(data, "营业收入", latest)
    by = hist.get("by_year") or {}
    prev_y = str(hist.get("prev_year") or "")
    hist_rate = float((by.get(prev_y) or {}).get("period_expense_ratio") or 0)
    if hist_rate <= 0 and by:
        # 取最近有数据年
        last_key = sorted(by.keys())[-1]
        hist_rate = float(by[last_key].get("period_expense_ratio") or 0)
    med = float(band.get("median") or 0.12)
    lo = float(band.get("min") or 0.08)
    hi = float(band.get("max") or 0.18)
    # 目标费用率：历史与行业中枢取中，夹在行业带内
    target_rate = hist_rate if hist_rate > 0 else med
    target_rate = min(max(target_rate, lo), hi)
    rev_growth = float(hist.get("revenue_growth_rate") or 0)
    fee_cap_growth = float(hist.get("fee_growth_cap") or rev_growth)
    volatile = bool(hist.get("revenue_volatile"))
    prev_period = 0.0
    if prev_y and prev_y in by:
        prev_period = float(by[prev_y].get("period_expense") or 0)
    max_total_by_growth = (
        prev_period * (1.0 + fee_cap_growth) if prev_period > 0 else rev * hi
    )
    max_total_by_band = rev * hi if rev > 0 else 0.0
    target_total = rev * target_rate if rev > 0 else 0.0
    # 最终上限：增速匹配与行业带取小（更严）；暴涨暴跌年更偏向行业带
    if rev > 0:
        candidates = [x for x in (max_total_by_growth, max_total_by_band) if x > 0]
        if volatile and max_total_by_band > 0:
            # 波动年：硬顶取行业带与增速帽的较小者，但目标用行业中枢权重更高
            hard_cap = min(candidates) if candidates else rev * hi
            target_rate = min(max((target_rate + med) / 2.0, lo), hi)
            target_total = rev * target_rate
        else:
            hard_cap = min(candidates) if candidates else rev * hi
    else:
        hard_cap = 0.0
    if hard_cap <= 0 and rev > 0:
        hard_cap = rev * hi

    # 稳健分科费用率（去掉最大异常年）供编制建议引用
    robust_ratios: Dict[str, float] = {}
    try:
        from . import reconciliation as recon_mod

        for subj, key in (
            ("销售费用", "selling"),
            ("管理费用", "admin"),
            ("研发费用", "rd"),
            ("财务费用", "finance"),
        ):
            r = recon_mod.robust_subject_ratio(data, subj, drop_max=True)
            if r is not None:
                robust_ratios[key] = round(r, 6)
    except Exception:
        pass

    near_zero_selling = False
    if by:
        last_key = str(latest) if str(latest) in by else sorted(by.keys())[-1]
        near_zero_selling = float(by[last_key].get("selling_ratio") or 0) < 0.0005

    return {
        "historical": hist,
        "industry_fee_band": band,
        "target_period_expense_ratio": round(target_rate, 6),
        "target_period_expense_total": round(target_total, 2),
        "hard_cap_period_expense_total": round(hard_cap, 2),
        "revenue_growth_rate": rev_growth,
        "max_fee_growth_rate": fee_cap_growth,
        "revenue_volatile": volatile,
        "fee_growth_mode": hist.get("fee_growth_mode"),
        "robust_subject_ratios": robust_ratios,
        "near_zero_selling": near_zero_selling,
        "max_single_line_ratio": MAX_SINGLE_LINE_RATIO,
        "max_single_line_ratio_rigid": MAX_SINGLE_LINE_RATIO_RIGID,
        "rules": HARD_RULES_LIST,
    }


def prompt_context_block(data: Optional[FinancialData], industry: str = "") -> str:
    """注入 user/system 的结构化约束 JSON 文本。"""
    if data is None:
        return HARD_RULES_BLOCK
    lim = budget_amount_limits(data, industry or data.industry or "")
    import json

    slim = {
        "rules": lim["rules"],
        "revenue_growth_rate": lim["revenue_growth_rate"],
        "max_fee_growth_rate": lim["max_fee_growth_rate"],
        "target_period_expense_ratio": lim["target_period_expense_ratio"],
        "target_period_expense_total": lim["target_period_expense_total"],
        "hard_cap_period_expense_total": lim["hard_cap_period_expense_total"],
        "industry_fee_band": lim["industry_fee_band"],
        "historical_by_year": lim["historical"].get("by_year"),
        "max_single_line_ratio": lim["max_single_line_ratio"],
    }
    return HARD_RULES_BLOCK + "\n【量化约束】\n" + json.dumps(slim, ensure_ascii=False)


def _amt(data: FinancialData, account: str, year: int) -> float:
    try:
        v = (data.income_statement.get(account) or {}).get(year)
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0
