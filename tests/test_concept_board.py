"""东财概念板块 fetcher 测试（字段按 2026-09-08 生产机实测响应）。"""

from __future__ import annotations

import pytest

from aqsp.data.concept_board import ConceptBoardItem, ConceptBoardSource, _parse_items


def test_parse_real_clist_shape():
    payload = {
        "data": {
            "diff": [
                {
                    "f12": "BK0001",
                    "f14": "人工智能",
                    "f104": 120,
                    "f105": 30,
                    "f3": 1.23,
                    "f62": 567890000.0,  # 元
                    "f184": 2.5,
                }
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 1
    it = items[0]
    assert it.board_code == "BK0001"
    assert it.board_name == "人工智能"
    assert it.up_count == 120
    assert it.down_count == 30
    assert it.change_pct == pytest.approx(1.23)
    # 元 → 万元
    assert it.main_net_inflow == pytest.approx(56789.0)
    assert it.main_net_ratio == pytest.approx(2.5)


def test_parse_skips_malformed():
    payload = {
        "data": {
            "diff": [
                {"only": "junk"},
                {"f12": "BK2", "f14": "n", "f3": 0.0},
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 2
    assert items[1].board_name == "n"
    assert items[1].up_count == 0


def test_source_from_items():
    src = ConceptBoardSource().from_items(
        [
            ConceptBoardItem(
                board_code="BK1",
                board_name="x",
                up_count=1,
                down_count=0,
                change_pct=0.0,
                main_net_inflow=0.0,
                main_net_ratio=0.0,
            )
        ]
    )
    assert src.items(autoload=False)[0].board_code == "BK1"
