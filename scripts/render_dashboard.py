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
from aqsp.data.source_readiness import source_supports_workload, workload_fit_for_source
from aqsp.presentation import (
    describe_source_health as present_source_health,
    describe_source_layers,
    format_source_route,
    format_review_meta,
    format_symbol_name,
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
from aqsp.web.entrypoint import public_dashboard_url, write_dashboard_artifact
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


@dataclass(frozen=True)
class CandidateSourceSelection:
    candidates: list[dict[str, str]]
    path: Path
    source_label: str


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


def _short_date(value: Any) -> str:
    return str(value or "").strip()[:10]


def _debate_match_date(debate: dict[str, Any]) -> str:
    for key in ("related_signal_date", "signal_date", "debate_date"):
        date_value = _short_date(debate.get(key))
        if date_value:
            return date_value
    return ""


def _candidate_match_date(candidate: dict[str, Any]) -> str:
    for key in ("date", "signal_date", "related_signal_date", "debate_date"):
        date_value = _short_date(candidate.get(key))
        if date_value:
            return date_value
    return ""


def _debate_storage_key(debate: dict[str, Any]) -> str:
    symbol = str(debate.get("symbol", "") or "").strip()
    match_date = _debate_match_date(debate)
    if symbol and match_date:
        return f"{symbol}::{match_date}"
    return symbol


def read_debate_results(path: Path) -> dict[str, dict[str, Any]]:
    """读取辩论结果，保留同一 symbol 在不同信号日的记录。"""
    if not path.exists():
        return {}
    results: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                data = json.loads(line)
                key = _debate_storage_key(data)
                if key:
                    debate_date = _short_date(data.get("debate_date"))
                    existing_date = _short_date(results.get(key, {}).get("debate_date"))
                    if key not in results or debate_date > existing_date:
                        results[key] = data
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


def _tag_candidate_source(
    candidates: list[dict[str, str]],
    *,
    source_label: str,
    source_path: Path,
) -> list[dict[str, str]]:
    return [
        {
            **row,
            "__candidate_source_label": source_label,
            "__candidate_source_path": str(source_path),
        }
        for row in candidates
    ]


def read_preferred_candidates(
    csv_path: Path,
    *,
    intraday_csv_path: Path | None = None,
) -> CandidateSourceSelection:
    close_candidates = read_candidates(csv_path)
    intraday_candidates = (
        read_candidates(intraday_csv_path)
        if intraday_csv_path is not None and intraday_csv_path.exists()
        else []
    )
    close_date = latest_candidate_date(close_candidates)
    intraday_date = latest_candidate_date(intraday_candidates)
    today = now_shanghai().date().isoformat()
    intraday_is_today = bool(intraday_date and intraday_date == today)
    if (
        intraday_candidates
        and intraday_is_today
        and (not close_date or intraday_date >= close_date)
    ):
        assert intraday_csv_path is not None
        return CandidateSourceSelection(
            candidates=_tag_candidate_source(
                intraday_candidates,
                source_label="盘中实时",
                source_path=intraday_csv_path,
            ),
            path=intraday_csv_path,
            source_label="盘中实时",
        )
    return CandidateSourceSelection(
        candidates=_tag_candidate_source(
            close_candidates,
            source_label="收盘主链",
            source_path=csv_path,
        ),
        path=csv_path,
        source_label="收盘主链",
    )


def read_daily_digest(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


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


_DAILY_DIGEST_PRIORITY_TERMS: tuple[tuple[str, ...], ...] = (
    ("结论", "总控"),
    ("跨市", "主线"),
    ("运行状态", "数据:", "数据日"),
    ("讨论结论", "委员会结论", "多 Agent 结论", "多Agent结论"),
    ("风险", "风控", "阻塞", "卡点", "失效"),
    ("市场上下文", "北向", "全局雷达", "跨市"),
    ("首要复核", "候选:", "纸面复核"),
    ("讨论支持", "讨论反对", "讨论待确认", "讨论执行", "讨论过程", "讨论"),
)


def _daily_digest_priority_rank(point: str) -> int:
    for index, terms in enumerate(_DAILY_DIGEST_PRIORITY_TERMS):
        if any(term in point for term in terms):
            return index
    return len(_DAILY_DIGEST_PRIORITY_TERMS)


def _daily_digest_points(markdown: str, *, limit: int = 5) -> list[str]:
    points: list[str] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("## "):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        elif line.startswith("* "):
            line = line[2:].strip()
        else:
            continue
        if line and line not in points:
            points.append(line)
    if len(points) <= limit:
        return points
    ordered = sorted(
        enumerate(points),
        key=lambda item: (_daily_digest_priority_rank(item[1]), item[0]),
    )
    return [point for _, point in ordered[:limit]]


def _daily_digest_panel(markdown: str) -> str:
    points = _daily_digest_points(markdown, limit=5)
    if not points:
        return ""
    items = "".join(f"<li>{html.escape(point)}</li>" for point in points)
    return f"""
  <section id="daily-digest" class="daily-digest-panel">
    <div class="section-heading">
      <span>当天消息汇总</span>
      <small>收盘总览压缩版</small>
    </div>
    <ul>{items}</ul>
  </section>
    """


def _vote_snapshot(debate: dict[str, Any]) -> str:
    final_vote = debate.get("final_vote", {}) or {}
    if not isinstance(final_vote, dict) or not final_vote:
        return "暂无投票快照"
    bull_count = sum(1 for value in final_vote.values() if value == "bullish")
    bear_count = sum(1 for value in final_vote.values() if value == "bearish")
    neutral_count = len(final_vote) - bull_count - bear_count
    return f"看多 {bull_count} / 中性 {neutral_count} / 看空 {bear_count}"


def _debate_frontdesk_lines(debate: dict[str, Any]) -> tuple[str, ...]:
    lines: list[str] = []
    consensus = str(debate.get("final_consensus", "") or "").strip()
    cross_market = _debate_cross_market_digest(debate)
    support_line = _debate_brief_line(debate, "support_points", "支持")
    watch_line = _debate_brief_line(debate, "watch_items", "待确认")
    if consensus:
        lines.append(f"结论: {normalize_research_tone(consensus)}")
    if cross_market:
        lines.append(f"跨市链: {normalize_research_tone(cross_market)}")
    if support_line:
        lines.append(normalize_research_tone(support_line))
    if watch_line:
        lines.append(normalize_research_tone(watch_line))
    lines.append(f"投票: {_vote_snapshot(debate)}")
    return tuple(lines[:5])


def _debate_process_snapshot(debate: dict[str, Any]) -> str:
    rounds = debate.get("rounds", []) or []
    if not rounds:
        return ""
    latest_round = rounds[-1]
    round_num = latest_round.get("round_num", len(rounds))
    summary = str(latest_round.get("summary", "") or "").strip()
    if not summary:
        return f"过程: 共 {len(rounds)} 轮讨论，保留结构化结论。"
    return f"过程: 第 {round_num} 轮摘要: {normalize_research_tone(summary)}"


def _frontdesk_debate_panel(debate_map: dict[str, dict[str, Any]]) -> str:
    cards: list[str] = []
    for symbol, debate in list(debate_map.items())[:3]:
        display_name = format_symbol_name(
            str(debate.get("symbol", symbol) or symbol).strip(),
            str(debate.get("name", "") or "").strip(),
        )
        adjustment = str(debate.get("recommended_adjustment", "keep") or "keep")
        tone = {"raise": "bull", "lower": "bear", "keep": "neutral"}.get(
            adjustment, "neutral"
        )
        process = _debate_process_snapshot(debate)
        line_items = "".join(
            f"<li>{html.escape(line)}</li>"
            for line in (*_debate_frontdesk_lines(debate), process)
            if line
        )
        cards.append(
            f"""
        <article class="frontdesk-debate-card {tone}">
          <div class="frontdesk-card-kicker">Agent 结果</div>
          <h3>{html.escape(display_name or symbol)}</h3>
          <ul>{line_items}</ul>
          <button class="debate-btn" onclick="showDebate('{html.escape(symbol)}')" aria-label="查看多 Agent 详情">查看结构化讨论</button>
        </article>
            """
        )
    if not cards:
        cards.append(
            """
        <article class="frontdesk-debate-card neutral">
          <div class="frontdesk-card-kicker">Agent 结果</div>
          <h3>候选已更新，讨论回填中</h3>
          <ul>
            <li>首页先展示全局盘中候选；多 Agent 讨论异步补充。</li>
            <li>该模块不阻塞盘中候选落盘，也不改写系统评分。</li>
          </ul>
        </article>
            """
        )
    return f"""
  <section id="agent-discussion" class="frontdesk-agent-panel">
    <div class="section-heading">
      <span>Agent讨论</span>
      <small>先看委员会结果，再看过程摘要</small>
    </div>
    <div class="frontdesk-debate-grid">
      {"".join(cards)}
    </div>
  </section>
    """


def _live_source_boundary_label(source_id: str) -> str:
    source = str(source_id or "").strip()
    if not source:
        return ""
    fit = workload_fit_for_source(source).get("live_short", "unknown")
    if source_supports_workload(source, "live_short"):
        return f"实时源 {source}（live_short={fit}）"
    return f"当前实际源 {source} 只适合历史验证，盘中短线不可用（live_short={fit}）"


def _latest_run_event(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in reversed(rows):
        if str(row.get("symbol", "") or "") == "__RUN__":
            return row
        if row.get("event_type") or row.get("run_task_id"):
            return row
    return None


def _text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return [part.strip() for part in stripped.split("；") if part.strip()]
        return _text_values(parsed)
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _candidate_news_catalyst_summary(row: dict[str, Any]) -> str:
    judgement = str(row.get("news_catalyst_judgement", "") or "").strip()
    if not judgement:
        return ""
    label = {
        "supports": "消息支持",
        "opposes": "消息反对",
        "mixed": "消息分歧",
        "needs_review": "消息待复核",
    }.get(judgement, "消息观察")
    lead = str(row.get("news_catalyst_lead", "") or "").strip()
    if not lead:
        for field in (
            "news_catalyst_opposes",
            "news_catalyst_supports",
            "news_catalyst_needs_review",
        ):
            values = _text_values(row.get(field))
            if values:
                lead = values[0]
                break
    source = str(row.get("news_catalyst_source", "") or "").strip()
    source_suffix = f"｜{source}" if source and source not in lead else ""
    return normalize_research_tone(f"{label}: {lead}{source_suffix}" if lead else label)


def _candidate_cross_market_summary(row: dict[str, Any]) -> str:
    theme = str(row.get("cross_market_primary_theme", "") or "").strip()
    if not theme:
        return ""
    action = str(row.get("cross_market_action", "") or "").strip()
    stack = str(row.get("cross_market_evidence_stack_summary", "") or "").strip()
    stack_suffix = f"｜{stack}" if stack else ""
    if action:
        return normalize_research_tone(f"{theme}({action}){stack_suffix}")
    return normalize_research_tone(f"{theme}{stack_suffix}")


def _candidate_cross_market_chain_lead(row: dict[str, Any]) -> str:
    chain = str(row.get("cross_market_chain_summary", "") or "").strip()
    if chain:
        return normalize_research_tone(chain.split("｜", 1)[0].strip())
    values = _text_values(row.get("cross_market_transmission_path"))
    return normalize_research_tone(values[0]) if values else ""


def _latest_market_context_lines(
    rows: list[dict[str, Any]],
    *,
    signal_date: str,
) -> list[str]:
    selected_date = signal_date.strip()[:10]
    candidates = [
        row
        for row in rows
        if (
            not selected_date
            or str(row.get("signal_date", "") or "")[:10] == selected_date
        )
        and _text_values(row.get("run_market_context_lines"))
    ]
    if not candidates and selected_date:
        candidates = [
            row for row in rows if _text_values(row.get("run_market_context_lines"))
        ]
    if not candidates:
        return []
    latest = max(
        candidates,
        key=lambda row: (
            str(row.get("signal_date", "") or ""),
            str(row.get("created_at", "") or ""),
            _safe_float(row.get("score")) or 0.0,
        ),
    )
    return _text_values(latest.get("run_market_context_lines"))[:2]


def _fmt_pct_points(value: Any) -> str:
    number = _safe_float(value)
    return "-" if number is None else f"{number:.2f}%"


def _runtime_digest_from_ledger(
    rows: list[dict[str, Any]],
    candidates: list[dict[str, str]],
) -> str:
    run = _latest_run_event(rows)
    if run is None:
        return ""

    task_id = str(run.get("run_task_id") or run.get("task_id") or "").strip()
    status = str(run.get("status") or "").strip()
    reason = str(
        run.get("run_circuit_breaker_reason") or run.get("reason") or ""
    ).strip()
    final_count = _safe_float(run.get("run_final_count"))
    screened_count = _safe_float(run.get("run_screened_count"))
    fetched_count = _safe_float(run.get("run_fetched_frame_count"))
    signal_date = str(
        run.get("signal_date") or run.get("signal_day_group") or ""
    ).strip()

    if status == "blocked_by_circuit_breaker" or run.get(
        "run_circuit_breaker_triggered"
    ):
        conclusion = "组合保护生效，暂停新增纸面复核"
    elif candidates:
        conclusion = f"当前看板有 {len(candidates)} 个候选，先看候选卡片"
    elif final_count == 0:
        conclusion = "最近运行无新增候选，先看阻塞与数据状态"
    else:
        conclusion = "最近运行已落盘，等待完整收盘摘要"

    lines = ["## 结果", f"- 结论: {conclusion}"]
    if reason:
        lines.append(f"- 风险/阻塞: {normalize_research_tone(reason)}")
    if task_id or signal_date:
        parts = []
        if task_id:
            parts.append(f"任务 {task_id}")
        if signal_date:
            parts.append(f"日期 {signal_date[:10]}")
        lines.append("- 运行状态: " + " / ".join(parts))

    source = str(run.get("run_actual_source") or run.get("run_requested_source") or "")
    data_date = str(run.get("run_data_latest_trade_date") or "")
    lag_value = run.get("run_data_lag_days")
    lag = "" if lag_value in ("", None) else str(lag_value)
    data_parts = []
    if source:
        data_parts.append(_live_source_boundary_label(source))
    if data_date:
        data_parts.append(f"数据日 {data_date}")
    if lag:
        data_parts.append(f"延迟 {lag} 天")
    if data_parts:
        lines.append("- 数据: " + " / ".join(data_parts))

    for context_line in _latest_market_context_lines(rows, signal_date=signal_date)[:1]:
        lines.append(f"- 市场上下文: {normalize_research_tone(context_line)}")

    count_parts = []
    if fetched_count is not None:
        count_parts.append(f"获取 {int(fetched_count)}")
    if screened_count is not None:
        count_parts.append(f"筛选 {int(screened_count)}")
    if final_count is not None:
        count_parts.append(f"候选 {int(final_count)}")
    if count_parts:
        lines.append("- 流程: " + " / ".join(count_parts))

    if any(run.get(key) is not None for key in ("daily_pnl_pct", "monthly_pnl_pct")):
        lines.append(
            "- 风控读数: "
            f"日 {_fmt_pct_points(run.get('daily_pnl_pct'))} / "
            f"月 {_fmt_pct_points(run.get('monthly_pnl_pct'))}"
        )
    return "\n".join(lines)


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


def _debate_matches_candidate_date(
    candidate: dict[str, str],
    debate: dict[str, Any],
) -> bool:
    candidate_date = _candidate_match_date(candidate)
    debate_date = _debate_match_date(debate)
    return bool(candidate_date and debate_date and candidate_date == debate_date)


def _debate_symbol_for_key(key: str, debate: dict[str, Any]) -> str:
    symbol = str(debate.get("symbol", "") or "").strip()
    if symbol:
        return symbol
    return str(key).split("::", 1)[0].strip()


def _select_debate_for_candidate(
    candidate: dict[str, Any],
    debate_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    symbol = str(candidate.get("symbol", "") or "").strip()
    if not symbol:
        return None
    candidate_date = _candidate_match_date(candidate)
    symbol_debates = [
        debate
        for key, debate in debate_map.items()
        if _debate_symbol_for_key(key, debate) == symbol
    ]
    if not symbol_debates:
        return None
    exact_matches = [
        debate
        for debate in symbol_debates
        if candidate_date and _debate_match_date(debate) == candidate_date
    ]
    if candidate_date:
        if not exact_matches:
            return None
        debates = exact_matches
    else:
        debates = symbol_debates
    return max(debates, key=lambda debate: _short_date(debate.get("debate_date")))


def _visible_debate_map_for_candidates(
    candidates: list[dict[str, str]],
    debate_map: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not candidates:
        return {}

    visible: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        symbol = str(candidate.get("symbol", "") or "").strip()
        if not symbol:
            continue
        debate = _select_debate_for_candidate(candidate, debate_map)
        if debate is None:
            continue
        visible[symbol] = {
            **debate,
            "_archive_only": not _debate_matches_candidate_date(candidate, debate),
        }
    return visible


def _role_display_name(role: str) -> str:
    emoji = agent_role_emoji(role)
    label = agent_role_label(role, language="zh-CN")
    return f"{emoji} {label}".strip()


def _debate_context_line_value(
    debate: dict[str, Any],
    *,
    attr_names: tuple[str, ...] = (),
    prefixes: tuple[str, ...] = (),
) -> str:
    for attr_name in attr_names:
        value = str(debate.get(attr_name, "") or "").strip()
        if value:
            return value
    for raw in debate.get("market_context_lines", []) or []:
        line = str(raw).strip()
        for prefix in prefixes:
            if line.startswith(prefix):
                return line[len(prefix) :].strip()
    return ""


def _debate_cross_market_digest(debate: dict[str, Any]) -> str:
    theme = _debate_context_line_value(
        debate,
        attr_names=("cross_market_summary",),
        prefixes=("跨市传导:",),
    )
    validation = _debate_context_line_value(
        debate,
        attr_names=("cross_market_validation_summary",),
        prefixes=("确认信号:", "确认条件:"),
    )
    invalidation = _debate_context_line_value(
        debate,
        attr_names=("cross_market_invalidation_summary",),
        prefixes=("失效信号:", "失效条件:"),
    )
    display = format_symbol_name(
        str(debate.get("symbol", "") or "").strip(),
        str(debate.get("name", "") or "").strip(),
    )
    parts: list[str] = []
    if theme:
        parts.append(theme)
    if display:
        parts.append(f"先看 {display}")
    if validation:
        parts.append(f"确认 {validation}")
    if invalidation:
        parts.append(f"失效 {invalidation}")
    if len(parts) <= 1 and not theme:
        return ""
    return " | ".join(parts)


def _debate_brief_line(debate: dict[str, Any], key: str, label: str) -> str:
    values = tuple(
        str(item).strip() for item in (debate.get(key, []) or []) if str(item).strip()
    )
    if not values:
        return ""
    return f"{label}: {'；'.join(values[:2])}"


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
        headline = "后续关注"
        headline_detail = review_items[0]
    elif watchlist:
        headline = "观察名单"
        headline_detail = "、".join(watchlist[:3])
    else:
        headline = "暂无主链"
        headline_detail = "等待下一轮有效候选输出"

    action_line = "明日无明确主链复核，先确认数据与候选是否完整。"
    if actionable:
        action_line = f"明日先盯 {' → '.join(actionable[:3])} 的开盘强弱与流动性。"
    elif review_items:
        action_line = f"明日先盯 {review_items[0]}。"
    elif watchlist:
        action_line = f"明日先围绕 {'、'.join(watchlist[:3])} 再看，不放大纸面仓位。"

    summary_cards = [
        ("主链复核", headline, headline_detail),
        (
            "候选分层",
            f"纸面复核 {len(actionable)} / 观察 {len(watchlist)}",
            "纸面复核与观察对象已按当前主链输出分层。",
        ),
        (
            "阻塞",
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
        news_catalyst = html.escape(_candidate_news_catalyst_summary(row))
        cross_market = html.escape(_candidate_cross_market_summary(row))
        cross_market_chain = html.escape(_candidate_cross_market_chain_lead(row))
        cross_market_validation = html.escape(
            (_text_values(row.get("cross_market_validation_signals")) or [""])[0]
        )
        cross_market_invalidation = html.escape(
            (_text_values(row.get("cross_market_invalidation_signals")) or [""])[0]
        )
        strategies = html.escape(" / ".join(_strategy_values(row.get("strategies"))))
        reasons = html.escape(row.get("reasons", ""))
        risks = html.escape(row.get("risks", ""))
        debate = debate_map.get(symbol)
        has_debate = debate is not None
        has_current_debate = (
            has_debate
            and debate is not None
            and _debate_matches_candidate_date(row, debate)
        )

        # 数据关联性检查：辩论时间是否在合理范围内
        debate_age = (
            _debate_age_label(str(debate.get("debate_date", ""))) if has_debate else ""
        )

        debate_btn = (
            f"""<button class="debate-btn" onclick="showDebate('{symbol}')" aria-label="查看辩论详情">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
                </svg>
                {"多 Agent 结论" if has_current_debate else "历史多 Agent 归档"} {f'<span class="debate-age">({debate_age})</span>' if debate_age else ""}
            </button>"""
            if has_debate
            else """<button class="debate-btn no-debate" disabled aria-label="暂无多 Agent 结论">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/>
                    <line x1="12" y1="8" x2="12" y2="12"/>
                    <line x1="12" y1="16" x2="12.01" y2="16"/>
                </svg>
                暂无多 Agent 结论
            </button>"""
        )
        adjustment_badge = ""
        if has_current_debate:
            adj = debate.get("recommended_adjustment", "keep")
            adj_class = {"raise": "bull", "lower": "bear", "keep": "neutral"}.get(
                adj, "neutral"
            )
            adj_text = {
                "raise": "观点修正上调",
                "lower": "观点修正下调",
                "keep": "观点修正维持",
            }.get(adj, "观点修正维持")
            adjustment_badge = (
                f"<span class='adjustment-badge {adj_class}'>{adj_text}</span>"
            )

        # 多 Agent 只能作为附件参考，候选卡主评分始终显示确定性筛选分。
        original_score = _fmt_num(row.get("score"))
        reference_score = ""
        score_diff = ""
        if has_current_debate:
            debate_original = debate.get("original_score", 0)
            debate_adjusted = debate.get("adjusted_score", debate_original)
            adj_weight = debate.get("adjustment_weight", 0)
            reference_score = _fmt_num(debate_adjusted)
            diff_pct = adj_weight * 100
            if diff_pct > 0:
                score_diff = f"<span class='score-diff bull'>+{diff_pct:.1f}%</span>"
            elif diff_pct < 0:
                score_diff = f"<span class='score-diff bear'>{diff_pct:.1f}%</span>"

        quick_lines = []
        if news_catalyst:
            quick_lines.append(("催化", news_catalyst))
        if cross_market:
            quick_lines.append(("跨市主线", cross_market))
        if cross_market_chain:
            quick_lines.append(("传导", cross_market_chain))
        if cross_market_validation:
            quick_lines.append(("确认", cross_market_validation))
        if cross_market_invalidation:
            quick_lines.append(("失效", cross_market_invalidation))
        quick_lines.extend(
            [
                ("逻辑", reasons or "暂无结构化理由"),
                ("风险", risks or "无明显风险标签"),
            ]
        )
        if candidate_blocker:
            quick_lines.append(("卡点", candidate_blocker))
        if candidate_next_step:
            quick_lines.append(("下一步", candidate_next_step))
        quick_html = "".join(
            f"<p><b>{html.escape(label)}</b>{value}</p>"
            for label, value in quick_lines[:6]
        )

        cards.append(
            f"""
            <article class="card" data-symbol="{symbol}">
              <div class="card-header">
                <div class="rank">#{idx}</div>
                <div class="title-area">
                    <h3>{symbol} <span>{name}</span></h3>
                    <div class="rating-row">
                        <span class="score">{score}</span>
                        <small>/ {decision_label}</small>
                        {f"<small>/ {candidate_status}</small>" if candidate_status else ""}
                        {f"<small>/ PM {portfolio_text}</small>" if portfolio_text and portfolio_action != "keep" else ""}
                        {adjustment_badge}
                    </div>
                    {f"<div class='score-compare'>系统评分 {original_score} · 附件参考 {reference_score} {score_diff}</div>" if has_current_debate and score_diff else ""}
                </div>
                <button class="expand-btn" onclick="toggleCard(this)" aria-label="展开详情" aria-expanded="false">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="6 9 12 15 18 9"/>
                    </svg>
                </button>
              </div>
              <div class="card-quick-lines">{quick_html}</div>
              <dl class="card-details" role="list">
                <dt>策略</dt><dd>{strategies or "-"}</dd>
                <dt>参考价</dt><dd>{_fmt_num(row.get("ideal_buy"))}</dd>
                <dt>收盘</dt><dd>{_fmt_num(row.get("close"))}</dd>
                <dt>最多亏到</dt><dd>{_fmt_num(row.get("stop_loss"))}</dd>
                <dt>先看目标</dt><dd>{_fmt_num(row.get("take_profit"))}</dd>
                <dt>仓位参考</dt><dd>{html.escape(row.get("position", "") or "-")}</dd>
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
        archive_only = bool(debate.get("_archive_only", False))
        name = html.escape(debate.get("name", symbol))
        consensus = html.escape(debate.get("final_consensus", ""))
        adjustment = debate.get("recommended_adjustment", "keep")
        adj_text = {
            "raise": "观点修正上调",
            "lower": "观点修正下调",
            "keep": "观点修正维持",
        }.get(adjustment, "观点修正维持")
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
        rounds_html = f"""
        <details class="debate-round">
            <summary>查看讨论附录</summary>
            <p class='round-summary'>讨论附录只保留轮次摘要，不展示原始辩词。{f"第 {round_num} 轮摘要：{summary}" if summary else ""}</p>
        </details>
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
        cross_market_digest = _debate_cross_market_digest(debate)
        support_line = _debate_brief_line(debate, "support_points", "讨论支持")
        watch_line = _debate_brief_line(debate, "watch_items", "讨论待确认")

        score_breakdown = f"""
        <div class="score-breakdown">
            <h3>📎 附件参考分</h3>
            <div class="score-comparison">
                <div class="score-item original">
                    <span class="score-label">系统评分</span>
                    <span class="score-value">{original_score:.1f}</span>
                </div>
                <div class="score-arrow">→</div>
                <div class="score-item adjusted">
                    <span class="score-label">附件参考分</span>
                    <span class="score-value">{"+" if adjustment_weight > 0 else ""}{adjusted_score:.1f}</span>
                </div>
                <div class="score-item adjustment">
                    <span class="score-label">参考幅度</span>
                    <span class="score-value {adjustment_class}">{adjustment_weight * 100:+.1f}%</span>
                </div>
            </div>
            <p class="muted">多 Agent 只提供附件参考，不改写系统筛选评分。</p>
            <div class="meta-info">
                <span>分歧程度: {disagreement_score:.0%}</span>
                <span>阈值版本: {html.escape(debate.get("thresholds_version", "N/A"))}</span>
                <span>市场状态: {html.escape(debate.get("regime", "N/A"))}</span>
                <span>数据源: {html.escape(debate.get("data_source", "N/A"))}</span>
            </div>
        </div>
        """
        if archive_only:
            score_breakdown = """
        <div class="score-breakdown archive-only">
            <h3>📎 归档说明</h3>
            <p class="muted">这条多 Agent 结论不是候选当日生成，只作为历史研究记录展示，不参与当前候选评分。</p>
        </div>
        """

        modals.append(f"""
        <div id="debate-{
            symbol
        }" class="debate-modal" role="dialog" aria-labelledby="debate-title-{
            symbol
        }" aria-hidden="true">
            <div class="debate-modal-content" role="document">
                <div class="debate-modal-header">
                    <div>
                        <h2 id="debate-title-{symbol}">{
            "历史多 Agent 归档" if archive_only else "多 Agent 结论摘要"
        }</h2>
                        <p class="debate-subtitle">{symbol} {name}</p>
                    </div>
                    <div class="header-badges">
                        {
            ""
            if archive_only
            else f'<span class="adjustment-badge {adj_class}">{adj_text}</span>'
        }
                        <button class="copy-btn" onclick="copyDebate('{
            symbol
        }')" aria-label="复制结论详情" title="复制结论详情">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                            </svg>
                        </button>
                        <button class="close-btn" onclick="closeDebate('{
            symbol
        }')" aria-label="关闭">&times;</button>
                    </div>
                </div>
                <div class="debate-modal-body">
                    {score_breakdown}
                    <div class="consensus-section">
                        <h3>📊 最终共识</h3>
                        <p>{consensus}</p>
                    </div>
                    {
            "<div class='consensus-section'>"
            "<h3>🧭 跨市判断</h3>"
            f"<p>{html.escape(cross_market_digest)}</p>"
            "</div>"
            if cross_market_digest
            else ""
        }
                    {
            "<div class='consensus-section'>"
            "<h3>📌 当前重点</h3>"
            + "".join(
                f"<p>{html.escape(line)}</p>"
                for line in (support_line, watch_line)
                if line
            )
            + "</div>"
            if support_line or watch_line
            else ""
        }
                    {vote_html}
                    <div class="rounds-section">
                        {rounds_html}
                    </div>
                    {
            "<div class='warnings-section'><h3>⚠️ 风险提示</h3><ul>"
            + risk_warnings_html
            + "</ul></div>"
            if risk_warnings_html
            else ""
        }
                    {
            "<div class='opportunities-section'><h3>✅ 机会亮点</h3><ul>"
            + opportunity_highlights_html
            + "</ul></div>"
            if opportunity_highlights_html
            else ""
        }
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
    actual_source = str(stats.actual_source or "").strip()
    if actual_source and not source_supports_workload(actual_source, "live_short"):
        fit = workload_fit_for_source(actual_source).get("live_short", "unknown")
        return (
            "warning historical-source-warning",
            f"最近一次运行实际源 {actual_source} 只适合历史验证，盘中短线不可用（live_short={fit}）。不要把本页当成实时短线信号质量样本。",
        )
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


def _static_research_unlock_panel(
    *,
    candidates: list[dict[str, str]],
    gate_pass: bool,
    gate_detail: str,
    candidate_date: str,
) -> str:
    source_label = _candidate_source_label(candidates)
    actionable = sum(1 for row in candidates if is_tradable_rating(row.get("rating")))
    watch = sum(1 for row in candidates if str(row.get("rating", "") or "") == "watch")
    blocked = sum(
        1
        for row in candidates
        if str(row.get("candidate_blocker", "") or "").strip()
        or str(row.get("portfolio_action", "") or "").strip() == "downgrade"
    )
    if candidates:
        status_title = "研究候选已解锁"
        status_line = (
            f"{len(candidates)} 张候选卡片可看；"
            f"纸面 {actionable} / 观察 {watch} / 阻塞 {blocked}。"
        )
        tone = "unlocked"
    else:
        status_title = "等待当日候选"
        status_line = "当前没有候选卡片，先看运行状态和消息雷达。"
        tone = "waiting"
    gate_line = (
        "生产 gate 已通过，可进入常规纸面研究复核。"
        if gate_pass
        else f"生产 gate 未放行: {gate_detail or '未确认通过'}。"
    )
    return f"""
    <aside class="static-rail-card {tone}">
      <div class="static-rail-kicker">DATE</div>
      <div class="static-rail-date">{html.escape(candidate_date or "暂无")}</div>
      <div class="static-rail-boundary">候选来源: {html.escape(source_label or "未记录")}。只做纸面研究；不连接券商；不触发真实委托。</div>
      <div class="static-unlock-box">
        <div class="static-unlock-title">{html.escape(status_title)}</div>
        <div class="static-unlock-line">{html.escape(status_line)}</div>
        <div class="static-unlock-line">{html.escape(gate_line)}</div>
      </div>
      <div class="static-module-title">模块</div>
      <a class="static-module active" href="#today-candidates">今日候选 <span>{len(candidates)}</span></a>
      <a class="static-module" href="#agent-discussion">Agent讨论 <span>结果/过程</span></a>
      <a class="static-module" href="#daily-digest">消息汇总 <span>当天</span></a>
      <a class="static-module" href="#supporting-status">系统状态 <span>{"通过" if gate_pass else "未放行"}</span></a>
    </aside>
    """


def _candidate_source_label(candidates: list[dict[str, str]]) -> str:
    for row in candidates:
        label = str(row.get("__candidate_source_label", "") or "").strip()
        if label:
            return label
    return ""


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
                    breaker_reason = f"解除日 {cooldown}"
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
            "观点状态",
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
    daily_digest_markdown: str = "",
) -> str:
    debate_map = debate_map or {}
    visible_debate_map = _visible_debate_map_for_candidates(candidates, debate_map)
    stats = summarize_ledger(rows)
    paper = summarize_paper(paper_rows or [])
    generated_at = now_shanghai().isoformat(timespec="seconds")
    today = now_shanghai().date().isoformat()
    candidate_date = latest_candidate_date(candidates)
    safe_title = html.escape(title)
    latest_date = html.escape(stats.latest_signal_date or "暂无")
    display_date = html.escape(candidate_date or "暂无")
    candidate_source_label = html.escape(
        _candidate_source_label(candidates) or "未记录"
    )
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
    effective_daily_digest = daily_digest_markdown or _runtime_digest_from_ledger(
        rows,
        candidates,
    )
    gate = _read_gate_status(Path("data/walkforward_gate.json"))
    gate_pass, gate_detail = _gate_status_for_display(gate)
    unlock_panel_html = _static_research_unlock_panel(
        candidates=candidates,
        gate_pass=gate_pass,
        gate_detail=gate_detail,
        candidate_date=candidate_date,
    )
    daily_digest_html = _daily_digest_panel(effective_daily_digest)
    candidate_grid_html = f"""
  <section id="today-candidates" class="focus-candidates">
    <div class="section-heading">
      <span>今日候选卡片</span>
      <small>先看当日复核对象，再展开辅助数据</small>
    </div>
    <main class="grid">
      {_candidate_cards(candidates, visible_debate_map)}
    </main>
  </section>
    """
    frontdesk_html = f"""
  <section class="aqsp-static-two-column">
    {unlock_panel_html}
    <div class="static-main-column">
      {candidate_grid_html}
      {_frontdesk_debate_panel(visible_debate_map)}
      {daily_digest_html}
    </div>
  </section>
    """
    stats_html = f"""
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
    """
    supporting_html = f"""
  <details id="supporting-status" class="panel panel-fold supporting-panel">
    <summary class="fold-summary">
      <div>
        <h2>系统与历史辅助信息</h2>
        <p class="muted">健康、统计、最近信号和纸面记录默认收起，避免抢占当天判断主线。</p>
      </div>
      <div class="fold-meta">
        <span class="pill">候选 {len(candidates)}</span>
        <span class="pill">ledger {stats.total}</span>
        <span class="fold-caret">展开</span>
      </div>
    </summary>
    <div class="fold-body">
      {health_panel_html}
      {_lifecycle_overview_panel(candidates)}
      {stats_html}
      {_source_runtime_panel(stats)}
      {render_source_health_panel(source_health_path)}
      <!-- PANEL_STRATEGY_PERF -->
      <!-- PANEL_KLINE -->
      <!-- PANEL_MORNING_EVENING -->
      {_research_panel(research_summary)}
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
    </div>
  </details>
    """

    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="aqsp-dashboard-entry" content="offline-archive">
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
    .aqsp-static-two-column {{
      display: grid;
      grid-template-columns: minmax(260px, 0.32fr) minmax(0, 0.68fr);
      gap: 22px;
      align-items: start;
      padding: 0 clamp(20px, 6vw, 80px) 24px;
    }}
    .static-rail-card {{
      position: sticky;
      top: 18px;
      padding: 20px;
      border-radius: 26px;
      border: 1px solid var(--line);
      background:
        radial-gradient(circle at 88% 10%, rgba(31, 122, 77, .12), transparent 32%),
        linear-gradient(180deg, rgba(255,255,252,.94), rgba(238,246,240,.92));
      box-shadow: 0 24px 70px rgba(28, 45, 31, .10);
    }}
    .static-rail-card.waiting {{
      background:
        radial-gradient(circle at 88% 10%, rgba(184, 107, 29, .13), transparent 32%),
        linear-gradient(180deg, rgba(255,255,252,.94), rgba(248,241,231,.92));
    }}
    .static-rail-kicker {{
      color: var(--muted);
      font-size: 12px;
      letter-spacing: .12em;
      text-transform: uppercase;
      font-weight: 800;
    }}
    .static-rail-date {{
      margin-top: 4px;
      color: var(--ink);
      font-size: 24px;
      font-weight: 900;
      font-variant-numeric: tabular-nums;
    }}
    .static-rail-boundary {{
      margin: 8px 0 14px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }}
    .static-unlock-box {{
      padding: 14px;
      border-radius: 18px;
      background: rgba(255,255,255,.66);
      box-shadow: inset 0 0 0 1px rgba(22,32,24,.08);
      margin-bottom: 16px;
    }}
    .static-unlock-title {{
      color: var(--ink);
      font-size: 17px;
      font-weight: 900;
      margin-bottom: 8px;
    }}
    .static-unlock-line {{
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
      margin-top: 4px;
    }}
    .static-module-title {{
      color: var(--ink);
      font-size: 14px;
      font-weight: 900;
      margin: 12px 0 8px;
    }}
    .static-module {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 11px 12px;
      border-radius: 15px;
      background: rgba(255,255,255,.58);
      box-shadow: inset 0 0 0 1px rgba(22,32,24,.08);
      color: var(--muted);
      font-size: 14px;
      margin-top: 8px;
      text-decoration: none;
      transition: transform .18s ease, background .18s ease, color .18s ease;
    }}
    .static-module:hover {{
      transform: translateX(3px);
      background: rgba(255,255,255,.78);
      color: var(--ink);
    }}
    .static-module.active {{
      background: #17384f;
      color: #fffdf7;
      box-shadow: 0 14px 28px rgba(23,56,79,.18);
    }}
    .static-main-column {{
      min-width: 0;
    }}
    .static-main-column .focus-candidates,
    .static-main-column .grid {{
      padding-left: 0;
      padding-right: 0;
    }}
    .static-main-column .daily-digest-panel {{
      margin-top: 0;
    }}
    .stats, .grid {{ display: grid; gap: 16px; padding: 0 clamp(20px, 6vw, 80px) 24px; }}
    .stats {{ grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }}
    .focus-candidates {{
      padding: 0 clamp(20px, 6vw, 80px) 24px;
    }}
    .focus-candidates .section-heading {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
      margin: 0 0 14px;
      font-weight: 800;
    }}
    .focus-candidates .section-heading small {{
      color: var(--muted);
      font-weight: 600;
    }}
    .focus-candidates .grid {{
      padding: 0;
    }}
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
    .card-quick-lines {{
      display: grid;
      gap: 8px;
      margin: 0 0 12px;
    }}
    .card-quick-lines p {{
      margin: 0;
      padding: 9px 11px;
      border-radius: 14px;
      background: rgba(22,32,24,.045);
      color: var(--ink);
      line-height: 1.55;
      font-size: 14px;
    }}
    .card-quick-lines b {{
      display: inline-block;
      min-width: 46px;
      color: var(--muted);
      margin-right: 8px;
    }}
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
    .fold-body > .panel {{
      margin: 16px 0;
    }}
    .fold-body > .stats {{
      padding: 0;
      margin: 16px 0;
    }}
    .supporting-panel {{
      margin-top: 10px;
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

    .frontdesk-agent-panel,
    .daily-digest-panel {{
      margin: 0 0 24px;
      padding: 20px;
      border-radius: 28px;
      background: var(--card);
      border: 1px solid var(--line);
      box-shadow: 0 24px 70px rgba(28, 45, 31, .10);
      backdrop-filter: blur(14px);
    }}
    .frontdesk-agent-panel .section-heading,
    .daily-digest-panel .section-heading {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
      margin: 0 0 14px;
      font-weight: 800;
    }}
    .frontdesk-agent-panel .section-heading small,
    .daily-digest-panel .section-heading small {{
      color: var(--muted);
      font-weight: 600;
    }}
    .frontdesk-debate-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
    }}
    .frontdesk-debate-card {{
      padding: 16px;
      border-radius: 20px;
      background: rgba(255,255,255,.56);
      border: 1px solid var(--line);
    }}
    .frontdesk-debate-card.bull {{ box-shadow: inset 4px 0 0 rgba(31,122,77,.55); }}
    .frontdesk-debate-card.bear {{ box-shadow: inset 4px 0 0 rgba(180,72,54,.55); }}
    .frontdesk-debate-card.neutral {{ box-shadow: inset 4px 0 0 rgba(104,117,104,.28); }}
    .frontdesk-card-kicker {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .08em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }}
    .frontdesk-debate-card h3 {{
      font-size: 19px;
      margin-bottom: 10px;
    }}
    .frontdesk-debate-card ul,
    .daily-digest-panel ul {{
      margin: 0;
      padding-left: 18px;
      color: var(--ink);
      line-height: 1.65;
    }}
    .frontdesk-debate-card li,
    .daily-digest-panel li {{
      margin: 6px 0;
    }}

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
    .debate-round summary {{
      cursor: pointer;
      font-weight: 700;
      color: var(--amber);
      list-style-position: inside;
    }}
    .debate-round[open] summary {{
      margin-bottom: 12px;
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
      .aqsp-static-two-column {{
        grid-template-columns: 1fr;
        padding: 0 14px 20px;
      }}
      .static-rail-card {{
        position: relative;
        top: auto;
      }}
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
        <h1>短线决策看板</h1>
        <p class="sub">{safe_title} · 生成时间 {generated_at}。仅供研究复核 / 不连接券商 / 不触发真实委托。</p>
        <p class="sub">离线归档 · 非生产首页；当前生产入口为 AQSP {html.escape(public_dashboard_url())}。</p>
        <div class="meta">
          <span class="pill">最新信号日 {latest_date}</span>
          <span class="pill">候选数据日 {display_date}</span>
          <span class="pill">候选来源 {candidate_source_label}</span>
          <span class="pill">阈值版本 {thresholds_version}</span>
          <span class="pill">候选数 {len(candidates)}</span>
          <span class="pill">通知级别 {notify_level}</span>
          <span class="pill">数据源 {source_health_label}</span>
        </div>
      </div>
      <a href="#agent-discussion" style="text-decoration:none;background:rgba(31,122,77,.12);border:1px solid rgba(31,122,77,.25);color:var(--green);padding:10px 16px;border-radius:12px;font-weight:600;display:inline-flex;align-items:center;gap:8px;transition:all .3s;">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="3"/>
          <path d="M12 1v6m0 6v6m4.22-13.22l-4.24 4.24m0 4.24l4.24 4.24m-12.44-8.48l4.24 4.24m0 4.24l-4.24 4.24"/>
        </svg>
        Agent讨论
      </a>
    </div>
  </header>
  {warning_html}
  {frontdesk_html}
  {supporting_html}
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
          showToast('结论详情已复制到剪贴板');
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
    daily_digest_path: str | Path = "reports/daily_digest.md",
    title: str = "AQSP 量化选股面板",
) -> str:
    if candidates is None:
        candidates = read_preferred_candidates(
            Path("reports/latest.csv"),
            intraday_csv_path=Path("reports/intraday_latest.csv"),
        ).candidates

    rows = read_ledger_rows(Path(ledger_path))
    paper_rows = read_paper_rows(Path(paper_ledger_path))
    debate_map = read_debate_results(Path(debate_path))
    research_summary = load_research_summary()
    daily_digest_markdown = read_daily_digest(Path(daily_digest_path))

    base_html = render_dashboard(
        candidates,
        rows,
        title,
        paper_rows,
        research_summary,
        debate_map,
        source_health_path,
        daily_digest_markdown,
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
    parser.add_argument("--csv", default="reports/latest.csv")
    parser.add_argument("--intraday-csv", default="reports/intraday_latest.csv")
    parser.add_argument("--ledger", default="data/predictions.jsonl")
    parser.add_argument("--paper-ledger", default="data/paper_trades.jsonl")
    parser.add_argument("--debate", default="data/debate_results.jsonl")
    parser.add_argument("--daily-digest", default="reports/daily_digest.md")
    parser.add_argument("--source-health", default="data/source_health.json")
    parser.add_argument("--output", default="dist/dashboard/index.html")
    parser.add_argument("--title", default="AQSP 量化选股面板")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    selection = read_preferred_candidates(
        Path(args.csv),
        intraday_csv_path=Path(args.intraday_csv),
    )
    html_text = render_all_panels(
        selection.candidates,
        args.ledger,
        args.paper_ledger,
        args.debate,
        args.source_health,
        args.daily_digest,
        args.title,
    )
    write_dashboard_artifact(output, html_text)
    print(f"dashboard={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
