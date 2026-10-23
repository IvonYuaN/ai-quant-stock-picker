"""Chart evidence survives existing event persistence and state transitions."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

import alert_events as ae
from aqsp.risk.live_risk import run_live_risk_check


@pytest.fixture
def chart_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(ae, "CACHE_DIR", str(tmp_path))
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-06-01", periods=41),
            "close": [10 + i * 0.1 for i in range(40)] + [10],
        }
    )
    reminder = run_live_risk_check(
        [{"code": "600000", "cost": 12, "shares": 100}],
        {"600000": 10},
        price_history={"600000": frame},
        position_rules=[
            {"id": "600000-L4", "level": "L4", "symbol": "600000", "enabled": True}
        ],
    )[0].to_dict()
    return json.loads(json.dumps(reminder))


def test_alert_chart_preserved_when_event_reloaded(chart_store: dict) -> None:
    saved = ae.upsert_from_reminders([chart_store])[0]
    loaded = ae.list_events()[0]
    assert loaded["id"] == saved["id"]
    assert loaded["basis_json"] == chart_store["basis_json"]


def test_alert_chart_updates_with_text_when_open_event_rechecked(
    chart_store: dict,
) -> None:
    saved = ae.upsert_from_reminders([chart_store])[0]
    chart_store["basis_json"]["actual"] = "updated check evidence"
    chart_store["basis_json"]["chart"]["source"] = "updated raw history source"
    assert ae.upsert_from_reminders([chart_store]) == []
    loaded = ae.list_events()[0]
    assert loaded["id"] == saved["id"]
    assert loaded["basis_json"] == chart_store["basis_json"]


def test_alert_chart_not_overwritten_when_event_terminal(chart_store: dict) -> None:
    saved = ae.upsert_from_reminders([chart_store])[0]
    ae.transition(saved["id"], "handled")
    original = ae.list_events()[0]["basis_json"]
    chart_store["basis_json"]["chart"]["trigger_date"] = "2099-01-01"
    ae.upsert_from_reminders([chart_store])
    assert ae.list_events()[0]["basis_json"] == original


def test_alert_chart_missing_keeps_text_when_legacy_event_reloaded(
    chart_store: dict,
) -> None:
    chart_store["basis_json"].pop("chart")
    ae.upsert_from_reminders([chart_store])
    assert ae.list_events()[0]["basis_json"] == chart_store["basis_json"]


@pytest.mark.parametrize("future_cross_only", [False, True])
def test_alert_chart_uses_clean_raw_history_when_service_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, future_cross_only: bool
) -> None:
    import aqsp.data.source_factory as factory
    from aqsp.core.time import latest_completed_trading_day, today_shanghai
    import decision_context
    import live_risk_service
    import portfolio as pf

    monkeypatch.setattr(ae, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pf, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(decision_context, "load_cached_market_overview", lambda: None)
    holding = {
        "code": "600000",
        "shares": 100,
        "cost": 12,
        "price": 10,
        "name": "合成图形证据",
        "opened_at": "2026-06-01",
    }
    monkeypatch.setattr(pf, "get_portfolio", lambda: {"holdings": [holding]})
    monkeypatch.setattr(
        pf,
        "observe_risk_prices",
        lambda _: [
            {"id": "600000-L4", "symbol": "600000", "level": "L4", "enabled": True}
        ],
    )
    dates = pd.bdate_range(end=latest_completed_trading_day(), periods=41)
    closes = [10 + i * 0.1 for i in range(41)]
    if not future_cross_only:
        closes[-1] = 10
    clean = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "close": closes})
    raw = pd.concat(
        [
            clean,
            clean.iloc[[3]],
            pd.DataFrame(
                [
                    {"date": "not-a-date", "close": 999},
                    {
                        "date": (today_shanghai() + timedelta(days=1)).isoformat(),
                        "close": 10,
                    },
                ]
            ),
        ],
        ignore_index=True,
    ).iloc[::-1]
    adjustments = []

    class Source:
        def fetch_daily(
            self, symbols: list, start: object, end: object, adjust: str = ""
        ) -> dict:
            adjustments.append(adjust)
            return {"600000": raw}

    monkeypatch.setattr(factory, "build_data_source", lambda *args, **kwargs: Source())
    result = live_risk_service.check_positions({"600000": 5}, notify=False)
    assert adjustments == [""]
    assert result["checks"]["skipped"] == []
    if future_cross_only:
        assert result["reminders"] == []
        assert ae.list_events() == []
    else:
        chart = result["reminders"][0]["basis_json"]["chart"]
        assert chart["trigger_date"] == dates[-1].date().isoformat()
        assert len(chart["points"]) == len(clean)
        assert [p["close"] for p in chart["points"]] == closes
        assert (
            ae.list_events()[0]["basis_json"]["chart"]["trigger_date"]
            == chart["trigger_date"]
        )
