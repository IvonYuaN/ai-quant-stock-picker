from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from aqsp.data.registry import get_registry_entry
from aqsp.research.repo_intake import (
    build_repo_backlog,
    load_repo_intake,
    summarize_repo_intake,
)


@dataclass(frozen=True)
class ResearchPipelineSummary:
    pipeline: str
    total: int
    p1: int
    top_repo: str


@dataclass(frozen=True)
class ResearchFamilySummary:
    family_id: str
    name: str
    status: str
    runtime_stage: str
    absorbed_from_count: int
    runtime_gate_count: int


@dataclass(frozen=True)
class ResearchSourceSummary:
    source_id: str
    name: str
    research_status: str
    runtime_ready: bool
    adoption_gate_count: int
    absorbed_from_count: int


@dataclass(frozen=True)
class ResearchActionItem:
    kind: str
    item_id: str
    name: str
    stage: str
    priority: str
    blocker: str
    reference_hint: str


@dataclass(frozen=True)
class ResearchPrereqItem:
    kind: str
    item_id: str
    name: str
    status: str
    missing_env_vars: tuple[str, ...]
    fixture_hints: tuple[str, ...]
    user_action: str
    code_action: str
    registry_runtime_ready: bool | None


@dataclass(frozen=True)
class ResearchRepoLaneSummary:
    lane: str
    count: int


@dataclass(frozen=True)
class ResearchRepoBacklogItem:
    repo: str
    lane: str
    priority: str
    landing: str
    next_action: str
    url: str


@dataclass(frozen=True)
class ResearchSummary:
    generated_at: str
    total_findings: int
    pipeline_summaries: tuple[ResearchPipelineSummary, ...]
    absorbed_families: tuple[ResearchFamilySummary, ...]
    source_candidates: tuple[ResearchSourceSummary, ...]
    next_actions: tuple[ResearchActionItem, ...]
    prereq_items: tuple[ResearchPrereqItem, ...]
    implemented_family_count: int
    report_only_family_count: int
    gated_family_count: int
    repo_intake_total: int = 0
    repo_substrate_candidate_count: int = 0
    repo_reject_boundary_count: int = 0
    repo_report_only_count: int = 0
    repo_lane_summaries: tuple[ResearchRepoLaneSummary, ...] = ()
    repo_backlog: tuple[ResearchRepoBacklogItem, ...] = ()


def research_findings_display(summary: ResearchSummary) -> str:
    if summary.total_findings > 0:
        return f"{summary.total_findings} 条"
    if (
        summary.absorbed_families
        or summary.next_actions
        or summary.source_candidates
        or summary.repo_intake_total > 0
    ):
        return "未落盘（按配置吸收队列展示）"
    return "暂无"


def research_findings_metric(summary: ResearchSummary) -> str:
    if summary.total_findings > 0:
        return str(summary.total_findings)
    if (
        summary.absorbed_families
        or summary.next_actions
        or summary.source_candidates
        or summary.repo_intake_total > 0
    ):
        return "配置队列"
    return "-"


def research_findings_badge(summary: ResearchSummary) -> str:
    if summary.total_findings > 0:
        return f"{summary.total_findings} findings"
    if (
        summary.absorbed_families
        or summary.next_actions
        or summary.source_candidates
        or summary.repo_intake_total > 0
    ):
        return "config-backed"
    return "empty"


