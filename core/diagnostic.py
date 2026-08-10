"""利润宝 · 第一轮诊断引擎（S5）。

基于 FinancialData + 行业基准生成结构化发现（Findings）。
所有建议均落在合法税务筹划范畴；增值税税负率为估算口径，须显著标注。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import finance as fin
from . import industry as ind
from .models import FinancialData

# 严重度
SEVERITY_HIGH = "高"
SEVERITY_MEDIUM = "中"
SEVERITY_LOW = "低"

# 类别
CATEGORY_TAX = "税负率"
CATEGORY_STRUCTURE = "成本费用结构"
CATEGORY_REALITY = "真实性风险"

# 风险等级（落地性影响）
RISK_HIGH = "高"
RISK_MEDIUM = "中"
RISK_LOW = "低"

# 合规声明（合法税务筹划边界）
COMPLIANCE_NOTE = (
    "本建议属于合法税务筹划范畴（如研发费用加计扣除、限额内据实扣除、"
    "业务模式优化等）。严禁任何形式的虚开发票、隐匿收入、虚构成本。"
    "具体执行前建议与主管税务机关或注册税务师确认。"
)

# 业务招待费扣除限额：发生额 60% 与营收 0.5% 孰低
ENTERTAIN_REVENUE_RATIO = 0.005
ENTERTAIN_DEDUCT_RATIO = 0.6

# 咨询服务费占营收比例预警阈值
CONSULTING_WARNING_RATIO = 0.03  # 3%

# 经营质量 / 结构类阈值（确定性规则，可复核）
AR_REVENUE_WARN = 0.40          # 应收账款/营收 ≥40% 关注回款
DEBT_RATIO_WARN = 0.70          # 资产负债率 ≥70% 关注杠杆
REVENUE_DECLINE_WARN = -0.10    # 营收同比下降 ≥10%
PROFIT_DECLINE_WARN = -0.15     # 净利润同比下降 ≥15%
WELFARE_SOFT_MIN_RATIO = 0.003  # 福利费/营收 <0.3% 且管理费用率不低 → 未用足信号
EDU_SOFT_MIN_RATIO = 0.0015     # 教育经费/营收 <0.15% → 未用足信号
AD_EXPENSE_TAX_CAP = 0.15       # 广告宣传费一般扣除上限：营收 15%
SOFT_MARGIN_FACTOR = 0.92       # 毛利率/净利率低于中枢×0.92 的「关注」线


@dataclass
class Option:
    """互动选项（A/B/C 之一）。

    单位规则：cost_saving / tax_saving / tax_impact / est_saving 均为「元」；
    target_value 单位由 finding.unit 决定（金额类为元，比例类为 %）。
    业务口径：
    - cost_saving：成本节约（费用压降带来的现金节约，非负）
    - tax_saving：税收节约（增加可扣除投入带来的所得税减少，非负；
      如研发加计扣除、限额内据实扣除）
    - tax_impact：税负影响（费用压降导致可扣除减少 → 所得税增加，正数；
      增加费用时为负数）
    - est_saving：净影响 = cost_saving + tax_saving - tax_impact（兼容旧字段）
    - tax_rate / deduction_rate：项目测算默认值，须以企业实际适用条件核验
    """
    label: str  # "A" / "B" / "C"
    name: str
    description: str
    target_value: float  # 目标值（单位与 finding.unit 一致）
    tax_rate: float = 0.0  # 适用税率（小数，如 0.25；项目测算默认值，待核验）
    est_saving: float = 0.0  # 净影响（元）= cost_saving + tax_saving - tax_impact
    cost_saving: float = 0.0   # 成本节约（元，非负）
    tax_saving: float = 0.0    # 税收节约（元，非负）
    tax_impact: float = 0.0    # 税负影响（元，可正可负；正=所得税增加）
    feasibility: str = "中"  # 高/中/低
    risk_level: str = RISK_LOW  # 高/中/低
    action_note: str = ""  # 操作细节
    # 加计扣除比例（小数；研发 100% 加计扣除取 1.0；普通据实扣除取 1.0；不享受取 0.0）
    # 项目测算默认值，须以企业实际适用政策核验
    deduction_rate: float = 0.0


@dataclass
class Finding:
    """一条诊断发现。"""
    id: str
    title: str
    category: str  # CATEGORY_*
    severity: str  # SEVERITY_*
    fact: str  # 事实描述
    benchmark: str  # 行业对标情况
    suggestion: str  # 初稿建议
    options: List[Option] = field(default_factory=list)
    current_value: float = 0.0
    target_value: float = 0.0
    unit: str = "%"  # 默认百分比；金额用 "元"
    status: str = "pending"  # pending / accepted / deferred


@dataclass
class DiagnosisResult:
    """诊断结果集合。"""
    company_name: str
    industry: str
    industry_fallback: bool
    years: List[int]
    findings: List[Finding] = field(default_factory=list)
    vat_estimate_note: str = fin.VAT_ESTIMATE_NOTE

    def finding_by_id(self, fid: str) -> Optional[Finding]:
        for f in self.findings:
            if f.id == fid:
                return f
        return None


def _fmt_pct(v: float) -> str:
    return f"{v:.2f}%"


def _fmt_money(v: float) -> str:
    return f"{v:,.0f} 元"


def _build_rd_options(revenue: float) -> List[Option]:
    """研发费用缺失 → A/B/C 三个可量化选项（金额化 + 加计扣除节税）。

    业务口径：研发投入增加 → 加计扣除 → 所得税减少（税收节约）。
    研发投入本身是费用增加，不产生成本节约（cost_saving=0），
    也不产生税负影响（费用增加使可扣除增加 → 所得税减少，
    已计入 tax_saving，不在 tax_impact 重复计算）。

    单位：current_amount=0 元（研发费用缺失）；target_amount=营收 × 比例（元）。
    税率与加计扣除比例为项目测算默认值，须以企业实际适用政策核验。
    """
    target_a = revenue * 0.05  # 行业中位数 5%（金额，元）
    target_b = revenue * 0.03  # 行业下限 3%（金额，元）
    # 研发费用 100% 加计扣除：扣除总额 = 投入 × 2.0；节约税款 = 投入 × 1.0 × 税率
    rd_deduction_rate = 1.0  # 项目测算默认值：研发费用 100% 加计扣除（待核验适用条件）
    cit_rate = 0.25  # 项目测算默认值：企业所得税率 25%（待核验企业实际适用税率）
    return [
        Option(
            label="A",
            name="立即启动研发费用归集与加计扣除",
            description=(
                f"按营收 5%（行业中位数）设立研发费用辅助账，"
                f"目标研发投入 {_fmt_money(target_a)}，享受 100% 加计扣除。"
            ),
            target_value=target_a,
            tax_rate=cit_rate,
            cost_saving=0.0,  # 研发投入增加，无成本节约
            tax_saving=fin.tax_saving_estimate(0.0, target_a, cit_rate, rd_deduction_rate),
            tax_impact=0.0,
            feasibility="中",
            risk_level=RISK_LOW,
            action_note="建立研发辅助账、立项文件、人员工时记录、材料领用单。",
            deduction_rate=rd_deduction_rate,
        ),
        Option(
            label="B",
            name="分阶段试点研发归集",
            description=(
                f"按营收 3%（行业下限）启动试点，"
                f"目标研发投入 {_fmt_money(target_b)}，按实际发生额加计扣除。"
            ),
            target_value=target_b,
            tax_rate=cit_rate,
            cost_saving=0.0,
            tax_saving=fin.tax_saving_estimate(0.0, target_b, cit_rate, rd_deduction_rate),
            tax_impact=0.0,
            feasibility="高",
            risk_level=RISK_LOW,
            action_note="先选 1-2 个产品线试点，完善制度后再推广。",
            deduction_rate=rd_deduction_rate,
        ),
        Option(
            label="C",
            name="暂维持现状，仅做研发费用备查",
            description="不立即启动加计扣除，仅留存研发支出凭证以备后续年度追溯。",
            target_value=0.0,
            tax_rate=0.0,
            cost_saving=0.0,
            tax_saving=0.0,
            tax_impact=0.0,
            feasibility="高",
            risk_level=RISK_MEDIUM,
            action_note="风险提示：放弃当期加计扣除，税收节约为 0；同时存在税务稽查关注研发归集不足的可能。",
            deduction_rate=0.0,
        ),
    ]


# 计算 Option 的净影响（est_saving），统一在此处汇总避免分支遗漏
def _finalize_option_savings(opts: List[Option]) -> None:
    """对每个 Option 填入 est_saving = cost_saving + tax_saving - tax_impact。"""
    for o in opts:
        o.est_saving = fin.net_benefit_estimate(o.cost_saving, o.tax_saving, o.tax_impact)


def _build_entertainment_options(revenue: float, current: float) -> List[Option]:
    """业务招待费超限 → A/B/C（费用压降：成本节约 + 税负影响）。

    业务口径：
    - 压降费用 → 现金支出减少 = cost_saving（非负）
    - 压降费用 → 可扣除减少 → 所得税增加 = tax_impact（正数）
    - 招待费扣除限额 = MIN(发生额×60%, 营收×0.5%)，超限部分本就不可扣
    - 不可扣部分压降不产生 tax_impact；限额内压降产生正 tax_impact
    - tax_saving=0（招待费不享受加计扣除）

    单位：current_amount=current 元；target_amount=目标金额（元）。
    税率为项目测算默认值，须以企业实际适用政策核验。
    """
    deduct_limit = min(current * ENTERTAIN_DEDUCT_RATIO, revenue * ENTERTAIN_REVENUE_RATIO)
    # 选项 A：压缩到限额内（发生额上限 = 限额 / 60%）
    target_a = deduct_limit / ENTERTAIN_DEDUCT_RATIO  # 发生额上限（金额，元）
    # 选项 B：压缩到营收 0.4%
    target_b = revenue * 0.004  # 金额，元
    cit_rate = 0.25  # 项目测算默认值：企业所得税率 25%（待核验企业实际适用税率）
    # 限额内可扣部分 = deduct_limit；不可扣部分 = current - deduct_limit
    # 压降到 target_a 时：可扣部分 = target_a * 60% 与营收 0.5% 孰低
    def _calc_savings(cur_amt: float, tgt_amt: float) -> tuple:
        # 不可扣部分压降不产生 tax_impact；可扣部分压降产生正 tax_impact
        cur_deduct = min(cur_amt * ENTERTAIN_DEDUCT_RATIO, revenue * ENTERTAIN_REVENUE_RATIO)
        tgt_deduct = min(tgt_amt * ENTERTAIN_DEDUCT_RATIO, revenue * ENTERTAIN_REVENUE_RATIO)
        # 可扣部分减少量
        deduct_drop = max(0.0, cur_deduct - tgt_deduct)
        cs = fin.cost_saving_estimate(cur_amt, tgt_amt)
        ts = 0.0  # 招待费无加计扣除
        ti = max(0.0, deduct_drop) * cit_rate  # 可扣减少 → 所得税增加（正数）
        return cs, ts, ti

    cs_a, ts_a, ti_a = _calc_savings(current, target_a)
    cs_b, ts_b, ti_b = _calc_savings(current, target_b)
    return [
        Option(
            label="A",
            name="压缩招待费至扣除限额内",
            description=(
                f"将招待费发生额降至 {_fmt_money(target_a)}，使按发生额 60% 计算的可扣除额"
                f"不超过营业收入 0.5% 上限（适用条件待核验）。"
            ),
            target_value=target_a,
            tax_rate=cit_rate,
            cost_saving=cs_a,
            tax_saving=ts_a,
            tax_impact=ti_a,
            feasibility="中",
            risk_level=RISK_LOW,
            action_note="建立事前审批、事中预算控制、事后业务真实性留痕制度。",
            deduction_rate=0.0,
        ),
        Option(
            label="B",
            name="适度压缩并规范业务真实性",
            description=f"降至 {_fmt_money(target_b)}，强化招待事由、人员、金额留痕。",
            target_value=target_b,
            tax_rate=cit_rate,
            cost_saving=cs_b,
            tax_saving=ts_b,
            tax_impact=ti_b,
            feasibility="高",
            risk_level=RISK_LOW,
            action_note="保留会议纪要、来访人员名单、餐饮发票等四流合一凭证。",
            deduction_rate=0.0,
        ),
        Option(
            label="C",
            name="暂维持现状，但补充业务真实性证据",
            description=f"维持 {_fmt_money(current)}，补充证据链应对后续稽查。",
            target_value=current,
            tax_rate=0.0,
            cost_saving=0.0,
            tax_saving=0.0,
            tax_impact=0.0,
            feasibility="高",
            risk_level=RISK_HIGH,
            action_note="风险提示：超过扣除限额部分不得税前扣除，且真实性不足可能被纳税调整。",
            deduction_rate=0.0,
        ),
    ]


def _build_consulting_options(revenue: float, current: float) -> List[Option]:
    """咨询服务费偏高 → A/B/C（费用压降：成本节约 + 税负影响）。

    业务口径：咨询费全额可税前扣除（非超限项），压降费用 →
    - cost_saving = current - target（正数，现金节约）
    - tax_impact = (current - target) × 税率（正数，所得税增加）
    - tax_saving = 0（无加计扣除）
    - 净影响 = cost_saving - tax_impact = 压降金额 × (1 - 税率)
    """
    target_a = revenue * 0.01  # 压到 1%（金额，元）
    target_b = revenue * 0.02  # 压到 2%（金额，元）
    cit_rate = 0.25
    cs_a = fin.cost_saving_estimate(current, target_a)
    ti_a = fin.tax_impact_estimate(current, target_a, cit_rate)
    cs_b = fin.cost_saving_estimate(current, target_b)
    ti_b = fin.tax_impact_estimate(current, target_b, cit_rate)
    return [
        Option(
            label="A",
            name="大幅压降咨询服务费并内化能力",
            description=f"将咨询费降至营收 1% 即 {_fmt_money(target_a)}，建立内部能力。",
            target_value=target_a,
            tax_rate=cit_rate,
            cost_saving=cs_a,
            tax_saving=0.0,
            tax_impact=ti_a,
            feasibility="中",
            risk_level=RISK_LOW,
            action_note="审查现有咨询合同，剔除非必要项；保留咨询服务须有交付物。",
        ),
        Option(
            label="B",
            name="适度压降并强化合同交付物审查",
            description=f"降至营收 2% 即 {_fmt_money(target_b)}，保留必要咨询。",
            target_value=target_b,
            tax_rate=cit_rate,
            cost_saving=cs_b,
            tax_saving=0.0,
            tax_impact=ti_b,
            feasibility="高",
            risk_level=RISK_LOW,
            action_note="每笔咨询须有合同、交付报告、验收记录，确保四流合一。",
        ),
        Option(
            label="C",
            name="暂维持现状，但补充合同与交付证据",
            description=f"维持 {_fmt_money(current)}，重点完善证据链。",
            target_value=current,
            tax_rate=0.0,
            cost_saving=0.0,
            tax_saving=0.0,
            tax_impact=0.0,
            feasibility="高",
            risk_level=RISK_HIGH,
            action_note="风险提示：咨询费偏高为稽查高发区，证据不足将被纳税调整。",
        ),
    ]


def _build_vat_low_options(revenue: float, current_vat_rate: float, industry_min: float) -> List[Option]:
    """增值税税负率偏低 → A/B/C（价外税，不直接节税）。

    业务口径：增值税为价外税，不直接减少所得税；
    cost_saving=0 / tax_saving=0 / tax_impact=0 / est_saving=0。
    提升税负率可降低稽查风险，但本期不量化为节税。
    """
    target_a = (industry_min + 0.5)  # 行业经验中枢 +0.5pp（百分比）
    target_b = industry_min  # 行业经验中枢（百分比，预算合规红线）
    return [
        Option(
            label="A",
            name="提升进项税管理至行业中枢+0.5pp",
            description=(
                f"加强进项税抵扣管理，将估算税负率从 {_fmt_pct(current_vat_rate)} "
                f"提升至 {_fmt_pct(target_a)}。"
            ),
            target_value=target_a,
            tax_rate=0.0,
            cost_saving=0.0,
            tax_saving=0.0,
            tax_impact=0.0,
            feasibility="中",
            risk_level=RISK_LOW,
            action_note=(
                "梳理进项税抵扣链条、规范供应商资格、强化票据时效管理。"
                "注：增值税税负率为估算值（基于税金及附加反推），实际以申报为准。"
            ),
        ),
        Option(
            label="B",
            name="规范进项税抵扣至行业经验中枢",
            description=f"将估算税负率提升至行业经验中枢 {_fmt_pct(target_b)}。",
            target_value=target_b,
            tax_rate=0.0,
            cost_saving=0.0,
            tax_saving=0.0,
            tax_impact=0.0,
            feasibility="高",
            risk_level=RISK_LOW,
            action_note="重点排查异常进项转出、滞留票、虚开风险供应商。",
        ),
        Option(
            label="C",
            name="暂维持现状，准备税负率偏低说明材料",
            description=f"维持 {_fmt_pct(current_vat_rate)}，准备说明材料应对税务问询。",
            target_value=current_vat_rate,
            tax_rate=0.0,
            cost_saving=0.0,
            tax_saving=0.0,
            tax_impact=0.0,
            feasibility="高",
            risk_level=RISK_HIGH,
            action_note="风险提示：增值税税负率持续偏低可能触发税务风险预警。",
        ),
    ]


def _build_benchmark_options(
    metric_name: str,
    current: float,
    median: float,
    is_low_risk: bool,
) -> List[Option]:
    """贴近行业基准的 A/B/C 通用选项（所得税/毛利率/净利率偏高或偏低）。"""
    target_a = median
    target_b = (current + median) / 2  # 分阶段过渡目标
    direction = "提升" if is_low_risk else "回落"
    return [
        Option(
            label="A",
            name=f"{direction}{metric_name}至行业经验中枢",
            description=(
                f"将{metric_name}从 {_fmt_pct(current)} "
                f"调整至行业经验中枢 {_fmt_pct(target_a)}。"
            ),
            target_value=target_a,
            tax_rate=0.0,
            cost_saving=0.0,
            tax_saving=0.0,
            tax_impact=0.0,
            feasibility="中",
            risk_level=RISK_LOW,
            action_note="按行业经验中枢设定经营或申报口径目标，执行前与主管税务机关/注册税务师确认。",
        ),
        Option(
            label="B",
            name=f"分阶段{direction}（目标 {_fmt_pct(target_b)}）",
            description=f"分阶段向行业经验中枢靠拢，过渡目标 {_fmt_pct(target_b)}。",
            target_value=target_b,
            tax_rate=0.0,
            cost_saving=0.0,
            tax_saving=0.0,
            tax_impact=0.0,
            feasibility="高",
            risk_level=RISK_LOW,
            action_note="每季度复盘一次，连续两季度偏离预算带超过 20% 时触发经营复盘。",
        ),
        Option(
            label="C",
            name="维持现状并准备说明材料",
            description=f"维持 {_fmt_pct(current)}，准备行业偏离的合理性说明材料。",
            target_value=current,
            tax_rate=0.0,
            cost_saving=0.0,
            tax_saving=0.0,
            tax_impact=0.0,
            feasibility="高",
            risk_level=RISK_HIGH,
            action_note="风险提示：持续偏离行业基准可能触发税务风险预警或经营质量问询。",
        ),
    ]


def _inc(data: FinancialData, account: str, year: int) -> float:
    return (data.income_statement.get(account, {}) or {}).get(year, 0.0) or 0.0


def _bal(data: FinancialData, account: str, year: int) -> float:
    return (data.balance_sheet.get(account, {}) or {}).get(year, 0.0) or 0.0


def _led(data: FinancialData, account: str, year: int) -> float:
    return (data.account_balances.get(account) or {}).get(year, 0.0) or 0.0


def _build_amount_cut_options(
    name: str,
    current: float,
    target_a: float,
    target_b: float,
    action_a: str,
    action_b: str,
) -> List[Option]:
    """通用金额压降类 A/B/C（管理类发现，不量化所得税）。"""
    return [
        Option(
            label="A",
            name=f"压降至 {_fmt_money(target_a)}（积极）",
            description=f"将{name}从 {_fmt_money(current)} 压降至 {_fmt_money(target_a)}。",
            target_value=target_a,
            cost_saving=fin.cost_saving_estimate(current, target_a),
            feasibility="中",
            risk_level=RISK_LOW,
            action_note=action_a,
        ),
        Option(
            label="B",
            name=f"分阶段压降至 {_fmt_money(target_b)}",
            description=f"先压降至 {_fmt_money(target_b)}，再视经营情况继续优化。",
            target_value=target_b,
            cost_saving=fin.cost_saving_estimate(current, target_b),
            feasibility="高",
            risk_level=RISK_LOW,
            action_note=action_b,
        ),
        Option(
            label="C",
            name="暂维持现状并加强台账",
            description=f"维持 {_fmt_money(current)}，完善审批与台账，下期再优化。",
            target_value=current,
            feasibility="高",
            risk_level=RISK_MEDIUM,
            action_note="风险提示：不压降则费用/占用持续占用利润与现金，需保留业务合理性说明。",
        ),
    ]


def _build_amount_raise_options(
    name: str,
    current: float,
    target_a: float,
    target_b: float,
    action_a: str,
    action_b: str,
    tax_rate: float = 0.25,
    deduction_rate: float = 1.0,
) -> List[Option]:
    """通用金额上调类 A/B/C（限额内据实扣除/研发等）。"""
    return [
        Option(
            label="A",
            name=f"提升至 {_fmt_money(target_a)}",
            description=f"将{name}从 {_fmt_money(current)} 提升至 {_fmt_money(target_a)}。",
            target_value=target_a,
            tax_rate=tax_rate,
            tax_saving=fin.tax_saving_estimate(current, target_a, tax_rate, deduction_rate),
            feasibility="中",
            risk_level=RISK_LOW,
            action_note=action_a,
            deduction_rate=deduction_rate,
        ),
        Option(
            label="B",
            name=f"分阶段提升至 {_fmt_money(target_b)}",
            description=f"先提升至 {_fmt_money(target_b)}，完善制度后再加码。",
            target_value=target_b,
            tax_rate=tax_rate,
            tax_saving=fin.tax_saving_estimate(current, target_b, tax_rate, deduction_rate),
            feasibility="高",
            risk_level=RISK_LOW,
            action_note=action_b,
            deduction_rate=deduction_rate,
        ),
        Option(
            label="C",
            name="暂维持现状",
            description=f"维持 {_fmt_money(current)}，仅完善凭证与台账备查。",
            target_value=current,
            feasibility="高",
            risk_level=RISK_MEDIUM,
            action_note="风险提示：未用足限额或投入不足，可能放弃合法节税空间。",
        ),
    ]


def _check_rd_missing(data: FinancialData, benchmark: Dict) -> Optional[Finding]:
    """研发费用缺失（该有没的）。"""
    latest = data.latest_year()
    if latest is None:
        return None
    rd = _inc(data, "研发费用", latest)
    revenue = _inc(data, "营业收入", latest)
    if revenue == 0:
        return None
    rd_ratio = rd / revenue * 100.0
    rd_min = benchmark["rd_expense_ratio"]["min"]
    if rd_ratio < rd_min and rd == 0:
        return Finding(
            id="RD_MISSING",
            title="研发费用缺失（该有没的）",
            category=CATEGORY_STRUCTURE,
            severity=SEVERITY_HIGH,
            fact=(
                f"{latest} 年研发费用为 0，研发费用率 {rd_ratio:.2f}%，"
                f"营业收入 {_fmt_money(revenue)}。"
            ),
            benchmark=(
                f"行业（{data.industry}）研发费用率合理区间 "
                f"{rd_min:.1f}%-{benchmark['rd_expense_ratio']['max']:.1f}%。"
            ),
            suggestion=(
                "建议设立研发辅助账、按研发项目归集费用，符合条件可享受研发费用加计扣除，"
                "同时为申请高新技术企业资格做准备。"
            ),
            options=_build_rd_options(revenue),
            current_value=rd_ratio,
            target_value=benchmark["rd_expense_ratio"]["median"],
            unit="%",
        )
    return None


def _check_rd_low(data: FinancialData, benchmark: Dict) -> Optional[Finding]:
    """研发费用率低于行业下限（有投入但不足）。与 RD_MISSING 互斥。"""
    latest = data.latest_year()
    if latest is None:
        return None
    rd = _inc(data, "研发费用", latest)
    revenue = _inc(data, "营业收入", latest)
    if revenue == 0 or rd <= 0:
        return None
    rd_ratio = rd / revenue * 100.0
    item = benchmark["rd_expense_ratio"]
    if rd_ratio >= item["min"]:
        return None
    return Finding(
        id="RD_LOW",
        title="研发费用率偏低（投入不足）",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_MEDIUM,
        fact=(
            f"{latest} 年研发费用 {_fmt_money(rd)}，研发费用率 {rd_ratio:.2f}%，"
            f"低于行业下限 {item['min']:.1f}%。"
        ),
        benchmark=(
            f"行业（{data.industry}）研发费用率合理区间 "
            f"{item['min']:.1f}%-{item['max']:.1f}%，中枢 {item['median']:.1f}%。"
        ),
        suggestion=(
            "建议补齐研发项目立项与辅助账，将可归集费用完整入账，"
            "符合条件申报研发费用加计扣除。"
        ),
        options=_build_rd_options(revenue),
        current_value=rd_ratio,
        target_value=item["median"],
        unit="%",
    )


def _check_entertainment_excess(data: FinancialData) -> Optional[Finding]:
    """业务招待费超限（可有可没有/应控）。

    判定：发生额/营收 > 0.5%/60% ≈ 0.833% 时，扣除限额被营收 0.5% 封顶，
    超过部分不得税前扣除，视为超限。
    """
    latest = data.latest_year()
    if latest is None:
        return None
    ent = (data.account_balances.get("业务招待费") or {}).get(latest, 0.0) or 0.0
    revenue = (data.income_statement.get("营业收入", {}) or {}).get(latest, 0.0) or 0.0
    if revenue == 0 or ent == 0:
        return None
    ratio = ent / revenue * 100.0
    # 扣除限额：发生额 60% 与营收 0.5% 孰低
    deduct_limit = min(ent * ENTERTAIN_DEDUCT_RATIO, revenue * ENTERTAIN_REVENUE_RATIO)
    nondeductible = ent - deduct_limit  # 不得税前扣除部分
    # 超限临界点：发生额/营收 > 0.5%/60% ≈ 0.833%
    excess_threshold = ENTERTAIN_REVENUE_RATIO / ENTERTAIN_DEDUCT_RATIO * 100.0
    if ratio > excess_threshold:
        return Finding(
            id="ENTERTAIN_EXCESS",
            title="业务招待费超限（可有可没有/应控）",
            category=CATEGORY_STRUCTURE,
            severity=SEVERITY_MEDIUM,
            fact=(
                f"{latest} 年业务招待费 {_fmt_money(ent)}，占营收 {ratio:.2f}%；"
                f"按孰低原则可税前扣除 {_fmt_money(deduct_limit)}，"
                f"不得扣除 {_fmt_money(nondeductible)}。"
            ),
            benchmark=(
                f"税法规定：业务招待费按发生额 60% 与营收 0.5% 孰低扣除。"
                f"超限临界点为营收的 {excess_threshold:.3f}%。"
            ),
            suggestion=(
                "建议压缩招待费至限额内，强化业务真实性留痕，"
                "避免超过限额部分被纳税调整。"
            ),
            options=_build_entertainment_options(revenue, ent),
            current_value=ent,
            target_value=revenue * ENTERTAIN_REVENUE_RATIO / ENTERTAIN_DEDUCT_RATIO,
            unit="元",
        )
    return None


def _check_consulting_high(data: FinancialData) -> Optional[Finding]:
    """咨询服务费偏高（真实性风险）。"""
    latest = data.latest_year()
    if latest is None:
        return None
    consult = (data.account_balances.get("咨询服务费") or {}).get(latest, 0.0) or 0.0
    revenue = (data.income_statement.get("营业收入", {}) or {}).get(latest, 0.0) or 0.0
    if revenue == 0 or consult == 0:
        return None
    ratio = consult / revenue * 100.0
    if ratio > CONSULTING_WARNING_RATIO * 100:
        return Finding(
            id="CONSULTING_HIGH",
            title="咨询服务费偏高（真实性风险）",
            category=CATEGORY_REALITY,
            severity=SEVERITY_HIGH,
            fact=(
                f"{latest} 年咨询服务费 {_fmt_money(consult)}，占营收 {ratio:.2f}%，"
                f"超过预警阈值 {CONSULTING_WARNING_RATIO*100:.1f}%。"
            ),
            benchmark=(
                "咨询费为四流合一稽查高发区，需确保合同、资金、发票、服务交付物一致。"
            ),
            suggestion=(
                "建议审查咨询合同、保留交付报告与验收记录，"
                "剔除非必要咨询，防范被纳税调整风险。"
            ),
            options=_build_consulting_options(revenue, consult),
            current_value=consult,
            target_value=revenue * 0.02,
            unit="元",
        )
    return None


def _check_vat_low(data: FinancialData, benchmark: Dict) -> Optional[Finding]:
    """增值税税负率偏低（真实性风险）：低于中枢×0.7 一级预警、×0.5 二级预警。"""
    latest = data.latest_year()
    if latest is None:
        return None
    ind_dict = fin.compute_year_indicators(data, latest)
    vat = ind_dict["增值税税负率"]["value"]
    note = ind_dict["增值税税负率"]["note"]
    revenue = ind_dict.get("营业收入", 0.0) or 0.0
    if revenue == 0:
        return None
    item = benchmark["vat_tax_rate"]
    med = item["median"]
    low_warn = item.get("low_warn") or med * 0.7
    if vat >= low_warn:
        return None
    level = "二级" if vat < med * 0.5 else "一级"
    factor = "0.5" if level == "二级" else "0.7"
    return Finding(
        id="VAT_LOW",
        title="增值税税负率偏低（真实性风险）",
        category=CATEGORY_REALITY,
        severity=SEVERITY_HIGH,
        fact=(
            f"{latest} 年增值税税负率 {vat:.2f}%（{note}），"
            f"低于行业经验中枢 {med:.2f}% 的 {factor} 倍（预警偏低线 {low_warn:.2f}%），"
            f"触发{level}预警。"
        ),
        benchmark=(
            f"行业（{data.industry}）增值税税负率经验中枢 {med:.2f}%，"
            f"参考区间 {item['min']:.1f}%-{item['max']:.1f}%，预警偏低线 {low_warn:.2f}%。"
            "注：本口径为估算值（基于税金及附加反推），实际以申报数据为准。"
        ),
        suggestion=(
            "建议梳理进项税抵扣链条、规范供应商资格、强化票据时效管理；"
            + (
                "该偏离已超过 50%，须重点准备发票链、能耗产出比等说明材料。"
                if level == "二级"
                else "并准备税负率偏低说明材料应对税务问询。"
            )
        ),
        options=_build_vat_low_options(revenue, vat, med),
        current_value=vat,
        target_value=med,
        unit="%",
    )


def _check_vat_high(data: FinancialData, benchmark: Dict) -> Optional[Finding]:
    """增值税税负率偏高（> 中枢×2.5）：偏高提示，核查进项管理。"""
    latest = data.latest_year()
    if latest is None:
        return None
    ind_dict = fin.compute_year_indicators(data, latest)
    vat = ind_dict["增值税税负率"]["value"]
    note = ind_dict["增值税税负率"]["note"]
    revenue = ind_dict.get("营业收入", 0.0) or 0.0
    if revenue == 0:
        return None
    item = benchmark["vat_tax_rate"]
    med = item["median"]
    high_warn = item.get("high_warn") or med * 2.5
    if vat <= high_warn:
        return None
    return Finding(
        id="VAT_HIGH",
        title="增值税税负率偏高",
        category=CATEGORY_TAX,
        severity=SEVERITY_LOW,
        fact=(
            f"{latest} 年增值税税负率 {vat:.2f}%（{note}），"
            f"高于行业经验中枢 {med:.2f}% 的 2.5 倍（预警偏高线 {high_warn:.2f}%）。"
        ),
        benchmark=(
            f"行业（{data.industry}）增值税税负率经验中枢 {med:.2f}%，"
            f"预警偏高线 {high_warn:.2f}%。"
        ),
        suggestion=(
            "建议核查进项税抵扣管理是否异常、是否错用税率或适用行业特殊政策，"
            "并准备业务合理性说明材料。"
        ),
        options=_build_benchmark_options("增值税税负率", vat, med, is_low_risk=False),
        current_value=vat,
        target_value=med,
        unit="%",
    )


def _check_income_tax_low(data: FinancialData, benchmark: Dict) -> Optional[Finding]:
    """盈利企业企业所得税税负率偏低（< 中枢×0.6）。"""
    latest = data.latest_year()
    if latest is None:
        return None
    ind_dict = fin.compute_year_indicators(data, latest)
    itr = ind_dict["所得税税负率"]["value"]
    revenue = ind_dict.get("营业收入", 0.0) or 0.0
    net_profit = (data.income_statement.get("净利润") or {}).get(latest, 0.0) or 0.0
    if revenue == 0 or net_profit <= 0:
        return None  # 仅盈利企业对标有效（亏损/小微优惠会失真）
    item = benchmark["income_tax_rate"]
    med = item["median"]
    low_warn = item.get("low_warn") or med * 0.6
    if itr >= low_warn:
        return None
    return Finding(
        id="INCOME_TAX_LOW",
        title="企业所得税税负率偏低（盈利企业）",
        category=CATEGORY_TAX,
        severity=SEVERITY_MEDIUM,
        fact=(
            f"{latest} 年盈利但企业所得税税负率 {itr:.2f}%，"
            f"低于行业经验中枢 {med:.2f}% 的 0.6 倍（预警偏低线 {low_warn:.2f}%）。"
        ),
        benchmark=(
            f"行业（{data.industry}）企业所得税税负率经验中枢 {med:.2f}%，"
            f"参考区间 {item['min']:.1f}%-{item['max']:.1f}%。"
            "小微企业税收优惠会使该指标失真，需结合利润规模判定。"
        ),
        suggestion=(
            "建议核查成本费用列支真实性、是否存在应确认未确认收入；"
            "若适用小微优惠或弥补亏损，需留存政策适用依据。"
        ),
        options=_build_benchmark_options("企业所得税税负率", itr, med, is_low_risk=True),
        current_value=itr,
        target_value=med,
        unit="%",
    )


def _check_income_tax_high(data: FinancialData, benchmark: Dict) -> Optional[Finding]:
    """企业所得税税负率偏高（> 中枢×2.0）：多为高利润，核查优惠适用。"""
    latest = data.latest_year()
    if latest is None:
        return None
    ind_dict = fin.compute_year_indicators(data, latest)
    itr = ind_dict["所得税税负率"]["value"]
    revenue = ind_dict.get("营业收入", 0.0) or 0.0
    if revenue == 0:
        return None
    item = benchmark["income_tax_rate"]
    med = item["median"]
    high_warn = item.get("high_warn") or med * 2.0
    if itr <= high_warn:
        return None
    return Finding(
        id="INCOME_TAX_HIGH",
        title="企业所得税税负率偏高",
        category=CATEGORY_TAX,
        severity=SEVERITY_LOW,
        fact=(
            f"{latest} 年企业所得税税负率 {itr:.2f}%，"
            f"高于行业经验中枢 {med:.2f}% 的 2.0 倍（预警偏高线 {high_warn:.2f}%）。"
        ),
        benchmark=(
            f"行业（{data.industry}）企业所得税税负率经验中枢 {med:.2f}%，"
            f"预警偏高线 {high_warn:.2f}%。"
        ),
        suggestion=(
            "多为高利润行业正常表现；核查税收优惠适用是否合规、"
            "是否存在应享未享优惠，并准备合理性说明。"
        ),
        options=_build_benchmark_options("企业所得税税负率", itr, med, is_low_risk=False),
        current_value=itr,
        target_value=med,
        unit="%",
    )


def _check_gross_margin_low(data: FinancialData, benchmark: Dict) -> Optional[Finding]:
    """毛利率低于参考区间下限×0.7（或 P10）。"""
    latest = data.latest_year()
    if latest is None:
        return None
    ind_dict = fin.compute_year_indicators(data, latest)
    gm = ind_dict["毛利率"]["value"]
    item = benchmark["gross_margin"]
    threshold = item.get("low_warn") or item["min"] * 0.7
    if gm >= threshold:
        return None
    return Finding(
        id="GROSS_MARGIN_LOW",
        title="毛利率偏低",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_MEDIUM,
        fact=(
            f"{latest} 年毛利率 {gm:.2f}%，低于行业参考区间下限 "
            f"{item['min']:.1f}% 的 0.7 倍（预警偏低线 {threshold:.1f}%）。"
        ),
        benchmark=(
            f"行业（{data.industry}）毛利率参考区间 "
            f"{item['min']:.1f}%-{item['max']:.1f}%，预警偏低线 {threshold:.1f}%。"
        ),
        suggestion=(
            "建议核查成本归集是否错误、收入是否漏记、定价是否失控，"
            "以及是否存在行业周期下行影响。"
        ),
        options=_build_benchmark_options("毛利率", gm, item["median"], is_low_risk=True),
        current_value=gm,
        target_value=item["median"],
        unit="%",
    )


def _check_gross_margin_high(data: FinancialData, benchmark: Dict) -> Optional[Finding]:
    """毛利率高于参考区间上限×1.5（或 P90）。"""
    latest = data.latest_year()
    if latest is None:
        return None
    ind_dict = fin.compute_year_indicators(data, latest)
    gm = ind_dict["毛利率"]["value"]
    item = benchmark["gross_margin"]
    threshold = item.get("high_warn") or item["max"] * 1.5
    if gm <= threshold:
        return None
    return Finding(
        id="GROSS_MARGIN_HIGH",
        title="毛利率偏高",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_LOW,
        fact=(
            f"{latest} 年毛利率 {gm:.2f}%，高于行业参考区间上限 "
            f"{item['max']:.1f}% 的 1.5 倍（预警偏高线 {threshold:.1f}%）。"
        ),
        benchmark=(
            f"行业（{data.industry}）毛利率参考区间 "
            f"{item['min']:.1f}%-{item['max']:.1f}%，预警偏高线 {threshold:.1f}%。"
        ),
        suggestion=(
            "多为特殊业务结构或高壁垒行业正常表现；核查收入确认口径、"
            "成本核算完整性，并准备业务合理性说明。"
        ),
        options=_build_benchmark_options("毛利率", gm, item["median"], is_low_risk=False),
        current_value=gm,
        target_value=item["median"],
        unit="%",
    )


def _check_net_margin_low(data: FinancialData, benchmark: Dict) -> Optional[Finding]:
    """净利率低于参考区间下限×0.7（或 P10）。"""
    latest = data.latest_year()
    if latest is None:
        return None
    ind_dict = fin.compute_year_indicators(data, latest)
    nm = ind_dict["净利率"]["value"]
    item = benchmark["net_margin"]
    threshold = item.get("low_warn") or item["min"] * 0.7
    if nm >= threshold:
        return None
    return Finding(
        id="NET_MARGIN_LOW",
        title="净利率偏低",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_MEDIUM,
        fact=(
            f"{latest} 年净利率 {nm:.2f}%，低于行业参考区间下限 "
            f"{item['min']:.1f}% 的 0.7 倍（预警偏低线 {threshold:.1f}%）。"
        ),
        benchmark=(
            f"行业（{data.industry}）净利率参考区间 "
            f"{item['min']:.1f}%-{item['max']:.1f}%，预警偏低线 {threshold:.1f}%。"
        ),
        suggestion=(
            "建议核查期间费用是否失控、资产减值计提是否不足、经营效率是否低下，"
            "并结合费用率（销售/管理/财务）逐项归因。"
        ),
        options=_build_benchmark_options("净利率", nm, item["median"], is_low_risk=True),
        current_value=nm,
        target_value=item["median"],
        unit="%",
    )


def _check_net_margin_high(data: FinancialData, benchmark: Dict) -> Optional[Finding]:
    """净利率高于参考区间上限×1.5。"""
    latest = data.latest_year()
    if latest is None:
        return None
    ind_dict = fin.compute_year_indicators(data, latest)
    nm = ind_dict["净利率"]["value"]
    item = benchmark["net_margin"]
    threshold = item.get("high_warn") or item["max"] * 1.5
    if nm <= threshold:
        return None
    return Finding(
        id="NET_MARGIN_HIGH",
        title="净利率偏高",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_LOW,
        fact=(
            f"{latest} 年净利率 {nm:.2f}%，高于行业参考区间上限 "
            f"{item['max']:.1f}% 的 1.5 倍（预警偏高线 {threshold:.1f}%）。"
        ),
        benchmark=(
            f"行业（{data.industry}）净利率参考区间 "
            f"{item['min']:.1f}%-{item['max']:.1f}%，预警偏高线 {threshold:.1f}%。"
        ),
        suggestion=(
            "多为轻资产/高壁垒良性表现；核查非经常性损益影响与利润质量，"
            "并准备业务合理性说明。"
        ),
        options=_build_benchmark_options("净利率", nm, item["median"], is_low_risk=False),
        current_value=nm,
        target_value=item["median"],
        unit="%",
    )


def _check_gross_margin_soft_low(data: FinancialData, benchmark: Dict) -> Optional[Finding]:
    """毛利率低于中枢×0.92 但未触达偏低预警线 → 关注级。"""
    latest = data.latest_year()
    if latest is None:
        return None
    ind_dict = fin.compute_year_indicators(data, latest)
    gm = ind_dict["毛利率"]["value"]
    item = benchmark["gross_margin"]
    hard = item.get("low_warn") or item["min"] * 0.7
    soft = item["median"] * SOFT_MARGIN_FACTOR
    if gm < hard or gm >= soft:
        return None
    return Finding(
        id="GROSS_MARGIN_SOFT_LOW",
        title="毛利率低于行业中枢（关注）",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_LOW,
        fact=(
            f"{latest} 年毛利率 {gm:.2f}%，低于行业经验中枢 "
            f"{item['median']:.1f}% 的 {SOFT_MARGIN_FACTOR*100:.0f}% 线（{soft:.1f}%），"
            f"尚未触发偏低预警线 {hard:.1f}%。"
        ),
        benchmark=(
            f"行业（{data.industry}）毛利率参考区间 "
            f"{item['min']:.1f}%-{item['max']:.1f}%，中枢 {item['median']:.1f}%。"
        ),
        suggestion=(
            "建议逐项目复核报价与成本归集，保住可赚钱订单；"
            "材料/人工涨价要同步调价或优化工艺。"
        ),
        options=_build_benchmark_options("毛利率", gm, item["median"], is_low_risk=True),
        current_value=gm,
        target_value=item["median"],
        unit="%",
    )


def _check_expense_ratio_high(
    data: FinancialData,
    benchmark: Dict,
    *,
    ratio_key: str,
    income_account: str,
    finding_id: str,
    title: str,
) -> Optional[Finding]:
    """期间费用率高于行业上限。"""
    latest = data.latest_year()
    if latest is None:
        return None
    ind_dict = fin.compute_year_indicators(data, latest)
    ratio = ind_dict[ratio_key]["value"]
    key_map = {
        "销售费用率": "selling_expense_ratio",
        "管理费用率": "admin_expense_ratio",
        "财务费用率": "financial_expense_ratio",
    }
    item = benchmark[key_map[ratio_key]]
    if ratio <= item["max"]:
        return None
    revenue = _inc(data, "营业收入", latest)
    current_amt = _inc(data, income_account, latest)
    target_a = revenue * item["median"] / 100.0
    target_b = revenue * (item["median"] + item["max"]) / 200.0
    return Finding(
        id=finding_id,
        title=title,
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_MEDIUM,
        fact=(
            f"{latest} 年{ratio_key} {ratio:.2f}%（金额 {_fmt_money(current_amt)}），"
            f"高于行业上限 {item['max']:.1f}%。"
        ),
        benchmark=(
            f"行业（{data.industry}）{ratio_key}合理区间 "
            f"{item['min']:.1f}%-{item['max']:.1f}%，中枢 {item['median']:.1f}%。"
        ),
        suggestion=(
            f"建议拆分{income_account}明细、压缩非必要支出，"
            f"设置月度预算并超预算审批。"
        ),
        options=_build_amount_cut_options(
            income_account,
            current_amt,
            target_a,
            target_b,
            f"按科目建立{income_account}预算，责任到部门，月度复盘。",
            f"先压降占比最高的 2-3 个子项，再滚动优化。",
        ),
        current_value=ratio,
        target_value=item["median"],
        unit="%",
    )


def _check_welfare_underused(data: FinancialData) -> Optional[Finding]:
    """福利费相对营收过低（该有没的/未用足信号，无工资总额时用营收近似）。"""
    latest = data.latest_year()
    if latest is None:
        return None
    # 仅当科目余额表出现过该科目时判定，避免无台账数据误报
    if "福利费" not in (data.account_balances or {}):
        return None
    revenue = _inc(data, "营业收入", latest)
    if revenue <= 0:
        return None
    welfare = _led(data, "福利费", latest)
    admin_ratio = fin.compute_year_indicators(data, latest)["管理费用率"]["value"]
    ratio = welfare / revenue
    # 有管理费用体量但福利费几乎为 0 / 极低
    if admin_ratio < 3.0 and welfare > 0:
        return None
    if ratio >= WELFARE_SOFT_MIN_RATIO and welfare > 0:
        return None
    target_a = revenue * 0.008
    target_b = revenue * 0.005
    return Finding(
        id="WELFARE_UNDERUSED",
        title="职工福利费偏低（限额未用足信号）",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_LOW,
        fact=(
            f"{latest} 年福利费 {_fmt_money(welfare)}，占营收 {ratio*100:.2f}%，"
            f"显著偏低（管理费用率 {admin_ratio:.2f}%）。"
        ),
        benchmark=(
            "税法：职工福利费在工资薪金总额 14% 限额内据实扣除。"
            "无工资总额时以营收占比作管理信号，实际扣除须按工资基数核验。"
        ),
        suggestion=(
            "建议盘点员工福利真实支出是否完整入账，"
            "在限额内据实列支合法福利，完善发放与签收凭证。"
        ),
        options=_build_amount_raise_options(
            "福利费",
            welfare,
            target_a,
            target_b,
            "建立福利费预算与台账，按工资薪金 14% 限额核验可扣空间。",
            "先覆盖餐补/体检/节日福利等高频项目，再扩展。",
        ),
        current_value=welfare,
        target_value=target_a,
        unit="元",
    )


def _check_edu_underused(data: FinancialData) -> Optional[Finding]:
    """职工教育经费偏低（未用足信号）。"""
    latest = data.latest_year()
    if latest is None:
        return None
    if "教育经费" not in (data.account_balances or {}):
        return None
    revenue = _inc(data, "营业收入", latest)
    if revenue <= 0:
        return None
    edu = _led(data, "教育经费", latest)
    ratio = edu / revenue
    if ratio >= EDU_SOFT_MIN_RATIO and edu > 0:
        return None
    target_a = revenue * 0.004
    target_b = revenue * 0.002
    return Finding(
        id="EDU_UNDERUSED",
        title="职工教育经费偏低（限额未用足信号）",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_LOW,
        fact=(
            f"{latest} 年教育经费 {_fmt_money(edu)}，占营收 {ratio*100:.2f}%，"
            f"投入明显不足。"
        ),
        benchmark=(
            "税法：职工教育经费在工资薪金总额 8% 以内据实扣除，超过部分可结转。"
            "无工资总额时以营收占比作管理信号。"
        ),
        suggestion=(
            "建议制定年度培训计划，将内训/外训/技能鉴定等合规支出完整归集，"
            "在限额内据实扣除。"
        ),
        options=_build_amount_raise_options(
            "教育经费",
            edu,
            target_a,
            target_b,
            "按岗位制定培训清单，保留签到/课件/发票，按工资 8% 核验限额。",
            "优先关键岗位与安全/质量培训，再扩展管理培训。",
        ),
        current_value=edu,
        target_value=target_a,
        unit="元",
    )


def _check_ad_expense_high(data: FinancialData) -> Optional[Finding]:
    """广告宣传费超过营收 15% 一般扣除上限。"""
    latest = data.latest_year()
    if latest is None:
        return None
    if "广告宣传费" not in (data.account_balances or {}):
        return None
    revenue = _inc(data, "营业收入", latest)
    ad = _led(data, "广告宣传费", latest)
    if revenue <= 0 or ad <= 0:
        return None
    ratio = ad / revenue
    if ratio <= AD_EXPENSE_TAX_CAP:
        return None
    cap = revenue * AD_EXPENSE_TAX_CAP
    excess = ad - cap
    target_b = (ad + cap) / 2
    return Finding(
        id="AD_EXPENSE_HIGH",
        title="广告宣传费超一般扣除上限",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_MEDIUM,
        fact=(
            f"{latest} 年广告宣传费 {_fmt_money(ad)}，占营收 {ratio*100:.2f}%，"
            f"超过一般企业 15% 扣除上限，超限约 {_fmt_money(excess)}。"
        ),
        benchmark="企业所得税法：一般企业广告费和业务宣传费不超过当年销售（营业）收入 15%。",
        suggestion=(
            "建议压缩非效果投放，区分可递延/可资本化支出，"
            "超限部分按规定结转以后年度扣除并保留投放效果证据。"
        ),
        options=_build_amount_cut_options(
            "广告宣传费",
            ad,
            cap,
            target_b,
            "按渠道评估 ROI，停掉无效投放，控制在营收 15% 内。",
            "先砍掉最低效 30% 渠道，季度再评估。",
        ),
        current_value=ad,
        target_value=cap,
        unit="元",
    )


def _check_ar_high(data: FinancialData) -> Optional[Finding]:
    """应收账款/营收偏高（回款压力）。"""
    latest = data.latest_year()
    if latest is None:
        return None
    revenue = _inc(data, "营业收入", latest)
    ar = _bal(data, "应收账款", latest)
    if revenue <= 0 or ar <= 0:
        return None
    ratio = ar / revenue
    if ratio < AR_REVENUE_WARN:
        return None
    target_a = revenue * 0.30
    target_b = revenue * 0.35
    return Finding(
        id="AR_HIGH",
        title="应收账款占收入偏高（回款压力）",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_HIGH if ratio >= 0.60 else SEVERITY_MEDIUM,
        fact=(
            f"{latest} 年应收账款 {_fmt_money(ar)}，占营业收入 {ratio*100:.2f}%，"
            f"客户欠款占用资金明显。"
        ),
        benchmark=(
            f"管理经验：应收账款/营收 ≥ {AR_REVENUE_WARN*100:.0f}% 需重点抓回款；"
            f"≥60% 视为高压力。"
        ),
        suggestion=(
            "建议按客户/项目/账龄列清单，明确责任人与回款节点；"
            "新项目优先谈预付款与进度款，控制长期垫资。"
        ),
        options=_build_amount_cut_options(
            "应收账款",
            ar,
            target_a,
            target_b,
            "每周更新逾期清单，大客户专人跟进，必要时停工/止损。",
            "先清理账龄 90 天以上款项，再优化合同付款条款。",
        ),
        current_value=ratio * 100.0,
        target_value=30.0,
        unit="%",
    )


def _check_debt_high(data: FinancialData) -> Optional[Finding]:
    """资产负债率偏高。"""
    latest = data.latest_year()
    if latest is None:
        return None
    assets = _bal(data, "资产总额", latest)
    liabilities = _bal(data, "负债总额", latest)
    if assets <= 0 or liabilities <= 0:
        return None
    ratio = liabilities / assets
    if ratio < DEBT_RATIO_WARN:
        return None
    return Finding(
        id="DEBT_HIGH",
        title="资产负债率偏高",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_MEDIUM if ratio < 0.80 else SEVERITY_HIGH,
        fact=(
            f"{latest} 年资产负债率 {ratio*100:.2f}%"
            f"（负债 {_fmt_money(liabilities)} / 资产 {_fmt_money(assets)}）。"
        ),
        benchmark=f"管理经验：资产负债率 ≥ {DEBT_RATIO_WARN*100:.0f}% 需关注偿债与再融资空间。",
        suggestion=(
            "建议梳理短长期债务期限、压降高息负债，"
            "同步加速回款与存货周转，避免流动性踩踏。"
        ),
        options=_build_benchmark_options(
            "资产负债率", ratio * 100.0, DEBT_RATIO_WARN * 100.0 * 0.85, is_low_risk=False
        ),
        current_value=ratio * 100.0,
        target_value=DEBT_RATIO_WARN * 100.0 * 0.85,
        unit="%",
    )


def _check_revenue_decline(data: FinancialData) -> Optional[Finding]:
    """最近一年营收同比明显下滑。"""
    years = sorted(data.years)
    if len(years) < 2:
        return None
    latest, prev = years[-1], years[-2]
    cur = _inc(data, "营业收入", latest)
    prv = _inc(data, "营业收入", prev)
    if prv <= 0 or cur <= 0:
        return None
    gr, _ = fin.growth_rate(cur, prv)
    if gr > REVENUE_DECLINE_WARN * 100.0:
        return None
    # 连续两年下滑加重
    consecutive = False
    if len(years) >= 3:
        older = _inc(data, "营业收入", years[-3])
        if older > 0:
            gr2, _ = fin.growth_rate(prv, older)
            consecutive = gr2 < 0
    severity = SEVERITY_HIGH if consecutive or gr <= -20 else SEVERITY_MEDIUM
    return Finding(
        id="REVENUE_DECLINE",
        title="营业收入同比下滑",
        category=CATEGORY_STRUCTURE,
        severity=severity,
        fact=(
            f"{prev}→{latest} 营业收入 {_fmt_money(prv)} → {_fmt_money(cur)}，"
            f"同比 {gr:+.2f}%"
            + ("；且已连续两年下滑" if consecutive else "")
            + "。"
        ),
        benchmark="管理经验：营收同比下降 ≥10% 需拆解订单/结算/价格/客户结构原因。",
        suggestion=(
            "建议区分「主动放弃低质量订单」与「被动丢单」；"
            "补高质量订单同时守住毛利率，避免以价换量。"
        ),
        options=_build_benchmark_options(
            "营业收入增速", gr, 0.0, is_low_risk=True
        ),
        current_value=gr,
        target_value=0.0,
        unit="%",
    )


def _check_profit_decline(data: FinancialData) -> Optional[Finding]:
    """净利润同比明显下滑。"""
    years = sorted(data.years)
    if len(years) < 2:
        return None
    latest, prev = years[-1], years[-2]
    cur = _inc(data, "净利润", latest)
    prv = _inc(data, "净利润", prev)
    if prv <= 0:
        return None
    gr, _ = fin.growth_rate(cur, prv)
    if gr > PROFIT_DECLINE_WARN * 100.0:
        return None
    return Finding(
        id="PROFIT_DECLINE",
        title="净利润同比下滑",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_MEDIUM if gr > -30 else SEVERITY_HIGH,
        fact=(
            f"{prev}→{latest} 净利润 {_fmt_money(prv)} → {_fmt_money(cur)}，"
            f"同比 {gr:+.2f}%。"
        ),
        benchmark="管理经验：净利润同比下降 ≥15% 需做毛利与期间费用归因。",
        suggestion=(
            "建议拆解毛利率变动、费用刚性与一次性损益；"
            "收入下降时同步锁定费用预算上限。"
        ),
        options=_build_benchmark_options("净利润增速", gr, 0.0, is_low_risk=True),
        current_value=gr,
        target_value=0.0,
        unit="%",
    )


def _check_period_expense_rising(data: FinancialData) -> Optional[Finding]:
    """收入下滑或持平而期间费用率上升。"""
    years = sorted(data.years)
    if len(years) < 2:
        return None
    latest, prev = years[-1], years[-2]
    cur_ind = fin.compute_year_indicators(data, latest)
    prev_ind = fin.compute_year_indicators(data, prev)
    cur_rev = cur_ind.get("营业收入", 0.0) or 0.0
    prev_rev = prev_ind.get("营业收入", 0.0) or 0.0
    if prev_rev <= 0 or cur_rev <= 0:
        return None
    cur_period = (
        cur_ind["销售费用率"]["value"]
        + cur_ind["管理费用率"]["value"]
        + cur_ind["财务费用率"]["value"]
    )
    prev_period = (
        prev_ind["销售费用率"]["value"]
        + prev_ind["管理费用率"]["value"]
        + prev_ind["财务费用率"]["value"]
    )
    rev_gr, _ = fin.growth_rate(cur_rev, prev_rev)
    if rev_gr > 5.0:  # 收入明显增长时费用率微升可接受
        return None
    if cur_period <= prev_period + 0.5:  # 至少上升 0.5pp
        return None
    return Finding(
        id="PERIOD_EXPENSE_RISING",
        title="期间费用率上升（收入承压时）",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_MEDIUM,
        fact=(
            f"{prev}→{latest} 营业收入同比 {rev_gr:+.2f}%，"
            f"期间费用率（销+管+财）由 {prev_period:.2f}% 升至 {cur_period:.2f}%"
            f"（+{cur_period-prev_period:.2f}pp）。"
        ),
        benchmark="管理经验：收入放缓时期间费用率应同步收敛，避免「越卖越贵的管理」。",
        suggestion=(
            "建议实行费用预算零基或增量上限，"
            "销售/管理/财务分项控费，超预算必须说明。"
        ),
        options=_build_benchmark_options(
            "期间费用率", cur_period, prev_period, is_low_risk=False
        ),
        current_value=cur_period,
        target_value=prev_period,
        unit="%",
    )


def _check_ar_growing_faster(data: FinancialData) -> Optional[Finding]:
    """应收账款增速快于营收（利润质量/现金占用信号）。"""
    years = sorted(data.years)
    if len(years) < 2:
        return None
    latest, prev = years[-1], years[-2]
    cur_ar = _bal(data, "应收账款", latest)
    prev_ar = _bal(data, "应收账款", prev)
    cur_rev = _inc(data, "营业收入", latest)
    prev_rev = _inc(data, "营业收入", prev)
    if prev_ar <= 0 or prev_rev <= 0 or cur_ar <= 0 or cur_rev <= 0:
        return None
    ar_gr, _ = fin.growth_rate(cur_ar, prev_ar)
    rev_gr, _ = fin.growth_rate(cur_rev, prev_rev)
    if ar_gr < rev_gr + 10.0:  # 应收增速至少快 10pp
        return None
    if ar_gr < 5.0:
        return None
    return Finding(
        id="AR_GROWING_FASTER",
        title="应收账款增速快于营收（现金占用）",
        category=CATEGORY_STRUCTURE,
        severity=SEVERITY_MEDIUM,
        fact=(
            f"{prev}→{latest} 应收账款同比 {ar_gr:+.2f}%，"
            f"营业收入同比 {rev_gr:+.2f}%，差额 {ar_gr-rev_gr:+.2f}pp。"
            f"易出现「账上有利润、手里缺现金」。"
        ),
        benchmark="管理经验：应收增速持续高于营收，利润质量与现金流承压。",
        suggestion=(
            "建议收紧信用政策，项目验收与开票节点前移，"
            "对逾期客户暂停新业务直至回款达标。"
        ),
        options=_build_amount_cut_options(
            "应收账款",
            cur_ar,
            cur_ar * 0.85,
            cur_ar * 0.92,
            "按账龄分级清收，90 天以上专案督办。",
            "先稳住新增应收，再消化历史积压。",
        ),
        current_value=ar_gr,
        target_value=rev_gr,
        unit="%",
    )


def diagnose(data: FinancialData) -> DiagnosisResult:
    """执行第一轮诊断，返回结构化发现列表。

    覆盖：税负对标、成本费用结构、真实性风险、经营质量（回款/杠杆/趋势）。
    全部规则为确定性判定，不依赖 AI；所有建议限于合法税务筹划与管理优化范畴。
    """
    benchmark, fallback = ind.get_benchmark(data.industry)
    findings: List[Finding] = []

    checks = [
        _check_rd_missing(data, benchmark),
        _check_rd_low(data, benchmark),
        _check_entertainment_excess(data),
        _check_consulting_high(data),
        _check_welfare_underused(data),
        _check_edu_underused(data),
        _check_ad_expense_high(data),
        _check_vat_low(data, benchmark),
        _check_vat_high(data, benchmark),
        _check_income_tax_low(data, benchmark),
        _check_income_tax_high(data, benchmark),
        _check_gross_margin_low(data, benchmark),
        _check_gross_margin_soft_low(data, benchmark),
        _check_gross_margin_high(data, benchmark),
        _check_net_margin_low(data, benchmark),
        _check_net_margin_high(data, benchmark),
        _check_expense_ratio_high(
            data, benchmark,
            ratio_key="销售费用率",
            income_account="销售费用",
            finding_id="SELLING_EXP_HIGH",
            title="销售费用率偏高",
        ),
        _check_expense_ratio_high(
            data, benchmark,
            ratio_key="管理费用率",
            income_account="管理费用",
            finding_id="ADMIN_EXP_HIGH",
            title="管理费用率偏高",
        ),
        _check_expense_ratio_high(
            data, benchmark,
            ratio_key="财务费用率",
            income_account="财务费用",
            finding_id="FIN_EXP_HIGH",
            title="财务费用率偏高",
        ),
        _check_ar_high(data),
        _check_debt_high(data),
        _check_revenue_decline(data),
        _check_profit_decline(data),
        _check_period_expense_rising(data),
        _check_ar_growing_faster(data),
    ]
    for f in checks:
        if f is not None:
            # 统一填入每个 Option 的 est_saving = cost_saving + tax_saving - tax_impact
            _finalize_option_savings(f.options)
            findings.append(f)

    # 高严重度优先，其次中、低；同级保持规则顺序
    severity_rank = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}
    findings.sort(key=lambda x: severity_rank.get(x.severity, 9))

    return DiagnosisResult(
        company_name=data.company_name,
        industry=data.industry,
        industry_fallback=fallback,
        years=list(data.years),
        findings=findings,
        vat_estimate_note=fin.VAT_ESTIMATE_NOTE,
    )
