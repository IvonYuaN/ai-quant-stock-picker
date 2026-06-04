from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from aqsp.config import (
    load_debate_runtime_config,
    load_runtime_config,
    online_fallback_allowed,
)
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.core.types import RunMetadata
from aqsp.data.registry import (
    local_data_status,
    registry_entry_dict,
    sort_registry_entries,
    workload_fit_label,
)
from aqsp.data.registry import get_registry_entry
from aqsp.data.source_readiness import inspect_source_readiness
from aqsp.data.source_health import (
    describe_source_health,
    prioritize_source_ids,
    read_source_health,
    record_source_failure,
    record_source_success,
)
from aqsp.data import fetch_akshare, load_csv, fetch_with_source
from aqsp.data.index_constituents import load_optional_index_constituents
from aqsp.data.akshare_source import AkshareSource
from aqsp.data.cache import DataCache
from aqsp.data.eastmoney_source import EastmoneySource
from aqsp.data.efinance_source import EfinanceSource
from aqsp.data.mootdx_source import MootdxSource
from aqsp.data.multi_source import MultiSource, SourceFactory
from aqsp.data.sina_source import SinaSource
from aqsp.data.tencent_source import TencentSource
from aqsp.data.tushare_pit import TusharePitClient
from aqsp.data.baostock_source import BaostockSource
from aqsp.data.sqlite_db_source import SqliteDbSource
from aqsp.data.tdx_vipdoc_source import TdxVipdocSource
from aqsp.filters_lethal.pipeline import LethalFilterPipeline
from aqsp.freshness import assert_fresh_data, latest_trade_date
from aqsp.ledger import (
    ExecutionConfig,
    append_predictions,
    strategy_weights_from_ledger,
    validate_predictions,
)
from aqsp.models import ScreeningConfig
from aqsp.notify_templates import (
    build_daily_run_notification,
    build_closing_premium_notification,
    build_closing_review_notification,
    build_morning_breakout_notification,
)
from aqsp.notifier import notify_markdown
from aqsp.research.summary import load_research_summary
from aqsp.research_engine import (
    ENGINE_CHOICES,
    WalkForwardEngineConfig,
    resolve_walkforward_engine,
)
from aqsp.regime.detector import RegimeDetector
from aqsp.report import to_dataframe, to_markdown
from aqsp.risk.circuit_breaker import CircuitBreaker
from aqsp.strategy import screen_universe
from aqsp.strategies.thresholds import load_thresholds
from aqsp.universe import DEFAULT_SYMBOLS
from aqsp.briefing.debate import (
    AShareDebateCoordinator,
    DebateResult,
    parse_agent_roles,
)
from aqsp.models import PickResult
from aqsp.presentation import has_meaningful_name


def serialize_debate_result(result: DebateResult) -> dict:
    """将辩论结果序列化为可JSON化的字典"""
    return {
        "debate_id": result.debate_id,
        "symbol": result.symbol,
        "name": result.name,
        "original_score": result.original_score,
        "rating": result.rating,
        "rounds": [
            {
                "round_num": r.round_num,
                "summary": r.summary,
                "opinions": [
                    {
                        "agent_id": o.agent_id,
                        "role": o.role.value,
                        "stance": o.stance,
                        "confidence": o.confidence,
                        "arguments": o.arguments,
                        "counterarguments": o.counterarguments,
                        "risk_factors": o.risk_factors,
                        "opportunity_factors": o.opportunity_factors,
                        "final_position": o.final_position,
                    }
                    for o in r.opinions
                ],
                "cross_opinions": r.cross_opinions,
            }
            for r in result.rounds
        ],
        "final_consensus": result.final_consensus,
        "final_vote": {k.value: v for k, v in result.final_vote.items()},
        "disagreement_score": result.disagreement_score,
        "adjustment_weight": result.adjustment_weight,
        "adjusted_score": result.adjusted_score,
        "recommended_adjustment": result.recommended_adjustment,
        "adjustment_reason": result.adjustment_reason,
        "risk_warnings": result.risk_warnings,
        "opportunity_highlights": result.opportunity_highlights,
        "thresholds_version": result.thresholds_version,
        "regime": result.regime,
        "data_source": result.data_source,
        "related_signal_date": result.related_signal_date,
        "agent_performance_snapshot": {
            k: v.to_dict() for k, v in result.agent_performance_snapshot.items()
        },
    }


SOURCE_CHOICES = [
    "auto",
    "local_first",
    "online_first",
    "multi",
    "akshare",
    "sina",
    "eastmoney",
    "tencent",
    "mootdx",
    "baostock",
    "efinance",
    "sqlite_db",
    "tdx_vipdoc",
]
WALKFORWARD_SOURCE_CHOICES = [
    "multi",
    "akshare",
    "mootdx",
    "sina",
    "eastmoney",
    "tencent",
    "baostock",
    "sqlite_db",
]
# 宪法 §1.3 #9：held-out 区间（2025-01~2026-04）绝对禁止用于训练
HELDOUT_TRAIN_CUTOFF = "2024-12-31"
# 宪法 §1.3 #12/#14：双门 gate 的 sidecar 文件
WALKFORWARD_GATE_PATH = "data/walkforward_gate.json"
# 冷启动期最低独立信号日。宪法 §1.3 #7/#14 明确要求 30 个独立信号日。
# 可用环境变量 AQSP_COLD_START_MIN_DAYS 覆盖（仅供测试加速，生产须为 30）。
COLD_START_MIN_DAYS = int(os.getenv("AQSP_COLD_START_MIN_DAYS", "30"))


def _extract_meaningful_name_from_frame(frame: pd.DataFrame, symbol: str) -> str:
    if frame.empty or "name" not in frame.columns:
        return ""
    names = (
        frame["name"]
        .dropna()
        .astype(str)
        .map(str.strip)
        .loc[lambda series: (series != "") & (series != symbol)]
    )
    return str(names.iloc[-1]) if not names.empty else ""


def _load_optional_symbol_name_map(symbols: list[str]) -> dict[str, str]:
    if not symbols:
        return {}
    try:
        source = SqliteDbSource()
    except Exception:
        return {}

    name_map: dict[str, str] = {}
    for symbol in symbols:
        name = str(source.get_symbol_name(symbol)).strip()
        if has_meaningful_name(symbol, name):
            name_map[symbol] = name
    return name_map


def _enrich_pick_names(
    picks: list[PickResult],
    frames: dict[str, pd.DataFrame] | None = None,
) -> list[PickResult]:
    if not picks:
        return picks

    name_map: dict[str, str] = {}
    if frames:
        for symbol, frame in frames.items():
            name = _extract_meaningful_name_from_frame(frame, symbol)
            if name:
                name_map[symbol] = name

    missing_symbols = [
        pick.symbol
        for pick in picks
        if not has_meaningful_name(pick.symbol, pick.name)
        and pick.symbol not in name_map
    ]
    if missing_symbols:
        name_map.update(_load_optional_symbol_name_map(missing_symbols))

    enriched: list[PickResult] = []
    for pick in picks:
        if has_meaningful_name(pick.symbol, pick.name):
            enriched.append(pick)
            continue
        resolved_name = name_map.get(pick.symbol, "").strip()
        if not has_meaningful_name(pick.symbol, resolved_name):
            enriched.append(pick)
            continue
        enriched.append(replace(pick, name=resolved_name))
    return enriched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aqsp")
    sub = parser.add_subparsers(dest="command", required=True)

    screen = sub.add_parser("screen", help="screen stock candidates")
    screen.add_argument("--mode", choices=["open", "close"], default="close")
    screen.add_argument("--symbols", default="", help="comma separated A-share symbols")
    screen.add_argument("--csv", default="", help="local OHLCV csv path")
    screen.add_argument(
        "--source",
        choices=SOURCE_CHOICES,
        default="auto",
        help="data source",
    )
    screen.add_argument("--limit", type=int, default=20)
    screen.add_argument("--min-avg-amount", type=float, default=50_000_000)
    screen.add_argument("--report", default="", help="write markdown report")
    screen.add_argument("--output-csv", default="", help="write result csv")
    screen.add_argument("--benchmark-symbol", default="000300")
    screen.add_argument(
        "--pool",
        type=str,
        default="",
        help="标的池: sh300, zz500, zz1000, cyb, zxb, all",
    )

    run = sub.add_parser(
        "run", help="scheduled screen with freshness check and optional notification"
    )
    run.add_argument("--mode", choices=["open", "close"], default="")
    run.add_argument("--symbols", default="")
    run.add_argument("--csv", default="")
    run.add_argument(
        "--source",
        choices=SOURCE_CHOICES,
        default="auto",
        help="data source",
    )
    run.add_argument("--limit", type=int, default=0)
    run.add_argument("--max-universe", type=int, default=0)
    run.add_argument("--min-avg-amount", type=float, default=0)
    run.add_argument("--max-data-lag-days", type=int, default=0)
    run.add_argument("--enable-online-factors", action="store_true")
    run.add_argument("--report", default="reports/latest.md")
    run.add_argument("--output-csv", default="reports/latest.csv")
    run.add_argument("--ledger", default="data/predictions.jsonl")
    run.add_argument("--horizon-days", type=int, default=3)
    run.add_argument("--fee-bps", type=float, default=8.0)
    run.add_argument("--slippage-bps", type=float, default=5.0)
    run.add_argument("--benchmark-symbol", default="000300")
    run.add_argument("--skip-validation", action="store_true")
    run.add_argument("--notify", action="store_true")
    run.add_argument(
        "--pool",
        type=str,
        default="",
        help="标的池: sh300, zz500, zz1000, cyb, zxb, all",
    )
    run.add_argument("--enable-debate", action="store_true", help="启用多Agent辩论分析")

    wf = sub.add_parser("walkforward", help="run walk-forward backtest")
    wf.add_argument(
        "--symbols", default="", help="comma separated symbols (default: HS300)"
    )
    wf.add_argument("--start", default="2018-01-01", help="backtest start date")
    wf.add_argument("--end", default="2024-12-31", help="backtest end date")
    wf.add_argument("--train-days", type=int, default=120)
    wf.add_argument("--test-days", type=int, default=30)
    wf.add_argument("--purge-days", type=int, default=5)
    wf.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="override composite.min_total_score (e.g. 0 to accept all). "
        "If omitted, the value from thresholds.yaml is used.",
    )
    wf.add_argument(
        "--cache-path", default="", help="independent cache path (avoid stale data)"
    )
    wf.add_argument("--log", default="", help="tee output to log file")
    wf.add_argument(
        "--update-yaml",
        action="store_true",
        help="auto-update last_walkforward_run in thresholds.yaml",
    )
    wf.add_argument("--report", default="docs/walkforward-2026-05.md")
    wf.add_argument(
        "--engine",
        choices=ENGINE_CHOICES,
        default="",
        help="研究引擎: auto, builtin, akquant",
    )
    wf.add_argument(
        "--source",
        choices=WALKFORWARD_SOURCE_CHOICES,
        default="sqlite_db",
    )
    wf.add_argument("--horizon-days", type=int, default=None, help="持仓天数 (默认: 3)")
    wf.add_argument(
        "--pool",
        type=str,
        default=None,
        help="标的池: sh300, zz500, zz1000, all (默认: sh300)",
    )
    wf.add_argument(
        "--tiered-stop",
        action="store_true",
        default=False,
        help="启用分级止损（3.1%硬止损+分级减仓）",
    )
    wf.add_argument(
        "--allow-heldout",
        action="store_true",
        default=False,
        help="（危险）显式允许 end 超过 2024-12-31，卷入 held-out 区间。仅用于 held-out 一次性验收，且必须在 walkforward 双门通过后。默认关闭——违反宪法 §1.3 #9 时拒绝运行。",
    )

    dash_cmd = sub.add_parser("dashboard", help="generate interactive dashboard")
    dash_cmd.add_argument("--ledger", default="data/predictions.jsonl")
    dash_cmd.add_argument("--output", default="dist/dashboard/index.html")

    monitor_cmd = sub.add_parser("monitor", help="run monitoring checks")
    monitor_cmd.add_argument("--config", default="config/monitors.yaml")
    monitor_cmd.add_argument("--notify", action="store_true")
    monitor_cmd.add_argument("--notify-critical-only", action="store_true")
    monitor_cmd.add_argument("--dry-run", action="store_true")

    doctor_cmd = sub.add_parser("doctor", help="diagnose server/runtime readiness")
    doctor_cmd.add_argument(
        "--probe-auth",
        action="store_true",
        help="主动探测 baostock / tushare 登录或 token 可用性",
    )
    doctor_cmd.add_argument(
        "--probe-llm",
        action="store_true",
        help="主动探测已配置的 LLM provider 联通性",
    )

    sources_cmd = sub.add_parser(
        "sources", help="show data source readiness and freshness tiers"
    )
    sources_cmd.add_argument("--ready-only", action="store_true")
    sources_cmd.add_argument("--json", action="store_true")
    sources_cmd.add_argument(
        "--probe-auth",
        action="store_true",
        help="主动探测 baostock/tushare 登录或 token 可用性",
    )

    briefing_cmd = sub.add_parser("briefing", help="generate daily briefing")
    briefing_cmd.add_argument("--ledger", default="data/predictions.jsonl")
    briefing_cmd.add_argument("--output", default="reports/briefing.md")
    briefing_cmd.add_argument("--enable-llm", action="store_true")
    briefing_cmd.add_argument("--notify", action="store_true")
    briefing_cmd.add_argument(
        "--email",
        action="store_true",
        help="生成 briefing 后通过邮件发送，配置从 AQSP_SMTP_* 环境变量读",
    )

    research_cmd = sub.add_parser(
        "research", help="show absorbed research runtime stages"
    )
    research_cmd.add_argument("--json", action="store_true")
    research_cmd.add_argument("--next", action="store_true")
    research_cmd.add_argument("--prereqs", action="store_true")

    pit_cmd = sub.add_parser("pit", help="inspect point-in-time data endpoints")
    pit_cmd.add_argument(
        "--kind",
        choices=["trade_calendar", "index_weights", "disclosure_dates"],
        required=True,
    )
    pit_cmd.add_argument("--start", default=today_shanghai().isoformat())
    pit_cmd.add_argument("--end", default=today_shanghai().isoformat())
    pit_cmd.add_argument("--exchange", default="SSE")
    pit_cmd.add_argument("--index-code", default="000300.SH")
    pit_cmd.add_argument("--symbols", default="600519")
    pit_cmd.add_argument("--json", action="store_true")

    compare_cmd = sub.add_parser(
        "compare-snapshots", help="compare stock snapshots between two dates"
    )
    compare_cmd.add_argument(
        "--date1",
        default="",
        help="first date (YYYY-MM-DD, default: yesterday)",
    )
    compare_cmd.add_argument(
        "--date2",
        default="",
        help="second date (YYYY-MM-DD, default: today)",
    )
    compare_cmd.add_argument(
        "--snapshot-path",
        default="data/pick_snapshots.jsonl",
        help="snapshot file path",
    )

    optimize_cmd = sub.add_parser("optimize", help="run parameter optimization")
    optimize_cmd.add_argument(
        "--method", choices=["grid", "bayesian"], default="bayesian"
    )
    optimize_cmd.add_argument("--trials", type=int, default=50)
    optimize_cmd.add_argument("--symbols", default="")
    optimize_cmd.add_argument("--start", default="2020-01-01")
    optimize_cmd.add_argument("--end", default="2024-12-31")
    optimize_cmd.add_argument(
        "--source",
        choices=WALKFORWARD_SOURCE_CHOICES,
        default="sqlite_db",
    )
    optimize_cmd.add_argument(
        "--engine",
        choices=ENGINE_CHOICES,
        default="",
        help="研究引擎: auto, builtin, akquant",
    )
    optimize_cmd.add_argument("--output", default="data/optimization_result.json")
    optimize_cmd.add_argument(
        "--apply",
        action="store_true",
        help="auto-apply best params to thresholds.yaml",
    )

    discover_cmd = sub.add_parser(
        "discover", help="discover new patterns from historical data"
    )
    discover_cmd.add_argument("--ledger", default="data/predictions.jsonl")
    discover_cmd.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    discover_cmd.add_argument("--min-sample", type=int, default=20)
    discover_cmd.add_argument("--min-winrate", type=float, default=0.55)
    discover_cmd.add_argument("--output", default="")
    discover_cmd.add_argument("--report", default="")

    mine_cmd = sub.add_parser("mine-factors", help="auto mine effective factors")
    mine_cmd.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    mine_cmd.add_argument("--min-ic", type=float, default=0.03)
    mine_cmd.add_argument("--min-ir", type=float, default=0.5)
    mine_cmd.add_argument("--output", default="data/mined_factors.json")
    mine_cmd.add_argument("--report", default="")

    evolve_cmd = sub.add_parser("evolve", help="auto evolve strategy parameters")
    evolve_cmd.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    evolve_cmd.add_argument("--config", default="config/evolution_config.yaml")
    evolve_cmd.add_argument(
        "--apply", action="store_true", help="auto-apply evolved params"
    )
    evolve_cmd.add_argument("--output", default="data/evolution_result.json")

    multi_factor_cmd = sub.add_parser(
        "multi-factor", help="run multi-factor rotation strategy"
    )
    multi_factor_cmd.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    multi_factor_cmd.add_argument("--pool", default="sh300")
    multi_factor_cmd.add_argument("--top", type=int, default=10)
    multi_factor_cmd.add_argument("--output", default="")
    multi_factor_cmd.add_argument("--report", default="")

    morning_cmd = sub.add_parser(
        "morning-breakout", help="run morning breakout strategy"
    )
    morning_cmd.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    morning_cmd.add_argument("--symbols", default="")
    morning_cmd.add_argument("--pool", default="sh300")
    morning_cmd.add_argument("--top", type=int, default=5)
    morning_cmd.add_argument("--notify", action="store_true")
    morning_cmd.add_argument("--output", default="")
    morning_cmd.add_argument("--report", default="")
    morning_cmd.add_argument("--ledger", default="data/predictions.jsonl")

    closing_cmd = sub.add_parser("closing-premium", help="run closing premium strategy")
    closing_cmd.add_argument("--source", choices=SOURCE_CHOICES, default="auto")
    closing_cmd.add_argument("--symbols", default="")
    closing_cmd.add_argument("--pool", default="sh300")
    closing_cmd.add_argument("--top", type=int, default=5)
    closing_cmd.add_argument("--notify", action="store_true")
    closing_cmd.add_argument("--output", default="")
    closing_cmd.add_argument("--report", default="")
    closing_cmd.add_argument("--ledger", default="data/predictions.jsonl")

    review_cmd = sub.add_parser("closing-review", help="generate closing review report")
    review_cmd.add_argument("--date", default="")
    review_cmd.add_argument("--weekly", action="store_true")
    review_cmd.add_argument("--notify", action="store_true")
    review_cmd.add_argument("--output", default="")
    review_cmd.add_argument("--report", default="")

    args = parser.parse_args(argv)
    # 宪法启动门：检查不变量，任一失败会直接 SystemExit
    from aqsp._constitution_check import assert_constitution_invariants

    assert_constitution_invariants()
    try:
        if args.command == "screen":
            return run_screen(args)
        if args.command == "run":
            return run_scheduled(args)
        if args.command == "dashboard":
            return run_dashboard(args)
        if args.command == "walkforward":
            return run_walkforward(args)
        if args.command == "briefing":
            return run_briefing(args)
        if args.command == "monitor":
            return run_monitor(args)
        if args.command == "doctor":
            return run_doctor(args)
        if args.command == "sources":
            return run_sources(args)
        if args.command == "research":
            return run_research(args)
        if args.command == "pit":
            return run_pit(args)
        if args.command == "compare-snapshots":
            return run_compare_snapshots(args)
        if args.command == "optimize":
            return run_optimize(args)
        if args.command == "discover":
            return run_discover(args)
        if args.command == "mine-factors":
            return run_mine_factors(args)
        if args.command == "evolve":
            return run_evolve(args)
        if args.command == "multi-factor":
            return run_multi_factor(args)
        if args.command == "morning-breakout":
            return run_morning_breakout(args)
        if args.command == "closing-premium":
            return run_closing_premium(args)
        if args.command == "closing-review":
            return run_closing_review(args)
    except DataError as exc:
        print(f"数据错误: {exc}")
        return 1
    except ValueError as exc:
        print(f"配置错误: {exc}")
        return 1
    return 1


