"""通用财报案例管线门面（Phase A）。

任意企业 / 任意份数财报：Extract 之后的 Normalize → Reconcile → Policy
统一走本模块，产出 CaseBundle。Web 导入三入口（upload / case / sample）
只做 I/O，业务收敛于此。

规范见：AGENTS.md「通用财报管线」节（原架构 Spec 20260811，已整合）
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from . import budget as budget_mod
from . import compliance_policy as compliance_mod
from . import industry as ind_mod
from . import numeric_audit as num_audit_mod
from . import parser as parser_mod
from . import reconciliation as recon_mod
from .models import FinancialData

PathLike = Union[str, Path]
ParseOneFn = Callable[[str, str, str], FinancialData]


@dataclass
class PipelineOptions:
    """管线选项（案例包 manifest / 上传表单共用）。"""

    company_name: str = ""
    industry: str = ""
    case_id: Optional[str] = None
    income_tax_nominal_rate: Optional[float] = None  # 0.05/0.15/0.25
    # 若提供，则不再调用 parse_one（用于已解析列表）
    prefer_existing_data: bool = False


@dataclass
class PolicySnapshot:
    """政策单点快照：E2/E3/E4 + 费用约束（下游只读）。"""

    industry_key: str = ""
    e2_industry_contribution: float = 0.0
    e3_company_contribution: float = 0.0
    e3_basis: str = ""
    e4_income_tax_rate: float = budget_mod.DEFAULT_INCOME_TAX_RATE
    e4_source: str = "default_hnte"
    fee_band: Dict[str, Any] = field(default_factory=dict)
    fee_growth_cap: float = 0.0
    fee_growth_mode: str = ""
    revenue_growth_rate: float = 0.0
    revenue_volatile: bool = False
    robust_subject_ratios: Dict[str, float] = field(default_factory=dict)
    near_zero_selling: bool = False
    target_period_expense_ratio: float = 0.0
    hard_cap_period_expense_total: float = 0.0
    hard_rules: List[str] = field(default_factory=lambda: list(compliance_mod.HARD_RULES_LIST))

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # API 短字段别名（前后端契约）
        d["e2"] = self.e2_industry_contribution
        d["e3"] = self.e3_company_contribution
        d["e4"] = self.e4_income_tax_rate
        return d

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "PolicySnapshot":
        if not raw:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in raw.items() if k in known}
        return cls(**kwargs)


@dataclass
class CaseBundle:
    """一次导入的完整业务包。"""

    financial_data: FinancialData
    sources: List[Dict[str, Any]] = field(default_factory=list)
    policy: PolicySnapshot = field(default_factory=PolicySnapshot)
    quality: Dict[str, Any] = field(default_factory=dict)
    ocr_texts: List[str] = field(default_factory=list)
    case_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_import_response(
        self,
        *,
        indicators: Optional[list] = None,
        previews: Optional[list] = None,
        summary: Optional[dict] = None,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """三入口统一响应键集合。"""
        data = self.financial_data
        body: Dict[str, Any] = {
            "summary": summary,
            "indicators": indicators or [],
            "years": list(data.years or []),
            "previews": previews or [],
            "sources": list(self.sources),
            "data_quality": dict(self.quality or {}),
            "policy": self.policy.to_dict(),
            "numeric_audit": dict((data.parsed_meta or {}).get("numeric_audit") or {}),
            "reconciliation": (data.parsed_meta or {}).get("reconciliation") or {},
            "cit_synthesis": (data.parsed_meta or {}).get("cit_synthesis") or {},
            "expense_anomalies": (
                ((data.parsed_meta or {}).get("expense_anomalies") or {}).get("anomalies")
                or []
            ),
            "case_id": self.case_id,
        }
        if message:
            body["message"] = message
        return body


def _default_parse_one(path: str, company_name: str, industry: str) -> FinancialData:
    return parser_mod.parse_smart(
        path, company_name=company_name or "", industry=industry or "制造业"
    )


def _resolve_industry(data: FinancialData, industry: str, company_name: str) -> str:
    raw = (industry or data.industry or "").strip()
    if not raw or raw in ("未知", "其他"):
        raw, _ = ind_mod.recommend_by_rule(company_name or data.company_name or "")
    key = ind_mod.resolve_industry_key(raw or "制造业")
    data.industry = key
    return key


def _resolve_e4(data: FinancialData, hint: Optional[float]) -> tuple[float, str]:
    """附注/提示 > parsed_meta > 默认高新 15%。"""
    meta = data.parsed_meta or {}
    for candidate, source in (
        (hint, "options"),
        (meta.get("income_tax_nominal_rate"), "parsed_meta"),
    ):
        try:
            rate = float(candidate) if candidate is not None else None
        except (TypeError, ValueError):
            rate = None
        if rate in budget_mod.INCOME_TAX_RATE_CHOICES:
            return float(rate), source or "hint"
    return float(budget_mod.DEFAULT_INCOME_TAX_RATE), "default_hnte"


def build_policy_snapshot(
    data: FinancialData,
    *,
    industry: str = "",
    company_name: str = "",
    income_tax_hint: Optional[float] = None,
) -> PolicySnapshot:
    """从 FinancialData（已 enrich）合成 PolicySnapshot。"""
    ind_key = _resolve_industry(data, industry, company_name)
    # 确保 cit_synthesis / quality 存在
    if not (data.parsed_meta or {}).get("cit_synthesis"):
        recon_mod.enrich_financial_data(data, industry=ind_key)

    syn = (data.parsed_meta or {}).get("cit_synthesis") or recon_mod.synthesize_company_contribution(
        data, ind_key
    )
    e2 = float(syn.get("wb_hub") or ind_mod.get_income_tax_contribution_rate(ind_key, mode="hub"))
    e3 = float(syn.get("company_contribution_rate") or e2)
    e3_basis = str(syn.get("basis") or "hub_only")
    e4, e4_source = _resolve_e4(data, income_tax_hint)

    lim: Dict[str, Any] = {}
    try:
        lim = compliance_mod.budget_amount_limits(data, ind_key)
    except Exception:
        lim = {}
    hist = lim.get("historical") or {}
    band = lim.get("industry_fee_band") or ind_mod.get_period_expense_ratio_band(ind_key)

    return PolicySnapshot(
        industry_key=ind_key,
        e2_industry_contribution=round(e2, 6),
        e3_company_contribution=round(e3, 6),
        e3_basis=e3_basis,
        e4_income_tax_rate=e4,
        e4_source=e4_source,
        fee_band=dict(band) if isinstance(band, dict) else {},
        fee_growth_cap=float(lim.get("max_fee_growth_rate") or hist.get("fee_growth_cap") or 0),
        fee_growth_mode=str(lim.get("fee_growth_mode") or hist.get("fee_growth_mode") or ""),
        revenue_growth_rate=float(lim.get("revenue_growth_rate") or hist.get("revenue_growth_rate") or 0),
        revenue_volatile=bool(lim.get("revenue_volatile") or hist.get("revenue_volatile")),
        robust_subject_ratios=dict(lim.get("robust_subject_ratios") or {}),
        near_zero_selling=bool(lim.get("near_zero_selling")),
        target_period_expense_ratio=float(lim.get("target_period_expense_ratio") or 0),
        hard_cap_period_expense_total=float(lim.get("hard_cap_period_expense_total") or 0),
        hard_rules=list(compliance_mod.HARD_RULES_LIST),
    )


def apply_policy_to_plan(plan, policy: PolicySnapshot) -> List[str]:
    """把 PolicySnapshot 写入 BudgetPlan 顶栏（from-session / export 共用）。"""
    notes: List[str] = []
    if plan is None or policy is None:
        return notes
    ti = plan.top_inputs
    if policy.industry_key:
        plan.industry = policy.industry_key
    old_e2 = float(getattr(ti, "industry_contribution_rate", 0) or 0)
    old_e3 = float(getattr(ti, "company_contribution_rate", 0) or 0)
    old_e4 = float(getattr(ti, "income_tax_rate", 0) or 0)
    ti.industry_contribution_rate = float(policy.e2_industry_contribution)
    ti.company_contribution_rate = float(policy.e3_company_contribution)
    if policy.e4_income_tax_rate in budget_mod.INCOME_TAX_RATE_CHOICES:
        ti.income_tax_rate = float(policy.e4_income_tax_rate)
    if abs(old_e2 - ti.industry_contribution_rate) > 1e-9:
        notes.append(
            f"E2←policy {ti.industry_contribution_rate*100:.2f}%（原 {old_e2*100:.2f}%）"
        )
    if abs(old_e3 - ti.company_contribution_rate) > 1e-9:
        notes.append(
            f"E3←policy {ti.company_contribution_rate*100:.2f}% basis={policy.e3_basis}"
        )
    if abs(old_e4 - ti.income_tax_rate) > 1e-9:
        notes.append(
            f"E4←policy {ti.income_tax_rate*100:.0f}% source={policy.e4_source}"
        )
    return notes


def assemble_bundle(
    data: FinancialData,
    *,
    options: Optional[PipelineOptions] = None,
    ocr_texts: Optional[Sequence[str]] = None,
    sources: Optional[Sequence[dict]] = None,
) -> CaseBundle:
    """已合并的 FinancialData → enrich + policy → CaseBundle。"""
    opts = options or PipelineOptions()
    company = opts.company_name or data.company_name or ""
    industry = opts.industry or data.industry or ""
    if company and not data.company_name:
        data.company_name = company
    ocr_list = [t for t in (ocr_texts or []) if str(t).strip()]

    recon_mod.enrich_financial_data(
        data, ocr_texts=ocr_list, industry=industry or data.industry or ""
    )
    if opts.income_tax_nominal_rate in budget_mod.INCOME_TAX_RATE_CHOICES:
        data.parsed_meta = data.parsed_meta or {}
        data.parsed_meta["income_tax_nominal_rate"] = float(opts.income_tax_nominal_rate)

    policy = build_policy_snapshot(
        data,
        industry=industry,
        company_name=company,
        income_tax_hint=opts.income_tax_nominal_rate,
    )
    # 写回 meta，便于 session 只存 FinancialData 时仍可恢复 policy
    data.parsed_meta = data.parsed_meta or {}
    data.parsed_meta["policy"] = policy.to_dict()
    data.industry = policy.industry_key

    # 数字专项质检（独立分析层）：恒等式全式 + 小数点错位归因 + 逐年跳变 +
    # 业务合理性 + OCR 原文字面双层防护；高风险发现将要求导出前人工核验
    numeric_report = num_audit_mod.audit_numbers(data, ocr_texts=ocr_list)
    data.parsed_meta["numeric_audit"] = numeric_report

    quality = dict(data.parsed_meta.get("data_quality") or {})
    quality.setdefault("require_confirm", bool(quality.get("require_confirm")))
    quality["numeric_grade"] = numeric_report.get("grade", "")
    quality["numeric_score"] = numeric_report.get("score", 0)
    if any(f.get("severity") == "high" for f in numeric_report.get("findings", [])):
        quality["require_confirm"] = True

    return CaseBundle(
        financial_data=data,
        sources=[dict(s) for s in (sources or [])],
        policy=policy,
        quality=quality,
        ocr_texts=ocr_list,
        case_id=opts.case_id,
        meta={
            "warnings": list(data.parsed_meta.get("warnings") or []),
            "merged_files": data.parsed_meta.get("merged_files"),
            "source": data.parsed_meta.get("source"),
        },
    )


def run_case_pipeline(
    paths: Sequence[PathLike],
    *,
    options: Optional[PipelineOptions] = None,
    parse_one: Optional[ParseOneFn] = None,
    ocr_texts: Optional[Sequence[str]] = None,
    sources: Optional[Sequence[dict]] = None,
) -> CaseBundle:
    """唯一编排入口：路径列表 → CaseBundle。

    parse_one: 单文件解析回调；Web 层传入带 DeepSeek 的 _parse_one。
    未提供时回退 parser.parse_smart（无 AI 扫 PDF 可能失败）。
    """
    opts = options or PipelineOptions()
    path_list = [Path(p) for p in paths]
    if not path_list:
        raise parser_mod.ParserError("管线输入为空：未提供任何文件路径")

    parse_fn = parse_one or _default_parse_one
    company = opts.company_name or ""
    industry = opts.industry or "制造业"

    parsed: List[FinancialData] = []
    errors: List[str] = []
    for p in path_list:
        if not p.is_file():
            errors.append(f"{p.name}: 文件不存在")
            continue
        try:
            one = parse_fn(str(p), company, industry)
            if company and not one.company_name:
                one.company_name = company
            if industry and (not one.industry or one.industry in ("未知",)):
                one.industry = industry
            parsed.append(one)
        except parser_mod.ParserError as e:
            errors.append(f"{p.name}: {e}")
        except Exception as e:
            errors.append(f"{p.name}: {type(e).__name__}: {e}")

    if errors and not parsed:
        raise parser_mod.ParserError("解析失败：" + "；".join(errors))
    if errors and parsed:
        # 部分失败：Spec 顾问场景可继续，但写入 warnings；当前严格模式：有失败即整批失败
        raise parser_mod.ParserError("以下文件解析失败：" + "；".join(errors))

    if len(parsed) == 1:
        data = parsed[0]
    else:
        data = parser_mod.merge_years(*parsed)
        # merge_years 内已 enrich 一次；assemble 会再 enrich（幂等）

    if company and not data.company_name:
        data.company_name = company
    if industry:
        data.industry = industry

    return assemble_bundle(
        data,
        options=opts,
        ocr_texts=ocr_texts,
        sources=sources,
    )


def run_case_pipeline_from_data(
    data: FinancialData,
    *,
    options: Optional[PipelineOptions] = None,
    ocr_texts: Optional[Sequence[str]] = None,
    sources: Optional[Sequence[dict]] = None,
) -> CaseBundle:
    """已有 FinancialData（sample / 测试）→ CaseBundle。"""
    return assemble_bundle(
        data, options=options, ocr_texts=ocr_texts, sources=sources
    )


def policy_from_data(data: Optional[FinancialData]) -> PolicySnapshot:
    """从会话 FinancialData.parsed_meta 恢复 policy；缺失则现算。"""
    if data is None:
        return PolicySnapshot()
    raw = (data.parsed_meta or {}).get("policy")
    if isinstance(raw, dict) and raw.get("e3_company_contribution") is not None:
        return PolicySnapshot.from_dict(raw)
    return build_policy_snapshot(data, industry=data.industry or "", company_name=data.company_name or "")


def resolve_cit_rate(data: Optional[FinancialData] = None) -> float:
    """诊断/AI 统一读取名义所得税率 E4（Policy 单点）。"""
    if data is not None:
        try:
            rate = float(policy_from_data(data).e4_income_tax_rate)
            if rate in budget_mod.INCOME_TAX_RATE_CHOICES:
                return rate
        except Exception:
            pass
    return float(budget_mod.DEFAULT_INCOME_TAX_RATE)


def stamp_findings_tax_rate(findings: list, data: Optional[FinancialData]) -> None:
    """把 Policy E4 写回诊断选项 tax_rate（就地修改）。"""
    rate = resolve_cit_rate(data)
    for f in findings or []:
        opts = getattr(f, "options", None)
        if opts is None and isinstance(f, dict):
            opts = f.get("options") or []
        for o in opts or []:
            if hasattr(o, "tax_rate"):
                o.tax_rate = rate
            elif isinstance(o, dict):
                o["tax_rate"] = rate


# 导入响应键集合（契约测试用）
IMPORT_RESPONSE_KEYS = frozenset(
    {
        "summary",
        "indicators",
        "years",
        "previews",
        "sources",
        "data_quality",
        "policy",
        "reconciliation",
        "cit_synthesis",
        "expense_anomalies",
        "case_id",
    }
)
