"""pit_enrichment 集成适配器测试：优雅回退 + PIT 生效两路。"""

from __future__ import annotations

import pandas as pd

from aqsp.data.industry_pit import IndustryPitSource
from aqsp.data.macro_pit import MacroPitSource, MacroPoint
from aqsp.features.pit_enrichment import (
    macro_pit_context,
    pit_sector_industry_maps,
)


class _Pick:
    def __init__(self, symbol: str, sector: str = "", industry: str = "") -> None:
        self.symbol = symbol
        self.metrics = {"sector": sector, "industry": industry}


def _make_industry_source() -> IndustryPitSource:
    df = pd.DataFrame(
        [
            {
                "股票代码": "000001",
                "计入日期": "1991-04-03",
                "行业代码": "480300",
                "更新日期": "1991-04-03",
            },
            {
                "股票代码": "000001",
                "计入日期": "2016-01-01",
                "行业代码": "480100",
                "更新日期": "2016-01-01",
            },
        ]
    )
    return IndustryPitSource().from_dataframe(df)


def test_pit_falls_back_to_existing_label_when_source_unloaded() -> None:
    picks = [_Pick("600519", sector="白酒", industry="食品饮料")]
    sector_map, industry_map = pit_sector_industry_maps(picks, "2026-08-18")
    assert sector_map == {"600519": "白酒"}
    assert industry_map == {"600519": "食品饮料"}


def test_pit_uses_pit_code_when_source_loaded() -> None:
    src = _make_industry_source()
    picks = [_Pick("000001", sector="OLD", industry="OLD")]
    sector_map, industry_map = pit_sector_industry_maps(picks, "2026-08-18", src)
    # 2026 年 000001 属 480100（2016 调整后），非 OLD
    assert sector_map["000001"] == "480100"
    assert industry_map["000001"] == "480100"


def test_pit_unlisted_symbol_omitted() -> None:
    src = _make_industry_source()
    picks = [_Pick("999999", sector="", industry="")]
    sector_map, industry_map = pit_sector_industry_maps(picks, "2026-08-18", src)
    assert "999999" not in sector_map  # 无 PIT 也无现有标签 -> 省略


def test_macro_empty_when_source_unloaded() -> None:
    assert macro_pit_context("2026-08-18") == {}


def test_macro_populated_when_loaded() -> None:
    ms = MacroPitSource().from_points(
        [
            MacroPoint(
                month="2026-07",
                social_financing_increment=0.5,
                pmi_manufacturing=49.3,
            ),
            MacroPoint(
                month="2026-08",
                social_financing_increment=0.7,
                pmi_manufacturing=50.1,
                pmi_composite=50.8,
            ),
        ]
    )
    ctx = macro_pit_context("2026-08-18", ms)
    assert ctx["macro_social_financing_increment"] == 0.7
    assert ctx["macro_pmi_manufacturing"] == 50.1
    assert ctx["macro_pmi_composite"] == 50.8


def test_macro_pit_respects_as_of_month_point_in_time() -> None:
    ms = MacroPitSource().from_points(
        [
            MacroPoint(
                month="2026-07", social_financing_increment=0.5, pmi_manufacturing=49.3
            ),
            MacroPoint(
                month="2026-08", social_financing_increment=0.7, pmi_manufacturing=50.1
            ),
        ]
    )
    # as_of 在 7 月 -> 只用 7 月数据（PIT：未发布月份不返回）
    ctx = macro_pit_context("2026-07-15", ms)
    assert ctx["macro_social_financing_increment"] == 0.5
    assert ctx["macro_pmi_manufacturing"] == 49.3
    # as_of 在 9 月但无 9 月数据 -> 用最新已发布（8 月）
    ctx2 = macro_pit_context("2026-09-10", ms)
    assert ctx2["macro_social_financing_increment"] == 0.7
