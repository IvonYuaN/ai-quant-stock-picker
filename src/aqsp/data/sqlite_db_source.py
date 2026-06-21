from __future__ import annotations

import sqlite3
import os
from datetime import date
from pathlib import Path
from typing import Literal
import pandas as pd

from aqsp.core.errors import DataError
from aqsp.data.source import (
    DataSource,
    OhlcvFrame,
    apply_limit_suspended_adj,
    require_non_empty_fetch_result,
)
from aqsp.data.cache import DataCache

_SQLITE_TIMEOUT_SECONDS = 30.0


def _parse_db_date(raw: str) -> date | None:
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _date_within_lag(left: str, right: str, *, max_days: int) -> bool:
    left_day = _parse_db_date(left)
    right_day = _parse_db_date(right)
    if left_day is None or right_day is None:
        return False
    delta = (right_day - left_day).days
    return 0 <= delta <= max_days


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
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            df = pd.read_sql("SELECT ts_code, name FROM stocks", conn)
        self._symbol_map = {}
        self._name_map: dict[str, str] = {}
        for ts_code_raw, name_raw in df[["ts_code", "name"]].itertuples(
            index=False, name=None
        ):
            ts_code = str(ts_code_raw).strip()
            name = str(name_raw).strip().rstrip("\x00")
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

    def price_mode(self) -> str:
        if "qfq" in self.db_path.name.lower():
            return "qfq"
        return "unknown"

    def get_symbols_with_daily_coverage(
        self,
        symbols: list[str],
        start: date,
        end: date,
        *,
        min_rows: int | None = None,
        min_coverage_ratio: float = 0.8,
    ) -> list[str]:
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")
        first_market_day, last_market_day, expected_rows = self._market_range(
            start_str, end_str
        )
        min_required_rows = (
            int(min_rows)
            if min_rows is not None
            else max(1, int(expected_rows * min_coverage_ratio))
        )
        covered: list[str] = []
        if not first_market_day or not last_market_day:
            return covered
        if not _date_within_lag(start_str, first_market_day, max_days=10):
            return covered
        if not _date_within_lag(last_market_day, end_str, max_days=10):
            return covered
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            for symbol in symbols:
                ts_code = self._to_ts_code(symbol)
                if ts_code is None:
                    continue
                first_date, last_date, count = conn.execute(
                    """
                    SELECT MIN(trade_date), MAX(trade_date), COUNT(*)
                    FROM daily_qfq
                    WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ?
                    """,
                    (ts_code, start_str, end_str),
                ).fetchone()
                if not first_date or not last_date:
                    continue
                if (
                    str(first_date) > first_market_day
                    or str(last_date) < last_market_day
                ):
                    continue
                if int(count) < min_required_rows:
                    continue
                covered.append(symbol)
        return covered

    def _market_range(self, start_str: str, end_str: str) -> tuple[str, str, int]:
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            row = conn.execute(
                """
                SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date)
                FROM daily_qfq
                WHERE trade_date >= ? AND trade_date <= ?
                """,
                (start_str, end_str),
            ).fetchone()
        first_day = str(row[0] or "")
        last_day = str(row[1] or "")
        return first_day, last_day, int(row[2] or 0)

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

        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
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

                # 估算缺失的 amount：用 (high + low + close) / 3 作为均价
                if "amount" in df.columns:
                    mask = df["amount"].isna() | (df["amount"] <= 0)
                    if mask.any():
                        avg_price = (df["high"] + df["low"] + df["close"]) / 3
                        df.loc[mask, "amount"] = df.loc[mask, "volume"] * avg_price
                if df.empty:
                    continue

                df = apply_limit_suspended_adj(
                    df, symbol, cache=self.cache if self._use_cache else None
                )
                if self._use_cache:
                    self.cache.set_ohlcv(symbol, df, source="sqlite_db")
                out[symbol] = df
        require_non_empty_fetch_result(self.name, "日线", symbols, out)

        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        raise DataError("sqlite_db 不支持分时数据")

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        raise DataError("sqlite_db 不支持实时行情")

    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            for code in index_codes:
                cached = (
                    self.cache.get_index(code, start, end) if self._use_cache else None
                )
                if cached is not None and not cached.empty:
                    out[code] = cached
                    continue

                ts_code = self._to_ts_code(code)
                if ts_code is None:
                    continue

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
                if df.empty:
                    continue

                df["date"] = df["trade_date"].apply(
                    lambda x: (
                        f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(str(x)) == 8 else str(x)
                    )
                )
                df = df.drop(columns=["trade_date"])
                df["symbol"] = code
                df["name"] = self.get_symbol_name(code)
                for col in ["open", "high", "low", "close", "volume", "amount"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["close"])
                if df.empty:
                    continue

                df = apply_limit_suspended_adj(
                    df, code, cache=self.cache if self._use_cache else None
                )
                validated = self._validate_ohlcv(df, code)
                if self._use_cache:
                    self.cache.set_index(code, validated, source="sqlite_db")
                out[code] = validated
        require_non_empty_fetch_result(self.name, "指数", index_codes, out)

        return out
