"""OpenAI protocol adapter using the official SDK.

ch05: 单条 system 消息（stable 在前 + environment 在后），
reminder 追加尾部 user 消息；缓存用量从 cached_tokens 解析。
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import openai

from csycode.config import ProviderConfig

from . import Request, StreamEvent, ToolCall, Usage


class OpenAIProvider:
    """Provider implementation for the OpenAI protocol."""

    def __init__(self, cfg: ProviderConfig) -> None:
        api_key = cfg.resolve_api_key()
        if not api_key or api_key.startswith("${"):
            raise ValueError(
                f"Provider '{cfg.name}': API key 为空或未设置。"
                f"请在 config.yaml 中配置 api_key 或设置对应的环境变量。"
            )
        client_kwargs: dict = {"api_key": api_key}
        if cfg.base_url:
            client_kwargs["base_url"] = cfg.base_url
        self._client = openai.AsyncOpenAI(**client_kwargs)
        self._model = cfg.model
        self._name = cfg.name
        self._is_openai: bool = (
            cfg.base_url is None
            or "api.openai.com" in (cfg.base_url or "")
        )
        self._max_tokens: int = 8192  # 默认值，可被 set_max_output_tokens 覆盖
        # thinking is ignored for OpenAI protocol

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    def set_max_output_tokens(self, tokens: int) -> None:
        """动态调整 max_tokens（用于 max_tokens 恢复）。"""
        self._max_tokens = tokens

    # ── 消息格式转换 ───────────────────────────────────────────────

    def _convert_messages(self, req: Request) -> list[dict]:
        """将 Request 转为 OpenAI SDK 的 messages 格式。

        ch05 改动：
        - 首条 system 消息 = stable + (environment 非空则 "\n\n" + environment)
        - reminder 非空则追加尾部 user 消息
        """
        result: list[dict] = []

        # 构造 system 消息：stable 在前
        system_content = req.system.stable
        if req.system.environment:
            system_content = (
                system_content + "\n\n" + req.system.environment
                if system_content
                else req.system.environment
            )
        if system_content:
            result.append({"role": "system", "content": system_content})

        # 映射对话历史
        for m in req.messages:
            if m.tool_calls:
                # Assistant 消息带工具调用
                entry: dict = {"role": "assistant", "content": m.content or None}
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in m.tool_calls
                ]
                result.append(entry)
            elif m.tool_call_id:
                # 工具执行结果消息
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id,
                        "content": m.content,
                    }
                )
            else:
                # 普通文本消息
                result.append({"role": m.role, "content": m.content})

        # reminder 追加为尾部 user 消息
        if req.reminder:
            result.append({"role": "user", "content": req.reminder})

        return result

    # ── 构造 OpenAI 格式的 tools ────────────────────────────────────

    @staticmethod
    def _to_openai_tools(tools: list[dict]) -> list[dict]:
        """将工具定义转为 OpenAI 格式（若尚未转换）。

        已是 OpenAI 格式（含 ``function`` 键）则原样返回；
        Anthropic 格式（含 ``input_schema`` 键）则转换。
        """
        result: list[dict] = []
        for t in tools:
            if "function" in t:
                result.append(t)
            elif "input_schema" in t:
                result.append(
                    {
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": t.get(
                                "input_schema", {"type": "object", "properties": {}}
                            ),
                        },
                    }
                )
            else:
                result.append(t)
        return result

    # ── 主入口 ──────────────────────────────────────────────────────

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        """Stream a response from OpenAI, yielding text deltas and a done/err event.

        ch05 改动：
        - system 消息 = stable + environment 拼接为单条。
        - reminder 追加为尾部 user 消息。
        - 缓存用量从 prompt_tokens_details.cached_tokens 解析。
        """
        messages = self._convert_messages(req)

        create_params: dict = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "stream": True,
        }
        # stream_options 是 OpenAI 特有参数，第三方 API（如 DeepSeek）可能不支持
        if self._is_openai:
            create_params["stream_options"] = {"include_usage": True}

        if req.tools:
            create_params["tools"] = self._to_openai_tools(req.tools)

        # 累积 tool_calls: key 是 tool_call index
        pending_tools: dict[int, dict] = {}

        # 缓存用量与结束原因
        cache_read = 0
        input_tokens = 0
        output_tokens = 0
        finish_reason: str = ""

        try:
            stream = await self._client.chat.completions.create(**create_params)

            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta

                    # 文本增量
                    if delta.content:
                        yield StreamEvent(text=delta.content)

                    # 工具调用增量
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in pending_tools:
                                pending_tools[idx] = {
                                    "id": tc_delta.id or "",
                                    "name": "",
                                    "arguments_json": "",
                                }
                            entry = pending_tools[idx]
                            if tc_delta.id:
                                entry["id"] = tc_delta.id
                            if tc_delta.function and tc_delta.function.name:
                                entry["name"] = tc_delta.function.name
                            if tc_delta.function and tc_delta.function.arguments:
                                entry["arguments_json"] += tc_delta.function.arguments

                    # 捕获 finish_reason
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason

                # 用量信息（OpenAI 在流式模式下可能通过 usage chunk 返回）
                if chunk.usage:
                    input_tokens = getattr(chunk.usage, "prompt_tokens", 0) or 0
                    output_tokens = getattr(chunk.usage, "completion_tokens", 0) or 0
                    pt_details = getattr(chunk.usage, "prompt_tokens_details", None)
                    if pt_details is not None:
                        cache_read = getattr(pt_details, "cached_tokens", 0) or 0

            # 流结束：检查是否有待处理的 tool_calls
            if pending_tools:
                parsed_tool_calls: list[ToolCall] = []
                for entry in pending_tools.values():
                    try:
                        arguments = json.loads(entry["arguments_json"] or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    parsed_tool_calls.append(
                        ToolCall(
                            id=entry["id"],
                            name=entry["name"],
                            arguments=arguments,
                        )
                    )
                yield StreamEvent(
                    tool_calls=parsed_tool_calls,
                    done=True,
                    stop_reason=finish_reason,
                    usage=Usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_write=0,
                        cache_read=cache_read,
                    ),
                )
            else:
                yield StreamEvent(
                    done=True,
                    stop_reason=finish_reason,
                    usage=Usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_write=0,
                        cache_read=cache_read,
                    ),
                )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 检测是否为 context_length_exceeded 错误并包装
            yield _wrap_ptl_error(e)


def _wrap_ptl_error(orig: Exception) -> StreamEvent:
    """将 OpenAI SDK 异常分类包装为对应错误类型。

    对齐 anthropic_provider: AuthenticationError / RateLimitError / NetworkError /
    PromptTooLongError / LLMError。
    """
    from . import (
        AuthenticationError,
        LLMError,
        NetworkError,
        PromptTooLongError,
        RateLimitError,
    )

    import openai

    err_msg = str(orig)

    # 1. 认证错误
    if isinstance(orig, openai.AuthenticationError):
        wrapped = AuthenticationError(f"openai auth: {err_msg}")
        wrapped.__cause__ = orig
        return StreamEvent(err=wrapped)

    # 2. 速率限制
    if isinstance(orig, openai.RateLimitError):
        retry_after = None
        if hasattr(orig, "response") and orig.response is not None:
            retry_str = orig.response.headers.get("retry-after")
            if retry_str is not None:
                try:
                    retry_after = float(retry_str)
                except (ValueError, TypeError):
                    pass
        wrapped = RateLimitError(f"openai rate limit: {err_msg}", retry_after=retry_after)
        wrapped.__cause__ = orig
        return StreamEvent(err=wrapped)

    # 3. 网络错误
    if isinstance(orig, openai.APIConnectionError):
        wrapped = NetworkError(f"openai network: {err_msg}")
        wrapped.__cause__ = orig
        return StreamEvent(err=wrapped)

    # 4. API 状态错误（含 BadRequestError）
    if isinstance(orig, openai.APIStatusError):
        code = getattr(orig, "status_code", 0)
        err_msg_lower = err_msg.lower()
        if "context_length_exceeded" in err_msg_lower or getattr(orig, "code", None) == "context_length_exceeded":
            wrapped = PromptTooLongError("openai context length exceeded")
            wrapped.__cause__ = orig
            return StreamEvent(err=wrapped)
        wrapped = LLMError(f"openai api error ({code}): {err_msg}")
        wrapped.__cause__ = orig
        return StreamEvent(err=wrapped)

    # 5. 通用 BadRequestError（可能不是 APIStatusError 子类）
    err_msg_lower = err_msg.lower()
    code = getattr(orig, "code", None)
    if code == "context_length_exceeded" or "context_length_exceeded" in err_msg_lower:
        wrapped = PromptTooLongError("openai context length exceeded")
        wrapped.__cause__ = orig
        return StreamEvent(err=wrapped)

    # 6. 其他异常
    wrapped = LLMError(f"openai error: {err_msg}")
    wrapped.__cause__ = orig
    return StreamEvent(err=wrapped)
