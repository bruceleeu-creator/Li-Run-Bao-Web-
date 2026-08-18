"""多年审计勾稽、科目归一、税负/费用异常与数据质量摘要。

导入与预算链路在结构化解析后调用 enrich_financial_data，
结果写入 FinancialData.parsed_meta（data_quality / reconciliations / ...）。
"""
from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import FinancialData, normalize_account_name

# 金额勾稽容差（元）
AMT_TOL = 1.0
# 营收暴涨暴跌：|增速| 超过该值时费用增速帽降权
REV_GROWTH_WINSOR = 0.30
# 单科费用率 YoY 绝对变化超过该值视为异常（百分点→小数）
FEE_RATIO_JUMP = 0.02
# 销售费用/营收低于该值 → 无销售费用型业务
NEAR_ZERO_SELL_RATIO = 0.0005


def _amt(data: FinancialData, account: str, year: int) -> Optional[float]:
    vals = data.income_statement.get(account) or {}
    if year not in vals:
        return None
    v = vals.get(year)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _amt0(data: FinancialData, account: str, year: int) -> float:
    v = _amt(data, account, year)
    return float(v) if v is not None else 0.0


def normalize_subjects(data: FinancialData) -> List[str]:
    """科目同义词/子项归一到利润宝规范科目；就地修改 income_statement。"""
    notes: List[str] = []
    inc = data.income_statement

    # 1) 键名规范化（营业总收入→营业收入 等，依赖 models.SYNONYM_MAP）
    renamed: Dict[str, Dict[int, Optional[float]]] = {}
    for raw_acc, yv in list(inc.items()):
        norm = normalize_account_name(raw_acc)
        if norm == raw_acc:
            continue
        bucket = renamed.setdefault(norm, {})
        for y, val in (yv or {}).items():
            if val is None:
                continue
            prev = bucket.get(y)
            if prev is None:
                bucket[y] = float(val)
            else:
                # 已有规范科目时：子项不覆盖主项；主项为空才写入
                pass
        notes.append(f"科目归一：{raw_acc} → {norm}")
        # 合并进规范键
        target = inc.setdefault(norm, {})
        for y, val in (yv or {}).items():
            if val is None:
                continue
            if target.get(y) is None:
                target[y] = float(val)
        # 删除原始非规范键（规范键自身除外）
        if norm != raw_acc:
            del inc[raw_acc]

    # 2) 研究费用 → 研发费用
    for alias in ("研究费用", "研究与开发费", "研发投入"):
        if alias not in inc:
            continue
        rd = inc.setdefault("研发费用", {})
        for y, val in list((inc[alias] or {}).items()):
            if val is None:
                continue
            if rd.get(y) is None:
                rd[y] = float(val)
                notes.append(f"{y}：{alias}→研发费用 {float(val):,.2f}")
            elif abs(float(rd[y]) - float(val)) > AMT_TOL:
                notes.append(
                    f"{y}：{alias}={float(val):,.2f} 与研发费用={float(rd[y]):,.2f} 并存，保留研发费用"
                )
        del inc[alias]

    # 3) 小企业：管理费用含研究费用时，若研发已拆出且管理≈管理净额+研发，可记录可比提示
    years = sorted(data.years or [])
    for y in years:
        admin = _amt(data, "管理费用", y)
        rd = _amt(data, "研发费用", y)
        if admin is None or rd is None or rd <= 0 or admin <= 0:
            continue
        # 若管理显著大于研发且次年管理≈本年管理-研发，仅记备注
        if rd / admin > 0.25:
            notes.append(
                f"{y}：管理费用可能含研发口径（研发/管理={rd/admin*100:.1f}%），"
                "跨年对比请用 管理+研发 合计"
            )

    return notes


def detect_accounting_standard_hints(texts: Sequence[str] | None = None) -> Dict[str, str]:
    """从 OCR/文本粗检会计准则（按全文，不按年细拆时返回 global）。"""
    blob = "\n".join(texts or [])
    out: Dict[str, str] = {}
    if "小企业会计准则" in blob:
        out["hint"] = "小企业会计准则"
    if "企业会计准则" in blob and "小企业会计准则" not in blob:
        out["hint"] = "企业会计准则"
    elif "企业会计准则" in blob and "小企业会计准则" in blob:
        out["hint"] = "混合/切换（文本同时出现小企业与企业会计准则）"
    return out


