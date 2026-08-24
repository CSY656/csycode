"""SubAgent 机制 —— Agent 角色定义、Catalog 加载、Fork 路径。

对齐 mewcode 的 agents 包，提供：
- Definition: Agent 角色的完整定义（从 Markdown+YAML frontmatter 解析）
- Catalog: 三层来源加载（项目 > 用户 > 内置），同名高优先级覆盖
- Source: 定义来源枚举（builtin / user / project / plugin 占位）
- parse_definition / parse_file: 解析器
- builtin_definitions: importlib.resources 读取内置 .md
- load_catalog: 便利函数，从项目根目录构建 Catalog
"""

from __future__ import annotations

from .definition import Definition, Source
from .parser import parse_definition, parse_file
from .catalog import Catalog, load_catalog
from .embed import builtin_definitions

__all__ = [
    "Definition",
    "Source",
    "Catalog",
    "load_catalog",
    "parse_definition",
    "parse_file",
    "builtin_definitions",
]
