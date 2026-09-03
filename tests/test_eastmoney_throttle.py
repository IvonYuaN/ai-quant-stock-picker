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


class TestTrends2HostFallback:
    """trends2 多域名回退。

    生产机 IP 会对 push2his 按 IP 限流（连接被无响应重置，curl HTTP=000），
    而 push2delay 走同一 trends2 路径仍可用。故域名按重试轮转，优先 push2delay。
    """

    @staticmethod
    def _payload() -> dict:
        return {
            "data": {
                "name": "平安银行",
                "trends": [
                    "2026-09-03 09:30,11.0,11.5,11.6,11.0,100,1150",
                    "2026-09-03 09:31,11.5,11.7,11.8,11.5,200,2340",
                ],
            }
        }

    @staticmethod
    def _source_with(handler):
        src = EastmoneySource()
        src._session.get = MagicMock(side_effect=handler)
        return src

    def test_primary_host_is_push2delay(self):
        urls: list[str] = []

        def handler(url, **kwargs):
            urls.append(url)
            resp = MagicMock()
            resp.json.return_value = self._payload()
            return resp

        out = self._source_with(handler)._fetch_eastmoney_intraday_trends2("000001", "5")
        assert out is not None and not out.empty
        assert urls, "应至少发起一次 trends2 请求"
        assert urls[0].startswith("https://")
        assert em._TRENDS2_HOSTS[0] in urls[0]

    def test_falls_back_to_secondary_host_on_error(self, monkeypatch):
        # 关掉退避等待，避免测试变慢
        monkeypatch.setattr(em, "_BACKOFF_BASE", 0.0)
        urls: list[str] = []

        def handler(url, **kwargs):
            urls.append(url)
            if em._TRENDS2_HOSTS[0] in url:
                # 非限流错误：允许重试并轮转到备用域名
                raise ValueError("boom")
            resp = MagicMock()
            resp.json.return_value = self._payload()
            return resp

        out = self._source_with(handler)._fetch_eastmoney_intraday_trends2("000001", "5")
        assert out is not None and not out.empty
        assert any(em._TRENDS2_HOSTS[1] in u for u in urls), f"未回退到备用域名: {urls}"

    def test_all_hosts_fail_returns_none(self, monkeypatch):
        monkeypatch.setattr(em, "_BACKOFF_BASE", 0.0)

        def handler(url, **kwargs):
            raise ValueError("boom")

        out = self._source_with(handler)._fetch_eastmoney_intraday_trends2(
            "000001", "5"
        )
        assert out is None

    def test_hosts_rotation_covers_both_hosts(self):
        """重试轮转应覆盖全部域名（与 _SPOT_HOSTS 同样按 attempt 取模）。"""
        assert len(em._TRENDS2_HOSTS) >= 2
        seen = {
            em._TRENDS2_HOSTS[a % len(em._TRENDS2_HOSTS)]
            for a in range(em._MAX_RETRIES)
        }
        assert seen == set(em._TRENDS2_HOSTS)
