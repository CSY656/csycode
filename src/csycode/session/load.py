"""会话加载恢复 —— 从 JSONL 重建 Message 列表。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from csycode.llm import Message, ToolCall

_logger = logging.getLogger(__name__)


def load_session(session_dir: str) -> list[Message]:
    """从 conversation.jsonl 加载并重建消息列表。

    - 坏行（JSON 解析失败）跳过
    - 从最后一个 compact 标记之后开始加载
    - 末尾孤立的 tool_calls 被截断
    """
    jsonl_path = Path(session_dir) / "conversation.jsonl"
    if not jsonl_path.is_file():
        return []

    # 逐行读取，记录最后 compact 标记的位置
    raw_lines: list[dict] = []
    last_compact_idx = -1

    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                _logger.warning("JSONL 坏行跳过")
                continue

            if data.get("type") == "compact":
                last_compact_idx = len(raw_lines)
                continue

            raw_lines.append(data)

    # 从最后一个 compact 标记之后开始
    if last_compact_idx >= 0:
        raw_lines = raw_lines[last_compact_idx:]

    # 转换为 Message 列表
    messages = _records_to_messages(raw_lines)

    # 截断孤立的 tool_calls
    messages = _truncate_orphaned_tool_calls(messages)

    return messages


def _records_to_messages(raw: list[dict]) -> list[Message]:
    """将 JSONL dict 列表转换为 Message 列表。"""
    messages: list[Message] = []
    pending_results: list[dict] = []  # (tool_call_id, content)

    for data in raw:
        role = data.get("role", "")
        content = data.get("content", "")
        if not isinstance(content, str):
            content = str(content)

        tool_call_id = data.get("tool_call_id")

        if tool_call_id is not None:
            # 这是一条工具结果
            pending_results.append(data)
            continue

        # 先 flush 挂起的工具结果
        if pending_results:
            results = [
                (r.get("tool_call_id", ""), r.get("content", ""))
                for r in pending_results
            ]
            # 将工具结果合并为单条 user 消息
            for tc_id, tc_content in results:
                messages.append(
                    Message(role="user", content=tc_content, tool_call_id=tc_id)
                )
            pending_results = []

        tool_calls_raw = data.get("tool_calls")
        tool_calls = None
        if tool_calls_raw and isinstance(tool_calls_raw, list):
            tool_calls = [
                ToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", {}),
                )
                for tc in tool_calls_raw
                if isinstance(tc, dict)
            ]

        msg = Message(role=role, content=content, tool_calls=tool_calls)
        messages.append(msg)

    # flush 剩余的工具结果
    if pending_results:
        for r in pending_results:
            messages.append(
                Message(
                    role="user",
                    content=r.get("content", ""),
                    tool_call_id=r.get("tool_call_id"),
                )
            )

    return messages


def _truncate_orphaned_tool_calls(msgs: list[Message]) -> list[Message]:
    """如果最后一条是带 tool_calls 的 assistant 消息且无后续 tool 结果，则截断。"""
    if not msgs:
        return msgs

    last = msgs[-1]
    if last.role == "assistant" and last.tool_calls:
        # 检查后面是否有 tool 结果与之配对
        # 因为这是最后一条，后面没有消息，所以是孤立的
        return msgs[:-1]

    return msgs
