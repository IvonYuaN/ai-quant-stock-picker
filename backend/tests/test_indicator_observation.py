from __future__ import annotations

from datetime import date, datetime, timedelta
import math

import pandas as pd

import live_risk_service


class _DailySource:
    name = "sqlite_db"

    def __init__(self, frame: pd.DataFrame, latest: date):
        self.frame = frame
        self.latest = latest

    def price_mode(self) -> str:
        return "raw"

    def get_latest_daily_date(self) -> date:
        return self.latest

    def fetch_daily(self, symbols, start, end, *, adjust):
        assert adjust == ""
        return {symbol: self.frame.copy() for symbol in symbols}


def test_indicator_snapshots_use_pit_qfq_prices_and_report_fresh_as_of(monkeypatch):
    as_of = date(2026, 10, 30)
    dates = [as_of - timedelta(days=19 - index) for index in range(20)]
    qfq_close = [50 + index / 10 for index in range(20)]
    frame = pd.DataFrame(
        {
            "date": [item.isoformat() for item in dates],
            "symbol": ["600519"] * 20,
            "open": [value * (2 if index < 12 else 1) for index, value in enumerate(qfq_close)],
            "high": [value * (2 if index < 12 else 1) + 1 for index, value in enumerate(qfq_close)],
            "low": [value * (2 if index < 12 else 1) - 1 for index, value in enumerate(qfq_close)],
            "close": [value * (2 if index < 12 else 1) for index, value in enumerate(qfq_close)],
            "volume": [1000.0] * 20,
            "amount": [100000.0] * 20,
            "adj_factor": [1.0 if index < 12 else 2.0 for index in range(20)],
        }
    )
    monkeypatch.setattr(
        live_risk_service, "build_data_source", lambda *_args, **_kwargs: _DailySource(frame, as_of)
    )
    monkeypatch.setattr(live_risk_service, "latest_completed_trading_day", lambda _checked: as_of)

    result = live_risk_service._indicator_snapshots(
        [{"symbol": "600519", "status": "active", "rule_type": "indicator", "metric": "rsi12"}],
        checked_at=datetime.fromisoformat("2026-10-30T16:00:00+08:00"),
    )

    snapshot = result[("600519", "rsi12")]
    assert snapshot.issue is None
    assert snapshot.source == "sqlite_db"
    assert snapshot.as_of == "2026-10-30"
    assert snapshot.value is not None and snapshot.value > 99


def test_indicator_snapshots_fail_closed_when_raw_daily_source_is_stale(monkeypatch):
    expected = date(2026, 10, 30)
    actual = date(2026, 10, 29)
    monkeypatch.setattr(
        live_risk_service,
        "build_data_source",
        lambda *_args, **_kwargs: _DailySource(pd.DataFrame(), actual),
    )
    monkeypatch.setattr(
        live_risk_service,
        "latest_completed_trading_day",
        lambda _checked: expected,
    )

    result = live_risk_service._indicator_snapshots(
        [{"symbol": "600519", "status": "active", "rule_type": "indicator", "metric": "rsi12"}],
        checked_at=datetime.fromisoformat("2026-10-30T16:00:00+08:00"),
    )

    snapshot = result[("600519", "rsi12")]
    assert snapshot.value is None
    assert snapshot.as_of == "2026-10-29"
    assert "陈旧" in str(snapshot.issue)


def test_indicator_snapshots_fail_closed_when_selected_symbol_lags_market_date(monkeypatch):
    expected = date(2026, 10, 30)
    symbol_latest = expected - timedelta(days=1)
    dates = [symbol_latest - timedelta(days=19 - index) for index in range(20)]
    closes = [50 + index / 10 for index in range(20)]
    frame = pd.DataFrame(
        {
            "date": [item.isoformat() for item in dates],
            "symbol": ["600519"] * 20,
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [1000.0] * 20,
            "amount": [100000.0] * 20,
            "adj_factor": [1.0] * 20,
        }
    )
    monkeypatch.setattr(
        live_risk_service,
        "build_data_source",
        lambda *_args, **_kwargs: _DailySource(frame, expected),
    )
    monkeypatch.setattr(
        live_risk_service,
        "latest_completed_trading_day",
        lambda _checked: expected,
    )

    result = live_risk_service._indicator_snapshots(
        [{"symbol": "600519", "status": "active", "rule_type": "indicator", "metric": "rsi12"}],
        checked_at=datetime.fromisoformat("2026-10-30T16:00:00+08:00"),
    )

    snapshot = result[("600519", "rsi12")]
    assert snapshot.value is None
    assert snapshot.as_of == "2026-10-29"
    assert "该标的日线陈旧" in str(snapshot.issue)


