"""Team 队员对话的保存与恢复。

对齐 mewcode teams/transcript.py。

用于 Pane 后端队员的 session 持久化：
- 队员 run_to_completion 结束后自动 save_transcript
- Lead SendMessage 续派时 load_transcript 恢复对话上下文
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.conversation import Conversation


def save_transcript(
    team_name: str,
    agent_id: str,
    conversation: Conversation,
    teams_dir: str | None = None,
) -> Path:
    """保存队员对话到 Team 的 transcripts 目录。

    Args:
        team_name: Team sanitized name。
        agent_id: 队员 agent_id。
        conversation: 对话实例。
        teams_dir: teams 根目录（默认 ~/.csycode/teams）。

    Returns:
        写入的文件路径。
    """
    if teams_dir is None:
        teams_dir = str(Path.home() / ".csycode" / "teams")

    transcript_dir = Path(teams_dir) / team_name / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / f"{agent_id}.json"

    data = _serialize_conversation(conversation)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_transcript(
    team_name: str,
    agent_id: str,
    teams_dir: str | None = None,
) -> Conversation | None:
    """从 Team transcripts 目录恢复队员对话。

    Args:
        team_name: Team sanitized name。
        agent_id: 队员 agent_id。
        teams_dir: teams 根目录。

    Returns:
        Conversation 实例，文件不存在返回 None。
    """
    if teams_dir is None:
        teams_dir = str(Path.home() / ".csycode" / "teams")

    path = Path(teams_dir) / team_name / "transcripts" / f"{agent_id}.json"
    if not path.exists():
        return None

    data = json.loads(path.read_text(encoding="utf-8"))
    return _deserialize_conversation(data)


# ── 序列化/反序列化 ────────────────────────────────────────────────

def _serialize_conversation(conv: Conversation) -> list[dict[str, Any]]:
    """将 Conversation 序列化为 JSON 列表。"""
    messages: list[dict[str, Any]] = []
    for msg in conv.messages():
        entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "name": tc.name,
                    "arguments": tc.arguments,
                }
                for tc in msg.tool_calls
            ]
        # tool_call_id 表示这是一条 tool result
        if msg.tool_call_id:
            entry["tool_call_id"] = msg.tool_call_id
        messages.append(entry)
    return messages


def _deserialize_conversation(data: list[dict[str, Any]]) -> Conversation:
    """从 JSON 列表反序列化 Conversation。"""
    from csycode.conversation import Conversation
    from csycode.llm import Message, ToolCall

    conv = Conversation()
    for entry in data:
        tool_calls = None
        if "tool_calls" in entry:
            tool_calls = [
                ToolCall(id=tc["id"], name=tc["name"], arguments=tc.get("arguments", ""))
                for tc in entry["tool_calls"]
            ]

        msg = Message(
            role=entry["role"],
            content=entry.get("content", ""),
            tool_calls=tool_calls,
            tool_call_id=entry.get("tool_call_id"),
        )
        conv._messages.append(msg)

    return conv
