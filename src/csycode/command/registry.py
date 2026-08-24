"""命令注册中心 —— register / lookup / visible / prefix_match + 冲突检测。"""

from __future__ import annotations

from .command import Command


class Registry:
    """命令注册中心。

    维护 _by_name 字典（主名 + 别名 → Command）、_visible 排序列表。
    register 时做名字/别名冲突检测，冲突立即 raise RuntimeError。
    """

    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}
        self._visible: list[Command] = []

    def register(self, cmd: Command) -> None:
        """注册一条 Command。

        校验 cmd.name 非空且全小写、aliases 全部非空且全小写。
        遍历 (name, *aliases) 每个 key，若已存在于 _by_name 则 raise RuntimeError。
        通过后把每个 key 指向同一个 cmd。
        若 not cmd.hidden 则加入 _visible 并按 name 字典序排序。

        Raises:
            RuntimeError: 名字或别名冲突。
        """
        # 校验
        if not cmd.name or cmd.name != cmd.name.lower():
            raise RuntimeError(f"命令名不能为空且必须全小写: {cmd.name!r}")
        for alias in cmd.aliases:
            if not alias or alias != alias.lower():
                raise RuntimeError(f"别名不能为空且必须全小写: {alias!r}")

        # 冲突检测
        for key in (cmd.name, *cmd.aliases):
            if key in self._by_name:
                raise RuntimeError(f"command conflict: {key}")

        # 注册
        for key in (cmd.name, *cmd.aliases):
            self._by_name[key] = cmd

        # 可见列表
        if not cmd.hidden:
            self._visible.append(cmd)
            self._visible.sort(key=lambda c: c.name)

    def lookup(self, name: str) -> Command | None:
        """按名查找命令（大小写不敏感）。"""
        return self._by_name.get(name.lower())

    def visible(self) -> list[Command]:
        """返回已排序的可见命令副本（防外部改动）。"""
        return list(self._visible)

    def prefix_match(self, prefix: str) -> list[Command]:
        """前缀匹配命令名（仅按主名前缀，不匹配别名/描述）。

        Args:
            prefix: 含 "/" 的前缀，内部会 strip "/" 并小写化。
                    p == "" 时返回全部 visible。

        Returns:
            按 name 字典序的匹配 Command 列表。
        """
        p = prefix.lstrip("/").lower()
        if not p:
            return list(self._visible)
        return [c for c in self._visible if c.name.startswith(p)]
