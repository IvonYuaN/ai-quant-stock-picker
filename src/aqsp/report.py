from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd

from aqsp.core.types import RunMetadata
from aqsp.models import PickResult
from aqsp.presentation import format_symbol_name
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


def _resolve_portfolio_action_label(action: str) -> str:
    return portfolio_action_label(action)


def _display_name(pick: PickResult) -> str:
    return format_symbol_name(pick.symbol, pick.name)


def _format_allocation_rationale(item: Any) -> str:
    rationale = tuple(getattr(item, "rationale", ()) or ())
    return "；".join(str(part) for part in rationale[:3])


def _format_final_decision_board(
    picks: list[PickResult],
    decision_map: dict[str, Any],
    portfolio_summary: Any | None = None,
) -> list[str]:
    if not picks:
        return []
    lines = ["## 最终决策看板", ""]
    if portfolio_summary is not None:
        lines.append(f"- PM主裁决: {portfolio_summary.headline}")
        if getattr(portfolio_summary, "regime_label", ""):
            lines.append(f"- 当前市况: {portfolio_summary.regime_label}")
        if getattr(portfolio_summary, "strategy_mix_name", ""):
            lines.append(
                "- 策略主配比: "
                f"{portfolio_summary.strategy_mix_name} | "
                f"{getattr(portfolio_summary, 'strategy_mix_description', '')}"
            )
        if getattr(portfolio_summary, "strategy_focus", ()):
            lines.append(
                "- 优先策略: "
                + "、".join(str(item) for item in portfolio_summary.strategy_focus[:4])
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
            lines.append(
                "- 重点关注: "
                + "、".join(str(item) for item in portfolio_summary.top_focus)
            )
        if getattr(portfolio_summary, "watchlist", ()):
            lines.append(
                "- 观察池: "
                + "、".join(str(item) for item in portfolio_summary.watchlist)
            )
        if getattr(portfolio_summary, "allocations", ()):
            lines.append("- 组合配置建议:")
            for item in tuple(getattr(portfolio_summary, "allocations", ()))[:3]:
                display = format_symbol_name(item.symbol, item.name)
                rationale = _format_allocation_rationale(item)
                line = f"  - {display}: {item.weight:.0%}"
                if rationale:
                    line += f" | {rationale}"
                lines.append(line)
            lead_allocations = tuple(getattr(portfolio_summary, "allocations", ()))[:2]
            if lead_allocations:
                order = " → ".join(
                    format_symbol_name(item.symbol, item.name)
                    for item in lead_allocations
                )
                lines.append(f"- 执行顺序: 先看 {order}")
        cash_reserve = float(getattr(portfolio_summary, "cash_reserve", 0.0) or 0.0)
        if cash_reserve > 0:
            lines.append(f"- 现金留存: {cash_reserve:.0%}")
        allocation_note = str(getattr(portfolio_summary, "allocation_note", "") or "")
        if allocation_note:
            lines.append(f"- 配置说明: {allocation_note}")
        lines.append("")
    for idx, pick in enumerate(picks[:3], 1):
        decision = decision_map.get(pick.symbol)
        action = getattr(decision, "action", "keep") if decision is not None else "keep"
        action_label = _resolve_portfolio_action_label(action)
        pm_reasons = getattr(decision, "reasons", ()) if decision is not None else ()
        label = _resolve_decision_label(pick)
        reason = "；".join(pick.reasons[:2]) if pick.reasons else "无"
        lines.append(
            f"- Top {idx}: {_display_name(pick)} | {label} | 评分 {pick.score} | PM {action_label}"
        )
        lines.append(f"  参考: {reason}")
        if pm_reasons and tuple(pm_reasons) != ("保持原排序",):
            lines.append("  PM依据: " + "；".join(str(item) for item in pm_reasons[:2]))
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
        "### Portfolio Manager",
        f"- 最终动作: {_resolve_portfolio_action_label(action)}",
        f"- 分数调整: {delta:+.1f}",
    ]
    if reasons:
        lines.append("- 决策依据: " + "；".join(str(item) for item in reasons))
    return "\n".join(lines)