def _close(a: Optional[float], b: Optional[float], tol: float = AMT_TOL) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def reconcile_income_statement(data: FinancialData) -> Dict[str, Any]:
    """跨年与表内勾稽。"""
    years = sorted(data.years or [])
    checks: List[Dict[str, Any]] = []
    errors: List[str] = []
    warnings: List[str] = []

    # R2/R3 逐年
    for y in years:
        rev = _amt(data, "营业收入", y)
        cost = _amt(data, "营业成本", y)
        pbt = _amt(data, "利润总额", y)
        tax = _amt(data, "所得税费用", y)
        ni = _amt(data, "净利润", y)

        if rev is not None and cost is not None:
            # R3 仅记录毛利存在性（恒等式定义）
            gm = rev - cost
            checks.append(
                {
                    "id": f"R3_gross_{y}",
                    "rule": "毛利=营收-成本",
                    "year": y,
                    "status": "pass",
                    "value": round(gm, 2),
                }
            )

        if pbt is not None and tax is not None and ni is not None:
            expect = pbt - tax
            ok = _close(expect, ni, tol=max(AMT_TOL, abs(pbt) * 1e-6 + 0.5))
            status = "pass" if ok else "fail"
            item = {
                "id": f"R2_ni_{y}",
                "rule": "利润总额-所得税费用≈净利润",
                "year": y,
                "status": status,
                "profit_total": pbt,
                "income_tax": tax,
                "net_income": ni,
                "expected_ni": round(expect, 2),
                "delta": round(expect - ni, 2),
            }
            checks.append(item)
            if not ok:
                msg = (
                    f"{y} 年勾稽失败：利润总额({pbt:,.2f})-所得税({tax:,.2f})"
                    f"={expect:,.2f} ≠ 净利润({ni:,.2f})"
                )
                errors.append(msg)

        if tax is not None and tax < 0:
            warnings.append(f"{y} 年所得税费用为负（{tax:,.2f}），不参与历史贡献率平均")

        if rev is not None and rev > 0 and tax is not None:
            cit = tax / rev
            if cit > 0.05:
                warnings.append(
                    f"{y} 年所得税/营收={cit*100:.2f}% 偏高（通常贡献率中枢约 1–3%）"
                )

    # R1 跨年：若仅有合并后的「本年」列，无法直接取「上期」；
    # 用相邻两年独立年值对比不了「报告上期」，但可提示用户用金标/OCR 双列。
    # 当 parsed_meta 带 prior_year_values 时再验。
    prior_map = (data.parsed_meta or {}).get("prior_year_values") or {}
    for y_str, accounts in prior_map.items():
        try:
            y = int(y_str)
        except (TypeError, ValueError):
            continue
        prev_y = y - 1
        if prev_y not in years:
            continue
        for acc, prior_val in (accounts or {}).items():
            actual = _amt(data, acc, prev_y)
            if actual is None or prior_val is None:
                continue
            ok = _close(float(prior_val), actual)
            checks.append(
                {
                    "id": f"R1_{acc}_{y}",
                    "rule": f"{y}报告上期={prev_y}本年",
                    "account": acc,
                    "status": "pass" if ok else "fail",
                    "prior_in_report": float(prior_val),
                    "actual_prev_year": actual,
                    "delta": round(float(prior_val) - actual, 2),
                }
            )
            if not ok:
                errors.append(
                    f"跨年不一致：{y} 报告「上期」{acc}={float(prior_val):,.2f} "
                    f"≠ {prev_y} 年 {actual:,.2f}"
                )

    # 相邻年营收存在性（弱检查）
    for i in range(1, len(years)):
        y0, y1 = years[i - 1], years[i]
        r0, r1 = _amt(data, "营业收入", y0), _amt(data, "营业收入", y1)
        if r0 and r1 and r0 > 0:
            g = (r1 - r0) / r0
            if abs(g) > REV_GROWTH_WINSOR:
                warnings.append(
                    f"营收波动剧烈：{y0}→{y1} 增速 {g*100:.1f}%（>{REV_GROWTH_WINSOR*100:.0f}%），"
                    "费用增速帽将降权"
                )

    hard_fail = any(c.get("status") == "fail" for c in checks if str(c.get("id", "")).startswith("R2"))
    return {
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "hard_fail": hard_fail,
        "ok": not hard_fail and not errors,
    }


