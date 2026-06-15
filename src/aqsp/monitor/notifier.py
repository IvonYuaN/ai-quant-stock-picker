from __future__ import annotations

from typing import TYPE_CHECKING

from aqsp.config import load_runtime_config
from aqsp.core.time import now_shanghai
from aqsp.notification_style import compact_notification_markdown
from aqsp.notify_templates import build_monitor_notification
from aqsp.notifier import notify_markdown, print_notify_results

if TYPE_CHECKING:
    from .checker import MonitorResult


def format_alert(results: list[MonitorResult]) -> str:
    """Format triggered monitors into alert message."""
    now = now_shanghai()

    critical = [r for r in results if r.severity == "critical" and r.triggered]
    warnings = [r for r in results if r.severity == "warning" and r.triggered]

    lines = [
        "# 系统监控告警",
        "",
        "## 结论",
        "",
        f"- 检查时间: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 严重告警: {len(critical)}",
        f"- 一般告警: {len(warnings)}",
        "",
    ]

    if critical:
        lines.append("## 严重")
        lines.append("")
        for r in critical:
            lines.append(f"### {r.name}")
            lines.append(f"- 描述: {r.message}")
            if r.details:
                for key, value in r.details.items():
                    lines.append(f"- {key}: {value}")
            lines.append("")

    if warnings:
        lines.append("## 警告")
        lines.append("")
        for r in warnings:
            lines.append(f"### {r.name}")
            lines.append(f"- 描述: {r.message}")
            if r.details:
                for key, value in r.details.items():
                    lines.append(f"- {key}: {value}")
            lines.append("")

    if not critical and not warnings:
        lines.append("- 总体状态: 正常")

    return compact_notification_markdown("\n".join(lines))


def send_alerts(results: list[MonitorResult]) -> None:
    """Send alerts for triggered monitors via notifier."""
    triggered = [r for r in results if r.triggered]
    if not triggered:
        return

    alert_msg = build_monitor_notification(
        triggered,
        mode=load_runtime_config().notify_mode,
    )
    notify_results = notify_markdown(alert_msg)
    print_notify_results(notify_results, prefix="monitor notify")
