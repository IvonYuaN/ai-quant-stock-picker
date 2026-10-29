"""Position evidence remains factual, bounded and separate from risk/T+1 state."""

from __future__ import annotations

from breadth_fixtures import complete_breadth

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import decision_context as context


NOW = datetime(2026, 10, 23, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _holding(**changes):
    return {
        "code": "600001",
        "name": "真实源名称",
        "opened_at": "2026-10-19",
        "position_id": "position-1",
        "cost": 20,
        "price": 8,
        "sellable_shares": 100,
        "industry": "电力",
        **changes,
    }


def _overview(**changes):
    return {
        "sentiment": complete_breadth(NOW),
        "sectors": [
            {
                "name": "电力",
                "pct": -2,
                "as_of": "2026-10-23T12:59:00+08:00",
                "source": "合成行业行情",
            }
        ],
        **changes,
    }


def _quote(**changes):
    return {
        "name": "真实源名称",
        "change_pct": -3,
        "quote_time": "20261023125900",
        **changes,
    }


def test_decision_context_counts_real_trade_days_when_weekends_and_holidays_intervene():
    assert context.holding_trade_days("2026-10-19", today=date(2026, 10, 23)) == 4
    assert context.holding_trade_days("2026-10-23", today=date(2026, 10, 23)) == 0
    assert context.holding_trade_days("2026-09-30", today=date(2026, 10, 9)) == 2


def test_decision_context_never_fetches_calendar_when_counting_display_days(
    monkeypatch,
):
    from aqsp.data.tushare_pit import TusharePitClient

    monkeypatch.setattr(
        TusharePitClient,
        "fetch_trade_calendar",
        lambda *args, **kwargs: pytest.fail("must not fetch calendar"),
    )
    assert context.holding_trade_days("2026-10-19", today=NOW.date()) == 4
    assert context.holding_trade_days("2025-01-02", today=NOW.date()) is None
    assert (
        context.build_position_context(
            _holding(), [], checked_at=NOW
        ).holding_trade_days
        == 4
    )


@pytest.mark.parametrize(
    "opened", [None, "bad", "2026-10-24", "2026-10-18", "20261019"]
)
def test_decision_context_keeps_days_unknown_when_opening_is_not_verifiable(opened):
    assert context.holding_trade_days(opened, today=date(2026, 10, 23)) is None


def test_decision_context_preserves_hard_risk_when_background_recovers():
    holding = _holding()
    original = holding.copy()
    result = context.build_position_context(
        holding,
        [{"symbol": "600001", "level": "L1", "position_id": "position-1"}],
        quote=_quote(change_pct=3),
        market_overview=_overview(),
        checked_at=NOW,
    )
    assert result.name == "真实源名称" and result.holding_trade_days == 4
    assert result.assessment.stance == "risk_review"
    assert "L1" in result.assessment.reason and "不能" in result.assessment.reason
    assert result.assessment.t_plan.eligible is None
    assert "止损线触发本身都不是做T依据" in result.assessment.t_plan.reason
    assert "同步走弱" not in result.assessment.title
    assert holding == original
    assert result.to_dict()["stock"]["status"] == "rising"


def test_decision_context_requires_same_session_weakness_when_reviewing_exit():
    result = context.build_position_context(
        _holding(), [], quote=_quote(), market_overview=_overview(), checked_at=NOW
    )
    assert result.assessment.stance == "exit_review"
    assert result.market.status == "weak"
    assert result.sector.status == result.stock.status == "falling"
    assert result.assessment.t_plan.eligible is False


def test_decision_context_recommends_exit_review_only_when_hard_risk_and_all_contexts_weak():
    result = context.build_position_context(
        _holding(),
        [{"symbol": "600001", "level": "L1", "position_id": "position-1"}],
        quote=_quote(),
        market_overview=_overview(),
        checked_at=NOW,
    )
    assert result.assessment.stance == "risk_review"
    assert "大盘、板块和个股走弱" in result.assessment.title
    assert "三项同交易日弱势证据一致" in result.assessment.reason
    assert result.assessment.t_plan.eligible is False


def test_decision_context_keeps_options_when_only_loss_is_known():
    result = context.build_position_context(_holding(), [], checked_at=NOW)
    assert (
        result.market.status == result.sector.status == result.stock.status == "unknown"
    )
    assert result.assessment.stance == "observe"
    assert result.assessment.t_plan.eligible is None
    assert any("继续持有" in item for item in result.assessment.options)
    assert any("减仓" in item for item in result.assessment.options)
    assert "亏损或止损线触发本身都不是做T依据" in result.assessment.t_plan.reason


def test_decision_context_rejects_sector_assumption_when_membership_is_missing():
    result = context.build_position_context(
        _holding(industry=None),
        [],
        quote=_quote(),
        market_overview=_overview(),
        checked_at=NOW,
    )
    assert result.sector.status == "unknown"
    assert result.assessment.stance == "observe"


def test_decision_context_rejects_sector_freshness_when_only_fetch_time_is_known():
    overview = _overview(sectors=[{"name": "电力", "pct": -2}])
    result = context.build_position_context(
        _holding(), [], quote=_quote(), market_overview=overview, checked_at=NOW
    )
    assert result.sector.status == "unknown"
    assert result.sector.as_of is None
    assert result.assessment.stance == "observe"


@pytest.mark.parametrize("stamp", ["20261022", "20261024", "bad"])
def test_decision_context_rejects_market_context_when_source_date_is_not_current(stamp):
    overview = _overview(
        sentiment={"date": stamp, "up": 800, "down": 3000, "breadth": "偏弱"}
    )
    result = context.build_position_context(
        _holding(), [], quote=_quote(), market_overview=overview, checked_at=NOW
    )
    assert result.market.status == "unknown"
    assert result.assessment.stance == "observe"


def test_decision_context_does_not_infer_t_suitability_when_t1_locked():
    result = context.build_position_context(
        _holding(sellable_shares=0), [], quote=_quote(change_pct=3), checked_at=NOW
    )
    assert result.assessment.t_plan.eligible is False
    assert "T+1" in result.assessment.t_plan.reason


def test_decision_context_ignores_old_position_alert_when_symbol_reentered():
    result = context.build_position_context(
        _holding(),
        [{"symbol": "600001", "level": "L1", "position_id": "old-position"}],
        checked_at=NOW,
    )
    assert result.assessment.stance != "risk_review"


def test_decision_context_uses_closed_raw_bars_when_intraday_quote_is_missing():
    history = pd.DataFrame(
        {"date": ["2026-10-21", "2026-10-22", "2026-10-23"], "close": [10, 9, 8]}
    )
    result = context.build_position_context(
        _holding(),
        [],
        stock_history=history,
        market_overview=_overview(),
        checked_at=NOW,
    )
    assert result.stock.status == "falling"
    assert result.stock.as_of == "2026-10-22"
    assert "非当前分时" in result.stock.summary
    assert result.assessment.stance == "observe"


@pytest.mark.parametrize(
    "dates,closes",
    [
        (["2026-10-20", "2026-10-21"], [10, 9]),
        (["2026-10-22", "2026-10-24"], [10, 9]),
        (["2026-10-21", "2026-10-22"], [10, float("nan")]),
    ],
)
def test_decision_context_keeps_stock_unknown_when_daily_evidence_is_invalid(
    dates, closes
):
    result = context.build_position_context(
        _holding(),
        [],
        stock_history=pd.DataFrame({"date": dates, "close": closes}),
        checked_at=NOW,
    )
    assert result.stock.status == "unknown"


def test_decision_context_does_not_invent_name_when_source_only_returns_code():
    result = context.build_position_context(_holding(name="600001"), [], checked_at=NOW)
    assert result.name == ""


@pytest.mark.parametrize(
    "quote",
    [
        _quote(quote_time="20261023093000"),
        _quote(quote_time="20261023140000"),
        _quote(quote_time="bad"),
        _quote(as_of="2026-10-23", quote_time=""),
        _quote(change_pct=float("nan")),
    ],
)
def test_decision_context_keeps_stock_unknown_when_quote_is_stale_future_or_invalid(
    quote,
):
    result = context.build_position_context(_holding(), [], quote=quote, checked_at=NOW)
    assert result.stock.status == "unknown"


def test_decision_context_rejects_adjusted_history_when_not_raw():
    history = pd.DataFrame({"date": ["2026-10-21", "2026-10-22"], "close": [10, 9]})
    history.attrs["price_mode"] = "qfq"
    assert (
        context.build_position_context(
            _holding(), [], stock_history=history, checked_at=NOW
        ).stock.status
        == "unknown"
    )


@pytest.mark.parametrize("current,stamp,valid", [
    ("2026-10-23T20:00:00+08:00", "20261023100000", False),
    ("2026-10-23T15:01:00+08:00", "20261023090000", False),
    ("2026-10-24T10:00:00+08:00", "20261023090000", False),
    ("2026-10-26T09:00:00+08:00", "20261023150000", True),
    ("2026-10-23T20:00:00+08:00", "20261023150003", True),
    ("2026-10-24T10:00:00+08:00", "20261023150000", True),
    ("2026-10-23T12:45:00+08:00", "20261023113000", True),
    ("2026-10-23T12:45:00+08:00", "20261023100000", False),
    ("2026-10-23T14:00:00+08:00", "20261023133000", True),
    ("2026-10-23T14:00:00+08:00", "20261023132959", False),
])
def test_position_quote_freshness_uses_session_clock_when_market_paused(current, stamp, valid):
    issue = context.position_quote_issue({"quote_time": stamp}, checked_at=datetime.fromisoformat(current))
    assert (issue == "") is valid


def test_decision_context_reads_existing_cache_without_fetching_when_fresh(
    tmp_path, monkeypatch
):
    import market

    monkeypatch.setattr(market, "get_overview", lambda: pytest.fail("must not fetch"))
    cache = tmp_path / "overview.json"
    payload = {"ts": NOW.timestamp(), "val": _overview()}
    cache.write_text(json.dumps(payload))
    before = cache.read_bytes()
    got = context.load_cached_market_overview(tmp_path, checked_at=NOW)
    assert got["sectors"] == payload["val"]["sectors"]
    assert all(got["sentiment"][key] == value for key, value in payload["val"]["sentiment"].items())
    assert context._market_context(got, NOW).status == "weak"
    assert cache.read_bytes() == before
    payload["ts"] = NOW.timestamp() - market._TTL - 1
    cache.write_text(json.dumps(payload))
    assert context.load_cached_market_overview(tmp_path, checked_at=NOW) is None


@pytest.mark.parametrize(
    "payload", ["bad json", "[]", '{"ts":"bad","val":{}}', '{"ts":1,"val":null}']
)
def test_decision_context_keeps_cache_unknown_when_missing_or_malformed(
    tmp_path, payload
):
    assert context.load_cached_market_overview(tmp_path, checked_at=NOW) is None
    (tmp_path / "overview.json").write_text(payload)
    assert context.load_cached_market_overview(tmp_path, checked_at=NOW) is None
