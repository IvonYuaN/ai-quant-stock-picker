from __future__ import annotations

import sqlite3
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Literal
import pandas as pd

from aqsp.core.errors import DataError
from aqsp.data.cache import (
    DataCache,
)
from aqsp.data.source import (
    DataSource,
    OhlcvFrame,
    apply_limit_suspended_adj,
    require_non_empty_fetch_result,
)

_SQLITE_TIMEOUT_SECONDS = 30.0
_SQLITE_BATCH_SIZE = 400
_ALLOW_QFQ_SQLITE_SOURCE_ENV = "AQSP_ALLOW_QFQ_SQLITE_SOURCE"
_PREFILTERED_SYMBOLS_ENV = "AQSP_SQLITE_PREFILTERED_SYMBOLS"
_LIQUID_SYMBOL_MIN_HISTORY_ROWS = 250
_LIQUID_SYMBOL_LOOKBACK_CALENDAR_DAYS = 500


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[idx : idx + size] for idx in range(0, len(items), size)]


def _format_db_date(value: object) -> str:
    text = str(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 else text


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
            or "A股量化分析数据/astocks_raw.db"
        )
        if not self.db_path.exists():
            raise FileNotFoundError(f"数据库不存在: {self.db_path}")
        self._use_cache = cache is not None
        self.cache = cache if cache is not None else DataCache()
        self._symbol_map: dict[str, str] | None = None
        self._last_coverage_snapshot: tuple[str, str, frozenset[str]] | None = None
        self._active_workload: str | None = None

    def set_workload(self, workload: str | None) -> None:
        """Set workload context; live_short is never valid for this source."""
        self._active_workload = workload

    def _assert_workload_allowed(self) -> None:
        if getattr(self, "_active_workload", None) == "live_short":
            raise DataError(
                "sqlite_db 不适合 live_short，禁止使用历史数据或 OHLCV cache"
            )

    def _annotate_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        frame.attrs["source_name"] = self.name
        workload = getattr(self, "_active_workload", None)
        if workload:
            frame.attrs["workload"] = workload
        return frame

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

    def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
        symbol_map = self._load_symbol_map()
        ts_to_symbol = {ts_code: symbol for symbol, ts_code in symbol_map.items()}
        row_limit = max(int(limit or 0), 0)
        min_amount_value = max(float(min_amount or 0.0), 0.0)

        def query_symbols(*, apply_amount_filter: bool) -> list[str]:
            amount_clause = (
                "AND COALESCE(amount, 0) >= ?" if apply_amount_filter else ""
            )
            params: list[object] = []
            if apply_amount_filter:
                params.append(min_amount_value)
            params.extend((_LIQUID_SYMBOL_MIN_HISTORY_ROWS, None))
            limit_clause = "LIMIT ?" if row_limit > 0 else ""
            if row_limit > 0:
                params.append(row_limit)
            with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
                latest = conn.execute(
                    "SELECT MAX(trade_date) FROM daily_qfq"
                ).fetchone()
                latest_day = str((latest or [""])[0] or "")
                if not latest_day:
                    return []
                latest_date = _parse_db_date(latest_day)
                min_first_date = (
                    latest_date - timedelta(days=_LIQUID_SYMBOL_LOOKBACK_CALENDAR_DAYS)
                    if latest_date is not None
                    else None
                )
                min_first_day = (
                    min_first_date.strftime("%Y%m%d")
                    if min_first_date is not None
                    else "00000000"
                )
                params[1 if apply_amount_filter else 0] = (
                    _LIQUID_SYMBOL_MIN_HISTORY_ROWS
                )
                params[2 if apply_amount_filter else 1] = min_first_day
                rows = conn.execute(
                    f"""
                    SELECT ts_code
                    FROM daily_qfq
                    WHERE trade_date = ?
                      {amount_clause}
                      AND ts_code IN (
                          SELECT ts_code
                          FROM daily_qfq
                          GROUP BY ts_code
                          HAVING COUNT(DISTINCT trade_date) >= ?
                             AND MIN(trade_date) <= ?
                      )
                    ORDER BY COALESCE(amount, 0) DESC, ts_code ASC
                    {limit_clause}
                    """,
                    (latest_day, *params),
                ).fetchall()
            return [
                ts_to_symbol[str(row[0])] for row in rows if str(row[0]) in ts_to_symbol
            ]

        filtered = query_symbols(apply_amount_filter=min_amount_value > 0)
        if filtered:
            return filtered
        return query_symbols(apply_amount_filter=False)

    def price_mode(self) -> str:
        columns: set[str] = set()
        samples: list[tuple[object, ...]] = []
        try:
            with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
                columns = {
                    str(row[1]).strip().lower()
                    for row in conn.execute("PRAGMA table_info(daily_qfq)").fetchall()
                }
                if {"open", "high", "low", "close"} <= columns:
                    samples = conn.execute(
                        """
                        SELECT open, high, low, close, open_qfq, high_qfq, low_qfq, close_qfq
                        FROM daily_qfq
                        WHERE close IS NOT NULL
                        LIMIT 50
                        """
                    ).fetchall()
        except sqlite3.Error:
            columns = set()
            samples = []

        if {"open", "high", "low", "close"} <= columns:
            for sample in samples:
                raw_values = sample[:4]
                qfq_values = sample[4:]
                if any(value is not None for value in raw_values):
                    if not any(value is not None for value in qfq_values):
                        return "raw"
                    if tuple(raw_values) != tuple(qfq_values):
                        return "raw"

        name = self.db_path.name.lower()
        if "raw" in name or "unadjust" in name:
            return "raw"
        if "qfq" in name:
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
        if not first_market_day or not last_market_day:
            return []
        if not _date_within_lag(start_str, first_market_day, max_days=10):
            return []
        if not _date_within_lag(last_market_day, end_str, max_days=10):
            return []

        symbol_map = self._load_symbol_map()
        ts_to_symbol = {ts_code: symbol for symbol, ts_code in symbol_map.items()}
        requested_ts_codes = [
            symbol_map[symbol] for symbol in symbols if symbol in symbol_map
        ]
        coverage_by_ts_code: dict[str, tuple[str, str, int]] = {}
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            for chunk in _chunks(requested_ts_codes, _SQLITE_BATCH_SIZE):
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""
                    SELECT ts_code, MIN(trade_date), MAX(trade_date), COUNT(*)
                    FROM daily_qfq
                    WHERE ts_code IN ({placeholders})
                      AND trade_date >= ? AND trade_date <= ?
                    GROUP BY ts_code
                    """,
                    (*chunk, start_str, end_str),
                ).fetchall()
                for ts_code, first_date, last_date, count in rows:
                    coverage_by_ts_code[str(ts_code)] = (
                        str(first_date or ""),
                        str(last_date or ""),
                        int(count or 0),
                    )

        covered: list[str] = []
        for ts_code in requested_ts_codes:
            row = coverage_by_ts_code.get(ts_code)
            if row is None:
                continue
            first_date, last_date, count = row
            if first_date > first_market_day or last_date < last_market_day:
                continue
            if count < min_required_rows:
                continue
            symbol = ts_to_symbol.get(ts_code)
            if symbol is not None:
                covered.append(symbol)
        self._last_coverage_snapshot = (
            start_str,
            end_str,
            frozenset(covered),
        )
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
        self._assert_workload_allowed()
        self._assert_price_mode_allowed(adjust)
        out: dict[str, OhlcvFrame] = {}
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")
        effective_symbols = list(symbols)
        prefiltered = str(os.getenv(_PREFILTERED_SYMBOLS_ENV, "")).strip().lower()
        skip_duplicate_coverage = adjust == "" and self._matches_last_coverage_snapshot(
            effective_symbols, start_str, end_str
        )
        if (
            adjust == ""
            and prefiltered not in {"1", "true", "yes", "on"}
            and not skip_duplicate_coverage
        ):
            try:
                covered_symbols = self.get_symbols_with_daily_coverage(
                    effective_symbols,
                    start,
                    end,
                    min_rows=None,
                )
            except Exception as exc:
                raise DataError(f"sqlite_db 覆盖检查失败: {exc}") from exc
            if covered_symbols:
                effective_symbols = covered_symbols
            elif effective_symbols:
                raise DataError(
                    f"sqlite_db 日线覆盖不足: requested={len(effective_symbols)} covered=0"
                )
        pending_symbols: list[str] = []
        cache_db_path = self._cache_db_path()
        cached_frames = self._get_cached_daily_frames(
            effective_symbols,
            start,
            end,
            price_mode=adjust or "raw",
        )
        for symbol in effective_symbols:
            cached = cached_frames.get(symbol)
            if cached is not None and not cached.empty:
                out[symbol] = cached
                continue
            if self._use_cache and cache_db_path is None:
                cached = self.cache.get_ohlcv(
                    symbol,
                    start,
                    end,
                    price_mode=adjust or "raw",
                    source=self.name,
                    workload=getattr(self, "_active_workload", None),
                )
                if cached is not None and not cached.empty:
                    if "name" not in cached.columns:
                        cached = cached.copy()
                        cached["name"] = self.get_symbol_name(symbol)
                    out[symbol] = cached
                    continue
            pending_symbols.append(symbol)

        symbol_map = self._load_symbol_map()
        ts_to_symbol = {ts_code: symbol for symbol, ts_code in symbol_map.items()}
        pending_ts_codes = [
            symbol_map[symbol] for symbol in pending_symbols if symbol in symbol_map
        ]
        frames_to_cache: dict[str, pd.DataFrame] = {}
        if pending_ts_codes:
            select_columns = (
                "trade_date, ts_code, open, high, low, close, volume, amount"
                if adjust == ""
                else "trade_date, ts_code, open_qfq as open, high_qfq as high, low_qfq as low, close_qfq as close, volume, amount"
            )
            with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
                for chunk in _chunks(pending_ts_codes, _SQLITE_BATCH_SIZE):
                    placeholders = ",".join("?" for _ in chunk)
                    df = pd.read_sql(
                        f"""
                        SELECT {select_columns}
                        FROM daily_qfq
                        WHERE ts_code IN ({placeholders})
                          AND trade_date >= ? AND trade_date <= ?
                        ORDER BY ts_code, trade_date
                        """,
                        conn,
                        params=(*chunk, start_str, end_str),
                    )
                    if df.empty:
                        continue
                    for ts_code, part in df.groupby("ts_code", sort=False):
                        symbol = ts_to_symbol.get(str(ts_code))
                        if symbol is None:
                            continue
                        frame = self._normalize_daily_frame(
                            part.drop(columns=["ts_code"]), symbol
                        )
                        if frame.empty:
                            continue
                        frames_to_cache[symbol] = frame
                        out[symbol] = frame
        self._set_cached_daily_frames(frames_to_cache, price_mode=adjust or "raw")

        require_non_empty_fetch_result(self.name, "日线", effective_symbols, out)

        return {symbol: self._annotate_frame(frame) for symbol, frame in out.items()}

    def _matches_last_coverage_snapshot(
        self,
        symbols: list[str],
        start_str: str,
        end_str: str,
    ) -> bool:
        snapshot = self._last_coverage_snapshot
        if snapshot is None:
            return False
        cached_start, cached_end, covered_symbols = snapshot
        if cached_start != start_str or cached_end != end_str:
            return False
        return all(symbol in covered_symbols for symbol in symbols)

    def _cache_db_path(self) -> Path | None:
        if not self._use_cache:
            return None
        db_path = getattr(self.cache, "db_path", None)
        return Path(db_path) if db_path else None

    def _get_cached_daily_frames(
        self,
        symbols: list[str],
        start: date,
        end: date,
        *,
        price_mode: str,
        max_age_hours: int = 24,
    ) -> dict[str, pd.DataFrame]:
        cache_db = self._cache_db_path()
        if cache_db is None or not symbols:
            return {}
        out = self.cache.get_ohlcv_many(
            symbols,
            start,
            end,
            max_age_hours=max_age_hours,
            price_mode=price_mode,
            source=self.name,
            workload=getattr(self, "_active_workload", None),
        )
        for symbol, frame in out.items():
            frame["name"] = self.get_symbol_name(symbol)
        return out

    def _set_cached_daily_frames(
        self,
        frames: dict[str, pd.DataFrame],
        *,
        price_mode: str,
    ) -> None:
        if not self._use_cache or not frames:
            return

        cache_db = self._cache_db_path()
        if cache_db is None:
            for symbol, frame in frames.items():
                self.cache.set_ohlcv(
                    symbol,
                    frame,
                    source="sqlite_db",
                    price_mode=price_mode,
                    workload=getattr(self, "_active_workload", None),
                )
            return
        self.cache.set_ohlcv_many(
            frames,
            source=self.name,
            workload=getattr(self, "_active_workload", None),
            price_mode=price_mode,
        )

    def _assert_price_mode_allowed(self, adjust: str) -> None:
        mode = self.price_mode()
        if adjust == "" and mode == "qfq":
            allowed = os.getenv(_ALLOW_QFQ_SQLITE_SOURCE_ENV, "").strip().lower()
            if allowed not in {"1", "true", "yes", "on"}:
                raise DataError(
                    "sqlite_db 当前指向 qfq 数据库；生产候选/ledger 必须使用 raw "
                    f"数据库，或显式设置 {_ALLOW_QFQ_SQLITE_SOURCE_ENV}=1 仅用于研究"
                )

    def _normalize_daily_frame(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if df.empty:
            return df
        frame = df.copy()
        frame["date"] = frame["trade_date"].apply(_format_db_date)
        frame = frame.drop(columns=["trade_date"])
        frame["symbol"] = symbol
        frame["name"] = self.get_symbol_name(symbol)

        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")

        frame = frame.dropna(subset=["close"])
        if "amount" in frame.columns:
            mask = frame["amount"].isna() | (frame["amount"] <= 0)
            if mask.any():
                avg_price = (frame["high"] + frame["low"] + frame["close"]) / 3
                frame.loc[mask, "amount"] = frame.loc[mask, "volume"] * avg_price
        if frame.empty:
            return frame

        return apply_limit_suspended_adj(
            frame, symbol, cache=self.cache if self._use_cache else None
        )

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
        self._assert_workload_allowed()
        out: dict[str, OhlcvFrame] = {}
        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            for code in index_codes:
                cached = (
                    self.cache.get_index(
                        code,
                        start,
                        end,
                        source=self.name,
                        workload=getattr(self, "_active_workload", None),
                    )
                    if self._use_cache
                    else None
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
                    self.cache.set_index(
                        code,
                        validated,
                        source=self.name,
                        workload=getattr(self, "_active_workload", None),
                    )
                out[code] = self._annotate_frame(validated)
        require_non_empty_fetch_result(self.name, "指数", index_codes, out)

        return out
