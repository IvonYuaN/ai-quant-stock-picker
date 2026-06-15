#!/usr/bin/env python3
"""Render a static AQSP dashboard from the latest run outputs."""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from aqsp.briefing.agent_roles import agent_role_emoji, agent_role_label
from aqsp.core.time import now_shanghai
from aqsp.data.source_health import (
    describe_source_health,
    notification_level_for_health_label,
    read_source_health,
)
from aqsp.presentation import (
    describe_source_health as present_source_health,
    describe_source_layers,
    format_source_route,
    format_review_meta,
    format_watch_review_line,
    normalize_research_tone,
    review_priority_label,
    source_health_label,
)
from aqsp.ratings import is_tradable_rating, portfolio_action_label, rating_label
from aqsp.research.summary import (
    ResearchSummary,
    load_research_summary,
    research_findings_badge,
    research_findings_display,
)
from aqsp.walkforward_gate import validate_walkforward_gate_payload


@dataclass(frozen=True)
class LedgerStats:
    total: int
    pending: int
    validated: int
    not_executable: int
    win_rate: float | None
    avg_return_pct: float | None
    latest_signal_date: str
    thresholds_version: str
    requested_source: str
    actual_source: str
    source_freshness_tier: str
    source_coverage_tier: str
    source_health_label: str
    source_health_message: str
    notify_level: str
    fallback_used: bool


@dataclass(frozen=True)
class PaperStats:
    total: int
    open_positions: int
    closed: int
    not_executable: int
    pending_entry: int
    avg_return_pct: float | None


