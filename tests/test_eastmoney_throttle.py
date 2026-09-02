from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
import requests

import aqsp.data.eastmoney_source as em
from aqsp.data.eastmoney_source import (
    EastmoneySource,
    _eastmoney_acquire_request_slot,
    _eastmoney_is_throttle,
    _eastmoney_record_result,
    _eastmoney_reset_breaker,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # 测试间隔离：关掉节奏睡眠、重置熔断，避免用例互相影响。
    monkeypatch.setattr(em, "_MIN_REQ_INTERVAL", 0.0)
    monkeypatch.setattr(em, "_BREAKER_COOLDOWN", 0.2)
    _eastmoney_reset_breaker()
    yield
    _eastmoney_reset_breaker()


def _remote_disconnected_exc() -> Exception:
    return requests.exceptions.ConnectionError(
        "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
    )


class TestThrottleClassification:
    def test_connection_error_is_throttle(self):
        assert _eastmoney_is_throttle(_remote_disconnected_exc()) is True

    def test_connection_aborted_string(self):
        assert _eastmoney_is_throttle(ConnectionError("Connection aborted.")) is True

    def test_remote_end_closed_string(self):
        assert _eastmoney_is_throttle(RuntimeError("Remote end closed connection")) is True

    def test_generic_error_not_throttle(self):
        assert _eastmoney_is_throttle(ValueError("boom")) is False
        assert _eastmoney_is_throttle(KeyError("x")) is False


class TestBreaker:
    def test_acquire_ok_when_closed(self):
        assert _eastmoney_acquire_request_slot() is True

    def test_acquire_false_when_open(self, monkeypatch):
        monkeypatch.setattr(em, "_BREAKER_OPEN_UNTIL", time.monotonic() + 10)
        assert _eastmoney_acquire_request_slot() is False

    def test_open_after_threshold(self):
        for _ in range(em._BREAKER_THRESHOLD):
            _eastmoney_record_result(True)
        # 达到阈值后下一次请求应被熔断拒绝
        assert _eastmoney_acquire_request_slot() is False

    def test_success_resets_breaker(self):
        for _ in range(em._BREAKER_THRESHOLD):
            _eastmoney_record_result(True)
        assert _eastmoney_acquire_request_slot() is False
        _eastmoney_record_result(False)
        assert _eastmoney_acquire_request_slot() is True


class TestPacing:
    def test_pacing_serializes(self, monkeypatch):
        monkeypatch.setattr(em, "_MIN_REQ_INTERVAL", 0.1)
        _eastmoney_reset_breaker()
        t0 = time.monotonic()
        _eastmoney_acquire_request_slot()
        _eastmoney_acquire_request_slot()
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.09


class TestIntradayThrottle:
    def _make_source(self, get_side_effect):
        src = EastmoneySource()
        src._session.get = MagicMock(side_effect=get_side_effect)
        return src

    def test_throttle_fast_fails_with_single_try(self):
        # 限流时不在紧凑重试，单次 fetch 只打 1 次请求即放弃
        src = self._make_source(_remote_disconnected_exc())
        out = src._fetch_eastmoney_intraday_trends2("000001", "5")
        assert out is None
        assert src._session.get.call_count == 1

    def test_breaker_opens_and_short_circuits(self):
        src = self._make_source(_remote_disconnected_exc())
        # 连续限流累计到阈值 -> 熔断开启
        for _ in range(em._BREAKER_THRESHOLD):
            src._fetch_eastmoney_intraday_trends2("000001", "5")
        assert _eastmoney_acquire_request_slot() is False
        # 熔断期间后续请求应短路（不再打东财），避免打满单 IP 限额
        before = src._session.get.call_count
        out = src._fetch_eastmoney_intraday_trends2("000001", "5")
        assert out is None
        assert src._session.get.call_count == before

    def test_success_returns_frame_and_keeps_breaker_closed(self):
        fake = MagicMock()
        fake.json.return_value = {
            "data": {
                "name": "平安银行",
                "trends": [
                    "2026-09-02 09:30,11.0,11.5,11.6,11.0,100,1150",
                    "2026-09-02 09:31,11.5,11.7,11.8,11.5,200,2340",
                ],
            }
        }
        src = EastmoneySource()
        src._session.get = MagicMock(return_value=fake)
        out = src._fetch_eastmoney_intraday_trends2("000001", "5")
        assert out is not None and not out.empty
        assert "close" in out.columns
        # 成功后熔断计数清零，下一帧仍可正常申请槽位
        assert _eastmoney_acquire_request_slot() is True
