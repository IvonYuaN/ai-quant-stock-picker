"""东财概念板块 fetcher 测试。"""

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
                    "f20": 156,
                    "f3": 1.23,
                    "f184": 56789.0,
                }
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 1
    it = items[0]
    assert it.board_code == "BK0001"
    assert it.board_name == "人工智能"
    assert it.constituent_count == 156
    assert it.change_pct == pytest.approx(1.23)
    assert it.main_net_inflow == pytest.approx(56789.0)


def test_parse_skips_malformed():
    payload = {
        "data": {
            "diff": [
                {"only": "junk"},
                {"f12": "BK2", "f14": "n", "f20": 1, "f3": 0.0, "f184": 0.0},
            ]
        }
    }
    items = _parse_items(payload)
    assert len(items) == 2
    assert items[1].board_name == "n"


def test_source_from_items():
    src = ConceptBoardSource().from_items(
        [
            ConceptBoardItem(
                board_code="BK1",
                board_name="x",
                constituent_count=1,
                change_pct=0.0,
                main_net_inflow=0.0,
            )
        ]
    )
    assert src.items(autoload=False)[0].board_code == "BK1"
