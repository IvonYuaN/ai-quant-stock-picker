from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from aqsp.data.cache import AdjustmentFactorRecord
import abnormal_history
from abnormal_history import build_historical_signal_snapshot


def _daily_frame(
    closes: list[float], volumes: list[float], *, name: str = "样例"
) -> pd.DataFrame:
    start = date(2026, 7, 1)
    return pd.DataFrame(
        {
            "date": [
                (start + timedelta(days=index)).isoformat()
                for index in range(len(closes))
            ],
            "name": name,
            "close": closes,
            "volume": volumes,
            "suspended": [False] * len(closes),
        }
    )


def _pit_factors(
    frame: pd.DataFrame,
    values: list[float] | None = None,
) -> dict[date, AdjustmentFactorRecord]:
    factor_values = values or [1.0] * len(frame)
    return {
        date.fromisoformat(str(day)): AdjustmentFactorRecord(
            date=date.fromisoformat(str(day)),
            value=value,
            source="fixture",
            fetched_at="2026-11-02T16:00:00+08:00",
        )
        for day, value in zip(frame["date"], factor_values, strict=True)
    }


def test_abnormal_history_computes_pit_sixty_session_extremes_and_previous_five_volume():
    closes = [float(index) for index in range(1, 61)] + [61.0]
    volumes = [100.0] * 60 + [200.0]
    descending = [float(index) for index in range(60, 0, -1)] + [0.5]
    frames = {
        "000001": _daily_frame(closes, volumes),
        "000002": _daily_frame(descending, [100.0] * 61, name="低点样例"),
        "000003": _daily_frame(
            closes[:-1] + [58.0], [100.0] * 60 + [199.0], name="非放量"
        ),
    }
    factors = {symbol: _pit_factors(frame) for symbol, frame in frames.items()}

    result = build_historical_signal_snapshot(
        frames,
        generated_at=datetime.fromisoformat("2026-11-02T16:00:00+08:00"),
        expected_as_of=date(2026, 7, 1) + timedelta(days=60),
        latest_data_date=date(2026, 7, 1) + timedelta(days=60),
        requested_symbols=3,
        volume_threshold=2.0,
        pit_adjustment_factors=factors,
    )

    assert result["freshness"] == "fresh"
    assert result["price_mode"] == "raw"
    assert result["adjustment_method"] == "pit_factor_ratio"
    assert result["counts"] == {"new_high": 1, "new_low": 1, "volume_surge": 1}
    high = next(row for row in result["rows"] if row["symbol"] == "000001")
    assert high["signals"] == ["new_high", "volume_surge"]
    assert high["high_60d"] == 60.0
    assert high["low_60d"] == 1.0
    low = next(row for row in result["rows"] if row["symbol"] == "000002")
    assert low["signals"] == ["new_low"]


def test_abnormal_history_skips_short_stale_and_suspended_latest_rows():
    latest_date = date(2026, 7, 1) + timedelta(days=60)
    complete = _daily_frame([float(i) for i in range(1, 61)] + [61.0], [100.0] * 61)
    short = complete.iloc[-20:].copy()
    stale = complete.iloc[:-1].copy()
    suspended = complete.copy()
    suspended.loc[suspended.index[-1], "suspended"] = True

    result = build_historical_signal_snapshot(
        {"000001": short, "000002": stale, "000003": suspended},
        generated_at=datetime.fromisoformat("2026-11-02T16:00:00+08:00"),
        expected_as_of=date(2026, 11, 2),
        latest_data_date=latest_date,
        requested_symbols=3,
        volume_threshold=2.0,
        pit_adjustment_factors={
            "000001": _pit_factors(complete),
            "000002": _pit_factors(stale),
            "000003": _pit_factors(suspended),
        },
    )

    assert result["freshness"] == "stale"
    assert result["as_of"] == latest_date.isoformat()
    assert result["rows"] == []
    assert result["eligible_symbols"] == 1
    assert result["skipped_stale_symbols"] == 2


