"""利润宝 · 行业对标基准库（税负率 / 费用率）。

提供确定性的行业合理区间查询；未知行业回退制造业并标注（§9.1）。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

DEFAULT_INDUSTRY = "制造业"

# 区间含义：min / max / median，单位均为百分比（%）
INDUSTRY_BENCHMARKS: Dict[str, Dict[str, Dict[str, float]]] = {
    "制造业": {
        "vat_tax_rate":           {"min": 2.0, "max": 5.0, "median": 3.5},
        "income_tax_rate":        {"min": 1.0, "max": 3.0, "median": 2.0},
        "gross_margin":           {"min": 15.0, "max": 30.0, "median": 22.0},
        "selling_expense_ratio":  {"min": 2.0, "max": 8.0, "median": 5.0},
        "admin_expense_ratio":    {"min": 5.0, "max": 15.0, "median": 10.0},
        "rd_expense_ratio":       {"min": 3.0, "max": 8.0, "median": 5.0},
        "financial_expense_ratio":{"min": 1.0, "max": 5.0, "median": 2.0},
    },
    "批发零售业": {
        "vat_tax_rate":           {"min": 1.5, "max": 4.0, "median": 2.5},
        "income_tax_rate":        {"min": 0.8, "max": 2.5, "median": 1.5},
        "gross_margin":           {"min": 8.0, "max": 20.0, "median": 12.0},
        "selling_expense_ratio":  {"min": 2.0, "max": 10.0, "median": 5.0},
        "admin_expense_ratio":    {"min": 3.0, "max": 12.0, "median": 7.0},
        "rd_expense_ratio":       {"min": 0.0, "max": 3.0, "median": 1.0},
        "financial_expense_ratio":{"min": 1.0, "max": 6.0, "median": 3.0},
    },
    "服务业": {
        "vat_tax_rate":           {"min": 3.0, "max": 6.0, "median": 4.5},
        "income_tax_rate":        {"min": 1.5, "max": 4.0, "median": 2.5},
        "gross_margin":           {"min": 20.0, "max": 45.0, "median": 30.0},
        "selling_expense_ratio":  {"min": 3.0, "max": 12.0, "median": 7.0},
        "admin_expense_ratio":    {"min": 8.0, "max": 20.0, "median": 13.0},
        "rd_expense_ratio":       {"min": 2.0, "max": 8.0, "median": 4.0},
        "financial_expense_ratio":{"min": 0.5, "max": 4.0, "median": 1.5},
    },
    "建筑业": {
        "vat_tax_rate":           {"min": 2.5, "max": 5.0, "median": 3.5},
        "income_tax_rate":        {"min": 1.0, "max": 3.0, "median": 2.0},
        "gross_margin":           {"min": 10.0, "max": 22.0, "median": 15.0},
        "selling_expense_ratio":  {"min": 1.0, "max": 5.0, "median": 3.0},
        "admin_expense_ratio":    {"min": 4.0, "max": 12.0, "median": 8.0},
        "rd_expense_ratio":       {"min": 0.0, "max": 3.0, "median": 1.5},
        "financial_expense_ratio":{"min": 1.0, "max": 6.0, "median": 3.0},
    },
    "软件和信息技术服务业": {
        "vat_tax_rate":           {"min": 3.0, "max": 6.0, "median": 4.0},
        "income_tax_rate":        {"min": 1.5, "max": 4.0, "median": 2.5},
        "gross_margin":           {"min": 30.0, "max": 60.0, "median": 45.0},
        "selling_expense_ratio":  {"min": 5.0, "max": 15.0, "median": 10.0},
        "admin_expense_ratio":    {"min": 8.0, "max": 20.0, "median": 14.0},
        "rd_expense_ratio":       {"min": 5.0, "max": 15.0, "median": 10.0},
        "financial_expense_ratio":{"min": 0.5, "max": 4.0, "median": 1.5},
    },
    "医药制造业": {
        "vat_tax_rate":           {"min": 2.5, "max": 6.0, "median": 4.0},
        "income_tax_rate":        {"min": 1.5, "max": 4.0, "median": 2.5},
        "gross_margin":           {"min": 25.0, "max": 55.0, "median": 40.0},
        "selling_expense_ratio":  {"min": 8.0, "max": 20.0, "median": 15.0},
        "admin_expense_ratio":    {"min": 5.0, "max": 15.0, "median": 10.0},
        "rd_expense_ratio":       {"min": 5.0, "max": 15.0, "median": 8.0},
        "financial_expense_ratio":{"min": 0.5, "max": 4.0, "median": 1.5},
    },
    "住宿餐饮业": {
        "vat_tax_rate":           {"min": 3.0, "max": 6.0, "median": 4.5},
        "income_tax_rate":        {"min": 1.0, "max": 3.0, "median": 2.0},
        "gross_margin":           {"min": 15.0, "max": 35.0, "median": 25.0},
        "selling_expense_ratio":  {"min": 3.0, "max": 12.0, "median": 7.0},
        "admin_expense_ratio":    {"min": 8.0, "max": 20.0, "median": 14.0},
        "rd_expense_ratio":       {"min": 0.0, "max": 2.0, "median": 0.5},
        "financial_expense_ratio":{"min": 1.0, "max": 6.0, "median": 3.0},
    },
    "交通运输业": {
        "vat_tax_rate":           {"min": 1.0, "max": 3.0, "median": 2.0},
        "income_tax_rate":        {"min": 0.5, "max": 2.0, "median": 1.0},
        "gross_margin":           {"min": 10.0, "max": 25.0, "median": 16.0},
        "selling_expense_ratio":  {"min": 2.0, "max": 10.0, "median": 5.0},
        "admin_expense_ratio":    {"min": 5.0, "max": 15.0, "median": 9.0},
        "rd_expense_ratio":       {"min": 0.0, "max": 2.0, "median": 0.5},
        "financial_expense_ratio":{"min": 2.0, "max": 8.0, "median": 4.0},
    },
    "房地产业": {
        "vat_tax_rate":           {"min": 2.0, "max": 5.0, "median": 3.0},
        "income_tax_rate":        {"min": 1.0, "max": 3.0, "median": 2.0},
        "gross_margin":           {"min": 15.0, "max": 35.0, "median": 25.0},
        "selling_expense_ratio":  {"min": 2.0, "max": 8.0, "median": 4.0},
        "admin_expense_ratio":    {"min": 4.0, "max": 12.0, "median": 8.0},
        "rd_expense_ratio":       {"min": 0.0, "max": 2.0, "median": 0.5},
        "financial_expense_ratio":{"min": 3.0, "max": 10.0, "median": 6.0},
    },
    "电力热力生产供应业": {
        "vat_tax_rate":           {"min": 1.5, "max": 4.0, "median": 2.5},
        "income_tax_rate":        {"min": 1.0, "max": 3.0, "median": 2.0},
        "gross_margin":           {"min": 15.0, "max": 35.0, "median": 22.0},
        "selling_expense_ratio":  {"min": 1.0, "max": 5.0, "median": 2.0},
        "admin_expense_ratio":    {"min": 4.0, "max": 12.0, "median": 8.0},
        "rd_expense_ratio":       {"min": 0.0, "max": 3.0, "median": 1.0},
        "financial_expense_ratio":{"min": 2.0, "max": 8.0, "median": 4.0},
    },
}

# ── WB 行业基准数据库 v1.0（2026-08-07）──────────────────────────────
# 四大核心指标：增值税税负率 / 企业所得税税负率 / 毛利率 / 净利率（单位 %）
# 每项字段：median=经验中枢, min/max=参考区间(P25-P75),
#          low_warn/high_warn=预警偏低/偏高线, budget_min/budget_max=预算建议区间
# 税负率类按「经验中枢±浮动系数」：参考区间=[中枢×0.85, 中枢×1.4]，
# 预警低=中枢×0.7、预警高=中枢×2.5；毛利率/净利率按分位数法（P10/P90 预警）。
# 来源：AGENTS.md「行业基准数据库」节（原《WB-行业基准数据库》文档 20260807，已整合）

_REFERENCE_KEYS = ("vat_tax_rate", "income_tax_rate", "gross_margin", "net_margin")


def _ref(median: float, rmin: float, rmax: float, low: float, high: float,
         bmin: float, bmax: float) -> Dict[str, float]:
    """构造一个指标的基准字段（median/min/max/预警线/预算区间）。"""
    return {
        "median": median,
        "min": rmin,
        "max": rmax,
        "low_warn": low,
        "high_warn": high,
        "budget_min": bmin,
        "budget_max": bmax,
    }


# 每行：(行业, VAT(中枢,参考低,参考高,预警低,预警高,预算低,预算高),
#        CIT(同 VAT 七元组), GM(参考低,参考高,预警低,预警高,预算低,预算高),
#        NM(同 GM 六元组))
_REFERENCE_ROWS: Tuple[Tuple[str, Tuple, Tuple, Tuple, Tuple], ...] = (
    ("农副食品加工业", (3.5, 3.0, 5.0, 2.5, 8.0, 3.5, 4.2),
     (1.0, 0.8, 1.5, 0.6, 2.5, 1.0, 1.2), (10.0, 15.0, 8.0, 22.0, 12.0, 14.0),
     (2.0, 5.0, 1.5, 8.0, 3.0, 4.0)),
    ("食品制造业", (4.5, 3.8, 6.0, 3.2, 10.0, 4.5, 5.5),
     (1.0, 0.8, 1.5, 0.6, 2.5, 1.0, 1.2), (25.0, 35.0, 18.0, 50.0, 30.0, 33.0),
     (5.0, 12.0, 3.0, 18.0, 8.0, 10.0)),
    ("纺织服装制造业", (2.91, 2.5, 4.0, 2.0, 7.0, 2.9, 3.5),
     (1.0, 0.8, 1.5, 0.6, 2.5, 1.0, 1.2), (30.0, 45.0, 22.0, 60.0, 38.0, 42.0),
     (3.0, 8.0, 2.0, 13.0, 5.0, 7.0)),
    ("造纸及纸制品业", (5.0, 4.2, 6.5, 3.5, 12.0, 5.0, 6.0),
     (1.0, 0.8, 1.5, 0.6, 2.5, 1.0, 1.2), (15.0, 22.0, 11.0, 32.0, 18.0, 21.0),
     (2.0, 6.0, 1.5, 10.0, 4.0, 5.0)),
    ("化学原料及制品制造业", (3.35, 2.8, 4.5, 2.3, 8.0, 3.35, 4.0),
     (2.0, 1.5, 3.0, 1.2, 4.5, 2.0, 2.4), (15.0, 25.0, 11.0, 35.0, 20.0, 23.0),
     (2.0, 6.0, 1.5, 10.0, 4.0, 5.0)),
    ("医药制造业", (8.5, 7.0, 11.0, 6.0, 18.0, 8.5, 10.0),
     (2.5, 2.0, 3.5, 1.5, 5.0, 2.5, 3.0), (40.0, 55.0, 28.0, 75.0, 45.0, 52.0),
     (8.0, 15.0, 5.0, 22.0, 10.0, 13.0)),
    ("非金属矿物制品业（建材）", (5.0, 4.2, 6.5, 3.5, 12.0, 5.0, 6.0),
     (2.5, 2.0, 3.5, 1.5, 5.0, 2.5, 3.0), (20.0, 30.0, 15.0, 42.0, 25.0, 28.0),
     (3.0, 8.0, 2.0, 13.0, 5.0, 7.0)),
    ("金属制品业", (2.2, 1.8, 3.2, 1.5, 5.5, 2.2, 2.7),
     (2.0, 1.5, 3.0, 1.2, 4.5, 2.0, 2.4), (15.0, 25.0, 12.0, 35.0, 20.0, 23.0),
     (3.0, 7.0, 2.0, 11.0, 5.0, 6.0)),
    ("通用/专用设备制造业", (3.7, 3.0, 5.0, 2.6, 9.0, 3.7, 4.5),
     (2.0, 1.5, 3.0, 1.2, 4.5, 2.0, 2.4), (22.0, 35.0, 16.0, 48.0, 28.0, 32.0),
     (6.0, 12.0, 4.0, 18.0, 8.0, 10.0)),
    ("计算机通信设备制造业", (2.65, 2.2, 3.8, 1.9, 6.5, 2.65, 3.2),
     (2.0, 1.5, 3.0, 1.2, 4.5, 2.0, 2.4), (20.0, 30.0, 15.0, 45.0, 25.0, 28.0),
     (4.0, 10.0, 2.5, 16.0, 6.0, 8.0)),
    ("电气机械及器材制造业", (3.7, 3.0, 5.0, 2.6, 9.0, 3.7, 4.5),
     (2.0, 1.5, 3.0, 1.2, 4.5, 2.0, 2.4), (20.0, 30.0, 15.0, 45.0, 25.0, 28.0),
     (4.0, 9.0, 2.5, 15.0, 6.0, 8.0)),
    ("商业批发业", (0.9, 0.7, 1.5, 0.6, 2.5, 0.9, 1.1),
     (1.0, 0.8, 1.5, 0.6, 2.5, 1.0, 1.2), (8.0, 15.0, 6.0, 22.0, 12.0, 14.0),
     (1.0, 3.0, 0.5, 6.0, 2.0, 3.0)),
    ("商业零售业", (2.5, 2.0, 3.5, 1.7, 6.0, 2.5, 3.0),
     (1.5, 1.0, 2.5, 0.8, 4.0, 1.5, 1.8), (25.0, 40.0, 18.0, 55.0, 30.0, 35.0),
     (2.0, 6.0, 1.0, 10.0, 3.0, 5.0)),
    ("建筑业", (3.0, 2.5, 4.0, 2.0, 7.0, 3.0, 3.6),
     (1.5, 1.0, 2.5, 0.8, 4.0, 1.5, 1.8), (10.0, 13.0, 8.0, 18.0, 11.0, 12.0),
     (1.5, 3.0, 1.0, 5.0, 2.0, 3.0)),
    ("房地产业", (1.5, 1.0, 2.5, 0.7, 4.0, 1.5, 1.8),
     (4.0, 2.5, 6.0, 1.5, 10.0, 4.0, 5.0), (10.0, 20.0, 8.0, 30.0, 15.0, 18.0),
     (3.0, 8.0, 1.0, 14.0, 5.0, 7.0)),
    ("软件与信息技术服务业", (3.0, 2.5, 4.5, 2.0, 7.0, 3.0, 3.6),
     (2.0, 1.5, 3.0, 1.2, 4.5, 2.0, 2.4), (50.0, 75.0, 38.0, 95.0, 60.0, 68.0),
     (15.0, 22.0, 10.0, 30.0, 18.0, 21.0)),
    ("专业技术服务业", (3.0, 2.5, 4.5, 2.0, 7.0, 3.0, 3.6),
     (2.5, 2.0, 3.5, 1.5, 5.0, 2.5, 3.0), (35.0, 55.0, 25.0, 75.0, 45.0, 52.0),
     (8.0, 15.0, 5.0, 22.0, 10.0, 13.0)),
    ("居民服务业", (3.0, 2.5, 4.5, 2.0, 7.0, 3.0, 3.6),
     (1.2, 0.8, 2.0, 0.6, 3.0, 1.2, 1.5), (40.0, 60.0, 28.0, 85.0, 50.0, 57.0),
     (5.0, 12.0, 3.0, 18.0, 8.0, 11.0)),
    ("住宿业", (3.0, 2.5, 4.5, 2.0, 7.0, 3.0, 3.6),
     (2.0, 1.2, 3.0, 0.8, 4.5, 2.0, 2.4), (35.0, 55.0, 25.0, 75.0, 45.0, 52.0),
     (6.0, 12.0, 4.0, 18.0, 8.0, 11.0)),
    ("餐饮业", (3.0, 2.5, 4.5, 2.0, 7.0, 3.0, 3.6),
     (2.0, 1.2, 3.0, 0.8, 4.5, 2.0, 2.4), (50.0, 65.0, 35.0, 95.0, 55.0, 62.0),
     (5.0, 10.0, 3.0, 16.0, 7.0, 9.0)),
    ("租赁业", (3.0, 2.5, 4.5, 2.0, 7.0, 3.0, 3.6),
     (1.5, 1.0, 2.5, 0.8, 4.0, 1.5, 1.8), (40.0, 60.0, 28.0, 85.0, 50.0, 57.0),
     (5.0, 10.0, 3.0, 16.0, 7.0, 9.0)),
    ("商务服务业", (3.0, 2.5, 4.5, 2.0, 7.0, 3.0, 3.6),
     (2.5, 2.0, 3.5, 1.5, 5.0, 2.5, 3.0), (40.0, 60.0, 28.0, 85.0, 50.0, 57.0),
     (5.0, 12.0, 3.0, 18.0, 8.0, 11.0)),
)


def _build_reference_db() -> Dict[str, Dict[str, Dict[str, float]]]:
    """把文档 22 行业条目构建为 {行业: {指标: 基准字段}}。"""
    db: Dict[str, Dict[str, Dict[str, float]]] = {}
    for name, vat, cit, gm, nm in _REFERENCE_ROWS:
        db[name] = {
            "vat_tax_rate": _ref(*vat),
            "income_tax_rate": _ref(*cit),
            "gross_margin": _ref((gm[0] + gm[1]) / 2, *gm),
            "net_margin": _ref((nm[0] + nm[1]) / 2, *nm),
        }
    return db


INDUSTRY_REFERENCE_DB = _build_reference_db()

# 应用层行业 → 文档行业聚合（未覆盖的交通运输/电力热力走自定义行）
_APP_REF_MAP: Dict[str, Tuple[str, ...]] = {
    "批发零售业": ("商业批发业", "商业零售业"),
    "服务业": ("居民服务业", "住宿业", "餐饮业", "租赁业", "商务服务业", "专业技术服务业"),
    "建筑业": ("建筑业",),
    "软件和信息技术服务业": ("软件与信息技术服务业",),
    "医药制造业": ("医药制造业",),
    "住宿餐饮业": ("住宿业", "餐饮业"),
    "房地产业": ("房地产业",),
}

_APP_CUSTOM_REF: Dict[str, Dict[str, Tuple[float, ...]]] = {
    # 制造业综合：GB/T C 类多子行业合并口径（文档未给单一综合行，取典型制造区间）
    "制造业": {
        "vat_tax_rate": (3.5, 2.5, 5.0, 2.45, 8.75, 3.5, 4.2),
        "income_tax_rate": (2.0, 1.2, 3.0, 1.2, 5.0, 2.0, 2.4),
        "gross_margin": (25.0, 20.0, 30.0, 15.0, 45.0, 25.0, 28.0),
        "net_margin": (6.0, 4.0, 8.0, 3.0, 13.0, 6.0, 8.0),
    },
    "交通运输业": {
        "vat_tax_rate": (2.0, 1.0, 3.0, 1.4, 5.0, 2.0, 2.4),
        "income_tax_rate": (1.0, 0.5, 2.0, 0.6, 2.5, 1.0, 1.2),
        "gross_margin": (17.5, 10.0, 25.0, 8.0, 32.0, 14.0, 18.0),
        "net_margin": (5.5, 3.0, 8.0, 2.0, 13.0, 4.0, 6.0),
    },
    "电力热力生产供应业": {
        "vat_tax_rate": (2.5, 1.5, 4.0, 1.75, 6.25, 2.5, 3.0),
        "income_tax_rate": (2.0, 1.0, 3.0, 1.2, 5.0, 2.0, 2.4),
        "gross_margin": (25.0, 15.0, 35.0, 11.0, 45.0, 20.0, 25.0),
        "net_margin": (5.5, 3.0, 8.0, 2.0, 13.0, 4.0, 6.0),
    },
}


def _aggregate_ref(names: Tuple[str, ...]) -> Dict[str, Dict[str, float]]:
    """聚合多个文档行业的基准：中枢取均值，区间/预警线取覆盖范围。"""
    out: Dict[str, Dict[str, float]] = {}
    for key in _REFERENCE_KEYS:
        items = [INDUSTRY_REFERENCE_DB[name][key] for name in names]
        out[key] = _ref(
            round(sum(item["median"] for item in items) / len(items), 2),
            min(item["min"] for item in items),
            max(item["max"] for item in items),
            min(item["low_warn"] for item in items),
            max(item["high_warn"] for item in items),
            min(item["budget_min"] for item in items),
            max(item["budget_max"] for item in items),
        )
    return out


def _app_reference(industry: str) -> Dict[str, Dict[str, float]]:
    """返回应用层行业四大指标基准（未知行业回退制造业）。"""
    names = _APP_REF_MAP.get(industry)
    if names:
        return _aggregate_ref(names)
    if industry in _APP_CUSTOM_REF:
        return {
            key: _ref(*tuple(item))
            for key, item in _APP_CUSTOM_REF[industry].items()
        }
    return _app_reference("制造业")


def _attach_reference_fields(benchmarks: Dict) -> Dict:
    """为每个应用行业补充四大指标基准字段（保留原费用率等字段）。"""
    for industry, bench in benchmarks.items():
        ref = _app_reference(industry)
        for key in _REFERENCE_KEYS:
            item = dict(bench.get(key, {}))
            item.update(ref[key])
            bench[key] = item
    return benchmarks


INDUSTRY_BENCHMARKS = _attach_reference_fields(INDUSTRY_BENCHMARKS)

# 行业一句话说明（前端选择器展示）
INDUSTRY_DESCRIPTIONS: Dict[str, str] = {
    "制造业": "生产加工型企业（含机械、电子、食品、纺织、化工等）",
    "批发零售业": "商品流通与销售型企业（含批发、零售、电商）",
    "服务业": "提供非实物服务的企业（含咨询、教育、医疗、文化等）",
    "建筑业": "工程施工、安装与装修型企业",
    "软件和信息技术服务业": "软件开发、信息系统集成与信息技术服务企业",
    "医药制造业": "药品、医疗器械等医药产品生产型企业",
    "住宿餐饮业": "酒店、餐饮等生活服务型企业",
    "交通运输业": "物流、客运、货运等交通运输服务企业",
    "房地产业": "房地产开发、经营与物业管理企业",
    "电力热力生产供应业": "电力、热力生产与供应企业",
}

# 行业基准可用指标键
BENCHMARK_KEYS = (
    "vat_tax_rate", "income_tax_rate", "gross_margin",
    "net_margin",
    "selling_expense_ratio", "admin_expense_ratio",
    "rd_expense_ratio", "financial_expense_ratio",
)

# 规则推荐：企业名称关键词 → 行业（无 AI 时的兜底）
INDUSTRY_RULE_KEYWORDS: List[Tuple[Tuple[str, ...], str]] = [
    (("软件", "信息技术", "信息科技", "网络科技", "科技", "数据", "互联网", "云计算", "智能科技"), "软件和信息技术服务业"),
    (("医药", "制药", "生物", "医疗科技", "医疗器械", "药业"), "医药制造业"),
    (("建筑", "工程", "安装", "装饰", "施工", "建设"), "建筑业"),
    (("餐饮", "酒店", "住宿", "饭店", "文旅", "旅游"), "住宿餐饮业"),
    (("物流", "运输", "货运", "航运", "快递", "配送", "交通"), "交通运输业"),
    (("房地产", "置业", "物业", "地产", "房产"), "房地产业"),
    (("电力", "热力", "能源", "供电", "发电"), "电力热力生产供应业"),
    (("商贸", "贸易", "批发", "零售", "超市", "百货", "连锁", "电商"), "批发零售业"),
]


def list_industries() -> list:
    return list(INDUSTRY_BENCHMARKS.keys())


def list_industries_with_desc() -> List[Dict[str, str]]:
    """返回 [{name, desc}]，供前端行业选择器展示说明。"""
    return [
        {"name": name, "desc": INDUSTRY_DESCRIPTIONS.get(name, "")}
        for name in INDUSTRY_BENCHMARKS
    ]


def get_industry_desc(industry: str) -> str:
    """返回行业说明；未知行业返回空串。"""
    return INDUSTRY_DESCRIPTIONS.get(industry, "")


def get_benchmark(industry: str) -> Tuple[Dict[str, Dict[str, float]], bool]:
    """返回行业基准；未知行业回退制造业，第二返回值为是否回退。"""
    if industry in INDUSTRY_BENCHMARKS:
        return INDUSTRY_BENCHMARKS[industry], False
    return INDUSTRY_BENCHMARKS[DEFAULT_INDUSTRY], True


def get_range(industry: str, key: str) -> Dict[str, float]:
    """返回某指标 {min, max, median}；缺失返回全 0。"""
    bench, _ = get_benchmark(industry)
    return bench.get(key, {"min": 0.0, "max": 0.0, "median": 0.0})


def recommend_by_rule(company_name: str, overview: str = "") -> Tuple[str, str]:
    """规则推荐行业：按企业名称关键词匹配；未命中返回默认行业。

    返回 (行业名, 推荐理由)。overview 为可选财务概览文本（当前规则版不使用，
    保留参数以与 AI 路径签名对齐）。
    """
    text = (company_name or "").strip()
    for keywords, industry in INDUSTRY_RULE_KEYWORDS:
        if any(kw in text for kw in keywords):
            return industry, f"企业名称包含「{next(k for k in keywords if k in text)}」，按规则匹配为{industry}（可修改）"
    return DEFAULT_INDUSTRY, f"未匹配到明确关键词，默认推荐{DEFAULT_INDUSTRY}（可修改）"


# ── WB 文档 §七 预算编制参考逻辑 ─────────────────────────────────────
# 净利率预算 ≈ 毛利率预算 − 行业平均费用率
# → 期间费用率 ≈ 毛利率 − 净利率
# 文档明示：制造业费用率约 15~20%，零售业约 20~25%，软件业约 30~40%
_NARRATIVE_FEE_RATE_PCT: Dict[str, Tuple[float, float]] = {
    "制造业": (15.0, 20.0),
    "医药制造业": (15.0, 25.0),
    "批发零售业": (20.0, 25.0),
    "软件和信息技术服务业": (30.0, 40.0),
    "建筑业": (8.0, 12.0),
    "房地产业": (8.0, 15.0),
    "服务业": (25.0, 40.0),
    "住宿餐饮业": (25.0, 45.0),
    "交通运输业": (10.0, 18.0),
    "电力热力生产供应业": (10.0, 18.0),
}


def resolve_industry_key(industry: str) -> str:
    """把自由文本行业名映射到应用层行业键（装饰/装修→建筑业等）。"""
    text = (industry or "").strip()
    if not text:
        return DEFAULT_INDUSTRY
    if text in INDUSTRY_BENCHMARKS:
        return text
    if text in INDUSTRY_REFERENCE_DB:
        for app, names in _APP_REF_MAP.items():
            if text in names:
                return app
        if "制造" in text:
            return "制造业"
        if text == "软件与信息技术服务业":
            return "软件和信息技术服务业"
        return DEFAULT_INDUSTRY
    for keywords, ind in INDUSTRY_RULE_KEYWORDS:
        if any(k in text for k in keywords):
            return ind
    name, _ = recommend_by_rule(text)
    return name


def get_period_expense_ratio_band(industry: str) -> Dict[str, float]:
    """期间费用率（销+管+研+财）/营收 合理带，单位为小数（0.12=12%）。

    依据 WB §七：费用率 ≈ 毛利率预算 − 净利率预算；并与文档叙述区间取覆盖。
    """
    key = resolve_industry_key(industry)
    bench, _ = get_benchmark(key)
    gm = bench.get("gross_margin") or {}
    nm = bench.get("net_margin") or {}
    gm_lo = float(gm.get("budget_min") or gm.get("min") or 0)
    gm_hi = float(gm.get("budget_max") or gm.get("max") or 0)
    nm_lo = float(nm.get("budget_min") or nm.get("min") or 0)
    nm_hi = float(nm.get("budget_max") or nm.get("max") or 0)
    gm_med = float(gm.get("median") or ((gm_lo + gm_hi) / 2 if (gm_lo or gm_hi) else 0))
    nm_med = float(nm.get("median") or ((nm_lo + nm_hi) / 2 if (nm_lo or nm_hi) else 0))
    fee_from_budget_lo = max(0.0, gm_lo - nm_hi)
    fee_from_budget_hi = max(0.0, gm_hi - nm_lo)
    fee_median = max(0.0, gm_med - nm_med)
    n_lo, n_hi = _NARRATIVE_FEE_RATE_PCT.get(
        key, (max(fee_median * 0.85, 8.0), max(fee_median * 1.25, 15.0) if fee_median else (12.0, 18.0))
    )
    band_min = min(fee_from_budget_lo if fee_from_budget_lo > 0 else n_lo, n_lo)
    band_max = max(fee_from_budget_hi if fee_from_budget_hi > 0 else n_hi, n_hi)
    if band_max < band_min:
        band_min, band_max = band_max, band_min
    if fee_median <= 0:
        fee_median = (band_min + band_max) / 2
    fee_median = min(max(fee_median, band_min), band_max)
    return {
        "min": round(band_min / 100.0, 6),
        "max": round(band_max / 100.0, 6),
        "median": round(fee_median / 100.0, 6),
        "budget_min": round(max(band_min, fee_from_budget_lo or band_min) / 100.0, 6),
        "budget_max": round(min(band_max, fee_from_budget_hi or band_max) / 100.0, 6),
        "source": "WB-20260807§七 毛利率−净利率 + 行业费用率叙述",
        "industry_key": key,
    }


def get_income_tax_contribution_rate(industry: str, *, mode: str = "hub") -> float:
    """企业预算所得税贡献率 E3（小数）。

    WB §七：税负率预算下限 = 行业经验中枢（合规红线，不得低于）。
    mode: hub | budget_mid | budget_floor
    """
    key = resolve_industry_key(industry)
    cit = get_range(key, "income_tax_rate")
    if mode == "budget_mid":
        lo = float(cit.get("budget_min") or cit.get("median") or 0)
        hi = float(cit.get("budget_max") or cit.get("median") or 0)
        pct = (lo + hi) / 2 if (lo or hi) else float(cit.get("median") or 1.5)
    elif mode == "budget_floor":
        pct = float(cit.get("budget_min") or cit.get("median") or 1.5)
    else:
        pct = float(cit.get("median") or 1.5)
    return round(pct / 100.0, 6)


def get_industry_vat_hub(industry: str) -> float:
    """增值税税负经验中枢（小数）。"""
    key = resolve_industry_key(industry)
    vat = get_range(key, "vat_tax_rate")
    return round(float(vat.get("median") or 3.0) / 100.0, 6)


def apply_historical_contribution_to_plan(plan, data, *, force: bool = False) -> list:
    """E3 = max(WB 中枢, 最近有效历史所得税/营收)；写入 plan.top_inputs。

    负所得税年份已在 historical_cit_rates 中剔除。
    """
    notes: list = []
    if data is None:
        return notes
    try:
        from . import reconciliation as recon_mod
    except Exception:
        return notes
    industry = getattr(plan, "industry", "") or getattr(data, "industry", "") or DEFAULT_INDUSTRY
    syn = recon_mod.synthesize_company_contribution(data, industry)
    e3 = float(syn.get("company_contribution_rate") or 0)
    if e3 <= 0:
        return notes
    ti = plan.top_inputs
    old = float(getattr(ti, "company_contribution_rate", 0) or 0)
    hub = float(syn.get("wb_hub") or 0)
    # 默认覆盖模板占位；force 或当前低于合成值时抬升（守合规红线）
    if force or old in (0.0, 0.003) or old < e3 - 1e-9:
        ti.company_contribution_rate = e3
        basis = syn.get("basis") or ""
        latest = syn.get("latest_valid")
        notes.append(
            f"E3 企业贡献率 ← max(WB中枢 {hub*100:.2f}%, 历史有效)"
            f"={e3*100:.2f}%（basis={basis}"
            + (f", latest={latest*100:.2f}%" if latest is not None else "")
            + f"；原 {old*100:.2f}%）"
        )
    # E2 同步中枢（若仍为占位）
    old_e2 = float(getattr(ti, "industry_contribution_rate", 0) or 0)
    if force or old_e2 in (0.0, 0.003) or (hub > 0 and old_e2 < hub * 0.5):
        ti.industry_contribution_rate = hub
        notes.append(f"E2 行业贡献率 ← WB 中枢 {hub*100:.2f}%")
    return notes


def apply_wb_top_rates_to_plan(plan, *, force: bool = False) -> list:
    """把 WB 基准写入预算顶栏 E2/E3（所得税贡献率）。

    E2/E3 = 行业所得税税负经验中枢（合规红线，§七）；模板默认 0.30% 视为未核验并覆盖。

    模板关系：E5=E3×C2，E6=E5/E4，E7=C4−E6。
    项目默认 E4=15%（高新）。若仍为小微 5% 而 E3 用行业实缴/营收中枢，
    则 E6=中枢/5% 会虚高导致 E7 为负：仅在 E4<15% 时将倒推税率调到 15%（高新口径），
    不再默认抬到 25%。若 15% 下 E7 仍不足，则按毛利约束下调 E3（不抬高 E4）。
    """
    notes: list = []
    industry = getattr(plan, "industry", "") or DEFAULT_INDUSTRY
    ti = plan.top_inputs
    hub = get_income_tax_contribution_rate(industry, mode="hub")
    defaultish = {0.0, 0.003}
    old_e2 = float(getattr(ti, "industry_contribution_rate", 0) or 0)
    old_e3 = float(getattr(ti, "company_contribution_rate", 0) or 0)
    if force or old_e2 in defaultish or (hub > 0 and old_e2 < hub * 0.5):
        ti.industry_contribution_rate = hub
        if hasattr(ti, "industry_rate_source"):
            ti.industry_rate_source = "WB-行业基准数据库-20260807 所得税税负经验中枢"
        if hasattr(ti, "industry_rate_verified"):
            ti.industry_rate_verified = True
        notes.append(f"E2 行业所得税贡献率 ← WB 中枢 {hub*100:.2f}%（原 {old_e2*100:.2f}%）")
    if force or old_e3 in defaultish or (hub > 0 and old_e3 < hub * 0.5):
        ti.company_contribution_rate = hub
        notes.append(f"E3 企业预算所得税贡献率 ← WB 合规红线/中枢 {hub*100:.2f}%（原 {old_e3*100:.2f}%）")

    # E7 保护：倒推税率与费用率下限兼容（默认高新 15%，不擅自抬到 25%）
    c2 = float(getattr(ti, "budget_revenue", 0) or 0)
    c3 = float(getattr(ti, "budget_cost", 0) or 0)
    e3 = float(getattr(ti, "company_contribution_rate", 0) or 0)
    e4 = float(getattr(ti, "income_tax_rate", 0) or 0)
    if c2 > 0 and e3 > 0 and e4 > 0:
        c4 = c2 - c3
        e5 = e3 * c2
        e6 = e5 / e4
        e7 = c4 - e6
        fee_min = float(get_period_expense_ratio_band(industry).get("min") or 0.08)
        need_e7 = c2 * fee_min * 0.5  # 至少半档费用空间
        if e7 < need_e7 and e4 < 0.15:
            old_e4 = e4
            ti.income_tax_rate = 0.15
            e4 = 0.15
            e6 = e5 / e4
            e7 = c4 - e6
            notes.append(
                f"E4 预算倒推税率 {old_e4*100:.0f}%→15%（高新默认口径；避免中枢/{old_e4*100:.0f}% "
                f"虚高利润总额挤占 E7；小微 5% 与预算倒推分列，可手动改回）"
            )
        if e7 < need_e7:
            # 15%/25% 下仍不足：按毛利约束下调 E3，不抬高税率
            e3_cap = max((c4 / c2) * e4 * 0.85, hub * 0.6) if c2 else hub
            if e3 > e3_cap > 0:
                ti.company_contribution_rate = round(e3_cap, 6)
                notes.append(
                    f"E3 受毛利约束下调至 {e3_cap*100:.2f}%（保证 E7 费用空间且接近 WB 中枢）"
                )
    return notes
