"""盘中兜底链：东财作为 online_first 的延迟兜底源。

背景（2026-09-02 生产实测）：`online_first` 的 live_short 竞赛里真正能出分时
的只有腾讯——sina 返回空、akshare 在 live_short 只是 observation 角色、
tdx_vipdoc 不支持分时。腾讯端点一旦故障，盘中新鲜度门无兜底硬失败。

东财 trends2 已具备限流韧性（全局节流 + 熔断），可作兜底；但它**不能**进并发
竞赛——`_with_live_short_fallback` 会把所有 eligible 源同时提交线程池，那样每
一轮盘中抓取都会打东财 trends2，把单 IP 限额打满、反而把兜底源废掉。

所以约定：东财是「延迟兜底」——竞赛不含它，只有全部竞赛源都失败时才顺序回退；
日线/指数历史链完全跳过它（其历史 API 单符号重试会拖长链路）。
"""

from __future__ import annotations

import threading

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.intraday import IntradayService
from aqsp.data.multi_source import MultiSource
from aqsp.data.tencent_source import TencentSource, _ENDPOINT_DOWN_THRESHOLD


def _intraday_frame(source_name: str) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "date": ["2026-09-02 09:30", "2026-09-02 09:35"],
            "open": [10.0, 10.1],
            "high": [10.2, 10.3],
            "low": [9.9, 10.0],
            "close": [10.1, 10.2],
            "volume": [1000, 1200],
        }
    )
    frame.attrs["source_name"] = source_name
    return frame


class _StubSource:
    def __init__(self, name: str, *, fails: bool = False) -> None:
        self.name = name
        self.fails = fails
        self.intraday_calls: list[list[str]] = []
        self.daily_calls: list[list[str]] = []

    def fetch_intraday(self, symbols, period="5"):
        self.intraday_calls.append(list(symbols))
        if self.fails:
            raise DataError(f"{self.name} intraday 不可用")
        return {symbol: _intraday_frame(self.name) for symbol in symbols}

    def fetch_daily(self, symbols, start, end, adjust=""):
        self.daily_calls.append(list(symbols))
        if self.fails:
            raise DataError(f"{self.name} daily 不可用")
        return {symbol: _intraday_frame(self.name) for symbol in symbols}


def _build(*, tencent_fails: bool, eastmoney_fails: bool = False):
    tencent = _StubSource("tencent", fails=tencent_fails)
    sina = _StubSource("sina", fails=True)
    eastmoney = _StubSource("eastmoney", fails=eastmoney_fails)
    source = MultiSource(
        tencent,
        [sina, eastmoney],
        validate_consistency=False,
        live_fetch_deadline_seconds=5.0,
        deferred_live_short_sources=frozenset({"eastmoney"}),
    )
    return source, tencent, sina, eastmoney


def test_healthy_primary_never_spends_deferred_source_quota() -> None:
    source, tencent, _sina, eastmoney = _build(tencent_fails=False)

    result = source.fetch_intraday(["000001"], "5")

    assert set(result) == {"000001"}
    assert source.last_used_sources == {"000001": "tencent"}
    assert tencent.intraday_calls == [["000001"]]
    # 关键断言：腾讯健康时东财一次都不能被调用，否则每轮盘中抓取都会自限流。
    assert eastmoney.intraday_calls == []


def test_deferred_source_answers_when_raced_sources_all_fail() -> None:
    source, tencent, sina, eastmoney = _build(tencent_fails=True)

    result = source.fetch_intraday(["000001", "600000"], "5")

    assert set(result) == {"000001", "600000"}
    assert source.last_used_source == "eastmoney"
    assert tencent.intraday_calls == [["000001", "600000"]]
    assert sina.intraday_calls == [["000001", "600000"]]
    assert eastmoney.intraday_calls == [["000001", "600000"]]


def test_all_sources_failing_still_raises_data_error() -> None:
    source, _tencent, _sina, eastmoney = _build(
        tencent_fails=True, eastmoney_fails=True
    )

    with pytest.raises(DataError) as excinfo:
        source.fetch_intraday(["000001"], "5")

    message = str(excinfo.value)
    assert "tencent" in message
    assert "eastmoney" in message
    assert eastmoney.intraday_calls == [["000001"]]


def test_deferred_source_skipped_on_daily_chain() -> None:
    source, tencent, sina, eastmoney = _build(tencent_fails=True)
    tencent.fails = True
    sina.fails = True

    with pytest.raises(DataError):
        source.fetch_daily(
            ["000001"],
            pd.Timestamp("2026-09-01").date(),
            pd.Timestamp("2026-09-02").date(),
        )

    # 日线链跳过延迟兜底源：其历史 API 单符号重试会拖长链路，
    # 当年把东财从 online_first 摘掉就是这个原因，不能顺带改回来。
    assert eastmoney.daily_calls == []


def test_deferred_source_is_opt_in_only() -> None:
    tencent = _StubSource("tencent", fails=True)
    eastmoney = _StubSource("eastmoney")
    source = MultiSource(
        tencent,
        [eastmoney],
        validate_consistency=False,
        live_fetch_deadline_seconds=5.0,
    )

    result = source.fetch_intraday(["000001"], "5")

    # 未声明延迟兜底时行为不变：东财照旧进并发竞赛。
    assert source.last_used_source == "eastmoney"
    assert source.deferred_live_short_sources == frozenset()
    assert eastmoney.intraday_calls == [["000001"]]
    assert result["000001"].attrs["source_name"] == "eastmoney"


