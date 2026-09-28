"""数据源健康监控告警模板"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aqsp.data.source_health import SourceHealthStatus


def build_source_health_alert(
    unhealthy_sources: list[SourceHealthStatus],
    *,
    failure_threshold: int = 3,
) -> str:
    """构建数据源健康告警消息

    Args:
        unhealthy_sources: 不健康的数据源列表
        failure_threshold: 失败阈值

    Returns:
        str: Markdown格式的告警消息
    """
    if not unhealthy_sources:
        return ""

    lines = [
        "# 数据源健康告警",
        "",
        "## 概要",
        "",
        f"- 检测到 **{len(unhealthy_sources)}** 个数据源异常",
        f"- 失败阈值: {failure_threshold} 次",
        "",
        "## 异常详情",
        "",
    ]

    # 按严重程度排序（连续失败次数降序）
    sorted_sources = sorted(
        unhealthy_sources,
        key=lambda s: s.consecutive_failures,
        reverse=True,
    )

    for status in sorted_sources:
        severity = "🔴 严重" if status.consecutive_failures >= failure_threshold * 2 else "⚠️ 警告"

        lines.extend([
            f"### {severity} {status.source_id}",
            "",
            f"- **连续失败**: {status.consecutive_failures} 次",
            f"- **成功率**: {status.success_rate:.1%} ({status.total_checks} 次检查)",
            f"- **最后成功**: {status.last_success or '从未成功'}",
            f"- **最后失败**: {status.last_failure or '无记录'}",
        ])

        if status.avg_response_time_ms > 0:
            lines.append(f"- **平均响应**: {status.avg_response_time_ms:.0f}ms")

        if status.last_error:
            error_preview = status.last_error[:150]
            if len(status.last_error) > 150:
                error_preview += "..."
            lines.append(f"- **错误信息**: `{error_preview}`")

        lines.append("")

    # 添加建议操作
    lines.extend([
        "## 建议操作",
        "",
        "1. 检查网络连接和数据源服务状态",
        "2. 验证API密钥和认证信息是否有效",
        "3. 查看数据源提供方是否有服务公告",
        "4. 考虑切换到备用数据源",
        "5. 检查本地缓存和磁盘空间",
        "",
        "---",
        "",
        f"监控系统已自动记录故障详情到健康文件",
    ])

    return "\n".join(lines)


def build_source_recovery_notification(
    source_id: str,
    previous_failures: int,
) -> str:
    """构建数据源恢复通知

    Args:
        source_id: 数据源ID
        previous_failures: 之前的连续失败次数

    Returns:
        str: Markdown格式的恢复通知
    """
    return "\n".join([
        "# 数据源恢复通知",
        "",
        f"✅ 数据源 **{source_id}** 已恢复正常",
        "",
        f"- 之前连续失败: {previous_failures} 次",
        f"- 当前状态: 健康",
        "",
        "系统已自动恢复使用该数据源。",
    ])


def format_health_summary_for_briefing(
    all_sources_count: int,
    healthy_count: int,
    unhealthy_count: int,
) -> str:
    """为简报生成数据源健康摘要

    Args:
        all_sources_count: 总数据源数
        healthy_count: 健康数据源数
        unhealthy_count: 不健康数据源数

    Returns:
        str: 简短的状态摘要
    """
    if unhealthy_count == 0:
        return f"数据源状态: ✅ 全部正常 ({healthy_count}/{all_sources_count})"
    elif unhealthy_count <= 2:
        return f"数据源状态: ⚠️ 部分异常 (正常 {healthy_count}/{all_sources_count})"
    else:
        return f"数据源状态: 🔴 多个异常 (正常 {healthy_count}/{all_sources_count})"
