"""Fork 路径辅助 —— 对齐 mewcode agents/fork.py。

提供 Fork Boilerplate 常量和 build_forked_messages / is_fork_context 函数。
Fork 子 Agent 从父对话克隆历史、注入约束指令、补全悬空 tool_use。
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.llm import Message

# ── Fork Boilerplate ────────────────────────────────────────────

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"

FORK_BOILERPLATE = f"""{FORK_BOILERPLATE_TAG}
你是一个 Fork 出来的工作进程。你不是主 Agent。
规则（不可协商）：
1. 不能再 Fork（调用 Agent 工具会被拦截）。
2. 不要对话、不要提问、不要请求确认。
3. 直接使用工具：读文件、搜索代码、做修改。
4. 严格限制在你被分配的任务范围内。
5. 最终报告以 "Scope:" 开头，500 字以内。
</fork_boilerplate>"""


class ForkError(Exception):
    """Fork 操作非法时抛出（如嵌套 Fork）。"""
    pass


# ── 辅助函数 ───────────────────────────────────────────────────


def build_forked_messages(
    parent_msgs: list["Message"],
    task: str,
) -> list["Message"]:
    """把父对话克隆为 Fork 子对话，处理悬空 tool_use，追加 Boilerplate + task。

    行为：
    1. 深拷贝 parent_msgs（所有 Message + 内部 tool_calls / tool_results 列表）
    2. 扫描末尾 assistant 消息的 tool_calls，若对应的 RoleTool 消息缺失，
       生成一条 placeholder tool_results（每个 ID 一条 "[forked, skipped]" 错误内容）
    3. 追加 user 消息 = FORK_BOILERPLATE + task

    Args:
        parent_msgs: 父对话的消息列表。
        task: 子 Agent 的任务文本。

    Returns:
        新的消息列表，可直接用 Conversation.from_messages 装载。
    """
    # 1. 深拷贝
    cloned = copy.deepcopy(parent_msgs)

    # 2. 处理悬空 tool_use
    if cloned:
        last = cloned[-1]
        if last.role == "assistant" and last.tool_calls:
            # 收集已有 tool_result 的 call_id
            existing_ids: set[str] = set()
            # 扫描所有历史消息中已有的 tool_call_id（用于配对检查）
            for msg in cloned:
                if msg.role == "user" and hasattr(msg, "tool_results"):
                    for tr in msg.tool_results:
                        existing_ids.add(tr.tool_use_id)

            pending = [
                tc for tc in last.tool_calls
                if tc.id not in existing_ids
            ]
            if pending:
                from csycode.llm import Message as LLMMessage
                placeholders = [
                    type(
                        "ToolResultBlock",
                        (),
                        {"tool_use_id": tc.id, "content": "[forked, skipped]", "is_error": False},
                    )()
                    for tc in pending
                ]
                cloned.append(
                    LLMMessage(
                        role="user",
                        content="",
                        tool_results=placeholders,
                    )
                )

    # 3. 追加 Fork Boilerplate + task
    from csycode.llm import Message as LLMMessage

    cloned.append(
        LLMMessage(
            role="user",
            content=f"{FORK_BOILERPLATE}\n\n你的任务：\n{task}",
        )
    )

    return cloned


def is_fork_context(msgs: list["Message"]) -> bool:
    """判定一个对话消息列表是否来自 Fork。

    扫描所有消息 content，检查是否包含 FORK_BOILERPLATE_TAG。
    这是 QuerySource 检测的兜底机制。

    Args:
        msgs: 对话消息列表。

    Returns:
        True 如果任一消息包含 fork boilerplate 标记。
    """
    for msg in msgs:
        content = getattr(msg, "content", "") or ""
        if FORK_BOILERPLATE_TAG in content:
            return True
    return False
