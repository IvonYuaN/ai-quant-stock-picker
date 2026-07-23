import sqlite3

import numpy as np
import pandas as pd
import pytest

from scripts.run_variant_suite import (
    _prepare_base_signal_frame,
    _prepare_signal_frame,
    attach_previous_variant_holdings,
    load_frames,
    run_suite,
    select_stratified_symbols,
    validate_previous_variant_baseline,
    validate_variant_artifact,
)


def test_run_suite_creates_distinct_independent_ten_wan_accounts(tmp_path):
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
    assert len(result["variants"]) >= 40
    assert len({item["variant_id"] for item in result["variants"]}) == len(
        result["variants"]
    )
    assert len({item["label"] for item in result["variants"]}) == len(
        result["variants"]
    )
    assert {item["initial_cash"] for item in result["variants"]} == {100_000.0}
    assert all("cash" in item and "total_pnl" in item for item in result["variants"])
    assert all("strategy" in item and "holdings" in item for item in result["variants"])
    assert result["optimization"]["evaluation_only"] is True
    assert result["optimization"]["grid_version"] == "2026.07.23.v4"
    assert result["optimization"]["family_count"] == 16
    assert result["optimization"]["configuration_count"] == 4
    assert result["optimization"]["selected_variant_id"]
    assert all(item["filled_orders"] >= 0 for item in result["variants"])
    assert all(item["strategy"]["max_positions"] >= 1 for item in result["variants"])
    assert {item["strategy"]["mode"] for item in result["variants"]} >= {
        "reversion",
        "trend",
        "breakout",
        "volume_breakout",
        "macd",
        "kdj",
        "low_vol",
    }
    assert all(item["strategy"]["hypothesis"] for item in result["variants"])
    assert result["universe_scope"]["board_scope"] == "沪深主板+创业板"
    assert result["universe_scope"]["excluded"] == ["ST", "科创板", "其他板块"]
    assert all(
        fill["evidence"]
        for item in result["variants"]
        for fill in item["fills"]
        if fill["status"] == "filled"
    )
    assert result["execution_rules"]["t_plus_one"] is True


def test_select_stratified_symbols_spans_boards_and_turnover_quantiles(tmp_path):
    db = tmp_path / "universe.db"
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
        symbols = ["000001", "000101", "300001", "300101", "600001", "601001"]
        rows = []
        for index, symbol in enumerate(symbols):
            rows.append(
                (
                    symbol,
                    "2026-07-16",
                    "raw",
                    "historical",
                    10,
                    11,
                    9,
                    10,
                    1000,
                    (index + 1) * 1_000_000,
                    0,
                    11,
                    9,
                )
            )
        conn.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    selected = select_stratified_symbols(db, "2026-07-20", max_symbols=7)

    assert set(selected) == set(symbols)
    assert selected != tuple(sorted(symbols))


