"""Deterministic active A-share universe shared by daily refresh and coverage checks."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from aqsp.data.sqlite_db_source import SqliteDbSource


_MAIN_BOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")
_CHINEXT_PREFIXES = ("300", "301")
_STAR_BOARD_PREFIXES = ("688",)
_ACTIVE_BASELINE_MIN_COVERAGE_RATIO = 0.8
_ACTIVE_BASELINE_LOOKBACK_DAYS = 180


def latest_available_daily_day(source: SqliteDbSource, *, not_after: date) -> date | None:
    """Find the latest recent dense day used to filter inactive symbols."""
    not_before = not_after - timedelta(days=_ACTIVE_BASELINE_LOOKBACK_DAYS)
    with sqlite3.connect(source.db_path) as connection:
        row = connection.execute(
            """
            WITH coverage AS (
                SELECT trade_date, COUNT(DISTINCT ts_code) AS symbol_count
                FROM daily_qfq
                WHERE trade_date >= ? AND trade_date <= ?
                GROUP BY trade_date
            )
            SELECT MAX(trade_date)
            FROM coverage
            WHERE symbol_count >= (
                SELECT MAX(symbol_count) * ? FROM coverage
            )
            """,
            (
                not_before.strftime("%Y%m%d"),
                not_after.strftime("%Y%m%d"),
                _ACTIVE_BASELINE_MIN_COVERAGE_RATIO,
            ),
        ).fetchone()
    raw = str((row or [""])[0] or "")
    if len(raw) != 8 or not raw.isdigit():
        return None
    return date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}")


def refreshable_daily_universe(
    source: SqliteDbSource,
    *,
    universe_limit: int = 0,
    reference_day: date | None = None,
) -> list[str]:
    """Return supported active A-share symbols in deterministic board rotation order."""
    boards: tuple[list[str], list[str], list[str], list[str]] = ([], [], [], [])
    for symbol in source.get_available_symbols():
        code = str(symbol).strip()
        name = source.get_symbol_name(code).upper().replace(" ", "")
        if "ST" in name or "退" in name:
            continue
        if code.startswith(_MAIN_BOARD_PREFIXES[:4]):
            boards[0].append(code)
        elif code.startswith(_MAIN_BOARD_PREFIXES[4:]):
            boards[1].append(code)
        elif code.startswith(_CHINEXT_PREFIXES):
            boards[2].append(code)
        elif code.startswith(_STAR_BOARD_PREFIXES):
            boards[3].append(code)

    ordered = [sorted(board) for board in boards]
    result: list[str] = []
    max_length = max((len(board) for board in ordered), default=0)
    for index in range(max_length):
        for board in ordered:
            if index < len(board):
                result.append(board[index])
    if reference_day is not None:
        coverage_day = latest_available_daily_day(source, not_after=reference_day)
        if coverage_day is None:
            raise RuntimeError("sqlite daily source has no historical coverage baseline")
        covered = set(
            source.get_symbols_with_daily_coverage(
                result,
                coverage_day,
                coverage_day,
                min_rows=1,
                min_coverage_ratio=1.0,
            )
        )
        result = [symbol for symbol in result if symbol in covered]
    if not result:
        raise RuntimeError("sqlite daily source has no active eligible supported A-share symbols")
    return result[:universe_limit] if universe_limit > 0 else result
