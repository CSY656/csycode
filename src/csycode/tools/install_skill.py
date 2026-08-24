"""远程 Skill 安装工具 —— 对 skills/install.py 的薄封装。

对齐 mewcode 的 tools/install_skill.py：
把 URL 解析 + GitHub API 递归拉取 + 原子安装逻辑都下沉到 skills/install.py，
本工具只负责参数校验、调用 install_skill、触发 reload。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from csycode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from csycode.skills.loader import SkillLoader

logger = logging.getLogger(__name__)


class InstallSkillTool(Tool):
    """从 URL 下载并安装 skill 到用户 Skills 目录。

    支持 skills.sh、GitHub tree、raw.githubusercontent.com 三种 URL 格式。
    安装完成后自动刷新 catalog，新 skill 可通过 /<name> 或 LoadSkill 直接使用。
    """

    def __init__(
        self,
        user_skills_dir: str | None = None,
        project_skills_dir: str | None = None,
        loader: "SkillLoader | None" = None,
    ) -> None:
        """初始化 InstallSkill 工具。

        Args:
            user_skills_dir: 用户级 Skills 目录（默认 ~/.csycode/skills/）。
            project_skills_dir: 项目级 Skills 目录。
            loader: SkillLoader 实例（安装后触发 reload）。
        """
        self._loader = loader
        self._install_root = user_skills_dir  # 默认安装到用户级目录
        self._on_installed: Callable[[str], None] | None = None

    def set_loader(self, loader: "SkillLoader") -> None:
        """注入 SkillLoader 引用。"""
        self._loader = loader

    def set_on_installed(self, callback: Callable[[str], None]) -> None:
        """设置安装完成回调（TUI 用来重新注册斜杠命令）。

        对齐 mewcode: 安装新 skill 后自动注册 /<new-skill> 命令。
        """
        self._on_installed = callback

    # ── Tool 协议 ──────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "install_skill"

    @property
    def description(self) -> str:
        return (
            "从远程 URL 下载并安装 Skill。"
            "支持 skills.sh URL (https://www.skills.sh/<owner>/<repo>/<name>)、"
            "GitHub tree URL (https://github.com/<owner>/<repo>/tree/<ref>/<path>)、"
            "以及 raw SKILL.md URL。"
            "安装后自动可用 —— 调用 LoadSkill 或直接通过 /<name> 使用。"
            "当用户粘贴 Skill URL 并要求安装时调用此工具。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "Skill 来源 URL。例如: "
                        '"https://www.skills.sh/anthropic/skills/frontend-design", '
                        '"https://github.com/anthropic/skills/tree/main/skills/pdf"。'
                    ),
                },
            },
            "required": ["url"],
        }

    timeout: float = 120.0
    is_system_tool: bool = True

    # ── 执行 ──────────────────────────────────────────────────────

    async def _execute(self, url: str, **kwargs) -> ToolResult:
        """下载并安装 Skill，触发 reload。

        Args:
            url: Skill 来源 URL。

        Returns:
            ToolResult: 成功时含安装报告，失败时含错误信息。
        """
        from csycode.skills.install import parse_skill_url, install_skill

        # 1. 解析 URL
        try:
            src = parse_skill_url(url)
        except ValueError as e:
            return ToolResult(
                success=False,
                content="",
                error=str(e),
                error_type="invalid_url",
            )

        # 2. 执行安装
        try:
            report = await install_skill(src, install_root=self._install_root)
        except Exception as e:
            logger.exception("Skill 安装失败: %s", src.name)
            return ToolResult(
                success=False,
                content="",
                error=f"安装失败: {e}",
                error_type="install_error",
            )

        # 3. 刷新 catalog，让新 skill 立即可用
        if self._loader is not None:
            try:
                self._loader.reload()
            except Exception as e:
                logger.warning("Skill reload 失败: %s", e)

        # 4. 回调：通知 TUI 重新注册斜杠命令（对齐 mewcode）
        if self._on_installed is not None:
            try:
                self._on_installed(report.skill_name)
            except Exception as e:
                logger.warning("on_installed 回调失败: %s", e)

        return ToolResult(
            success=True,
            content=(
                f"已安装 Skill '{report.skill_name}' 从 {src.original}\n"
                f"目标目录: {report.target_dir}\n"
                f"文件数: {report.file_count}, 总大小: {report.total_bytes} bytes\n"
                f"现在可用 —— 调用 LoadSkill({{name: {report.skill_name!r}}}) "
                f"或直接输入 /{report.skill_name} 使用。"
            ),
        )
