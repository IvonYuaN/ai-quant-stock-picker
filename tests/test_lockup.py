"""限售解禁 fetcher 测试。"""

from __future__ import annotations

import pytest

from aqsp.data.lockup import LockupItem, LockupSource, _parse_items


def test_parse_real_shape():
    payload = {
        "result": {
            "data": [
                {
                    "SECURITY_CODE": "300750",
                    "SECURITY_NAME_ABBR": "宁德时代",
                    "FREE_DATE": "2026-09-15 00:00:00",
                    "FREE_SHARES": 1234.5,
                    "FREE_RATIO": 0.0264,
                    "FREE_SHARES_TYPE": "首发原股东限售股份",
                }
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "300750"
    assert it.lockup_type == "首发原股东限售股份"
    assert it.ratio == pytest.approx(0.0264)


def test_parse_skips_malformed_returns_at_least_valid():
    payload = {
        "result": {
            "data": [
                {"x": 1},
                {
                    "SECURITY_CODE": "000001",
                    "SECURITY_NAME_ABBR": "ok",
                    "FREE_DATE": "2026-01-01",
                    "FREE_SHARES": 1,
                    "FREE_RATIO": 0.01,
                    "FREE_SHARES_TYPE": "定向增发机构配售股份",
                },
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 2
    assert items[1].lockup_type == "定向增发机构配售股份"


def test_source_from_items():
    src = LockupSource().from_items(
        [
            LockupItem(
                symbol="000001",
                name="x",
                plan_date="2026-01-01",
                lockup_shares=1.0,
                ratio=0.01,
                lockup_type="首发",
            )
        ]
    )
    assert src.items(autoload=False)[0].symbol == "000001"
