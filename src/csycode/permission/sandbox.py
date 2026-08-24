"""路径沙箱 —— 文件操作围栏检测（N2）。

核心逻辑：
  1. 解析项目根（含符号链接展开）
  2. 对目标路径做符号链接安全解析（存在则 resolve；不存在则逐级回退到最近已存在祖先）
  3. 前缀比对判定是否在项目根子树内

沙箱仅对文件类工具（read_file / write_file / edit_file / glob / grep）做检查，
不拦截 bash 命令执行（命令执行的路径围栏无法可靠静态判定）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import Engine


def resolve_root(root: str) -> str:
    """解析并规范化项目根目录（含符号链接展开、~ 展开）。

    Args:
        root: 原始项目根路径字符串。

    Returns:
        绝对、已解析符号链接的规范化路径字符串。

    Raises:
        FileNotFoundError: 项目根目录不存在。
    """
    p = Path(root).expanduser().resolve(strict=True)
    return str(p)


def eval_symlinks_or_ancestor(abs_path: str) -> str:
    """安全解析路径的符号链接。

    对已存在的路径：直接 resolve(strict=True)。
    对不存在的路径（如新建文件、含未创建的中间目录）：
      从目标开始逐级向上寻找最近已存在祖先目录，
      对该祖先 resolve(strict=True) 后拼接剩余段。

    这确保「项目内新建文件」不会因文件尚未存在而被误判为越界。

    Args:
        abs_path: 绝对路径字符串（尚未解析符号链接）。

    Returns:
        已解析符号链接的规范化路径字符串。
    """
    target = Path(abs_path)

    # 路径存在 → 直接解析
    if target.exists():
        return str(target.resolve(strict=True))

    # 逐级回退到最近已存在祖先
    resolved_parts: list[str] = []
    current = target
    while current != current.parent:
        try:
            resolved_ancestor = current.resolve(strict=True)
            # 拼接剩余未解析段
            if resolved_parts:
                result = resolved_ancestor.joinpath(*reversed(resolved_parts))
            else:
                result = resolved_ancestor
            return str(result)
        except (FileNotFoundError, OSError, RuntimeError):
            resolved_parts.append(current.name)
            current = current.parent

    # 极端情况：连根目录都不存在（不应发生，但做防御）
    return abs_path


def sandbox_ok(engine: "Engine", path: str) -> bool:
    """检查文件路径是否在项目根（沙箱）内。

    判定逻辑：
      1. 空路径 → 视为根，通过。
      2. 相对路径 → 相对 engine.root 解析为绝对路径。
      3. 对绝对路径做符号链接安全解析（含祖先回退）。
      4. 前缀比对：resolved == root 或以 root + os.sep 开头则通过。

    Args:
        engine: 权限引擎（取其 root 字段做沙箱边界）。
        path: 待检查的文件路径（可能为空、相对或绝对）。

    Returns:
        True 如果路径在项目目录内，False 如果越界。
    """
    root = engine.root

    # 空路径 → 视为项目根
    stripped = path.strip()
    if not stripped:
        return True

    # 相对路径 → 相对于项目根解析
    p = Path(stripped)
    if not p.is_absolute():
        p = Path(root, p)

    abs_path = str(p)

    # 符号链接安全解析（含祖先回退，处理新建文件场景）
    try:
        resolved = eval_symlinks_or_ancestor(abs_path)
    except (ValueError, OSError, RuntimeError):
        # 解析失败 → 保守拒绝
        return False

    # 前缀比对
    root_normalized = root.rstrip(os.sep) + os.sep
    resolved_normalized = resolved.rstrip(os.sep) + os.sep

    return resolved == root or resolved_normalized.startswith(root_normalized)
