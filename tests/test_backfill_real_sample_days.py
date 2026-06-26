from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from aqsp.core.types import PickResult
from scripts import backfill_real_sample_days as backfill


def test_build_backfill_plan_keeps_days_missing_signal_or_paper(monkeypatch) -> None:
    monkeypatch.setattr(
        backfill,
        "is_trading_day",
        lambda day: day.weekday() < 5,
    )

    plan = backfill.build_backfill_plan(
        start_date=date(2026, 6, 2),
        end_date=date(2026, 6, 8),
        existing_signal_days={"2026-06-03"},
        existing_paper_days={"2026-06-04"},
        max_days=10,
    )

    assert [day.isoformat() for day in plan.trading_days] == [
        "2026-06-02",
        "2026-06-03",
        "2026-06-04",
        "2026-06-05",
        "2026-06-08",
    ]
    assert plan.missing_signal_days == {
        "2026-06-02",
        "2026-06-04",
        "2026-06-05",
        "2026-06-08",
    }
    assert plan.missing_paper_days == {
        "2026-06-02",
        "2026-06-03",
        "2026-06-05",
        "2026-06-08",
    }


def test_collect_signal_days_treats_no_pick_marker_as_attempted_day(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    ledger.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "signal_date": "2026-06-02",
                        "symbol": "__RUN__",
                        "status": backfill.BACKFILL_NO_PICKS_STATUS,
                    }
                ),
                json.dumps(
                    {
                        "signal_date": "2026-06-03",
                        "symbol": "__RUN__",
                        "status": "blocked_by_circuit_breaker",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert backfill.collect_signal_days(ledger) == {"2026-06-02"}


def test_truncate_frames_to_date_filters_future_rows_and_keeps_tail() -> None:
    frame = pd.DataFrame(
        [
            {"date": "2026-06-01", "close": 1.0},
            {"date": "2026-06-02", "close": 2.0},
            {"date": "2026-06-03", "close": 3.0},
            {"date": "2026-06-04", "close": 4.0},
        ]
    )

    trimmed = backfill.truncate_frames_to_date(
        {"600000": frame},
        end_date=date(2026, 6, 3),
        lookback_days=2,
    )

    assert trimmed["600000"]["date"].tolist() == ["2026-06-02", "2026-06-03"]


def test_fetch_history_window_filters_symbols_by_coverage() -> None:
    seen: dict[str, object] = {}

    class DummySource:
        def get_symbols_with_daily_coverage(self, symbols, start, end, min_rows=None):
            seen["coverage"] = (list(symbols), start, end, min_rows)
            return ["600519"]

        def fetch_daily(self, symbols, start, end, adjust=""):
            seen["fetch"] = (list(symbols), start, end, adjust)
            return {"600519": pd.DataFrame([{"date": "2026-06-23", "close": 1.0}])}

    out = backfill.fetch_history_window(
        source=DummySource(),
        symbols=["600519", "688981"],
        signal_day=date(2026, 6, 23),
        lookback_days=120,
        future_buffer_days=2,
    )

    assert list(out.keys()) == ["600519"]
    assert seen["coverage"][0] == ["600519", "688981"]
    assert seen["coverage"][3] == 1
    assert seen["fetch"][0] == ["600519"]


def test_collect_paper_sync_symbols_dedupes_and_skips_run_marker(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    ledger_path.write_text(
        "\n".join(
            [
                json.dumps({"symbol": "__RUN__", "signal_date": "2026-06-02"}),
                json.dumps({"symbol": "600000", "signal_date": "2026-06-02"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text(
        json.dumps({"symbol": "600519", "signal_date": "2026-06-03"}) + "\n",
        encoding="utf-8",
    )

    picks = [
        PickResult(
            symbol="600000",
            name="浦发银行",
            date="2026-06-04",
            close=10.0,
            score=1.0,
            rating="watch",
            entry_type="next_open",
            ideal_buy=10.0,
            stop_loss=9.5,
            take_profit=11.0,
            position="10%",
        ),
        PickResult(
            symbol="601318",
            name="中国平安",
            date="2026-06-04",
            close=20.0,
            score=2.0,
            rating="watch",
            entry_type="next_open",
            ideal_buy=20.0,
            stop_loss=19.0,
            take_profit=22.0,
            position="10%",
        ),
    ]

    symbols = backfill.collect_paper_sync_symbols(
        ledger_path=ledger_path,
        paper_ledger_path=paper_path,
        new_picks=picks,
    )

    assert symbols == ["600000", "600519", "601318"]


def test_collect_paper_sync_symbols_can_scope_to_signal_dates(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "predictions.jsonl"
    paper_path = tmp_path / "paper_trades.jsonl"
    ledger_path.write_text(
        "\n".join(
            [
                json.dumps({"symbol": "600000", "signal_date": "2026-06-02"}),
                json.dumps({"symbol": "600001", "signal_date": "2026-06-03"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    paper_path.write_text(
        "\n".join(
            [
                json.dumps({"symbol": "600519", "signal_date": "2026-06-02"}),
                json.dumps({"symbol": "000001", "signal_date": "2026-06-04"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    symbols = backfill.collect_paper_sync_symbols(
        ledger_path=ledger_path,
        paper_ledger_path=paper_path,
        new_picks=[],
        signal_dates={"2026-06-02"},
    )

    assert symbols == ["600000", "600519"]


def test_resolve_backfill_symbols_uses_sqlite_source_directly() -> None:
    class DummySqliteSource:
        def get_available_symbols(self):
            return ["600000", "600519", "601318"]

        def get_symbols_with_daily_coverage(self, symbols, start, end, min_rows=None):
            assert min_rows is None
            assert start == date(2025, 8, 17)
            assert end == date(2026, 6, 23)
            return symbols[:2]

    symbols = backfill.resolve_backfill_symbols(
        source_name="sqlite_db",
        source=DummySqliteSource(),
        explicit_symbols="",
        pool_name="",
        signal_day=date(2026, 6, 23),
        max_universe=0,
        min_avg_amount=50_000_000,
        lookback_days=130,
    )

    assert symbols == ["600000", "600519"]


def test_history_window_start_scales_with_lookback() -> None:
    assert backfill._history_window_start(date(2026, 6, 23), 120) == date(2025, 8, 27)
    assert backfill._history_window_start(date(2026, 6, 23), 260) == date(2025, 4, 9)


def test_build_backfill_plan_trims_missing_sets_with_max_days(monkeypatch) -> None:
    monkeypatch.setattr(
        backfill,
        "is_trading_day",
        lambda day: day.weekday() < 5,
    )

    plan = backfill.build_backfill_plan(
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 5),
        existing_signal_days={"2026-06-02"},
        existing_paper_days=set(),
        max_days=2,
    )

    assert [day.isoformat() for day in plan.trading_days] == [
        "2026-06-04",
        "2026-06-05",
    ]
    assert plan.missing_signal_days == {"2026-06-04", "2026-06-05"}
    assert plan.missing_paper_days == {"2026-06-04", "2026-06-05"}


def test_screen_backfill_picks_batches_and_keeps_global_top_n(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_fetch_history_window(**kwargs):
        batch = list(kwargs["symbols"])
        calls.append(batch)
        return {
            symbol: pd.DataFrame(
                [{"date": "2026-04-01", "symbol": symbol, "name": symbol, "close": 1.0}]
            )
            for symbol in batch
        }

    def fake_truncate_frames_to_date(frames, **_kwargs):
        return frames

    def fake_screen(screen_frames, _config, _thresholds):
        picks = []
        for symbol in screen_frames:
            score = float(symbol.replace("S", ""))
            picks.append(
                PickResult(
                    symbol=symbol,
                    name=symbol,
                    date="2026-04-01",
                    close=10.0,
                    score=score,
                    rating="watch",
                    entry_type="next_open",
                    ideal_buy=10.0,
                    stop_loss=9.5,
                    take_profit=11.0,
                    position="10%",
                )
            )
        return picks

    monkeypatch.setattr(backfill, "fetch_history_window", fake_fetch_history_window)
    monkeypatch.setattr(
        backfill, "truncate_frames_to_date", fake_truncate_frames_to_date
    )
    monkeypatch.setattr(backfill, "_screen_universe_with_thresholds", fake_screen)
    monkeypatch.setattr(
        backfill, "_drop_benchmark_frame", lambda frames, _benchmark: frames
    )
    monkeypatch.setattr(backfill, "_enrich_pick_names", lambda picks, _frames: picks)

    picks, pick_frames = backfill.screen_backfill_picks(
        source=object(),
        symbols=["S1", "S2", "S3", "S4", "S5"],
        signal_day=date(2026, 4, 1),
        lookback_days=120,
        future_buffer_days=2,
        benchmark_symbol="000300",
        thresholds=object(),
        config=backfill.ScreeningConfig(),
        limit=2,
        batch_size=2,
    )

    assert calls == [["S1", "S2"], ["S3", "S4"], ["S5"]]
    assert [pick.symbol for pick in picks] == ["S5", "S4"]
    assert sorted(pick_frames) == ["S1", "S2", "S3", "S4", "S5"]


def test_screen_backfill_picks_reuses_prefetched_first_batch(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_fetch_history_window(**kwargs):
        batch = list(kwargs["symbols"])
        calls.append(batch)
        return {
            symbol: pd.DataFrame(
                [{"date": "2026-04-01", "symbol": symbol, "name": symbol, "close": 1.0}]
            )
            for symbol in batch
        }

    def fake_screen(screen_frames, _config, _thresholds):
        return [
            PickResult(
                symbol=symbol,
                name=symbol,
                date="2026-04-01",
                close=10.0,
                score=float(symbol.replace("S", "")),
                rating="watch",
                entry_type="next_open",
                ideal_buy=10.0,
                stop_loss=9.5,
                take_profit=11.0,
                position="10%",
            )
            for symbol in screen_frames
        ]

    monkeypatch.setattr(backfill, "fetch_history_window", fake_fetch_history_window)
    monkeypatch.setattr(backfill, "_screen_universe_with_thresholds", fake_screen)
    monkeypatch.setattr(
        backfill, "_drop_benchmark_frame", lambda frames, _benchmark: frames
    )
    monkeypatch.setattr(backfill, "_enrich_pick_names", lambda picks, _frames: picks)

    picks, _pick_frames = backfill.screen_backfill_picks(
        source=object(),
        symbols=["S1", "S2", "S3", "S4"],
        signal_day=date(2026, 4, 1),
        lookback_days=120,
        future_buffer_days=2,
        benchmark_symbol="000300",
        thresholds=object(),
        config=backfill.ScreeningConfig(),
        limit=4,
        batch_size=2,
        prefetched_frames={
            "S1": pd.DataFrame(
                [{"date": "2026-04-01", "symbol": "S1", "name": "S1", "close": 1.0}]
            ),
            "S2": pd.DataFrame(
                [{"date": "2026-04-01", "symbol": "S2", "name": "S2", "close": 1.0}]
            ),
        },
    )

    assert calls == [["S3", "S4"]]
    assert [pick.symbol for pick in picks] == ["S4", "S3", "S2", "S1"]
