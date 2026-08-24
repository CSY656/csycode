"""Plan Mode 的回合级注入文本（已废弃）。

⚠ 此模块的常量从未被导入或使用，实际 Plan Mode 提醒逻辑在
  prompt/reminder.py 的 plan_reminder() 中实现。
  本文保留仅用于参考，后续版本应删除。
"""

PLAN_MODE_REPEAT_INTERVAL: int = 3

FULL_PLAN_MODE_INSTRUCTIONS: str = (
    "你当前处于 Plan Mode（计划模式），只能阅读代码、分析问题，不能修改文件或执行命令。\n\n"
    "规则：\n"
    "- 只能使用只读工具：read_file、glob、grep、ask_user_question。\n"
    "- 不能使用 write_file、edit_file、run_command 等有副作用的工具。\n"
    "- 你的目标是调研代码、理清需求、生成可执行计划。\n"
    "- 当你完成调研并准备好计划时，调用 exit_plan_mode 工具。\n"
    "- 在 exit_plan_mode 的 plan_content 中写出完整的 markdown 格式计划：\n"
    "  * 方案概述\n"
    "  * 需要修改/创建的文件列表\n"
    "  * 具体步骤\n"
    "  * 注意事项\n"
    "- 不要问用户「是否可以开始执行」——直接调研并输出计划。\n"
    "- 需要澄清需求时使用 ask_user_question 工具。"
)

SLIM_PLAN_MODE_REMINDER: str = (
    "（仍在 Plan Mode 中。只读工具 only。调研完毕后调用 exit_plan_mode 输出计划。）"
)
