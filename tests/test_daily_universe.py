from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from aqsp.data.daily_universe import latest_available_daily_day
from aqsp.data.daily_universe import refreshable_daily_universe


def test_latest_available_daily_day_uses_recent_dense_baseline_without_scanning_old_history(
    tmp_path: Path,
):
    db_path = tmp_path / "daily.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE daily_qfq (ts_code TEXT, trade_date TEXT)"
        )
        connection.executemany(
            "INSERT INTO daily_qfq(ts_code, trade_date) VALUES (?, ?)",
            [
                (f"{index:06d}.SZ", "20250101") for index in range(1000)
            ]
            + [(f"{index:06d}.SZ", "20260930") for index in range(100)]
            + [("000001.SZ", "20261102")],
        )

    class Source:
        pass

    Source.db_path = str(db_path)

    assert latest_available_daily_day(Source(), not_after=date(2026, 11, 2)) == date(2026, 9, 30)


def test_refreshable_daily_universe_includes_star_and_excludes_unsupported_bj_and_stocks():
    class Source:
        names = {
            "600000": "浦发银行",
            "000001": "平安银行",
            "300001": "特锐德",
            "688001": "华兴源创",
            "430001": "北交所样例",
            "600001": "ST样例",
        }

        def get_available_symbols(self):
            return list(self.names)

        def get_symbol_name(self, symbol):
            return self.names[symbol]

    assert refreshable_daily_universe(Source()) == [
        "600000",
        "000001",
        "300001",
        "688001",
    ]
