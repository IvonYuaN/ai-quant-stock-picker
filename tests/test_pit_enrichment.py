"""pit_enrichment 集成适配器测试：优雅回退 + PIT 生效两路。

另锁死 #335 断裂3：默认单例必须从 pit_cache 缓存预热（缓存只读、绝不联网），
否则 industry_pit/macro_pit 运行时永远空转。
"""

from __future__ import annotations

import pandas as pd
import pytest

from aqsp.data import industry_pit
from aqsp.data import macro_pit as macro_pit_mod
from aqsp.data.industry_pit import IndustryPitSource
from aqsp.data.macro_pit import MacroPitSource, MacroPoint
from aqsp.features import pit_enrichment as pe
from aqsp.features.pit_enrichment import (
    macro_pit_context,
    pit_sector_industry_maps,
)


class _Pick:
    def __init__(self, symbol: str, sector: str = "", industry: str = "") -> None:
        self.symbol = symbol
        self.metrics = {"sector": sector, "industry": industry}


def _raw_sw() -> pd.DataFrame:
    return pd.DataFrame(
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


def _make_industry_source() -> IndustryPitSource:
    return IndustryPitSource().from_dataframe(_raw_sw())


def _reset_default_singletons(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """把默认单例钉到空 tmp data root + 复位预热标记（隔离真实环境缓存）。"""
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(pe, "_DEFAULT_INDUSTRY_SOURCE", IndustryPitSource())
    monkeypatch.setattr(pe, "_DEFAULT_MACRO_SOURCE", MacroPitSource())
    monkeypatch.setattr(pe, "_cache_preload_attempted", False)


def test_pit_falls_back_to_existing_label_when_source_unloaded(
    tmp_path, monkeypatch
) -> None:
    _reset_default_singletons(monkeypatch, tmp_path)
    # 即使环境里有真实缓存也绝不联网：联网即测试失败
    monkeypatch.setattr(
        industry_pit,
        "sw_industry_history",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不允许联网")),
    )
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


def test_macro_empty_when_source_unloaded(tmp_path, monkeypatch) -> None:
    _reset_default_singletons(monkeypatch, tmp_path)
    monkeypatch.setattr(
        macro_pit_mod,
        "_fetch_social_financing",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不允许联网")),
    )
    monkeypatch.setattr(
        macro_pit_mod,
        "_fetch_pmi",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不允许联网")),
    )
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


# ---------------------------------------------------------------------------
# #335 断裂3：默认单例缓存预热 —— mock 缓存文件后，消费者经默认路径能取到数据
# ---------------------------------------------------------------------------
def test_default_singletons_preload_from_cache(tmp_path, monkeypatch) -> None:
    _reset_default_singletons(monkeypatch, tmp_path)
    # 生产者落盘形态写缓存：行业（规范表 to_csv）+ 宏观（__dict__ DataFrame）
    industry_csv = IndustryPitSource()._default_cache_path()
    IndustryPitSource().from_dataframe(_raw_sw())._df.to_csv(industry_csv, index=False)
    macro_points = [
        MacroPoint(month="2026-07", social_financing_increment=0.5, pmi_manufacturing=49.3)
    ]
    macro_csv = MacroPitSource()._default_cache_path()
    pd.DataFrame([p.__dict__ for p in macro_points]).to_csv(macro_csv, index=False)

    # 默认路径（不显式传 source）：预热后取到 PIT 数据而非回退标签
    picks = [_Pick("000001", sector="OLD", industry="OLD")]
    sector_map, industry_map = pit_sector_industry_maps(picks, "2026-08-18")
    assert sector_map["000001"] == "480100", "缓存预热后必须用 PIT 行业码（非 OLD 回退）"
    assert industry_map["000001"] == "480100"

    ctx = macro_pit_context("2026-08-15")
    assert ctx["macro_social_financing_increment"] == 0.5
    assert ctx["macro_pmi_manufacturing"] == 49.3


def test_cache_preload_is_once_per_process(tmp_path, monkeypatch) -> None:
    """预热只做一次（进程内幂等），不随每次筛选重复读盘。"""
    _reset_default_singletons(monkeypatch, tmp_path)
    industry_csv = IndustryPitSource()._default_cache_path()
    IndustryPitSource().from_dataframe(_raw_sw())._df.to_csv(industry_csv, index=False)
    picks = [_Pick("000001")]
    pit_sector_industry_maps(picks, "2026-08-18")
    assert pe._cache_preload_attempted is True
    pit_sector_industry_maps(picks, "2026-08-18")
    assert pe._cache_preload_attempted is True  # 仍是 True，无重复读盘副作用
