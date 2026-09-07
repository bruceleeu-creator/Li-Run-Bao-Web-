"""利润宝 · 多轮互动状态机与第二稿生成（S5）。

状态机：IDLE → FINDING_LOOP → DRAFT2 → CONFIRMATION → FINAL
- 每轮针对一条发现给出 A/B/C 三个可量化选项，记录用户决策与战略意图
- 全部发现处理完进入 DRAFT2，生成第二稿
- 落地性 ≥95% 或用户确认后进入 FINAL，解锁最终导出
- AI 未配置时不触网；超时或失败时回退规则引擎并返回可展示提示
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import finance as fin
from .diagnostic import (
    DiagnosisResult, Finding, Option,
    RISK_HIGH, RISK_MEDIUM, RISK_LOW,
    COMPLIANCE_NOTE,
)
from .models import FinancialData

# 状态机常量
STATE_IDLE = "IDLE"
STATE_FINDING_LOOP = "FINDING_LOOP"
STATE_DRAFT2 = "DRAFT2"
STATE_CONFIRMATION = "CONFIRMATION"
STATE_FINAL = "FINAL"

# 落地性阈值
FEASIBILITY_THRESHOLD = 95.0

# 高风险"暂维持"扣分权重
HIGH_RISK_PENALTY = 12.0
MEDIUM_RISK_PENALTY = 4.0

# v32 低价值发现自动处理门槛：severity==低 且 最大预计影响 < max(绝对下限, 最新年营收×该比例)
LOW_VALUE_ABS_FLOOR = 1000.0  # 元
LOW_VALUE_REVENUE_RATIO = 0.0005  # 营收的 0.05%


@dataclass
class Decision:
    """一条发现的用户决策记录。"""
    finding_id: str
    finding_title: str
    option_label: str  # "A" / "B" / "C"
    option_name: str
    current_value: float
    target_value: float
    est_saving: float
    risk_level: str
    strategy_note: str = ""  # 用户补充的战略意图
    # 第二稿要素（生成时填入）
    trend: str = ""  # 环比同比
    change_amount: float = 0.0  # 变动幅度（目标 - 当前）
    change_pct: float = 0.0  # 变动百分比
    action_detail: str = ""  # 操作细节
    cautions: str = ""  # 注意事项
    auto: bool = False  # v32：程序自动处理（低价值发现按暂维持），非用户决策


@dataclass
class Draft2Entry:
    """第二稿中一条决策的完整呈现。

    cost_saving / tax_saving / tax_impact / est_saving 单位均为「元」。
    - cost_saving：成本节约（非负）
    - tax_saving：税收节约（非负，加计扣除等）
    - tax_impact：税负影响（正=所得税增加；负=所得税减少）
    - est_saving：净影响 = cost_saving + tax_saving - tax_impact
    """
    finding_id: str
    finding_title: str
    option_label: str
    option_name: str
    trend: str
    current_value: float
    target_value: float
    change_amount: float
    change_pct: str  # 文本形式，方便展示
    est_saving: float
    action_detail: str
    cautions: str
    risk_level: str
    # T6.4 第二轮复核：补充分栏金额，供 Excel 公式与报告分栏展示
    cost_saving: float = 0.0
    tax_saving: float = 0.0
    tax_impact: float = 0.0
    # 适用税率与加计扣除比例（用于 Excel 公式；标注「项目测算默认值/待核验」）
    tax_rate: float = 0.0
    deduction_rate: float = 0.0


@dataclass
class Session:
    """一次完整的互动会话。"""
    data: FinancialData
    diagnosis: DiagnosisResult
    state: str = STATE_IDLE
    decisions: List[Decision] = field(default_factory=list)
    strategy_notes: List[str] = field(default_factory=list)  # 全局战略意图
    draft2: List[Draft2Entry] = field(default_factory=list)
    ai_fallback_message: str = ""  # AI 不可用时的提示
    user_confirmed: bool = False
    # 落地性
    feasibility_score: float = 100.0
    feasibility_breakdown: List[str] = field(default_factory=list)
    # v32 提问聚焦：只对 interactive_ids 中的发现逐题问（高→中→低、金额降序）；
    # 低价值发现自动按「暂维持」生成 auto_decisions，不进互动轮次
    interactive_ids: List[str] = field(default_factory=list)
    auto_decisions: List[Decision] = field(default_factory=list)

    @property
    def current_finding_index(self) -> int:
        return len(self.decisions)

    @property
    def current_finding(self) -> Optional[Finding]:
        if self.state != STATE_FINDING_LOOP:
            return None
        if self.current_finding_index >= len(self.interactive_ids):
            return None
        fid = self.interactive_ids[self.current_finding_index]
        return self.diagnosis.finding_by_id(fid)

    @property
    def is_export_unlocked(self) -> bool:
        """是否解锁最终导出。"""
        if self.state == STATE_FINAL:
            return True
        if self.state == STATE_CONFIRMATION and self.user_confirmed:
            return True
        if self.state == STATE_CONFIRMATION and self.feasibility_score >= FEASIBILITY_THRESHOLD:
            return True
        return False

    @property
    def total_est_saving(self) -> float:
        return round(sum(d.est_saving for d in self.decisions + self.auto_decisions), 2)


def is_low_value(finding: Finding, data: FinancialData) -> bool:
    """v32 低价值发现判定：低严重度且预计影响金额低于门槛。

    门槛 = max(1000 元, 最新年营收 × 0.05%)。此类发现不进互动轮次，
    自动按「暂维持」处理并在第二稿中如实列出，决策精力集中于大额问题。
    """
    if str(getattr(finding, "severity", "") or "").strip() != "低":
        return False
    from . import compliance_policy as compliance_mod

    money = compliance_mod.finding_money_key(finding)
    years = sorted(data.years or [])
    revenue = 0.0
    if years:
        revenue = float(
            (data.income_statement.get("营业收入", {}) or {}).get(years[-1], 0.0) or 0.0
        )
    threshold = max(LOW_VALUE_ABS_FLOOR, revenue * LOW_VALUE_REVENUE_RATIO)
    return money < threshold


def start_session(data: FinancialData, diagnosis: DiagnosisResult) -> Session:
    """启动互动会话：若有需互动的发现进入 FINDING_LOOP，否则直接 DRAFT2。

    v32 提问聚焦：
    - 互动顺序 = 高→中→低风险、同级按预计节税金额降序（重点先问，
      与诊断页低→高展示顺序解耦）
    - 低价值发现（低严重度且金额影响小）自动按「暂维持」生成决策，
      不逐题打扰；其决策不参与落地性扣分
    """
    from . import compliance_policy as compliance_mod

    # 复制列表再排，避免外部持有旧顺序
    findings = compliance_mod.sort_findings_for_interaction(list(diagnosis.findings or []))
    diagnosis.findings = findings

    interactive: List[Finding] = []
    auto_decisions: List[Decision] = []
    for f in findings:
        if is_low_value(f, data):
            option = next((o for o in f.options if o.label == "C"), None)
            if option is None and f.options:
                option = f.options[-1]
            auto_decisions.append(Decision(
                finding_id=f.id,
                finding_title=f.title,
                option_label=option.label if option else "C",
                option_name=option.name if option else "暂维持（低影响自动处理）",
                current_value=f.current_value,
                target_value=option.target_value if option else f.current_value,
                est_saving=option.est_saving if option else 0.0,
                risk_level=option.risk_level if option else RISK_MEDIUM,
                action_detail=(option.action_note if option else "低影响发现，自动按暂维持处理。"),
                auto=True,
            ))
        else:
            interactive.append(f)

    sess = Session(
        data=data,
        diagnosis=diagnosis,
        interactive_ids=[f.id for f in interactive],
        auto_decisions=auto_decisions,
    )
    if interactive:
        sess.state = STATE_FINDING_LOOP
    else:
        sess.state = STATE_DRAFT2
        _generate_draft2(sess)
    return sess


def submit_decision(
    sess: Session,
    finding_id: str,
    option_label: str,
    strategy_note: str = "",
    ai_engine=None,
) -> Optional[Decision]:
    """提交对当前发现的决策。

    - 严格只接受当前发现的 finding_id，防止状态机跳项
    - 记录决策与战略意图
    - 推进到下一条发现；全部处理完则进入 DRAFT2
    - 若提供 ai_engine 且配置可用，尝试增强选项描述；失败则静默回退
    """
    if sess.state != STATE_FINDING_LOOP:
        return None
    # 严格校验：必须针对当前发现，不允许跳项
    current = sess.current_finding
    if current is None:
        return None
    if finding_id != current.id:
        # 状态机不跳项：返回 None，调用方应使用 sess.current_finding.id
        return None
    option = next((o for o in current.options if o.label == option_label), None)
    if option is None:
        return None

    if strategy_note:
        sess.strategy_notes.append(strategy_note)

    decision = Decision(
        finding_id=current.id,
        finding_title=current.title,
        option_label=option.label,
        option_name=option.name,
        current_value=current.current_value,
        target_value=option.target_value,
        est_saving=option.est_saving,
        risk_level=option.risk_level,
        strategy_note=strategy_note,
        action_detail=option.action_note,
    )
    sess.decisions.append(decision)

    # 推进到下一条或进入 DRAFT2（v32：完成条件按互动集合，auto 决策已在 start 时生成）
    if sess.current_finding_index >= len(sess.interactive_ids):
        sess.state = STATE_DRAFT2
        _generate_draft2(sess)
    return decision


def _account_series(data: FinancialData, account: str, years: List[int]):
    """取科目多年序列：优先科目余额表，其次利润表。

    返回 [(年, 值)]（只含有数据的年份）；科目无年度序列返回 None。
    """
    for table in (data.account_balances, data.income_statement):
        mapping = table.get(account) or {}
        if isinstance(mapping, dict):
            pairs = [(y, mapping.get(y)) for y in years if mapping.get(y) is not None]
            if pairs:
                return pairs
    return None


def _trend_from_account(data: FinancialData, account: str, years: List[int]) -> Optional[str]:
    """按科目多年序列生成环比同比文本；序列不足两年返回 None（走旧特判/兜底）。"""
    series = _account_series(data, account, years)
    if not series or len(series) < 2:
        return None
    prev_y, prev_v = series[-2]
    latest_y, cur = series[-1]
    if cur == 0 and prev_v == 0:
        return f"近 {len(series)} 年{account}持续为 0"
    val, _ = fin.growth_rate(cur, prev_v)
    return (f"{account} {prev_y}→{latest_y}：{prev_v:,.0f} → {cur:,.0f}"
            f"（同比 {val:+.2f}%）")


def _compute_trend(sess: Session, decision: Decision) -> str:
    """计算当前发现对应指标的环比/同比趋势。

    返回文本形式，无历史时标注"无历史，仅列最新值"。

    v32 泛化：发现挂了 account_key（真实科目名）时，优先用科目多年序列
    生成趋势；否则回退 4 个硬编码发现的特判，最后"无历史"兜底。
    """
    data = sess.data
    years = sorted(data.years)
    if len(years) < 2:
        return "无历史，仅列最新值"

    fid = decision.finding_id
    latest = years[-1]
    prev = years[-2]

    # 根据发现类型取对应年份序列
    def _series(table, account):
        return [
            (y, (table.get(account, {}) or {}).get(y, 0.0) or 0.0)
            for y in years
        ]

    # v32：发现挂接科目 → 科目序列真实趋势（覆盖规则与 AI 发现）
    finding = sess.diagnosis.finding_by_id(fid)
    account_key = str(getattr(finding, "account_key", "") or "") if finding else ""
    if account_key:
        trend = _trend_from_account(data, account_key, years)
        if trend:
            return trend

    if fid == "RD_MISSING":
        series = _series(data.income_statement, "研发费用")
        cur = series[-1][1]
        prev_v = series[-2][1]
        if cur == 0 and prev_v == 0:
            return f"近 {len(years)} 年研发费用持续为 0（无历史投入）"
        val, note = fin.growth_rate(cur, prev_v)
        return f"研发费用 {prev}→{latest}：{prev_v:,.0f} → {cur:,.0f}（同比 {val:+.2f}%，{note}）"

    if fid == "ENTERTAIN_EXCESS":
        # 招待费无科目序列时用管理费用代替说明趋势
        series = _series(data.income_statement, "管理费用")
        cur = series[-1][1]
        prev_v = series[-2][1]
        val, _ = fin.growth_rate(cur, prev_v)
        return f"管理费用（含招待费）{prev}→{latest}：{prev_v:,.0f} → {cur:,.0f}（同比 {val:+.2f}%）"

    if fid == "CONSULTING_HIGH":
        # 同上，无科目余额表年度序列，用管理费用趋势说明
        series = _series(data.income_statement, "管理费用")
        cur = series[-1][1]
        prev_v = series[-2][1]
        val, _ = fin.growth_rate(cur, prev_v)
        return f"管理费用（含咨询费）{prev}→{latest}：{prev_v:,.0f} → {cur:,.0f}（同比 {val:+.2f}%）"

    if fid == "VAT_LOW":
        cur_ind = fin.compute_year_indicators(data, latest)
        prev_ind = fin.compute_year_indicators(data, prev)
        cur_v = cur_ind["增值税税负率"]["value"]
        prev_v = prev_ind["增值税税负率"]["value"]
        return (f"增值税税负率 {prev}→{latest}：{prev_v:.2f}% → {cur_v:.2f}%"
                f"（估算口径，基于税金及附加反推）")

    return "无历史，仅列最新值"


def _build_cautions(decision: Decision, finding: Finding) -> str:
    """根据风险等级生成注意事项。"""
    parts = []
    if decision.risk_level == RISK_HIGH:
        parts.append("本选项为高风险暂维持方案，存在被纳税调整或稽查关注的风险。")
    if finding.category == "真实性风险":
        parts.append("属真实性风险，须确保合同、资金、发票、业务交付物四流合一。")
    parts.append(COMPLIANCE_NOTE)
    return " ".join(parts)


def _generate_draft2(sess: Session) -> None:
    """生成第二稿：每条决策含环比同比、当前值→目标值→变动幅度→预计节税、操作细节、注意事项。

    v32：按 findings 顺序（高→中→低、金额降序）遍历，合并用户决策与
    低价值自动决策（auto），每条发现都有对应第二稿条目。
    """
    decision_map: Dict[str, Decision] = {}
    for d in list(sess.decisions) + list(sess.auto_decisions):
        decision_map[d.finding_id] = d

    entries: List[Draft2Entry] = []
    for finding in sess.diagnosis.findings or []:
        d = decision_map.get(finding.id)
        if d is None:
            continue
        trend = _compute_trend(sess, d)
        d.trend = trend
        change = round(d.target_value - d.current_value, 2)
        d.change_amount = change
        if d.current_value != 0 and finding.unit == "%":
            d.change_pct = round(change / d.current_value * 100.0, 2)
        elif finding.unit == "元" and d.current_value > 0:
            d.change_pct = round(change / d.current_value * 100.0, 2)
        cautions = _build_cautions(d, finding)
        d.cautions = cautions
        change_pct_text = f"{d.change_pct:+.2f}%" if d.current_value != 0 else "基数缺失"
        # 从原 Option 中取出分栏金额与税率/加计扣除比例
        option = next((o for o in finding.options if o.label == d.option_label), None)
        cost_saving = float(option.cost_saving) if option else 0.0
        tax_saving = float(option.tax_saving) if option else 0.0
        tax_impact = float(option.tax_impact) if option else 0.0
        tax_rate = float(option.tax_rate) if option else 0.0
        deduction_rate = float(getattr(option, "deduction_rate", 0.0)) if option else 0.0
        # 研发类 Option 已显式写入加计扣除比例；旧字段缺失时按 finding_id 推断
        if option is not None and deduction_rate == 0.0 and d.finding_id == "RD_MISSING":
            deduction_rate = 1.0  # 项目测算默认值：研发费用 100% 加计扣除（待核验适用条件）
        action_detail = d.action_detail
        if d.auto:
            action_detail = f"[低影响·自动暂维持] {action_detail}"
        entries.append(Draft2Entry(
            finding_id=d.finding_id,
            finding_title=d.finding_title,
            option_label=d.option_label,
            option_name=d.option_name,
            trend=trend,
            current_value=d.current_value,
            target_value=d.target_value,
            change_amount=change,
            change_pct=change_pct_text,
            est_saving=d.est_saving,
            action_detail=action_detail,
            cautions=cautions,
            risk_level=d.risk_level,
            cost_saving=cost_saving,
            tax_saving=tax_saving,
            tax_impact=tax_impact,
            tax_rate=tax_rate,
            deduction_rate=deduction_rate,
        ))
    sess.draft2 = entries
    _compute_feasibility(sess)
    sess.state = STATE_CONFIRMATION


def _compute_feasibility(sess: Session) -> None:
    """落地性评分：默认 100%；高风险暂维持扣分；中风险轻微扣分。"""
    score = 100.0
    breakdown: List[str] = []
    for d in sess.decisions:
        if d.option_label == "C" and d.risk_level == RISK_HIGH:
            score -= HIGH_RISK_PENALTY
            breakdown.append(f"{d.finding_title} 选 C（暂维持/高风险）-{HIGH_RISK_PENALTY:.0f}pp")
        elif d.risk_level == RISK_HIGH:
            score -= HIGH_RISK_PENALTY * 0.5
            breakdown.append(f"{d.finding_title} 高风险 -{HIGH_RISK_PENALTY*0.5:.0f}pp")
        elif d.option_label == "C":
            score -= MEDIUM_RISK_PENALTY
            breakdown.append(f"{d.finding_title} 选 C（保守）-{MEDIUM_RISK_PENALTY:.0f}pp")
    score = max(0.0, min(100.0, round(score, 2)))
    sess.feasibility_score = score
    sess.feasibility_breakdown = breakdown


def confirm(sess: Session, user_confirmed: bool = True) -> str:
    """用户确认或自动判定是否进入 FINAL。

    返回新状态。低于阈值且未确认时不解锁。
    """
    if sess.state != STATE_CONFIRMATION:
        return sess.state
    sess.user_confirmed = user_confirmed
    if user_confirmed or sess.feasibility_score >= FEASIBILITY_THRESHOLD:
        sess.state = STATE_FINAL
    return sess.state


def feasibility_summary(sess: Session) -> str:
    """落地性可解释文本。"""
    parts = [f"落地性评分：{sess.feasibility_score:.2f}%"]
    if sess.feasibility_breakdown:
        parts.append("扣分明细：")
        parts.extend(f"  - {b}" for b in sess.feasibility_breakdown)
    if sess.feasibility_score >= FEASIBILITY_THRESHOLD:
        parts.append(f"已 ≥ {FEASIBILITY_THRESHOLD:.0f}% 阈值，导出已解锁。")
    elif sess.user_confirmed:
        parts.append("用户已确认，导出已解锁。")
    else:
        parts.append(f"低于 {FEASIBILITY_THRESHOLD:.0f}% 且未确认，导出未解锁；请调整选项或确认。")
    return "\n".join(parts)


def try_ai_enhance(sess: Session, ai_engine) -> bool:
    """尝试用 AI 增强第二稿描述；失败静默回退并写入提示。

    原子回退策略：
    - 先备份所有 entry.action_detail 原文
    - 逐条尝试 AI 增强；任一条失败立即回滚所有已修改项
    - 返回 True 表示全部成功；False 表示已回退到原文

    返回 True 表示成功；False 表示已回退。
    """
    if ai_engine is None or not ai_engine.is_available():
        sess.ai_fallback_message = "大模型未配置或不可用，已使用本地规则引擎生成第二稿。"
        return False
    if not sess.draft2:
        sess.ai_fallback_message = "大模型已启用，但当前无第二稿条目可增强。"
        return True
    # 原子备份
    originals = [entry.action_detail for entry in sess.draft2]
    enhanced_count = 0
    try:
        for i, entry in enumerate(sess.draft2):
            if not entry.action_detail:
                continue
            enhanced = ai_engine.refine_report(entry.action_detail, max_tokens=200)
            if enhanced:
                entry.action_detail = enhanced
                enhanced_count += 1
        sess.ai_fallback_message = (
            f"大模型已增强第二稿操作细节描述（共 {enhanced_count} 条）。"
        )
        return True
    except Exception as e:
        # 原子回退：恢复所有 entry 的 action_detail
        for entry, orig in zip(sess.draft2, originals):
            entry.action_detail = orig
        sess.ai_fallback_message = (
            f"大模型调用失败（{type(e).__name__}），已原子回退到本地规则引擎第二稿。"
        )
        return False
