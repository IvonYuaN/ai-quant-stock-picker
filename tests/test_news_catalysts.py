from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

import aqsp.data.news_source as news_source
import aqsp.news.catalysts as catalysts
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.data.news_source import (
    AkshareNewsSource,
    CompositeNewsSource,
    NewsSource,
    NewsSourceHealth,
    RssFeedConfig,
    RssNewsSource,
    build_default_news_source,
    build_rss_news_source_from_config,
    rss_news_runtime_summary,
)
from aqsp.news.catalysts import (
    CatalystEvent,
    CatalystReport,
    NewsCatalystConfig,
    _classify_title,
    _select_diverse_events,
    build_catalyst_report,
    format_catalyst_notification,
    _akshare_global_news,
    load_catalyst_report_artifact,
    serialize_catalyst_report,
)
from aqsp.market_context import build_market_context_artifact


_RECENT_NEWS_TIME = (now_shanghai() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
_RECENT_NEWS_DATE = today_shanghai().isoformat()


@pytest.mark.parametrize(
    ("title", "category"),
    [
        ("PCB覆铜板厂商宣布新一轮报价上调", "电子材料涨价/缺货"),
        ("高端铜箔供应紧张，厂商称短期缺货", "电子材料涨价/缺货"),
        ("HBM供给紧张，服务器内存出现缺货", "存储涨价/缺货"),
        ("DRAM现货价格上涨，供应趋紧", "存储涨价/缺货"),
        ("半导体设备订单同比大增，刻蚀设备需求提升", "订单/需求验证"),
        ("GPT-Red: Unlocking Self-Improvement for Robustness", "AI/半导体技术动态"),
        ("Minutes of the Board's discount rate meetings", "宏观流动性"),
    ],
)
def test_news_catalyst_classifies_supply_chain_evidence(
    title: str, category: str
) -> None:
    classified = _classify_title(title)

    assert classified is not None
    assert classified[0] == category


@pytest.mark.parametrize(
    "title",
    [
        "PCB企业发布年度报告",
        "HBM产业论坛召开，厂商展示新产品",
        "半导体设备公司参加行业展会",
    ],
)
def test_news_catalyst_does_not_classify_supply_chain_keyword_without_evidence(
    title: str,
) -> None:
    assert _classify_title(title) is None


def test_news_catalyst_maps_supply_chain_evidence_to_affected_sectors() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "PCB覆铜板厂商宣布新一轮报价上调",
                    "来源": "证券报",
                    "时间": _RECENT_NEWS_TIME,
                },
                {
                    "标题": "HBM供给紧张，服务器内存出现缺货",
                    "来源": "路透",
                    "时间": _RECENT_NEWS_TIME,
                },
                {
                    "标题": "半导体设备订单同比大增，刻蚀设备需求提升",
                    "来源": "公司公告",
                    "时间": _RECENT_NEWS_TIME,
                },
            ]
        )
    )

    by_title = {event.title: event for event in report.events}
    assert {"PCB", "覆铜板", "铜箔"}.issubset(
        set(by_title["PCB覆铜板厂商宣布新一轮报价上调"].affected_sectors)
    )
    assert {"存储", "AI算力"}.issubset(
        set(by_title["HBM供给紧张，服务器内存出现缺货"].affected_sectors)
    )
    assert {"半导体设备", "半导体材料"}.issubset(
        set(by_title["半导体设备订单同比大增，刻蚀设备需求提升"].affected_sectors)
    )


@pytest.mark.parametrize(
    ("url", "source"),
    [
        ("https://nvidianews.nvidia.com/news/physical-ai", "NVIDIA Newsroom"),
        ("https://ir.amd.com/news-events/press-releases", "AMD Press Releases"),
        ("https://www.intc.com/news-events/press-releases", "Intel Press Releases"),
        ("https://openai.com/news/", "OpenAI News"),
    ],
)
def test_news_catalyst_marks_overseas_official_sources_as_high_value(
    url: str, source: str
) -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "NVIDIA announces Physical AI robotics platform",
                    "链接": url,
                    "时间": _RECENT_NEWS_TIME,
                }
            ]
        )
    )

    assert report.events
    assert report.events[0].source == source
    assert report.events[0].source_quality_label == "高价值来源"
    assert report.events[0].source_quality_score == 4
    assert report.events[0].source_region == "international"


def test_news_catalyst_selects_ranked_events_from_diverse_sources() -> None:
    def event(title: str, source: str, weight: int) -> CatalystEvent:
        return CatalystEvent(
            title=title,
            source=source,
            published_at=_RECENT_NEWS_TIME,
            weight=weight,
            confidence=0.8,
        )

    selected = _select_diverse_events(
        (
            event("同源高分1", "NVIDIA Newsroom", 10),
            event("同源高分2", "NVIDIA Newsroom", 9),
            event("另一来源", "Reuters", 8),
            event("第三来源", "AMD Press Releases", 7),
        ),
        3,
    )

    assert tuple(item.title for item in selected) == ("同源高分1", "另一来源", "第三来源")
    assert len({item.source for item in selected}) == 3


def test_news_catalyst_builds_explicit_pcb_transmission_chain() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "PCB覆铜板厂商宣布报价上调，电子材料供应紧张",
                    "来源": "产业公告",
                    "时间": _RECENT_NEWS_TIME,
                    "链接": "https://example.com/pcb",
                }
            ]
        ),
        config=NewsCatalystConfig(
            allow_undated_news=False,
            min_confidence=0.1,
            max_news_age_days=7,
        ),
    )

    assert len(report.events) == 1
    event = report.events[0]
    assert event.affected_sectors[0] == "PCB"
    assert event.transmission_path == (
        "原材料/覆铜板价格",
        "PCB厂商报价与订单",
        "高频通信/服务器板需求",
    )
    assert "报价" in event.validation_signals[0]
    assert event.invalidation_signals


def test_news_catalyst_prioritizes_title_theme_over_broad_sector_tags() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "NVIDIA launches Physical AI robotics platform",
                    "来源": "NVIDIA Newsroom",
                    "时间": _RECENT_NEWS_TIME,
                }
            ]
        ),
        config=NewsCatalystConfig(min_confidence=0.1, max_news_age_days=7),
    )

    assert report.events[0].transmission_path == (
        "平台/模型发布",
        "机器人本体与传感器",
        "控制器/算力/伺服",
    )


def test_news_catalyst_report_prioritizes_verified_price_hike_events() -> None:
    def symbol_news(symbol: str, limit: int) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "新闻标题": "MLCC 行业报价上调，龙头排产紧张",
                    "文章来源": "证券报",
                    "发布时间": _RECENT_NEWS_TIME,
                    "新闻链接": "https://example.com/a",
                },
                {
                    "新闻标题": "网传 MLCC 涨价，尚未获得公司确认",
                    "文章来源": "自媒体",
                    "发布时间": _RECENT_NEWS_TIME,
                },
            ]
        )

    def global_news(limit: int) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "标题": "MLCC 行业报价上调，龙头排产紧张",
                    "来源": "财联社",
                    "时间": _RECENT_NEWS_TIME,
                },
                {
                    "标题": "某公司被立案调查",
                    "来源": "公告",
                    "时间": _RECENT_NEWS_TIME,
                },
            ]
        )

    report = build_catalyst_report(
        symbols=("300001",),
        symbol_names={"300001": "样本电子"},
        fetch_symbol_news=symbol_news,
        fetch_global_news=global_news,
        config=NewsCatalystConfig(symbols=("300001",), max_events=5),
    )

    assert report.source_status == "ok"
    assert report.events[0].category == "涨价/供需催化"
    assert report.events[0].source_count == 2
    assert report.events[0].verification == "多源交叉"
    assert report.events[0].source_quality_label == "多源/权威媒体"
    assert report.events[0].source_quality_score >= 3
    assert report.events[0].confidence >= 0.6
    assert any(event.impact == "negative" for event in report.events)

    markdown = format_catalyst_notification(report)
    assert "来源: 证券报、财联社" in markdown
    assert f"时间: {_RECENT_NEWS_TIME}" in markdown
    assert "原文: https://example.com/a" in markdown
    assert "不要做:" not in markdown
    assert "怎么验证:" not in markdown
    assert "模型复核" not in markdown
    assert "依据:" not in markdown


