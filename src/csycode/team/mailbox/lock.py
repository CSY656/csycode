"""Mailbox 文件锁模块 —— 从 csycode.team.filelock 导入。

保留此文件以保证 mailbox 子包的完整性，
实际锁逻辑统一在 team.filelock 中。
"""

from __future__ import annotations

from csycode.team.filelock import acquire_lock  # noqa: F401
