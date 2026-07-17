from __future__ import annotations

import pandas as pd
import pytest

from aqsp.indicators import enrich_indicators
from aqsp.models import ScreeningConfig
from aqsp.internet_strategies import evaluate_strategy_signals
from aqsp.strategies.thresholds import (
    InternetStrategyThresholds,
    NReboundThresholds,
    ScoringThresholds,
    Thresholds,
    load_thresholds,
)
from aqsp.strategy import (
    _entry_type,
    apply_candidate_quality_gate,
    assess_short_term_quality,
    score_symbol,
    screen_universe,
    strategy_weights_for_regime,
)


def test_short_term_quality_requires_independent_evidence_and_score() -> None:
    scoring = ScoringThresholds()

    quality = assess_short_term_quality(
        score=48.0,
        rating="buy_candidate",
        ret5_pct=2.0,
        ret20_pct=13.0,
        volume_ratio=1.2,
        rsi12=55.0,
        bias20_pct=3.0,
        ma_trend=True,
        ma_slope_up=True,
        macd_improving=False,
        near_high_confirmed=False,
        pullback_confirmed=False,
        scoring=scoring,
    )

    assert quality.action == "blocked"
    assert quality.paper_review_eligible is False
    assert any("观察门槛" in reason for reason in quality.reasons)


def test_short_term_quality_downgrades_recent_drawdown_to_observation() -> None:
    quality = assess_short_term_quality(
        score=72.0,
        rating="strong_buy_candidate",
        ret5_pct=-13.0,
        ret20_pct=18.0,
        volume_ratio=1.2,
        rsi12=55.0,
        bias20_pct=4.0,
        ma_trend=True,
        ma_slope_up=True,
        macd_improving=True,
        near_high_confirmed=False,
        pullback_confirmed=False,
        scoring=ScoringThresholds(),
    )

    assert quality.action == "observe"
    assert quality.paper_review_eligible is False
    assert any("近5日动量偏弱" in reason for reason in quality.reasons)


def test_apply_candidate_quality_gate_removes_blocked_and_marks_observation() -> None:
    from aqsp.core.types import PickResult

    def pick(symbol: str, action: str) -> PickResult:
        return PickResult(
            symbol=symbol,
            name=symbol,
            date="2026-07-17",
            close=10.0,
            score=60.0,
            rating="buy_candidate",
            entry_type="relative_strength",
            ideal_buy=10.0,
            stop_loss=9.0,
            take_profit=12.0,
            position="10%-20%",
            metrics={"quality_gate_status": action, "quality_gate_reasons": ("test",)},
        )

    kept = apply_candidate_quality_gate([pick("OBS", "observe"), pick("BAD", "blocked")])

    assert [item.symbol for item in kept] == ["OBS"]
    assert kept[0].metrics["paper_review_eligible"] is False
    assert kept[0].metrics["quality_gate_action"] == "observe"


def _frame(symbol: str, drift: float, volume_boost: float = 1.0) -> pd.DataFrame:
    rows = []
    close = 10.0
    for i in range(120):
        close *= 1 + drift
        if i == 119:
            close *= 1.025
        open_ = close * 0.99
        high = close * 1.02
        low = close * 0.985
        volume = 1_000_000 * (volume_boost if i == 119 else 1)
        rows.append(
            {
                "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=i),
                "symbol": symbol,
                "name": symbol,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": close * volume * 100,
            }
        )
    frame = pd.DataFrame(rows)
    frame.attrs.update({"source_name": "synthetic", "workload": "walkforward"})
    return frame


def test_intraday_volume_ratio_uses_elapsed_minutes_and_prior_complete_days() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-07-09", periods=6, freq="B"),
            "open": [10.0] * 6,
            "high": [10.2] * 6,
            "low": [9.8] * 6,
            "close": [10.0] * 6,
            "volume": [1_000.0] * 5 + [600.0],
            "amount": [1_000_000.0] * 6,
            "intraday_elapsed_minutes": [None] * 5 + [120],
            "intraday_session_minutes": [None] * 5 + [240],
        }
    )

    enriched = enrich_indicators(frame)

    assert enriched.iloc[-1]["volume_ratio"] == pytest.approx(1.2)