def test_news_catalyst_notification_only_outputs_results_and_inference() -> None:
    report = build_catalyst_report(
        fetch_symbol_news=lambda _symbol, _limit: pd.DataFrame(),
        fetch_global_news=lambda _limit: pd.DataFrame(
            [{"标题": "政策支持半导体材料国产替代", "来源": "新华社"}]
        ),
        config=NewsCatalystConfig(allow_undated_news=True),
    )

    markdown = format_catalyst_notification(report)

    assert markdown.startswith("# 消息面雷达-")
    assert "## 结论" in markdown
    assert "## 事件" in markdown
    assert "结论: 市场/行业 交易催化明确，短线偏强。" in markdown
    assert "推论:" not in markdown
    assert "影响: 短线偏多" in markdown
    assert "来源: 新华社" in markdown
    forbidden = (
        "不替代",
        "交易指令",
        "怎么验证",
        "不要做",
        "开盘怎么用",
        "靠不靠谱",
        "模型复核",
        "降级判断",
        "助手",
        "依据",
    )
    assert not any(text in markdown for text in forbidden)


def test_news_catalyst_filters_undated_event_by_default() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {"标题": "政策支持半导体材料国产替代", "来源": "新华社", "时间": ""},
                {
                    "标题": "MLCC 行业报价上调，龙头排产紧张",
                    "来源": "证券报",
                    "时间": _RECENT_NEWS_TIME,
                },
            ]
        ),
    )

    titles = tuple(event.title for event in report.events)
    assert "MLCC 行业报价上调，龙头排产紧张" in titles
    assert "政策支持半导体材料国产替代" not in titles
    assert any("已过滤 1 条无日期消息" in warning for warning in report.warnings)


def test_news_catalyst_prefers_dated_recent_event_over_undated_event_when_enabled() -> (
    None
):
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {"标题": "政策支持半导体材料国产替代", "来源": "新华社", "时间": ""},
                {
                    "标题": "MLCC 行业报价上调，龙头排产紧张",
                    "来源": "证券报",
                    "时间": _RECENT_NEWS_TIME,
                },
            ]
        ),
        config=NewsCatalystConfig(allow_undated_news=True),
    )

    markdown = format_catalyst_notification(report)
    assert "MLCC 行业报价上调，龙头排产紧张" in markdown
    assert markdown.index("MLCC 行业报价上调，龙头排产紧张") < markdown.index(
        "政策支持半导体材料国产替代"
    )


def test_news_catalyst_filters_stale_history_news() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "政策支持半导体材料国产替代",
                    "来源": "新华社",
                    "时间": "2016-06-10 08:00",
                },
                {
                    "标题": "MLCC 行业报价上调，龙头排产紧张",
                    "来源": "证券报",
                    "时间": _RECENT_NEWS_TIME,
                },
            ]
        ),
    )

    titles = tuple(event.title for event in report.events)
    assert "MLCC 行业报价上调，龙头排产紧张" in titles
    assert "政策支持半导体材料国产替代" not in titles
    assert any("已过滤 1 条过期消息" in warning for warning in report.warnings)


def test_news_catalyst_max_age_zero_keeps_only_today_news() -> None:
    today = today_shanghai()
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "今日政策支持半导体材料国产替代",
                    "来源": "新华社",
                    "时间": today.isoformat(),
                },
                {
                    "标题": "昨日政策支持半导体材料国产替代",
                    "来源": "新华社",
                    "时间": (today - timedelta(days=1)).isoformat(),
                },
                {
                    "标题": "未来政策支持半导体材料国产替代",
                    "来源": "新华社",
                    "时间": (today + timedelta(days=1)).isoformat(),
                },
            ]
        ),
        config=NewsCatalystConfig(max_news_age_days=0),
    )

    assert tuple(event.title for event in report.events) == (
        "今日政策支持半导体材料国产替代",
    )
    assert report.stale_news_count == 2


def test_news_catalyst_rejects_same_day_future_timestamp_with_auditable_status(
    monkeypatch,
) -> None:
    observed_at = datetime.fromisoformat("2026-07-14T10:00:00+08:00")
    monkeypatch.setattr("aqsp.news.catalysts.today_shanghai", lambda: date(2026, 7, 14))
    monkeypatch.setattr("aqsp.news.catalysts.now_shanghai", lambda: observed_at)

    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "未来政策支持半导体材料国产替代",
                    "来源": "新华社",
                    "时间": "2026-07-14 10:05:00+08:00",
                }
            ]
        )
    )

    assert report.events == ()
    assert report.future_news_count == 1
    assert report.news_status == "stale_only"
    assert any("未来时间戳消息" in warning for warning in report.warnings)


def test_news_catalyst_treats_utc_news_as_shanghai_calendar_day(monkeypatch) -> None:
    fixed_day = date(2026, 7, 13)
    monkeypatch.setattr("aqsp.news.catalysts.today_shanghai", lambda: fixed_day)
    monkeypatch.setattr(
        "aqsp.news.catalysts.now_shanghai",
        lambda: datetime.fromisoformat("2026-07-13T10:00:00+08:00"),
    )

    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "隔夜政策支持半导体材料国产替代",
                    "来源": "新华社",
                    "时间": "2026-07-12T16:30:00Z",
                }
            ]
        ),
        config=NewsCatalystConfig(max_news_age_days=0),
    )

    assert report.events
    assert report.stale_news_count == 0


def test_news_catalyst_does_not_cache_empty_result(
    tmp_path,
) -> None:
    calls = {"count": 0}
    config = NewsCatalystConfig(
        cache_path=str(tmp_path / "catalyst_cache.json"),
        cache_ttl_seconds=120.0,
    )

    def fetch_global_news(_limit: int) -> pd.DataFrame:
        calls["count"] += 1
        if calls["count"] == 1:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "标题": "今日政策支持半导体材料国产替代",
                    "来源": "新华社",
                    "时间": _RECENT_NEWS_TIME,
                }
            ]
        )

    empty = build_catalyst_report(fetch_global_news=fetch_global_news, config=config)
    current = build_catalyst_report(fetch_global_news=fetch_global_news, config=config)

    assert empty.source_status == "empty"
    assert current.events
    assert calls["count"] == 2


def test_news_catalyst_does_not_promote_same_source_duplicates_to_multi_source() -> (
    None
):
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "机器人订单需求放量",
                    "来源": "新华社",
                    "时间": _RECENT_NEWS_TIME,
                },
                {
                    "标题": "机器人订单需求放量",
                    "来源": "新华社",
                    "时间": _RECENT_NEWS_TIME,
                },
            ]
        ),
        config=NewsCatalystConfig(min_confidence=0.3),
    )

    assert report.events
    assert report.events[0].source_count == 1
    assert report.events[0].verification == "媒体来源"


def test_news_catalyst_infers_international_quality_from_known_url() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "美联储政策支持流动性改善",
                    "链接": "https://www.federalreserve.gov/newsevents/pressreleases.htm",
                    "时间": _RECENT_NEWS_TIME,
                }
            ]
        )
    )

    assert report.events
    assert report.events[0].source == "Federal Reserve"
    assert report.events[0].source_region == "international"
    assert report.events[0].source_quality_score == 4


def test_news_catalyst_filters_stale_history_news_when_only_title_contains_date() -> (
    None
):
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "2016-06-10 政策支持半导体材料国产替代",
                    "来源": "新华社",
                    "时间": "",
                },
                {
                    "标题": f"{_RECENT_NEWS_DATE} MLCC 行业报价上调，龙头排产紧张",
                    "来源": "证券报",
                    "时间": "",
                },
            ]
        ),
    )

    titles = tuple(event.title for event in report.events)
    assert f"{_RECENT_NEWS_DATE} MLCC 行业报价上调，龙头排产紧张" in titles
    assert "2016-06-10 政策支持半导体材料国产替代" not in titles
    assert any("已过滤 1 条过期消息" in warning for warning in report.warnings)


def test_news_catalyst_filters_stale_history_news_when_only_url_contains_date() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "政策支持半导体材料国产替代",
                    "来源": "新华社",
                    "时间": "",
                    "链接": "https://example.com/20160610/a.html",
                },
                {
                    "标题": "MLCC 行业报价上调，龙头排产紧张",
                    "来源": "证券报",
                    "时间": "",
                    "链接": f"https://example.com/{_RECENT_NEWS_DATE.replace('-', '')}/b.html",
                },
            ]
        ),
    )

    titles = tuple(event.title for event in report.events)
    assert "MLCC 行业报价上调，龙头排产紧张" in titles
    assert "政策支持半导体材料国产替代" not in titles
    assert any("已过滤 1 条过期消息" in warning for warning in report.warnings)