def _format_debate_result(result: Any) -> str:
    lines = []
    lines.append("### 多Agent辩论")
    lines.append(f"- 最终共识: {result.final_consensus}")
    lines.append(f"- 建议调整: {result.recommended_adjustment}")
    if float(getattr(result, "adjusted_score", 0.0) or 0.0) > 0:
        lines.append(
            f"- 评分变化: {result.original_score:.1f} → {result.adjusted_score:.1f}"
        )
    lines.append(f"- 分歧度: {result.disagreement_score:.0%}")
    lines.append(f"- 辩论轮次: {len(result.rounds)}")

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
        return "\n".join(lines)

    debate_map = {r.symbol: r for r in debate_results} if debate_results else {}
    decision_map = (
        {item.symbol: item for item in portfolio_decisions}
        if portfolio_decisions
        else {}
    )
    lines.extend(_format_final_decision_board(picks, decision_map, portfolio_summary))

    for idx, pick in enumerate(picks, 1):
        display = _display_name(pick)
        lines.extend(
            [
                f"## {idx}. {display}",
                f"- 日期: {pick.date}",
                f"- 决策: {_resolve_decision_label(pick)} | 评分 {pick.score}",
                f"- 收盘/参考买点: {pick.close} / {pick.ideal_buy}",
                f"- 策略入口: {pick.entry_type}",
                f"- 命中策略: {', '.join(pick.strategies) or '无'}",
                f"- 仓位建议: {pick.position}",
                f"- 止损/止盈位: {pick.stop_loss} / {pick.take_profit}",
                f"- 理由: {'；'.join(pick.reasons) or '无'}",
                f"- 风险提示: {'；'.join(pick.risks) or '无'}",
                "",
            ]
        )
        if pick.symbol in debate_map:
            lines.append(_format_debate_result(debate_map[pick.symbol]))
            lines.append("")
        if (
            pick.symbol in decision_map
            and getattr(decision_map[pick.symbol], "action", "keep") == "promote"
        ):
            decision_text = _format_portfolio_decision(decision_map[pick.symbol])
            if decision_text:
                lines.append(decision_text)
                lines.append("")

    lines.append("> 仅供研究，不构成投资建议。")
    return "\n".join(lines)


def _metadata_lines(metadata: RunMetadata) -> list[str]:
    actual = metadata.actual_source or "unknown"
    source_text = (
        actual
        if metadata.requested_source == actual
        else f"{metadata.requested_source} -> {actual}"
    )
    return [
        "## 运行参数",
        f"- 数据源: {source_text}",
        (
            "- 数据层级: "
            f"fresh={metadata.source_freshness_tier or 'unknown'} / "
            f"cover={metadata.source_coverage_tier or 'unknown'} / "
            f"local={metadata.source_local_status or 'unknown'}"
        ),
        (
            "- 数据时效: "
            f"latest={metadata.data_latest_trade_date or 'unknown'} / "
            f"lag={metadata.data_lag_days}d"
        ),
        (
            "- 数据健康: "
            f"{metadata.source_health_label or 'unknown'}"
            + (
                f" / {metadata.source_health_message}"
                if metadata.source_health_message
                else ""
            )
        ),
        (
            "- 候选池: "
            f"显式 {metadata.explicit_symbol_count} / "
            f"解析 {metadata.resolved_symbol_count} / "
            f"取数 {metadata.fetched_frame_count} / "
            f"筛选前 {metadata.screened_count} / "
            f"最终 {metadata.final_count}"
        ),
        f"- max_universe: {metadata.max_universe}",
        f"- 价格边界: {metadata.min_price} - {metadata.max_price}",
        f"- 20日均成交额下限: {metadata.min_avg_amount:.0f}",
        f"- 在线观察因子: {'on' if metadata.online_factors_enabled else 'off'}",
        f"- thresholds.version: {metadata.thresholds_version}",
        f"- regime: {metadata.regime or 'unknown'}",
        "",
    ]
