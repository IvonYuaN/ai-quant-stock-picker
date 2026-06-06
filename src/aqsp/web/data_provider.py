"""仪表盘数据工具 - 只读取已落盘的真实主链数据。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pandas as pd

from aqsp.audit.trade_logger import TradeLogger
from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.ledger.base import read_ledger
from aqsp.paper import read_paper_trades

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardSummary:
    signal_count: int
    latest_signal_date: str
    open_positions: int
    pending_entries: int
    not_executable: int
    closed_trades: int
    execution_logs: int


class DashboardDataProvider:
    """仪表盘数据提供器，只读信号账本、虚拟盘和执行日志。"""

    def __init__(
        self,
        ledger_path: str = "data/ledger.jsonl",
        paper_ledger_path: str = "data/paper_trades.jsonl",
        logs_path: str = "logs/trades",
    ) -> None:
        self.ledger_path = Path(ledger_path)
        self.paper_ledger_path = Path(paper_ledger_path)
        self.logs_path = Path(logs_path)
        self.logger = TradeLogger(str(logs_path))

    def load_signal_rows(self) -> list[dict]:
        try:
            return read_ledger(self.ledger_path)
        except Exception as exc:
            logger.error("加载 signal ledger 失败: %s", exc)
            return []

    def load_paper_rows(self) -> list[dict]:
        try:
            return read_paper_trades(self.paper_ledger_path)
        except Exception as exc:
            logger.error("加载 paper ledger 失败: %s", exc)
            return []

    def summarize(self) -> DashboardSummary:
        signal_rows = self.load_signal_rows()
        paper_rows = self.load_paper_rows()
        execution_logs = len(self.get_recent_execution_logs(days=7))
        latest_signal_date = ""
        if signal_rows:
            latest_signal_date = max(str(row.get("signal_date", "") or "") for row in signal_rows)
        return DashboardSummary(
            signal_count=len(signal_rows),
            latest_signal_date=latest_signal_date,
            open_positions=sum(1 for row in paper_rows if row.get("status") == "open"),
            pending_entries=sum(
                1 for row in paper_rows if row.get("status") == "pending_entry"
            ),
            not_executable=sum(
                1 for row in paper_rows if row.get("status") == "not_executable"
            ),
            closed_trades=sum(1 for row in paper_rows if row.get("status") == "closed"),
            execution_logs=execution_logs,
        )

    def latest_signal_frame(self, limit: int = 20) -> pd.DataFrame:
        rows = self.load_signal_rows()
        if not rows:
            return pd.DataFrame()
        latest_signal_date = max(str(row.get("signal_date", "") or "") for row in rows)
        latest_rows = [
            row for row in rows if str(row.get("signal_date", "") or "") == latest_signal_date
        ]
        latest_rows.sort(
            key=lambda row: float(row.get("score") or 0.0),
            reverse=True,
        )
        table = [
            {
                "日期": row.get("signal_date", ""),
                "代码": row.get("symbol", ""),
                "名称": row.get("name", ""),
                "评分": row.get("score", ""),
                "评级": row.get("rating", ""),
                "状态": row.get("status", ""),
                "数据源": row.get("run_actual_source", ""),
                "健康度": row.get("run_source_health_label", ""),
            }
            for row in latest_rows[:limit]
        ]
        return pd.DataFrame(table)

    def open_positions_frame(self) -> pd.DataFrame:
        rows = [
            row
            for row in self.load_paper_rows()
            if row.get("status") == "open"
        ]
        if not rows:
            return pd.DataFrame()
        table = [
            {
                "代码": row.get("symbol", ""),
                "名称": row.get("name", ""),
                "入场日": row.get("entry_date", ""),
                "入场价": row.get("entry_price", ""),
                "止损": row.get("stop_loss", ""),
                "止盈": row.get("take_profit", ""),
                "持有周期": row.get("horizon_days", ""),
            }
            for row in rows
        ]
        return pd.DataFrame(table)

    def paper_events_frame(self, limit: int = 20) -> pd.DataFrame:
        rows = self.load_paper_rows()
        if not rows:
            return pd.DataFrame()
        table = [
            {
                "代码": row.get("symbol", ""),
                "名称": row.get("name", ""),
                "状态": row.get("status", ""),
                "信号日": row.get("signal_date", ""),
                "入场日": row.get("entry_date", ""),
                "退出日": row.get("exit_date", ""),
                "退出原因": row.get("exit_reason", row.get("not_executable_reason", "")),
                "收益%": row.get("return_pct", ""),
            }
            for row in rows[-limit:]
        ]
        return pd.DataFrame(table[::-1])

    def get_recent_execution_logs(self, days: int = 7) -> list[dict]:
        start_date = today_shanghai() - timedelta(days=days)
        try:
            rows = self.logger.query_logs(
                start_date=start_date,
                end_date=today_shanghai(),
            )
        except Exception as exc:
            logger.error("加载执行日志失败: %s", exc)
            return []
        return [row for row in rows if row.get("type") == "execution"]

    def recent_execution_frame(self, limit: int = 20) -> pd.DataFrame:
        rows = self.get_recent_execution_logs(days=7)
        if not rows:
            return pd.DataFrame()
        table = [
            {
                "时间": row.get("timestamp", ""),
                "代码": row.get("symbol", ""),
                "动作": row.get("action", ""),
                "数量": row.get("shares", ""),
                "价格": row.get("price", ""),
                "成本": row.get("cost", ""),
            }
            for row in rows[-limit:]
        ]
        return pd.DataFrame(table[::-1])

    def latest_source_status(self) -> dict[str, str]:
        rows = self.load_signal_rows()
        if not rows:
            return {}
        latest_row = max(
            rows,
            key=lambda row: (
                str(row.get("signal_date", "") or ""),
                str(row.get("created_at", "") or ""),
            ),
        )
        return {
            "requested_source": str(latest_row.get("run_requested_source", "") or ""),
            "actual_source": str(latest_row.get("run_actual_source", "") or ""),
            "health_label": str(latest_row.get("run_source_health_label", "") or ""),
            "health_message": str(latest_row.get("run_source_health_message", "") or ""),
            "data_latest_trade_date": str(
                latest_row.get("run_data_latest_trade_date", "") or ""
            ),
            "lag_days": str(latest_row.get("run_data_lag_days", "") or ""),
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
        }