def run_sources(args: argparse.Namespace) -> int:
    entries = sort_registry_entries(ready_only=args.ready_only)
    health = read_source_health()
    source_health = health.get("sources", {})
    if args.json:
        payload = []
        for entry in entries:
            item = registry_entry_dict(entry)
            item["local_status"] = local_data_status(entry)
            stats = source_health.get(entry.id, {})
            readiness = inspect_source_readiness(entry, probe_auth=args.probe_auth)
            item["health_successes"] = int(stats.get("successes", 0))
            item["health_failures"] = int(stats.get("failures", 0))
            item["health_last_success"] = stats.get("last_success", "")
            item["auth_kind"] = readiness.auth_kind
            item["auth_status"] = readiness.auth_status
            item["auth_message"] = readiness.auth_message
            item["auth_checked_at"] = readiness.auth_checked_at
            item["active_probe"] = readiness.active_probe
            item["workload_fit"] = readiness.workload_fit
            payload.append(item)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for entry in entries:
        ready = "yes" if entry.runtime_ready else "no"
        account = "yes" if entry.requires_account else "no"
        stats = source_health.get(entry.id, {})
        readiness = inspect_source_readiness(entry, probe_auth=args.probe_auth)
        profiles = ", ".join(
            f"{name}={workload_fit_label(value)}"
            for name, value in readiness.workload_fit.items()
        )
        print(
            f"- {entry.id}: ready={ready} local={local_data_status(entry)} "
            f"fresh={entry.freshness_tier} cover={entry.coverage_tier} "
            f"daily={'yes' if entry.supports_daily else 'no'} "
            f"intraday={'yes' if entry.supports_intraday else 'no'} "
            f"realtime={'yes' if entry.supports_realtime else 'no'} "
            f"health={int(stats.get('successes', 0))}/{int(stats.get('failures', 0))} "
            f"account={account} auth={readiness.auth_status}"
        )
        print(
            f"  profiles: {profiles} | auth_kind={readiness.auth_kind}"
            + (
                f" | checked_at={readiness.auth_checked_at}"
                if readiness.auth_checked_at
                else ""
            )
        )
        print(f"  auth: {readiness.auth_message}")
        print(f"  uses: {', '.join(entry.default_for)}")
        print(f"  setup: {entry.setup}")
    return 0


def run_doctor(args: argparse.Namespace) -> int:
    from scripts.server_doctor import main as doctor_main

    argv: list[str] = []
    if args.probe_auth:
        argv.append("--probe-auth")
    if args.probe_llm:
        argv.append("--probe-llm")
    return doctor_main(argv)


