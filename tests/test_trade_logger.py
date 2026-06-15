"""交易日志记录器单元测试。"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from aqsp.core.time import now_shanghai
from aqsp.audit.trade_logger import (
    TradeDecisionLog,
    PaperExecutionLog,
    TradeLogger,
)


@pytest.fixture
def temp_log_dir() -> Path:
    """创建临时日志目录。"""
    temp_dir = Path(tempfile.mkdtemp(prefix="trade_logs_"))
    yield temp_dir
    # 清理
    if temp_dir.exists():
        shutil.rmtree(temp_dir)


@pytest.fixture
def logger(temp_log_dir: Path) -> TradeLogger:
    """创建测试用的TradeLogger实例。"""
    return TradeLogger(log_dir=str(temp_log_dir))


class TestTradeDecisionLog:
    """测试TradeDecisionLog数据类。"""

    def test_trade_decision_log_creation(self) -> None:
        """测试创建TradeDecisionLog。"""
        decision = TradeDecisionLog(
            timestamp=datetime(2026, 6, 5, 9, 30, 0),
            symbol="000021",
            name="深科技",
            action="PAPER_REVIEW",
            score=75.5,
            strategies=["n_rebound", "momentum"],
            debate_summary="Bull:技术强势; Bear:估值偏高; Judge:适度参与",
            risk_check_passed=True,
            regime="sideways",
            reason="N字反弹形态+量价配合",
        )

        assert decision.symbol == "000021"
        assert decision.action == "PAPER_REVIEW"
        assert decision.score == 75.5
        assert len(decision.strategies) == 2
        assert decision.risk_check_passed is True

    def test_trade_decision_log_to_dict(self) -> None:
        """测试TradeDecisionLog的to_dict方法。"""
        decision = TradeDecisionLog(
            timestamp=datetime(2026, 6, 5, 9, 30, 0),
            symbol="000021",
            name="深科技",
            action="PAPER_REVIEW",
            score=75.5,
            strategies=["n_rebound"],
            debate_summary="test",
            risk_check_passed=True,
            regime="sideways",
            reason="test reason",
        )

        d = decision.to_dict()
        assert d["symbol"] == "000021"
        assert d["timestamp"] == "2026-06-05T09:30:00"
        assert d["action"] == "PAPER_REVIEW"
        assert d["score"] == 75.5
        assert isinstance(d["strategies"], list)


class TestPaperExecutionLog:
    """测试PaperExecutionLog数据类。"""

    def test_trade_execution_log_creation(self) -> None:
        """测试创建PaperExecutionLog。"""
        execution = PaperExecutionLog(
            timestamp=datetime(2026, 6, 5, 10, 0, 0),
            symbol="000021",
            action="PAPER_ENTRY",
            shares=100,
            price=23.50,
            cost=2350.00,
        )

        assert execution.symbol == "000021"
        assert execution.action == "PAPER_ENTRY"
        assert execution.shares == 100
        assert execution.price == 23.50

    def test_trade_execution_log_to_dict(self) -> None:
        """测试PaperExecutionLog的to_dict方法。"""
        execution = PaperExecutionLog(
            timestamp=datetime(2026, 6, 5, 10, 0, 0),
            symbol="000021",
            action="PAPER_ENTRY",
            shares=100,
            price=23.50,
            cost=2350.00,
        )

        d = execution.to_dict()
        assert d["symbol"] == "000021"
        assert d["action"] == "PAPER_ENTRY"
        assert d["shares"] == 100
        assert d["price"] == 23.50
        assert d["timestamp"] == "2026-06-05T10:00:00"


class TestTradeLoggerBasics:
    """测试TradeLogger基本功能。"""

    def test_logger_initialization(self, logger: TradeLogger) -> None:
        """测试日志记录器初始化。"""
        assert logger.log_dir.exists()
        assert logger.log_dir.is_dir()

    def test_logger_creates_directory_if_not_exists(self) -> None:
        """测试日志目录不存在时自动创建。"""
        temp_dir = Path(tempfile.gettempdir()) / "aqsp_test_logs_new"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        TradeLogger(log_dir=str(temp_dir))
        assert temp_dir.exists()

        # 清理
        shutil.rmtree(temp_dir)

    def test_logger_raises_on_permission_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试目录创建失败时抛出异常。"""

        def _raise_permission_error(
            self: Path, parents: bool = False, exist_ok: bool = False
        ) -> None:
            raise PermissionError("mocked permission denied")

        monkeypatch.setattr(Path, "mkdir", _raise_permission_error)

        with pytest.raises(IOError, match="Failed to create log directory"):
            TradeLogger(log_dir="/any/path")


