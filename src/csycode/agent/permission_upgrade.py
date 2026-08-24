"""权限升级回调类型 —— 对齐 mewcode agent.py 内联定义。

子 Agent 把审批请求升级到父 TUI 的回调签名。
"""

from __future__ import annotations

from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.agent.events import ApprovalRequest
    from csycode.permission import Outcome

# ApprovalUpgrader 是子 Agent 把审批请求升级到父 TUI 的回调。
# 实现方：TaskManager 把请求转发到主 TUI 的事件流；
# 前台 inline 模式直接复用现有 Approval 路径。
#
# 返回 (outcome, ok)：
#   ok=True   → 调用方使用 outcome 做决策
#   ok=False  → 调用方走默认 emit Approval 路径
ApprovalUpgrader = Callable[
    ["ApprovalRequest"],
    Awaitable["tuple[Outcome, bool]"],
]
