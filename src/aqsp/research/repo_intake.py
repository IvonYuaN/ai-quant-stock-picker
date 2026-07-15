from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RepoIntakeItem:
    full_name: str
    url: str
    description: str
    language: str
    stars: int
    updated_at: str
    lane: str
    stage: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RepoIntakeSummary:
    total: int
    lane_counts: dict[str, int]
    stage_counts: dict[str, int]
    top_candidates: tuple[RepoIntakeItem, ...]


@dataclass(frozen=True)
class RepoBacklogItem:
    repo: str
    lane: str
    priority: str
    stage: str
    landing: str
    next_action: str
    reason: str
    stars: int
    url: str


def load_repo_intake(paths: tuple[str | Path, ...]) -> tuple[RepoIntakeItem, ...]:
    merged: dict[str, RepoIntakeItem] = {}
    for path in paths:
        source_path = Path(path)
        if not source_path.exists():
            continue
        for raw in _load_raw_repos(source_path):
            item = classify_repo(raw)
            existing = merged.get(item.full_name)
            if existing is None or item.stars > existing.stars:
                merged[item.full_name] = item
    return tuple(
        sorted(
            merged.values(),
            key=lambda item: (item.stage != "substrate_candidate", -item.stars),
        )
    )


def classify_repo(raw: dict[str, Any]) -> RepoIntakeItem:
    full_name = str(raw.get("fullName") or raw.get("full_name") or "").strip()
    description = str(raw.get("description", "") or "").strip()
    text = f"{full_name} {description}".lower()
    lane = _classify_lane(text)
    stage, reasons = _classify_stage(text, lane)
    return RepoIntakeItem(
        full_name=full_name,
        url=str(raw.get("url") or raw.get("html_url") or "").strip(),
        description=description,
        language=str(raw.get("language", "") or "").strip(),
        stars=int(raw.get("stargazersCount") or raw.get("stargazers_count") or 0),
        updated_at=str(raw.get("updatedAt") or raw.get("pushed_at") or "").strip(),
        lane=lane,
        stage=stage,
        reasons=reasons,
    )


def summarize_repo_intake(
    items: tuple[RepoIntakeItem, ...],
    *,
    top_n: int = 12,
) -> RepoIntakeSummary:
    lane_counts = Counter(item.lane for item in items)
    stage_counts = Counter(item.stage for item in items)
    top_candidates = tuple(
        sorted(
            (item for item in items if item.stage == "substrate_candidate"),
            key=lambda item: (-item.stars, item.full_name),
        )[:top_n]
    )
    return RepoIntakeSummary(
        total=len(items),
        lane_counts=dict(sorted(lane_counts.items())),
        stage_counts=dict(sorted(stage_counts.items())),
        top_candidates=top_candidates,
    )


def build_repo_backlog(
    items: tuple[RepoIntakeItem, ...],
    *,
    limit_per_lane: int = 5,
) -> tuple[RepoBacklogItem, ...]:
    backlog: list[RepoBacklogItem] = []
    lane_counts: Counter[str] = Counter()
    for item in sorted(items, key=lambda value: (-value.stars, value.full_name)):
        if item.stage != "substrate_candidate":
            continue
        if lane_counts[item.lane] >= limit_per_lane:
            continue
        lane_counts[item.lane] += 1
        backlog.append(_backlog_item(item))
    return tuple(
        sorted(
            backlog,
            key=lambda item: (_priority_order(item.priority), item.lane, -item.stars),
        )
    )


