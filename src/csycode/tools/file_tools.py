"""文件操作工具：读文件、写文件、精确替换编辑。"""

from __future__ import annotations

import os
from pathlib import Path

from .base import Tool, ToolResult
from .sandbox import PathValidator, SecurityViolation
from .ctx import resolve_path


def _detect_file_encoding(file_path: Path) -> str:
    """检测文件的编码。

    尝试 UTF-8 读取，失败则回退到 latin-1（latin-1 永不抛 UnicodeDecodeError）。
    返回检测到的编码名称。
    """
    try:
        file_path.read_text(encoding="utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


class ReadFileTool(Tool):
    """读取文件内容，支持可选的起始行和行数限制。"""

    def __init__(self, project_root: str | None = None,
                 cache: "FileStateCache | None" = None) -> None:
        self._project_root = project_root or os.getcwd()
        self._cache = cache

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "读取文件内容。支持指定起始行号（offset）和读取行数（limit）来分段读取大文件。"
            "若文件不存在则返回错误。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要读取的文件路径（相对于工作目录）。",
                },
                "offset": {
                    "type": "integer",
                    "description": "起始行号（1-indexed，可选，默认从第 1 行开始）。",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多读取的行数（可选，默认读取全部）。",
                },
            },
            "required": ["file_path"],
        }

    is_readonly: bool = True
    timeout: float = 10.0
    show_result_to_user: bool = False

    async def _execute(
        self,
        file_path: str,
        offset: int | None = None,
        limit: int | None = None,
    ) -> ToolResult:
        validator = PathValidator(cwd=self._project_root)
        try:
            abs_path = resolve_path(file_path)
            resolved = validator.validate(abs_path)
        except SecurityViolation as e:
            return ToolResult(
                success=False, content="", error=str(e), error_type="security"
            )

        if not resolved.exists():
            return ToolResult(
                success=False,
                content="",
                error=f"文件不存在: {file_path}",
                error_type="not_found",
            )
        if not resolved.is_file():
            return ToolResult(
                success=False,
                content="",
                error=f"路径不是文件: {file_path}",
                error_type="exec_error",
            )

        encoding = "utf-8"
        try:
            lines = resolved.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            encoding = "latin-1"
            lines = resolved.read_text(encoding="latin-1").splitlines()

        total_lines = len(lines)

        # 计算切片范围
        start = 0
        end = total_lines
        if offset is not None:
            start = max(0, offset - 1)  # 1-indexed → 0-indexed
        if limit is not None:
            end = min(end, start + limit)

        sliced = lines[start:end]

        # 构造返回内容：带行号（对齐 mewcode 的 \t 分隔格式）
        numbered_lines = []
        for i, line in enumerate(sliced, start=start + 1):
            numbered_lines.append(f"{i}\t{line}")
        content_body = "\n".join(numbered_lines)

        header = f"[{file_path}] 共 {total_lines} 行"
        if encoding != "utf-8":
            header += f"（编码: {encoding}，写入时将保持此编码）"
        if offset is not None or limit is not None:
            header += f"，读取第 {start + 1}-{start + len(sliced)} 行"
        content = header + "\n" + content_body

        # 记录到文件状态缓存（供后续写入/编辑检查）
        if self._cache is not None:
            try:
                full_content = "\n".join(lines)
                mtime_ns = resolved.stat().st_mtime_ns
                self._cache.record(str(resolved), full_content, mtime_ns)
            except OSError:
                pass

        return ToolResult(success=True, content=content)


class WriteFileTool(Tool):
    """创建或覆盖写入文件，自动创建中间目录。"""

    def __init__(self, project_root: str | None = None,
                 cache: "FileStateCache | None" = None) -> None:
        self._project_root = project_root or os.getcwd()
        self._cache = cache

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "创建或覆盖写入文件。若文件所在目录不存在则自动创建。"
            "小心：此操作会覆盖已有文件的全部内容。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要写入的文件路径（相对于工作目录）。",
                },
                "content": {
                    "type": "string",
                    "description": "要写入文件的完整内容。",
                },
            },
            "required": ["file_path", "content"],
        }

    timeout: float = 10.0

    async def _execute(self, file_path: str, content: str) -> ToolResult:
        validator = PathValidator(cwd=self._project_root)
        try:
            abs_path = resolve_path(file_path)
            resolved = validator.validate(abs_path)
        except SecurityViolation as e:
            return ToolResult(
                success=False, content="", error=str(e), error_type="security"
            )

        # 文件状态缓存检查（若文件已存在，需先被读取过且未被外部修改）
        if self._cache is not None and resolved.exists():
            ok, err_msg = self._cache.check(str(resolved))
            if not ok:
                return ToolResult(
                    success=False, content="", error=err_msg, error_type="security"
                )

        # 检测已有文件的编码，保持编码一致性
        encoding = "utf-8"
        if resolved.exists() and resolved.is_file():
            encoding = _detect_file_encoding(resolved)

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding=encoding)
            msg = f"已写入 {file_path}（{len(content)} 字符）"
            if encoding != "utf-8":
                msg += f"，编码: {encoding}"
            # 更新文件状态缓存
            if self._cache is not None:
                self._cache.update(str(resolved))
            return ToolResult(success=True, content=msg)
        except Exception as e:
            return ToolResult(
                success=False,
                content="",
                error=f"写入文件失败: {e}",
                error_type="exec_error",
            )


