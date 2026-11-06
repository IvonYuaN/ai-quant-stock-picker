"""涨停四池的三态契约：成功(可能空) / 取数失败(None)。

`em_zt_topic_pool` 原来在**网络失败**和**该日确实没有这只池**两种情况下都返回
`[]`，二者不可区分。后果是两类假陈述：

1. `dt_count = len(dt)` 把失败算成 **「跌停 0 家」**。强势日跌停池为空是事实，
   取数失败时是假话 —— 页面没法区分，用户看到的是同一句陈述。
2. `_emotion` 的「从今天往前回溯找最近交易日」逻辑，遇到失败会继续往前翻，
   最终可能把**上一交易日**（甚至更早）的池当成今天的，页面标着「今天」，
   连板梯队却是一周前的。

改为三态后，`None` 表示失败：回溯必须停，计数缺失就整块标「未返回」。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astock
import market
import app as app_module

_ZT = [
    {"c": "600001", "n": "甲", "lbc": "3", "zdp": "10.0", "amount": 1e8, "ltsz": 5e9, "hybk": "电池"},
    {"c": "600002", "n": "乙", "lbc": "1", "zdp": "10.0", "amount": 2e8, "ltsz": 6e9, "hybk": "银行"},
]


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(astock, "_POOL_RETRY_SLEEP", 0)


# --------------------------------------------------------------------------
# em_zt_topic_pool：失败与空必须可区分
# --------------------------------------------------------------------------


class _Resp:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_pool_returns_none_after_all_attempts_fail(monkeypatch, no_sleep) -> None:
    calls: list[str] = []

    def boom(*a, **k):
        calls.append("x")
        raise RuntimeError("network down")

    monkeypatch.setattr(astock, "em_get", boom)
    assert astock.em_zt_topic_pool("getTopicZTPool", "20261019") is None
    assert len(calls) == astock._POOL_ATTEMPTS  # 有限次


def test_pool_retries_once_then_succeeds(monkeypatch, no_sleep) -> None:
    """抖一次不该判死 —— 与行业榜/隔夜外围同一节奏。"""
    state = {"n": 0}

    def flaky(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("blip")
        return _Resp({"data": {"pool": _ZT, "qdate": "20261019"}})

    monkeypatch.setattr(astock, "em_get", flaky)
    assert astock.em_zt_topic_pool("getTopicZTPool", "20261019") == _ZT


def test_pool_preserves_actual_source_trading_date(monkeypatch, no_sleep) -> None:
    monkeypatch.setattr(
        astock,
        "em_get",
        lambda *args, **kwargs: _Resp({"data": {"pool": _ZT, "qdate": "20261030"}}),
    )

    pool = astock.em_zt_topic_pool("getTopicZTPool", "20261031")

    assert pool == _ZT
    assert pool.source_date == "20261030"


def test_pool_rejects_nonempty_rows_without_source_date(monkeypatch, no_sleep) -> None:
    monkeypatch.setattr(astock, "em_get", lambda *args, **kwargs: _Resp({"data": {"pool": _ZT}}))

    assert astock.em_zt_topic_pool("getTopicZTPool", "20261031") is None


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": {}}, {"data": {"pool": None}}])
def test_pool_returns_empty_list_when_day_has_no_pool(monkeypatch, no_sleep, payload) -> None:
    """请求成功但池子为空（非交易日/强势日无跌停）⇒ ``[]``，不是 ``None``。"""
    monkeypatch.setattr(astock, "em_get", lambda *a, **k: _Resp(payload))
    assert astock.em_zt_topic_pool("getTopicDTPool", "20261019") == []


# --------------------------------------------------------------------------
# _emotion：失败不回溯、不拿 0 冒充
# --------------------------------------------------------------------------


def _stub(monkeypatch, handler):
    """handler(endpoint, date) -> list | None"""
    dates: list[tuple[str, str]] = []

    def fake(endpoint, date, sort=""):
        dates.append((endpoint, date))
        return handler(endpoint, date)

    monkeypatch.setattr(market.astock, "em_zt_topic_pool", fake)
    monkeypatch.setattr(market, "_breadth_counts", lambda: None)
    return dates


def test_emotion_stops_backtracking_on_fetch_failure(monkeypatch) -> None:
    """取数失败时**只问今天一次**，不许往前翻。

    往前翻会把上一交易日的池当成今天的 —— 页面标「今天」，数据是上周的。
    """
    dates = _stub(monkeypatch, lambda endpoint, date: None)

    assert market._emotion() == {}
    assert len(dates) == 1
    assert dates[0][0] == "getTopicZTPool"


def test_emotion_backtracks_on_genuine_empty_day(monkeypatch) -> None:
    """成功但空（非交易日/盘前）仍要回溯 —— 这是原本正确的行为。"""
    seen: list[str] = []

    def handler(endpoint, date):
        if endpoint == "getTopicZTPool":
            seen.append(date)
            return [] if len(seen) == 1 else _ZT
        return []

    dates = _stub(monkeypatch, handler)

    payload = market._emotion()
    assert payload  # 找到了池子
    assert len([d for e, d in dates if e == "getTopicZTPool"]) == 2  # 回溯了一次
    # 用的是回溯到的那一天，不是今天
    assert payload["date"] != seen[0][:4] + "-" + seen[0][4:6] + "-" + seen[0][6:]


def test_emotion_dates_pools_from_provider_qdate_not_weekend_request(monkeypatch) -> None:
    requested_dates: list[tuple[str, str]] = []

    class Pool(list):
        def __init__(self, rows, source_date):
            super().__init__(rows)
            self.source_date = source_date

    def fetch(endpoint, requested_date, sort=""):
        requested_dates.append((endpoint, requested_date))
        if endpoint == "getTopicZTPool":
            return Pool(_ZT, "20261030")
        return Pool([], "20261030")

    monkeypatch.setattr(market.astock, "em_zt_topic_pool", fetch)
    monkeypatch.setattr(market, "_breadth_counts", lambda: None)
    monkeypatch.setattr(market, "_emotion_assessment", lambda **kwargs: {})
    class Clock:
        @staticmethod
        def now(_tz):
            from datetime import datetime
            return datetime(2026, 10, 31, 10)
    monkeypatch.setattr(market, "datetime", Clock)

    payload = market._emotion()

    assert payload["date"] == "2026-10-30"
    assert all(day == "20261030" for _endpoint, day in requested_dates[1:])


def test_emotion_returns_empty_when_dt_pool_fails(monkeypatch) -> None:
    """跌停池取不到 ⇒ 整块「未返回」，不拿「跌停 0 家」冒充。"""

    def handler(endpoint, date):
        if endpoint == "getTopicDTPool":
            return None
        if endpoint == "getTopicZTPool":
            return _ZT
        return []

    _stub(monkeypatch, handler)
    assert market._emotion() == {}


def test_emotion_returns_empty_when_zb_pool_fails(monkeypatch) -> None:
    def handler(endpoint, date):
        if endpoint == "getTopicZBPool":
            return None
        if endpoint == "getTopicZTPool":
            return _ZT
        return []

    _stub(monkeypatch, handler)
    assert market._emotion() == {}


def test_emotion_keeps_counts_when_yzt_pool_fails(monkeypatch) -> None:
    """昨涨停池只影响晋级率 ⇒ 缺失时降级为 None，不抹掉整块。"""

    def handler(endpoint, date):
        if endpoint == "getYesterdayZTPool":
            return None
        if endpoint == "getTopicZTPool":
            return _ZT
        return []

    _stub(monkeypatch, handler)

    payload = market._emotion()
    assert payload["zt_count"] == 2
    assert payload["promotion_rate"] is None  # 降级，不是 0
    assert payload["yzt_count"] == 0


def test_emotion_counts_zero_dt_when_pool_genuinely_empty(monkeypatch) -> None:
    """跌停池**成功且为空**时 0 是真话，必须照常返回。"""

    def handler(endpoint, date):
        if endpoint == "getTopicZTPool":
            return _ZT
        return []

    _stub(monkeypatch, handler)

    payload = market._emotion()
    assert payload["dt_count"] == 0
    assert payload["zb_count"] == 0


def test_emotion_includes_bounded_objective_abnormal_pools(monkeypatch) -> None:
    rows = [
        {"c": "600001", "n": "甲", "lbc": 3, "p": "12345", "zdp": 10.0, "amount": "-", "ltsz": 5e9, "hybk": "电池"},
        {"c": "600002", "n": "乙", "lbc": 1, "p": "10000", "zdp": 9.0, "amount": 2e8, "hybk": "银行"},
        {"c": "bad", "n": "脏行"},
    ]
    monkeypatch.setattr(
        market.astock,
        "em_zt_topic_pool",
        lambda endpoint, _date, sort="": rows if endpoint == "getTopicZTPool" else [],
    )
    monkeypatch.setattr(market, "_breadth_counts", lambda: None)
    monkeypatch.setattr(market, "_emotion_assessment", lambda **_kwargs: {})

    payload = market._emotion()

    pool = payload["abnormal_pools"]["limit_up"]
    assert pool["count"] == 3
    assert pool["displayed_count"] == 2
    assert [row["code"] for row in pool["items"]] == ["600001", "600002"]
    assert pool["items"][0]["price"] == 12.35
    assert pool["items"][0]["amount"] is None
    assert payload["abnormal_pools"]["blown_up"]["count"] == 0
    assert payload["abnormal_pools"]["limit_down"]["count"] == 0


def test_emotion_keeps_abnormal_pool_truncation_metadata(monkeypatch) -> None:
    rows = [
        {"c": f"{600000 + index:06d}", "n": "股票", "p": "10000", "zdp": 10.0}
        for index in range(market._ABNORMAL_POOL_LIMIT + 1)
    ]
    monkeypatch.setattr(
        market.astock,
        "em_zt_topic_pool",
        lambda endpoint, _date, sort="": rows if endpoint == "getTopicZTPool" else [],
    )
    monkeypatch.setattr(market, "_breadth_counts", lambda: None)
    monkeypatch.setattr(market, "_emotion_assessment", lambda **_kwargs: {})

    payload = market._emotion()
    pool = payload["abnormal_pools"]["limit_up"]

    assert pool["count"] == market._ABNORMAL_POOL_LIMIT + 1
    assert pool["displayed_count"] == market._ABNORMAL_POOL_LIMIT
    assert pool["truncated"] is True


def test_market_emotion_api_exposes_abnormal_pool_source_day(monkeypatch) -> None:
    payload = {
        "date": "2026-10-30",
        "abnormal_pools": {
            "limit_up": {"count": 1, "displayed_count": 1, "truncated": False, "items": [{"code": "600001"}]},
            "blown_up": {"count": 0, "displayed_count": 0, "truncated": False, "items": []},
            "limit_down": {"count": 0, "displayed_count": 0, "truncated": False, "items": []},
        },
    }
    monkeypatch.setattr(market, "get_short_term_emotion", lambda: payload)

    response = TestClient(app_module.app).get("/api/market/emotion")

    assert response.status_code == 200
    assert response.json()["data"]["date"] == "2026-10-30"
    assert response.json()["data"]["abnormal_pools"]["limit_up"]["items"][0]["code"] == "600001"
