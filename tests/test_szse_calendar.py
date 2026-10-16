"""SZSE 官方交易日历源（``aqsp.data.szse_calendar``）测试。

覆盖：纯解析容错、缓存 TTL / 陈旧兜底、锚点月缺失降级、
以及接入 ``resolve_is_trading_day`` 后的运行时覆盖行为。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from aqsp.data import szse_calendar
from aqsp.data.szse_calendar import (
    SzseCalendarDay,
    SzseCalendarSource,
    clear_memo,
    month_key,
    months_between,
    parse_month_payload,
)
from aqsp.data.trading_calendar import resolve_is_trading_day

SHANGHAI = szse_calendar.now_shanghai().tzinfo


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeSession:
    """按 ``month`` 参数返回预置响应，并记录调用次数。"""

    def __init__(self, mapping: dict[str, _FakeResponse]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def get(self, url: str, params: dict | None = None, timeout: float | None = None):
        month = str((params or {}).get("month", ""))
        self.calls.append(month)
        response = self.mapping.get(month)
        if response is None:
            raise RuntimeError(f"unexpected month {month}")
        return response


def _payload(
    month: str,
    *,
    open_days: set[int] | None = None,
    closed_days: set[int] | None = None,
) -> dict:
    """构造一个月响应：默认只有周末休市。

    ``open_days`` 指定后完全接管交易日集合；``closed_days`` 在默认基础上
    追加休市日（用于表达国庆这类法定长假）。
    """
    year, mon = (int(part) for part in month.split("-"))
    days = []
    cursor = date(year, mon, 1)
    while cursor.month == mon:
        is_open = cursor.weekday() < 5
        if open_days is not None:
            is_open = cursor.day in open_days
        if closed_days and cursor.day in closed_days:
            is_open = False
        days.append(
            {
                "zrxh": cursor.isoweekday(),
                "jybz": "1" if is_open else "0",
                "jyrq": cursor.isoformat(),
            }
        )
        cursor += timedelta(days=1)
    return {"data": days}


@pytest.fixture(autouse=True)
def _reset_memo():
    clear_memo()
    yield
    clear_memo()


def _fixed_now(value: datetime):
    return lambda: value


# ---------------------------------------------------------------------------
# 纯解析
# ---------------------------------------------------------------------------


def test_parse_month_payload_maps_flags_to_open_days():
    payload = _payload("2026-10", closed_days={1, 2, 3, 4, 5, 6, 7})
    days = parse_month_payload(payload, month="2026-10")
    assert len(days) == 31
    by_date = {d.trade_date: d.is_open for d in days}
    assert by_date[date(2026, 10, 1)] is False  # 国庆
    assert by_date[date(2026, 10, 8)] is True
    assert by_date[date(2026, 10, 10)] is False  # 周六
    assert by_date[date(2026, 10, 16)] is True


def test_parse_month_payload_returns_empty_when_top_level_not_mapping():
    assert parse_month_payload(["not", "a", "dict"], month="2026-10") == ()


def test_parse_month_payload_returns_empty_when_data_not_list():
    assert parse_month_payload({"data": {"oops": 1}}, month="2026-10") == ()


def test_parse_month_payload_skips_unparsable_and_blank_dates():
    payload = {
        "data": [
            {"jybz": "1", "jyrq": "2026-10-08"},
            {"jybz": "1", "jyrq": ""},
            {"jybz": "1", "jyrq": "not-a-date"},
            {"jybz": "1"},
            "not-a-dict",
        ]
    }
    days = parse_month_payload(payload, month="2026-10")
    assert [d.trade_date for d in days] == [date(2026, 10, 8)]


def test_parse_month_payload_skips_dates_outside_requested_month():
    """上游串月时必须丢弃，否则别的月份的日子会被混进本月覆盖区间。"""
    payload = {
        "data": [
            {"jybz": "1", "jyrq": "2026-10-08"},
            {"jybz": "1", "jyrq": "2026-11-02"},
        ]
    }
    days = parse_month_payload(payload, month="2026-10")
    assert [d.trade_date for d in days] == [date(2026, 10, 8)]


def test_parse_month_payload_treats_unknown_flag_as_closed():
    payload = {"data": [{"jybz": "X", "jyrq": "2026-10-08"}]}
    days = parse_month_payload(payload, month="2026-10")
    assert len(days) == 1
    assert days[0].is_open is False


def test_parse_month_payload_sorts_ascending():
    payload = {
        "data": [
            {"jybz": "1", "jyrq": "2026-10-09"},
            {"jybz": "1", "jyrq": "2026-10-08"},
        ]
    }
    days = parse_month_payload(payload, month="2026-10")
    assert [d.trade_date for d in days] == [date(2026, 10, 8), date(2026, 10, 9)]


def test_month_key_accepts_date_and_datetime():
    assert month_key(date(2026, 3, 5)) == "2026-03"
    assert month_key(datetime(2026, 12, 31, 23, 59)) == "2026-12"


def test_months_between_spans_year_boundary():
    assert months_between(date(2026, 11, 20), date(2027, 2, 3)) == (
        "2026-11",
        "2026-12",
        "2027-01",
        "2027-02",
    )


def test_months_between_returns_empty_for_reversed_range():
    assert months_between(date(2026, 5, 1), date(2026, 4, 1)) == ()


# ---------------------------------------------------------------------------
# 取数 + 缓存
# ---------------------------------------------------------------------------


def test_load_month_fetches_and_persists_cache(tmp_path):
    session = _FakeSession({"2026-10": _FakeResponse(200, _payload("2026-10"))})
    source = SzseCalendarSource(tmp_path, session=session)
    days = source.load_month("2026-10")
    assert days is not None
    assert len(days) == 31
    assert session.calls == ["2026-10"]
    assert (tmp_path / "2026-10.json").is_file()


def test_load_month_uses_fresh_cache_without_network(tmp_path):
    now = datetime(2026, 10, 16, 10, 0, tzinfo=SHANGHAI)
    first = _FakeSession({"2026-10": _FakeResponse(200, _payload("2026-10"))})
    SzseCalendarSource(tmp_path, session=first, now=_fixed_now(now)).load_month("2026-10")
    clear_memo()

    later = now + timedelta(hours=1)
    second = _FakeSession({})
    days = SzseCalendarSource(
        tmp_path, session=second, now=_fixed_now(later)
    ).load_month("2026-10")
    assert days is not None and len(days) == 31
    assert second.calls == []  # 24h 内不打网络


def test_load_month_refetches_after_ttl_expiry(tmp_path):
    now = datetime(2026, 10, 16, 10, 0, tzinfo=SHANGHAI)
    first = _FakeSession({"2026-10": _FakeResponse(200, _payload("2026-10"))})
    SzseCalendarSource(tmp_path, session=first, now=_fixed_now(now)).load_month("2026-10")
    clear_memo()

    later = now + timedelta(hours=25)
    second = _FakeSession({"2026-10": _FakeResponse(200, _payload("2026-10"))})
    SzseCalendarSource(tmp_path, session=second, now=_fixed_now(later)).load_month("2026-10")
    assert second.calls == ["2026-10"]


def test_load_month_falls_back_to_stale_cache_within_seven_days(tmp_path):
    now = datetime(2026, 10, 16, 10, 0, tzinfo=SHANGHAI)
    SzseCalendarSource(
        tmp_path,
        session=_FakeSession({"2026-10": _FakeResponse(200, _payload("2026-10"))}),
        now=_fixed_now(now),
    ).load_month("2026-10")
    clear_memo()

    later = now + timedelta(days=3)
    failing = _FakeSession({"2026-10": _FakeResponse(503, None)})
    days = SzseCalendarSource(
        tmp_path, session=failing, now=_fixed_now(later)
    ).load_month("2026-10")
    assert days is not None and len(days) == 31
    assert failing.calls == ["2026-10"]


def test_load_month_returns_none_when_cache_too_stale_and_fetch_fails(tmp_path):
    now = datetime(2026, 10, 16, 10, 0, tzinfo=SHANGHAI)
    SzseCalendarSource(
        tmp_path,
        session=_FakeSession({"2026-10": _FakeResponse(200, _payload("2026-10"))}),
        now=_fixed_now(now),
    ).load_month("2026-10")
    clear_memo()

    later = now + timedelta(days=30)
    failing = _FakeSession({"2026-10": _FakeResponse(500, None)})
    assert (
        SzseCalendarSource(
            tmp_path, session=failing, now=_fixed_now(later)
        ).load_month("2026-10")
        is None
    )


def test_load_month_returns_none_when_no_cache_and_network_error(tmp_path):
    failing = _FakeSession({"2026-10": _FakeResponse(403, None)})
    assert SzseCalendarSource(tmp_path, session=failing).load_month("2026-10") is None


def test_load_month_returns_none_when_transport_raises(tmp_path):
    class _Boom:
        def get(self, *args, **kwargs):
            raise OSError("connection reset")

    assert SzseCalendarSource(tmp_path, session=_Boom()).load_month("2026-10") is None


def test_load_month_returns_empty_tuple_for_announced_empty_month(tmp_path):
    """成功但该月无数据 ⇒ 空元组（与「取数失败返回 None」严格区分）。"""
    session = _FakeSession({"2027-06": _FakeResponse(200, {"data": []})})
    days = SzseCalendarSource(tmp_path, session=session).load_month("2027-06")
    assert days == ()


def test_load_month_returns_none_when_disabled(tmp_path):
    session = _FakeSession({"2026-10": _FakeResponse(200, _payload("2026-10"))})
    source = SzseCalendarSource(tmp_path, session=session, enabled=False)
    assert source.load_month("2026-10") is None
    assert session.calls == []


def test_load_month_tolerates_corrupted_cache_file(tmp_path):
    (tmp_path / "2026-10.json").write_text("{ not json", encoding="utf-8")
    session = _FakeSession({"2026-10": _FakeResponse(200, _payload("2026-10"))})
    days = SzseCalendarSource(tmp_path, session=session).load_month("2026-10")
    assert days is not None and len(days) == 31


# ---------------------------------------------------------------------------
# as_frame：锚点月覆盖规则
# ---------------------------------------------------------------------------


def test_as_frame_returns_tushare_compatible_schema(tmp_path):
    session = _FakeSession(
        {
            "2026-09": _FakeResponse(200, _payload("2026-09")),
            "2026-10": _FakeResponse(200, _payload("2026-10")),
            "2026-11": _FakeResponse(200, _payload("2026-11")),
        }
    )
    frame = SzseCalendarSource(tmp_path, session=session).as_frame(
        date(2026, 9, 15), date(2026, 11, 16), anchor=date(2026, 10, 16)
    )
    assert frame is not None
    assert list(frame.columns) == ["cal_date", "is_open"]
    assert frame["cal_date"].min() == "2026-09-15"
    assert frame["cal_date"].max() == "2026-11-16"
    assert set(frame["is_open"].unique()) <= {0, 1}
    # 与既有消费者对齐：is_open_day_in_calendar 需要 ISO 字符串 + 0/1
    assert frame.dtypes["is_open"] == "int64"


def test_as_frame_returns_none_when_anchor_month_has_no_data(tmp_path):
    """锚点月尚未公布 ⇒ 必须降级，不能拿邻月数据假装覆盖。"""
    session = _FakeSession(
        {
            "2026-09": _FakeResponse(200, _payload("2026-09")),
            "2026-10": _FakeResponse(200, _payload("2026-10")),
            "2026-11": _FakeResponse(200, {"data": []}),
        }
    )
    frame = SzseCalendarSource(tmp_path, session=session).as_frame(
        date(2026, 9, 15), date(2026, 11, 16), anchor=date(2026, 11, 10)
    )
    assert frame is None


def test_as_frame_returns_none_when_anchor_month_fetch_fails(tmp_path):
    session = _FakeSession(
        {
            "2026-09": _FakeResponse(200, _payload("2026-09")),
            "2026-10": _FakeResponse(503, None),
            "2026-11": _FakeResponse(200, _payload("2026-11")),
        }
    )
    frame = SzseCalendarSource(tmp_path, session=session).as_frame(
        date(2026, 9, 15), date(2026, 11, 16), anchor=date(2026, 10, 16)
    )
    assert frame is None


def test_as_frame_tolerates_failing_neighbour_month(tmp_path):
    """邻月失败不该拖垮整个窗口 —— 锚点月可信即可用。"""
    session = _FakeSession(
        {
            "2026-09": _FakeResponse(503, None),
            "2026-10": _FakeResponse(200, _payload("2026-10")),
            "2026-11": _FakeResponse(503, None),
        }
    )
    frame = SzseCalendarSource(tmp_path, session=session).as_frame(
        date(2026, 9, 15), date(2026, 11, 16), anchor=date(2026, 10, 16)
    )
    assert frame is not None
    assert frame["cal_date"].min().startswith("2026-10")
    assert frame["cal_date"].max().startswith("2026-10")


def test_as_frame_returns_none_for_reversed_range(tmp_path):
    source = SzseCalendarSource(tmp_path, session=_FakeSession({}))
    assert source.as_frame(date(2026, 10, 20), date(2026, 10, 1)) is None


# ---------------------------------------------------------------------------
# 接入 resolve_is_trading_day
# ---------------------------------------------------------------------------


def test_resolve_is_trading_day_uses_szse_calendar_for_unlisted_year(monkeypatch):
    """2027 春节未写进静态名单时，运行时日历必须能把它判成休市。"""
    frame = pd.DataFrame(
        {
            "cal_date": ["2027-02-05", "2027-02-08", "2027-02-09"],
            "is_open": [0, 0, 1],
        }
    )
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_szse_trade_calendar",
        lambda start, end, **kwargs: frame,
    )
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_optional_trade_calendar",
        lambda *args, **kwargs: None,
    )
    assert resolve_is_trading_day(date(2027, 2, 8)) is False
    assert resolve_is_trading_day(date(2027, 2, 9)) is True


def test_static_holiday_list_still_overrides_runtime_calendar(monkeypatch):
    """静态名单是硬覆盖：运行时日历说「开市」也必须休市（issue #312 语义）。"""
    frame = pd.DataFrame({"cal_date": ["2026-10-01"], "is_open": [1]})
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_szse_trade_calendar",
        lambda start, end, **kwargs: frame,
    )
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_optional_trade_calendar",
        lambda *args, **kwargs: None,
    )
    assert resolve_is_trading_day(date(2026, 10, 1)) is False


