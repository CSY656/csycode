"""文件路径安全验证模块。

PathValidator 确保所有文件路径操作限定在工作目录和配置白名单内。
"""

from __future__ import annotations

import os
from pathlib import Path


class SecurityViolation(Exception):
    """路径安全违规异常，由 PathValidator 在验证失败时抛出。

    Attributes:
        raw_path: 用户/模型请求的原始路径。
        cwd: 当前工作目录。
    """

    def __init__(self, raw_path: str, cwd: Path) -> None:
        self.raw_path = raw_path
        self.cwd = cwd
        super().__init__(f"安全限制：路径 '{raw_path}' 不在允许范围内。工作目录: {cwd}")


class PathValidator:
    """校验文件路径是否在允许范围内。

    默认限制在工作目录（cwd）内，可通过 allow_paths 添加额外白名单。
    任何试图逃逸到允许范围之外的操作都被拒绝。
    """

    def __init__(
        self, cwd: str | None = None, allow_paths: list[str] | None = None
    ) -> None:
        """初始化路径验证器。

        Args:
            cwd: 工作目录，默认使用当前进程的 cwd。
            allow_paths: 额外允许访问的路径列表（绝对路径或相对路径）。
        """
        self._cwd = Path(cwd or os.getcwd()).resolve()
        self._allowed: list[Path] = [self._cwd]
        if allow_paths:
            for p in allow_paths:
                resolved = Path(p).resolve()
                if resolved not in self._allowed:
                    self._allowed.append(resolved)

    def validate(self, raw_path: str) -> Path:
        """校验并解析路径。

        将 raw_path 相对于 cwd 解析为绝对路径，然后检查是否在允许范围内。

        Args:
            raw_path: 用户/模型提供的路径（相对或绝对）。

        Returns:
            解析后的绝对 Path 对象。

        Raises:
            SecurityViolation: 当路径不在允许范围内时抛出，
                               调用方（工具）应捕获并转为 ToolResult。
        """
        # 解析为绝对路径（兼容已为绝对路径的输入，避免 Python 3.12+ 报错）
        p = Path(raw_path)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self._cwd / p).resolve()

        # 检查是否在任一允许路径的子树内
        for allowed in self._allowed:
            try:
                resolved.relative_to(allowed)
                return resolved
            except ValueError:
                continue

        # 不在任何允许范围内
        raise SecurityViolation(raw_path, self._cwd)