def run_research(args: argparse.Namespace) -> int:
    summary = load_research_summary()
    if summary is None:
        print("research summary unavailable")
        return 1
    if args.json:
        payload = {
            "total_findings": summary.total_findings,
            "implemented_family_count": summary.implemented_family_count,
            "report_only_family_count": summary.report_only_family_count,
            "gated_family_count": summary.gated_family_count,
            "pipelines": [
                {
                    "pipeline": item.pipeline,
                    "p1": item.p1,
                    "total": item.total,
                    "top_repo": item.top_repo,
                }
                for item in summary.pipeline_summaries
            ],
            "families": [
                {
                    "family_id": item.family_id,
                    "name": item.name,
                    "runtime_stage": item.runtime_stage,
                    "absorbed_from_count": item.absorbed_from_count,
                    "runtime_gate_count": item.runtime_gate_count,
                }
                for item in summary.absorbed_families
            ],
            "source_candidates": [
                {
                    "source_id": item.source_id,
                    "name": item.name,
                    "research_status": item.research_status,
                    "adoption_gate_count": item.adoption_gate_count,
                    "absorbed_from_count": item.absorbed_from_count,
                }
                for item in summary.source_candidates
            ],
            "next_actions": [
                {
                    "kind": item.kind,
                    "item_id": item.item_id,
                    "name": item.name,
                    "stage": item.stage,
                    "priority": item.priority,
                    "blocker": item.blocker,
                    "reference_hint": item.reference_hint,
                }
                for item in summary.next_actions
            ],
            "prereq_items": [
                {
                    "kind": item.kind,
                    "item_id": item.item_id,
                    "name": item.name,
                    "status": item.status,
                    "missing_env_vars": list(item.missing_env_vars),
                    "fixture_hints": list(item.fixture_hints),
                    "user_action": item.user_action,
                    "code_action": item.code_action,
                    "registry_runtime_ready": item.registry_runtime_ready,
                }
                for item in summary.prereq_items
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.next:
        for item in summary.next_actions[:8]:
            print(
                f"- {item.priority} {item.kind} {item.item_id}: "
                f"stage={item.stage} blocker={item.blocker or '-'} "
                f"ref={item.reference_hint or '-'}"
            )
        return 0

    if args.prereqs:
        for item in summary.prereq_items:
            missing_env = ",".join(item.missing_env_vars) or "-"
            fixtures = ",".join(item.fixture_hints) or "-"
            registry_ready = (
                "-"
                if item.registry_runtime_ready is None
                else ("yes" if item.registry_runtime_ready else "no")
            )
            print(
                f"- {item.kind} {item.item_id}: status={item.status} "
                f"registry_ready={registry_ready} missing_env={missing_env}"
            )
            print(f"  user: {item.user_action or '-'}")
            print(f"  code: {item.code_action or '-'}")
            print(f"  fixtures: {fixtures}")
        return 0

    print(
        f"- findings={summary.total_findings} implemented={summary.implemented_family_count} "
        f"report_only={summary.report_only_family_count} gated={summary.gated_family_count}"
    )
    for item in summary.pipeline_summaries:
        print(
            f"- pipeline {item.pipeline}: p1={item.p1} total={item.total} top={item.top_repo or '-'}"
        )
    for item in summary.absorbed_families:
        print(
            f"- family {item.family_id}: stage={item.runtime_stage} "
            f"absorbed_from={item.absorbed_from_count} runtime_gate={item.runtime_gate_count}"
        )
    for item in summary.source_candidates[:6]:
        print(
            f"- source {item.source_id}: status={item.research_status} "
            f"adoption_gate={item.adoption_gate_count} absorbed_from={item.absorbed_from_count}"
        )
    if summary.next_actions:
        print("- next actions:")
        for item in summary.next_actions[:5]:
            print(
                f"  {item.priority} {item.kind} {item.item_id}: {item.blocker or '-'}"
            )
    if summary.prereq_items:
        print("- prereqs:")
        for item in summary.prereq_items[:4]:
            missing_env = ",".join(item.missing_env_vars) or "-"
            print(
                f"  {item.kind} {item.item_id}: status={item.status} missing_env={missing_env}"
            )
    return 0


def run_pit(args: argparse.Namespace) -> int:
    client = TusharePitClient()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if args.kind == "trade_calendar":
        df = client.fetch_trade_calendar(start, end, exchange=args.exchange)
    elif args.kind == "index_weights":
        df = client.fetch_index_weights(args.index_code, start, end)
    else:
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
        df = client.fetch_disclosure_dates(symbols, start, end)

    if args.json:
        print(df.to_json(orient="records", force_ascii=False, indent=2))
    else:
        print(df.to_string(index=False))
    return 0


def run_compare_snapshots(args: argparse.Namespace) -> int:
    from aqsp.portfolio.snapshot import compare_snapshots, format_snapshot_diff

    date2 = args.date2 or today_shanghai().isoformat()
    date1 = args.date1 or (today_shanghai() - timedelta(days=1)).isoformat()

    diff = compare_snapshots(
        current_date=date2,
        previous_date=date1,
        snapshot_path=args.snapshot_path,
    )
    if diff is None:
        print(f"无法比较快照: {date1} 或 {date2} 的快照数据不存在")
        return 1

    print(format_snapshot_diff(diff))

    if diff.score_changes:
        print("\n📈 评分变化明细:")
        print(f"{'股票':>10s}  {'旧分':>6s}  {'新分':>6s}  {'变化':>6s}")
        print("-" * 36)
        for symbol, old_score, new_score in diff.score_changes:
            delta = new_score - old_score
            sign = "+" if delta > 0 else ""
            print(
                f"{symbol:>10s}  {old_score:6.1f}  {new_score:6.1f}  {sign}{delta:.1f}"
            )

    return 0


def _source_runtime_metadata(source_id: str) -> tuple[str, str, str]:
    entry = get_registry_entry(source_id)
    if entry is None:
        return "unknown", "unknown", "unknown"
    return (
        entry.freshness_tier,
        entry.coverage_tier,
        local_data_status(entry),
    )


def _reorder_source_refs(
    source_refs: list[object],
    *,
    pinned_last: tuple[str, ...] = (),
) -> list[object]:
    order = prioritize_source_ids(
        [str(getattr(item, "name", "")) for item in source_refs]
    )
    by_name = {str(getattr(item, "name", "")): item for item in source_refs}
    prioritized = [by_name[name] for name in order if name in by_name]
    if not pinned_last:
        return prioritized
    keep: list[object] = []
    tail: list[object] = []
    pinned = set(pinned_last)
    for item in prioritized:
        if str(getattr(item, "name", "")) in pinned:
            tail.append(item)
        else:
            keep.append(item)
    return keep + tail


def _get_source(source_name: str):
    cache = DataCache()
    if source_name in {"auto", "local_first"}:
        if not online_fallback_allowed():
            return TdxVipdocSource()
        fallbacks = _reorder_source_refs(
            [
                EastmoneySource(cache=cache),
                SinaSource(cache=cache),
                TencentSource(cache=cache),
                AkshareSource(cache=cache),
            ],
            pinned_last=("akshare",),
        )
        return MultiSource(
            SourceFactory("tdx_vipdoc", TdxVipdocSource),
            fallbacks,
            validate_consistency=False,
        )
    if source_name == "online_first":
        online_sources = _reorder_source_refs(
            [
                EastmoneySource(cache=cache),
                SinaSource(cache=cache),
                TencentSource(cache=cache),
                AkshareSource(cache=cache),
            ],
            pinned_last=("akshare",),
        )
        return MultiSource(
            online_sources[0],
            online_sources[1:] + [SourceFactory("tdx_vipdoc", TdxVipdocSource)],
            validate_consistency=False,
        )
    if source_name == "multi":
        sources = _reorder_source_refs(
            [
                AkshareSource(cache=cache),
                SinaSource(cache=cache),
                EastmoneySource(cache=cache),
                TencentSource(cache=cache),
            ],
            pinned_last=("akshare",),
        )
        return MultiSource(
            sources[0],
            sources[1:],
        )
    if source_name == "akshare":
        return AkshareSource(cache=cache)
    if source_name == "sina":
        return SinaSource(cache=cache)
    if source_name == "eastmoney":
        return EastmoneySource(cache=cache)
    if source_name == "tencent":
        return TencentSource(cache=cache)
    if source_name == "mootdx":
        return MootdxSource(cache=cache)
    if source_name == "baostock":
        return BaostockSource(cache=cache)
    if source_name == "efinance":
        return EfinanceSource(cache=cache)
    if source_name == "sqlite_db":
        return SqliteDbSource(cache=cache)
    if source_name == "tdx_vipdoc":
        return TdxVipdocSource()
    raise ValueError(f"Unknown data source: {source_name}")


def _fetch_frames_for_cli(
    source_name: str,
    symbols: list[str],
    *,
    benchmark_symbol: str | None,
    cache_path: str | None = None,
    days: int = 260,
) -> dict[str, pd.DataFrame]:
    frames, _actual_source = _fetch_frames_for_cli_with_metadata(
        source_name,
        symbols,
        benchmark_symbol=benchmark_symbol,
        cache_path=cache_path,
        days=days,
    )
    return frames


def _fetch_frames_for_cli_with_metadata(
    source_name: str,
    symbols: list[str],
    *,
    benchmark_symbol: str | None,
    cache_path: str | None = None,
    days: int = 260,
) -> tuple[dict[str, pd.DataFrame], str]:
    try:
        if source_name == "akshare":
            frames = fetch_akshare(
                symbols,
                days=days,
                benchmark_symbol=benchmark_symbol,
                cache_path=cache_path,
            )
            record_source_success(source_name, "akshare")
            return frames, "akshare"
        source = _get_source(source_name)
        frames = fetch_with_source(
            source, symbols, days=days, benchmark_symbol=benchmark_symbol
        )
        actual_source = str(getattr(source, "last_used_source", None) or source.name)
        record_source_success(source_name, actual_source)
        return frames, actual_source
    except DataError as exc:
        record_source_failure(source_name, str(exc))
        raise
    except Exception as exc:
        record_source_failure(source_name, str(exc))
        raise DataError(f"数据源 {source_name} 获取失败: {exc}") from exc


def _drop_benchmark_frame(
    frames: dict[str, pd.DataFrame],
    benchmark_symbol: str | None,
) -> dict[str, pd.DataFrame]:
    if not benchmark_symbol:
        return frames
    return {symbol: df for symbol, df in frames.items() if symbol != benchmark_symbol}


def _resolve_run_symbols(
    source_name: str,
    explicit_symbols: str,
    *,
    pool_name: str = "",
    as_of: date | None = None,
    max_universe: int,
    min_avg_amount: float,
) -> list[str]:
    target_day = as_of or today_shanghai()
    symbols = [item.strip() for item in explicit_symbols.split(",") if item.strip()]
    if symbols:
        return symbols
    if pool_name and pool_name != "all":
        from aqsp.universe.pool import UniversePool

        pool = UniversePool.from_default(pool_name)
        return pool.get_symbols(as_of=target_day)
    source = _get_source(source_name)
    if hasattr(source, "get_liquid_symbols"):
        try:
            liquid_symbols = source.get_liquid_symbols(
                limit=max_universe,
                min_amount=min_avg_amount,
            )
        except DataError:
            liquid_symbols = []
        if liquid_symbols:
            return liquid_symbols
    if hasattr(source, "get_available_symbols"):
        available = source.get_available_symbols()
        if available:
            return available[:max_universe] if max_universe > 0 else available
    return list(DEFAULT_SYMBOLS)


def _count_independent_signal_days(ledger_path: str) -> int:
    from aqsp.ledger.base import read_ledger

    rows = read_ledger(ledger_path)
    signal_dates = set()
    for row in rows:
        if row.get("status") in ("validated", "pending"):
            sd = row.get("signal_date", "")
            if sd:  # 过滤空 signal_date，避免虚增冷启动计数
                signal_dates.add(sd)
    return len(signal_dates)


def _compute_real_pnl(ledger_path: str) -> tuple[float, float, float]:
    from aqsp.ledger.base import read_ledger

    rows = read_ledger(ledger_path)
    if not rows:
        return 0.0, 0.0, 0.0

    today = now_shanghai().date()
    validated: list[tuple[date, float]] = []
    for row in rows:
        if row.get("status") != "validated":
            continue
        ret_pct = row.get("return_pct")
        if ret_pct is None:
            continue
        signal_date_str = row.get("signal_date", "")
        if not signal_date_str:
            continue
        try:
            signal_date = date.fromisoformat(signal_date_str)
        except (ValueError, TypeError):
            continue
        validated.append((signal_date, float(ret_pct)))

    if not validated:
        return 0.0, 0.0, 0.0

    validated.sort(key=lambda x: x[0])

    # daily_pnl：取「最近一个有 validated 记录的交易日」当天所有收益的累计，
    # 而非数组最后一条单笔（最后一条的 signal_date 可能不是最近日，且会漏掉同日多笔）。
    latest_signal_date = validated[-1][0]
    same_day_returns = [r for d, r in validated if d == latest_signal_date]
    daily_cum = 1.0
    for r in same_day_returns:
        daily_cum *= 1 + r / 100
    daily_pnl = (daily_cum - 1) * 100

    weekly_returns = [r for d, r in validated if (today - d).days <= 7]
    weekly_cum = 1.0
    for r in weekly_returns:
        weekly_cum *= 1 + r / 100
    weekly_pnl = (weekly_cum - 1) * 100 if weekly_returns else 0.0

    monthly_returns = [r for d, r in validated if (today - d).days <= 30]
    monthly_cum = 1.0
    for r in monthly_returns:
        monthly_cum *= 1 + r / 100
    monthly_pnl = (monthly_cum - 1) * 100 if monthly_returns else 0.0

    return daily_pnl, weekly_pnl, monthly_pnl


def _walkforward_fetch_days(start: str, end: str) -> int:
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    span_days = max((end_d - start_d).days, 0)
    return max(260, int(span_days * 1.8) + 90)


def _get_hs300_symbols(as_of: date | None = None) -> list[str]:
    """沪深300成分股的近似快照（手工维护，去重后保序）。

    若本地已配置 `TUSHARE_TOKEN`，优先按 `as_of` 读取 000300.SH 成分；
    否则回退到手工快照。
    """
    target_day = as_of or today_shanghai()
    live_symbols = load_optional_index_constituents("000300.SH", target_day)
    if live_symbols:
        return live_symbols

    raw = [
        "600519",
        "601318",
        "600036",
        "000858",
        "600276",
        "601166",
        "600900",
        "601888",
        "000333",
        "002415",
        "300750",
        "601012",
        "000001",
        "600000",
        "002594",
        "600887",
        "002475",
        "300059",
        "000725",
        "002714",
        "601398",
        "601288",
        "600030",
        "600048",
        "601668",
        "600050",
        "601857",
        "601985",
        "600104",
        "600016",
        "601328",
        "600019",
        "601601",
        "601628",
        "600585",
        "601138",
        "600837",
        "601225",
        "600309",
        "601211",
        "600547",
        "601360",
        "600196",
        "601390",
        "600031",
        "601186",
        "600009",
        "601766",
        "601669",
        "600436",
        "600028",
        "600015",
        "601919",
        "601111",
        "600690",
        "600089",
        "601006",
        "601800",
        "600346",
        "601117",
        "601688",
        "600570",
        "600176",
        "601236",
        "601877",
        "600183",
        "600010",
        "600029",
        "601155",
        "600061",
        "600741",
        "600660",
        "601881",
        "600115",
        "601336",
        "601939",
        "601998",
        "600011",
        "600018",
        "600025",
        "600085",
        "600111",
        "600150",
        "600256",
        "600332",
        "600352",
        "600362",
        "600406",
        "600438",
        "600489",
        "600588",
        "600600",
        "600655",
        "600703",
        "600745",
        "600760",
        "600795",
        "600809",
        "600845",
        "600848",
        "600867",
        "600871",
        "600875",
        "600885",
        "600886",
        "600893",
        "600918",
        "600919",
        "600926",
        "600938",
        "600941",
        "600989",
        "601009",
        "601021",
        "601066",
        "601077",
        "601088",
        "601100",
        "601108",
        "601162",
        "601169",
        "601229",
        "601231",
        "601238",
        "601298",
        "601319",
        "601377",
        "601456",
        "601555",
        "601577",
        "601607",
        "601618",
        "601633",
        "601658",
        "601698",
        "601728",
        "601788",
        "601816",
        "601818",
        "601838",
        "601878",
        "601898",
        "601899",
        "601901",
        "601916",
        "601933",
        "601966",
        "601988",
        "601989",
        "601992",
        "603019",
        "603077",
        "603127",
        "603160",
        "603195",
        "603233",
        "603259",
        "603288",
        "603290",
        "603345",
        "603369",
        "603392",
        "603486",
        "603501",
        "603517",
        "603568",
        "603605",
        "603613",
        "603658",
        "603799",
        "603806",
        "603816",
        "603833",
        "603882",
        "603886",
        "603899",
        "603986",
        "603993",
        "000002",
        "000063",
        "000066",
        "000069",
        "000100",
        "000157",
        "000166",
        "000301",
        "000338",
        "000425",
        "000538",
        "000568",
        "000596",
        "000625",
        "000651",
        "000661",
        "000703",
        "000708",
        "000723",
        "000728",
        "000768",
        "000776",
        "000783",
        "000786",
        "000800",
        "000876",
        "000895",
        "000938",
        "000963",
        "000977",
        "001979",
        "002001",
        "002007",
        "002008",
        "002024",
        "002027",
        "002032",
        "002044",
        "002049",
        "002050",
        "002065",
        "002074",
        "002120",
        "002128",
        "002142",
        "002146",
        "002157",
        "002179",
        "002180",
        "002202",
        "002230",
        "002236",
        "002241",
        "002252",
        "002271",
        "002304",
        "002311",
        "002340",
        "002352",
        "002371",
        "002375",
        "002382",
        "002410",
        "002414",
        "002422",
        "002430",
        "002456",
        "002460",
        "002463",
        "002466",
        "002468",
        "002493",
        "002507",
        "002508",
        "002555",
        "002557",
        "002568",
        "002600",
        "002601",
        "002602",
        "002607",
        "002624",
        "002625",
        "002736",
        "002739",
        "002745",
        "002756",
        "002812",
        "002821",
        "002832",
        "002841",
        "002916",
        "002920",
        "002938",
        "002939",
        "002945",
        "002958",
        "003816",
        "004997",
        "300003",
        "300014",
        "300015",
        "300033",
        "300122",
        "300124",
        "300142",
        "300144",
        "300146",
        "300347",
        "300408",
        "300413",
        "300433",
        "300450",
        "300454",
        "300498",
        "300529",
        "300601",
        "300628",
        "300661",
        "300676",
        "300760",
        "300763",
        "300782",
        "300832",
        "300866",
        "300896",
        "300919",
        "300999",
    ]
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for s in raw:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _regime_description(regime: str) -> str:
    descriptions = {
        "stable_bull": "稳定上涨：低波动+正趋势",
        "volatile_bull": "波动上涨：高波动+正趋势",
        "stable_bear": "稳定下跌：低波动+负趋势",
        "volatile_bear": "波动下跌：高波动+负趋势",
        "stable_sideways": "稳定盘整：低波动+无趋势",
        "volatile_sideways": "波动盘整：高波动+无趋势",
        "bull_trend": "牛市趋势：20日均收益 > 0.5%",
        "mild_bear": "温和熊市：20日均收益 -0.5% ~ -2%",
        "sideways": "震荡市：20日均收益 -0.5% ~ 0.5%",
        "bear_filter": "熊市过滤：20日均收益 < -2%",
    }
    return descriptions.get(regime, "未知 regime")


def _find_thresholds_yaml() -> Path | None:
    """Locate config/thresholds.yaml relative to this file or CWD.

    cli.py lives at <repo>/src/aqsp/cli.py — repo root is parents[2].
    Fall back to CWD-relative for non-standard installs.
    """
    candidate = Path(__file__).resolve().parents[2] / "config" / "thresholds.yaml"
    if candidate.exists():
        return candidate
    cwd_candidate = Path.cwd() / "config" / "thresholds.yaml"
    if cwd_candidate.exists():
        return cwd_candidate
    return None


def _update_thresholds_metadata(run_date: str) -> bool:
    """Rewrite last_walkforward_run in thresholds.yaml.

    Returns True if the field was found and updated, False otherwise.
    Tolerant to double-quoted, single-quoted, or bare values.
    """
    import re

    path = _find_thresholds_yaml()
    if path is None:
        return False
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'^(last_walkforward_run:\s*)("[^"]*"|\'[^\']*\'|[^\s#].*?)(\s*(?:#.*)?)$',
        flags=re.MULTILINE,
    )
    new_content, n = pattern.subn(
        lambda m: f'{m.group(1)}"{run_date}"{m.group(3)}',
        content,
    )
    if n == 0:
        return False
    path.write_text(new_content, encoding="utf-8")
    return True


def _assert_not_heldout(end: str, *, allow: bool, logger=None) -> None:
    """宪法 §1.3 #9：end 不得越过 held-out 边界。

    end > 2024-12-31 且未显式 --allow-heldout → fail loud（SystemExit）。
    开了 --allow-heldout → 红字警告放行并留痕（一次性 held-out 验收专用）。

    日期用 date.fromisoformat 解析后比较，而非字符串字典序——避免
    "2024/12/31" 或带空格等非标准格式被误判。非法日期本身 fail loud。
    """
    from datetime import date

    cutoff = date.fromisoformat(HELDOUT_TRAIN_CUTOFF)
    try:
        end_d = date.fromisoformat(end.strip())
    except (ValueError, AttributeError) as exc:
        raise SystemExit(
            f"[宪法 §1.3 #9] --end={end!r} 不是合法 ISO 日期 (YYYY-MM-DD): {exc}"
        ) from exc

    if end_d <= cutoff:
        return
    msg = f"[宪法 §1.3 #9] --end={end} 越过 held-out 边界 {HELDOUT_TRAIN_CUTOFF}，会把 2025-01~2026-04 held-out 区间卷入训练。"
    if not allow:
        raise SystemExit(
            msg
            + "\n  这是绝对禁止条款。如确为 held-out 一次性验收，显式加 --allow-heldout（且必须在双门通过后）。"
        )
    warn = (
        "⚠️  "
        + msg
        + "\n  已显式 --allow-heldout 放行。请确认这是双门通过后的一次性 held-out 验收，结果不得回灌训练。"
    )
    print(warn)
    if logger:
        logger.warning(warn)


def _write_walkforward_gate(
    *, dsr: float, pbo: float, run_date: str, start: str, end: str, n_periods: int
) -> None:
    """写双门 sidecar，供 run_scheduled 的 notify gate 读取。
    独立 JSON，不污染 thresholds.yaml，不 bump version（§1.3 #12/#14）。
    """
    import json

    # 宪法 §17.7：pbo==0.0 是单序列回测的占位值（无法做真 CSCV），
    # 不能当作「零过拟合」通过门。pbo_pass 要求 0 < pbo < 0.5。
    pbo_valid = pbo > 0.0
    pbo_pass = pbo_valid and pbo < 0.5
    dsr_pass = dsr > 1.0
    payload = {
        "run_date": run_date,
        "deflated_sharpe": dsr,
        "pbo": pbo,
        "pbo_valid": pbo_valid,
        "dsr_pass": dsr_pass,
        "pbo_pass": pbo_pass,
        "both_pass": dsr_pass and pbo_pass,
        "data_start": start,
        "data_end": end,
        "n_periods": n_periods,
    }
    p = Path(WALKFORWARD_GATE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ 双门 sidecar 已写入: {p}（both_pass={payload['both_pass']}）")


def _check_notification_gate(
    *, cold_start_days: int, gate_path: str = WALKFORWARD_GATE_PATH
) -> tuple[bool, list[str]]:
    """宪法 §1.3 #12/#14：返回 (是否放行, 未达原因列表)。

    三个串联条件，缺一不可（#14 明确串联）：
      1. 冷启动 >= {COLD_START_MIN_DAYS} 个独立信号日
      2. DSR >1.0
      3. PBO <0.5
    sidecar 缺失/解析失败/过期 → fail-closed（不放行）。
    """
    import json
    from datetime import date

    reasons: list[str] = []

    if cold_start_days < COLD_START_MIN_DAYS:
        reasons.append(
            f"冷启动未满: {cold_start_days}/{COLD_START_MIN_DAYS} 个独立信号日"
        )

    p = Path(gate_path)
    if not p.exists():
        reasons.append(f"双门 sidecar 不存在（{gate_path}）—— 请先跑 walkforward")
        return False, reasons

    try:
        gate = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"双门 sidecar 解析失败: {exc}")
        return False, reasons

    # 过期检查（对齐 §17.10，留 35 天缓冲期）
    try:
        run_date = date.fromisoformat(gate["run_date"])
        age = (today_shanghai() - run_date).days
        if age > 35:
            reasons.append(
                f"双门结果过期: {age} 天前（上限 35 天）—— 请重新跑 walkforward"
            )
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"双门 sidecar run_date 异常: {exc}")
        return False, reasons

    if not gate.get("dsr_pass"):
        reasons.append(f"DSR 未过门: {gate.get('deflated_sharpe')}（需 >1.0）")
    if not gate.get("pbo_pass"):
        reasons.append(f"PBO 未过门: {gate.get('pbo')}（需 <0.5）")

    # 宪法 §17.7：n_periods=0 意味着 sidecar 来自占位/测试/无效运行，
    # 不可能算出有效 DSR/PBO。即使标了 both_pass=true 也不可信。
    # fail-closed：0周期视为无效，不放行推送。
    n_periods = gate.get("n_periods", 0)
    if not n_periods or int(n_periods) <= 0:
        reasons.append(
            f"双门 sidecar 无有效回测周期（n_periods={n_periods}）"
            "—— 疑似占位/测试数据，需真正跑 walkforward 后重写"
        )

    # 宪法 §1.3 #9：拒绝用 held-out 污染的回测结果解锁推送。
    # 即使 DSR/PBO 都过门，若 sidecar 的 data_end 越过 held-out 边界，
    # 说明这个成绩是用 held-out 数据（可能经 --allow-heldout）算出来的，
    # 不得用于解锁实盘推送 —— fail-closed。
    data_end = gate.get("data_end", "")
    if data_end:
        from datetime import date

        try:
            end_d = date.fromisoformat(str(data_end).strip())
            cutoff_d = date.fromisoformat(HELDOUT_TRAIN_CUTOFF)
            if end_d > cutoff_d:
                reasons.append(
                    f"双门成绩用了 held-out 数据（data_end={data_end} > "
                    f"{HELDOUT_TRAIN_CUTOFF}）—— 不得用于解锁推送（§1.3 #9）"
                )
        except (ValueError, TypeError):
            # sidecar 的 data_end 格式异常 —— 看不懂就拦（fail-closed）
            reasons.append(
                f"双门 sidecar 的 data_end 格式异常（{data_end!r}）—— fail-closed"
            )

    return len(reasons) == 0, reasons


