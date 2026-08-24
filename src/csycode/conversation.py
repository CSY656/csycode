"""In-memory multi-turn conversation history.

ch08: 内建 token 追踪（baseline_tokens / anchor_count），与 compact 协作。
ch09: on_append / on_replace 回调 + inject_long_term_memory（对齐 mewcode）。
"""

from __future__ import annotations

import copy
import json
from datetime import date
from typing import Callable

from .llm import Message, ToolCall

# 估算 token 时使用的字节/token 比率
_CHARS_PER_TOKEN = 3.5          # 纯 ASCII 内容
_CHARS_PER_TOKEN_CJK = 2.0      # 纯 CJK 内容（CJK 字符 3 字节/约 1.5 token）


def _message_chars(m: Message) -> int:
    """估算单条消息的字符数（UTF-8 字节）。"""
    n = len(m.content.encode("utf-8")) if m.content else 0
    if m.tool_calls:
        for tc in m.tool_calls:
            n += len(tc.name.encode("utf-8"))
            try:
                n += len(json.dumps(tc.arguments, ensure_ascii=False).encode("utf-8"))
            except (TypeError, ValueError):
                n += len(str(tc.arguments).encode("utf-8"))
    return n


def _message_text(m: Message) -> str:
    """拼接消息文本内容（用于 CJK 检测）。"""
    parts = [m.content or ""]
    if m.tool_calls:
        for tc in m.tool_calls:
            parts.append(tc.name)
            try:
                parts.append(json.dumps(tc.arguments, ensure_ascii=False))
            except (TypeError, ValueError):
                parts.append(str(tc.arguments))
    return "".join(parts)


def estimate_tokens(messages: list[Message]) -> int:
    """基于字符数对一组消息做 token 估算，CJK 自适应 ratio。"""
    total_bytes = sum(_message_chars(m) for m in messages)
    # 根据文本中 CJK 占比自适应选择 bytes-per-token 比率
    all_text = "".join(_message_text(m) for m in messages)
    ratio = _CHARS_PER_TOKEN
    if all_text:
        import re
        # 粗略 CJK 检测：统计 Unicode CJK 范围字符占比
        cjk_chars = len(re.findall(r'[一-鿿㐀-䶿豈-﫿぀-ゟ゠-ヿ가-힯　-〿＀-￯]', all_text))
        cjk_ratio = cjk_chars / len(all_text) if all_text else 0.0
        ratio = _CHARS_PER_TOKEN - cjk_ratio * (_CHARS_PER_TOKEN - _CHARS_PER_TOKEN_CJK)
    return int(total_bytes / ratio)


