"""Deterministic news entity and supply-chain alias matching.

This module deliberately contains no network, model, or mutable runtime state.
The graph is a small, reviewable knowledge base used to enrich news evidence;
it does not score or rank a catalyst.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

EntityKind = Literal["company", "sector", "theme"]


@dataclass(frozen=True)
class GraphEntity:
    """One canonical entity and its deterministic aliases."""

    kind: EntityKind
    canonical: str
    aliases: tuple[str, ...]
    symbols: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityMatch:
    """A graph hit, retaining the exact alias found in source text."""

    kind: EntityKind
    canonical: str
    alias: str
    symbols: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityResolution:
    """Normalized entities found in a news title and summary."""

    matches: tuple[EntityMatch, ...] = ()
    symbols: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()


def _clean_alias(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().casefold())


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return tuple(result)


def _valid_symbol(value: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "")))


def _contains_alias(normalized_text: str, alias: str) -> bool:
    """Match short ASCII aliases without matching inside another word."""

    clean = _clean_alias(alias)
    if not clean:
        return False
    if re.fullmatch(r"[a-z0-9]+", clean):
        return (
            re.search(rf"(?<![a-z0-9]){re.escape(clean)}(?![a-z0-9])", normalized_text)
            is not None
        )
    return clean in normalized_text


@dataclass(frozen=True)
class EntityGraph:
    """Immutable alias graph with stable insertion-order matching."""

    entities: tuple[GraphEntity, ...]

    def canonicalize_sector_labels(self, labels: object) -> tuple[str, ...]:
        """Map source/universe industry labels to the graph's stable names."""
        values: list[str] = []
        if isinstance(labels, str):
            raw = re.split(r"[,，;；|/、\s]+", labels)
        elif isinstance(labels, (list, tuple, set, frozenset)):
            raw = [str(item) for item in labels]
        else:
            raw = []
        sector_entities = tuple(item for item in self.entities if item.kind == "sector")
        for item in raw:
            for part in re.split(r"[,，;；|/、\s]+", str(item or "")):
                clean = part.strip()
                normalized = _clean_alias(clean)
                if not normalized:
                    continue
                match = next(
                    (
                        entity
                        for entity in sector_entities
                        if any(
                            _clean_alias(alias) == normalized
                            for alias in entity.aliases
                        )
                        or _clean_alias(entity.canonical) == normalized
                    ),
                    None,
                )
                canonical = match.canonical if match is not None else clean
                if canonical not in values:
                    values.append(canonical)
        return tuple(values)

    def resolve(self, title: str = "", summary: str = "") -> EntityResolution:
        text = f"{title}\n{summary}"
        normalized = _clean_alias(text)
        matches: list[EntityMatch] = []
        symbols: list[str] = []
        sectors: list[str] = []

        # A code is itself an explicit entity reference. Keep this independent
        # of the curated company table so new listed companies are not missed.
        for symbol in re.findall(r"(?<!\d)\d{6}(?!\d)", text):
            if symbol not in symbols:
                symbols.append(symbol)
            matches.append(
                EntityMatch(
                    kind="company",
                    canonical=symbol,
                    alias=symbol,
                    symbols=(symbol,),
                )
            )

        for entity in self.entities:
            aliases = sorted(
                (alias for alias in entity.aliases if _clean_alias(alias)),
                key=lambda value: (-len(_clean_alias(value)), value),
            )
            alias = next(
                (
                    candidate
                    for candidate in aliases
                    if _contains_alias(normalized, candidate)
                ),
                None,
            )
            if alias is None:
                continue
            match = EntityMatch(
                kind=entity.kind,
                canonical=entity.canonical,
                alias=alias,
                symbols=entity.symbols,
                sectors=entity.sectors
                or ((entity.canonical,) if entity.kind in {"sector", "theme"} else ()),
            )
            matches.append(match)
            for symbol in match.symbols:
                if _valid_symbol(symbol) and symbol not in symbols:
                    symbols.append(symbol)
            for sector in match.sectors:
                if sector not in sectors:
                    sectors.append(sector)

        return EntityResolution(
            matches=tuple(matches),
            symbols=_unique(symbols),
            sectors=_unique(sectors),
        )


def _company(
    canonical: str,
    symbols: tuple[str, ...],
    aliases: tuple[str, ...],
    sectors: tuple[str, ...],
) -> GraphEntity:
    return GraphEntity("company", canonical, aliases, symbols, sectors)


