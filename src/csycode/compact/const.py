"""上下文管理模块的全部硬编码常量。

这些常量的调整属于代码变更，不属于配置变更；它们不暴露为配置项。
"""

# 单条工具结果落盘阈值（字节），超过此值触发单条落盘
SINGLE_RESULT_LIMIT = 50000

# 单条 RoleTool 消息内工具结果聚合阈值（字节），超过此值按字节倒序落盘
MESSAGE_AGGREGATE_LIMIT = 200000

# 给摘要 LLM 输出预留的 token 空间
SUMMARY_RESERVE = 20000

# 自动触发的额外安全余量（token），防估算误差与单轮波动
AUTO_SAFETY_MARGIN = 13000

# 手动触发的安全余量（token），只用来判断摘要请求本身能不能塞下
MANUAL_SAFETY_MARGIN = 3000

# 恢复段最多展示几个文件
RECOVERY_FILE_LIMIT = 5

# 单个文件快照的 token 上限，超出时保留头部、截掉尾部
RECOVERY_TOKENS_PER_FILE = 5000

# 摘要后保留近期原文的 token 下界
RECENT_KEEP_TOKENS = 10000

# 摘要后保留近期原文的条数下界
RECENT_KEEP_MESSAGES = 5

# 自动摘要连续失败熔断阈值
MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES = 3

# 摘要请求自身 PTL 的"直接重试"次数（每次丢最旧 1 组）
PTL_RETRY_LIMIT = 3

# 超过直接重试次数后每次丢弃的消息组比例
PTL_DROP_PERCENTAGE = 0.2

# 增量估算的字节/token 比（纯 ASCII 内容）
ESTIMATE_CHARS_PER_TOKEN = 3.5

# 纯 CJK 内容的字节/token 比（CJK 字符 3 字节/约 1.5 token）
ESTIMATE_CHARS_PER_TOKEN_CJK = 2.0

# 预览体头部字节数上限
PREVIEW_HEAD_BYTES = 2048

# 预览体头部行数上限
PREVIEW_HEAD_LINES = 20