def run_screen(args: argparse.Namespace) -> int:
    actual_source = "csv"
    explicit_symbol_count = 0
    symbols: list[str] = []
    if args.csv:
        frames = load_csv(args.csv)
    else:
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
        if not symbols:
            symbols = _resolve_run_symbols(
                args.source,
                "",
                pool_name=getattr(args, "pool", ""),
                as_of=today_shanghai(),
                max_universe=0,
                min_avg_amount=args.min_avg_amount,
            )
        else:
            explicit_symbol_count = len(symbols)
        frames, actual_source = _fetch_frames_for_cli_with_metadata(
            args.source,
            symbols,
            benchmark_symbol=args.benchmark_symbol,
        )

    latest = latest_trade_date(frames)
    data_lag_days = (today_shanghai() - latest).days if latest is not None else 0
    freshness_tier, coverage_tier, source_local_status = _source_runtime_metadata(
        actual_source
    )
    source_health_label, source_health_message, fallback_used = describe_source_health(
        args.source,
        actual_source,
    )
    screen_frames = _drop_benchmark_frame(frames, args.benchmark_symbol)
    thresholds = load_thresholds()
    config = ScreeningConfig(
        mode=args.mode,
        min_avg_amount=args.min_avg_amount,
        min_price=thresholds.filter.min_price,
        max_price=thresholds.filter.max_price,
    )
    picks = _enrich_pick_names(screen_universe(screen_frames, config), screen_frames)[
        : args.limit
    ]
    table = to_dataframe(picks)
    if table.empty:
        print("No candidates.")
    else:
        print(table.to_string(index=False))

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        run_metadata = RunMetadata(
            requested_source=args.source,
            actual_source=actual_source,
            source_freshness_tier=freshness_tier,
            source_coverage_tier=coverage_tier,
            source_local_status=source_local_status,
            source_health_label=source_health_label,
            source_health_message=source_health_message,
            fallback_used=fallback_used,
            explicit_symbol_count=explicit_symbol_count,
            resolved_symbol_count=len(symbols) if symbols else len(screen_frames),
            fetched_frame_count=len(frames),
            screened_count=len(picks),
            final_count=len(picks),
            min_price=thresholds.filter.min_price,
            max_price=thresholds.filter.max_price,
            min_avg_amount=args.min_avg_amount,
            online_factors_enabled=False,
            thresholds_version=thresholds.version,
            data_latest_trade_date=latest.isoformat() if latest is not None else "",
            data_lag_days=data_lag_days,
        )
        Path(args.report).write_text(
            to_markdown(
                picks,
                title=f"AI 量化选股报告({args.mode})",
                metadata=run_metadata,
            ),
            encoding="utf-8",
        )
    if args.output_csv:
        Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(args.output_csv, index=False)
    return 0


