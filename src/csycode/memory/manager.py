"""记忆管理器 —— 协调项目级和用户级记忆存储 + 召回。

对齐 mewcode 的 memory/auto_memory.py + memory/recall.py。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .prompts import MEMORY_UPDATE_SYSTEM_PROMPT
from .store import Store, ENTRYPOINT_NAME
from .types import Note, NoteType, UpdateAction

if TYPE_CHECKING:
    from csycode.llm import Message, Provider

_logger = logging.getLogger(__name__)

MAX_INDEX_BYTES = 25_000

# ── 路径工具（对齐 mewcode paths.go）───────────────────────────────────


def _get_user_mem_dir() -> str:
    """返回用户级记忆目录：~/.csycode/memory/。"""
    try:
        home = str(Path.home())
    except RuntimeError:
        return ""
    return os.path.join(home, ".csycode", "memory")


def _get_project_mem_dir(project_root: str) -> str:
    """返回项目级记忆目录：<projectRoot>/.csycode/memory/。"""
    return os.path.join(os.path.abspath(project_root), ".csycode", "memory")


# ── build_memory_prompt（对齐 mewcode BuildMemoryPrompt）──────────────


def build_memory_prompt(user_mem_dir: str, project_mem_dir: str) -> str:
    """构建记忆系统提示：行为指令 + MEMORY.md 索引内容。

    对齐 mewcode 的 build_memory_prompt：生成 '# auto memory' 段，
    用于 inject_long_term_memory。
    """
    lines = _build_memory_behavior_lines(user_mem_dir, project_mem_dir)
    parts = [lines]

    if user_mem_dir:
        ep_path = os.path.join(user_mem_dir, ENTRYPOINT_NAME)
        parts.append("")
        parts.append(_build_entrypoint_section("User-level", ep_path))

    if project_mem_dir:
        ep_path = os.path.join(project_mem_dir, ENTRYPOINT_NAME)
        parts.append("")
        parts.append(_build_entrypoint_section("Project-level", ep_path))

    return "\n".join(parts)


def _build_entrypoint_section(scope_label: str, entrypoint_path: str) -> str:
    """读取 MEMORY.md 并格式化为提示段。"""
    header = "## %s %s (`%s`)\n" % (scope_label, ENTRYPOINT_NAME, entrypoint_path)
    try:
        data = Path(entrypoint_path).read_text(encoding="utf-8")
        if data.strip():
            return header + "\n" + _truncate_entrypoint(data)
    except OSError:
        pass
    return (
        header
        + "\nThis %s is currently empty. When you save new %s-level memories, "
        "add their pointers here." % (ENTRYPOINT_NAME, scope_label.lower())
    )


def _build_memory_behavior_lines(user_mem_dir: str, project_mem_dir: str) -> str:
    """构建记忆行为指令文本（不含 MEMORY.md 内容）。"""
    dir_exists_guidance = (
        "This directory already exists — write to it directly with the Write tool "
        "(do not run mkdir or check for its existence)."
    )

    parts = ["# auto memory\n"]
    parts.append(
        "You have a persistent, file-based memory system organized into "
        "two locations by content type:\n"
    )

    if user_mem_dir:
        parts.append(
            "- **User-level** (`%s`) — memories with `type: user` or `type: feedback`. "
            "These follow you across all projects, because they describe the human or "
            "how the human likes to work. %s" % (user_mem_dir, dir_exists_guidance)
        )
    if project_mem_dir:
        parts.append(
            "- **Project-level** (`%s`) — memories with `type: project` or `type: reference`. "
            "These belong to the current repo, can be committed for team sharing or "
            "git-ignored for personal use. %s" % (project_mem_dir, dir_exists_guidance)
        )

    parts.append(
        "\nThe `type` field in each memory file's frontmatter determines which "
        "directory it belongs to — pick the type first, then write to the "
        "matching directory."
    )
    parts.append(
        "\nYou should build up this memory system over time so that future "
        "conversations can have a complete picture of who the user is, how "
        "they'd like to collaborate with you, what behaviors to avoid or "
        "repeat, and the context behind the work the user gives you."
    )

    # frontmatter 格式说明
    parts.append("\n## How to save memories\n")
    parts.append(
        "Saving a memory is a two-step process:\n\n"
        "**Step 1** — write the memory to its own file (e.g., `user_role.md`, "
        "`feedback_testing.md`) using this frontmatter format:\n\n"
        "```markdown\n"
        "---\n"
        "name: {{memory name}}\n"
        "description: {{one-line description}}\n"
        "type: {{user, feedback, project, reference}}\n"
        "---\n\n"
        "{{memory content}}\n"
        "```\n\n"
        "**Step 2** — add a pointer to that file in the `%s` index in the SAME "
        "directory as the memory file. `%s` is an index, not a memory — each entry "
        "should be one line, under ~150 characters: "
        "`- [Title](file.md) — one-line hook`. It has no frontmatter. "
        "Never write memory content directly into `%s`.\n\n"
        "- Both `%s` files are always loaded into your conversation context"
        " — lines after 200 each will be truncated, so keep each index concise\n"
        "- Keep the name, description, and type fields in memory files up-to-date "
        "with the content\n"
        "- Organize memory semantically by topic, not chronologically\n"
        "- Update or remove memories that turn out to be wrong or outdated\n"
        "- Do not write duplicate memories. First check if there is an existing "
        "memory you can update before writing a new one."
        % (ENTRYPOINT_NAME, ENTRYPOINT_NAME, ENTRYPOINT_NAME, ENTRYPOINT_NAME)
    )

    return "\n".join(parts)


def _truncate_entrypoint(raw: str) -> str:
    """截断 MEMORY.md 内容，超过 200 行或 25KB 时添加警告。"""
    trimmed = raw.strip()
    lines = trimmed.split("\n")
    line_count = len(lines)
    byte_count = len(trimmed.encode("utf-8"))

    over_lines = line_count > 200
    over_bytes = byte_count > MAX_INDEX_BYTES

    if not over_lines and not over_bytes:
        return trimmed

    result = trimmed
    if over_lines:
        result = "\n".join(lines[:200])

    result_bytes = result.encode("utf-8")
    if len(result_bytes) > MAX_INDEX_BYTES:
        cut = result[:MAX_INDEX_BYTES].rfind("\n")
        if cut > 0:
            result = result[:cut]
        else:
            result = result[:MAX_INDEX_BYTES]

    if over_bytes and not over_lines:
        reason = "%s (limit: 25KB) — index entries are too long" % _format_size(byte_count)
    elif over_lines and not over_bytes:
        reason = "%d lines (limit: 200)" % line_count
    else:
        reason = "%d lines and %s" % (line_count, _format_size(byte_count))

    result += (
        "\n\n> WARNING: %s is %s. "
        "Only part of it was loaded. Keep index entries to one line "
        "under ~200 chars; move detail into topic files." % (ENTRYPOINT_NAME, reason)
    )
    return result


def _format_size(byte_count: int) -> str:
    if byte_count < 1024:
        return "%dB" % byte_count
    elif byte_count < 1024 * 1024:
        return "%.1fKB" % (byte_count / 1024)
    else:
        return "%.1fMB" % (byte_count / (1024 * 1024))


# ── Manager（对齐 mewcode MemoryManager）─────────────────────────────


class Manager:
    """记忆管理器：双级存储 + 异步 LLM 更新 + 召回。"""

    def __init__(
        self,
        project_dir: str,
        user_dir: str | None = None,
        provider: "Provider | None" = None,
        model: str = "",
    ) -> None:
        self._project_store = Store(project_dir)
        self._user_store = Store(user_dir or _get_user_mem_dir())
        self._provider = provider
        self._model = model
        self._lock = asyncio.Lock()
        self._last_extraction_msg_count = 0

    @property
    def project_mem_dir(self) -> str:
        return self._project_store._dir

    @property
    def user_mem_dir(self) -> str:
        return self._user_store._dir

    def set_provider(self, provider: "Provider", model: str) -> None:
        """延迟设置 provider（启动时可能尚未选定）。"""
        self._provider = provider
        self._model = model

    # ── ch10: 文件列表查询 ──────────────────────────────────────────

    def list_files(self) -> tuple[list[str], list[str]]:
        """列出项目层与用户层 memory 目录下的 .md 文件。

        返回 (project_files, user_files)，均已按文件名字典序排序。
        目录不存在视为空 list，不抛异常；其它 OSError 用 logging.warning 记录后视为空 list。
        """
        result: tuple[list[str], list[str]] = ([], [])
        project_files = self._list_md_files(self._project_store._dir)
        user_files = self._list_md_files(self._user_store._dir)
        return (project_files, user_files)

    @staticmethod
    def _list_md_files(dir_path: str) -> list[str]:
        """列出单级目录下的 .md 文件，按字典序，异常安全。"""
        try:
            if not os.path.isdir(dir_path):
                return []
            entries = os.listdir(dir_path)
            files = [
                e for e in entries
                if os.path.isfile(os.path.join(dir_path, e)) and e.endswith(".md")
            ]
            files.sort()
            return files
        except OSError:
            logging.warning("list_files(%s) 失败", dir_path)
            return []

    # ── 索引加载 ──────────────────────────────────────────────────

    def load_index(self) -> str:
        """加载两级 MEMORY.md 索引合并返回。项目级在前，用户级在后。"""
        parts: list[str] = []
        for label, store in [
            ("项目级记忆", self._project_store),
            ("用户级记忆", self._user_store),
        ]:
            store.ensure_dir()
            idx = store.load_index()
            if idx.strip():
                parts.append("## %s\n\n%s" % (label, idx))
        if not parts:
            return ""
        return "\n\n".join(parts)

    # ── 完整系统提示（行为指令 + 索引）─────────────────────────────

    def load(self) -> str:
        """构建完整的记忆系统提示段（对齐 mewcode Manager.load）。

        包含行为指令 + 两级 MEMORY.md 的索引内容，
        用于 Conversation.inject_long_term_memory。
        """
        user_dir = self._user_store._dir
        project_dir = self._project_store._dir

        if not user_dir and not project_dir:
            return ""

        # 确保目录存在
        self._user_store.ensure_dir()
        self._project_store.ensure_dir()

        return build_memory_prompt(user_dir, project_dir)

    # ── 异步记忆更新 ──────────────────────────────────────────────

    async def update_async(self, recent_msgs: list["Message"]) -> None:
        """异步触发记忆提取和更新。

        使用 LLM 分析最近对话，通过工具调用写入记忆文件。
        失败静默，不中断主会话。
        """
        if self._provider is None:
            _logger.warning("记忆更新跳过：provider 未设置")
            return

        async with self._lock:
            try:
                await self._do_update(recent_msgs)
            except Exception:
                _logger.exception("记忆更新失败")

    async def _do_update(self, recent_msgs: list["Message"]) -> None:
        """执行记忆更新的核心逻辑。

        两阶段策略：
        1. 尝试工具模式：给 LLM Write/Edit 工具直接写记忆文件
        2. 回退 JSON 模式：解析 LLM JSON 响应后调 Store.apply
        """
        from csycode.llm import Request, System

        conv_lines: list[str] = []
        for msg in recent_msgs:
            if msg.role == "user" and msg.content:
                conv_lines.append("用户: %s" % msg.content)
            elif msg.role == "assistant" and msg.content:
                conv_lines.append("助手: %s" % msg.content)
        if not conv_lines:
            return

        conversation = "\n".join(conv_lines)
        index_content = self.load_index()

        system_content = MEMORY_UPDATE_SYSTEM_PROMPT.format(
            index_content=index_content if index_content else "(空)",
            conversation=conversation,
        )

        # ── 工具模式：给 LLM Write/Edit 工具直接操作记忆文件 ──
        # 如果工具模式失败（比如 LLM 不支持 tools），回退到 JSON 模式。
        tool_result = await self._try_tool_mode(system_content)
        if tool_result:
            return  # 工具模式成功

        # ── 回退：JSON 模式 ──
        await self._fallback_json_mode(system_content)

    async def _try_tool_mode(self, system_content: str) -> bool:
        """工具模式：给 LLM WriteFile 工具直接写记忆文件。

        Returns:
            True 表示工具模式成功，False 表示需要回退。
        """
        from csycode.llm import Message, Request, System

        # 构建最小工具注册中心（仅 WriteFile + EditFile）
        from csycode.tools.registry import ToolRegistry
        from csycode.tools.file_tools import WriteFileTool, EditFileTool

        mini_registry = ToolRegistry()
        # 工具的工作目录设为项目记忆目录或用户记忆目录
        # 因为记忆可跨项目，我们使用用户目录作为基础
        mem_base = self._project_store._dir or self._user_store._dir
        write_tool = WriteFileTool(project_root=mem_base)
        edit_tool = EditFileTool(project_root=mem_base)
        mini_registry.register(write_tool)
        mini_registry.register(edit_tool)

        # 构造工具定义
        tools = [
            {
                "name": "write_file",
                "description": write_tool.description,
                "input_schema": write_tool.parameters,
            },
            {
                "name": "edit_file",
                "description": edit_tool.description,
                "input_schema": edit_tool.parameters,
            },
        ]

        # 增强 system prompt：告诉 LLM 记忆目录的位置
        enhanced = (
            system_content
            + "\n\n## 记忆目录\n"
            + f"- 项目级: {self._project_store._dir}\n"
            + f"- 用户级: {self._user_store._dir}\n"
            + "\n请直接用 write_file 工具在上述目录中创建/更新记忆文件。"
            + "文件名使用 kebab-case，每个记忆一个 .md 文件。"
            + "别忘了更新 MEMORY.md 索引。"
        )

        tool_called = False
        max_rounds = 3

        for _ in range(max_rounds):
            req = Request(
                messages=[Message(role="user", content=enhanced)],
                tools=tools,
                system=System(stable="", environment=""),
                reminder="",
            )

            text = ""
            tc_list: list = []
            try:
                async for ev in self._provider.stream(req):
                    if ev.err is not None:
                        _logger.warning("记忆更新工具模式 LLM 错误: %s", ev.err)
                        return False
                    if ev.text:
                        text += ev.text
                    if ev.tool_calls:
                        tc_list = ev.tool_calls
                    if ev.done and ev.tool_calls:
                        tc_list = ev.tool_calls
            except Exception as e:
                _logger.warning("记忆更新工具模式异常: %s", e)
                return False

            if not tc_list:
                # LLM 没有调用工具 → 尝试从 text 中解析 JSON
                # （某些模型不支持 function calling）
                if tool_called:
                    break  # 已写过文件，结束
                return False  # 首轮就没工具调用，回退 JSON 模式

            # 执行工具调用
            for tc in tc_list:
                tool_name = tc.name
                tool = mini_registry.get(tool_name)
                if tool is None:
                    continue
                try:
                    await tool.execute(**tc.arguments)
                    tool_called = True
                except Exception as e:
                    _logger.warning("记忆工具执行失败 %s: %s", tool_name, e)

            # 用工具结果追加新一轮对话
            if tc_list:
                enhanced += "\n\n工具已执行。"  # 简单反馈
                continue
            else:
                break

        if tool_called:
            # 重载两级 Store 的索引
            try:
                self._project_store.ensure_dir()
                self._user_store.ensure_dir()
            except Exception:
                pass

        return tool_called

    async def _fallback_json_mode(self, system_content: str) -> None:
        """回退 JSON 模式：解析 LLM 文本响应中的 JSON 动作数组。"""
        from csycode.llm import Message, Request, System

        req = Request(
            messages=[Message(role="user", content=system_content)],
            tools=[],
            system=System(stable="", environment=""),
            reminder="",
        )

        collected = ""
        try:
            async for ev in self._provider.stream(req):
                if ev.err is not None:
                    _logger.warning("记忆更新 JSON 模式 LLM 错误: %s", ev.err)
                    return
                if ev.text:
                    collected += ev.text
        except Exception as e:
            _logger.warning("记忆更新 JSON 模式异常: %s", e)
            return

        if not collected.strip():
            return

        # 解析 JSON 响应
        try:
            collected = collected.strip()
            if collected.startswith("```"):
                lines = collected.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                collected = "\n".join(lines)
            actions_raw = json.loads(collected)
            if not isinstance(actions_raw, list):
                return
        except json.JSONDecodeError:
            _logger.warning("记忆更新响应 JSON 解析失败")
            return

        if not actions_raw:
            return

        project_actions: list[UpdateAction] = []
        user_actions: list[UpdateAction] = []

        for item in actions_raw:
            if not isinstance(item, dict):
                continue
            action = item.get("action", "")
            level = item.get("level", "")

            note = None
            if action in ("create", "update"):
                nt_str = item.get("type", "project")
                if nt_str not in ("user", "feedback", "project", "reference"):
                    nt_str = "project"
                note = Note(
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    type=NoteType(nt_str),
                    content=item.get("content", ""),
                )
            elif action == "delete":
                note = Note(
                    name=item.get("name", ""),
                    description="",
                    type=NoteType(item.get("type", "project")),
                    content="",
                )

            ua = UpdateAction(action=action, level=level, note=note)
            if level == "user":
                user_actions.append(ua)
            else:
                project_actions.append(ua)

        if project_actions:
            self._project_store.apply(project_actions)
        if user_actions:
            self._user_store.apply(user_actions)
