"""Skill 加载器 —— 两级路径扫描、热重载、catalog 管理。

对齐 mewcode 的 skills/loader.py，负责:
- 项目级 (.csycode/skills/) + 用户级 (~/.csycode/skills/) + builtins 三层
- 项目优先于用户，同名先到先得
- 单文件 .md / 目录型 SKILL.md / skill.yaml + prompt.md 三种布局
- get(name) 热重载，失败回退缓存
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

from csycode.skills.parser import SkillDef, SkillParseError, parse_skill_file

log = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────

PROJECT_SKILLS_DIR = ".csycode/skills"
USER_SKILLS_DIR = "~/.csycode/skills"


class SkillLoader:
    """三层 Skill 加载器（对齐 mewcode）。

    加载优先级：项目级 (.csycode/skills/) > 用户级 (~/.csycode/skills/) > builtins。
    首次出现的 name 占位，后续同名跳过。
    """

    def __init__(self, work_dir: str) -> None:
        self._work_dir = work_dir
        self._project_dir = Path(work_dir) / PROJECT_SKILLS_DIR
        self._user_dir = Path(USER_SKILLS_DIR).expanduser()
        self._skills: dict[str, SkillDef] = {}
        self._cache: dict[str, SkillDef] = {}
        self._dir_mod_times: dict[str, float] = {}

    # ── 加载 ──────────────────────────────────────────────────────

    def load_all(self) -> dict[str, SkillDef]:
        """扫描三层路径，加载所有 Skill。

        顺序：项目级 → 用户级 → builtins。同名 skill 先到先得。

        Returns:
            name → SkillDef 字典。
        """
        seen: dict[str, SkillDef] = {}

        # 1. 项目级优先
        for skill in self._scan_directory(self._project_dir, "project"):
            if skill.name not in seen:
                seen[skill.name] = skill

        # 2. 用户级补漏
        for skill in self._scan_directory(self._user_dir, "user"):
            if skill.name not in seen:
                seen[skill.name] = skill

        # 3. builtins 兜底
        for skill in self._load_builtins():
            if skill.name not in seen:
                seen[skill.name] = skill

        self._skills = seen
        self._cache = {k: v for k, v in seen.items()}
        self._snapshot_dir_mod_times()
        return seen

    def reload(self) -> dict[str, SkillDef]:
        """重新扫描三层目录（等价于 load_all）。"""
        return self.load_all()

    # ── 查询 ──────────────────────────────────────────────────────

    def get(self, name: str) -> SkillDef | None:
        """按名称获取 Skill，每次重读源文件实现热重载。

        解析成功 → 更新 _skills 与 _cache。
        解析失败 → 从 _cache 回退旧版本 + log.warning。

        Returns:
            SkillDef 或 None（name 不存在时）。
        """
        skill = self._skills.get(name)
        if skill is None:
            return None

        if skill.source_path is not None:
            try:
                fresh = parse_skill_file(skill.source_path)
                fresh.is_directory = skill.is_directory
                fresh.allowed_tools = skill.allowed_tools
                self._skills[name] = fresh
                self._cache[name] = fresh
                return fresh
            except SkillParseError as e:
                log.warning(
                    "热重载失败 skill '%s'，使用缓存版本: %s",
                    name,
                    e,
                )
                return self._cache.get(name, skill)

        return skill

    def get_catalog(self) -> list[tuple[str, str]]:
        """返回 (name, description) 列表，供 catalog 注入和 /skill list 使用。"""
        return [(s.name, s.description) for s in self._skills.values()]

    def get_source_label(self, name: str) -> str:
        """按路径前缀返回来源标签: project | user | builtin | unknown。"""
        skill = self._skills.get(name)
        if skill is None:
            return "unknown"
        if skill.source_path is None:
            return "builtin"
        path_str = str(skill.source_path)
        if path_str.startswith(str(self._project_dir)):
            return "project"
        if path_str.startswith(str(self._user_dir)):
            return "user"
        return "builtin"

    # ── 目录变更检测 ──────────────────────────────────────────────

    def needs_reload(self) -> bool:
        """检查 skill 目录的 mtime 是否变化（新增/删除 skill）。"""
        for dir_path, recorded in self._dir_mod_times.items():
            try:
                current = os.stat(dir_path).st_mtime
                if current != recorded:
                    return True
            except OSError:
                if recorded != 0.0:
                    return True
        # 检查新创建的目录
        for d in [str(self._user_dir), str(self._project_dir)]:
            if d not in self._dir_mod_times:
                try:
                    os.stat(d)
                    return True
                except OSError:
                    pass
        return False

    def _snapshot_dir_mod_times(self) -> None:
        """记录 skill 目录的当前 mtime。"""
        self._dir_mod_times = {}
        for d in [str(self._user_dir), str(self._project_dir)]:
            try:
                self._dir_mod_times[d] = os.stat(d).st_mtime
            except OSError:
                self._dir_mod_times[d] = 0.0

    # ── 内部扫描 ──────────────────────────────────────────────────

    def _scan_directory(self, path: Path, source: str) -> list[SkillDef]:
        """扫描单个目录下的所有 Skill。

        处理三种布局：
        - 单文件 .md：直接 parse_skill_file()
        - skill.yaml + prompt.md：对齐 Go 版 directory-type skill
        - 回退 SKILL.md：目录含 SKILL.md 文件

        解析失败 → log.warning + 跳过（N1 容错）。
        """
        results: list[SkillDef] = []
        if not path.is_dir():
            return results

        for entry in sorted(path.iterdir()):
            try:
                if entry.is_file() and entry.suffix == ".md":
                    # 单文件 skill
                    skill = parse_skill_file(entry)
                    skill.source_path = entry
                    results.append(skill)
                elif entry.is_dir():
                    # 优先尝试 skill.yaml + prompt.md 格式（对齐 Go 版）
                    skill_yaml = entry / "skill.yaml"
                    if skill_yaml.is_file():
                        skill = self._parse_skill_yaml(skill_yaml, entry)
                        if skill is not None:
                            results.append(skill)
                            continue
                    # 回退到 SKILL.md 格式
                    skill_md = entry / "SKILL.md"
                    if skill_md.is_file():
                        skill = parse_skill_file(skill_md)
                        skill.source_path = skill_md
                        skill.is_directory = True
                        results.append(skill)
            except SkillParseError as e:
                log.warning("跳过 %s skill '%s': %s", source, entry.name, e)

        return results

    @staticmethod
    def _parse_skill_yaml(yaml_path: Path, skill_dir: Path) -> SkillDef | None:
        """解析 skill.yaml + prompt.md 格式的 skill（对齐 Go 版）。"""
        try:
            data = yaml_path.read_text(encoding="utf-8")
            meta = yaml.safe_load(data)
        except (OSError, yaml.YAMLError) as e:
            log.warning("无法解析 %s: %s", yaml_path, e)
            return None

        if not isinstance(meta, dict):
            log.warning("skill.yaml 不是 mapping: %s", skill_dir)
            return None

        name = meta.get("name", "")
        if not name:
            name = skill_dir.name.lower().replace(" ", "-")

        description = meta.get("description", "")

        # 读取 prompt.md 作为 prompt body
        prompt_md = skill_dir / "prompt.md"
        prompt_body = ""
        if prompt_md.is_file():
            try:
                prompt_body = prompt_md.read_text(encoding="utf-8")
            except OSError as e:
                log.warning("无法读取 prompt.md: %s", e)

        # 没有 description 时从 prompt body 推断
        if not description and prompt_body:
            for line in prompt_body.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("---"):
                    description = line[:200]
                    break

        mode = meta.get("mode", "inline")
        if mode not in ("inline", "fork"):
            mode = "inline"

        return SkillDef(
            name=name,
            description=description,
            prompt_body=prompt_body,
            allowed_tools=meta.get("allowed_tools", []),
            mode=mode,
            model=meta.get("model"),
            context=meta.get("context", "full"),
            source_path=prompt_md if prompt_md.is_file() else yaml_path,
            is_directory=True,
        )

    def _load_builtins(self) -> list[SkillDef]:
        """加载内置 Skill（对齐 mewcode，当前为空）。"""
        return []
