"""补充消息与规划提醒构造。

提供：
- system_reminder: 用 ``<system-reminder>…</system-reminder>`` 包裹内容
- plan_reminder: 按完整/精简产出规划模式提醒
- EXECUTE_DIRECTIVE: /do 命令注入的用户消息文案
"""

from __future__ import annotations


def system_reminder(body: str) -> str:
    """用 ``<system-reminder>`` 标签包裹 body。

    Args:
        body: 要包裹的正文内容。

    Returns:
        带标签的完整提醒字符串。
    """
    return f"<system-reminder>\n{body}\n</system-reminder>"


# ── 规划提醒常量 ────────────────────────────────────────────────────────

_PLAN_REMINDER_FULL: str = (
    "You are currently in **Plan Mode** (planning-only).\n\n"
    "Rules:\n"
    "- You may ONLY use read-only tools: `read_file`, `glob`, `grep`, `ask_user_question`.\n"
    "- You may NOT use write tools (`write_file`, `edit_file`) or execute commands "
    "(`run_command`).\n"
    "- Your goal is to research the codebase, understand the problem, and produce a "
    "detailed, step-by-step implementation plan.\n"
    "- When your research is complete and you have written your plan, "
    "call the `exit_plan_mode` tool to submit your plan and exit plan mode.\n"
    "- Do NOT ask the user \"should I proceed\" — just call `exit_plan_mode` with your plan.\n"
    "- The plan should include: (1) summary of findings, (2) files to create/modify, "
    "(3) step-by-step instructions, (4) caveats and considerations.\n"
    "- If you need clarification on requirements, use the `ask_user_question` tool."
)

_PLAN_REMINDER_CONCISE: str = (
    "（Still in Plan Mode — read-only tools only. "
    "Research thoroughly, then call `exit_plan_mode` to submit your plan.）"
)

# ── /do 指令注入 ─────────────────────────────────────────────────────────

EXECUTE_DIRECTIVE: str = (
    "Plan Mode has been deactivated. All tools are now available. "
    "Proceed with implementing the plan — write code, edit files, run commands as needed."
)


def plan_reminder(full: bool) -> str:
    """返回包好 ``<system-reminder>`` 标签的规划模式提醒。

    Args:
        full: True 返回完整版指令，False 返回精简版。

    Returns:
        带标签的提醒字符串，可直接作为 ``Request.reminder`` 使用。
    """
    body = _PLAN_REMINDER_FULL if full else _PLAN_REMINDER_CONCISE
    return system_reminder(body)
