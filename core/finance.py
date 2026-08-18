"""利润宝 · 确定性财务计算引擎（S3）。

所有计算为确定性数学，可逐笔由原始报表反算复核（ADR-002）。
严格遵循质量红线：增值税税负率为估算值，必须显著标注。
"""
from __future__ import annotations

from typing import Dict, Tuple

# 增值税附加税费占增值税比例（用于反推估算）。行业经验值约 12%。
VAT_SURCHARGE_RATIO = 0.12

# 增值税税负率口径标注（质量红线）
VAT_ESTIMATE_NOTE = "估算值（基于税金及附加反推）"
BASE_MISSING_NOTE = "基数缺失"

# 金额 / 百分比精度
PRECISION = 2


def _safe_div(numerator: float, denominator: float) -> Tuple[float, bool]:
    """返回 (value, ok)。分母为 0 时 value=0.0, ok=False。"""
    if denominator == 0 or denominator is None:
        return 0.0, False
    return numerator / denominator, True


def _round(value: float) -> float:
    return round(value, PRECISION)


def vat_tax_rate(tax_and_surcharge: float, revenue: float) -> Tuple[float, str]:
    """增值税税负率（估算）。

    公式：估算增值税 = 税金及附加 ÷ 12%；税负率 = 估算增值税 ÷ 营业收入 × 100%。
    返回 (百分比, 标注)。营收为 0 时返回 (0.0, '基数缺失')。
    """
    if revenue == 0:
        return 0.0, BASE_MISSING_NOTE
    est_vat = tax_and_surcharge / VAT_SURCHARGE_RATIO
    rate = est_vat / revenue * 100.0
    return _round(rate), VAT_ESTIMATE_NOTE


def income_tax_rate(income_tax: float, revenue: float) -> Tuple[float, str]:
    val, ok = _safe_div(income_tax, revenue)
    if not ok:
        return 0.0, BASE_MISSING_NOTE
    return _round(val * 100.0), ""


def composite_tax_rate(tax_and_surcharge: float, income_tax: float, revenue: float) -> Tuple[float, str]:
    val, ok = _safe_div(tax_and_surcharge + income_tax, revenue)
    if not ok:
        return 0.0, BASE_MISSING_NOTE
    return _round(val * 100.0), ""


def gross_margin(revenue: float, cost: float) -> Tuple[float, str]:
    val, ok = _safe_div(revenue - cost, revenue)
    if not ok:
        return 0.0, BASE_MISSING_NOTE
    return _round(val * 100.0), ""


def net_margin(net_profit: float, revenue: float) -> Tuple[float, str]:
    val, ok = _safe_div(net_profit, revenue)
    if not ok:
        return 0.0, BASE_MISSING_NOTE
    return _round(val * 100.0), ""


def expense_ratio(expense: float, revenue: float) -> Tuple[float, str]:
    val, ok = _safe_div(expense, revenue)
    if not ok:
        return 0.0, BASE_MISSING_NOTE
    return _round(val * 100.0), ""


def growth_rate(current: float, previous: float) -> Tuple[float, str]:
    """环比 / 同比增长率（百分比）。previous=0 时返回 (0.0, '无同比基数')。"""
    val, ok = _safe_div(current - previous, previous)
    if not ok:
        return 0.0, "无同比基数"
    return _round(val * 100.0), ""


def value_add_estimate(current: float, target: float, tax_rate: float) -> float:
    """增值测算（保留以兼容旧测试，但语义已细化）。

    旧公式：(目标值 - 当前值) × 税率。
    新业务应优先使用 `cost_saving_estimate` / `tax_saving_estimate` / `tax_impact_estimate`，
    它们分别对应成本节约、税收节约、税负影响，避免方向错误与跨量纲。
    """
    return _round((target - current) * tax_rate)


# ── T6.4 P0-1/P0-2：分离节税 / 成本节约 / 税负影响 ────────────────────────
def cost_saving_estimate(current: float, target: float) -> float:
    """成本节约（金额，元）= max(0, 当前值 - 目标值)。

    业务语义：仅当目标值小于当前值（即压降费用）时才产生正的成本节约。
    目标值 ≥ 当前值时成本节约为 0（不应出现负节约）。
    """
    if current is None or target is None:
        return 0.0
    diff = float(current) - float(target)
    return _round(diff) if diff > 0 else 0.0


