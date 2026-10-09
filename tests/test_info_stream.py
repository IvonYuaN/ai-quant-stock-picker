"""小道信息流聚合测试（issue #317 W2）：全部用注入源，不打外部接口。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import info_stream  # noqa: E402
from aqsp.data.cls_news import ClsNewsItem, ClsNewsSource  # noqa: E402
from aqsp.data.concept_board import ConceptBoardItem, ConceptBoardSource  # noqa: E402
from aqsp.core.errors import DataError  # noqa: E402


def _news_item(item_id: str = "1", title: str = "测试快讯") -> ClsNewsItem:
    return ClsNewsItem(
        item_id=item_id,
        title=title,
        summary=f"【{title}】正文",
        ctime="2026-10-09T15:00:00+08:00",
        level="A",
        subjects=("宏观",),
    )


def _concept_item(name: str = "AI语料", change_pct: float = 5.0) -> ConceptBoardItem:
    return ConceptBoardItem(
        board_code="BK0001",
        board_name=name,
        up_count=30,
        down_count=2,
        change_pct=change_pct,
        main_net_inflow=10000.0,
        main_net_ratio=5.0,
    )


class _FailingNewsSource(ClsNewsSource):
    def items(self, autoload: bool = False, limit: int = 50):
        raise DataError("cls_news: 模拟网络失败")


class _FailingConceptSource(ConceptBoardSource):
    def items(self, autoload: bool = False):
        raise DataError("concept_board: 模拟网络失败")


def test_info_stream_merges_news_and_concepts_with_source_labels():
    payload = info_stream.get_info_stream(
        force=True,
        news_source=ClsNewsSource().from_items([_news_item("1", "央行降准")]),
        concept_source=ConceptBoardSource().from_items(
            [_concept_item("AI语料", 5.99), _concept_item("TOP贴片", 6.01)]
        ),
    )
    assert payload["sources"] == {"cls_news": "ok", "concept_board": "ok"}
    assert payload["news"][0]["title"] == "央行降准"
    assert payload["news"][0]["source"] == "财联社电报"
    # 概念异动榜按涨跌幅降序
    assert [c["board_name"] for c in payload["concepts"]] == ["TOP贴片", "AI语料"]
    assert payload["generated_at"].startswith("2026-")


def test_info_stream_degrades_single_source_with_explicit_error():
    """单源失败 ⇒ 该区为空 + sources 显式 error（缺材料可见，不静默）。"""
    payload = info_stream.get_info_stream(
        force=True,
        news_source=_FailingNewsSource(),
        concept_source=ConceptBoardSource().from_items([_concept_item("AI语料", 5.99)]),
    )
    assert payload["news"] == []
    assert payload["sources"]["cls_news"].startswith("error:")
    assert payload["sources"]["concept_board"] == "ok"
    assert len(payload["concepts"]) == 1
