"""给 Agent 提供带时区的当前时间。"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Asia/Shanghai"


def current_time_context() -> str:
    """取本轮统一使用的时间，并提醒模型用它计算相对日期。"""
    timezone_name = os.getenv("AGENT_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        now = datetime.now(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(f"AGENT_TIMEZONE 不是可识别的时区：{timezone_name}") from error
    return (
        f"当前时间：{now:%Y-%m-%d %H:%M:%S %z}（{timezone_name}）。"
        "回答今天、明天、昨天等日期问题时，以这个时间为准。"
    )


def create_current_time_tool() -> object:
    """创建可由主 Agent 或专科 Agent 主动调用的时间工具。"""
    from langchain_core.tools import tool

    @tool
    def get_current_time() -> str:
        """获取当前日期、时间、时区；需要刷新当前时刻时调用。"""
        return current_time_context()

    return get_current_time
