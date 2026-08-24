"""危险命令黑名单 —— 启发式、非完备、不可配置放开（N1）。

内置一组编译好的正则模式，匹配常见的危险 shell 命令。
黑名单拦截位于权限流水线第一层，**所有模式（含 BYPASS）下均生效**。
仅对 Category.EXEC（bash 工具）的命令串做检查，非命令类工具跳过。
"""

from __future__ import annotations

import re

# ── 黑名单模式集 ──────────────────────────────────────────────────────────
# 注意：这些是启发式规则，无法穷尽所有危险命令。
# 防御纵深由沙箱 + 规则引擎 + 人在回路补充。

_BLACKLIST_PATTERNS: list[str] = [
    # 1. rm 递归强制删除危险路径（/ ~ $HOME /*）
    r"rm\s+.*(?:-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(?:/|~|\$HOME|/\*)",
    r"rm\s+-(?:-[a-zA-Z]*[rf])+\s+(?:/|~|\$HOME|/\*)",
    # 2. dd 写入块设备
    r"dd\s+.*of=/dev/(?:sd[a-z]|hd[a-z]|nvme|disk)",
    # 3. Fork bomb（经典 :(){ :|:& };: 模式）
    r":\(\)\s*\{[^}]*\|[^}]*&\s*\}[^;]*;",
    # 4. 格式化文件系统
    r"mkfs\.",
    # 5. 重定向覆写磁盘设备
    r">\s*/dev/(?:sd[a-z]|hd[a-z]|nvme|disk)",
    # 6. chmod -R 777 危险路径
    r"chmod\s+.*-R\s+.*0?777\s+/",
    # 7. 强制覆写关键系统文件
    r">\s*/etc/(?:passwd|shadow|sudoers|hosts)",
    # 8. curl/wget 管道到 shell（需结合上下文，此处做保守匹配）
    r"(?:curl|wget)\s+.*\|\s*(?:ba)?sh",
    # 9. 清空/覆写磁盘原始设备
    r"(?:cat|cp)\s+/dev/(?:zero|null|urandom)\s+>\s*/dev/(?:sd[a-z]|hd[a-z]|nvme)",
    # 10. 递归删除系统目录
    r"rm\s+-(?:-[a-zA-Z]*[rf])+\s+/(?:etc|usr|var|boot|lib|bin|sbin|sys|proc|dev)",
]

# 编译正则（模块加载时执行一次）
_BLACKLIST: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in _BLACKLIST_PATTERNS
]


def hits_blacklist(command: str) -> bool:
    """检查命令串是否命中黑名单中的任一危险模式。

    Args:
        command: 要检查的 shell 命令字符串。

    Returns:
        True 如果命中黑名单，False 否则。
    """
    if not command.strip():
        return False
    return any(p.search(command) for p in _BLACKLIST)


def blacklist_pattern_count() -> int:
    """返回已注册的黑名单模式数量（供测试/诊断使用）。"""
    return len(_BLACKLIST)


# ── 安全命令白名单 ───────────────────────────────────────────────────────────
# 这些命令在所有模式下都自动放行（不弹窗），但**前提是通过了黑名单检查**。
# 对齐 mewcode permissions/dangerous.py _SAFE_COMMANDS。

_SAFE_COMMANDS = frozenset({
    "ls", "dir", "pwd", "echo", "cat", "head", "tail", "wc",
    "find", "which", "whereis", "whoami", "hostname", "uname",
    "date", "cal", "uptime", "df", "du", "free", "env", "printenv",
    "file", "stat", "readlink", "realpath", "basename", "dirname",
    "sort", "uniq", "tr", "cut", "awk", "sed", "grep", "egrep", "fgrep",
    "diff", "comm", "tee", "xargs", "true", "false", "test",
    "git status", "git log", "git diff", "git show", "git branch",
    "git tag", "git remote", "git rev-parse", "git ls-files",
    "git blame", "git stash list",
    "go version", "go env",
    "node -v", "npm -v", "npx",
    "python --version", "pip list",
    "cargo --version", "rustc --version",
    "java -version", "java --version",
})


def is_safe_command(command: str) -> bool:
    """检查命令是否在白名单中且不含 shell 元字符。

    安全命令定义：只读、无副作用、不修改文件系统或进程状态。
    必须同时满足：(1) 在白名单中 (2) 不含任何 shell 元字符。

    Args:
        command: 去首尾空白的 shell 命令字符串。

    Returns:
        True 如果是安全命令，False 否则。
    """
    trimmed = command.strip()
    if not trimmed:
        return False
    # 禁止任何 shell 元字符
    for ch in ("|", ";", "&&", ">", "$(", "`"):
        if ch in trimmed:
            return False
    # 精确匹配或前缀匹配（如 "git log --oneline" 匹配 "git log"）
    for safe in _SAFE_COMMANDS:
        if trimmed == safe or trimmed.startswith(safe + " "):
            return True
    return False
