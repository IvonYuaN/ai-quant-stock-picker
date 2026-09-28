"""MultiSource 健康监控集成

在故障切换时自动记录健康事件，支持监控系统。
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)


def is_source_monitor_enabled() -> bool:
    """检查是否启用数据源健康监控"""
    return os.getenv("AQSP_ENABLE_SOURCE_MONITOR", "false").lower() in {"true", "1", "yes"}


def record_fetch_event(
    requested_source: str,
    actual_source: str,
    success: bool,
    error_message: str = "",
    response_time_ms: float | None = None,
) -> None:
    """记录数据获取事件到健康监控系统

    Args:
        requested_source: 请求的数据源ID
        actual_source: 实际使用的数据源ID
        success: 是否成功
        error_message: 错误信息（如果失败）
        response_time_ms: 响应时间（毫秒）
    """
    if not is_source_monitor_enabled():
        return

    try:
        from aqsp.data.source_health import (
            record_source_success,
            record_source_failure,
        )

        if success:
            record_source_success(
                requested_source=requested_source,
                actual_source=actual_source,
                response_time_ms=response_time_ms,
            )
        else:
            record_source_failure(
                requested_source=requested_source,
                error_message=error_message,
            )
    except Exception as exc:
        # 健康记录失败不应影响主流程
        _logger.debug(f"记录数据源健康事件失败: {exc}")


def track_multi_source_fetch(
    requested_source: str,
    actual_source: str | None,
    start_time: float,
    success: bool,
    error: Exception | None = None,
) -> None:
    """跟踪 MultiSource 获取操作的健康指标

    Args:
        requested_source: 请求的源ID（通常是"multi"）
        actual_source: 实际使用的源ID
        start_time: 开始时间（time.time()）
        success: 是否成功
        error: 错误对象（如果失败）
    """
    if not is_source_monitor_enabled():
        return

    response_time_ms = (time.time() - start_time) * 1000

    if success and actual_source:
        record_fetch_event(
            requested_source=requested_source,
            actual_source=actual_source,
            success=True,
            response_time_ms=response_time_ms,
        )
    elif error:
        error_msg = f"{type(error).__name__}: {str(error)}"
        record_fetch_event(
            requested_source=requested_source,
            actual_source=actual_source or "unknown",
            success=False,
            error_message=error_msg[:500],  # 限制错误消息长度
            response_time_ms=response_time_ms,
        )
