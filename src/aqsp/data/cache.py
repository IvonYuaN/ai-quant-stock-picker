from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional
import pandas as pd

from aqsp.core.time import now_shanghai


class DataCache:
    def __init__(self, db_path: str | Path = "data/cache.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ohlcv (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
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
                    PRIMARY KEY (symbol, date)
                )
                """
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

    def get_ohlcv(
        self,
        symbol: str,
        start: date,
        end: date,
        max_age_hours: int = 24,
    ) -> Optional[pd.DataFrame]:
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql(
                """
                SELECT * FROM ohlcv
                WHERE symbol = ? AND date >= ? AND date <= ?
                """,
                conn,
                params=(symbol, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
            )
        if df.empty:
            return None

        cutoff = (now_shanghai() - pd.Timedelta(hours=max_age_hours)).isoformat()
        stale = df[df["fetched_at"] < cutoff]
        if not stale.empty:
            return None

        df = df.sort_values("date").reset_index(drop=True)
        return df

    def set_ohlcv(self, symbol: str, df: pd.DataFrame, source: str = "unknown") -> None:
        df = df.copy()
        df["symbol"] = symbol
        df["source"] = source
        df["fetched_at"] = now_shanghai().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            for _, row in df.iterrows():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO ohlcv (
                        symbol, date, open, high, low, close, volume, amount,
                        suspended, limit_up, limit_down, adj_factor, source, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["symbol"],
                        row["date"],
                        row.get("open"),
                        row.get("high"),
                        row.get("low"),
                        row.get("close"),
                        row.get("volume"),
                        row.get("amount"),
                        row.get("suspended", 0),
                        row.get("limit_up", 0.0),
                        row.get("limit_down", 0.0),
                        row.get("adj_factor", 1.0),
                        row["source"],
                        row["fetched_at"],
                    ),
                )
            conn.commit()

    def get_index(
        self,
        code: str,
        start: date,
        end: date,
        max_age_hours: int = 24,
    ) -> Optional[pd.DataFrame]:
        with sqlite3.connect(self.db_path) as conn:
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

        cutoff = (now_shanghai() - pd.Timedelta(hours=max_age_hours)).isoformat()
        stale = df[df["fetched_at"] < cutoff]
        if not stale.empty:
            return None

        df = df.sort_values("date").reset_index(drop=True)
        return df

    def set_index(self, code: str, df: pd.DataFrame, source: str = "unknown") -> None:
        df = df.copy()
        df["code"] = code
        df["source"] = source
        df["fetched_at"] = now_shanghai().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            for _, row in df.iterrows():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO index_ohlcv (
                        code, date, open, high, low, close, volume, amount, source, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["code"],
                        row["date"],
                        row.get("open"),
                        row.get("high"),
                        row.get("low"),
                        row.get("close"),
                        row.get("volume"),
                        row.get("amount"),
                        row["source"],
                        row["fetched_at"],
                    ),
                )
            conn.commit()

    def get_adj_factor(self, symbol: str, date: date) -> Optional[float]:
        with sqlite3.connect(self.db_path) as conn:
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

        with sqlite3.connect(self.db_path) as conn:
            for _, row in df.iterrows():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO adj_factors (
                        symbol, date, adj_factor, source, fetched_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["symbol"],
                        row["date"],
                        row["adj_factor"],
                        row["source"],
                        row["fetched_at"],
                    ),
                )
            conn.commit()

    def get_financial(
        self,
        symbol: str,
        max_age_hours: int = 168,
    ) -> Optional[pd.DataFrame]:
        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
            for _, row in df.iterrows():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO financials (
                        symbol, pubDate, statDate, roeAvg, npMargin, gpMargin,
                        epsTTM, totalShare, source, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["symbol"],
                        str(row.get("pubDate", "")),
                        str(row.get("statDate", "")),
                        row.get("roeAvg"),
                        row.get("npMargin"),
                        row.get("gpMargin"),
                        row.get("epsTTM"),
                        row.get("totalShare"),
                        row["source"],
                        row["fetched_at"],
                    ),
                )
            conn.commit()

    def clear_expired(self, max_age_hours: int = 168) -> int:
        cutoff = (now_shanghai() - pd.Timedelta(hours=max_age_hours)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
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
