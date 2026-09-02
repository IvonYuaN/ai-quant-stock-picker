"""东财分时走 trends2 端点的回归测试。

背景：生产环境 `kline/get?klt=N` 返回空体/连接重置，盘中新鲜度门因此永远
拿不到 realtime 数据。trends2 是东财真正的分时端点，只提供 1 分钟粒度，
更大周期由 `_resample_intraday_bars` 合成。
"""

from __future__ import annotations

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.eastmoney_source import (
    EastmoneySource,
    _parse_eastmoney_trends2,
    _resample_intraday_bars,
)

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def _minute_stamps(day: str = "2026-09-02") -> list[str]:
    """A 股一个完整交易日的 1 分钟时间戳：09:30-11:29 + 13:00-14:59。"""
    out: list[str] = []
    for hour, minutes in (
        (9, range(30, 60)),
        (10, range(0, 60)),
        (11, range(0, 30)),
        (13, range(0, 60)),
        (14, range(0, 60)),
    ):
        for minute in minutes:
            out.append(f"{day} {hour:02d}:{minute:02d}")
    return out


def _trends_rows(day: str = "2026-09-02") -> list[str]:
    """240 根 1 分钟 bar，volume 单位为手、amount 单位为元。"""
    rows = []
    for index, stamp in enumerate(_minute_stamps(day)):
        price = 10.0 + index * 0.01
        volume = 100 + index  # 手
        amount = price * volume * 100.0  # 元
        rows.append(
            f"{stamp},{price:.2f},{price + 0.01:.2f},"
            f"{price + 0.02:.2f},{price - 0.01:.2f},{volume},{amount:.2f}"
        )
    return rows


class _RecordingSession:
    """按 URL 分派响应，并记录每次请求的 (url, params)。"""

    def __init__(self, responses: dict[str, object]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, **_kwargs):
        self.calls.append((url, dict(params or {})))
        payload = self._responses.get(url)
        return _DummyResponse(payload)


class _DummyResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _make_source(session: _RecordingSession, *, throttle=True):
    source = EastmoneySource.__new__(EastmoneySource)
    source.name = "eastmoney"
    source._session = session
    source.cache = None
    source._last_request_ts = 0.0
    source._active_workload = None
    if not throttle:
        source._throttle = lambda: None  # type: ignore[attr-defined]
    return source


# --------------------------------------------------------------------------
# 解析 / 重采样
# --------------------------------------------------------------------------


def test_parse_trends2_extracts_ohlcv_in_documented_order():
    frame = _parse_eastmoney_trends2(
        ["2026-09-02 09:31,11.94,11.96,11.99,11.93,29302,35043042.00"]
    )

    row = frame.iloc[0]
    assert row["date"] == "2026-09-02 09:31"
    assert row["open"] == pytest.approx(11.94)
    assert row["close"] == pytest.approx(11.96)
    assert row["high"] == pytest.approx(11.99)
    assert row["low"] == pytest.approx(11.93)
    assert row["volume"] == pytest.approx(29302.0)
    assert row["amount"] == pytest.approx(35043042.0)


def test_parse_trends2_skips_malformed_rows():
    frame = _parse_eastmoney_trends2(
        [
            "bad-row",
            "2026-09-02 09:30,not,a,number,row,1,2,3",
            "2026-09-02 09:30,11.92,11.92,11.92,11.92,5725,6824712.00",
        ]
    )
    assert len(frame) == 1


def test_resample_produces_a_share_bar_counts():
    frame = _parse_eastmoney_trends2(_trends_rows())

    assert len(frame) == 240
    assert len(_resample_intraday_bars(frame, "1")) == 240
    assert len(_resample_intraday_bars(frame, "5")) == 48
    assert len(_resample_intraday_bars(frame, "15")) == 16
    assert len(_resample_intraday_bars(frame, "30")) == 8
    assert len(_resample_intraday_bars(frame, "60")) == 4


def test_resample_60min_bars_do_not_leak_across_lunch_break():
    frame = _parse_eastmoney_trends2(_trends_rows())

    resampled = _resample_intraday_bars(frame, "60")

    assert list(resampled["date"]) == [
        "2026-09-02 09:30",
        "2026-09-02 10:30",
        "2026-09-02 13:00",
        "2026-09-02 14:00",
    ]


def test_resample_conserves_volume_and_amount():
    frame = _parse_eastmoney_trends2(_trends_rows())

    resampled = _resample_intraday_bars(frame, "5")

    assert resampled["volume"].sum() == pytest.approx(frame["volume"].sum())
    assert resampled["amount"].sum() == pytest.approx(frame["amount"].sum())
    first = resampled.iloc[0]
    assert first["open"] == pytest.approx(10.0)
    assert first["close"] == pytest.approx(10.05)
    assert first["high"] == pytest.approx(10.06)
    assert first["low"] == pytest.approx(9.99)


def test_resample_returns_source_frame_when_period_is_unusable():
    frame = _parse_eastmoney_trends2(_trends_rows())

    assert _resample_intraday_bars(frame, "abc") is frame
    assert _resample_intraday_bars(frame, "1") is frame


# --------------------------------------------------------------------------
# 端点选择 / 兜底
# --------------------------------------------------------------------------


