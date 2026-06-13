from __future__ import annotations

from aqsp.presentation import (
    format_review_meta,
    format_watch_review_action,
    format_watch_review_line,
    normalize_research_tone,
    review_priority_label,
)


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