def test_external_raw_database_loader_keeps_board_symbols_and_dates(tmp_path):
    db = tmp_path / "astocks_raw.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE daily_qfq (
                ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL,
                close REAL, volume REAL, amount REAL
            );
            """
        )
        conn.executemany(
            "INSERT INTO stocks VALUES (?, ?)",
            [("600001.SH", "沪市样本"), ("000001.SZ", "深市样本")],
        )
        conn.executemany(
            "INSERT INTO daily_qfq VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (code, date, 10.0, 10.5, 9.5, 10.0, 1000.0, 1_000_000.0)
                for code in ("600001.SH", "000001.SZ")
                for date in ("20260717", "20260720")
            ]
            + [
                (code, "20260718", 10.0, 10.5, 9.5, 10.0, 1000.0, 1_000_000.0)
                for code in ("688001.SH", "688002.SH", "688003.SH")
            ],
        )

    selected = select_stratified_symbols(db, "2026-07-20")
    frames = load_frames(db, selected, "2026-07-17", "2026-07-20")

    assert set(selected) == {"600001", "000001"}
    assert set(frames) == set(selected)
    assert set(frames["600001"]["date"]) == {"2026-07-17", "2026-07-20"}


def test_attach_previous_variant_holdings_requires_exact_previous_date() -> None:
    current = {
        "end_date": "2026-07-20",
        "variants": [
            {
                "variant_id": "trend_lb10_n3",
                "holdings": [{"symbol": "600001", "quantity": 100}],
            }
        ],
    }
    previous = {
        "end_date": "2026-07-17",
        "variants": [
            {
                "variant_id": "trend_lb10_n3",
                "holdings": [{"symbol": "000001", "quantity": 200}],
            }
        ],
    }

    carried = attach_previous_variant_holdings(
        current,
        previous,
        expected_previous_date="2026-07-17",
    )
    assert carried["variants"][0]["previous_holdings"] == [
        {"symbol": "000001", "quantity": 200}
    ]
    assert (
        "previous_holdings"
        not in attach_previous_variant_holdings(
            current,
            previous,
            expected_previous_date="2026-07-18",
        )["variants"][0]
    )


def test_attach_previous_variant_holdings_reuses_baseline_on_same_date_retry() -> None:
    current = {
        "end_date": "2026-07-20",
        "variants": [{"variant_id": "trend_lb10_n3", "holdings": []}],
    }
    previous = {
        "end_date": "2026-07-20",
        "previous_holdings_date": "2026-07-17",
        "variants": [
            {
                "variant_id": "trend_lb10_n3",
                "holdings": [{"symbol": "600001", "quantity": 100}],
                "previous_holdings": [{"symbol": "000001", "quantity": 200}],
            }
        ],
    }

    carried = attach_previous_variant_holdings(
        current,
        previous,
        expected_previous_date="2026-07-17",
    )

    assert carried["variants"][0]["previous_holdings"] == [
        {"symbol": "000001", "quantity": 200}
    ]


def test_validate_previous_variant_baseline_rejects_missing_same_day_baseline() -> None:
    payload = {
        "end_date": "2026-07-20",
        "variants": [{"variant_id": "trend_lb10_n3", "holdings": []}],
    }
    previous = {
        "end_date": "2026-07-20",
        "previous_holdings_date": "2026-07-17",
        "variants": [{"variant_id": "trend_lb10_n3", "holdings": []}],
    }

    with pytest.raises(ValueError, match="缺少昨日持仓基线"):
        validate_previous_variant_baseline(
            payload,
            previous,
            expected_previous_date="2026-07-17",
        )


def test_validate_previous_variant_baseline_rejects_stale_previous_artifact() -> None:
    payload = {
        "end_date": "2026-07-23",
        "variants": [
            {
                "variant_id": "trend_lb10_n3",
                "previous_holdings": [],
            }
        ],
    }
    previous = {
        "end_date": "2026-07-21",
        "variants": [{"variant_id": "trend_lb10_n3", "holdings": []}],
    }

    with pytest.raises(ValueError, match="准确的昨日持仓基线"):
        validate_previous_variant_baseline(
            payload,
            previous,
            expected_previous_date="2026-07-22",
        )


def test_fast_indicator_cache_matches_causal_pandas_features() -> None:
    dates = pd.date_range("2026-01-01", periods=40, freq="D")
    close = pd.Series(np.linspace(10.0, 14.0, len(dates)))
    raw = pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.linspace(1000.0, 2000.0, len(dates)),
            "amount": close * 1000.0,
            "suspended": 0,
            "limit_up": np.nan,
            "limit_down": np.nan,
        }
    )

    base = _prepare_base_signal_frame(raw)
    prepared = _prepare_signal_frame(raw, 10, base=base)
    expected_ema = close.ewm(span=12, adjust=False).mean().to_numpy()
    expected_sma = close.rolling(10).mean().to_numpy()
    expected_high = raw["high"].rolling(10).max().shift(1).to_numpy()

    assert np.allclose(base["ema12"], expected_ema, equal_nan=True)
    assert np.allclose(prepared["sma"], expected_sma, equal_nan=True)
    assert np.allclose(prepared["prior_high"], expected_high, equal_nan=True)


def test_validate_variant_artifact_allows_historical_warmup_before_reset():
    payload = {
        "schema_version": "variant-suite-v1",
        "data_mode": "historical_raw_unadjusted",
        "start_date": "2026-07-01",
        "end_date": "2026-07-20",
        "symbols": ["600001"],
        "universe_scope": {
            "symbol_count": 1,
            "board_scope": "沪深主板+创业板",
            "excluded": ["ST", "科创板", "其他板块"],
        },
        "data_coverage": {"end_date_coverage_pct": 100.0},
        "variants": [
            {
                "variant_id": "v1",
                "label": "趋势·20日",
                "initial_cash": 100_000.0,
                "fills": [],
            }
        ],
    }

    validate_variant_artifact(payload, expected_end_date="2026-07-20")


def test_validate_variant_artifact_rejects_wrong_reset_date_and_missing_evidence():
    payload = {
        "schema_version": "variant-suite-v1",
        "data_mode": "historical_raw_unadjusted",
        "start_date": "2026-07-20",
        "end_date": "2026-07-20",
        "symbols": ["600001"],
        "universe_scope": {
            "symbol_count": 1,
            "board_scope": "沪深主板+创业板",
            "excluded": ["ST", "科创板", "其他板块"],
        },
        "data_coverage": {"end_date_coverage_pct": 100.0},
        "variants": [
            {
                "variant_id": "v1",
                "label": "趋势·20日",
                "initial_cash": 100_000.0,
                "fills": [
                    {"status": "filled", "evidence": []},
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match="start_date"):
        validate_variant_artifact(
            payload,
            expected_end_date="2026-07-20",
            expected_start_date="2026-07-21",
        )
    with pytest.raises(ValueError, match="技术证据"):
        validate_variant_artifact(
            payload,
            expected_end_date="2026-07-20",
            expected_start_date="2026-07-20",
        )
