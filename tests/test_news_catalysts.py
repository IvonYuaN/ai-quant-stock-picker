from __future__ import annotations

import time

import pandas as pd

from aqsp.news.catalysts import (
    NewsCatalystConfig,
    build_catalyst_report,
    format_catalyst_notification,
)


def test_news_catalyst_report_prioritizes_verified_price_hike_events() -> None:
    def symbol_news(symbol: str, limit: int) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "新闻标题": "MLCC 行业报价上调，龙头排产紧张",
                    "文章来源": "证券报",
                    "发布时间": "2026-06-11 08:30",
                    "新闻链接": "https://example.com/a",
                },
                {
                    "新闻标题": "网传 MLCC 涨价，尚未获得公司确认",
                    "文章来源": "自媒体",
                    "发布时间": "2026-06-11 08:40",
                },
            ]
        )

    def global_news(limit: int) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "标题": "MLCC 行业报价上调，龙头排产紧张",
                    "来源": "财联社",
                    "时间": "2026-06-11 08:32",
                },
                {
                    "标题": "某公司被立案调查",
                    "来源": "公告",
                    "时间": "2026-06-11 09:00",
                },
            ]
        )

    report = build_catalyst_report(
        symbols=("300001",),
        symbol_names={"300001": "测试电子"},
        fetch_symbol_news=symbol_news,
        fetch_global_news=global_news,
        config=NewsCatalystConfig(symbols=("300001",), max_events=5),
    )

    assert report.source_status == "ok"
    assert report.events[0].category == "涨价/供需催化"
    assert report.events[0].source_count == 2
    assert report.events[0].verification == "多源交叉"
    assert report.events[0].confidence >= 0.6
    assert any(event.impact == "negative" for event in report.events)


def test_news_catalyst_notification_keeps_research_boundary() -> None:
    report = build_catalyst_report(
        fetch_symbol_news=lambda _symbol, _limit: pd.DataFrame(),
        fetch_global_news=lambda _limit: pd.DataFrame(
            [{"标题": "政策支持半导体材料国产替代", "来源": "新华社"}]
        ),
    )

    markdown = format_catalyst_notification(report)

    assert markdown.startswith("# 消息面雷达-")
    assert "不直接改写系统评分" in markdown
    assert "多源交叉或公告来源优先" in markdown
    assert "交易指令" in markdown


def test_news_catalyst_report_degrades_when_source_times_out() -> None:
    def slow_global_news(_limit: int) -> pd.DataFrame:
        time.sleep(0.2)
        return pd.DataFrame([{"标题": "MLCC 行业报价上调", "来源": "慢源"}])

    report = build_catalyst_report(
        fetch_global_news=slow_global_news,
        config=NewsCatalystConfig(source_timeout_seconds=0.01),
    )

    assert report.source_status == "failed"
    assert not report.events
    assert "超过 0.0s 未返回" in report.warnings[0]
