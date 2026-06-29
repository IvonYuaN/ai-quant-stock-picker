from __future__ import annotations

from dataclasses import asdict
from html import escape
from typing import Any

import pandas as pd

from aqsp.core.types import RunMetadata
from aqsp.models import PickResult
from aqsp.presentation import (
    describe_source_health,
    describe_source_layers,
    format_source_route,
    format_review_meta,
    format_symbol_name,
    format_watch_review_line,
    humanize_runtime_snapshot_line,
    normalize_research_tone,
    review_priority_label,
)
from aqsp.ratings import portfolio_action_label, rating_label

RESULT_COLUMNS = [
    "symbol",
    "name",
    "date",
    "close",
    "score",
    "rating",
    "entry_type",
    "ideal_buy",
    "stop_loss",
    "take_profit",
    "position",
    "strategies",
    "reasons",
    "risks",
]


def _resolve_decision_label(pick: PickResult) -> str:
    return rating_label(pick.rating)


def _resolve_display_decision_label(pick: PickResult, decision: Any | None) -> str:
    if getattr(decision, "action", "") == "downgrade":
        return rating_label("avoid")
    if pick.metrics.get("portfolio_action") == "downgrade":
        return rating_label("avoid")
    return _resolve_decision_label(pick)


def _resolve_portfolio_action_label(action: str) -> str:
    return portfolio_action_label(action)


def _display_name(pick: PickResult) -> str:
    return _safe_markdown_text(format_symbol_name(pick.symbol, pick.name))


