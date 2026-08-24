"""csycode CLI entry point — load config, construct permission engine, launch TUI.

ch09: 串联 instructions / memory / session 子系统的启动初始化。
ch15: 串联 Team Manager、Coordinator Mode、--team-member 自治循环。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path

from .config import ConfigError, load_merged
from .permission import new_engine
from .tools import create_default_registry
from .tui.app import csyCodeApp

from .mcp.manager import MCPManager


def _parse_args() -> argparse.Namespace:
    """解析 CLI 参数。"""
    parser = argparse.ArgumentParser(description="csyCode - 终端 AI 编程助手")
    # ch15: --team-member 模式
    parser.add_argument("--team-member", action="store_true", help="以 Team 队员模式启动（无 TUI）")
    parser.add_argument("--team", type=str, default="", help="Team 名称")
    parser.add_argument("--member", type=str, default="", help="队员名")
    parser.add_argument("--agent-id", type=str, default="", help="队员 agent_id")
    parser.add_argument("--session-dir", type=str, default="", help="Session 目录")
    parser.add_argument("--worktree", type=str, default="", help="Worktree 路径")
    parser.add_argument("--agent-type", type=str, default="", help="SubAgent 类型")
    parser.add_argument("--model", type=str, default="", help="模型覆盖")
    parser.add_argument("--plan-mode", action="store_true", help="以 Plan 模式启动")
    return parser.parse_args()


async def _amain() -> int:
    cli_args = _parse_args()
    root = str(Path.cwd().resolve())

    try:
        cfg = load_merged(root)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1
    tool_config = cfg.tools if hasattr(cfg, "tools") else None
    registry = create_default_registry(tool_config, project_root=root)

    mcp_mgr = MCPManager()
    if cfg.mcp_servers:
        mcp_mgr.load_configs(cfg.mcp_servers)
        await mcp_mgr.register_all_tools(registry)

    # ── ch09: 加载项目指令 ──
    from .instructions.loader import Loader

    instruction_text = ""
    try:
        loader = Loader(root)
        instruction_text = loader.load()
    except Exception:
        pass  # 指令加载失败静默，不阻塞启动

    # ── ch09: 初始化记忆管理器 ──
    from .memory.manager import Manager, _get_project_mem_dir, _get_user_mem_dir

    mem_mgr = Manager(
        project_dir=_get_project_mem_dir(root),
        user_dir=_get_user_mem_dir(),
        # provider 延迟设置，等 TUI 选定 provider 后注入
    )

    # ── ch09: 创建会话 Writer ──
    from .compact.state import new_session_context
    from .session.writer import Writer
    from .session.cleanup import clean_expired

    ses_ctx = new_session_context(root)
    writer = Writer(ses_ctx.session_dir)

    # 后台清理过期会话
    sessions_dir = os.path.join(root, ".csycode", "sessions")
    asyncio.create_task(
        asyncio.to_thread(clean_expired, sessions_dir, timedelta(days=30))
    )

    # ── Conversation 绑定回调 ──
    from .conversation import Conversation

    conv = Conversation(
        on_append=writer.on_append,
        on_replace=writer.on_replace,
    )

    try:
        engine, err = new_engine(root)
        if err is not None:
            print(f"权限引擎降级: {err}", file=sys.stderr)

        # ── ch12: 加载 Hook 引擎 ──
        from csycode import hook

        hook_engine = hook.load(root)

        # ── ch13: SubAgent 系统 ──
        from csycode.subagent import load_catalog as load_subagent_catalog
        from csycode.task.manager import Manager as TaskManager
        from csycode.task.tools import (
            TaskListTool,
            TaskGetTool,
            TaskStopTool,
            SendMessageTool,
        )
        from csycode.agent.agent_tool import AgentTool

        subagent_catalog = load_subagent_catalog(root)
        task_mgr = TaskManager()

        # ── ch14: Worktree 管理器 ──
        from csycode.worktree import (
            Manager as WorktreeManager,
            patch_manager_methods,
            start_stale_cleanup_task,
        )

        try:
            worktree_mgr = WorktreeManager(root)
            patch_manager_methods(worktree_mgr)
            # 从持久化 session 恢复（若存在）
            worktree_mgr.restore_session()
        except Exception as exc:
            print(f"Worktree 管理器降级: {exc}", file=sys.stderr)
            worktree_mgr = None
        else:
            # 启动周期性后台过期清理任务（对齐 mewcode start_stale_cleanup_task）
            asyncio.create_task(
                start_stale_cleanup_task(worktree_mgr, interval=3600, cutoff_hours=24)
            )

        # 注册 4 个 task 工具
        registry.register(TaskListTool(task_mgr))
        registry.register(TaskGetTool(task_mgr))
        registry.register(TaskStopTool(task_mgr))
        registry.register(SendMessageTool(task_mgr))

        # ── ch15: Team 系统 ───────────────────────────────────────
        from csycode.team.registry import AgentNameRegistry
        from csycode.team.manager import Manager as TeamManager
        from csycode.team.spawn import _patch_manager
        from csycode.team.tools import (
            TeamCreateTool,
            TeamDeleteTool,
            TaskCreateTool as TeamTaskCreateTool,
            TaskGetTool as TeamTaskGetTool,
            TaskListTool as TeamTaskListTool,
            TaskUpdateTool as TeamTaskUpdateTool,
            SendMessageTool as TeamSendMessageTool,
        )
        from csycode.coordinator import is_enabled as coordinator_enabled

        # 创建 AgentNameRegistry
        name_reg = AgentNameRegistry()
        task_mgr.set_name_registry(name_reg)

        # 创建 Team Manager
        home_dir = str(Path.home())
        team_mgr = TeamManager(
            home_dir=home_dir,
            project_root=root,
            wt_mgr=worktree_mgr,
            task_mgr=task_mgr,
            registry=name_reg,
        )
        # 打上 TeamHook 方法的猴子补丁
        _patch_manager(team_mgr)

        # 注册 7 个 Team 工具
        registry.register(TeamCreateTool(team_mgr))
        registry.register(TeamDeleteTool(team_mgr))
        registry.register(TeamTaskCreateTool(team_mgr))
        registry.register(TeamTaskGetTool(team_mgr))
        registry.register(TeamTaskListTool(team_mgr))
        registry.register(TeamTaskUpdateTool(team_mgr))
        registry.register(TeamSendMessageTool(team_mgr))

        # 注册 on_task_done 回调：队员完成后通知 Team Manager
        async def _on_teammate_done(task_id: str) -> None:
            await team_mgr.handle_task_done(task_id)

        task_mgr.on_task_done(_on_teammate_done)

        # Agent 工具（注入 team_hook）
        agent_tool = AgentTool(
            subagent_catalog,
            task_mgr,
            parent=None,
            bg_enabled=cfg.effective_enable_subagent_background(),
            worktree_mgr=worktree_mgr,
            team_hook=team_mgr,  # ch15: Manager 实现 TeamHook Protocol
        )
        registry.register(agent_tool)

        # ── ch15: --team-member 分支 ───────────────────────────────
        if cli_args.team_member:
            # 切到 worktree 目录
            if cli_args.worktree:
                try:
                    os.chdir(cli_args.worktree)
                except OSError as e:
                    print(f"切换 worktree 失败: {e}", file=sys.stderr)

            from csycode.cli.team_member import run_team_member

            # 构造简易 args 对象传给 team_member
            class _MemberArgs:
                pass

            member_args = _MemberArgs()
            member_args.team_mgr = team_mgr
            member_args.team_name = cli_args.team
            member_args.member_name = cli_args.member
            member_args.agent_id = cli_args.agent_id
            member_args.session_dir = cli_args.session_dir
            member_args.worktree_path = cli_args.worktree
            member_args.agent_type = cli_args.agent_type
            member_args.model = cli_args.model
            member_args.plan_mode = cli_args.plan_mode
            member_args.cfg = cfg
            member_args.registry = registry
            member_args.engine = engine
            member_args.hook_engine = hook_engine
            member_args.project_root = root
            member_args.version = __import__("csycode").__version__

            await run_team_member(member_args)
            return 0

        # ── Coordinator Mode ──────────────────────────────────────
        coordinator_mode = False
        if coordinator_enabled(cfg):
            coordinator_mode = True
            # 工具集收窄会在 app._run_agent 首次构造 Agent 时应用
            # 这里先存储标记

        app = csyCodeApp(
            cfg,
            engine,
            registry=registry,
            work_dir=root,
            writer=writer,
            mem_mgr=mem_mgr,
            instruction_text=instruction_text,
            hook_engine=hook_engine,  # ch12
            task_mgr=task_mgr,  # ch13
            subagent_catalog=subagent_catalog,  # ch13
            worktree_mgr=worktree_mgr,  # ch14
            team_mgr=team_mgr,  # ch15
            coordinator_mode=coordinator_mode,  # ch15
            name_reg=name_reg,  # ch15
        )
        # 用绑定回调的 conversation 替换默认的
        app.conv = conv

        # ch13: Agent 工具的 parent 引用在 app._run_agent() 中
        # Agent 首次构造时回填（对齐 mewcode）

        await app.run_async()

        # ── ch12: 兜底 SessionEnd ──
        from csycode.hook.event import Event as HE

        await hook_engine.dispatch(HE.SESSION_END, {
            "event": "SessionEnd",
            "session_id": "",
            "cwd": root,
            "mode": "default",
        })
        await hook_engine.close()
    finally:
        await mcp_mgr.shutdown()
        try:
            writer.close()
        except Exception:
            pass

    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(_amain())
    except KeyboardInterrupt:
        exit_code = 0
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
