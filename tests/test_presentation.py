from __future__ import annotations

from aqsp.presentation import (
    format_review_meta,
    format_watch_review_action,
    format_watch_review_line,
    normalize_research_tone,
    review_priority_label,
)
from aqsp.notification_style import compact_notification_markdown


def test_review_priority_label_returns_chinese_label_when_known() -> None:
    assert review_priority_label("high") == "高优先级"


def test_format_review_meta_returns_priority_and_window_when_both_present() -> None:
    assert format_review_meta("high", "盘中走强后") == "高优先级 / 盘中走强后"


def test_format_watch_review_line_returns_unified_watch_review_text() -> None:
    assert (
        format_watch_review_line(
            "688981 中芯国际",
            priority="high",
            review_window="盘中走强后",
            next_step="等待量价继续走强后，再评估是否转入执行名单",
        )
        == "688981 中芯国际 | 高优先级 / 盘中走强后 | 等待量价继续走强后，再评估是否转入纸面复核名单"
    )


def test_format_watch_review_action_returns_unified_action_text() -> None:
    assert (
        format_watch_review_action(
            "688981 中芯国际",
            priority="high",
            review_window="盘中走强后",
            next_step="等待量价继续走强后，再评估是否转入执行名单",
        )
        == "先盯 688981 中芯国际，等待量价继续走强后，再评估是否转入纸面复核名单（高优先级 / 盘中走强后）。"
    )


def test_normalize_research_tone_preserves_paper_review_wording() -> None:
    assert (
        normalize_research_tone("等待量价确认后转入纸面复核名单")
        == "等待量价确认后转入纸面复核名单"
    )
    assert (
        normalize_research_tone("等待量价确认后转入重点跟踪名单")
        == "等待量价确认后转入纸面复核名单"
    )


def test_normalize_research_tone_removes_process_wording() -> None:
    assert (
        normalize_research_tone("AI 研究给出模型复核，调整依据来自 runtime 原始分")
        == "研究给出复核，调整原因来自 系统原始评分"
    )
    assert normalize_research_tone("待核对依据: 分歧扩大") == "待核对原因: 分歧扩大"


def test_compact_notification_markdown_adds_spacing_and_removes_process_terms() -> None:
    markdown = compact_notification_markdown(
        "\n".join(
            [
                "# AI 量化选股日报",
                "## 数据源状态",
                "  - 依据: fallback 到 eastmoney",
                "## 主链候选",
                "  - AI 研究: 模型复核通过",
                "  - 重点跟踪名单: 600519 等待下单",
            ]
        )
    )

    assert "# 每日研究复盘" in markdown
    assert "\n\n## 数据\n\n" in markdown
    assert "\n\n## 候选\n\n" in markdown
    assert "- 原因: 已切换到备用数据源 eastmoney" in markdown
    assert "- 纸面复核名单: 600519 等待纸面记录" in markdown
    assert "AI 研究" not in markdown
    assert "模型复核" not in markdown
    assert "复核通过" not in markdown
    assert "依据" not in markdown
