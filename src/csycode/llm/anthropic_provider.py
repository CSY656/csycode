"""Anthropic protocol adapter using the official SDK.

ch05: 分离缓存通道（稳定 system 块打 cache_control 断点）与
消息通道；reminder 并入末条 user 消息的 content 块。
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import anthropic

from csycode.config import ProviderConfig

from . import Message, Request, StreamEvent, ToolCall, Usage


class AnthropicProvider:
    """Provider implementation for the Anthropic protocol."""

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
        self._client = anthropic.AsyncAnthropic(**client_kwargs)
        self._model = cfg.model
        self._name = cfg.name
        self._thinking = cfg.thinking
        self._max_tokens: int = 4096  # 默认值，可被 set_max_output_tokens 覆盖

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

    def _convert_message(self, m: Message) -> dict:
        """将内部 Message 转为 Anthropic SDK 的 messages 格式。"""
        # 情况 1: assistant 消息带 tool_calls
        if m.tool_calls:
            content_blocks: list[dict] = []
            if m.content:
                content_blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                )
            return {"role": "assistant", "content": content_blocks}

        # 情况 2: tool_result 消息（user 角色 + tool_call_id）
        if m.tool_call_id:
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": m.content,
                    }
                ],
            }

        # 情况 3: 普通文本消息
        return {"role": m.role, "content": m.content}

    def _build_messages(self, msgs: list[Message]) -> list[dict]:
        """将对话历史转为 Anthropic SDK messages 列表。

        处理 tool_result 消息的合并：连续的多条 tool_result 消息
        （user 角色 + tool_call_id）合并为一条 user 消息，
        content 为多个 tool_result block 的列表。
        """
        result: list[dict] = []
        i = 0
        while i < len(msgs):
            m = msgs[i]
            # 检测是否是连续 tool_result 消息的开头
            if m.tool_call_id:
                tool_result_blocks: list[dict] = []
                while i < len(msgs) and msgs[i].tool_call_id:
                    tool_result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": msgs[i].tool_call_id or "",
                            "content": msgs[i].content,
                        }
                    )
                    i += 1
                result.append({"role": "user", "content": tool_result_blocks})
            else:
                result.append(self._convert_message(m))
                i += 1
        return result

    # ── 构造 Anthropic 格式的 tools ─────────────────────────────────

    @staticmethod
    def _to_anthropic_tools(tools: list[dict]) -> list[dict]:
        """将工具定义转为 Anthropic 格式（若尚未转换）。

        已是 Anthropic 格式（含 ``input_schema`` 键）则原样返回；
        OpenAI 格式（含 ``function`` 键）则转换。
        """
        result: list[dict] = []
        for t in tools:
            if "input_schema" in t:
                result.append(t)
            elif "function" in t:
                func = t["function"]
                result.append(
                    {
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "input_schema": func.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    }
                )
            else:
                result.append(t)
        return result

    # ── reminder 织入 ───────────────────────────────────────────────

    @staticmethod
    def _append_reminder(messages: list[dict], reminder: str) -> None:
        """将 reminder 文本块追加到最后一条消息的 content 中。

        确保目标消息的 content 为 list 形态后追加；
        若末条非 user 则新起一条 user 消息。
        """
        if not messages:
            messages.append({"role": "user", "content": reminder})
            return

        last = messages[-1]
        if last.get("role") != "user":
            messages.append({"role": "user", "content": reminder})
            return

        content = last.get("content")
        if isinstance(content, str):
            last["content"] = [
                {"type": "text", "text": content},
                {"type": "text", "text": reminder},
            ]
        elif isinstance(content, list):
            content.append({"type": "text", "text": reminder})
        else:
            last["content"] = [{"type": "text", "text": reminder}]

    # ── system 块构造（暴露为方法便于测试） ──────────────────────────

    @staticmethod
    def _build_system_blocks(req: Request) -> list[dict]:
        """构造 Anthropic system 参数列表。

        stable 块打 ``cache_control: ephemeral`` 断点；
        environment 块不断点。
        """
        blocks: list[dict] = []
        if req.system.stable:
            blocks.append(
                {
                    "type": "text",
                    "text": req.system.stable,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        if req.system.environment:
            blocks.append(
                {
                    "type": "text",
                    "text": req.system.environment,
                }
            )
        return blocks

    # ── user-tail 缓存断点 ──────────────────────────────────────────

    @staticmethod
    def _mark_last_user_tail_for_cache(messages: list[dict]) -> None:
        """给最后一条 user 消息的尾部打 cache_control 断点。

        对齐 mewcode _mark_last_user_tail_for_cache:
        从消息列表末尾向前查找最后一条 user 消息，将其 content 转为
        list 形态后给最后一个 block 标记 cache_control: ephemeral。

        这样 system + tools + 历史 user 尾部 构成完整的缓存前缀，
        最大化缓存命中率。
        """
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                content = messages[i].get("content")
                if isinstance(content, str):
                    messages[i]["content"] = [
                        {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}},
                    ]
                elif isinstance(content, list) and content:
                    content[-1]["cache_control"] = {"type": "ephemeral"}
                return

    # ── 主入口 ──────────────────────────────────────────────────────

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        """Stream a response from Anthropic, yielding text deltas and a done/err event.

        ch05 改动：
        - system 分为两块：stable 打 ``cache_control`` 断点，environment 不打。
        - reminder 织入末条 user 消息的 content 块。
        - 解析并透传缓存用量。
        """
        messages = self._build_messages(req.messages)

        # 构造 system 列表：stable 块断点 + environment 块不断点
        system_blocks = self._build_system_blocks(req)

        # reminder 织入
        if req.reminder:
            self._append_reminder(messages, req.reminder)

        # user-tail 缓存断点：给最后一条 user 消息尾部打 cache_control
        self._mark_last_user_tail_for_cache(messages)

        # 转换工具格式
        anthropic_tools = self._to_anthropic_tools(req.tools) if req.tools else None

        params: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
        }

        if system_blocks:
            params["system"] = system_blocks

        if self._thinking:
            params["thinking"] = {"type": "enabled", "budget_tokens": 2048}

        if anthropic_tools:
            anthropic_tools = list(anthropic_tools)  # 拷贝，避免修改上游
            # ── ch15: 给最后一个 tool 打 cache_control 断点 ──
            # 对齐 mewcode _mark_last_tool_for_cache：
            # 稳定块（system）和工具定义构成缓存前缀，最后一个 tool
            # 标记 cache_control 后，前缀中所有工具定义都进入缓存。
            if anthropic_tools:
                anthropic_tools[-1]["cache_control"] = {"type": "ephemeral"}
            params["tools"] = anthropic_tools

        # 累积 tool_use blocks: key 是 SDK 分配的 content_block index
        pending_tools: dict[int, dict] = {}

        # 缓存用量（从最终消息的 usage 中提取）
        cache_write = 0
        cache_read = 0
        input_tokens = 0
        output_tokens = 0

        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        block = event.content_block
                        if hasattr(block, "type") and block.type == "tool_use":
                            tool_id = getattr(block, "id", "")
                            tool_name = getattr(block, "name", "")
                            pending_tools[event.index] = {
                                "id": tool_id,
                                "name": tool_name,
                                "arguments_json": "",
                            }
                            # 流期间产出 tool_start 事件（对齐 mewcode）
                            yield StreamEvent(
                                event_type="tool_start",
                                tool_id=tool_id,
                                tool_name=tool_name,
                            )

                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, "type") and delta.type == "text_delta":
                            if hasattr(delta, "text") and delta.text:
                                yield StreamEvent(text=delta.text, event_type="text")
                        elif (
                            hasattr(delta, "type") and delta.type == "input_json_delta"
                        ):
                            # 累积 tool_use 的参数 JSON 片段
                            partial = (
                                delta.partial_json if hasattr(delta, "partial_json") else ""
                            )
                            if event.index in pending_tools:
                                pending_tools[event.index]["arguments_json"] += (
                                    partial or ""
                                )
                                # 流期间产出 tool_delta 事件
                                yield StreamEvent(
                                    event_type="tool_delta",
                                    tool_id=pending_tools[event.index]["id"],
                                    partial_json=partial or "",
                                )
                        # thinking_delta 等类型丢弃

                    elif event.type == "content_block_stop":
                        # 工具调用参数接收完成 → 解析 JSON → 产出 tool_complete
                        if event.index in pending_tools:
                            entry = pending_tools[event.index]
                            try:
                                arguments = json.loads(entry["arguments_json"] or "{}")
                            except json.JSONDecodeError:
                                arguments = {}
                            yield StreamEvent(
                                event_type="tool_complete",
                                tool_id=entry["id"],
                                tool_name=entry["name"],
                                arguments=arguments,
                            )

                # 流结束：提取用量（含缓存）和 stop_reason
                stop_reason: str = ""
                if hasattr(stream, "final_message") and stream.final_message:
                    final = stream.final_message
                    stop_reason = getattr(final, "stop_reason", "") or ""
                    if hasattr(final, "usage") and final.usage:
                        u = final.usage
                        input_tokens = getattr(u, "input_tokens", 0) or 0
                        output_tokens = getattr(u, "output_tokens", 0) or 0
                        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
                        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0

                # 构造完成事件
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
                        event_type="done",
                        stop_reason=stop_reason,
                        usage=Usage(
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cache_write=cache_write,
                            cache_read=cache_read,
                        ),
                    )
                else:
                    yield StreamEvent(
                        done=True,
                        event_type="done",
                        stop_reason=stop_reason,
                        usage=Usage(
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cache_write=cache_write,
                            cache_read=cache_read,
                        ),
                    )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 检测是否为 prompt_too_long 类错误并包装
            yield _wrap_ptl_error(e)


def _wrap_ptl_error(orig: Exception) -> StreamEvent:
    """将 Anthropic SDK 异常分类包装为对应错误类型。

    对齐 mewcode 的错误分类: AuthenticationError / RateLimitError / NetworkError /
    PromptTooLongError / LLMError。
    """
    from . import (
        AuthenticationError,
        LLMError,
        NetworkError,
        PromptTooLongError,
        RateLimitError,
    )

    err_msg = str(orig)

    # 1. 认证错误
    if isinstance(orig, anthropic.AuthenticationError):
        wrapped = AuthenticationError(f"anthropic auth: {err_msg}")
        wrapped.__cause__ = orig
        return StreamEvent(err=wrapped)

    # 2. 速率限制
    if isinstance(orig, anthropic.RateLimitError):
        retry_after = None
        if hasattr(orig, "response") and orig.response is not None:
            retry_str = orig.response.headers.get("retry-after")
            if retry_str is not None:
                try:
                    retry_after = float(retry_str)
                except (ValueError, TypeError):
                    pass
        wrapped = RateLimitError(f"anthropic rate limit: {err_msg}", retry_after=retry_after)
        wrapped.__cause__ = orig
        return StreamEvent(err=wrapped)

    # 3. 网络错误
    if isinstance(orig, anthropic.APIConnectionError):
        wrapped = NetworkError(f"anthropic network: {err_msg}")
        wrapped.__cause__ = orig
        return StreamEvent(err=wrapped)

    # 4. API 状态错误（含 BadRequestError -> prompt too long）
    if isinstance(orig, anthropic.APIStatusError):
        err_msg_lower = err_msg.lower()
        if "prompt is too long" in err_msg_lower or "context_length" in err_msg_lower:
            wrapped = PromptTooLongError("anthropic prompt too long")
            wrapped.__cause__ = orig
            return StreamEvent(err=wrapped)
        wrapped = LLMError(f"anthropic api error ({orig.status_code}): {err_msg}")
        wrapped.__cause__ = orig
        return StreamEvent(err=wrapped)

    # 5. BadRequestError (may not be APIStatusError subclass in all SDK versions)
    err_msg_lower = err_msg.lower()
    if "prompt is too long" in err_msg_lower or "context_length" in err_msg_lower:
        wrapped = PromptTooLongError("anthropic prompt too long")
        wrapped.__cause__ = orig
        return StreamEvent(err=wrapped)

    # 6. 其他异常
    wrapped = LLMError(f"anthropic error: {err_msg}")
    wrapped.__cause__ = orig
    return StreamEvent(err=wrapped)
