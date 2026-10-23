"""L4 chart snapshots use the same raw observations and EMA as the trigger."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import json

import pandas as pd
import pytest

from aqsp.risk.live_risk import SellReminder, run_live_risk_check


def _history(periods: int = 41) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-06-01", periods=periods),
            "close": [10 + i * 0.1 for i in range(periods - 1)] + [10],
        }
    )


def _check(frame: pd.DataFrame, level: str = "L4") -> list[SellReminder]:
    return run_live_risk_check(
        [{"code": "600000", "shares": 100, "cost": 12}],
        {"600000": 10},
        price_history={"600000": frame},
        position_rules=[
            {
                "id": "600000-" + level,
                "symbol": "600000",
                "level": level,
                "enabled": True,
                "param_value": 0.08,
            }
        ],
    )


def test_live_risk_chart_matches_trigger_when_dead_cross() -> None:
    frame = _history()
    reminder = _check(frame)[0]
    chart = reminder.to_dict()["basis_json"]["chart"]
    dif = (
        frame.close.ewm(span=12, adjust=False).mean()
        - frame.close.ewm(span=26, adjust=False).mean()
    )
    dea = dif.ewm(span=9, adjust=False).mean()
    assert chart["price_adjustment"] == "none"
    assert chart["trigger_date"] == "2026-07-11"
    assert chart["kind"] == "macd_dead_cross"
    assert len(chart["points"]) == len(frame)
    assert [p["dif"] for p in chart["points"]] == dif.tolist()
    assert [p["dea"] for p in chart["points"]] == dea.tolist()
    assert chart["points"][-2]["dif"] >= chart["points"][-2]["dea"]
    assert chart["points"][-1]["dif"] < chart["points"][-1]["dea"]
    assert f"{chart['points'][-1]['dif']:.6f}" in reminder.basis["actual"]
    assert (
        json.loads(json.dumps(reminder.to_dict()))["basis_json"]["chart"]["points"][-1][
            "close"
        ]
        == 10
    )
    with pytest.raises(FrozenInstanceError):
        reminder.chart_evidence.trigger_date = "2099-01-01"


def test_live_risk_chart_preserves_warmup_when_display_window_capped() -> None:
    frame = _history(100)
    chart = _check(frame)[0].chart_evidence
    assert len(chart.points) == 60
    expected = (
        frame.close.ewm(span=12, adjust=False).mean()
        - frame.close.ewm(span=26, adjust=False).mean()
    )
    assert chart.points[0].dif == expected.iloc[-60]
    assert chart.points[-1].dif == expected.iloc[-1]
    assert chart.points[-1].date == chart.trigger_date


@pytest.mark.parametrize("closes", [[10.0] * 41, list(range(10, 51)), [10] * 20])
def test_live_risk_chart_absent_when_no_valid_dead_cross(closes: list[float]) -> None:
    frame = pd.DataFrame(
        {"date": pd.date_range("2026-06-01", periods=len(closes)), "close": closes}
    )
    assert _check(frame) == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -1])
def test_live_risk_chart_absent_when_close_invalid(bad: float) -> None:
    frame = _history()
    frame.loc[3, "close"] = bad
    assert _check(frame) == []


@pytest.mark.parametrize(
    "bad", ["not-a-date", "2026-02-30", "2026-06-01", "2026-05-01", None]
)
def test_live_risk_chart_absent_when_dates_invalid_or_unordered(
    bad: str | None,
) -> None:
    frame = _history()
    frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
    frame.loc[3, "date"] = bad
    assert _check(frame) == []


def test_live_risk_chart_absent_when_date_column_missing() -> None:
    assert _check(_history().drop(columns=["date"])) == []


def test_live_risk_chart_not_added_when_other_rule_triggers() -> None:
    reminder = _check(_history(), "L1")[0]
    assert reminder.chart_evidence is None
    assert "chart" not in reminder.to_dict()["basis_json"]


def test_live_risk_chart_detached_when_source_frame_changes() -> None:
    frame = _history()
    reminder = _check(frame)[0]
    before = reminder.to_dict()
    frame.loc[len(frame) - 1, "close"] = 1000
    assert reminder.to_dict() == before
