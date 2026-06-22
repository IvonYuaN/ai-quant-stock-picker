from __future__ import annotations

from aqsp.briefing.closing_review import (
    ClosingReviewer,
    DailyReview,
    TradeReview,
    WeeklySummary,
    format_daily_review,
    format_weekly_summary,
)
from aqsp.briefing.generator import Briefing, BriefingGenerator, BriefingSection

__all__ = [
    "Briefing",
    "BriefingGenerator",
    "BriefingSection",
    "ClosingReviewer",
    "DailyReview",
    "TradeReview",
    "WeeklySummary",
    "enhance_briefing",
    "format_daily_review",
    "format_weekly_summary",
    "compose_briefing_notification_markdown",
    "send_briefing",
]


def __getattr__(name: str):
    if name == "enhance_briefing":
        from aqsp.briefing.llm import enhance_briefing

        return enhance_briefing
    if name == "send_briefing":
        from aqsp.briefing.notifier import send_briefing

        return send_briefing
    if name == "compose_briefing_notification_markdown":
        from aqsp.briefing.notifier import compose_briefing_notification_markdown

        return compose_briefing_notification_markdown
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
