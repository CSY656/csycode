"""输入解析 —— 判断是否以 / 开头并提取命令名与参数。"""

from __future__ import annotations


def parse(input_text: str) -> tuple[str, str, bool]:
    """解析用户输入，判断是否为斜杠命令。

    Args:
        input_text: 用户输入的原始文本。

    Returns:
        (name, args, is_slash) 元组：
        - is_slash=False → ("", "", False)，不进入命令分发。
        - is_slash=True 时：
          - name: 命令名（小写），为空表示退化输入。
          - args: 命令参数（去除前导空白）。
    """
    text = input_text.strip()
    if not text.startswith("/"):
        return ("", "", False)

    # 仅为 "/"
    if text == "/":
        return ("", "", True)

    # 取掉前导 "/"、按 str.split(maxsplit=1) 切
    inner = text[1:]

    # "/ " 之后紧跟空格 → 退化输入
    if inner.startswith((" ", "\t")):
        return ("", inner.strip(), True)

    parts = inner.split(maxsplit=1)
    name_part = parts[0]

    # 纯空白
    if not name_part:
        return ("", "", True)

    args = parts[1].strip() if len(parts) > 1 else ""

    return (name_part.lower(), args, True)
