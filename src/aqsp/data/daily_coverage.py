"""Read-only coverage diagnostics for the local raw daily SQLite database.

A single symbol with a recent bar can raise ``MAX(trade_date)`` for the whole
table while every other symbol is months behind. Consumers that treat that
maximum as a market-wide as-of date would then announce fresh data they do not
have. This module reports the gap explicitly; it never writes to the database
and never changes the semantics of ``SqliteDbSource.get_latest_daily_date()``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from aqsp.core.errors import DataError


@dataclass(frozen=True)
class DailyCoverageSummary:
    """Coverage of the newest daily bar date across all symbols."""

    db_path: str
    total_symbols: int
    market_max_date: str | None
    symbols_on_max_date: int
    sentinel_symbols: tuple[str, ...]

    @property
    def coverage_pct(self) -> float:
        """Share of symbols that actually have the newest date."""
        if not self.total_symbols or not self.market_max_date:
            return 0.0
        return self.symbols_on_max_date / self.total_symbols


def _read_only_connection(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise DataError(f"日线数据库不存在：{db_path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def summarize_daily_coverage(
    db_path: str, *, exclude_symbols: tuple[str, ...] = ()
) -> DailyCoverageSummary:
    """Report the newest date and how many symbols actually reach it.

    ``exclude_symbols`` lets callers drop index rows, matching the market-date
    query used by ``SqliteDbSource``. The same exclusion is applied to the
    denominator so the ratio stays meaningful.
    """
    placeholders = ",".join("?" for _ in exclude_symbols)
    where = f" WHERE ts_code NOT IN ({placeholders})" if exclude_symbols else ""
    with _read_only_connection(db_path) as conn:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_qfq'"
        ).fetchone()
        if not has_table:
            raise DataError("日线数据库缺少 daily_qfq 表")
        row = conn.execute(
            f"SELECT MAX(trade_date) FROM daily_qfq{where}", exclude_symbols
        ).fetchone()
        market_max = str(row[0]) if row and row[0] else None
        total = int(
            conn.execute(
                f"SELECT COUNT(DISTINCT ts_code) FROM daily_qfq{where}", exclude_symbols
            ).fetchone()[0]
        )
        sentinels: tuple[str, ...] = ()
        if market_max:
            condition = f"{where} AND trade_date = ?" if where else " WHERE trade_date = ?"
            sentinels = tuple(
                str(item[0])
                for item in conn.execute(
                    "SELECT DISTINCT ts_code FROM daily_qfq"
                    f"{condition} ORDER BY ts_code",
                    (*exclude_symbols, market_max),
                )
            )
    return DailyCoverageSummary(
        db_path=db_path,
        total_symbols=total,
        market_max_date=market_max,
        symbols_on_max_date=len(sentinels),
        sentinel_symbols=sentinels,
    )


def market_date_is_usable(
    summary: DailyCoverageSummary, *, min_coverage_ratio: float
) -> bool:
    """Whether the newest date may represent the whole market.

    The threshold is caller-supplied: it is a data-readiness contract, not a
    strategy parameter, and must be stated explicitly at every call site.
    """
    if not 0 < min_coverage_ratio <= 1:
        raise ValueError("最低覆盖比例必须在 (0, 1] 之间")
    return (
        summary.market_max_date is not None
        and summary.coverage_pct >= min_coverage_ratio
    )


def latest_date_histogram(db_path: str) -> tuple[tuple[str, int], ...]:
    """Per-symbol newest-date distribution, newest first (diagnostic only).

    This scans the whole daily table and is intentionally kept out of the
    request path.
    """
    with _read_only_connection(db_path) as conn:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_qfq'"
        ).fetchone()
        if not has_table:
            raise DataError("日线数据库缺少 daily_qfq 表")
        rows = conn.execute(
            "SELECT latest, COUNT(*) FROM ("
            "  SELECT ts_code, MAX(trade_date) AS latest FROM daily_qfq GROUP BY ts_code"
            ") GROUP BY latest ORDER BY latest DESC"
        ).fetchall()
    return tuple((str(latest), int(count)) for latest, count in rows if latest)


__all__ = [
    "DailyCoverageSummary",
    "latest_date_histogram",
    "market_date_is_usable",
    "summarize_daily_coverage",
]