def test_abnormal_history_suppresses_false_split_low_when_pit_factors_align_prices():
    closes = [124.0] * 60 + [62.0]
    frame = _daily_frame(closes, [100.0] * len(closes))
    factors = _pit_factors(frame, [1.0] * 60 + [2.0])

    result = build_historical_signal_snapshot(
        {"000001": frame},
        generated_at=datetime.fromisoformat("2026-11-02T16:00:00+08:00"),
        expected_as_of=date(2026, 8, 30),
        latest_data_date=date(2026, 8, 30),
        requested_symbols=1,
        volume_threshold=2.0,
        pit_adjustment_factors={"000001": factors},
    )

    assert result["counts"]["new_low"] == 0
    assert result["counts"]["new_high"] == 0
    assert result["rows"] == []


def test_abnormal_history_fails_closed_on_price_extremes_without_factor_coverage():
    frame = _daily_frame([float(i) for i in range(1, 61)] + [100.0], [100.0] * 61)

    result = build_historical_signal_snapshot(
        {"000001": frame},
        generated_at=datetime.fromisoformat("2026-11-02T16:00:00+08:00"),
        expected_as_of=date(2026, 8, 30),
        latest_data_date=date(2026, 8, 30),
        requested_symbols=1,
        volume_threshold=2.0,
        pit_adjustment_factors={},
    )

    assert result["counts"]["new_high"] == 0
    assert result["skipped_missing_factor_symbols"] == 1
    assert result["rows"] == []


def test_abnormal_history_rejects_invalid_volume_threshold():
    try:
        build_historical_signal_snapshot(
            {},
            generated_at=datetime.fromisoformat("2026-11-02T16:00:00+08:00"),
            expected_as_of=date(2026, 11, 2),
            latest_data_date=date(2026, 11, 2),
            requested_symbols=0,
            volume_threshold=float("nan"),
        )
    except ValueError as exc:
        assert "finite and positive" in str(exc)
    else:
        raise AssertionError("invalid volume threshold must fail closed")


def test_abnormal_history_scans_stale_source_through_its_actual_latest_date(
    monkeypatch,
):
    source_latest = date(2026, 8, 30)
    expected_latest = date(2026, 11, 2)
    frames = {
        "000001": _daily_frame([float(i) for i in range(1, 62)], [100.0] * 61)
    }

    class StaleRawSource:
        name = "sqlite_db"

        def set_workload(self, workload: str) -> None:
            assert workload == "historical"

        def price_mode(self) -> str:
            return "raw"

        def get_latest_daily_date(self) -> date:
            return source_latest

        def get_available_symbols(self) -> list[str]:
            return ["000001"]

        def get_point_in_time_adjustment_factors(self, symbols, dates):
            assert symbols == ["000001"]
            return {"000001": _pit_factors(frames["000001"])}

        def fetch_daily(self, symbols, start, end, *, adjust):
            assert symbols == ["000001"]
            assert end == source_latest
            return frames

    monkeypatch.setattr(
        abnormal_history.source_factory,
        "build_data_source",
        lambda *_args, **_kwargs: StaleRawSource(),
    )
    monkeypatch.setattr(
        abnormal_history,
        "now_shanghai",
        lambda: datetime.fromisoformat("2026-11-02T16:00:00+08:00"),
    )
    monkeypatch.setattr(
        abnormal_history, "latest_completed_trading_day", lambda _now: expected_latest
    )
    monkeypatch.setattr(
        abnormal_history,
        "load_thresholds",
        lambda: SimpleNamespace(
            closing_premium=SimpleNamespace(volume_signal_ratio=2.0)
        ),
    )

    result = abnormal_history.scan_local_daily_signals()

    assert result["as_of"] == source_latest.isoformat()
    assert result["expected_as_of"] == expected_latest.isoformat()
    assert result["freshness"] == "stale"
