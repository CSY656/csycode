"""spawn_teammate 主流程 —— 被 Agent 工具 Team Hook 调用。

执行完整的队员 spawn 流程：校验权限 → 创建 Worktree → 申请 session →
构造子 Agent/Conv → 注入协作工具和 TeammateContext → 按后端分流 spawn →
注册到 Registry → 持久化。
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import TYPE_CHECKING, Any

from csycode.team.types import (
    BackendType,
    TeammateInfo,
    InProcessTeammateNoSpawnError,
    TeamNotFoundError,
)
from csycode.team.mailbox import Box, create_message, MessageType
from csycode.agent.team_hook import (
    TeamSpawnRequest,
    TeammateContext,
    IncomingMessage,
    TEAMMATE_CTX_KEY,
    TEAMMATE_SYSTEM_PROMPT_SUFFIX,
    build_team_context_reminder,
    truncate_for_summary,
)

if TYPE_CHECKING:
    from csycode.team.manager import Manager
    from csycode.agent.loop import Agent
    from csycode.conversation import Conversation

log = logging.getLogger(__name__)


# ── spawn_teammate ────────────────────────────────────────────────

async def spawn_teammate(
    mgr: Manager,
    req: TeamSpawnRequest,
    parent_agent: Agent | None = None,
    parent_conv: Conversation | None = None,
    parent_provider: Any = None,
    parent_registry: Any = None,
    parent_engine: Any = None,
    parent_hook_engine: Any = None,
    parent_version: str = "",
    ctx: dict | None = None,
) -> str:
    """在 Team 中 spawn 一名队员。

    这是 Agent 工具 team_name 分支的完整实现。

    Args:
        mgr: Team Manager 实例。
        req: spawn 请求参数。
        parent_agent: 父 Agent（Lead）。
        parent_conv: 父对话。
        parent_provider: 父 provider。
        parent_registry: 父工具注册表。
        parent_engine: 权限引擎。
        parent_hook_engine: Hook 引擎。
        parent_version: 版本号。
        ctx: 当前执行上下文。

    Returns:
        JSON 字符串描述 spawn 结果。

    Raises:
        TeamNotFoundError: Team 不存在。
        InProcessTeammateNoSpawnError: in-process 队员试图再 spawn。
        ValueError: 参数无效。
    """
    # 1. 取 Team
    team = mgr.get(req.team_name)
    if team is None:
        raise TeamNotFoundError(f"Team 不存在: {req.team_name}")

    # 2. 校验调用者权限
    if ctx:
        tc = ctx.get(TEAMMATE_CTX_KEY)
        if tc is not None:
            # 当前调用者是队员
            if tc.backend_type == "in-process":
                raise InProcessTeammateNoSpawnError(
                    "in-process 队员不允许再 spawn 子队员"
                )
            # Pane 队员：team_name 参数已被屏蔽（在 AgentTool 层拦截）

    # 3. 解析 SubAgentDefinition（简化：本期用 general-purpose）
    from csycode.subagent import Definition, Source

    member_name = req.member_name or f"agent-{secrets.token_hex(4)}"
    agent_type = req.subagent_type or "general-purpose"

    # 尝试加载角色定义
    subagent_def = None
    try:
        catalog = getattr(parent_agent, "_catalog", None) if parent_agent else None
        if catalog is None and hasattr(mgr, "project_root"):
            from csycode.subagent import load_catalog
            catalog = load_catalog(mgr.project_root)
        if catalog:
            subagent_def = catalog.resolve(agent_type)
    except Exception:
        pass

    if subagent_def is None:
        # Fallback: built-in general-purpose
        from csycode.subagent import Definition, Source
        subagent_def = Definition(
            name="general-purpose",
            description="通用子 Agent",
            system_prompt="你是一个通用的 AI 编程助手。",
            source=Source.BUILTIN,
        )

    # 4. 创建 Worktree
    worktree_slug = f"team-{team.sanitized_name}/{member_name}"
    worktree_path = ""
    worktree_branch = ""

    if mgr.wt_mgr is not None:
        try:
            from csycode.worktree.create import _create_worktree
            wt = await _create_worktree(
                mgr.wt_mgr,
                worktree_slug,
                "HEAD",
                False,
            )
            worktree_path = wt.path
            worktree_branch = wt.branch
        except Exception as e:
            log.warning("创建 worktree 失败: %s，继续使用项目根目录", e)
            worktree_path = mgr.project_root
    else:
        worktree_path = mgr.project_root

    # 5. 申请 session 目录
    from csycode.compact.state import new_session_context
    ses_ctx = new_session_context(worktree_path or mgr.project_root)
    session_dir = ses_ctx.session_dir

    # 6. 预生成 agent_id
    agent_id = f"agent-{secrets.token_hex(7)}"

    # 7. 确定后端
    backend_type = team.backend
    for m in team.members:
        if m.name == member_name:
            backend_type = m.backend_type
            break

    from csycode.team.backend import SpawnRequest as BackendSpawnRequest, new_backend
    backend = new_backend(backend_type, task_mgr=mgr.task_mgr)

    # 8. 计算工具白名单
    from csycode.tool.filter import FilterParams, apply_agent_tool_filter

    all_names = []
    if parent_registry:
        all_names = [t.name for t in parent_registry.list_all()]

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

    # 构造子工具注册表
    from csycode.tools.registry import ToolRegistry
    sub_registry = ToolRegistry()
    if parent_registry:
        for name in allowed_names:
            tool = parent_registry.get(name)
            if tool is not None:
                sub_registry.register(tool)

    # 9. 构造 system prompt
    system_prompt = (subagent_def.system_prompt or "") + TEAMMATE_SYSTEM_PROMPT_SUFFIX

    # 10. 权限模式
    from csycode.permission import Mode as PMode, parse_mode
    perm_mode = PMode.DEFAULT
    if req.plan_mode_required or subagent_def.permission_mode == "plan":
        perm_mode = PMode.PLAN
    elif subagent_def.permission_mode and subagent_def.permission_mode != "default":
        parsed, ok = parse_mode(subagent_def.permission_mode)
        if ok:
            perm_mode = parsed

    # 11. 构造子 Agent（in-process）或准备 SpawnRequest（Pane 后端）
    spawn_req = BackendSpawnRequest(
        team_name=team.sanitized_name,
        member_name=member_name,
        agent_id=agent_id,
        worktree_path=worktree_path,
        session_dir=session_dir,
        agent_type=agent_type,
        model=req.model,
        initial_prompt=req.prompt,
        plan_mode_required=req.plan_mode_required or (perm_mode == PMode.PLAN),
    )

    # 构造队员的 TeammateContext
    mailbox_dir = team.mailbox_dir
    box = Box(mailbox_dir)

    async def _read_unread() -> tuple[list[int], list[IncomingMessage]]:
        indices, msgs = await box.read_unread(agent_id)
        incoming = [
            IncomingMessage(
                from_=m.from_,
                type=str(m.type),
                summary=m.summary,
                content=m.content,
                payload=m.payload,
            )
            for m in msgs
        ]
        return indices, incoming

    async def _mark_read(indices: list[int]) -> None:
        await box.mark_read(agent_id, indices)

    async def _send_message_wake(target: str) -> None:
        # Pane 后端：唤醒目标
        pass

    teammate_ctx = TeammateContext(
        team_name=team.sanitized_name,
        member_name=member_name,
        agent_id=agent_id,
        worktree_path=worktree_path,
        backend_type=str(backend_type),
        read_unread=_read_unread,
        mark_read=_mark_read,
        send_message_wake=_send_message_wake,
    )

    # 12. 构造 <team-context> reminder
    members_summary = ", ".join(
        f"{m.name}" + ("(lead)" if m.name == "lead" else "")
        for m in team.members
    )
    team_context_reminder = build_team_context_reminder(
        team_name=team.sanitized_name,
        member_name=member_name,
        agent_id=agent_id,
        worktree_path=worktree_path,
        members_summary=members_summary,
    )

    # 13. 按后端分流 spawn
    if backend_type == BackendType.IN_PROCESS:
        # 构造子 Agent
        from csycode.agent.loop import Agent
        from csycode.conversation import Conversation
        from csycode.config import AgentConfig

        sub_conv = Conversation()
        # 注入 <team-context> 到子对话
        sub_conv.add_system_reminder(team_context_reminder)

        sub_agent = Agent(
            provider=parent_provider,
            tool_registry=sub_registry,
            conversation=sub_conv,
            config=AgentConfig(
                max_iterations=subagent_def.max_turns if subagent_def.max_turns > 0 else 25,
            ),
            version=parent_version,
            engine=parent_engine,
            context_window=getattr(parent_agent, "context_window", 200000) if parent_agent else 200000,
            system_prompt=system_prompt,
            max_turns=subagent_def.max_turns,
            permission_mode=perm_mode,
            dont_ask=True,  # F39a: 队员一律 dont_ask
            approval_upgrader=None,
            hook_engine=parent_hook_engine,
        )
        sub_agent.is_sub_agent = True
        sub_agent.parent_id = getattr(parent_agent, "agent_id", "")

        # 注入 TeammateContext 到子 Agent
        sub_agent._teammate_ctx = teammate_ctx

        spawn_req.sub_agent = sub_agent
        spawn_req.conv = sub_conv
        spawn_req.task_mgr = mgr.task_mgr
    else:
        # Pane 后端：预写 initial_prompt 到 mailbox（F13）
        if req.prompt:
            summary = truncate_for_summary(req.prompt)
            init_msg = create_message(
                from_agent="lead",
                to_agent=agent_id,
                content=req.prompt,
                summary=summary,
                message_type=MessageType.TEXT,
            )
            await box.write(agent_id, init_msg)

    # 14. 调 backend.spawn
    pane_id, actual_agent_id = await backend.spawn(spawn_req)

    # 15. 注册到 AgentNameRegistry
    mgr.registry.register(member_name, agent_id)

    # 16. 构造 TeammateInfo 并持久化
    info = TeammateInfo(
        name=member_name,
        agent_id=agent_id,
        agent_type=agent_type,
        model=req.model,
        worktree_path=worktree_path,
        branch=worktree_branch,
        backend_type=backend_type,
        pane_id=pane_id,
        is_active=True,
        plan_mode_required=req.plan_mode_required,
        session_dir=session_dir,
    )
    await mgr.add_member(team, info)

    # 17. 返回结果
    result = {
        "member_name": member_name,
        "agent_id": agent_id,
        "worktree": worktree_path,
        "backend": str(backend_type),
        "pane_id": pane_id,
    }
    return json.dumps(result, ensure_ascii=False)


# ── Manager.spawn_teammate 桥接 ───────────────────────────────────

async def _manager_spawn_teammate(
    mgr: Manager,
    req: TeamSpawnRequest,
    parent_agent: Any = None,
    parent_conv: Any = None,
    parent_provider: Any = None,
    parent_registry: Any = None,
    parent_engine: Any = None,
    parent_hook_engine: Any = None,
    parent_version: str = "",
    ctx: dict | None = None,
) -> str:
    """Manager 上的 spawn_teammate 方法。"""
    return await spawn_teammate(
        mgr=mgr,
        req=req,
        parent_agent=parent_agent,
        parent_conv=parent_conv,
        parent_provider=parent_provider,
        parent_registry=parent_registry,
        parent_engine=parent_engine,
        parent_hook_engine=parent_hook_engine,
        parent_version=parent_version,
        ctx=ctx,
    )


# 将 spawn_teammate 和 is_teammate_context 方法绑定到 Manager
def _patch_manager(mgr: Manager) -> None:
    """给 Manager 实例打上 TeamHook 方法的猴子补丁。"""
    mgr.spawn_teammate = lambda req, **kw: _manager_spawn_teammate(
        mgr, req,
        parent_agent=kw.get("parent_agent"),
        parent_conv=kw.get("parent_conv"),
        parent_provider=kw.get("parent_provider"),
        parent_registry=kw.get("parent_registry"),
        parent_engine=kw.get("parent_engine"),
        parent_hook_engine=kw.get("parent_hook_engine"),
        parent_version=kw.get("parent_version", ""),
        ctx=kw.get("ctx"),
    )

    def _is_teammate_context(ctx: dict | None) -> tuple[str | None, str | None, bool]:
        if ctx is None:
            return (None, None, False)
        tc = ctx.get(TEAMMATE_CTX_KEY)
        if tc is None:
            return (None, None, False)
        is_ip = tc.backend_type == "in-process"
        return (tc.team_name, tc.member_name, is_ip)

    mgr.is_teammate_context = _is_teammate_context