def load_research_summary(
    absorption_path: str | Path = "docs/research_absorption.json",
    strategy_sources_path: str | Path = "config/strategy_sources.yaml",
    data_sources_path: str | Path = "config/data_sources.yaml",
    repo_intake_paths: tuple[str | Path, ...] = (
        "docs/research/repo_radar_raw.json",
        "_external/archive/repo-scout-2026-06-04/recent_repos_manifest_2026-06-04.json",
    ),
) -> ResearchSummary | None:
    absorption_file = Path(absorption_path)
    strategy_sources_file = Path(strategy_sources_path)
    data_sources_file = Path(data_sources_path)
    if not strategy_sources_file.exists() or not data_sources_file.exists():
        return None

    findings = _load_json(absorption_file) if absorption_file.exists() else []
    strategy_config = _load_yaml(strategy_sources_file)
    data_config = _load_yaml(data_sources_file)
    if (
        not isinstance(findings, list)
        or not isinstance(strategy_config, dict)
        or not isinstance(data_config, dict)
    ):
        return None

    pipeline_order = [
        "data_source",
        "strategy",
        "timing",
        "execution_risk",
        "ai_research",
    ]
    pipeline_summaries = []
    for pipeline in pipeline_order:
        scoped = [
            item for item in findings if str(item.get("pipeline", "") or "") == pipeline
        ]
        if not scoped:
            continue
        top_repo = str(scoped[0].get("full_name", "") or "")
        pipeline_summaries.append(
            ResearchPipelineSummary(
                pipeline=pipeline,
                total=len(scoped),
                p1=sum(
                    1 for item in scoped if str(item.get("priority", "") or "") == "P1"
                ),
                top_repo=top_repo,
            )
        )

    absorbed_families = []
    implemented_family_count = 0
    report_only_family_count = 0
    gated_family_count = 0
    for family in strategy_config.get("families", []):
        if not isinstance(family, dict):
            continue
        status = str(family.get("current_status", "") or "")
        if status == "implemented_partial":
            implemented_family_count += 1
        if status != "research_absorbed":
            continue
        runtime_stage = _runtime_stage_for_family(family)
        if runtime_stage == "report_only":
            report_only_family_count += 1
        else:
            gated_family_count += 1
        absorbed_families.append(
            ResearchFamilySummary(
                family_id=str(family.get("id", "") or ""),
                name=str(family.get("name", "") or ""),
                status=status,
                runtime_stage=runtime_stage,
                absorbed_from_count=len(family.get("absorbed_from", []) or []),
                runtime_gate_count=len(family.get("runtime_gate", []) or []),
            )
        )

    source_candidates = []
    for source in data_config.get("sources", []):
        if not isinstance(source, dict):
            continue
        if bool(source.get("runtime_ready", False)):
            continue
        source_candidates.append(
            ResearchSourceSummary(
                source_id=str(source.get("id", "") or ""),
                name=str(source.get("name", "") or ""),
                research_status=str(source.get("research_status", "") or ""),
                runtime_ready=bool(source.get("runtime_ready", False)),
                adoption_gate_count=len(source.get("adoption_gate", []) or []),
                absorbed_from_count=len(source.get("absorbed_from", []) or []),
            )
        )

    next_actions = _build_next_actions(strategy_config, data_config)
    prereq_items = _build_prereq_items(next_actions)
    repo_intake = _load_repo_intake_summary(repo_intake_paths)

    generated_at = ""
    if findings and isinstance(findings[0], dict):
        generated_at = str(findings[0].get("generated_at", "") or "")

    return ResearchSummary(
        generated_at=generated_at,
        total_findings=len(findings),
        pipeline_summaries=tuple(pipeline_summaries),
        absorbed_families=tuple(absorbed_families),
        source_candidates=tuple(source_candidates),
        next_actions=tuple(next_actions),
        prereq_items=tuple(prereq_items),
        implemented_family_count=implemented_family_count,
        report_only_family_count=report_only_family_count,
        gated_family_count=gated_family_count,
        repo_intake_total=repo_intake["total"],
        repo_substrate_candidate_count=repo_intake["substrate_candidate"],
        repo_reject_boundary_count=repo_intake["reject_boundary"],
        repo_report_only_count=repo_intake["report_only"],
        repo_lane_summaries=repo_intake["lane_summaries"],
        repo_backlog=repo_intake["backlog"],
    )


def _load_repo_intake_summary(
    repo_intake_paths: tuple[str | Path, ...],
) -> dict[str, Any]:
    existing_paths = tuple(
        Path(path) for path in repo_intake_paths if Path(path).exists()
    )
    if not existing_paths:
        return {
            "total": 0,
            "substrate_candidate": 0,
            "reject_boundary": 0,
            "report_only": 0,
            "lane_summaries": (),
            "backlog": (),
        }
    items = load_repo_intake(existing_paths)
    summary = summarize_repo_intake(items)
    backlog = build_repo_backlog(items, limit_per_lane=3)
    return {
        "total": summary.total,
        "substrate_candidate": summary.stage_counts.get("substrate_candidate", 0),
        "reject_boundary": summary.stage_counts.get("reject_boundary", 0),
        "report_only": summary.stage_counts.get("report_only", 0),
        "lane_summaries": tuple(
            ResearchRepoLaneSummary(lane=lane, count=count)
            for lane, count in summary.lane_counts.items()
        ),
        "backlog": tuple(
            ResearchRepoBacklogItem(
                repo=item.repo,
                lane=item.lane,
                priority=item.priority,
                landing=item.landing,
                next_action=item.next_action,
                url=item.url,
            )
            for item in backlog
        ),
    }


def _runtime_stage_for_family(family: dict[str, Any]) -> str:
    gates = [
        str(item).lower()
        for item in (family.get("runtime_gate", []) or [])
        if str(item).strip()
    ]
    report_only_markers = (
        "report-only",
        "不参与总分",
        "不直接生成买入信号",
        "不直接进入评分",
        "不允许写入 pickresult.score",
        "shadow mode",
        "只增强 ledger",
        "只做候选和解释",
    )
    if any(marker in gate for gate in gates for marker in report_only_markers):
        return "report_only"
    return "gated_runtime"


