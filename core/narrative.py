"""利润宝 · 阶段叙事生成（小白版经营故事线）。

把多年财报拆成「最以前 / 中间 / 现在 / 将来」，用看得懂的话说明：
- 每个阶段是什么情况
- 现在最大的问题与亮点
- 以后要做什么

全部为确定性文本（可复核），不依赖 AI。AI 增强可选，在 report 层叠加。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from . import finance as fin
from .diagnostic import DiagnosisResult, Finding
from .models import FinancialData


@dataclass
class StageBlock:
    """一个历史阶段的叙事块。"""
    title: str
    years: List[int]
    summary: str
    bullets: List[str] = field(default_factory=list)


@dataclass
class MetricExplain:
    name: str
    plain: str
    value_text: str
    judgment: str


@dataclass
class NowPoint:
    """「现在」章节下的小标题 + 正文（对应艺康报告 Heading 2）。"""
    title: str
    body: str


@dataclass
class YearRow:
    """跨年对照表一行。"""
    year: int
    revenue: str
    net_profit: str
    gross_margin: str
    net_margin: str
    one_liner: str


@dataclass
class TimelineItem:
    """落地时间表一行。"""
    when: str
    action: str
    purpose: str


@dataclass
class MonthlyRow:
    """老板月度看板一行。"""
    metric: str
    why: str
    how: str


@dataclass
class StageNarrative:
    """完整阶段叙事，供报告 / 第二稿复用（对标艺康体小白版）。"""
    company_name: str
    industry: str
    years: List[int]
    headline: str
    stages: List[StageBlock]
    now_metrics: List[MetricExplain]
    future_actions: List[str]
    monthly_kpis: List[str]
    decision_summaries: List[str] = field(default_factory=list)
    # 艺康体扩展字段
    subtitle: str = "小白版：用看得懂的话，讲最以前、现在和将来"
    data_source_note: str = ""
    one_liner: str = ""
    year_rows: List[YearRow] = field(default_factory=list)
    now_points: List[NowPoint] = field(default_factory=list)
    stage_insight: str = ""
    monthly_rows: List[MonthlyRow] = field(default_factory=list)
    timeline: List[TimelineItem] = field(default_factory=list)
    methodology_notes: List[str] = field(default_factory=list)


def _money(v: float) -> str:
    if abs(v) >= 100_000_000:
        return f"{v/100_000_000:.2f}亿元"
    if abs(v) >= 10_000:
        return f"{v/10_000:.2f}万元"
    return f"{v:,.0f}元"


def _pct(v: float) -> str:
    return f"{v:.2f}%"


def _get_inc(data: FinancialData, account: str, year: int) -> float:
    return (data.income_statement.get(account, {}) or {}).get(year, 0.0) or 0.0


def _get_bal(data: FinancialData, account: str, year: int) -> float:
    return (data.balance_sheet.get(account, {}) or {}).get(year, 0.0) or 0.0


def _year_snapshot(data: FinancialData, year: int) -> dict:
    ind = fin.compute_year_indicators(data, year)
    rev = float(ind.get("营业收入", 0.0) or 0.0)
    np_ = _get_inc(data, "净利润", year)
    ar = _get_bal(data, "应收账款", year)
    assets = _get_bal(data, "资产总额", year)
    liab = _get_bal(data, "负债总额", year)
    debt = (liab / assets * 100.0) if assets > 0 else 0.0
    ar_ratio = (ar / rev * 100.0) if rev > 0 else 0.0
    return {
        "year": year,
        "revenue": rev,
        "net_profit": np_,
        "gross_margin": ind["毛利率"]["value"],
        "net_margin": ind["净利率"]["value"],
        "vat": ind["增值税税负率"]["value"],
        "itr": ind["所得税税负率"]["value"],
        "ar": ar,
        "ar_ratio": ar_ratio,
        "debt_ratio": debt,
        "selling": ind["销售费用率"]["value"],
        "admin": ind["管理费用率"]["value"],
        "period": (
            ind["销售费用率"]["value"]
            + ind["管理费用率"]["value"]
            + ind["财务费用率"]["value"]
        ),
    }


def _one_line_year(s: dict) -> str:
    bits = [
        f"收入{_money(s['revenue'])}",
        f"净利润{_money(s['net_profit'])}",
        f"毛利率{_pct(s['gross_margin'])}",
        f"净利率{_pct(s['net_margin'])}",
    ]
    if s["ar"] > 0:
        bits.append(f"应收/收入{_pct(s['ar_ratio'])}")
    if s["debt_ratio"] > 0:
        bits.append(f"负债率{_pct(s['debt_ratio'])}")
    return "；".join(bits) + "。"


def _split_stages(years: List[int]) -> List[tuple]:
    """返回 [(title, year_list), ...]。"""
    ys = sorted(years)
    if not ys:
        return []
    if len(ys) == 1:
        return [("现在", ys)]
    if len(ys) == 2:
        return [("最以前", [ys[0]]), ("现在", [ys[1]])]
    if len(ys) == 3:
        return [("最以前", [ys[0]]), ("中间阶段", [ys[1]]), ("现在", [ys[2]])]
    # 4+：首年、中间若干、最近一年
    return [
        ("最以前", [ys[0]]),
        ("中间阶段", ys[1:-1]),
        ("现在", [ys[-1]]),
    ]


def _stage_summary(title: str, snaps: List[dict]) -> tuple:
    """生成阶段摘要与要点。"""
    if not snaps:
        return "暂无数据。", []
    first, last = snaps[0], snaps[-1]
    bullets: List[str] = []
    for s in snaps:
        bullets.append(f"{s['year']}年：{_one_line_year(s)}")

    if len(snaps) == 1:
        s = snaps[0]
        if title == "现在":
            summary = (
                f"{s['year']}年是当前观察窗口：收入{_money(s['revenue'])}，"
                f"净利润{_money(s['net_profit'])}，毛利率{_pct(s['gross_margin'])}，"
                f"净利率{_pct(s['net_margin'])}。"
            )
        else:
            summary = (
                f"{s['year']}年：收入{_money(s['revenue'])}，"
                f"净利润{_money(s['net_profit'])}，"
                f"毛利率{_pct(s['gross_margin'])}。"
            )
        return summary, bullets

    rev_gr, _ = fin.growth_rate(last["revenue"], first["revenue"])
    np_gr, _ = fin.growth_rate(last["net_profit"], first["net_profit"])
    gm_delta = last["gross_margin"] - first["gross_margin"]
    summary = (
        f"{first['year']}-{last['year']}：收入从{_money(first['revenue'])}"
        f"到{_money(last['revenue'])}（区间变化 {rev_gr:+.1f}%），"
        f"净利润从{_money(first['net_profit'])}到{_money(last['net_profit'])}"
        f"（{np_gr:+.1f}%），毛利率变化 {gm_delta:+.1f} 个百分点。"
    )
    if rev_gr < -10 and np_gr > 0:
        summary += "用大白话说：生意规模缩小了，但赚钱能力反而更好。"
    elif rev_gr > 10 and np_gr < 0:
        summary += "用大白话说：做得更大了，但最终留下的利润变少了。"
    elif rev_gr > 10 and np_gr > 10:
        summary += "用大白话说：规模和利润都在往上走。"
    elif rev_gr < -10 and np_gr < -10:
        summary += "用大白话说：规模和利润一起承压，需要同时抓订单与费用。"
    return summary, bullets


def _build_headline(data: FinancialData, diagnosis: DiagnosisResult, latest: dict) -> str:
    highs = [f for f in diagnosis.findings if f.severity == "高"]
    top = highs[0].title if highs else (diagnosis.findings[0].title if diagnosis.findings else "")
    years = sorted(data.years)
    if len(years) >= 2:
        prev = _year_snapshot(data, years[-2])
        rev_gr, _ = fin.growth_rate(latest["revenue"], prev["revenue"])
        np_gr, _ = fin.growth_rate(latest["net_profit"], prev["net_profit"])
        head = (
            f"{latest['year']}年收入{_money(latest['revenue'])}"
            f"（同比 {rev_gr:+.1f}%），净利润{_money(latest['net_profit'])}"
            f"（同比 {np_gr:+.1f}%）。"
        )
        if rev_gr < 0 and np_gr > 0:
            head += "卖得少了但更赚钱，"
        elif rev_gr < 0 and np_gr < 0:
            head += "收入和利润双双承压，"
        elif rev_gr > 0 and np_gr > 0:
            head += "规模与利润同步改善，"
        else:
            head += "经营结构出现分化，"
    else:
        head = (
            f"{latest['year']}年收入{_money(latest['revenue'])}，"
            f"净利润{_money(latest['net_profit'])}，"
        )
    if latest["ar_ratio"] >= 40:
        head += f"最大压力在回款（应收占收入{_pct(latest['ar_ratio'])}）"
    elif top:
        head += f"优先处理「{top}」"
    else:
        head += "主要指标大体落在行业合理区间"
    head += "。"
    return head


def _now_metrics(latest: dict) -> List[MetricExplain]:
    rows = [
        MetricExplain(
            "营业收入",
            "公司一年做成并确认了多少生意",
            _money(latest["revenue"]),
            "看规模是否持续",
        ),
        MetricExplain(
            "毛利率",
            "每100元收入先能留下多少毛利",
            _pct(latest["gross_margin"]),
            "看项目/产品赚钱能力",
        ),
        MetricExplain(
            "净利率",
            "每100元收入最后能留下多少",
            _pct(latest["net_margin"]),
            "看最终经营成果",
        ),
        MetricExplain(
            "增值税税负率（估算）",
            "估算的增值税负担轻重",
            _pct(latest["vat"]),
            "偏低要防真实性问询；偏高要查进项",
        ),
        MetricExplain(
            "所得税税负率",
            "所得税相对收入的负担",
            _pct(latest["itr"]),
            "结合利润与优惠适用核验",
        ),
    ]
    if latest["ar"] > 0:
        rows.append(
            MetricExplain(
                "应收账款/收入",
                "客户欠款相当于一年收入的多少",
                _pct(latest["ar_ratio"]),
                "偏高必须抓回款" if latest["ar_ratio"] >= 40 else "保持跟进即可",
            )
        )
    if latest["debt_ratio"] > 0:
        rows.append(
            MetricExplain(
                "资产负债率",
                "资产里有多少靠负债撑着",
                _pct(latest["debt_ratio"]),
                "偏高要盯偿债与期限结构" if latest["debt_ratio"] >= 70 else "结构尚可",
            )
        )
    return rows


def _default_future_actions(
    diagnosis: DiagnosisResult,
    latest: dict,
    decisions: Optional[Sequence] = None,
) -> List[str]:
    actions: List[str] = []
    ids = {f.id for f in diagnosis.findings}

    if decisions:
        for d in decisions:
            label = getattr(d, "option_label", "")
            name = getattr(d, "option_name", "")
            title = getattr(d, "finding_title", getattr(d, "finding_id", ""))
            detail = getattr(d, "action_detail", "") or ""
            line = f"针对「{title}」已选 {label}. {name}"
            if detail:
                line += f"：{detail}"
            actions.append(line)

    # 通用优先级（按经营常识排序）
    if "AR_HIGH" in ids or "AR_GROWING_FASTER" in ids or latest["ar_ratio"] >= 40:
        actions.append(
            "把回款当一号工程：按客户/项目/账龄列清单，明确责任人、预计回款日与卡点，每周更新。"
        )
    if "REVENUE_DECLINE" in ids:
        actions.append(
            "补高质量订单，而不是低价冲量：新项目先算全成本与付款条件，再签合同。"
        )
    if "GROSS_MARGIN_LOW" in ids or "GROSS_MARGIN_SOFT_LOW" in ids:
        actions.append(
            "保住毛利率：报价覆盖材料、人工、运输、安装、税费与资金占用，低毛利项目慎接。"
        )
    if "PERIOD_EXPENSE_RISING" in ids or "ADMIN_EXP_HIGH" in ids or "SELLING_EXP_HIGH" in ids:
        actions.append(
            "收入承压时费用同步收敛：月度费用预算，超预算必须说明原因。"
        )
    if "RD_MISSING" in ids or "RD_LOW" in ids:
        actions.append(
            "建立研发辅助账与立项文件，符合条件申报研发费用加计扣除（合法税务筹划）。"
        )
    if "ENTERTAIN_EXCESS" in ids or "CONSULTING_HIGH" in ids or "AD_EXPENSE_HIGH" in ids:
        actions.append(
            "压缩超限/异常费用，强化合同-资金-发票-交付物四流合一留痕。"
        )
    if "VAT_LOW" in ids or "INCOME_TAX_LOW" in ids:
        actions.append(
            "准备税负偏离说明材料，核对进项与成本费用真实性，避免稽查被动。"
        )
    if "WELFARE_UNDERUSED" in ids or "EDU_UNDERUSED" in ids:
        actions.append(
            "在工资薪金限额内据实列支福利与培训支出，完善签收与培训档案。"
        )
    if "DEBT_HIGH" in ids:
        actions.append(
            "梳理债务期限结构，压降高息短期负债，同步加速回款与存货周转。"
        )

    actions.append(
        "建立月度经营看板：收入、毛利率、净利润、回款、应收、逾期款、现金余额、未来三个月付款计划。"
    )
    actions.append(
        "预留现金安全垫：至少覆盖 1-2 个月固定支出；大额投资与分红前先看现金余量。"
    )

    # 去重保序
    seen = set()
    uniq: List[str] = []
    for a in actions:
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


def _monthly_kpis() -> List[str]:
    return [
        "本月收入 —— 业务有没有持续",
        "本月毛利率 —— 项目到底赚不赚钱",
        "本月净利润 —— 最终经营成果",
        "本月实际回款 —— 钱有没有回来",
        "应收账款余额 —— 客户欠款有没有越滚越大",
        "逾期应收账款 —— 风险款项",
        "账上可用现金（若有）—— 短期安全垫",
        "未来三个月付款计划 —— 会不会断档",
    ]


def _year_one_liner(s: dict, prev: Optional[dict]) -> str:
    if prev is None:
        return f"起点年：收入{_money(s['revenue'])}，净利率{_pct(s['net_margin'])}。"
    rev_gr, _ = fin.growth_rate(s["revenue"], prev["revenue"])
    np_gr, _ = fin.growth_rate(s["net_profit"], prev["net_profit"])
    bits = []
    if rev_gr < -10:
        bits.append("收入明显下滑")
    elif rev_gr > 10:
        bits.append("收入明显增长")
    else:
        bits.append("收入大体平稳")
    if np_gr > 10:
        bits.append("利润改善")
    elif np_gr < -10:
        bits.append("利润承压")
    if s["ar_ratio"] >= 40:
        bits.append("应收占用偏高")
    if s["gross_margin"] - prev["gross_margin"] >= 2:
        bits.append("毛利率抬升")
    elif s["gross_margin"] - prev["gross_margin"] <= -2:
        bits.append("毛利率回落")
    return "，".join(bits) + "。"


def _build_year_rows(snaps: dict, years: List[int]) -> List[YearRow]:
    rows: List[YearRow] = []
    prev = None
    for y in years:
        s = snaps[y]
        rows.append(
            YearRow(
                year=y,
                revenue=_money(s["revenue"]),
                net_profit=_money(s["net_profit"]),
                gross_margin=_pct(s["gross_margin"]),
                net_margin=_pct(s["net_margin"]),
                one_liner=_year_one_liner(s, prev),
            )
        )
        prev = s
    return rows


def _build_now_points(
    data: FinancialData,
    snaps: dict,
    years: List[int],
    diagnosis: DiagnosisResult,
) -> List[NowPoint]:
    latest_y = years[-1]
    latest = snaps[latest_y]
    prev = snaps[years[-2]] if len(years) >= 2 else None
    points: List[NowPoint] = []
    n = 1
    if prev:
        rev_gr, _ = fin.growth_rate(latest["revenue"], prev["revenue"])
        delta = latest["revenue"] - prev["revenue"]
        direction = "下降" if rev_gr < 0 else "上升"
        points.append(NowPoint(
            title=f"{n}. 收入{direction}，说明业务规模{'承压' if rev_gr < 0 else '在扩张'}",
            body=(
                f"{latest_y}年营业收入为{_money(latest['revenue'])}，"
                f"比{years[-2]}年{'少' if delta < 0 else '多'}{_money(abs(delta))}，"
                f"同比 {rev_gr:+.2f}%。"
                f"用通俗话讲，就是当年做成并确认的业务{'少了' if rev_gr < 0 else '多了'}。"
                f"原因可能是订单变化、项目结算节奏、市场竞争，或主动筛选项目质量。"
            ),
        ))
        n += 1
        gm_delta = latest["gross_margin"] - prev["gross_margin"]
        points.append(NowPoint(
            title=(
                f"{n}. 毛利率"
                f"{'提高' if gm_delta > 0 else '下降' if gm_delta < 0 else '基本持平'}，"
                f"说明项目赚钱能力"
                f"{'变强' if gm_delta > 0 else '变弱' if gm_delta < 0 else '大致稳定'}"
            ),
            body=(
                f"{latest_y}年毛利率为{_pct(latest['gross_margin'])}，"
                f"{years[-2]}年是{_pct(prev['gross_margin'])}。"
                f"意思是每100元收入，{years[-2]}年大约留下{prev['gross_margin']:.2f}元毛利，"
                f"{latest_y}年大约留下{latest['gross_margin']:.2f}元毛利。"
                + (
                    "这是一个积极信号，说明报价、成本控制或项目选择可能更好。"
                    if gm_delta > 0
                    else "需要复核成本归集与定价是否失控。"
                    if gm_delta < 0
                    else "毛利结构保持稳定。"
                )
            ),
        ))
        n += 1
        np_gr, _ = fin.growth_rate(latest["net_profit"], prev["net_profit"])
        points.append(NowPoint(
            title=f"{n}. 净利润{'增长' if np_gr > 0 else '下滑' if np_gr < 0 else '持平'}，但不能只看利润",
            body=(
                f"{latest_y}年净利润为{_money(latest['net_profit'])}，"
                f"同比 {np_gr:+.2f}%。表面看{'不错' if np_gr > 0 else '需要警惕'}，"
                f"但经营管理不能只看利润：利润是账面结果，回款与现金才是每天能用的钱。"
            ),
        ))
        n += 1
    else:
        points.append(NowPoint(
            title=f"{n}. 当前经营画像",
            body=(
                f"{latest_y}年收入{_money(latest['revenue'])}，"
                f"净利润{_money(latest['net_profit'])}，"
                f"毛利率{_pct(latest['gross_margin'])}，净利率{_pct(latest['net_margin'])}。"
            ),
        ))
        n += 1

    if latest["ar"] > 0:
        points.append(NowPoint(
            title=f"{n}. 应收账款占用资金，要防止「账上有利润、手里缺现金」",
            body=(
                f"{latest_y}年应收账款{_money(latest['ar'])}，"
                f"相当于全年收入的{_pct(latest['ar_ratio'])}。"
                f"{'偏高，必须把回款放到一号工程。' if latest['ar_ratio'] >= 40 else '需持续跟进，避免越滚越大。'}"
                f"应收不一定是坏账，但不跟进就会拖慢现金周转。"
            ),
        ))
        n += 1

    # 诊断高风险各取一条作「现在」补充
    for f in diagnosis.findings:
        if f.severity != "高":
            continue
        if any(f.title[:6] in p.title for p in points):
            continue
        points.append(NowPoint(
            title=f"{n}. {f.title}",
            body=f"{f.fact} {f.suggestion}",
        ))
        n += 1
        if n > 6:
            break
    return points


def _build_one_liner(years: List[int], snaps: dict, headline: str) -> str:
    if not years:
        return headline
    arc = []
    if len(years) >= 2:
        first, last = snaps[years[0]], snaps[years[-1]]
        rev_gr, _ = fin.growth_rate(last["revenue"], first["revenue"])
        np_gr, _ = fin.growth_rate(last["net_profit"], first["net_profit"])
        arc.append(f"{years[0]}到{years[-1]}")
        if rev_gr < -10 and np_gr > 0:
            arc.append("经历了「规模回落但利润质量改善」")
        elif rev_gr > 10 and np_gr > 10:
            arc.append("经历了「规模与利润同步扩张」")
        elif rev_gr < -10 and np_gr < -10:
            arc.append("经历了「收入利润双承压」")
        else:
            arc.append("经营结构在调整")
    if snaps[years[-1]]["ar_ratio"] >= 40:
        arc.append("当前最大压力在回款")
    text = "，".join(arc) + "。" if arc else headline
    return f"一句话结论：{text} {headline}"


def _build_stage_insight(stages: List[StageBlock]) -> str:
    if not stages:
        return "以前阶段的启发：收入大不等于经营健康。真正健康的生意，要同时看收入、利润和回款。"
    return (
        "以前阶段的启发：收入大不等于经营健康。真正健康的生意，要同时看三个东西："
        "有没有收入、有没有利润、有没有把钱收回来。"
        "只看其中一项，容易误判。"
    )


def _monthly_rows() -> List[MonthlyRow]:
    how = "每月10日前更新，开会只讨论异常和解决办法。"
    return [
        MonthlyRow("本月收入", "看业务有没有持续。", how),
        MonthlyRow("本月毛利率", "看项目到底赚不赚钱。", how),
        MonthlyRow("本月净利润", "看最终经营成果。", how),
        MonthlyRow("本月实际回款", "看钱有没有回来。", how),
        MonthlyRow("应收账款余额", "看客户欠款有没有越滚越大。", how),
        MonthlyRow("逾期应收账款", "看风险款项。", how),
        MonthlyRow("账上现金余额（若有）", "看短期安全垫。", how),
        MonthlyRow("未来3个月要付款金额", "提前安排资金，不临时抱佛脚。", how),
    ]


def _build_timeline(diagnosis: DiagnosisResult, latest: dict, decisions: Optional[Sequence]) -> List[TimelineItem]:
    items = [
        TimelineItem(
            "7天内",
            "做出应收账款明细表（若有应收）：前十大欠款客户、金额、账龄、责任人。",
            "先把钱在哪里看清楚。",
        ),
        TimelineItem(
            "15天内",
            "针对诊断高/中风险问题，选定 A/B/C 落地动作并指定负责人。",
            "把方案从口号变成行动。",
        ),
        TimelineItem(
            "30天内",
            "完成费用/税负/回款中至少 1 个可量化改进项，并复盘数字变化。",
            "形成第一轮可见成果。",
        ),
        TimelineItem(
            "每月",
            "固定做一页经营看板，管理层只看关键异常。",
            "避免问题拖到年底。",
        ),
        TimelineItem(
            "每季度",
            "复盘客户和项目质量，减少低毛利、高垫资、回款慢项目。",
            "让未来收入更健康。",
        ),
    ]
    if decisions:
        items.insert(
            2,
            TimelineItem(
                "互动确认后",
                "按已选方案推进：" + "；".join(
                    f"{getattr(d, 'finding_title', '')}→{getattr(d, 'option_label', '')}"
                    for d in list(decisions)[:4]
                ),
                "把互动决策落到执行清单。",
            ),
        )
    if latest.get("ar_ratio", 0) >= 40:
        items[0] = TimelineItem(
            "7天内",
            "做出应收账款明细表，列出前十大欠款客户、金额、账龄、责任人；逾期款单独标红。",
            "先把钱在哪里看清楚。",
        )
    return items


def build_stage_narrative(
    data: FinancialData,
    diagnosis: DiagnosisResult,
    decisions: Optional[Sequence] = None,
) -> StageNarrative:
    """构建阶段叙事（结论 → 各阶段 → 现在指标 → 将来动作）。"""
    years = sorted(data.years)
    if not years:
        return StageNarrative(
            company_name=data.company_name or "未命名企业",
            industry=data.industry,
            years=[],
            headline="暂无可用财报年度，无法生成阶段分析。",
            stages=[],
            now_metrics=[],
            future_actions=["请先导入近三年利润表/资产负债表后再诊断。"],
            monthly_kpis=_monthly_kpis(),
            data_source_note="尚未导入有效财报年度。",
            one_liner="一句话结论：数据不足，暂无法形成经营判断。",
            methodology_notes=["请先导入近三年审计报告或财报表后再导出。"],
        )

    snaps = {y: _year_snapshot(data, y) for y in years}
    latest = snaps[years[-1]]
    stages: List[StageBlock] = []
    for title, ys in _split_stages(years):
        stage_snaps = [snaps[y] for y in ys]
        summary, bullets = _stage_summary(title, stage_snaps)
        # 标题润色
        if title == "最以前":
            full_title = (
                f"二、最以前：{ys[0]}年"
                if len(ys) == 1
                else f"二、最以前：{ys[0]}-{ys[-1]}年"
            )
            # 章节号在 Word 层统一编排，这里用语义标题
            full_title = f"最以前：{ys[0]}年" if len(ys) == 1 else f"最以前：{ys[0]}-{ys[-1]}年"
        elif title == "中间阶段":
            full_title = f"中间阶段：{ys[0]}-{ys[-1]}年" if len(ys) > 1 else f"中间阶段：{ys[0]}年"
        else:
            tag = ""
            if len(years) >= 2:
                prev = snaps[years[-2]]
                rev_gr, _ = fin.growth_rate(latest["revenue"], prev["revenue"])
                np_gr, _ = fin.growth_rate(latest["net_profit"], prev["net_profit"])
                if rev_gr < 0 and np_gr > 0:
                    tag = "是「利润好看，但要盯回款」的一年"
                elif rev_gr < 0 and np_gr < 0:
                    tag = "是「规模与利润双承压」的一年"
                elif rev_gr > 0 and np_gr > 0:
                    tag = "是「规模利润同步改善」的一年"
                else:
                    tag = "是经营结构分化的一年"
            full_title = f"现在：{ys[-1]}年{tag}"
        stages.append(StageBlock(title=full_title, years=list(ys), summary=summary, bullets=bullets))

    decision_summaries: List[str] = []
    if decisions:
        for d in decisions:
            title = getattr(d, "finding_title", "")
            label = getattr(d, "option_label", "")
            name = getattr(d, "option_name", "")
            saving = getattr(d, "est_saving", 0.0) or 0.0
            decision_summaries.append(
                f"{title} → 选 {label}. {name}（预计净影响 {_money(saving)}）"
            )

    headline = _build_headline(data, diagnosis, latest)
    year_label = "、".join(f"{y}年" for y in years)
    return StageNarrative(
        company_name=data.company_name or "未命名企业",
        industry=data.industry,
        years=list(years),
        headline=headline,
        stages=stages,
        now_metrics=_now_metrics(latest),
        future_actions=_default_future_actions(diagnosis, latest, decisions),
        monthly_kpis=_monthly_kpis(),
        decision_summaries=decision_summaries,
        subtitle="小白版：用看得懂的话，讲最以前、现在和将来",
        data_source_note=(
            f"数据来源：导入的 {year_label} 财报/审计报告（科目经同义词归并后计算）。"
            f"分析日期：{__import__('datetime').datetime.now().strftime('%Y年%m月%d日')}。"
        ),
        one_liner=_build_one_liner(years, snaps, headline),
        year_rows=_build_year_rows(snaps, years),
        now_points=_build_now_points(data, snaps, years, diagnosis),
        stage_insight=_build_stage_insight(stages),
        monthly_rows=_monthly_rows(),
        timeline=_build_timeline(diagnosis, latest, decisions),
        methodology_notes=[
            f"本报告基于导入的 {year_label} 报表数据，经确定性计算引擎复核指标（可逐笔反算）。",
            "增值税税负率为估算值（税金及附加 ÷ 12% ÷ 营业收入），实际以增值税申报表为准。",
            "诊断建议属于合法税务筹划与经营管理建议，不构成投资、融资或法律意见，不替代审计报告。",
            "具体执行前建议与主管税务机关或注册税务师确认，并以企业实际经营情况为准。",
        ],
    )


def narrative_to_paragraphs(n: StageNarrative) -> List[str]:
    """扁平化为段落列表（便于 PDF 等简单排版）。"""
    paras: List[str] = [n.one_liner or n.headline, n.headline]
    for st in n.stages:
        paras.append(st.title)
        paras.append(st.summary)
        paras.extend(st.bullets)
    for p in n.now_points:
        paras.append(p.title)
        paras.append(p.body)
    paras.append("关键指标白话解读")
    for m in n.now_metrics:
        paras.append(f"{m.name}：{m.plain}。当前 {m.value_text}。判断：{m.judgment}。")
    if n.decision_summaries:
        paras.append("互动决策摘要")
        paras.extend(n.decision_summaries)
    paras.append("将来要做什么")
    for i, a in enumerate(n.future_actions, 1):
        paras.append(f"{i}. {a}")
    paras.append("建议老板每月盯住这些数")
    paras.extend(n.monthly_kpis)
    return paras
