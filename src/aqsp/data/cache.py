from __future__ import annotations

import sqlite3
from collections.abc import Collection
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from aqsp.core.time import now_shanghai

_SQLITE_TIMEOUT_SECONDS = 30.0
_STALE_CACHE_RECENCY_DAYS = 7
_DEFAULT_CACHE_WORKLOAD = "historical"


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


def _normalize_workload(workload: str | None) -> str:
    value = str(workload or "").strip()
    return value or _DEFAULT_CACHE_WORKLOAD


def _has_cache_provenance(
    df: pd.DataFrame,
    *,
    source: str | None,
    workload: str | None,
) -> bool:
    required = {"source", "workload", "fetched_at", "timestamp_source"}
    if not required.issubset(df.columns) or df.empty:
        return False
    if df[list(required)].isna().any().any():
        return False
    if any(
        not str(value).strip()
        for column in required - {"fetched_at"}
        for value in df[column].tolist()
    ):
        return False
    for value in df["fetched_at"].tolist():
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return False
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return False
        if parsed > now_shanghai():
            return False
    if source is not None and not (df["source"].astype(str) == source).all():
        return False
    # Omitting workload means the ordinary historical cache, not a wildcard.
    # This prevents live_short or walkforward rows from entering production.
    expected_workload = _normalize_workload(workload)
    if not (df["workload"].astype(str).str.strip() == expected_workload).all():
        return False
    return True


