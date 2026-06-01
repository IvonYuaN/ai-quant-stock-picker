from __future__ import annotations

import sqlite3
import os
from datetime import date
from pathlib import Path
from typing import Literal
import pandas as pd

from aqsp.data.source import DataSource, OhlcvFrame, apply_limit_suspended_adj
from aqsp.data.cache import DataCache


class SqliteDbSource(DataSource):
    name: str = "sqlite_db"

    def __init__(
        self,
        db_path: str | Path | None = None,
        cache: DataCache | None = None,
    ) -> None:
        self.db_path = Path(
            db_path
            or os.getenv("AQSP_SQLITE_DB_PATH")
            or "A股量化分析数据/astocks_qfq.db"
        )
        if not self.db_path.exists():
            raise FileNotFoundError(f"数据库不存在: {self.db_path}")
        self._use_cache = cache is not None
        self.cache = cache if cache is not None else DataCache()
        self._symbol_map: dict[str, str] | None = None

    def _load_symbol_map(self) -> dict[str, str]:
        if self._symbol_map is not None:
            return self._symbol_map
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql("SELECT ts_code, name FROM stocks", conn)
        self._symbol_map = {}
        self._name_map: dict[str, str] = {}
        for _, row in df.iterrows():
            ts_code = str(row["ts_code"]).strip()
            name = str(row["name"]).strip().rstrip("\x00")
            if "." in ts_code:
                symbol = ts_code.split(".")[0]
            else:
                symbol = ts_code
            self._symbol_map[symbol] = ts_code
            self._name_map[symbol] = name
        return self._symbol_map

    def get_symbol_name(self, symbol: str) -> str:
        if not hasattr(self, "_name_map") or self._name_map is None:
            self._load_symbol_map()
        return self._name_map.get(symbol, symbol)

    def _to_ts_code(self, symbol: str) -> str | None:
        sym_map = self._load_symbol_map()
        return sym_map.get(symbol)

    def get_available_symbols(self) -> list[str]:
        return list(self._load_symbol_map().keys())

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        with sqlite3.connect(self.db_path) as conn:
            for symbol in symbols:
                cached = (
                    self.cache.get_ohlcv(symbol, start, end)
                    if self._use_cache
                    else None
                )
                if cached is not None and not cached.empty:
                    if "name" not in cached.columns:
                        cached = cached.copy()
                        cached["name"] = self.get_symbol_name(symbol)
                    out[symbol] = cached
                    continue

                ts_code = self._to_ts_code(symbol)
                if ts_code is None:
                    continue

                if adjust == "":
                    # 默认不复权
                    df = pd.read_sql(
                        """
                        SELECT trade_date, open, high, low, close, volume, amount
                        FROM daily_qfq
                        WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ?
                        ORDER BY trade_date
                        """,
                        conn,
                        params=(ts_code, start_str, end_str),
                    )
                else:
                    # 前复权
                    df = pd.read_sql(
                        """
                        SELECT trade_date, open_qfq as open, high_qfq as high, low_qfq as low, close_qfq as close, volume, amount
                        FROM daily_qfq
                        WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ?
                        ORDER BY trade_date
                        """,
                        conn,
                        params=(ts_code, start_str, end_str),
                    )
                if df.empty:
                    continue

                df["date"] = df["trade_date"].apply(
                    lambda x: (
                        f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(str(x)) == 8 else str(x)
                    )
                )
                df = df.drop(columns=["trade_date"])
                df["symbol"] = symbol
                df["name"] = self.get_symbol_name(symbol)

                for col in ["open", "high", "low", "close", "volume", "amount"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                df = df.dropna(subset=["close"])
                if df.empty:
                    continue

                df = apply_limit_suspended_adj(
                    df, symbol, cache=self.cache if self._use_cache else None
                )
                if self._use_cache:
                    self.cache.set_ohlcv(symbol, df, source="sqlite_db")
                out[symbol] = df

        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        return {}

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        return {}

    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        with sqlite3.connect(self.db_path) as conn:
            for code in index_codes:
                cached = self.cache.get_index(code, start, end)
                if cached is not None and not cached.empty:
                    out[code] = cached
                    continue

                ts_code = self._to_ts_code(code)
                if ts_code is None:
                    continue

                df = pd.read_sql(
                    """
                    SELECT trade_date, open, high, low, close_qfq as close, volume, amount
                    FROM daily_qfq
                    WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ?
                    ORDER BY trade_date
                    """,
                    conn,
                    params=(ts_code, start_str, end_str),
                )
                if df.empty:
                    continue

                df["date"] = df["trade_date"].apply(
                    lambda x: (
                        f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(str(x)) == 8 else str(x)
                    )
                )
                df = df.drop(columns=["trade_date"])
                for col in ["open", "high", "low", "close", "volume", "amount"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["close"])
                if df.empty:
                    continue

                self.cache.set_index(code, df, source="sqlite_db")
                out[code] = df

        return out
