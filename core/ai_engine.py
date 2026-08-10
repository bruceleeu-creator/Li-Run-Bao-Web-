"""利润宝 · 可选大模型接口（S5，可选增强层）。

OpenAI 兼容协议（/v1/chat/completions）。未配置时绝不触网；调用失败或超时
抛异常，由调用方静默回退到本地规则引擎并展示提示。核心计算与离线闭环不依赖本模块。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from . import finance as fin
from .diagnostic import (
    CATEGORY_REALITY,
    CATEGORY_STRUCTURE,
    CATEGORY_TAX,
    Finding,
    Option,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
)
from .models import FinancialData

# 默认超时（秒）：保守设置，避免阻塞主流程
DEFAULT_TIMEOUT = 8.0
# 诊断发现 / 互动出题需要更长输出与等待
DIAGNOSIS_MAX_TOKENS = 8192
OPTIONS_MAX_TOKENS = 2048
DISCOVER_MAX_FINDINGS = 10

_COMPLIANCE_SYSTEM = (
    "你是面向中国大陆的企业财税与经营顾问。"
    "所有建议必须属于合法税务筹划与合法经营管理范畴"
    "（研发费用加计扣除、限额内据实扣除、优惠适用核验、业务模式与回款优化等）。"
    "严禁任何虚开发票、隐匿收入、虚构成本或教唆违法表述。"
    "金额单位统一为元；比例用百分比数值（如 12.5 表示 12.5%）。"
    "只输出 JSON，不要 markdown 代码围栏，不要额外解释。"
)


class AIEngineError(Exception):
    """AI 引擎错误基类。"""


class AICompletionError(AIEngineError):
    """服务已响应，但 completion 未以可采纳状态结束。"""

    code = "AI_COMPLETION_INCOMPLETE"

    def __init__(self, finish_reason: str, message: str):
        super().__init__(message)
        self.finish_reason = finish_reason


class AIRequestError(AIEngineError):
    """AI 请求、传输或响应协议错误。"""

    def __init__(self, code: str, message: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class AIChatResult:
    """一次 OpenAI 兼容聊天完成的可审计结果。"""

    content: str
    finish_reason: str
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    model: str


class AIEngine:
    """OpenAI 兼容大模型接口（可选增强）。

    - 未配置 api_key 或 base_url 时，is_available() 返回 False，绝不触网
    - 配置后调用失败/超时抛 AIEngineError，由调用方静默回退
    """

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout = float(timeout)
        self._last_error: str = ""

    def is_available(self) -> bool:
        """是否已配置可用（base_url + api_key + model 三者齐备）。"""
        return bool(self.base_url and self.api_key and self.model)

    @property
    def last_error(self) -> str:
        return self._last_error

    def _chat_result(
        self,
        messages: List[Dict],
        max_tokens: int = 400,
        extra: Optional[Dict] = None,
    ) -> AIChatResult:
        """调用 /v1/chat/completions，返回完整结束元数据。"""
        if not self.is_available():
            raise AIRequestError(
                "AI_NOT_CONFIGURED",
                "大模型未配置，已回退本地规则引擎。",
                retryable=False,
            )
        try:
            import requests
        except ImportError as e:
            raise AIRequestError(
                "AI_CLIENT_UNAVAILABLE",
                "AI 客户端不可用，已回退本地规则引擎。",
                retryable=False,
            ) from e

        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            # deepseek-v4-flash 对复杂任务会产生长推理（reasoning_content），
            # 会占满 max_tokens 导致正文 content 为 0（finish_reason=length）。
            # 本产品调用均为确定性提取/整理/润色任务，默认禁用思考，直接输出正文。
            # 个别需要推理的场景可在 extra 中传 {"thinking": {...}} 覆盖。
            "thinking": {"type": "disabled"},
        }
        if extra:
            # 允许调用方覆盖 thinking（如需要推理的生成任务）
            merged = dict(payload)
            merged.update(extra)
            payload = merged
        response_received = False
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            response_received = True
            # 兼容测试 mock（无 status_code 属性时跳过 401 分支）
            status_code = getattr(resp, "status_code", None)
            if status_code in (401, 403):
                # 密钥无效/无权限：给出可操作的明确提示，避免用户误以为网络问题
                raise AIRequestError(
                    "AI_AUTH_FAILED",
                    "API Key 无效或已失效，请到「设置」中检查并重新保存（提示：密钥前后不要有空格）。",
                    retryable=False,
                )
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            finish_reason = str(choice.get("finish_reason") or "")
            tool_requested = bool(extra and extra.get("tools"))
            if finish_reason == "length":
                raise AICompletionError(
                    finish_reason,
                    "AI 输出被截断（达到输出上限），请提高 max_tokens 后重试。",
                )
            if finish_reason == "content_filter":
                raise AICompletionError(
                    finish_reason,
                    "AI 输出因内容过滤而未完成，请调整提示后重试。",
                )
            if finish_reason == "insufficient_system_resource":
                raise AICompletionError(
                    finish_reason,
                    "AI 系统资源不足，未生成完整输出，请稍后重试。",
                )
            if finish_reason == "tool_calls" and not tool_requested:
                raise AICompletionError(
                    finish_reason,
                    "AI 返回工具调用，但当前请求未声明工具调用。",
                )
            if finish_reason not in {"stop", "tool_calls"}:
                raise AICompletionError(
                    finish_reason,
                    f"AI 未正常完成输出（finish_reason={finish_reason or '缺失'}）。",
                )

            message = choice["message"]
            content = message.get("content") or ""
            usage = data.get("usage") or {}
            return AIChatResult(
                content=str(content).strip(),
                finish_reason=finish_reason,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                model=str(data.get("model") or self.model),
            )
        except AIEngineError as e:
            self._last_error = str(e)
            raise
        except Exception as e:
            self._last_error = type(e).__name__
            if isinstance(e, (requests.Timeout, TimeoutError)):
                code, message, retryable = "AI_TIMEOUT", "大模型请求超时，请稍后重试。", True
            elif isinstance(e, requests.ConnectionError):
                code, message, retryable = (
                    "AI_CONNECTION_FAILED",
                    "无法连接大模型服务，请稍后重试。",
                    True,
                )
            elif isinstance(e, requests.HTTPError):
                code, message, retryable = "AI_HTTP_ERROR", "大模型服务暂时不可用。", True
            elif response_received:
                code, message, retryable = (
                    "AI_RESPONSE_INVALID",
                    "大模型响应协议无效，未采纳输出。",
                    False,
                )
            else:
                code, message, retryable = (
                    "AI_CLIENT_ERROR",
                    "大模型客户端发生内部错误，未采纳输出。",
                    False,
                )
            raise AIRequestError(
                code,
                message,
                retryable=retryable,
            ) from e

    def _chat(self, messages: List[Dict], max_tokens: int = 400) -> str:
        """兼容既有内部调用，仍只返回 content 字符串。"""
        return self._chat_result(messages, max_tokens=max_tokens).content

    def chat_result(
        self,
        user_prompt: str,
        system_prompt: str = "",
        max_tokens: int = 400,
        extra: Optional[Dict] = None,
    ) -> AIChatResult:
        """通用对话接口，返回内容及服务端结束原因、用量元数据。"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        return self._chat_result(messages, max_tokens=max_tokens, extra=extra)

    def chat(self, user_prompt: str, system_prompt: str = "", max_tokens: int = 400) -> str:
        """兼容接口：返回 AI 文本内容。"""
        return self.chat_result(
            user_prompt, system_prompt=system_prompt, max_tokens=max_tokens
        ).content

    def refine_report(self, text: str, max_tokens: int = 200) -> str:
        """润色报告文本（如第二稿操作细节）。

        失败抛 AIEngineError；调用方应捕获并回退到原文本。
        """
        if not text:
            return text
        system_prompt = (
            "你是财税筹划报告润色助手。请在不改变事实与建议合法性的前提下，"
            "将给定文本改写为更专业、简洁的中文表述，保留所有风险提示。"
            "严禁出现虚开发票、隐匿收入、虚构成本等任何违规筹划表述。"
        )
        return self._chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": text}],
            max_tokens=max_tokens,
        )

    # ── 财务上下文压缩（供诊断发现 / 互动出题） ──────────────────────────

    @staticmethod
    def compact_financial_context(
        data: FinancialData,
        ocr_texts: Optional[Sequence[str]] = None,
        max_ocr_chars: int = 4000,
    ) -> str:
        """把多年财报压成模型可读的短文本（确定性，无 LLM）。"""
        years = sorted(data.years or [])
        lines = [
            f"企业：{data.company_name or '未命名'}",
            f"行业：{data.industry or '未指定'}",
            f"年度：{', '.join(str(y) for y in years) or '无'}",
            "【利润表关键科目 元】",
        ]
        income_keys = [
            "营业收入", "营业成本", "税金及附加", "销售费用", "管理费用",
            "研发费用", "财务费用", "利润总额", "所得税费用", "净利润",
        ]
        for acc in income_keys:
            series = data.income_statement.get(acc) or {}
            parts = [f"{y}={float(series.get(y, 0.0) or 0.0):.0f}" for y in years]
            if any(not p.endswith("=0") for p in parts):
                lines.append(f"{acc}: " + ", ".join(parts))

        if data.balance_sheet:
            lines.append("【资产负债表关键科目 元】")
            for acc in ["资产总额", "负债总额", "所有者权益", "应收账款", "存货", "固定资产"]:
                series = data.balance_sheet.get(acc) or {}
                parts = [f"{y}={float(series.get(y, 0.0) or 0.0):.0f}" for y in years]
                if any(not p.endswith("=0") for p in parts):
                    lines.append(f"{acc}: " + ", ".join(parts))

        if data.account_balances:
            lines.append("【科目余额表（最新年）元】")
            latest = years[-1] if years else None
            if latest is not None:
                for acc, series in sorted(data.account_balances.items()):
                    val = float((series or {}).get(latest, 0.0) or 0.0)
                    lines.append(f"{acc}@{latest}={val:.0f}")

        if years:
            lines.append("【计算指标（确定性）】")
            for y in years:
                ind = fin.compute_year_indicators(data, y)
                lines.append(
                    f"{y}: 营收={float(ind.get('营业收入', 0) or 0):.0f}, "
                    f"毛利率={ind['毛利率']['value']}%, 净利率={ind['净利率']['value']}%, "
                    f"增值税税负率(估算)={ind['增值税税负率']['value']}%, "
                    f"所得税税负率={ind['所得税税负率']['value']}%, "
                    f"销管财费用率="
                    f"{ind['销售费用率']['value']+ind['管理费用率']['value']+ind['财务费用率']['value']:.2f}%"
                )

        if ocr_texts:
            blob = "\n---\n".join(str(t)[:1500] for t in ocr_texts if t)
            if blob.strip():
                lines.append("【审计报告/OCR 摘录（可能含识别噪声）】")
                lines.append(blob[:max_ocr_chars])
        return "\n".join(lines)

    def discover_findings(
        self,
        data: FinancialData,
        existing: Optional[Sequence[Finding]] = None,
        ocr_texts: Optional[Sequence[str]] = None,
        max_new: int = DISCOVER_MAX_FINDINGS,
    ) -> List[Finding]:
        """DeepSeek 补充发现更多问题（与规则引擎结果合并用）。

        返回 AI 新增 Finding 列表；不得覆盖规则发现 id。失败抛 AIEngineError。
        """
        existing = list(existing or [])
        existing_ids = {f.id for f in existing}
        existing_titles = {f.title for f in existing}
        existing_brief = "\n".join(
            f"- [{f.id}] {f.title}（{f.severity}/{f.category}）：{f.fact[:120]}"
            for f in existing[:20]
        ) or "（规则引擎暂无发现）"

        system_prompt = (
            _COMPLIANCE_SYSTEM
            + "任务：在规则引擎已有发现之外，继续挖掘经营、税负、费用结构、回款、真实性与合规管理问题。"
            "优先输出「规则未覆盖」或「可讲清阶段故事」的发现。"
            "每条发现必须可被给定数字支撑，禁止编造不存在的科目金额。"
            "category 只能是：税负率 / 成本费用结构 / 真实性风险。"
            "severity 只能是：高 / 中 / 低。"
            "返回 JSON 对象：{\"findings\":[...]}，每项字段："
            "id,title,category,severity,fact,benchmark,suggestion,"
            "current_value,target_value,unit,"
            "options:[{label,name,description,target_value,est_saving,cost_saving,"
            "tax_saving,tax_impact,feasibility,risk_level,action_note}]。"
            "options 必须恰好 A/B/C 三项：A 积极落地、B 分阶段、C 暂维持/备查。"
            f"最多 {max_new} 条；id 使用大写蛇形且以 AI_ 开头（如 AI_CASH_PRESSURE）。"
        )
        user_prompt = (
            "以下是企业财务数据与规则引擎已发现的问题。请补充更多诊断发现。\n\n"
            f"{self.compact_financial_context(data, ocr_texts=ocr_texts)}\n\n"
            f"【规则引擎已有发现】\n{existing_brief}\n\n"
            "请输出 JSON：{\"findings\":[...]}。"
        )
        content = self._chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=DIAGNOSIS_MAX_TOKENS,
        )
        raw_items = self._parse_findings_payload(content)
        findings: List[Finding] = []
        for item in raw_items:
            if len(findings) >= max_new:
                break
            raw_id = str(item.get("id", "")).strip().upper()
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            # 与规则引擎 id 冲突（含未加 AI_ 前缀的原 id）直接跳过
            bare = raw_id[3:] if raw_id.startswith("AI_") else raw_id
            if raw_id in existing_ids or bare in existing_ids:
                continue
            if title in existing_titles or any(title in t or t in title for t in existing_titles if t):
                continue
            if not raw_id.startswith("AI_"):
                fid = "AI_" + re.sub(r"[^A-Z0-9_]+", "_", raw_id or "FINDING").strip("_")
            else:
                fid = re.sub(r"[^A-Z0-9_]+", "_", raw_id).strip("_")
            if not fid.startswith("AI_"):
                fid = "AI_" + fid
            # 与已生成 AI 发现去重
            if fid in {f.id for f in findings} or title in {f.title for f in findings}:
                continue
            finding = self._finding_from_dict(item, default_id=fid)
            if finding is None:
                continue
            # 选项不足则补全失败 → 跳过该条（调用方可再 generate_options）
            if len(finding.options) != 3:
                try:
                    finding.options = self.generate_options(
                        finding, data=data, ocr_texts=ocr_texts
                    )
                except AIEngineError:
                    continue
            findings.append(finding)
            existing_ids.add(finding.id)
            existing_titles.add(finding.title)
        return findings

    def generate_options(
        self,
        finding: Finding,
        data: Optional[FinancialData] = None,
        ocr_texts: Optional[Sequence[str]] = None,
        prior_decisions: Optional[Sequence[dict]] = None,
        strategy_notes: Optional[Sequence[str]] = None,
    ) -> List[Option]:
        """根据发现生成 A/B/C 三个选项（增强版）。

        返回的 Option 列表会替换规则引擎生成的默认选项。
        失败抛 AIEngineError；调用方应捕获并回退到 finding.options。
        """
        system_prompt = (
            _COMPLIANCE_SYSTEM
            + "任务：为一条诊断发现生成互动出题用的 A/B/C 三个可量化选项。"
            "A=积极落地（目标更进取，动作具体），B=分阶段平衡，C=暂维持/仅备查（须提示风险）。"
            "每个选项必须能让非财务老板看懂「选了以后发生什么」。"
            "返回 JSON 数组，每项字段："
            "label,name,description,target_value,est_saving,cost_saving,tax_saving,"
            "tax_impact,feasibility,risk_level,action_note。"
            "feasibility/risk_level 取 高/中/低；label 必须是 A/B/C。"
            "est_saving 为净影响（元）≈ cost_saving + tax_saving - tax_impact；未知则填 0。"
        )
        ctx = ""
        if data is not None:
            ctx = "\n【企业数据摘要】\n" + self.compact_financial_context(
                data, ocr_texts=ocr_texts, max_ocr_chars=1500
            )
        prior = ""
        if prior_decisions:
            prior = "\n【此前互动决策】\n" + "\n".join(
                f"- {d.get('finding_title', d.get('finding_id', ''))} → "
                f"{d.get('option_label', '')}. {d.get('option_name', '')}"
                for d in prior_decisions[-8:]
            )
        strategy = ""
        if strategy_notes:
            strategy = "\n【用户战略意图】\n" + "\n".join(f"- {s}" for s in strategy_notes[-6:] if s)

        rule_opts = ""
        if finding.options:
            rule_opts = "\n【规则引擎参考选项（可改写优化，勿照抄空话）】\n" + "\n".join(
                f"{o.label}. {o.name} | 目标={o.target_value} | {o.description[:100]}"
                for o in finding.options
            )

        user_prompt = (
            f"发现ID：{finding.id}\n"
            f"标题：{finding.title}\n"
            f"类别：{finding.category}；严重度：{finding.severity}\n"
            f"事实：{finding.fact}\n"
            f"行业对标：{finding.benchmark}\n"
            f"初稿建议：{finding.suggestion}\n"
            f"当前值：{finding.current_value} {finding.unit}；参考目标：{finding.target_value}\n"
            f"{rule_opts}{ctx}{prior}{strategy}\n"
            "请生成恰好 3 个选项的 JSON 数组。"
        )
        content = self._chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            max_tokens=OPTIONS_MAX_TOKENS,
        )
        return self._parse_options_json(content, finding)

    def enrich_interaction_question(
        self,
        finding: Finding,
        data: Optional[FinancialData] = None,
        prior_decisions: Optional[Sequence[dict]] = None,
        strategy_notes: Optional[Sequence[str]] = None,
    ) -> Finding:
        """优化互动题面：重写 fact/suggestion 为「老板能懂的提问」，并刷新 A/B/C。

        失败抛 AIEngineError；调用方回退原 finding。
        """
        options = self.generate_options(
            finding,
            data=data,
            prior_decisions=prior_decisions,
            strategy_notes=strategy_notes,
        )
        system_prompt = (
            _COMPLIANCE_SYSTEM
            + "任务：把诊断发现改写成互动环节的「出题文案」。"
            "返回 JSON 对象：{question_title, plain_fact, why_it_matters, suggestion_prompt}。"
            "plain_fact 用小白话讲清现状数字；why_it_matters 讲清楚不管会怎样；"
            "suggestion_prompt 引导用户在 A/B/C 中选择，并允许补充战略意图。"
            "不要输出选项本身。"
        )
        user_prompt = (
            f"标题：{finding.title}\n事实：{finding.fact}\n对标：{finding.benchmark}\n"
            f"建议：{finding.suggestion}\n当前值：{finding.current_value}{finding.unit}\n"
            f"选项概要：" + "；".join(f"{o.label}.{o.name}" for o in options)
        )
        if strategy_notes:
            user_prompt += "\n用户意图：" + "；".join(strategy_notes[-3:])
        content = self._chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1200,
        )
        payload = self._parse_json_object(content)
        plain = str(payload.get("plain_fact") or finding.fact).strip()
        why = str(payload.get("why_it_matters") or "").strip()
        suggest = str(payload.get("suggestion_prompt") or finding.suggestion).strip()
        qtitle = str(payload.get("question_title") or finding.title).strip()
        fact = plain if not why else f"{plain} 影响：{why}"
        # 不改 id/category/数值字段，只优化可读文案与选项
        finding.title = qtitle or finding.title
        finding.fact = fact or finding.fact
        finding.suggestion = suggest or finding.suggestion
        finding.options = options
        return finding

    @staticmethod
    def _parse_json_object(content: str) -> dict:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise AIEngineError(f"AI 返回非 JSON 对象：{content[:120]}")
        try:
            obj = json.loads(content[start:end + 1])
        except Exception as e:
            raise AIEngineError(f"AI 返回 JSON 解析失败：{e}") from e
        if not isinstance(obj, dict):
            raise AIEngineError("AI 返回 JSON 不是对象")
        return obj

    @staticmethod
    def _parse_findings_payload(content: str) -> List[dict]:
        """解析 {\"findings\":[...]} 或直接数组。"""
        text = content.strip()
        # 兼容 ```json 围栏
        if "```" in text:
            text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
        try:
            if "[" in text and (text.find("[") < text.find("{") or "{" not in text):
                start, end = text.find("["), text.rfind("]")
                arr = json.loads(text[start:end + 1])
                return [x for x in arr if isinstance(x, dict)]
            obj = AIEngine._parse_json_object(text)
            arr = obj.get("findings") or obj.get("items") or []
            if not isinstance(arr, list):
                raise AIEngineError("findings 字段不是数组")
            return [x for x in arr if isinstance(x, dict)]
        except AIEngineError:
            raise
        except Exception as e:
            raise AIEngineError(f"解析 findings 失败：{e}") from e

    @staticmethod
    def _normalize_severity(v: str) -> str:
        s = str(v or "").strip()
        if s in (SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW):
            return s
        if "高" in s:
            return SEVERITY_HIGH
        if "低" in s:
            return SEVERITY_LOW
        return SEVERITY_MEDIUM

    @staticmethod
    def _normalize_category(v: str) -> str:
        s = str(v or "").strip()
        if s in (CATEGORY_TAX, CATEGORY_STRUCTURE, CATEGORY_REALITY):
            return s
        if "税" in s:
            return CATEGORY_TAX
        if "真实" in s or "风险" in s:
            return CATEGORY_REALITY
        return CATEGORY_STRUCTURE

    @staticmethod
    def _normalize_risk(v: str) -> str:
        s = str(v or "").strip()
        if s in (RISK_HIGH, RISK_MEDIUM, RISK_LOW):
            return s
        if "高" in s:
            return RISK_HIGH
        if "低" in s:
            return RISK_LOW
        return RISK_MEDIUM

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        """从 AI 字段尽量解析数字；文字描述/空值回退 default，避免整轮发现被一条脏字段打挂。"""
        if value is None:
            return default
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default
        text = str(value).strip()
        if not text:
            return default
        # 抽首个数字片段（支持 12.5 / -3% / 1,200.00 元）
        cleaned = text.replace(",", "").replace("，", "").replace("%", "")
        m = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
        if not m:
            return default
        try:
            return float(m.group(0))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _finding_from_dict(cls, item: dict, default_id: str) -> Optional[Finding]:
        title = str(item.get("title", "")).strip()
        if not title:
            return None
        options: List[Option] = []
        for o in item.get("options") or []:
            if not isinstance(o, dict):
                continue
            label = str(o.get("label", "")).strip().upper()
            if label not in ("A", "B", "C"):
                continue
            cost_s = cls._safe_float(o.get("cost_saving"), 0.0)
            tax_s = cls._safe_float(o.get("tax_saving"), 0.0)
            tax_i = cls._safe_float(o.get("tax_impact"), 0.0)
            est = cls._safe_float(o.get("est_saving"), 0.0)
            if est == 0.0 and (cost_s or tax_s or tax_i):
                est = cost_s + tax_s - tax_i
            options.append(Option(
                label=label,
                name=str(o.get("name", "")).strip() or f"方案{label}",
                description=str(o.get("description", "")).strip(),
                target_value=cls._safe_float(o.get("target_value"), 0.0),
                tax_rate=cls._safe_float(o.get("tax_rate"), 0.25),
                est_saving=est,
                cost_saving=cost_s,
                tax_saving=tax_s,
                tax_impact=tax_i,
                feasibility=str(o.get("feasibility", "中")).strip() or "中",
                risk_level=cls._normalize_risk(o.get("risk_level", "低")),
                action_note=str(o.get("action_note", "")).strip(),
                deduction_rate=cls._safe_float(o.get("deduction_rate"), 0.0),
            ))
        # 保证 label 顺序 A/B/C
        by_label = {o.label: o for o in options}
        ordered = [by_label[k] for k in ("A", "B", "C") if k in by_label]
        unit = str(item.get("unit", "%") or "%").strip()
        if unit not in ("%", "元"):
            unit = "%" if "率" in title or "%" in str(item.get("unit", "")) else "元"
        return Finding(
            id=default_id,
            title=title,
            category=cls._normalize_category(item.get("category", CATEGORY_STRUCTURE)),
            severity=cls._normalize_severity(item.get("severity", SEVERITY_MEDIUM)),
            fact=str(item.get("fact", "")).strip() or title,
            benchmark=str(item.get("benchmark", "")).strip() or "行业/管理经验对标",
            suggestion=str(item.get("suggestion", "")).strip() or "建议结合经营实际落地优化。",
            options=ordered,
            current_value=cls._safe_float(item.get("current_value"), 0.0),
            target_value=cls._safe_float(item.get("target_value"), 0.0),
            unit=unit,
            status="pending",
        )

    @staticmethod
    def _parse_options_json(content: str, finding: Finding) -> List[Option]:
        """解析 AI 返回的 JSON 选项列表；解析失败抛 AIEngineError。"""
        # 截取首个 JSON 数组片段
        start = content.find("[")
        end = content.rfind("]")
        if start < 0 or end < 0 or end <= start:
            raise AIEngineError(f"AI 返回非 JSON 数组：{content[:120]}")
        try:
            arr = json.loads(content[start:end + 1])
        except Exception as e:
            raise AIEngineError(f"AI 返回 JSON 解析失败：{e}") from e
        options: List[Option] = []
        for item in arr:
            label = str(item.get("label", "")).strip().upper()
            if label not in ("A", "B", "C"):
                continue
            cost_s = AIEngine._safe_float(item.get("cost_saving"), 0.0)
            tax_s = AIEngine._safe_float(item.get("tax_saving"), 0.0)
            tax_i = AIEngine._safe_float(item.get("tax_impact"), 0.0)
            est = AIEngine._safe_float(item.get("est_saving"), 0.0)
            if est == 0.0 and (cost_s or tax_s or tax_i):
                est = cost_s + tax_s - tax_i
            options.append(Option(
                label=label,
                name=str(item.get("name", "")).strip() or f"方案{label}",
                description=str(item.get("description", "")).strip(),
                target_value=AIEngine._safe_float(item.get("target_value"), finding.target_value),
                tax_rate=AIEngine._safe_float(item.get("tax_rate"), 0.25),
                est_saving=est,
                cost_saving=cost_s,
                tax_saving=tax_s,
                tax_impact=tax_i,
                feasibility=str(item.get("feasibility", "中")).strip() or "中",
                risk_level=str(item.get("risk_level", "低")).strip() or "低",
                action_note=str(item.get("action_note", "")).strip(),
                deduction_rate=AIEngine._safe_float(item.get("deduction_rate"), 0.0),
            ))
        by_label = {o.label: o for o in options}
        ordered = [by_label[k] for k in ("A", "B", "C") if k in by_label]
        if len(ordered) != 3:
            raise AIEngineError(f"AI 返回选项数非 3 个：{len(ordered)}")
        return ordered


def safe_call(ai_engine: Optional[AIEngine], method_name: str, *args, **kwargs):
    """安全调用 AI 方法；失败时返回 (None, error_message)，不抛出。"""
    if ai_engine is None or not ai_engine.is_available():
        return None, "大模型未配置，已使用本地规则引擎。"
    method = getattr(ai_engine, method_name, None)
    if method is None:
        return None, f"AI 方法不存在：{method_name}"
    try:
        return method(*args, **kwargs), ""
    except AIEngineError as e:
        return None, str(e)
    except Exception as e:
        return None, f"AI 调用异常：{type(e).__name__}: {e}"