def _n_rebound_frame() -> pd.DataFrame:
    base_closes = [9.7 + i * 0.015 for i in range(20)]
    pattern_closes = [
        10.0,
        10.05,
        10.08,
        10.12,
        10.18,
        10.22,
        10.28,
        10.35,
        10.42,
        10.48,
        10.55,
        10.65,
        11.72,
        11.45,
        11.34,
        11.33,
        11.34,
        11.35,
        11.35,
        11.34,
        11.34,
        11.34,
    ]
    closes = base_closes + pattern_closes
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    base_volumes = [1_000_000] * len(base_closes)
    pattern_volumes = [
        1_000_000,
        1_010_000,
        1_000_000,
        1_020_000,
        1_030_000,
        1_040_000,
        1_050_000,
        1_060_000,
        1_050_000,
        1_040_000,
        1_020_000,
        1_100_000,
        2_400_000,
        1_100_000,
        980_000,
        920_000,
        900_000,
        880_000,
        850_000,
        820_000,
        800_000,
        780_000,
    ]
    volumes = base_volumes + pattern_volumes
    frame = pd.DataFrame(
        {
            "date": dates,
            "symbol": "NREB",
            "name": "NREB",
            "open": [price * 0.99 for price in closes],
            "high": [price * 1.01 for price in closes],
            "low": [price * 0.985 for price in closes],
            "close": closes,
            "volume": volumes,
            "amount": [price * volume * 100 for price, volume in zip(closes, volumes)],
        }
    )
    frame.attrs.update({"source_name": "synthetic", "workload": "walkforward"})
    return frame


def test_screen_prefers_strong_trend() -> None:
    frames = {
        "GOOD": _frame("GOOD", 0.004, 1.8),
        "BAD": _frame("BAD", -0.002, 1.0),
    }
    picks = screen_universe(frames, ScreeningConfig(min_avg_amount=1))
    assert picks
    assert picks[0].symbol == "GOOD"
    assert picks[0].score > 50


def test_low_liquidity_penalty() -> None:
    frames = {"LOW": _frame("LOW", 0.004, 1.5)}
    picks = screen_universe(frames, ScreeningConfig(min_avg_amount=10**12))
    assert picks[0].score < 55
    assert any("流动性" in risk for risk in picks[0].risks)


def test_score_symbol_rejects_missing_source_provenance() -> None:
    frame = _frame("UNVERIFIED", 0.004, 1.8)
    frame.attrs.clear()

    pick = score_symbol(
        "UNVERIFIED",
        frame,
        ScreeningConfig(min_avg_amount=1),
        ScoringThresholds(),
    )

    assert pick is None


def test_score_symbol_rejects_stale_realtime_source() -> None:
    frame = _frame("STALE", 0.004, 1.8)
    frame.attrs.update({"source_name": "eastmoney", "workload": "live_short"})

    pick = score_symbol(
        "STALE",
        frame,
        ScreeningConfig(min_avg_amount=1),
        ScoringThresholds(),
    )

    assert pick is None


def test_score_symbol_preserves_runtime_source_provenance() -> None:
    frame = _frame("TRACEABLE", 0.004, 1.8)
    frame.attrs.update(
        {
            "source_name": "eastmoney",
            "fetched_at": "2026-07-16T13:05:00+08:00",
            "timestamp_source": "bar_time",
            "historical_source": "sina",
            "workload": "walkforward",
        }
    )

    pick = score_symbol(
        "TRACEABLE",
        frame,
        ScreeningConfig(min_avg_amount=1),
        ScoringThresholds(),
    )

    assert pick is not None
    assert pick.metrics["data_source"] == "eastmoney"
    assert pick.metrics["data_fetched_at"] == "2026-07-16T13:05:00+08:00"
    assert pick.metrics["data_timestamp_source"] == "bar_time"
    assert pick.metrics["historical_data_source"] == "sina"


