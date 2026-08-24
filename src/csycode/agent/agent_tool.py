"""Agent 工具 —— 对齐 mewcode tools/agent_tool.py。

主 Agent 通过此工具调用子 Agent（定义式 / Fork 式），
支持前台同步和后台异步两种模式。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from csycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from csycode.agent.loop import Agent
    from csycode.agent.team_hook import TeamHook  # ch15
    from csycode.subagent.catalog import Catalog as AgentCatalog
    from csycode.task.manager import Manager as TaskManager
    from csycode.worktree.manager import Manager as WorktreeManager

log = logging.getLogger(__name__)

# 前台子 Agent 超时自动切后台的秒数
AUTO_BACKGROUND_SECONDS = 120.0

# Fork 路径的 query_source 标记
FORK_QUERY_SOURCE = "agent:builtin:fork"


# ── 参数模型 ──────────────────────────────────────────────────────


@dataclass
class AgentArgs:
    """Agent 工具的解析后参数。"""
    prompt: str
    description: str
    subagent_type: str = ""
    model: str = ""
    run_in_background: bool = False
    name: str = ""
    isolation: str = ""  # ch14: "" | "worktree"
    team_name: str = ""  # ch15: 非空时走 Team spawn 分支


# ── Agent 工具 ────────────────────────────────────────────────────


class AgentTool(Tool):
    """注册到 ToolRegistry 的统一 Agent 工具。

    主 Agent 调用此工具来启动子 Agent。
    子 Agent 可以用预定义角色（subagent_type）或 Fork 当前对话。
    """

    def __init__(
        self,
        catalog: "AgentCatalog",
        task_mgr: "TaskManager",
        parent: "Agent | None" = None,
        bg_enabled: bool = True,
        worktree_mgr: "WorktreeManager | None" = None,
        team_hook: "TeamHook | None" = None,  # ch15
    ) -> None:
        self._catalog = catalog
        self._task_mgr = task_mgr
        self._parent = parent
        self._bg_enabled = bg_enabled
        self._worktree_mgr = worktree_mgr
        self._team_hook = team_hook  # ch15

    # ── Tool 接口 ────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "Agent"

    @property
    def description(self) -> str:
        """动态描述：列出当前可用的 subagent_type。"""
        base = (
            "启动一个子 Agent 来执行独立任务。"
            "子 Agent 拥有独立的对话上下文和工具集，"
            "可前台同步运行或后台异步运行。"
        )
        try:
            defs = self._catalog.list_all()
            if defs:
                names = ", ".join(d.name for d in defs)
                base += f" 可用的 subagent_type: {names}。"
        except Exception:
            pass
        return base

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "交给子 Agent 的任务指令",
                },
                "description": {
                    "type": "string",
                    "description": "一句话描述任务，供 UI 展示",
                },
                "subagent_type": {
                    "type": "string",
                    "description": (
                        "指定预定义角色名（如 Explore / Plan / general-purpose），"
                        "留空时走 Fork 路径（克隆当前对话）"
                    ),
                },
                "model": {
                    "type": "string",
                    "description": "模型覆盖: haiku / sonnet / opus / inherit",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "true 时强制后台启动；Fork 路径忽略此字段（无条件后台）",
                },
                "name": {
                    "type": "string",
                    "description": "给本次启动的子 Agent 命名，供 SendMessage 续派任务用",
                },
                "isolation": {
                    "type": "string",
                    "description": (
                        "隔离模式。设为 'worktree' 时子 Agent 在独立的 Git Worktree "
                        "副本中运行，与主 Agent 的文件系统完全隔离。"
                        "留空表示无隔离（默认）。"
                    ),
                    "enum": ["", "worktree"],
                },
                "team_name": {
                    "type": "string",
                    "description": (
                        "Team 名称。非空时走 Team spawn 分支：在指定 Team 中创建新队员，"
                        "队员拥有协作工具（TaskCreate/SendMessage 等），"
                        "可通过邮箱与其他队员通信。留空时走普通子 Agent 路径。"
                    ),
                },
            },
            "required": ["prompt", "description"],
        }

    is_readonly: bool = False
    is_concurrency_safe: bool = False
    is_system_tool: bool = True

    # ── execute ───────────────────────────────────────────────────

    async def _execute(self, **kwargs) -> ToolResult:
        """执行 Agent 工具调用。

        解析参数 → 防嵌套 → 解析定义 → 工具过滤 → 构造子 Agent → 启动。
        """
        # 1. 解析参数
        a = AgentArgs(
            prompt=str(kwargs.get("prompt", "")),
            description=str(kwargs.get("description", "")),
            subagent_type=str(kwargs.get("subagent_type", "")),
            model=str(kwargs.get("model", "")),
            run_in_background=bool(kwargs.get("run_in_background", False)),
            name=str(kwargs.get("name", "")),
            isolation=str(kwargs.get("isolation", "")),
            team_name=str(kwargs.get("team_name", "")),
        )

        if not a.prompt:
            return ToolResult(success=False, content="", error="prompt 不能为空")
        if not a.description:
            return ToolResult(success=False, content="", error="description 不能为空")

        # ── ch15: Team spawn 分支 ──────────────────────────────────
        if a.team_name:
            return await self._execute_team_spawn(a, kwargs)

        if self._parent is None:
            return ToolResult(
                success=False, content="", error="Agent 工具未绑定父 Agent"
            )

        # 2. 防嵌套：子 Agent 不能再调 Agent 工具
        if self._parent.is_sub_agent:
            return ToolResult(
                success=False,
                content="子 Agent 不能再启动 Agent（嵌套被阻断）",
                error="subagent nesting blocked",
            )

        # 检查父对话是否含 Fork boilerplate
        try:
            from .fork import is_fork_context

            parent_msgs = self._parent._conversation.messages()
            if is_fork_context(parent_msgs):
                return ToolResult(
                    success=False,
                    content="Fork 子 Agent 不能再启动 Agent（检测到 fork boilerplate）",
                    error="fork nesting blocked",
                )
        except Exception:
            pass

        # 3. 解析定义

        if a.subagent_type:
            defi = self._catalog.resolve(a.subagent_type)
            if defi is None:
                return ToolResult(
                    success=False,
                    content=f"未知 subagent_type: {a.subagent_type}",
                    error="unknown subagent_type",
                )
        else:
            defi = self._catalog.fork_definition()

        # 3.5 决定 isolation（对齐 mewcode: 定义优先，参数可补充）
        isolation = defi.isolation or a.isolation

        # 4. 决定后台
        background = defi.background or a.run_in_background or defi.is_fork()
        if background and not self._bg_enabled:
            if defi.is_fork():
                return ToolResult(
                    success=False,
                    content="后台模式被禁用，无法 Fork",
                    error="background disabled",
                )
            # 非 Fork 路径：降级为前台
            background = False

        # 5. 工具过滤
        from csycode.tool.filter import FilterParams, apply_agent_tool_filter

        all_names = [t.name for t in self._parent._tool_registry.list_all()]
        allowed_names = apply_agent_tool_filter(
            FilterParams(
                all=all_names,
                source=int(defi.source),
                background=background,
                allowed=defi.tools,
                disallowed=defi.disallowed_tools,
            )
        )

        # 构造子 Agent 的工具注册表
        from csycode.tools.registry import ToolRegistry

        sub_registry = ToolRegistry()
        for name in allowed_names:
            tool = self._parent._tool_registry.get(name)
            if tool is not None:
                sub_registry.register(tool)

        # 6. 选 provider
        # 本期简化：不按 model 切换 provider，直接用父 provider
        provider = self._parent._provider

        # 7. 权限模式
        from csycode.permission import Mode as PMode
        from csycode.permission import parse_mode

        perm_mode = PMode.DEFAULT
        if defi.permission_mode and defi.permission_mode != "default":
            parsed, ok = parse_mode(defi.permission_mode)
            if ok:
                perm_mode = parsed

        # 8. 构造子 Agent
        from csycode.agent.loop import Agent
        from csycode.conversation import Conversation
        from csycode.config import AgentConfig

        sub_conv = Conversation()
        if defi.is_fork():
            # Fork 路径：克隆父对话 + 装填 Boilerplate
            from .fork import build_forked_messages

            parent_msgs = self._parent._conversation.messages()
            forked = build_forked_messages(parent_msgs, a.prompt)
            sub_conv = Conversation.from_messages(forked)

        sub_agent = Agent(
            provider=provider,
            tool_registry=sub_registry,
            conversation=sub_conv,
            config=AgentConfig(
                max_iterations=defi.max_turns if defi.max_turns > 0 else 25,
            ),
            version=self._parent._version,
            engine=self._parent._engine,
            context_window=self._parent.context_window,
            system_prompt=defi.system_prompt,
            max_turns=defi.max_turns,
            permission_mode=perm_mode,
            dont_ask=defi.dont_ask,
            approval_upgrader=None,  # 本期不实现升级审批
            hook_engine=self._parent._hook_engine,
        )
        sub_agent.is_sub_agent = True
        sub_agent.parent_id = getattr(self._parent, "agent_id", "")

        # 9. Worktree 隔离分支（对齐 mewcode: 定义或参数均可启用）
        if isolation == "worktree":
            if self._worktree_mgr is None:
                return ToolResult(
                    success=False,
                    content="",
                    error="Worktree 管理器未配置，无法使用 isolation:worktree",
                )
            # isolation:worktree 强制前台同步（spec F23: 本期最小实现）
            events: asyncio.Queue = asyncio.Queue(maxsize=64)
            try:
                from .agent_worktree import _execute_with_worktree

                final_text = await _execute_with_worktree(
                    self._worktree_mgr,
                    defi,
                    sub_agent,
                    sub_conv,
                    a.prompt,
                    events,
                )
                return ToolResult(success=True, content=final_text)
            except Exception as e:
                log.warning("Worktree 子 Agent 异常: %s", e)
                return ToolResult(
                    success=False,
                    content="",
                    error=f"Worktree 子 Agent 执行异常: {e}",
                )

        # 10. 启动
        if background:
            task_id = await self._task_mgr.launch(
                sub_agent,
                sub_conv,
                a.name,
                a.prompt if not defi.is_fork() else "",
            )
            return ToolResult(
                success=True,
                content=json.dumps(
                    {"task_id": task_id, "status": "async_launched"},
                    ensure_ascii=False,
                ),
            )
        else:
            # 前台路径（带超时自动切后台）
            events: asyncio.Queue = asyncio.Queue(maxsize=64)

            try:
                final_text = await asyncio.wait_for(
                    sub_agent.run_to_completion(sub_conv, a.prompt, events),
                    timeout=AUTO_BACKGROUND_SECONDS,
                )
                return ToolResult(success=True, content=final_text)
            except asyncio.TimeoutError:
                # 超时自动切后台
                running_handle = asyncio.create_task(
                    sub_agent.run_to_completion(sub_conv, "", events)
                )
                task_id = await self._task_mgr.adopt_running(
                    sub_agent, sub_conv, a.name, events, running_handle, a.prompt
                )
                return ToolResult(
                    success=True,
                    content=json.dumps(
                        {"task_id": task_id, "status": "timed_out_to_background"},
                        ensure_ascii=False,
                    ),
                )
            except Exception as e:
                log.warning("前台子 Agent 异常: %s", e)
                return ToolResult(
                    success=False,
                    content="",
                    error=f"子 Agent 执行异常: {e}",
                )

    # ── ch15: Team spawn 分支 ─────────────────────────────────────

    async def _execute_team_spawn(self, a: AgentArgs, kwargs: dict) -> ToolResult:
        """处理 team_name 非空的 Team spawn 分支。

        委托给 TeamHook.spawn_teammate，由 team.Manager 执行完整 spawn 流程。
        """
        if self._team_hook is None:
            return ToolResult(
                success=False,
                content="",
                error="Team 功能未初始化（team_hook 为空）",
            )

        # 校验调用者权限：in-process 队员不允许再 spawn
        ctx = kwargs.get("_ctx", {})
        if isinstance(ctx, dict):
            team_name, member_name, is_ip = self._team_hook.is_teammate_context(ctx)
            if is_ip:
                return ToolResult(
                    success=False,
                    content="in-process 队员不允许再 spawn 子队员",
                    error="in_process_teammate_no_spawn",
                )

        # 构造 TeamSpawnRequest 并委托
        from csycode.agent.team_hook import TeamSpawnRequest

        req = TeamSpawnRequest(
            team_name=a.team_name,
            member_name=a.name,
            subagent_type=a.subagent_type,
            model=a.model,
            prompt=a.prompt,
            description=a.description,
            plan_mode_required=False,  # 由 subagent 定义决定
            isolation=a.isolation,
        )

        try:
            result_json = await self._team_hook.spawn_teammate(req)
            return ToolResult(success=True, content=result_json)
        except Exception as e:
            log.warning("Team spawn 失败: %s", e)
            return ToolResult(
                success=False,
                content="",
                error=f"Team spawn 失败: {e}",
            )

    # ── parent 回填 ───────────────────────────────────────────────

    def set_parent(self, agent: "Agent") -> None:
        """CLI 在 csyCodeApp 构造后回填父 Agent 引用。"""
        self._parent = agent
