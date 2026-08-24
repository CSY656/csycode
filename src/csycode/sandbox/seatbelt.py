"""macOS Seatbelt 沙箱实现。

通过 sandbox-exec -p <profile> 执行命令，利用 macOS 内核 Seatbelt
框架限制进程的文件写入和网络访问。
"""

from __future__ import annotations

import shlex
from pathlib import Path

from csycode.sandbox import Sandbox, SandboxConfig

_SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _build_profile(config: SandboxConfig) -> str:
    """根据 SandboxConfig 生成 SBPL profile 字符串。"""
    rules: list[str] = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow sysctl-read)",
        '(allow file-read* (subpath "/"))',
    ]

    for path in config.allow_write:
        resolved = str(Path(path).resolve())
        rules.append(f'(allow file-write* (subpath "{resolved}"))')

    for path in config.deny_write:
        resolved = str(Path(path).resolve())
        matcher = "subpath" if Path(resolved).is_dir() else "literal"
        rules.append(f'(deny file-write* ({matcher} "{resolved}"))')

    if config.network_enabled:
        rules.append("(allow network*)")
    else:
        rules.append("(deny network*)")

    return "\n".join(rules)


class SeatbeltSandbox(Sandbox):
    """macOS sandbox-exec 沙箱。"""

    def wrap(self, command: str, config: SandboxConfig) -> str:
        profile = _build_profile(config)
        return f"{_SANDBOX_EXEC} -p {shlex.quote(profile)} bash -c {shlex.quote(command)}"

    def available(self) -> bool:
        return Path(_SANDBOX_EXEC).is_file()
