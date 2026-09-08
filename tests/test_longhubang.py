"""龙虎榜 fetcher 测试。"""

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
                    "SIDE": "净买入",
                    "OPERATE_DEPT_NAME": "某证券上海分公司",
                    "BUY_AMT": 12345.67,
                    "SELL_AMT": 0.0,
                    "NET_AMT": 12345.67,
                    "EXPLANATION": "一线游资",
                }
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "600519"
    assert it.name == "贵州茅台"
    assert it.dept_name == "某证券上海分公司"
    assert it.buy_amount == pytest.approx(12345.67)


def test_parse_skips_malformed():
    payload = {
        "result": {
            "data": [
                {"SECURITY_CODE": "000001"},
                {
                    "TRADE_DATE": "x",
                    "SECURITY_CODE": "000002",
                    "SECURITY_NAME_ABBR": "ok",
                    "OPERATE_DEPT_NAME": "d",
                    "SIDE": "买",
                },
            ]
        }
    }
    items = _parse_items(payload)
    # 第一条 name 缺但 dataclass 默认空字符串，_parse 不抛；第二条完整
    assert len(items) == 2
    assert items[1].symbol == "000002"


def test_source_from_items():
    src = LongHubangSource().from_items(
        [
            LongHubangItem(
                trade_date="2026-09-05",
                symbol="000001",
                name="平安银行",
                side="买",
                dept_name="d",
                buy_amount=1.0,
                sell_amount=0.0,
                net_amount=1.0,
                interpretation="",
            )
        ]
    )
    assert src.items(autoload=False)[0].symbol == "000001"