def test_resolve_falls_back_to_static_when_szse_returns_none(monkeypatch):
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_szse_trade_calendar",
        lambda start, end, **kwargs: None,
    )
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_optional_trade_calendar",
        lambda *args, **kwargs: None,
    )
    assert resolve_is_trading_day(date(2026, 10, 16)) is True  # 周五
    assert resolve_is_trading_day(date(2026, 10, 17)) is False  # 周六


def test_tushare_takes_priority_over_szse(monkeypatch):
    tushare_frame = pd.DataFrame({"cal_date": ["2026-10-16"], "is_open": [0]})
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_optional_trade_calendar",
        lambda *args, **kwargs: tushare_frame,
    )

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("Tushare 有数据时不应回落到 SZSE")

    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_szse_trade_calendar", _should_not_be_called
    )
    assert resolve_is_trading_day(date(2026, 10, 16)) is False


def test_load_month_result_is_sorted_and_deduplicated(tmp_path):
    session = _FakeSession({"2026-10": _FakeResponse(200, _payload("2026-10"))})
    days = SzseCalendarSource(tmp_path, session=session).load_month("2026-10")
    assert days is not None
    assert isinstance(days[0], SzseCalendarDay)
    dates = [d.trade_date for d in days]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))


# ---------------------------------------------------------------------------
# 性能红线：已覆盖年份不得联网
# ---------------------------------------------------------------------------


