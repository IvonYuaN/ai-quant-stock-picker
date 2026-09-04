from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from aqsp.core.errors import DataError
from scripts import backfill_index_to_sqlite as backfill


def _bar_frame(rows: list[dict]) -> pd.DataFrame:
    """Build the minimal column set ``_fetch_index_rows`` consumes."""
    return pd.DataFrame(rows)


class _FakeIndexSource:
    """Stand-in for TencentSource.fetch_index with a fixed in-memory table."""

    def __init__(self, bars: dict[str, pd.DataFrame]):
        self._bars = bars
        self.calls: list[tuple[list[str], date, date]] = []

    def fetch_index(self, index_codes, start, end):
        self.calls.append((list(index_codes), start, end))
        out: dict[str, pd.DataFrame] = {}
        for code in index_codes:
            frame = self._bars.get(code)
            if frame is None:
                continue
            mask = frame["date"].between(
                start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
            )
            part = frame[mask]
            if not part.empty:
                out[code] = part.reset_index(drop=True)
        if not out:
            raise DataError("fake 指数获取失败")
        return out


def test_index_ts_code_mapping() -> None:
    assert backfill._index_ts_code("000300") == "000300.SH"
    assert backfill._index_ts_code("000905") == "000905.SH"
    assert backfill._index_ts_code("399006") == "399006.SZ"
    assert backfill._index_ts_code("399005") == "399005.SZ"
    assert backfill._index_ts_code("899050") == "899050.BJ"


def test_default_universe_excludes_colliding_stock_symbols() -> None:
    # ``SqliteDbSource._load_symbol_map`` keys ``stocks`` by the bare code, so an
    # index sharing a code with a real stock (000001 平安银行, 000905 厦门港务,
    # 000852 石化机械) would silently shadow it.  The default universe must only
    # carry the collision-free walkforward benchmark.
    codes = {code for code, _ in backfill.INDEX_UNIVERSE}
    assert codes == {"000300"}
    assert not codes & {"000001", "000905", "000852"}


def test_backfill_writes_index_bars_with_raw_convention(tmp_path: Path) -> None:
    frame = _bar_frame(
        [
            {
                "date": "2024-01-02",
                "open": 3400.0,
                "high": 3410.0,
                "low": 3390.0,
                "close": 3405.0,
                "volume": 1_000_000.0,
                "amount": 3_400_000_000.0,
            },
            {
                "date": "2024-01-03",
                "open": 3405.0,
                "high": 3420.0,
                "low": 3400.0,
                "close": 3415.0,
                "volume": 1_200_000.0,
                "amount": 4_100_000_000.0,
            },
        ]
    )
    source = _FakeIndexSource({"000300": frame})
    db = tmp_path / "astocks_raw.db"

    summary = backfill.backfill_index(
        db_path=db,
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
        index_universe=(("000300", "沪深300"),),
        source=source,  # type: ignore[arg-type]
    )

    assert summary.indices_seen == 1
    assert summary.indices_updated == 1
    assert summary.rows_written == 2

    import sqlite3

    with sqlite3.connect(db) as conn:
        stock = conn.execute(
            "SELECT name FROM stocks WHERE ts_code = '000300.SH'"
        ).fetchone()
        assert stock is not None and stock[0] == "沪深300"
        rows = conn.execute(
            """
            SELECT trade_date, open, high, low, close, close_qfq,
                   open_qfq, high_qfq, low_qfq, volume, amount
            FROM daily_qfq WHERE ts_code = '000300.SH' ORDER BY trade_date
            """
        ).fetchall()
    assert [row[0] for row in rows] == ["20240102", "20240103"]
    first = rows[0]
    # raw convention: close_qfq == close, qfq open/high/low left NULL.
    assert first[4] == first[5] == 3405.0
    assert first[6] is None and first[7] is None and first[8] is None
    assert first[9] == 1_000_000
    assert first[10] == 3_400_000_000.0


def test_backfill_is_idempotent_and_incremental(tmp_path: Path) -> None:
    bars = {
        "2024-01-02": [3400.0, 3410.0, 3390.0, 3405.0, 1_000_000.0, 3.4e9],
        "2024-01-03": [3405.0, 3420.0, 3400.0, 3415.0, 1_200_000.0, 4.1e9],
    }

    def build_frame() -> pd.DataFrame:
        return _bar_frame(
            [
                {
                    "date": day,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "amount": amount,
                }
                for day, (open_, high, low, close, volume, amount) in bars.items()
            ]
        )

    db = tmp_path / "astocks_raw.db"
    universe = (("000300", "沪深300"),)

    source = _FakeIndexSource({"000300": build_frame()})
    first = backfill.backfill_index(
        db_path=db,
        start=date(2024, 1, 1),
        end=date(2024, 1, 3),
        index_universe=universe,
        source=source,  # type: ignore[arg-type]
    )
    assert first.rows_written == 2

    # Idempotent: re-running over the same window is a no-op.
    source2 = _FakeIndexSource({"000300": build_frame()})
    second = backfill.backfill_index(
        db_path=db,
        start=date(2024, 1, 1),
        end=date(2024, 1, 3),
        index_universe=universe,
        source=source2,  # type: ignore[arg-type]
    )
    assert second.rows_written == 0

    # Incremental: a newer bar is fetched strictly after the stored latest date.
    bars["2024-01-05"] = [3415.0, 3430.0, 3410.0, 3425.0, 1_300_000.0, 4.4e9]
    source3 = _FakeIndexSource({"000300": build_frame()})
    third = backfill.backfill_index(
        db_path=db,
        start=date(2024, 1, 1),
        end=date(2024, 1, 5),
        index_universe=universe,
        source=source3,  # type: ignore[arg-type]
    )
    assert third.rows_written == 1
    # The incremental fetch must begin strictly after the stored latest date.
    assert all(call[1] >= date(2024, 1, 4) for call in source3.calls)

    import sqlite3

    with sqlite3.connect(db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM daily_qfq WHERE ts_code = '000300.SH'"
        ).fetchone()[0]
    assert count == 3
