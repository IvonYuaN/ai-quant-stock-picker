"""Read-only coverage diagnostics for raw daily SQLite databases.

A single symbol with a recent bar can raise ``MAX(trade_date)`` for the whole
table while every other symbol is months behind. Consumers that treat that
maximum as a market-wide as-of date would then announce fresh data they do not
have. This module reports the gap explicitly; it never writes to the database.

Two physical layouts exist in this project and both must be supported, because
the development line (``astocks_raw.db``) and the production line
(``cache.db``) do not share a schema:

* ``daily_qfq(ts_code, trade_date, ...)``  -- runner / local research database
* ``ohlcv(symbol, date, price_mode, ...)`` -- production cache

Advertising a single hard-coded layout made the gate raise ``DataError`` on any
database using the other one, which is worse than the bare ``MAX`` it replaced.
Layout detection is therefore part of the contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from aqsp.core.errors import DataError

# Data-readiness contract shared by both development lines. This is not a
# strategy threshold: it only decides whether a daily cross-section may be
# treated as a complete market session.
_MARKET_DAILY_COVERAGE_ENV = "AQSP_MIN_MARKET_DAILY_COVERAGE"
_DEFAULT_MIN_MARKET_DAILY_COVERAGE = 0.8

# Denominator scope. ``0`` keeps the original contract (every symbol ever
# stored, including delisted and long-suspended tickers). Production is a
# display tier that only maintains liquid, currently listed symbols, so a
# whole-history denominator makes it report a permanent shortfall that says
# nothing about whether *its* data is fresh.
_COVERAGE_UNIVERSE_LOOKBACK_ENV = "AQSP_COVERAGE_UNIVERSE_LOOKBACK_DAYS"
_DEFAULT_COVERAGE_UNIVERSE_LOOKBACK_DAYS = 0


def min_market_daily_coverage_ratio() -> float:
    """Read the coverage threshold, falling back to the strict default."""
    import os

    raw = str(os.getenv(_MARKET_DAILY_COVERAGE_ENV, "")).strip()
    if not raw:
        return _DEFAULT_MIN_MARKET_DAILY_COVERAGE
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    return value if 0 < value <= 1 else _DEFAULT_MIN_MARKET_DAILY_COVERAGE


def coverage_universe_lookback_days() -> int:
    """Calendar days that define the denominator's "active universe".

    ``0`` means "every symbol ever stored". A positive value restricts the
    denominator to symbols that traded within that window, which is what a
    display-tier database actually commits to keeping current.
    """
    import os

    raw = str(os.getenv(_COVERAGE_UNIVERSE_LOOKBACK_ENV, "")).strip()
    if not raw:
        return _DEFAULT_COVERAGE_UNIVERSE_LOOKBACK_DAYS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_COVERAGE_UNIVERSE_LOOKBACK_DAYS
    return value if value > 0 else 0


@dataclass(frozen=True)
class _DayTableSpec:
    """Physical layout of a daily bar table.

    ``table``/``symbol_col``/``date_col`` are interpolated into SQL, so they are
    restricted to this module's own allow-list and never originate from user
    input.
    """

    table: str
    symbol_col: str
    date_col: str
    # Some layouts store adjusted and unadjusted rows side by side. When the
    # column exists we must pin it, otherwise one session is counted once per
    # price mode and the ratio becomes meaningless.
    price_mode_col: str | None = None
    price_mode_value: str | None = None


# Order matters: the research database is preferred when a file somehow
# contains both layouts.
_KNOWN_LAYOUTS: tuple[_DayTableSpec, ...] = (
    _DayTableSpec("daily_qfq", "ts_code", "trade_date"),
    _DayTableSpec("ohlcv", "symbol", "date", "price_mode", "raw"),
)


def _detect_layout(conn: sqlite3.Connection) -> _DayTableSpec:
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for spec in _KNOWN_LAYOUTS:
        if spec.table not in tables:
            continue
        if spec.price_mode_col is None:
            return spec
        columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({spec.table})")  # noqa: S608 - allow-listed
        }
        if spec.price_mode_col in columns:
            return spec
        # Column absent: fall back to the same layout without the pin.
        return _DayTableSpec(spec.table, spec.symbol_col, spec.date_col)
    expected = " / ".join(spec.table for spec in _KNOWN_LAYOUTS)
    raise DataError(f"日线数据库缺少可识别的日线表（期望其一：{expected}）")


def _normalize_date(value: object) -> str | None:
    """Return ``YYYYMMDD`` regardless of the layout's storage format.

    ``daily_qfq`` stores ``20261105`` while ``ohlcv`` stores ``2026-11-05``.
    Consumers compare the as-of date against trading calendars in the compact
    form, so the boundary normalizes once here instead of at every call site.
    """
    if value is None:
        return None
    compact = str(value).strip().replace("-", "").replace("/", "")
    return compact or None


def _shift_date(raw: str, days: int) -> str:
    """Subtract ``days`` from a stored date, preserving its own formatting.

    ``daily_qfq`` stores ``20261105`` and ``ohlcv`` stores ``2026-11-05``; the
    shifted boundary has to be comparable with the column it filters.
    """
    from datetime import datetime, timedelta

    dashed = "-" in raw
    compact = raw.strip().replace("-", "").replace("/", "")
    shifted = datetime.strptime(compact, "%Y%m%d") - timedelta(days=days)
    return shifted.strftime("%Y-%m-%d") if dashed else shifted.strftime("%Y%m%d")


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


def _filters(spec: _DayTableSpec, exclude_symbols: tuple[str, ...]) -> tuple[str, list[str]]:
    """Build the shared WHERE clause plus its bound parameters."""
    clauses: list[str] = []
    params: list[str] = []
    if spec.price_mode_col and spec.price_mode_value:
        clauses.append(f"{spec.price_mode_col} = ?")
        params.append(spec.price_mode_value)
    if exclude_symbols:
        placeholders = ",".join("?" for _ in exclude_symbols)
        clauses.append(f"{spec.symbol_col} NOT IN ({placeholders})")
        params.extend(exclude_symbols)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def summarize_daily_coverage(
    db_path: str, *, exclude_symbols: tuple[str, ...] = ()
) -> DailyCoverageSummary:
    """Report the newest date and how many symbols actually reach it.

    ``exclude_symbols`` lets callers drop index rows, matching the market-date
    query used by ``SqliteDbSource``. The same exclusion is applied to the
    denominator so the ratio stays meaningful.
    """
    with _read_only_connection(db_path) as conn:
        spec = _detect_layout(conn)
        where, params = _filters(spec, exclude_symbols)
        row = conn.execute(
            f"SELECT MAX({spec.date_col}) FROM {spec.table}{where}",  # noqa: S608 - allow-listed
            params,
        ).fetchone()
        raw_max = str(row[0]) if row and row[0] else None
        # Denominator: whole history by default, or only symbols that traded
        # inside the configured lookback window.
        lookback = coverage_universe_lookback_days()
        if lookback > 0 and raw_max:
            floor = _shift_date(raw_max, lookback)
            universe_where = (
                f"{where} AND {spec.date_col} >= ?" if where else f" WHERE {spec.date_col} >= ?"
            )
            total = int(
                conn.execute(
                    f"SELECT COUNT(DISTINCT {spec.symbol_col}) FROM {spec.table}{universe_where}",  # noqa: S608
                    [*params, floor],
                ).fetchone()[0]
            )
        else:
            total = int(
                conn.execute(
                    f"SELECT COUNT(DISTINCT {spec.symbol_col}) FROM {spec.table}{where}",  # noqa: S608
                    params,
                ).fetchone()[0]
            )
        sentinels: tuple[str, ...] = ()
        if raw_max:
            condition = f"{where} AND {spec.date_col} = ?" if where else f" WHERE {spec.date_col} = ?"
            sentinels = tuple(
                str(item[0])
                for item in conn.execute(
                    f"SELECT DISTINCT {spec.symbol_col} FROM {spec.table}{condition}"  # noqa: S608
                    f" ORDER BY {spec.symbol_col}",
                    [*params, raw_max],
                )
            )
    return DailyCoverageSummary(
        db_path=db_path,
        total_symbols=total,
        market_max_date=_normalize_date(raw_max),
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
        spec = _detect_layout(conn)
        where, params = _filters(spec, ())
        rows = conn.execute(
            f"SELECT latest, COUNT(*) FROM ("  # noqa: S608 - allow-listed
            f"  SELECT {spec.symbol_col} AS sym, MAX({spec.date_col}) AS latest"
            f"  FROM {spec.table}{where} GROUP BY {spec.symbol_col}"
            ") GROUP BY latest ORDER BY latest DESC",
            params,
        ).fetchall()
    return tuple(
        (normalized, int(count))
        for latest, count in rows
        if (normalized := _normalize_date(latest))
    )


def verified_market_date(
    db_path: str, *, exclude_symbols: tuple[str, ...] = ()
) -> str | None:
    """Single entry point for consumers that used a bare ``MAX(trade_date)``.

    Returns the newest ``YYYYMMDD`` date only when its cross-section passes the
    market coverage gate. Anything unverifiable returns ``None`` so callers
    fail closed instead of treating one refreshed symbol as a complete session.
    Index-row exclusion is opt-in so this module stays independent of any
    particular data-source implementation.
    """
    try:
        summary = summarize_daily_coverage(db_path, exclude_symbols=exclude_symbols)
    except (DataError, OSError, ValueError, RuntimeError, sqlite3.Error):
        return None
    if not market_date_is_usable(
        summary, min_coverage_ratio=min_market_daily_coverage_ratio()
    ):
        return None
    return summary.market_max_date


__all__ = [
    "DailyCoverageSummary",
    "latest_date_histogram",
    "market_date_is_usable",
    "min_market_daily_coverage_ratio",
    "summarize_daily_coverage",
    "verified_market_date",
]
