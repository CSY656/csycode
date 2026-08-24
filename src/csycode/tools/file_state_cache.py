"""文件状态缓存：强制「先读后写」协议 + mtime 外部修改检测。

对齐 mewcode tools/file_state_cache.py，防止 LLM 在未读取文件的情况下
直接编辑或覆盖写入，也防止读取后文件被外部修改导致冲突。
"""

from __future__ import annotations

from pathlib import Path


class FileStateCache:
    """追踪已读取文件的状态，对写入/编辑做双重门控。

    存储 { absolute_path: (content, mtime_ns) }，在 ReadFile 调用后记录。
    EditFile 和 WriteFile 在操作前检查:
      - Gate 1: 文件必须已被读取（缓存中存在）。
      - Gate 2: 文件未被外部修改（mtime_ns 匹配读取时的值）。
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, int]] = {}

    def record(self, path: str, content: str, mtime_ns: int) -> None:
        """读取成功后记录文件内容和修改时间。"""
        self._cache[path] = (content, mtime_ns)

    def check(self, path: str) -> tuple[bool, str]:
        """检查文件是否可以安全编辑/写入。

        Returns:
            (ok, error_message)。ok 为 True 时 error_message 为空字符串。
        """
        entry = self._cache.get(path)
        if entry is None:
            return False, (
                "Error: 文件尚未被读取。请先用 read_file 读取该文件后再编辑。"
            )

        _, cached_mtime_ns = entry
        try:
            current_mtime_ns = Path(path).stat().st_mtime_ns
        except OSError:
            # 文件可能已被删除，允许写入操作继续
            # (WriteFile 会重新创建，EditFile 会自己报错)
            return True, ""

        if current_mtime_ns != cached_mtime_ns:
            return False, (
                "Error: 文件自上次读取后已被外部修改。请重新用 read_file 读取后再编辑。"
            )

        return True, ""

    def update(self, path: str) -> None:
        """写入/编辑成功后更新缓存条目。"""
        try:
            p = Path(path)
            content = p.read_text(encoding="utf-8")
            mtime_ns = p.stat().st_mtime_ns
            self._cache[path] = (content, mtime_ns)
        except OSError:
            # 读不回就移除过期条目
            self._cache.pop(path, None)

    def invalidate(self, path: str) -> None:
        """显式移除缓存条目。"""
        self._cache.pop(path, None)
