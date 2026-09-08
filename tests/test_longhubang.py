"""龙虎榜 fetcher 测试（字段映射按 2026-09-08 生产机实测响应）。"""

from __future__ import annotations

import pytest

from aqsp.data.longhubang import LongHubangItem, LongHubangSource, _parse_items


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