def _bars(count: int, as_of: date) -> pd.DataFrame:
    dates = [as_of - timedelta(days=count - 1 - index) for index in range(count)]
    closes = [50 + index / 10 for index in range(count)]
    return pd.DataFrame(
        {
            "date": [item.isoformat() for item in dates],
            "symbol": ["600519"] * count,
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [1000.0] * count,
            "amount": [100000.0] * count,
            "adj_factor": [1.0] * count,
        }
    )


def _install_source(monkeypatch, frame: pd.DataFrame, as_of: date) -> list[tuple[date, date]]:
    captured: list[tuple[date, date]] = []

    class RecordingSource(_DailySource):
        def fetch_daily(self, symbols, start, end, *, adjust):
            captured.append((start, end))
            return super().fetch_daily(symbols, start, end, adjust=adjust)

    monkeypatch.setattr(
        live_risk_service,
        "build_data_source",
        lambda *_args, **_kwargs: RecordingSource(frame, as_of),
    )
    monkeypatch.setattr(live_risk_service, "latest_completed_trading_day", lambda _checked: as_of)
    return captured


def _rules(*metrics: str) -> list[dict]:
    return [
        {"symbol": "600519", "status": "active", "rule_type": "indicator", "metric": metric}
        for metric in metrics
    ]


CHECKED_AT = datetime.fromisoformat("2026-10-30T16:00:00+08:00")


def test_indicator_snapshots_compute_each_metric_on_shared_pit_basis(monkeypatch):
    as_of = date(2026, 10, 30)
    captured = _install_source(monkeypatch, _bars(60, as_of), as_of)
    metrics = ("rsi12", "macd_hist", "atr14", "volume_ratio", "kdj_j")

    result = live_risk_service._indicator_snapshots(_rules(*metrics), checked_at=CHECKED_AT)

    assert set(result) == {("600519", metric) for metric in metrics}
    assert all(snapshot.issue is None for snapshot in result.values())
    assert all(
        snapshot.value is not None and math.isfinite(snapshot.value)
        for snapshot in result.values()
    )
    assert all(snapshot.source == "sqlite_db" and snapshot.as_of == as_of.isoformat() for snapshot in result.values())
    # The fetch window must cover the longest warm-up (macd_hist), not just RSI.
    assert captured and (captured[0][1] - captured[0][0]).days >= 36 * 2


def test_indicator_snapshots_fail_closed_for_unknown_metric_only(monkeypatch):
    as_of = date(2026, 10, 30)
    _install_source(monkeypatch, _bars(60, as_of), as_of)

    result = live_risk_service._indicator_snapshots(
        _rules("rsi12", "future_metric"), checked_at=CHECKED_AT
    )

    assert "不支持的监控指标" in str(result[("600519", "future_metric")].issue)
    assert result[("600519", "future_metric")].value is None
    assert result[("600519", "rsi12")].issue is None
    assert result[("600519", "rsi12")].value is not None


def test_indicator_snapshots_block_only_metrics_without_warmup(monkeypatch):
    as_of = date(2026, 10, 30)
    # 20 bars warm up RSI(12) but cannot warm up MACD(26/9).
    _install_source(monkeypatch, _bars(20, as_of), as_of)

    result = live_risk_service._indicator_snapshots(
        _rules("rsi12", "macd_hist"), checked_at=CHECKED_AT
    )

    assert result[("600519", "rsi12")].issue is None
    assert result[("600519", "rsi12")].value is not None
    assert "样本不足" in str(result[("600519", "macd_hist")].issue)
    assert "36 根" in str(result[("600519", "macd_hist")].issue)