def test_fetch_intraday_prefers_trends2_and_converts_lots_to_shares(monkeypatch):
    session = _RecordingSession(
        {
            "https://push2his.eastmoney.com/api/qt/stock/trends2/get": {
                "data": {
                    "code": "000001",
                    "name": "平安银行",
                    "trends": [
                        "2026-09-02 09:30,11.92,11.92,11.92,11.92,5725,6824712.00",
                        "2026-09-02 09:31,11.94,11.96,11.99,11.93,29302,35043042.00",
                    ],
                }
            }
        }
    )
    source = _make_source(session)
    monkeypatch.setattr(source, "_throttle", lambda: None)

    frames = source.fetch_intraday(["000001"], period="1")

    frame = frames["000001"]
    # trends2 volume 单位为手（amount/(close*volume)≈100），应换算成股
    assert list(frame["volume"]) == pytest.approx([572500.0, 2930200.0])
    assert frame.attrs.get("volume_unit") == "shares"
    assert frame["symbol"].iloc[0] == "000001"
    assert frame["name"].iloc[0] == "平安银行"
    assert [url for url, _ in session.calls] == [
        "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
    ]


def test_fetch_intraday_resamples_trends2_minutes_into_requested_period(
    monkeypatch,
):
    session = _RecordingSession(
        {
            "https://push2his.eastmoney.com/api/qt/stock/trends2/get": {
                "data": {
                    "code": "000001",
                    "name": "平安银行",
                    "trends": [
                        "2026-09-02 09:30,11.92,11.92,11.92,11.92,5725,6824712.00",
                        "2026-09-02 09:31,11.94,11.96,11.99,11.93,29302,35043042.00",
                    ],
                }
            }
        }
    )
    source = _make_source(session)
    monkeypatch.setattr(source, "_throttle", lambda: None)

    frame = source.fetch_intraday(["000001"], period="5")["000001"]

    assert len(frame) == 1
    # 5 分钟桶内 volume 求和后再做手→股换算
    assert frame["volume"].iloc[0] == pytest.approx(3502700.0)
    assert frame["date"].iloc[0] == "2026-09-02 09:30"


def test_trends2_request_uses_bare_six_digit_secid(monkeypatch):
    session = _RecordingSession(
        {
            "https://push2his.eastmoney.com/api/qt/stock/trends2/get": {
                "data": {"name": "浦发银行", "trends": _trends_rows()}
            }
        }
    )
    source = _make_source(session)
    monkeypatch.setattr(source, "_throttle", lambda: None)

    source.fetch_intraday(["600000"], period="15")

    url, params = session.calls[0]
    assert url.endswith("/trends2/get")
    # 带后缀的代码会拼成非法的 0.000001.SZ，必须传裸 6 位码
    assert params["secid"] == "1.600000"
    assert params["ndays"] == "1"


def test_fetch_intraday_falls_back_to_kline_when_trends2_empty(monkeypatch):
    session = _RecordingSession(
        {
            "https://push2his.eastmoney.com/api/qt/stock/trends2/get": {"data": None},
            KLINE_URL: {
                "data": {
                    "name": "平安银行",
                    "klines": [
                        "2026-09-02 09:35,11.92,11.96,11.99,11.93,5725,6824712.00,"
                        "0.59,0.34,0.04,0.03"
                    ],
                }
            },
        }
    )
    source = _make_source(session)
    monkeypatch.setattr(source, "_throttle", lambda: None)

    frame = source.fetch_intraday(["000001"], period="5")["000001"]

    assert [url for url, _ in session.calls] == [
        "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
        KLINE_URL,
    ]
    assert frame["date"].iloc[0] == "2026-09-02 09:35"
    assert frame["volume"].iloc[0] == pytest.approx(572500.0)


def test_fetch_intraday_raises_when_both_endpoints_empty(monkeypatch):
    session = _RecordingSession(
        {
            "https://push2his.eastmoney.com/api/qt/stock/trends2/get": {"data": None},
            KLINE_URL: {"data": None},
        }
    )
    source = _make_source(session)
    monkeypatch.setattr(source, "_throttle", lambda: None)

    with pytest.raises(DataError):
        source.fetch_intraday(["000001"], period="5")


def test_fetch_index_intraday_uses_shanghai_market_prefix(monkeypatch):
    session = _RecordingSession(
        {
            "https://push2his.eastmoney.com/api/qt/stock/trends2/get": {
                "data": {"name": "沪深300", "trends": _trends_rows()}
            }
        }
    )
    source = _make_source(session)
    monkeypatch.setattr(source, "_throttle", lambda: None)

    frames = source.fetch_index_intraday(["000300"], period="5")

    assert len(frames["000300"]) == 48
    assert session.calls[0][1]["secid"] == "1.000300"


def test_trends2_exception_falls_back_without_raising(monkeypatch):
    session = _RecordingSession(
        {
            "https://push2his.eastmoney.com/api/qt/stock/trends2/get": ValueError(
                "Expecting value: line 1 column 1"
            ),
            KLINE_URL: {
                "data": {
                    "name": "平安银行",
                    "klines": [
                        "2026-09-02 09:35,11.92,11.96,11.99,11.93,5725,6824712.00,"
                        "0.59,0.34,0.04,0.03"
                    ],
                }
            },
        }
    )
    source = _make_source(session)
    monkeypatch.setattr(source, "_throttle", lambda: None)
    monkeypatch.setattr("aqsp.data.eastmoney_source.time.sleep", lambda _secs: None)

    frame = source.fetch_intraday(["000001"], period="5")["000001"]

    assert frame["date"].iloc[0] == "2026-09-02 09:35"
    assert len(session.calls) == 4  # trends2 重试 3 次 + kline 兜底 1 次


def test_resample_handles_empty_and_dirty_frames():
    empty = _parse_eastmoney_trends2([])
    assert _resample_intraday_bars(empty, "5").empty

    dirty = _parse_eastmoney_trends2(["2026-09-02 09:30,x,y,z,1,2,3"])
    assert dirty.empty
    assert isinstance(dirty, pd.DataFrame)
