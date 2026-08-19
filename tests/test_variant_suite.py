import sqlite3

from scripts.run_variant_suite import _with_indicators, run_suite


def test_variant_indicators_keep_volume_ratio_with_small_history_gaps() -> None:
    import pandas as pd

    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=45, freq="D"),
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "volume": 100.0,
        }
    )
    frame.loc[[5, 12, 19, 26], "volume"] = None

    enriched = _with_indicators(frame, lookback=40)

    assert pd.notna(enriched.iloc[-1]["volume_ratio"])


def test_variant_indicators_reject_volume_ratio_with_too_many_history_gaps() -> None:
    import pandas as pd

    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=45, freq="D"),
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "volume": 100.0,
        }
    )
    frame.loc[range(5, 15), "volume"] = None

    enriched = _with_indicators(frame, lookback=40)

    assert pd.isna(enriched.iloc[-1]["volume_ratio"])


def test_run_suite_creates_twenty_four_independent_ten_wan_accounts(tmp_path):
    db = tmp_path / "history.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE ohlcv (
                symbol TEXT, date TEXT, price_mode TEXT, workload TEXT,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                amount REAL, suspended INTEGER, limit_up REAL, limit_down REAL
            )
            """
        )
        rows = []
        for index in range(30):
            close = 10.0 + index * 0.2
            rows.append(
                (
                    "AAA",
                    f"2026-01-{index + 1:02d}",
                    "raw",
                    "historical",
                    close,
                    close + 0.1,
                    close - 0.1,
                    close,
                    100000.0,
                    close * 100000.0,
                    0,
                    close * 1.1,
                    close * 0.9,
                )
            )
        conn.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    result = run_suite(db, ("AAA",), "2026-01-01", "2026-01-30")
    assert result["initial_cash"] == 100_000.0
    assert len(result["variants"]) == 24
    assert {item["initial_cash"] for item in result["variants"]} == {100_000.0}
    assert all("cash" in item and "total_pnl" in item for item in result["variants"])
    assert all("strategy" in item and "holdings" in item for item in result["variants"])
    assert result["optimization"]["evaluation_only"] is True
    assert result["optimization"]["selected_variant_id"]
    assert all(item["filled_orders"] >= 0 for item in result["variants"])
    assert {item["strategy"]["mode"] for item in result["variants"]} >= {
        "reversion",
        "volume_breakout",
        "atr_trend",
        "defensive_range",
    }
    assert all(item["strategy"]["hypothesis"] for item in result["variants"])
    assert result["execution_rules"]["t_plus_one"] is True