def test_static_calendar_covers_year_reads_holiday_years():
    from aqsp.data.trading_calendar import _static_calendar_covers_year

    # 真实 config/trading_holidays.json 当前只含 2026 年
    assert _static_calendar_covers_year(date(2026, 6, 1)) is True
    assert _static_calendar_covers_year(date(2027, 6, 1)) is False


def test_szse_source_is_never_called_for_years_covered_by_static_calendar(monkeypatch):
    """🔴 性能红线：静态名单已覆盖该年时**不得**联网。

    `is_trading_day()` 是高频路径；CI（GitHub runner 在境外）连不上 szse.cn，
    若每次调用都打接口，整个测试套件会被重试超时拖死。
    """
    calls: list[date] = []
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_szse_trade_calendar",
        lambda start, end, **kwargs: calls.append(start) or None,
    )
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_optional_trade_calendar",
        lambda *args, **kwargs: None,
    )
    assert resolve_is_trading_day(date(2026, 10, 16)) is True
    assert resolve_is_trading_day(date(2026, 10, 17)) is False
    assert calls == []


def test_szse_source_is_called_for_years_missing_from_static_calendar(monkeypatch):
    calls: list[date] = []
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_szse_trade_calendar",
        lambda start, end, **kwargs: calls.append(start) or None,
    )
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_optional_trade_calendar",
        lambda *args, **kwargs: None,
    )
    resolve_is_trading_day(date(2027, 2, 9))
    assert len(calls) == 1


def test_tushare_result_short_circuits_before_the_year_guard(monkeypatch):
    """Tushare 有数据时直接返回，不触发年份判定，也不碰 SZSE。"""
    tushare_frame = pd.DataFrame({"cal_date": ["2027-02-09"], "is_open": [0]})
    monkeypatch.setattr(
        "aqsp.data.trading_calendar.load_optional_trade_calendar",
        lambda *args, **kwargs: tushare_frame,
    )

    def _boom(*args, **kwargs):
        raise AssertionError("Tushare 命中时不应走到 SZSE")

    monkeypatch.setattr("aqsp.data.trading_calendar.load_szse_trade_calendar", _boom)
    assert resolve_is_trading_day(date(2027, 2, 9)) is False


def test_repeated_failures_do_not_refetch_within_failure_memo(tmp_path):
    """网络不可达时，同一进程内不得反复重试（失败 memo 兜住）。"""
    failing = _FakeSession({"2026-10": _FakeResponse(503, None)})
    source = SzseCalendarSource(tmp_path, session=failing)
    assert source.load_month("2026-10") is None
    assert source.load_month("2026-10") is None
    assert source.load_month("2026-10") is None
    assert failing.calls == ["2026-10"]  # 只打了一次