class Conversation:
    """Maintains the full history of a single chat session (not persisted).

    ch08 改动：
    - 内建 token 追踪：baseline_tokens / anchor_count / current_tokens
    - replace_history 重置锚点

    ch09 改动（对齐 mewcode）：
    - on_append / on_replace 回调，驱动 JSONL 实时写入
    - from_messages 类方法
    - inject_environment / inject_long_term_memory：将环境/指令/记忆
      作为 <system-reminder> 注入到对话历史（而非 system prompt），
      保证 system prompt 稳定可缓存。
    """

    def __init__(
        self,
        on_append: Callable[[Message], None] | None = None,
        on_replace: Callable[[list[Message]], None] | None = None,
    ) -> None:
        self._messages: list[Message] = []
        self._on_append = on_append
        self._on_replace = on_replace

        # ── ch08: token 追踪锚点 ──
        self.baseline_tokens: int = 0
        self.anchor_count: int = 0

        # ── ch09: 注入标记（对齐 mewcode）──
        self.env_injected: bool = False
        self.ltm_injected: bool = False

    @classmethod
    def from_messages(
        cls,
        msgs: list[Message],
        on_append: Callable[[Message], None] | None = None,
        on_replace: Callable[[list[Message]], None] | None = None,
    ) -> "Conversation":
        """从已有消息列表构造 Conversation（用于会话恢复）。"""
        conv = cls(on_append=on_append, on_replace=on_replace)
        conv._messages = list(msgs)
        return conv

    # ── Token 追踪 ──────────────────────────────────────────────────

    def record_usage_anchor(
        self,
        input_tokens: int,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_creation: int = 0,
    ) -> None:
        """根据一次 API 响应钉下真实用量锚点。

        baseline = input + cache_creation + output。
        cache_read 已包含在 input_tokens 中（Anthropic 和 OpenAI 均如此），
        不应重复计入，否则会导致缓存命中 token 被翻倍计算。
        anchor_count 对齐到当前消息数量。
        """
        self.baseline_tokens = (
            input_tokens + cache_creation + output_tokens
        )
        self.anchor_count = len(self._messages)

    def current_tokens(self) -> int:
        """对当前对话中的 token 数量做出最佳估算。

        有锚点时：baseline（真实用量）+ 锚点后新增消息的字符估算。
        无锚点时（冷启动 / 刚压缩）：退化为全量字符估算。
        """
        if self.baseline_tokens <= 0:
            return estimate_tokens(self._messages)
        tail = self._messages[self.anchor_count :]
        return self.baseline_tokens + estimate_tokens(tail)

    # ── 消息操作 ────────────────────────────────────────────────────

    def add_user(self, text: str) -> None:
        """Append a user message to the history."""
        msg = Message(role="user", content=text)
        self._messages.append(msg)
        if self._on_append:
            self._on_append(msg)

    def add_assistant(self, text: str) -> None:
        """Append an assistant text message to the history."""
        msg = Message(role="assistant", content=text)
        self._messages.append(msg)
        if self._on_append:
            self._on_append(msg)

    def add_assistant_with_tools(self, text: str, tool_calls: list[ToolCall]) -> None:
        """Append an assistant message that contains tool calls."""
        msg = Message(role="assistant", content=text, tool_calls=tool_calls)
        self._messages.append(msg)
        if self._on_append:
            self._on_append(msg)

    def add_tool_results(self, results: list[tuple[str, str]]) -> None:
        """Append tool execution results to the history."""
        for call_id, content in results:
            msg = Message(role="user", content=content, tool_call_id=call_id)
            self._messages.append(msg)
            if self._on_append:
                self._on_append(msg)

    def messages(self) -> list[Message]:
        """Return a shallow copy of the current history."""
        return list(self._messages)

    def length(self) -> int:
        """返回当前消息数量。"""
        return len(self._messages)

    def last_role(self) -> str | None:
        """返回最后一条消息的 role，空历史返回 None。"""
        if not self._messages:
            return None
        return self._messages[-1].role

    # ── 整体替换（compact 用）─────────────────────────────────────

    def replace_history(self, msgs: list[Message]) -> None:
        """整体替换对话历史并重置 token 锚点和注入标记。

        compact 后需要重新注入 environment 和 ltm。
        """
        self._messages = copy.deepcopy(msgs)
        self.baseline_tokens = 0
        self.anchor_count = 0
        self.env_injected = False
        self.ltm_injected = False
        if self._on_replace:
            self._on_replace(list(self._messages))

    # ── ch09: 环境与长期记忆注入（对齐 mewcode）──────────────────
    # ── ch13 fix: inject 方法改为先移除已有标记消息再插入，保证幂等 ──

    # 用于标记注入消息的 _supplement_tag 值
    _ENV_TAG = "__env__"
    _LTM_TAG = "__ltm__"

    def inject_environment(self, context: str) -> None:
        """注入环境信息到对话历史头部（幂等：重复调用先移除已有 env 消息）。

        类似 mewcode 的 inject_environment：在历史最前面插入一条
        user 消息包含环境上下文。

        ch13 fix: compact 后 replace_history 会重置 env_injected 标记，
        但旧 env 消息可能仍在历史中。改为先移除再插入，保证不重复。
        """
        # 移除已有的 env 消息（保证幂等）
        self._messages = [
            m for m in self._messages
            if m._supplement_tag != self._ENV_TAG
        ]
        self._messages.insert(
            0, Message(role="user", content=context, _supplement_tag=self._ENV_TAG)
        )
        if self.anchor_count > 0:
            self.anchor_count += 1
        self.env_injected = True

    def inject_long_term_memory(self, instructions: str, memories: str) -> None:
        """注入项目指令和长期记忆为 <system-reminder> 消息（幂等）。

        对齐 mewcode 的 inject_long_term_memory：
        将 instructions 和 memory index 包装为 system-reminder，
        插入到对话历史头部（env 消息之后）。包含日期信息。

        ch13 fix: compact 后 replace_history 会重置 ltm_injected 标记，
        但旧 ltm 消息可能仍在历史中。改为先移除再插入，保证不重复。

        Args:
            instructions: 项目指令文本（来自 instructions.Loader.load()）。
            memories: 记忆索引文本（来自 memory.Manager.load_index()）。
        """
        if self.ltm_injected:
            return

        sections: list[str] = []
        if instructions:
            sections.append(
                "# claudeMd\n"
                "Codebase and user instructions are shown below. "
                "Be sure to adhere to these instructions. "
                "IMPORTANT: These instructions OVERRIDE any default behavior "
                "and you MUST follow them exactly as written.\n\n" + instructions
            )
        if memories:
            sections.append("# autoMemory\n" + memories)
        if not sections:
            return

        sections.append(
            "# currentDate\nToday's date is %s." % date.today().isoformat()
        )
        body = "\n\n".join(sections)
        wrapped = (
            "<system-reminder>\n"
            "As you answer the user's questions, you can use the following context:\n"
            + body
            + "\n\n      IMPORTANT: this context may or may not be relevant to your tasks."
            " You should not respond to this context unless it is highly relevant to your task.\n"
            "</system-reminder>"
        )
        pos = 1 if self.env_injected else 0
        # 移除已有的 ltm 消息（保证幂等）
        self._messages = [
            m for m in self._messages
            if m._supplement_tag != self._LTM_TAG
        ]
        self._messages.insert(
            pos,
            Message(role="user", content=wrapped, _supplement_tag=self._LTM_TAG),
        )
        # 插入的消息在 anchor 之前时，需将边界后移 1，防止 current_tokens()
        # 把这条 LTM 消息计入 tail 估算（即避免双重计数）。
        if self.anchor_count > 0:
            self.anchor_count += 1
        self.ltm_injected = True

    # ── 补充消息 ──────────────────────────────────────────────────

    def add_supplement(self, text: str, tag: str) -> None:
        """添加带标签的补充消息。"""
        self._messages.append(
            Message(role="user", content=text, _supplement_tag=tag)
        )

    def remove_supplements_by_tag(self, tag: str) -> None:
        """移除所有匹配 tag 的补充消息。"""
        self._messages = [m for m in self._messages if m._supplement_tag != tag]

    def remove_orphaned_tool_calls(self) -> None:
        """清理孤立的 tool_calls 消息。"""
        cleaned: list[Message] = []
        i = 0
        while i < len(self._messages):
            msg = self._messages[i]
            if msg.role == "assistant" and msg.tool_calls:
                has_tool_results = False
                for j in range(i + 1, len(self._messages)):
                    if self._messages[j].tool_call_id is not None:
                        has_tool_results = True
                        break
                    if self._messages[j].role == "assistant":
                        break
                if has_tool_results:
                    cleaned.append(msg)
            else:
                cleaned.append(msg)
            i += 1
        self._messages = cleaned
