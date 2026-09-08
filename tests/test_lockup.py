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
                    "PLAN_DATE": "2026-09-15 00:00:00",
                    "LIFTING_VOL": 1234.5,
                    "LIFTING_RATIO": 0.0264,
                    "LIFTING_TYPE": "首发",
                }
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "300750"
    assert it.lockup_type == "首发"
    assert it.ratio == pytest.approx(0.0264)


def test_parse_skips_malformed_returns_at_least_valid():
    payload = {
        "result": {
            "data": [
                {"x": 1},
                {
                    "SECURITY_CODE": "000001",
                    "SECURITY_NAME_ABBR": "ok",
                    "PLAN_DATE": "2026-01-01",
                    "LIFTING_VOL": 1,
                    "LIFTING_RATIO": 0.01,
                    "LIFTING_TYPE": "定增",
                },
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 2
    assert items[1].lockup_type == "定增"


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
