from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from aqsp.core.time import now_shanghai

_SQLITE_TIMEOUT_SECONDS = 30.0
_STALE_CACHE_RECENCY_DAYS = 7


def _has_implausible_amount_scale(df: pd.DataFrame) -> bool:
    """Detect obviously broken amount data (for example price-change mistaken as turnover).

    Valid normalized data in this project usually has:
    - amount ~= close * volume
    - or amount ~= close * volume * 100 (volume reported in lots)
    - or, for some sources, a smaller but still non-trivial scale.

    The old eastmoney bug wrote `涨跌额` into `amount`, making the ratio collapse
    to ~1e-7. Use a very small threshold so we only reject clearly impossible rows
    and avoid harming legitimate alternative unit conventions.
    """
    required = {"amount", "volume", "close"}
    if not required.issubset(df.columns):
        return False
    amount = pd.to_numeric(df["amount"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    valid = (amount > 0) & (volume > 0) & (close > 0)
    if not valid.any():
        return False
    baseline = volume[valid] * close[valid]
    ratio = (amount[valid] / baseline).replace([float("inf"), float("-inf")], pd.NA)
    ratio = ratio.dropna()
    if ratio.empty:
        return False
    return float(ratio.median()) < 1e-5


def _normalize_price_mode(price_mode: str) -> str:
    mode = str(price_mode or "raw").strip().lower()
    return mode if mode in {"raw", "qfq", "hfq"} else "raw"


def _requires_freshness_window(end: date) -> bool:
    return (now_shanghai().date() - end).days <= _STALE_CACHE_RECENCY_DAYS


class DataCache:
    def __init__(self, db_path: str | Path = "data/cache.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            self._migrate_ohlcv_price_mode(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ohlcv (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    price_mode TEXT NOT NULL DEFAULT 'raw',
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    suspended INTEGER DEFAULT 0,
                    limit_up REAL DEFAULT 0.0,
                    limit_down REAL DEFAULT 0.0,
                    adj_factor REAL DEFAULT 1.0,
                    source TEXT,
                    fetched_at TEXT,
                    PRIMARY KEY (symbol, date, price_mode)
                )
                """
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(ohlcv)").fetchall()
            }
            if "price_mode" not in columns:
                conn.execute(
                    "ALTER TABLE ohlcv ADD COLUMN price_mode TEXT NOT NULL DEFAULT 'raw'"
                )
            indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(ohlcv)").fetchall()
            }
            if "idx_ohlcv_symbol_date" not in indexes:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_date "
                    "ON ohlcv(symbol, date)"
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ohlcv_symbol_date_price_mode "
                "ON ohlcv(symbol, date, price_mode)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS index_ohlcv (
                    code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    source TEXT,
                    fetched_at TEXT,
                    PRIMARY KEY (code, date)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS adj_factors (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    adj_factor REAL NOT NULL,
                    source TEXT,
                    fetched_at TEXT,
                    PRIMARY KEY (symbol, date)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS financials (
                    symbol TEXT NOT NULL,
                    pubDate TEXT NOT NULL,
                    statDate TEXT,
                    roeAvg REAL,
                    npMargin REAL,
                    gpMargin REAL,
                    epsTTM REAL,
                    totalShare REAL,
                    source TEXT,
                    fetched_at TEXT,
                    PRIMARY KEY (symbol, pubDate)
                )
                """
            )
            conn.commit()

    def _migrate_ohlcv_price_mode(self, conn: sqlite3.Connection) -> None:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ohlcv'"
        ).fetchone()
        if table is None:
            return
        info = conn.execute("PRAGMA table_info(ohlcv)").fetchall()
        columns = {str(row[1]) for row in info}
        pk_columns = [
            str(row[1]) for row in sorted(info, key=lambda row: int(row[5])) if row[5]
        ]
        if "price_mode" in columns and pk_columns == ["symbol", "date", "price_mode"]:
            return
        if "price_mode" not in columns:
            conn.execute(
                "ALTER TABLE ohlcv ADD COLUMN price_mode TEXT NOT NULL DEFAULT 'raw'"
            )
        conn.execute("ALTER TABLE ohlcv RENAME TO ohlcv_legacy")
        conn.execute(
            """
            CREATE TABLE ohlcv (
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                price_mode TEXT NOT NULL DEFAULT 'raw',
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                suspended INTEGER DEFAULT 0,
                limit_up REAL DEFAULT 0.0,
                limit_down REAL DEFAULT 0.0,
                adj_factor REAL DEFAULT 1.0,
                source TEXT,
                fetched_at TEXT,
                PRIMARY KEY (symbol, date, price_mode)
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO ohlcv (
                symbol, date, price_mode, open, high, low, close, volume, amount,
                suspended, limit_up, limit_down, adj_factor, source, fetched_at
            )
            SELECT
                symbol, date, COALESCE(NULLIF(price_mode, ''), 'raw'),
                open, high, low, close, volume, amount,
                suspended, limit_up, limit_down, adj_factor, source, fetched_at
            FROM ohlcv_legacy
            """
        )
        conn.execute("DROP TABLE ohlcv_legacy")

    @staticmethod
    def _records_for_insert(
        df: pd.DataFrame,
        columns: list[str],
        defaults: dict[str, object],
    ) -> list[tuple[object, ...]]:
        prepared = df.copy()
        for column, default in defaults.items():
            if column not in prepared.columns:
                prepared[column] = default
        return list(prepared[columns].itertuples(index=False, name=None))

    def get_ohlcv(
        self,
        symbol: str,
        start: date,
        end: date,
        max_age_hours: int = 24,
        price_mode: str = "raw",
    ) -> Optional[pd.DataFrame]:
        normalized_price_mode = _normalize_price_mode(price_mode)
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            df = pd.read_sql(
                """
                SELECT * FROM ohlcv
                WHERE symbol = ? AND price_mode = ? AND date >= ? AND date <= ?
                """,
                conn,
                params=(
                    symbol,
                    normalized_price_mode,
                    start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"),
                ),
            )
        if df.empty:
            return None

        if _requires_freshness_window(end):
            cutoff = (now_shanghai() - pd.Timedelta(hours=max_age_hours)).isoformat()
            stale = df[df["fetched_at"] < cutoff]
            if not stale.empty:
                return None

        # 缓存完整性校验：仅当缓存覆盖了请求区间的两端才算命中。
        # 否则（例如上次只缓存了区间的一半）会把残缺数据当完整返回，
        # 导致回测/选股用缺段数据且无感知。
        # 留 7 天容差吸收节假日/停牌（如春节长假），避免对边界非交易日误判。
        TOLERANCE_DAYS = 7
        cached_min = pd.to_datetime(df["date"].min())
        cached_max = pd.to_datetime(df["date"].max())
        req_start = pd.Timestamp(start)
        req_end = pd.Timestamp(end)
        if cached_min > req_start + pd.Timedelta(days=TOLERANCE_DAYS):
            return None
        if cached_max < req_end - pd.Timedelta(days=TOLERANCE_DAYS):
            return None
        if _has_implausible_amount_scale(df):
            return None

        df = df.sort_values("date").reset_index(drop=True)
        if "symbol" not in df.columns:
            df["symbol"] = symbol
        if "name" not in df.columns:
            df["name"] = symbol
        if "suspended" in df.columns:
            df["suspended"] = df["suspended"].fillna(0).astype(bool)
        if "limit_up" in df.columns:
            df["limit_up"] = df["limit_up"].fillna(0.0)
        if "limit_down" in df.columns:
            df["limit_down"] = df["limit_down"].fillna(0.0)
        return df

    def set_ohlcv(
        self,
        symbol: str,
        df: pd.DataFrame,
        source: str = "unknown",
        price_mode: str = "raw",
    ) -> None:
        normalized_price_mode = _normalize_price_mode(price_mode)
        df = df.copy()
        df["symbol"] = symbol
        df["price_mode"] = normalized_price_mode
        df["source"] = source
        df["fetched_at"] = now_shanghai().isoformat()
        columns = [
            "symbol",
            "date",
            "price_mode",
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
            "source",
            "fetched_at",
        ]
        rows = self._records_for_insert(
            df,
            columns,
            {
                "suspended": 0,
                "limit_up": 0.0,
                "limit_down": 0.0,
                "adj_factor": 1.0,
            },
        )

        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO ohlcv (
                    symbol, date, price_mode, open, high, low, close, volume, amount,
                    suspended, limit_up, limit_down, adj_factor, source, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def get_index(
        self,
        code: str,
        start: date,
        end: date,
        max_age_hours: int = 24,
    ) -> Optional[pd.DataFrame]:
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            df = pd.read_sql(
                """
                SELECT * FROM index_ohlcv
                WHERE code = ? AND date >= ? AND date <= ?
                """,
                conn,
                params=(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
            )
        if df.empty:
            return None

        if _requires_freshness_window(end):
            cutoff = (now_shanghai() - pd.Timedelta(hours=max_age_hours)).isoformat()
            stale = df[df["fetched_at"] < cutoff]
            if not stale.empty:
                return None

        # 缓存完整性校验（同 get_ohlcv）：缓存须覆盖请求区间两端，
        # 否则残缺指数数据会被当完整返回。7 天容差吸收节假日/停牌。
        TOLERANCE_DAYS = 7
        cached_min = pd.to_datetime(df["date"].min())
        cached_max = pd.to_datetime(df["date"].max())
        if cached_min > pd.Timestamp(start) + pd.Timedelta(days=TOLERANCE_DAYS):
            return None
        if cached_max < pd.Timestamp(end) - pd.Timedelta(days=TOLERANCE_DAYS):
            return None
        if _has_implausible_amount_scale(df):
            return None

        df = df.sort_values("date").reset_index(drop=True)
        df["symbol"] = code
        df["name"] = code
        df["suspended"] = False
        df["limit_up"] = 0.0
        df["limit_down"] = 0.0
        return df

    def set_index(self, code: str, df: pd.DataFrame, source: str = "unknown") -> None:
        df = df.copy()
        df["code"] = code
        df["source"] = source
        df["fetched_at"] = now_shanghai().isoformat()
        columns = [
            "code",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "source",
            "fetched_at",
        ]
        rows = self._records_for_insert(df, columns, {})

        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO index_ohlcv (
                    code, date, open, high, low, close, volume, amount, source, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def get_adj_factor(self, symbol: str, date: date) -> Optional[float]:
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            cursor = conn.execute(
                """
                SELECT adj_factor FROM adj_factors
                WHERE symbol = ? AND date <= ?
                ORDER BY date DESC LIMIT 1
                """,
                (symbol, date.strftime("%Y-%m-%d")),
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def set_adj_factors(
        self, symbol: str, df: pd.DataFrame, source: str = "unknown"
    ) -> None:
        df = df.copy()
        df["symbol"] = symbol
        df["source"] = source
        df["fetched_at"] = now_shanghai().isoformat()
        columns = ["symbol", "date", "adj_factor", "source", "fetched_at"]
        rows = self._records_for_insert(df, columns, {})

        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO adj_factors (
                    symbol, date, adj_factor, source, fetched_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def get_financial(
        self,
        symbol: str,
        max_age_hours: int = 168,
    ) -> Optional[pd.DataFrame]:
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            df = pd.read_sql(
                "SELECT * FROM financials WHERE symbol = ?",
                conn,
                params=(symbol,),
            )
        if df.empty:
            return None
        cutoff = (now_shanghai() - pd.Timedelta(hours=max_age_hours)).isoformat()
        if df["fetched_at"].max() < cutoff:
            return None
        return df

    def set_financial(
        self, symbol: str, df: pd.DataFrame, source: str = "unknown"
    ) -> None:
        df = df.copy()
        df["symbol"] = symbol
        df["source"] = source
        df["fetched_at"] = now_shanghai().isoformat()
        if "pubDate" in df.columns:
            df["pubDate"] = df["pubDate"].map(
                lambda value: str(value) if value is not None else ""
            )
        else:
            df["pubDate"] = ""
        if "statDate" in df.columns:
            df["statDate"] = df["statDate"].map(
                lambda value: str(value) if value is not None else ""
            )
        else:
            df["statDate"] = ""
        columns = [
            "symbol",
            "pubDate",
            "statDate",
            "roeAvg",
            "npMargin",
            "gpMargin",
            "epsTTM",
            "totalShare",
            "source",
            "fetched_at",
        ]
        rows = self._records_for_insert(df, columns, {})
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO financials (
                    symbol, pubDate, statDate, roeAvg, npMargin, gpMargin,
                    epsTTM, totalShare, source, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def clear_expired(self, max_age_hours: int = 168) -> int:
        cutoff = (now_shanghai() - pd.Timedelta(hours=max_age_hours)).isoformat()
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            deleted = conn.execute(
                "DELETE FROM ohlcv WHERE fetched_at < ?", (cutoff,)
            ).rowcount
            deleted += conn.execute(
                "DELETE FROM index_ohlcv WHERE fetched_at < ?", (cutoff,)
            ).rowcount
            deleted += conn.execute(
                "DELETE FROM adj_factors WHERE fetched_at < ?", (cutoff,)
            ).rowcount
            conn.commit()
        return deleted
