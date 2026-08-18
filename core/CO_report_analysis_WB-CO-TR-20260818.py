"""利润宝 · 经营预算分析（前世今生）DeepSeek 文案层。

定位：数字与结构全部来自确定性引擎（narrative/finance/diagnostic），
DeepSeek 只负责把事实清单改写成「艺康体小白版」文案（一句话结论 /
先说结论 / 最以前-中间-现在 / 现在要点 / 将来建议）。

三道防线保证「结果准确、文案精准」：
1. 事实清单（factsheet）：只喂确定性数字，提示词禁止编造数字；
2. 数字白名单校验：返回文案中出现事实清单外的数字 → 生成警告
   （只提示，绝不静默改数，与 numeric_audit 原则一致）；
3. 结构合并（merge）：按标题/指标名逐项覆盖文本字段，数字表格、
   折线图、附录等模板结构原样保留。

导出链路（第二稿与导出页）：
① 经营预算分析（DeepSeek）→ Word/PDF（同一份内容）
② 费用编制建议（DeepSeek）→ 测算模型 / 费用预算三表
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import replace
from typing import Any, List, Optional

from .narrative import StageNarrative, build_stage_narrative

# 文案长度上限（字符）：防 DeepSeek 跑飞，超长截断
_MAX_ONE_LINER = 200
_MAX_HEADLINE = 600
_MAX_STAGE_SUMMARY = 500
_MAX_BULLET = 200
_MAX_POINT_BODY = 500
_MAX_ACTION = 200
_MAX_STAGES = 5
_MAX_BULLETS = 6
_MAX_POINTS = 8
_MAX_ACTIONS = 10

_NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _clip(text: Any, limit: int) -> str:
    """清洗为单行文本并截断；非字符串/空返回空串。"""
    if not isinstance(text, str):
        return ""
    s = text.replace("\r", " ").replace("\n", " ").strip()
    return s[:limit]


def _clip_list(values: Any, item_limit: int, list_limit: int) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for v in values:
        s = _clip(v, item_limit)
        if s:
            out.append(s)
        if len(out) >= list_limit:
            break
    return out


def build_analysis_factsheet(data, diagnosis, decisions) -> dict:
    """构建确定性事实清单：DeepSeek 唯一允许引用的数字来源。"""
    narr: StageNarrative = build_stage_narrative(data, diagnosis, decisions)
    facts: dict[str, Any] = {
        "company_name": narr.company_name or data.company_name,
        "industry": narr.industry,
        "years": list(narr.years or []),
        "feasibility_score": round(float(getattr(diagnosis, "feasibility_score", 0) or 0), 2),
        "headline_reference": narr.headline,
        "one_liner_reference": narr.one_liner,
        "stages": [
            {
                "title": st.title,
                "years": list(st.years or []),
                "summary": st.summary,
                "bullets": list(st.bullets or []),
            }
            for st in narr.stages
        ],
        "now_points": [{"title": p.title, "body": p.body} for p in narr.now_points],
        "metric_judgments": {m.name: m.judgment for m in narr.now_metrics},
        "year_rows": [
            {
                "year": r.year,
                "revenue": r.revenue,
                "net_profit": r.net_profit,
                "gross_margin": r.gross_margin,
                "net_margin": r.net_margin,
                "one_liner": r.one_liner,
            }
            for r in narr.year_rows
        ],
        "findings": [
            {"title": f.title, "severity": f.severity, "fact": f.fact}
            for f in (getattr(diagnosis, "findings", None) or [])[:8]
        ],
        "decision_summaries": list(narr.decision_summaries or []),
        "future_actions_reference": list(narr.future_actions or []),
    }
    return facts


def _whitelist_from_facts(facts: dict) -> tuple[set, set]:
    """从事实清单提取允许出现的数字集合（含元/万元/亿元换算与常见舍入）。"""
    raw = json.dumps(facts, ensure_ascii=False)
    allowed: set[float] = set()
    for m in _NUM_RE.finditer(raw):
        try:
            allowed.add(float(m.group(0).replace(",", "")))
        except ValueError:
            continue
    # 金额换算：元 → 万元 / 亿元（DeepSeek 常改换单位叙述）
    extras: List[float] = []
    for v in list(allowed):
        av = abs(v)
        if av >= 1000:
            extras.append(round(v / 10_000, 2))
            extras.append(round(v / 10_000, 1))
            extras.append(round(v / 100_000_000, 2))
        extras.append(round(v, 1))
        extras.append(round(v))
    allowed.update(extras)
    years = set(int(y) for y in facts.get("years") or [])
    return allowed, years  # type: ignore[return-value]


def _is_allowed_number(n: float, allowed: set, years: set) -> bool:
    """数字是否在白名单内：精确/舍入匹配，或年份，或 0-100 的计数整数。"""
    if n in allowed:
        return True
    if round(n) in years:
        return True
    if n == int(n) and 0 <= n <= 100:
        return True  # 计数/序数（如「8 个数」「3 条建议」）
    # 相对容差：允许 DeepSeek 引用舍入值（如 12.34% 写成 12.3%）
    for a in allowed:
        if abs(a) >= 1 and abs(n - a) <= abs(a) * 0.005:
            return True
    return False


def validate_analysis_numbers(analysis: dict, facts: dict) -> List[str]:
    """扫描 DeepSeek 文案中的数字，标出事实清单之外的数字（只提示不拦截）。"""
    try:
        allowed, years = _whitelist_from_facts(facts)
    except Exception:
        return []
    texts: List[str] = []
    for key in ("one_liner", "headline", "stage_insight", "summary"):
        texts.append(_clip(analysis.get(key), 10_000))
    for st in analysis.get("stages") or []:
        if isinstance(st, dict):
            texts.append(_clip(st.get("summary"), 10_000))
            texts.extend(_clip_list(st.get("bullets"), 10_000, 20))
    for pt in analysis.get("now_points") or []:
        if isinstance(pt, dict):
            texts.append(_clip(pt.get("body"), 10_000))
    texts.extend(_clip_list(analysis.get("future_actions"), 10_000, 20))
    for j in (analysis.get("now_judgments") or {}).values():
        texts.append(_clip(j, 2_000))

    unknown: List[str] = []
    for text in texts:
        for m in _NUM_RE.finditer(text):
            try:
                n = float(m.group(0).replace(",", ""))
            except ValueError:
                continue
            if not _is_allowed_number(n, allowed, years):
                token = m.group(0)
                if token not in unknown:
                    unknown.append(token)
    if not unknown:
        return []
    return [
        "文案中出现事实清单外的数字（请人工核对，系统未改动）："
        + "、".join(unknown[:8])
    ]


def _stage_key(title: str) -> str:
    """阶段标题归一：最以前/中间/现在 三段式对应键。"""
    t = (title or "").strip()
    if t.startswith("最以前"):
        return "最以前"
    if t.startswith("中间"):
        return "中间"
    if t.startswith("现在"):
        return "现在"
    return t[:6]


def merge_narrative(base: StageNarrative, analysis: dict) -> StageNarrative:
    """把 DeepSeek 文案合并进规则叙事：只覆盖文本字段，结构/数字不动。

    缺项、超长、条数不匹配时保留规则引擎原文案（宁缺毋滥）。
    """
    if not isinstance(analysis, dict):
        return base
    narr = copy.deepcopy(base)

    one_liner = _clip(analysis.get("one_liner"), _MAX_ONE_LINER)
    if one_liner:
        narr.one_liner = one_liner
    headline = _clip(analysis.get("headline"), _MAX_HEADLINE)
    if headline:
        narr.headline = headline
    insight = _clip(analysis.get("stage_insight"), _MAX_ONE_LINER)
    if insight:
        narr.stage_insight = insight

    # 阶段故事：按 最以前/中间/现在 键对位合并
    ai_stages = {}
    for st in analysis.get("stages") or []:
        if isinstance(st, dict):
            key = _stage_key(_clip(st.get("title"), 60))
            if key:
                ai_stages[key] = st
    merged_stages = []
    for st in narr.stages:
        ai = ai_stages.get(_stage_key(st.title))
        if ai is None:
            merged_stages.append(st)
            continue
        summary = _clip(ai.get("summary"), _MAX_STAGE_SUMMARY)
        bullets = _clip_list(ai.get("bullets"), _MAX_BULLET, _MAX_BULLETS)
        merged_stages.append(
            replace(st, summary=summary or st.summary, bullets=bullets or st.bullets)
        )
    if merged_stages:
        narr.stages = merged_stages

    # 「现在」要点：按标题对位，只换正文
    ai_points = {}
    for pt in analysis.get("now_points") or []:
        if isinstance(pt, dict):
            t = _clip(pt.get("title"), 60)
            if t:
                ai_points[t] = _clip(pt.get("body"), _MAX_POINT_BODY)
    if ai_points:
        merged_points = []
        for p in narr.now_points:
            body = ai_points.get(p.title)
            merged_points.append(replace(p, body=body or p.body))
        narr.now_points = merged_points

    # 指标管理判断：只换判断列，小白解释/当前情况保持确定性
    judgments = analysis.get("now_judgments")
    if isinstance(judgments, dict) and judgments:
        merged_metrics = []
        for m in narr.now_metrics:
            j = _clip(judgments.get(m.name), 160)
            merged_metrics.append(replace(m, judgment=j or m.judgment))
        narr.now_metrics = merged_metrics

    actions = _clip_list(analysis.get("future_actions"), _MAX_ACTION, _MAX_ACTIONS)
    if actions:
        narr.future_actions = actions

    return narr


def normalize_analysis_payload(raw: dict) -> dict:
    """整形 DeepSeek 返回：统一字段名 + 截断 + 剔除空项（供 API 返回与合并）。"""
    stages = []
    for st in (raw.get("stages") or [])[:_MAX_STAGES]:
        if not isinstance(st, dict):
            continue
        stages.append(
            {
                "title": _clip(st.get("title"), 60),
                "summary": _clip(st.get("summary"), _MAX_STAGE_SUMMARY),
                "bullets": _clip_list(st.get("bullets"), _MAX_BULLET, _MAX_BULLETS),
            }
        )
    points = []
    for pt in (raw.get("now_points") or [])[:_MAX_POINTS]:
        if not isinstance(pt, dict):
            continue
        points.append(
            {"title": _clip(pt.get("title"), 60), "body": _clip(pt.get("body"), _MAX_POINT_BODY)}
        )
    judgments = {}
    if isinstance(raw.get("now_judgments"), dict):
        for k, v in raw["now_judgments"].items():
            j = _clip(v, 160)
            if j and isinstance(k, str):
                judgments[k.strip()[:30]] = j
    return {
        "one_liner": _clip(raw.get("one_liner"), _MAX_ONE_LINER),
        "headline": _clip(raw.get("headline"), _MAX_HEADLINE),
        "stage_insight": _clip(raw.get("stage_insight"), _MAX_ONE_LINER),
        "stages": stages,
        "now_points": points,
        "now_judgments": judgments,
        "future_actions": _clip_list(raw.get("future_actions"), _MAX_ACTION, _MAX_ACTIONS),
        "ai_summary": _clip(raw.get("summary"), 400),
    }


def has_analysis_content(payload: Optional[dict]) -> bool:
    """分析是否有效（至少有结论文案或阶段故事）。"""
    if not isinstance(payload, dict):
        return False
    return bool(
        payload.get("one_liner")
        or payload.get("headline")
        or payload.get("stages")
        or payload.get("future_actions")
    )