def test_news_catalyst_uses_title_or_url_date_as_visible_timestamp() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "2026-06-27 政策支持半导体材料国产替代",
                    "来源": "新华社",
                    "时间": "",
                    "链接": "https://example.com/20260627/a.html",
                }
            ]
        ),
        config=NewsCatalystConfig(allow_undated_news=True),
    )

    markdown = format_catalyst_notification(report)
    assert "时间: 2026-06-27" in markdown


def test_news_catalyst_reads_extended_published_at_fields() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "MLCC 行业报价上调，龙头排产紧张",
                    "来源": "证券报",
                    "发布日期": "2026-06-28 09:15",
                }
            ]
        ),
        config=NewsCatalystConfig(allow_undated_news=True),
    )

    markdown = format_catalyst_notification(report)
    assert "时间: 2026-06-28 09:15" in markdown


def test_news_catalyst_filters_pure_market_price_action_noise() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {"标题": "证券ETF盘中涨超3%，成交额明显放量", "来源": "东财"},
                {"标题": "某概念股早盘拉升封板，板块走强", "来源": "东财"},
                {"标题": "半导体板块放量冲击涨停", "来源": "同花顺"},
                {"标题": "MLCC 行业报价上调，龙头排产紧张", "来源": "证券报"},
                {"标题": "新能源汽车订单需求放量", "来源": "证券报"},
            ]
        ),
        config=NewsCatalystConfig(allow_undated_news=True),
    )

    titles = tuple(event.title for event in report.events)
    assert "MLCC 行业报价上调，龙头排产紧张" in titles
    assert "新能源汽车订单需求放量" in titles
    assert "证券ETF盘中涨超3%，成交额明显放量" not in titles
    assert "某概念股早盘拉升封板，板块走强" not in titles
    assert "半导体板块放量冲击涨停" not in titles


def test_news_catalyst_filters_price_hike_headline_with_regulation_risk() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "苹果提价或招来美国监管重拳！众议员提议拆分规模过大的科技公司",
                    "来源": "东财",
                    "时间": "2026-06-29 10:00",
                }
            ]
        ),
    )

    assert report.events == ()


def test_news_catalyst_report_surfaces_source_warnings() -> None:
    df = pd.DataFrame()
    df.attrs["aqsp_warnings"] = ("source timeout", "source timeout")

    report = build_catalyst_report(fetch_global_news=lambda _limit: df)

    assert report.source_status == "timeout"
    assert report.warnings == ("全市场快讯: source timeout",)
    markdown = format_catalyst_notification(report)
    assert "无有效结论：消息源超时" in markdown


def test_news_catalyst_report_fails_when_all_attempted_sources_warn_empty() -> None:
    symbol_df = pd.DataFrame()
    symbol_df.attrs["aqsp_warnings"] = ("symbol timeout",)
    global_df = pd.DataFrame()
    global_df.attrs["aqsp_warnings"] = ("global timeout",)

    report = build_catalyst_report(
        symbols=("300001",),
        fetch_symbol_news=lambda _symbol, _limit: symbol_df,
        fetch_global_news=lambda _limit: global_df,
    )

    assert report.source_status == "timeout"
    assert report.events == ()
    assert report.warnings == (
        "300001 个股新闻: symbol timeout",
        "全市场快讯: global timeout",
    )


def test_news_catalyst_report_fails_when_all_attempted_sources_are_empty() -> None:
    report = build_catalyst_report(
        symbols=("300001",),
        fetch_symbol_news=lambda _symbol, _limit: pd.DataFrame(),
        fetch_global_news=lambda _limit: pd.DataFrame(),
    )

    assert report.source_status == "empty"
    assert report.events == ()


def test_news_catalyst_report_fails_when_fetcher_returns_none() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: None,  # type: ignore[return-value]
    )

    assert report.source_status == "empty"
    assert report.events == ()


def test_news_catalyst_report_fails_when_one_source_errors_and_other_is_empty() -> None:
    def broken_symbol_news(_symbol: str, _limit: int) -> pd.DataFrame:
        raise DataError("symbol source down")

    report = build_catalyst_report(
        symbols=("300001",),
        fetch_symbol_news=broken_symbol_news,
        fetch_global_news=lambda _limit: pd.DataFrame(),
    )

    assert report.source_status == "failed"
    assert report.events == ()


def test_news_catalyst_report_marks_partial_when_raw_news_has_no_strong_event() -> None:
    df = pd.DataFrame([{"标题": "今日市场平稳运行", "来源": "新华社"}])
    df.attrs["aqsp_warnings"] = ("one slow source",)

    report = build_catalyst_report(fetch_global_news=lambda _limit: df)

    assert report.source_status == "partial"
    assert not report.events
    markdown = format_catalyst_notification(report)
    assert "数据状态: 部分可用" in markdown
    assert "抓取失败" not in markdown.splitlines()[0]


def test_news_catalyst_notification_dedupes_timeout_warnings() -> None:
    df = pd.DataFrame([{"标题": "今日市场平稳运行", "来源": "新华社"}])
    df.attrs["aqsp_warnings"] = (
        "消息源超过 6.0s 未返回",
        "HTTPSConnectionPool(host='x'): Read timed out.",
    )

    report = build_catalyst_report(fetch_global_news=lambda _limit: df)
    markdown = format_catalyst_notification(report)

    assert markdown.count("部分消息源超时或连接中断，已降级使用其它来源") == 1
    assert "Read timed out" not in markdown


def test_news_catalyst_notification_hides_low_level_connection_errors() -> None:
    df = pd.DataFrame([{"标题": "今日市场平稳运行", "来源": "新华社"}])
    df.attrs["aqsp_warnings"] = (
        "HTTPSConnectionPool(host='np-anotice-stock.eastmoney.com', port=443): Max retries exceeded",
        "Remote end closed connection without response",
    )

    report = build_catalyst_report(fetch_global_news=lambda _limit: df)
    markdown = format_catalyst_notification(report)

    assert markdown.count("部分消息源超时或连接中断，已降级使用其它来源") == 1
    assert "HTTPSConnectionPool" not in markdown
    assert "Remote end closed" not in markdown


def test_news_catalyst_merges_same_company_event_across_sources() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "华虹宏力：上交所并购重组审核委员会定于2026年6月18日召开2026年第9次并购重组委会议",
                    "来源": "同花顺",
                    "链接": "https://news.10jqka.com.cn/a",
                },
                {
                    "标题": "华虹宏力：上交所并购重组委定于6月18日审核公司发行股份购买资产并募集配套资金事项",
                    "链接": "https://news.futunn.com/flash/20409494",
                },
            ]
        ),
        config=NewsCatalystConfig(allow_undated_news=True),
    )

    assert len(report.events) == 1
    assert report.events[0].source == "同花顺、富途"
    assert report.events[0].source_count == 2
    assert report.events[0].verification == "多源交叉"
    markdown = format_catalyst_notification(report)
    assert markdown.count("- 1. 利好") == 1
    assert "- 2. 利好" not in markdown
    assert "来源: 同花顺、富途" in markdown


def test_news_catalyst_merges_english_cross_source_duplicates() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "NVIDIA announces Physical AI robotics platform",
                    "来源": "NVIDIA",
                    "时间": "2026-07-13 09:30:00+08:00",
                },
                {
                    "标题": "NVIDIA announces Physical AI robotics platform",
                    "来源": "Reuters",
                    "时间": "2026-07-13 09:31:00+08:00",
                },
            ]
        )
    )

    assert len(report.events) == 1
    assert report.events[0].source_count == 2
    assert report.events[0].source == "NVIDIA、Reuters"


def test_news_catalyst_downgrades_unverified_source_tips() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "消息人士：美伊协议涉及减免制裁",
                    "链接": "https://example.com/tip",
                }
            ]
        ),
    )

    assert report.source_status == "ok"
    assert not report.events
    assert any("已过滤 1 条无日期消息" in warning for warning in report.warnings)


