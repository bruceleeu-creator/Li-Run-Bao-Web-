"""利润宝 · 模板版预算计算引擎（T6.1 + T6.2）。

实现《企业成本费用计划表（模板）》的顶部公式与明细十栏公式，
全部除法加入零值保护，比例与金额在模型层显式区分。

- 顶部公式：C4/C5/C7/C9/E5/E6/E7/E8/E9/G100
- 明细公式：E（上年费用率）/ F（参考金额）/ H（预算率）/ J（差额）
- 合计行：D98/G98/I98/J98 求和；E98/F98/H98 联动
- E8 自动等于 I98（实际已发生费用与明细联动）
- 工会经费（R90）补齐 F/H/J 公式
- 预算上限 / 已分配 / 实际发生 / 未分配余额 / 超支金额 实时联动
- 所有金额单位为「元」，比例为「小数」（如 0.05 表示 5%）

合规边界：本模块仅做确定性数学计算；所有建议须符合现行法律法规
与真实业务原则（研发加计扣除、限额内据实扣除、业务模式优化等）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .budget_categories import (
    EXPENSE_CATEGORIES,
    ExpenseCategory,
    SUBJECT_ADMIN,
    SUBJECT_FINANCE,
    SUBJECT_NON_OPERATING,
    SUBJECT_SALES,
    TEMPLATE_BALANCE_ROW,
    TEMPLATE_FIRST_ROW,
    TEMPLATE_LAST_ROW,
    TEMPLATE_TOTAL_ROW,
)


# ── 单位与精度 ───────────────────────────────────────────────────────────
UNIT_YUAN = "元"
UNIT_PERCENT = "百分比"  # 内部用小数存储（0.05 = 5%），UI 显示时乘 100
PRECISION_YUAN = 2       # 金额精度：2 位
PRECISION_RATIO = 6      # 比例精度：6 位（避免 0.003 被截为 0.0）

# 默认值（与原模板一致；模板默认 0.30% 与 5% 仅作示例，须标注"待核验"）
DEFAULT_INDUSTRY_CONTRIBUTION_RATE = 0.003  # 0.30%
DEFAULT_COMPANY_CONTRIBUTION_RATE = 0.003
DEFAULT_INCOME_TAX_RATE = 0.05  # 5%（小型微利企业优惠税率，须核验适用条件）
INCOME_TAX_RATE_CHOICES = (0.05, 0.15, 0.25)  # 5% / 15% / 25%

# 执行监控阈值（执行率 = 实际 / 预算）
EXEC_THRESHOLD_NORMAL = 0.80      # < 80% 正常
EXEC_THRESHOLD_CRITICAL = 1.00    # 80%-100% 临界；> 100% 超支
EXEC_STATUS_NORMAL = "正常"
EXEC_STATUS_CRITICAL = "临界"
EXEC_STATUS_OVER = "超支"
EXEC_STATUS_PENDING = "待补录"


def _safe_div(numerator: float, denominator: float) -> float:
    """零值保护除法：分母为 0 时返回 0.0。"""
    if denominator == 0 or denominator is None:
        return 0.0
    return numerator / denominator


def _round_yuan(v: float) -> float:
    """金额四舍五入到 2 位。"""
    return round(v, PRECISION_YUAN)


def _round_ratio(v: float) -> float:
    """比例四舍五入到 6 位（保留 0.003 等小比例的有效数字）。"""
    return round(v, PRECISION_RATIO)


# 向后兼容别名（旧测试可能引用）
_round2 = _round_yuan


# ── 领域模型 ─────────────────────────────────────────────────────────────

@dataclass
class TopInputs:
    """顶部输入区（A2:E9）。

    金额字段单位：元；比例字段单位：小数（0.05 = 5%）。
    行业贡献率与企业贡献率均以小数存储，UI 显示时乘 100。
    """
    budget_revenue: float = 0.0           # C2 预算营业收入（元）
    budget_cost: float = 0.0              # C3 预算营业成本（元）
    last_year_revenue: float = 0.0        # C6 上年度同期营业收入（元）
    last_year_cost: float = 0.0           # C8 上年度同期营业成本（元）
    industry_contribution_rate: float = DEFAULT_INDUSTRY_CONTRIBUTION_RATE  # E2 行业所得税贡献率（小数）
    company_contribution_rate: float = DEFAULT_COMPANY_CONTRIBUTION_RATE    # E3 企业预算所得税贡献率（小数）
    income_tax_rate: float = DEFAULT_INCOME_TAX_RATE                       # E4 所得税税率（小数）
    # 来源元数据（不参与计算，仅供核验）
    industry_rate_source: str = "模板默认（待核验）"
    industry_rate_year: str = ""
    industry_rate_region: str = ""
    industry_rate_verified: bool = False


@dataclass
class TopComputed:
    """顶部公式计算结果（C4/C5/C7/C9/E5/E6/E7/E8/E9）。单位与输入一致。"""
    gross_profit: float = 0.0             # C4 毛利 = C2 - C3
    gross_margin: float = 0.0             # C5 毛利率 = C4 / C2（小数）
    revenue_growth_rate: float = 0.0      # C7 收入增长率 = (C2-C6)/C6（小数）
    last_year_gross_margin: float = 0.0   # C9 上年度毛利率 = (C6-C8)/C6（小数）
    income_tax_budget: float = 0.0        # E5 应交所得税预算 = E3 * C2
    profit_total_budget: float = 0.0      # E6 利润总额预算 = E5 / E4
    expense_budget_cap: float = 0.0       # E7 费用预算上限 = C4 - E6
    actual_expense_total: float = 0.0     # E8 实际已发生费用 = I98（明细联动）
    expense_diff: float = 0.0             # E9 费用差额 = E7 - E8（正=剩余，负=超支）
    unallocated_balance: float = 0.0      # G100 未分配余额 = E7 - G98


@dataclass
class ExpenseLine:
    """单行费用明细（模板 R14-R97 之一）。

    金额单位：元；比例单位：小数。
    D/G/I 为输入；E/F/H/J 为公式计算结果。
    """
    row: int                              # 模板行号 14-97
    subject: str                          # 科目名称（四大类）
    expense_name: str                     # 费用名称（二级）
    invoice_name: str                     # 发票名称（三级）
    last_year_actual: float = 0.0         # D 上年同期实际费用（元）
    budget_amount: float = 0.0            # G 预算费用金额（元）
    actual_amount: float = 0.0            # I 实际已发生费用金额（元）
    # 公式列（计算后填入）
    last_year_expense_ratio: float = 0.0  # E = IF($C$6=0,0, D/$C$6)
    reference_amount: float = 0.0         # F = D * (1+$C$7)
    budget_expense_ratio: float = 0.0     # H = IF($C$2=0,0, G/$C$2)
    diff: float = 0.0                     # J = G - I
    # 派生状态
    exec_rate: float = 0.0                # 执行率 = I / G
    exec_status: str = EXEC_STATUS_PENDING

    @property
    def is_union_fee(self) -> bool:
        """是否为工会经费行（R90，原模板缺 F/H/J 公式）。"""
        return self.row == 90


@dataclass
class BudgetPlan:
    """一份完整的预算计划（顶部 + 84 行明细 + 行业基准）。"""
    company_name: str = ""
    industry: str = "制造业"
    year: int = 0
    top_inputs: TopInputs = field(default_factory=TopInputs)
    top_computed: TopComputed = field(default_factory=TopComputed)
    lines: List[ExpenseLine] = field(default_factory=list)
    # 行业基准（参考表）
    industry_benchmarks: List[Dict] = field(default_factory=list)
    # 元数据
    source_path: str = ""
    notes: List[str] = field(default_factory=list)

    # ── 便捷访问 ──
    @property
    def lines_by_subject(self) -> Dict[str, List[ExpenseLine]]:
        """按科目分组返回明细行。"""
        result: Dict[str, List[ExpenseLine]] = {s: [] for s in
                                                (SUBJECT_SALES, SUBJECT_ADMIN, SUBJECT_FINANCE, SUBJECT_NON_OPERATING)}
        for line in self.lines:
            result.setdefault(line.subject, []).append(line)
        return result

    @property
    def total_lines(self) -> int:
        return len(self.lines)

    @property
    def allocated_total(self) -> float:
        """已分配预算（G98 = SUM(G14:G97)）。"""
        return _round_yuan(sum(l.budget_amount for l in self.lines))

    @property
    def actual_total(self) -> float:
        """实际已发生费用（I98 = SUM(I14:I97)）。"""
        return _round_yuan(sum(l.actual_amount for l in self.lines))

    @property
    def last_year_total(self) -> float:
        """上年同期实际费用（D98 = SUM(D14:D97)）。"""
        return _round_yuan(sum(l.last_year_actual for l in self.lines))

    @property
    def diff_total(self) -> float:
        """差额合计（J98 = SUM(J14:J97)）。"""
        return _round_yuan(sum(l.diff for l in self.lines))

    @property
    def over_budget_count(self) -> int:
        """超支项数。"""
        return sum(1 for l in self.lines if l.exec_status == EXEC_STATUS_OVER)

    @property
    def critical_count(self) -> int:
        """临界项数。"""
        return sum(1 for l in self.lines if l.exec_status == EXEC_STATUS_CRITICAL)

    @property
    def pending_count(self) -> int:
        """待补录项数（预算与实际都为 0）。"""
        return sum(1 for l in self.lines if l.exec_status == EXEC_STATUS_PENDING)


# ── 计算引擎 ─────────────────────────────────────────────────────────────

def compute_top(ti: TopInputs) -> TopComputed:
    """计算顶部公式（零值保护）。

    输入：TopInputs（金额单位元，比例单位小数）
    输出：TopComputed（金额单位元，比例单位小数）
    """
    c2 = float(ti.budget_revenue or 0.0)
    c3 = float(ti.budget_cost or 0.0)
    c6 = float(ti.last_year_revenue or 0.0)
    c8 = float(ti.last_year_cost or 0.0)
    e3 = float(ti.company_contribution_rate or 0.0)
    e4 = float(ti.income_tax_rate or 0.0)

    c4 = c2 - c3                                              # 毛利
    c5 = _safe_div(c4, c2)                                    # 毛利率
    c7 = _safe_div(c2 - c6, c6)                               # 收入增长率
    c9 = _safe_div(c6 - c8, c6)                               # 上年度毛利率
    e5 = e3 * c2                                              # 应交所得税预算
    e6 = _safe_div(e5, e4)                                    # 利润总额预算
    e7 = c4 - e6                                              # 费用预算上限
    # E8 = I98：在 compute_all 中联动填充
    return TopComputed(
        gross_profit=_round_yuan(c4),
        gross_margin=_round_ratio(c5),
        revenue_growth_rate=_round_ratio(c7),
        last_year_gross_margin=_round_ratio(c9),
        income_tax_budget=_round_yuan(e5),
        profit_total_budget=_round_yuan(e6),
        expense_budget_cap=_round_yuan(e7),
        # actual_expense_total / expense_diff / unallocated_balance 在 compute_all 填入
    )


def compute_line(line: ExpenseLine, ti: TopInputs) -> None:
    """计算单行明细公式（零值保护，原地更新 line）。

    E = IF($C$6=0,0, D/$C$6)
    F = D * (1+$C$7)
    H = IF($C$2=0,0, G/$C$2)
    J = G - I
    执行率 = I / G（G=0 时为 0）
    状态：待补录（G=I=0）/ 正常（<80%）/ 临界（80-100%）/ 超支（>100%）

    注：R90 工会经费在原模板缺 F/H/J 公式，本引擎统一补齐（不依赖行号特判）。
    """
    c2 = float(ti.budget_revenue or 0.0)
    c6 = float(ti.last_year_revenue or 0.0)
    c7 = compute_top(ti).revenue_growth_rate  # 复用零值保护

    d = float(line.last_year_actual or 0.0)
    g = float(line.budget_amount or 0.0)
    i = float(line.actual_amount or 0.0)

    line.last_year_expense_ratio = _round_ratio(_safe_div(d, c6))
    line.reference_amount = _round_yuan(d * (1 + c7))
    line.budget_expense_ratio = _round_ratio(_safe_div(g, c2))
    line.diff = _round_yuan(g - i)
    line.exec_rate = _round_ratio(_safe_div(i, g))

    # 状态判定
    if g == 0 and i == 0:
        line.exec_status = EXEC_STATUS_PENDING
    elif line.exec_rate > EXEC_THRESHOLD_CRITICAL:
        line.exec_status = EXEC_STATUS_OVER
    elif line.exec_rate >= EXEC_THRESHOLD_NORMAL:
        line.exec_status = EXEC_STATUS_CRITICAL
    else:
        line.exec_status = EXEC_STATUS_NORMAL


def compute_all(plan: BudgetPlan) -> BudgetPlan:
    """计算整份预算计划：顶部 → 各行 → 合计联动 → E8=I98 → E9 → G100。

    返回 plan 本身（已就地更新 top_computed 与各 line 公式列）。
    """
    ti = plan.top_inputs
    # 1) 顶部公式（不含 E8/E9/G100）
    tc = compute_top(ti)

    # 2) 各行公式
    for line in plan.lines:
        compute_line(line, ti)

    # 3) 合计联动
    d98 = plan.last_year_total
    g98 = plan.allocated_total
    i98 = plan.actual_total
    j98 = plan.diff_total

    # 4) E8 = I98；E9 = E7 - E8；G100 = E7 - G98
    tc.actual_expense_total = i98
    tc.expense_diff = _round_yuan(tc.expense_budget_cap - i98)
    tc.unallocated_balance = _round_yuan(tc.expense_budget_cap - g98)

    plan.top_computed = tc
    # 暴露合计行（便于导出与测试访问）
    plan.notes = [
        f"D98（上年同期实际费用合计）= {d98:,.2f} 元",
        f"G98（预算费用合计）= {g98:,.2f} 元",
        f"I98（实际已发生费用合计）= {i98:,.2f} 元",
        f"J98（差额合计）= {j98:,.2f} 元",
        f"E8 = I98 联动：{tc.actual_expense_total:,.2f} 元",
        f"E9 = E7 - E8 = {tc.expense_diff:,.2f} 元（正=剩余额度，负=超支）",
        f"G100 = E7 - G98 = {tc.unallocated_balance:,.2f} 元（负=预算分配超上限）",
    ]
    return plan


# ── 工厂函数 ─────────────────────────────────────────────────────────────

def make_empty_plan(company_name: str = "", industry: str = "制造业", year: int = 0) -> BudgetPlan:
    """新建空白预算计划：84 行字典全 0，顶部输入全 0。"""
    lines = [
        ExpenseLine(
            row=c.row, subject=c.subject,
            expense_name=c.expense_name, invoice_name=c.invoice_name,
        )
        for c in EXPENSE_CATEGORIES
    ]
    return BudgetPlan(
        company_name=company_name,
        industry=industry,
        year=year,
        top_inputs=TopInputs(),
        top_computed=TopComputed(),
        lines=lines,
    )


def validate_plan(plan: BudgetPlan) -> Tuple[bool, List[str]]:
    """P1-1：校验预算计划完整性与字段范围。返回 (ok, errors)。

    校验项：
    - 84 行明细完整性（行数 + 行号连续 14-97）
    - 金额非负：D/G/I 各行 + C2/C3/C6/C8 顶部金额
    - 比例范围：E2/E3 行业/企业贡献率 ∈ [0, 1]；E4 所得税率 ∈ {0.05, 0.15, 0.25}
    - 逻辑约束：C2 预算营业收入 ≥ 0；C3 预算营业成本 ≥ 0；C6/C8 上年度金额 ≥ 0
    """
    errors: List[str] = []
    # 1) 84 行明细完整性
    if len(plan.lines) != 84:
        errors.append(f"明细行数应为 84，实际 {len(plan.lines)}")
    rows = [l.row for l in plan.lines]
    expected = list(range(TEMPLATE_FIRST_ROW, TEMPLATE_LAST_ROW + 1))
    if rows != expected:
        errors.append("明细行号不连续 14-97")
    # 2) 行级金额非负
    for l in plan.lines:
        if l.last_year_actual < 0 or l.budget_amount < 0 or l.actual_amount < 0:
            errors.append(f"R{l.row} 金额不能为负（D={l.last_year_actual}, G={l.budget_amount}, I={l.actual_amount}）")
            break
    # 3) 顶部金额非负
    ti = plan.top_inputs
    if ti.budget_revenue < 0:
        errors.append("C2 预算营业收入不能为负")
    if ti.budget_cost < 0:
        errors.append("C3 预算营业成本不能为负")
    if ti.last_year_revenue < 0:
        errors.append("C6 上年度营业收入不能为负")
    if ti.last_year_cost < 0:
        errors.append("C8 上年度营业成本不能为负")
    # 4) 比例范围（E2/E3 ∈ [0, 1]；E4 必须为 5%/15%/25%）
    if not (0.0 <= ti.industry_contribution_rate <= 1.0):
        errors.append(f"E2 行业所得税贡献率应在 [0, 1] 之间，实际 {ti.industry_contribution_rate}")
    if not (0.0 <= ti.company_contribution_rate <= 1.0):
        errors.append(f"E3 企业所得税贡献率应在 [0, 1] 之间，实际 {ti.company_contribution_rate}")
    if ti.income_tax_rate not in INCOME_TAX_RATE_CHOICES:
        errors.append(f"E4 所得税税率必须为 {INCOME_TAX_RATE_CHOICES} 之一（5%/15%/25%），实际 {ti.income_tax_rate}")
    # 5) 逻辑约束：C2 应 ≥ C3（毛利非负；仅警告不阻塞）
    # （不加入 errors，仅在 GUI 提示）
    return (len(errors) == 0, errors)


def filter_lines(
    plan: BudgetPlan,
    subject: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
) -> List[ExpenseLine]:
    """按科目 / 状态 / 关键词筛选明细行。"""
    result = plan.lines
    if subject:
        result = [l for l in result if l.subject == subject]
    if status:
        result = [l for l in result if l.exec_status == status]
    if keyword:
        kw = keyword.strip().lower()
        if kw:
            result = [
                l for l in result
                if kw in l.subject.lower()
                or kw in l.expense_name.lower()
                or kw in l.invoice_name.lower()
            ]
    return result
