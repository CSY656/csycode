"""Textual App — state machine, compose layout, key bindings, streaming."""

from __future__ import annotations

import asyncio
import os
import time
from enum import Enum
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import OptionList, RichLog, Static
from textual.widgets import TextArea

from rich.text import Text

from .widgets import SubmitTextArea
from textual.widgets.option_list import Option

from csycode import __version__
from csycode.agent import (
    Agent,
    ApprovalRequest,
    CompactNotification,
    CompactPhase,
    LoopEnd,
    LoopProgress,
    TextDelta,
    TokenUsage,
    ToolCallEnd,
    ToolCallStart,
    ToolUseEvent,
)
from csycode.agent.plan_mode import PlanModeFilter
from csycode.command.command import Kind
from csycode.command.registry import Registry
from csycode.command.builtins import register_builtins
from csycode.command.dispatch import parse
from csycode.config import Config, effective_context_window
from csycode.conversation import Conversation
from csycode.effort import (
    DEFAULT_REASONING_EFFORT,
    ReasoningEffort,
    parse_reasoning_effort,
)
from csycode.llm import Provider, new_provider
from csycode.permission import Engine, Mode, Outcome
from csycode.prompt import render_banner
from csycode.tools import create_default_registry
from csycode.tools.load_skill import LoadSkill
from csycode.tools.install_skill import InstallSkillTool
from csycode.tools.registry import ToolRegistry
from csycode.skills.loader import SkillLoader
from csycode.skills.executor import SkillExecutor

from .complete import (
    CompletionMenu,
    _execute_selected_completion,
    _render_completion,
    _sync_completion_from_input,
)

from .approval_widget import InlineApprovalWidget
from .view import (
    assistant_block,
    error_block,
    help_content,
    plan_mode_banner,
    streaming_text,
    tool_call_block,
    tool_error_block,
    tool_result_block,
    tool_result_panel,
    turn_separator,
    user_block,
)

# ── 历史文件路径 ─────────────────────────────────────────────────
HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".csycode_history")
MAX_HISTORY = 500


class SessionState(Enum):
    SELECTING = "selecting"
    IDLE = "idle"
    STREAMING = "streaming"
    APPROVING = "approving"  # ch06: 人在回路待批准态
    RESUMING = "resuming"    # ch09: 会话恢复选择态


