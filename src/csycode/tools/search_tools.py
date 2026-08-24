"""搜索工具：Glob 按模式找文件、Grep 搜代码内容。"""

from __future__ import annotations

import glob as glob_module
import os
import re
from pathlib import Path

from .base import Tool, ToolResult
from .sandbox import PathValidator, SecurityViolation
from .ctx import resolve_path

# 默认跳过的目录（对齐 mewcode tools/base.py SKIP_DIRS）
_SKIP_DIRS = frozenset({
    ".git", ".svn", ".hg",
    ".venv", ".env", "venv", "env",
    "node_modules",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    ".tox", ".nox",
    ".idea", ".vscode",
    "dist", "build", ".eggs",
    ".mewcode", ".csycode",
})


class GlobTool(Tool):
    """按 glob 模式查找文件，返回匹配路径列表，按修改时间降序排列。"""

    def __init__(self, project_root: str | None = None) -> None:
        self._project_root = project_root or os.getcwd()

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return (
            "按 glob 模式查找文件（如 '**/*.py'、'src/**/*.ts'），"
            "返回匹配的文件路径列表，按修改时间降序排列。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob 匹配模式，支持 ** 递归匹配，如 '**/*.py'。",
                },
                "path": {
                    "type": "string",
                    "description": "查找的起始目录（相对于工作目录，默认 '.'）。",
                },
            },
            "required": ["pattern"],
        }

    is_readonly: bool = True
    timeout: float = 30.0
    show_result_to_user: bool = False

    async def _execute(self, pattern: str, path: str = ".") -> ToolResult:
        validator = PathValidator(cwd=self._project_root)
        try:
            abs_path = resolve_path(path)
            resolved = validator.validate(abs_path)
        except SecurityViolation as e:
            return ToolResult(
                success=False, content="", error=str(e), error_type="security"
            )

        if not resolved.exists():
            return ToolResult(
                success=False,
                content="",
                error=f"目录不存在: {path}",
                error_type="not_found",
            )

        # ch13 fix: 在线程池中执行阻塞的 os.chdir + glob.glob，避免阻塞事件循环
        import asyncio

        def _blocking_glob() -> list[str]:
            original_cwd = os.getcwd()
            try:
                os.chdir(resolved)
                return glob_module.glob(pattern, recursive=True)
            finally:
                os.chdir(original_cwd)

        loop = asyncio.get_running_loop()
        try:
            matches: list[str] = await loop.run_in_executor(None, _blocking_glob)
        except Exception as e:
            return ToolResult(
                success=False,
                content="",
                error=f"Glob 搜索失败: {e}",
                error_type="exec_error",
            )

        if not matches:
            return ToolResult(
                success=True, content=f"[{path}] 未找到匹配 '{pattern}' 的文件"
            )

        # 过滤 SKIP_DIRS
        matches = [
            m for m in matches
            if not any(seg in _SKIP_DIRS for seg in Path(m).parts)
        ]

        if not matches:
            return ToolResult(
                success=True, content=f"[{path}] 未找到匹配 '{pattern}' 的文件（已过滤系统目录）"
            )

        # 按 mtime 降序排列
        def get_mtime(p: str) -> float:
            try:
                return os.path.getmtime(os.path.join(resolved, p))
            except OSError:
                return 0.0

        matches.sort(key=get_mtime, reverse=True)

        # 构造结果
        header = f"[{path}] 匹配 '{pattern}' 共 {len(matches)} 个文件:"
        lines = [header] + [f"  {m}" for m in matches]
        return ToolResult(success=True, content="\n".join(lines))


class GrepTool(Tool):
    """在文件中搜索正则表达式匹配行，返回文件路径、行号和内容。"""

    def __init__(self, project_root: str | None = None) -> None:
        self._project_root = project_root or os.getcwd()

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "在文件中搜索正则表达式匹配的行。返回匹配的文件路径、行号和行内容。"
            "支持通过 glob 参数过滤文件（如 '*.py'）。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "要搜索的正则表达式模式。",
                },
                "path": {
                    "type": "string",
                    "description": "搜索的目录或文件路径（相对于工作目录，默认 '.'）。",
                },
                "glob": {
                    "type": "string",
                    "description": "文件过滤 glob（可选），如 '*.py' 仅搜索 Python 文件。",
                },
            },
            "required": ["pattern"],
        }

    is_readonly: bool = True
    timeout: float = 60.0
    show_result_to_user: bool = False

    async def _execute(
        self, pattern: str, path: str = ".", glob: str | None = None
    ) -> ToolResult:
        validator = PathValidator(cwd=self._project_root)
        try:
            abs_path = resolve_path(path)
            resolved = validator.validate(abs_path)
        except SecurityViolation as e:
            return ToolResult(
                success=False, content="", error=str(e), error_type="security"
            )

        if not resolved.exists():
            return ToolResult(
                success=False,
                content="",
                error=f"路径不存在: {path}",
                error_type="not_found",
            )

        # 编译正则表达式
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(
                success=False,
                content="",
                error=f"正则表达式无效: {e}",
                error_type="exec_error",
            )

        # ch13 fix: 在线程池中执行阻塞的 glob + 文件读取，避免阻塞事件循环
        import asyncio

        def _blocking_grep() -> list[str]:
            """在线程池中执行文件收集 + 搜索 + 读取（全部 I/O 操作）。"""
            if resolved.is_file():
                target_files = [resolved]
            else:
                if glob:
                    file_pattern = os.path.join(str(resolved), "**", glob)
                else:
                    file_pattern = os.path.join(str(resolved), "**", "*")
                target_files = [
                    Path(p) for p in glob_module.glob(file_pattern, recursive=True)
                ]
                # 过滤 SKIP_DIRS 和只保留文件
                target_files = [
                    f for f in target_files
                    if f.is_file()
                    and not any(seg in _SKIP_DIRS for seg in f.parts)
                ]

            if not target_files:
                return []

            grep_results: list[str] = []
            matched = 0
            limit = 500  # 防止结果过多

            for fp in target_files:
                if matched >= limit:
                    grep_results.append(f"... (已达到 {limit} 条匹配上限，结果已截断)")
                    break

                try:
                    file_lines = fp.read_text(encoding="utf-8").splitlines()
                except (UnicodeDecodeError, OSError):
                    continue

                rel = str(fp.relative_to(resolved))

                for lno, ln in enumerate(file_lines, 1):
                    if regex.search(ln):
                        if matched >= limit:
                            break
                        grep_results.append(f"{rel}:{lno}: {ln}")
                        matched += 1

            return grep_results

        loop = asyncio.get_running_loop()
        results: list[str] = await loop.run_in_executor(None, _blocking_grep)

        if not results:
            return ToolResult(success=True, content=f"未找到匹配 '{pattern}' 的内容")

        header = f"[{path}] 匹配 '{pattern}' 共 {len(results)} 条:"
        return ToolResult(success=True, content=header + "\n" + "\n".join(results))
