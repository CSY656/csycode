"""Worktree 创建 + 快速恢复 + 创建后设置 A/B/C/D。

对齐 mewcode manager.py create() + setup.py:
- 快速恢复: 目录已存在时用 read_worktree_head_sha 还原
- 创建后设置: 四类 best-effort 环境初始化
"""

from __future__ import annotations

import fnmatch
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from .slug import validate_slug, flatten_slug
from .git import _run_git, read_worktree_head_sha

log = logging.getLogger(__name__)


async def _create_worktree(
    manager, name: str, base_ref: str, manual: bool
):
    """在 Manager 上创建 Worktree。

    对齐 mewcode WorktreeManager.create:
    1. validate_slug → 2. lock 检查重复 → 3. 快速恢复 →
    4. git worktree add -B → 5. post-creation setup → 6. 取 head_sha
    """
    from .manager import Worktree

    err = validate_slug(name)
    if err:
        raise ValueError(err)

    async with manager._lock:
        if name in manager.active:
            raise ValueError(f"Worktree 已存在: {name}")

        flat = flatten_slug(name)
        wt_path = os.path.join(manager.worktree_dir, flat)
        branch = f"worktree-{flat}"

        # 快速恢复路径
        if os.path.isdir(wt_path):
            head_sha = read_worktree_head_sha(wt_path)
            if head_sha is not None:
                log.info("快速恢复: 复用已有 Worktree at %s", wt_path)
                wt = Worktree(
                    name=name,
                    path=wt_path,
                    branch=branch,
                    based_on=base_ref,
                    head_commit=head_sha,
                    created=datetime.now(),
                    manual=manual,
                )
                manager.active[name] = wt
                return wt

        # 正常创建路径
        os.makedirs(manager.worktree_dir, exist_ok=True)

        try:
            await _run_git(
                manager.repo_root,
                "worktree",
                "add",
                "-B",
                branch,
                wt_path,
                base_ref,
            )
        except RuntimeError:
            shutil.rmtree(wt_path, ignore_errors=True)
            raise

        # 创建后设置（best-effort）
        _perform_post_creation_setup(
            manager.repo_root, wt_path, manager.symlink_directories
        )

        # 取 head SHA
        head_sha = read_worktree_head_sha(wt_path) or ""
        wt = Worktree(
            name=name,
            path=wt_path,
            branch=branch,
            based_on=base_ref,
            head_commit=head_sha,
            created=datetime.now(),
            manual=manual,
        )
        manager.active[name] = wt
        return wt


# ── 创建后设置（对齐 mewcode setup.py） ───────────────────────────


def _perform_post_creation_setup(
    repo_root: str, wt_path: str, symlink_dirs: list[str]
) -> None:
    """执行创建后四类环境初始化。

    全部 best-effort: 失败仅 stderr 警告，不中断创建流程。
    对齐 mewcode perform_post_creation_setup。
    """
    _copy_local_configs(repo_root, wt_path)
    _setup_git_hooks(repo_root, wt_path)
    _create_symlinks(repo_root, wt_path, symlink_dirs)
    _copy_ignored_files(repo_root, wt_path)


def _copy_local_configs(repo_root: str, wt_path: str) -> None:
    """创建后设置 A: 复制本地配置文件。

    复制 .csycode/config.yaml 和 .csycode/settings.local.yaml。
    """
    config_files = [
        ".csycode/config.yaml",
        ".csycode/settings.local.yaml",
    ]
    for rel in config_files:
        src = Path(repo_root) / rel
        dst = Path(wt_path) / rel
        if not src.exists():
            continue
        if dst.exists():
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(str(src), str(dst))
        except OSError as e:
            print(f"worktree: setup config copy ({rel}): {e}", file=sys.stderr)


def _setup_git_hooks(repo_root: str, wt_path: str) -> None:
    """创建后设置 B: 配置 git hooks。

    检测主仓库 core.hooksPath 与 .husky/ 目录。
    若有则同步到 Worktree。
    """
    import subprocess

    hooks_path = ""
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "config", "--get", "core.hooksPath"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            hooks_path = result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass

    if hooks_path:
        abs_hooks = str(Path(hooks_path))
        if not Path(abs_hooks).is_absolute():
            abs_hooks = str(Path(repo_root) / abs_hooks)
        try:
            subprocess.run(
                ["git", "-C", wt_path, "config", "core.hooksPath", abs_hooks],
                capture_output=True,
                timeout=10,
                check=True,
            )
        except (subprocess.SubprocessError, OSError) as e:
            print(f"worktree: setup hooks: {e}", file=sys.stderr)

    husky_dir = Path(repo_root) / ".husky"
    if husky_dir.exists():
        try:
            subprocess.run(
                ["git", "-C", wt_path, "config", "core.hooksPath", str(husky_dir)],
                capture_output=True,
                timeout=10,
                check=True,
            )
        except (subprocess.SubprocessError, OSError) as e:
            print(f"worktree: setup hooks (.husky): {e}", file=sys.stderr)


def _create_symlinks(
    repo_root: str, wt_path: str, symlink_dirs: list[str]
) -> None:
    """创建后设置 C: 软链大目录。

    对齐 mewcode _create_symlinks: 主仓存在且 Worktree 不存在时创建 symlink。
    """
    for d in symlink_dirs:
        src = Path(repo_root) / d
        dst = Path(wt_path) / d
        if not src.exists():
            continue
        if dst.exists() or dst.is_symlink():
            continue
        try:
            os.symlink(str(src), str(dst))
        except OSError as e:
            print(f"worktree: setup symlink ({d}): {e}", file=sys.stderr)


def _copy_ignored_files(repo_root: str, wt_path: str) -> None:
    """创建后设置 D: 按 .worktreeinclude 复制被忽略但运行需要的文件。

    对齐 mewcode _copy_ignored_files:
    读 .worktreeinclude 每行为 glob 模式，用 fnmatch 匹配被 git 忽略的文件。
    """
    import subprocess

    include_file = Path(repo_root) / ".worktreeinclude"
    if not include_file.exists():
        return

    try:
        patterns = [
            line.strip()
            for line in include_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    except OSError as e:
        print(f"worktree: setup read .worktreeinclude: {e}", file=sys.stderr)
        return

    if not patterns:
        return

    # 列出所有被忽略的文件
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                repo_root,
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        ignored_files = (
            result.stdout.strip().split("\n")
            if result.returncode == 0
            else []
        )
    except (subprocess.SubprocessError, OSError) as e:
        print(f"worktree: setup list-ignored: {e}", file=sys.stderr)
        return

    for f in ignored_files:
        if not f.strip():
            continue
        for pat in patterns:
            if fnmatch.fnmatch(f, pat) or f.startswith(pat.rstrip("*")):
                src = Path(repo_root) / f
                dst = Path(wt_path) / f
                if not src.exists() or dst.exists():
                    continue
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if src.is_file():
                        shutil.copy(str(src), str(dst))
                    elif src.is_dir():
                        shutil.copytree(str(src), str(dst))
                except OSError as e:
                    print(
                        f"worktree: setup copy-included ({f}): {e}",
                        file=sys.stderr,
                    )