class TestLogDecision:
    """测试记录交易决策。"""

    def test_log_decision(self, logger: TradeLogger) -> None:
        """测试记录决策。"""
        decision = TradeDecisionLog(
            timestamp=datetime(2026, 6, 5, 9, 30, 0),
            symbol="000021",
            name="深科技",
            action="PAPER_REVIEW",
            score=75.5,
            strategies=["n_rebound", "momentum"],
            debate_summary="Bull:技术强势",
            risk_check_passed=True,
            regime="sideways",
            reason="N字反弹",
        )

        logger.log_decision(decision)

        # 验证文件被创建
        log_file = logger.log_dir / "2026-06-05.jsonl"
        assert log_file.exists()

    def test_log_decision_persistence(self, logger: TradeLogger) -> None:
        """测试决策日志持久化。"""
        decision = TradeDecisionLog(
            timestamp=datetime(2026, 6, 5, 9, 30, 0),
            symbol="000021",
            name="深科技",
            action="PAPER_REVIEW",
            score=75.5,
            strategies=["n_rebound"],
            debate_summary="test",
            risk_check_passed=True,
            regime="sideways",
            reason="test",
        )

        logger.log_decision(decision)

        # 读取文件验证内容
        log_file = logger.log_dir / "2026-06-05.jsonl"
        with open(log_file, "r", encoding="utf-8") as f:
            line = f.readline()
            record = json.loads(line)

        assert record["type"] == "decision"
        assert record["symbol"] == "000021"
        assert record["action"] == "PAPER_REVIEW"

    def test_log_multiple_decisions_same_day(self, logger: TradeLogger) -> None:
        """测试同一天记录多条决策。"""
        for i in range(5):
            decision = TradeDecisionLog(
                timestamp=datetime(2026, 6, 5, 9, 30 + i),
                symbol=f"00002{i}",
                name=f"股票{i}",
                action="PAPER_REVIEW",
                score=70.0 + i,
                strategies=["strategy1"],
                debate_summary="test",
                risk_check_passed=True,
                regime="sideways",
                reason="test",
            )
            logger.log_decision(decision)

        log_file = logger.log_dir / "2026-06-05.jsonl"
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 5