def _safe_float(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _strategy_values(raw: Any) -> list[str]:
    if raw in ("", None):
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = [part.strip() for part in raw.split(",") if part.strip()]
        else:
            if isinstance(parsed, str):
                parsed = [parsed]
            elif not isinstance(parsed, list):
                parsed = [str(parsed)]
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(raw, (list, tuple, set)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [str(raw).strip()] if str(raw).strip() else []


def read_ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_paper_rows(path: Path) -> list[dict[str, Any]]:
    return read_ledger_rows(path)


def summarize_ledger(rows: list[dict[str, Any]]) -> LedgerStats:
    latest_row = next(
        (
            row
            for row in reversed(rows)
            if row.get("run_requested_source") or row.get("run_actual_source")
        ),
        rows[-1] if rows else {},
    )
    validated_rows = [r for r in rows if r.get("status") == "validated"]
    executable_validated = [
        r for r in validated_rows if r.get("status") != "not_executable"
    ]
    returns = [
        value
        for value in (_safe_float(r.get("return_pct")) for r in executable_validated)
        if value is not None
    ]
    wins = sum(1 for r in executable_validated if bool(r.get("win")))
    win_rate = wins / len(executable_validated) if executable_validated else None
    avg_return = sum(returns) / len(returns) if returns else None
    return LedgerStats(
        total=len(rows),
        pending=sum(1 for r in rows if r.get("status") == "pending"),
        validated=len(validated_rows),
        not_executable=sum(1 for r in rows if r.get("status") == "not_executable"),
        win_rate=win_rate,
        avg_return_pct=avg_return,
        latest_signal_date=str(rows[-1].get("signal_date", "")) if rows else "",
        thresholds_version=str(rows[-1].get("thresholds_version", "")) if rows else "",
        requested_source=str(latest_row.get("run_requested_source", "")),
        actual_source=str(latest_row.get("run_actual_source", "")),
        source_freshness_tier=str(latest_row.get("run_source_freshness_tier", "")),
        source_coverage_tier=str(latest_row.get("run_source_coverage_tier", "")),
        source_health_label=str(latest_row.get("run_source_health_label", "")),
        source_health_message=str(latest_row.get("run_source_health_message", "")),
        notify_level=notification_level_for_health_label(
            str(latest_row.get("run_source_health_label", "") or "")
        ),
        fallback_used=bool(latest_row.get("run_fallback_used", False)),
    )


def summarize_paper(rows: list[dict[str, Any]]) -> PaperStats:
    closed_rows = [r for r in rows if r.get("status") == "closed"]
    returns = [
        value
        for value in (_safe_float(r.get("return_pct")) for r in closed_rows)
        if value is not None
    ]
    avg_return = sum(returns) / len(returns) if returns else None
    return PaperStats(
        total=len(rows),
        open_positions=sum(1 for r in rows if r.get("status") == "open"),
        closed=len(closed_rows),
        not_executable=sum(1 for r in rows if r.get("status") == "not_executable"),
        pending_entry=sum(1 for r in rows if r.get("status") == "pending_entry"),
        avg_return_pct=avg_return,
    )


def read_debate_results(path: Path) -> dict[str, dict[str, Any]]:
    """读取辩论结果，按symbol聚合，每个股票返回最新日期的辩论"""
    if not path.exists():
        return {}
    results: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                data = json.loads(line)
                symbol = data.get("symbol", "")
                if symbol:
                    debate_date = data.get("debate_date", "")
                    # 如果该股票还没有记录，或者新记录的日期更近，则更新
                    if symbol not in results or debate_date > results[symbol].get(
                        "debate_date", ""
                    ):
                        results[symbol] = data
            except json.JSONDecodeError:
                continue
    return results


def read_candidates(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path, dtype=str).fillna("")
    except EmptyDataError:
        return []
    return [dict(row) for row in df.to_dict(orient="records")]


def latest_candidate_date(candidates: list[dict[str, str]]) -> str:
    dates = sorted({row.get("date", "") for row in candidates if row.get("date")})
    return dates[-1] if dates else ""


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "冷启动"
    return f"{value:.2%}"


def _fmt_num(value: str | float | None) -> str:
    if value in ("", None):
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _fmt_return(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "-"
    cls = "pos" if number > 0 else "neg" if number < 0 else ""
    return f"<span class='{cls}'>{number:.2f}</span>"


def _debate_age_label(debate_date: str) -> str:
    if not debate_date:
        return ""
    try:
        debate_dt = datetime.strptime(debate_date, "%Y-%m-%d").date()
    except ValueError:
        return ""
    days_diff = (now_shanghai().date() - debate_dt).days
    if days_diff <= 0:
        return "今日"
    if days_diff == 1:
        return "昨日"
    return f"{days_diff}天前"


def _role_display_name(role: str) -> str:
    emoji = agent_role_emoji(role)
    label = agent_role_label(role, language="zh-CN")
    return f"{emoji} {label}".strip()


def _decision_label_from_rating(rating: str) -> str:
    return rating_label(rating)


def _candidate_display_name(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol", "") or "")
    name = str(row.get("name", "") or "")
    if not name or name == symbol:
        return symbol
    return f"{symbol} {name}"


def _review_priority_label(priority: str) -> str:
    return review_priority_label(priority)


def _candidate_review_meta(row: dict[str, Any]) -> str:
    return format_review_meta(
        str(row.get("candidate_review_priority", "") or ""),
        str(row.get("candidate_review_window", "") or ""),
    )


def _lifecycle_overview_panel(candidates: list[dict[str, str]]) -> str:
    actionable: list[str] = []
    watchlist: list[str] = []
    blocked: list[str] = []
    review_items: list[str] = []

    for row in candidates:
        display = _candidate_display_name(row)
        rating = str(row.get("rating", "") or "")
        action = str(row.get("portfolio_action", "") or "")
        status = str(row.get("candidate_status", "") or "")
        blocker = str(row.get("candidate_blocker", "") or "")
        next_step = str(row.get("candidate_next_step", "") or "")
        review_meta = _candidate_review_meta(row)

        if action == "promote" or is_tradable_rating(rating):
            actionable.append(display)
        if rating == "watch" or action == "downgrade" or status:
            watchlist.append(display)
        if blocker:
            blocked.append(f"{display}: {blocker}")
        if next_step or review_meta:
            review_items.append(
                format_watch_review_line(
                    display,
                    priority=str(row.get("candidate_review_priority", "") or ""),
                    review_window=str(row.get("candidate_review_window", "") or ""),
                    next_step=next_step,
                )
            )

    if actionable:
        headline = "今日纸面复核名单"
        headline_detail = "、".join(actionable[:3])
    elif review_items:
        headline = "观察名单接下来"
        headline_detail = review_items[0]
    elif watchlist:
        headline = "继续观察名单"
        headline_detail = "、".join(watchlist[:3])
    else:
        headline = "暂无主链"
        headline_detail = "等待下一轮有效候选输出"

    action_line = "明日无明确主链复核，先确认数据与候选是否完整。"
    if actionable:
        action_line = f"明日先盯 {' → '.join(actionable[:2])} 的开盘强弱与流动性。"
    elif review_items:
        action_line = f"明日先盯 {review_items[0]}。"
    elif watchlist:
        action_line = f"明日先围绕 {'、'.join(watchlist[:2])} 再看，不放大纸面仓位。"

    summary_cards = [
        ("主链复核", headline, headline_detail),
        (
            "候选分层",
            f"纸面复核 {len(actionable)} / 观察 {len(watchlist)}",
            "纸面复核与观察对象已按当前主链输出分层。",
        ),
        (
            "现在卡在哪",
            blocked[0] if blocked else "暂无明确阻塞",
            "；".join(blocked[1:3]) if len(blocked) > 1 else "",
        ),
        (
            "明日复核",
            action_line,
            review_items[1] if len(review_items) > 1 else "",
        ),
    ]
    summary_cards = [
        (
            normalize_research_tone(label),
            normalize_research_tone(value),
            normalize_research_tone(detail),
        )
        for label, value, detail in summary_cards
    ]

    card_html = "".join(
        f"""
        <div class="lifecycle-card">
          <div class="lifecycle-label">{html.escape(label)}</div>
          <div class="lifecycle-value">{html.escape(value)}</div>
          {f"<div class='lifecycle-detail'>{html.escape(detail)}</div>" if detail else ""}
        </div>
        """
        for label, value, detail in summary_cards
    )

    return f"""
    <section class="panel lifecycle-panel">
      <h2>主链状态总览</h2>
      <div class="lifecycle-grid">
        {card_html}
      </div>
    </section>
    """


def _candidate_cards(
    candidates: list[dict[str, str]], debate_map: dict[str, dict[str, Any]]
) -> str:
    if not candidates:
        return '<section class="empty">本次没有候选股，或数据源未成功返回。</section>'
    cards: list[str] = []
    for idx, row in enumerate(candidates, 1):
        symbol = html.escape(row.get("symbol", ""))
        name = html.escape(row.get("name", ""))
        score = _fmt_num(row.get("score"))
        rating = str(row.get("rating", "") or "")
        decision_label = html.escape(_decision_label_from_rating(rating))
        portfolio_action = str(row.get("portfolio_action", "") or "").strip()
        portfolio_text = (
            html.escape(portfolio_action_label(portfolio_action))
            if portfolio_action
            else ""
        )
        candidate_status = html.escape(str(row.get("candidate_status", "") or ""))
        candidate_blocker = html.escape(str(row.get("candidate_blocker", "") or ""))
        candidate_next_step = html.escape(str(row.get("candidate_next_step", "") or ""))
        candidate_review_window = html.escape(
            str(row.get("candidate_review_window", "") or "")
        )
        candidate_review_priority = html.escape(
            _review_priority_label(str(row.get("candidate_review_priority", "") or ""))
        )
        candidate_review_meta = " / ".join(
            part
            for part in (candidate_review_priority, candidate_review_window)
            if part
        )
        strategies = html.escape(" / ".join(_strategy_values(row.get("strategies"))))
        reasons = html.escape(row.get("reasons", ""))
        risks = html.escape(row.get("risks", ""))
        debate = debate_map.get(symbol)
        has_debate = debate is not None

        # 数据关联性检查：辩论时间是否在合理范围内
        debate_age = (
            _debate_age_label(str(debate.get("debate_date", ""))) if has_debate else ""
        )

        debate_btn = (
            f"""<button class="debate-btn" onclick="showDebate('{symbol}')" aria-label="查看辩论详情">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
                </svg>
                Agent讨论 {f'<span class="debate-age">({debate_age})</span>' if debate_age else ""}
            </button>"""
            if has_debate
            else """<button class="debate-btn no-debate" disabled aria-label="暂无辩论数据">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/>
                    <line x1="12" y1="8" x2="12" y2="12"/>
                    <line x1="12" y1="16" x2="12.01" y2="16"/>
                </svg>
                暂无 Agent 讨论
            </button>"""
        )
        adjustment_badge = ""
        if has_debate:
            adj = debate.get("recommended_adjustment", "keep")
            adj_class = {"raise": "bull", "lower": "bear", "keep": "neutral"}.get(
                adj, "neutral"
            )
            adj_text = {
                "raise": "辩论倾向上调",
                "lower": "辩论倾向下调",
                "keep": "辩论倾向维持",
            }.get(adj, "辩论倾向维持")
            adjustment_badge = (
                f"<span class='adjustment-badge {adj_class}'>{adj_text}</span>"
            )

        # 准备评分展示
        original_score = _fmt_num(row.get("score"))
        adj_score = ""
        score_diff = ""
        if has_debate:
            debate_original = debate.get("original_score", 0)
            debate_adjusted = debate.get("adjusted_score", debate_original)
            adj_weight = debate.get("adjustment_weight", 0)
            adj_score = _fmt_num(debate_adjusted)
            diff_pct = adj_weight * 100
            if diff_pct > 0:
                score_diff = f"<span class='score-diff bull'>+{diff_pct:.1f}%</span>"
            elif diff_pct < 0:
                score_diff = f"<span class='score-diff bear'>{diff_pct:.1f}%</span>"

        cards.append(
            f"""
            <article class="card" data-symbol="{symbol}">
              <div class="card-header">
                <div class="rank">#{idx}</div>
                <div class="title-area">
                    <h3>{symbol} <span>{name}</span></h3>
                    <div class="rating-row">
                        <span class="score">{adj_score if has_debate else score}</span>
                        <small>/ {decision_label}</small>
                        {f"<small>/ {candidate_status}</small>" if candidate_status else ""}
                        {f"<small>/ PM {portfolio_text}</small>" if portfolio_text and portfolio_action != "keep" else ""}
                        {adjustment_badge}
                    </div>
                    {f"<div class='score-compare'>原始 {original_score} · 调整 {adj_score} {score_diff}</div>" if has_debate and score_diff else ""}
                </div>
                <button class="expand-btn" onclick="toggleCard(this)" aria-label="展开详情" aria-expanded="false">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="6 9 12 15 18 9"/>
                    </svg>
                </button>
              </div>
              <dl class="card-details" role="list">
                <dt>策略</dt><dd>{strategies or "-"}</dd>
                <dt>参考价</dt><dd>{_fmt_num(row.get("ideal_buy"))}</dd>
                <dt>收盘</dt><dd>{_fmt_num(row.get("close"))}</dd>
                <dt>最多亏到</dt><dd>{_fmt_num(row.get("stop_loss"))}</dd>
                <dt>先看目标</dt><dd>{_fmt_num(row.get("take_profit"))}</dd>
                <dt>比例参考</dt><dd>{html.escape(row.get("position", "") or "-")}</dd>
              </dl>
              <div class="card-footer">
                <p class="reason">{reasons or "无"}</p>
                <p class="risk">风险: {risks or "无明显风险标签"}</p>
                {f"<p class='card-note blocker'>阻塞: {candidate_blocker}</p>" if candidate_blocker else ""}
                {f"<p class='card-note next-step'>下一步: {candidate_next_step}</p>" if candidate_next_step else ""}
                {f"<p class='card-note review'>复核: {candidate_review_meta}</p>" if candidate_review_meta else ""}
                {debate_btn}
              </div>
            </article>
            """
        )
    return "\n".join(cards)


def _debate_modals(debate_map: dict[str, dict[str, Any]]) -> str:
    """生成辩论详情模态框HTML"""
    if not debate_map:
        return ""
    modals: list[str] = []
    for symbol, debate in debate_map.items():
        name = html.escape(debate.get("name", symbol))
        consensus = html.escape(debate.get("final_consensus", ""))
        adjustment = debate.get("recommended_adjustment", "keep")
        adj_text = {
            "raise": "辩论倾向上调",
            "lower": "辩论倾向下调",
            "keep": "辩论倾向维持",
        }.get(adjustment, "辩论倾向维持")
        adj_class = {"raise": "bull", "lower": "bear", "keep": "neutral"}.get(
            adjustment, "neutral"
        )

        final_vote = debate.get("final_vote", {})
        bull_count = sum(1 for v in final_vote.values() if v == "bullish")
        bear_count = sum(1 for v in final_vote.values() if v == "bearish")
        vote_html = (
            f"""
        <div class="vote-summary">
            <span class="bull-vote">看多 {bull_count}</span>
            <span class="neutral-vote">中性 {len(final_vote) - bull_count - bear_count}</span>
            <span class="bear-vote">看空 {bear_count}</span>
        </div>
        """
            if final_vote
            else ""
        )

        latest_round = debate.get("rounds", [])[-1] if debate.get("rounds") else {}
        round_num = latest_round.get("round_num", 0)
        summary = html.escape(latest_round.get("summary", ""))
        opinions_html = ""
        for opinion in latest_round.get("opinions", []):
            role = opinion.get("role", "")
            role_name = html.escape(_role_display_name(str(role)))
            stance = opinion.get("stance", "neutral")
            stance_icon = {"bullish": "🐂", "bearish": "🐻", "neutral": "⚖️"}.get(
                stance, ""
            )
            confidence = opinion.get("confidence", 0) * 100
            arguments = opinion.get("arguments", [])
            counterarguments = opinion.get("counterarguments", [])
            risk_factors = opinion.get("risk_factors", [])
            opportunity_factors = opinion.get("opportunity_factors", [])
            bullets: list[str] = []
            for argument in arguments[:2]:
                bullets.append(
                    f"<li><span class='point-label'>论点</span>{html.escape(argument)}</li>"
                )
            for opportunity in opportunity_factors[:1]:
                bullets.append(
                    "<li class='opp-item'><span class='point-label'>机会</span>"
                    f"✅ {html.escape(opportunity)}</li>"
                )
            for risk in risk_factors[:1]:
                bullets.append(
                    "<li class='risk-item'><span class='point-label'>风险</span>"
                    f"⚠️ {html.escape(risk)}</li>"
                )
            for counterargument in counterarguments[:1]:
                bullets.append(
                    "<li><span class='point-label'>反驳</span>"
                    f"{html.escape(counterargument)}</li>"
                )
            bullet_html = "".join(bullets)

            opinions_html += f"""
                <div class="opinion-card {stance}">
                    <div class="opinion-header">
                        <span class="agent-role">{role_name}</span>
                        <span class="stance-badge">{stance_icon} {stance}</span>
                        <span class="confidence-bar">
                            <span class="confidence-fill" style="width: {confidence:.0f}%"></span>
                        </span>
                        <span class="confidence-text">{confidence:.0f}%</span>
                    </div>
                    <div class="opinion-body">
                        {"<ul class='opinion-points'>" + bullet_html + "</ul>" if bullet_html else "<p class='muted'>暂无观点细节。</p>"}
                    </div>
                </div>
                """
        rounds_html = f"""
        <div class="debate-round">
            <h4>最终一轮观点</h4>
            <p class='round-summary'>仅保留最终一轮，避免重复堆叠。{f"第 {round_num} 轮摘要：{summary}" if summary else ""}</p>
            <div class="opinions-grid">{opinions_html or "<p class='muted'>暂无最终观点明细。</p>"}</div>
        </div>
        """

        risk_warnings = debate.get("risk_warnings", [])
        risk_warnings_html = (
            "".join(f"<li>⚠️ {html.escape(r)}</li>" for r in risk_warnings)
            if risk_warnings
            else ""
        )

        opportunity_highlights = debate.get("opportunity_highlights", [])
        opportunity_highlights_html = (
            "".join(f"<li>✅ {html.escape(o)}</li>" for o in opportunity_highlights)
            if opportunity_highlights
            else ""
        )

        original_score = debate.get("original_score", 0)
        adjusted_score = debate.get("adjusted_score", original_score)
        adjustment_weight = debate.get("adjustment_weight", 0)
        disagreement_score = debate.get("disagreement_score", 0)
        adjustment_class = (
            "pos" if adjustment_weight > 0 else "neg" if adjustment_weight < 0 else ""
        )

        score_breakdown = f"""
        <div class="score-breakdown">
            <h3>📈 评分分解</h3>
            <div class="score-comparison">
                <div class="score-item original">
                    <span class="score-label">原始评分</span>
                    <span class="score-value">{original_score:.1f}</span>
                </div>
                <div class="score-arrow">→</div>
                <div class="score-item adjusted">
                    <span class="score-label">调整后评分</span>
                    <span class="score-value">{"+" if adjustment_weight > 0 else ""}{adjusted_score:.1f}</span>
                </div>
                <div class="score-item adjustment">
                    <span class="score-label">调整幅度</span>
                    <span class="score-value {adjustment_class}">{adjustment_weight * 100:+.1f}%</span>
                </div>
            </div>
            <div class="meta-info">
                <span>分歧程度: {disagreement_score:.0%}</span>
                <span>阈值版本: {html.escape(debate.get("thresholds_version", "N/A"))}</span>
                <span>市场状态: {html.escape(debate.get("regime", "N/A"))}</span>
                <span>数据源: {html.escape(debate.get("data_source", "N/A"))}</span>
            </div>
        </div>
        """

        modals.append(f"""
        <div id="debate-{symbol}" class="debate-modal" role="dialog" aria-labelledby="debate-title-{symbol}" aria-hidden="true">
            <div class="debate-modal-content" role="document">
                <div class="debate-modal-header">
                    <div>
                        <h2 id="debate-title-{symbol}">多Agent讨论摘要</h2>
                        <p class="debate-subtitle">{symbol} {name}</p>
                    </div>
                    <div class="header-badges">
                        <span class="adjustment-badge {adj_class}">{adj_text}</span>
                        <button class="copy-btn" onclick="copyDebate('{symbol}')" aria-label="复制辩论详情" title="复制辩论详情">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                            </svg>
                        </button>
                        <button class="close-btn" onclick="closeDebate('{symbol}')" aria-label="关闭">&times;</button>
                    </div>
                </div>
                <div class="debate-modal-body">
                    {score_breakdown}
                    <div class="consensus-section">
                        <h3>📊 最终共识</h3>
                        <p>{consensus}</p>
                    </div>
                    {vote_html}
                    <div class="rounds-section">
                        {rounds_html}
                    </div>
                    {"<div class='warnings-section'><h3>⚠️ 风险提示</h3><ul>" + risk_warnings_html + "</ul></div>" if risk_warnings_html else ""}
                    {"<div class='opportunities-section'><h3>✅ 机会亮点</h3><ul>" + opportunity_highlights_html + "</ul></div>" if opportunity_highlights_html else ""}
                </div>
            </div>
        </div>
        """)
    return "\n".join(modals)


def _recent_rows(rows: list[dict[str, Any]]) -> str:
    recent = list(reversed(rows[-12:]))
    if not recent:
        return "<tr><td colspan='5'>还没有信号记录。先等下一次主链跑批完成，再回来回看。</td></tr>"
    out = []
    for row in recent:
        out.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('signal_date', '')))}</td>"
            f"<td>{html.escape(str(row.get('symbol', '')))}</td>"
            f"<td>{_fmt_num(row.get('score'))}</td>"
            f"<td>{html.escape(str(row.get('status', '')))}</td>"
            f"<td>{_fmt_return(row.get('return_pct'))}</td>"
            "</tr>"
        )
    return "\n".join(out)


def _paper_rows(rows: list[dict[str, Any]]) -> str:
    recent = list(reversed(rows[-10:]))
    if not recent:
        return "<tr><td colspan='6'>还没有纸面跟踪记录。出现候选后，系统才会记录入场、阻塞或退出。</td></tr>"
    out = []
    for row in recent:
        reason = (
            row.get("not_executable_reason")
            or row.get("exit_reason")
            or (
                f"等待 {row.get('signal_date', '')} 次日开盘"
                if row.get("status") == "pending_entry"
                else ""
            )
        )
        out.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('symbol', '')))}</td>"
            f"<td>{html.escape(str(row.get('status', '')))}</td>"
            f"<td>{html.escape(str(row.get('entry_date', '')))}</td>"
            f"<td>{_fmt_num(row.get('entry_price'))}</td>"
            f"<td>{_fmt_return(row.get('return_pct'))}</td>"
            f"<td>{html.escape(str(reason))}</td>"
            "</tr>"
        )
    return "\n".join(out)