def test_liquidity_penalty_uses_scoring_threshold() -> None:
    frame = _frame("LOW", 0.004, 1.5)
    config = ScreeningConfig(min_avg_amount=10**12)

    baseline = score_symbol("LOW", frame, config, ScoringThresholds())
    milder = score_symbol(
        "LOW",
        frame,
        config,
        ScoringThresholds(liquidity_penalty=-5),
    )

    assert baseline is not None
    assert milder is not None
    assert milder.score > baseline.score


def test_score_symbol_caps_position_text_by_configured_max_position() -> None:
    frame = _frame("CAP", 0.004, 1.8)
    result = score_symbol(
        "CAP",
        frame,
        ScreeningConfig(min_avg_amount=1, max_position_pct=0.12),
        ScoringThresholds(position_strong_score=1, position_strong_risks=99),
        Thresholds(),
    )

    assert result is not None
    assert result.position == "12%-12%"


def test_reversal_entry_uses_configured_rsi_threshold() -> None:
    row = pd.Series(
        {
            "close": 10.0,
            "volume_ratio": 1.0,
            "rsi12": 50.0,
            "macd_hist": 0.2,
        }
    )
    prev = pd.Series({"high_20": 12.0, "macd_hist": 0.1})

    assert _entry_type(row, prev, False, ScoringThresholds()) == "relative_strength"
    assert (
        _entry_type(row, prev, False, ScoringThresholds(reversal_rsi_threshold=60))
        == "reversal_watch"
    )


def test_internet_strategy_signal_uses_configured_score() -> None:
    df = pd.DataFrame(
        [
            {
                "close": 10.0,
                "high_20": 10.0,
                "ma5": 10.0,
                "ma10": 9.5,
                "ma20": 9.0,
                "ma60": 8.5,
                "volume_ratio": 1.0,
                "ret_20": 0.0,
                "bias20": 1.0,
                "rsi12": 50.0,
                "macd_hist": 0.1,
                "amplitude_pct": 3.0,
                "range_pos": 0.5,
                "low_20": 9.0,
            },
            {
                "close": 10.3,
                "high_20": 10.0,
                "ma5": 10.2,
                "ma10": 9.8,
                "ma20": 9.4,
                "ma60": 8.8,
                "volume_ratio": 1.4,
                "ret_20": 0.13,
                "bias20": 2.0,
                "rsi12": 50.0,
                "macd_hist": 0.2,
                "amplitude_pct": 3.0,
                "range_pos": 0.7,
                "low_20": 9.0,
            },
        ]
    )

    signals = evaluate_strategy_signals(
        df,
        thresholds=InternetStrategyThresholds(volume_breakout_score=31.0),
    )

    breakout = next(item for item in signals if item.strategy_id == "volume_breakout")
    assert breakout.score == 31.0


def test_internet_strategy_thresholds_do_not_reload_yaml(monkeypatch) -> None:
    def fail_load_thresholds():
        raise AssertionError("unexpected thresholds reload")

    monkeypatch.setattr(
        "aqsp.internet_strategies.load_thresholds", fail_load_thresholds
    )
    df = pd.DataFrame(
        [
            {
                "close": 10.0,
                "high_20": 10.0,
                "ma5": 10.0,
                "ma10": 9.5,
                "ma20": 9.0,
                "ma60": 8.5,
                "volume_ratio": 1.0,
                "ret_20": 0.0,
                "bias20": 1.0,
                "rsi12": 50.0,
                "macd_hist": 0.1,
                "amplitude_pct": 3.0,
                "range_pos": 0.5,
                "low_20": 9.0,
            },
            {
                "close": 10.3,
                "high_20": 10.0,
                "ma5": 10.2,
                "ma10": 9.8,
                "ma20": 9.4,
                "ma60": 8.8,
                "volume_ratio": 1.4,
                "ret_20": 0.13,
                "bias20": 2.0,
                "rsi12": 50.0,
                "macd_hist": 0.2,
                "amplitude_pct": 3.0,
                "range_pos": 0.7,
                "low_20": 9.0,
            },
        ]
    )

    signals = evaluate_strategy_signals(df, thresholds=InternetStrategyThresholds())

    assert any(item.strategy_id == "volume_breakout" for item in signals)