def _has_stale_fetch_timestamp(df: pd.DataFrame, max_age_hours: int) -> bool:
    """Return whether any fetch timestamp is missing, invalid, or too old."""
    if df.empty or "fetched_at" not in df.columns:
        return True
    fetched_at = pd.to_datetime(df["fetched_at"], errors="coerce", utc=True)
    if fetched_at.isna().any():
        return True
    cutoff = pd.Timestamp(now_shanghai()).tz_convert("UTC") - pd.Timedelta(
        hours=max_age_hours
    )
    return bool((fetched_at < cutoff).any())


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
                    workload TEXT DEFAULT 'historical',
                    timestamp_source TEXT,
                    PRIMARY KEY (symbol, date, price_mode, workload)
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
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(ohlcv)").fetchall()
            }
            for column in ("workload", "timestamp_source"):
                if column not in columns:
                    conn.execute(f"ALTER TABLE ohlcv ADD COLUMN {column} TEXT")
            indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(ohlcv)").fetchall()
            }
            if "idx_ohlcv_symbol_date" not in indexes:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_date "
                    "ON ohlcv(symbol, date)"
                )
            conn.execute("DROP INDEX IF EXISTS idx_ohlcv_symbol_date_price_mode")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ohlcv_symbol_date_price_mode_workload "
                "ON ohlcv(symbol, date, price_mode, workload)"
            )
            self._migrate_index_workload(conn)
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
                    workload TEXT DEFAULT 'historical',
                    timestamp_source TEXT,
                    PRIMARY KEY (code, date, workload)
                )
                """
            )
            index_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(index_ohlcv)").fetchall()
            }
            for column in ("workload", "timestamp_source"):
                if column not in index_columns:
                    conn.execute(f"ALTER TABLE index_ohlcv ADD COLUMN {column} TEXT")
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
        if "price_mode" not in columns:
            conn.execute(
                "ALTER TABLE ohlcv ADD COLUMN price_mode TEXT NOT NULL DEFAULT 'raw'"
            )
            columns.add("price_mode")
        for column in ("workload", "timestamp_source"):
            if column not in columns:
                conn.execute(f"ALTER TABLE ohlcv ADD COLUMN {column} TEXT")
                columns.add(column)
        if pk_columns == ["symbol", "date", "price_mode", "workload"]:
            return
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
                    workload TEXT DEFAULT 'historical',
                    timestamp_source TEXT,
                    PRIMARY KEY (symbol, date, price_mode, workload)
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO ohlcv (
                symbol, date, price_mode, open, high, low, close, volume, amount,
                suspended, limit_up, limit_down, adj_factor, source, fetched_at
                , workload, timestamp_source
            )
            SELECT
                symbol, date, COALESCE(NULLIF(price_mode, ''), 'raw'),
                open, high, low, close, volume, amount,
                suspended, limit_up, limit_down, adj_factor, source, fetched_at,
                COALESCE(NULLIF(workload, ''), 'historical'), timestamp_source
            FROM ohlcv_legacy
            """
        )
        conn.execute("DROP TABLE ohlcv_legacy")

    def _migrate_index_workload(self, conn: sqlite3.Connection) -> None:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='index_ohlcv'"
        ).fetchone()
        if table is None:
            return
        info = conn.execute("PRAGMA table_info(index_ohlcv)").fetchall()
        columns = {str(row[1]) for row in info}
        pk_columns = [
            str(row[1]) for row in sorted(info, key=lambda row: int(row[5])) if row[5]
        ]
        if "workload" not in columns:
            conn.execute("ALTER TABLE index_ohlcv ADD COLUMN workload TEXT")
            columns.add("workload")
        if "timestamp_source" not in columns:
            conn.execute("ALTER TABLE index_ohlcv ADD COLUMN timestamp_source TEXT")
            columns.add("timestamp_source")
        if pk_columns == ["code", "date", "workload"]:
            conn.execute(
                "UPDATE index_ohlcv SET workload = 'historical' "
                "WHERE workload IS NULL OR TRIM(workload) = ''"
            )
            return
        conn.execute("ALTER TABLE index_ohlcv RENAME TO index_ohlcv_legacy")
        conn.execute(
            """
            CREATE TABLE index_ohlcv (
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
                workload TEXT DEFAULT 'historical',
                timestamp_source TEXT,
                PRIMARY KEY (code, date, workload)
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO index_ohlcv (
                code, date, open, high, low, close, volume, amount, source,
                fetched_at, workload, timestamp_source
            )
            SELECT
                code, date, open, high, low, close, volume, amount, source,
                fetched_at, COALESCE(NULLIF(workload, ''), 'historical'),
                timestamp_source
            FROM index_ohlcv_legacy
            """
        )
        conn.execute("DROP TABLE index_ohlcv_legacy")

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
        source: str | None = None,
        workload: str | None = None,
    ) -> Optional[pd.DataFrame]:
        normalized_price_mode = _normalize_price_mode(price_mode)
        normalized_workload = _normalize_workload(workload)
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            df = pd.read_sql(
                """
                SELECT * FROM ohlcv
                WHERE symbol = ? AND price_mode = ? AND workload = ?
                  AND date >= ? AND date <= ?
                """,
                conn,
                params=(
                    symbol,
                    normalized_price_mode,
                    normalized_workload,
                    start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"),
                ),
            )
        if df.empty:
            return None

        if not _has_cache_provenance(df, source=source, workload=workload):
            return None

        if _requires_freshness_window(end):
            if _has_stale_fetch_timestamp(df, max_age_hours):
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
        df.attrs["source_name"] = str(df["source"].iloc[0])
        df.attrs["workload"] = str(df["workload"].iloc[0])
        df.attrs["fetched_at"] = str(df["fetched_at"].max())
        return df

    def get_ohlcv_many(
        self,
        symbols: list[str],
        start: date,
        end: date,
        *,
        max_age_hours: int = 24,
        price_mode: str = "raw",
        source: str | None = None,
        workload: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Read complete, provenance-validated OHLCV cache rows in one query."""
        if not symbols:
            return {}
        normalized_price_mode = _normalize_price_mode(price_mode)
        normalized_workload = _normalize_workload(workload)
        placeholders = ",".join("?" for _ in symbols)
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            df = pd.read_sql(
                f"""
                SELECT * FROM ohlcv
                WHERE symbol IN ({placeholders}) AND price_mode = ?
                  AND workload = ? AND date >= ? AND date <= ?
                ORDER BY symbol, date
                """,
                conn,
                params=(
                    *symbols,
                    normalized_price_mode,
                    normalized_workload,
                    start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"),
                ),
            )
        result: dict[str, pd.DataFrame] = {}
        for symbol, frame in df.groupby("symbol", sort=False):
            cached = self._validate_cached_ohlcv_frame(
                frame.copy(),
                symbol=str(symbol),
                start=start,
                end=end,
                max_age_hours=max_age_hours,
                source=source,
                workload=workload,
            )
            if cached is not None:
                result[str(symbol)] = cached
        return result

    def _validate_cached_ohlcv_frame(
        self,
        df: pd.DataFrame,
        *,
        symbol: str,
        start: date,
        end: date,
        max_age_hours: int,
        source: str | None,
        workload: str | None,
    ) -> Optional[pd.DataFrame]:
        if df.empty or not _has_cache_provenance(df, source=source, workload=workload):
            return None
        if _requires_freshness_window(end):
            if _has_stale_fetch_timestamp(df, max_age_hours):
                return None
        cached_min = pd.to_datetime(df["date"].min())
        cached_max = pd.to_datetime(df["date"].max())
        if cached_min > pd.Timestamp(start) + pd.Timedelta(days=7):
            return None
        if cached_max < pd.Timestamp(end) - pd.Timedelta(days=7):
            return None
        if _has_implausible_amount_scale(df):
            return None
        df = df.sort_values("date").reset_index(drop=True)
        df["symbol"] = symbol
        if "name" not in df.columns:
            df["name"] = symbol
        if "suspended" in df.columns:
            df["suspended"] = df["suspended"].fillna(0).astype(bool)
        if "limit_up" in df.columns:
            df["limit_up"] = df["limit_up"].fillna(0.0)
        if "limit_down" in df.columns:
            df["limit_down"] = df["limit_down"].fillna(0.0)
        df.attrs["source_name"] = str(df["source"].iloc[0])
        df.attrs["workload"] = str(df["workload"].iloc[0])
        df.attrs["fetched_at"] = str(df["fetched_at"].max())
        return df

    def set_ohlcv(
        self,
        symbol: str,
        df: pd.DataFrame,
        source: str = "unknown",
        price_mode: str = "raw",
        workload: str | None = None,
        timestamp_source: str = "received_at",
    ) -> None:
        normalized_price_mode = _normalize_price_mode(price_mode)
        df = df.copy()
        df["symbol"] = symbol
        df["price_mode"] = normalized_price_mode
        df["source"] = source
        df["fetched_at"] = now_shanghai().isoformat()
        df["workload"] = _normalize_workload(workload)
        df["timestamp_source"] = timestamp_source
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
            "workload",
            "timestamp_source",
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
                    suspended, limit_up, limit_down, adj_factor, source, fetched_at,
                    workload, timestamp_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def set_ohlcv_many(
        self,
        frames: dict[str, pd.DataFrame],
        *,
        source: str,
        workload: str | None = None,
        price_mode: str = "raw",
        timestamp_source: str = "received_at",
    ) -> None:
        """Persist multiple OHLCV frames with one shared provenance timestamp."""
        if not frames:
            return
        normalized_price_mode = _normalize_price_mode(price_mode)
        normalized_workload = _normalize_workload(workload)
        fetched_at = now_shanghai().isoformat()
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
            "workload",
            "timestamp_source",
        ]
        rows: list[tuple[object, ...]] = []
        for symbol, frame in frames.items():
            prepared = frame.copy()
            prepared["symbol"] = symbol
            prepared["price_mode"] = normalized_price_mode
            prepared["source"] = source
            prepared["fetched_at"] = fetched_at
            prepared["workload"] = normalized_workload
            prepared["timestamp_source"] = timestamp_source
            rows.extend(
                self._records_for_insert(
                    prepared,
                    columns,
                    {
                        "suspended": 0,
                        "limit_up": 0.0,
                        "limit_down": 0.0,
                        "adj_factor": 1.0,
                    },
                )
            )
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO ohlcv (
                    symbol, date, price_mode, open, high, low, close, volume, amount,
                    suspended, limit_up, limit_down, adj_factor, source, fetched_at,
                    workload, timestamp_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        source: str | None = None,
        workload: str | None = None,
    ) -> Optional[pd.DataFrame]:
        normalized_workload = _normalize_workload(workload)
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            df = pd.read_sql(
                """
                SELECT * FROM index_ohlcv
                WHERE code = ? AND workload = ? AND date >= ? AND date <= ?
                """,
                conn,
                params=(
                    code,
                    normalized_workload,
                    start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"),
                ),
            )
        if df.empty:
            return None

        if not _has_cache_provenance(df, source=source, workload=workload):
            return None

        if _requires_freshness_window(end):
            if _has_stale_fetch_timestamp(df, max_age_hours):
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
        df.attrs["source_name"] = str(df["source"].iloc[0])
        df.attrs["workload"] = str(df["workload"].iloc[0])
        df.attrs["fetched_at"] = str(df["fetched_at"].max())
        return df

    def set_index(
        self,
        code: str,
        df: pd.DataFrame,
        source: str = "unknown",
        workload: str | None = None,
        timestamp_source: str = "received_at",
    ) -> None:
        df = df.copy()
        df["code"] = code
        df["source"] = source
        df["fetched_at"] = now_shanghai().isoformat()
        df["workload"] = _normalize_workload(workload)
        df["timestamp_source"] = timestamp_source
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
            "workload",
            "timestamp_source",
        ]
        rows = self._records_for_insert(df, columns, {})

        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO index_ohlcv (
                    code, date, open, high, low, close, volume, amount, source, fetched_at,
                    workload, timestamp_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def get_adj_factors(
        self,
        symbol: str,
        dates: list[date],
    ) -> dict[date, float]:
        normalized_dates = sorted({item for item in dates if isinstance(item, date)})
        if not normalized_dates:
            return {}
        start = normalized_dates[0].strftime("%Y-%m-%d")
        end = normalized_dates[-1].strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            rows = conn.execute(
                """
                SELECT date, adj_factor FROM adj_factors
                WHERE symbol = ? AND date >= ? AND date <= ?
                ORDER BY date
                """,
                (symbol, start, end),
            ).fetchall()

        factors_by_day: dict[date, float] = {}
        for raw_day, raw_factor in rows:
            try:
                day = date.fromisoformat(str(raw_day)[:10])
                factor = float(raw_factor)
            except (TypeError, ValueError):
                continue
            factors_by_day[day] = factor

        resolved: dict[date, float] = {}
        latest_factor = 1.0
        for day in normalized_dates:
            if day in factors_by_day:
                latest_factor = factors_by_day[day]
            resolved[day] = latest_factor
        return resolved

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

    def clear_expired(
        self,
        max_age_hours: int = 168,
        *,
        workloads: Collection[str] | None = None,
    ) -> int:
        """Remove transient rows while allowing production to preserve history.

        The legacy call with ``workloads=None`` keeps broad maintenance
        semantics. Production daily runs pass ``workloads=("live_short",)``
        so historical and walk-forward rows are not deleted by routine cleanup.
        """
        cutoff = (now_shanghai() - pd.Timedelta(hours=max_age_hours)).isoformat()
        with sqlite3.connect(self.db_path, timeout=_SQLITE_TIMEOUT_SECONDS) as conn:
            if workloads is None:
                deleted = conn.execute(
                    "DELETE FROM ohlcv WHERE fetched_at < ?", (cutoff,)
                ).rowcount
                deleted += conn.execute(
                    "DELETE FROM index_ohlcv WHERE fetched_at < ?", (cutoff,)
                ).rowcount
                deleted += conn.execute(
                    "DELETE FROM adj_factors WHERE fetched_at < ?", (cutoff,)
                ).rowcount
            else:
                normalized_workloads = tuple(
                    sorted(
                        {
                            _normalize_workload(workload)
                            for workload in workloads
                            if str(workload or "").strip()
                        }
                    )
                )
                if not normalized_workloads:
                    return 0
                placeholders = ",".join("?" for _ in normalized_workloads)
                params = (cutoff, *normalized_workloads)
                deleted = conn.execute(
                    "DELETE FROM ohlcv "
                    f"WHERE fetched_at < ? AND workload IN ({placeholders})",
                    params,
                ).rowcount
                deleted += conn.execute(
                    "DELETE FROM index_ohlcv "
                    f"WHERE fetched_at < ? AND workload IN ({placeholders})",
                    params,
                ).rowcount
            conn.commit()
        return deleted
