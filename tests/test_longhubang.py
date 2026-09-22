"""龙虎榜 fetcher 测试（全离线：合成 payload / 假 requests，不触网）。

字段映射按 2026-09-08 生产机实测响应。
"""

from __future__ import annotations

import sys
import types

import pytest

from aqsp.core.errors import DataError
from aqsp.data.longhubang import (
    _PAGE_SIZE,
    LongHubangItem,
    LongHubangSource,
    _is_truncated,
    _parse_items,
)

_VALID_ROW = {
    "TRADE_DATE": "2026-09-05 00:00:00",
    "SECURITY_CODE": "600519",
    "SECURITY_NAME_ABBR": "贵州茅台",
    "CLOSE_PRICE": 1410.0,
    "CHANGE_RATE": 1.23,
    "BILLBOARD_BUY_AMT": 123456700.0,  # 元
    "BILLBOARD_SELL_AMT": 0.0,
    "BILLBOARD_NET_AMT": 123456700.0,
    "EXPLAIN": "4家机构买入",
}


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def _install_fake_requests(monkeypatch, payloads: list, captured: dict) -> None:
    """按调用顺序回放 payloads（最后一次重复用于后续调用），零网络。"""
    fake = types.ModuleType("requests")
    calls: list[dict] = []

    def _get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params})
        return _FakeResponse(payloads[min(len(calls) - 1, len(payloads) - 1)])

    fake.get = _get  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "requests", fake)
    captured["calls"] = calls


