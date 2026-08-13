import json
import sqlite3
from collections import Counter
from datetime import date, timedelta
from types import SimpleNamespace

from scripts import run_variant_suite as variant_suite
from scripts.check_variant_results import validate_variant_results
from scripts.run_variant_suite import run_suite


def test_adjustments_prioritizes_all_position_changes_over_retained_holdings() -> None:
    holdings = [{"symbol": f"{index:06d}", "quantity": 100} for index in range(1, 10)]
    previous = [{"symbol": f"{index:06d}", "quantity": 100} for index in range(1, 8)]
    names = {item["symbol"]: item["symbol"] for item in holdings}

    lines = variant_suite._adjustments(
        holdings, previous, SimpleNamespace(fills=()), names
    )

    assert len(lines) == 8
    assert lines[0].startswith("买入 000008")
    assert lines[1].startswith("买入 000009")


def test_generate_variant_profiles_stays_diverse_and_explained() -> None:
    profiles = variant_suite.generate_variant_profiles({})

    signatures = {
        (
            profile.mode,
            profile.lookback,
            profile.entry_return_pct,
            profile.max_bias_pct,
            profile.max_positions,
            profile.position_weight,
        )
        for profile in profiles
    }

    assert len(profiles) == 24
    assert len({profile.variant_id for profile in profiles}) == len(profiles)
    assert len(signatures) == len(profiles)
    assert len({profile.mode for profile in profiles}) >= 12
    assert all(profile.hypothesis for profile in profiles)


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
    assert len(variants) >= 20
    assert {item["initial_cash"] for item in variants} == {100_000.0}
    assert len({item["variant_id"] for item in variants}) == len(variants)
    assert len({item["strategy_signature"] for item in variants}) == len(variants)
    assert len({item["holdings_signature"] for item in variants}) > 1
    assert (
        Counter(item["holdings_signature"] for item in variants[:12]).most_common(1)[0][
            1
        ]
        <= 1
    )
    assert all("cash" in item and "total_pnl" in item for item in variants)
    assert all(item["strategy"]["hypothesis"] for item in variants)
    assert all(item["holdings_date"] == "2026-03-31" for item in variants)
    assert all(item["previous_holdings_date"] == "2026-03-30" for item in variants)
    assert all("previous_holdings" in item for item in variants)
    assert all(item["adjustments"] for item in variants)
    assert all(item["technical_evidence"] for item in variants if item["filled_orders"])
    assert any(
        all(
            key in evidence for key in ("macd_hist", "kdj_j", "volume_ratio", "atr_pct")
        )
        for item in variants
        for evidence in item["technical_evidence"]
    )
    assert any(
        holding.get("entry_evidence")
        for item in variants
        for holding in item["holdings"]
    )
    assert all("orders_signature" in item for item in variants)
    assert all("filled_orders_signature" in item for item in variants)
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
    held_variant = next(item for item in variants if item["holdings"])
    evidence = held_variant["technical_evidence"]
    assert {item["symbol"] for item in evidence} == {
        item["symbol"] for item in held_variant["holdings"]
    }
    assert all(item.get("name") for item in held_variant["holdings"])
    assert all(item.get("name") for item in evidence)
    assert {item["date"] for item in evidence} == {result["end_date"]}
    assert {item["evidence_kind"] for item in evidence} == {"current_holding_snapshot"}
    for item in evidence:
        assert all(
            isinstance(item[key], float)
            for key in ("macd_hist", "kdj_j", "volume_ratio", "atr_pct")
        )
    artifact = tmp_path / "variant_results.json"
    production_payload = {
        **result,
        "universe": {
            "supported_symbols": len(symbols),
            "selected_symbols": len(symbols),
        },
    }
    artifact.write_text(
        json.dumps(production_payload, ensure_ascii=False), encoding="utf-8"
    )
    validated = validate_variant_results(
        artifact,
        expected_end="2026-03-31",
        min_symbols=1,
    )
    assert validated.variants == len(variants)


def test_variant_suite_loads_raw_frames_from_daily_qfq_schema(tmp_path) -> None:
    db = tmp_path / "raw-rebuild.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """
            CREATE TABLE daily_qfq (
                ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close_qfq REAL,
                volume REAL, amount REAL, open_qfq REAL, high_qfq REAL,
                low_qfq REAL, close REAL,
                UNIQUE(ts_code, trade_date)
            )
            """
        )
        conn.execute("INSERT INTO stocks VALUES (?, ?)", ("000001.SZ", "平安银行"))
        conn.executemany(
            "INSERT INTO daily_qfq VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "000001.SZ",
                    f"202601{day:02d}",
                    10.0 + day,
                    10.5 + day,
                    9.5 + day,
                    None,
                    100000.0,
                    (10.0 + day) * 100000.0,
                    None,
                    None,
                    None,
                    10.0 + day,
                )
                for day in range(1, 4)
            ],
        )

    frames = variant_suite.load_frames(db, ("000001",), "2026-01-01", "2026-01-03")

    assert list(frames) == ["000001"]
    frame = frames["000001"]
    assert frame["name"].tolist() == ["平安银行"] * 3
    assert frame["date"].tolist() == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert frame["close"].tolist() == [11.0, 12.0, 13.0]


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
    assert result["variants"]
    evidence = variant_suite._technical_evidence_values(
        profile=variant_suite.VariantProfile(
            "flat_probe", "平线证据", 20, 1.0, 5.0, "kdj_rebound", 3, 1 / 3, "测试"
        ),
        symbol="FLAT",
        signal_date="2026-01-10",
        execution_date="2026-01-11",
        side="buy",
        ret=1.0,
        bias=0.5,
        macd_hist=0.1,
        kdj_j=variant_suite._optional_metric(float("nan")),
        volume_ratio=1.0,
        atr_pct=0.0,
        score=1.0,
    )
    assert evidence["kdj_j"] is None
    assert evidence["kdj_available"] is False


def test_run_suite_reuses_indicator_frames_by_lookback_when_profiles_share_inputs(
    tmp_path, monkeypatch
):
    db = tmp_path / "cached.db"
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
        for symbol, base in {"AAA": 10.0, "BBB": 20.0}.items():
            for index in range(80):
                current = start + timedelta(days=index)
                close = base + index * 0.08 + ((index % 5) - 2) * 0.03
                rows.append(
                    (
                        symbol,
                        current.isoformat(),
                        symbol,
                        "raw",
                        "historical",
                        close,
                        close + 0.2,
                        close - 0.2,
                        close,
                        100000.0 + index * 1000.0,
                        close * 100000.0,
                        0,
                        close * 1.1,
                        close * 0.9,
                    )
                )
        conn.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    calls: Counter[tuple[str, int]] = Counter()
    original = variant_suite._with_indicators

    def counted(raw, lookback):
        calls[(str(raw["name"].iloc[0]), lookback)] += 1
        return original(raw, lookback)

    monkeypatch.setattr(variant_suite, "_with_indicators", counted)
    result = variant_suite.run_suite(db, ("AAA", "BBB"), "2026-01-01", "2026-03-21")

    assert result["schema_version"] == "variant-suite-v2"
    assert (
        len(calls)
        == len(
            {
                profile.lookback
                for profile in variant_suite.generate_variant_profiles({})
            }
        )
        * 2
    )
    assert set(calls.values()) == {1}