def _sector(canonical: str, aliases: tuple[str, ...]) -> GraphEntity:
    return GraphEntity("sector", canonical, aliases, (), (canonical,))


# This is intentionally explicit rather than inferred from a data source. Each
# entry is easy to review and can be extended without changing matching logic.
DEFAULT_ENTITY_GRAPH = EntityGraph(
    entities=(
        _company("深科技", ("000021",), ("深科技", "深圳长城开发"), ("存储", "半导体")),
        _company("沪电股份", ("002463",), ("沪电股份", "沪电"), ("PCB",)),
        _company("胜宏科技", ("300476",), ("胜宏科技",), ("PCB",)),
        _company("生益科技", ("600183",), ("生益科技",), ("PCB", "覆铜板")),
        _company("景旺电子", ("603228",), ("景旺电子",), ("PCB",)),
        _company("浪潮信息", ("000977",), ("浪潮信息", "浪潮"), ("服务器", "AI算力")),
        _company("工业富联", ("601138",), ("工业富联",), ("服务器", "AI算力")),
        _company("中际旭创", ("300308",), ("中际旭创",), ("光模块", "AI算力")),
        _company("天孚通信", ("300394",), ("天孚通信",), ("光模块", "AI算力")),
        _company("光迅科技", ("002281",), ("光迅科技",), ("光模块", "AI算力")),
        _company("北方华创", ("002371",), ("北方华创",), ("半导体设备", "半导体")),
        _company("中微公司", ("688012",), ("中微公司", "中微"), ("半导体设备",)),
        _company("寒武纪", ("688256",), ("寒武纪",), ("AI算力", "半导体")),
        _company("宁德时代", ("300750",), ("宁德时代", "宁德"), ("电池", "新能源汽车")),
        _company("航天科技", ("000901",), ("航天科技",), ("商业航天", "军工电子")),
        _company(
            "英伟达",
            (),
            ("英伟达", "NVIDIA", "Nvidia"),
            ("AI算力", "具身智能", "机器人"),
        ),
        _company("AMD", (), ("AMD",), ("AI算力", "半导体")),
        _company(
            "SpaceX",
            (),
            ("SpaceX", "Space X", "星舰", "Starlink"),
            ("商业航天", "卫星互联网"),
        ),
        _sector("PCB", ("PCB", "印制电路板", "电路板", "线路板")),
        _sector("覆铜板", ("覆铜板", "铜箔基板", "CCL")),
        _sector("存储", ("存储芯片", "存储器", "HBM", "DRAM", "NAND", "内存")),
        _sector("半导体", ("半导体", "芯片", "集成电路", "IC")),
        _sector("半导体设备", ("半导体设备", "刻蚀设备", "薄膜沉积", "晶圆设备")),
        _sector("先进封装", ("先进封装", "Chiplet", "芯粒")),
        _sector("商业航天", ("商业航天", "卫星互联网", "低轨卫星", "火箭发射")),
        _sector(
            "机器人", ("机器人", "人形机器人", "具身智能", "Physical AI", "Robotics")
        ),
        _sector("AI算力", ("AI算力", "人工智能算力", "数据中心", "服务器", "GPU")),
        _sector("光模块", ("光模块", "800G", "1.6T", "光通信模块")),
        _sector("军工电子", ("军工", "军工电子", "国防装备")),
        _sector("黄金", ("黄金", "贵金属", "金价")),
        _sector("油气", ("原油", "油价", "石油", "天然气")),
        _sector("稀土资源", ("稀土", "氧化镨钕", "钕铁硼", "永磁")),
        _sector("锂电材料", ("锂电", "碳酸锂", "氢氧化锂", "电解液", "正极", "负极")),
        _sector("MLCC", ("MLCC", "多层陶瓷电容")),
    ),
)


def match_news_entities(
    title: str = "", summary: str = "", *, graph: EntityGraph = DEFAULT_ENTITY_GRAPH
) -> EntityResolution:
    """Resolve known company, code, sector, and theme aliases deterministically."""

    return graph.resolve(title=title, summary=summary)


__all__ = [
    "DEFAULT_ENTITY_GRAPH",
    "EntityGraph",
    "EntityKind",
    "EntityMatch",
    "EntityResolution",
    "GraphEntity",
    "match_news_entities",
]
