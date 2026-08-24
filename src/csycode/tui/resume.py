"""会话恢复 UI —— 会话列表选择与恢复逻辑。"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from rich.text import Text
from textual.widgets import OptionList, RichLog, Static

from csycode.compact.state import open_session_context
from csycode.conversation import Conversation
from csycode.session.list import SessionInfo, list_sessions
from csycode.session.load import load_session
from csycode.session.writer import Writer

_logger = logging.getLogger(__name__)

# 时间跨度提醒阈值（小时）
STALE_HOURS = 6


def _relative_time(ts: float) -> str:
    """将 Unix 时间戳转为相对时间字符串。"""
    now = datetime.now(timezone.utc).timestamp()
    diff = now - ts
    if diff < 60:
        return "just now"
    elif diff < 3600:
        return "%d min ago" % int(diff / 60)
    elif diff < 86400:
        return "%d hours ago" % int(diff / 3600)
    else:
        return "%d days ago" % int(diff / 86400)


def _format_size(size: int) -> str:
    """格式化文件大小。"""
    if size < 1024:
        return "%dB" % size
    elif size < 1024 * 1024:
        return "%.1fKB" % (size / 1024)
    else:
        return "%.1fMB" % (size / (1024 * 1024))


def begin_resume(app: "csyCodeApp") -> None:  # noqa: F821
    """进入会话恢复选择界面。

    从 .csycode/sessions/ 扫描会话列表，构建 OptionList 展示。
    """
    from csycode.tui.app import SessionState

    sessions_dir = os.path.join(app._work_dir, ".csycode", "sessions")
    sessions = list_sessions(sessions_dir)

    selector = app.query_one("#selector", OptionList)

    if not sessions:
        log = app.query_one("#log", RichLog)
        log.write(Text("没有可恢复的会话", style="bold yellow"))
        return

    # 缓存候选列表
    app._resume_candidates = sessions

    # 构建 OptionList
    selector.clear_options()
    from textual.widgets.option_list import Option

    for info in sessions:
        title = info.title if info.title else "(无标题)"
        rel_time = _relative_time(info.modified_at) if info.modified_at > 0 else "?"
        model = info.model if info.model else "?"
        size = _format_size(info.size_bytes)
        label = "%s · %s · %s · %s" % (title, rel_time, model, size)
        selector.add_option(Option(label, id=info.id))

    # 默认高亮最新会话（第一项），保证 Enter 直接可用
    selector.highlighted = 0

    # 显示列表，切换状态，聚焦选择器（原子操作顺序）
    app._set_selector_visible(True)
    label_widget = app.query_one("#selector-label", Static)
    label_widget.update("选择要恢复的会话 (Enter 恢复, Esc 取消):")
    label_widget.remove_class("hidden")
    app.state = SessionState.RESUMING
    selector.focus()


async def do_resume_session(app: "csyCodeApp", info: SessionInfo) -> None:  # noqa: F821
    """执行会话恢复，替换当前 conversation 和 writer。"""
    from csycode.tui.app import SessionState
    from .widgets import SubmitTextArea

    log = app.query_one("#log", RichLog)

    # 乐观修改前的快照，用于失败回滚
    old_writer = getattr(app, "_writer", None)
    old_conv = app.conv
    old_ses_ctx = app.agent.runtime.session if app.agent is not None else None
    new_writer: Writer | None = None

    try:
        # 1. 加载消息
        msgs = load_session(info.dir_path)

        # 2. 检查时间跨度
        now_ts = datetime.now(timezone.utc).timestamp()
        if info.modified_at > 0 and (now_ts - info.modified_at) > STALE_HOURS * 3600:
            from csycode.llm import Message

            reminder = (
                "⚠ 此会话已暂停 %d 小时。"
                "以下是暂停前的对话摘要，请确认是否继续之前的工作。"
                % STALE_HOURS
            )
            msgs.append(Message(role="user", content=reminder))

        # 3. 打开已有会话的 writer
        try:
            new_writer = Writer.open_existing(info.dir_path)
        except OSError:
            log.write(Text("无法打开会话文件", style="bold red"))
            return

        # 4. ch12: 切离旧会话前派发 SessionEnd
        await app._dispatch_session_end()

        # 5. 替换 conversation
        new_conv = Conversation.from_messages(
            msgs,
            on_append=new_writer.on_append,
            on_replace=new_writer.on_replace,
        )
        app.conv = new_conv

        # 6. 更新 session context
        try:
            new_ses_ctx = open_session_context(app._work_dir, info.id)
        except FileNotFoundError:
            new_ses_ctx = None

        # 7. 同步 Agent 的 conversation 和 runtime
        #    （修复: 旧 conversation 的 on_append 指向已关闭的 writer，
        #     导致下次 run_agent 写 closed file 报错）
        if app.agent is not None:
            app.agent._conversation = new_conv
            if new_ses_ctx is not None:
                app.agent.runtime.reset_for_new_session(new_ses_ctx)

        # 8. 保存 writer 引用，关闭旧 writer
        app._writer = new_writer
        if old_writer is not None:
            try:
                old_writer.close()
            except OSError:
                pass

        # 9. ch12: 新会话就绪后派发 SessionResume
        await app._dispatch_session_resume()

        # 10. 返回 IDLE
        app.state = SessionState.IDLE
        app._resume_in_progress = False
        app._set_selector_visible(False)
        app._update_statusbar()
        app.query_one("#input", SubmitTextArea).focus()

        log.write(
            Text(
                "已恢复会话 %s，共 %d 条消息" % (info.id, len(msgs)),
                style="bold green",
            )
        )
    except Exception as e:
        _logger.exception("恢复会话失败")

        # 回滚：恢复旧 conversation / writer / runtime
        if old_conv is not app.conv:
            if new_writer is not None:
                try:
                    new_writer.close()
                except OSError:
                    pass
            app.conv = old_conv
            app._writer = old_writer
            if app.agent is not None and old_ses_ctx is not None:
                app.agent._conversation = old_conv
                app.agent.runtime.session = old_ses_ctx

        app.state = SessionState.IDLE
        app._resume_in_progress = False
        app._set_selector_visible(False)
        try:
            app.query_one("#input", SubmitTextArea).focus()
        except Exception:
            pass

        log.write(Text("恢复会话失败: %s" % e, style="bold red"))
