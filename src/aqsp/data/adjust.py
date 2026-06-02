from __future__ import annotations

import logging
from datetime import date
from typing import Literal
import pandas as pd

from aqsp.data.cache import DataCache

logger = logging.getLogger(__name__)


class AdjustmentService:
    """
    符合 docs/architecture.md §3.1 约定的复权服务：
    - 不复权原始数据用于 ledger/回测
    - 前复权用于技术指标计算
    - point-in-time 复权因子管理
    """

    def __init__(self, cache: DataCache | None = None):
        self.cache = cache or DataCache()

    def get_point_in_time_factors(self, symbol: str, dates: list[str]) -> pd.DataFrame:
        factors = []
        for dt in dates:
            date_obj = pd.to_datetime(dt).date()
            factor = self.cache.get_adj_factor(symbol, date_obj)
            factors.append({"date": dt, "adj_factor": factor or 1.0})
        return pd.DataFrame(factors)

    def apply_qfq(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        前复权：用最新价格向前回溯，用于技术指标计算（仅用于指标生成，不用于 ledger/回测）
        """
        df = df.copy().sort_values("date").reset_index(drop=True)
        if "adj_factor" not in df.columns:
            symbols = df["symbol"].unique()
            if len(symbols) == 1:
                factors = self.get_point_in_time_factors(symbols[0], list(df["date"]))
                df = df.merge(factors, on="date", how="left")
            else:
                df["adj_factor"] = 1.0

        df["adj_factor"] = df["adj_factor"].ffill().fillna(1.0)
        latest_factor = df["adj_factor"].iloc[-1] if not df.empty else 1.0

        df["open_qfq"] = df["open"] * df["adj_factor"] / latest_factor
        df["high_qfq"] = df["high"] * df["adj_factor"] / latest_factor
        df["low_qfq"] = df["low"] * df["adj_factor"] / latest_factor
        df["close_qfq"] = df["close"] * df["adj_factor"] / latest_factor

        return df

    def apply_hfq(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        后复权：保持上市价不变，向后累积因子，用于长期收益计算
        """
        df = df.copy().sort_values("date").reset_index(drop=True)
        if "adj_factor" not in df.columns:
            symbols = df["symbol"].unique()
            if len(symbols) == 1:
                factors = self.get_point_in_time_factors(symbols[0], list(df["date"]))
                df = df.merge(factors, on="date", how="left")
            else:
                df["adj_factor"] = 1.0

        df["adj_factor"] = df["adj_factor"].ffill().fillna(1.0)

        df["open_hfq"] = df["open"] * df["adj_factor"]
        df["high_hfq"] = df["high"] * df["adj_factor"]
        df["low_hfq"] = df["low"] * df["adj_factor"]
        df["close_hfq"] = df["close"] * df["adj_factor"]

        return df

    def fetch_and_cache_factors(self, symbol: str, start: date, end: date) -> None:
        try:
            import akshare as ak

            df = ak.stock_zh_a_adjust_factor(symbol=symbol)
            if not df.empty:
                df = df[["日期", "复权因子"]].rename(
                    columns={"日期": "date", "复权因子": "adj_factor"}
                )
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                df = df[
                    (df["date"] >= start.strftime("%Y-%m-%d"))
                    & (df["date"] <= end.strftime("%Y-%m-%d"))
                ]
                if not df.empty:
                    self.cache.set_adj_factors(symbol, df, source="akshare")
                    logger.info(
                        "复权因子缓存成功: symbol=%s, count=%d", symbol, len(df)
                    )
        except Exception:
            logger.warning(
                "获取复权因子失败: symbol=%s, start=%s, end=%s",
                symbol,
                start,
                end,
                exc_info=True,
            )

    def ensure_factors_cached(self, symbol: str, dates: list[str]) -> None:
        if not dates:
            return
        min_date = pd.to_datetime(min(dates)).date()
        max_date = pd.to_datetime(max(dates)).date()
        self.fetch_and_cache_factors(symbol, min_date, max_date)

    def get_adjusted_df(
        self, df_raw: pd.DataFrame, adjust: Literal["", "qfq", "hfq"]
    ) -> pd.DataFrame:
        """
        获取指定复权方式的数据，符合架构约定：
        - adjust="" : 原始不复权（默认，用于 ledger/回测）
        - adjust="qfq": 前复权（用于指标计算）
        - adjust="hfq": 后复权（用于收益计算）
        """
        df = df_raw.copy()
        if adjust == "":
            return df  # 原始数据不修改

        # 确保有复权因子
        if "adj_factor" not in df.columns:
            symbols = df["symbol"].unique()
            if len(symbols) == 1:
                self.ensure_factors_cached(symbols[0], list(df["date"]))
                factors = self.get_point_in_time_factors(symbols[0], list(df["date"]))
                df = df.merge(factors, on="date", how="left")
            else:
                df["adj_factor"] = 1.0

        if adjust == "qfq":
            return self.apply_qfq(df)
        elif adjust == "hfq":
            return self.apply_hfq(df)

        return df
