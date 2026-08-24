"""Linux bubblewrap（bwrap）沙箱实现。

通过 bwrap 创建隔离的用户命名空间，将根文件系统以只读方式挂载，
仅对白名单路径开放写权限，支持网络隔离。
"""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from csycode.sandbox import Sandbox, SandboxConfig


class BwrapSandbox(Sandbox):
    """Linux bubblewrap 沙箱。"""

    def wrap(self, command: str, config: SandboxConfig) -> str:
        args: list[str] = [
            "bwrap",
            "--unshare-user",
            "--unshare-pid",
            "--ro-bind", "/", "/",
        ]

        for path in config.allow_write:
            resolved = str(Path(path).resolve())
            args.extend(["--bind", resolved, resolved])

        for path in config.deny_write:
            resolved = str(Path(path).resolve())
            args.extend(["--ro-bind", resolved, resolved])

        if not config.network_enabled:
            args.append("--unshare-net")

        args.extend(["--proc", "/proc"])
        args.extend(["--dev", "/dev"])
        args.append("--")
        args.extend(["bash", "-c", command])

        return " ".join(shlex.quote(a) for a in args)

    def available(self) -> bool:
        return shutil.which("bwrap") is not None