def test_news_catalyst_infers_source_from_known_url() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "中国西电：中标国家电网特高压项目，金额18.99亿元",
                    "链接": f"https://news.10jqka.com.cn/{_RECENT_NEWS_DATE.replace('-', '')}/c677419676.shtml",
                    "时间": f"{_RECENT_NEWS_TIME}:16",
                }
            ]
        ),
    )

    assert report.events
    assert report.events[0].source == "同花顺"
    assert report.events[0].name == "中国西电"
    assert report.events[0].verification == "媒体来源"
    assert report.events[0].source_quality_label == "主流媒体"
    assert report.events[0].confidence >= 0.5
    markdown = format_catalyst_notification(report)
    assert "中国西电 交易催化明确，短线偏强。" in markdown
    assert "利好 | 中国西电" in markdown
    assert "来源: 同花顺" in markdown


def test_news_catalyst_does_not_treat_wire_prefix_as_target_name() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "据伊朗迈赫尔通讯社：伊朗与美国谅解备忘录涉及解除制裁",
                    "链接": "https://news.futunn.com/flash/20409368",
                }
            ]
        ),
        config=NewsCatalystConfig(min_confidence=0.3, allow_undated_news=True),
    )

    assert report.events
    assert report.events[0].name == ""
    assert report.events[0].source == "富途"
    markdown = format_catalyst_notification(report)
    assert "据伊朗迈赫尔通讯社**" not in markdown
    assert "来源: 富途" in markdown


def test_news_catalyst_prioritizes_higher_quality_source_when_weight_and_time_match() -> (
    None
):
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "机器人订单需求放量",
                    "来源": "自媒体",
                    "时间": "2026-07-03 09:30",
                },
                {
                    "标题": "机器人订单需求放量",
                    "来源": "新华社",
                    "时间": "2026-07-03 09:30",
                },
            ]
        ),
        config=NewsCatalystConfig(allow_undated_news=True, min_confidence=0.3),
    )

    assert report.events
    assert report.events[0].source == "自媒体、新华社"
    assert report.events[0].verification == "多源交叉"
    assert report.events[0].source_quality_label == "多源/权威媒体"
    assert report.events[0].source_quality_score >= 3


def test_news_catalyst_filters_non_actionable_discipline_news() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "广东通用医药有限公司调研副经理接受纪律审查和监察调查",
                    "来源": "纪委监委",
                },
                {
                    "标题": "上市公司控股股东涉嫌严重违纪违法接受监察调查",
                    "来源": "公司公告",
                },
            ]
        ),
        config=NewsCatalystConfig(allow_undated_news=True),
    )

    titles = tuple(event.title for event in report.events)
    assert "广东通用医药有限公司调研副经理接受纪律审查和监察调查" not in titles
    assert "上市公司控股股东涉嫌严重违纪违法接受监察调查" in titles


def test_news_catalyst_report_degrades_when_source_times_out() -> None:
    def slow_global_news(_limit: int) -> pd.DataFrame:
        time.sleep(0.2)
        return pd.DataFrame([{"标题": "MLCC 行业报价上调", "来源": "慢源"}])

    report = build_catalyst_report(
        fetch_global_news=slow_global_news,
        config=NewsCatalystConfig(source_timeout_seconds=0.01),
    )

    assert report.source_status == "timeout"
    assert not report.events
    assert "超过 0.0s 未返回" in report.warnings[0]


