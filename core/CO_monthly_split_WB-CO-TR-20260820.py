"""利润宝 · 年度预算月度拆分引擎（确定性数学，纯标准库）。

数字安全底线（对齐项目数字白名单原则）：
- AI / 规则只决定每行的「形状」（12 个权重或节奏标签）；
- 金额一律由本引擎用「年度金额 × 权重 + 尾差归位」计算；
- 逐行 Σ月 = 年（元级相等）与整表恒等是硬门槛，校验不过不出结果；
- 引擎绝不静默改数，AI 说明中的数字仅记 warning 提示。

plan_rows 契约：[{"row": int, "subject": str, "expense_name": str, "annual": float}]
answers 契约：{question_id: value_str}
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

ENGINE_VERSION = "1.0"

# 刚性费用：金额全年基本恒定，按月平摊
RIGID_UNIFORM_KEYWORDS = (
    "工资", "薪酬", "社保", "公积金", "五险", "房租", "租赁", "物业",
    "折旧", "摊销", "利息", "通讯", "网络",
)
# 峰值费用：年终奖/提成类，集中在春节月发放
PEAK_KEYWORDS = ("年终奖", "奖金", "提成")
# 投放费用：广宣营销类，按旺季节奏前置或集中
CAMPAIGN_KEYWORDS = ("广宣", "推广", "广告", "营销", "策划")

# 春节所在月（1 或 2 月）；年份超出表范围时取最近已知年份
_SPRING_MONTHS = {
    2024: 2, 2025: 1, 2026: 2, 2027: 2, 2028: 1, 2029: 2,
    2030: 2, 2031: 1, 2032: 2, 2033: 1, 2034: 2, 2035: 2,
}

VALID_SHAPES = ("uniform", "front_load", "back_load", "peak", "lump", "custom")


def spring_festival_month(year: int) -> int:
    """预算年度春节所在月（1 或 2）；未知年份取最近已知年份的映射。"""
    y = int(year or 0)
    if y in _SPRING_MONTHS:
        return _SPRING_MONTHS[y]
    if y <= 0:
        return 2
    nearest = min(_SPRING_MONTHS, key=lambda k: abs(k - y))
    return _SPRING_MONTHS[nearest]


@dataclass
class MonthlyRow:
    row: int
    subject: str
    expense_name: str
    annual: float = 0.0
    months: list = field(default_factory=list)  # 长度 12，元级
    shape: str = "uniform"
    shape_note: str = ""

    def to_dict(self) -> dict:
        return {
            "row": self.row,
            "subject": self.subject,
            "expense_name": self.expense_name,
            "annual": round(float(self.annual or 0), 2),
            "months": [float(m) for m in self.months],
            "shape": self.shape,
            "shape_note": self.shape_note,
        }


@dataclass
class SplitResult:
    rows: list = field(default_factory=list)
    month_totals: list = field(default_factory=lambda: [0.0] * 12)
    grand_total: float = 0.0
    mode: str = "rule"
    warnings: list = field(default_factory=list)
    checks: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        return {
            "engine": "monthly_split",
            "engine_version": ENGINE_VERSION,
            "matrix": [r.to_dict() for r in self.rows],
            "month_totals": [round(float(m), 2) for m in self.month_totals],
            "grand_total": round(float(self.grand_total), 2),
            "mode": self.mode,
            "warnings": list(self.warnings),
            "checks": dict(self.checks),
        }


# ── 权重与金额数学（恒等硬门槛） ────────────────────────────────────────

def normalize_weights(weights) -> list:
    """清洗并归一化 12 个权重：负值清零、NaN/Inf/非数值拒绝、Σ=1。

    全零权重回退均匀分布（1/12）。长度非 12 或含非法值时抛 ValueError。
    """
    if not isinstance(weights, (list, tuple)) or len(weights) != 12:
        raise ValueError("权重必须是 12 个数值")
    vals = []
    for w in weights:
        if isinstance(w, bool) or not isinstance(w, (int, float)):
            raise ValueError("权重含非数值")
        f = float(w)
        if math.isnan(f) or math.isinf(f):
            raise ValueError("权重含 NaN/Inf")
        vals.append(max(0.0, f))
    s = sum(vals)
    if s <= 0:
        return [1.0 / 12.0] * 12
    return [v / s for v in vals]


def ai_weights_valid(weights) -> bool:
    """AI 权重行级校验：12 个非负有限数且 Σ 偏离 1 不超过 0.05。"""
    if not isinstance(weights, (list, tuple)) or len(weights) != 12:
        return False
    total = 0.0
    for w in weights:
        if isinstance(w, bool) or not isinstance(w, (int, float)):
            return False
        f = float(w)
        if math.isnan(f) or math.isinf(f) or f < 0:
            return False
        total += f
    return total > 0 and abs(total - 1.0) <= 0.05


def distribute(annual: float, weights=None) -> list:
    """按权重把年度金额拆到 12 个月：round 到元 + 尾差归位到 argmax 月。

    annual=0 的行 12 个月全 0；保证 Σmonths == round(annual) 精确相等。
    """
    if weights is None:
        weights = [1.0] * 12
    w = normalize_weights(weights)
    target = int(round(float(annual or 0)))
    if target <= 0:
        return [0.0] * 12
    months = [float(int(round(target * wi))) for wi in w]
    diff = target - int(sum(months))
    if diff:
        idx = max(range(12), key=lambda i: w[i])
        months[idx] += float(diff)
    return months


def verify(rows) -> dict:
    """整表恒等校验：逐行 Σ月 == round(annual)，返回失败行数与总偏差。"""
    failures = 0
    gap = 0.0
    total_months = 0
    total_target = 0
    for r in rows:
        t = int(round(float(r.annual or 0)))
        s = int(round(sum(r.months)))
        total_months += s
        total_target += t
        if s != t:
            failures += 1
            gap += abs(s - t)
    gap += abs(total_months - total_target)
    return {"row_failures": failures, "total_gap": round(float(gap), 2)}


def _finalize(rows, mode: str, warnings: list) -> SplitResult:
    """汇总月合计/年度总计并强制校验；规则与 AI 路径共用。"""
    month_totals = [0.0] * 12
    grand = 0.0
    for r in rows:
        for i, m in enumerate(r.months):
            month_totals[i] += float(m or 0)
        grand += int(round(float(r.annual or 0)))
    checks = verify(rows)
    result = SplitResult(
        rows=rows,
        month_totals=month_totals,
        grand_total=float(grand),
        mode=mode,
        warnings=warnings,
        checks=checks,
    )
    if checks["row_failures"] or checks["total_gap"]:
        raise ArithmeticError(
            f"月度拆分恒等校验未通过：{checks['row_failures']} 行失败，总偏差 {checks['total_gap']} 元"
        )
    return result


def _answer(answers: dict, qid: str, default: str = "") -> str:
    v = (answers or {}).get(qid)
    return str(v).strip() if v is not None and str(v).strip() else default


def _parse_lump_text(text: str) -> list:
    """解析一次性支出文本：「3月 50万 装修费；11月 20万 展会」。

    每段提取 {month, amount(元), keyword}；无法解析月份的段落忽略。
    金额仅用于与年度预算比对提示，绝不改年度值。
    """
    out = []
    for seg in re.split(r"[;；\n]", text or ""):
        seg = seg.strip()
        if not seg:
            continue
        m = re.search(r"(\d{1,2})\s*月", seg)
        if not m:
            continue
        month = int(m.group(1))
        if not 1 <= month <= 12:
            continue
        amount = 0.0
        am = re.search(r"([\d,]+(?:\.\d+)?)\s*(万元|万|元)?", seg[m.end():])
        if am:
            amount = float(am.group(1).replace(",", ""))
            if am.group(2) and "万" in am.group(2):
                amount *= 10000.0
        kw = re.sub(r"\d{1,2}\s*月", "", seg)
        kw = re.sub(r"[\d,]+(?:\.\d+)?\s*(?:万元|万|元)?", "", kw)
        kw = re.sub(r"[，,。：:；;\s]+", "", kw)
        out.append({"month": month, "amount": amount, "keyword": kw})
    return out


def _bonus_months_from(text: str) -> int:
    """从薪酬节奏答案解析年终奖月数（0=无/提成按月）。"""
    if "2" in text and "3" in text:
        return 2
    if "3个月" in text:
        return 3
    if "1个月" in text or "一个月" in text:
        return 1
    return 0


def _peak_weights(bonus_months: int, spring: int) -> list:
    """年终奖峰值权重：春节月 ≈ N/(12+N)，其余 11 个月均摊。"""
    p = bonus_months / (12.0 + bonus_months)
    rest = (1.0 - p) / 11.0
    w = [rest] * 12
    w[spring - 1] = p
    return w


def _campaign_window(season: str, campaign: str) -> Optional[list]:
    """投放窗口（月份列表，1 基）：前置取旺季前 2 月；大促取 10/11 月。"""
    if "前置" in campaign:
        if "下半年" in season:
            return [7, 8]
        if "上半年" in season:
            return [1, 2]
        return None  # 收入均匀且未指明旺季 → 无法定位前置窗口
    if "集中" in campaign or "大促" in campaign:
        return [10, 11]
    return None


def build_rule_questions() -> list:
    """规则兜底题库（离线可用）：6 题全带默认推荐项，一路默认可走通。"""
    return [
        {
            "id": "q_season", "type": "single",
            "title": "贵司全年收入是否具有季节性？",
            "options": ["基本均匀", "上半年旺", "下半年旺"],
            "default": "基本均匀", "placeholder": "",
        },
        {
            "id": "q_bonus", "type": "single",
            "title": "人员薪酬与年终奖的节奏是？",
            "options": ["无年终奖", "年终奖约1个月工资", "年终奖2~3个月工资", "提成制（按月发放）"],
            "default": "无年终奖", "placeholder": "",
        },
        {
            "id": "q_fixed", "type": "single",
            "title": "房租物业等固定费用是否按月平摊？",
            "options": ["是，按月平摊", "否（不平摊）"],
            "default": "是，按月平摊", "placeholder": "",
        },
        {
            "id": "q_campaign", "type": "single",
            "title": "广宣/推广费用的投放节奏是？",
            "options": ["全年均匀投放", "旺季前1~2个月前置投放", "大促期间集中投放"],
            "default": "全年均匀投放", "placeholder": "",
        },
        {
            "id": "q_lump", "type": "text",
            "title": "有无已知的一次性大额支出？（格式：月份+金额+费用项目；多项用分号分隔，无则留空）",
            "options": [],
            "default": "",
            "placeholder": "例：3月 50万 装修费；11月 20万 展会",
        },
        {
            "id": "q_start", "type": "single",
            "title": "预算年度从几月开始？",
            "options": ["1月", "其他（暂按1月处理）"],
            "default": "1月", "placeholder": "",
        },
    ]


def rule_split(plan_rows: Sequence[dict], answers: Optional[dict] = None, budget_year: int = 0) -> SplitResult:
    """规则兜底拆分（离线可用，同时是 AI 失败的重试终态）。"""
    ans = answers if isinstance(answers, dict) else {}
    season = _answer(ans, "q_season", "基本均匀")
    bonus_txt = _answer(ans, "q_bonus", "")
    fixed_flat = _answer(ans, "q_fixed", "是")
    campaign = _answer(ans, "q_campaign", "")
    lump_txt = _answer(ans, "q_lump", "")
    spring = spring_festival_month(budget_year)
    warnings: list = []

    peak_w = None
    bonus_months = _bonus_months_from(bonus_txt)
    if bonus_months > 0:
        peak_w = _peak_weights(bonus_months, spring)

    camp_w = None
    window = _campaign_window(season, campaign)
    if window:
        camp_w = [1.0] * 12
        for m in window:
            camp_w[m - 1] = 1.8
    elif "前置" in campaign:
        warnings.append("收入节奏均匀且未指明旺季，广宣类按全年均摊")

    lumps = _parse_lump_text(lump_txt)
    matched_lumps = set()
    rows: list = []
    for pr in plan_rows or []:
        row = int(pr.get("row") or 0)
        subject = str(pr.get("subject") or "")
        name = str(pr.get("expense_name") or "")
        annual = round(float(pr.get("annual") or 0), 2)
        blob = f"{name}{subject}"

        lump_hit = None
        for idx, lp in enumerate(lumps):
            kw = lp["keyword"]
            if kw and (kw in blob or (len(name) >= 2 and name in kw)):
                lump_hit = (idx, lp)
                break
        if lump_hit is not None and annual > 0:
            idx, lp = lump_hit
            matched_lumps.add(idx)
            w = [0.0] * 12
            w[lp["month"] - 1] = 1.0
            note = f"一次性支出·{lp['month']}月全额"
            if lp["amount"] > annual + 0.005:
                warnings.append(
                    f"第{row}行「{name}」一次性支出声明 {lp['amount']:,.0f} 元"
                    f"超过年度预算 {annual:,.0f} 元（仅提示，不改数）"
                )
            rows.append(MonthlyRow(row, subject, name, annual, distribute(annual, w), "lump", note))
            continue

        if peak_w is not None and any(k in blob for k in PEAK_KEYWORDS):
            rows.append(MonthlyRow(
                row, subject, name, annual, distribute(annual, peak_w),
                "peak", f"年终奖·春节月（{spring}月）集中",
            ))
            continue

        if camp_w is not None and any(k in blob for k in CAMPAIGN_KEYWORDS):
            rows.append(MonthlyRow(
                row, subject, name, annual, distribute(annual, camp_w),
                "front_load", "广宣投放·旺季窗口抬升（×1.8）",
            ))
            continue

        if any(k in blob for k in RIGID_UNIFORM_KEYWORDS):
            note = "刚性费用·按月平摊"
            if "否" in fixed_flat:
                note = "刚性费用·答案声明不平摊，仍按月均摊"
                warnings.append(f"第{row}行「{name}」为刚性费用但答案选择不平摊，已按均摊处理")
        else:
            note = "常规费用·按月均摊"
        rows.append(MonthlyRow(row, subject, name, annual, distribute(annual), "uniform", note))

    for idx, lp in enumerate(lumps):
        if idx not in matched_lumps and lp["keyword"]:
            warnings.append(
                f"一次性支出「{lp['keyword']}」未匹配到预算行，已忽略（仅提示）"
            )

    return _finalize(rows, "rule", warnings)


def merge_ai_weights(plan_rows: Sequence[dict], ai_rows: Sequence[dict]) -> SplitResult:
    """把 AI 权重合并为拆分结果：行缺失/非法 → 该行回退 uniform 并记 warning。"""
    plan_by_row = {}
    for pr in plan_rows or []:
        plan_by_row[int(pr.get("row") or 0)] = pr
    given: dict = {}
    warnings: list = []
    for item in ai_rows or []:
        if not isinstance(item, dict):
            continue
        try:
            row = int(item.get("row"))
        except (TypeError, ValueError):
            continue
        if row not in plan_by_row:
            continue
        w = item.get("weights")
        if not ai_weights_valid(w):
            continue  # 无效行按缺失处理
        note = str(item.get("note") or "").strip()
        shape = str(item.get("shape") or "custom")
        if shape not in VALID_SHAPES:
            shape = "custom"
        given[row] = (w, shape, note)

    rows: list = []
    for row, pr in plan_by_row.items():
        subject = str(pr.get("subject") or "")
        name = str(pr.get("expense_name") or "")
        annual = round(float(pr.get("annual") or 0), 2)
        if row in given:
            w, shape, note = given[row]
            rows.append(MonthlyRow(row, subject, name, annual, distribute(annual, w), shape, note or "AI 分布"))
            if note and re.search(r"\d", note):
                warnings.append(f"第{row}行 AI 说明含数字（仅提示，不改数）：{note[:60]}")
        else:
            if annual > 0:
                warnings.append(f"第{row}行 AI 未给出有效权重，已回退按月均摊")
            rows.append(MonthlyRow(row, subject, name, annual, distribute(annual), "uniform", "AI 缺行·回退均摊"))

    return _finalize(rows, "ai", warnings)


def ai_weights_coverage(plan_rows: Sequence[dict], ai_rows: Sequence[dict]) -> tuple:
    """统计 AI 权重覆盖情况：返回 (非零行数, 其中权重有效的行数)。

    供路由判断「行数不全 → 失败重试」；全覆盖才接受 AI 结果。
    """
    needed = {int(p.get("row") or 0) for p in plan_rows or [] if float(p.get("annual") or 0) > 0}
    valid_rows = set()
    for item in ai_rows or []:
        if not isinstance(item, dict):
            continue
        try:
            row = int(item.get("row"))
        except (TypeError, ValueError):
            continue
        if row in needed and ai_weights_valid(item.get("weights")):
            valid_rows.add(row)
    return len(needed), len(valid_rows & needed)
