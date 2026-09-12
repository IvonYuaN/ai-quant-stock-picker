"""A1/A2 口径自解释回归测试。

1. ``hold_days=None`` 时市场窗口收益与历史行为**逐位一致**（向后兼容铁律）。
2. ``hold_days=k`` 时改用窗口**前 k 根 bar**（与策略持仓等长 ⇒ 同暴露对照）。
3. 资本利用率 / 暴露归一化显式落地到 runtime 行。
"""

from __future__ import annotations

import pandas as pd
import pytest

from aqsp.cli import (
    _exposure_window_return,
    _market_window_summary_payload,
    _summarize_walkforward_market_window,
    _walkforward_runtime_rows,
    _walkforward_utilization,
)

SIX = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]


def _frame(closes: list[float]) -> pd.DataFrame:
    dates = [f"2026-06-{day:02d}" for day in range(1, 1 + len(closes))]
    return pd.DataFrame({"date": dates, "close": closes})


def test_exposure_window_return_whole_window_when_hold_days_none() -> None:
    assert _exposure_window_return(_frame(SIX), None) == pytest.approx(0.5)


def test_exposure_window_return_uses_first_hold_days_bars() -> None:
    # 前 3 根：10 → 12 ⇒ +20%
    assert _exposure_window_return(_frame(SIX), 3) == pytest.approx(0.2)


@pytest.mark.parametrize("hold_days", [1, 0, -1, "bad"])
def test_exposure_window_return_rejects_degenerate_hold_days(hold_days: object) -> None:
    assert _exposure_window_return(_frame(SIX), hold_days) is None  # type: ignore[arg-type]


def test_exposure_window_return_none_for_missing_or_short_window() -> None:
    assert _exposure_window_return(None, 3) is None
    assert _exposure_window_return(_frame([10.0]), None) is None


def test_summary_hold_days_none_is_legacy_shape_and_values() -> None:
    """向后兼容：不传 hold_days 时返回字典不得新增 hold_* 字段，且数值为旧口径。"""
    frames = {"A": _frame([10.0, 12.0]), "B": _frame([20.0, 18.0])}
    summary = _summarize_walkforward_market_window(frames, "2026-06-01", "2026-06-30")
    assert set(summary) == {"sample_count", "avg_return", "negative_ratio"}
    assert summary["sample_count"] == 2
    assert summary["avg_return"] == pytest.approx((0.2 + -0.1) / 2)


def test_summary_hold_days_adds_same_exposure_fields() -> None:
    frames = {"A": _frame([10.0, 11.0, 12.0, 13.0]), "B": _frame([20.0, 20.0, 22.0, 24.0])}
    summary = _summarize_walkforward_market_window(
        frames, "2026-06-01", "2026-06-30", hold_days=2
    )
    assert summary["hold_days"] == 2
    assert summary["hold_sample_count"] == 2
    # A: 10→11 = +10%；B: 20→20 = 0% ⇒ 均值 +5%；整窗口仍是历史口径
    assert summary["avg_return_hold"] == pytest.approx(0.05)
    assert summary["avg_return"] == pytest.approx((0.3 + 0.2) / 2)


def test_summary_empty_frames_is_sample_count_zero() -> None:
    assert _summarize_walkforward_market_window({}, "2026-06-01", "2026-06-30") == {
        "sample_count": 0
    }


def test_payload_only_emits_hold_fields_when_hold_days_given() -> None:
    assert "avg_return_hold" not in _market_window_summary_payload([0.1], [], None)
    payload = _market_window_summary_payload([0.1], [], 3)
    assert payload["hold_days"] == 3
    assert payload["hold_sample_count"] == 0
    assert payload["avg_return_hold"] is None  # 无样本时不得冒充 0


@pytest.mark.parametrize(
    ("horizon", "test_days", "expected"),
    [
        (3, 30, 0.1),
        (1, 30, pytest.approx(1 / 30)),
        (10, 30, pytest.approx(10 / 30)),
        (40, 30, 1.0),  # 夹到 1.0
    ],
)
def test_utilization_values(horizon: int, test_days: int, expected: object) -> None:
    assert _walkforward_utilization(horizon, test_days) == expected


@pytest.mark.parametrize(
    ("horizon", "test_days"),
    [(3, None), (None, 30), (0, 30), (3, 0), ("x", 30), (3, "y")],
)
def test_utilization_none_on_bad_input(horizon: object, test_days: object) -> None:
    assert _walkforward_utilization(horizon, test_days) is None  # type: ignore[arg-type]


class _Args:
    source = "sqlite_db"
    symbols = ""
    min_score = None
    test_days = 30
    pool = "all"
    engine = ""
    grid_cscv = False
    grid_profile = "stable"
    tiered_stop = False
    cache_path = ""
    allow_heldout = False


def _runtime_rows(args: object, horizon: int = 3) -> dict[str, str]:
    return dict(
        _walkforward_runtime_rows(args, horizon, fee_bps=3.0, slippage_bps=20.0)  # type: ignore[arg-type]
    )


def test_runtime_rows_expose_utilization() -> None:
    assert _runtime_rows(_Args())["utilization"] == "3/30 = 10.0%"


def test_runtime_rows_utilization_is_dash_without_test_days() -> None:
    class _NoTestDays(_Args):
        test_days = None

    assert _runtime_rows(_NoTestDays())["utilization"] == "-"