def _dead_endpoint_tencent(calls: list, monkeypatch):
    """真实 TencentSource，但分时端点恒返回 WAF 拦截页（501 + HTML）。"""

    class _RejectingResponse:
        status_code = 501

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    class _RejectingSession:
        def get(self, url, params=None, **_kwargs):
            calls.append(params)
            return _RejectingResponse()

    tencent = TencentSource.__new__(TencentSource)
    tencent._session = _RejectingSession()
    tencent._last_request_ts = 0.0
    tencent._endpoint_down_lock = threading.Lock()
    tencent._endpoint_down_streak = 0
    tencent._endpoint_down_until = 0.0
    monkeypatch.setattr(tencent, "_throttle", lambda: None)
    monkeypatch.setattr("aqsp.data.tencent_source.time.sleep", lambda _s: None)
    return tencent


def test_dead_tencent_endpoint_frees_deadline_for_deferred_source(monkeypatch) -> None:
    """腾讯端点被 WAF 拦时，竞速必须快速失败，把 deadline 让给 deferred 东财。

    2026-09-18 生产实测：腾讯按每只标的退避重试 3 次（2s+4s ≈ 6s），一批 20 只
    就要 120s，而 IntradayService 的整批共享预算只有 90s —— 90s 内只来得及试
    8 只标的，257/257 全部跳过，兜底源根本没轮到。短路后端点只被试探
    _ENDPOINT_DOWN_THRESHOLD 次，竞速立即失败，东财拿到完整预算。
    """
    calls: list = []
    tencent = _dead_endpoint_tencent(calls, monkeypatch)
    eastmoney = _StubSource("eastmoney")
    source = MultiSource(
        tencent,
        [eastmoney],
        validate_consistency=False,
        live_fetch_deadline_seconds=5.0,
        deferred_live_short_sources=frozenset({"eastmoney"}),
    )
    symbols = ["600000", "600001", "600002", "600003", "600004"]

    result = source.fetch_intraday(symbols, "5")

    # 兜底源真的答上了 —— 这是修复前拿不到的结果
    assert set(result) == set(symbols)
    assert source.last_used_sources == {symbol: "eastmoney" for symbol in symbols}
    # 且端点只被试探到阈值，不是 5 只 × 3 次重试 = 15 次
    assert len(calls) == _ENDPOINT_DOWN_THRESHOLD


# --- #149：delayed 兜底独立预算 —— 兜底阶段不再被 fast-race 的 30s 吃满 ---

def test_effective_deferred_deadline_scales_with_symbol_count() -> None:
    """兜底阶段预算 = base + n_symbols × per-symbol，随标的数线性增长。

    2026-09 生产实测：腾讯 WAF 全挂时 256 只标的走东财兜底，东财全局节流
    ~0.347s/标的 ⇒ 串行 ~89s，旧的 flat 30s deadline 在收尾前就到期，
    0 覆盖。修复后兜底阶段拿到独立预算（默认 0.5s/标的），256 只 = 158s。
    """
    source, *_ = _build(tencent_fails=True)
    assert source.deferred_fetch_deadline_per_symbol_seconds == 0.5
    # _build 用 live_fetch_deadline_seconds=5.0：base 5s + 256 × 0.5s = 133s
    assert source._effective_deferred_deadline(256) == pytest.approx(133.0)
    # 1 只 = 5.5s；空列表按 1 只兜底
    assert source._effective_deferred_deadline(1) == pytest.approx(5.5)
    assert source._effective_deferred_deadline(0) == pytest.approx(5.5)


def test_explicit_deferred_deadline_overrides_scaling() -> None:
    """显式给定 deferred_live_short_deadline_seconds 时固定使用该值，不叠加。"""
    tencent = _StubSource("tencent", fails=True)
    eastmoney = _StubSource("eastmoney")
    source = MultiSource(
        tencent,
        [eastmoney],
        validate_consistency=False,
        live_fetch_deadline_seconds=30.0,
        deferred_live_short_deadline_seconds=90.0,
        deferred_live_short_sources=frozenset({"eastmoney"}),
    )
    # 无论标的数多少，都取固定 90s 上限
    assert source._effective_deferred_deadline(256) == 90.0
    assert source._effective_deferred_deadline(1) == 90.0


# --- #149：IntradayService 外层共享 deadline 感知兜底排水 ---

def test_intraday_wait_deadline_scales_only_with_deferred_reserve() -> None:
    """有 deferred 兜底源时，外层 wait 按 (n_symbols/workers) × per-symbol 加预算，
    使 13 个批次串行排水不被 13/13 跳过；无兜底源时是纯 no-op。

    2026-09 生产日志：13 批 × 20 只 = 256 只，东财串行排水 ~77s，flat 90s
    里后段批次排队等锁就超时 ⇒ “跳过 13/13 个批次”。修复后外层 = 90 +
    (256/4) × 0.5 = 122s，覆盖排水。
    """
    with_reserve, *_ = _build(tencent_fails=True)
    service = IntradayService(
        with_reserve,
        fetch_deadline_seconds=90.0,
        fetch_max_workers=4,
    )
    # 有兜底：90 + (256/4) × 0.5 = 122.0
    assert service._effective_wait_deadline(13, 256) == pytest.approx(122.0)

    # 无兜底源 ⇒ 保持 flat base，不膨胀预算（健康路径不受影响）
    plain = MultiSource(
        _StubSource("tencent", fails=True),
        [_StubSource("sina")],
        validate_consistency=False,
    )
    plain_service = IntradayService(
        plain,
        fetch_deadline_seconds=90.0,
        fetch_max_workers=4,
    )
    assert plain_service._effective_wait_deadline(13, 256) == 90.0

