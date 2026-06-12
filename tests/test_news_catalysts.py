from __future__ import annotations

import time

import pandas as pd

from aqsp.news.catalysts import (
    NewsCatalystConfig,
    build_catalyst_report,
    format_catalyst_notification,
    _akshare_global_news,
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
        symbol_names={"300001": "样本电子"},
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

    markdown = format_catalyst_notification(report)
    assert "来源: 证券报、财联社" in markdown
    assert "时间: 2026-06-11 08:30" in markdown
    assert "原文: https://example.com/a" in markdown


def test_news_catalyst_notification_keeps_research_boundary() -> None:
    report = build_catalyst_report(
        fetch_symbol_news=lambda _symbol, _limit: pd.DataFrame(),
        fetch_global_news=lambda _limit: pd.DataFrame(
            [{"标题": "政策支持半导体材料国产替代", "来源": "新华社"}]
        ),
    )

    markdown = format_catalyst_notification(report)

    assert markdown.startswith("# 消息面雷达-")
    assert "不替代主报告结论" in markdown
    assert "多源交叉或公告来源优先" in markdown
    assert "交易指令" in markdown
    assert "来源: 新华社" in markdown
    assert "怎么验证:" in markdown
    assert "不要做:" in markdown


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
    )

    titles = tuple(event.title for event in report.events)
    assert "MLCC 行业报价上调，龙头排产紧张" in titles
    assert "新能源汽车订单需求放量" in titles
    assert "证券ETF盘中涨超3%，成交额明显放量" not in titles
    assert "某概念股早盘拉升封板，板块走强" not in titles
    assert "半导体板块放量冲击涨停" not in titles


def test_news_catalyst_report_surfaces_source_warnings() -> None:
    df = pd.DataFrame()
    df.attrs["aqsp_warnings"] = ("source timeout",)

    report = build_catalyst_report(fetch_global_news=lambda _limit: df)

    assert report.source_status == "failed"
    assert report.warnings == ("全市场快讯: source timeout",)
    markdown = format_catalyst_notification(report)
    assert "今天不要用这条通知下结论" in markdown


def test_news_catalyst_report_marks_partial_when_raw_news_has_no_strong_event() -> None:
    df = pd.DataFrame([{"标题": "今日市场平稳运行", "来源": "新华社"}])
    df.attrs["aqsp_warnings"] = ("one slow source",)

    report = build_catalyst_report(fetch_global_news=lambda _limit: df)

    assert report.source_status == "partial"
    assert not report.events
    markdown = format_catalyst_notification(report)
    assert "只拿到部分消息" in markdown
    assert "抓取失败" not in markdown.splitlines()[0]


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

    assert report.source_status == "empty"
    assert not report.events


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

    assert report.source_status == "failed"
    assert not report.events
    assert "超过 0.0s 未返回" in report.warnings[0]


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
        ),
    )

    assert len(calls) == 1
    assert any(event.verification.startswith("模型复核") for event in report.events)


def test_news_catalyst_notification_truncates_long_event_titles() -> None:
    long_title = "美国财政部宣布制裁9家中国和中国香港的个人及实体，外交部回应将采取一切必要措施坚定维护本国企业和公民权益"
    long_title += "，相关事项仍需继续观察后续政策原文、企业公告、产业链反馈以及市场承接"
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [{"标题": long_title, "来源": "新华社", "链接": "https://example.com/b"}]
        ),
    )

    markdown = format_catalyst_notification(report)

    assert (
        "美国财政部宣布制裁9家中国和中国香港的个人及实体，外交部回应将采取一切必要措施坚定维护本国企业和公民权益"
        not in markdown
    )
    assert "美国财政部宣布制裁9家中国和中国香港的个人及实体" in markdown
    assert "外交部、财政部、海外监管或权威媒体原文" in markdown


def test_global_news_prioritizes_notice_sources(monkeypatch) -> None:
    class FakeAk:
        @staticmethod
        def stock_info_global_cls() -> pd.DataFrame:
            return pd.DataFrame(
                [{"标题": f"普通快讯{i}", "来源": "财联社"} for i in range(5)]
            )

        @staticmethod
        def stock_info_global_em() -> pd.DataFrame:
            return pd.DataFrame()

        @staticmethod
        def stock_info_global_ths() -> pd.DataFrame:
            return pd.DataFrame()

        @staticmethod
        def stock_info_global_futu() -> pd.DataFrame:
            return pd.DataFrame()

        @staticmethod
        def stock_info_global_sina() -> pd.DataFrame:
            return pd.DataFrame()

        @staticmethod
        def news_cctv() -> pd.DataFrame:
            return pd.DataFrame([{"标题": "国常会部署设备更新", "来源": "央视"}])

        @staticmethod
        def news_economic_baidu() -> pd.DataFrame:
            return pd.DataFrame()

        @staticmethod
        def stock_notice_report() -> pd.DataFrame:
            return pd.DataFrame(
                [{"公告标题": "公司签订重大订单", "公告类型": "公司公告"}]
            )

    import sys

    monkeypatch.setitem(sys.modules, "akshare", FakeAk)

    df = _akshare_global_news(limit=3)

    rows = df.to_dict(orient="records")
    titles = []
    for row in rows:
        title = str(row.get("标题") or "")
        if not title or title.lower() == "nan":
            title = str(row.get("公告标题") or "")
        titles.append(title)
    assert titles[:2] == ["公司签订重大订单", "国常会部署设备更新"]
