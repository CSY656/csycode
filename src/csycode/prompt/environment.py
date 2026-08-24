"""环境信息采集与渲染。

采集当前运行时环境（工作目录、平台、日期、git 状态等），
渲染为系统提示的「环境信息」第二段（不缓存、每轮可能变化）。
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class Environment:
    """运行时环境快照。

    Attributes:
        working_dir: 当前工作目录（os.getcwd()）。
        platform: 操作系统标识（sys.platform）。
        date: 当前日期（ISO 格式）。
        git_status: ``git status --porcelain`` 摘要，非 git 目录或失败时为空字符串。
        version: 应用版本号（由 Agent 透传）。
        model: 当前使用的 LLM 模型 ID（provider.model）。
    """

    working_dir: str
    platform: str
    date: str
    git_status: str
    version: str
    model: str

    def render(self) -> str:
        """渲染为「环境信息」文本段，逐行 ``Key: Value``，空值项省略。"""
        lines: list[str] = []
        if self.working_dir:
            lines.append(f"Working Directory: {self.working_dir}")
        if self.platform:
            lines.append(f"Platform: {self.platform}")
        if self.date:
            lines.append(f"Date: {self.date}")
        if self.git_status:
            lines.append(f"Git Status: {self.git_status}")
        if self.version:
            lines.append(f"App Version: {self.version}")
        if self.model:
            lines.append(f"Model: {self.model}")
        return "\n".join(lines)


def gather_environment(version: str, model: str) -> Environment:
    """采集当前运行时环境信息。

    Args:
        version: 应用版本号。
        model: 当前 LLM 模型 ID。

    Returns:
        Environment 实例。git 状态采集失败时 ``git_status`` 为空字符串，
        不抛异常（N5：不读取环境变量）。
    """
    # 工作目录
    try:
        working_dir = os.getcwd()
    except OSError:
        working_dir = ""

    # 平台
    platform_str = sys.platform

    # 日期
    date_str = datetime.date.today().isoformat()

    # Git 状态（超时 2 秒，失败降级为空字符串）
    git_status = _collect_git_status()

    return Environment(
        working_dir=working_dir,
        platform=platform_str,
        date=date_str,
        git_status=git_status,
        version=version,
        model=model,
    )


def _collect_git_status() -> str:
    """采集 git 状态摘要，超时 2 秒，失败返回空字符串。"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if result.returncode != 0:
            return ""
        output = result.stdout.rstrip("\n")
        if not output:
            return "clean"
        # 摘要：统计改动文件数
        line_count = len(output.splitlines())
        return f"{line_count} file(s) changed"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
