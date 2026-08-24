"""记忆文件存储 —— 笔记 .md 文件 + MEMORY.md 索引的 CRUD。"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from .types import NoteType, UpdateAction

ENTRYPOINT_NAME = "MEMORY.md"
_VALID_TYPES = {t.value for t in NoteType}
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _slug(name: str) -> str:
    """将记忆名称转为安全文件名（kebab-case）。"""
    return re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-") or "memory"


class Store:
    """单级记忆目录的 CRUD 存储。"""

    def __init__(self, dir_path: str) -> None:
        self._dir = dir_path
        self._lock = threading.Lock()

    def ensure_dir(self) -> None:
        """确保记忆目录存在。"""
        os.makedirs(self._dir, exist_ok=True)

    def load_index(self) -> str:
        """读取 MEMORY.md 索引文件内容。"""
        ep_path = os.path.join(self._dir, ENTRYPOINT_NAME)
        try:
            return Path(ep_path).read_text(encoding="utf-8")
        except OSError:
            return ""

    def _index_path(self) -> str:
        return os.path.join(self._dir, ENTRYPOINT_NAME)

    def apply(self, actions: list[UpdateAction]) -> None:
        """批量执行记忆更新操作。"""
        with self._lock:
            for action in actions:
                if action.action == "create":
                    self._do_create(action)
                elif action.action == "update":
                    self._do_update(action)
                elif action.action == "delete":
                    self._do_delete(action)

    def _do_create(self, action: UpdateAction) -> None:
        note = action.note
        if note is None:
            return
        self.ensure_dir()

        slug = _slug(note.name)
        fname = "%s_%s.md" % (note.type.value, slug)
        fpath = os.path.join(self._dir, fname)

        # ── 幂等检查：文件已存在 → 退化为 update（避免覆盖）──
        if os.path.exists(fpath):
            self._do_update(action)
            return

        now = datetime.now(timezone.utc).isoformat()
        frontmatter = (
            "---\n"
            "name: %s\n"
            "description: %s\n"
            "type: %s\n"
            "created: %s\n"
            "updated: %s\n"
            "---\n"
        ) % (note.name, note.description, note.type.value, now, now)
        content = frontmatter + "\n" + note.content + "\n"

        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)

        # 在 MEMORY.md 追加索引行（先检查是否已有同名文件引用，避免重复行）
        idx_path = self._index_path()
        if not self._index_has_entry(fname):
            index_line = "- [%s](%s) — %s\n" % (note.name, fname, note.description)
            with open(idx_path, "a", encoding="utf-8") as f:
                f.write(index_line)

    def _do_update(self, action: UpdateAction) -> None:
        note = action.note
        if note is None:
            return

        slug = _slug(note.name)
        fname = "%s_%s.md" % (note.type.value, slug)
        fpath = os.path.join(self._dir, fname)

        if not os.path.exists(fpath):
            # 文件不存在时回退到 create
            self._do_create(action)
            return

        # 读取旧文件保留 created 时间戳
        try:
            old_content = Path(fpath).read_text(encoding="utf-8")
        except OSError:
            return

        created = ""
        m = _FRONTMATTER_RE.match(old_content)
        if m:
            for line in m.group(1).split("\n"):
                if line.startswith("created:"):
                    created = line.split(":", 1)[1].strip().strip('"').strip("'")
                    break

        now = datetime.now(timezone.utc).isoformat()
        frontmatter = (
            "---\n"
            "name: %s\n"
            "description: %s\n"
            "type: %s\n"
            "created: %s\n"
            "updated: %s\n"
            "---\n"
        ) % (note.name, note.description, note.type.value, created or now, now)
        new_content = frontmatter + "\n" + note.content + "\n"

        with open(fpath, "w", encoding="utf-8") as f:
            f.write(new_content)

        # 更新 MEMORY.md 中对应行
        self._update_index_line(note.name, fname, note.description)

    def _do_delete(self, action: UpdateAction) -> None:
        note = action.note
        if note is None:
            return

        slug = _slug(note.name)
        fname = "%s_%s.md" % (note.type.value, slug)
        fpath = os.path.join(self._dir, fname)

        try:
            os.remove(fpath)
        except OSError:
            pass

        # 从 MEMORY.md 移除对应行
        self._remove_index_line(fname)

    def _index_has_entry(self, fname: str) -> bool:
        """检查 MEMORY.md 中是否已有指向 fname 的条目。"""
        idx_path = self._index_path()
        try:
            text = Path(idx_path).read_text(encoding="utf-8")
        except OSError:
            return False
        return ("(%s)" % fname) in text

    def _update_index_line(self, name: str, fname: str, description: str) -> None:
        """更新 MEMORY.md 中匹配 fname 的索引行。"""
        idx_path = self._index_path()
        try:
            lines = Path(idx_path).read_text(encoding="utf-8").split("\n")
        except OSError:
            return

        new_line = "- [%s](%s) — %s" % (name, fname, description)
        updated = []
        found = False
        for line in lines:
            if ("(%s)" % fname) in line:
                updated.append(new_line)
                found = True
            else:
                updated.append(line)

        if not found:
            updated.append(new_line)

        with open(idx_path, "w", encoding="utf-8") as f:
            f.write("\n".join(updated) + "\n")

    def _remove_index_line(self, fname: str) -> None:
        """从 MEMORY.md 移除匹配 fname 的行。"""
        idx_path = self._index_path()
        try:
            lines = Path(idx_path).read_text(encoding="utf-8").split("\n")
        except OSError:
            return

        updated = [line for line in lines if ("(%s)" % fname) not in line]

        with open(idx_path, "w", encoding="utf-8") as f:
            f.write("\n".join(updated) + "\n")