def historical_cit_rates(data: FinancialData) -> Dict[str, Any]:
    """各年所得税贡献率；剔除税<0 或营收≤0。"""
    years = sorted(data.years or [])
    series: List[Dict[str, Any]] = []
    valid: List[float] = []
    for y in years:
        rev = _amt0(data, "营业收入", y)
        tax = _amt(data, "所得税费用", y)
        if rev <= 0:
            series.append({"year": y, "cit": None, "excluded": True, "reason": "无营收"})
            continue
        if tax is None:
            series.append({"year": y, "cit": None, "excluded": True, "reason": "无所得税"})
            continue
        if tax < 0:
            series.append(
                {
                    "year": y,
                    "cit": round(tax / rev, 6),
                    "excluded": True,
                    "reason": "所得税为负",
                    "tax": tax,
                    "revenue": rev,
                }
            )
            continue
        cit = tax / rev
        series.append(
            {
                "year": y,
                "cit": round(cit, 6),
                "excluded": False,
                "tax": tax,
                "revenue": rev,
            }
        )
        valid.append(cit)
    latest_valid = None
    for row in reversed(series):
        if not row.get("excluded") and row.get("cit") is not None:
            latest_valid = float(row["cit"])
            break
    median_valid = statistics.median(valid) if valid else None
    return {
        "series": series,
        "valid_rates": valid,
        "latest_valid": latest_valid,
        "median_valid": median_valid,
    }


def synthesize_company_contribution(
    data: FinancialData,
    industry: str,
    *,
    mode: str = "max_hub_latest",
) -> Dict[str, Any]:
    """E3 = max(WB中枢, 最近有效历史贡献率)。"""
    from . import industry as ind_mod

    hub = ind_mod.get_income_tax_contribution_rate(industry or data.industry or "", mode="hub")
    hist = historical_cit_rates(data)
    latest = hist.get("latest_valid")
    median = hist.get("median_valid")
    if mode == "max_hub_median" and median is not None:
        e3 = max(hub, float(median))
        basis = "median_valid"
    elif latest is not None:
        e3 = max(hub, float(latest))
        basis = "latest_valid"
    else:
        e3 = hub
        basis = "hub_only"
    return {
        "company_contribution_rate": round(e3, 6),
        "wb_hub": round(hub, 6),
        "latest_valid": latest,
        "median_valid": median,
        "basis": basis,
        "historical": hist,
    }


def detect_expense_anomalies(data: FinancialData) -> Dict[str, Any]:
    """费用科目异常：YoY 跳变、销售近零、财务暴涨等。"""
    years = sorted(data.years or [])
    anomalies: List[Dict[str, Any]] = []
    by_year: Dict[str, dict] = {}
    subjects = ("销售费用", "管理费用", "研发费用", "财务费用")

    for y in years:
        rev = _amt0(data, "营业收入", y)
        if rev <= 0:
            continue
        row = {"revenue": rev}
        for s in subjects:
            a = _amt0(data, s, y)
            row[s] = a
            row[f"{s}_ratio"] = a / rev
        by_year[str(y)] = row

    year_list = [y for y in years if str(y) in by_year]
    for i in range(1, len(year_list)):
        y0, y1 = year_list[i - 1], year_list[i]
        r0, r1 = by_year[str(y0)], by_year[str(y1)]
        for s in subjects:
            k = f"{s}_ratio"
            d = float(r1.get(k) or 0) - float(r0.get(k) or 0)
            if abs(d) >= FEE_RATIO_JUMP:
                anomalies.append(
                    {
                        "type": "fee_ratio_jump",
                        "subject": s,
                        "from_year": y0,
                        "to_year": y1,
                        "delta_pp": round(d * 100, 2),
                        "severity": "高" if abs(d) >= 0.04 else "中",
                        "message": (
                            f"{s} 占营收 {y0}→{y1} 变化 {d*100:+.2f} 个百分点"
                        ),
                    }
                )

    # 销售近零（最近年）
    if year_list:
        yl = year_list[-1]
        sell_r = float(by_year[str(yl)].get("销售费用_ratio") or 0)
        if sell_r < NEAR_ZERO_SELL_RATIO:
            anomalies.append(
                {
                    "type": "near_zero_selling",
                    "subject": "销售费用",
                    "year": yl,
                    "ratio": sell_r,
                    "severity": "低",
                    "message": (
                        f"{yl} 销售费用/营收≈{sell_r*100:.3f}%，"
                        "工程/装饰型无销售费用，禁止虚增销售预算"
                    ),
                }
            )

    return {"by_year": by_year, "anomalies": anomalies}


