from __future__ import annotations

from aqsp.news.entity_graph import (
    DEFAULT_ENTITY_GRAPH,
    EntityGraph,
    GraphEntity,
    match_news_entities,
)


def test_news_entity_graph_matches_company_alias_and_supply_chain_theme() -> None:
    result = match_news_entities("英伟达发布 Physical AI 平台")

    assert "AI算力" in result.sectors
    assert "机器人" in result.sectors
    assert any(item.canonical == "英伟达" for item in result.matches)


def test_news_entity_graph_matches_summary_aliases() -> None:
    result = match_news_entities(
        "客户推出新产品",
        "上游印制电路板供应紧张，沪电股份订单增加。",
    )

    assert "002463" in result.symbols
    assert "PCB" in result.sectors


def test_news_entity_graph_matches_cross_market_supply_chain_aliases() -> None:
    result = match_news_entities("国防订单增长，LNG供应紧张推升金价")

    assert {"军工电子", "油气", "黄金"}.issubset(set(result.sectors))


def test_news_entity_graph_matches_explicit_six_digit_codes() -> None:
    result = match_news_entities("公告关注 300750 与 688256 的产业链影响")

    assert result.symbols == ("300750", "688256")


def test_news_entity_graph_does_not_match_short_ascii_alias_inside_word() -> None:
    result = match_news_entities("price policy discussion without semiconductor terms")

    assert "半导体" not in result.sectors


def test_news_entity_graph_is_deterministic_and_immutable() -> None:
    first = DEFAULT_ENTITY_GRAPH.resolve("NVIDIA 与 Space X 推出新平台")
    second = DEFAULT_ENTITY_GRAPH.resolve("NVIDIA 与 Space X 推出新平台")

    assert first == second
    assert isinstance(DEFAULT_ENTITY_GRAPH, EntityGraph)
    assert isinstance(DEFAULT_ENTITY_GRAPH.entities[0], GraphEntity)
    assert DEFAULT_ENTITY_GRAPH.entities[0].aliases
