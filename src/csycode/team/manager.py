"""Team Manager —— Team 生命周期管理的核心协调器。

对齐 mewcode teams/manager.py 的 TeamManager。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from dataclasses import dataclass as _dc
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from csycode.team.types import (
    Team,
    TeammateInfo,
    TeamNotFoundError,
    TeamHasActiveMembersError,
    MemberExistsError,
)
from csycode.team.persistence import sanitize, atomic_write_json, read_json, reload_from_disk_locked
from csycode.team.mailbox import Box, MessageType, create_message
from csycode.team.registry import AgentNameRegistry
from csycode.team.tasks import Store as TaskStore

if TYPE_CHECKING:
    from csycode.worktree.manager import Manager as WorktreeManager
    from csycode.task.manager import Manager as TaskManager

log = logging.getLogger(__name__)


# ── Manager ───────────────────────────────────────────────────────

class Manager:
    """Team 生命周期管理器。

    在单 csycode 进程内管理多个 Team。
    典型场景同时只有一个活跃 Team。
    """

    def __init__(
        self,
        home_dir: str | Path,
        project_root: str,
        wt_mgr: WorktreeManager | None = None,
        task_mgr: TaskManager | None = None,
        registry: AgentNameRegistry | None = None,
    ) -> None:
        """初始化 Manager。

        Args:
            home_dir: 用户主目录（~/.csycode/teams/ 的父目录）。
            project_root: 项目根目录。
            wt_mgr: Worktree 管理器。
            task_mgr: 后台任务管理器。
            registry: Agent 名称注册表。
        """
        self.home_dir = str(Path(home_dir))
        self.project_root = str(Path(project_root).resolve())
        self.wt_mgr = wt_mgr
        self.task_mgr = task_mgr
        self.registry = registry or AgentNameRegistry()

        self._lock = asyncio.Lock()
        self.teams: dict[str, Team] = {}  # 按 sanitized_name 索引
        self._task_stores: dict[str, TaskStore] = {}
        self._mailboxes: dict[str, Box] = {}
        self._teammate_team_map: dict[str, str] = {}  # agent_id → sanitized_name

        # 确保 teams 目录存在并扫描已有 Team
        self._teams_dir = Path(self.home_dir) / ".csycode" / "teams"
        self._teams_dir.mkdir(parents=True, exist_ok=True)
        self._scan_existing_teams()

    # ── 扫描已有 Team ─────────────────────────────────────────────

    def _scan_existing_teams(self) -> None:
        """扫描 ~/.csycode/teams/ 下所有子目录，还原 teams dict。"""
        for subdir in self._teams_dir.iterdir():
            if not subdir.is_dir():
                continue
            config_path = subdir / "config.json"
            if not config_path.exists():
                continue
            try:
                data = read_json(config_path)
                team = Team.from_dict(data)
                # 填充派生路径
                team.config_dir = str(subdir)
                team.config_path = str(config_path)
                team.tasks_path = str(subdir / "tasks.json")
                team.mailbox_dir = str(subdir / "mailbox")
                self.teams[team.sanitized_name] = team

                # 恢复 mailbox 和 task_store 缓存
                self._mailboxes[team.sanitized_name] = Box(team.mailbox_dir)
                self._task_stores[team.sanitized_name] = TaskStore(team.tasks_path)
            except Exception as e:
                print(f"警告: 跳过损坏的 Team 配置 {subdir}: {e}", file=sys.stderr)

    # ── 查询 ───────────────────────────────────────────────────────

    def get(self, name: str) -> Team | None:
        """按 sanitized name 查询 Team。"""
        sname = sanitize(name)
        return self.teams.get(sname)

    def list_(self) -> list[Team]:
        """返回所有 Team（按创建时间排序）。"""
        return sorted(self.teams.values(), key=lambda t: t.created_at)

    def get_task_store(self, team_name: str) -> TaskStore | None:
        """获取 Team 的共享任务存储。"""
        sname = sanitize(team_name)
        return self._task_stores.get(sname)

    def get_mailbox(self, team_name: str) -> Box | None:
        """获取 Team 的邮箱。"""
        sname = sanitize(team_name)
        return self._mailboxes.get(sname)

    def get_team_for_teammate(self, agent_id: str) -> str | None:
        """按 agent_id 反查所属 Team 的 sanitized_name。"""
        if agent_id in self._teammate_team_map:
            return self._teammate_team_map[agent_id]
        for sname, team in self.teams.items():
            for m in team.members:
                if m.agent_id == agent_id:
                    return sname
        return None

    # ── create ─────────────────────────────────────────────────────

    async def create(
        self,
        name: str,
        description: str = "",
    ) -> Team:
        """创建新 Team。

        Args:
            name: 团队名（原始，会经 sanitize 处理）。
            description: 团队描述。

        Returns:
            新创建的 Team。

        Raises:
            ValueError: sanitize 后为空字符串。
        """
        sname = sanitize(name)
        if not sname:
            raise ValueError(f"团队名 '{name}' sanitize 后为空，请使用有效字符")

        async with self._lock:
            # 同名冲突检测：自动后缀 -2 / -3
            original = sname
            counter = 2
            while sname in self.teams or (self._teams_dir / sname).exists():
                sname = f"{original}-{counter}"
                counter += 1

            # 检测后端
            from csycode.team.backend.detect import detect
            backend = detect()

            # 创建目录结构
            config_dir = self._teams_dir / sname
            config_dir.mkdir(parents=True, exist_ok=True)
            mailbox_dir = config_dir / "mailbox"
            mailbox_dir.mkdir(parents=True, exist_ok=True)
            tasks_path = config_dir / "tasks.json"

            config_path = config_dir / "config.json"

            # 构造 Team
            team = Team(
                name=name,
                sanitized_name=sname,
                lead_agent_id="lead",
                backend=backend,
                description=description,
                created_at=datetime.now(),
                members=[
                    TeammateInfo(
                        name="lead",
                        agent_id="lead",
                        is_active=None,
                    )
                ],
                config_dir=str(config_dir),
                config_path=str(config_path),
                tasks_path=str(tasks_path),
                mailbox_dir=str(mailbox_dir),
            )

            # 持久化
            atomic_write_json(config_path, team.to_dict())

            # 初始化 tasks.json
            task_store = TaskStore(tasks_path)

            # 初始化 mailbox
            mailbox = Box(mailbox_dir)

            # 注册到内存
            self.teams[sname] = team
            self._task_stores[sname] = task_store
            self._mailboxes[sname] = mailbox

            log.info("创建 Team '%s' (sanitized='%s', backend=%s)", name, sname, backend)
            return team

    # ── delete ─────────────────────────────────────────────────────

    async def delete(self, name: str, force: bool = False) -> None:
        """删除 Team。

        Args:
            name: Team sanitized name。
            force: 是否强制删除（忽略活跃成员检查）。

        Raises:
            TeamNotFoundError: Team 不存在。
            TeamHasActiveMembersError: 有活跃成员且非 force。
        """
        sname = sanitize(name)
        team = self.teams.get(sname)
        if team is None:
            raise TeamNotFoundError(f"Team 不存在: {name}")

        async with team._lock:
            # 非 force 时检查活跃成员
            if not force:
                active = [m for m in team.members if m.is_active is not False and m.name != "lead"]
                if active:
                    names = ", ".join(m.name for m in active)
                    raise TeamHasActiveMembersError(
                        f"无法删除 Team '{name}'：以下成员仍在活跃: {names}"
                    )

            # 逐个清理非 lead 成员
            for member in list(team.members):
                if member.name == "lead":
                    continue

                # Kill 后端进程
                try:
                    from csycode.team.backend import new_backend
                    backend_inst = new_backend(
                        member.backend_type,
                        task_mgr=self.task_mgr,
                    )
                    await backend_inst.kill(member.pane_id, member.agent_id)
                except Exception as e:
                    log.warning("kill 成员 %s 失败: %s", member.name, e)

                # 清理 worktree（best-effort）
                if member.worktree_path and self.wt_mgr is not None:
                    try:
                        from csycode.worktree.lifecycle import ExitOptions, _remove_worktree
                        opts = ExitOptions(discard_changes=True)
                        await _remove_worktree(
                            self.wt_mgr, f"team-{sname}/{member.name}", opts
                        )
                    except Exception as e:
                        log.warning("清理 worktree %s 失败: %s", member.worktree_path, e)

                # 清理 session 目录
                if member.session_dir:
                    try:
                        shutil.rmtree(member.session_dir, ignore_errors=True)
                    except Exception:
                        pass

                # 取消注册
                self.registry.unregister(member.name)
                self._teammate_team_map.pop(member.agent_id, None)

            # 清理 mailbox
            mailbox = self._mailboxes.get(sname)
            if mailbox:
                try:
                    await mailbox.cleanup_all()
                except Exception as e:
                    log.warning("清理 mailbox 失败: %s", e)

            # 删除整个 team 目录
            try:
                shutil.rmtree(team.config_dir, ignore_errors=True)
            except Exception as e:
                log.warning("删除 team 目录 %s 失败: %s", team.config_dir, e)

            # 从内存移除
            self.teams.pop(sname, None)
            self._task_stores.pop(sname, None)
            self._mailboxes.pop(sname, None)

            log.info("删除 Team '%s'", name)

    # ── 成员操作 ──────────────────────────────────────────────────

    async def add_member(self, team: Team, info: TeammateInfo) -> None:
        """向 Team 添加成员（持锁，先 reload 再修改）。

        Args:
            team: Team 实例。
            info: 队员信息。

        Raises:
            MemberExistsError: 成员名已存在。
        """
        async with team._lock:
            # 跨进程并发保护：先重读磁盘
            await reload_from_disk_locked(team)

            # 检查重名
            if team.member_by_name(info.name) is not None:
                raise MemberExistsError(f"成员 '{info.name}' 在 Team 中已存在")

            team.members.append(info)
            atomic_write_json(team.config_path, team.to_dict())

            # 注册到名称注册表
            self.registry.register(info.name, info.agent_id)
            self._teammate_team_map[info.agent_id] = team.sanitized_name

    async def set_member_active(
        self, team: Team, member_name: str, active: bool
    ) -> bool:
        """设置成员活跃状态。

        Args:
            team: Team 实例。
            member_name: 成员名。
            active: True 表示活跃，False 表示空闲。

        Returns:
            True 如果更新成功，False 如果成员不存在。
        """
        async with team._lock:
            # 跨进程并发保护：先重读磁盘
            await reload_from_disk_locked(team)

            member = team.member_by_name(member_name)
            if member is None:
                return False

            member.is_active = active
            atomic_write_json(team.config_path, team.to_dict())
            return True

    async def remove_member(self, team: Team, member_name: str) -> bool:
        """从 Team 移除成员。

        Args:
            team: Team 实例。
            member_name: 成员名。

        Returns:
            True 如果移除成功，False 如果成员不存在。
        """
        async with team._lock:
            await reload_from_disk_locked(team)

            member = team.member_by_name(member_name)
            if member is None:
                return False

            team.members = [m for m in team.members if m.name != member_name]
            atomic_write_json(team.config_path, team.to_dict())

            self.registry.unregister(member_name)
            self._teammate_team_map.pop(member.agent_id, None)
            return True

    # ── 任务完成回调 ──────────────────────────────────────────────

    async def handle_task_done(self, agent_id: str) -> None:
        """队员 run_to_completion 结束后的回调。

        设置 is_active=False 并通知 Lead。

        Args:
            agent_id: 完成任务的 agent_id。
        """
        sname = self.get_team_for_teammate(agent_id)
        if sname is None:
            return

        team = self.teams.get(sname)
        if team is None:
            return

        member_name = self.registry.name_of(agent_id)
        if member_name is None:
            return

        # 设置空闲
        await self.set_member_active(team, member_name, False)

        # 通知 Lead
        mailbox = self._mailboxes.get(sname)
        if mailbox:
            msg = create_message(
                from_agent=member_name,
                to_agent=team.lead_agent_id,
                content=f"队员 '{member_name}' (agent_id={agent_id}) 已完成工作，等待新任务。",
                summary=f"{member_name} idle",
                message_type=MessageType.TEXT,
            )
            await mailbox.write(team.lead_agent_id, msg)

    # ── Lead 邮箱轮询 ─────────────────────────────────────────────

    async def poll_lead_mailboxes(self) -> list[LeadMessage]:
        """轮询所有 Team 的 Lead 邮箱，消费未读消息。

        Returns:
            LeadMessage 列表。
        """
        results: list[LeadMessage] = []
        for sname, team in list(self.teams.items()):
            mailbox = self._mailboxes.get(sname)
            if mailbox is None:
                continue
            try:
                msgs = await mailbox.consume(team.lead_agent_id)
                for m in msgs:
                    results.append(LeadMessage(
                        team_name=sname,
                        from_=m.from_,
                        type=m.type,
                        summary=m.summary,
                        content=m.content,
                        timestamp=m.timestamp,
                    ))
            except Exception as e:
                log.warning("轮询 Lead 邮箱失败 (team=%s): %s", sname, e)
        return results


# ── LeadMessage ───────────────────────────────────────────────────


@_dc
class LeadMessage:
    """Lead 邮箱中的一条消息（已消费）。"""
    team_name: str
    from_: str
    type: str
    summary: str
    content: str
    timestamp: float = 0.0