def _fold_panel(
    title: str,
    subtitle: str,
    body_html: str,
    *,
    badge: str = "",
    open_by_default: bool = False,
) -> str:
    open_attr = " open" if open_by_default else ""
    badge_html = (
        f"<span class='state-pill neutral'>{html.escape(badge)}</span>" if badge else ""
    )
    return f"""
    <section class="panel source-panel compact-panel">
      <details class="panel-fold"{open_attr}>
        <summary class="fold-summary">
          <div>
            <h2>{html.escape(title)}</h2>
            <p class="muted">{html.escape(subtitle)}</p>
          </div>
          <div class="fold-meta">
            {badge_html}
            <span class="fold-caret" aria-hidden="true">展开</span>
          </div>
        </summary>
        <div class="fold-body">
          {body_html}
        </div>
      </details>
    </section>
    """


def _source_runtime_panel(stats: LedgerStats) -> str:
    if not stats.requested_source and not stats.actual_source:
        return """
        <section class="panel source-panel">
          <h2>数据情况</h2>
          <p class="muted">还没有最近一次运行的数据状态。先确认宝塔任务是否已跑过 daily 或 intraday。</p>
        </section>
        """

    source_route = format_source_route(stats.requested_source, stats.actual_source)
    label = html.escape(source_health_label(stats.source_health_label or "unknown"))
    tone = {
        "healthy": "healthy",
        "fallback": "fallback",
        "degraded": "degraded",
        "cold_start": "cold",
    }.get(stats.source_health_label, "neutral")
    message = html.escape(
        present_source_health(
            stats.source_health_label or "unknown",
            stats.source_health_message or "",
        )
    )
    layers = html.escape(
        describe_source_layers(
            stats.source_freshness_tier or "unknown",
            stats.source_coverage_tier or "unknown",
        )
    )
    fallback_text = "是" if stats.fallback_used else "否"
    return f"""
    <section class="panel source-panel">
      <div class="source-head">
        <div>
          <h2>数据情况</h2>
          <p class="muted">最近一次运行的数据来源、完整度和备用源情况。</p>
        </div>
        <span class="state-pill {tone}">{label}</span>
      </div>
      <dl class="source-grid">
        <dt>数据来源</dt><dd>{html.escape(source_route)}</dd>
        <dt>通知级别</dt><dd>{html.escape(stats.notify_level or "info")}</dd>
        <dt>数据完整度</dt><dd>{layers}</dd>
        <dt>备用源</dt><dd>{fallback_text}</dd>
      </dl>
      <p class="source-message">{message}</p>
    </section>
    """


def _source_warning(stats: LedgerStats) -> tuple[str, str] | None:
    if stats.source_health_label == "fallback":
        return (
            "warning fallback-warning",
            "本次候选由 fallback 数据源生成，主源未直接命中。请先人工复核报价、成交额和板块状态。",
        )
    if stats.source_health_label == "degraded":
        return (
            "warning degraded-warning",
            "本次数据源处于降级状态，最近失败偏多。不要把这次结果当成正常质量样本。",
        )
    if stats.source_health_label == "cold_start":
        return (
            "warning cold-warning",
            "本次数据源仍处于冷启动观察期，缺少足够健康历史。建议只做参考，不要重仓。",
        )
    return None


def _read_gate_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _gate_status_for_display(gate: dict[str, Any]) -> tuple[bool, str]:
    validation = validate_walkforward_gate_payload(gate, today=now_shanghai().date())
    if validation.ok:
        return True, _gate_display_metrics(validation)
    if validation.dsr is None or validation.pbo is None or validation.n_periods is None:
        return False, "missing/invalid metrics"
    return False, (
        f"{_gate_display_metrics(validation)}; "
        f"阻塞: {', '.join(_gate_display_blockers(validation.blockers))}"
    )


def _gate_display_metrics(validation: Any) -> str:
    return (
        f"DSR={validation.dsr:.4f}, "
        f"PBO={validation.pbo:.2%}, "
        f"periods={validation.n_periods}"
    )


def _gate_display_blockers(blockers: tuple[str, ...]) -> list[str]:
    labels: list[str] = []
    for blocker in blockers:
        if blocker.startswith("both_pass"):
            labels.append("both_pass")
        elif blocker.startswith("DSR") or blocker.startswith("dsr_pass"):
            labels.append("DSR")
        elif blocker.startswith("pbo_valid"):
            labels.append("PBO占位")
        elif blocker.startswith("PBO") or blocker.startswith("pbo_pass"):
            labels.append("PBO")
        elif blocker.startswith("n_periods"):
            labels.append("periods")
        else:
            labels.append(blocker)
    return labels


def _read_risk_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _system_health_panel(
    rows: list[dict[str, Any]],
    candidates: list[dict[str, str]],
    debate_map: dict[str, dict[str, Any]],
    source_health_path: str | Path | None = None,
) -> str:
    today = now_shanghai().date()
    today_str = today.isoformat()

    last_signal_date = ""
    for row in reversed(rows):
        d = str(row.get("signal_date", ""))
        if d:
            last_signal_date = d
            break

    freshness_cls = "health-red"
    freshness_label = "无数据"
    if last_signal_date:
        try:
            last_dt = datetime.strptime(last_signal_date[:10], "%Y-%m-%d").date()
            delta = (today - last_dt).days
            if delta <= 0:
                freshness_cls = "health-green"
                freshness_label = "今日"
            elif delta == 1:
                freshness_cls = "health-yellow"
                freshness_label = "昨日"
            else:
                freshness_cls = "health-red"
                freshness_label = f"{delta}天前"
        except ValueError:
            freshness_cls = "health-red"
            freshness_label = "日期异常"

    today_candidates = [c for c in candidates if c.get("date", "") == today_str]
    picks_today = len(today_candidates)
    high_conf = sum(
        1
        for c in today_candidates
        if _safe_float(c.get("score")) and _safe_float(c.get("score")) >= 65
    )
    med_conf = sum(
        1
        for c in today_candidates
        if _safe_float(c.get("score")) and 50 <= _safe_float(c.get("score")) < 65
    )
    low_conf = picks_today - high_conf - med_conf
    picks_cls = "health-green" if picks_today > 0 else "health-yellow"
    picks_value = f"{picks_today} 只"

    decay_alerts: list[str] = []
    try:
        ledger_df = pd.DataFrame(rows)
        if not ledger_df.empty:
            from aqsp.ledger.learner import StrategyDecayDetector

            detector = StrategyDecayDetector()
            alerts = detector.detect(ledger_df)
            for alert in alerts:
                icon = (
                    "🔴"
                    if alert.severity == "critical"
                    else "🟡"
                    if alert.severity == "warning"
                    else "🔵"
                )
                decay_alerts.append(
                    f"{icon} {alert.strategy_name} ({alert.recent_win_rate:.0%})"
                )
    except Exception:
        pass
    decay_cls = (
        "health-red"
        if any("🔴" in a for a in decay_alerts)
        else "health-yellow"
        if decay_alerts
        else "health-green"
    )
    decay_value = f"{len(decay_alerts)} 个告警" if decay_alerts else "正常"

    regime_name = "未知"
    regime_desc = "数据不足"
    regime_cls = "health-yellow"
    try:
        for row in reversed(rows):
            r = str(row.get("regime_at_signal", "") or "")
            if r:
                regime_name = r
                regime_desc = _REGIME_DESC_SHORT.get(r, r)
                regime_cls = "health-green"
                if "bear" in r or "熊" in r:
                    regime_cls = "health-red"
                elif "sideways" in r or "震荡" in r:
                    regime_cls = "health-yellow"
                break
    except Exception:
        pass

    debate_dates: list[str] = []
    consensus_dist: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0}
    for debate in debate_map.values():
        dd = str(debate.get("debate_date", ""))
        if dd:
            debate_dates.append(dd)
        consensus = str(debate.get("final_consensus", "neutral"))
        if consensus in consensus_dist:
            consensus_dist[consensus] += 1
        else:
            consensus_dist["neutral"] += 1
    debates_today = sum(1 for d in debate_dates if d == today_str)
    debate_cls = "health-green" if debates_today > 0 else "health-yellow"
    debate_value = f"{debates_today} 场"

    source_label = "未知"
    source_cls = "health-yellow"
    try:
        health = read_source_health(source_health_path)
        requested = health.get("last_requested_source", "")
        actual = health.get("last_actual_source", "")
        label, _, _ = describe_source_health(requested, actual, path=source_health_path)
        source_label = {
            "healthy": "健康",
            "fallback": "降级",
            "degraded": "异常",
            "cold_start": "冷启动",
        }.get(label, label)
        source_cls = {
            "healthy": "health-green",
            "fallback": "health-yellow",
            "degraded": "health-red",
            "cold_start": "health-yellow",
        }.get(label, "health-yellow")
    except Exception:
        pass

    breaker_triggered = False
    breaker_reason = ""
    breaker_cls = "health-green"
    try:
        risk_state = _read_risk_state(Path("data/risk_state.json"))
        cooldown = risk_state.get("cooldown_until")
        if cooldown:
            try:
                cooldown_date = datetime.strptime(cooldown, "%Y-%m-%d").date()
                if today < cooldown_date:
                    breaker_triggered = True
                    breaker_reason = f"冷却至 {cooldown}"
                    breaker_cls = "health-red"
            except ValueError:
                pass
    except Exception:
        pass
    breaker_value = "已触发" if breaker_triggered else "正常"

    gate_path = Path("data/walkforward_gate.json")
    gate = _read_gate_status(gate_path)
    gate_pass, gate_detail = _gate_status_for_display(gate)
    gate_cls = "health-green" if gate_pass else "health-red"
    gate_value = "通过" if gate_pass else "未通过"

    cards = [
        _health_card(
            "📊", "数据状态", last_signal_date or "无", freshness_label, freshness_cls
        ),
        _health_card(
            "🎯",
            "选股状态",
            picks_value,
            f"高{high_conf}/中{med_conf}/低{low_conf}",
            picks_cls,
        ),
        _health_card(
            "📉",
            "策略健康",
            decay_value,
            "\n".join(decay_alerts[:3]) if decay_alerts else "无衰减策略",
            decay_cls,
        ),
        _health_card("📈", "市场状态", regime_desc, regime_name, regime_cls),
        _health_card(
            "🤖",
            "辩论状态",
            debate_value,
            f"多{consensus_dist['bullish']}/空{consensus_dist['bearish']}/中{consensus_dist['neutral']}",
            debate_cls,
        ),
        _health_card("🔌", "数据源", source_label, "", source_cls),
        _health_card("🛡️", "组合保护", breaker_value, breaker_reason, breaker_cls),
        _health_card(
            "✅",
            "双门验证",
            gate_value,
            gate_detail,
            gate_cls,
        ),
    ]

    return f"""
    <section class="panel health-overview-panel">
      <h2>系统健康概览</h2>
      <div class="health-grid">
        {"".join(cards)}
      </div>
    </section>
    """


_REGIME_DESC_SHORT: dict[str, str] = {
    "stable_bull": "平稳牛市",
    "volatile_bull": "波动牛市",
    "stable_bear": "平稳熊市",
    "volatile_bear": "波动熊市",
    "stable_sideways": "平稳震荡",
    "volatile_sideways": "波动震荡",
    "bull_trend": "牛市趋势",
    "mild_bear": "温和熊市",
    "sideways": "震荡市",
    "bear_filter": "熊市过滤",
}


def _health_card(icon: str, label: str, value: str, detail: str, cls: str) -> str:
    detail_html = ""
    if detail:
        detail_lines = html.escape(detail).split("\n")
        detail_html = "<br>".join(
            f"<span class='health-detail'>{line}</span>"
            for line in detail_lines
            if line
        )
    return f"""
    <div class="health-card {cls}">
      <div class="health-icon">{icon}</div>
      <div class="health-info">
        <div class="health-label">{html.escape(label)}</div>
        <div class="health-value">{html.escape(value)}</div>
        {detail_html}
      </div>
      <div class="health-dot"></div>
    </div>
    """