class TestLogPaperExecution:
    """测试记录纸面验证回写。"""

    def test_log_paper_execution(self, logger: TradeLogger) -> None:
        """测试记录纸面回写。"""
        logger.log_paper_execution(
            symbol="000021",
            action="PAPER_ENTRY",
            shares=100,
            price=23.50,
            cost=2350.00,
            timestamp=datetime(2026, 6, 5, 10, 0, 0),
        )

        log_file = logger.log_dir / "2026-06-05.jsonl"
        assert log_file.exists()

    def test_log_paper_execution_persistence(self, logger: TradeLogger) -> None:
        """测试纸面回写日志持久化。"""
        logger.log_paper_execution(
            symbol="000021",
            action="PAPER_ENTRY",
            shares=100,
            price=23.50,
            cost=2350.00,
            timestamp=datetime(2026, 6, 5, 10, 0, 0),
        )

        log_file = logger.log_dir / "2026-06-05.jsonl"
        with open(log_file, "r", encoding="utf-8") as f:
            line = f.readline()
            record = json.loads(line)

        assert record["type"] == "paper_execution"
        assert record["symbol"] == "000021"
        assert record["action"] == "PAPER_ENTRY"
        assert record["shares"] == 100

    def test_log_paper_execution_invalid_action(self, logger: TradeLogger) -> None:
        """测试无效的操作类型。"""
        with pytest.raises(ValueError):
            logger.log_paper_execution(
                symbol="000021",
                action="INVALID",
                shares=100,
                price=23.50,
                cost=2350.00,
            )

    def test_log_paper_execution_invalid_shares(self, logger: TradeLogger) -> None:
        """测试无效的股数。"""
        with pytest.raises(ValueError):
            logger.log_paper_execution(
                symbol="000021",
                action="PAPER_ENTRY",
                shares=-100,
                price=23.50,
                cost=2350.00,
            )

    def test_log_paper_execution_invalid_price(self, logger: TradeLogger) -> None:
        """测试无效的价格。"""
        with pytest.raises(ValueError):
            logger.log_paper_execution(
                symbol="000021",
                action="PAPER_ENTRY",
                shares=100,
                price=-23.50,
                cost=2350.00,
            )

    def test_log_paper_execution_uses_current_time_by_default(
        self, logger: TradeLogger
    ) -> None:
        """测试纸面回写时间默认使用当前时间。"""
        logger.log_paper_execution(
            symbol="000021",
            action="PAPER_ENTRY",
            shares=100,
            price=23.50,
            cost=2350.00,
        )

        today = now_shanghai().date()
        log_file = logger.log_dir / f"{today.isoformat()}.jsonl"
        assert log_file.exists()

    def test_log_execution_rejects_real_execution_api(
        self, logger: TradeLogger
    ) -> None:
        with pytest.raises(ValueError, match="real execution logging is disabled"):
            logger.log_execution(
                symbol="000021",
                action="BUY",
                shares=100,
                price=23.50,
                cost=2350.00,
            )


class TestQueryLogs:
    """测试查询日志。"""

    def test_query_logs_empty(self, logger: TradeLogger) -> None:
        """测试查询空日志。"""
        start_date = date(2026, 6, 1)
        end_date = date(2026, 6, 5)
        results = logger.query_logs(start_date, end_date)

        assert results == []

    def test_query_logs_single_day(self, logger: TradeLogger) -> None:
        """测试查询单天的日志。"""
        # 记录一条决策
        decision = TradeDecisionLog(
            timestamp=datetime(2026, 6, 5, 9, 30, 0),
            symbol="000021",
            name="深科技",
            action="PAPER_REVIEW",
            score=75.5,
            strategies=["n_rebound"],
            debate_summary="test",
            risk_check_passed=True,
            regime="sideways",
            reason="test",
        )
        logger.log_decision(decision)

        results = logger.query_logs(date(2026, 6, 5), date(2026, 6, 5))
        assert len(results) == 1
        assert results[0]["symbol"] == "000021"

    def test_query_logs_date_range(self, logger: TradeLogger) -> None:
        """测试查询日期范围的日志。"""
        # 记录多天的日志
        for day in range(1, 6):
            decision = TradeDecisionLog(
                timestamp=datetime(2026, 6, day, 9, 30, 0),
                symbol="000021",
                name="深科技",
                action="PAPER_REVIEW",
                score=75.5,
                strategies=["n_rebound"],
                debate_summary="test",
                risk_check_passed=True,
                regime="sideways",
                reason="test",
            )
            logger.log_decision(decision)

        results = logger.query_logs(date(2026, 6, 2), date(2026, 6, 4))
        assert len(results) == 3  # 6月2日、3日、4日各一条

    def test_query_logs_with_symbol_filter(self, logger: TradeLogger) -> None:
        """测试按符号过滤日志。"""
        # 记录不同符号的日志
        for symbol in ["000021", "000022", "000023"]:
            decision = TradeDecisionLog(
                timestamp=datetime(2026, 6, 5, 9, 30, 0),
                symbol=symbol,
                name=f"股票{symbol}",
                action="PAPER_REVIEW",
                score=75.5,
                strategies=["n_rebound"],
                debate_summary="test",
                risk_check_passed=True,
                regime="sideways",
                reason="test",
            )
            logger.log_decision(decision)

        results = logger.query_logs(date(2026, 6, 5), date(2026, 6, 5), symbol="000021")
        assert len(results) == 1
        assert results[0]["symbol"] == "000021"

    def test_query_logs_invalid_date_range(self, logger: TradeLogger) -> None:
        """测试无效的日期范围。"""
        with pytest.raises(ValueError):
            logger.query_logs(date(2026, 6, 5), date(2026, 6, 1))


