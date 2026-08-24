"""项目指令加载器：三层优先级扫描 + @include 递归展开。

对齐 mewcode 的 memory/instructions.py。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

MAX_INCLUDE_DEPTH = 5
_INCLUDE_RE = re.compile(r"^@include\s+(.+)$")


# ---------------------------------------------------------------------------
# @include 指令解析与展开
# ---------------------------------------------------------------------------


def _parse_include(trimmed: str) -> str:
    """解析一行文本，提取 @include 路径。

    支持旧格式 "@include path"，返回空字符串表示不是 include 指令。
    """
    m = _INCLUDE_RE.match(trimmed)
    if m:
        return m.group(1).strip()
    return ""


def _resolve_include(path: str, base_dir: Path) -> Path:
    """将 include 路径解析为绝对路径。"""
    if path.startswith("~/"):
        return Path.home() / path[2:]
    if os.path.isabs(path):
        return Path(path)
    return base_dir / path


def process_includes(
    content: str,
    base_dir: Path,
    project_root: Path,
    depth: int = 0,
    visited: set[str] | None = None,
    stack: set[str] | None = None,
) -> str:
    """展开 @include 指令。

    - 深度限制：最多 MAX_INCLUDE_DEPTH 层
    - 真正的循环检测：stack 集合记录当前包含链（从根到当前文件），
      若再次遇到 stack 中已存在的文件，说明出现了 A→B→A 型循环。
    - 重复检测：visited 集合记录所有已加载文件的绝对路径，
      用于跳过菱形依赖（A→{B,C}, B→C）中的重复加载。
    - 代码块跳过：``` 围栏内的 @include 不展开
    - 文件不存在时输出 HTML 注释警告
    - 二进制文件（前 512 字节含 \\x00）跳过
    """
    if depth > MAX_INCLUDE_DEPTH:
        return content + "\n<!-- @include 超过最大嵌套深度 (%d) -->" % MAX_INCLUDE_DEPTH

    if visited is None:
        visited = set()
    if stack is None:
        stack = set()

    lines = content.split("\n")
    result: list[str] = []
    in_code = False

    for line in lines:
        stripped = line.strip()

        # 围栏代码块边界检测
        if stripped.startswith("```"):
            in_code = not in_code
            result.append(line)
            continue

        # 代码块内不展开
        if not in_code:
            include_path = _parse_include(stripped)
            if include_path:
                resolved = _resolve_include(include_path, base_dir)
                try:
                    abs_str = os.path.realpath(str(resolved))
                except OSError:
                    result.append(line)
                    continue

                # 路径逃逸检测
                try:
                    project_root_real = os.path.realpath(str(project_root))
                    if not abs_str.startswith(project_root_real + os.sep) and abs_str != project_root_real:
                        result.append(
                            "<!-- @include 跳过: 路径超出允许范围: %s -->" % include_path
                        )
                        continue
                except OSError:
                    pass

                # 真正的循环检测（当前包含链上的文件再次出现）
                if abs_str in stack:
                    result.append(
                        "<!-- @include 跳过: 检测到环路: %s -->" % include_path
                    )
                    continue

                # 菱形依赖检测（已通过其他路径加载，跳过以避免重复）
                if abs_str in visited:
                    result.append(
                        "<!-- @include 跳过: 已加载: %s -->" % include_path
                    )
                    continue

                if not resolved.exists() or not resolved.is_file():
                    result.append(
                        "<!-- @include 跳过: 文件不存在: %s -->" % include_path
                    )
                    continue

                # 二进制检测
                try:
                    with open(abs_str, "rb") as f:
                        head = f.read(512)
                    if b"\x00" in head:
                        result.append(
                            "<!-- @include 跳过: 二进制文件: %s -->" % include_path
                        )
                        continue
                except OSError:
                    result.append(line)
                    continue

                try:
                    included = resolved.read_text(encoding="utf-8")
                except OSError:
                    result.append(line)
                    continue

                visited.add(abs_str)
                stack.add(abs_str)
                result.append("<!-- included from %s -->" % include_path)
                result.append(
                    process_includes(
                        included, resolved.parent, project_root, depth + 1, visited, stack
                    )
                )
                stack.discard(abs_str)
                continue

        result.append(line)

    return "\n".join(result)


# ---------------------------------------------------------------------------
# 指令文件发现
# ---------------------------------------------------------------------------


def _find_git_root(start: Path) -> Path | None:
    """从 start 向上查找 .git 目录。"""
    cur = start.resolve()
    while True:
        if (cur / ".git").exists():
            return cur
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


def _project_instruction_dirs(work_dir: Path) -> list[Path]:
    """返回从 git root 到 work_dir 的所有目录。"""
    abs_dir = work_dir.resolve()
    root = _find_git_root(abs_dir)
    if root is None:
        return [abs_dir]

    dirs: list[Path] = []
    cur = abs_dir
    while True:
        dirs.insert(0, cur)
        if cur == root:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return dirs


class Loader:
    """项目指令加载器：三层优先级 + @include 展开。"""

    def __init__(self, project_root: str, user_home: str | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.user_home = Path(user_home) if user_home else Path.home()
        self.max_depth = MAX_INCLUDE_DEPTH

    def load(self) -> str:
        """按优先级扫描并加载所有指令文件。

        优先级（低 → 高）：
        1. 项目根 csyCODE.md
        2. .csycode/csyCODE.md（项目本地配置）
        3. ~/.csycode/csyCODE.md（用户全局配置）

        每个文件内部递归展开 @include 指令。
        """
        root = self.project_root
        seen: set[str] = set()
        sources: list[tuple[str, str]] = []

        def _add(path: Path) -> None:
            try:
                abs_path = path.resolve()
                abs_str = str(abs_path)
            except OSError:
                return
            if abs_str in seen:
                return
            if not abs_path.exists() or not abs_path.is_file():
                return
            try:
                data = abs_path.read_text(encoding="utf-8")
            except OSError:
                return
            seen.add(abs_str)
            include_visited: set[str] = {abs_str}
            content = process_includes(
                data, abs_path.parent, root, 0, include_visited
            )
            try:
                label = str(abs_path.relative_to(root))
            except ValueError:
                label = abs_str
            sources.append((label, content.rstrip("\n")))

        # 1. 项目根 csyCODE.md
        _add(root / "csyCODE.md")
        _add(root / "CLAUDE.md")  # 兼容 Claude Code 项目指令文件

        # 2. 目录链扫描：从 git root 到 work_dir，逐层查找 CLAUDE.md / csyCODE.md
        #    对齐 mewcode 的 _project_instruction_dirs()，支持 monorepo 子目录级指令。
        for d in _project_instruction_dirs(root):
            if d == root:
                continue  # 根目录已在步骤 1 处理
            _add(d / "CLAUDE.md")
            _add(d / "csyCODE.md")

        # 3. .csycode/csyCODE.md（项目本地配置）
        _add(root / ".csycode" / "csyCODE.md")

        # 4. 用户全局 ~/.csycode/csyCODE.md
        _add(self.user_home / ".csycode" / "csyCODE.md")

        if not sources:
            return ""

        parts = ["Contents of %s:\n\n%s" % (label, content) for label, content in sources]
        return "\n\n---\n\n".join(parts)
