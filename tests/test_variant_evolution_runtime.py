import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from aqsp.data.sqlite_db_source import SqliteDbSource
from scripts.refresh_sqlite_batch import _persist_target_snapshot, _refresh_universe
from scripts.refresh_variant_results_from_market_db import (
    MarketSymbol,
    attach_discussion_links,
    evolution_profiles,
    prioritize_focus_symbols,
)
from scripts.run_variant_suite import (
    VariantProfile,
    _current_holding_technical_evidence,
    assign_variant_lifecycle,
)


def _profile() -> VariantProfile:
    return VariantProfile(
        variant_id="trend_base",
        label="趋势基线",
        lookback=20,
        entry_return_pct=1.5,
        max_bias_pct=10.0,
        mode="trend",
        max_positions=3,
        position_weight=1 / 3,
        hypothesis="趋势延续",
    )


def test_refresh_universe_uses_latest_available_day_when_database_is_stale(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "stale.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            "CREATE TABLE daily_qfq (ts_code TEXT, trade_date TEXT, amount REAL)"
        )
        conn.executemany(
            "INSERT INTO stocks VALUES (?, ?)",
            [("000001.SZ", "平安银行"), ("600000.SH", "浦发银行")],
        )
        conn.executemany(
            "INSERT INTO daily_qfq VALUES (?, ?, ?)",
            [("000001.SZ", "20260812", 1.0), ("600000.SH", "20260812", 1.0)],
        )
        conn.execute("INSERT INTO daily_qfq VALUES ('000001.SZ', '20260813', 1.0)")

    result = _refresh_universe(
        SqliteDbSource(db_path=db_path, cache=None),
        universe_limit=0,
        reference_day=date(2026, 8, 13),
    )

    assert result == ["600000", "000001"]


def test_target_snapshot_persists_prices_with_decimal_precision(tmp_path: Path) -> None:
    db_path = tmp_path / "market.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """CREATE TABLE daily_qfq (
                ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL,
                close_qfq REAL, volume INTEGER, amount REAL, close REAL,
                UNIQUE(ts_code, trade_date)
            )"""
        )
        conn.execute("INSERT INTO stocks VALUES ('600000.SH', '浦发银行')")

    inserted_symbols = _persist_target_snapshot(
        db_path=db_path,
        target_day=date(2026, 8, 14),
        snapshot=pd.DataFrame(
            [
                {
                    "symbol": "600000",
                    "name": "浦发银行",
                    "open": 12.31,
                    "high": 12.66,
                    "low": 12.22,
                    "close": 12.58,
                    "volume": 1000,
                    "amount": 12580.5,
                }
            ]
        ),
        eligible_symbols={"600000"},
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT open, high, low, close, amount FROM daily_qfq"
        ).fetchone()
    assert inserted_symbols == ["600000"]
    assert row == (12.31, 12.66, 12.22, 12.58, 12580.5)


def test_variant_refresh_rejects_runtime_import_failure() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "variant_refresh.sh").read_text(
        encoding="utf-8"
    )

    assert 'export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}' in script
    assert "交易日检查失败，拒绝把运行错误当成非交易日" in script


def test_variant_refresh_does_not_accept_stale_same_day_artifact() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "variant_refresh.sh").read_text(
        encoding="utf-8"
    )

    assert 'status.get("status") == "completed"' in script
    assert "if refresh_published &&" in script
    assert '"$OUTPUT_PATH" --expected-end "$(date +%F)"' in script


def test_current_holding_evidence_tolerates_sparse_historical_volume() -> None:
    dates = pd.date_range("2026-06-15", periods=41, freq="B").strftime("%Y-%m-%d")
    frame = pd.DataFrame(
        {
            "date": dates,
            "volume": [None, *([1000.0] * 39), 1200.0],
            "volume_ratio": [None] * 41,
            "ret": [5.0] * 41,
            "bias": [2.0] * 41,
            "macd_hist": [0.1] * 41,
            "kdj_j": [55.0] * 41,
            "atr_pct": [3.0] * 41,
            "close": [10.5] * 41,
        }
    )

    evidence = _current_holding_technical_evidence(
        [{"symbol": "600228", "quantity": 100}],
        {"600228": frame},
        _profile(),
        dates[-1],
        {"600228": "返利科技"},
    )

    assert evidence[0]["volume_ratio"] == 1.2


def test_variant_lifecycle_eliminates_negative_result_when_samples_sufficient() -> None:
    results = [
        {
            "variant_id": "loser",
            "rank": 5,
            "return_pct": -3.0,
            "independent_signal_days": 35,
            "strategy": {
                "entry_return_pct": 1.0,
                "max_bias_pct": 10.0,
                "max_positions": 4,
            },
        }
    ]

    assign_variant_lifecycle(results)

    assert results[0]["lifecycle_status"] == "淘汰"
    assert results[0]["next_generation"]["max_positions"] == 3


def test_variant_evolution_replaces_eliminated_parent_in_next_generation() -> None:
    previous = {
        "variants": [
            {
                "variant_id": "trend_base",
                "label": "趋势基线",
                "generation": 1,
                "lifecycle_status": "淘汰",
                "strategy": {
                    "id": "trend_base",
                    "lookback_days": 20,
                    "entry_return_pct": 1.5,
                    "max_bias_pct": 10.0,
                    "mode": "trend",
                    "max_positions": 3,
                    "hypothesis": "趋势延续",
                },
                "next_generation": {
                    "generation": 2,
                    "entry_return_pct": 2.0,
                    "max_bias_pct": 9.0,
                    "max_positions": 2,
                },
            }
        ]
    }

    evolved = evolution_profiles((_profile(),), previous)

    assert evolved[0].variant_id == "trend_base__g2"
    assert evolved[0].parent_variant_id == "trend_base"
    assert evolved[0].max_bias_pct == 9.0


def test_variant_batch_forces_discussed_candidates_into_bounded_pool() -> None:
    eligible = tuple(
        MarketSymbol(f"{index:06d}.SZ", f"{index:06d}", str(index), "深市主板")
        for index in range(5)
    )
    batch = eligible[:3]

    selected = prioritize_focus_symbols(
        batch,
        eligible,
        ({"symbol": "000004", "strategies": ["ma_pullback"]},),
    )

    assert [item.symbol for item in selected] == ["000004", "000000", "000001"]


def test_variant_discussion_links_only_matching_strategy_modes() -> None:
    payload: dict[str, object] = {
        "variants": [
            {"strategy": {"mode": "pullback"}},
            {"strategy": {"mode": "volume_breakout"}},
        ]
    }
    cohort = (
        {
            "symbol": "000001",
            "display_name": "000001 平安银行",
            "strategies": ["ma_pullback"],
            "risk_gate": "跌破防守位失效",
        },
    )

    attach_discussion_links(payload, cohort)

    variants = payload["variants"]
    assert isinstance(variants, list)
    assert variants[0]["discussion_links"][0]["symbol"] == "000001"
    assert variants[1]["discussion_links"] == []
