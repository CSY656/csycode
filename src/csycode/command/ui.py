"""UI 抽象层 —— handler 操作 TUI 的唯一通道。

handler 通过 UI Protocol 访问 TUI 能力，不直接持有 App 引用。
NopUI 是测试桩，所有写入方法 no-op，所有查询返回零值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from csycode.effort import DEFAULT_REASONING_EFFORT, parse_reasoning_effort
from csycode.permission import Mode


# ── ch14: Worktree UI 接口 ──────────────────────────────────────


@dataclass
class WorktreeSummary:
    """用于 /worktree list 输出的单条 Worktree 摘要。"""
    name: str
    path: str
    branch: str
    active: bool
    manual: bool


class WorktreeAccessor(Protocol):
    """TUI 通过此协议操作 worktree.Manager，避免命令层导入 worktree 包。"""

    async def create(self, name: str) -> tuple[str, str]:
        """创建 Worktree，返回 (path, branch)。"""
        ...

    def list(self) -> list[WorktreeSummary]:
        """列出所有 Worktree 摘要。"""
        ...

    async def enter(self, name: str) -> None:
        """进入 Worktree。"""
        ...

    async def exit(self, action: str, discard: bool) -> bool:
        """退出 Worktree，返回是否已删除。"""
        ...

    async def remove(self, name: str, discard: bool) -> None:
        """删除指定 Worktree。"""
        ...


# ── UI Protocol ──────────────────────────────────────────────────


class UI(Protocol):
    """命令 handler 操作 TUI 的抽象接口。

    csyCodeApp 实现此协议。handler 函数签名仅依赖 UI，不依赖具体 TUI 实现。
    """

    # ── 输出 ──────────────────────────────────────────────────────

    def println(self, msg: str) -> None:
        """向 scrollback 输出一行普通消息。"""
        ...

    def error(self, msg: str) -> None:
        """向 scrollback 输出一条错误消息。"""
        ...

    # ── 模式 ──────────────────────────────────────────────────────

    def mode(self) -> Mode:
        """返回当前权限模式。"""
        ...

    def set_mode(self, m: Mode) -> None:
        """设置权限模式。"""
        ...

    # ── 对话注入（KindPrompt 命令使用）─────────────────────────────

    def inject_and_send(
        self, display_label: str, preset_prompt: str
    ) -> None:
        """注入一条 user 消息到对话并立即触发回合。

        display_label 在 scrollback 中展示；preset_prompt 是实际写入
        conversation/JSONL 的文本。
        """
        ...

    # ── /status 与 /memory 等只读查询 ─────────────────────────────

    def usage_in(self) -> int:
        """返回累计输入 token 数。"""
        ...

    def usage_out(self) -> int:
        """返回累计输出 token 数。"""
        ...

    def model_name(self) -> str:
        """返回当前模型名。"""
        ...

    def cwd(self) -> str:
        """返回当前工作目录。"""
        ...

    def tool_count(self) -> int:
        """返回已注册工具数量。"""
        ...

    def memory_files(self) -> list[str]:
        """返回已加载的 .md 记忆文件名列表。"""
        ...

    def session_path(self) -> str:
        """返回当前会话存档文件的绝对路径。"""
        ...

    def session_id(self) -> str:
        """返回当前 session 标识。"""
        ...

    # ── 影响界面动作 ──────────────────────────────────────────────

    def quit(self) -> None:
        """退出进程。"""
        ...

    def force_compact(self) -> None:
        """手动触发上下文压缩。"""
        ...

    def open_resume_menu(self) -> None:
        """打开历史会话恢复列表。"""
        ...

    def clear_and_new_session(self) -> None:
        """关闭当前会话、开新会话、清空对话。"""
        ...

    # ── 状态机查询 ────────────────────────────────────────────────

    def idle(self) -> bool:
        """返回当前是否处于 IDLE 状态。"""
        ...

    def reasoning_effort(self) -> str:
        """返回当前思考强度等级。"""
        ...

    def set_reasoning_effort(self, value: str) -> bool:
        """校验并设置思考强度，成功返回 True。"""
        ...

    # ── ch14: Worktree ────────────────────────────────────────────

    def worktree_accessor(self) -> WorktreeAccessor | None:
        """返回 Worktree 操作接口（若启用），否则 None。"""
        ...


class NopUI:
    """测试桩：所有写入方法 no-op，所有查询返回零值。"""

    # ── 输出 ──

    def println(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass

    # ── 模式 ──

    def mode(self) -> Mode:
        return Mode.DEFAULT

    def set_mode(self, m: Mode) -> None:
        pass

    # ── 对话注入 ──

    def inject_and_send(
        self, display_label: str, preset_prompt: str
    ) -> None:
        pass

    # ── 只读查询 ──

    def usage_in(self) -> int:
        return 0

    def usage_out(self) -> int:
        return 0

    def model_name(self) -> str:
        return ""

    def cwd(self) -> str:
        return ""

    def tool_count(self) -> int:
        return 0

    def memory_files(self) -> list[str]:
        return []

    def session_path(self) -> str:
        return ""

    def session_id(self) -> str:
        return ""

    # ── 影响界面动作 ──

    def quit(self) -> None:
        pass

    def force_compact(self) -> None:
        pass

    def open_resume_menu(self) -> None:
        pass

    def clear_and_new_session(self) -> None:
        pass

    # ── 状态机查询 ──

    def idle(self) -> bool:
        return True

    def reasoning_effort(self) -> str:
        return getattr(self, "_reasoning_effort", DEFAULT_REASONING_EFFORT)

    def set_reasoning_effort(self, value: str) -> bool:
        parsed = parse_reasoning_effort(value)
        if parsed is None:
            return False
        self._reasoning_effort = parsed
        return True

    # ── ch14: Worktree ──

    def worktree_accessor(self) -> WorktreeAccessor | None:
        return None
