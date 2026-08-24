"""Team 模块 feature flags。

对齐 mewcode 的 feature flag 模式。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.config import Config


def fork_teammate_enabled(cfg: Config) -> bool:
    """检查 Fork Teammate 是否启用。

    从 config.features.fork_teammate 读取，默认 False。
    """
    features = getattr(cfg, "features", None)
    if features is None:
        return False
    return bool(getattr(features, "fork_teammate", False))
