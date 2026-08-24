"""OS 级沙箱 —— 限制 Bash 命令的文件写入和网络访问。

对齐 mewcode sandbox/。

- macOS: sandbox-exec (Seatbelt)
- Linux: bubblewrap (bwrap)
- Windows: 不支持（返回 None）

与 permission/sandbox.py 的 PathSandbox（路径级权限检查）不同，
这里是操作系统层面的强制隔离——即使命令绕过路径检查，内核也会阻止越权。
"""

from __future__ import annotations

import platform
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SandboxConfig:
    """沙箱配置：控制可写路径、禁写路径和网络访问。"""

    allow_write: list[str] = field(default_factory=list)
    deny_write: list[str] = field(default_factory=list)
    network_enabled: bool = False


class Sandbox(ABC):
    """沙箱抽象基类。"""

    @abstractmethod
    def wrap(self, command: str, config: SandboxConfig) -> str:
        """将原始命令包装为沙箱内执行的命令字符串。"""
        ...

    @abstractmethod
    def available(self) -> bool:
        """检测当前环境是否支持该沙箱。"""
        ...


def create_sandbox() -> Sandbox | None:
    """根据操作系统自动选择沙箱实现。

    macOS -> SeatbeltSandbox (sandbox-exec)
    Linux -> BwrapSandbox (bubblewrap)
    其他系统 -> None
    """
    system = platform.system()
    if system == "Darwin":
        from csycode.sandbox.seatbelt import SeatbeltSandbox
        return SeatbeltSandbox()
    elif system == "Linux":
        from csycode.sandbox.bwrap import BwrapSandbox
        return BwrapSandbox()
    return None
