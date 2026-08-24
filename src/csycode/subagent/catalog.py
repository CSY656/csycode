"""Catalog —— Agent 定义的三层加载与查询。

对齐 mewcode agents/loader.py 的 AgentLoader。
优先级：项目级 > 用户级 > 内置级（同名高优先级覆盖）。
插件级预留但本期不加载任何文件。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from threading import Lock

from .definition import Definition, Source
from .embed import builtin_definitions
from .parser import AgentParseError, parse_file

log = logging.getLogger(__name__)

PROJECT_AGENTS_DIR = ".csycode/agents"
USER_AGENTS_DIR = "~/.csycode/agents"


class Catalog:
    """Agent 角色定义目录。

    提供按 name 查询、列表和 fork 临时定义。
    线程安全（通过 _lock），但实际使用中多为单线程（asyncio 事件循环）。

    IMPORTANT: _lock 临界区内禁止 await —— 任何异步操作必须移到锁外执行。
    """

    def __init__(self) -> None:
        # 使用 threading.Lock 而非 asyncio.Lock：
        # - _add_all() 在同步启动路径中被调用（async 事件循环尚未运行）
        # - resolve/list_all/list_by_source 在 async 上下文被调用但临界区仅含
        #   纯同步 dict/set 操作，无 await 点
        # - 约束：永远不要在 _lock 内执行 await
        self._lock: Lock = Lock()
        self._defs: dict[str, Definition] = {}
        self._by_source: dict[Source, list[Definition]] = {
            Source.BUILTIN: [],
            Source.USER: [],
            Source.PROJECT: [],
            Source.PLUGIN: [],
        }

    # ── 加载 ──────────────────────────────────────────────────────

    def _add_all(self, defs: list[Definition], source: Source) -> None:
        """添加一批同 source 的定义，同名时后添加的覆盖（高优先级覆盖低）。"""
        with self._lock:
            for d in defs:
                self._defs[d.name] = d
                self._by_source[source].append(d)

    # ── 查询 ──────────────────────────────────────────────────────

    def resolve(self, name: str) -> Definition | None:
        """按名称查找定义。

        支持热重载：若定义来自文件且文件存在，返回时重新解析。
        """
        with self._lock:
            cached = self._defs.get(name)
            if cached is None:
                return None

            # 热重载：文件来源且文件仍存在→重新解析
            if cached.file_path is not None and cached.file_path.exists():
                try:
                    reloaded = parse_file(cached.file_path, cached.source)
                    self._defs[name] = reloaded
                    return reloaded
                except AgentParseError as e:
                    log.warning(
                        "热重载失败 %s，使用缓存版本: %s", name, e
                    )
            return cached

    def list_all(self) -> list[Definition]:
        """返回所有定义，按 name 升序排列。"""
        with self._lock:
            return sorted(self._defs.values(), key=lambda d: d.name)

    def list_by_source(self, src: Source) -> list[Definition]:
        """返回指定来源的所有定义。"""
        with self._lock:
            return list(self._by_source.get(src, []))

    # ── Fork ──────────────────────────────────────────────────────

    def fork_definition(self) -> Definition:
        """返回 Fork 路径用的临时 Definition。

        name="__fork__"，tools / disallowed_tools 留空（工具集继承父）。
        Fork 子 Agent 工具集保留 Agent（靠 QuerySource + Boilerplate 兜底拦截）。
        """
        return Definition(
            name="__fork__",
            description="Fork-based subagent",
            model="inherit",
            max_turns=0,  # 沿用全局默认
            permission_mode="default",
            source=Source.BUILTIN,
        )


# ── 便利加载函数 ─────────────────────────────────────────────────


def _load_from_dir(dir_path: Path, source: Source) -> list[Definition]:
    """从目录加载所有 .md 文件，解析失败 stderr 警告并跳过。"""
    if not dir_path.is_dir():
        return []

    results: list[Definition] = []
    for entry in sorted(dir_path.iterdir()):
        if not entry.is_file() or entry.suffix != ".md":
            continue
        try:
            defi = parse_file(entry, source)
            results.append(defi)
        except AgentParseError as e:
            print(
                f"subagent: 跳过 {entry.name}: {e}",
                file=sys.stderr,
            )
    return results


def load_catalog(root: str) -> Catalog:
    """按优先级顺序加载三层 Agent 定义。

    加载顺序（后加载覆盖前加载同名）：
    1. builtin（内置，随包发布）
    2. user（~/.csycode/agents/）
    3. project（<root>/.csycode/agents/）
    4. plugin（预留，本期恒为空）

    Args:
        root: 项目根目录路径。

    Returns:
        包含所有已加载定义的 Catalog（即使没有任何定义也返回非空 Catalog）。
    """
    c = Catalog()

    # 1. 内置
    try:
        c._add_all(builtin_definitions(), Source.BUILTIN)
    except Exception as e:
        # 内置加载失败是严重问题，打印错误但不阻断启动
        print(f"subagent: 内置定义加载失败: {e}", file=sys.stderr)

    # 2. 用户级
    user_dir = Path(USER_AGENTS_DIR).expanduser()
    c._add_all(_load_from_dir(user_dir, Source.USER), Source.USER)

    # 3. 项目级
    project_dir = Path(root) / PROJECT_AGENTS_DIR
    c._add_all(_load_from_dir(project_dir, Source.PROJECT), Source.PROJECT)

    # 4. 插件级（预留，本期空）

    return c