class csyCodeApp(App):
    """Terminal LLM chat client with multi-protocol support."""

    CSS = """
    #log {
        height: 1fr;
        border: none;
        background: $surface;
    }

    #streaming {
        height: auto;
        min-height: 0;
        max-height: 12;
        padding: 0 1;
        color: $text;
        overflow-y: auto;
    }

    #inline-widgets {
        height: auto;
        min-height: 0;
    }

    #input-container {
        height: auto;
        min-height: 3;
        border: solid $primary;
        padding: 0 1;
    }

    #mode-label {
        height: 1;
        padding: 0 0;
        color: $text;
        text-style: bold;
    }

    #input {
        height: auto;
        min-height: 1;
        max-height: 10;
        border: none;
        background: $surface;
    }

    #statusbar {
        height: 1;
        dock: bottom;
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    #selector {
        height: 1fr;
        padding: 1 2;
    }

    #selector-label {
        padding: 1 0;
        text-style: bold;
        color: $text;
    }

    .hidden {
        display: none;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Interrupt", show=True),
        Binding("ctrl+d", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_screen", "清屏", show=True),
    ]

    def __init__(
        self,
        config: Config,
        engine: Engine | None = None,
        registry: ToolRegistry | None = None,
        *,
        work_dir: str = "",
        writer: Any = None,
        mem_mgr: Any = None,
        instruction_text: str = "",
        hook_engine: Any = None,  # ch12: HookEngine | None
        task_mgr: Any = None,  # ch13: TaskManager
        subagent_catalog: Any = None,  # ch13: subagent.Catalog
        worktree_mgr: Any = None,  # ch14: Worktree Manager
        team_mgr: Any = None,  # ch15: Team Manager
        coordinator_mode: bool = False,  # ch15: Coordinator Mode
        name_reg: Any = None,  # ch15: AgentNameRegistry
    ) -> None:
        super().__init__()
        self._providers = config.providers
        self._tool_config = config.tools
        self._agent_config = config.agent
        self._external_registry = registry
        self.provider: Provider | None = None
        self.conv = Conversation()
        self.cur_reply: str = ""
        self.turn_start: float = 0.0
        self._stream_task: asyncio.Task | None = None
        self._timer_task: asyncio.Task | None = None
        self.state: SessionState = SessionState.IDLE
        self.tool_registry: ToolRegistry | None = None
        self._protocol: str = ""
        self._plan_mode_filter: PlanModeFilter | None = None
        self._reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT

        # ── ch08: Agent 引用 ──────────────────────────────────────
        self.agent: Agent | None = None
        self._context_window: int = 200000

        # ── ch09: 会话持久化 & 记忆 ──────────────────────────────
        self._work_dir = work_dir or str(Path.cwd().resolve())
        self._writer: Any = writer
        self._mem_mgr: Any = mem_mgr
        self._instruction_text = instruction_text
        self._resume_candidates: list[Any] = []
        self._resume_in_progress: bool = False

        # ── ch10: 命令系统 & 补全菜单 ───────────────────────────
        self.cmd_registry: Registry | None = None
        self.completion: CompletionMenu = CompletionMenu()
        self._pending_println: list[str] = []
        self._usage_in: int = 0
        self._usage_out: int = 0
        # ── ch16: 最近一轮 LLM 请求的实际 input_tokens；None = 无记录 ──
        self._last_input_tokens: int | None = None

        # ── ch11: Skill 系统 ──────────────────────────────────
        self._skill_loader: SkillLoader | None = None
        self._skill_executor: SkillExecutor | None = None
        self._load_skill_tool: LoadSkill | None = None

        # ── ch13: SubAgent 系统 ────────────────────────────────
        self.task_mgr: Any = task_mgr
        self.subagent_catalog: Any = subagent_catalog
        self._install_skill_tool: InstallSkillTool | None = None
        self._skills_registered: bool = False

        # ── ch12: Hook 引擎 ──────────────────────────────────
        self.hook_engine = hook_engine

        # ── ch14: Worktree 管理 ─────────────────────────────
        self.worktree_mgr: Any = worktree_mgr
        self.active_cwd: str = ""
        # 启动时若已有 session，恢复 active_cwd
        if worktree_mgr is not None:
            session = worktree_mgr.current_session
            if session is not None:
                self.active_cwd = session.worktree_path

        # ── ch15: Team 系统 ─────────────────────────────────
        self.team_mgr: Any = team_mgr
        self.coordinator_mode: bool = coordinator_mode
        self.name_reg: Any = name_reg
        self.lead_mail_event: asyncio.Event = asyncio.Event()
        self._lead_mail_task: asyncio.Task | None = None

        # ── ch06: 权限系统 ──────────────────────────────────
        self.engine = engine
        self._mode: Mode = engine.start_mode if engine is not None else Mode.DEFAULT
        self.pending: ApprovalRequest | None = None
        self.approve_cursor: int = 2  # 默认 DENY_ONCE，更安全
        self._approval_widget: InlineApprovalWidget | None = None

        # ── AskUserQuestion 异步桥接 ──────────────────────────
        self._question_future: asyncio.Future | None = None
        """当 AskUserQuestion 等待用户回答时，此 Future 用于桥接 TUI 输入。"""

        # ── 输入历史 ──────────────────────────────────────────
        self._input_history: list[str] = []
        self._history_index: int = -1  # -1 表示正在编辑新文本
        self._history_saved: str = ""  # 进入历史导航前正在编辑的文本

    # ── Layout ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield RichLog(id="log", wrap=True, markup=True, highlight=True, max_lines=20000, auto_scroll=True)
        yield Static(id="streaming")
        yield Container(id="inline-widgets")  # ch12: HITL 审批等内联组件挂载点
        with Container(id="input-container"):
            yield Static("👤 You:", id="mode-label")
            yield SubmitTextArea(
                id="input",
                on_submit=self._handle_submit,
                on_history_up=self._history_up,
                on_history_down=self._history_down,
                # 参考 Agent/csycode 的 _cycle_mode_callback 全局回调模式，
                # 在 widget 层拦截 Shift+Tab / Ctrl+P，防止 Textual Screen
                # 的 focus_previous 绑定抢先
                on_cycle_mode=self.action_cycle_mode,
            )
        yield Static("", id="completion")  # ch10: 补全菜单
        yield Static(id="statusbar")
        # Provider selector (shown only during SELECTING, hidden otherwise)
        yield Static("Select a provider:", id="selector-label", classes="hidden")
        yield OptionList(id="selector", classes="hidden")

    def on_mount(self) -> None:
        """Initialize the UI after the DOM is ready."""
        log = self.query_one("#log", RichLog)

        # ── ch10: 命令注册中心 ──
        reg = Registry()
        register_builtins(
            reg,
            engine=self.engine,
            mem_mgr=self._mem_mgr,
            sessions_dir=str(Path(self._work_dir) / ".csycode" / "sessions"),
        )
        # ch15: 注册 /team 系列命令
        if self.team_mgr is not None:
            from csycode.command.builtin_team import register_team_commands
            register_team_commands(reg, self.team_mgr)
        self.cmd_registry = reg

        # 初始化工具注册中心（优先使用外部注入的 registry，ch07 MCP 集成）
        if self._external_registry is not None:
            self.tool_registry = self._external_registry
        else:
            self.tool_registry = create_default_registry(self._tool_config)

        # 初始化 Plan Mode 过滤器
        self._plan_mode_filter = PlanModeFilter(self.tool_registry)

        # 注入 AskUserQuestion 的异步回调（替代同步 input()，防止 TUI 卡死）
        ask_tool = self.tool_registry.get("ask_user_question")
        if ask_tool is not None:
            from csycode.tools.plan_tools import AskUserQuestion

            if isinstance(ask_tool, AskUserQuestion):
                ask_tool.set_question_handler(self._handle_question)

        # ── ch11: Skill 系统初始化 ──
        self._skill_loader = SkillLoader(self._work_dir)
        self._skill_loader.load_all()

        # 注册 /skill 管理命令（对齐 mewcode：启动时即注册，不等待 Agent 创建）
        from csycode.commands.handlers.skill import (
            _print_skill_list,
            _print_skill_info,
        )

        async def _handle_skill(ui, args: str = "") -> None:
            """处理 /skill list | info | reload 命令。

            使用闭包捕获 self，在调用时动态获取 executor/agent 等依赖，
            以支持 reload 子命令在 Agent 创建后正常工作。
            """
            loader = self._skill_loader
            if loader is None:
                ui.error("Skill 系统未初始化")
                return

            parts = args.strip().split(maxsplit=1)
            subcmd = parts[0] if parts else "list"
            sub_args = parts[1] if len(parts) > 1 else ""

            if subcmd == "list":
                _print_skill_list(ui, loader)
            elif subcmd == "info":
                _print_skill_info(ui, loader, sub_args)
            elif subcmd == "reload":
                # reload 依赖 executor + agent + cmd_registry + tool_registry
                # 这些在 Agent 创建后由 _wire_skills() 设置
                skills = loader.reload()

                if self.cmd_registry is not None:
                    from csycode.commands.handlers.skill_register import (
                        register_skill_commands,
                    )

                    register_skill_commands(
                        self.cmd_registry,
                        loader,
                        self._skill_executor,
                        self.tool_registry,
                    )

                if self.agent is not None:
                    catalog = loader.get_catalog()
                    if catalog:
                        lines = ["你可以使用以下 Skills：", ""]
                        for name, desc in catalog:
                            lines.append(f"- {name}: {desc}")
                        lines.append("")
                        lines.append(
                            "如果用户请求匹配某个 Skill 的描述，调用 LoadSkill 工具激活它。"
                        )
                        self.agent.set_skill_catalog("\n".join(lines))
                    else:
                        self.agent.set_skill_catalog("")

                ui.println(f"已重新加载 {len(skills)} 个 Skill")
            else:
                ui.error(
                    f"未知子命令：{subcmd}\n"
                    "用法：/skill list | /skill info <name> | /skill reload"
                )

        from csycode.command.command import Command

        self.cmd_registry.register(
            Command(
                name="skill",
                description="管理 Skill 技能包 [skill]",
                kind=Kind.LOCAL,
                handler=_handle_skill,
                aliases=["skills"],
            )
        )

        # 注册 LoadSkill 系统工具
        self._load_skill_tool = LoadSkill()
        self._load_skill_tool.set_loader(self._skill_loader)
        self.tool_registry.register(self._load_skill_tool)

        # 注册 InstallSkill 远程安装工具
        self._install_skill_tool = InstallSkillTool(
            user_skills_dir=str(Path(self._work_dir) / ".csycode" / "skills"),
            project_skills_dir=str(Path(self._work_dir) / ".csycode" / "skills"),
            loader=self._skill_loader,
        )
        self.tool_registry.register(self._install_skill_tool)

        # 统计工具
        all_tools = self.tool_registry.list_all()
        readonly_count = sum(1 for t in all_tools if t.is_readonly)

        # 欢迎横幅（含命令/快捷键/工具统计）
        banner = render_banner(__version__, os.getcwd(), len(all_tools), readonly_count)
        log.write(banner)

        if len(self._providers) == 1:
            try:
                self.provider = new_provider(self._providers[0])
            except Exception as e:
                log.write(Text(f"● Provider 初始化失败: {e}", style="bold red"))
                self._update_statusbar()
                return
            self._protocol = self._providers[0].protocol
            self._context_window = effective_context_window(self._providers[0])
            self._update_statusbar()
            self._enter_idle()
        else:
            self._enter_selecting()

        # 加载输入历史
        self._load_history()

        self.query_one("#input", SubmitTextArea).focus()

        # ── ch12: SessionStart emit ───────────────────────────
        if self.hook_engine is not None:
            asyncio.create_task(self._dispatch_session_start())

        # ── ch13: 启动 task notification 消费协程 ───────────────
        if self.task_mgr is not None:
            asyncio.create_task(self._consume_task_done())

        # ── ch15: 启动 Lead 邮箱轮询 ──────────────────────────────
        if self.team_mgr is not None:
            self._lead_mail_task = asyncio.create_task(self._consume_lead_mail())

    async def _consume_task_done(self) -> None:
        """消费 TaskManager done 队列（ch13）。"""
        from .tasks import consume_task_done

        await consume_task_done(self)

    # ── ch15: Lead 邮箱轮询 ───────────────────────────────────────

    async def _consume_lead_mail(self) -> None:
        """每秒轮询 Lead 邮箱，有新消息时唤醒 Lead。"""
        while True:
            try:
                await asyncio.sleep(1)
                if self.team_mgr is None:
                    continue

                msgs = await self.team_mgr.poll_lead_mailboxes()
                if not msgs:
                    continue

                # 构造 <team-update> reminder
                parts = ['<team-update>']
                for m in msgs:
                    content = m.content[:8000] if m.content else "(无内容)"
                    parts.append(
                        f"来自 {m.from_} (team={m.team_name}): {m.summary}\n"
                        f"{content}"
                    )
                parts.append('</team-update>')
                reminder = "\n".join(parts)

                # 推送到 pending_reminders
                if self.agent is not None:
                    self.agent.runtime.append_reminders([reminder])

                # 设置 lead_mail_event
                self.lead_mail_event.set()

                # 若 Lead 空闲，自动开启新轮
                if self.state == SessionState.IDLE:
                    await self._begin_autonomous_turn(msgs)

            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _begin_autonomous_turn(self, msgs: list) -> None:
        """Lead 空闲时自动开启新轮处理队员消息。"""
        try:
            names = ", ".join(set(m.from_ for m in msgs))
            trigger_msg = (
                f"[team-update] 队员发来新消息 ({names})，"
                f"请按 Coordinator 流程处理..."
            )
            # 通过 _submit 走正常流程
            await self._submit(trigger_msg)
        except Exception:
            pass

    # ── ch12: Hook dispatch helpers ─────────────────────────────────────

    def _base_payload(self, event_name: str) -> dict:
        """构建 hook 事件的通用 payload 字段。"""
        session_id = ""
        try:
            if self.agent is not None:
                session_id = self.agent.runtime.session.session_id
        except Exception:
            pass
        return {
            "event": event_name,
            "session_id": session_id,
            "cwd": self._work_dir,
            "mode": str(self._mode),
        }

    async def _dispatch_session_start(self) -> None:
        """派发 SessionStart 事件，收集 injected_prompts 写入 runtime。"""
        if self.hook_engine is None:
            return
        from csycode.hook.event import Event as HE

        payload = self._base_payload("SessionStart")
        result = await self.hook_engine.dispatch(HE.SESSION_START, payload)
        if result.injected_prompts and self.agent is not None:
            self.agent.runtime.append_reminders(result.injected_prompts)

    async def _dispatch_session_end(self) -> None:
        """派发 SessionEnd 事件。"""
        if self.hook_engine is None:
            return
        from csycode.hook.event import Event as HE

        payload = self._base_payload("SessionEnd")
        await self.hook_engine.dispatch(HE.SESSION_END, payload)

    async def _dispatch_session_resume(self) -> None:
        """派发 SessionResume 事件。"""
        if self.hook_engine is None:
            return
        from csycode.hook.event import Event as HE

        payload = self._base_payload("SessionResume")
        result = await self.hook_engine.dispatch(HE.SESSION_RESUME, payload)
        if result.injected_prompts and self.agent is not None:
            self.agent.runtime.append_reminders(result.injected_prompts)

    # ── State transitions ─────────────────────────────────────────────

    def _enter_idle(self) -> None:
        """Switch to IDLE state: show input, hide selector."""
        self.state = SessionState.IDLE
        self._set_selector_visible(False)
        self._update_mode_label()
        self.query_one("#input", SubmitTextArea).focus()
        # 自动滚动对话日志到底部
        try:
            log = self.query_one("#log", RichLog)
            log.scroll_end(animate=False)
        except Exception:
            pass

    def _enter_selecting(self) -> None:
        """Switch to SELECTING state: show provider list."""
        self.state = SessionState.SELECTING
        self._populate_selector()
        self._set_selector_visible(True)

    def _set_selector_visible(self, visible: bool) -> None:
        """Show or hide the provider selector widgets."""
        self.query_one("#selector-label", Static).set_class(not visible, "hidden")
        self.query_one("#selector", OptionList).set_class(not visible, "hidden")

    def _populate_selector(self) -> None:
        """Fill the OptionList with provider entries."""
        ol = self.query_one("#selector", OptionList)
        ol.clear_options()
        for i, p in enumerate(self._providers):
            ol.add_option(Option(f"{p.name}  —  {p.model}  ({p.protocol})", id=str(i)))

    # ── Mode label ────────────────────────────────────────────────────

    def _update_mode_label(self) -> None:
        """Refresh the mode label above the input area."""
        label = self.query_one("#mode-label", Static)
        if self._plan_mode_filter is not None and self._plan_mode_filter.is_plan_mode:
            label.update("📋 Plan:")
        else:
            label.update("👤 You:")

    # ── Status bar ────────────────────────────────────────────────────

    def _statusbar_left(self) -> str:
        """构造状态栏左侧文本：权限模式 [+ COORDINATOR] [+ " · N% context used"]。"""
        mode_display = {
            Mode.DEFAULT: "DEFAULT",
            Mode.ACCEPT_EDITS: "ACCEPT EDITS",
            Mode.PLAN: "PLAN",
            Mode.BYPASS: "BYPASS",
        }
        left = mode_display.get(self._mode, str(self._mode))
        # ch15: Coordinator Mode 标签
        if self.coordinator_mode:
            left += " [COORDINATOR]"
        left += f" · Effort: {self._reasoning_effort.upper()}"

        # ch16: 上下文窗口使用百分比
        if (
            self._last_input_tokens is not None
            and self._context_window is not None
            and self._context_window > 0
        ):
            # 使用 0.5 偏移取整实现真正的四舍五入（Python round 是银行家舍入）
            percent = int(self._last_input_tokens / self._context_window * 100 + 0.5)
            left += f" · {percent}% context used"

        return left

    def _render_statusbar(self) -> None:
        """状态栏唯一渲染入口：左侧 + 空格填充 + 右侧。"""
        if self.provider is None:
            return
        bar = self.query_one("#statusbar", Static)

        left = self._statusbar_left()
        right = f"{self.provider.model}  |  ↗{self._usage_in} ↑{self._usage_out}"

        width = self.size.width - 2 if self.size else 80
        spacing = max(1, width - len(left) - len(right) - 4)
        bar.update(f" {left}{' ' * spacing}{right} ")

    def _update_statusbar(self) -> None:
        """Refresh the status bar — left: permission mode, right: model + tokens."""
        self._render_statusbar()

    # ── Interrupt ──────────────────────────────────────────────────────

    async def action_interrupt(self) -> None:
        """中断当前 AI 回复（Ctrl+C），保留已生成的部分内容.

        同时处理 AskUserQuestion 等待中和 APPROVING 态中的取消。
        """
        # ── 人在回路待批准态取消 ──
        if self.state == SessionState.APPROVING and self.pending is not None:
            if not self.pending.respond.done():
                self.pending.respond.set_result(Outcome.DENY_ONCE)
            self.pending = None
            # 移除审批组件
            if self._approval_widget is not None:
                self._approval_widget.remove()
                self._approval_widget = None
            await self._cancel_turn()
            return

        # ── 取消正在等待的 AskUserQuestion ──
        if self._question_future is not None and not self._question_future.done():
            self._question_future.cancel()
            self._question_future = None
            self._update_question_label(False)

        if self.state != SessionState.STREAMING:
            return

        # 保存部分内容
        partial = self.cur_reply
        log = self.query_one("#log", RichLog)
        if partial:
            log.write(assistant_block(partial))
            self.conv.add_assistant(partial)

        await self._cancel_turn()

    # ── Quit ──────────────────────────────────────────────────────────

    async def action_quit(self) -> None:
        """退出程序（Ctrl+D 或 /exit）."""
        # ch12: SessionEnd emit
        await self._dispatch_session_end()

        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._save_history()
        self.exit()

    # ── Mode Cycle ──────────────────────────────────────────────────

    async def action_cycle_mode(self) -> None:
        """循环切换权限模式（Shift+Tab），仅 IDLE 态生效.

        DEFAULT → ACCEPT_EDITS → PLAN → BYPASS → DEFAULT
        """
        if self.state != SessionState.IDLE:
            return

        self._mode = Mode((int(self._mode) + 1) % 4)

        # 同步 PlanModeFilter（兼容旧逻辑）
        if self._plan_mode_filter is not None:
            if self._mode == Mode.PLAN:
                self._plan_mode_filter.enter_plan_mode()
            else:
                self._plan_mode_filter.enter_do_mode()

        self._update_mode_label()
        self._update_statusbar()

        # 提示块
        mode_labels = {
            Mode.DEFAULT: "DEFAULT — 只读放行，写入和命令需确认",
            Mode.ACCEPT_EDITS: "ACCEPT EDITS — 文件编辑放行，命令需确认",
            Mode.PLAN: "PLAN — 仅只读工具，模型产出计划",
            Mode.BYPASS: "BYPASS — 全部放行（危险命令仍拦截）",
        }
        msg = mode_labels.get(self._mode, str(self._mode))
        log = self.query_one("#log", RichLog)
        log.write(Text(f"● 已切换到 {msg}", style="bold blue"))

    # ── Clear screen ──────────────────────────────────────────────────

    async def action_clear_screen(self) -> None:
        """清屏（Ctrl+L）."""
        log = self.query_one("#log", RichLog)
        log.clear()

    # ── Provider selection callback ───────────────────────────────────

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """Handle provider selection from the OptionList."""
        if self.state == SessionState.SELECTING:
            idx = int(event.option_id)  # type: ignore[arg-type]
            cfg = self._providers[idx]
            try:
                self.provider = new_provider(cfg)
            except Exception as e:
                log = self.query_one("#log", RichLog)
                log.write(Text(f"● Provider 初始化失败: {e}", style="bold red"))
                return
            self._protocol = cfg.protocol
            self._context_window = effective_context_window(cfg)
            # ch09: 通知 mem_mgr 当前 provider
            if self._mem_mgr is not None:
                self._mem_mgr.set_provider(self.provider, self.provider.model)
            self._update_statusbar()
            self._enter_idle()
        elif self.state == SessionState.RESUMING:
            # ch09: 恢复选中会话（防止重复 Enter 启动多个恢复任务）
            if self._resume_in_progress:
                return
            self._resume_in_progress = True
            session_id = event.option_id
            if session_id and self._resume_candidates:
                for info in self._resume_candidates:
                    if info.id == session_id:
                        from .resume import do_resume_session
                        asyncio.create_task(do_resume_session(self, info))
                        break
            else:
                self._resume_in_progress = False

    # ── Submit ────────────────────────────────────────────────────────

    def _handle_submit(self, text: str) -> None:
        """Sync callback for SubmitTextArea — schedules async submit task.

        如果当前正在等待 AskUserQuestion 回答（_question_future 非 None），
        则将输入解析为问题答案，而不是启动新的 Agent 循环。
        """
        text = text.strip()
        if not text:
            return

        # ── AskUserQuestion 模式：将输入作为答案返回给等待中的工具 ──
        if self._question_future is not None and not self._question_future.done():
            input_widget = self.query_one("#input", SubmitTextArea)
            input_widget.clear()
            # 桥接：resolve future → 工具 _execute() 恢复 → Agent 循环继续
            self._question_future.set_result(text)
            return

        asyncio.create_task(self._submit(text))

    async def _handle_question(
        self,
        question: str,
        options: list[str] | None,
        multi_select: bool,
    ) -> str:
        """AskUserQuestion 的异步回调：显示问题并等待用户输入。

        通过 asyncio.Future 桥接 TUI 输入和工具执行：
        1. 在 RichLog 中显示问题
        2. 更新 mode label 为 "❓ Question"
        3. 创建 Future 并等待
        4. 用户提交后返回答案

        此方法在 Agent 的 asyncio Task 中被 await，不会阻塞事件循环。
        """
        log = self.query_one("#log", RichLog)

        # 构建问题显示
        lines = [f"❓ {question}"]
        if options:
            for i, opt in enumerate(options, 1):
                lines.append(f"  [{i}] {opt}")
            if multi_select:
                lines.append("（多选：输入编号，用逗号分隔）")

        log.write(Text("\n".join(lines), style="bold cyan"))

        # 更新 UI 状态
        self._update_question_label(True)

        # 创建 Future 并等待用户输入
        loop = asyncio.get_running_loop()
        self._question_future = loop.create_future()

        try:
            answer = await self._question_future
            return answer
        except asyncio.CancelledError:
            # 工具超时或 Agent 被取消 → 清理 Future
            if self._question_future is not None and not self._question_future.done():
                self._question_future.cancel()
            return ""
        finally:
            self._question_future = None
            self._update_question_label(False)

    def _update_question_label(self, in_question: bool) -> None:
        """更新 mode label：提问模式显示 "❓ Question:"，否则恢复。"""
        label = self.query_one("#mode-label", Static)
        if in_question:
            label.update("❓ Question:")
        else:
            self._update_mode_label()

    async def _submit(self, text: str) -> None:
        """Process user input — 命令走 dispatch_slash，普通文本走 Agent Loop。

        ch12: 非 slash 路径先经过 UserPromptSubmit hook 拦截检查。
        """
        text = text.strip()
        if not text:
            return

        input_widget = self.query_one("#input", SubmitTextArea)

        # ── ch10: 统一命令分发 ──
        if await self.dispatch_slash(text):
            input_widget.clear()
            self.completion.hide()
            self._render_completion()
            return

        if self.state != SessionState.IDLE:
            return

        # ── ch12: UserPromptSubmit hook 拦截 ──────────────────
        if self.hook_engine is not None:
            from csycode.hook.event import Event as HE

            payload = self._base_payload("UserPromptSubmit") | {"prompt": text}
            result = await self.hook_engine.dispatch(HE.USER_PROMPT_SUBMIT, payload)
            if result.blocked:
                # 输入被拦截：显示错误提示，不消费输入
                log = self.query_one("#log", RichLog)
                from rich.text import Text
                log.write(Text(
                    f"[hook {result.blocking_hook_name}] {result.reason}",
                    style="bold red",
                ))
                return
            # 注入 prompt 到 runtime
            if result.injected_prompts and self.agent is not None:
                self.agent.runtime.append_reminders(result.injected_prompts)

        log = self.query_one("#log", RichLog)

        # Append user message to conversation and display
        self.conv.add_user(text)
        log.write(user_block(text))

        # ── 保存到输入历史 ──────────────────────────────────────
        self._add_to_history(text)

        # Clear input and prepare for streaming
        input_widget.clear()
        self.cur_reply = ""
        self.turn_start = time.monotonic()
        self.state = SessionState.STREAMING

        # Start the Agent Loop consumer task
        self._stream_task = asyncio.create_task(self._run_agent())

        # Start the timer for "Imagining… (Ns)" display
        self._timer_task = asyncio.create_task(self._tick_timer())

    # ── Help & Clear ──────────────────────────────────────────────────

    # ── ch11: Skill 接线 ──────────────────────────────────────

    def _wire_skills(self) -> None:
        """Agent 创建后完成 Skill 系统的接线。

        1. 设置 LoadSkill 工具的 agent 引用
        2. 构建 skill catalog 文本并注入 agent
        3. 创建 SkillExecutor
        4. 注册 skill 命令
        5. 设置 InstallSkill 的安装完成回调
        """
        if self._skill_loader is None or self.agent is None:
            return

        # 1. LoadSkill 注入 agent 引用
        if self._load_skill_tool is not None:
            self._load_skill_tool.set_agent(self.agent)

        # 2. 构建 catalog 文本
        catalog = self._skill_loader.get_catalog()
        if catalog:
            lines = ["你可以使用以下 Skills：", ""]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append(
                "如果用户请求匹配某个 Skill 的描述，调用 LoadSkill 工具激活它。"
            )
            self.agent.set_skill_catalog("\n".join(lines))

        # 3. 创建执行器
        self._skill_executor = SkillExecutor(agent=self.agent)

        # 4. 注册 skill 命令（仅首次）
        #    注：/skill 管理命令已在 on_mount() 中注册，
        #    此处只注册 /<skill-name> 动态命令
        if not self._skills_registered and self.cmd_registry is not None:
            from csycode.commands.handlers.skill_register import (
                register_skill_commands,
            )

            register_skill_commands(
                self.cmd_registry,
                self._skill_loader,
                self._skill_executor,
                self.tool_registry,
            )

            self._skills_registered = True

        # 5. 设置 InstallSkill 安装完成回调（对齐 mewcode）
        #    安装新 skill 后自动重新注册斜杠命令
        if self._install_skill_tool is not None:

            def _on_skill_installed(name: str) -> None:
                from csycode.commands.handlers.skill_register import (
                    register_skill_commands,
                )

                register_skill_commands(
                    self.cmd_registry,
                    self._skill_loader,
                    self._skill_executor,
                    self.tool_registry,
                )

            self._install_skill_tool.set_on_installed(_on_skill_installed)

    # ── ch13: Agent Catalog 注入 ──────────────────────────────────────

    def _inject_agent_catalog(self) -> None:
        """将 SubAgent 角色列表注入到主 Agent 的 system prompt。

        对齐 mewcode：在 provider 选定后执行一次，
        格式化 catalog 文本并设置到 agent。
        """
        if self.subagent_catalog is None or self.agent is None:
            return

        defs = self.subagent_catalog.list_all()
        if not defs:
            return

        lines = ["## Available Sub-Agent Types", ""]
        for d in defs:
            lines.append(f"- **{d.name}**: {d.description}")
        lines.append("")
        lines.append(
            "使用 Agent 工具启动子 Agent 时，"
            "通过 subagent_type 参数指定上述角色名之一。"
        )

        self.agent.set_agent_catalog("\n".join(lines))

    def _wire_agent_tool_parent(self) -> None:
        """ch13: Agent 工具注册后回填父 Agent 引用。

        对齐 mewcode：AgentTool 在 provider 选定 + Agent 构造后
        才拿到 parent_agent。CLI 注册时 parent=None，
        此处回填保证子 Agent 能正确获取 provider / registry / engine 等。
        """
        if self.tool_registry is None or self.agent is None:
            return
        agent_tool = self.tool_registry.get("Agent")
        if agent_tool is not None and hasattr(agent_tool, "set_parent"):
            agent_tool.set_parent(self.agent)

    def _refresh_skills_if_needed(self) -> None:
        """每轮对话前检查 skill 目录 modtime，有变化则自动 reload。

        对齐 mewcode: 当用户在外部手动添加/删除 skill 文件时，
        自动重新扫描并刷新斜杠命令和 catalog。
        """
        if self._skill_loader is None or self.agent is None:
            return
        if not self._skill_loader.needs_reload():
            return

        self._skill_loader.reload()

        # 重新注册 skill 命令
        if self.cmd_registry is not None:
            from csycode.commands.handlers.skill_register import (
                register_skill_commands,
            )

            register_skill_commands(
                self.cmd_registry,
                self._skill_loader,
                self._skill_executor,
                self.tool_registry,
            )

        # 刷新 agent 的 skill catalog
        catalog = self._skill_loader.get_catalog()
        if catalog:
            lines = ["你可以使用以下 Skills：", ""]
            for name, desc in catalog:
                lines.append(f"- {name}: {desc}")
            lines.append("")
            lines.append(
                "如果用户请求匹配某个 Skill 的描述，调用 LoadSkill 工具激活它。"
            )
            self.agent.set_skill_catalog("\n".join(lines))
        else:
            self.agent.set_skill_catalog("")

    def _show_help(self) -> None:
        """显示帮助信息."""
        log = self.query_one("#log", RichLog)
        if self.tool_registry is None:
            return

        all_tools = self.tool_registry.list_all()
        readonly = [t.name for t in all_tools if t.is_readonly]
        side_effect = [t.name for t in all_tools if not t.is_readonly]

        help_md = help_content(readonly, side_effect)
        log.write(assistant_block(help_md))

    def _clear_conversation(self) -> None:
        """清空对话历史，保留 TUI 界面框架，重新显示欢迎横幅."""
        log = self.query_one("#log", RichLog)
        log.clear()
        self.conv = Conversation()
        # ── ch11: 清空激活的 Skill ──
        if self.agent is not None:
            self.agent.clear_active_skills()
        # 重新渲染欢迎横幅，保持 TUI 界面完整
        all_tools = self.tool_registry.list_all() if self.tool_registry else []
        readonly_count = sum(1 for t in all_tools if t.is_readonly)
        banner = render_banner(__version__, os.getcwd(), len(all_tools), readonly_count)
        log.write(banner)

    # ── Input history ─────────────────────────────────────────────────

    def _add_to_history(self, text: str) -> None:
        """Add a submitted text to the input history (dedup consecutive)."""
        # 去重连续重复
        if self._input_history and self._input_history[-1] == text:
            return
        self._input_history.append(text)
        # 限制历史数量
        if len(self._input_history) > MAX_HISTORY:
            self._input_history = self._input_history[-MAX_HISTORY:]
        self._history_index = -1
        self._history_saved = ""

    def _history_up(self) -> str | None:
        """Ctrl+↑ 回调：上一条历史."""
        if not self._input_history:
            return None

        input_widget = self.query_one("#input", SubmitTextArea)

        # 首次进入历史导航时保存当前正在编辑的文本
        if self._history_index == -1:
            self._history_saved = input_widget.text

        if self._history_index < len(self._input_history) - 1:
            self._history_index += 1
            return self._input_history[-(self._history_index + 1)]

        # 已到最旧的一条
        return None

    def _history_down(self) -> str | None:
        """Ctrl+↓ 回调：下一条历史."""
        if self._history_index == -1:
            return None

        if self._history_index > 0:
            self._history_index -= 1
            text = self._input_history[-(self._history_index + 1)]
            return text

        # 回到最新（恢复进入历史导航前正在编辑的文本）
        self._history_index = -1
        return self._history_saved

    def _load_history(self) -> None:
        """从文件加载输入历史."""
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                self._input_history = [line.rstrip("\n") for line in f if line.strip()][
                    -MAX_HISTORY:
                ]
        except FileNotFoundError:
            self._input_history = []

    def _save_history(self) -> None:
        """保存输入历史到文件."""
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                for line in self._input_history:
                    f.write(line + "\n")
        except OSError:
            pass  # 静默失败，不影响退出体验

    # ── Agent Loop ────────────────────────────────────────────────────

    async def _run_agent(self) -> None:
        """创建/复用 Agent 实例，订阅事件流，驱动 UI 更新。"""
        log = self.query_one("#log", RichLog)
        streaming_widget = self.query_one("#streaming", Static)

        # ── ch11: Skill 自动刷新（目录变更检测）──
        self._refresh_skills_if_needed()

        # ch08: 延迟构造 Agent，ch09: 传入 instructions_content / mem_mgr
        if self.agent is None:
            self.agent = Agent(
                provider=self.provider,  # type: ignore[arg-type]
                tool_registry=self.tool_registry,  # type: ignore[arg-type]
                conversation=self.conv,
                config=self._agent_config,
                version=__version__,
                engine=self.engine,
                plan_mode_filter=self._plan_mode_filter,
                context_window=self._context_window,
                work_dir=str(Path.cwd().resolve()),
                instructions_content=self._instruction_text,
                mem_mgr=self._mem_mgr,
                hook_engine=self.hook_engine,  # ch12
                reasoning_effort=self._reasoning_effort,
            )

            # ch12: 将 hook_engine 注入 runtime
            self.agent.runtime.hook_engine = self.hook_engine

            # ── ch11: Skill 系统接线（Agent 创建后）──
            self._wire_skills()

            # ── ch13: Agent Catalog 注入（SubAgent 角色列表）──
            self._inject_agent_catalog()

            # ── ch13: 回填 Agent 工具的 parent 引用 ──
            self._wire_agent_tool_parent()

            # ── ch15: Coordinator Mode 应用 ──
            if self.coordinator_mode:
                from csycode.coordinator import disabled_tools, system_prompt_suffix

                # 禁用 write_file / edit_file（Coordinator 只能用 bash 写文件）
                for name in disabled_tools():
                    try:
                        self.tool_registry.disable(name)
                    except Exception:
                        pass

                # 追加 coordinator 系统提示词到 Agent
                self.agent._system_prompt += "\n" + system_prompt_suffix()

        round_text = ""
        # 工具过程仅供内部调度使用，主对话日志不展示调用详情。
        show_tool_details = False

        # ── ch14: Worktree cwd 注入 ──
        from csycode.tools.ctx import _ctx_cwd as _cwd_var

        _cwd_token = _cwd_var.set(self._effective_cwd())
        # 让出事件循环一次，确保 Textual 处理完挂起的 UI 事件后再
        # 进入长时间阻塞的 agent 循环（对齐 mewcode app.py:1375）。
        await asyncio.sleep(0)
        try:
            async for event in self.agent.run(self._mode):
                if isinstance(event, TextDelta):
                    self.cur_reply += event.text
                    round_text += event.text
                    elapsed = max(0, time.monotonic() - self.turn_start)
                    streaming_widget.update(streaming_text(self.cur_reply, elapsed))

                elif isinstance(event, ToolUseEvent):
                    # 流期间工具调用（对齐 mewcode）：LLM 还未说完，工具已开始执行
                    call_label = tool_call_block(event.tool_name, event.arguments)
                    if show_tool_details:
                        log.write(call_label)

                elif isinstance(event, ToolCallStart):
                    if round_text:
                        round_text = ""
                        self.cur_reply = ""
                        elapsed = max(0, time.monotonic() - self.turn_start)
                        streaming_widget.update(streaming_text("", elapsed))
                    call_label = tool_call_block(event.tool_name, event.tool_args)
                    if show_tool_details:
                        log.write(call_label)
                        streaming_widget.update(str(call_label))

                elif isinstance(event, ToolCallEnd):
                    if event.success:
                        if show_tool_details:
                            log.write(tool_result_block(event.tool_name, True))
                        # ch08: 展示原始输出（若被 offload 则展示 TUI 预览 + 落盘路径）
                        if show_tool_details and event.show_result_to_user:
                            if event.offloaded:
                                # 被落盘：显示预览体（含路径提示）
                                log.write(
                                    tool_result_panel(
                                        event.tool_name,
                                        event.content
                                        + f"\n\n[已存盘: {event.offload_path}]",
                                    )
                                )
                            elif event.original_output:
                                log.write(
                                    tool_result_panel(
                                        event.tool_name, event.original_output
                                    )
                                )
                    else:
                        log.write(
                            tool_error_block(event.tool_name, event.error or "未知错误")
                        )

                elif isinstance(event, LoopProgress):
                    if show_tool_details and event.round_num > 1 and event.status == "thinking":
                        log.write(turn_separator(event.round_num))
                    if show_tool_details and event.status == "executing":
                        streaming_widget.update(
                            f"🔧 执行工具中… (第 {event.round_num}/{event.max_rounds} 轮)"
                        )

                elif isinstance(event, TokenUsage):
                    self._update_token_display(event)

                elif isinstance(event, CompactNotification):
                    # ch08: 压缩通知（自动/紧急路径的 phase 事件 + 手动路径回投）
                    from .commands import format_compact_notice

                    if event.phase is not None:
                        # 自动/紧急路径：使用 format_compact_notice 渲染统一文案
                        notice = format_compact_notice(
                            phase=event.phase,
                            before=event.before_tokens,
                            after=event.after_tokens,
                            err=event.error,
                        )
                        if event.error:
                            log.write(Text(f"⚠ {notice}", style="bold red"))
                        elif event.phase in (
                            CompactPhase.BEFORE_AUTO,
                            CompactPhase.BEFORE_EMERGENCY,
                        ):
                            log.write(Text(f"⏳ {notice}", style="bold blue"))
                        else:
                            log.write(Text(f"📦 {notice}", style="bold green"))
                    elif event.error:
                        log.write(Text(f"⚠ 压缩失败: {event.error}", style="bold red"))
                    else:
                        log.write(
                            Text(
                                f"📦 {event.message}",
                                style="bold green",
                            )
                        )

                elif isinstance(event, ApprovalRequest):
                    # ── ch06: 人在回路 ──
                    # 先落盘累积的文本
                    if round_text:
                        log.write(assistant_block(round_text))
                        round_text = ""
                        self.cur_reply = ""
                        streaming_widget.update("")
                    # 挂载内联审批组件（对齐 mewcode InlinePermissionWidget）
                    # ch12 fix: mount 到 #inline-widgets（正常 Container），
                    # 而非 #log（RichLog/ScrollView 不渲染子 widget）。
                    self.pending = event
                    self.state = SessionState.APPROVING
                    # 挂载前清除可能残留的旧审批 widget（同一轮次多工具时防止 ID 冲突）
                    if self._approval_widget is not None:
                        self._approval_widget.remove()
                        self._approval_widget = None
                    self._approval_widget = InlineApprovalWidget(
                        tool_name=event.name,
                        args_preview=event.args,
                        reason=event.reason,
                        default_cursor=2,
                    )
                    inline = self.query_one("#inline-widgets", Container)
                    await inline.mount(self._approval_widget)
                    # 审批期间禁用输入框（对齐 mewcode），防止焦点被夺走导致
                    # Future 永不 resolve → agent 永久阻塞在 await respond。
                    try:
                        self.query_one("#input", SubmitTextArea).disabled = True
                    except Exception:
                        pass
                    # agent 此时正在 await event.respond (Future)
                    # event.respond 由 on_inline_approval_widget_responded 解除

                elif isinstance(event, LoopEnd):
                    if round_text:
                        self._finish_with_assistant(round_text)
                    elif event.final_text:
                        self._finish_with_assistant(event.final_text)
                    elif event.reason == "plan_submitted":
                        # 同步重置 TUI 模式，避免 status bar 仍显示 PLAN
                        # 而 PlanModeFilter 已经切换到 do_mode 的不一致状态
                        self._mode = Mode.DEFAULT
                        log.write(
                            plan_mode_banner(
                                "计划已生成 — 已自动退出 Plan Mode，请审阅计划文件"
                            )
                        )
                        streaming_widget.update("[计划已提交]")
                        self._update_mode_label()
                        self._update_statusbar()
                        self._cleanup_stream()
                    elif event.reason == "user_cancel":
                        log.write(Text("● [Interrupted]", style="bold yellow"))
                        streaming_widget.update("[Interrupted]")
                        self._cleanup_stream()
                    elif event.reason == "max_rounds":
                        log.write(
                            Text(
                                f"● [达到最大迭代轮数 ({event.total_rounds})]",
                                style="bold yellow",
                            )
                        )
                        streaming_widget.update(
                            f"[达到最大迭代轮数: {event.total_rounds}]"
                        )
                        self._cleanup_stream()
                    elif event.reason == "unknown_tools":
                        log.write(
                            Text("● [模型连续调用未知工具，已停止]", style="bold red")
                        )
                        streaming_widget.update("[未知工具错误]")
                        self._cleanup_stream()
                    elif event.reason == "stream_error":
                        err_detail = f": {event.error_msg}" if event.error_msg else ""
                        log.write(Text(f"● [LLM 流式响应异常{err_detail}]", style="bold red"))
                        streaming_widget.update("[流错误]")
                        self._cleanup_stream()
                    else:
                        self._cleanup_stream()
                    return

        except asyncio.CancelledError:
            if round_text:
                log.write(assistant_block(round_text))
                self.conv.add_assistant(round_text)
            log.write(Text("● [Interrupted]", style="bold yellow"))
            streaming_widget.update("[Interrupted]")
            self._cleanup_stream()
        except Exception as e:
            self._finish_with_error(e)
        finally:
            _cwd_var.reset(_cwd_token)

    def _update_token_display(self, event: TokenUsage) -> None:
        """收到 TokenUsage 事件时更新累计用量与上下文使用百分比。"""
        # ch10: 累计用量
        self._usage_in += event.input_tokens
        self._usage_out += event.output_tokens

        # ch16: 记录最近一轮请求的实际 input_tokens，用于状态栏百分比
        self._last_input_tokens = event.input_tokens

        self._render_statusbar()

    async def _tick_timer(self) -> None:
        """Periodically refresh the timer display during streaming."""
        streaming_widget = self.query_one("#streaming", Static)
        try:
            while True:
                await asyncio.sleep(0.1)
                if self.state == SessionState.STREAMING:
                    elapsed = max(0, time.monotonic() - self.turn_start)
                    streaming_widget.update(streaming_text(self.cur_reply, elapsed))
                    # 流式输出期间自动滚动日志到底部
                    try:
                        log = self.query_one("#log", RichLog)
                        log.scroll_end(animate=False)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass

    def _finish_with_assistant(self, reply: str) -> None:
        """Handle normal stream completion — render markdown, update history."""
        log = self.query_one("#log", RichLog)

        if reply:
            log.write(assistant_block(reply))
            self.conv.add_assistant(reply)

        # 立即滚动到底部显示结果
        log.scroll_end(animate=False)

        # 短暂显示完成提示后清空
        elapsed = max(0, time.monotonic() - self.turn_start)
        streaming_widget = self.query_one("#streaming", Static)
        streaming_widget.update(f"✓ 完成 ({elapsed:.1f}s)")

        self._cleanup_stream()

    def _finish_with_error(self, err: Exception) -> None:
        """Handle stream error — show red error block, stay alive."""
        log = self.query_one("#log", RichLog)

        log.write(error_block(err))
        log.scroll_end(animate=False)

        self._cleanup_stream()

    def _cleanup_stream(self) -> None:
        """Stop timer, clear stream task, return to IDLE."""
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = None
        self._stream_task = None
        self.state = SessionState.IDLE
        # 清空 streaming 区域，避免残留"✓ 完成"文字占用空间
        streaming_widget = self.query_one("#streaming", Static)
        streaming_widget.update("")
        # 滚动对话日志到底部
        log = self.query_one("#log", RichLog)
        log.scroll_end(animate=False)
        # 清理残留的 AskUserQuestion Future（流异常终止时）
        if self._question_future is not None and not self._question_future.done():
            self._question_future.cancel()
            self._question_future = None
            self._update_question_label(False)
        # 清理残留的审批组件（流异常终止时）
        if self._approval_widget is not None:
            self._approval_widget.remove()
            self._approval_widget = None
        self._update_statusbar()
        self._update_mode_label()
        self.query_one("#input", SubmitTextArea).focus()

    # ── Approval handling (ch06) ─────────────────────────────────────

    def on_inline_approval_widget_responded(
        self, event: InlineApprovalWidget.Responded
    ) -> None:
        """审批组件回调：resolve Agent 等待的 Future 并移除组件。"""
        if self.pending is not None and not self.pending.respond.done():
            self.pending.respond.set_result(event.outcome)
        self.pending = None
        # 移除审批组件
        if self._approval_widget is not None:
            self._approval_widget.remove()
            self._approval_widget = None
        # 重新启用输入框（对齐 mewcode）
        try:
            self.query_one("#input", SubmitTextArea).disabled = False
            self.query_one("#input", SubmitTextArea).focus()
        except Exception:
            pass
        self.state = SessionState.STREAMING
        event.stop()

    def on_key(self, event) -> None:
        """Handle raw key events for APPROVING state (menu navigation)."""
        # ── ch09: RESUMING 态的 Esc 取消 ──
        if self.state == SessionState.RESUMING:
            if event.key == "escape":
                self.state = SessionState.IDLE
                self._set_selector_visible(False)
                self._update_statusbar()
                self.query_one("#input", SubmitTextArea).focus()
                event.prevent_default()
            return

        # ── ch10: 补全键位拦截已移至 SubmitTextArea._on_key() ──
        # widget 层 _on_key 在 BINDINGS 之前触发，通过 event.stop()
        # 阻止事件冒泡，App.on_key 不再需要处理补全按键。

        if self.state != SessionState.APPROVING:
            # Let Textual handle normally (BINDINGS, etc.)
            return
        # APPROVING 态的按键由 InlineApprovalWidget 的 BINDINGS 处理，
        # Esc 仍走 BINDINGS → action_interrupt


    # ── Cancel turn helper ────────────────────────────────────────────

    async def _cancel_turn(self) -> None:
        """Cancel the current streaming/approval turn and return to IDLE."""
        log = self.query_one("#log", RichLog)
        streaming_widget = self.query_one("#streaming", Static)

        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = None
        self._stream_task = None

        # ch16 fix: 取消未决的 AskUserQuestion future，防止 Agent 永久阻塞
        if self._question_future is not None and not self._question_future.done():
            self._question_future.cancel()
            self._question_future = None

        log.write(Text("● [Cancelled]", style="bold yellow"))
        streaming_widget.update("[Cancelled]")

        self.state = SessionState.IDLE
        self._update_statusbar()
        self._update_mode_label()
        self.query_one("#input", SubmitTextArea).focus()

    # ── ch09: 会话恢复入口 ─────────────────────────────────────────────

    def begin_resume(self) -> None:
        """进入会话恢复选择界面。"""
        from .resume import begin_resume as _impl

        _impl(self)

    # ── ch10: UI Protocol 只读查询方法 (T9a) ────────────────────────

    def _mode_impl(self) -> Mode:
        return self._mode

    def _usage_in_impl(self) -> int:
        return self._usage_in

    def _usage_out_impl(self) -> int:
        return self._usage_out

    def _model_name_impl(self) -> str:
        return self.provider.model if self.provider else ""

    def _cwd_impl(self) -> str:
        return self._work_dir

    def _tool_count_impl(self) -> int:
        if self.tool_registry is not None:
            return self.tool_registry.count()
        return 0

    def _memory_files_impl(self) -> list[str]:
        if self._mem_mgr is None:
            return []
        try:
            project_files, user_files = self._mem_mgr.list_files()
            return project_files + user_files
        except Exception:
            return []

    def _session_path_impl(self) -> str:
        return self._writer.path if self._writer else ""

    def _session_id_impl(self) -> str:
        try:
            if self.agent is not None and self.agent.runtime is not None:
                return self.agent.runtime.session.session_id
        except Exception:
            pass
        return ""

    def _idle_impl(self) -> bool:
        return self.state == SessionState.IDLE

    def _reasoning_effort_impl(self) -> str:
        return self._reasoning_effort

    # ── ch10: UI Protocol 写入方法 (T9b) ───────────────────────────

    def _println_impl(self, msg: str) -> None:
        self._pending_println.append(msg)

    def _error_impl(self, msg: str) -> None:
        self._pending_println.append(f"ERROR\x00{msg}")

    def _set_mode_impl(self, m: Mode) -> None:
        self._mode = m
        if self._plan_mode_filter is not None:
            if m == Mode.PLAN:
                self._plan_mode_filter.enter_plan_mode()
            else:
                self._plan_mode_filter.enter_do_mode()
        self._update_mode_label()
        self._update_statusbar()

    def _set_reasoning_effort_impl(self, value: str) -> bool:
        parsed = parse_reasoning_effort(value)
        if parsed is None:
            return False
        self._reasoning_effort = parsed
        if self.agent is not None:
            self.agent.set_reasoning_effort(parsed)
        self._update_statusbar()
        return True

    def _quit_impl(self) -> None:
        asyncio.create_task(self.action_quit())

    def _force_compact_impl(self) -> None:
        from .commands import format_compact_notice
        from rich.text import Text

        if self.agent is None:
            self._error_impl("Agent 未初始化")
            self._flush_pending()
            return

        log = self.query_one("#log", RichLog)
        log.write(Text("正在压缩上下文...", style="bold blue"))

        async def _run_compact() -> None:
            try:
                result = await self.agent.manual_compact()
                if result is not None:
                    before, after = result
                    notice = format_compact_notice(before=before, after=after)
                    log.write(Text(f"📦 {notice}", style="bold green"))
                else:
                    log.write(Text("当前无需压缩（未达阈值或前缀太小）", style="bold yellow"))
            except Exception as e:
                notice = format_compact_notice(err=str(e))
                log.write(Text(f"⚠ {notice}", style="bold red"))

        asyncio.create_task(_run_compact())

    def _open_resume_menu_impl(self) -> None:
        """打开历史会话恢复列表（T10: 从 resume.py 迁移）。"""
        from .resume import begin_resume as _impl

        _impl(self)

    def _clear_and_new_session_impl(self) -> None:
        """关闭当前会话、开新会话、清空对话（T9b step 7）。

        ch12: 触发 SessionEnd → reset → SessionStart 序列。
        """
        from csycode.compact.state import new_session_context
        from csycode.session.writer import Writer

        # ch12: SessionEnd emit（关闭旧会话前）
        asyncio.create_task(self._dispatch_session_end())

        # a. 关闭旧 writer
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass

        # b. 新 SessionContext
        try:
            new_ses_ctx = new_session_context(self._work_dir)
        except Exception as e:
            self._error_impl(str(e))
            self._flush_pending()
            return

        # c. 新 Writer
        try:
            new_writer = Writer(new_ses_ctx.session_dir)
        except Exception as e:
            self._error_impl(str(e))
            self._flush_pending()
            return

        self._writer = new_writer

        # d. 重新构造 Conversation
        self.conv = self._bind_conversation(new_writer)

        # e. 重置 runtime + 更新 agent 的 conversation 引用
        #    （修复 Bug: 旧 conversation 的 on_append 指向已关闭的 writer，
        #     导致下次 run_agent 写 closed file 报 "I/O operation on closed file"）
        if self.agent is not None:
            self.agent._conversation = self.conv
            self.agent.runtime.reset_for_new_session(new_ses_ctx)
            # ── ch11: 清空激活的 Skill ──
            self.agent.clear_active_skills()

        # f. 重置计数
        self._usage_in = 0
        self._usage_out = 0
        # ── ch16: 清空上下文使用百分比记录 ──
        self._last_input_tokens = None
        self._reasoning_effort = DEFAULT_REASONING_EFFORT
        if self.agent is not None:
            self.agent.set_reasoning_effort(DEFAULT_REASONING_EFFORT)

        # g. 清屏
        try:
            log = self.query_one("#log", RichLog)
            log.clear()
        except Exception:
            pass

        # 重新显示 banner
        try:
            all_tools = self.tool_registry.list_all() if self.tool_registry else []
            readonly_count = sum(1 for t in all_tools if t.is_readonly)
            banner = render_banner(__version__, self._work_dir, len(all_tools), readonly_count)
            log = self.query_one("#log", RichLog)
            log.write(banner)
        except Exception:
            pass

        self._update_statusbar()

        # ch12: SessionStart emit（新会话就绪后）
        asyncio.create_task(self._dispatch_session_start())

    def _inject_and_send_impl(self, label: str, preset: str) -> None:
        """向对话注入 user 消息并触发 LLM 回合。"""
        self.conv.add_user(preset)
        try:
            log = self.query_one("#log", RichLog)
            log.write(user_block(label))
        except Exception:
            pass
        self._add_to_history(label)
        asyncio.create_task(self._start_turn())

    async def _start_turn(self) -> None:
        """开始一个新的 Agent 回合（供 inject_and_send 使用）。"""
        self.cur_reply = ""
        self.turn_start = time.monotonic()
        self.state = SessionState.STREAMING
        self._stream_task = asyncio.create_task(self._run_agent())
        self._timer_task = asyncio.create_task(self._tick_timer())

    # ── ch10: conversation 构造辅助 ────────────────────────────────

    def _bind_conversation(self, writer: Any) -> Conversation:
        """构造 Conversation 并绑定 writer 的 on_append/on_replace 回调。"""
        return Conversation(on_append=writer.on_append, on_replace=writer.on_replace)

    # ── ch10: 待输出刷新 ──────────────────────────────────────────

    def _flush_pending(self) -> None:
        """把 _pending_println 中的消息写入 RichLog 并清空缓冲。"""
        if not self._pending_println:
            return
        try:
            log = self.query_one("#log", RichLog)
            from rich.text import Text

            for msg in self._pending_println:
                if msg.startswith("ERROR\x00"):
                    content = msg[6:]
                    log.write(Text(f"⚠ {content}", style="bold red"))
                else:
                    log.write(Text(msg, style="bold blue"))
        except Exception:
            pass
        self._pending_println.clear()

    # ── ch10: dispatch_slash 分发入口 (T9c) ─────────────────────────

    async def dispatch_slash(self, text: str) -> bool:
        """解析并分发斜杠命令。

        支持多词命令名（如 /team list），贪心匹配最长的已注册命令名。

        Returns:
            True 表示输入已被命令系统消费，False 表示应走普通对话流程。
        """
        name, args, is_slash = parse(text)
        if not is_slash:
            return False

        self._pending_println.clear()

        if self.cmd_registry is None:
            self._pending_println.append("命令系统未初始化")
            self._flush_pending()
            return True

        # 贪心匹配多词命令名：/team list → 先试 "team list"，再试 "team"
        cmd, args = self._lookup_greedy(name, args)

        if cmd is None:
            # 未命中
            if name == "":
                self._pending_println.append("未知命令: 输入 /help 查看可用命令")
            else:
                self._pending_println.append(f"未知命令: /{name}。输入 /help 查看可用命令")
            self._flush_pending()
            return True

        # 非 idle 状态拒绝 UI/PROMPT 命令
        if cmd.kind in (Kind.UI, Kind.PROMPT) and self.state != SessionState.IDLE:
            from rich.text import Text

            try:
                log = self.query_one("#log", RichLog)
                log.write(Text("请等待当前任务完成", style="bold yellow"))
            except Exception:
                pass
            return True

        try:
            # 传入 args（向后兼容：老 handler 只接受 ui 参数时，忽略 args）
            try:
                await cmd.handler(self, args)
            except TypeError:
                await cmd.handler(self)
        except Exception as exc:
            from rich.text import Text

            try:
                log = self.query_one("#log", RichLog)
                log.write(Text(f"⚠ 命令执行失败: {exc}", style="bold red"))
            except Exception:
                pass

        self._flush_pending()
        return True

    def _lookup_greedy(self, name: str, args: str):  # -> tuple[Command | None, str]
        """贪心匹配多词命令名。

        例如 /team list → 先查 "team list"，命中则消耗 args 中的 "list"；
        未命中则回退到 "team"，保留完整 args。
        继续尝试更长的组合直到 args 耗尽。

        Returns:
            (cmd, remaining_args) — cmd 为 None 表示未命中。
        """
        # 先直接查（单词命令名）
        cmd = self.cmd_registry.lookup(name) if self.cmd_registry else None
        if cmd is not None:
            return cmd, args

        # 贪心尝试更长的命令名
        if not args:
            return None, args

        arg_words = args.split()
        for i in range(len(arg_words), 0, -1):
            candidate = name + " " + " ".join(arg_words[:i])
            cmd = self.cmd_registry.lookup(candidate) if self.cmd_registry else None
            if cmd is not None:
                remaining = " ".join(arg_words[i:]) if i < len(arg_words) else ""
                return cmd, remaining

        return None, args

    def _effective_cwd(self) -> str:
        """返回当前生效的 cwd（Worktree 路径或进程 cwd）。"""
        if self.active_cwd:
            return self.active_cwd
        return str(Path.cwd())

    def _worktree_accessor_impl(self):
        """返回 WorktreeAccessor 协议实现（若启用）。"""
        if self.worktree_mgr is None:
            return None
        from csycode.tui.worktree_adapter import WorktreeAdapter

        return WorktreeAdapter(
            self.worktree_mgr,
            lambda cwd: setattr(self, "active_cwd", cwd),
        )

    # ── ch10: UI Protocol 别名（让 cmd.handler(self) 可调）───────

    println = _println_impl
    error = _error_impl
    mode = _mode_impl  # 注意：实例属性 self._mode 不会被此类属性遮蔽
    set_mode = _set_mode_impl
    inject_and_send = _inject_and_send_impl
    usage_in = _usage_in_impl
    usage_out = _usage_out_impl
    model_name = _model_name_impl
    cwd = _cwd_impl
    tool_count = _tool_count_impl
    memory_files = _memory_files_impl
    session_path = _session_path_impl
    session_id = _session_id_impl
    idle = _idle_impl
    reasoning_effort = _reasoning_effort_impl
    set_reasoning_effort = _set_reasoning_effort_impl
    worktree_accessor = _worktree_accessor_impl
    quit = _quit_impl
    force_compact = _force_compact_impl
    open_resume_menu = _open_resume_menu_impl
    clear_and_new_session = _clear_and_new_session_impl

    # ── ch10: Completion 方法绑定 ──────────────────────────────────

    _execute_selected_completion = _execute_selected_completion
    _sync_completion_from_input = _sync_completion_from_input
    _render_completion = _render_completion

    # ── ch10: TextArea 变化 → 同步补全菜单 ──────────────────────────

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """每次 TextArea 内容变化后同步补全菜单状态。"""
        if event.text_area.id == "input" and self.cmd_registry is not None:
            try:
                self._sync_completion_from_input()
            except Exception:
                pass  # DOM 未就绪时忽略

    # ── Resize handler ────────────────────────────────────────────────

    def on_resize(self) -> None:
        """Update status bar on resize."""
        if self.provider is not None:
            self._update_statusbar()