def run_scheduled(args: argparse.Namespace) -> int:
    env = load_runtime_config()
    mode = args.mode or env.mode
    explicit_symbols = args.symbols or ",".join(env.symbols)
    limit = args.limit or env.limit
    max_universe = args.max_universe or env.max_universe
    min_avg_amount = args.min_avg_amount or env.min_avg_amount
    max_data_lag_days = args.max_data_lag_days or env.max_data_lag_days
    enable_online_factors = args.enable_online_factors or env.enable_online_factors
    explicit_symbol_count = len(
        [item.strip() for item in explicit_symbols.split(",") if item.strip()]
    )
    breaker = CircuitBreaker()
    daily_pnl, weekly_pnl, monthly_pnl = _compute_real_pnl(args.ledger)
    status = breaker.check(
        daily_pnl_pct=daily_pnl,
        weekly_pnl_pct=weekly_pnl,
        monthly_pnl_pct=monthly_pnl,
    )

    symbols: list[str] = []

    if args.csv:
        frames = load_csv(args.csv)
        actual_source = "csv"
    else:
        symbols = _resolve_run_symbols(
            args.source,
            explicit_symbols,
            pool_name=getattr(args, "pool", ""),
            as_of=today_shanghai(),
            max_universe=max_universe,
            min_avg_amount=min_avg_amount,
        )
        frames, actual_source = _fetch_frames_for_cli_with_metadata(
            args.source,
            symbols,
            benchmark_symbol=args.benchmark_symbol,
        )

    latest = assert_fresh_data(frames, max_data_lag_days)
    data_lag_days = (today_shanghai() - latest).days
    freshness_tier, coverage_tier, source_local_status = _source_runtime_metadata(
        actual_source
    )
    source_health_label, source_health_message, fallback_used = describe_source_health(
        args.source,
        actual_source,
    )

    weights = strategy_weights_from_ledger(args.ledger)

    try:
        from aqsp.ledger.base import ledger_rows_to_frame, read_ledger
        from aqsp.ledger.learner import PerformanceLearner

        learner = PerformanceLearner()
        ledger_df = ledger_rows_to_frame(read_ledger(args.ledger))
        learner_weights = learner.compute_weights(ledger_df)
        if learner_weights:
            for k, v in learner_weights.items():
                if k in weights:
                    weights[k] = round(weights[k] * v, 3)
    except Exception:
        pass

    cold_start_days = _count_independent_signal_days(args.ledger)
    is_cold_start = cold_start_days < COLD_START_MIN_DAYS

    thresholds = load_thresholds()

    bench_frame = frames.get(args.benchmark_symbol) if args.benchmark_symbol else None
    if bench_frame is not None and not bench_frame.empty:
        regime = RegimeDetector().detect({args.benchmark_symbol: bench_frame}).name
    else:
        regime = ""

    if regime:
        regime_multiplier = thresholds.regime.adjustments.get(regime, 1.0)
        if regime_multiplier != 1.0:
            weights = {k: round(v * regime_multiplier, 3) for k, v in weights.items()}

    screen_frames = _drop_benchmark_frame(frames, args.benchmark_symbol)
    config = ScreeningConfig(
        mode=mode,
        min_avg_amount=min_avg_amount,
        min_price=thresholds.filter.min_price,
        max_price=thresholds.filter.max_price,
        strategy_weights=weights,
    )
    screened_picks = _enrich_pick_names(
        screen_universe(screen_frames, config),
        screen_frames,
    )

    try:
        from aqsp.strategies.composite import CompositeStrategy

        composite = CompositeStrategy(thresholds=thresholds)
        composite_scores = composite.calculate_score(screen_frames, regime=regime)
        if composite_scores:
            max_cs = max(composite_scores.values()) if composite_scores else 1.0
            for pick in screened_picks:
                raw_cs = composite_scores.get(pick.symbol, 0.0)
                normalized_cs = (raw_cs / max_cs * 100) if max_cs > 0 else 0.0
                pick.regime_score = round(normalized_cs, 2)
                pick.score = round(pick.score * 0.7 + normalized_cs * 0.3, 2)
            screened_picks.sort(key=lambda p: p.score, reverse=True)
    except Exception:
        pass

    picks = screened_picks[:limit]

    lethal_pipeline = LethalFilterPipeline()
    filtered_picks = []
    for pick in picks:
        df = screen_frames.get(pick.symbol, pd.DataFrame())
        passed, rejected_by = lethal_pipeline.run(pick.symbol, df)
        if passed:
            filtered_picks.append(pick)
    if len(filtered_picks) < len(picks):
        print(
            f"排雷过滤: {len(picks)} → {len(filtered_picks)} (过滤 {len(picks) - len(filtered_picks)} 只)"
        )
    picks = filtered_picks

    from aqsp.universe.t1_filter import filter_t1_held

    kept, removed = filter_t1_held(
        candidates=[r.symbol for r in picks],
        ledger_path=args.ledger,
        today=today_shanghai(),
    )
    if removed:
        print(f"T+1 过滤剔除 {len(removed)} 只（昨日已买）: {removed}")
    picks = [r for r in picks if r.symbol in kept]

    execution = ExecutionConfig(
        horizon_days=args.horizon_days,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        benchmark_symbol=args.benchmark_symbol,
    )

    nb_z = 0.0
    margin_z = 0.0
    if enable_online_factors:
        try:
            from aqsp.data.cn.northbound import (
                compute_northbound_factor,
                fetch_northbound_flow,
            )

            nb_flow = fetch_northbound_flow()
            nb_z = compute_northbound_factor(nb_flow) if not nb_flow.empty else 0.0
        except Exception:
            nb_z = 0.0
        try:
            from aqsp.data.cn.margin_trading import compute_margin_factor

            top_symbol = picks[0].symbol if picks else ""
            margin_z = compute_margin_factor(top_symbol) if top_symbol else 0.0
        except Exception:
            margin_z = 0.0

    run_metadata = RunMetadata(
        requested_source=args.source,
        actual_source=actual_source,
        source_freshness_tier=freshness_tier,
        source_coverage_tier=coverage_tier,
        source_local_status=source_local_status,
        source_health_label=source_health_label,
        source_health_message=source_health_message,
        fallback_used=fallback_used,
        explicit_symbol_count=explicit_symbol_count,
        resolved_symbol_count=len(symbols),
        fetched_frame_count=len(frames),
        screened_count=len(screened_picks),
        final_count=len(picks),
        min_price=thresholds.filter.min_price,
        max_price=thresholds.filter.max_price,
        min_avg_amount=min_avg_amount,
        online_factors_enabled=enable_online_factors,
        thresholds_version=thresholds.version,
        data_latest_trade_date=latest.isoformat(),
        data_lag_days=data_lag_days,
        regime=regime,
        max_universe=max_universe,
    )

    diff = None
    concentration = None

    from aqsp.data.anomaly import detect_anomalies, format_anomaly_alerts
    from aqsp.data.freshness import check_freshness, format_freshness_report

    anomaly_alerts = detect_anomalies(screen_frames)
    freshness_reports = check_freshness(screen_frames)

    critical_alerts = [a for a in anomaly_alerts if a.severity == "critical"]
    warning_alerts = [a for a in anomaly_alerts if a.severity == "warning"]
    if critical_alerts:
        print(f"🔴 数据异常: {len(critical_alerts)} 条严重告警")
        for alert in critical_alerts:
            print(f"   {alert.symbol}: {alert.detail}")
    if warning_alerts:
        print(f"🟡 数据异常: {len(warning_alerts)} 条警告")
        for alert in warning_alerts:
            print(f"   {alert.symbol}: {alert.detail}")

    stale_reports = [r for r in freshness_reports if r.status != "fresh"]
    if stale_reports:
        print(f"📅 数据新鲜度: {len(stale_reports)} 只标的数据过期")
        for r in stale_reports:
            print(f"   {r.symbol}: 最新 {r.last_date}, 延迟 {r.delay_days} 天")

    # 板块集中度检查
    if picks:
        from aqsp.portfolio.sector_check import (
            check_sector_concentration,
            format_concentration,
        )

        concentration = check_sector_concentration([p.symbol for p in picks])
        if concentration.warnings:
            print(format_concentration(concentration))

    correlation_result = None
    if picks:
        from aqsp.portfolio.correlation import compute_correlation, format_correlation

        correlation_result = compute_correlation(
            screen_frames,
            [p.symbol for p in picks],
        )
        if correlation_result.matrix:
            print(format_correlation(correlation_result))

    if picks:
        from aqsp.risk.dynamic_stop import compute_dynamic_stop

        updated_picks = []
        for pick in picks:
            df = screen_frames.get(pick.symbol, pd.DataFrame())
            stop = compute_dynamic_stop(df, pick.close, symbol=pick.symbol)
            if stop.recommended_stop > pick.stop_loss:
                pick = PickResult(
                    symbol=pick.symbol,
                    name=pick.name,
                    date=pick.date,
                    close=pick.close,
                    score=pick.score,
                    rating=pick.rating,
                    entry_type=pick.entry_type,
                    ideal_buy=pick.ideal_buy,
                    stop_loss=stop.recommended_stop,
                    take_profit=pick.take_profit,
                    position=pick.position,
                    strategies=pick.strategies,
                    reasons=pick.reasons,
                    risks=pick.risks,
                    metrics={**pick.metrics, "stop_method": stop.method},
                )
            updated_picks.append(pick)
        picks = updated_picks

    debate_results = []
    debate_file = Path("data/debate_results.jsonl")
    DEBATE_RETENTION_DAYS = 30
    DEBATE_COOLDOWN_DAYS = 3
    DEBATE_MIN_DISAGREEMENT = 0.3
    DEBATE_MIN_ADJUSTMENT_PCT = 0.02

    debate_runtime = load_debate_runtime_config()
    debate_enabled = getattr(args, "enable_debate", False) or debate_runtime.enabled

    if debate_enabled and picks:
        print("📢 启动多Agent辩论分析...")

        coordinator = AShareDebateCoordinator(
            enable_llm=debate_runtime.enable_llm,
            max_rounds=debate_runtime.max_rounds,
            thresholds_version=thresholds.version,
            regime=regime or "unknown",
            data_source="multi" if args.source == "multi" else str(args.source),
            language=debate_runtime.language,
            roles=parse_agent_roles(debate_runtime.roles),
            role_runtime=debate_runtime.role_runtime,
        )
        debate_file.parent.mkdir(parents=True, exist_ok=True)

        existing_debates: dict[str, dict] = {}
        cutoff_date = (
            (now_shanghai() - timedelta(days=DEBATE_RETENTION_DAYS)).date().isoformat()
        )
        if debate_file.exists():
            for line in debate_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        data = json.loads(line)
                        debate_date = data.get("related_signal_date", "")
                        if debate_date >= cutoff_date:
                            key = f"{data['symbol']}_{debate_date}"
                            if key not in existing_debates or existing_debates[key].get(
                                "created_at", ""
                            ) < data.get("created_at", ""):
                                existing_debates[key] = data
                    except json.JSONDecodeError:
                        pass

        today = now_shanghai().date().isoformat()
        now = now_shanghai().isoformat(timespec="seconds")

        cooldown_symbols: set[str] = set()
        cooldown_cutoff = (
            (now_shanghai() - timedelta(days=DEBATE_COOLDOWN_DAYS)).date().isoformat()
        )
        for data in existing_debates.values():
            if data.get("related_signal_date", "") >= cooldown_cutoff:
                cooldown_symbols.add(data.get("symbol", ""))

        adjusted_picks: list[Any] = []
        skipped_cooldown = 0

        for pick in picks[:3]:
            if pick.symbol in cooldown_symbols:
                print(
                    f"   ⏭️  跳过 {pick.symbol} {pick.name}（{DEBATE_COOLDOWN_DAYS}天内已辩论）"
                )
                adjusted_picks.append(pick)
                skipped_cooldown += 1
                continue

            df = screen_frames.get(pick.symbol, pd.DataFrame())
            if not df.empty:
                try:
                    result = coordinator.run_debate(pick, df, signal_date=today)

                    if result.disagreement_score < DEBATE_MIN_DISAGREEMENT:
                        print(
                            f"   ⏭️  跳过 {pick.symbol} {pick.name}（分歧度 {result.disagreement_score:.2f} < {DEBATE_MIN_DISAGREEMENT}）"
                        )
                        adjusted_picks.append(pick)
                        continue

                    debate_results.append(result)

                    serialized = serialize_debate_result(result)
                    serialized["debate_date"] = today
                    serialized["created_at"] = now
                    key = f"{result.symbol}_{today}"
                    existing_debates[key] = serialized

                    adjustment_pct = abs(result.adjustment_weight)
                    if adjustment_pct < DEBATE_MIN_ADJUSTMENT_PCT:
                        print(
                            f"   ⏭️  {pick.symbol} {pick.name} 调整幅度 {adjustment_pct * 100:.1f}% < {DEBATE_MIN_ADJUSTMENT_PCT * 100:.0f}%，保持原评分"
                        )
                        adjusted_picks.append(pick)
                        continue

                    updated_pick = PickResult(
                        symbol=pick.symbol,
                        name=pick.name,
                        date=pick.date,
                        close=pick.close,
                        score=pick.score,
                        rating=pick.rating,
                        entry_type=pick.entry_type,
                        ideal_buy=pick.ideal_buy,
                        stop_loss=pick.stop_loss,
                        take_profit=pick.take_profit,
                        position=pick.position,
                        strategies=pick.strategies,
                        reasons=pick.reasons,
                        risks=pick.risks,
                        metrics=pick.metrics,
                        adjusted_score=result.adjusted_score,
                        recommended_adjustment=result.recommended_adjustment,
                        debate_consensus=result.final_consensus,
                        confidence=max(
                            0,
                            min(
                                100,
                                pick.confidence
                                + {"bullish": 10, "bearish": -15, "split": -5}.get(
                                    result.final_consensus, 0
                                ),
                            ),
                        ),
                    )
                    adjusted_picks.append(updated_pick)
                    print(
                        f"   ✅ 辩论完成: {pick.symbol} {pick.name} | 原始 {result.original_score:.1f} → 调整 {result.adjusted_score:.1f}（{adjustment_pct * 100:+.1f}%）"
                    )
                except Exception as e:
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.warning(f"辩论失败 {pick.symbol}: {e}")
                    adjusted_picks.append(pick)

        for pick in picks[3:]:
            adjusted_picks.append(pick)

        adjusted_picks.sort(
            key=lambda p: p.adjusted_score if p.adjusted_score > 0 else p.score,
            reverse=True,
        )

        picks = adjusted_picks
        if skipped_cooldown > 0:
            print(f"   ⏭️  {skipped_cooldown}只股票因冷却期跳过")
        print("   📊 重新排序完成（使用辩论后评分）")

        with open(debate_file, "w", encoding="utf-8") as f:
            for data in existing_debates.values():
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        print("📢 辩论分析完成")

    validation = None
    if not args.skip_validation:
        validation = validate_predictions(args.ledger, frames)

    if validation and validation.checked and debate_results:
        try:
            from aqsp.briefing.debate_tracker import DebatePerformanceTracker

            tracker = DebatePerformanceTracker()
            debates_by_key: dict[str, dict] = {}
            for dr in debate_results:
                key = f"{dr.symbol}_{dr.related_signal_date}"
                debates_by_key[key] = dr

            from aqsp.ledger.base import read_ledger

            rows = read_ledger(args.ledger)
            for row in rows:
                if row.get("status") != "validated":
                    continue
                symbol = str(row.get("symbol", ""))
                signal_date = str(row.get("signal_date", ""))
                key = f"{symbol}_{signal_date}"
                debate = debates_by_key.get(key)
                if not debate:
                    continue
                actual_return = float(row.get("return_pct", 0))
                target_stance = "bullish" if actual_return > 0 else "bearish"
                for round_data in debate.rounds:
                    for opinion in round_data.opinions:
                        was_correct = opinion.stance == target_stance
                        tracker.record_prediction(
                            role=opinion.role,
                            agent_id=opinion.agent_id,
                            predicted_stance=opinion.stance,
                            was_correct=was_correct,
                        )
            print("   📊 Agent表现已自动更新")
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug(f"自动反馈跳过: {e}")

    portfolio_decisions = None
    portfolio_summary = None
    if picks:
        from aqsp.portfolio.manager import apply_portfolio_manager

        bundle = apply_portfolio_manager(
            picks,
            concentration=concentration,
            correlation_result=correlation_result,
        )
        picks = bundle.picks
        portfolio_decisions = list(bundle.decisions)
        portfolio_summary = bundle.summary
        if portfolio_decisions:
            print("📦 Portfolio Manager 裁决完成")

    append_predictions(
        args.ledger,
        picks,
        execution=execution,
        thresholds_version=thresholds.version,
        regime=regime,
        northbound_flow_5d_z=nb_z,
        margin_balance_change_5d=margin_z,
        run_metadata=run_metadata,
    )

    # 保存选股快照：必须使用 PM 裁决后的最终候选，确保后续报告/briefing/仪表盘一致
    if picks:
        from aqsp.portfolio.snapshot import (
            save_snapshot,
            compare_snapshots,
            format_snapshot_diff,
        )

        save_snapshot(
            picks, snapshot_path="data/snapshots", date=today_shanghai().isoformat()
        )
        diff = compare_snapshots(
            current_date=today_shanghai().isoformat(),
            previous_date=(today_shanghai() - timedelta(days=1)).isoformat(),
            snapshot_path="data/snapshots",
        )
        if diff is not None and (
            diff.new_picks or diff.removed_picks or diff.rank_changes
        ):
            print(format_snapshot_diff(diff))

    table = to_dataframe(picks)
    markdown = to_markdown(
        picks,
        title=f"AI 量化选股报告({mode}, 数据日期 {latest.isoformat()})",
        metadata=run_metadata,
        debate_results=debate_results if debate_results else None,
        portfolio_decisions=portfolio_decisions,
        portfolio_summary=portfolio_summary,
    )

    # 执行摘要：3秒抓重点，插在报告标题后第一段
    tradable = [p for p in picks if p.rating in ("strong_buy_candidate", "buy_candidate")]
    summary_lines = [
        "",
        "---",
        "## 📌 执行摘要",
        "",
    ]
    if status.triggered:
        summary_lines.append(f"🛡️ **组合保护已触发**: {status.reason}，暂停新开仓")
    elif not tradable:
        summary_lines.append("👀 **今日无可执行标的**，仅观察。等待更强信号。")
    else:
        top = tradable[0]
        summary_lines.append(
            f"🎯 **首选**: {top.symbol} {top.name} | 评分 {top.score:.0f} | "
            f"买点 {top.ideal_buy} / 止损 {top.stop_loss} / 目标 {top.take_profit}"
        )
        if len(tradable) > 1:
            others = "、".join(f"{p.symbol} {p.name}" for p in tradable[1:3])
            summary_lines.append(f"📋 **其他候选**: {others}")

    if is_cold_start:
        summary_lines.append(f"⏳ 冷启动期: {cold_start_days}/{COLD_START_MIN_DAYS} 天（策略权重未调整，仅供观察）")

    summary_lines.append("")
    summary_lines.append("---")
    summary_lines.append("")

    # 把摘要插到标题行之后（第一个 \n\n 之后）
    title_end = markdown.find("\n\n")
    if title_end > 0:
        markdown = markdown[:title_end] + "\n" + "\n".join(summary_lines) + markdown[title_end:]
    if status.triggered:
        markdown += (
            "\n\n## 组合保护\n"
            f"- ⚠️ 熔断触发: {status.reason}\n"
            "- 本期信号仅供参考，不建议新建仓位\n"
        )
    if validation is not None:
        validation_text = "\n\n## 策略自检\n"
        if is_cold_start:
            validation_text += f"- ⏳ 冷启动期:已积累 {cold_start_days}/{COLD_START_MIN_DAYS} 个独立信号日\n"
            validation_text += "- 策略权重调整和胜率统计将在冷启动期结束后启用\n"
        elif validation.checked:
            validation_text += f"- 本次验证历史预测: {validation.checked} 条\n"
            validation_text += (
                f"- 胜率: {(validation.wins / validation.checked * 100):.1f}%\n"
            )
        else:
            validation_text += "- 本次暂无可验证历史预测\n"
        if not is_cold_start and validation.checked:
            validation_text += f"- 平均收益: {validation.avg_return_pct}%\n"
            validation_text += f"- 平均超额收益: {validation.avg_excess_pct}%\n"
        if weights:
            validation_text += (
                "- 当前策略权重: "
                + ", ".join(f"{k}={v}" for k, v in sorted(weights.items()))
                + "\n"
            )
        markdown += validation_text

    anomaly_text = format_anomaly_alerts(anomaly_alerts)
    if critical_alerts or warning_alerts:
        markdown += "\n\n" + anomaly_text

    freshness_text = format_freshness_report(freshness_reports)
    if stale_reports:
        markdown += "\n\n" + freshness_text

    if diff is not None and (diff.new_picks or diff.removed_picks or diff.rank_changes):
        from aqsp.portfolio.snapshot import format_snapshot_diff

        markdown += "\n\n## 选股变化\n" + format_snapshot_diff(diff)

    if concentration is not None and concentration.warnings:
        from aqsp.portfolio.sector_check import format_concentration

        markdown += "\n\n## 板块集中度\n" + format_concentration(concentration)

    if correlation_result is not None and correlation_result.high_corr_pairs:
        from aqsp.portfolio.correlation import format_correlation

        markdown += "\n\n## 候选股相关性\n" + format_correlation(correlation_result)
        markdown += "\n\n> ⚠️ 存在高相关性配对，分散化不足，建议关注组合风险\n"

    try:
        from aqsp.ledger.base import ledger_rows_to_frame, read_ledger
        from aqsp.ledger.learner import StrategyDecayDetector, format_decay_alerts

        decay_detector = StrategyDecayDetector()
        decay_alerts = decay_detector.detect(
            ledger_rows_to_frame(read_ledger(args.ledger))
        )
        if decay_alerts and not is_cold_start:
            markdown += "\n\n" + format_decay_alerts(decay_alerts)
            print(format_decay_alerts(decay_alerts))
    except Exception:
        pass

    try:
        from aqsp.ledger.failure_analysis import (
            analyze_failures,
            format_failure_patterns,
        )
        from aqsp.ledger.base import read_ledger as _read_ledger_for_failure

        _failure_rows = _read_ledger_for_failure(args.ledger)
        _failure_df = pd.DataFrame(_failure_rows) if _failure_rows else pd.DataFrame()
        failure_patterns = analyze_failures(_failure_df)
        if failure_patterns:
            failure_text = format_failure_patterns(failure_patterns)
            markdown += "\n\n" + failure_text
            print("\n⚠️ 发现失败模式:")
            for p in failure_patterns:
                print(
                    f"   - {p.pattern_name}: {p.description} (平均亏损 {p.avg_loss:.2f}%)"
                )
    except Exception:
        pass

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    # 先检查双门 gate，决定是否放行 notify
    gate_ok, gate_reasons = _check_notification_gate(cold_start_days=cold_start_days)
    if args.notify and not gate_ok:
        # 宪法 §1.3 #12/#14：门未达，--notify 自动失效
        print("⛔ 双门未达，--notify 自动失效。原因：")
        for r in gate_reasons:
            print(f"   - {r}")
        # 在 markdown 头部加警告（§1.3 #13）
        markdown = (
            "> ⚠️ **未通过 walk-forward 双门验证，仅供观察，请勿实盘使用**\n"
            "> " + "；".join(gate_reasons) + "\n\n"
        ) + markdown
        args.notify = False
    # 写报告（可能包含警告）
    Path(args.report).write_text(markdown, encoding="utf-8")
    Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.output_csv, index=False)
    print(markdown)

    if args.notify:
        results = notify_markdown(
            build_daily_run_notification(
                run_date=latest.isoformat(),
                tradable=tradable,
                portfolio_summary=portfolio_summary,
                actual_source=actual_source,
                source_health_label=source_health_label,
                source_health_message=source_health_message,
                requested_source=args.source,
                cold_start_days=cold_start_days,
                cold_start_min_days=COLD_START_MIN_DAYS,
                is_cold_start=is_cold_start,
                circuit_breaker_reason=status.reason if status.triggered else "",
                mode=load_runtime_config().notify_mode,
            )
        )
        if not results:
            print("No notification channel configured.")
        for result in results:
            status = "ok" if result.ok else "failed"
            print(f"notify {result.channel}: {status} ({result.detail})")
    return 2 if status.triggered else 0