def _candidate_status(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_status", "") or "")


def _candidate_blocker(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_blocker", "") or "")


def _candidate_next_step(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_next_step", "") or "")


def _candidate_review_window(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_review_window", "") or "")


def _candidate_review_priority(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_review_priority", "") or "")


def _review_priority_label(priority: str) -> str:
    return review_priority_label(priority)


def _format_allocation_rationale(item: Any) -> str:
    rationale = tuple(getattr(item, "rationale", ()) or ())
    return "；".join(str(part) for part in rationale[:3])


def _normalize_reason_text(reason: object) -> str:
    text = _safe_markdown_text(reason)
    if not text:
        return ""
    for marker in ("与前序候选高相关", "多Agent辩论偏谨慎", "多Agent辩论支持"):
        if marker in text and f"；{marker}" not in text and not text.startswith(marker):
            text = text.replace(marker, f"；{marker}", 1)
    return text


def _safe_markdown_text(value: object) -> str:
    return escape(normalize_research_tone(str(value).strip()), quote=False)


def _format_reason_list(reasons: Any, *, limit: int | None = None) -> str:
    if not reasons:
        return ""
    raw_items = (reasons,) if isinstance(reasons, str) else tuple(reasons)
    items = tuple(
        item for item in (_normalize_reason_text(item) for item in raw_items) if item
    )
    if limit is not None:
        items = items[:limit]
    return "；".join(items)


def _format_final_decision_board(
    picks: list[PickResult],
    decision_map: dict[str, Any],
    portfolio_summary: Any | None = None,
) -> list[str]:
    if not picks:
        return []
    lines = ["## 今日重点看板", ""]
    if portfolio_summary is not None:
        lines.append(f"- PM主裁决: {_safe_markdown_text(portfolio_summary.headline)}")
        if getattr(portfolio_summary, "regime_label", ""):
            lines.append(
                f"- 当前市况: {_safe_markdown_text(portfolio_summary.regime_label)}"
            )
        if getattr(portfolio_summary, "strategy_mix_name", ""):
            lines.append(
                "- 策略主配比: "
                f"{_safe_markdown_text(portfolio_summary.strategy_mix_name)} | "
                f"{_safe_markdown_text(getattr(portfolio_summary, 'strategy_mix_description', ''))}"
            )
        if getattr(portfolio_summary, "strategy_focus", ()):
            lines.append(
                "- 优先策略: "
                + "、".join(
                    _safe_markdown_text(item)
                    for item in portfolio_summary.strategy_focus[:4]
                )
            )
        if getattr(portfolio_summary, "strategy_weights", ()):
            lines.append(
                "- 策略权重建议: "
                + "、".join(
                    f"{strategy_id} {weight:.0%}"
                    for strategy_id, weight in tuple(
                        getattr(portfolio_summary, "strategy_weights", ())
                    )[:4]
                )
            )
        if getattr(portfolio_summary, "top_focus", ()):
            focus_label = (
                "纸面复核"
                if getattr(portfolio_summary, "allocations", ())
                else "观察重点"
            )
            lines.append(
                f"- {focus_label}: "
                + "、".join(
                    _safe_markdown_text(item) for item in portfolio_summary.top_focus
                )
            )
        if getattr(portfolio_summary, "watchlist", ()):
            lines.append(
                "- 继续观察名单: "
                + "、".join(
                    _safe_markdown_text(item) for item in portfolio_summary.watchlist
                )
            )
        if getattr(portfolio_summary, "action_hotspots", ()):
            lines.append(
                "- 需要重点确认: "
                + "；".join(
                    _safe_markdown_text(item)
                    for item in portfolio_summary.action_hotspots
                )
            )
        if getattr(portfolio_summary, "execution_blockers", ()):
            lines.append("- 现在卡在哪:")
            for item in tuple(getattr(portfolio_summary, "execution_blockers", ()))[:3]:
                lines.append(f"  - {_safe_markdown_text(item)}")
        if getattr(portfolio_summary, "watch_reviews", ()):
            lines.append("- 观察名单接下来:")
            for item in tuple(getattr(portfolio_summary, "watch_reviews", ()))[:2]:
                lines.append(
                    "  - "
                    + format_watch_review_line(
                        format_symbol_name(item.symbol, item.name),
                        priority=str(getattr(item, "priority", "") or ""),
                        review_window=str(getattr(item, "review_window", "") or ""),
                        next_step=normalize_research_tone(
                            str(getattr(item, "next_step", "") or "")
                        ),
                    )
                )
        if getattr(portfolio_summary, "allocations", ()):
            lines.append("- 比例参考:")
            for item in tuple(getattr(portfolio_summary, "allocations", ()))[:3]:
                display = _safe_markdown_text(
                    format_symbol_name(item.symbol, item.name)
                )
                rationale = _format_allocation_rationale(item)
                line = f"  - {display}: {item.weight:.0%}"
                if rationale:
                    line += f" | {_safe_markdown_text(rationale)}"
                lines.append(line)
            lead_allocations = tuple(getattr(portfolio_summary, "allocations", ()))[:2]
            if lead_allocations:
                order = " → ".join(
                    _safe_markdown_text(format_symbol_name(item.symbol, item.name))
                    for item in lead_allocations
                )
                lines.append(f"- 再看顺序: 先看 {order}")
        cash_reserve = float(getattr(portfolio_summary, "cash_reserve", 0.0) or 0.0)
        if cash_reserve > 0:
            lines.append(f"- 现金留存: {cash_reserve:.0%}")
        allocation_note = str(getattr(portfolio_summary, "allocation_note", "") or "")
        if allocation_note:
            lines.append(f"- 配置说明: {_safe_markdown_text(allocation_note)}")
        lines.append("")
    for idx, pick in enumerate(picks[:3], 1):
        decision = decision_map.get(pick.symbol)
        action = getattr(decision, "action", "keep") if decision is not None else "keep"
        action_label = _resolve_portfolio_action_label(action)
        pm_reasons = getattr(decision, "reasons", ()) if decision is not None else ()
        label = _resolve_display_decision_label(pick, decision)
        status = _candidate_status(pick)
        blocker = _candidate_blocker(pick)
        next_step = _candidate_next_step(pick)
        review_window = _candidate_review_window(pick)
        review_priority = _review_priority_label(_candidate_review_priority(pick))
        reason = _format_reason_list(pick.reasons[:2]) if pick.reasons else "无"
        headline = f"- 重点 {idx}: {_safe_markdown_text(_display_name(pick))} | {label}"
        if status:
            headline += f" | {_safe_markdown_text(status)}"
        headline += f" | 评分 {pick.score} | 处理 {action_label}"
        lines.append(headline)
        lines.append(f"  参考: {reason}")
        if blocker:
            lines.append(f"  现在卡在哪: {_safe_markdown_text(blocker)}")
        if next_step:
            lines.append(f"  下一步: {_safe_markdown_text(next_step)}")
        if review_priority or review_window:
            lines.append(
                "  再看时间: "
                + _safe_markdown_text(
                    format_review_meta(review_priority, review_window)
                )
            )
        pm_reason_text = _format_reason_list(pm_reasons, limit=2)
        if pm_reason_text and pm_reason_text != "保持原排序":
            lines.append("  PM依据: " + pm_reason_text)
    lines.append("")
    return lines


def _format_portfolio_decision(decision: Any) -> str:
    action = getattr(decision, "action", "keep")
    delta = getattr(decision, "score_delta", 0.0)
    reasons = getattr(decision, "reasons", ())
    if (
        action == "keep"
        and abs(delta) < 1e-9
        and (not reasons or tuple(reasons) == ("保持原排序",))
    ):
        return ""
    lines = [
        "### 排序调整",
        f"- 今日处理: {_resolve_portfolio_action_label(action)}",
        f"- 分数调整: {delta:+.1f}",
    ]
    if reasons:
        lines.append("- 决策依据: " + _format_reason_list(reasons))
    return normalize_research_tone("\n".join(lines))


def _format_debate_result(result: Any) -> str:
    lines = []
    lines.append("### 不同看法")
    lines.append(f"- 最终共识: {result.final_consensus}")
    lines.append(
        f"- 讨论倾向: {result.recommended_adjustment}（附件观点，不覆盖 runtime 打分）"
    )
    if float(getattr(result, "adjusted_score", 0.0) or 0.0) > 0:
        lines.append(
            f"- 参考分歧: runtime 原始分 {result.original_score:.1f}；附件参考分 {result.adjusted_score:.1f}"
        )
    lines.append(f"- 分歧: {result.disagreement_score:.0%}")
    lines.append(f"- 讨论轮次: {len(result.rounds)}")

    bull_count = sum(1 for v in result.final_vote.values() if v == "bullish")
    bear_count = sum(1 for v in result.final_vote.values() if v == "bearish")
    if bull_count or bear_count:
        lines.append(f"- 投票结果: 看多 {bull_count} 票 / 看空 {bear_count} 票")

    if result.risk_warnings:
        lines.append("#### 风险提示")
        for risk in result.risk_warnings:
            lines.append(f"- ⚠️ {risk}")

    if result.opportunity_highlights:
        lines.append("#### 机会亮点")
        for opp in result.opportunity_highlights:
            lines.append(f"- ✅ {opp}")

    return "\n".join(lines)


def to_dataframe(picks: list[PickResult]) -> pd.DataFrame:
    rows = []
    for pick in picks:
        row = asdict(pick)
        row["strategies"] = ",".join(pick.strategies)
        row["reasons"] = "；".join(pick.reasons)
        row["risks"] = "；".join(pick.risks)
        row.update(pick.metrics)
        del row["metrics"]
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    return pd.DataFrame(rows)


def to_markdown(
    picks: list[PickResult],
    title: str = "AI 量化选股报告",
    metadata: RunMetadata | None = None,
    debate_results: list[Any] | None = None,
    portfolio_decisions: list[Any] | None = None,
    portfolio_summary: Any | None = None,
) -> str:
    lines = [f"# {title}", ""]
    if metadata is not None:
        lines.extend(_metadata_lines(metadata))
    if not picks:
        lines.append("无符合条件的候选。")
        return normalize_research_tone("\n".join(lines))

    debate_map = {r.symbol: r for r in debate_results} if debate_results else {}
    decision_map = (
        {item.symbol: item for item in portfolio_decisions}
        if portfolio_decisions
        else {}
    )
    lines.extend(_format_final_decision_board(picks, decision_map, portfolio_summary))

    for idx, pick in enumerate(picks, 1):
        display = _display_name(pick)
        status = _candidate_status(pick)
        blocker = _candidate_blocker(pick)
        next_step = _candidate_next_step(pick)
        review_window = _candidate_review_window(pick)
        review_priority = _review_priority_label(_candidate_review_priority(pick))
        decision = decision_map.get(pick.symbol)
        decision_text = _resolve_display_decision_label(pick, decision)
        if status:
            decision_text += f" | {status}"
        lines.extend(
            [
                f"## {idx}. {display}",
                f"- 日期: {pick.date}",
                f"- 决策: {decision_text} | 评分 {pick.score:.1f}",
                f"- 收盘/参考价: {pick.close} / {pick.ideal_buy}",
                f"- 策略入口: {_safe_markdown_text(pick.entry_type)}",
                f"- 命中策略: {_format_reason_list(pick.strategies) or '无'}",
                f"- 比例参考: {pick.position}",
                f"- 最多亏到/先看目标: {pick.stop_loss} / {pick.take_profit}",
                f"- 理由: {_format_reason_list(pick.reasons) or '无'}",
                f"- 风险提示: {_format_reason_list(pick.risks) or '无'}",
                "",
            ]
        )
        if blocker:
            lines.insert(
                len(lines) - 1, f"- 现在卡在哪: {_safe_markdown_text(blocker)}"
            )
        if next_step:
            lines.insert(
                len(lines) - 1,
                f"- 接下来先看: {_safe_markdown_text(next_step)}",
            )
        if review_priority or review_window:
            lines.insert(
                len(lines) - 1,
                "- 再看优先级/时机: "
                + format_review_meta(review_priority, review_window),
            )
        if pick.symbol in debate_map:
            lines.append(_format_debate_result(debate_map[pick.symbol]))
            lines.append("")
        if pick.symbol in decision_map:
            decision_text = _format_portfolio_decision(decision_map[pick.symbol])
            if decision_text:
                lines.append(decision_text)
                lines.append("")

    return normalize_research_tone("\n".join(lines))


def _metadata_lines(metadata: RunMetadata) -> list[str]:
    source_text = format_source_route(
        metadata.requested_source,
        metadata.actual_source,
    )
    return [
        "## 数据与规则",
        f"- 数据来源: {source_text}",
        "- 数据完整度: "
        + describe_source_layers(
            metadata.source_freshness_tier,
            metadata.source_coverage_tier,
            metadata.source_local_status,
        ),
        (
            "- 数据时效: "
            f"最新交易日 {metadata.data_latest_trade_date or '未记录'} / "
            f"延迟 {metadata.data_lag_days} 天"
        ),
        "- 数据状态: "
        + describe_source_health(
            metadata.source_health_label,
            metadata.source_health_message,
        ),
        (
            "- 扫描范围: "
            f"显式 {metadata.explicit_symbol_count} / "
            f"解析 {metadata.resolved_symbol_count} / "
            f"取数 {metadata.fetched_frame_count} / "
            f"筛选前 {metadata.screened_count} / "
            f"最终 {metadata.final_count}"
        ),
        f"- 最大扫描范围: {metadata.max_universe}",
        f"- 价格范围: {metadata.min_price} - {metadata.max_price}",
        f"- 20日均成交额下限: {metadata.min_avg_amount:.0f}",
        f"- 盘中增强: {'已开启' if metadata.online_factors_enabled else '未开启'}",
        "- "
        + humanize_runtime_snapshot_line(
            f"thresholds.version: {metadata.thresholds_version}"
        ),
        "- "
        + humanize_runtime_snapshot_line(f"市场标签: {metadata.regime or 'unknown'}"),
        "",
    ]