def _build_next_actions(
    strategy_config: dict[str, Any],
    data_config: dict[str, Any],
) -> list[ResearchActionItem]:
    actions: list[ResearchActionItem] = []
    for family in strategy_config.get("families", []):
        if not isinstance(family, dict):
            continue
        if str(family.get("current_status", "") or "") != "research_absorbed":
            continue
        stage = _runtime_stage_for_family(family)
        actions.append(
            ResearchActionItem(
                kind="strategy",
                item_id=str(family.get("id", "") or ""),
                name=str(family.get("name", "") or ""),
                stage=stage,
                priority="P1" if stage == "report_only" else "P2",
                blocker=_first_text(
                    family.get("runtime_gate", [])
                    or family.get("validation_required", [])
                ),
                reference_hint=_first_text(
                    family.get("absorbed_from", []) or family.get("references", [])
                ),
            )
        )
    for source in data_config.get("sources", []):
        if not isinstance(source, dict):
            continue
        if bool(source.get("runtime_ready", False)):
            continue
        research_status = str(source.get("research_status", "") or "")
        if research_status == "future_optional":
            priority = "P4"
        elif research_status in {"research_candidate"}:
            priority = "P3"
        else:
            priority = "P1" if research_status == "next_adapter" else "P2"
        actions.append(
            ResearchActionItem(
                kind="data_source",
                item_id=str(source.get("id", "") or ""),
                name=str(source.get("name", "") or ""),
                stage=research_status or "candidate",
                priority=priority,
                blocker=_first_text(source.get("adoption_gate", [])),
                reference_hint=_first_text(
                    source.get("absorbed_from", []) or [source.get("reference", "")]
                ),
            )
        )
    return sorted(actions, key=_action_sort_key)


def _build_prereq_items(
    actions: list[ResearchActionItem],
) -> list[ResearchPrereqItem]:
    items: list[ResearchPrereqItem] = []
    for action in actions:
        spec = _PREREQ_SPECS.get((action.kind, action.item_id))
        if spec is None:
            continue
        env_vars = tuple(spec.get("env_vars", ()))
        missing_env_vars = tuple(
            env_name for env_name in env_vars if not os.getenv(env_name, "").strip()
        )
        registry_entry = (
            get_registry_entry(action.item_id) if action.kind == "data_source" else None
        )
        status = "ready"
        if missing_env_vars:
            status = "needs_env"
        elif spec.get("fixture_hints"):
            status = "needs_fixture"
        items.append(
            ResearchPrereqItem(
                kind=action.kind,
                item_id=action.item_id,
                name=action.name,
                status=status,
                missing_env_vars=missing_env_vars,
                fixture_hints=tuple(spec.get("fixture_hints", ())),
                user_action=str(spec.get("user_action", "") or ""),
                code_action=str(spec.get("code_action", "") or ""),
                registry_runtime_ready=(
                    None if registry_entry is None else registry_entry.runtime_ready
                ),
            )
        )
    return items


def _first_text(items: list[Any]) -> str:
    for item in items:
        text = str(item).strip()
        if text:
            return text
    return ""


def _action_sort_key(item: ResearchActionItem) -> tuple[int, int, str]:
    priority_rank = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}.get(item.priority, 9)
    kind_rank = 0 if item.kind == "data_source" else 1
    return (priority_rank, kind_rank, item.item_id)


_PREREQ_SPECS: dict[tuple[str, str], dict[str, object]] = {
    ("data_source", "tushare"): {
        "env_vars": ("TUSHARE_TOKEN",),
        "fixture_hints": (
            "tests/fixtures/tushare_trade_calendar.json",
            "tests/fixtures/tushare_index_weight.json",
            "tests/fixtures/tushare_disclosure_date.json",
        ),
        "user_action": "在本地 .env 或 GitHub Actions Secrets 配置 TUSHARE_TOKEN。",
        "code_action": "先接交易日历、指数成分、财报披露日，并补 PIT fixture。",
    },
    ("data_source", "efinance"): {
        "env_vars": (),
        "fixture_hints": (
            "tests/fixtures/efinance_quote.json",
            "tests/fixtures/efinance_fund_flow.json",
        ),
        "user_action": "无需注册；允许我继续用公开接口做字段回归。",
        "code_action": "先接资金流和东方财富补充字段，补 schema fixture。",
    },
    ("data_source", "baostock"): {
        "env_vars": (),
        "fixture_hints": (
            "tests/fixtures/baostock_daily.csv",
            "tests/fixtures/baostock_profit.csv",
        ),
        "user_action": "通常无需额外 key；若后续接口策略变化再补本地登录验证。",
        "code_action": "把历史日线和财务 adapter 拆开，失败必须抛 DataError。",
    },
    ("strategy", "market_regime_timing_filter"): {
        "env_vars": (),
        "fixture_hints": ("tests/fixtures/regime_index_breadth.csv",),
        "user_action": "无需额外账号。",
        "code_action": "先做 regime detector v2，只改变过滤标签，不改总分。",
    },
    ("strategy", "multi_factor_quality_value_momentum"): {
        "env_vars": ("TUSHARE_TOKEN",),
        "fixture_hints": ("tests/fixtures/pit_factor_panel.csv",),
        "user_action": "需要 TUSHARE_TOKEN 支撑 PIT 财报/成分数据。",
        "code_action": "先做 report-only 因子面板，不进入 runtime scoring。",
    },
}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        return None
