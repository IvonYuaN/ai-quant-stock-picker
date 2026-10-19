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


def test_info_stream_drops_noise_concept_labels_before_ranking():
    """噪声标签（指数成分/交易机制）必须被剔除，且不得占掉异动榜名额。"""
    payload = info_stream.get_info_stream(
        force=True,
        news_source=ClsNewsSource().from_items([_news_item()]),
        concept_source=ConceptBoardSource().from_items(
            [
                _concept_item("融资融券", 9.9),  # 噪声，涨得最猛也必须出局
                _concept_item("沪深300_", 8.8),  # 噪声
                _concept_item("光模块", 5.0),
                _concept_item("种业", 3.0),
            ]
        ),
    )
    assert [c["board_name"] for c in payload["concepts"]] == ["光模块", "种业"]


def test_info_stream_annotates_each_concept_with_its_narrative():
    payload = info_stream.get_info_stream(
        force=True,
        news_source=ClsNewsSource().from_items([_news_item()]),
        concept_source=ConceptBoardSource().from_items(
            [_concept_item("光通信模块", 5.0), _concept_item("TOP贴片", 4.0)]
        ),
    )
    by_name = {c["board_name"]: c["narrative"] for c in payload["concepts"]}
    assert by_name["光通信模块"] == "光通信/CPO"
    assert by_name["TOP贴片"] is None  # 未映射不等于噪声


def test_info_stream_merges_synonym_concepts_into_one_narrative():
    """同一主线的多个标签必须合成一条，而不是把主线切碎成 4 条。"""
    payload = info_stream.get_info_stream(
        force=True,
        news_source=ClsNewsSource().from_items([_news_item()]),
        concept_source=ConceptBoardSource().from_items(
            [
                _concept_item("CPO", 6.0),
                _concept_item("光模块", 4.0),
                _concept_item("光通信模块", 5.0),
                _concept_item("种业", 3.0),
            ]
        ),
    )
    names = [n["name"] for n in payload["narratives"]]
    assert names == ["光通信/CPO", "农业"]
    cpo = payload["narratives"][0]
    assert cpo["label_count"] == 3
    assert cpo["max_change_pct"] == 6.0
    assert cpo["mean_change_pct"] == 5.0


def test_info_stream_narratives_are_empty_when_concept_source_fails():
    payload = info_stream.get_info_stream(
        force=True,
        news_source=ClsNewsSource().from_items([_news_item()]),
        concept_source=_FailingConceptSource(),
    )
    assert payload["concepts"] == []
    assert payload["narratives"] == []
    assert payload["sources"]["concept_board"].startswith("error:")


# --------------------------------------------------------------------------
# 证据链（#317 W3）：快讯区的「数据时间」必须来自各条 ctime，不是取数时刻
# --------------------------------------------------------------------------


def _news_item_at(item_id: str, ctime: str) -> ClsNewsItem:
    return ClsNewsItem(
        item_id=item_id,
        title=f"快讯{item_id}",
        summary="正文",
        ctime=ctime,
        level="A",
        subjects=(),
    )


def test_news_latest_ctime_is_newest_item_time():
    payload = info_stream.get_info_stream(
        force=True,
        news_source=ClsNewsSource().from_items(
            [
                _news_item_at("1", "2026-10-19T09:15:00+08:00"),
                _news_item_at("2", "2026-10-19T11:42:00+08:00"),
                _news_item_at("3", "2026-10-18T22:00:00+08:00"),
            ]
        ),
        concept_source=ConceptBoardSource().from_items([_concept_item()]),
    )
    assert payload["news_latest_ctime"] == "2026-10-19T11:42:00+08:00"
    # 与 generated_at（取数时刻）不是一回事：停更时前者会停在旧日期
    assert payload["generated_at"] != payload["news_latest_ctime"]


def test_news_latest_ctime_is_null_when_news_source_fails():
    payload = info_stream.get_info_stream(
        force=True,
        news_source=_FailingNewsSource(),
        concept_source=ConceptBoardSource().from_items([_concept_item()]),
    )
    assert payload["news"] == []
    assert payload["news_latest_ctime"] is None  # 不拿 generated_at 顶替