class TestFileManagement:
    """测试文件管理功能。"""

    def test_automatic_directory_creation(self) -> None:
        """测试目录自动创建。"""
        temp_dir = Path(tempfile.gettempdir()) / "aqsp_test_auto_dir"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        TradeLogger(log_dir=str(temp_dir))
        assert temp_dir.exists()

        shutil.rmtree(temp_dir)

    def test_multiple_loggers_same_directory(self, temp_log_dir: Path) -> None:
        """测试多个logger使用同一目录。"""
        logger1 = TradeLogger(log_dir=str(temp_log_dir))
        logger2 = TradeLogger(log_dir=str(temp_log_dir))

        decision = TradeDecisionLog(
            timestamp=datetime(2026, 6, 5, 9, 30, 0),
            symbol="000021",
            name="深科技",
            action="PAPER_REVIEW",
            score=75.5,
            strategies=["n_rebound"],
            debate_summary="test",
            risk_check_passed=True,
            regime="sideways",
            reason="test",
        )

        logger1.log_decision(decision)
        logger2.log_decision(decision)

        results = logger1.query_logs(date(2026, 6, 5), date(2026, 6, 5))
        assert len(results) == 2


class TestStatistics:
    """测试统计功能。"""

    def test_get_statistics_empty(self, logger: TradeLogger) -> None:
        """测试空日志的统计。"""
        stats = logger.get_statistics(date(2026, 6, 1), date(2026, 6, 5))

        assert stats["total_records"] == 0
        assert stats["decisions"] == 0
        assert stats["paper_executions"] == 0

    def test_get_statistics_with_data(self, logger: TradeLogger) -> None:
        """测试有数据的统计。"""
        # 记录决策
        for i in range(3):
            decision = TradeDecisionLog(
                timestamp=datetime(2026, 6, 5, 9, 30 + i),
                symbol="000021",
                name="深科技",
                action="PAPER_REVIEW" if i % 2 == 0 else "SKIP",
                score=75.5,
                strategies=["n_rebound"],
                debate_summary="test",
                risk_check_passed=True,
                regime="sideways",
                reason="test",
            )
            logger.log_decision(decision)

        # 记录纸面回写
        logger.log_paper_execution(
            symbol="000021",
            action="PAPER_ENTRY",
            shares=100,
            price=23.50,
            cost=2350.00,
            timestamp=datetime(2026, 6, 5, 10, 0, 0),
        )

        stats = logger.get_statistics(date(2026, 6, 5), date(2026, 6, 5))

        assert stats["total_records"] == 4
        assert stats["decisions"] == 3
        assert stats["paper_executions"] == 1
        assert stats["actions"]["PAPER_REVIEW"] == 2
        assert stats["actions"]["SKIP"] == 1
        assert stats["paper_actions"]["PAPER_ENTRY"] == 1
        assert "000021" in stats["symbols"]
        assert stats["symbols"]["000021"]["decisions"] == 3
        assert stats["symbols"]["000021"]["paper_executions"] == 1

    def test_get_statistics_multiple_symbols(self, logger: TradeLogger) -> None:
        """测试多个符号的统计。"""
        symbols = ["000021", "000022", "000023"]
        for symbol in symbols:
            decision = TradeDecisionLog(
                timestamp=datetime(2026, 6, 5, 9, 30, 0),
                symbol=symbol,
                name=f"股票{symbol}",
                action="PAPER_REVIEW",
                score=75.5,
                strategies=["n_rebound"],
                debate_summary="test",
                risk_check_passed=True,
                regime="sideways",
                reason="test",
            )
            logger.log_decision(decision)

        stats = logger.get_statistics(date(2026, 6, 5), date(2026, 6, 5))

        assert len(stats["symbols"]) == 3
        for symbol in symbols:
            assert symbol in stats["symbols"]