def tax_saving_estimate(current_amount: float, target_amount: float,
                        tax_rate: float, deduction_rate: float = 1.0) -> float:
    """税收节约（金额，元）。

    业务语义：合法税务筹划带来的所得税减少，仅在「增加可扣除投入」时为正：
    - 研发费用加计扣除：target > current 时新增研发投入享受加计扣除
    - 限额内据实扣除：把超限费用压回限额内，使原本不可扣部分重新可扣

    公式：税收节约 = max(0, (target - current)) × deduction_rate × tax_rate

    参数：
        current_amount: 当前可扣除金额（元）
        target_amount:  目标可扣除金额（元）
        tax_rate:       所得税税率（小数，如 0.25）
        deduction_rate: 加计扣除比例（如研发 100% 加计扣除取 1.0；
                        普通据实扣除取 1.0；研发费用 100% 加计扣除下总扣除为 200%）
    """
    if current_amount is None or target_amount is None or tax_rate is None:
        return 0.0
    delta = float(target_amount) - float(current_amount)
    if delta <= 0:
        return 0.0
    return _round(delta * float(deduction_rate) * float(tax_rate))


def tax_impact_estimate(current_amount: float, target_amount: float,
                        tax_rate: float) -> float:
    """税负影响（金额，元，可正可负）。

    业务语义：费用压降带来的所得税增加（因为可扣除金额减少）。
    - 正数：压降费用 → 可扣除减少 → 所得税增加
    - 负数：增加费用 → 可扣除增加 → 所得税减少

    公式：税负影响 = max(0, current - target) × tax_rate
    返回值为正表示「所得税增加」，需在报告中以正数+文字说明，不得标成"节税"。
    """
    if current_amount is None or target_amount is None or tax_rate is None:
        return 0.0
    delta = float(current_amount) - float(target_amount)
    if delta <= 0:
        # 费用增加 → 可扣除增加 → 所得税减少（负数）
        return _round(delta * float(tax_rate))
    # 费用压降 → 可扣除减少 → 所得税增加（正数）
    return _round(delta * float(tax_rate))


def net_benefit_estimate(cost_saving: float, tax_saving: float,
                         tax_impact: float) -> float:
    """综合净影响 = 成本节约 + 税收节约 - 税负影响。

    业务语义：当且仅当三项单位一致（均为元）时才可汇总。
    税负影响为正表示所得税增加，故以减号计入净影响。
    """
    return _round(float(cost_saving) + float(tax_saving) - float(tax_impact))


def compute_year_indicators(data, year: int) -> Dict[str, object]:
    """对单个年份计算全部确定性指标，返回结构化结果字典。

    data: FinancialData 实例。缺失科目以 0.0 参与计算并标注。
    """
    inc = data.income_statement
    get = lambda acc: (inc.get(acc, {}) or {}).get(year, 0.0) or 0.0

    revenue = get("营业收入")
    cost = get("营业成本")
    tax_surcharge = get("税金及附加")
    selling = get("销售费用")
    admin = get("管理费用")
    rd = get("研发费用")
    fin = get("财务费用")
    income_tax = get("所得税费用")
    net_profit = get("净利润")

    indicators: Dict[str, object] = {}
    vat, vat_note = vat_tax_rate(tax_surcharge, revenue)
    indicators["增值税税负率"] = {"value": vat, "note": vat_note, "estimate": True}

    itr, itr_note = income_tax_rate(income_tax, revenue)
    indicators["所得税税负率"] = {"value": itr, "note": itr_note, "estimate": False}

    comp, comp_note = composite_tax_rate(tax_surcharge, income_tax, revenue)
    indicators["综合税负率"] = {"value": comp, "note": comp_note, "estimate": False}

    gm, gm_note = gross_margin(revenue, cost)
    indicators["毛利率"] = {"value": gm, "note": gm_note, "estimate": False}

    nm, nm_note = net_margin(net_profit, revenue)
    indicators["净利率"] = {"value": nm, "note": nm_note, "estimate": False}

    sell_val, sell_note = expense_ratio(selling, revenue)
    indicators["销售费用率"] = {"value": sell_val, "note": sell_note, "estimate": False}

    admin_val, admin_note = expense_ratio(admin, revenue)
    indicators["管理费用率"] = {"value": admin_val, "note": admin_note, "estimate": False}

    rd_val, rd_note = expense_ratio(rd, revenue)
    indicators["研发费用率"] = {"value": rd_val, "note": rd_note, "estimate": False}

    fin_val, fin_note = expense_ratio(fin, revenue)
    indicators["财务费用率"] = {"value": fin_val, "note": fin_note, "estimate": False}

    indicators["营业收入"] = revenue
    indicators["利润总额"] = get("利润总额")
    return indicators
