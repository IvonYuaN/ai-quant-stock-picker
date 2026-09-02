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

import pandas as pd
import pytest

from aqsp.core.errors import DataError
from aqsp.data.multi_source import MultiSource


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