class EditFileTool(Tool):
    """精确替换编辑：在文件中唯一匹配并替换一段文本。"""

    def __init__(self, project_root: str | None = None,
                 cache: "FileStateCache | None" = None) -> None:
        self._project_root = project_root or os.getcwd()
        self._cache = cache

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "精确替换文件中的文本片段。原文片段（old_string）必须在文件中恰好出现一次，"
            "零次或多次均报错并告知匹配次数，不执行任何修改。"
            "替换后直接将新内容写回文件。"
            "编辑前请先用 `read_file` 读取目标文件，确认 `old_string` 唯一。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要编辑的文件路径（相对于工作目录）。",
                },
                "old_string": {
                    "type": "string",
                    "description": "要被替换的原文片段。必须在文件中唯一匹配。",
                },
                "new_string": {
                    "type": "string",
                    "description": "替换后的新文本。",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    timeout: float = 10.0

    async def _execute(
        self, file_path: str, old_string: str, new_string: str
    ) -> ToolResult:
        validator = PathValidator(cwd=self._project_root)
        try:
            abs_path = resolve_path(file_path)
            resolved = validator.validate(abs_path)
        except SecurityViolation as e:
            return ToolResult(
                success=False, content="", error=str(e), error_type="security"
            )

        if not resolved.exists():
            return ToolResult(
                success=False,
                content="",
                error=f"文件不存在: {file_path}",
                error_type="not_found",
            )
        if not resolved.is_file():
            return ToolResult(
                success=False,
                content="",
                error=f"路径不是文件: {file_path}",
                error_type="exec_error",
            )

        # 文件状态缓存检查（文件需先被读取且未被外部修改）
        if self._cache is not None:
            ok, err_msg = self._cache.check(str(resolved))
            if not ok:
                return ToolResult(
                    success=False, content="", error=err_msg, error_type="security"
                )

        # 处理空 old_string 的情况
        if not old_string:
            return ToolResult(
                success=False,
                content="",
                error="old_string 不能为空",
                error_type="match_error",
            )

        try:
            file_content = resolved.read_text(encoding="utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            file_content = resolved.read_text(encoding="latin-1")
            encoding = "latin-1"

        count = file_content.count(old_string)

        if count == 0:
            return ToolResult(
                success=False,
                content="",
                error="未找到匹配文本。请检查原文片段是否正确，确保包含足够的上下文以唯一标识目标位置。",
                error_type="match_error",
            )
        elif count > 1:
            return ToolResult(
                success=False,
                content="",
                error=f"匹配到 {count} 处，请提供更精确的原文片段（含更多上下文）以唯一匹配。",
                error_type="match_error",
            )

        # 恰好 1 次匹配 → 替换
        new_content = file_content.replace(old_string, new_string, 1)
        try:
            resolved.write_text(new_content, encoding=encoding)

            # 构造 diff 输出（对齐 mewcode build_diff）
            old_lines = old_string.split("\n")
            new_lines = new_string.split("\n")
            additions = max(0, len(new_lines) - len(old_lines))
            removals = max(0, len(old_lines) - len(new_lines))

            diff_parts = []
            # - 行（删除）
            for line in old_lines:
                diff_parts.append(f"- {line}")
            # + 行（新增）
            for line in new_lines:
                diff_parts.append(f"+ {line}")

            msg_parts = [f"已成功替换 1 处。文件: {file_path}"]
            if additions > 0 or removals > 0:
                msg_parts.append(
                    f"新增 {additions} 行，删除 {removals} 行"
                )
            if encoding != "utf-8":
                msg_parts.append(f"编码: {encoding}")
            msg_parts.append("\n变更摘要:\n" + "\n".join(diff_parts))
            msg = "\n".join(msg_parts)

            # 更新文件状态缓存
            if self._cache is not None:
                self._cache.update(str(resolved))
            return ToolResult(success=True, content=msg)
        except Exception as e:
            return ToolResult(
                success=False,
                content="",
                error=f"写回文件失败: {e}",
                error_type="exec_error",
            )
