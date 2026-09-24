from __future__ import annotations

import sqlite3

from scripts.factor_ic_diagnosis import load_prices


def test_load_prices_uses_raw_ohlc_columns_from_daily_qfq_table(tmp_path, capsys) -> None:
    db = tmp_path / "prices.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE daily_qfq (
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                open_qfq REAL,
                high_qfq REAL,
                low_qfq REAL,
                close_qfq REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO daily_qfq VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "000001.SZ",
                "20260918",
                10.0,
                11.0,
                9.0,
                10.5,
                1000.0,
                100.0,
                110.0,
                90.0,
                105.0,
            ),
        )
        conn.commit()

    prices = load_prices(str(db), "2026-09-18", "2026-09-18", ["000001"])

    assert prices.iloc[0][["open", "high", "low", "close"]].tolist() == [
        10.0,
        11.0,
        9.0,
        10.5,
    ]
    assert "price_basis=raw" in capsys.readouterr().out
