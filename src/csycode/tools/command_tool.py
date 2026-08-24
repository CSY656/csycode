"""命令执行工具：在子进程中运行 shell 命令，返回 stdout、stderr 和退出码。

ch14 fix: 根治卡死问题
- 移除内层 asyncio.wait_for，由基类 Tool.execute() 统一管理超时
- stdin=DEVNULL 防止子进程继承 stdin 导致挂起（Windows 常见根因）
- finally 块确保取消/异常/超时时进程被 kill 并 wait
- proc.wait() 有独立 5s 超时，防止僵尸进程阻塞事件循环
- 不再嵌套两层相同超时，消除竞态条件
"""

from __future__ import annotations

import asyncio

from .base import Tool, ToolResult
from .ctx import resolve_path


class RunCommandTool(Tool):
    """在工作目录中执行 shell 命令。"""

    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return (
            "在工作目录中执行 shell 命令，返回标准输出、标准错误和退出码。"
            "用于运行构建、测试、代码检查等命令。命令在子进程中运行，带超时保护。"
            "读文件、找文件、搜内容请优先用 `read_file` / `glob` / `grep`，不要用 bash 拼凑。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令字符串。",
                },
            },
            "required": ["command"],
        }

    timeout: float = 120.0

    async def _execute(self, command: str) -> ToolResult:
        """执行 shell 命令（对齐 mewcode Bash 工具）。

        ch14 fix:
        - 不自行做 asyncio.wait_for，由基类 Tool.execute() 统一超时管理
        - 避免嵌套两层相同超时的竞态条件（外层取消时内层清理不执行 → 僵尸进程）
        - stdin=DEVNULL 彻底关闭子进程 stdin，Windows 下 inherit stdin 是挂起常见根因
        - finally 块保证任何退出路径（正常/异常/CancelledError）都 kill 进程
        - proc.wait() 独立 5s 超时，防止 kill 后进程不响应
        """
        cwd = resolve_path("")
        proc = None
        stdout_bytes = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdin=asyncio.subprocess.DEVNULL,   # ch14: 关闭 stdin，防止挂起
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout_bytes, _ = await proc.communicate()
        except Exception as e:
            # 进程创建失败、communicate 异常等
            return ToolResult(
                success=False,
                content="",
                error=f"命令执行失败: {e}",
                error_type="exec_error",
            )
        finally:
            # 确保进程被清理（覆盖 CancelledError / 异常 / 正常完成）
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except Exception:
                    pass

        # 防御：proc 在异常路径（如 CancelledError 绕过 except Exception）之后
        # 可能已被 finally 清理但仍为 None；在此显式保护。
        if proc is None:
            return ToolResult(
                success=False,
                content="",
                error="命令执行被中断或进程未创建",
                error_type="exec_error",
            )
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        exit_code = proc.returncode if proc.returncode is not None else -1

        result_parts = [f"EXIT_CODE: {exit_code}"]
        if stdout:
            result_parts.append(f"STDOUT:\n{stdout}")

        return ToolResult(
            success=exit_code == 0,
            content="\n\n".join(result_parts),
        )
