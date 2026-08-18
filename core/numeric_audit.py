"""利润宝 · 数字质检与优化引擎（独立数字专项分析层）。

定位与分工（2026-08-18 架构新增）：
- ``reconciliation`` 负责表间/跨年勾稽（R1/R2/R3）与政策合成；
- 本模块专做「文档中提取的数字本身」的体检——会计恒等式全式核对、
  小数点/量级错位归因（如 22263237393 → 222,632,373.93）、逐年跳变、
  业务合理性，并输出结构化质检报告（评分 + 逐条发现 + 修正建议）。

原则：
- 全部为确定性数学，可逐项由原始数字反算复核；
- 只分析与建议，不静默改数（任何修正必须人工确认）；
- 纯标准库，不依赖上层模块（架构护栏）。

输出契约（dict，前后端同构）：
{
  "engine": "numeric_audit", "version": 1,
  "score": 0-100, "grade": "高"|"中"|"低",
  "summary": "...",
  "checked": {"subjects": int, "years": [...]},
  "identities": [{id, rule, year, status, value, expected, gap, gap_pct}],
  "findings": [{id, check, year, subject, severity, value, expected,
                gap, gap_pct, message, suggestion}]
}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import FinancialData

# ── 口径与阈值 ─────────────────────────────────────────────────────────

# 利润表全式核对：真实报告含其他收益/营业外收支等杂项，残差以营收占比表达
_PBT_INFO_PCT = 0.02     # ≤2% 视为正常杂项差
_PBT_WARN_PCT = 0.10     # >10% 升级为警告并尝试错位归因
# 资产负债恒等式：正常仅四舍五入差，容差 = max(1元, 资产×0.01%)
_BAL_REL_TOL = 1e-4
_BAL_MIN_TOL = 1.0
# 逐年跳变：相邻年 |变化率| 超过该值提示人工核对
_JUMP_RATIO = 0.80
# 错位归因：把某科目乘 10^k 后残差缩小到原残差的 1/100 以内视为命中
_SCALE_FACTORS = (0.0001, 0.001, 0.01, 0.1, 10.0, 100.0, 1000.0, 10000.0)
_SCALE_COLLAPSE = 100.0
# 评分权重
_SCORE_WEIGHTS = {"high": 25, "medium": 10, "low": 3}

_PBT_TERMS = ("营业成本", "税金及附加", "销售费用", "管理费用", "研发费用", "财务费用")
_KEY_SUBJECTS = ("营业收入", "营业成本", "利润总额", "净利润", "资产总额", "负债总额", "所有者权益")
_NON_NEGATIVE = ("营业收入", "营业成本", "资产总额", "所有者权益")
_FEE_SUBJECTS = ("销售费用", "管理费用", "研发费用", "财务费用")


@dataclass
class _Finding:
    id: str
    check: str
    severity: str  # high | medium | low
    message: str
    year: Optional[int] = None
    subject: str = ""
    value: Optional[float] = None
    expected: Optional[float] = None
    gap: Optional[float] = None
    gap_pct: Optional[float] = None
    suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "check": self.check,
            "year": self.year,
            "subject": self.subject,
            "severity": self.severity,
            "value": round(self.value, 2) if self.value is not None else None,
            "expected": round(self.expected, 2) if self.expected is not None else None,
            "gap": round(self.gap, 2) if self.gap is not None else None,
            "gap_pct": round(self.gap_pct, 6) if self.gap_pct is not None else None,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class _Audit:
    findings: List[_Finding] = field(default_factory=list)
    identities: List[Dict[str, Any]] = field(default_factory=list)
    subjects_checked: int = 0

    def add(self, f: _Finding) -> None:
        self.findings.append(f)


def _amt(data: FinancialData, account: str, year: int) -> Optional[float]:
    table = {
        "营业收入": data.income_statement,
        "营业成本": data.income_statement,
        "税金及附加": data.income_statement,
        "销售费用": data.income_statement,
        "管理费用": data.income_statement,
        "研发费用": data.income_statement,
        "财务费用": data.income_statement,
        "利润总额": data.income_statement,
        "所得税费用": data.income_statement,
        "净利润": data.income_statement,
        "资产总额": data.balance_sheet,
        "负债总额": data.balance_sheet,
        "所有者权益": data.balance_sheet,
    }.get(account)
    if table is None:
        return None
    v = (table.get(account) or {}).get(year)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _fmt(v: Optional[float]) -> str:
    return f"{v:,.2f}" if v is not None else "—"


# ── A. 利润表全式恒等 + D. 错位归因 ───────────────────────────────────

def _audit_pbt_identity(data: FinancialData, year: int, audit: _Audit) -> None:
    rev = _amt(data, "营业收入", year)
    pbt = _amt(data, "利润总额", year)
    if rev is None or pbt is None or rev == 0:
        return
    terms = {t: _amt(data, t, year) for t in _PBT_TERMS}
    missing = [t for t, v in terms.items() if v is None]
    known_sum = sum(v for v in terms.values() if v is not None)
    expected = rev - known_sum
    gap = pbt - expected
    gap_pct = abs(gap) / abs(rev)
    status = "pass" if gap_pct <= _PBT_INFO_PCT else ("warn" if gap_pct <= _PBT_WARN_PCT else "fail")
    audit.identities.append({
        "id": f"NA-PBT-{year}",
        "rule": "利润总额 ≈ 营收 − 成本 − 税金及附加 − 四费（残差=其他收支）",
        "year": year,
        "status": status,
        "value": round(pbt, 2),
        "expected": round(expected, 2),
        "gap": round(gap, 2),
        "gap_pct": round(gap_pct, 6),
        "missing_terms": missing,
    })
    if status == "pass":
        return
    # 错位归因：某科目乘 10^k 后残差骤减 → 高置信小数点错位建议
    attribution = _attribute_scale_error(rev, pbt, terms)
    if attribution is not None:
        subject, factor, residual_after = attribution
        raw_value = rev if subject == "营业收入" else terms[subject]
        audit.add(_Finding(
            id=f"NA-PBT-SCALE-{year}-{subject}",
            check="小数点/量级错位归因",
            severity="high",
            year=year,
            subject=subject,
            value=raw_value,
            expected=round(raw_value * factor, 2),
            gap=round(residual_after, 2),
            message=(
                f"{year} 年利润表恒等式残差 { _fmt(gap) } 元（占营收 {gap_pct*100:.1f}%）；"
                f"将「{subject}」×{factor:g} 后残差降至 {_fmt(residual_after)} 元"
            ),
            suggestion=(
                f"疑似「{subject}」小数点错位：{_fmt(raw_value)} → "
                f"{_fmt(raw_value * factor)}。请对照审计报告原文核对小数位。"
            ),
        ))
        return
    if status == "fail":
        audit.add(_Finding(
            id=f"NA-PBT-{year}",
            check="利润表恒等式",
            severity="medium",
            year=year,
            subject="利润总额",
            value=pbt,
            expected=round(expected, 2),
            gap=round(gap, 2),
            gap_pct=round(gap_pct, 6),
            message=(
                f"{year} 年利润总额 {_fmt(pbt)} 与「营收−成本−费用」推算值 "
                f"{_fmt(expected)} 相差 {_fmt(gap)} 元（占营收 {gap_pct*100:.1f}%）"
                + (f"；缺失科目：{'、'.join(missing)}" if missing else "")
            ),
            suggestion="残差可能来自其他收益/营业外收支，也可能有科目提取错误；请对照原文核对。",
        ))


def _attribute_scale_error(
    rev: float, pbt: float, terms: Dict[str, Optional[float]]
) -> Optional[tuple]:
    """尝试把恒等式大残差归因到单一科目的 10^k 错位（含营收自身）。

    返回 (科目, 因子, 归因后残差)；无法归因返回 None。
    """
    base_residual = pbt - (rev - sum(v for v in terms.values() if v is not None))
    if abs(base_residual) < 1:
        return None
    components: Dict[str, Optional[float]] = {"营业收入": rev}
    components.update(terms)
    best = None
    for subject, v in components.items():
        if v is None or v == 0:
            continue
        for factor in _SCALE_FACTORS:
            scaled = v * factor
            if subject == "营业收入":
                residual = pbt - (scaled - sum(o for o in terms.values() if o is not None))
            else:
                others = sum(o for s, o in terms.items() if s != subject and o is not None)
                residual = pbt - (rev - others - scaled)
            if abs(residual) < max(1.0, abs(base_residual) / _SCALE_COLLAPSE):
                if best is None or abs(residual) < best[2]:
                    best = (subject, factor, residual)
    return best


# ── B. 资产负债恒等式 ──────────────────────────────────────────────────

def _audit_balance_identity(data: FinancialData, year: int, audit: _Audit) -> None:
    assets = _amt(data, "资产总额", year)
    liab = _amt(data, "负债总额", year)
    eq = _amt(data, "所有者权益", year)
    if assets is None or liab is None or eq is None:
        return
    expected = liab + eq
    gap = assets - expected
    tol = max(_BAL_MIN_TOL, abs(assets) * _BAL_REL_TOL)
    ok = abs(gap) <= tol
    audit.identities.append({
        "id": f"NA-BAL-{year}",
        "rule": "资产总额 = 负债总额 + 所有者权益",
        "year": year,
        "status": "pass" if ok else "fail",
        "value": round(assets, 2),
        "expected": round(expected, 2),
        "gap": round(gap, 2),
        "gap_pct": round(abs(gap) / abs(assets), 6) if assets else None,
    })
    if ok:
        return
    # 错位归因（三项任一 ×10^k 使等式闭合）
    attribution = None
    for subject, v in (("资产总额", assets), ("负债总额", liab), ("所有者权益", eq)):
        if v == 0:
            continue
        for factor in _SCALE_FACTORS:
            scaled = v * factor
            if subject == "资产总额":
                residual = scaled - (liab + eq)
            elif subject == "负债总额":
                residual = assets - (scaled + eq)
            else:
                residual = assets - (liab + scaled)
            if abs(residual) <= tol:
                attribution = (subject, factor, residual)
                break
        if attribution:
            break
    if attribution:
        subject, factor, residual = attribution
        audit.add(_Finding(
            id=f"NA-BAL-SCALE-{year}-{subject}",
            check="小数点/量级错位归因",
            severity="high",
            year=year,
            subject=subject,
            value={"资产总额": assets, "负债总额": liab, "所有者权益": eq}[subject],
            expected=round({"资产总额": assets, "负债总额": liab, "所有者权益": eq}[subject] * factor, 2),
            gap=round(residual, 2),
            message=f"{year} 年资产负债恒等式差 {_fmt(gap)} 元；「{subject}」×{factor:g} 后等式闭合",
            suggestion=(
                f"疑似「{subject}」小数点错位，请对照审计报告原文核对小数位。"
            ),
        ))
    else:
        audit.add(_Finding(
            id=f"NA-BAL-{year}",
            check="资产负债恒等式",
            severity="high",
            year=year,
            subject="资产总额",
            value=assets,
            expected=round(expected, 2),
            gap=round(gap, 2),
            message=f"{year} 年资产 {_fmt(assets)} ≠ 负债+权益 {_fmt(expected)}，差 {_fmt(gap)} 元",
            suggestion="恒等式不闭合通常意味着数字提取错误；请逐项对照原文核对三张表。",
        ))


# ── E1. 逐年跳变 / E2. 业务合理性 ─────────────────────────────────────

def _audit_jumps(data: FinancialData, audit: _Audit) -> None:
    years = sorted(data.years or [])
    for i in range(1, len(years)):
        y0, y1 = years[i - 1], years[i]
        for acc in _KEY_SUBJECTS:
            v0, v1 = _amt(data, acc, y0), _amt(data, acc, y1)
            if not v0 or not v1 or v0 == 0:
                continue
            change = (v1 - v0) / abs(v0)
            if abs(change) > _JUMP_RATIO:
                audit.add(_Finding(
                    id=f"NA-JUMP-{acc}-{y1}",
                    check="逐年跳变",
                    severity="medium" if abs(change) >= 2.0 else "low",
                    year=y1,
                    subject=acc,
                    value=v1,
                    expected=v0,
                    gap=round(v1 - v0, 2),
                    gap_pct=round(change, 6),
                    message=f"{acc} {y0}→{y1} 变动 {change*100:+.1f}%（{_fmt(v0)} → {_fmt(v1)}）",
                    suggestion="波动超过 80%，建议对照原文确认非提取错误（若业务属实可忽略）。",
                ))


def _audit_plausibility(data: FinancialData, audit: _Audit) -> None:
    for y in sorted(data.years or []):
        rev = _amt(data, "营业收入", y)
        cost = _amt(data, "营业成本", y)
        for acc in _NON_NEGATIVE:
            v = _amt(data, acc, y)
            if v is not None and v < 0:
                audit.add(_Finding(
                    id=f"NA-NEG-{acc}-{y}",
                    check="数值合理性",
                    severity="high",
                    year=y, subject=acc, value=v,
                    message=f"{y} 年{acc}为负（{_fmt(v)}）",
                    suggestion="该科目正常不应为负；可能是括号负数解析或列错位，请核对原文。",
                ))
        if rev and rev > 0 and cost is not None:
            gm = (rev - cost) / rev
            if gm > 1.0 or gm < -0.3:
                audit.add(_Finding(
                    id=f"NA-GM-{y}",
                    check="毛利率合理性",
                    severity="medium",
                    year=y, subject="毛利率", value=round(gm, 6),
                    message=f"{y} 年毛利率 {gm*100:.1f}%（超出常见区间）",
                    suggestion="毛利率超常通常意味着收入/成本之一提取错误，请核对原文。",
                ))
        if rev and rev > 0:
            for acc in _FEE_SUBJECTS:
                v = _amt(data, acc, y)
                if v is not None and v > rev:
                    audit.add(_Finding(
                        id=f"NA-FEE-{acc}-{y}",
                        check="费用量级合理性",
                        severity="medium",
                        year=y, subject=acc, value=v, expected=rev,
                        gap=round(v - rev, 2),
                        message=f"{y} 年{acc} {_fmt(v)} 超过营业收入 {_fmt(rev)}",
                        suggestion="单项费用大于营收多为错位或串列，请核对原文。",
                    ))
        pbt = _amt(data, "利润总额", y)
        ni = _amt(data, "净利润", y)
        if pbt is not None and ni is not None and pbt * ni < 0 and abs(pbt) > 1:
            audit.add(_Finding(
                id=f"NA-SIGN-{y}",
                check="符号一致性",
                severity="medium",
                year=y, subject="净利润", value=ni, expected=pbt,
                message=f"{y} 年利润总额 {_fmt(pbt)} 与净利润 {_fmt(ni)} 符号相反",
                suggestion="两者符号应一致（税不会反号）；请核对所得税费用与两科目数字。",
            ))


# ── C. OCR 原文数字字面质检（初次扫描层）───────────────────────────────
# 常见 OCR 数字误读模式：形近字母混入（O/0、I·l/1、S/5、B/8、Z/2）、
# 数字粘连漏分隔、全角数字、千分位与小数点混用。

_OCR_LITERAL_PATTERNS: tuple = (
    (
        re.compile(r"\d[OILlSsBbZz][\d,\.]*|[\d,\.]*[OILl]\d"),
        "形近字母混入数字（O↔0、I/l↔1、S↔5、B↔8、Z↔2）",
        "medium",
    ),
    (re.compile(r"[０-９]+"), "全角数字", "low"),
    (re.compile(r"\d{13,}"), "超长连续数字（OCR 粘连/漏分隔）", "medium"),
    (
        re.compile(r"\d{1,3}(?:,\d{3})+\.\d+\.\d+|\b\d+\.\d+,\d+\b"),
        "千分位与小数点混用",
        "medium",
    ),
)
# 每个文件每种模式最多报告的样本数（避免刷屏）
_OCR_MAX_SAMPLES_PER_PATTERN = 3


def _audit_ocr_literals(ocr_texts: Optional[List[str]], audit: _Audit) -> None:
    """扫描 OCR/原文文本中可疑的数字字面，报告给人工核对。"""
    for file_idx, text in enumerate(ocr_texts or []):
        if not text:
            continue
        for pattern, reason, severity in _OCR_LITERAL_PATTERNS:
            reported = 0
            for m in pattern.finditer(text):
                if reported >= _OCR_MAX_SAMPLES_PER_PATTERN:
                    break
                literal = m.group(0)
                start = max(0, m.start() - 10)
                snippet = text[start:m.end() + 10].replace("\n", " ").strip()
                audit.add(_Finding(
                    id=f"NA-OCR-F{file_idx}-{reported}-{reason[:4]}",
                    check="OCR 数字字面质检",
                    severity=severity,
                    subject=f"原文片段",
                    message=f"第 {file_idx + 1} 份文档 OCR 文本发现{reason}：…{snippet}…",
                    suggestion=(
                        f"该字面为「{literal}」，OCR 误读会直接污染金额；"
                        "请对照原始扫描件逐位核对。"
                    ),
                ))
                reported += 1


def _add_ocr_context_note(
    data: FinancialData, ocr_texts: Optional[List[str]], audit: _Audit
) -> None:
    """数字来源含 OCR/扫描时给出整体风险上下文（提示核对重点）。"""
    quality = (data.parsed_meta or {}).get("data_quality") or {}
    ocr_involved = bool(ocr_texts) or bool(quality.get("ocr_used")) or (
        quality.get("text_layer") is False
    )
    if not ocr_involved:
        return
    audit.add(_Finding(
        id="NA-OCR-CONTEXT",
        check="数字来源风险",
        severity="low",
        subject="整体",
        message="本案例部分/全部数字来自 OCR 或扫描提取，数字误读风险高于文本层直读",
        suggestion=(
            "常见误读：0↔O、1↔l、8↔B、数字粘连、千分位与小数点错位；"
            "建议重点核对恒等式不闭合的年度与高风险科目。"
        ),
    ))


# ── 主入口 ─────────────────────────────────────────────────────────────

def audit_numbers(
    data: FinancialData, ocr_texts: Optional[List[str]] = None
) -> Dict[str, Any]:
    """对 FinancialData 全部数字做专项质检，返回结构化报告。

    ocr_texts 为导入时采集的各文件 OCR/原文文本——用于在「初次扫描层」
    检出数字字面可疑（形近字母、粘连、全角、分隔符混乱），配合最终数字的
    恒等式/错位归因形成双层防护：扫描错误要么在字面层被点名，
    要么在数字层被恒等式残差暴露。
    """
    audit = _Audit()
    years = sorted(data.years or [])

    for table in (data.income_statement, data.balance_sheet):
        for acc, yv in (table or {}).items():
            if isinstance(yv, dict) and any(v is not None for v in yv.values()):
                audit.subjects_checked += 1

    for y in years:
        _audit_pbt_identity(data, y, audit)
        _audit_balance_identity(data, y, audit)
    _audit_jumps(data, audit)
    _audit_plausibility(data, audit)
    _audit_ocr_literals(ocr_texts, audit)
    _add_ocr_context_note(data, ocr_texts, audit)

    severity_count = {"high": 0, "medium": 0, "low": 0}
    for f in audit.findings:
        severity_count[f.severity] = severity_count.get(f.severity, 0) + 1
    score = max(
        0,
        100 - sum(_SCORE_WEIGHTS.get(s, 0) * c for s, c in severity_count.items()),
    )
    grade = "高" if score >= 90 else ("中" if score >= 70 else "低")

    identity_count = len(audit.identities)
    if severity_count["high"] == 0 and severity_count["medium"] == 0:
        summary = (
            f"共核对 {audit.subjects_checked} 个科目 · {identity_count} 条恒等式 · "
            f"{len(years)} 个年度；未发现数字异常"
            + (f"（{severity_count['low']} 条低风险提示）" if severity_count["low"] else "")
            + "。"
        )
    else:
        summary = (
            f"共核对 {audit.subjects_checked} 个科目 · {identity_count} 条恒等式 · "
            f"{len(years)} 个年度；发现 {severity_count['high']} 处高风险 / "
            f"{severity_count['medium']} 处中风险 / {severity_count['low']} 处低风险，"
            "请按建议逐项核对原文数字。"
        )

    return {
        "engine": "numeric_audit",
        "version": 1,
        "score": score,
        "grade": grade,
        "summary": summary,
        "checked": {"subjects": audit.subjects_checked, "years": years},
        "identities": audit.identities,
        "findings": [f.to_dict() for f in audit.findings],
    }