def run_dashboard(args: argparse.Namespace) -> int:
    from scripts.render_dashboard import render_all_panels

    html = render_all_panels(ledger_path=args.ledger)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(html, encoding="utf-8")
    print(f"Dashboard saved to {args.output}")
    return 0


def run_walkforward(args: argparse.Namespace) -> int:
    import logging
    import sys
    from aqsp.core.time import now_shanghai, today_shanghai
    from aqsp.data import load_csv
    from aqsp.strategies.composite import CompositeStrategy
    from aqsp.strategies.thresholds import load_thresholds

    log_path = Path(args.log) if args.log else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[
                logging.FileHandler(log_path, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )
        logger = logging.getLogger("aqsp.walkforward")
        logger.info("Walk-forward 开始，日志文件: %s", log_path)
    else:
        logger = None

    # 宪法 §1.3 #9：held-out 护栏
    _assert_not_heldout(args.end, allow=args.allow_heldout, logger=logger)

    explicit_symbols = args.symbols.strip()
    if not explicit_symbols:
        explicit_symbols = os.getenv("AQSP_WALKFORWARD_SYMBOLS", "").strip()
    symbols = [s.strip() for s in explicit_symbols.split(",") if s.strip()]
    if not symbols:
        if args.pool == "all":
            src = _get_source(args.source)
            if hasattr(src, "get_available_symbols"):
                symbols = src.get_available_symbols()
                print(f"使用全市场标的池: {len(symbols)} 只")
            else:
                symbols = _get_hs300_symbols(date.fromisoformat(args.start))
                print(f"数据源不支持全市场查询，回退到沪深300: {len(symbols)} 只")
        elif args.pool and args.pool != "sh300":
            from aqsp.universe.pool import UniversePool

            pool = UniversePool.from_default(args.pool)
            symbols = pool.get_symbols(as_of=date.fromisoformat(args.start))
            pool_name = {"zz500": "中证500", "zz1000": "中证1000", "cyb": "创业板"}.get(
                args.pool, args.pool
            )
            print(f"使用 {pool_name} 标的池: {len(symbols)} 只")
        else:
            symbols = _get_hs300_symbols(date.fromisoformat(args.start))
            print(f"使用沪深300默认池: {len(symbols)} 只")
    else:
        # 用户传入的也去重，保持顺序
        seen: set[str] = set()
        deduped: list[str] = []
        for s in symbols:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        if len(deduped) != len(symbols):
            print(f"提示: --symbols 去重后 {len(symbols)} → {len(deduped)} 只")
        symbols = deduped

    print(f"正在获取 {len(symbols)} 只股票 {args.start} ~ {args.end} 的日线数据...")
    if logger:
        logger.info("获取 %d 只股票数据...", len(symbols))

    if args.source in {"multi", "akshare", "eastmoney", "tencent"}:
        frames = _fetch_frames_for_cli(
            args.source,
            symbols,
            benchmark_symbol=None,
            cache_path=args.cache_path or None,
            days=_walkforward_fetch_days(args.start, args.end),
        )
    elif args.source == "mootdx":
        from datetime import date as _date
        from aqsp.data.mootdx_source import MootdxSource

        src = MootdxSource()
        start_d = _date.fromisoformat(args.start)
        end_d = _date.fromisoformat(args.end)
        # 7 年 ~1700 交易日，count=2000 留余量；mootdx 实际上限可能 < 2000，
        # 拿不满会按实际返回。第一次跑后看 logs 确认覆盖了 args.start。
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="", count=2000)
    elif args.source == "sina":
        from datetime import date as _date
        from aqsp.data.sina_source import SinaSource

        src = SinaSource(cache=DataCache())
        start_d = _date.fromisoformat(args.start)
        end_d = _date.fromisoformat(args.end)
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="")
    elif args.source == "baostock":
        from datetime import date as _date
        from aqsp.data.pit_financial import enrich_ohlcv_with_pit_financials

        src = BaostockSource(cache=DataCache())
        start_d = _date.fromisoformat(args.start)
        end_d = _date.fromisoformat(args.end)
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="")
        print(
            f"正在获取 {len(symbols)} 只股票 {args.start} ~ {args.end} 的 point-in-time 财务数据..."
        )
        pit_result = enrich_ohlcv_with_pit_financials(
            frames,
            symbols,
            start_d,
            end_d,
            cache=DataCache(),
        )
        frames = pit_result.frames
        print(f"财务数据合并完成: {pit_result.financial_symbol_count} 只有财务数据")
        if pit_result.disclosure_symbol_count:
            print(f"Tushare 披露日覆盖完成: {pit_result.disclosure_symbol_count} 只")
        for status in getattr(pit_result, "source_statuses", ()):
            print(f"PIT源 {status.source_id}: {status.status} - {status.message}")
    elif args.source == "sqlite_db":
        from datetime import date as _date
        from aqsp.data.pit_financial import enrich_ohlcv_with_pit_financials

        # walkforward 直接读本地历史库，避免被短周期 runtime cache 截断区间。
        src = SqliteDbSource(cache=None)
        start_d = _date.fromisoformat(args.start)
        end_d = _date.fromisoformat(args.end)
        available = src.get_available_symbols()
        symbols = [s for s in symbols if s in available]
        print(f"SQLite 数据库中可用标的: {len(symbols)} 只")
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="")
        print(
            f"正在获取 {len(symbols)} 只股票 {args.start} ~ {args.end} 的 point-in-time 财务数据..."
        )
        pit_result = enrich_ohlcv_with_pit_financials(
            frames,
            symbols,
            start_d,
            end_d,
            cache=DataCache(),
        )
        frames = pit_result.frames
        print(f"财务数据合并完成: {pit_result.financial_symbol_count} 只有财务数据")
        if pit_result.disclosure_symbol_count:
            print(f"Tushare 披露日覆盖完成: {pit_result.disclosure_symbol_count} 只")
        for status in getattr(pit_result, "source_statuses", ()):
            print(f"PIT源 {status.source_id}: {status.status} - {status.message}")
    else:
        frames = load_csv(args.source)

    filtered = {}
    for sym, df in frames.items():
        if df is None or df.empty:
            continue
        mask = (df["date"].astype(str) >= args.start) & (
            df["date"].astype(str) <= args.end
        )
        sliced = df.loc[mask]
        if len(sliced) >= 100:
            filtered[sym] = sliced.copy()

    if not filtered:
        print("没有足够数据进行回测")
        if logger:
            logger.error("没有足够数据进行回测")
        return 1

    print(f"有效标的: {len(filtered)} 只")
    if logger:
        logger.info("有效标的: %d 只", len(filtered))

    thresholds = load_thresholds()
    if args.min_score is not None:
        from aqsp.strategies.thresholds import CompositeThresholds

        thresholds = thresholds.__class__(
            **{
                **thresholds.__dict__,
                "composite": CompositeThresholds(
                    **{
                        **thresholds.composite.__dict__,
                        "min_total_score": args.min_score,
                    }
                ),
            }
        )
        print(f"⚠️  使用 --min-score={args.min_score} 覆盖 thresholds.yaml 默认值")

    strategy = CompositeStrategy(thresholds=thresholds)
    runtime_cfg = load_runtime_config()
    requested_engine = (args.engine or runtime_cfg.research_engine or "auto").strip()
    engine, resolution = resolve_walkforward_engine(requested_engine)
    engine_cfg = WalkForwardEngineConfig(
        train_days=args.train_days,
        test_days=args.test_days,
        purge_days=args.purge_days,
        horizon_days=args.horizon_days or 3,
        use_tiered_stop=getattr(args, "tiered_stop", False),
    )

    print("开始 walk-forward 回测...")
    print(
        f"研究引擎: requested={resolution.requested} resolved={resolution.resolved} "
        f"mode={resolution.mode}"
    )
    print(f"引擎说明: {resolution.message}")
    if logger:
        logger.info(
            "Walk-forward 回测开始... engine requested=%s resolved=%s mode=%s",
            resolution.requested,
            resolution.resolved,
            resolution.mode,
        )

    result = engine.run(
        strategy,
        filtered,
        start_date=args.start,
        end_date=args.end,
        config=engine_cfg,
    )

    regime_counts: dict[str, int] = {}
    if hasattr(result, "regime_winrates") and result.regime_winrates:
        for regime in result.regime_winrates:
            regime_counts[regime] = regime_counts.get(regime, 0) + 1

    tl_dr = []
    dsr_pass = result.deflated_sharpe > 1.0
    # 宪法 §17.7：PBO 必须经真 CSCV（N>=2 变体）计算。
    # 单序列回测无法做 CSCV，calculate_cscv_pbo_from_single 会返回占位值 0.0。
    # 真 CSCV 的 PBO 几乎不可能恰为 0.0（252 组合中通常有 λ<=0）。
    # 因此 pbo==0.0 视为「未经有效 CSCV 验证」，不予通过门 —— 避免单策略
    # 用占位 0.0 蒙混过双门。需要 grid（多变体）walkforward 才能得到有效 PBO。
    pbo_is_valid = result.pbo > 0.0
    pbo_pass = pbo_is_valid and result.pbo < 0.5
    both_pass = dsr_pass and pbo_pass
    verdict = "PASS" if both_pass else "FAIL"
    tl_dr.append(
        f"**TL;DR**: {verdict} — DSR={result.deflated_sharpe:.4f}, "
        f"PBO={result.pbo:.2%}, Sharpe={result.overall.sharpe_ratio:.2f}, "
        f"TotalReturn={result.overall.total_return:.2%}"
    )

    report_lines = [
        "# Walk-Forward 回测报告",
        "",
        "## TL;DR",
        "",
        *tl_dr,
        "",
        "## 双门判定",
        "",
        f"- DSR > 1.0：{'PASS' if dsr_pass else 'FAIL'}（实测 {result.deflated_sharpe:.4f}）",
        f"- PBO < 0.5：{'PASS' if pbo_pass else 'FAIL'}（实测 {result.pbo:.2%}）",
        "",
        f"**运行日期**: {now_shanghai().strftime('%Y-%m-%d %H:%M')}",
        f"**回测区间**: {args.start} ~ {args.end}",
        f"**标的数量**: {len(filtered)}",
        f"**训练窗口**: {args.train_days} 天",
        f"**测试窗口**: {args.test_days} 天",
        f"**Purge Gap**: {args.purge_days} 天",
        f"**研究引擎**: {resolution.resolved} ({resolution.mode})",
        "",
        "## 整体指标",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 总收益 | {result.overall.total_return:.2%} |",
        f"| 年化收益 | {result.overall.annual_return:.2%} |",
        f"| 最大回撤 | {result.overall.max_drawdown:.2%} |",
        f"| Sharpe Ratio | {result.overall.sharpe_ratio:.2f} |",
        f"| 胜率 | {result.overall.win_rate:.2%} |",
        f"| 盈利因子 | {result.overall.profit_factor:.2f} |",
        f"| 总交易次数 | {result.overall.trades} |",
        f"| 不可成交次数 | {result.overall.not_executable} |",
        "",
        "## 过拟合检测",
        "",
        "| 指标 | 值 | 说明 |",
        "|------|-----|------|",
        f"| Deflated Sharpe Ratio | {result.deflated_sharpe:.4f} | > 1.0 表示策略可能有效 |",
        f"| PBO (过拟合概率) | {result.pbo:.2%} | < 50% 表示低过拟合风险 |",
        f"| 稳健性评分 | {result.robustness_score:.2%} | > 70% 表示稳定 |",
        f"| 参数标准差 | {result.parameter_std:.4f} | 越小越稳定 |",
        "",
        "## 分 Regime 统计",
        "",
    ]

    if result.regime_winrates:
        report_lines.extend(
            [
                "| Regime | 胜率 | 说明 |",
                "|--------|------|------|",
            ]
        )
        for regime, wr in sorted(result.regime_winrates.items()):
            desc = _regime_description(regime)
            report_lines.append(f"| {regime} | {wr:.2%} | {desc} |")
    else:
        report_lines.append("*无 regime 数据*")

    report_lines.extend(
        [
            "",
            "## 分阶段表现",
            "",
            "| 阶段 | 收益 | Sharpe | 胜率 | 交易次数 | 不可成交 |",
            "|------|------|--------|------|----------|----------|",
        ]
    )

    for period in result.periods:
        report_lines.append(
            f"| {period.period} | {period.total_return:.2%} | {period.sharpe_ratio:.2f} | "
            f"{period.win_rate:.2%} | {period.trades} | {period.not_executable} |"
        )

    report_lines.extend(
        [
            "",
            "## 结论",
            "",
        ]
    )

    if both_pass:
        report_lines.append(
            f"✅ **{verdict}**: DSR={result.deflated_sharpe:.4f} > 1.0 且 PBO={result.pbo:.2%} < 50%,可以考虑实盘使用。"
        )
    else:
        reasons = []
        if not dsr_pass:
            reasons.append(f"DSR={result.deflated_sharpe:.4f} < 1.0")
        if not pbo_pass:
            if not pbo_is_valid:
                reasons.append(
                    f"PBO={result.pbo:.2%}（占位值，未经有效 CSCV 验证——"
                    "单策略回测无法做 CSCV，需用 grid 多变体网格，见宪法 §17.7）"
                )
            else:
                reasons.append(f"PBO={result.pbo:.2%} > 50%")
        report_lines.append(f"❌ **{verdict}**: {'，'.join(reasons)}，不建议实盘使用。")

    report = "\n".join(report_lines)

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(report, encoding="utf-8")
    print(report)
    print(f"\n报告已保存到: {args.report}")

    # 写双门 sidecar（不依赖 args.update_yaml，始终写，供 notify gate 用）
    _write_walkforward_gate(
        dsr=result.deflated_sharpe,
        pbo=result.pbo,
        run_date=today_shanghai().isoformat(),
        start=args.start,
        end=args.end,
        n_periods=len(result.periods),
    )

    if args.update_yaml:
        ok = _update_thresholds_metadata(today_shanghai().isoformat())
        if ok:
            print(
                f"✅ thresholds.yaml 的 last_walkforward_run 已更新为 {today_shanghai().isoformat()}"
            )
        else:
            print("⚠️  thresholds.yaml 中未找到 last_walkforward_run 字段，跳过更新")
        print(
            "   注意：阈值参数若有变更应同时手动升 version 并设 effective_from（architecture §4）"
        )

    if logger:
        logger.info("Walk-forward 完成，报告: %s", args.report)

    return 0