def render_source_health_panel(path: str | Path | None = None) -> str:
    health = read_source_health(path)
    updated_at = health.get("updated_at", "")
    consecutive_failures = health.get("consecutive_failures", 0)
    last_error = health.get("last_error", "")
    fallback_used = health.get("fallback_used", False)
    requested = health.get("last_requested_source", "")
    actual = health.get("last_actual_source", "")

    if not updated_at and not health.get("sources") and not health.get("plans"):
        return _fold_panel(
            "数据源健康明细",
            "累计成功率、失败次数和 fallback 路由历史。",
            "<p class='muted'>暂无数据源健康记录。</p>",
        )

    label, message, _ = describe_source_health(requested, actual, path=path)
    tone = {
        "healthy": "healthy",
        "fallback": "fallback",
        "degraded": "degraded",
        "cold_start": "cold",
    }.get(label, "neutral")

    sources = health.get("sources", {})
    source_rows: list[str] = []
    for name, stats in sorted(sources.items()):
        successes = int(stats.get("successes", 0))
        failures = int(stats.get("failures", 0))
        total = successes + failures
        rate = successes / total if total > 0 else 0
        if total > 0 and failures / total > 0.5:
            row_cls = "health-row-red"
        elif total > 0 and failures / total > 0.2:
            row_cls = "health-row-yellow"
        else:
            row_cls = ""
        source_rows.append(
            f"<tr class='{row_cls}'>"
            f"<td>{html.escape(name)}</td>"
            f"<td>{successes}</td>"
            f"<td>{failures}</td>"
            f"<td>{rate:.1%}</td>"
            f"<td>{html.escape(stats.get('last_success', '')[:19] or '-')}</td>"
            f"<td>{html.escape(stats.get('last_error', '') or '-')}</td>"
            "</tr>"
        )

    plans = health.get("plans", {})
    plan_rows: list[str] = []
    for name, stats in sorted(plans.items()):
        successes = int(stats.get("successes", 0))
        failures = int(stats.get("failures", 0))
        fb_successes = int(stats.get("fallback_successes", 0))
        total = successes + failures
        if total > 0 and failures / total > 0.5:
            row_cls = "health-row-red"
        elif total > 0 and failures / total > 0.2:
            row_cls = "health-row-yellow"
        else:
            row_cls = ""
        plan_rows.append(
            f"<tr class='{row_cls}'>"
            f"<td>{html.escape(name)}</td>"
            f"<td>{successes}</td>"
            f"<td>{failures}</td>"
            f"<td>{fb_successes}</td>"
            f"<td>{html.escape(stats.get('last_success', '')[:19] or '-')}</td>"
            f"<td>{html.escape(stats.get('last_error', '') or '-')}</td>"
            "</tr>"
        )

    fallback_text = "是" if fallback_used else "否"

    body_html = f"""
      <div class="source-head">
        <span class="state-pill {tone}">{html.escape(label)}</span>
      </div>
      <dl class="source-grid">
        <dt>更新</dt><dd>{html.escape(updated_at[:19] or "-")}</dd>
        <dt>连续失败</dt><dd>{consecutive_failures}</dd>
        <dt>fallback</dt><dd>{fallback_text}</dd>
        <dt>最后错误</dt><dd>{html.escape(last_error or "-")}</dd>
      </dl>
      <p class="source-message">{html.escape(message)}</p>
      <h3>数据源</h3>
      <table>
        <thead><tr><th>源</th><th>成功</th><th>失败</th><th>成功率</th><th>最后成功</th><th>最后错误</th></tr></thead>
        <tbody>{"".join(source_rows) or "<tr><td colspan='6'>还没有数据源记录。等下一次跑批后再看成功率。</td></tr>"}</tbody>
      </table>
      <h3>路由计划</h3>
      <table>
        <thead><tr><th>计划</th><th>成功</th><th>失败</th><th>fallback成功</th><th>最后成功</th><th>最后错误</th></tr></thead>
        <tbody>{"".join(plan_rows) or "<tr><td colspan='6'>还没有路由计划记录。等下一次自动任务取数后再看。</td></tr>"}</tbody>
      </table>
    """
    return _fold_panel(
        "数据源健康明细",
        "累计成功率、失败次数和 fallback 路由历史。",
        body_html,
        badge=label,
    )


def _research_panel(summary: ResearchSummary | None) -> str:
    if summary is None:
        return _fold_panel(
            "研究进展",
            "补充研究接入情况与后续待补项。",
            "<p class='muted'>研究进展未更新，当前页面仅展示本次运行结果。</p>",
        )
    pipeline_lines = []
    for item in summary.pipeline_summaries[:4]:
        pipeline_lines.append(
            "<tr>"
            f"<td>{html.escape(item.pipeline)}</td>"
            f"<td>{item.p1}</td>"
            f"<td>{item.total}</td>"
            f"<td>{html.escape(item.top_repo or '-')}</td>"
            "</tr>"
        )
    action_lines = []
    for item in summary.next_actions[:5]:
        action_lines.append(
            "<tr>"
            f"<td>{html.escape(item.priority)}</td>"
            f"<td>{html.escape(item.kind)}</td>"
            f"<td>{html.escape(item.item_id)}</td>"
            f"<td>{html.escape(item.blocker or '-')}</td>"
            "</tr>"
        )
    family_names = (
        "、".join(item.name for item in summary.absorbed_families[:4]) or "暂无"
    )
    body_html = f"""
      <div class="source-head">
        <span class="state-pill neutral">{html.escape(research_findings_badge(summary))}</span>
      </div>
      <dl class="source-grid">
        <dt>研究发现</dt><dd>{html.escape(research_findings_display(summary))}</dd>
        <dt>已吸收</dt><dd>{len(summary.absorbed_families)}</dd>
        <dt>已接入</dt><dd>{summary.implemented_family_count}</dd>
        <dt>只进报告</dt><dd>{summary.report_only_family_count}</dd>
        <dt>门控中</dt><dd>{summary.gated_family_count}</dd>
        <dt>主题</dt><dd>{html.escape(family_names)}</dd>
      </dl>
      <table>
        <thead><tr><th>研究管线</th><th>P1</th><th>Total</th><th>Top Repo</th></tr></thead>
        <tbody>{"".join(pipeline_lines) or "<tr><td colspan='4'>暂无研究管线摘要</td></tr>"}</tbody>
      </table>
      <table>
        <thead><tr><th>优先级</th><th>类型</th><th>对象</th><th>第一道 gate</th></tr></thead>
        <tbody>{"".join(action_lines) or "<tr><td colspan='4'>暂无接入动作</td></tr>"}</tbody>
      </table>
    """
    return _fold_panel(
        "研究进展",
        "补充研究接入情况与后续待补项。",
        body_html,
        badge=research_findings_badge(summary),
    )


