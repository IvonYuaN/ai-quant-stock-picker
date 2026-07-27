import sqlite3
from collections import Counter
from datetime import date, timedelta

from scripts.run_variant_suite import run_suite


def test_run_suite_creates_many_explained_nonduplicate_accounts(tmp_path):
    db = tmp_path / "history.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE ohlcv (
                symbol TEXT, date TEXT, name TEXT, price_mode TEXT, workload TEXT,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                amount REAL, suspended INTEGER, limit_up REAL, limit_down REAL
            )
            """
        )
        rows = []
        start = date(2026, 1, 1)
        symbols = {
            "AAA": ("阿尔法", 10.0, 0.18, 1.00),
            "BBB": ("贝塔", 18.0, 0.08, 0.75),
            "CCC": ("伽马", 14.0, -0.04, 1.45),
            "DDD": ("德尔塔", 9.0, 0.03, 0.55),
            "EEE": ("伊普西龙", 22.0, 0.12, 1.20),
        }
        for symbol, (name, base, slope, volume_scale) in symbols.items():
            for index in range(90):
                current = start + timedelta(days=index)
                wave = ((index % 9) - 4) * 0.06
                breakout = 1.2 if symbol == "EEE" and index in {45, 70} else 0.0
                close = base + index * slope + wave + breakout
                high = close + 0.25
                low = close - 0.22
                volume = 100000.0 * volume_scale * (1.0 + (index % 7) / 10.0)
                if symbol in {"AAA", "EEE"} and index in {44, 69}:
                    volume *= 2.2
                rows.append(
                    (
                        symbol,
                        current.isoformat(),
                        name,
                        "raw",
                        "historical",
                        close,
                        high,
                        low,
                        close,
                        volume,
                        close * volume,
                        0,
                        close * 1.1,
                        close * 0.9,
                    )
                )
        conn.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    result = run_suite(db, tuple(symbols), "2026-01-01", "2026-03-31")
    variants = result["variants"]

    assert result["initial_cash"] == 100_000.0
    assert result["schema_version"] == "variant-suite-v2"
    assert len(variants) >= 100
    assert {item["initial_cash"] for item in variants} == {100_000.0}
    assert len({item["variant_id"] for item in variants}) == len(variants)
    assert len({item["strategy_signature"] for item in variants}) >= 100
    assert len({item["holdings_signature"] for item in variants}) > 1
    assert (
        Counter(item["holdings_signature"] for item in variants[:12]).most_common(1)[0][
            1
        ]
        <= 3
    )
    assert all("cash" in item and "total_pnl" in item for item in variants)
    assert all(item["strategy"]["hypothesis"] for item in variants)
    assert all(item["holdings_date"] == "2026-03-31" for item in variants)
    assert all(item["previous_holdings_date"] == "2026-03-30" for item in variants)
    assert all("previous_holdings" in item for item in variants)
    assert all(item["adjustments"] for item in variants)
    assert any(
        holding.get("name") == "阿尔法"
        for item in variants
        for holding in item["holdings"]
    )
    assert all(item["filled_orders"] >= 0 for item in variants)
    assert {item["strategy"]["mode"] for item in variants} >= {
        "reversion",
        "volume_breakout",
        "atr_trend",
        "defensive_range",
        "macd_cross",
        "kdj_rebound",
    }
    assert result["execution_rules"]["t_plus_one"] is True
    assert result["execution_rules"]["raw_unadjusted_prices"] is True
    assert result["optimization"]["evaluation_only"] is True
    assert result["optimization"]["selected_variant_id"]


def test_write_home_snapshot_recovers_variant_actions_from_legacy_fills():
    from aqsp.web.home_snapshot import HomeSnapshotHolding
    from scripts.write_home_snapshot import (
        _variant_adjustment_lines,
        _variant_recent_actions,
    )

    item = {
        "fills": [
            {
                "date": "2026-07-23",
                "symbol": "002379",
                "side": "buy",
                "quantity": 100,
                "price": 150.0,
                "status": "filled",
            }
        ]
    }
    holdings = (
        HomeSnapshotHolding(
            symbol="002379",
            quantity=100,
            average_price=150.0,
            last_price=165.0,
            market_value=16500.0,
            unrealized_pnl=1500.0,
        ),
    )

    actions = _variant_recent_actions(item)
    adjustments = _variant_adjustment_lines(item, holdings, (), actions)

    assert actions[0]["symbol"] == "002379"
    assert "v2 重算后补齐" in str(actions[0]["reason"])
    assert adjustments[0].startswith("持有 002379")


def test_run_suite_handles_flat_kdj_range_without_object_dtype_crash(tmp_path):
    db = tmp_path / "flat.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE ohlcv (
                symbol TEXT, date TEXT, name TEXT, price_mode TEXT, workload TEXT,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                amount REAL, suspended INTEGER, limit_up REAL, limit_down REAL
            )
            """
        )
        rows = []
        start = date(2026, 1, 1)
        for index in range(80):
            current = start + timedelta(days=index)
            close = 10.0 if index < 20 else 10.0 + (index - 20) * 0.03
            rows.append(
                (
                    "FLAT",
                    current.isoformat(),
                    "平线测试",
                    "raw",
                    "historical",
                    close,
                    close,
                    close,
                    close,
                    100000.0,
                    close * 100000.0,
                    0,
                    close * 1.1,
                    close * 0.9,
                )
            )
        conn.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    result = run_suite(db, ("FLAT",), "2026-01-01", "2026-03-21")

    assert result["schema_version"] == "variant-suite-v2"
    assert len(result["variants"]) >= 100
