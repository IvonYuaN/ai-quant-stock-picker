from __future__ import annotations

import pytest

from aqsp.news.catalysts import CatalystEvent
from aqsp.news.watch_candidates import (
    NewsUniverseInstrument,
    discover_watch_candidates,
)


def _event(**kwargs: object) -> CatalystEvent:
    values: dict[str, object] = {
        "title": "PCB覆铜板报价上调，供应紧张",
        "source": "财联社",
        "published_at": "2026-07-20 09:00:00+08:00",
        "category": "电子材料涨价/缺货",
        "weight": 5,
        "confidence": 0.9,
        "source_quality_score": 3,
        "summary": "覆铜板供应收紧，厂商报价继续上调。",
        "affected_sectors": ("PCB", "覆铜板"),
        "transmission_path": ("上游材料涨价", "PCB成本上升", "下游订单与利润分化"),
        "transmission_hypothesis": "材料涨价 -> PCB成本 -> 关注有议价权公司",
        "supporting_evidence": ("财联社: PCB覆铜板报价上调",),
        "validation_signals": ("PCB现货报价继续上行",),
        "invalidation_signals": ("报价回落且库存回升",),
    }
    values.update(kwargs)
    return CatalystEvent(**values)  # type: ignore[arg-type]


def test_discover_watch_candidates_expands_event_to_full_universe() -> None:
    candidates = discover_watch_candidates(
        (_event(),),
        (
            NewsUniverseInstrument("002463", "沪电股份", ("PCB",)),
            NewsUniverseInstrument("300476", "胜宏科技", ("PCB",)),
            NewsUniverseInstrument("600183", "生益科技", ("覆铜板",)),
            NewsUniverseInstrument("000001", "平安银行", ("银行",)),
        ),
    )

    assert {item.symbol for item in candidates} == {"300476", "600183", "002463"}
    assert all(item.relation == "price_supply" for item in candidates)
    assert all(item.source == "财联社" for item in candidates)
    assert candidates[0].summary == "覆铜板供应收紧，厂商报价继续上调。"
    assert candidates[0].transmission_path == (
        "上游材料涨价",
        "PCB成本上升",
        "下游订单与利润分化",
    )


def test_discover_watch_candidates_prefers_direct_company_link() -> None:
    event = _event(
        title="沪电股份公告新一代高速PCB产品量产",
        summary="沪电股份新产品开始量产。",
        affected_symbols=("002463",),
        affected_sectors=("PCB",),
        category="新品/产品发布",
    )
    candidates = discover_watch_candidates(
        (event,),
        (
            NewsUniverseInstrument("002463", "沪电股份", ("PCB",)),
            NewsUniverseInstrument("300476", "胜宏科技", ("PCB",)),
        ),
    )

    assert candidates[0].symbol == "002463"
    assert candidates[0].relation == "company"
    assert candidates[1].relation == "product"


def test_discover_watch_candidates_accepts_mapping_universe_and_deduplicates_events() -> (
    None
):
    first = _event(source="公司公告", source_quality_score=4)
    second = _event(
        title="覆铜板供给收紧",
        source="路透",
        weight=4,
        summary="海外供应商确认短期供给偏紧。",
    )
    candidates = discover_watch_candidates(
        (first, second),
        (
            {"代码": "600183", "名称": "生益科技", "行业": "电子材料,PCB"},
            {"代码": "600183", "名称": "重复行", "行业": "PCB"},
        ),
    )

    assert len(candidates) == 1
    assert candidates[0].symbol == "600183"
    assert candidates[0].source == "公司公告、路透"
    assert set(candidates[0].affected_sectors) == {"PCB", "覆铜板"}


def test_discover_watch_candidates_preserves_conflicting_evidence() -> None:
    candidates = discover_watch_candidates(
        (
            _event(impact="positive", title="PCB订单增长"),
            _event(
                impact="negative",
                title="PCB下游需求放缓",
                weight=4,
                source="路透",
            ),
        ),
        (NewsUniverseInstrument("002463", "沪电股份", ("PCB",)),),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.impact_direction == "mixed"
    assert candidate.supporting_event_count == 1
    assert candidate.contradicting_event_count == 1
    assert candidate.evidence_stack_summary == "支持 1 条｜反对 1 条"


def test_discover_watch_candidates_normalizes_exchange_qualified_symbols() -> None:
    event = _event(affected_symbols=("SZ.002463",))
    candidates = discover_watch_candidates(
        (event,),
        ({"代码": "002463.SZ", "名称": "沪电股份", "行业": "电子材料,PCB"},),
    )

    assert len(candidates) == 1
    assert candidates[0].relation == "company"


@pytest.mark.parametrize(
    ("title", "category", "sector", "relation"),
    [
        ("PCB覆铜板报价上调，供应紧张", "电子材料涨价/缺货", "PCB", "price_supply"),
        ("HBM缺货，内存价格上涨", "存储涨价/缺货", "存储", "price_supply"),
        ("厂商发布新一代800G产品", "新品/产品发布", "光模块", "product"),
        ("半导体设备订单大增", "订单/需求验证", "半导体设备", "supply_chain"),
        ("地缘冲突升级，防务合同增加", "地缘冲突", "军工电子", "geopolitical"),
        ("地缘风险推升金价", "黄金/贵金属催化", "黄金", "geopolitical"),
        ("LNG供应中断，天然气价格上涨", "油气供需催化", "油气", "price_supply"),
        ("国防采购合同落地", "军工订单/政策", "军工电子", "supply_chain"),
    ],
)
def test_news_watch_candidates_reaches_each_requested_chain(
    title: str, category: str, sector: str, relation: str
) -> None:
    event = CatalystEvent(
        title=title,
        summary=f"{title}，等待产业数据确认。",
        source="公司公告",
        published_at="2026-07-20T09:00:00+08:00",
        category=category,
        confidence=0.9,
        weight=5,
        source_quality_score=4,
        affected_sectors=(sector,),
        transmission_path=("消息事实", "产业链传导"),
        validation_signals=("价格或订单继续确认",),
        invalidation_signals=("验证指标反向",),
    )

    candidates = discover_watch_candidates(
        (event,),
        (NewsUniverseInstrument("600000", "测试标的", (sector,)),),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.relation == relation
    assert candidate.source == "公司公告"
    assert candidate.summary.startswith(title)
    assert candidate.published_at.endswith("+08:00")
    assert candidate.transmission_path == ("消息事实", "产业链传导")
    assert candidate.validation_signals == ("价格或订单继续确认",)
    assert candidate.invalidation_signals == ("验证指标反向",)