def render_repo_backlog_markdown(
    items: tuple[RepoIntakeItem, ...],
    *,
    limit_per_lane: int = 5,
) -> str:
    summary = summarize_repo_intake(items)
    backlog = build_repo_backlog(items, limit_per_lane=limit_per_lane)
    lines = [
        "# Repo Intake Backlog",
        "",
        f"- total: {summary.total}",
        f"- substrate_candidate: {summary.stage_counts.get('substrate_candidate', 0)}",
        f"- reject_boundary: {summary.stage_counts.get('reject_boundary', 0)}",
        f"- report_only: {summary.stage_counts.get('report_only', 0)}",
        "",
        "## Lane Counts",
        "",
    ]
    for lane, count in summary.lane_counts.items():
        lines.append(f"- {lane}: {count}")
    lines.extend(
        [
            "",
            "## Backlog",
            "",
            "| Priority | Lane | Repo | Landing | Next Action |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item in backlog:
        repo_link = f"[{item.repo}]({item.url})" if item.url else item.repo
        lines.append(
            f"| {item.priority} | {item.lane} | {repo_link} | {item.landing} | {item.next_action} |"
        )
    return "\n".join(lines) + "\n"


def _load_raw_repos(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        repos: list[dict[str, Any]] = []
        for value in data.values():
            if isinstance(value, list):
                repos.extend(item for item in value if isinstance(item, dict))
        return repos
    raise ValueError(f"repo intake file must contain a list or mapping: {path}")


def _classify_lane(text: str) -> str:
    if _has_any(text, ("freqtrade", "crypto", "binance", "broker", "execution")):
        return "execution_boundary"
    if _has_any(text, ("data", "openbb", "akshare", "tushare", "yfinance")):
        return "data_source"
    if _has_any(text, ("backtest", "zipline", "backtrader", "vectorbt", "lean")):
        return "backtest_validation"
    if _has_any(text, ("portfolio", "risk", "allocation", "optimization", "cvar")):
        return "portfolio_risk"
    if _has_any(text, ("factor", "alpha", "qlib", "machine learning", "finrl", "ml")):
        return "factor_sandbox"
    if _has_any(text, ("agent", "llm", "news", "dashboard", "notification")):
        return "agent_context"
    if _has_any(text, ("screener", "selector", "stock", "indicator", "technical")):
        return "screening_strategy"
    return "research_reference"


def _classify_stage(text: str, lane: str) -> tuple[str, tuple[str, ...]]:
    if lane == "execution_boundary":
        return (
            "reject_boundary",
            ("交易执行/券商/crypto 自动交易能力不进入 AQSP runtime",),
        )
    if _has_any(text, ("bot", "live trading", "automated trading", "place order")):
        return (
            "reject_boundary",
            ("自动交易语义只可作反例或执行边界参考",),
        )
    if lane in {
        "data_source",
        "backtest_validation",
        "portfolio_risk",
        "factor_sandbox",
        "agent_context",
        "screening_strategy",
    }:
        return ("substrate_candidate", (_stage_reason(lane),))
    return ("report_only", ("只保留为研究参考，不进入主链",))


def _stage_reason(lane: str) -> str:
    return {
        "data_source": "可沉淀为数据源目录、字段映射、freshness/schema gate",
        "backtest_validation": "可沉淀为 walk-forward、成本、PIT 和防泄漏校验",
        "portfolio_risk": "可沉淀为组合风险、集中度、相关性和报告指标",
        "factor_sandbox": "可沉淀为因子沙箱、表达式、IC/RankIC 和 shadow mode",
        "agent_context": "可沉淀为通知、上下文卡、产物追溯和多 Agent 结构化输出",
        "screening_strategy": "可沉淀为策略目录、假设、验证门槛和 report-only 对照",
    }[lane]


def _backlog_item(item: RepoIntakeItem) -> RepoBacklogItem:
    priority = _priority(item)
    landing, next_action = _landing_and_action(item.lane)
    reason = item.reasons[0] if item.reasons else _stage_reason(item.lane)
    return RepoBacklogItem(
        repo=item.full_name,
        lane=item.lane,
        priority=priority,
        stage=item.stage,
        landing=landing,
        next_action=next_action,
        reason=reason,
        stars=item.stars,
        url=item.url,
    )


def _priority(item: RepoIntakeItem) -> str:
    if item.stars >= 10000 and item.lane in {
        "data_source",
        "backtest_validation",
        "factor_sandbox",
        "agent_context",
    }:
        return "P1"
    if item.stars >= 5000:
        return "P2"
    return "P3"


def _priority_order(priority: str) -> int:
    return {"P1": 0, "P2": 1, "P3": 2}.get(priority, 9)


def _landing_and_action(lane: str) -> tuple[str, str]:
    return {
        "data_source": (
            "config/data_sources.yaml + aqsp.data.source_catalog",
            "抽取字段/schema/freshness gate，不直接引入重依赖",
        ),
        "backtest_validation": (
            "backtest/walkforward guardrail",
            "抽取成本、PIT、防泄漏和窗口验证规则",
        ),
        "portfolio_risk": (
            "portfolio/risk report-only metrics",
            "抽取集中度、相关性、回撤和风险归因指标",
        ),
        "factor_sandbox": (
            "aqsp.research.factor_expression + factor backtest",
            "只进 shadow/report-only，补字段依赖和 IC 验证",
        ),
        "agent_context": (
            "briefing/context/artifact metadata",
            "抽取结构化输出、通知编排和证据追溯",
        ),
        "screening_strategy": (
            "config/strategy_sources.yaml",
            "登记假设、信号、验证门槛，不直接入分",
        ),
    }[lane]


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