def run_briefing(args: argparse.Namespace) -> int:
    from aqsp.briefing import BriefingGenerator, enhance_briefing, send_briefing
    from aqsp.ledger.base import read_ledger

    rows = read_ledger(args.ledger)
    latest_date = ""
    for row in reversed(rows):
        if row.get("status") in ("pending", "validated", "watch_only"):
            latest_date = row.get("signal_date", "")
            break

    symbol_name_map: dict[str, str] = {}
    for row in rows:
        symbol = str(row.get("symbol", "")).strip()
        name = str(row.get("name", "")).strip()
        if symbol and has_meaningful_name(symbol, name):
            symbol_name_map[symbol] = name

    picks: list[PickResult] = []
    for row in rows:
        if row.get("signal_date") != latest_date:
            continue
        if row.get("status") not in ("pending", "validated", "watch_only"):
            continue
        symbol = str(row.get("symbol", ""))
        name = str(row.get("name", ""))
        if not has_meaningful_name(symbol, name):
            name = symbol_name_map.get(symbol, name)
        picks.append(
            PickResult(
                symbol=symbol,
                name=name,
                date=str(row.get("signal_date", "")),
                close=float(row.get("signal_close", 0)),
                score=float(row.get("score", 0)),
                rating=str(row.get("rating", "")),
                entry_type="next_open",
                ideal_buy=float(
                    row.get("intended_entry_price", row.get("signal_close", 0))
                ),
                stop_loss=float(row.get("stop_loss", 0)),
                take_profit=float(row.get("take_profit", 0)),
                position=str(row.get("position", "")),
                strategies=tuple(row.get("strategies", [])),
                reasons=tuple(row.get("reasons", [])),
                risks=tuple(row.get("risks", [])),
                metrics={
                    "portfolio_action": str(row.get("portfolio_action", "") or ""),
                    "stop_method": str(row.get("stop_method", "") or ""),
                },
                adjusted_score=float(row.get("adjusted_score", 0) or 0),
                recommended_adjustment=str(
                    row.get("recommended_adjustment", "keep") or "keep"
                ),
                debate_consensus=str(row.get("debate_consensus", "") or ""),
                confidence=float(row.get("confidence", 0) or 0),
                regime_score=float(row.get("regime_score", 0) or 0),
            )
        )

    regime_str = ""
    for row in rows:
        if row.get("regime_at_signal"):
            regime_str = str(row["regime_at_signal"])
    latest_source_row = next(
        (
            row
            for row in reversed(rows)
            if row.get("run_requested_source") or row.get("run_actual_source")
        ),
        None,
    )

    source_status = None
    if latest_source_row is not None:
        source_status = {
            "requested_source": str(
                latest_source_row.get("run_requested_source", "") or ""
            ),
            "actual_source": str(latest_source_row.get("run_actual_source", "") or ""),
            "freshness_tier": str(
                latest_source_row.get("run_source_freshness_tier", "") or ""
            ),
            "coverage_tier": str(
                latest_source_row.get("run_source_coverage_tier", "") or ""
            ),
            "health_label": str(
                latest_source_row.get("run_source_health_label", "") or ""
            ),
            "health_message": str(
                latest_source_row.get("run_source_health_message", "") or ""
            ),
            "fallback_used": bool(latest_source_row.get("run_fallback_used", False)),
        }

    picks = _enrich_pick_names(picks)

    portfolio_summary = None
    if picks:
        from aqsp.portfolio.manager import PortfolioDecision, summarize_portfolio_decisions

        portfolio_summary = summarize_portfolio_decisions(
            picks,
            [
                PortfolioDecision(
                    symbol=pick.symbol,
                    action=str(pick.metrics.get("portfolio_action", "keep") or "keep"),
                    score_delta=0.0,
                    reasons=("保持原排序",),
                )
                for pick in picks
            ],
        )

    generator = BriefingGenerator()
    research_summary = load_research_summary()
    briefing = generator.generate(
        picks=picks,
        frames={},
        regime=regime_str,
        source_status=source_status,
        research_summary=research_summary,
        portfolio_summary=portfolio_summary,
    )
    briefing = enhance_briefing(briefing, enable_llm=args.enable_llm)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(briefing.to_markdown(), encoding="utf-8")
    print(briefing.to_markdown())

    if args.notify:
        send_briefing(briefing, source_status=source_status)

    if getattr(args, "email", False):
        from aqsp.briefing.email_notifier import (
            load_email_config_from_env,
            send_briefing_email,
        )

        cfg = load_email_config_from_env()
        if cfg is None:
            print("⚠️  --email 已开启但 AQSP_SMTP_* 环境变量不全，跳过邮件发送")
        else:
            from datetime import date as _date

            body = Path(args.output).read_text(encoding="utf-8")
            ok = send_briefing_email(
                cfg=cfg,
                subject=f"aqsp briefing {_date.today().isoformat()}",
                markdown_body=body,
            )
            print("✅ 邮件已发送" if ok else "❌ 邮件发送失败")

    return 0


def run_monitor(args: argparse.Namespace) -> int:
    from aqsp.monitor.checker import MonitorChecker
    from aqsp.monitor.notifier import format_alert, send_alerts

    checker = MonitorChecker(config_path=args.config)
    results = checker.check_all()

    triggered = [r for r in results if r.triggered]
    if not triggered:
        print("✅ 所有监控项正常")
        return 0

    alert_msg = format_alert(triggered)
    print(alert_msg)

    if args.notify and not args.dry_run:
        notify_targets = triggered
        if args.notify_critical_only:
            notify_targets = [r for r in triggered if r.severity == "critical"]
        if notify_targets:
            send_alerts(notify_targets)

    return 1 if any(r.severity == "critical" for r in triggered) else 0


def run_optimize(args: argparse.Namespace) -> int:
    from aqsp.optimizer.param_optimizer import (
        BayesianOptimizer,
        GridSearchOptimizer,
        ParamSpace,
        create_walkforward_evaluator,
    )

    thresholds = load_thresholds()
    default_spaces = [
        ParamSpace("composite.momentum_weight", 0.1, 0.5, 0.05),
        ParamSpace("composite.quality_weight", 0.0, 0.4, 0.05),
        ParamSpace("composite.value_weight", 0.0, 0.4, 0.05),
        ParamSpace("composite.volume_weight", 0.0, 0.3, 0.05),
        ParamSpace("composite.triple_rise_weight", 0.0, 0.5, 0.05),
        ParamSpace("scoring.near_high_bonus", 10.0, 25.0, 2.0),
        ParamSpace("scoring.pullback_bonus", 8.0, 24.0, 2.0),
        ParamSpace("scoring.rsi_healthy_bonus", 3.0, 12.0, 1.0),
    ]

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        symbols = _get_hs300_symbols(date.fromisoformat(args.start))[:30]

    print(f"正在获取 {len(symbols)} 只股票数据...")
    frames = _fetch_frames_for_cli(
        args.source,
        symbols,
        benchmark_symbol=None,
        days=_walkforward_fetch_days(args.start, args.end),
    )
    filtered: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = frames.get(sym)
        if df is None or df.empty:
            continue
        mask = (df["date"].astype(str) >= args.start) & (
            df["date"].astype(str) <= args.end
        )
        sliced = df.loc[mask]
        if len(sliced) >= 100:
            filtered[sym] = sliced.copy()
    symbols = list(filtered.keys())
    print(f"有效标的: {len(symbols)} 只")

    if len(symbols) < 5:
        print("有效标的不足，无法进行优化")
        return 1

    evaluate_fn = create_walkforward_evaluator(
        symbols=symbols,
        frames=frames,
        start=args.start,
        end=args.end,
        engine=args.engine or load_runtime_config().research_engine,
    )

    print(f"开始 {args.method} 参数优化，trials={args.trials}...")
    if args.method == "grid":
        optimizer = GridSearchOptimizer(default_spaces)
        result = optimizer.optimize(evaluate_fn, max_trials=args.trials)
    else:
        optimizer = BayesianOptimizer(default_spaces)
        result = optimizer.optimize(evaluate_fn, n_trials=args.trials)

    _print_optimization_result(result)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "method": result.method,
        "n_trials": result.n_trials,
        "best_score": result.best_score,
        "best_params": result.best_params,
        "thresholds_version": thresholds.version,
        "run_date": now_shanghai().isoformat(timespec="seconds"),
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果已保存到: {output_path}")

    if args.apply:
        _apply_best_params(result.best_params)

    return 0


def _print_optimization_result(result: Any) -> None:
    print(f"\n{'=' * 50}")
    print(f"优化完成 ({result.method}), 共 {result.n_trials} 次试验")
    print(f"{'=' * 50}")
    print(f"最优分数: {result.best_score:.4f}")
    print("最优参数:")
    for k, v in sorted(result.best_params.items()):
        print(f"  {k}: {v}")


def _apply_best_params(best_params: dict[str, float]) -> None:
    import re

    path = _find_thresholds_yaml()
    if path is None:
        print("⚠️  找不到 thresholds.yaml，无法自动应用参数")
        return

    content = path.read_text(encoding="utf-8")
    new_version = f"{now_shanghai().strftime('%Y%m%d')}.opt"

    for key, val in best_params.items():
        parts = key.split(".")
        if len(parts) == 2:
            section, field = parts
            pattern = re.compile(
                rf"^({field}:\s*)(['\"]?)(.*?)\2(\s*(?:#.*)?)$",
                flags=re.MULTILINE,
            )
            new_content, n = pattern.subn(
                lambda m, v=val: f"{m.group(1)}{m.group(2)}{v}{m.group(2)}{m.group(4)}",
                content,
            )
            if n > 0:
                content = new_content

    version_pattern = re.compile(
        r"^(version:\s*)(['\"]?)(.*?)\2(\s*(?:#.*)?)$",
        flags=re.MULTILINE,
    )
    content = version_pattern.sub(
        lambda m: f'{m.group(1)}"{new_version}"{m.group(4)}',
        content,
    )
    path.write_text(content, encoding="utf-8")
    print(f"✅ 最优参数已写入 thresholds.yaml (version={new_version})")


