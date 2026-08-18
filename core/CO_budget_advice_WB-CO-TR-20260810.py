"""利润宝 · 费用预算编制建议算法（导出后问答 / 补零）。

规则：
1. 以费用预算上限 E7 为总盘子，已分配 G 合计之外的 residual 用于补零行。
2. 若「上年同期实际 D=0」：不虚构 D；只建议 参考费用金额 F、预算金额 G、预算占比 H。
3. 若 D>0 且 G=0：F=D×(1+增长率)，G 默认跟 F（可微调）。
4. 科目结构按行业默认占比分配 residual；科目内按「老板该花什么钱」优先级加权。
5. DeepSeek 可对规则草案做加减项与话术增强（由 web 层调用后 merge）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

from . import budget as budget_mod
from .budget_categories import (
    SUBJECT_ADMIN,
    SUBJECT_FINANCE,
    SUBJECT_NON_OPERATING,
    SUBJECT_SALES,
)

# 行业 → 四大科目占 residual 的默认结构（合计 1.0）
_INDUSTRY_SUBJECT_MIX: dict[str, dict[str, float]] = {
    "建筑业": {
        SUBJECT_SALES: 0.12,
        SUBJECT_ADMIN: 0.62,
        SUBJECT_FINANCE: 0.18,
        SUBJECT_NON_OPERATING: 0.08,
    },
    "制造业": {
        SUBJECT_SALES: 0.20,
        SUBJECT_ADMIN: 0.52,
        SUBJECT_FINANCE: 0.18,
        SUBJECT_NON_OPERATING: 0.10,
    },
    "批发和零售业": {
        SUBJECT_SALES: 0.35,
        SUBJECT_ADMIN: 0.45,
        SUBJECT_FINANCE: 0.12,
        SUBJECT_NON_OPERATING: 0.08,
    },
    "信息传输、软件和信息技术服务业": {
        SUBJECT_SALES: 0.25,
        SUBJECT_ADMIN: 0.55,
        SUBJECT_FINANCE: 0.10,
        SUBJECT_NON_OPERATING: 0.10,
    },
}
_DEFAULT_MIX = {
    SUBJECT_SALES: 0.22,
    SUBJECT_ADMIN: 0.55,
    SUBJECT_FINANCE: 0.15,
    SUBJECT_NON_OPERATING: 0.08,
}

# 科目内优先级关键词权重（越高越应给老板写预算）
_PRIORITY_RULES: list[tuple[list[str], int, str]] = [
    (["工资", "薪酬", "奖金", "提成", "劳务"], 14, "刚性人力成本，必须先留足"),
    (["社保", "公积金", "五险"], 12, "法定社保公积金，合规刚性支出"),
    (["房租", "租赁", "物业"], 11, "经营场所固定成本"),
    (["广宣", "推广", "广告", "营销", "策划"], 10, "获客与品牌：老板应规划的市场投入"),
    (["业务招待", "招待"], 8, "客户关系维护费用（需合规票据）"),
    (["差旅", "交通", "油费", "过路", "停车", "ETC"], 7, "业务跑动与差旅交通"),
    (["办公", "通讯", "水电", "网络"], 7, "日常运营开销"),
    (["咨询", "审计", "律师", "服务费"], 6, "专业服务与合规支持"),
    (["折旧", "摊销", "无形资产"], 6, "资产摊销类费用"),
    (["培训", "教育", "福利"], 5, "人才与福利投入"),
    (["研发", "技术"], 8, "技术/研发投入（若适用）"),
    (["利息", "手续费", "金融", "汇兑"], 6, "融资与结算成本"),
    (["保险"], 4, "经营风险对冲"),
    (["维修", "保养"], 4, "资产维护"),
    (["捐赠", "赞助"], 2, "营业外/公益类（可酌情）"),
]


@dataclass
class BudgetAdviceItem:
    row: int
    subject: str
    expense_name: str
    invoice_name: str
    has_last_year: bool
    last_year_actual: float
    reference_amount: float  # F 参考费用金额
    budget_amount: float  # G 预算费用金额
    budget_ratio: float  # H 小数
    budget_ratio_pct: float  # H*100 展示
    priority: str  # high | mid | low
    reason: str
    source: str = "rule"  # rule | ai | merge
    selected: bool = True
    write_last_year: bool = False  # 永远默认 False：无 D 不写假上年

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BudgetAdviceResult:
    company_name: str
    industry: str
    year: int
    budget_revenue: float
    expense_budget_cap: float
    allocated_before: float
    residual: float
    zero_lines: int
    suggestions: list[BudgetAdviceItem] = field(default_factory=list)
    algorithm_notes: list[str] = field(default_factory=list)
    ai_used: bool = False
    ai_summary: str = ""
    subject_mix: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "company_name": self.company_name,
            "industry": self.industry,
            "year": self.year,
            "budget_revenue": self.budget_revenue,
            "expense_budget_cap": self.expense_budget_cap,
            "allocated_before": self.allocated_before,
            "residual": self.residual,
            "zero_lines": self.zero_lines,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "algorithm_notes": self.algorithm_notes,
            "ai_used": self.ai_used,
            "ai_summary": self.ai_summary,
            "subject_mix": self.subject_mix,
            "suggestion_count": len(self.suggestions),
            "selected_budget_total": round(
                sum(s.budget_amount for s in self.suggestions if s.selected), 2
            ),
        }


def _subject_mix(industry: str) -> dict[str, float]:
    ind = (industry or "").strip()
    for key, mix in _INDUSTRY_SUBJECT_MIX.items():
        if key in ind:
            return dict(mix)
    return dict(_DEFAULT_MIX)


def _line_weight(line: budget_mod.ExpenseLine) -> tuple[int, str]:
    blob = f"{line.expense_name}{line.invoice_name}"
    best_w, best_why = 1, "行业常规费用项，可按经营需要酌情编制"
    for kws, w, why in _PRIORITY_RULES:
        if any(k in blob for k in kws):
            if w > best_w:
                best_w, best_why = w, why
    return best_w, best_why


def _priority_label(weight: int) -> str:
    if weight >= 10:
        return "high"
    if weight >= 5:
        return "mid"
    return "low"


def _round_yuan(v: float) -> float:
    return round(float(v or 0.0), 2)


def build_rule_advice(plan: budget_mod.BudgetPlan) -> BudgetAdviceResult:
    """纯规则编制建议（不依赖 DeepSeek）。"""
    budget_mod.compute_all(plan)
    ti = plan.top_inputs
    tc = plan.top_computed
    c2 = float(ti.budget_revenue or 0.0)
    cap = float(tc.expense_budget_cap or 0.0)
    allocated = float(plan.allocated_total or 0.0)
    residual = max(0.0, cap - allocated)
    growth = float(tc.revenue_growth_rate or 0.0)
    mix = _subject_mix(plan.industry)

    notes = [
        f"费用预算上限 E7 = {cap:,.2f} 元",
        f"已分配预算 G 合计 = {allocated:,.2f} 元",
        f"可补零 residual = {residual:,.2f} 元",
        f"收入增长率 C7 = {growth:.2%}",
        "算法：无上年实际(D=0) 不写假 D，只写参考金额 F + 预算 G + 占比 H",
        "算法：有上年实际且预算为 0 时，参考 F=D×(1+增长率)，预算默认跟 F",
    ]

    # 需要建议的行：G=0 且 (D=0 或 需补预算)
    candidates: list[tuple[budget_mod.ExpenseLine, int, str]] = []
    zero_lines = 0
    for line in plan.lines:
        d = float(line.last_year_actual or 0)
        g = float(line.budget_amount or 0)
        if g <= 0 and d <= 0:
            zero_lines += 1
            w, why = _line_weight(line)
            candidates.append((line, w, why))
        elif g <= 0 and d > 0:
            w, why = _line_weight(line)
            candidates.append((line, w, why + "；沿用上年实际按增长推算预算"))

    suggestions: list[BudgetAdviceItem] = []

    # 1) 有上年、无预算：直接跟 F
    for line, w, why in list(candidates):
        d = float(line.last_year_actual or 0)
        g = float(line.budget_amount or 0)
        if d > 0 and g <= 0:
            ref = _round_yuan(d * (1 + growth))
            bud = ref
            ratio = (bud / c2) if c2 > 0 else 0.0
            suggestions.append(
                BudgetAdviceItem(
                    row=line.row,
                    subject=line.subject,
                    expense_name=line.expense_name,
                    invoice_name=line.invoice_name,
                    has_last_year=True,
                    last_year_actual=_round_yuan(d),
                    reference_amount=ref,
                    budget_amount=bud,
                    budget_ratio=round(ratio, 8),
                    budget_ratio_pct=round(ratio * 100, 6),
                    priority=_priority_label(w),
                    reason=why,
                    source="rule",
                    selected=True,
                    write_last_year=False,
                )
            )

    spent_from_history = sum(s.budget_amount for s in suggestions)
    residual2 = max(0.0, residual - spent_from_history)

    # 若 residual 仍很小但大量为 0：用 cap 的一部分做「老板参考编制」
    # （至少给到 cap 的 35% 或 residual 较大者，避免全表空白无参考）
    fill_pool = residual2
    if zero_lines >= 20 and fill_pool < max(cap * 0.15, 1.0):
        fill_pool = max(fill_pool, cap * 0.35)
        notes.append(
            f"空白行过多：按费用上限的 35%（{fill_pool:,.2f} 元）生成老板参考编制（非强制执行）"
        )
    elif fill_pool <= 0 and zero_lines > 0 and cap > 0:
        fill_pool = cap * 0.30
        notes.append(f"已分配用尽或上限紧张：仍按上限 30%（{fill_pool:,.2f}）给出参考结构")

    # 2) 无上年、无预算：按科目 mix × 优先级权重分配 fill_pool
    empty_by_subject: dict[str, list[tuple[budget_mod.ExpenseLine, int, str]]] = {
        SUBJECT_SALES: [],
        SUBJECT_ADMIN: [],
        SUBJECT_FINANCE: [],
        SUBJECT_NON_OPERATING: [],
    }
    for line, w, why in candidates:
        d = float(line.last_year_actual or 0)
        g = float(line.budget_amount or 0)
        if d <= 0 and g <= 0:
            empty_by_subject.setdefault(line.subject, []).append((line, w, why))

    for subject, share in mix.items():
        bucket = empty_by_subject.get(subject) or []
        if not bucket or fill_pool <= 0:
            continue
        # 只取权重较高的前 N 行，避免 84 行全部撒胡椒面
        bucket_sorted = sorted(bucket, key=lambda x: -x[1])
        top_n = bucket_sorted[: min(8, len(bucket_sorted))]
        # 过滤极低权重除非不足 3 行
        top_n = [x for x in top_n if x[1] >= 4] or top_n[:3]
        total_w = sum(x[1] for x in top_n) or 1
        subject_pool = fill_pool * float(share)
        for line, w, why in top_n:
            amount = _round_yuan(subject_pool * (w / total_w))
            if amount < 100 and subject_pool >= 500:
                amount = 0.0  # 过小可忽略
            if amount <= 0:
                continue
            # 无上年：F 参考 = G 预算（老板参考「该花多少」）
            ref = amount
            bud = amount
            ratio = (bud / c2) if c2 > 0 else 0.0
            suggestions.append(
                BudgetAdviceItem(
                    row=line.row,
                    subject=line.subject,
                    expense_name=line.expense_name,
                    invoice_name=line.invoice_name,
                    has_last_year=False,
                    last_year_actual=0.0,
                    reference_amount=ref,
                    budget_amount=bud,
                    budget_ratio=round(ratio, 8),
                    budget_ratio_pct=round(ratio * 100, 6),
                    priority=_priority_label(w),
                    reason=f"{why}（无上年实际：只写参考金额与预算/占比，不虚构上年）",
                    source="rule",
                    selected=True,
                    write_last_year=False,
                )
            )

    # 按优先级+金额排序
    prio_rank = {"high": 0, "mid": 1, "low": 2}
    suggestions.sort(key=lambda s: (prio_rank.get(s.priority, 9), -s.budget_amount, s.row))

    notes.append(f"生成建议 {len(suggestions)} 条；其中无上年仅写 F/G/H 的 "
                 f"{sum(1 for s in suggestions if not s.has_last_year)} 条")
    notes.append(
        "科目结构参考："
        + "、".join(f"{k}{v:.0%}" for k, v in mix.items())
    )

    return BudgetAdviceResult(
        company_name=plan.company_name,
        industry=plan.industry,
        year=plan.year,
        budget_revenue=_round_yuan(c2),
        expense_budget_cap=_round_yuan(cap),
        allocated_before=_round_yuan(allocated),
        residual=_round_yuan(residual),
        zero_lines=zero_lines,
        suggestions=suggestions,
        algorithm_notes=notes,
        ai_used=False,
        subject_mix=mix,
    )


def merge_ai_refinements(
    result: BudgetAdviceResult,
    ai_items: Sequence[dict],
    *,
    ai_summary: str = "",
    revenue: float = 0.0,
) -> BudgetAdviceResult:
    """把 DeepSeek 返回的行级调整合并进规则建议。"""
    if not ai_items:
        if ai_summary:
            result.ai_summary = ai_summary
            result.ai_used = True
        return result

    by_row = {s.row: s for s in result.suggestions}
    c2 = revenue or result.budget_revenue or 0.0

    for raw in ai_items:
        if not isinstance(raw, dict):
            continue
        try:
            row = int(raw.get("row"))
        except (TypeError, ValueError):
            continue
        try:
            bud = float(raw.get("budget_amount") or 0)
        except (TypeError, ValueError):
            bud = 0.0
        try:
            ref = float(raw.get("reference_amount") or 0)
        except (TypeError, ValueError):
            ref = 0.0
        reason = str(raw.get("reason") or "").strip()
        if bud <= 0 and ref <= 0:
            # AI 建议删除/忽略：取消选中
            if row in by_row and raw.get("drop"):
                by_row[row].selected = False
                by_row[row].source = "ai"
                if reason:
                    by_row[row].reason = reason
            continue

        if row in by_row:
            item = by_row[row]
            if not item.has_last_year:
                # 无上年：只允许改 F/G，绝不写 D
                item.budget_amount = _round_yuan(bud or ref or item.budget_amount)
                item.reference_amount = _round_yuan(ref or item.budget_amount)
                item.last_year_actual = 0.0
                item.write_last_year = False
            else:
                item.budget_amount = _round_yuan(bud or item.budget_amount)
                if ref > 0:
                    item.reference_amount = _round_yuan(ref)
            ratio = (item.budget_amount / c2) if c2 > 0 else 0.0
            item.budget_ratio = round(ratio, 8)
            item.budget_ratio_pct = round(ratio * 100, 6)
            if reason:
                item.reason = reason
            item.source = "merge"
            item.selected = True if raw.get("selected", True) else False
        else:
            # AI 新增行（必须在模板内）
            subject = str(raw.get("subject") or "")
            exp = str(raw.get("expense_name") or "")
            inv = str(raw.get("invoice_name") or "")
            has_ly = bool(raw.get("has_last_year"))
            amount = _round_yuan(bud or ref)
            if amount <= 0:
                continue
            ratio = (amount / c2) if c2 > 0 else 0.0
            by_row[row] = BudgetAdviceItem(
                row=row,
                subject=subject,
                expense_name=exp,
                invoice_name=inv,
                has_last_year=has_ly,
                last_year_actual=_round_yuan(float(raw.get("last_year_actual") or 0)) if has_ly else 0.0,
                reference_amount=_round_yuan(ref or amount),
                budget_amount=amount,
                budget_ratio=round(ratio, 8),
                budget_ratio_pct=round(ratio * 100, 6),
                priority=str(raw.get("priority") or "mid"),
                reason=reason or "DeepSeek 建议补充",
                source="ai",
                selected=True,
                write_last_year=False,
            )

    result.suggestions = sorted(
        by_row.values(),
        key=lambda s: ({"high": 0, "mid": 1, "low": 2}.get(s.priority, 9), -s.budget_amount, s.row),
    )
    result.ai_used = True
    result.ai_summary = ai_summary or result.ai_summary
    result.algorithm_notes.append(f"DeepSeek 已介入调整，当前建议 {len(result.suggestions)} 条")
    return result


# 单行占营收上限（刚性人力可放宽）
_MAX_SINGLE_RATIO = 0.04
_RIGID_KEYWORDS = ("工资", "薪酬", "社保", "公积金", "奖金", "房租", "租赁")


def _max_fee_rate(industry: str) -> float:
    """总费用率上限（小数）：WB §七 费用率带上沿 = 毛利率−净利率 + 叙述区间。"""
    from . import industry as ind_mod

    band = ind_mod.get_period_expense_ratio_band(industry)
    return float(band.get("max") or band.get("median") or 0.15)


def _min_fee_rate(industry: str) -> float:
    """总费用率下限（小数）：WB 费用率带下沿（稳健预算）。"""
    from . import industry as ind_mod

    band = ind_mod.get_period_expense_ratio_band(industry)
    return float(band.get("min") or band.get("median") or 0.08)


def _target_fee_rate(industry: str) -> float:
    """总费用率目标中枢（小数）。"""
    from . import industry as ind_mod

    band = ind_mod.get_period_expense_ratio_band(industry)
    return float(band.get("median") or 0.12)


def _is_rigid_line(expense_name: str, invoice_name: str) -> bool:
    blob = f"{expense_name}{invoice_name}"
    return any(k in blob for k in _RIGID_KEYWORDS)


# 必须尽量填满的经营必要行（权重；用于拆大类整笔 + 补 residual）
_MUST_FILL_WEIGHTS: list[tuple[list[str], int]] = [
    (["工资", "薪酬", "提成"], 14),
    (["奖金"], 10),
    (["社保"], 12),
    (["公积金"], 10),
    (["房租", "租赁", "物业", "水电"], 11),
    (["广宣", "推广", "广告", "宣传"], 9),
    (["业务招待", "招待"], 7),
    (["差旅", "高铁", "机票", "住宿"], 7),
    (["办公", "宽带", "快递", "耗材"], 6),
    (["汽车", "油费", "过路", "停车", "ETC"], 5),
    (["折旧", "摊销"], 6),
    (["研发"], 8),
    (["利息", "手续费", "金融"], 6),
    (["审计", "顾问", "咨询", "中介", "法律"], 5),
    (["培训", "教育", "福利"], 4),
    (["工会"], 3),
]


def _line_fill_weight(line: budget_mod.ExpenseLine) -> int:
    blob = f"{line.expense_name}{line.invoice_name}"
    best = 0
    for kws, w in _MUST_FILL_WEIGHTS:
        if any(k in blob for k in kws):
            best = max(best, w)
    return best


def ensure_all_required_fields_filled(plan: budget_mod.BudgetPlan) -> list[str]:
    """保证该填的列/行都填上（导出前最后一道补全）。

    - 有 D（上年）→ 必有 F、G
    - 有 I（本年实际）→ 必有 G
    - 有 G → 必有 F（无 D 时 F=G）与可算 H
    - 大类只落在 1 行整笔时 → 按必要项权重拆到多行
    - 费用合计 < 目标额度时 → 把 residual 补到仍为空的必要经营行
    """
    from .budget_categories import ALL_SUBJECTS

    notes: list[str] = []
    budget_mod.compute_all(plan)
    ti = plan.top_inputs
    tc = plan.top_computed
    c2 = float(ti.budget_revenue or 0.0)
    c7 = float(tc.revenue_growth_rate or 0.0)
    filled_ops = 0

    # ── 1) 列联动：该有就有 ──
    for l in plan.lines:
        d = float(l.last_year_actual or 0)
        g = float(l.budget_amount or 0)
        i = float(l.actual_amount or 0)
        f = float(l.reference_amount or 0)
        changed = False
        if d > 0:
            expect_f = _round_yuan(d * (1 + c7))
            if f <= 0:
                l.reference_amount = expect_f
                f = expect_f
                changed = True
            if g <= 0:
                l.budget_amount = f if f > 0 else d
                g = float(l.budget_amount)
                changed = True
        if i > 0 and g <= 0:
            l.budget_amount = i
            g = i
            changed = True
        if g > 0 and f <= 0 and d <= 0:
            l.reference_amount = g
            changed = True
        if f > 0 and g <= 0:
            l.budget_amount = f
            changed = True
        if d <= 0 and g > 0 and float(l.reference_amount or 0) <= 0:
            l.reference_amount = g
            changed = True
        if changed:
            filled_ops += 1
    if filled_ops:
        notes.append(f"列联动补全 {filled_ops} 行（D/F/G/I 互推）")

    # ── 2) 大类整笔拆分：一科只有 1～2 行有数、同科大量为空 → 按权重拆 ──
    split_n = 0
    for subject in ALL_SUBJECTS:
        lines = [l for l in plan.lines if l.subject == subject]
        if not lines:
            continue
        with_g = [l for l in lines if float(l.budget_amount or 0) > 0]
        empty = [l for l in lines if float(l.budget_amount or 0) <= 0 and _line_fill_weight(l) > 0]
        if len(with_g) > 3 or not empty:
            continue
        total_g = sum(float(l.budget_amount or 0) for l in with_g)
        total_d = sum(float(l.last_year_actual or 0) for l in with_g)
        total_i = sum(float(l.actual_amount or 0) for l in with_g)
        if total_g < 1000:
            continue
        # 目标：至少填满 min(8, 有权重空行+已有行) 行
        pool = with_g + sorted(empty, key=lambda x: -_line_fill_weight(x))
        pool = pool[: max(4, min(10, len(with_g) + len(empty)))]
        weights = [_line_fill_weight(l) or 1 for l in pool]
        wsum = sum(weights) or 1
        # 清空原集中行再按权重写
        for l in with_g:
            l.budget_amount = 0.0
            if float(l.last_year_actual or 0) > 0 and len(with_g) <= 2:
                pass  # 保留 D 在原行，金额拆 G
        for l, w in zip(pool, weights):
            share = total_g * (w / wsum)
            l.budget_amount = _round_yuan(float(l.budget_amount or 0) + share)
            if float(l.last_year_actual or 0) <= 0:
                l.reference_amount = float(l.budget_amount)
            split_n += 1
        # 上年/本年合计若只在代表行，也拆一点到明细（有 D/I 的保留，无则按权分摊 D）
        if total_d > 0 and sum(1 for l in lines if float(l.last_year_actual or 0) > 0) <= 2:
            holders = [l for l in lines if float(l.last_year_actual or 0) > 0]
            for l in holders:
                l.last_year_actual = 0.0
            d_pool = sorted(pool, key=lambda x: -_line_fill_weight(x))[: len(pool)]
            dw = [_line_fill_weight(l) or 1 for l in d_pool]
            dws = sum(dw) or 1
            for l, w in zip(d_pool, dw):
                l.last_year_actual = _round_yuan(total_d * (w / dws))
                if float(l.budget_amount or 0) <= 0:
                    l.budget_amount = _round_yuan(l.last_year_actual * (1 + c7))
                if float(l.reference_amount or 0) <= 0:
                    l.reference_amount = _round_yuan(float(l.last_year_actual) * (1 + c7))
        if total_i > 0 and sum(1 for l in lines if float(l.actual_amount or 0) > 0) <= 2:
            for l in lines:
                if float(l.actual_amount or 0) > 0 and l not in pool[:3]:
                    continue
            holders = [l for l in lines if float(l.actual_amount or 0) > 0]
            for l in holders:
                l.actual_amount = 0.0
            i_pool = pool[: min(6, len(pool))]
            iw = [_line_fill_weight(l) or 1 for l in i_pool]
            iws = sum(iw) or 1
            for l, w in zip(i_pool, iw):
                l.actual_amount = _round_yuan(total_i * (w / iws))
        notes.append(f"{subject}整笔拆分→{len(pool)}行（合计G≈{total_g:,.0f}）")
    if split_n:
        notes.append(f"大类整笔拆分写入 {split_n} 行次")

    # ── 3) residual 补必要空行：该有的经营费用尽量有数 ──
    budget_mod.compute_all(plan)
    tc = plan.top_computed
    e7 = float(tc.expense_budget_cap or 0.0)
    fee_rate = _max_fee_rate(plan.industry)
    target = min(x for x in (e7, c2 * fee_rate) if x > 0) if c2 > 0 else e7
    g_now = float(plan.allocated_total or 0)
    residual = max(0.0, target - g_now) if target > 0 else 0.0
    if residual > 500 and c2 > 0:
        candidates = [
            l
            for l in plan.lines
            if float(l.budget_amount or 0) <= 0 and _line_fill_weight(l) >= 4
        ]
        candidates.sort(key=lambda x: -_line_fill_weight(x))
        # 每个科目至少留几条
        picked: list[budget_mod.ExpenseLine] = []
        per_subject: dict[str, int] = {}
        for l in candidates:
            cnt = per_subject.get(l.subject, 0)
            if cnt >= 6:
                continue
            picked.append(l)
            per_subject[l.subject] = cnt + 1
            if len(picked) >= 24:
                break
        if picked:
            weights = [_line_fill_weight(l) for l in picked]
            wsum = sum(weights) or 1
            for l, w in zip(picked, weights):
                amt = _round_yuan(residual * (w / wsum))
                if amt < 50:
                    continue
                l.budget_amount = amt
                l.reference_amount = amt
            notes.append(
                f"额度补全：residual {residual:,.0f} 元 → {len(picked)} 条必要费用行"
            )

    # ── 4) 最终列扫尾 ──
    budget_mod.compute_all(plan)
    for l in plan.lines:
        g = float(l.budget_amount or 0)
        d = float(l.last_year_actual or 0)
        if g > 0 and float(l.reference_amount or 0) <= 0:
            l.reference_amount = (
                _round_yuan(d * (1 + c7)) if d > 0 else g
            )
    budget_mod.compute_all(plan)
    g_final = float(plan.allocated_total or 0)
    n_g = sum(1 for l in plan.lines if float(l.budget_amount or 0) > 0)
    n_f = sum(1 for l in plan.lines if float(l.reference_amount or 0) > 0)
    n_d = sum(1 for l in plan.lines if float(l.last_year_actual or 0) > 0)
    notes.append(
        f"补全后：有G {n_g}/84 · 有F {n_f}/84 · 有D {n_d}/84 · ΣG={g_final:,.0f}"
    )
    return notes


def _period_expense_anchors(period_expenses: dict | None) -> dict[str, float]:
    pe = period_expenses if isinstance(period_expenses, dict) else {}
    return {
        "销售费用": float(pe.get("selling_latest") or pe.get("selling_expense") or 0),
        "管理费用": float(pe.get("admin_latest") or pe.get("admin_expense") or 0)
        + float(pe.get("rd_latest") or pe.get("rd_expense") or 0),
        "财务费用": float(pe.get("finance_latest") or pe.get("finance_expense") or 0),
    }


def align_budget_to_period_level(
    plan: budget_mod.BudgetPlan,
    *,
    period_expenses: dict | None = None,
) -> list[str]:
    """把预算规模对齐到利润表期间费用水平（解决「费用有数但占比只有 0.0x%」）。

    根因：DeepSeek 常把总费用率压到营收的 5%～7%，而审计报告期间费用常为 12%～15%。
    本函数**保持行间结构**，只做：
    1) 分科目：预算合计远低于期间费用锚时，按比例上调该科各行
    2) 总体：ΣG 仍低于 max(期间合计, 上年D合计, 本年I合计)×增长因子 时整体上调
    3) 不超过 E7 费用上限；超上限再等比压回
    H 始终 = G/C2，金额上去后占比自然回到合理量级。
    """
    notes: list[str] = []
    budget_mod.compute_all(plan)
    c2 = float(plan.top_inputs.budget_revenue or 0.0)
    e7 = float(plan.top_computed.expense_budget_cap or 0.0)
    c7 = float(plan.top_computed.revenue_growth_rate or 0.0)
    if c2 <= 0:
        return ["期间对齐跳过：C2=0"]

    anchors = _period_expense_anchors(period_expenses)
    # 增长因子：收入下滑时预算仍至少贴近最近一期实际（不主动砍半）
    growth_factor = 1.0 + max(c7, -0.05)
    growth_factor = max(0.85, min(growth_factor, 1.25))

    d_total = sum(float(l.last_year_actual or 0) for l in plan.lines)
    i_total = sum(float(l.actual_amount or 0) for l in plan.lines)
    pe_total = sum(anchors.values())

    # ── 1) 分科目对齐（结构在科目内保持）──
    for subj, anchor in anchors.items():
        if anchor <= 0:
            continue
        target_subj = anchor * growth_factor
        lines = [l for l in plan.lines if l.subject == subj]
        got = sum(float(l.budget_amount or 0) for l in lines)
        if got <= 0:
            # 整科为空：把目标拆到该科有权重/有 D 或 I 的行
            pool = [
                l
                for l in lines
                if float(l.last_year_actual or 0) > 0
                or float(l.actual_amount or 0) > 0
                or _line_fill_weight(l) >= 4
            ] or lines[:8]
            if not pool:
                continue
            w = [_line_fill_weight(l) or 1 for l in pool]
            ws = sum(w) or 1
            for l, wi in zip(pool, w):
                amt = _round_yuan(target_subj * (wi / ws))
                l.budget_amount = amt
                if float(l.last_year_actual or 0) <= 0:
                    l.reference_amount = amt
            notes.append(f"科目补齐 {subj}→{target_subj:,.0f} 元（原为空）")
            continue
        # 偏低 >20% 上调；偏高 >40% 下调。单科倍率封顶，避免少数行暴涨
        if got < target_subj * 0.80:
            scale = min(target_subj / got, 3.0)
            new_total = 0.0
            for l in lines:
                g = float(l.budget_amount or 0)
                if g <= 0:
                    continue
                l.budget_amount = _round_yuan(g * scale)
                if float(l.last_year_actual or 0) <= 0:
                    l.reference_amount = float(l.budget_amount)
                new_total += float(l.budget_amount)
            # 封顶后仍不足：把差额按权重摊到本科空行/高权行，避免全堆在原行
            gap = target_subj - new_total
            if gap > 1000:
                pool = [
                    l
                    for l in lines
                    if _line_fill_weight(l) >= 3
                ]
                pool = sorted(pool, key=lambda x: -_line_fill_weight(x))[:12]
                if pool:
                    w = [_line_fill_weight(l) or 1 for l in pool]
                    ws = sum(w) or 1
                    for l, wi in zip(pool, w):
                        add = _round_yuan(gap * (wi / ws))
                        l.budget_amount = _round_yuan(float(l.budget_amount or 0) + add)
                        if float(l.last_year_actual or 0) <= 0:
                            l.reference_amount = float(l.budget_amount)
            notes.append(
                f"科目上调 {subj}：{got:,.0f}→约{target_subj:,.0f}（×{scale:.3f}，对齐期间费用）"
            )
        elif got > target_subj * 1.40 and target_subj > 0:
            scale = target_subj / got
            for l in lines:
                g = float(l.budget_amount or 0)
                if g <= 0:
                    continue
                l.budget_amount = _round_yuan(g * scale)
                if float(l.last_year_actual or 0) <= 0:
                    l.reference_amount = float(l.budget_amount)
            notes.append(
                f"科目下调 {subj}：{got:,.0f}→{target_subj:,.0f}（×{scale:.3f}）"
            )

    budget_mod.compute_all(plan)
    g_sum = float(plan.allocated_total or 0)

    # ── 2) 总体地板：不得明显低于历史/期间费用规模 ──
    floor_candidates = [x for x in (pe_total, d_total, i_total) if x > 0]
    if floor_candidates:
        floor = max(floor_candidates) * growth_factor
    else:
        floor = 0.0
    # WB §七 费用率带 + 历史期间费用：历史上沿优先（审计真实费用率 > 软上限时跟历史）
    min_rate = _min_fee_rate(plan.industry)
    med_rate = _target_fee_rate(plan.industry)
    max_rate = _max_fee_rate(plan.industry)
    hist_rate = 0.0
    if c2 > 0 and i_total > 0:
        hist_rate = max(hist_rate, i_total / c2)
    c6 = float(plan.top_inputs.last_year_revenue or 0)
    if c6 > 0 and d_total > 0:
        hist_rate = max(hist_rate, d_total / c6)
    # 软顶：WB max 与历史费用率取较大（真实审计优先），再与 E7 比较
    soft_max_rate = max(max_rate, hist_rate) if hist_rate > 0 else max_rate
    wb_floor = c2 * min_rate
    wb_target = c2 * med_rate
    floor = max(floor, wb_floor) if floor > 0 else wb_floor
    if floor > wb_target:
        target_amt = min(floor, c2 * soft_max_rate)
    else:
        target_amt = min(max(wb_target, floor), c2 * soft_max_rate)
    ceiling = c2 * soft_max_rate
    if e7 > 0:
        ceiling = min(ceiling, e7) if ceiling > 0 else e7
        floor = min(floor, e7)
        target_amt = min(target_amt, e7)
    target = min(max(target_amt, floor), ceiling) if ceiling > 0 else max(target_amt, floor)
    notes.append(
        f"WB费用率带：{min_rate:.1%}–{max_rate:.1%}（中枢{med_rate:.1%}）"
        + (f"·历史{hist_rate:.1%}" if hist_rate else "")
        + f" · 目标ΣG≈{target:,.0f} · E7={e7:,.0f}"
    )
    # 若当前 ΣG 明显高于天花板，等比压回（科目结构保持）
    g_now = float(plan.allocated_total or 0)
    if ceiling > 0 and g_now > ceiling * 1.02:
        scale = ceiling / g_now
        for l in plan.lines:
            g = float(l.budget_amount or 0)
            if g <= 0:
                continue
            l.budget_amount = _round_yuan(g * scale)
            if float(l.last_year_actual or 0) <= 0:
                l.reference_amount = float(l.budget_amount)
        budget_mod.compute_all(plan)
        notes.append(f"总费用压回天花板 {ceiling:,.0f}（×{scale:.3f}）")

    g_sum = float(plan.allocated_total or 0)
    if target > 0 and g_sum > 0 and g_sum < target * 0.90:
        scale = target / g_sum
        for l in plan.lines:
            g = float(l.budget_amount or 0)
            if g <= 0:
                continue
            l.budget_amount = _round_yuan(g * scale)
            if float(l.last_year_actual or 0) <= 0:
                l.reference_amount = float(l.budget_amount)
        budget_mod.compute_all(plan)
        notes.append(
            f"总费用率上调：ΣG {g_sum:,.0f}→{float(plan.allocated_total):,.0f} "
            f"（×{scale:.3f}，对齐期间/历史费用，目标费用率约 {target/c2:.2%}）"
        )
    elif target > 0 and g_sum <= 0:
        notes.append("总费用对齐跳过：ΣG=0")

    # ── 3) 超 E7 压回 ──
    budget_mod.compute_all(plan)
    g_sum = float(plan.allocated_total or 0)
    e7 = float(plan.top_computed.expense_budget_cap or 0.0)
    if e7 > 0 and g_sum > e7 * 1.02:
        scale = e7 / g_sum
        for l in plan.lines:
            g = float(l.budget_amount or 0)
            if g <= 0:
                continue
            l.budget_amount = _round_yuan(g * scale)
            if float(l.last_year_actual or 0) <= 0:
                l.reference_amount = float(l.budget_amount)
        budget_mod.compute_all(plan)
        notes.append(f"总费用压回上限 E7={e7:,.0f}（×{scale:.3f}）")

    budget_mod.compute_all(plan)
    g_final = float(plan.allocated_total or 0)
    notes.append(
        f"期间对齐后：ΣG={g_final:,.0f} · 总费用率 {g_final/c2:.2%} · "
        f"期间锚 {pe_total:,.0f} · D合计 {d_total:,.0f} · I合计 {i_total:,.0f}"
    )
    return notes


def finalize_accuracy_qa(
    plan: budget_mod.BudgetPlan,
    *,
    period_expenses: dict | None = None,
) -> dict:
    """导出前最终准确性核验（确定性，不改 DeepSeek 结构权重）。

    - 强制 H = G / C2（每行 budget_expense_ratio）
    - 有 G 必有 F（无 D 时 F=G）
    - 科目合计 vs 利润表期间费用偏差报告
    - 总费用率、C2 一致性
    返回 qa dict（含 ok / notes / subject_gaps），并写回 plan 公式列。
    """
    budget_mod.compute_all(plan)
    ti = plan.top_inputs
    c2 = float(ti.budget_revenue or 0.0)
    e7 = float(plan.top_computed.expense_budget_cap or 0.0)
    notes: list[str] = []
    fixed_h = 0
    fixed_f = 0

    for l in plan.lines:
        g = float(l.budget_amount or 0)
        d = float(l.last_year_actual or 0)
        f = float(l.reference_amount or 0)
        if g > 0 and f <= 0:
            if d > 0:
                c7 = float(plan.top_computed.revenue_growth_rate or 0)
                l.reference_amount = _round_yuan(d * (1 + c7))
            else:
                l.reference_amount = g
            fixed_f += 1
        # 金额本身不改；H 由 compute 重算

    budget_mod.compute_all(plan)

    # 恒等核验：H 必须 = G/C2
    h_bad = 0
    for l in plan.lines:
        g = float(l.budget_amount or 0)
        if g <= 0 or c2 <= 0:
            continue
        expect = g / c2
        if abs(float(l.budget_expense_ratio or 0) - expect) > 1e-6:
            l.budget_expense_ratio = round(expect, 6)
            fixed_h += 1
            h_bad += 1
    if fixed_f:
        notes.append(f"QA补F：{fixed_f} 行")
    if fixed_h:
        notes.append(f"QA校正H=G/C2：{fixed_h} 行")

    # 科目合计 vs 期间费用
    subj_sums: dict[str, float] = {}
    for l in plan.lines:
        subj_sums[l.subject] = subj_sums.get(l.subject, 0.0) + float(l.budget_amount or 0)
    anchors = _period_expense_anchors(period_expenses)
    gaps = []
    for subj, anchor in anchors.items():
        got = subj_sums.get(subj, 0.0)
        if anchor <= 0:
            continue
        rel = abs(got - anchor) / anchor
        gaps.append(
            {
                "subject": subj,
                "period_latest": round(anchor, 2),
                "budget_sum": round(got, 2),
                "rel_error": round(rel, 4),
                "ok": rel <= 0.35,
            }
        )
        if rel > 0.35:
            notes.append(
                f"QA科目偏差 {subj}：预算{got:,.0f} vs 期间{anchor:,.0f}（{rel:.0%}）"
            )

    g_sum = float(plan.allocated_total or 0)
    fee_rate = (g_sum / c2) if c2 else 0.0
    n_g = sum(1 for l in plan.lines if float(l.budget_amount or 0) > 0)
    n_f = sum(1 for l in plan.lines if float(l.reference_amount or 0) > 0)
    # 抽样恒等：任意有 G 行 H≈G/C2
    identity_ok = True
    if c2 > 0:
        for l in plan.lines:
            g = float(l.budget_amount or 0)
            if g <= 0:
                continue
            if abs(float(l.budget_expense_ratio or 0) - g / c2) > 1e-5:
                identity_ok = False
                break
    missing_f = sum(
        1
        for l in plan.lines
        if float(l.budget_amount or 0) > 0 and float(l.reference_amount or 0) <= 0
    )
    subject_ok = all(g.get("ok", True) for g in gaps) if gaps else True
    ok = identity_ok and missing_f == 0 and c2 > 0 and n_g > 0
    notes.append(
        f"QA定稿：C2={c2:,.0f} · ΣG={g_sum:,.0f} · 费用率{fee_rate:.2%} · "
        f"有G {n_g}/84 · 有F {n_f}/84 · H恒等={'是' if identity_ok else '否'} · "
        f"科目对齐={'是' if subject_ok else '偏差见上'}"
    )
    return {
        "ok": ok,
        "identity_ok": identity_ok,
        "subject_ok": subject_ok,
        "c2": c2,
        "e7": e7,
        "g_sum": round(g_sum, 2),
        "fee_rate": round(fee_rate, 6),
        "n_g": n_g,
        "n_f": n_f,
        "missing_f": missing_f,
        "subject_sums": {k: round(v, 2) for k, v in subj_sums.items()},
        "subject_gaps": gaps,
        "notes": notes,
    }


def optimize_expense_ratios(
    plan: budget_mod.BudgetPlan,
    *,
    max_fee_rate: float | None = None,
    max_single_ratio: float = _MAX_SINGLE_RATIO,
) -> list[str]:
    """占比优化算法：保证「预算 G / 营收 C2」合理，且合计不超费用上限。

    1) 目标费用总额 target = min(E7 费用上限, 营收 × 行业费用率上限)
    2) 若 ΣG > target：按比例缩放全部有预算的行（保持结构）
    3) 单行占比 > max_single_ratio：非刚性科目压到上限，溢出按权重分给未触顶行
    4) 重算 H = G/C2，并同步无上年行的 F≈G

    解决「表格行很多时 DeepSeek 金额堆高、占比失真 / 合计超上限」问题。
    """
    budget_mod.compute_all(plan)
    ti = plan.top_inputs
    tc = plan.top_computed
    c2 = float(ti.budget_revenue or 0.0)
    e7 = float(tc.expense_budget_cap or 0.0)
    fee_cap_rate = max_fee_rate if max_fee_rate is not None else _max_fee_rate(plan.industry)
    notes: list[str] = []

    if c2 <= 0:
        notes.append("占比优化跳过：预算营业收入 C2 为 0")
        return notes

    g_total = sum(float(l.budget_amount or 0) for l in plan.lines)
    if g_total <= 0:
        notes.append("占比优化跳过：预算费用合计为 0")
        return notes

    # 目标总额
    by_rate = c2 * fee_cap_rate
    targets = [by_rate]
    if e7 > 0:
        targets.append(e7)
    target = min(targets)
    notes.append(
        f"占比优化目标：费用合计 ≤ {target:,.0f} 元"
        f"（min(E7={e7:,.0f}, 营收×{fee_cap_rate:.0%}={by_rate:,.0f})）"
    )

    # 1) 整体缩放
    if g_total > target * 1.001:
        scale = target / g_total
        for l in plan.lines:
            g = float(l.budget_amount or 0)
            if g <= 0:
                continue
            l.budget_amount = _round_yuan(g * scale)
            if float(l.last_year_actual or 0) <= 0:
                l.reference_amount = float(l.budget_amount)
        notes.append(f"整体缩放 ×{scale:.4f}：ΣG {g_total:,.0f} → {target:,.0f} 元")
        g_total = target

    # 2) 单行触顶压缩 + 再分配
    overflow = 0.0
    under: list[tuple[budget_mod.ExpenseLine, float]] = []  # (line, room)
    for l in plan.lines:
        g = float(l.budget_amount or 0)
        if g <= 0:
            continue
        ratio = g / c2
        rigid = _is_rigid_line(l.expense_name, l.invoice_name)
        cap_ratio = max_single_ratio * (1.8 if rigid else 1.0)
        max_g = c2 * cap_ratio
        if g > max_g * 1.001:
            overflow += g - max_g
            l.budget_amount = _round_yuan(max_g)
            if float(l.last_year_actual or 0) <= 0:
                l.reference_amount = float(l.budget_amount)
        else:
            under.append((l, max_g - g))

    if overflow > 1 and under:
        total_room = sum(r for _, r in under) or 1.0
        distributed = 0.0
        for l, room in under:
            add = overflow * (room / total_room)
            l.budget_amount = _round_yuan(float(l.budget_amount or 0) + add)
            if float(l.last_year_actual or 0) <= 0:
                l.reference_amount = float(l.budget_amount)
            distributed += add
        notes.append(
            f"单行占比封顶（非刚性≤{max_single_ratio:.0%}，刚性≤{max_single_ratio*1.8:.0%}）："
            f"溢出 {overflow:,.0f} 元已再分配 {distributed:,.0f} 元"
        )
    elif overflow > 1:
        notes.append(f"单行占比封顶：压缩溢出 {overflow:,.0f} 元（无接收行，计入未分配）")

    # 3) 若仍超 target（舍入），微调
    g_total = sum(float(l.budget_amount or 0) for l in plan.lines)
    if g_total > target * 1.001:
        scale = target / g_total
        for l in plan.lines:
            if float(l.budget_amount or 0) > 0:
                l.budget_amount = _round_yuan(float(l.budget_amount) * scale)
                if float(l.last_year_actual or 0) <= 0:
                    l.reference_amount = float(l.budget_amount)
        notes.append(f"二次微调缩放 ×{scale:.4f}")

    budget_mod.compute_all(plan)
    g_final = float(plan.allocated_total or 0)
    notes.append(
        f"优化后：ΣG={g_final:,.0f} 元 · 总费用率={g_final / c2:.2%} · "
        f"未分配余额≈{float(tc.expense_budget_cap or 0) - g_final:,.0f} 元"
    )
    return notes


def optimize_advice_item_ratios(
    suggestions: list[BudgetAdviceItem],
    *,
    revenue: float,
    expense_cap: float,
    industry: str = "",
) -> list[str]:
    """对编制建议列表做同样的占比优化（导出前 UI 展示用）。"""
    notes: list[str] = []
    if not suggestions or revenue <= 0:
        return notes
    fee_rate = _max_fee_rate(industry)
    by_rate = revenue * fee_rate
    target = min(x for x in (by_rate, expense_cap) if x > 0) if expense_cap > 0 else by_rate
    if target <= 0:
        return notes

    selected = [s for s in suggestions if s.selected]
    total = sum(float(s.budget_amount or 0) for s in selected)
    if total <= 0:
        return notes

    if total > target * 1.001:
        scale = target / total
        for s in selected:
            s.budget_amount = _round_yuan(float(s.budget_amount) * scale)
            if not s.has_last_year:
                s.reference_amount = s.budget_amount
            s.budget_ratio = round(s.budget_amount / revenue, 8)
            s.budget_ratio_pct = round(s.budget_ratio * 100, 6)
        notes.append(f"建议占比优化：合计 {total:,.0f}→{target:,.0f} 元（×{scale:.4f}）")
    else:
        for s in selected:
            s.budget_ratio = round(float(s.budget_amount) / revenue, 8) if revenue else 0.0
            s.budget_ratio_pct = round(s.budget_ratio * 100, 6)

    # 单行封顶
    overflow = 0.0
    under: list[BudgetAdviceItem] = []
    for s in selected:
        rigid = _is_rigid_line(s.expense_name, s.invoice_name)
        cap_r = _MAX_SINGLE_RATIO * (1.8 if rigid else 1.0)
        max_g = revenue * cap_r
        if s.budget_amount > max_g * 1.001:
            overflow += s.budget_amount - max_g
            s.budget_amount = _round_yuan(max_g)
            if not s.has_last_year:
                s.reference_amount = s.budget_amount
        else:
            under.append(s)
    if overflow > 1 and under:
        room_sum = sum(max(0.0, revenue * _MAX_SINGLE_RATIO - s.budget_amount) for s in under) or 1.0
        for s in under:
            room = max(0.0, revenue * _MAX_SINGLE_RATIO - s.budget_amount)
            s.budget_amount = _round_yuan(s.budget_amount + overflow * (room / room_sum))
            if not s.has_last_year:
                s.reference_amount = s.budget_amount
        notes.append(f"建议单行封顶后再分配溢出 {overflow:,.0f} 元")

    for s in selected:
        s.budget_ratio = round(float(s.budget_amount) / revenue, 8) if revenue else 0.0
        s.budget_ratio_pct = round(s.budget_ratio * 100, 6)

    final = sum(s.budget_amount for s in selected)
    notes.append(f"建议优化后合计 {final:,.0f} 元 · 总费用率 {final / revenue:.2%}")
    return notes


def apply_advice_to_plan(
    plan: budget_mod.BudgetPlan,
    items: Sequence[dict | BudgetAdviceItem],
    *,
    optimize_ratios: bool = True,
) -> budget_mod.BudgetPlan:
    """把选中建议写入 plan（无上年不写 D）。默认再跑占比优化。"""
    by_row = {l.row: l for l in plan.lines}
    for raw in items:
        if isinstance(raw, BudgetAdviceItem):
            d = raw.to_dict()
        else:
            d = raw
        if not d.get("selected", True):
            continue
        try:
            row = int(d.get("row"))
        except (TypeError, ValueError):
            continue
        line = by_row.get(row)
        if line is None:
            continue
        has_ly = bool(d.get("has_last_year")) and float(d.get("last_year_actual") or 0) > 0
        if has_ly and d.get("write_last_year"):
            line.last_year_actual = _round_yuan(float(d.get("last_year_actual") or 0))
        # 默认：不改 D；只写 F/G（无上年时 F 必须有数，否则 Excel 公式 F=D×(1+g) 恒为 0）
        try:
            ref = _round_yuan(float(d.get("reference_amount") or 0))
        except (TypeError, ValueError):
            ref = 0.0
        try:
            bud = _round_yuan(float(d.get("budget_amount") or 0))
        except (TypeError, ValueError):
            bud = 0.0
        if bud <= 0 and ref > 0:
            bud = ref
        if ref <= 0 and bud > 0:
            ref = bud
        if bud > 0:
            line.budget_amount = bud
        if ref > 0:
            line.reference_amount = ref
        # 无上年时强制 F=G，避免写出后参考列空白
        if float(line.last_year_actual or 0) <= 0 and float(line.budget_amount or 0) > 0:
            if float(line.reference_amount or 0) <= 0:
                line.reference_amount = float(line.budget_amount)
    budget_mod.compute_all(plan)
    # compute_all 后再次保证无上年行的参考金额
    for line in plan.lines:
        if float(line.last_year_actual or 0) <= 0 and float(line.budget_amount or 0) > 0:
            if float(line.reference_amount or 0) <= 0:
                line.reference_amount = float(line.budget_amount)
    if optimize_ratios:
        opt_notes = optimize_expense_ratios(plan)
        if opt_notes:
            plan.notes = list(plan.notes or []) + opt_notes
    return plan


def plan_snapshot(plan: budget_mod.BudgetPlan, data=None) -> dict[str, Any]:
    """计划快照：给 DeepSeek 全量编制用（含 WB 行业基准 + 合规三条硬规则）。"""
    from . import compliance_policy as compliance_mod
    from . import industry as ind_mod

    budget_mod.compute_all(plan)
    ti = plan.top_inputs
    tc = plan.top_computed
    c2 = float(ti.budget_revenue or 0.0)
    cap = float(tc.expense_budget_cap or 0.0)
    allocated = float(plan.allocated_total or 0.0)
    residual = max(0.0, cap - allocated)
    c6 = float(ti.last_year_revenue or 0.0)
    c7 = float(tc.revenue_growth_rate or 0.0)
    zero_lines = sum(
        1
        for l in plan.lines
        if float(l.budget_amount or 0) <= 0 and float(l.last_year_actual or 0) <= 0
    )
    fee_band = ind_mod.get_period_expense_ratio_band(plan.industry)
    cit_hub = ind_mod.get_income_tax_contribution_rate(plan.industry, mode="hub")
    fee_med = float(fee_band.get("median") or 0.12)
    fee_min = float(fee_band.get("min") or 0.08)
    fee_max = float(fee_band.get("max") or 0.18)
    # 建议总费用目标：中枢×营收 与 E7 取小
    target_total = min(x for x in (c2 * fee_med, cap) if x > 0) if c2 > 0 else cap
    catalog = [
        {
            "row": l.row,
            "subject": l.subject,
            "expense_name": l.expense_name,
            "invoice_name": l.invoice_name,
            "last_year_actual": float(l.last_year_actual or 0),
            "budget_amount": float(l.budget_amount or 0),
            "actual_amount": float(l.actual_amount or 0),
            "has_last_year": float(l.last_year_actual or 0) > 0,
            # 有上年时，导出表 F 列将用公式 D×(1+C7)，AI 参考可对齐此值
            "suggested_reference_if_has_ly": _round_yuan(
                float(l.last_year_actual or 0) * (1.0 + c7)
            )
            if float(l.last_year_actual or 0) > 0
            else 0.0,
        }
        for l in plan.lines
    ]
    snap: dict[str, Any] = {
        "company_name": plan.company_name,
        "industry": plan.industry,
        "year": plan.year,
        "budget_revenue": _round_yuan(c2),
        "budget_cost": _round_yuan(float(ti.budget_cost or 0)),
        "last_year_revenue": _round_yuan(c6),
        "expense_budget_cap": _round_yuan(cap),
        "allocated_before": _round_yuan(allocated),
        "residual": _round_yuan(residual),
        "revenue_growth_rate": c7,
        "zero_lines": zero_lines,
        "subject_mix_hint": _subject_mix(plan.industry),
        "wb_model": {
            "source": "WB-行业基准数据库-四大财务指标参考区间与判定规则-20260807",
            "income_tax_contribution_hub": cit_hub,
            "period_expense_ratio_band": fee_band,
            "target_fee_total": _round_yuan(target_total),
            "modeling_notes": [
                "导出：E=D/C6、H=G/C2 预计算/公式；AI 只给金额",
                f"行业费用率 {fee_min:.1%}–{fee_max:.1%}（中枢 {fee_med:.1%}）",
                f"所得税贡献中枢 {cit_hub*100:.2f}%",
            ],
        },
        "catalog": catalog,
        "compliance_rules": compliance_mod.HARD_RULES_LIST,
        "hard_rules": [
            *compliance_mod.HARD_RULES_LIST,
            "必须由你（DeepSeek）给出完整编制建议，不要只改几行",
            "无上年实际时 last_year_actual=0，禁止虚构上年",
            "无上年：必填 reference_amount 与 budget_amount",
            "有上年：reference≈D×(1+g)；budget 增幅≤收入增速+3个百分点",
            f"Σbudget 目标约 {target_total:,.0f} 元，落在营收×[{fee_min:.0%},{fee_max:.0%}] "
            "且 ≤ expense_budget_cap / hard_cap",
            "大类合计对齐历史占比与 period_expenses",
            "reason 须点明：历史占比对标 / 增速匹配 / 金税合规",
            "row 必须来自 catalog",
        ],
    }
    if data is not None:
        lim = compliance_mod.budget_amount_limits(data, plan.industry)
        snap["compliance_limits"] = lim
        if float(lim.get("hard_cap_period_expense_total") or 0) > 0:
            snap["wb_model"]["target_fee_total"] = min(
                float(snap["wb_model"]["target_fee_total"] or 0) or 1e18,
                float(lim["hard_cap_period_expense_total"]),
            )
    return snap


def advice_catalog_for_ai(result: BudgetAdviceResult, plan: budget_mod.BudgetPlan) -> dict[str, Any]:
    """兼容旧调用：全量快照 + 可选规则草案仅作参考。"""
    snap = plan_snapshot(plan)
    snap["rule_draft_optional"] = [
        {
            "row": s.row,
            "subject": s.subject,
            "expense_name": s.expense_name,
            "budget_amount": s.budget_amount,
            "reason": s.reason,
        }
        for s in (result.suggestions or [])[:20]
    ]
    snap["rules"] = snap["hard_rules"]
    snap["empty_or_zero_budget_lines"] = [
        c for c in snap["catalog"] if float(c.get("budget_amount") or 0) <= 0
    ][:60]
    return snap


def build_from_ai_items(
    plan: budget_mod.BudgetPlan,
    ai_items: Sequence[dict],
    *,
    ai_summary: str = "",
) -> BudgetAdviceResult:
    """以 DeepSeek 输出为主构造建议结果（规则只校验硬约束）。"""
    budget_mod.compute_all(plan)
    ti = plan.top_inputs
    tc = plan.top_computed
    c2 = float(ti.budget_revenue or 0.0)
    cap = float(tc.expense_budget_cap or 0.0)
    allocated = float(plan.allocated_total or 0.0)
    residual = max(0.0, cap - allocated)
    zero_lines = sum(
        1
        for l in plan.lines
        if float(l.budget_amount or 0) <= 0 and float(l.last_year_actual or 0) <= 0
    )
    line_map = {l.row: l for l in plan.lines}
    suggestions: list[BudgetAdviceItem] = []

    for raw in ai_items:
        if not isinstance(raw, dict) or raw.get("drop"):
            continue
        try:
            row = int(raw.get("row"))
        except (TypeError, ValueError):
            continue
        line = line_map.get(row)
        if line is None:
            continue
        try:
            bud = float(raw.get("budget_amount") or 0)
        except (TypeError, ValueError):
            bud = 0.0
        try:
            ref = float(raw.get("reference_amount") or 0)
        except (TypeError, ValueError):
            ref = 0.0
        try:
            ratio_pct_in = float(raw.get("budget_ratio_pct") or 0)
        except (TypeError, ValueError):
            ratio_pct_in = 0.0
        d_existing = float(line.last_year_actual or 0)
        has_ly = d_existing > 0
        # DeepSeek 占比优先：G = 营收 × 占比%
        if ratio_pct_in > 0 and c2 > 0:
            amount = _round_yuan(c2 * ratio_pct_in / 100.0)
        else:
            amount = _round_yuan(bud or ref)
        if amount <= 0 and ref <= 0:
            continue
        if has_ly:
            ref_out = _round_yuan(ref or d_existing * (1 + float(tc.revenue_growth_rate or 0)))
            bud_out = _round_yuan(amount)
            ly = _round_yuan(d_existing)
        else:
            # 硬约束：无上年不写 D，只写 F/G
            bud_out = _round_yuan(amount)
            ref_out = _round_yuan(ref or amount)
            ly = 0.0
        ratio = (bud_out / c2) if c2 > 0 else 0.0
        prio = str(raw.get("priority") or "mid")
        if prio not in ("high", "mid", "low"):
            prio = "mid"
        suggestions.append(
            BudgetAdviceItem(
                row=row,
                subject=line.subject,
                expense_name=line.expense_name,
                invoice_name=line.invoice_name,
                has_last_year=has_ly,
                last_year_actual=ly,
                reference_amount=ref_out,
                budget_amount=bud_out,
                budget_ratio=round(ratio, 8),
                budget_ratio_pct=round(ratio * 100, 6),
                priority=prio,
                reason=str(raw.get("reason") or "").strip() or "DeepSeek 编制建议",
                source="ai",
                selected=bool(raw.get("selected", True)),
                write_last_year=False,
            )
        )

    prio_rank = {"high": 0, "mid": 1, "low": 2}
    suggestions.sort(key=lambda s: (prio_rank.get(s.priority, 9), -s.budget_amount, s.row))
    # 本地不再硬缩放占比结构；仅做一致性：H=G/C2（DeepSeek 负责结构）
    for s in suggestions:
        if c2 > 0 and s.budget_amount > 0:
            s.budget_ratio = round(s.budget_amount / c2, 8)
            s.budget_ratio_pct = round(s.budget_ratio * 100, 6)
            if not s.has_last_year and s.reference_amount <= 0:
                s.reference_amount = s.budget_amount
    total_sug = sum(s.budget_amount for s in suggestions if s.selected)
    notes = [
        "编制模式：DeepSeek 全量介入（金额+占营收比重）",
        f"费用预算上限 E7 = {cap:,.2f} 元 · 营收 C2 = {c2:,.2f} 元",
        f"导出前已分配 G = {allocated:,.2f} 元；residual = {residual:,.2f} 元",
        f"DeepSeek 建议 {len(suggestions)} 条，勾选合计约 {total_sug:,.2f} 元"
        + (f"（总费用率 {total_sug / c2:.2%}）" if c2 else ""),
        "占比 H = 预算G ÷ 预算营业收入C2（与 DeepSeek budget_ratio_pct 对齐）",
        "无上年实际不写 D，只写参考金额 F / 预算 G / 占比 H",
    ]

    return BudgetAdviceResult(
        company_name=plan.company_name,
        industry=plan.industry,
        year=plan.year,
        budget_revenue=_round_yuan(c2),
        expense_budget_cap=_round_yuan(cap),
        allocated_before=_round_yuan(allocated),
        residual=_round_yuan(residual),
        zero_lines=zero_lines,
        suggestions=suggestions,
        algorithm_notes=notes,
        ai_used=True,
        ai_summary=ai_summary or "",
        subject_mix=_subject_mix(plan.industry),
    )