class TestCleanupOldLogs:
    """测试清理过期日志。"""

    def test_cleanup_old_logs(self, logger: TradeLogger) -> None:
        """测试清理旧日志。"""
        # 创建旧日志文件
        old_date = now_shanghai().date() - timedelta(days=100)
        old_log_file = logger.log_dir / f"{old_date.isoformat()}.jsonl"
        old_log_file.write_text('{"test": "data"}\n')

        # 创建新日志文件
        recent_date = now_shanghai().date()
        recent_log_file = logger.log_dir / f"{recent_date.isoformat()}.jsonl"
        recent_log_file.write_text('{"test": "data"}\n')

        # 清理
        deleted = logger.cleanup_old_logs(days_to_keep=30)

        assert deleted == 1
        assert not old_log_file.exists()
        assert recent_log_file.exists()

    def test_cleanup_preserves_recent_logs(self, logger: TradeLogger) -> None:
        """测试清理不删除最近的日志。"""
        # 创建多个日志文件
        for days_ago in [5, 15, 30, 60]:
            file_date = now_shanghai().date() - timedelta(days=days_ago)
            log_file = logger.log_dir / f"{file_date.isoformat()}.jsonl"
            log_file.write_text('{"test": "data"}\n')

        deleted = logger.cleanup_old_logs(days_to_keep=40)

        assert deleted == 1  # 只删除60天前的


class TestEdgeCases:
    """测试边界情况。"""

    def test_corrupted_json_line_handling(self, logger: TradeLogger) -> None:
        """测试处理损坏的JSON行。"""
        log_file = logger.log_dir / "2026-06-05.jsonl"

        # 写入混合的有效和无效JSON
        with open(log_file, "w", encoding="utf-8") as f:
            f.write('{"type": "decision", "symbol": "000021"}\n')
            f.write("{ invalid json\n")
            f.write('{"type": "paper_execution", "symbol": "000022"}\n')

        # 查询应该跳过损坏的行
        results = logger.query_logs(date(2026, 6, 5), date(2026, 6, 5))
        assert len(results) == 2

    def test_empty_log_file_handling(self, logger: TradeLogger) -> None:
        """测试处理空日志文件。"""
        log_file = logger.log_dir / "2026-06-05.jsonl"
        log_file.write_text("")

        results = logger.query_logs(date(2026, 6, 5), date(2026, 6, 5))
        assert results == []

    def test_log_file_with_blank_lines(self, logger: TradeLogger) -> None:
        """测试处理包含空行的日志文件。"""
        log_file = logger.log_dir / "2026-06-05.jsonl"

        with open(log_file, "w", encoding="utf-8") as f:
            f.write('{"type": "decision", "symbol": "000021"}\n')
            f.write("\n")
            f.write('{"type": "paper_execution", "symbol": "000022"}\n')
            f.write("  \n")

        results = logger.query_logs(date(2026, 6, 5), date(2026, 6, 5))
        assert len(results) == 2

    def test_unicode_chinese_characters(self, logger: TradeLogger) -> None:
        """测试处理中文字符。"""
        decision = TradeDecisionLog(
            timestamp=datetime(2026, 6, 5, 9, 30, 0),
            symbol="000021",
            name="深科技",
            action="PAPER_REVIEW",
            score=75.5,
            strategies=["N字反弹", "动量策略"],
            debate_summary="多头:技术强势; 空头:估值偏高; 评审:适度参与",
            risk_check_passed=True,
            regime="sideways",
            reason="形成N字反弹形态，量价配合良好",
        )

        logger.log_decision(decision)

        results = logger.query_logs(date(2026, 6, 5), date(2026, 6, 5))
        assert len(results) == 1
        assert results[0]["name"] == "深科技"
        assert "N字反弹" in results[0]["strategies"]