def run_discover(args: argparse.Namespace) -> int:
    from aqsp.ledger.base import read_ledger
    from aqsp.optimizer.pattern_discovery import (
        PatternDiscoveryEngine,
        format_discovered_patterns,
    )

    rows = read_ledger(args.ledger)
    if not rows:
        print("Ledger 为空，无法发现形态")
        return 1
    ledger_df = pd.DataFrame(rows)

    symbols_in_ledger: list[str] = []
    seen: set[str] = set()
    for row in rows:
        sym = str(row.get("symbol", ""))
        if sym and sym not in seen:
            seen.add(sym)
            symbols_in_ledger.append(sym)

    if not symbols_in_ledger:
        print("Ledger 中没有有效标的")
        return 1

    print(f"正在获取 {len(symbols_in_ledger)} 只标的的 OHLCV 数据...")
    try:
        frames = _fetch_frames_for_cli(
            args.source,
            symbols_in_ledger,
            benchmark_symbol=None,
            days=500,
        )
    except Exception:
        frames = {}

    if not frames:
        print("无法获取数据")
        return 1

    print(f"数据获取完成，{len(frames)} 只标的可用")

    engine = PatternDiscoveryEngine(
        min_sample_size=args.min_sample,
        min_win_rate=args.min_winrate,
    )
    patterns = engine.discover(ledger_df, frames)

    report = format_discovered_patterns(patterns)
    print(report)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        import json as _json

        payload = [
            {
                "pattern_id": p.pattern_id,
                "pattern_type": p.pattern_type,
                "description": p.description,
                "conditions": p.conditions,
                "historical_win_rate": p.historical_win_rate,
                "historical_avg_return": p.historical_avg_return,
                "sample_size": p.sample_size,
                "confidence": p.confidence,
                "first_seen": p.first_seen,
                "last_seen": p.last_seen,
            }
            for p in patterns
        ]
        output_path.write_text(
            _json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON 已保存到: {output_path}")

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        print(f"报告已保存到: {report_path}")

    return 0


def run_mine_factors(args: argparse.Namespace) -> int:
    from aqsp.strategies.auto_factor_mining import AutoFactorMiner, FactorLibrary

    print("开始自动因子挖掘...")
    miner = AutoFactorMiner(min_ic=args.min_ic, min_ir=args.min_ir)
    explicit_symbols = os.getenv("AQSP_SYMBOLS", "").strip()

    symbols = _resolve_run_symbols(
        args.source,
        explicit_symbols,
        pool_name="sh300",
        max_universe=300,
        min_avg_amount=10_000_000,
    )
    if not symbols:
        print("无法解析股票池")
        return 1

    print(f"正在获取 {len(symbols)} 只股票数据...")
    try:
        frames = _fetch_frames_for_cli(
            args.source,
            symbols,
            benchmark_symbol=None,
            days=250,
        )
    except Exception:
        frames = {}

    if not frames:
        print("无法获取数据")
        return 1

    print(f"数据获取完成，{len(frames)} 只股票可用")
    print("开始挖掘因子...")

    results = miner.mine_factors(frames)

    if not results:
        print("未发现有效因子")
        return 0

    print(f"\n发现 {len(results)} 个有效因子:")
    for r in results[:20]:
        print(
            f"  - {r['factor_name']}: IC={r['ic_mean']:.4f}, IR={r['ic_ir']:.2f}, 胜率={r['win_rate']:.2%}"
        )

    library = FactorLibrary()
    library.load()
    added_count = 0
    for r in results:
        if library.add_factor(r):
            added_count += 1
    library.save()

    print(f"\n已将 {added_count} 个新因子添加到因子库")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"结果已保存到: {output_path}")

    return 0


def run_evolve(args: argparse.Namespace) -> int:
    from aqsp.strategies.auto_evolution import AutoEvolution

    print("开始自动进化...")
    evolution = AutoEvolution(config_path=args.config)

    if not evolution.config.enabled:
        print("自动进化已禁用")
        return 0

    print("分析当前策略表现...")
    explicit_symbols = os.getenv("AQSP_SYMBOLS", "").strip()
    symbols = _resolve_run_symbols(
        args.source,
        explicit_symbols,
        pool_name="sh300",
        max_universe=300,
        min_avg_amount=10_000_000,
    )
    if not symbols:
        print("无法解析股票池")
        return 1

    try:
        frames = _fetch_frames_for_cli(
            args.source,
            symbols,
            benchmark_symbol=None,
            days=250,
        )
    except Exception:
        frames = {}

    if not frames:
        print("无法获取数据")
        return 1

    print("正在进化参数...")
    result = evolution.evolve_parameters("composite", frames)

    if result:
        print("\n进化完成:")
        print(f"  策略: {result.strategy_name}")
        print(f"  性能提升: {result.performance_improvement:.2%}")
        print(f"  置信度: {result.confidence:.2%}")
        print(f"  原因: {result.reason}")

        if args.apply and result.confidence >= evolution.config.confidence_threshold:
            print("\n正在应用进化后的参数...")
            evolution._apply_evolution(result)
            print("参数已应用到 thresholds.yaml")
        else:
            print("\n置信度不足，未自动应用参数")
    else:
        print("当前无需进化")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "strategy_name": result.strategy_name if result else "",
            "old_params": result.old_params if result else {},
            "new_params": result.new_params if result else {},
            "performance_improvement": result.performance_improvement if result else 0,
            "confidence": result.confidence if result else 0,
            "reason": result.reason if result else "",
            "timestamp": now_shanghai().isoformat(),
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"结果已保存到: {output_path}")

    return 0


def run_multi_factor(args: argparse.Namespace) -> int:
    from aqsp.strategies.multi_factor_rotation import MultiFactorRotationStrategy

    print("运行多因子轮动策略...")
    strategy = MultiFactorRotationStrategy()

    symbols = _resolve_run_symbols(
        args.source,
        "",
        pool_name=args.pool,
        max_universe=300,
        min_avg_amount=10_000_000,
    )
    if not symbols:
        print("无法解析股票池")
        return 1

    print(f"正在获取 {len(symbols)} 只股票数据...")
    try:
        frames = _fetch_frames_for_cli(
            args.source,
            symbols,
            benchmark_symbol=None,
            days=250,
        )
    except Exception:
        frames = {}

    if not frames:
        print("无法获取数据")
        return 1

    print(f"数据获取完成，{len(frames)} 只股票可用")
    print("计算多因子得分...")

    scores = strategy.calculate_score(frames)

    if not scores:
        print("无法计算得分")
        return 1

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_n = sorted_scores[: args.top]

    print(f"\nTop {args.top} 股票:")
    for rank, (symbol, score) in enumerate(top_n, 1):
        name = ""
        if symbol in frames and "name" in frames[symbol].columns:
            name = frames[symbol]["name"].iloc[0] if not frames[symbol].empty else ""
        print(f"  {rank}. {symbol} {name}: {score:.2f}")

    effectiveness = strategy.get_factor_effectiveness()
    if effectiveness:
        print("\n因子有效性:")
        for factor, eff in sorted(
            effectiveness.items(), key=lambda x: x[1], reverse=True
        )[:10]:
            print(f"  - {factor}: {eff:.2%}")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "top_stocks": [{"symbol": s, "score": sc} for s, sc in top_n],
            "all_scores": dict(sorted_scores),
            "factor_effectiveness": effectiveness,
            "timestamp": now_shanghai().isoformat(),
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结果已保存到: {output_path}")

    return 0


def run_morning_breakout(args: argparse.Namespace) -> int:
    from aqsp.strategies.morning_breakout import (
        MorningBreakoutStrategy,
        format_morning_signals,
    )

    print("🔥 运行早盘打板策略...")
    strategy = MorningBreakoutStrategy()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if symbols:
        symbols = list(dict.fromkeys(symbols))
    else:
        symbols = _resolve_run_symbols(
            args.source,
            "",
            pool_name=args.pool,
            max_universe=300,
            min_avg_amount=10_000_000,
        )
    if not symbols:
        print("无法解析股票池")
        return 1

    print(f"正在获取 {len(symbols)} 只股票数据...")
    try:
        frames = _fetch_frames_for_cli(
            args.source,
            symbols,
            benchmark_symbol=None,
            days=250,
        )
    except Exception:
        frames = {}

    if not frames:
        print("无法获取数据")
        return 1

    print(f"数据获取完成，{len(frames)} 只股票可用")
    print("分析打板信号...")

    signals = strategy.analyze_pre_market(frames)

    report = format_morning_signals(signals, top_n=args.top)
    print(report)

    if args.notify and signals:
        try:
            notify_markdown(
                build_morning_breakout_notification(
                    signals,
                    mode=load_runtime_config().notify_mode,
                    top_n=args.top,
                )
            )
        except Exception:
            pass

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "signals": [
                {
                    "symbol": s.symbol,
                    "name": s.name,
                    "signal_type": s.signal_type,
                    "score": s.score,
                    "current_price": s.current_price,
                    "target_price": s.target_price,
                    "stop_loss": s.stop_loss,
                    "confidence": s.confidence,
                    "entry_time": s.entry_time,
                    "position_pct": s.position_pct,
                    "reasons": list(s.reasons),
                    "risks": list(s.risks),
                }
                for s in signals[: args.top]
            ],
            "timestamp": now_shanghai().isoformat(),
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结果已保存到: {output_path}")

    from aqsp.ledger.base import read_ledger, write_ledger
    from uuid import uuid4

    ledger_path = getattr(args, "ledger", "data/predictions.jsonl")
    rows = read_ledger(ledger_path)
    existing_keys = {
        (
            str(r.get("signal_date", "")),
            str(r.get("symbol", "")),
            str(r.get("thresholds_version", "")),
        )
        for r in rows
    }
    thresholds_version = strategy.thresholds.version
    now = now_shanghai().isoformat(timespec="seconds")
    signal_date = now_shanghai().date().isoformat()
    for signal in signals:
        key = (signal_date, signal.symbol, thresholds_version)
        if key in existing_keys:
            continue
        rows.append(
            {
                "id": uuid4().hex,
                "created_at": now,
                "signal_date": signal_date,
                "symbol": signal.symbol,
                "name": signal.name,
                "signal_close": signal.current_price,
                "intended_entry": "next_open",
                "score": signal.score,
                "rating": "buy_candidate",
                "strategies": ["morning_breakout"],
                "sub_strategy": signal.signal_type,
                "reasons": list(signal.reasons),
                "risks": list(signal.risks),
                "stop_loss": signal.stop_loss,
                "confidence": signal.confidence,
                "thresholds_version": thresholds_version,
                "status": "pending",
            }
        )
        existing_keys.add(key)
    write_ledger(ledger_path, rows)

    return 0


def run_closing_premium(args: argparse.Namespace) -> int:
    from aqsp.strategies.closing_premium import (
        ClosingPremiumStrategy,
        format_closing_signals,
    )

    print("📈 运行尾盘溢价策略...")
    strategy = ClosingPremiumStrategy()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if symbols:
        symbols = list(dict.fromkeys(symbols))
    else:
        symbols = _resolve_run_symbols(
            args.source,
            "",
            pool_name=args.pool,
            max_universe=300,
            min_avg_amount=10_000_000,
        )
    if not symbols:
        print("无法解析股票池")
        return 1

    print(f"正在获取 {len(symbols)} 只股票数据...")
    try:
        frames = _fetch_frames_for_cli(
            args.source,
            symbols,
            benchmark_symbol=None,
            days=250,
        )
    except Exception:
        frames = {}

    if not frames:
        print("无法获取数据")
        return 1

    print(f"数据获取完成，{len(frames)} 只股票可用")
    print("分析溢价信号...")

    signals = strategy.analyze_closing(frames)

    report = format_closing_signals(signals, top_n=args.top)
    print(report)

    if args.notify and signals:
        try:
            notify_markdown(
                build_closing_premium_notification(
                    signals,
                    mode=load_runtime_config().notify_mode,
                    top_n=args.top,
                )
            )
        except Exception:
            pass

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "signals": [
                {
                    "symbol": s.symbol,
                    "name": s.name,
                    "signal_type": s.signal_type,
                    "score": s.score,
                    "current_price": s.current_price,
                    "entry_price": s.entry_price,
                    "stop_loss": s.stop_loss,
                    "take_profit_1": s.take_profit_1,
                    "take_profit_2": s.take_profit_2,
                    "confidence": s.confidence,
                    "holding_days": s.holding_days,
                    "expected_return": s.expected_return,
                    "reasons": list(s.reasons),
                    "risks": list(s.risks),
                }
                for s in signals[: args.top]
            ],
            "timestamp": now_shanghai().isoformat(),
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n结果已保存到: {output_path}")

    from aqsp.ledger.base import read_ledger, write_ledger
    from uuid import uuid4

    ledger_path = getattr(args, "ledger", "data/predictions.jsonl")
    rows = read_ledger(ledger_path)
    existing_keys = {
        (
            str(r.get("signal_date", "")),
            str(r.get("symbol", "")),
            str(r.get("thresholds_version", "")),
        )
        for r in rows
    }
    thresholds_version = strategy.thresholds.version
    now = now_shanghai().isoformat(timespec="seconds")
    signal_date = now_shanghai().date().isoformat()
    for signal in signals:
        key = (signal_date, signal.symbol, thresholds_version)
        if key in existing_keys:
            continue
        rows.append(
            {
                "id": uuid4().hex,
                "created_at": now,
                "signal_date": signal_date,
                "symbol": signal.symbol,
                "name": signal.name,
                "signal_close": signal.entry_price,
                "intended_entry": "next_open",
                "score": signal.score,
                "rating": "buy_candidate",
                "strategies": ["closing_premium"],
                "sub_strategy": signal.signal_type,
                "reasons": list(signal.reasons),
                "risks": list(signal.risks),
                "stop_loss": signal.stop_loss,
                "confidence": signal.confidence,
                "thresholds_version": thresholds_version,
                "status": "pending",
            }
        )
        existing_keys.add(key)
    write_ledger(ledger_path, rows)

    return 0


def run_closing_review(args: argparse.Namespace) -> int:
    from aqsp.briefing.closing_review import (
        ClosingReviewer,
        format_daily_review,
        format_weekly_summary,
    )

    print("📊 生成收盘复盘报告...")
    reviewer = ClosingReviewer(ledger_path="data/predictions.jsonl")

    if args.weekly:
        summary = reviewer.generate_weekly_summary(args.date or None)
        report = format_weekly_summary(summary)
    else:
        review = reviewer.review_today(args.date or None)
        report = format_daily_review(review)

    print(report)

    if args.notify:
        try:
            notify_markdown(
                build_closing_review_notification(
                    review=review if not args.weekly else None,
                    weekly_summary=summary if args.weekly else None,
                    mode=load_runtime_config().notify_mode,
                )
            )
        except Exception:
            pass

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"\n报告已保存到: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