def test_strategy_weights_for_regime_maps_screening_strategy_ids() -> None:
    from dataclasses import replace

    from aqsp.strategies.thresholds import CompositeThresholds, Thresholds

    thresholds = replace(
        Thresholds(),
        composite=CompositeThresholds(
            momentum_weight=0.3,
            quality_weight=0.0,
            value_weight=0.0,
            volume_weight=0.3,
            mean_reversion_weight=0.3,
            triple_rise_weight=0.3,
        ),
    )
    weights = strategy_weights_for_regime(thresholds, "stable_bull")

    assert weights["rps_momentum"] == pytest.approx(1.06)
    assert weights["volume_breakout"] == pytest.approx(1.03)
    assert weights["bowl_rebound"] == pytest.approx(0.91)
    assert weights["n_rebound"] == pytest.approx(1.03)


def test_strategy_weights_for_regime_uses_composite_blend_weights() -> None:
    from dataclasses import replace

    from aqsp.strategies.thresholds import (
        CompositeThresholds,
        RegimeStrategyWeights,
        RegimeThresholds,
        Thresholds,
    )

    thresholds = replace(
        Thresholds(),
        composite=CompositeThresholds(
            momentum_weight=0.3,
            quality_weight=0.0,
            value_weight=0.0,
            volume_weight=0.0,
            mean_reversion_weight=0.0,
            triple_rise_weight=0.3,
            base_blend_weight=0.25,
            regime_blend_weight=0.75,
        ),
        regime=RegimeThresholds(
            strategy_weights={
                "test_regime": RegimeStrategyWeights(
                    momentum=2.0,
                    triple_rise=0.5,
                )
            }
        ),
    )

    weights = strategy_weights_for_regime(thresholds, "test_regime")

    assert weights["rps_momentum"] == 1.75
    assert weights["ma_pullback"] == 1.75
    assert weights["low_vol_trend"] == 1.75
    assert weights["n_rebound"] == 0.625


def test_strategy_weights_for_regime_filters_disabled_strategy_buckets() -> None:
    from dataclasses import replace

    from aqsp.strategies.thresholds import (
        CompositeThresholds,
        MeanReversionThresholds,
        Thresholds,
        TripleRiseThresholds,
        VolumeThresholds,
    )

    thresholds = replace(
        Thresholds(),
        composite=CompositeThresholds(
            momentum_weight=0.3,
            quality_weight=0.0,
            value_weight=0.0,
            volume_weight=0.0,
            mean_reversion_weight=0.0,
            triple_rise_weight=0.3,
        ),
        volume=VolumeThresholds(enabled=False),
        mean_reversion=MeanReversionThresholds(enabled=False),
        triple_rise=TripleRiseThresholds(enabled=True),
    )

    weights = strategy_weights_for_regime(thresholds, "stable_bull")

    assert set(weights) == {"rps_momentum", "ma_pullback", "low_vol_trend", "n_rebound"}
    assert weights["n_rebound"] == pytest.approx(1.03)


def test_score_symbol_applies_screening_strategy_weights() -> None:
    frame = _frame("VOL", 0.004, 1.8)
    base = score_symbol(
        "VOL",
        frame,
        ScreeningConfig(min_avg_amount=1, strategy_weights={"rps_momentum": 1.0}),
        ScoringThresholds(),
        Thresholds(),
    )
    boosted = score_symbol(
        "VOL",
        frame,
        ScreeningConfig(min_avg_amount=1, strategy_weights={"rps_momentum": 2.0}),
        ScoringThresholds(),
        Thresholds(),
    )

    assert base is not None
    assert boosted is not None
    assert "rps_momentum" in base.strategies
    assert boosted.score > base.score


