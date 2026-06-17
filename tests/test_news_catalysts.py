from __future__ import annotations

import time
from types import SimpleNamespace

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
    )

    markdown = format_catalyst_notification(report)

    assert markdown.startswith("# 消息面雷达-")
    assert "## 结论" in markdown
    assert "## 事件" in markdown
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
    df.attrs["aqsp_warnings"] = ("source timeout", "source timeout")

    report = build_catalyst_report(fetch_global_news=lambda _limit: df)

    assert report.source_status == "failed"
    assert report.warnings == ("全市场快讯: source timeout",)
    markdown = format_catalyst_notification(report)
    assert "无有效结论：消息源失败" in markdown


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
    )

    assert len(report.events) == 1
    assert report.events[0].source == "同花顺、富途"
    assert report.events[0].source_count == 2
    assert report.events[0].verification == "多源交叉"
    markdown = format_catalyst_notification(report)
    assert markdown.count("- 1. 利好") == 1
    assert "- 2. 利好" not in markdown
    assert "来源: 同花顺、富途" in markdown


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


def test_news_catalyst_infers_source_from_known_url() -> None:
    report = build_catalyst_report(
        fetch_global_news=lambda _limit: pd.DataFrame(
            [
                {
                    "标题": "中国西电：中标国家电网特高压项目，金额18.99亿元",
                    "链接": "https://news.10jqka.com.cn/20260612/c677419676.shtml",
                    "时间": "2026-06-12 15:33:16",
                }
            ]
        ),
    )

    assert report.events
    assert report.events[0].source == "同花顺"
    assert report.events[0].name == "中国西电"
    assert report.events[0].verification == "媒体来源"
    assert report.events[0].confidence >= 0.5
    markdown = format_catalyst_notification(report)
    assert "中国西电|订单/需求验证" in markdown
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
        config=NewsCatalystConfig(min_confidence=0.3),
    )

    assert report.events
    assert report.events[0].name == ""
    assert report.events[0].source == "富途"
    markdown = format_catalyst_notification(report)
    assert "据伊朗迈赫尔通讯社**" not in markdown
    assert "来源: 富途" in markdown


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
    assert all("模型复核" not in event.verification for event in report.events)
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
            pd.DataFrame(
                [{"公告标题": "公司签订重大订单", "公告类型": "公司公告"}]
            ),
        ],
        fetch_symbol_news=lambda _symbol: (
            pd.DataFrame(),
        ),
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
