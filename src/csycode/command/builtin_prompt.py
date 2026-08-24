"""/do /review 提示词命令 handler。"""

from __future__ import annotations

from csycode.permission import Mode

# ── /review 指令常量 ─────────────────────────────────────────────────

REVIEW_DIRECTIVE = (
    "请审查当前上下文中的代码变更/已读取的文件，"
    "指出潜在 bug、可读性问题和可简化处。"
)


# ── handler ──────────────────────────────────────────────────────────


async def handle_do(ui) -> None:
    """切回 DEFAULT 模式 + 注入执行指令 + 触发 LLM 回合。"""
    from csycode import prompt

    ui.set_mode(Mode.DEFAULT)
    ui.inject_and_send("/do", str(prompt.EXECUTE_DIRECTIVE))


async def handle_review(ui) -> None:
    """注入代码审查请求 + 触发 LLM 回合。"""
    ui.inject_and_send("/review", REVIEW_DIRECTIVE)
