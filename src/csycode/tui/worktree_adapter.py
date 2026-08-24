"""WorktreeAdapter —— 将 worktree.Manager 适配为 WorktreeAccessor 协议。

实现 Command 包需要的 WorktreeAccessor，隔离 worktree 包反向依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from csycode.command.ui import WorktreeSummary

if TYPE_CHECKING:
    from csycode.worktree import Manager


class WorktreeAdapter:
    """将 worktree.Manager 包装为 WorktreeAccessor 协议。"""

    def __init__(
        self,
        manager: "Manager",
        set_active_cwd: Callable[[str], None],
    ) -> None:
        self._mgr = manager
        self._set_active_cwd = set_active_cwd

    async def create(self, name: str) -> tuple[str, str]:
        wt = await self._mgr.create(name, "HEAD", manual=True)
        return (wt.path, wt.branch)

    def list(self) -> list[WorktreeSummary]:
        result = []
        session = self._mgr.current_session
        active_path = session.worktree_path if session else None
        for wt in self._mgr.list():
            result.append(
                WorktreeSummary(
                    name=wt.name,
                    path=wt.path,
                    branch=wt.branch,
                    active=(wt.path == active_path),
                    manual=wt.manual,
                )
            )
        return result

    async def enter(self, name: str) -> None:
        from csycode.worktree import ExitAction, ExitOptions

        # 若已有 session，先退出当前（保留 worktree）
        if self._mgr.current_session is not None:
            cur_name = self._mgr.current_session.worktree_name
            await self._mgr.exit(cur_name, ExitAction.KEEP, ExitOptions())

        session = await self._mgr.enter(name)
        self._set_active_cwd(session.worktree_path)

    async def exit(self, action: str, discard: bool) -> bool:
        from csycode.worktree import ExitAction, ExitOptions

        if self._mgr.current_session is None:
            raise ValueError("当前没有活跃的 Worktree 会话")

        name = self._mgr.current_session.worktree_name
        act = ExitAction(action)
        opts = ExitOptions(discard_changes=discard)
        report = await self._mgr.exit(name, act, opts)

        self._set_active_cwd("")
        return report.removed

    async def remove(self, name: str, discard: bool) -> None:
        from csycode.worktree import ExitOptions

        opts = ExitOptions(discard_changes=discard)
        await self._mgr.remove(name, opts)
