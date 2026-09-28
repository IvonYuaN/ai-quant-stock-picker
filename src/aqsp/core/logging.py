"""结构化日志配置模块。

使用 structlog 输出 JSON Lines 格式日志，支持：
- 开发模式：彩色控制台输出
- 生产模式：JSON Lines 格式
- 环境变量 AQSP_LOG_LEVEL 配置日志级别
- 环境变量 AQSP_LOG_MODE 配置输出模式（dev/prod）

用法：
    # 在应用启动时调用一次
    from aqsp.core.logging import configure_logging
    configure_logging()

    # 获取 logger 并使用
    import structlog
    logger = structlog.get_logger(__name__)
    logger.info("operation_started", symbol="000001", source="akshare")
    logger.error("fetch_failed", symbol="000001", exc_info=True)
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog


def configure_logging(
    level: str | None = None,
    mode: str | None = None,
    force: bool = False,
) -> None:
    """配置结构化日志系统。

    Args:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）。
               默认从 AQSP_LOG_LEVEL 环境变量读取，回退到 INFO。
        mode: 输出模式（dev/prod）。
              dev: 彩色控制台输出，适合开发调试
              prod: JSON Lines 格式，适合生产环境和日志聚合
              默认从 AQSP_LOG_MODE 环境变量读取，回退到 dev。
        force: 是否强制重新配置（即使已配置过）。
    """
    # 防止重复配置（除非 force=True）
    if not force and hasattr(configure_logging, "_configured"):
        return
    configure_logging._configured = True  # type: ignore

    # 解析日志级别
    level_str = (level or os.getenv("AQSP_LOG_LEVEL", "INFO")).upper()
    log_level = getattr(logging, level_str, logging.INFO)

    # 解析输出模式
    mode_str = (mode or os.getenv("AQSP_LOG_MODE", "dev")).lower()
    is_dev = mode_str == "dev"

    # 配置标准库 logging（structlog 会集成它）
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        stream=sys.stdout,
        force=True,
    )

    # 构建处理器链
    processors: list[Any] = [
        # 添加日志级别
        structlog.stdlib.add_log_level,
        # 添加调用者信息（模块、函数、行号）
        structlog.processors.CallsiteParameterAdder(
            {
                structlog.processors.CallsiteParameter.MODULE,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            }
        ),
        # 添加时间戳
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        # 堆栈信息提取器（处理 exc_info）
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    if is_dev:
        # 开发模式：彩色控制台输出
        processors.extend([
            structlog.dev.ConsoleRenderer(
                colors=True,
                pad_event=30,
            )
        ])
    else:
        # 生产模式：JSON Lines 格式
        processors.extend([
            # 添加进程和线程信息（生产环境有用）
            structlog.processors.add_log_level,
            # 格式化为 JSON
            structlog.processors.JSONRenderer(),
        ])

    # 配置 structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 静默第三方库的日志（根据需要调整）
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("akshare").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取结构化 logger 实例。

    Args:
        name: logger 名称，通常传入 __name__。

    Returns:
        配置好的 structlog logger 实例。

    示例：
        logger = get_logger(__name__)
        logger.info("data_fetched", symbol="000001", rows=100)
    """
    return structlog.get_logger(name)


# 向后兼容：提供标准库 logging 风格的接口
def get_stdlib_logger(name: str) -> logging.Logger:
    """获取标准库 logging.Logger（用于渐进迁移）。

    返回的 logger 会被 structlog 拦截和处理，但保持标准库接口。
    适合那些暂时不想改动现有日志调用的模块。

    Args:
        name: logger 名称。

    Returns:
        标准库 Logger 实例。
    """
    return logging.getLogger(name)