def robust_subject_ratio(
    data: FinancialData,
    subject: str,
    *,
    drop_max: bool = True,
) -> Optional[float]:
    """科目占营收稳健比率：各年比率中位数；可选去掉最大异常年后再中位。"""
    years = sorted(data.years or [])
    ratios: List[float] = []
    for y in years:
        rev = _amt0(data, "营业收入", y)
        if rev <= 0:
            continue
        a = _amt0(data, subject, y)
        ratios.append(a / rev)
    if not ratios:
        return None
    if drop_max and len(ratios) >= 3:
        ratios = sorted(ratios)[:-1]  # 去掉最大
    return float(statistics.median(ratios))


def build_data_quality(
    data: FinancialData,
    *,
    ocr_texts: Sequence[str] | None = None,
    parse_notes: Sequence[str] | None = None,
) -> Dict[str, Any]:
    """汇总数据质量块（API / 导出共用）。"""
    meta = data.parsed_meta or {}
    text_layer = bool(meta.get("text_layer"))
    ocr_used = bool(meta.get("ocr_used")) or bool(ocr_texts)
    # 置信度粗分
    matched = int(meta.get("matched_cells") or meta.get("matched") or 0)
    conf = "high"
    if matched <= 0 and ocr_used:
        conf = "low"
    elif matched < 8:
        conf = "medium"
    rec = meta.get("reconciliation") or {}
    if rec.get("hard_fail"):
        conf = "low"
    elif rec.get("warnings"):
        conf = "medium" if conf == "high" else conf

    std_hints = detect_accounting_standard_hints(ocr_texts)
    std_meta = meta.get("accounting_standard") or std_hints

    return {
        "confidence": conf,
        "text_layer": text_layer,
        "ocr_used": ocr_used,
        "matched_cells": matched,
        "merged_files": meta.get("merged_files"),
        "accounting_standard": std_meta,
        "normalize_notes": list(meta.get("normalize_notes") or []),
        "reconciliation": {
            "ok": rec.get("ok", True),
            "hard_fail": rec.get("hard_fail", False),
            "error_count": len(rec.get("errors") or []),
            "warning_count": len(rec.get("warnings") or []),
            "errors": list(rec.get("errors") or [])[:20],
            "warnings": list(rec.get("warnings") or [])[:20],
        },
        "expense_anomalies": (meta.get("expense_anomalies") or {}).get("anomalies") or [],
        "cit_synthesis": meta.get("cit_synthesis") or {},
        "parse_notes": list(parse_notes or meta.get("warnings") or [])[:30],
        "export_blocked": False,  # 默认不阻断；UI 可提示确认
        "require_confirm": bool(rec.get("hard_fail") or conf == "low"),
    }


def enrich_financial_data(
    data: FinancialData,
    *,
    ocr_texts: Sequence[str] | None = None,
    industry: str = "",
) -> FinancialData:
    """归一 + 勾稽 + 税负/费用异常 → 写入 parsed_meta。"""
    if data.parsed_meta is None:
        data.parsed_meta = {}
    notes = normalize_subjects(data)
    rec = reconcile_income_statement(data)
    anomalies = detect_expense_anomalies(data)
    ind = industry or data.industry or ""
    cit = synthesize_company_contribution(data, ind)
    data.parsed_meta["normalize_notes"] = notes
    data.parsed_meta["reconciliation"] = rec
    data.parsed_meta["expense_anomalies"] = anomalies
    data.parsed_meta["cit_synthesis"] = cit
    if ocr_texts:
        std = detect_accounting_standard_hints(ocr_texts)
        if std:
            data.parsed_meta.setdefault("accounting_standard", std)
    data.parsed_meta["data_quality"] = build_data_quality(data, ocr_texts=ocr_texts)
    # 合并 warnings
    warns = list(data.parsed_meta.get("warnings") or [])
    warns.extend(rec.get("warnings") or [])
    for a in anomalies.get("anomalies") or []:
        warns.append(a.get("message") or str(a))
    data.parsed_meta["warnings"] = warns
    return data
