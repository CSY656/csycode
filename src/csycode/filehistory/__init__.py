"""文件编辑历史 —— 支持撤销/回退到任意快照点。

对齐 mewcode filehistory/history.py。

每次 EditFile / WriteFile 调用前 track_edit 备份文件内容，
每个 user turn 结束时 make_snapshot 记录快照，
/rewind 命令调 rewind 恢复文件到指定快照。
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_SNAPSHOTS = 100


@dataclass
class Backup:
    """单次文件备份。"""
    backup_path: str
    version: int
    timestamp: float


@dataclass
class Snapshot:
    """一个快照点，记录某个 user turn 结束时所有被追踪文件的状态。"""
    message_index: int
    user_text: str
    backups: dict[str, Backup] = field(default_factory=dict)
    timestamp: float = 0.0


class FileHistory:
    """文件编辑历史追踪器（线程安全）。

    用法:
        fh = FileHistory(work_dir, session_id)

        # 每次编辑前
        fh.track_edit("/path/to/file.py")

        # 每个 user turn 结束
        fh.make_snapshot(msg_index, user_text)

        # 撤销到第 N 个快照
        changed = fh.rewind(3)
    """

    def __init__(self, base_dir: str, session_id: str) -> None:
        self._session_dir = Path(base_dir) / ".csycode" / "file-history" / session_id
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._tracked: dict[str, int] = {}  # 文件路径 → 当前版本号
        self._snapshots: list[Snapshot] = []
        self._lock = threading.Lock()

    # ── 内部 ──────────────────────────────────────────────────────

    def _backup_name(self, file_path: str, version: int) -> str:
        """生成备份文件名：sha256(path)[:16]@v{version}。"""
        h = hashlib.sha256(file_path.encode()).hexdigest()[:16]
        return f"{h}@v{version}"

    # ── 公开 API ──────────────────────────────────────────────────

    def track_edit(self, path: str) -> None:
        """在编辑文件前调用，备份当前内容。

        Args:
            path: 要编辑的文件路径。
        """
        with self._lock:
            abs_path = str(Path(path).resolve())
            ver = self._tracked.get(abs_path, 0)
            new_ver = ver + 1

            try:
                data = Path(abs_path).read_bytes()
                bp = self._session_dir / self._backup_name(abs_path, new_ver)
                bp.write_bytes(data)
            except FileNotFoundError:
                pass  # 新文件，无需备份

            self._tracked[abs_path] = new_ver

    def make_snapshot(self, msg_index: int, user_text: str) -> None:
        """在 user turn 结束时调用，创建快照。

        Args:
            msg_index: 对话消息索引。
            user_text: 用户输入的文本。
        """
        with self._lock:
            backups: dict[str, Backup] = {}
            for path, ver in self._tracked.items():
                bp = self._session_dir / self._backup_name(path, ver)
                # 确保备份文件存在（可能没调用 track_edit）
                if not bp.exists():
                    try:
                        data = Path(path).read_bytes()
                        bp.write_bytes(data)
                    except (FileNotFoundError, OSError):
                        pass
                backups[path] = Backup(
                    backup_path=str(bp), version=ver, timestamp=time.time(),
                )

            self._snapshots.append(Snapshot(
                message_index=msg_index,
                user_text=user_text,
                backups=backups,
                timestamp=time.time(),
            ))
            # 修剪：最多保留 MAX_SNAPSHOTS 个快照
            if len(self._snapshots) > MAX_SNAPSHOTS:
                self._snapshots = self._snapshots[-MAX_SNAPSHOTS:]

    def get_snapshots(self) -> list[Snapshot]:
        """获取所有快照的副本。"""
        with self._lock:
            return list(self._snapshots)

    def has_snapshots(self) -> bool:
        """是否有快照。"""
        with self._lock:
            return len(self._snapshots) > 0

    def rewind(self, snapshot_index: int) -> list[str]:
        """回退到指定快照，恢复所有被追踪文件。

        Args:
            snapshot_index: 快照索引（0-based）。

        Returns:
            发生变更的文件路径列表。
        """
        with self._lock:
            if snapshot_index < 0 or snapshot_index >= len(self._snapshots):
                return []

            target = self._snapshots[snapshot_index]
            changed: list[str] = []

            for file_path, backup in target.backups.items():
                bp = Path(backup.backup_path)
                try:
                    backup_data = bp.read_bytes()
                except FileNotFoundError:
                    # 备份文件丢失 → 删除原文件
                    fp = Path(file_path)
                    if fp.exists():
                        fp.unlink()
                        changed.append(file_path)
                    continue

                fp = Path(file_path)
                try:
                    current_data = fp.read_bytes()
                except FileNotFoundError:
                    current_data = b""

                # 只在内容不同时才写回（避免无效 I/O）
                if current_data != backup_data:
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_bytes(backup_data)
                    changed.append(file_path)

            # 截断快照列表到目标位置
            self._snapshots = self._snapshots[: snapshot_index + 1]
            for file_path, backup in target.backups.items():
                self._tracked[file_path] = backup.version

            return changed
