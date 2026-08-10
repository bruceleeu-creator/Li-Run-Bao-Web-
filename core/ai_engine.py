"""利润宝 · 可选大模型接口（S5，可选增强层）。

OpenAI 兼容协议（/v1/chat/completions）。未配置时绝不触网；调用失败或超时
抛异常，由调用方静默回退到本地规则引擎并展示提示。核心计算与离线闭环不依赖本模块。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional

from .diagnostic import Finding, Option

# 默认超时（秒）：保守设置，避免阻塞主流程
DEFAULT_TIMEOUT = 8.0


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

    def generate_options(self, finding: Finding) -> List[Option]:
        """根据发现生成 A/B/C 三个选项（增强版）。

        返回的 Option 列表会替换规则引擎生成的默认选项。
        失败抛 AIEngineError；调用方应捕获并回退到 finding.options。
        """
        system_prompt = (
            "你是财税筹划专家。根据给定诊断发现，生成 A/B/C 三个可量化选项，"
            "分别对应激进优化 / 平衡 / 保守。每个选项需含 name、description、"
            "target_value、est_saving、feasibility、risk_level。"
            "所有建议必须属于合法税务筹划范畴。"
            "返回 JSON 数组，每项含 label/name/description/target_value/est_saving/feasibility/risk_level。"
        )
        user_prompt = (
            f"发现标题：{finding.title}\n"
            f"事实：{finding.fact}\n"
            f"行业对标：{finding.benchmark}\n"
            f"当前值：{finding.current_value} {finding.unit}\n"
            f"请生成三个选项。"
        )
        content = self._chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_prompt}],
            max_tokens=600,
        )
        return self._parse_options_json(content, finding)

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
            options.append(Option(
                label=label,
                name=str(item.get("name", "")).strip(),
                description=str(item.get("description", "")).strip(),
                target_value=float(item.get("target_value", 0.0) or 0.0),
                tax_rate=0.25,
                est_saving=float(item.get("est_saving", 0.0) or 0.0),
                feasibility=str(item.get("feasibility", "中")).strip(),
                risk_level=str(item.get("risk_level", "低")).strip(),
                action_note=str(item.get("action_note", "")).strip(),
            ))
        if len(options) != 3:
            raise AIEngineError(f"AI 返回选项数非 3 个：{len(options)}")
        return options


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
