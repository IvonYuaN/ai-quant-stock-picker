from __future__ import annotations

import os
import sqlite3
import struct
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

# TDX .day schema: date, open, high, low, close, amount, volume, reserved.
TDX_DAY_RECORD = struct.Struct("<IIIIIfII")
TDX_DAY_RECORD_SIZE = TDX_DAY_RECORD.size
TDX_DAY_COLUMNS = [
    "date",
    "symbol",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "suspended",
    "limit_up",
    "limit_down",
    "adj_factor",
]


def _default_vipdoc_path() -> Path:
    return Path(os.getenv("AQSP_TDX_VIPDOC_PATH", "private_data/tdx"))


def _normal_stock_prefix(symbol: str) -> str:
    if symbol.startswith(("6", "5", "9")):
        return "sh"
    if symbol.startswith(("4", "8")):
        return "bj"
    return "sz"


def _canonical_stock_market(symbol: str) -> str | None:
    if symbol.startswith(("600", "601", "603", "605", "688")):
        return "sh"
    if symbol.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz"
    if symbol.startswith(("4", "8")):
        return "bj"
    return None


class TdxVipdocSource(DataSource):
    name: str = "tdx_vipdoc"

    def __init__(self, vipdoc_path: str | Path | None = None) -> None:
        raw_path = (
            Path(vipdoc_path) if vipdoc_path is not None else _default_vipdoc_path()
        )
        self.vipdoc_path = self._resolve_vipdoc_path(raw_path)
        if not self.vipdoc_path.exists():
            raise DataError(
                f"通达信 vipdoc 目录不存在: {self.vipdoc_path}. "
                "先运行 scripts/download_tdx_vipdoc.py，或设置 AQSP_TDX_VIPDOC_PATH。"
            )
        self._symbol_paths = self._build_symbol_path_index()
        self._symbol_names = self._load_symbol_names()

    @staticmethod
    def _resolve_vipdoc_path(path: Path) -> Path:
        if (path / "sh").exists() or (path / "sz").exists() or (path / "bj").exists():
            return path
        nested = path / "vipdoc"
        if nested.exists():
            return nested
        return path

    def get_available_symbols(self) -> list[str]:
        return list(self._symbol_paths)

    def get_liquid_symbols(
        self,
        *,
        limit: int,
        min_amount: float,
    ) -> list[str]:
        latest_market_date = self._latest_market_date()
        if latest_market_date is None:
            return []

        ranked: list[tuple[float, str]] = []
        for symbol, path in self._symbol_paths.items():
            if not _is_common_a_share(symbol):
                continue
            latest = self._read_latest_day_record(path)
            if latest is None:
                continue
            trade_day, amount, volume = latest
            if trade_day != latest_market_date:
                continue
            if volume <= 0 or amount < min_amount:
                continue
            ranked.append((amount, symbol))
        ranked.sort(reverse=True)
        selected = [symbol for _amount, symbol in ranked]
        return selected[:limit] if limit > 0 else selected

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",
    ) -> dict[str, OhlcvFrame]:
        if adjust:
            raise DataError("tdx_vipdoc 只提供不复权原始日线，adjust 必须为空字符串")

        out: dict[str, OhlcvFrame] = {}
        for symbol in symbols:
            path = self._symbol_paths.get(symbol)
            if path is None:
                path = self._day_file(symbol, _normal_stock_prefix(symbol))
            if not path.exists():
                continue
            df = self._read_day_file(path, symbol, start, end)
            if df.empty:
                continue
            validated = self._validate_ohlcv(df, symbol)
            out[symbol] = validated
        require_non_empty_fetch_result(self.name, "日线", symbols, out)
        return out

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]:
        raise DataError("tdx_vipdoc 不支持分时数据")

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        raise DataError("tdx_vipdoc 不支持实时行情")

    def fetch_index(
        self,
        index_codes: list[str],
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]:
        out: dict[str, OhlcvFrame] = {}
        for code in index_codes:
            path = next(
                (
                    self._day_file(code, market)
                    for market in ("sh", "sz", "bj")
                    if self._day_file(code, market).exists()
                ),
                None,
            )
            if path is None:
                continue
            df = self._read_day_file(path, code, start, end)
            if not df.empty:
                out[code] = self._validate_ohlcv(df, code)
        require_non_empty_fetch_result(self.name, "指数", index_codes, out)
        return out

    def _day_file(self, symbol: str, market: str) -> Path:
        return self.vipdoc_path / market / "lday" / f"{market}{symbol}.day"

    def _build_symbol_path_index(self) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        for market in ("sh", "sz", "bj"):
            for path in sorted(
                (self.vipdoc_path / market / "lday").glob(f"{market}*.day")
            ):
                symbol = path.stem[2:]
                if _canonical_stock_market(symbol) != market:
                    continue
                paths.setdefault(symbol, path)
        return paths

    def _load_symbol_names(self) -> dict[str, str]:
        db_path = _stock_name_db_path()
        if db_path is None:
            return {}
        try:
            with sqlite3.connect(db_path, timeout=30.0) as conn:
                rows = conn.execute("SELECT ts_code, name FROM stocks").fetchall()
        except sqlite3.Error:
            return {}

        names: dict[str, str] = {}
        for ts_code, raw_name in rows:
            code = str(ts_code or "").split(".", maxsplit=1)[0].strip()
            name = _clean_stock_name(raw_name)
            if code and name:
                names[code] = name
        return names

    def _latest_market_date(self) -> date | None:
        latest: date | None = None
        for path in self._symbol_paths.values():
            record = self._read_latest_day_record(path)
            if record is None:
                continue
            trade_day, _amount, _volume = record
            if latest is None or trade_day > latest:
                latest = trade_day
        return latest

    def _read_latest_day_record(self, path: Path) -> tuple[date, float, int] | None:
        size = path.stat().st_size
        if size < TDX_DAY_RECORD_SIZE or size % TDX_DAY_RECORD_SIZE != 0:
            return None
        with path.open("rb") as fh:
            fh.seek(size - TDX_DAY_RECORD_SIZE)
            record = fh.read(TDX_DAY_RECORD_SIZE)
        trade_date, _open, _high, _low, _close, amount, volume, _reserved = (
            TDX_DAY_RECORD.unpack(record)
        )
        trade_day = date(
            trade_date // 10000,
            trade_date % 10000 // 100,
            trade_date % 100,
        )
        return trade_day, float(amount), int(volume)

    def _read_day_file(
        self,
        path: Path,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        raw = path.read_bytes()
        if len(raw) % TDX_DAY_RECORD_SIZE != 0:
            raise DataError(f"通达信 .day 文件长度异常: {path}")

        rows: list[dict[str, object]] = []
        for offset in range(0, len(raw), TDX_DAY_RECORD_SIZE):
            trade_date, open_, high, low, close, amount, volume, _reserved = (
                TDX_DAY_RECORD.unpack_from(raw, offset)
            )
            trade_day = date(
                trade_date // 10000,
                trade_date % 10000 // 100,
                trade_date % 100,
            )
            if trade_day < start or trade_day > end:
                continue
            rows.append(
                {
                    "date": trade_day.isoformat(),
                    "symbol": symbol,
                    "name": self._symbol_names.get(symbol, symbol),
                    "open": open_ / 100.0,
                    "high": high / 100.0,
                    "low": low / 100.0,
                    "close": close / 100.0,
                    "volume": float(volume),
                    "amount": float(amount),
                }
            )

        if not rows:
            return pd.DataFrame(columns=TDX_DAY_COLUMNS)

        df = pd.DataFrame(rows)
        df = apply_limit_suspended_adj(df, symbol, cache=None)
        return df[TDX_DAY_COLUMNS].reset_index(drop=True)


def _is_common_a_share(symbol: str) -> bool:
    return symbol.startswith(
        ("600", "601", "603", "605", "688", "000", "001", "002", "003", "300", "301")
    )


def _stock_name_db_path() -> Path | None:
    raw = (
        os.getenv("AQSP_STOCK_NAME_DB_PATH")
        or os.getenv("AQSP_SQLITE_DB_PATH")
        or "A股量化分析数据/astocks_qfq.db"
    )
    path = Path(raw)
    return path if path.exists() else None


def _clean_stock_name(value: object) -> str:
    return str(value or "").replace("\x00", "").replace("\\x00", "").strip()