def test_parse_real_datacenter_shape():
    payload = {
        "result": {
            "data": [
                {
                    "TRADE_DATE": "2026-09-05 00:00:00",
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "CLOSE_PRICE": 1410.0,
                    "CHANGE_RATE": 1.23,
                    "BILLBOARD_BUY_AMT": 123456700.0,  # 元
                    "BILLBOARD_SELL_AMT": 0.0,
                    "BILLBOARD_NET_AMT": 123456700.0,
                    "EXPLAIN": "4家机构买入",
                }
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "600519"
    assert it.name == "贵州茅台"
    assert it.close_price == pytest.approx(1410.0)
    assert it.change_rate == pytest.approx(1.23)
    # 元 → 万元
    assert it.buy_amount == pytest.approx(12345.67)
    assert it.net_amount == pytest.approx(12345.67)
    assert it.interpretation == "4家机构买入"


def test_parse_skips_malformed():
    payload = {
        "result": {
            "data": [
                {"SECURITY_CODE": "000001"},
                {
                    "TRADE_DATE": "x",
                    "SECURITY_CODE": "000002",
                    "SECURITY_NAME_ABBR": "ok",
                    "BILLBOARD_NET_AMT": 100.0,
                },
            ]
        }
    }
    items = _parse_items(payload)
    # 第一条缺字段走默认值不抛；第二条完整
    assert len(items) == 2
    assert items[1].symbol == "000002"
    assert items[1].net_amount == pytest.approx(0.01)  # 100 元 → 0.01 万元


def test_source_from_items():
    src = LongHubangSource().from_items(
        [
            LongHubangItem(
                trade_date="2026-09-05",
                symbol="000001",
                name="平安银行",
                close_price=10.5,
                change_rate=1.0,
                buy_amount=1.0,
                sell_amount=0.0,
                net_amount=1.0,
                interpretation="",
            )
        ]
    )
    assert src.items(autoload=False)[0].symbol == "000001"


@pytest.mark.parametrize(
    "count,expected", [(0, False), (1, False), (499, False), (500, True), (501, True)]
)
def test_is_truncated_boundaries(count, expected):
    assert _is_truncated(count, _PAGE_SIZE) is expected


def test_fetch_uses_multi_day_range_not_single_day(monkeypatch):
    """回归：filter 必须是 [anchor-5, anchor] 多日区间。

    历史坑：原实现是单日区间 + to_csv 覆盖写盘 ⇒ 缓存恒定只含 1 个交易日，
    与消费方 lookback_days=5 口径冲突，1 天的数据证明不了 5 天的事。
    """
    captured: dict = {}
    _install_fake_requests(
        monkeypatch, [{"result": {"data": [dict(_VALID_ROW)]}}], captured
    )
    src = LongHubangSource()
    src._fetch(trade_date="2026-09-05")
    assert len(captured["calls"]) == 1  # 显式传日期 ⇒ 不做探针
    params = captured["calls"][0]["params"]
    assert params["filter"] == (
        "(TRADE_DATE>='2026-08-31 00:00:00')(TRADE_DATE<='2026-09-05 23:59:59')"
    )
    assert params["sortColumns"] == "TRADE_DATE,BILLBOARD_NET_AMT"
    assert params["sortTypes"] == "-1,-1"
    assert params["pageSize"] == str(_PAGE_SIZE)
    assert params["pageNumber"] == "1"
    assert src.truncated is False


def test_fetch_lookback_days_widens_window(monkeypatch):
    captured: dict = {}
    _install_fake_requests(
        monkeypatch, [{"result": {"data": [dict(_VALID_ROW)]}}], captured
    )
    LongHubangSource()._fetch(trade_date="2026-09-05", lookback_days=3)
    assert captured["calls"][0]["params"]["filter"] == (
        "(TRADE_DATE>='2026-09-02 00:00:00')(TRADE_DATE<='2026-09-05 23:59:59')"
    )


def test_fetch_probes_latest_trading_day_then_queries_range(monkeypatch):
    """不传日期时：第 1 次探针（pageSize=1 + TRADE_DATE 倒序）定锚点，第 2 次取区间。"""
    captured: dict = {}
    _install_fake_requests(
        monkeypatch,
        [
            {"result": {"data": [{"TRADE_DATE": "2026-09-08 00:00:00"}]}},
            {"result": {"data": [dict(_VALID_ROW)]}},
        ],
        captured,
    )
    src = LongHubangSource()
    items = src._fetch()
    assert len(items) == 1
    assert len(captured["calls"]) == 2
    probe = captured["calls"][0]["params"]
    assert probe["pageSize"] == "1"
    assert probe["sortColumns"] == "TRADE_DATE"
    assert probe["sortTypes"] == "-1"
    assert "filter" not in probe
    main = captured["calls"][1]["params"]
    # 锚点 2026-09-08 往前 DEFAULT_LOOKBACK_DAYS(=5) 个自然日 = 2026-09-03
    assert main["filter"] == (
        "(TRADE_DATE>='2026-09-03 00:00:00')(TRADE_DATE<='2026-09-08 23:59:59')"
    )


def test_fetch_probe_with_empty_result_returns_empty(monkeypatch):
    captured: dict = {}
    _install_fake_requests(monkeypatch, [{"result": {"data": []}}], captured)
    src = LongHubangSource()
    assert src._fetch() == []
    assert src.truncated is False
    assert len(captured["calls"]) == 1


def test_fetch_merges_multiple_trading_days(monkeypatch):
    """多日合并：两个交易日的记录都要保留（不再被覆盖成单日）。"""
    captured: dict = {}
    payload = {
        "result": {
            "data": [
                dict(
                    _VALID_ROW,
                    TRADE_DATE="2026-09-05 00:00:00",
                    SECURITY_CODE="600519",
                ),
                dict(
                    _VALID_ROW,
                    TRADE_DATE="2026-09-04 00:00:00",
                    SECURITY_CODE="000001",
                ),
                dict(
                    _VALID_ROW,
                    TRADE_DATE="2026-09-04 00:00:00",
                    SECURITY_CODE="000002",
                ),
            ]
        }
    }
    _install_fake_requests(monkeypatch, [payload], captured)
    items = LongHubangSource()._fetch(trade_date="2026-09-05")
    assert len(items) == 3
    assert {i.trade_date[:10] for i in items} == {"2026-09-05", "2026-09-04"}
    # 服务端按 TRADE_DATE 降序返回，客户端不再二次排序（顺序原样保留）
    assert [i.trade_date[:10] for i in items] == [
        "2026-09-05",
        "2026-09-04",
        "2026-09-04",
    ]


def test_fetch_sets_truncated_and_warns_lookback_incomplete(monkeypatch, caplog):
    full_page = [dict(_VALID_ROW, SECURITY_CODE=f"{i:06d}") for i in range(_PAGE_SIZE)]
    captured: dict = {}
    _install_fake_requests(monkeypatch, [{"result": {"data": full_page}}], captured)
    src = LongHubangSource()
    with caplog.at_level("WARNING", logger="aqsp.data.longhubang"):
        items = src._fetch(trade_date="2026-09-05")
    assert len(items) == _PAGE_SIZE
    assert src.truncated is True
    # 必须说清「lookback 覆盖不完整」，否则下游会以为 5 天都看全了
    assert any("lookback 覆盖不完整" in r.message for r in caplog.records)


def test_fetch_not_truncated_below_page_size(monkeypatch, caplog):
    captured: dict = {}
    _install_fake_requests(
        monkeypatch,
        [{"result": {"data": [dict(_VALID_ROW)] * (_PAGE_SIZE - 1)}}],
        captured,
    )
    src = LongHubangSource()
    with caplog.at_level("WARNING", logger="aqsp.data.longhubang"):
        src._fetch(trade_date="2026-09-05")
    assert src.truncated is False
    assert not [r for r in caplog.records if "截断" in r.message]


def test_fetch_illegal_trade_date_raises_data_error(monkeypatch):
    captured: dict = {}
    _install_fake_requests(
        monkeypatch, [{"result": {"data": [dict(_VALID_ROW)]}}], captured
    )
    with pytest.raises(DataError, match="trade_date 非法"):
        LongHubangSource()._fetch(trade_date="不是日期")
    assert captured["calls"] == []  # 参数非法时不该发请求


def test_fetch_script_reports_real_covered_span():
    """预加载脚本打印的覆盖区间必须是实际数据的 min~max（不是请求窗口）。"""
    from scripts.fetch_longhubang import _span

    assert _span(["2026-09-05 00:00:00", "2026-09-01"]) == "2026-09-01 ~ 2026-09-05"
    assert _span([]) == "-"
    assert _span(["", None]) == "-"