def test_news_catalyst_timeout_does_not_block_process_exit() -> None:
    code = """
import time
from aqsp.news.catalysts import _call_fetcher_with_timeout

try:
    _call_fetcher_with_timeout(lambda: time.sleep(10), timeout_seconds=0.05)
except TimeoutError:
    pass
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=2,
    )

    assert result.returncode == 0, result.stderr


def test_news_catalyst_fetches_symbol_and_global_news_in_parallel() -> None:
    symbol_started = threading.Event()
    global_started = threading.Event()
    released_by_global = {"value": False}

    def symbol_news(_symbol: str, _limit: int) -> pd.DataFrame:
        symbol_started.set()
        released_by_global["value"] = global_started.wait(0.3)
        return pd.DataFrame(
            [{"标题": "个股订单落地", "来源": "公司公告", "时间": _RECENT_NEWS_TIME}]
        )

    def global_news(_limit: int) -> pd.DataFrame:
        global_started.set()
        return pd.DataFrame(
            [
                {
                    "标题": "海外风险偏好回升",
                    "来源": "国际媒体",
                    "时间": _RECENT_NEWS_TIME,
                }
            ]
        )

    report = build_catalyst_report(
        symbols=("600001",),
        fetch_symbol_news=symbol_news,
        fetch_global_news=global_news,
        config=NewsCatalystConfig(
            allow_undated_news=True,
            source_timeout_seconds=0.5,
        ),
    )

    assert symbol_started.is_set()
    assert global_started.is_set()
    assert released_by_global["value"] is True
    assert report.raw_news_count == 2
    assert report.source_status == "ok"


def test_news_catalyst_keeps_global_news_when_symbol_source_times_out() -> None:
    def slow_symbol_news(_symbol: str, _limit: int) -> pd.DataFrame:
        time.sleep(0.3)
        return pd.DataFrame(
            [{"标题": "不应阻塞全市场消息", "来源": "慢源", "时间": _RECENT_NEWS_TIME}]
        )

    report = build_catalyst_report(
        symbols=("600002",),
        fetch_symbol_news=slow_symbol_news,
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "政策支持半导体材料国产替代",
                    "来源": "快源",
                    "时间": _RECENT_NEWS_TIME,
                }
            ]
        ),
        config=NewsCatalystConfig(
            allow_undated_news=True,
            min_confidence=0.3,
            source_timeout_seconds=0.1,
        ),
    )

    assert report.source_status == "partial"
    assert report.raw_news_count == 1
    assert report.events
    assert report.events[0].title == "政策支持半导体材料国产替代"
    health = {(item.name, item.status) for item in report.source_statuses}
    assert ("600002:symbol_news", "timeout") in health
    assert ("global_news", "ok") in health


def test_default_news_adapters_use_configured_source_timeout(monkeypatch) -> None:
    timeouts: list[float] = []
    fake_source = SimpleNamespace(
        fetch_symbol_news=lambda _symbol: [],
        fetch_global_news=lambda: [],
        last_health=(),
    )

    def fetch_optional(_fetch, timeout_seconds: float):
        timeouts.append(timeout_seconds)
        return [], ""

    monkeypatch.setattr(catalysts, "_get_akshare_news_source", lambda: fake_source)
    monkeypatch.setattr(catalysts, "_fetch_optional_frame", fetch_optional)

    build_catalyst_report(
        symbols=("600276",),
        config=NewsCatalystConfig(
            symbols=("600276",),
            source_timeout_seconds=0.25,
        ),
    )

    assert timeouts == [0.25, 0.25]


def test_composite_news_keeps_fast_fallback_when_later_source_times_out() -> None:
    frame = pd.DataFrame([{"标题": "美股风险偏好回升", "来源": "RSS"}])
    fast_source = SimpleNamespace(
        name="rss_news",
        region="international",
        last_health=(),
        fetch_symbol_news=lambda _symbol: [frame],
        fetch_global_news=lambda: [frame],
    )

    def timeout():
        raise TimeoutError("source deadline")

    slow_source = SimpleNamespace(
        name="akshare_news",
        region="domestic",
        last_health=(),
        fetch_symbol_news=lambda _symbol: timeout(),
        fetch_global_news=timeout,
    )
    source = CompositeNewsSource((fast_source, slow_source))

    assert source.fetch_symbol_news("600276")[0].iloc[0]["来源"] == "RSS"
    assert source.last_health[-1].status == "timeout"
    assert source.fetch_global_news()[0].iloc[0]["来源"] == "RSS"
    assert source.last_health[-1].status == "timeout"


def test_composite_news_returns_fast_source_before_slow_source_deadline() -> None:
    frame = pd.DataFrame([{"标题": "RSS 先返回", "来源": "RSS"}])

    def slow_fetch() -> list[pd.DataFrame]:
        time.sleep(1.0)
        return [pd.DataFrame([{"标题": "慢源", "来源": "AkShare"}])]

    fast_source = SimpleNamespace(
        name="rss_news",
        region="international",
        last_health=(),
        fetch_symbol_news=lambda _symbol: [frame],
        fetch_global_news=lambda: [frame],
    )
    slow_source = SimpleNamespace(
        name="akshare_news",
        region="domestic",
        _timeout_seconds=1.0,
        last_health=(),
        fetch_symbol_news=lambda _symbol: slow_fetch(),
        fetch_global_news=slow_fetch,
    )
    source = CompositeNewsSource((fast_source, slow_source), timeout_seconds=0.2)

    started = time.monotonic()
    frames = source.fetch_global_news()
    elapsed = time.monotonic() - started

    assert elapsed < 0.7
    assert frames[0].iloc[0]["标题"] == "RSS 先返回"
    assert any(item.name == "akshare_news" and item.status == "timeout" for item in source.last_health)


def test_akshare_news_source_returns_partial_frames_when_next_endpoint_blocks() -> None:
    def slow() -> pd.DataFrame:
        time.sleep(1.0)
        return pd.DataFrame([{"标题": "不应等待"}])

    source = AkshareNewsSource.__new__(AkshareNewsSource)
    source._timeout_seconds = 0.2
    source._ak = SimpleNamespace(
        stock_info_global_cls=lambda: pd.DataFrame([{"标题": "已拿到"}]),
        stock_info_global_em=slow,
        stock_info_global_ths=lambda: pd.DataFrame(),
        stock_info_global_futu=lambda: pd.DataFrame(),
        stock_info_global_sina=lambda: pd.DataFrame(),
        news_cctv=lambda: pd.DataFrame(),
        news_economic_baidu=lambda: pd.DataFrame(),
        stock_notice_report=lambda: pd.DataFrame(),
    )

    started = time.monotonic()
    frames = source.fetch_global_news()
    elapsed = time.monotonic() - started

    assert elapsed < 0.7
    assert len(frames) == 1
    assert frames[0].iloc[0]["标题"] == "已拿到"
    assert "stock_info_global_em" in frames[0].attrs["aqsp_warnings"][0]
    assert source.last_health[1].status == "timeout"


def test_news_catalyst_report_uses_fresh_cache_before_refetch(
    tmp_path,
) -> None:
    calls = {"count": 0}

    def global_news(_limit: int) -> pd.DataFrame:
        calls["count"] += 1
        return pd.DataFrame(
            [
                {
                    "标题": "MLCC 行业报价上调",
                    "来源": "证券报",
                    "时间": "2026-07-03 09:30",
                }
            ]
        )

    config = NewsCatalystConfig(
        cache_path=str(tmp_path / "catalyst_cache.json"),
        cache_ttl_seconds=120.0,
    )

    first = build_catalyst_report(fetch_global_news=global_news, config=config)
    second = build_catalyst_report(
        fetch_global_news=lambda _limit: (_ for _ in ()).throw(
            AssertionError("fresh cache should skip refetch")
        ),
        config=config,
    )

    assert calls["count"] == 1
    assert first.events == second.events
    assert second.source_status == "ok"


def test_news_catalyst_report_falls_back_to_stale_cache_when_fetch_fails(
    monkeypatch,
    tmp_path,
) -> None:
    cache_path = tmp_path / "catalyst_cache.json"
    config = NewsCatalystConfig(
        cache_path=str(cache_path),
        cache_ttl_seconds=30.0,
        allow_stale_cache_on_failure=True,
    )

    monkeypatch.setattr(
        "aqsp.news.catalysts.now_shanghai",
        lambda: datetime.fromisoformat("2026-07-03T10:00:00+08:00"),
    )
    cached = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "MLCC 行业报价上调",
                    "来源": "证券报",
                    "时间": "2026-07-03 09:30",
                }
            ]
        ),
        config=config,
    )

    monkeypatch.setattr(
        "aqsp.news.catalysts.now_shanghai",
        lambda: datetime.fromisoformat("2026-07-03T10:05:00+08:00"),
    )
    fallback = build_catalyst_report(
        fetch_global_news=lambda _limit: (_ for _ in ()).throw(
            DataError("source down")
        ),
        config=config,
    )

    assert cached.events == fallback.events
    assert fallback.source_status == "partial"
    assert fallback.generated_at == "2026-07-03T10:05:00+08:00"
    assert any("消息缓存回退" in warning for warning in fallback.warnings)
    assert any("source down" in warning for warning in fallback.warnings)


def test_news_catalyst_report_rejects_too_old_stale_cache_when_fetch_fails(
    monkeypatch,
    tmp_path,
) -> None:
    cache_path = tmp_path / "catalyst_cache.json"
    config = NewsCatalystConfig(
        cache_path=str(cache_path),
        cache_ttl_seconds=30.0,
        allow_stale_cache_on_failure=True,
        max_stale_cache_age_seconds=30 * 60,
    )

    monkeypatch.setattr(
        "aqsp.news.catalysts.now_shanghai",
        lambda: datetime.fromisoformat("2026-07-03T10:00:00+08:00"),
    )
    cached = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "MLCC 行业报价上调",
                    "来源": "证券报",
                    "时间": "2026-07-03 09:30",
                }
            ]
        ),
        config=config,
    )

    monkeypatch.setattr(
        "aqsp.news.catalysts.now_shanghai",
        lambda: datetime.fromisoformat("2026-07-03T12:00:00+08:00"),
    )
    rejected = build_catalyst_report(
        fetch_global_news=lambda _limit: (_ for _ in ()).throw(
            DataError("source down")
        ),
        config=config,
    )

    assert cached.events
    assert rejected.events == ()
    assert rejected.source_status == "failed"
    assert any("消息缓存过期" in warning for warning in rejected.warnings)


def test_news_catalyst_report_rejects_cross_day_stale_cache_when_fetch_fails(
    monkeypatch,
    tmp_path,
) -> None:
    cache_path = tmp_path / "catalyst_cache.json"
    config = NewsCatalystConfig(
        cache_path=str(cache_path),
        cache_ttl_seconds=30.0,
        allow_stale_cache_on_failure=True,
        max_stale_cache_age_seconds=30 * 60,
    )

    monkeypatch.setattr("aqsp.news.catalysts.today_shanghai", lambda: date(2026, 7, 13))
    monkeypatch.setattr(
        "aqsp.news.catalysts.now_shanghai",
        lambda: datetime.fromisoformat("2026-07-13T23:55:00+08:00"),
    )
    cached = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "MLCC 行业报价上调",
                    "来源": "证券报",
                    "时间": "2026-07-13 23:40",
                }
            ]
        ),
        config=config,
    )

    monkeypatch.setattr("aqsp.news.catalysts.today_shanghai", lambda: date(2026, 7, 14))
    monkeypatch.setattr(
        "aqsp.news.catalysts.now_shanghai",
        lambda: datetime.fromisoformat("2026-07-14T00:05:00+08:00"),
    )
    rejected = build_catalyst_report(
        fetch_global_news=lambda _limit: (_ for _ in ()).throw(
            DataError("source down")
        ),
        config=config,
    )

    assert cached.events
    assert rejected.events == ()
    assert rejected.source_status == "failed"
    assert any("跨自然日缓存" in warning for warning in rejected.warnings)


def test_news_catalyst_llm_review_is_bounded(monkeypatch) -> None:
    calls: list[str] = []

    def fake_llm_call_or_fallback(**kwargs):
        calls.append(kwargs["prompt"])
        return type(
            "Result",
            (),
            {
                "text": "可信度=80; 影响=利好; 理由=来源可信",
                "degraded": False,
            },
        )()

    monkeypatch.setattr(
        "aqsp.utils.llm_safe.llm_call_or_fallback", fake_llm_call_or_fallback
    )

    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {"标题": "政策支持半导体材料国产替代", "来源": "新华社"},
                {"标题": "MLCC 行业报价上调", "来源": "证券报"},
                {"标题": "某公司被立案调查", "来源": "公告"},
            ]
        ),
        config=NewsCatalystConfig(
            enable_llm_review=True,
            max_llm_review_events=1,
            llm_timeout_seconds=2,
            allow_undated_news=True,
        ),
    )

    assert len(calls) == 1
    assert all("模型复核" not in event.verification for event in report.events)
    reviewed = next(event for event in report.events if event.llm_review)
    assert reviewed.title == "某公司被立案调查"
    assert reviewed.confidence == pytest.approx(0.66)
    assert reviewed.category == "监管/合规风险"
    assert reviewed.weight == 5
    markdown = format_catalyst_notification(report)
    assert "模型复核" not in markdown
    assert "降级判断" not in markdown


def test_news_catalyst_notification_truncates_long_event_titles() -> None:
    long_title = "美国财政部宣布制裁9家中国和中国香港的个人及实体，外交部回应将采取一切必要措施坚定维护本国企业和公民权益"
    long_title += "，相关事项仍需继续观察后续政策原文、企业公告、产业链反馈以及市场承接"
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [{"标题": long_title, "来源": "新华社", "链接": "https://example.com/b"}]
        ),
        config=NewsCatalystConfig(allow_undated_news=True),
    )

    markdown = format_catalyst_notification(report)

    assert (
        "美国财政部宣布制裁9家中国和中国香港的个人及实体，外交部回应将采取一切必要措施坚定维护本国企业和公民权益"
        not in markdown
    )
    assert "美国财政部宣布制裁9家中国和中国香港的个人及实体" in markdown
    assert "怎么验证" not in markdown


def test_global_news_prioritizes_notice_sources(monkeypatch) -> None:
    fake_ak = SimpleNamespace(
        fetch_global_news=lambda: [
            pd.DataFrame(
                [{"标题": f"普通快讯{i}", "来源": "财联社"} for i in range(5)]
            ),
            pd.DataFrame([{"标题": "国常会部署设备更新", "来源": "央视"}]),
            pd.DataFrame([{"公告标题": "公司签订重大订单", "公告类型": "公司公告"}]),
        ],
        fetch_symbol_news=lambda _symbol: (pd.DataFrame(),),
    )

    monkeypatch.setattr("aqsp.news.catalysts._AKSHARE_NEWS", fake_ak)

    df = _akshare_global_news(limit=3)

    rows = df.to_dict(orient="records")
    titles = []
    for row in rows:
        title = str(row.get("标题") or "")
        if not title or title.lower() == "nan":
            title = str(row.get("公告标题") or "")
        titles.append(title)
    assert titles[:2] == ["公司签订重大订单", "国常会部署设备更新"]


def test_akshare_news_source_raises_data_error_when_all_global_sources_fail() -> None:
    def boom() -> pd.DataFrame:
        raise RuntimeError("remote closed")

    source = AkshareNewsSource.__new__(AkshareNewsSource)
    source._ak = SimpleNamespace(
        stock_info_global_cls=boom,
        stock_info_global_em=boom,
        stock_info_global_ths=boom,
        stock_info_global_futu=boom,
        stock_info_global_sina=boom,
        news_cctv=boom,
        news_economic_baidu=boom,
        stock_notice_report=boom,
    )

    with pytest.raises(DataError, match="akshare 全市场新闻获取失败"):
        source.fetch_global_news()


def test_akshare_news_source_stops_global_batch_after_timeout() -> None:
    called: list[str] = []

    def timeout() -> pd.DataFrame:
        called.append("timeout")
        raise TimeoutError("source deadline")

    def should_not_run() -> pd.DataFrame:
        called.append("after-timeout")
        return pd.DataFrame([{"标题": "不应请求"}])

    source = AkshareNewsSource.__new__(AkshareNewsSource)
    source._ak = SimpleNamespace(
        stock_info_global_cls=timeout,
        stock_info_global_em=should_not_run,
        stock_info_global_ths=should_not_run,
        stock_info_global_futu=should_not_run,
        stock_info_global_sina=should_not_run,
        news_cctv=should_not_run,
        news_economic_baidu=should_not_run,
        stock_notice_report=should_not_run,
    )

    with pytest.raises(DataError, match="akshare 全市场新闻获取失败"):
        source.fetch_global_news()

    assert called == ["timeout"]
    assert source.last_health[0].status == "timeout"


def test_akshare_news_source_surfaces_partial_fetch_warnings() -> None:
    def empty() -> pd.DataFrame:
        return pd.DataFrame()

    source = AkshareNewsSource.__new__(AkshareNewsSource)
    source._ak = SimpleNamespace(
        stock_info_global_cls=lambda: pd.DataFrame(
            [{"标题": "政策支持半导体材料国产替代", "来源": "新华社"}]
        ),
        stock_info_global_em=empty,
        stock_info_global_ths=empty,
        stock_info_global_futu=empty,
        stock_info_global_sina=empty,
        news_cctv=empty,
        news_economic_baidu=empty,
        stock_notice_report=empty,
    )

    frames = source.fetch_global_news()

    assert len(frames) == 1
    assert "stock_info_global_em: empty" in frames[0].attrs["aqsp_warnings"]


def test_akshare_symbol_news_bypasses_broken_jsonp_and_passes_security(
    monkeypatch,
) -> None:
    calls: list[object] = []

    class Response:
        text = (
            'aqsp_callback({"result":{"cmsArticleWebOld":[{"title":"'
            '商业航天订单落地","content":"","date":"2026-07-14 09:10:00",'
            '"mediaName":"东财","code":"A202607140001"}]}});'
        )

        def raise_for_status(self) -> None:
            return None

    def get(*_args: object, **_kwargs: object) -> Response:
        return Response()

    monkeypatch.setattr(news_source.requests, "get", get)

    def broken_stock_news_em(**_kwargs: object) -> pd.DataFrame:
        raise RuntimeError("Invalid regular expression: invalid escape sequence: \\u")

    def notice(*, security: str) -> pd.DataFrame:
        calls.append(security)
        return pd.DataFrame()

    source = AkshareNewsSource.__new__(AkshareNewsSource)
    source._ak = SimpleNamespace(
        stock_news_em=broken_stock_news_em,
        stock_individual_notice_report=notice,
        stock_research_report_em=lambda **_kwargs: pd.DataFrame(),
    )

    frames = source.fetch_symbol_news("002084")

    assert calls == ["002084"]
    assert len(frames) == 1
    assert frames[0].iloc[0]["新闻标题"] == "商业航天订单落地"
    assert frames[0].attrs["aqsp_fetched_at"]
    health = {item.name: item for item in source.last_health}
    assert health["stock_news_em"].status == "ok"
    assert health["stock_news_em"].fetched_at
    assert health["stock_individual_notice_report"].status == "empty"


def test_akshare_global_news_preserves_adapter_warnings(monkeypatch) -> None:
    frame = pd.DataFrame([{"标题": "政策支持半导体材料国产替代", "来源": "新华社"}])
    frame.attrs["aqsp_warnings"] = ("stock_info_global_em: empty",)
    fake_ak = SimpleNamespace(
        fetch_global_news=lambda: [frame],
        fetch_symbol_news=lambda _symbol: (),
    )
    monkeypatch.setattr("aqsp.news.catalysts._AKSHARE_NEWS", fake_ak)

    df = _akshare_global_news(limit=3)

    assert df.attrs["aqsp_warnings"] == ("stock_info_global_em: empty",)


def test_rss_news_source_parses_feed_items(monkeypatch) -> None:
    class Response:
        content = """<?xml version="1.0" encoding="utf-8"?>
        <rss><channel>
          <item>
            <title>300750 宁德时代中标储能大单</title>
            <link>https://example.com/catl</link>
            <pubDate>Tue, 07 Jul 2026 01:30:00 GMT</pubDate>
          </item>
        </channel></rss>""".encode()

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "aqsp.data.news_source.requests.get",
        lambda *_args, **_kwargs: Response(),
    )

    source = RssNewsSource(
        (
            RssFeedConfig(
                name="RSSHub-财经",
                url="https://rsshub.example.com/finance",
                category="stock",
                symbols=("300750",),
            ),
        )
    )

    frames = source.fetch_global_news()
    assert len(frames) == 1
    assert frames[0].iloc[0]["标题"] == "300750 宁德时代中标储能大单"
    assert frames[0].iloc[0]["来源"] == "RSSHub-财经"
    assert frames[0].iloc[0]["source_group"] == "rss"
    assert frames[0].iloc[0]["title"] == frames[0].iloc[0]["标题"]
    assert frames[0].iloc[0]["source"] == frames[0].iloc[0]["来源"]
    assert frames[0].iloc[0]["published_at"] == frames[0].iloc[0]["时间"]
    assert frames[0].iloc[0]["url"] == "https://example.com/catl"

    symbol_frames = source.fetch_symbol_news("300750")
    assert len(symbol_frames) == 1
    assert "宁德时代中标储能大单" in symbol_frames[0].iloc[0]["标题"]


def test_rss_symbol_news_ignores_feed_level_symbol_metadata(monkeypatch) -> None:
    class Response:
        content = """<?xml version="1.0" encoding="utf-8"?>
        <rss><channel>
          <item>
            <title>宏观流动性观察</title>
            <link>https://example.com/macro</link>
            <description>没有个股代码</description>
            <pubDate>Tue, 07 Jul 2026 01:30:00 GMT</pubDate>
          </item>
        </channel></rss>""".encode()

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "aqsp.data.news_source.requests.get",
        lambda *_args, **_kwargs: Response(),
    )
    source = RssNewsSource(
        (
            RssFeedConfig(
                name="RSS-宏观",
                url="https://rsshub.example.com/macro",
                symbols=("300750",),
            ),
        )
    )

    assert source.fetch_symbol_news("300750") == []


def test_rss_news_source_filters_items_by_keywords(monkeypatch) -> None:
    class Response:
        content = """<?xml version="1.0" encoding="utf-8"?>
        <rss><channel>
          <item>
            <title>Celebrity lifestyle newsletter</title>
            <link>https://example.com/lifestyle</link>
            <description>no market signal</description>
            <pubDate>Tue, 07 Jul 2026 01:20:00 GMT</pubDate>
          </item>
          <item>
            <title>Early Career Faculty (ECF) 2025 Awards</title>
            <link>https://www.nasa.gov/news-release/early-career-faculty-awards/</link>
            <description>Faculty recognition and education awards</description>
            <pubDate>Tue, 07 Jul 2026 01:25:00 GMT</pubDate>
          </item>
          <item>
            <title>Nasdaq futures rally as AI stocks rebound</title>
            <link>https://example.com/markets</link>
            <description>risk-on tone returns before the open</description>
            <pubDate>Tue, 07 Jul 2026 01:30:00 GMT</pubDate>
          </item>
        </channel></rss>""".encode()

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "aqsp.data.news_source.requests.get",
        lambda *_args, **_kwargs: Response(),
    )

    source = RssNewsSource(
        (
            RssFeedConfig(
                name="MarketWatch-RealTimeHeadlines",
                url="https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
                category="global_risk_appetite",
                keywords=("Nasdaq", "risk-on", "AI stocks"),
            ),
        )
    )

    frames = source.fetch_global_news()

    assert len(frames) == 1
    assert list(frames[0]["标题"]) == ["Nasdaq futures rally as AI stocks rebound"]
    assert frames[0].iloc[0]["keyword_matched"] == "Nasdaq,risk-on,AI stocks"


def test_rss_news_source_loads_from_yaml_config(tmp_path) -> None:
    config_path = tmp_path / "news_sources.yaml"
    config_path.write_text(
        """