def test_score_symbol_skips_unlisted_strategy_when_weights_are_explicit() -> None:
    frame = _frame("VOL", 0.004, 1.8)

    pick = score_symbol(
        "VOL",
        frame,
        ScreeningConfig(min_avg_amount=1, strategy_weights={"rps_momentum": 1.0}),
        ScoringThresholds(),
        Thresholds(),
    )

    assert pick is not None
    assert "volume_breakout" not in pick.strategies
    assert "volume_breakout" not in pick.metrics["strategy_weights"]


def test_score_symbol_records_strategy_weight_feedback() -> None:
    frame = _frame("VOL", 0.004, 1.8)

    pick = score_symbol(
        "VOL",
        frame,
        ScreeningConfig(
            min_avg_amount=1,
            strategy_weights={"rps_momentum": 0.5},
            strategy_weight_reasons={
                "rps_momentum": "recent not_executable rate 60% (3/5)"
            },
        ),
        ScoringThresholds(),
        Thresholds(),
    )

    assert pick is not None
    assert pick.metrics["strategy_weights"]["rps_momentum"] == 0.5
    assert "not_executable" in pick.metrics["strategy_weight_reasons"]["rps_momentum"]


def test_screen_filters_price_outside_bounds() -> None:
    frames = {
        "HIGH": _frame("HIGH", 0.004, 1.5),
        "OK": _frame("OK", 0.004, 1.5),
    }
    frames["HIGH"]["close"] = 1200.0
    frames["HIGH"]["open"] = 1190.0
    frames["HIGH"]["high"] = 1210.0
    frames["HIGH"]["low"] = 1180.0

    picks = screen_universe(
        frames,
        ScreeningConfig(min_avg_amount=1, min_price=1.0, max_price=1000.0),
    )

    assert {pick.symbol for pick in picks} == {"OK"}


def test_screen_detects_n_rebound_pattern() -> None:
    frame = _n_rebound_frame()

    picks = screen_universe(
        {"NREB": frame},
        ScreeningConfig(min_avg_amount=1, min_bars=20),
    )

    assert picks
    assert picks[0].symbol == "NREB"
    assert "n_rebound" in picks[0].strategies


def test_score_symbol_uses_single_threshold_snapshot_for_n_rebound() -> None:
    frame = _n_rebound_frame()
    config = ScreeningConfig(min_avg_amount=1, min_bars=20)

    enabled = score_symbol("NREB", frame, config, ScoringThresholds(), Thresholds())
    disabled = score_symbol(
        "NREB",
        frame,
        config,
        ScoringThresholds(),
        Thresholds(n_rebound=NReboundThresholds(enabled=False)),
    )

    assert enabled is not None
    assert disabled is not None
    assert "n_rebound" in enabled.strategies
    assert "n_rebound" not in disabled.strategies


def test_screen_universe_preserves_full_threshold_snapshot_for_n_rebound() -> None:
    frame = _n_rebound_frame()

    picks = screen_universe(
        {"NREB": frame},
        ScreeningConfig(min_avg_amount=1, min_bars=20),
        thresholds=Thresholds(n_rebound=NReboundThresholds(enabled=False)),
    )

    assert picks
    assert "n_rebound" not in picks[0].strategies


def test_screen_universe_skips_invalid_frames() -> None:
    valid = _frame("GOOD", 0.004, 1.8)
    invalid = valid.copy()
    invalid.loc[0, "high"] = invalid.loc[0, "low"] - 1

    picks = screen_universe(
        {"GOOD": valid, "BROKEN": invalid},
        ScreeningConfig(min_avg_amount=1),
    )

    assert picks
    assert {pick.symbol for pick in picks} == {"GOOD"}


def test_load_thresholds_raises_when_config_missing(tmp_path) -> None:
    missing = tmp_path / "missing.yaml"

    try:
        load_thresholds(str(missing))
    except ValueError as exc:
        assert "thresholds config not found" in str(exc)
    else:
        raise AssertionError("missing thresholds config should fail closed")


def test_load_thresholds_allows_explicit_missing_fallback(tmp_path) -> None:
    thresholds = load_thresholds(str(tmp_path / "missing.yaml"), allow_missing=True)

    assert thresholds.version
