"""结构化日志模块测试。"""

from __future__ import annotations

import json
import logging
import os
from io import StringIO

import pytest
import structlog

from aqsp.core.logging import configure_logging, get_logger


class TestLoggingConfiguration:
    """测试日志配置功能。"""

    def test_configure_logging_default(self):
        """测试默认配置。"""
        # 清理环境变量
        for key in ["AQSP_LOG_LEVEL", "AQSP_LOG_MODE"]:
            os.environ.pop(key, None)

        configure_logging(force=True)

        # 应该能获取 logger
        logger = get_logger(__name__)
        assert logger is not None

    def test_configure_logging_with_level(self):
        """测试指定日志级别。"""
        configure_logging(level="DEBUG", force=True)
        logger = get_logger(__name__)

        # 验证 DEBUG 级别可用
        logger.debug("test_debug_message", test_field="value")

    def test_configure_logging_dev_mode(self):
        """测试开发模式（彩色输出）。"""
        configure_logging(level="INFO", mode="dev", force=True)
        logger = get_logger(__name__)

        # 应该能正常记录日志
        logger.info("test_dev_mode", field="value")

    def test_configure_logging_prod_mode(self):
        """测试生产模式（JSON 格式）。"""
        configure_logging(level="INFO", mode="prod", force=True)
        logger = get_logger(__name__)

        # 应该能正常记录日志
        logger.info("test_prod_mode", field="value")

    def test_get_logger(self):
        """测试获取 logger。"""
        configure_logging(force=True)
        logger = get_logger(__name__)

        assert logger is not None
        assert hasattr(logger, "info")
        assert hasattr(logger, "error")
        assert hasattr(logger, "warning")

    def test_configure_from_env(self):
        """测试从环境变量读取配置。"""
        os.environ["AQSP_LOG_LEVEL"] = "WARNING"
        os.environ["AQSP_LOG_MODE"] = "prod"

        configure_logging(force=True)
        logger = get_logger(__name__)

        # 应该使用环境变量配置
        logger.warning("test_env_config", source="env")

        # 清理
        del os.environ["AQSP_LOG_LEVEL"]
        del os.environ["AQSP_LOG_MODE"]


class TestStructuredLogging:
    """测试结构化日志功能。"""

    def test_basic_logging(self):
        """测试基本日志记录。"""
        configure_logging(level="INFO", mode="prod", force=True)
        logger = get_logger(__name__)

        # 应该能记录各级别日志
        logger.debug("debug_event", value=1)
        logger.info("info_event", value=2)
        logger.warning("warning_event", value=3)
        logger.error("error_event", value=4)

    def test_context_binding(self):
        """测试上下文绑定。"""
        configure_logging(level="INFO", mode="prod", force=True)
        logger = get_logger(__name__)

        # 绑定上下文
        ctx_logger = logger.bind(symbol="000001", source="akshare")

        # 后续日志应该自动带上绑定的字段
        ctx_logger.info("fetch_started")
        ctx_logger.info("fetch_completed", row_count=100)

    def test_exception_logging(self):
        """测试异常日志记录。"""
        configure_logging(level="INFO", mode="prod", force=True)
        logger = get_logger(__name__)

        try:
            raise ValueError("Test exception")
        except ValueError:
            # 使用 exc_info=True 自动捕获异常信息
            logger.error("operation_failed", operation="test", exc_info=True)

    def test_log_fields(self):
        """测试日志字段。"""
        configure_logging(level="INFO", mode="prod", force=True)
        logger = get_logger(__name__)

        # 记录带多个字段的日志
        logger.info(
            "data_fetched",
            symbol="000001",
            source="akshare",
            row_count=100,
            duration_ms=250,
            cache_hit=False,
        )


class TestBackwardCompatibility:
    """测试向后兼容性。"""

    def test_stdlib_logger_integration(self):
        """测试与标准库 logging 的集成。"""
        from aqsp.core.logging import get_stdlib_logger

        configure_logging(force=True)

        # 获取标准库 logger
        stdlib_logger = get_stdlib_logger(__name__)

        # 应该仍然能正常使用
        stdlib_logger.info("Test stdlib integration")
        stdlib_logger.warning("Test warning")
        stdlib_logger.error("Test error")


class TestLogOutput:
    """测试日志输出格式。"""

    def test_json_output_format(self, capsys):
        """测试 JSON 输出格式（需要手动验证输出）。"""
        configure_logging(level="INFO", mode="prod", force=True)
        logger = get_logger(__name__)

        logger.info("test_json_format", field1="value1", field2=42)

        # 注意：由于 structlog 输出到 stdout，这里很难直接捕获
        # 实际测试中可以重定向 logging 输出到 StringIO

    def test_dev_output_format(self, capsys):
        """测试开发模式输出格式。"""
        configure_logging(level="INFO", mode="dev", force=True)
        logger = get_logger(__name__)

        logger.info("test_dev_format", field1="value1", field2=42)

        # 开发模式应该是彩色输出，这里只验证不会报错


@pytest.fixture
def clean_logging_config():
    """清理日志配置的 fixture。"""
    # 测试前：清理环境变量
    saved_env = {}
    for key in ["AQSP_LOG_LEVEL", "AQSP_LOG_MODE"]:
        if key in os.environ:
            saved_env[key] = os.environ[key]
            del os.environ[key]

    # 清理配置标记
    if hasattr(configure_logging, "_configured"):
        delattr(configure_logging, "_configured")

    yield

    # 测试后：恢复环境变量
    for key, value in saved_env.items():
        os.environ[key] = value


# 在所有测试前使用 fixture
@pytest.fixture(autouse=True)
def setup_logging(clean_logging_config):
    """自动设置日志配置。"""
    pass
