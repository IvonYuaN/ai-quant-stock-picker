"""
交易决策日志和审计追踪系统。

提供持久化的交易决策记录、执行记录和日志查询功能。
所有日志以JSON Lines格式存储，按日期分文件，便于grep查询和备份。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeDecisionLog:
    """
    交易决策日志。

    记录策略生成的交易信号、决策理由、风险检查结果等信息。
    """
    timestamp: datetime
    symbol: str
    name: str
    action: str  # "BUY" / "SELL" / "HOLD"
    score: float
    strategies: list[str]
    debate_summary: str
    risk_check_passed: bool
    regime: str
    reason: str

    def to_dict(self) -> dict:
        """
        转换为字典，便于JSON序列化。

        Returns:
            dict: 包含所有字段的字典
        """
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "name": self.name,
            "action": self.action,
            "score": self.score,
            "strategies": self.strategies,
            "debate_summary": self.debate_summary,
            "risk_check_passed": self.risk_check_passed,
            "regime": self.regime,
            "reason": self.reason,
        }


@dataclass
class TradeExecutionLog:
    """
    交易执行日志。

    记录实际执行的交易，包括时间、价格、数量等信息。
    """
    timestamp: datetime
    symbol: str
    action: str  # "BUY" / "SELL"
    shares: int
    price: float
    cost: float

    def to_dict(self) -> dict:
        """
        转换为字典，便于JSON序列化。

        Returns:
            dict: 包含所有字段的字典
        """
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "action": self.action,
            "shares": self.shares,
            "price": self.price,
            "cost": self.cost,
        }


class TradeLogger:
    """
    交易日志记录器。

    功能：
    - 记录交易决策（包含策略、评分、风险检查等）
    - 记录交易执行（包含价格、数量、成本等）
    - 查询日志（按日期范围和符号过滤）

    存储格式：
    - JSON Lines（每行一条完整记录）
    - 按日期分文件：logs/trades/2026-06-05.jsonl
    - 支持grep查询和备份
    """

    def __init__(self, log_dir: str = "logs/trades") -> None:
        """
        初始化日志记录器。

        Args:
            log_dir: 日志目录路径，默认为"logs/trades"

        Raises:
            IOError: 当目录创建失败时
        """
        self.log_dir = Path(log_dir)
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"日志目录已就绪: {self.log_dir}")
        except OSError as e:
            logger.error(f"无法创建日志目录 {self.log_dir}: {e}")
            raise IOError(f"Failed to create log directory: {e}") from e

    def _get_log_file(self, dt: datetime) -> Path:
        """
        根据日期获取日志文件路径。

        Args:
            dt: 日期时间对象

        Returns:
            Path: 日志文件路径（格式：YYYY-MM-DD.jsonl）
        """
        date_str = dt.strftime("%Y-%m-%d")
        return self.log_dir / f"{date_str}.jsonl"

    def log_decision(self, decision: TradeDecisionLog) -> None:
        """
        记录交易决策。

        Args:
            decision: TradeDecisionLog对象

        Raises:
            IOError: 当写入文件失败时
        """
        try:
            log_file = self._get_log_file(decision.timestamp)
            record = {
                "type": "decision",
                **decision.to_dict(),
            }

            self._append_line(log_file, record)
            logger.debug(f"决策已记录: {decision.symbol} {decision.action}")
        except Exception as e:
            logger.error(f"记录决策失败: {e}")
            raise IOError(f"Failed to log decision: {e}") from e

    def log_execution(
        self,
        symbol: str,
        action: str,
        shares: int,
        price: float,
        cost: float,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """
        记录交易执行。

        Args:
            symbol: 股票代码
            action: 操作类型 ("BUY" / "SELL")
            shares: 交易数量
            price: 交易价格
            cost: 交易成本（含手续费）
            timestamp: 执行时间，默认使用当前时间

        Raises:
            IOError: 当写入文件失败时
            ValueError: 当参数无效时
        """
        try:
            if action not in ("BUY", "SELL"):
                raise ValueError(f"Invalid action: {action}")
            if shares <= 0:
                raise ValueError(f"Invalid shares: {shares}")
            if price <= 0:
                raise ValueError(f"Invalid price: {price}")

            timestamp = timestamp or datetime.now()
            execution = TradeExecutionLog(
                timestamp=timestamp,
                symbol=symbol,
                action=action,
                shares=shares,
                price=price,
                cost=cost,
            )

            log_file = self._get_log_file(timestamp)
            record = {
                "type": "execution",
                **execution.to_dict(),
            }

            self._append_line(log_file, record)
            logger.info(
                f"执行已记录: {symbol} {action} {shares}股 @ {price:.2f}, 成本: {cost:.2f}"
            )
        except ValueError as e:
            logger.error(f"参数无效: {e}")
            raise
        except Exception as e:
            logger.error(f"记录执行失败: {e}")
            raise IOError(f"Failed to log execution: {e}") from e

    def query_logs(
        self,
        start_date: date,
        end_date: date,
        symbol: Optional[str] = None,
    ) -> list[dict]:
        """
        查询日志。

        Args:
            start_date: 起始日期
            end_date: 结束日期（包含）
            symbol: 可选，按股票代码过滤

        Returns:
            list[dict]: 符合条件的日志记录列表

        Raises:
            ValueError: 当日期范围无效时
        """
        if start_date > end_date:
            raise ValueError(f"start_date ({start_date}) > end_date ({end_date})")

        results: list[dict] = []

        try:
            # 遍历日期范围内的所有日志文件
            current_date = start_date
            while current_date <= end_date:
                log_file = self.log_dir / f"{current_date.isoformat()}.jsonl"

                if log_file.exists():
                    records = self._read_log_file(log_file)
                    for record in records:
                        # 按符号过滤
                        if symbol is None or record.get("symbol") == symbol:
                            results.append(record)

                # 移动到下一天
                current_date = current_date + timedelta(days=1)

            logger.info(
                f"查询完成: {start_date} - {end_date}, "
                f"符号: {symbol or 'all'}, 找到 {len(results)} 条记录"
            )
            return results
        except Exception as e:
            logger.error(f"查询日志失败: {e}")
            raise

    def _append_line(self, log_file: Path, record: dict) -> None:
        """
        追加JSON行到日志文件。

        Args:
            log_file: 日志文件路径
            record: 要写入的记录字典

        Raises:
            IOError: 当写入失败时
        """
        try:
            json_line = json.dumps(record, ensure_ascii=False, sort_keys=True)

            # 使用追加模式写入，确保原子性
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json_line + "\n")
        except OSError as e:
            logger.error(f"写入日志文件失败 {log_file}: {e}")
            raise IOError(f"Failed to write to log file: {e}") from e

    def _read_log_file(self, log_file: Path) -> list[dict]:
        """
        读取JSON Lines格式的日志文件。

        Args:
            log_file: 日志文件路径

        Returns:
            list[dict]: 日志记录列表

        Raises:
            IOError: 当读取失败时
        """
        records: list[dict] = []

        if not log_file.exists():
            return records

        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        record = json.loads(line)
                        records.append(record)
                    except json.JSONDecodeError as e:
                        # 单行损坏不应让整个文件读取崩溃
                        logger.warning(
                            f"日志文件 {log_file} 第 {line_num} 行 JSON 损坏，已跳过: {e}"
                        )
        except OSError as e:
            logger.error(f"读取日志文件失败 {log_file}: {e}")
            raise IOError(f"Failed to read log file: {e}") from e

        return records

    def get_statistics(
        self,
        start_date: date,
        end_date: date,
    ) -> dict:
        """
        获取时间范围内的日志统计信息。

        Args:
            start_date: 起始日期
            end_date: 结束日期

        Returns:
            dict: 统计信息（决策数、执行数、各符号统计等）
        """
        logs = self.query_logs(start_date, end_date)

        stats: dict = {
            "total_records": len(logs),
            "decisions": 0,
            "executions": 0,
            "symbols": {},
            "actions": {"BUY": 0, "SELL": 0, "HOLD": 0},
        }

        for record in logs:
            record_type = record.get("type", "unknown")
            if record_type == "decision":
                stats["decisions"] += 1
                action = record.get("action", "")
                if action in stats["actions"]:
                    stats["actions"][action] += 1
            elif record_type == "execution":
                stats["executions"] += 1

            symbol = record.get("symbol", "")
            if symbol:
                if symbol not in stats["symbols"]:
                    stats["symbols"][symbol] = {"decisions": 0, "executions": 0}
                if record_type == "decision":
                    stats["symbols"][symbol]["decisions"] += 1
                elif record_type == "execution":
                    stats["symbols"][symbol]["executions"] += 1

        return stats

    def cleanup_old_logs(self, days_to_keep: int = 90) -> int:
        """
        清理超过指定天数的日志文件。

        Args:
            days_to_keep: 保留天数，默认90天

        Returns:
            int: 删除的文件数
        """
        cutoff_date = date.today() - timedelta(days=days_to_keep)
        deleted_count = 0

        try:
            for log_file in self.log_dir.glob("*.jsonl"):
                try:
                    # 从文件名解析日期
                    date_str = log_file.stem
                    file_date = date.fromisoformat(date_str)

                    if file_date < cutoff_date:
                        log_file.unlink()
                        deleted_count += 1
                        logger.info(f"删除过期日志文件: {log_file}")
                except (ValueError, OSError) as e:
                    logger.warning(f"无法处理日志文件 {log_file}: {e}")
        except Exception as e:
            logger.error(f"清理过期日志失败: {e}")

        return deleted_count