def _load_kline_data(
    cache_path: Path, symbol: str, signal_date: str
) -> list[dict[str, Any]]:
    try:
        with sqlite3.connect(cache_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT date, open, high, low, close, volume "
                "FROM ohlcv WHERE symbol = ? ORDER BY date",
                (symbol,),
            ).fetchall()
    except (sqlite3.Error, OSError):
        rows = []

    if rows:
        target = datetime.strptime(signal_date[:10], "%Y-%m-%d")
        filtered = [
            {
                "date": r["date"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
            }
            for r in rows
            if r["date"]
            and r["close"] is not None
            and abs((datetime.strptime(r["date"][:10], "%Y-%m-%d") - target).days) <= 60
        ]
        if filtered:
            return filtered

    signal_close = 100.0
    entry_price = 100.0
    return [
        {
            "date": signal_date,
            "open": signal_close,
            "high": signal_close,
            "low": signal_close,
            "close": signal_close,
            "volume": 0,
        },
        {
            "date": signal_date,
            "open": entry_price,
            "high": entry_price,
            "low": entry_price,
            "close": entry_price,
            "volume": 0,
        },
    ]


def render_strategy_performance_panel(
    ledger_path: str, *, asset_path: str = "assets"
) -> str:
    rows = read_ledger_rows(Path(ledger_path))
    validated = [r for r in rows if r.get("status") == "validated"]

    strategy_data: dict[str, dict[str, Any]] = {}
    for row in validated:
        strategies = _strategy_values(row.get("strategies"))
        if not strategies:
            continue
        ret = _safe_float(
            row.get("excess_return_pct")
            if row.get("excess_return_pct") is not None
            else row.get("return_pct")
        )
        signal_date = str(row.get("signal_date", ""))
        for strategy in strategies:
            strategy = str(strategy)
            if strategy not in strategy_data:
                strategy_data[strategy] = {
                    "wins": 0,
                    "total": 0,
                    "returns": [],
                    "dates": [],
                }
            entry = strategy_data[strategy]
            entry["total"] += 1
            if ret is not None:
                entry["returns"].append(ret)
                entry["dates"].append(signal_date)
                if ret > 0:
                    entry["wins"] += 1

    total_picks = len(validated)
    overall_wins = sum(1 for r in validated if bool(r.get("win")))
    overall_win_rate = overall_wins / total_picks if total_picks > 0 else 0

    best_strategy = "N/A"
    best_avg = float("-inf")
    for name, data in strategy_data.items():
        if data["returns"]:
            avg = sum(data["returns"]) / len(data["returns"])
            if avg > best_avg:
                best_avg = avg
                best_strategy = name

    chart_data = {}
    for name, data in strategy_data.items():
        returns = data["returns"]
        dates = data["dates"]
        pairs = sorted(zip(dates, returns))
        rolling_win = []
        if len(pairs) >= 30:
            for i in range(29, len(pairs)):
                window = [r for _, r in pairs[i - 29 : i + 1]]
                rolling_win.append(
                    [pairs[i][0], round(sum(1 for r in window if r > 0) / 30 * 100, 1)]
                )
        chart_data[name] = {
            "win_rate": round(data["wins"] / data["total"] * 100, 1)
            if data["total"]
            else 0,
            "avg_return": round(sum(returns) / len(returns), 2) if returns else 0,
            "rolling_win": rolling_win,
            "total": data["total"],
        }

    if not chart_data:
        return _fold_panel(
            "策略胜率分析",
            "已验证信号的历史胜率、滚动胜率和平均收益。",
            "<p class='muted'>暂无已验证信号数据，无法生成策略胜率图表。</p>",
        )

    chart_json = json.dumps(chart_data, ensure_ascii=False)

    body_html = f"""
      <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px;">
        <div class="stat" style="flex:1;min-width:140px;"><b>{total_picks}</b><span>已验证总信号</span></div>
        <div class="stat" style="flex:1;min-width:140px;"><b>{overall_win_rate:.1%}</b><span>整体胜率</span></div>
        <div class="stat" style="flex:1;min-width:140px;"><b>{html.escape(best_strategy)}</b><span>最佳策略</span></div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:16px;">
        <div id="chart-winrate" style="height:320px;"></div>
        <div id="chart-rolling-win" style="height:320px;"></div>
        <div id="chart-avg-return" style="height:320px;"></div>
      </div>
    """
    return (
        _fold_panel(
            "策略胜率分析",
            "已验证信号的历史胜率、滚动胜率和平均收益。",
            body_html,
            badge=f"{len(chart_data)} strategies",
        )
        + f"""
    <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
    <script>
      (function() {{
        const data = {chart_json};
        const names = Object.keys(data).sort();
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        const textColor = isDark ? '#e8e6e1' : '#162018';
        const gridStyle = {{ left:'8%',right:'4%',top:'18%',bottom:'14%' }};

        const wrChart = echarts.init(document.getElementById('chart-winrate'));
        wrChart.setOption({{
          title:{{ text:'策略历史胜率',textStyle:{{ color:textColor,fontSize:14 }} }},
          tooltip:{{ trigger:'axis' }},
          grid:gridStyle,
          xAxis:{{ type:'category',data:names,axisLabel:{{ color:textColor,rotate:20,fontSize:11 }} }},
          yAxis:{{ type:'value',axisLabel:{{ formatter:'{{value}}%',color:textColor }},splitLine:{{ lineStyle:{{ type:'dashed' }} }} }},
          series:[{{ type:'bar',data:names.map(n=>data[n].win_rate),itemStyle:{{ color:'#1f7a4d' }},
            label:{{ show:true,position:'top',formatter:'{{c}}%',fontSize:11 }} }}]
        }});

        const rollChart = echarts.init(document.getElementById('chart-rolling-win'));
        const rollSeries = [];
        const colors = ['#1f7a4d','#b86b1d','#b44836','#3b82f6','#8b5cf6','#ec4899','#14b8a6'];
        names.forEach((n,i) => {{
          const rd = data[n].rolling_win;
          if (rd.length > 0) {{
            rollSeries.push({{
              name:n, type:'line', smooth:true, symbol:'none',
              data:rd.map(d=>d[1]),
              lineStyle:{{ width:2 }},
              itemStyle:{{ color:colors[i % colors.length] }}
            }});
          }}
        }});
        const rollDates = names.reduce((acc,n) => {{
          const rd = data[n].rolling_win;
          if (rd.length > acc.length) return rd.map(d=>d[0]);
          return acc;
        }}, []);
        rollChart.setOption({{
          title:{{ text:'滚动胜率 (30日窗口)',textStyle:{{ color:textColor,fontSize:14 }} }},
          tooltip:{{ trigger:'axis' }},
          legend:{{ top:30, textStyle:{{ color:textColor }} }},
          grid:{{ left:'8%',right:'4%',top:'26%',bottom:'14%' }},
          xAxis:{{ type:'category',data:rollDates,axisLabel:{{ color:textColor,fontSize:11 }} }},
          yAxis:{{ type:'value',min:0,max:100,axisLabel:{{ formatter:'{{value}}%',color:textColor }},splitLine:{{ lineStyle:{{ type:'dashed' }} }} }},
          series:rollSeries
        }});

        const retChart = echarts.init(document.getElementById('chart-avg-return'));
        retChart.setOption({{
          title:{{ text:'策略平均收益 (%)',textStyle:{{ color:textColor,fontSize:14 }} }},
          tooltip:{{ trigger:'axis' }},
          grid:gridStyle,
          xAxis:{{ type:'category',data:names,axisLabel:{{ color:textColor,rotate:20,fontSize:11 }} }},
          yAxis:{{ type:'value',axisLabel:{{ formatter:'{{value}}%',color:textColor }},splitLine:{{ lineStyle:{{ type:'dashed' }} }} }},
          series:[{{ type:'bar',data:names.map(n=>({{
            value:data[n].avg_return,
            itemStyle:{{ color:data[n].avg_return>=0?'#1f7a4d':'#b44836' }}
          }})),
            label:{{ show:true,position:'top',formatter:p=>p.value+'%',fontSize:11 }} }}]
        }});

        window.addEventListener('resize',()=>{{ wrChart.resize();rollChart.resize();retChart.resize(); }});
      }})();
    </script>
    """
    )


def render_morning_evening_panel(
    ledger_path: str,
) -> str:
    rows = read_ledger_rows(Path(ledger_path))

    morning_signals = []
    evening_signals = []

    for row in rows:
        strategies = _strategy_values(row.get("strategies"))
        if any(
            token in strategy
            for strategy in strategies
            for token in ("morning_breakout", "morning-breakout", "早盘")
        ):
            morning_signals.append(row)
        elif any(
            token in strategy
            for strategy in strategies
            for token in ("closing_premium", "closing-premium", "尾盘")
        ):
            evening_signals.append(row)

    if not morning_signals and not evening_signals:
        return _fold_panel(
            "早盘/尾盘策略",
            "盘中轻量策略与尾盘策略的单独跟踪。",
            "<p class='muted'>暂无早盘或尾盘策略的信号数据。</p>",
        )

    def _format_signal_list(signals, label, color):
        if not signals:
            return f"""
            <div style="flex:1;min-width:300px;">
              <h3 style="margin:0 0 14px;color:{color};">{label}</h3>
              <p class="muted">暂无该类策略的信号数据</p>
            </div>
            """

        recent = signals[-10:]
        html = f"""
        <div style="flex:1;min-width:300px;">
          <h3 style="margin:0 0 14px;color:{color};">{label}</h3>
          <table style="font-size:14px;">
            <thead><tr><th>日期</th><th>代码</th><th>名称</th><th>评分</th><th>状态</th><th>收益</th></tr></thead>
            <tbody>
        """
        for s in recent:
            ret = _fmt_return(s.get("return_pct"))
            status = html.escape(str(s.get("status", "-")))
            html += f"""
            <tr>
              <td>{html.escape(str(s.get("signal_date", "-")))}</td>
              <td>{html.escape(str(s.get("symbol", "-")))}</td>
              <td>{html.escape(str(s.get("name", "-")))}</td>
              <td>{_fmt_num(s.get("score"))}</td>
              <td>{status}</td>
              <td>{ret}</td>
            </tr>
            """
        html += """
            </tbody>
          </table>
        </div>
        """
        return html

    def _stats(signals):
        validated = [s for s in signals if s.get("status") == "validated"]
        wins = sum(1 for s in validated if s.get("win"))
        total = len(validated)
        win_rate = wins / total if total > 0 else 0
        avg_return = (
            sum(
                _safe_float(s.get("return_pct"))
                for s in validated
                if s.get("return_pct")
            )
            / len(validated)
            if validated
            else 0
        )
        return f"""
        <div style="display:flex;gap:16px;margin-bottom:16px;flex-wrap:wrap;">
          <div class="stat" style="padding:14px 16px;border-radius:16px;"><b>{len(signals)}</b><span>总信号</span></div>
          <div class="stat" style="padding:14px 16px;border-radius:16px;"><b>{total}</b><span>已验证</span></div>
          <div class="stat" style="padding:14px 16px;border-radius:16px;"><b>{_fmt_pct(win_rate)}</b><span>胜率</span></div>
          <div class="stat" style="padding:14px 16px;border-radius:16px;"><b>{_fmt_num(avg_return)}</b><span>平均收益</span></div>
        </div>
        """

    body_html = f"""
      <div style="display:flex;gap:16px;flex-wrap:wrap;">
        {_stats(morning_signals)}
      </div>
      <div style="display:flex;gap:16px;flex-wrap:wrap;">
        {_format_signal_list(morning_signals, "早盘打板", "var(--amber)")}
        {_format_signal_list(evening_signals, "尾盘溢价", "var(--green)")}
      </div>
    """
    return _fold_panel(
        "早盘/尾盘策略",
        "盘中轻量策略与尾盘策略的单独跟踪。",
        body_html,
        badge="双策略并行",
    )


def render_kline_panel(
    ledger_path: str, *, asset_path: str = "assets", max_stocks: int = 5
) -> str:
    rows = read_ledger_rows(Path(ledger_path))
    if not rows:
        return _fold_panel(
            "K线图",
            "最新候选的价格、均线和成交量快照。",
            "<p class='muted'>暂无信号数据，无法生成K线图。</p>",
        )

    latest_date = ""
    for row in reversed(rows):
        d = str(row.get("signal_date", ""))
        if d:
            latest_date = d
            break
    if not latest_date:
        return _fold_panel(
            "K线图",
            "最新候选的价格、均线和成交量快照。",
            "<p class='muted'>暂无有效信号日期。</p>",
        )

    latest_rows = sorted(
        [r for r in rows if str(r.get("signal_date", "")) == latest_date],
        key=lambda r: float(r.get("score") or 0),
        reverse=True,
    )[:max_stocks]

    if not latest_rows:
        return _fold_panel(
            "K线图",
            "最新候选的价格、均线和成交量快照。",
            "<p class='muted'>最新信号日无候选股数据。</p>",
        )

    cache_path = Path("data/cache.db")
    stocks: list[dict[str, Any]] = []
    for row in latest_rows:
        symbol = str(row.get("symbol", ""))
        if not symbol:
            continue
        name = str(row.get("name", symbol))
        score = _safe_float(row.get("score"))
        signal_close = _safe_float(row.get("signal_close"))
        entry_price = _safe_float(row.get("entry_price"))
        stop_loss = _safe_float(row.get("stop_loss"))
        take_profit = _safe_float(row.get("take_profit"))
        ohlcv = _load_kline_data(cache_path, symbol, latest_date)
        stocks.append(
            {
                "symbol": symbol,
                "name": name,
                "score": score,
                "signal_close": signal_close,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "signal_date": latest_date,
                "ohlcv": ohlcv,
            }
        )

    if not stocks:
        return _fold_panel(
            "K线图",
            "最新候选的价格、均线和成交量快照。",
            "<p class='muted'>无法加载K线数据。</p>",
        )

    stocks_json = json.dumps(stocks, ensure_ascii=False)

    tab_buttons: list[str] = []
    for i, s in enumerate(stocks):
        bg = "rgba(31,122,77,.15)" if i == 0 else "var(--card)"
        clr = "var(--green)" if i == 0 else "var(--muted)"
        tab_buttons.append(
            f'<button class="kline-tab" data-idx="{i}" '
            f'style="padding:8px 16px;border:1px solid var(--line);border-radius:12px;'
            f'background:{bg};color:{clr};cursor:pointer;font-weight:600;transition:all .2s;">'
            f"{html.escape(s['name'])} ({html.escape(s['symbol'])}) "
            f"<small>评分:{_fmt_num(s['score'])}</small></button>"
        )
    chart_divs: list[str] = []
    for i in range(len(stocks)):
        disp = "block" if i == 0 else "none"
        chart_divs.append(
            f'<div class="kline-chart" id="kline-{i}" style="height:520px;display:{disp};">'
            f'<div id="kline-main-{i}" style="height:380px;"></div>'
            f'<div id="kline-vol-{i}" style="height:140px;border-top:1px solid var(--line);"></div>'
            f"</div>"
        )

    body_html = f"""
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <h3 style="margin:0;">信号日 {html.escape(latest_date)}</h3>
        <div id="kline-legend" style="display:flex;gap:16px;font-size:13px;color:var(--muted);">
          <span><span style="display:inline-block;width:20px;height:3px;background:#3b82f6;vertical-align:middle;margin-right:4px;"></span>MA5</span>
          <span><span style="display:inline-block;width:20px;height:3px;background:#b86b1d;vertical-align:middle;margin-right:4px;"></span>MA10</span>
          <span><span style="display:inline-block;width:20px;height:3px;background:#8b5cf6;vertical-align:middle;margin-right:4px;"></span>MA20</span>
        </div>
      </div>
      <div class="kline-tabs" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
        {"".join(tab_buttons)}
      </div>
      {"".join(chart_divs)}
    """
    return (
        _fold_panel(
            "K线图",
            "最新候选的价格、均线和成交量快照。",
            body_html,
            badge=f"{len(stocks)} symbols",
        )
        + f"""
    <script src="https://unpkg.com/lightweight-charts@4/dist/lightweight-charts.standalone.production.js"></script>
    <script>
      (function() {{
        const stocks = {stocks_json};
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        const charts = [];

        function getThemeColors() {{
          const dark = document.documentElement.getAttribute('data-theme') === 'dark';
          return {{
            bg: dark ? '#1a1a18' : '#ffffff',
            text: dark ? '#e8e6e1' : '#162018',
            grid: dark ? 'rgba(255,255,255,.06)' : 'rgba(0,0,0,.06)',
            upColor: '#1f7a4d',
            downColor: '#b44836',
            crosshair: dark ? 'rgba(255,255,255,.3)' : 'rgba(0,0,0,.3)',
          }};
        }}

        function calcMA(ohlcv, period) {{
          const result = [];
          for (let i = 0; i < ohlcv.length; i++) {{
            if (i < period - 1) continue;
            let sum = 0;
            for (let j = 0; j < period; j++) sum += ohlcv[i - j].close;
            result.push({{ time: ohlcv[i].date, value: sum / period }});
          }}
          return result;
        }}

        function createStockChart(stock, idx) {{
          const mainEl = document.getElementById('kline-main-' + idx);
          const volEl = document.getElementById('kline-vol-' + idx);
          const colors = getThemeColors();
          const ohlcv = stock.ohlcv;

          if (!ohlcv || ohlcv.length < 2) {{
            mainEl.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);">数据不足</div>';
            return null;
          }}

          const chart = LightweightCharts.createChart(mainEl, {{
            layout: {{
              background: {{ type: 'solid', color: colors.bg }},
              textColor: colors.text,
              fontSize: 12,
            }},
            grid: {{
              vertLines: {{ color: colors.grid }},
              horzLines: {{ color: colors.grid }},
            }},
            crosshair: {{
              mode: LightweightCharts.CrosshairMode.Normal,
              vertLine: {{ color: colors.crosshair, width: 1, style: 2, labelBackgroundColor: colors.bg }},
              horzLine: {{ color: colors.crosshair, width: 1, style: 2, labelBackgroundColor: colors.bg }},
            }},
            rightPriceScale: {{
              borderColor: colors.grid,
              scaleMargins: {{ top: 0.08, bottom: 0.08 }},
            }},
            timeScale: {{
              borderColor: colors.grid,
              timeVisible: false,
              rightOffset: 5,
              barSpacing: 8,
            }},
            handleScroll: true,
            handleScale: true,
          }});

          const candleSeries = chart.addCandlestickSeries({{
            upColor: colors.upColor,
            downColor: colors.downColor,
            borderUpColor: colors.upColor,
            borderDownColor: colors.downColor,
            wickUpColor: colors.upColor,
            wickDownColor: colors.downColor,
          }});

          const candleData = ohlcv.map(d => ({{
            time: d.date,
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
          }}));
          candleSeries.setData(candleData);

          const ma5 = calcMA(ohlcv, 5);
          const ma10 = calcMA(ohlcv, 10);
          const ma20 = calcMA(ohlcv, 20);

          const ma5Series = chart.addLineSeries({{
            color: '#3b82f6',
            lineWidth: 2,
            crosshairMarkerVisible: false,
            priceLineVisible: false,
            lastValueVisible: false,
          }});
          ma5Series.setData(ma5);

          const ma10Series = chart.addLineSeries({{
            color: '#b86b1d',
            lineWidth: 2,
            crosshairMarkerVisible: false,
            priceLineVisible: false,
            lastValueVisible: false,
          }});
          ma10Series.setData(ma10);

          const ma20Series = chart.addLineSeries({{
            color: '#8b5cf6',
            lineWidth: 2,
            crosshairMarkerVisible: false,
            priceLineVisible: false,
            lastValueVisible: false,
          }});
          ma20Series.setData(ma20);

          const signalIdx = ohlcv.findIndex(d => d.date === stock.signal_date);
          if (signalIdx >= 0) {{
            const markers = [{{
              time: ohlcv[signalIdx].date,
              position: 'belowBar',
              color: '#b86b1d',
              shape: 'arrowUp',
              text: '信号',
              size: 2,
            }}];
            candleSeries.setMarkers(markers);

            if (stock.entry_price) {{
              candleSeries.createPriceLine({{
                price: stock.entry_price,
                color: '#3b82f6',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: '参考价',
              }});
            }}
            if (stock.stop_loss) {{
              candleSeries.createPriceLine({{
                price: stock.stop_loss,
                color: '#b44836',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: '最多亏到',
              }});
            }}
            if (stock.take_profit) {{
              candleSeries.createPriceLine({{
                price: stock.take_profit,
                color: '#1f7a4d',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: '先看目标',
              }});
            }}
          }}

          const volChart = LightweightCharts.createChart(volEl, {{
            layout: {{
              background: {{ type: 'solid', color: colors.bg }},
              textColor: colors.text,
              fontSize: 11,
            }},
            grid: {{
              vertLines: {{ color: colors.grid }},
              horzLines: {{ color: colors.grid }},
            }},
            rightPriceScale: {{
              borderColor: colors.grid,
              scaleMargins: {{ top: 0.1, bottom: 0 }},
            }},
            timeScale: {{
              borderColor: colors.grid,
              timeVisible: false,
              rightOffset: 5,
              barSpacing: 8,
              visible: true,
            }},
            crosshair: {{
              mode: LightweightCharts.CrosshairMode.Normal,
              vertLine: {{ color: colors.crosshair, width: 1, style: 2, labelVisible: false }},
              horzLine: {{ visible: false }},
            }},
            handleScroll: true,
            handleScale: true,
          }});

          const volSeries = volChart.addHistogramSeries({{
            priceFormat: {{ type: 'volume' }},
            priceScaleId: 'right',
          }});
          const volData = ohlcv.map(d => ({{
            time: d.date,
            value: d.volume,
            color: d.close >= d.open ? 'rgba(31,122,77,.5)' : 'rgba(180,72,54,.5)',
          }}));
          volSeries.setData(volData);

          chart.timeScale().subscribeVisibleLogicalRangeChange(range => {{
            if (range) volChart.timeScale().setVisibleLogicalRange(range);
          }});
          volChart.timeScale().subscribeVisibleLogicalRangeChange(range => {{
            if (range) chart.timeScale().setVisibleLogicalRange(range);
          }});

          chart.timeScale().fitContent();

          return {{ chart, volChart, candleSeries }};
        }}

        stocks.forEach((stock, idx) => {{
          const result = createStockChart(stock, idx);
          if (result) charts.push(result);
        }});

        document.querySelectorAll('.kline-tab').forEach(btn => {{
          btn.addEventListener('click', function() {{
            const idx = parseInt(this.dataset.idx);
            document.querySelectorAll('.kline-chart').forEach((el, i) => {{
              el.style.display = i === idx ? 'block' : 'none';
            }});
            document.querySelectorAll('.kline-tab').forEach(b => {{
              b.style.background = b === this ? 'rgba(31,122,77,.15)' : 'var(--card)';
              b.style.color = b === this ? 'var(--green)' : 'var(--muted)';
            }});
            if (charts[idx]) {{
              charts[idx].chart.timeScale().fitContent();
            }}
          }});
        }});

        window.addEventListener('resize', () => {{
          charts.forEach(c => {{
            if (c) {{
              c.chart.timeScale().fitContent();
              c.volChart.timeScale().fitContent();
            }}
          }});
        }});
      }})();
    </script>
    """
    )


def render_dashboard(
    candidates: list[dict[str, str]],
    rows: list[dict[str, Any]],
    title: str,
    paper_rows: list[dict[str, Any]] | None = None,
    research_summary: ResearchSummary | None = None,
    debate_map: dict[str, dict[str, Any]] | None = None,
    source_health_path: str | Path | None = None,
) -> str:
    debate_map = debate_map or {}
    candidate_symbols = {
        str(candidate.get("symbol", "") or "").strip()
        for candidate in candidates
        if str(candidate.get("symbol", "") or "").strip()
    }
    visible_debate_map = (
        {
            symbol: debate
            for symbol, debate in debate_map.items()
            if str(symbol).strip() in candidate_symbols
        }
        if candidate_symbols
        else debate_map
    )
    stats = summarize_ledger(rows)
    paper = summarize_paper(paper_rows or [])
    generated_at = now_shanghai().isoformat(timespec="seconds")
    today = now_shanghai().date().isoformat()
    candidate_date = latest_candidate_date(candidates)
    safe_title = html.escape(title)
    latest_date = html.escape(stats.latest_signal_date or "暂无")
    display_date = html.escape(candidate_date or "暂无")
    thresholds_version = html.escape(stats.thresholds_version or "未知")
    notify_level = html.escape(stats.notify_level or "info")
    source_health_label = html.escape(stats.source_health_label or "unknown")
    warnings: list[tuple[str, str]] = []
    if not candidates:
        warnings.append(
            (
                "warning",
                "本次没有候选股。先确认宝塔 daily/intraday 是否成功跑完；如果刚开盘或数据源降级，这是正常空档，不要硬找方向。",
            )
        )
    elif candidate_date and candidate_date != today:
        warnings.append(
            (
                "warning",
                f"当前候选数据日期为 {html.escape(candidate_date)}, "
                f"不是今天 {html.escape(today)}。不要把这个页面当作今日复核结论。",
            )
        )
    source_warning = _source_warning(stats)
    if source_warning is not None:
        warnings.append(source_warning)
    warning_html = "\n".join(
        f"<section class='{css_class}'>{message}</section>"
        for css_class, message in warnings
    )

    debate_modals_html = _debate_modals(visible_debate_map)
    health_panel_html = _system_health_panel(
        rows, candidates, debate_map, source_health_path
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <link rel="icon" href="data:,">
  <style>
    :root {{
      --ink: #162018;
      --muted: #687568;
      --paper: #f7f4ec;
      --card: rgba(255, 255, 252, .90);
      --green: #1f7a4d;
      --amber: #b86b1d;
      --line: rgba(22, 32, 24, .14);
      --red: #b44836;
      --warn: #fff1c2;
      --shadow: rgba(28, 45, 31, .12);
      --bg-gradient: linear-gradient(135deg, #fbf6ea 0%, #e2ead9 48%, #f5ead4 100%);
    }}
    
    @media (prefers-color-scheme: dark) {{
      :root {{
        --ink: #e8e6e1;
        --muted: #9a978f;
        --paper: #1a1a18;
        --card: rgba(30, 30, 28, .95);
        --green: #4caf7d;
        --amber: #d4873a;
        --line: rgba(232, 230, 225, .12);
        --red: #d46b5f;
        --warn: #3d3520;
        --shadow: rgba(0, 0, 0, .3);
        --bg-gradient: linear-gradient(135deg, #1a1a18 0%, #252523 48%, #1f1f1d 100%);
      }}
    }}
    
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Iowan Old Style", "Songti SC", "Noto Serif CJK SC", serif;
      background: var(--bg-gradient);
      min-height: 100vh;
    }}
    
    .theme-toggle {{
      position: fixed;
      top: 20px;
      right: 20px;
      z-index: 100;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      width: 44px;
      height: 44px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 4px 12px var(--shadow);
      transition: all 0.3s ease;
    }}
    .theme-toggle:hover {{
      transform: scale(1.05);
    }}
    .theme-toggle svg {{ color: var(--ink); }}
    header {{ padding: 56px clamp(20px, 6vw, 80px) 26px; }}
    h1 {{ font-size: clamp(36px, 7vw, 88px); line-height: .92; margin: 0; letter-spacing: -.05em; }}
    .sub {{ color: var(--muted); font-size: 16px; margin-top: 18px; }}
    .meta {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 22px; }}
    .pill {{ border: 1px solid var(--line); border-radius: 999px; padding: 8px 12px; background: rgba(255,255,255,.42); color: var(--muted); }}
    .warning {{ margin: 0 clamp(20px, 6vw, 80px) 22px; padding: 14px 18px; border: 1px solid rgba(184,107,29,.32); background: var(--warn); border-radius: 18px; color: #7a4a12; }}
    .fallback-warning {{ background: #ffe5c2; border-color: rgba(184,107,29,.46); color: #80480f; }}
    .degraded-warning {{ background: #ffd8d1; border-color: rgba(180,72,54,.42); color: #8f3426; }}
    .cold-warning {{ background: #ece7db; border-color: rgba(22,32,24,.20); color: #535f54; }}
    .stats, .grid {{ display: grid; gap: 16px; padding: 0 clamp(20px, 6vw, 80px) 24px; }}
    .stats {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
    .stat, .card, .panel {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: 0 24px 70px rgba(28, 45, 31, .12);
      backdrop-filter: blur(14px);
    }}
    .stat {{ padding: 20px; }}
    .stat b {{ display: block; font-size: 30px; }}
    .stat span {{ color: var(--muted); }}
    .grid {{ grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); align-items: start; }}
    .card {{
      padding: 22px;
      position: relative;
      overflow: hidden;
      transition: transform 0.3s ease, box-shadow 0.3s ease;
    }}
    .card .card-details {{
      display: none;
    }}
    .card .card-footer {{
      display: none;
    }}
    .card.expanded .card-details {{
      display: grid;
    }}
    .card.expanded .card-footer {{
      display: block;
    }}
    .card:hover {{
      transform: translateY(-4px);
      box-shadow: 0 32px 80px rgba(28, 45, 31, .18);
    }}
    .card-header {{
      display: flex;
      align-items: flex-start;
      gap: 14px;
      margin-bottom: 16px;
    }}
    .rank {{ color: var(--amber); font-weight: 800; font-size: 20px; min-width: 32px; }}
    .title-area {{ flex: 1; }}
    h3 {{ margin: 0; font-size: 22px; line-height: 1.3; }}
    h3 span {{ color: var(--muted); font-size: 15px; font-weight: normal; }}
    .rating-row {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 6px;
    }}
    .score {{ font-size: 36px; font-weight: 700; color: var(--green); line-height: 1; }}
    .score small {{ font-size: 14px; color: var(--muted); }}
    .adjustment-badge {{
      padding: 4px 10px;
      border-radius: 12px;
      font-size: 12px;
      font-weight: 600;
    }}
    .adjustment-badge.bull {{ background: rgba(31,122,77,.15); color: var(--green); }}
    .adjustment-badge.bear {{ background: rgba(180,72,54,.15); color: var(--red); }}
    .adjustment-badge.neutral {{ background: rgba(104,117,104,.12); color: var(--muted); }}
    .score-compare {{
        font-size: 12px;
        color: var(--muted);
        margin-top: 6px;
    }}
    .score-diff {{
        font-weight: 600;
        margin-left: 4px;
        padding: 2px 6px;
        border-radius: 8px;
    }}
    .score-diff.bull {{ background: rgba(31,122,77,.15); color: var(--green); }}
    .score-diff.bear {{ background: rgba(180,72,54,.15); color: var(--red); }}
    .expand-btn {{
      background: rgba(22,32,24,.06);
      border: 1px solid var(--line);
      border-radius: 12px;
      width: 36px;
      height: 36px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.3s ease;
      color: var(--muted);
    }}
    .expand-btn:hover {{
      background: rgba(31,122,77,.12);
      color: var(--green);
    }}
    .card-details {{
      display: grid;
      grid-template-columns: 56px 1fr;
      gap: 8px;
      margin: 0;
    }}
    .card-details dt {{ color: var(--muted); }}
    .card-details dd {{ margin: 0; }}
    .card-footer {{
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
    }}
    .reason {{ line-height: 1.7; margin: 0 0 8px; }}
    .risk {{ color: #9b3f2f; margin: 0; font-size: 14px; }}
    .card-note {{
      margin: 8px 0 0;
      font-size: 14px;
      line-height: 1.6;
      color: var(--ink);
    }}
    .card-note.blocker {{ color: var(--red); }}
    .card-note.review {{ color: var(--amber); font-weight: 600; }}
    .debate-btn {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 12px;
      padding: 10px 16px;
      background: linear-gradient(135deg, rgba(31,122,77,.12), rgba(31,122,77,.08));
      border: 1px solid rgba(31,122,77,.25);
      border-radius: 14px;
      color: var(--green);
      font-weight: 600;
      cursor: pointer;
      width: 100%;
      justify-content: center;
      transition: all 0.3s ease;
      font-size: 14px;
    }}
    .debate-btn:hover {{
      background: linear-gradient(135deg, rgba(31,122,77,.18), rgba(31,122,77,.12));
      transform: translateY(-2px);
      box-shadow: 0 8px 20px rgba(31,122,77,.15);
    }}
    .debate-btn svg {{ flex-shrink: 0; }}
    .pos {{ color: var(--green); font-weight: 700; }}
    .neg {{ color: var(--red); font-weight: 700; }}
    .panel {{ margin: 0 clamp(20px, 6vw, 80px) 56px; padding: 22px; overflow-x: auto; }}
    .panel h2 {{ margin-top: 0; }}
    .muted {{ color: var(--muted); }}
    .source-panel {{ margin-bottom: 24px; overflow: hidden; }}
    .compact-panel {{ padding: 0; }}
    .panel-fold {{ border-radius: 28px; }}
    .panel-fold[open] .fold-caret {{ transform: rotate(180deg); }}
    .fold-summary {{
      list-style: none;
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: center;
      padding: 22px;
      cursor: pointer;
    }}
    .fold-summary::-webkit-details-marker {{ display: none; }}
    .fold-summary h2 {{ margin: 0; }}
    .fold-summary p {{ margin: 6px 0 0; }}
    .fold-meta {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-shrink: 0;
    }}
    .fold-caret {{
      color: var(--muted);
      font-size: 13px;
      transition: transform .2s ease;
    }}
    .fold-body {{
      padding: 0 22px 22px;
      border-top: 1px solid var(--line);
    }}
    .source-head {{ display: flex; justify-content: space-between; gap: 18px; align-items: center; margin-bottom: 16px; }}
    .source-grid {{ display: grid; grid-template-columns: 72px 1fr 72px 1fr; gap: 10px 14px; margin: 0 0 14px; }}
    .source-grid dt {{ color: var(--muted); }}
    .source-grid dd {{ margin: 0; }}
    .state-pill {{ border-radius: 999px; padding: 8px 14px; font-weight: 700; border: 1px solid var(--line); }}
    .state-pill.healthy {{ background: rgba(31,122,77,.12); color: var(--green); }}
    .state-pill.fallback {{ background: rgba(184,107,29,.14); color: #8b5716; }}
    .state-pill.degraded {{ background: rgba(180,72,54,.14); color: var(--red); }}
    .state-pill.cold {{ background: rgba(22,32,24,.08); color: var(--muted); }}
    .state-pill.neutral {{ background: rgba(22,32,24,.06); color: var(--ink); }}
    .source-message {{ margin: 0; line-height: 1.65; }}
    .health-row-red td {{ background: rgba(180,72,54,.10); }}
    .health-row-yellow td {{ background: rgba(184,107,29,.10); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 12px; border-bottom: 1px solid var(--line); text-align: left; }}
    .empty {{ margin: 0 clamp(20px, 6vw, 80px); padding: 24px; border: 1px dashed var(--line); border-radius: 20px; }}

    .health-overview-panel {{
      margin-bottom: 24px;
      overflow: hidden;
    }}
    .health-overview-panel h2 {{
      margin-top: 0;
      margin-bottom: 16px;
      font-size: 20px;
    }}
    .lifecycle-panel {{
      margin-bottom: 24px;
      overflow: hidden;
    }}
    .lifecycle-panel h2 {{
      margin-top: 0;
      margin-bottom: 16px;
      font-size: 20px;
    }}
    .lifecycle-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }}
    .lifecycle-card {{
      padding: 16px 18px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.45);
      min-height: 120px;
    }}
    .lifecycle-label {{
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .5px;
      margin-bottom: 8px;
    }}
    .lifecycle-value {{
      font-size: 18px;
      font-weight: 700;
      line-height: 1.45;
    }}
    .lifecycle-detail {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }}
    .health-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
    }}
    .health-card {{
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: var(--card);
      position: relative;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
    }}
    .health-card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 8px 24px var(--shadow);
    }}
    .health-icon {{
      font-size: 24px;
      line-height: 1;
      flex-shrink: 0;
      margin-top: 2px;
    }}
    .health-info {{
      flex: 1;
      min-width: 0;
    }}
    .health-label {{
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 4px;
    }}
    .health-value {{
      font-size: 18px;
      font-weight: 700;
      line-height: 1.2;
    }}
    .health-detail {{
      font-size: 11px;
      color: var(--muted);
      line-height: 1.4;
      display: block;
      margin-top: 2px;
    }}
    .health-dot {{
      position: absolute;
      top: 12px;
      right: 12px;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      flex-shrink: 0;
    }}
    .health-card.health-green .health-dot {{ background: var(--green); box-shadow: 0 0 6px rgba(31,122,77,.4); }}
    .health-card.health-green .health-value {{ color: var(--green); }}
    .health-card.health-yellow .health-dot {{ background: var(--amber); box-shadow: 0 0 6px rgba(184,107,29,.4); }}
    .health-card.health-yellow .health-value {{ color: var(--amber); }}
    .health-card.health-red .health-dot {{ background: var(--red); box-shadow: 0 0 6px rgba(180,72,54,.4); }}
    .health-card.health-red .health-value {{ color: var(--red); }}
    
    .debate-modal {{
      display: none;
      position: fixed;
      z-index: 1000;
      left: 0;
      top: 0;
      width: 100%;
      height: 100%;
      background-color: rgba(0,0,0,0.6);
      backdrop-filter: blur(4px);
      animation: fadeIn 0.3s ease;
    }}
    
    @media (prefers-color-scheme: dark) {{
      .debate-modal {{
        background-color: rgba(0,0,0,0.8);
      }}
    }}
    @keyframes fadeIn {{
      from {{ opacity: 0; }}
      to {{ opacity: 1; }}
    }}
    @keyframes slideIn {{
      from {{ transform: translateY(-50px); opacity: 0; }}
      to {{ transform: translateY(0); opacity: 1; }}
    }}
    .debate-modal.active {{
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .debate-modal-content {{
      background: var(--paper);
      border-radius: 24px;
      max-width: 900px;
      width: 100%;
      max-height: 90vh;
      overflow-y: auto;
      box-shadow: 0 32px 100px rgba(0,0,0,.3);
      animation: slideIn 0.3s ease;
    }}
    .debate-modal-header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      padding: 24px;
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      background: var(--paper);
      z-index: 10;
    }}
    .debate-modal-header h2 {{
      margin: 0;
      font-size: 24px;
    }}
    .debate-subtitle {{
      color: var(--muted);
      margin: 4px 0 0;
      font-size: 15px;
    }}
    .header-badges {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .close-btn {{
      background: rgba(22,32,24,.08);
      border: none;
      border-radius: 12px;
      width: 40px;
      height: 40px;
      font-size: 28px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      transition: all 0.3s ease;
    }}
    .close-btn:hover {{
      background: rgba(180,72,54,.15);
      color: var(--red);
    }}
    .copy-btn {{
      background: rgba(31,122,77,.08);
      border: 1px solid rgba(31,122,77,.2);
      border-radius: 10px;
      width: 40px;
      height: 40px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--green);
      transition: all 0.3s ease;
    }}
    .copy-btn:hover {{
      background: rgba(31,122,77,.15);
      transform: scale(1.05);
    }}
    .debate-modal-body {{
      padding: 24px;
    }}
    .consensus-section {{
      background: linear-gradient(135deg, rgba(31,122,77,.08), rgba(31,122,77,.04));
      border: 1px solid rgba(31,122,77,.2);
      border-radius: 16px;
      padding: 18px;
      margin-bottom: 20px;
    }}
    .consensus-section h3 {{
      margin: 0 0 10px;
      color: var(--green);
    }}
    .score-breakdown {{
      background: linear-gradient(135deg, rgba(31,122,77,.12), rgba(31,122,77,.06));
      border: 1px solid rgba(31,122,77,.25);
      border-radius: 16px;
      padding: 18px;
      margin-bottom: 20px;
    }}
    .score-breakdown h3 {{
      margin: 0 0 14px;
      color: var(--green);
    }}
    .score-comparison {{
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 14px;
    }}
    .score-item {{
      flex: 1;
      text-align: center;
      padding: 12px;
      background: rgba(255,255,255,.5);
      border-radius: 12px;
    }}
    .score-label {{
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .score-value {{
      font-size: 28px;
      font-weight: 700;
      color: var(--ink);
    }}
    .score-arrow {{
      font-size: 24px;
      color: var(--muted);
    }}
    .score-item.adjustment .score-value.pos {{ color: var(--green); }}
    .score-item.adjustment .score-value.neg {{ color: var(--red); }}
    .meta-info {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      font-size: 12px;
      color: var(--muted);
    }}
    .meta-info span {{
      padding: 4px 10px;
      background: rgba(255,255,255,.4);
      border-radius: 8px;
    }}
      .consensus-section p {{
      margin: 0;
      line-height: 1.7;
    }}
    .vote-summary {{
      display: flex;
      gap: 12px;
      margin-bottom: 20px;
    }}
    .vote-summary span {{
      padding: 8px 16px;
      border-radius: 12px;
      font-weight: 600;
    }}
    .bull-vote {{ background: rgba(31,122,77,.15); color: var(--green); }}
    .neutral-vote {{ background: rgba(104,117,104,.12); color: var(--muted); }}
    .bear-vote {{ background: rgba(180,72,54,.15); color: var(--red); }}
    .debate-round {{
      background: rgba(255,255,255,.6);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      margin-bottom: 16px;
    }}
    .debate-round h4 {{
      margin: 0 0 12px;
      color: var(--amber);
    }}
    .round-summary {{
      color: var(--muted);
      margin: 0 0 14px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }}
    .opinions-grid {{
      display: grid;
      gap: 12px;
    }}
    .opinion-card {{
      background: white;
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
    }}
    .opinion-card.bullish {{
      border-left: 4px solid var(--green);
    }}
    .opinion-card.bearish {{
      border-left: 4px solid var(--red);
    }}
    .opinion-card.neutral {{
      border-left: 4px solid var(--muted);
    }}
    .opinion-header {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 12px 14px;
      background: rgba(247,244,236,.5);
    }}
    .agent-role {{
      font-weight: 700;
      color: var(--ink);
    }}
    .stance-badge {{
      padding: 3px 8px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 600;
    }}
    .opinion-card.bullish .stance-badge {{ background: rgba(31,122,77,.12); color: var(--green); }}
    .opinion-card.bearish .stance-badge {{ background: rgba(180,72,54,.12); color: var(--red); }}
    .opinion-card.neutral .stance-badge {{ background: rgba(104,117,104,.1); color: var(--muted); }}
    .confidence-bar {{
      flex: 1;
      height: 6px;
      background: rgba(22,32,24,.08);
      border-radius: 3px;
      overflow: hidden;
    }}
    .confidence-fill {{
      height: 100%;
      background: linear-gradient(90deg, var(--green), var(--amber));
      border-radius: 3px;
      transition: width 0.5s ease;
    }}
    .confidence-text {{
      font-size: 12px;
      color: var(--muted);
      min-width: 36px;
      text-align: right;
    }}
    .opinion-body {{
      padding: 12px 14px;
    }}
    .opinion-points {{
      margin: 0;
      padding-left: 18px;
    }}
    .opinion-points li {{
      margin-bottom: 4px;
      line-height: 1.5;
      font-size: 14px;
    }}
    .point-label {{
      display: inline-block;
      min-width: 32px;
      margin-right: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .risk-item {{ color: var(--red); }}
    .opp-item {{ color: var(--green); }}
    .warnings-section, .opportunities-section {{
      background: white;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      margin-top: 16px;
    }}
    .warnings-section h3 {{ color: var(--red); margin: 0 0 10px; }}
    .opportunities-section h3 {{ color: var(--green); margin: 0 0 10px; }}
    .warnings-section ul, .opportunities-section ul {{
      margin: 0;
      padding-left: 20px;
    }}
    .warnings-section li, .opportunities-section li {{
      margin-bottom: 6px;
      line-height: 1.5;
    }}
    
    @media (max-width: 768px) {{
      .opinions-grid {{
        grid-template-columns: 1fr;
      }}
      .card-header {{
        flex-wrap: wrap;
      }}
      .expand-btn {{
        order: -1;
        margin-left: auto;
      }}
      .health-grid {{
        grid-template-columns: repeat(2, 1fr);
      }}
      .health-card {{
        padding: 10px 12px;
      }}
      .health-value {{
        font-size: 15px;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;">
      <div>
        <h1>{safe_title}</h1>
        <p class="sub">生成时间 {generated_at}。仅供研究复核 / 不连接券商 / 不触发真实委托。</p>
        <div class="meta">
          <span class="pill">最新信号日 {latest_date}</span>
          <span class="pill">候选数据日 {display_date}</span>
          <span class="pill">阈值版本 {thresholds_version}</span>
          <span class="pill">候选数 {len(candidates)}</span>
          <span class="pill">通知级别 {notify_level}</span>
          <span class="pill">数据源 {source_health_label}</span>
        </div>
      </div>
      <a href="agents.html" style="text-decoration:none;background:rgba(31,122,77,.12);border:1px solid rgba(31,122,77,.25);color:var(--green);padding:10px 16px;border-radius:12px;font-weight:600;display:inline-flex;align-items:center;gap:8px;transition:all .3s;">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="3"/>
          <path d="M12 1v6m0 6v6m4.22-13.22l-4.24 4.24m0 4.24l4.24 4.24m-12.44-8.48l4.24 4.24m0 4.24l-4.24 4.24"/>
        </svg>
        Agent性能
      </a>
    </div>
  </header>
  {warning_html}
  {health_panel_html}
  {_lifecycle_overview_panel(candidates)}
  <!-- PANEL_STRATEGY_PERF -->
  <section class="stats">
    <div class="stat"><b>{stats.total}</b><span>ledger 总记录</span></div>
    <div class="stat"><b>{stats.pending}</b><span>待验证信号</span></div>
    <div class="stat"><b>{stats.validated}</b><span>已验证信号</span></div>
    <div class="stat"><b>{stats.not_executable}</b><span>不可成交样本</span></div>
    <div class="stat"><b>{_fmt_pct(stats.win_rate)}</b><span>已验证胜率</span></div>
    <div class="stat"><b>{_fmt_num(stats.avg_return_pct)}</b><span>平均收益 pct</span></div>
    <div class="stat"><b>{paper.open_positions}</b><span>纸面持有跟踪</span></div>
    <div class="stat"><b>{paper.closed}</b><span>纸面退出记录</span></div>
    <div class="stat"><b>{paper.not_executable}</b><span>纸面不可成交</span></div>
    <div class="stat"><b>{paper.pending_entry}</b><span>等待入场数据</span></div>
    <div class="stat"><b>{_fmt_num(paper.avg_return_pct)}</b><span>纸面平均收益 pct</span></div>
  </section>
  {_source_runtime_panel(stats)}
  {render_source_health_panel(source_health_path)}
  <!-- PANEL_KLINE -->
  <!-- PANEL_MORNING_EVENING -->
  {_research_panel(research_summary)}
  <main class="grid">
    {_candidate_cards(candidates, visible_debate_map)}
  </main>
  <section class="panel">
    <h2>最近信号</h2>
    <table>
      <thead><tr><th>日期</th><th>代码</th><th>评分</th><th>状态</th><th>收益 pct</th></tr></thead>
      <tbody>{_recent_rows(rows)}</tbody>
    </table>
  </section>
  <section class="panel">
    <h2>纸面记录</h2>
    <table>
      <thead><tr><th>代码</th><th>状态</th><th>入场日</th><th>入场价</th><th>收益 pct</th><th>原因</th></tr></thead>
      <tbody>{_paper_rows(paper_rows or [])}</tbody>
    </table>
  </section>
  {debate_modals_html}
  <button class="theme-toggle" onclick="toggleTheme()" aria-label="切换深色模式" title="切换深色模式">
    <svg class="sun-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="5"/>
      <line x1="12" y1="1" x2="12" y2="3"/>
      <line x1="12" y1="21" x2="12" y2="23"/>
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
      <line x1="1" y1="12" x2="3" y2="12"/>
      <line x1="21" y1="12" x2="23" y2="12"/>
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
    </svg>
    <svg class="moon-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="display:none;">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
    </svg>
  </button>
  <script>
    // 主题切换
    function toggleTheme() {{
      const html = document.documentElement;
      const isDark = html.getAttribute('data-theme') === 'dark';
      html.setAttribute('data-theme', isDark ? 'light' : 'dark');
      localStorage.setItem('theme', isDark ? 'light' : 'dark');
      updateThemeIcon(!isDark);
    }}
    
    function updateThemeIcon(isDark) {{
      document.querySelector('.sun-icon').style.display = isDark ? 'none' : 'block';
      document.querySelector('.moon-icon').style.display = isDark ? 'block' : 'none';
    }}
    
    // 初始化主题
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme) {{
      document.documentElement.setAttribute('data-theme', savedTheme);
      updateThemeIcon(savedTheme === 'dark');
    }} else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {{
      document.documentElement.setAttribute('data-theme', 'dark');
      updateThemeIcon(true);
    }}
    
    // 辩论模态框
    function showDebate(symbol) {{
      const modal = document.getElementById('debate-' + symbol);
      if (modal) {{
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
        modal.setAttribute('aria-hidden', 'false');
        // 聚焦关闭按钮
        const closeBtn = modal.querySelector('.close-btn');
        if (closeBtn) closeBtn.focus();
      }}
    }}
    
    function closeDebate(symbol) {{
      const modal = document.getElementById('debate-' + symbol);
      if (modal) {{
        modal.classList.remove('active');
        document.body.style.overflow = '';
        modal.setAttribute('aria-hidden', 'true');
      }}
    }}
    
    function toggleCard(btn) {{
      const card = btn.closest('.card');
      const details = card.querySelector('.card-details');
      const footer = card.querySelector('.card-footer');
      const isExpanded = card.classList.contains('expanded');
      
      if (isExpanded) {{
        card.classList.remove('expanded');
        details.style.display = '';
        footer.style.display = '';
        btn.setAttribute('aria-expanded', 'false');
        btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>';
      }} else {{
        card.classList.add('expanded');
        btn.setAttribute('aria-expanded', 'true');
        btn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="18 15 12 9 6 15"/></svg>';
      }}
    }}
    
    // 键盘导航
    document.addEventListener('keydown', function(e) {{
      // ESC 关闭模态框
      if (e.key === 'Escape') {{
        const activeModal = document.querySelector('.debate-modal.active');
        if (activeModal) {{
          e.preventDefault();
          activeModal.classList.remove('active');
          document.body.style.overflow = '';
          activeModal.setAttribute('aria-hidden', 'true');
        }}
      }}
      
      // Tab 在模态框内循环
      if (e.key === 'Tab') {{
        const activeModal = document.querySelector('.debate-modal.active');
        if (activeModal) {{
          const focusableElements = activeModal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
          if (focusableElements.length > 0) {{
            const firstElement = focusableElements[0];
            const lastElement = focusableElements[focusableElements.length - 1];
            
            if (e.shiftKey && document.activeElement === firstElement) {{
              e.preventDefault();
              lastElement.focus();
            }} else if (!e.shiftKey && document.activeElement === lastElement) {{
              e.preventDefault();
              firstElement.focus();
            }}
          }}
        }}
      }}
    }});
    
    // 点击模态框外部关闭
    document.addEventListener('click', function(e) {{
      if (e.target.classList.contains('debate-modal') && e.target.classList.contains('active')) {{
        const symbol = e.target.id.replace('debate-', '');
        closeDebate(symbol);
      }}
    }});
    
    // 复制辩论结果
    function copyDebate(symbol) {{
      const modal = document.getElementById('debate-' + symbol);
      if (modal) {{
        const text = modal.textContent;
        navigator.clipboard.writeText(text).then(() => {{
          showToast('辩论详情已复制到剪贴板');
        }}).catch(() => {{
          showToast('复制失败，请手动复制');
        }});
      }}
    }}
    
    // Toast 提示
    function showToast(message) {{
      const toast = document.createElement('div');
      toast.className = 'toast';
      toast.textContent = message;
      toast.setAttribute('role', 'alert');
      document.body.appendChild(toast);
      setTimeout(() => {{
        toast.classList.add('show');
        setTimeout(() => {{
          toast.classList.remove('show');
          setTimeout(() => toast.remove(), 300);
        }}, 2000);
      }}, 10);
    }}
  </script>
  <style>
    .toast {{
      position: fixed;
      bottom: 30px;
      left: 50%;
      transform: translateX(-50%) translateY(100px);
      background: var(--ink);
      color: var(--paper);
      padding: 12px 24px;
      border-radius: 12px;
      box-shadow: 0 8px 32px var(--shadow);
      opacity: 0;
      transition: all 0.3s ease;
      z-index: 9999;
    }}
    .toast.show {{
      transform: translateX(-50%) translateY(0);
      opacity: 1;
    }}
    
    [data-theme="dark"] .sun-icon {{ display: none; }}
    [data-theme="dark"] .moon-icon {{ display: block !important; }}
    [data-theme="light"] .moon-icon {{ display: none; }}
    
    @media (prefers-color-scheme: dark) {{
      [data-theme="dark"] .sun-icon {{ display: none; }}
      [data-theme="dark"] .moon-icon {{ display: block !important; }}
    }}
  </style>
</body>
</html>
"""


def render_all_panels(
    candidates: list[dict[str, str]] | None = None,
    ledger_path: str = "data/predictions.jsonl",
    paper_ledger_path: str = "data/paper_trades.jsonl",
    debate_path: str = "data/debate_results.jsonl",
    source_health_path: str | Path | None = None,
    title: str = "AQSP 量化选股面板",
) -> str:
    if candidates is None:
        csv_path = Path("reports/close.csv")
        candidates = read_candidates(csv_path)

    rows = read_ledger_rows(Path(ledger_path))
    paper_rows = read_paper_rows(Path(paper_ledger_path))
    debate_map = read_debate_results(Path(debate_path))
    research_summary = load_research_summary()

    base_html = render_dashboard(
        candidates,
        rows,
        title,
        paper_rows,
        research_summary,
        debate_map,
        source_health_path,
    )

    strategy_panel = render_strategy_performance_panel(ledger_path)
    kline_panel = render_kline_panel(ledger_path)
    morning_evening_panel = render_morning_evening_panel(ledger_path)

    result = base_html.replace("<!-- PANEL_STRATEGY_PERF -->\n", strategy_panel + "\n")
    result = result.replace("<!-- PANEL_KLINE -->\n", kline_panel + "\n")
    result = result.replace(
        "<!-- PANEL_MORNING_EVENING -->\n", morning_evening_panel + "\n"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="reports/close.csv")
    parser.add_argument("--ledger", default="data/predictions.jsonl")
    parser.add_argument("--paper-ledger", default="data/paper_trades.jsonl")
    parser.add_argument("--debate", default="data/debate_results.jsonl")
    parser.add_argument("--source-health", default="data/source_health.json")
    parser.add_argument("--output", default="dist/dashboard/index.html")
    parser.add_argument("--title", default="AQSP 量化选股面板")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # 从predictions.jsonl中提取最新的candidates
    candidates = []
    ledger_rows = read_ledger_rows(Path(args.ledger))
    if ledger_rows:
        # 取最新的一天的信号
        last_date = None
        for row in reversed(ledger_rows):
            if row.get("signal_date"):
                if last_date is None or row["signal_date"] > last_date:
                    last_date = row["signal_date"]
        if last_date:
            candidates = [
                {
                    "symbol": row.get("symbol", ""),
                    "name": row.get("name", ""),
                    "score": row.get("score", 0),
                    "rating": row.get("rating", ""),
                    "strategies": row.get("strategies", ""),
                    "thresholds_version": row.get("thresholds_version", ""),
                }
                for row in ledger_rows
                if row.get("signal_date") == last_date
            ]
    html_text = render_all_panels(
        candidates,
        args.ledger,
        args.paper_ledger,
        args.debate,
        args.source_health,
        args.title,
    )
    output.write_text(html_text, encoding="utf-8")
    print(f"dashboard={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
