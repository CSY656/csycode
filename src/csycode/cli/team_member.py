"""--team-member 自治循环 —— Pane 后端队员的入口。

当 csycode 以 --team-member 启动时，不启动 TUI，
而是跑一个自治协程：
1. 读 mailbox 取任务
2. run_to_completion
3. 通知 Lead idle
4. stdin 监听 Wake 信号
5. 循环等待下一轮任务

对齐 mewcode teams/spawn_inprocess.py 的长期运行队员模型。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 轮询间隔（秒）
MAILBOX_POLL_INTERVAL = 2.0


async def run_team_member(args: Any) -> None:
    """Pane 后端的队员自治循环。

    Args:
        args: 解析后的 CLI 参数，需包含 team_manager, team_name, member_name,
              agent_id, session_dir, worktree_path, agent_type, model, plan_mode。
    """
    team_mgr = args.team_mgr
    team_name = args.team_name
    member_name = args.member_name
    agent_id = args.agent_id
    session_dir = args.session_dir
    worktree_path = args.worktree_path
    agent_type = args.agent_type
    model = args.model
    plan_mode = args.plan_mode  # noqa: F841

    # 切到 worktree 目录
    if worktree_path:
        try:
            os.chdir(worktree_path)
        except OSError as e:
            print(f"[team-member] 切换 worktree 目录失败: {e}", file=sys.stderr)

    print(f"[team-member] {member_name} · team={team_name} · agent={agent_id} · cwd={os.getcwd()}")

    # 获取 Team 和 Mailbox
    team = team_mgr.get(team_name)
    if team is None:
        print(f"[team-member] 错误: Team '{team_name}' 不存在", file=sys.stderr)
        return

    mailbox = team_mgr.get_mailbox(team_name)
    if mailbox is None:
        print(f"[team-member] 错误: Team '{team_name}' 的邮箱不存在", file=sys.stderr)
        return

    # 标记自己为活跃
    await team_mgr.set_member_active(team, member_name, True)

    # Wake 事件：stdin 有输入时触发
    wake_event = asyncio.Event()

    async def _stdin_reader() -> None:
        """监听 stdin，任何输入触发 wake_event。"""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        while True:
            try:
                line = await reader.readline()
                if not line:
                    break
                wake_event.set()
            except Exception:
                break

    stdin_task = asyncio.create_task(_stdin_reader())

    # 主循环
    try:
        while True:
            # 检测 mailbox 目录是否还存在（Lead 可能已删除 Team）
            if not Path(team.mailbox_dir).exists():
                print("[team-member] Team 已删除，退出")
                break

            # 读未读消息
            indices, unread = await mailbox.read_unread(agent_id)

            if not unread:
                # 没有消息：等待 wake_event 或超时
                try:
                    await asyncio.wait_for(wake_event.wait(), timeout=MAILBOX_POLL_INTERVAL)
                    wake_event.clear()
                except asyncio.TimeoutError:
                    pass
                continue

            # 标记已读
            await mailbox.mark_read(agent_id, indices)

            # 处理消息
            task_text = ""
            shutdown = False

            for msg in unread:
                msg_type = str(msg.type)

                if msg_type == "shutdown_request":
                    shutdown = True
                    print(f"[team-member] 收到 shutdown 请求 from {msg.from_}")
                    break

                if msg_type == "plan_approval_response":
                    if msg.payload and msg.payload.get("approve"):
                        print("[team-member] ✅ Plan 已批准，开始执行")
                        task_text = "Plan 已批准，请按计划执行。\n\n原始任务: " + (msg.content or "")
                    else:
                        feedback = (msg.payload or {}).get("feedback", "无反馈")
                        print(f"[team-member] ❌ Plan 被驳回: {feedback}")
                        task_text = f"Plan 被驳回。反馈: {feedback}\n请根据反馈调整后重新提交计划。"

                elif msg_type == "text":
                    if task_text:
                        task_text += "\n---\n"
                    task_text += msg.content or ""

            if shutdown:
                print("[team-member] 优雅退出")
                break

            if not task_text:
                continue

            # 构造 Agent 并 run_to_completion
            print("[team-member] 执行任务...")
            await _run_single_turn(
                team_mgr=team_mgr,
                team=team,
                member_name=member_name,
                agent_id=agent_id,
                agent_type=agent_type,
                model=model,
                task_text=task_text,
                session_dir=session_dir,
                worktree_path=worktree_path,
                args=args,
            )

            # 通知 Lead idle
            await team_mgr.set_member_active(team, member_name, False)
            from csycode.team.mailbox import create_message, MessageType
            idle_msg = create_message(
                from_agent=member_name,
                to_agent=team.lead_agent_id,
                content=f"队员 '{member_name}' 已完成工作，等待新任务。",
                summary=f"{member_name} idle",
                message_type=MessageType.TEXT,
            )
            await mailbox.write(team.lead_agent_id, idle_msg)
            print(f"[team-member] 已通知 Lead: {member_name} idle")

    except asyncio.CancelledError:
        print("[team-member] 被取消")
    except Exception as e:
        print(f"[team-member] 异常: {e}", file=sys.stderr)
        log.exception("team_member 异常")
    finally:
        stdin_task.cancel()
        try:
            await stdin_task
        except asyncio.CancelledError:
            pass
        print(f"[team-member] {member_name} 退出")


async def _run_single_turn(
    team_mgr: Any,
    team: Any,
    member_name: str,
    agent_id: str,
    agent_type: str,
    model: str,
    task_text: str,
    session_dir: str,
    worktree_path: str,
    args: Any,
) -> None:
    """执行单轮任务：构造 Agent → run_to_completion → 打印事件。"""
    # 加载 subagent 定义
    subagent_def = None
    try:
        from csycode.subagent import load_catalog, Definition, Source
        catalog = load_catalog(args.project_root if hasattr(args, 'project_root') else os.getcwd())
        subagent_def = catalog.resolve(agent_type)
    except Exception:
        pass

    if subagent_def is None:
        from csycode.subagent import Definition, Source
        subagent_def = Definition(
            name=agent_type or "general-purpose",
            description="Team worker",
            system_prompt="你是一个 AI 编程助手，正在 Team 中协作。",
            source=Source.BUILTIN,
        )

    # 构造工具注册表
    from csycode.tools.registry import ToolRegistry
    from csycode.tool.filter import FilterParams, apply_agent_tool_filter

    all_names = []
    if hasattr(args, 'registry') and args.registry:
        all_names = [t.name for t in args.registry.list_all()]

    allowed_names = apply_agent_tool_filter(
        FilterParams(
            all=all_names,
            source=int(subagent_def.source),
            background=False,
            allowed=subagent_def.tools,
            disallowed=subagent_def.disallowed_tools,
            teammate=True,
        )
    )

    sub_registry = ToolRegistry()
    if hasattr(args, 'registry') and args.registry:
        for name in allowed_names:
            tool = args.registry.get(name)
            if tool is not None:
                sub_registry.register(tool)

    # 构造 provider
    from csycode.llm import new_provider
    provider_cfg = args.cfg.providers[0] if hasattr(args, 'cfg') and args.cfg.providers else None
    if provider_cfg is None:
        print("[team-member] 错误: 无 provider 配置", file=sys.stderr)
        return
    provider = new_provider(provider_cfg)

    # 构造 Agent
    from csycode.agent.loop import Agent
    from csycode.conversation import Conversation
    from csycode.config import AgentConfig
    from csycode.permission import Mode as PMode
    from csycode.agent.team_hook import TEAMMATE_SYSTEM_PROMPT_SUFFIX, build_team_context_reminder

    system_prompt = (subagent_def.system_prompt or "") + TEAMMATE_SYSTEM_PROMPT_SUFFIX

    perm_mode = PMode.DEFAULT
    if hasattr(args, 'plan_mode') and args.plan_mode:
        perm_mode = PMode.PLAN

    conv = Conversation()
    # 注入 <team-context>
    team_ctx = build_team_context_reminder(
        team_name=team.sanitized_name,
        member_name=member_name,
        agent_id=agent_id,
        worktree_path=worktree_path,
        members_summary=", ".join(m.name for m in team.members),
    )
    conv.add_system_reminder(team_ctx)

    engine = getattr(args, 'engine', None)
    hook_engine = getattr(args, 'hook_engine', None)

    agent = Agent(
        provider=provider,
        tool_registry=sub_registry,
        conversation=conv,
        config=AgentConfig(max_iterations=subagent_def.max_turns if subagent_def.max_turns > 0 else 25),
        version=getattr(args, 'version', ''),
        engine=engine,
        context_window=200000,
        system_prompt=system_prompt,
        max_turns=subagent_def.max_turns,
        permission_mode=perm_mode,
        dont_ask=True,
        approval_upgrader=None,
        hook_engine=hook_engine,
    )
    agent.is_sub_agent = True

    # 执行
    events: asyncio.Queue = asyncio.Queue(maxsize=64)
    try:
        result = await agent.run_to_completion(conv, task_text, events)
        print("── 完成 ──")
        if result:
            # 截断过长的输出
            preview = result[:500] + "..." if len(result) > 500 else result
            print(preview)
    except Exception as e:
        print(f"[team-member] 任务执行异常: {e}", file=sys.stderr)