rss:
  enabled: true
  timeout_seconds: 2
  feeds:
    - name: 财经雷达
      url: https://rsshub.example.com/cls
      category: macro
      enabled: true
      keywords: [政策, 订单]
""",
        encoding="utf-8",
    )

    source = build_rss_news_source_from_config(str(config_path))

    assert source is not None
    assert source.name == "rss_news"


def test_build_default_news_source_returns_rss_when_akshare_is_not_installed(
    monkeypatch,
) -> None:
    rss_source = RssNewsSource(
        (RssFeedConfig(name="财经雷达", url="https://example.com/rss"),)
    )

    def missing_akshare() -> NewsSource:
        raise news_source._AkshareOptionalDependencyError("akshare not installed")

    monkeypatch.setattr(news_source, "AkshareNewsSource", missing_akshare)
    monkeypatch.setattr(
        news_source,
        "build_rss_news_source_from_config",
        lambda: rss_source,
    )

    source = build_default_news_source()

    assert source is rss_source


def test_build_default_news_source_propagates_unexpected_akshare_error(
    monkeypatch,
) -> None:
    def broken_akshare() -> NewsSource:
        raise RuntimeError("akshare initialization failed")

    monkeypatch.setattr(news_source, "AkshareNewsSource", broken_akshare)
    monkeypatch.setattr(
        news_source,
        "build_rss_news_source_from_config",
        lambda: RssNewsSource(
            (RssFeedConfig(name="财经雷达", url="https://example.com/rss"),)
        ),
    )

    with pytest.raises(RuntimeError, match="akshare initialization failed"):
        build_default_news_source()


def test_build_catalyst_report_uses_rss_when_akshare_is_not_installed(
    monkeypatch,
) -> None:
    class Response:
        content = """<?xml version="1.0" encoding="utf-8"?>
        <rss><channel><item>
          <title>NVIDIA announces Physical AI robotics platform</title>
          <link>https://example.com/physical-ai</link>
          <pubDate>Sat, 11 Jul 2026 01:30:00 GMT</pubDate>
        </item></channel></rss>""".encode()

        def raise_for_status(self) -> None:
            return None

    def missing_akshare() -> NewsSource:
        raise news_source._AkshareOptionalDependencyError("akshare not installed")

    rss_source = RssNewsSource(
        (
            RssFeedConfig(
                name="英伟达-NVIDIADeveloper",
                url="https://example.com/rss",
                keywords=("NVIDIA", "Physical AI", "robotics"),
            ),
        )
    )
    monkeypatch.setattr(news_source, "AkshareNewsSource", missing_akshare)
    monkeypatch.setattr(
        news_source,
        "build_rss_news_source_from_config",
        lambda: rss_source,
    )
    monkeypatch.setattr(
        "aqsp.data.news_source.requests.get",
        lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setattr("aqsp.news.catalysts._AKSHARE_NEWS", None)
    monkeypatch.setattr(
        "aqsp.news.catalysts.now_shanghai",
        lambda: datetime.fromisoformat("2026-07-11T10:00:00+08:00"),
    )

    report = build_catalyst_report(
        config=NewsCatalystConfig(allow_undated_news=False),
    )

    assert report.source_status != "failed"
    assert any("Physical AI" in event.title for event in report.events)


def test_build_default_news_source_raises_data_error_when_no_source_is_available(
    monkeypatch,
) -> None:
    def missing_akshare() -> NewsSource:
        raise news_source._AkshareOptionalDependencyError("akshare not installed")

    monkeypatch.setattr(news_source, "AkshareNewsSource", missing_akshare)
    monkeypatch.setattr(
        news_source,
        "build_rss_news_source_from_config",
        lambda: None,
    )

    with pytest.raises(DataError, match="未配置可用新闻源"):
        build_default_news_source()


def test_default_news_source_config_enables_official_rss_feeds() -> None:
    source = build_rss_news_source_from_config("config/news_sources.yaml")

    assert source is not None
    feeds = tuple(source._feeds)
    feed_names = {feed.name for feed in feeds}
    assert {
        "美联储-FederalReserve",
        "美国SEC-PressReleases",
        "欧洲央行-ECB",
        "英伟达-NVIDIADeveloper",
        "NASA-NewsReleases",
        "MarketWatch-RealTimeHeadlines",
        "MarketWatch-MarketPulse",
    }.issubset(feed_names)
    assert all(feed.url.startswith("https://") for feed in feeds)
    assert all("rsshub.example.com" not in feed.url for feed in feeds)
    assert source._max_concurrency == 11
    assert Path("config/news_sources.yaml").exists()


def test_default_news_source_config_covers_core_cross_market_triggers() -> None:
    source = build_rss_news_source_from_config("config/news_sources.yaml")

    assert source is not None
    keyword_blob_by_feed = {
        feed.name: " ".join(feed.keywords).casefold() for feed in source._feeds
    }
    all_keywords = " ".join(keyword_blob_by_feed.values())
    assert "physical ai" in all_keywords
    assert "spacex" in all_keywords
    assert "nasdaq" in all_keywords
    assert "risk-on" in all_keywords
    assert "gold" in all_keywords
    assert "oil" in all_keywords
    assert "crude" in all_keywords
    assert "war" in all_keywords
    assert "军工" in all_keywords


def test_rss_news_runtime_summary_reports_core_trigger_coverage() -> None:
    summary = rss_news_runtime_summary("config/news_sources.yaml")

    assert summary.enabled is True
    assert summary.feed_count >= 7
    assert summary.keyword_gated_feeds == summary.feed_count
    assert summary.all_core_triggers_covered is True
    assert summary.covered_triggers == (
        "commercial_space",
        "physical_ai",
        "us_risk_on",
        "geopolitics",
        "oil_price_shock",
    )
    assert summary.missing_triggers == ()


def test_rss_cross_market_events_reach_catalyst_and_market_context(
    monkeypatch,
) -> None:
    class Response:
        content = """<?xml version="1.0" encoding="utf-8"?>
        <rss><channel>
          <item>
            <title>NVIDIA announces Physical AI robotics platform</title>
            <link>https://developer.nvidia.com/blog/physical-ai</link>
            <pubDate>Wed, 08 Jul 2026 10:30:00 GMT</pubDate>
          </item>
          <item>
            <title>SpaceX evaluates IPO for satellite launch network</title>
            <link>https://www.nasa.gov/news-release/spacex</link>
            <pubDate>Wed, 08 Jul 2026 10:35:00 GMT</pubDate>
          </item>
          <item>
            <title>Nasdaq rallies as tech stocks lead a risk-on session</title>
            <link>https://www.marketwatch.com/story/nasdaq-risk-on</link>
            <pubDate>Wed, 08 Jul 2026 10:40:00 GMT</pubDate>
          </item>
          <item>
            <title>Middle East attack lifts gold and defense stocks</title>
            <link>https://www.marketwatch.com/story/geopolitical-attack</link>
            <pubDate>Wed, 08 Jul 2026 10:45:00 GMT</pubDate>
          </item>
          <item>
            <title>Crude oil jumps after OPEC signals deeper supply cuts</title>
            <link>https://www.marketwatch.com/story/crude-oil-opec-cuts</link>
            <pubDate>Wed, 08 Jul 2026 10:50:00 GMT</pubDate>
          </item>
        </channel></rss>""".encode()

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "aqsp.data.news_source.requests.get",
        lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setattr(
        "aqsp.news.catalysts.now_shanghai",
        lambda: datetime.fromisoformat("2026-07-08T19:00:00+08:00"),
    )
    source = RssNewsSource(
        (
            RssFeedConfig(
                name="MarketWatch-MarketPulse",
                url="https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
                category="global_tech",
                keywords=(
                    "NVIDIA",
                    "Physical AI",
                    "robotics",
                    "SpaceX",
                    "IPO",
                    "Nasdaq",
                    "risk-on",
                    "attack",
                    "gold",
                    "defense",
                    "crude oil",
                    "OPEC",
                ),
            ),
        )
    )

    def rss_global_news(_limit: int) -> pd.DataFrame:
        return pd.concat(source.fetch_global_news(), ignore_index=True)

    report = build_catalyst_report(
        fetch_global_news=rss_global_news,
        config=NewsCatalystConfig(allow_undated_news=False, max_events=8),
    )
    artifact = build_market_context_artifact(catalyst_report=report)

    titles = tuple(event.title for event in report.events)
    assert "NVIDIA announces Physical AI robotics platform" in titles
    assert "SpaceX evaluates IPO for satellite launch network" in titles
    assert "Nasdaq rallies as tech stocks lead a risk-on session" in titles
    assert "Middle East attack lifts gold and defense stocks" in titles
    assert "Crude oil jumps after OPEC signals deeper supply cuts" in titles
    assert {event.source for event in report.events} == {"MarketWatch-MarketPulse"}
    rule_ids = tuple(item.rule_id for item in artifact.cross_market_implications)
    assert "physical_ai" in rule_ids
    assert "commercial_space" in rule_ids
    assert "us_risk_on" in rule_ids
    assert "geopolitics" in rule_ids
    assert "oil_price_shock" in rule_ids


def test_news_catalyst_does_not_classify_awards_as_war() -> None:
    assert _classify_title("Early Career Faculty (ECF) 2025 Awards") is None
    assert _classify_title("War escalates in the Middle East") is not None


def test_rss_news_source_distinguishes_domestic_international_empty_and_timeout(
    monkeypatch,
) -> None:
    class Response:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def raise_for_status(self) -> None:
            return None

    good = """<?xml version="1.0"?><rss><channel><item>
      <title>国内政策支持设备更新</title><pubDate>Tue, 07 Jul 2026 01:30:00 GMT</pubDate>
    </item></channel></rss>""".encode()
    empty = b"""<?xml version="1.0"?><rss><channel></channel></rss>"""

    def get(url: str, **_kwargs: object) -> Response:
        if url.endswith("/timeout"):
            raise requests.exceptions.Timeout("read timed out")
        if url.endswith("/empty"):
            return Response(empty)
        return Response(good)

    monkeypatch.setattr("aqsp.data.news_source.requests.get", get)
    source = RssNewsSource(
        (
            RssFeedConfig(name="国内政策", url="https://example/ok", region="domestic"),
            RssFeedConfig(
                name="海外市场", url="https://example/empty", region="international"
            ),
            RssFeedConfig(
                name="海外快讯", url="https://example/timeout", region="international"
            ),
        )
    )

    frames = source.fetch_global_news()

    assert len(frames) == 1
    health = {(item.name, item.region): item.status for item in source.last_health}
    assert health == {
        ("国内政策", "domestic"): "ok",
        ("海外市场", "international"): "empty",
        ("海外快讯", "international"): "timeout",
    }
    assert frames[0].attrs["aqsp_source_status"] == "partial"
    assert frames[0].iloc[0]["source_region"] == "domestic"


def test_news_catalyst_marks_successful_no_event_run_as_ok_not_waiting() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [{"标题": "今日市场平稳运行", "来源": "新华社", "时间": _RECENT_NEWS_TIME}]
        )
    )

    markdown = format_catalyst_notification(report)

    assert report.source_status == "ok"
    assert report.events == ()
    assert "未筛出高影响消息" in markdown
    assert "等待" not in markdown


def test_news_catalyst_keeps_source_health_quality_and_publish_time_traceable() -> None:
    frame = pd.DataFrame(
        [
            {
                "标题": "政策支持半导体材料国产替代",
                "来源": "欧洲央行-ECB",
                "时间": "2026-07-13 09:15:00+08:00",
                "source_region": "international",
                "链接": "https://example.com/news",
            }
        ]
    )
    frame.attrs["aqsp_source_health"] = (
        NewsSourceHealth(
            name="欧洲央行-ECB",
            region="international",
            status="ok",
            successful=1,
            row_count=1,
        ),
    )

    report = build_catalyst_report(fetch_global_news=lambda _limit: frame)
    markdown = format_catalyst_notification(report)

    assert report.source_statuses[0].region == "international"
    assert report.events[0].source_quality_score == 4
    assert report.events[0].published_at == "2026-07-13 09:15:00+08:00"
    assert "质量 高价值来源（4/4）" in markdown
    assert "区域 international" in markdown
    assert "时间: 2026-07-13 09:15:00+08:00" in markdown


def test_news_catalyst_cross_source_merge_keeps_newest_publication_and_fetch_time() -> (
    None
):
    old_frame = pd.DataFrame(
        [
            {
                "标题": "样本电子签订机器人订单合同",
                "来源": "财联社",
                "时间": "2026-07-13 09:00:00+08:00",
            }
        ]
    )
    old_frame.attrs["aqsp_fetched_at"] = "2026-07-13T09:05:00+08:00"
    new_frame = pd.DataFrame(
        [
            {
                "标题": "样本电子签订机器人订单合同",
                "来源": "公司公告",
                "时间": "2026-07-13 10:00:00+08:00",
            }
        ]
    )
    new_frame.attrs["aqsp_fetched_at"] = "2026-07-13T10:05:00+08:00"

    report = build_catalyst_report(
        symbols=("300001",),
        fetch_symbol_news=lambda _symbol, _limit: old_frame,
        fetch_global_news=lambda _limit: new_frame,
    )

    assert len(report.events) == 1
    assert report.events[0].published_at == "2026-07-13 10:00:00+08:00"
    assert report.events[0].source_fetched_at == "2026-07-13T10:05:00+08:00"


def test_catalyst_report_artifact_round_trips_with_date_and_freshness_gate(
    tmp_path: Path,
) -> None:
    report = CatalystReport(
        date=today_shanghai().isoformat(),
        generated_at=now_shanghai().isoformat(timespec="seconds"),
        events=(),
        source_status="partial",
        warnings=("国际源超时，已降级",),
        event_status="no_valid_news",
    )
    path = tmp_path / "news.json"
    path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_catalyst_report_artifact(
        path,
        expected_date=report.date,
        max_age_seconds=60,
    )

    assert loaded == report
    assert (
        load_catalyst_report_artifact(
            path,
            expected_date="2026-01-01",
            max_age_seconds=60,
        )
        is None
    )
