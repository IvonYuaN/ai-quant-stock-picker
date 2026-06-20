from __future__ import annotations

import argparse
import json
import logging
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
from aqsp.data import (
    fetch_akshare,
    fetch_frames_for_cli_with_metadata,
    fetch_with_source,
    load_csv,
)
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
from aqsp.data.trading_calendar import trading_day_lag
from aqsp.filters_lethal.pipeline import LethalFilterPipeline
from aqsp.freshness import assert_fresh_data, latest_trade_date
from aqsp.ledger import (
    ExecutionConfig,
    append_predictions,
    compute_real_pnl,
    count_independent_signal_days,
    strategy_weights_from_ledger,  # noqa: F401 - kept for legacy monkeypatches.
    validate_predictions,
)
from aqsp.models import ScreeningConfig
from aqsp.notify_templates import (
    build_daily_run_notification,
    build_closing_premium_notification,
    build_closing_review_notification,
    build_morning_breakout_notification,
)
from aqsp.notification_runtime import (
    dispatch_notification_once as _dispatch_notification_once_impl,
    dispatch_scheduled_daily_notification,
    finalize_scheduled_notification,
    finalize_scheduled_outputs,
)
from aqsp.notifier import (
    notify_gate_markdown,
    notify_markdown as _notify_markdown_default,
    notify_markdown_via_config,
    print_notify_results,
)
from aqsp.research.summary import load_research_summary
from aqsp.research_engine import (
    ENGINE_CHOICES,
    WalkForwardEngineConfig,
    resolve_walkforward_engine,
)
from aqsp.regime import build_synthetic_regime_frame, detect_runtime_regime
from aqsp.report import to_dataframe, to_markdown
from aqsp.risk.circuit_breaker import CircuitBreaker
from aqsp.runtime.gate_notify import (
    build_gate_notification_markdown,
    mark_gate_notification_sent,
    should_send_gate_notification,
)
from aqsp.strategy import screen_universe
from aqsp.strategies.thresholds import load_thresholds
from aqsp.universe import DEFAULT_SYMBOLS
from aqsp.utils.env import read_env_value
from aqsp.walkforward_gate import (
    MAX_GATE_AGE_DAYS,
    WalkForwardGateValidation,
    build_walkforward_gate_payload,
    validate_walkforward_gate_payload,
)
from aqsp.briefing.debate import (
    AShareDebateCoordinator,
    DebateResult,
    parse_agent_roles,
)
from aqsp.models import PickResult
from aqsp.presentation import format_symbol_name, has_meaningful_name

LOGGER = logging.getLogger(__name__)
notify_markdown = _notify_markdown_default


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
GATE_NOTIFY_STATE_PATH = "data/gate_notify_state.json"
NOTIFY_STATE_PATH = "data/notify_state.json"
# 冷启动期最低独立信号日。宪法 §1.3 #7/#14 明确要求 30 个独立信号日。
# 可用环境变量 AQSP_COLD_START_MIN_DAYS 覆盖（仅供测试加速，生产须为 30）。
COLD_START_MIN_DAYS = int(os.getenv("AQSP_COLD_START_MIN_DAYS", "30"))


def _resolve_runtime_state_path(path: str) -> str:
    state_path = Path(path)
    if state_path.is_absolute():
        return str(state_path)
    project_root = Path(__file__).resolve().parents[2]
    return str(project_root / state_path)


def _notify_via_config(markdown: str, *, mode: str) -> list:
    if notify_markdown is not _notify_markdown_default:
        return notify_markdown(markdown)
    return notify_markdown_via_config(markdown, mode=mode)


def _dispatch_notification_once(
    markdown: str,
    *,
    prefix: str,
    mode: str,
    kind: str,
    summary_markdown: str | None = None,
) -> list:
    if notify_markdown is not _notify_markdown_default:
        payload = (
            summary_markdown
            if str(mode).strip().lower() == "summary" and summary_markdown
            else markdown
        )
        results = notify_markdown(payload)
        print_notify_results(results, prefix=prefix)
        return results
    return _dispatch_notification_once_impl(
        markdown,
        mode=mode,
        prefix=prefix,
        kind=kind,
        state_path=_resolve_runtime_state_path(
            os.getenv("AQSP_NOTIFY_STATE_PATH", NOTIFY_STATE_PATH)
        ),
        summary_markdown=summary_markdown,
    )


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
    db_path = _resolve_sqlite_db_path()
    try:
        source = SqliteDbSource(db_path=db_path) if db_path else SqliteDbSource()
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


def _resolve_sqlite_db_path() -> str | None:
    db_candidates = [
        os.getenv("AQSP_SQLITE_DB_PATH", "").strip(),
        read_env_value(".env", "AQSP_SQLITE_DB_PATH"),
        "/opt/market-data/astocks_qfq.db",
        "A股量化分析数据/astocks_qfq.db",
    ]
    return next((str(p) for p in db_candidates if p and Path(str(p)).exists()), None)


def _build_sqlite_db_source(*, cache: DataCache | None) -> SqliteDbSource:
    db_path = _resolve_sqlite_db_path()
    if db_path:
        try:
            return SqliteDbSource(db_path=db_path, cache=cache)
        except TypeError:
            return SqliteDbSource(cache=cache)
    return SqliteDbSource(cache=cache)


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
        "run",
        aliases=["run-scheduled"],
        help="scheduled screen with freshness check and optional notification",
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
        help="启用分级止损（3.1%%硬止损+分级减仓）",
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

    news_cmd = sub.add_parser(
        "news-catalysts", help="summarize high-impact market news catalysts"
    )
    news_cmd.add_argument("--symbols", default="")
    news_cmd.add_argument("--names", default="")
    news_cmd.add_argument("--notify", action="store_true")
    news_cmd.add_argument("--output", default="")
    news_cmd.add_argument("--max-events", type=int, default=8)
    news_cmd.add_argument("--source-timeout-seconds", type=float, default=8.0)
    news_cmd.add_argument("--llm-timeout-seconds", type=float, default=8.0)
    news_cmd.add_argument("--max-llm-review-events", type=int, default=3)
    news_cmd.add_argument("--enable-llm-review", action="store_true")

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
        help="proposal-only; never writes thresholds.yaml automatically",
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
        "--apply",
        action="store_true",
        help="proposal-only; never writes thresholds.yaml automatically",
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
        if args.command in {"run", "run-scheduled"}:
            return run_scheduled(args)
        if args.command == "dashboard":
            return run_dashboard(args)
        if args.command == "walkforward":
            return run_walkforward(args)
        if args.command == "briefing":
            return run_briefing(args)
        if args.command == "monitor":
            return run_monitor(args)
        if args.command == "news-catalysts":
            return run_news_catalysts(args)
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


def _runtime_data_lag_days(
    latest: date | None,
    *,
    reference_day: date | None = None,
) -> int:
    if latest is None:
        return 0
    return trading_day_lag(latest, reference_day or today_shanghai())


def _source_runtime_metadata(
    source_id: str,
    *,
    latest_trade_day: date | None = None,
    reference_day: date | None = None,
) -> tuple[str, str, str]:
    entry = get_registry_entry(source_id)
    if entry is None:
        return "unknown", "unknown", "unknown"
    freshness_tier = entry.freshness_tier
    if latest_trade_day is not None:
        lag_days = _runtime_data_lag_days(
            latest_trade_day,
            reference_day=reference_day,
        )
        if lag_days > 0 and freshness_tier in {
            "terminal_realtime",
            "realtime",
            "delayed_realtime",
        }:
            freshness_tier = "end_of_day"
    return (
        freshness_tier,
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
    return fetch_frames_for_cli_with_metadata(
        source_name,
        symbols,
        benchmark_symbol=benchmark_symbol,
        cache_path=cache_path,
        days=days,
        fetch_akshare_fn=fetch_akshare,
        get_source_fn=_get_source,
        fetch_with_source_fn=fetch_with_source,
        record_source_success_fn=record_source_success,
        record_source_failure_fn=record_source_failure,
    )


def _drop_benchmark_frame(
    frames: dict[str, pd.DataFrame],
    benchmark_symbol: str | None,
) -> dict[str, pd.DataFrame]:
    if not benchmark_symbol:
        return frames
    return {symbol: df for symbol, df in frames.items() if symbol != benchmark_symbol}


def _build_synthetic_regime_frame(
    frames: dict[str, pd.DataFrame],
) -> pd.DataFrame | None:
    return build_synthetic_regime_frame(frames)


def _detect_runtime_regime(
    frames: dict[str, pd.DataFrame],
    *,
    benchmark_symbol: str | None,
) -> str:
    return detect_runtime_regime(
        frames,
        benchmark_symbol=benchmark_symbol,
    )


def _augment_summary_with_t1_blockers(
    summary: Any | None,
    *,
    removed_symbols: list[str],
    removed_name_map: dict[str, str],
) -> Any | None:
    if summary is None or not removed_symbols:
        return summary

    removed_displays = tuple(
        format_symbol_name(symbol, removed_name_map.get(symbol, ""))
        for symbol in removed_symbols
    )
    hotspot = "T+1 持仓约束：昨日已买标的今日不纳入纸面复核名单"
    blockers = tuple(
        f"{display}: T+1 持仓约束，昨日已买，今日仅保留观察"
        for display in removed_displays
    )
    existing_watchlist = tuple(getattr(summary, "watchlist", ()) or ())
    existing_hotspots = tuple(getattr(summary, "action_hotspots", ()) or ())
    existing_blockers = tuple(getattr(summary, "execution_blockers", ()) or ())
    merged_watchlist = tuple(
        dict.fromkeys(existing_watchlist + removed_displays).keys()
    )[:5]
    merged_hotspots = tuple(dict.fromkeys(existing_hotspots + (hotspot,)).keys())[:3]
    merged_blockers = tuple(dict.fromkeys(existing_blockers + blockers).keys())[:5]
    note = str(getattr(summary, "allocation_note", "") or "")
    t1_note = (
        f"T+1 限制：昨日已买 {len(removed_symbols)} 只"
        f"（{'、'.join(removed_symbols[:3])}）仅保留观察"
    )
    merged_note = f"{note}；{t1_note}" if note else t1_note
    return replace(
        summary,
        watchlist=merged_watchlist,
        action_hotspots=merged_hotspots,
        execution_blockers=merged_blockers,
        allocation_note=merged_note,
    )


def _build_execution_summary_line(
    tradable: list[PickResult],
    portfolio_summary: Any | None,
) -> str:
    has_allocations = bool(getattr(portfolio_summary, "allocations", ()) or ())
    if tradable and has_allocations:
        top = tradable[0]
        return (
            f"🎯 **优先纸面复核**: {top.symbol} {top.name} | 评分 {top.score:.0f} | "
            f"观察参考 {top.ideal_buy} / 防守 {top.stop_loss} / 目标 {top.take_profit}"
        )
    watchlist = tuple(getattr(portfolio_summary, "watchlist", ()) or ())
    blockers = tuple(getattr(portfolio_summary, "execution_blockers", ()) or ())
    if watchlist:
        names = "、".join(watchlist[:2])
        return f"👀 **今日无纸面复核对象**，转入继续观察名单：{names}"
    if tradable:
        top = tradable[0]
        return (
            f"👀 **首位观察**: {top.symbol} {top.name} | 评分 {top.score:.0f} | "
            "等待 PM 阻塞解除"
        )
    if blockers:
        return "👀 **今日无纸面复核对象**，受纸面约束影响，暂仅观察。"
    return "👀 **今日无纸面复核对象**，仅观察。等待更强信号。"


def _resolve_audit_action(
    pick: PickResult,
    *,
    allocation_symbols: set[str],
) -> str:
    if pick.symbol in allocation_symbols:
        return "PAPER_REVIEW"
    return "SKIP"


def _build_execution_preview(
    pick: PickResult,
    *,
    frame: pd.DataFrame,
    action: str,
) -> dict[str, Any]:
    if action != "PAPER_REVIEW" or frame.empty:
        return {}

    recent_frame = frame.tail(20).copy()
    if "volume" not in recent_frame.columns or "close" not in recent_frame.columns:
        return {}

    avg_daily_volume = float(recent_frame["volume"].fillna(0).mean() or 0.0)
    estimated_price = float(pick.ideal_buy or pick.close or 0.0)
    if avg_daily_volume <= 0 or estimated_price <= 0:
        return {}

    from aqsp.execution.executor import ExecutionCoordinator

    coordinator = ExecutionCoordinator()
    plan = coordinator.plan_execution(
        symbol=pick.symbol,
        target_shares=100,
        avg_daily_volume=avg_daily_volume,
        estimated_price=estimated_price,
        is_sell=False,
    )
    return {
        "board_lot_shares": 100,
        "estimated_amount": round(estimated_price * 100, 2),
        "estimated_total_cost": round(plan.estimated_total_cost, 4),
        "estimated_cost_rate_pct": round(plan.estimated_cost_rate, 4),
        "twap_order_count": len(plan.twap_plan.orders),
        "plan_valid": bool(plan.is_valid),
        "validation_errors": list(plan.validation_errors),
    }


def _log_run_decisions(
    *,
    picks: list[PickResult],
    frames: dict[str, pd.DataFrame],
    debate_results: list[DebateResult],
    portfolio_summary: Any | None,
    circuit_breaker_triggered: bool,
    regime: str,
    run_metadata: RunMetadata,
) -> None:
    if not picks:
        return

    from aqsp.audit.trade_logger import TradeDecisionLog, TradeLogger

    allocation_symbols = {
        str(item.symbol)
        for item in tuple(getattr(portfolio_summary, "allocations", ()) or ())
    }
    blocker_map = _candidate_blocker_map(portfolio_summary)
    review_map = _candidate_review_map(portfolio_summary)
    debate_by_symbol = {result.symbol: result for result in debate_results}
    trade_logger = TradeLogger(log_dir=os.getenv("AQSP_TRADE_LOG_DIR", "logs/trades"))
    timestamp = now_shanghai()

    for pick in picks:
        action = _resolve_audit_action(
            pick,
            allocation_symbols=allocation_symbols,
        )
        review_meta = review_map.get(pick.symbol, {})
        blocker = blocker_map.get(pick.symbol, "")
        debate = debate_by_symbol.get(pick.symbol)
        execution_preview = _build_execution_preview(
            pick,
            frame=frames.get(pick.symbol, pd.DataFrame()),
            action=action,
        )
        reason_parts = [
            f"PM裁决 {str(pick.metrics.get('portfolio_action', '') or 'keep')}",
            f"评级 {pick.rating}",
        ]
        candidate_status = str(pick.metrics.get("candidate_status", "") or "").strip()
        if candidate_status:
            reason_parts.append(f"状态 {candidate_status}")
        if blocker:
            reason_parts.append(f"阻塞 {blocker}")

        context: dict[str, Any] = {
            "thresholds_version": run_metadata.thresholds_version,
            "signal_date": pick.date,
            "requested_source": run_metadata.requested_source,
            "actual_source": run_metadata.actual_source,
            "source_health_label": run_metadata.source_health_label,
            "source_health_message": run_metadata.source_health_message,
            "data_latest_trade_date": run_metadata.data_latest_trade_date,
            "data_lag_days": run_metadata.data_lag_days,
            "portfolio_action": str(pick.metrics.get("portfolio_action", "") or "keep"),
            "candidate_status": candidate_status,
            "candidate_blocker": blocker,
            "candidate_next_step": str(review_meta.get("next_step", "") or ""),
            "candidate_review_window": str(review_meta.get("review_window", "") or ""),
            "candidate_review_priority": str(review_meta.get("priority", "") or ""),
            "intended_entry": pick.entry_type,
            "ideal_buy": pick.ideal_buy,
            "stop_loss": pick.stop_loss,
            "take_profit": pick.take_profit,
            "paper_position": pick.position,
            "run_task_id": run_metadata.task_id,
        }
        if execution_preview:
            context["paper_execution_preview"] = execution_preview
        if debate is not None:
            context["debate_consensus"] = debate.final_consensus
            context["debate_adjustment"] = debate.recommended_adjustment
            context["debate_disagreement_score"] = debate.disagreement_score

        trade_logger.log_decision(
            TradeDecisionLog(
                timestamp=timestamp,
                symbol=pick.symbol,
                name=pick.name,
                action=action,
                score=float(pick.score),
                strategies=list(pick.strategies),
                debate_summary=(
                    str(debate.final_consensus)
                    if debate is not None
                    else "no_debate_attached"
                ),
                risk_check_passed=(
                    action == "PAPER_REVIEW"
                    and not circuit_breaker_triggered
                    and not blocker
                ),
                regime=regime or "unknown",
                reason="；".join(reason_parts),
                context=context,
            )
        )


def _candidate_blocker_map(portfolio_summary: Any | None) -> dict[str, str]:
    blockers: dict[str, str] = {}
    if portfolio_summary is None:
        return blockers
    for item in tuple(getattr(portfolio_summary, "execution_blockers", ()) or ()):
        raw = str(item).strip()
        if not raw or ":" not in raw:
            continue
        display, reason = raw.split(":", 1)
        symbol = display.split(" ", 1)[0].strip()
        clean_reason = reason.strip()
        if symbol and clean_reason:
            blockers[symbol] = clean_reason
    return blockers


def _candidate_review_map(portfolio_summary: Any | None) -> dict[str, dict[str, str]]:
    reviews: dict[str, dict[str, str]] = {}
    if portfolio_summary is None:
        return reviews
    for item in tuple(getattr(portfolio_summary, "watch_reviews", ()) or ()):
        symbol = str(getattr(item, "symbol", "") or "").strip()
        if not symbol:
            continue
        reviews[symbol] = {
            "blocker": str(getattr(item, "blocker", "") or ""),
            "next_step": str(getattr(item, "next_step", "") or ""),
            "review_window": str(getattr(item, "review_window", "") or ""),
            "priority": str(getattr(item, "priority", "") or ""),
        }
    return reviews


def _default_candidate_review(status: str) -> dict[str, str]:
    if status == "新晋":
        return {
            "next_step": "等待量价继续走强后，再评估是否转入纸面复核名单",
            "review_window": "盘中走强后",
            "priority": "high",
        }
    if status == "延续上升":
        return {
            "next_step": "优先复核趋势延续与承接强度，再决定是否提升纸面复核优先级",
            "review_window": "午前确认后",
            "priority": "medium",
        }
    if status == "延续下降":
        return {
            "next_step": "若弱势延续则继续观察，等待重新企稳后再恢复关注",
            "review_window": "尾盘前",
            "priority": "low",
        }
    return {}


def _annotate_candidate_status(
    picks: list[PickResult],
    *,
    diff: Any | None,
    portfolio_summary: Any | None,
) -> list[PickResult]:
    if not picks:
        return picks

    from aqsp.portfolio.snapshot import build_candidate_status_map

    status_map = build_candidate_status_map(diff)
    blocker_map = _candidate_blocker_map(portfolio_summary)
    review_map = _candidate_review_map(portfolio_summary)

    enriched: list[PickResult] = []
    for pick in picks:
        status = status_map.get(pick.symbol, "")
        review = review_map.get(pick.symbol, {})
        blocker_reason = str(
            review.get("blocker", "") or blocker_map.get(pick.symbol, "")
        )
        if not status and blocker_reason:
            status = "观察阻塞"
        if not review and status:
            review = _default_candidate_review(status)
        if not status and not blocker_reason:
            enriched.append(pick)
            continue
        metrics = dict(pick.metrics)
        if status:
            metrics["candidate_status"] = status
        if blocker_reason:
            metrics["candidate_blocker"] = blocker_reason
        if review:
            metrics["candidate_next_step"] = str(review.get("next_step", "") or "")
            metrics["candidate_review_window"] = str(
                review.get("review_window", "") or ""
            )
            metrics["candidate_review_priority"] = str(review.get("priority", "") or "")
        enriched.append(replace(pick, metrics=metrics))
    return enriched


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
    try:
        source = _get_source(source_name)
    except DataError:
        return list(
            DEFAULT_SYMBOLS[:max_universe] if max_universe > 0 else DEFAULT_SYMBOLS
        )
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
        try:
            available = source.get_available_symbols()
        except DataError:
            available = []
        if available:
            return available[:max_universe] if max_universe > 0 else available
    return list(DEFAULT_SYMBOLS[:max_universe] if max_universe > 0 else DEFAULT_SYMBOLS)


def _check_sector_concentration_with_runtime_hints(
    symbols: list[str],
    *,
    sector_map: dict[str, str] | None = None,
    industry_map: dict[str, str] | None = None,
):
    from aqsp.portfolio.sector_check import check_sector_concentration

    try:
        return check_sector_concentration(
            symbols,
            sector_map=sector_map,
            industry_map=industry_map,
        )
    except TypeError:
        return check_sector_concentration(symbols)


def _count_independent_signal_days(ledger_path: str) -> int:
    return count_independent_signal_days(ledger_path)


def _ledger_signal_date(row: dict[str, Any]) -> str:
    from aqsp.ledger.runtime import ledger_signal_date

    return ledger_signal_date(row)


def _compute_real_pnl(ledger_path: str) -> tuple[float, float, float]:
    return compute_real_pnl(ledger_path)


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

    payload = build_walkforward_gate_payload(
        dsr=dsr,
        pbo=pbo,
        run_date=run_date,
        start=start,
        end=end,
        n_periods=n_periods,
    )
    p = Path(WALKFORWARD_GATE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ 双门 sidecar 已写入: {p}（both_pass={payload['both_pass']}）")


def _format_walkforward_count_map(
    items: dict[str, int] | tuple[tuple[str, int], ...],
) -> str:
    if not items:
        return "无"
    pairs = items.items() if isinstance(items, dict) else items
    return "；".join(f"{key}: {value}" for key, value in sorted(pairs))


def _append_walkforward_diagnostics(report_lines: list[str], result: Any) -> None:
    diagnostics = getattr(result, "diagnostics", None)
    if diagnostics is None:
        return

    report_lines.extend(
        [
            "",
            "## 失败诊断",
            "",
            "| 指标 | 值 |",
            "|------|-----|",
            f"| 总信号交易 | {diagnostics.total_trades} |",
            f"| 可成交交易 | {diagnostics.executable_trades} |",
            f"| 不可成交 | {diagnostics.not_executable} |",
            f"| 退出原因 | {_format_walkforward_count_map(diagnostics.exit_reason_counts)} |",
            f"| 不可成交原因 | {_format_walkforward_count_map(diagnostics.not_executable_reason_counts)} |",
            "",
        ]
    )

    if diagnostics.worst_symbols:
        report_lines.extend(
            [
                "### 拖累最大的标的",
                "",
                "| Symbol | 交易次数 | 平均收益点 | 累计收益点 |",
                "|--------|----------|------------|------------|",
            ]
        )
        for symbol, trades, avg_return, sum_return in diagnostics.worst_symbols:
            report_lines.append(
                f"| {symbol} | {trades} | {avg_return:.4f}% | {sum_return:.4f}% |"
            )
    else:
        report_lines.append("*无可成交标的诊断*")


def _format_walkforward_pbo(pbo: float, pbo_is_valid: bool) -> str:
    value = f"{pbo:.2%}"
    return value if pbo_is_valid else f"{value}（无效占位，需 grid 多变体 CSCV）"


def _walkforward_runtime_rows(
    args: argparse.Namespace, effective_horizon: int
) -> list[tuple[str, str]]:
    min_score = "thresholds.yaml"
    if getattr(args, "min_score", None) is not None:
        min_score = str(args.min_score)
    return [
        ("source", str(args.source)),
        ("pool", str(getattr(args, "pool", ""))),
        ("symbols", str(args.symbols or "AQSP_WALKFORWARD_SYMBOLS/default_pool")),
        ("engine", str(getattr(args, "engine", "") or "runtime_config/auto")),
        ("min_score", min_score),
        ("horizon_days", str(effective_horizon)),
        ("tiered_stop", str(bool(getattr(args, "tiered_stop", False)))),
        ("cache_path", str(getattr(args, "cache_path", "") or "")),
        ("allow_heldout", str(bool(getattr(args, "allow_heldout", False)))),
    ]


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
    if not isinstance(gate, dict):
        reasons.append("双门 sidecar 解析失败: JSON 顶层不是对象")
        return False, reasons

    validation = validate_walkforward_gate_payload(
        gate,
        today=today_shanghai(),
        max_age_days=MAX_GATE_AGE_DAYS,
        heldout_cutoff=date.fromisoformat(HELDOUT_TRAIN_CUTOFF),
    )
    reasons.extend(_notification_gate_reasons(gate, validation))

    return len(reasons) == 0, reasons


def _notification_gate_reasons(
    gate: dict[str, Any], validation: WalkForwardGateValidation
) -> list[str]:
    raw_reasons: list[str] = []
    internal_flags: list[str] = []
    for blocker in validation.blockers:
        if blocker.startswith("run_date"):
            raw_reasons.append(f"双门 sidecar run_date 异常: {gate.get('run_date')!r}")
        elif blocker.startswith("gate stale"):
            age = validation.age_days if validation.age_days is not None else "?"
            raw_reasons.append(
                f"双门结果过期: {age} 天前（上限 {MAX_GATE_AGE_DAYS} 天）—— 请重新跑 walkforward"
            )
        elif blocker.startswith("deflated_sharpe"):
            raw_reasons.append("DSR 字段缺失或格式异常")
        elif blocker.startswith("DSR="):
            raw_reasons.append(f"DSR 未过门: {validation.dsr:.4f}（需 >1.0）")
        elif blocker.startswith("pbo missing"):
            raw_reasons.append("PBO 字段缺失或格式异常")
        elif blocker.startswith("PBO="):
            if validation.pbo == 0.0 and validation.pbo_valid is not True:
                raw_reasons.append(
                    "PBO 未通过: 当前为单策略占位 0.00%，缺少多变体 CSCV 证据"
                )
            else:
                raw_reasons.append(
                    f"PBO 未过门: {validation.pbo:.2%}（需 0 < PBO < 50%）"
                )
        elif blocker.startswith("dsr_pass"):
            internal_flags.append("dsr_pass")
        elif blocker.startswith("pbo_pass"):
            internal_flags.append("pbo_pass")
        elif blocker.startswith("pbo_valid"):
            internal_flags.append("pbo_valid")
        elif blocker.startswith("both_pass"):
            internal_flags.append("both_pass")
        elif blocker.startswith("n_periods"):
            raw_reasons.append(
                f"双门 sidecar 无有效回测周期（n_periods={gate.get('n_periods')}）"
                "—— 需真正跑 walkforward 后重写"
            )
        elif blocker.startswith("data_end malformed"):
            raw_reasons.append(
                f"双门 sidecar 的 data_end 格式异常（{gate.get('data_end')!r}）—— fail-closed"
            )
        elif blocker.startswith("data_end="):
            raw_reasons.append(
                f"双门成绩用了 held-out 数据（data_end={gate.get('data_end')} > "
                f"{HELDOUT_TRAIN_CUTOFF}）—— 不得用于解锁推送（§1.3 #9）"
            )
        else:
            raw_reasons.append(f"双门 sidecar 未通过: {blocker}")

    if internal_flags and not any("PBO 未通过" in item for item in raw_reasons):
        raw_reasons.append("双门 sidecar 内部通过标志未全部为真")

    return _dedupe_gate_reasons(raw_reasons)


def _dedupe_gate_reasons(reasons: list[str]) -> list[str]:
    deduped: list[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped

def _notification_gate_actions(
    reasons: list[str],
    *,
    cold_start_days: int,
) -> list[str]:
    actions: list[str] = []
    joined = " ".join(reasons)

    if "冷启动未满" in joined:
        remaining_days = max(COLD_START_MIN_DAYS - cold_start_days, 0)
        actions.append(
            "继续按日运行主链，先把冷启动样本积累到 "
            f"{COLD_START_MIN_DAYS} 个独立信号日"
            + (f"（当前还差 {remaining_days} 天）" if remaining_days > 0 else "")
            + "。"
        )
    if (
        "sidecar 不存在" in joined
        or "n_periods=0" in joined
        or "过期" in joined
        or "解析失败" in joined
    ):
        actions.append(
            "重跑双门回测以刷新 gate：`.venv/bin/python3 -m aqsp walkforward --source sqlite_db --end 2024-12-31`。"
        )
    if "单策略占位" in joined or "多变体 CSCV" in joined:
        actions.append(
            "生成多变体 grid CSCV 证据后再刷新 gate；旧归档 Markdown 不作为生产放行依据。"
        )
    if "DSR 未过门" in joined or "PBO 未过门" in joined or "PBO 未通过" in joined:
        actions.append("在双门过线前保留观察模式，不要开启自动通知或放大纸面仓位。")
    if "held-out" in joined:
        actions.append(
            "回测窗口退回到 2024-12-31 及以前，避免 held-out 成绩污染通知门禁。"
        )

    if not actions:
        actions.append("先处理上述门禁原因，再重新执行 `aqsp run --notify`。")

    return actions


def _format_notification_gate_block(
    gate_reasons: list[str],
    next_actions: list[str],
) -> str:
    lines = [
        "> ⚠️ **未通过 walk-forward 双门验证，仅供观察，请勿实盘使用**",
        ">",
        "> 未达原因：",
    ]
    lines.extend(f"> - {reason}" for reason in gate_reasons)
    lines.append(">")
    lines.append("> 处理项：")
    lines.extend(f"> - {action}" for action in next_actions)
    lines.append("")
    return "\n".join(lines) + "\n"


def _gate_notification_allowed(task_id: str | None = None) -> bool:
    value = task_id if task_id is not None else os.getenv("AQSP_RUN_TASK_ID", "")
    return str(value or "").strip().lower() in {"daily", "scheduled", "manual"}


def _should_send_gate_notification(
    *,
    gate_ok: bool,
    gate_reasons: list[str],
    run_date: str,
) -> bool:
    return should_send_gate_notification(
        gate_ok=gate_ok,
        gate_reasons=gate_reasons,
        state_path=_resolve_runtime_state_path(
            os.getenv("AQSP_GATE_NOTIFY_STATE_PATH", GATE_NOTIFY_STATE_PATH)
        ),
        run_date=run_date,
    )


def _mark_gate_notification_sent(
    *,
    gate_reasons: list[str],
    run_date: str,
) -> None:
    mark_gate_notification_sent(
        gate_reasons=gate_reasons,
        state_path=_resolve_runtime_state_path(
            os.getenv("AQSP_GATE_NOTIFY_STATE_PATH", GATE_NOTIFY_STATE_PATH)
        ),
        run_date=run_date,
    )


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
    data_lag_days = _runtime_data_lag_days(latest)
    freshness_tier, coverage_tier, source_local_status = _source_runtime_metadata(
        actual_source,
        latest_trade_day=latest,
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
            task_id=str(os.environ.get("AQSP_RUN_TASK_ID", "") or "").strip(),
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
    task_id = str(os.environ.get("AQSP_RUN_TASK_ID", "") or "").strip()
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
    data_lag_days = _runtime_data_lag_days(latest)
    freshness_tier, coverage_tier, source_local_status = _source_runtime_metadata(
        actual_source,
        latest_trade_day=latest,
    )
    source_health_label, source_health_message, fallback_used = describe_source_health(
        args.source,
        actual_source,
    )

    weight_proposals: dict[str, float] = {}
    try:
        from aqsp.ledger.base import ledger_rows_to_frame, read_ledger
        from aqsp.ledger.learner import PerformanceLearner

        learner = PerformanceLearner()
        ledger_df = ledger_rows_to_frame(read_ledger(args.ledger))
        weight_proposals = learner.compute_weights(ledger_df)
    except Exception as exc:
        LOGGER.warning("学习权重提案计算失败，按无提案继续: %s", exc)
        weight_proposals = {}

    # Runtime screening must not self-tune from recent ledger outcomes. Learning
    # output remains a research proposal until an approved weight artifact exists.
    if weight_proposals:
        print(
            f"学习权重提案: {len(weight_proposals)} 个策略，仅记录研究观察，未应用到本次筛选"
        )
    weights: dict[str, float] = {}

    cold_start_days = _count_independent_signal_days(args.ledger)
    is_cold_start = cold_start_days < COLD_START_MIN_DAYS

    thresholds = load_thresholds()

    regime = _detect_runtime_regime(
        frames,
        benchmark_symbol=args.benchmark_symbol,
    )

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
            rescored_picks = []
            for pick in screened_picks:
                raw_cs = composite_scores.get(pick.symbol, 0.0)
                normalized_cs = (raw_cs / max_cs * 100) if max_cs > 0 else 0.0
                rescored_picks.append(
                    replace(
                        pick,
                        regime_score=round(normalized_cs, 2),
                        score=round(pick.score * 0.7 + normalized_cs * 0.3, 2),
                    )
                )
            screened_picks = sorted(rescored_picks, key=lambda p: p.score, reverse=True)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "CompositeStrategy 重评分失败，回退到基础筛选结果: %s",
            exc,
        )

    picks = screened_picks[:limit]

    sector_map = {
        pick.symbol: str(pick.metrics.get("sector", "") or "")
        for pick in picks
        if str(pick.metrics.get("sector", "") or "").strip()
    }
    industry_map = {
        pick.symbol: str(pick.metrics.get("industry", "") or "")
        for pick in picks
        if str(pick.metrics.get("industry", "") or "").strip()
    }

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

    pick_name_map = {pick.symbol: pick.name for pick in picks}
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
        except Exception as exc:
            LOGGER.warning("北向资金因子计算失败，按 0 处理: %s", exc)
            nb_z = 0.0
        try:
            from aqsp.data.cn.margin_trading import compute_margin_factor

            top_symbol = picks[0].symbol if picks else ""
            margin_z = compute_margin_factor(top_symbol) if top_symbol else 0.0
        except Exception as exc:
            LOGGER.warning("两融因子计算失败，按 0 处理: %s", exc)
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
        task_id=str(os.environ.get("AQSP_RUN_TASK_ID", "") or "").strip(),
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
            format_concentration,
        )

        concentration = _check_sector_concentration_with_runtime_hints(
            [p.symbol for p in picks],
            sector_map=sector_map,
            industry_map=industry_map,
        )
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

        skipped_cooldown = 0

        for pick in picks[:3]:
            if pick.symbol in cooldown_symbols:
                print(
                    f"   ⏭️  跳过 {pick.symbol} {pick.name}（{DEBATE_COOLDOWN_DAYS}天内已辩论）"
                )
                skipped_cooldown += 1
                continue

            df = screen_frames.get(pick.symbol, pd.DataFrame())
            if not df.empty:
                try:
                    result = coordinator.run_debate(pick, df, signal_date=today)

                    if result.disagreement_score < DEBATE_MIN_DISAGREEMENT:
                        print(
                            f"   ⏭️  跳过 {pick.symbol} {pick.name}（分歧 {result.disagreement_score:.2f} < {DEBATE_MIN_DISAGREEMENT}）"
                        )
                        continue

                    debate_results.append(result)

                    serialized = serialize_debate_result(result)
                    serialized["debate_date"] = today
                    serialized["created_at"] = now
                    key = f"{result.symbol}_{today}"
                    existing_debates[key] = serialized

                    print(
                        f"   ✅ 辩论完成: {pick.symbol} {pick.name} | 结论={result.recommended_adjustment} 分歧={result.disagreement_score:.2f}（非keep将接入PM）"
                    )
                except Exception as e:
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.warning(f"辩论失败 {pick.symbol}: {e}")

        if skipped_cooldown > 0:
            print(f"   ⏭️  {skipped_cooldown}只股票因冷却期跳过")
        print(
            "   📎 辩论结果已落附件；runtime评分与ledger score保持原样，非keep结论将接入PM调整优先级"
        )

        with open(debate_file, "w", encoding="utf-8") as f:
            for data in existing_debates.values():
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        print("📢 辩论分析完成")

        # 将辩论结论回写到对应 pick，使 PM 能据此调整优先级与配仓。
        # 仅当辩论给出非 keep 结论时才覆盖，避免无分歧时引入噪声。
        debate_by_symbol = {dr.symbol: dr for dr in debate_results}
        if debate_by_symbol:
            rewritten = 0
            updated_picks = []
            for pick in picks:
                dr = debate_by_symbol.get(pick.symbol)
                if dr is not None and dr.recommended_adjustment in ("raise", "lower"):
                    pick = replace(
                        pick,
                        recommended_adjustment=dr.recommended_adjustment,
                        adjusted_score=dr.adjusted_score,
                        debate_consensus=dr.final_consensus,
                    )
                    rewritten += 1
                updated_picks.append(pick)
            picks = updated_picks
            if rewritten:
                print(f"   🔀 辩论结论已接入 PM：{rewritten} 只候选将据此调整优先级")

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
            regime=regime,
            concentration=concentration,
            correlation_result=correlation_result,
            sector_map=sector_map,
            industry_map=industry_map,
        )
        picks = bundle.picks
        portfolio_decisions = list(bundle.decisions)
        portfolio_summary = bundle.summary
        portfolio_summary = _augment_summary_with_t1_blockers(
            portfolio_summary,
            removed_symbols=removed,
            removed_name_map=pick_name_map,
        )
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
        if diff is not None and diff.has_changes:
            print(format_snapshot_diff(diff))

    if picks:
        picks = _annotate_candidate_status(
            picks,
            diff=diff,
            portfolio_summary=portfolio_summary,
        )

    _log_run_decisions(
        picks=picks,
        frames=screen_frames,
        debate_results=debate_results,
        portfolio_summary=portfolio_summary,
        circuit_breaker_triggered=status.triggered,
        regime=regime,
        run_metadata=run_metadata,
    )

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
    tradable = [
        p for p in picks if p.rating in ("strong_buy_candidate", "buy_candidate")
    ]
    has_allocations = bool(getattr(portfolio_summary, "allocations", ()) or ())
    summary_lines = [
        "",
        "---",
        "## 📌 执行摘要",
        "",
    ]
    if status.triggered:
        summary_lines.append(f"🛡️ **组合保护已触发**: {status.reason}，暂停新增纸面复核")
    else:
        summary_lines.append(_build_execution_summary_line(tradable, portfolio_summary))
        if has_allocations and len(tradable) > 1:
            others = "、".join(f"{p.symbol} {p.name}" for p in tradable[1:3])
            summary_lines.append(f"📋 **其他候选**: {others}")
        elif not has_allocations:
            watchlist = tuple(getattr(portfolio_summary, "watchlist", ()) or ())
            blockers = tuple(getattr(portfolio_summary, "execution_blockers", ()) or ())
            if watchlist:
                summary_lines.append(f"📋 **观察重点**: {'、'.join(watchlist[:3])}")
            if blockers:
                summary_lines.append(f"🚧 **主要阻塞**: {blockers[0]}")
        if diff is not None and diff.has_changes:
            from aqsp.portfolio.snapshot import snapshot_diff_highlights

            summary_lines.extend(snapshot_diff_highlights(diff, max_items=2))

    if is_cold_start:
        summary_lines.append(
            f"⏳ 冷启动期: {cold_start_days}/{COLD_START_MIN_DAYS} 天（策略权重未调整，仅供观察）"
        )

    summary_lines.append("")
    summary_lines.append("---")
    summary_lines.append("")

    # 把摘要插到标题行之后（第一个 \n\n 之后）
    title_end = markdown.find("\n\n")
    if title_end > 0:
        markdown = (
            markdown[:title_end]
            + "\n"
            + "\n".join(summary_lines)
            + markdown[title_end:]
        )
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

    if diff is not None and diff.has_changes:
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
    except Exception as exc:
        LOGGER.warning("策略衰减诊断失败，跳过附加提示: %s", exc)

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
    except Exception as exc:
        LOGGER.warning("失败模式分析失败，跳过附加提示: %s", exc)

    report_path = str(getattr(args, "report", "") or "").strip()
    output_csv_path = str(getattr(args, "output_csv", "") or "").strip()
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    # 先检查双门 gate，决定是否放行 notify
    gate_ok, gate_reasons = _check_notification_gate(cold_start_days=cold_start_days)
    next_actions = (
        _notification_gate_actions(
            gate_reasons,
            cold_start_days=cold_start_days,
        )
        if not gate_ok
        else []
    )
    notify_mode = load_runtime_config().notify_mode
    title_label = str(
        os.environ.get("AQSP_NOTIFY_TITLE_LABEL", "") or "收盘研究日报"
    ).strip()
    legacy_notify = (
        notify_markdown if notify_markdown is not _notify_markdown_default else None
    )
    notification_artifacts = finalize_scheduled_notification(
        markdown=markdown,
        args_notify=args.notify,
        gate_ok=gate_ok,
        gate_reasons=gate_reasons,
        next_actions=next_actions,
        latest_iso=latest.isoformat(),
        notify_mode=notify_mode,
        dispatch_gate_notification_fn=lambda **kwargs: notify_gate_markdown(
            build_gate_notification_markdown(
                run_date=kwargs["run_date"],
                gate_reasons=kwargs["gate_reasons"],
                next_actions=kwargs["next_actions"],
            )
        ),
        should_send_gate_notification_fn=lambda **kwargs: _should_send_gate_notification(
            gate_ok=kwargs["gate_ok"],
            gate_reasons=kwargs["gate_reasons"],
            run_date=kwargs["run_date"],
        ),
        format_notification_gate_block_fn=_format_notification_gate_block,
        legacy_notify_fn=legacy_notify,
        print_fn=print,
        mark_gate_notification_sent_fn=lambda **kwargs: _mark_gate_notification_sent(
            gate_reasons=kwargs["gate_reasons"],
            run_date=kwargs["run_date"],
        ),
        gate_state_path=_resolve_runtime_state_path(
            os.getenv("AQSP_GATE_NOTIFY_STATE_PATH", GATE_NOTIFY_STATE_PATH)
        ),
        task_id=task_id,
    )
    finalize_scheduled_outputs(
        markdown=notification_artifacts.markdown,
        report_path=report_path,
        output_csv_path=output_csv_path,
        table=table,
        print_fn=print,
    )
    dispatch_scheduled_daily_notification(
        notify_enabled=notification_artifacts.notify_enabled,
        notify_mode=notify_mode,
        latest_iso=latest.isoformat(),
        tradable=tradable,
        picks=picks,
        portfolio_summary=portfolio_summary,
        debate_results=debate_results,
        actual_source=actual_source,
        source_health_label=source_health_label,
        source_health_message=source_health_message,
        requested_source=args.source,
        cold_start_days=cold_start_days,
        cold_start_min_days=COLD_START_MIN_DAYS,
        is_cold_start=is_cold_start,
        circuit_breaker_reason=status.reason if status.triggered else "",
        snapshot_diff=diff,
        title_label=title_label,
        build_daily_run_notification_fn=build_daily_run_notification,
        dispatch_notification_fn=lambda markdown, **kwargs: _dispatch_notification_once(
            markdown,
            mode=kwargs["mode"],
            prefix=kwargs["prefix"],
            kind=kwargs["kind"],
            summary_markdown=kwargs.get("summary_markdown"),
        ),
        notification_kind=f"daily:{latest.isoformat()}",
    )
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
        src = _build_sqlite_db_source(cache=None)
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
    effective_horizon = args.horizon_days or 3
    engine_cfg = WalkForwardEngineConfig(
        train_days=args.train_days,
        test_days=args.test_days,
        purge_days=args.purge_days,
        horizon_days=effective_horizon,
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
    pbo_display = _format_walkforward_pbo(result.pbo, pbo_is_valid)
    tl_dr.append(
        f"**TL;DR**: {verdict} — DSR={result.deflated_sharpe:.4f}, "
        f"PBO={pbo_display}, Sharpe={result.overall.sharpe_ratio:.2f}, "
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
        f"- PBO < 0.5：{'PASS' if pbo_pass else 'FAIL'}（实测 {pbo_display}）",
        "",
        f"**运行日期**: {now_shanghai().strftime('%Y-%m-%d %H:%M')}",
        f"**回测区间**: {args.start} ~ {args.end}",
        f"**标的数量**: {len(filtered)}",
        f"**训练窗口**: {args.train_days} 天",
        f"**测试窗口**: {args.test_days} 天",
        f"**Purge Gap**: {args.purge_days} 天",
        f"**研究引擎**: {resolution.resolved} ({resolution.mode})",
        "",
        "## 运行参数",
        "",
        "| 参数 | 值 |",
        "|------|-----|",
        *[
            f"| {key} | {value or '-'} |"
            for key, value in _walkforward_runtime_rows(args, effective_horizon)
        ],
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
        f"| PBO (过拟合概率) | {pbo_display} | < 50% 且非占位才表示低过拟合风险 |",
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

    _append_walkforward_diagnostics(report_lines, result)

    report_lines.extend(
        [
            "",
            "## 结论",
            "",
        ]
    )

    if both_pass:
        report_lines.append(
            f"✅ **{verdict}**: DSR={result.deflated_sharpe:.4f} > 1.0 且 PBO={result.pbo:.2%} < 50%，可进入人工纸面复核候选。"
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
                    "sector": str(row.get("sector", "") or ""),
                    "industry": str(row.get("industry", "") or ""),
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
        from aqsp.portfolio.manager import (
            PortfolioDecision,
            summarize_portfolio_decisions,
        )

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
            regime=regime_str,
            concentration=None,
            correlation_result=None,
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
        print_notify_results(
            send_briefing(briefing, source_status=source_status),
            prefix="briefing notify",
        )

    if getattr(args, "email", False):
        from aqsp.briefing.email_notifier import (
            load_email_config_from_env,
            send_briefing_email,
        )

        cfg = load_email_config_from_env()
        if cfg is None:
            print("⚠️  --email 已开启但 AQSP_SMTP_* 环境变量不全，跳过邮件发送")
        else:
            body = Path(args.output).read_text(encoding="utf-8")
            ok = send_briefing_email(
                cfg=cfg,
                subject=f"aqsp briefing {today_shanghai().isoformat()}",
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


def run_news_catalysts(args: argparse.Namespace) -> int:
    from aqsp.news import (
        NewsCatalystConfig,
        build_catalyst_report,
        format_catalyst_notification,
    )

    symbols = tuple(
        item.strip() for item in str(args.symbols or "").split(",") if item.strip()
    )
    names = _parse_symbol_names(str(args.names or ""))
    report = build_catalyst_report(
        symbols=symbols,
        symbol_names=names,
        config=NewsCatalystConfig(
            symbols=symbols,
            max_events=args.max_events,
            enable_llm_review=args.enable_llm_review,
            source_timeout_seconds=args.source_timeout_seconds,
            llm_timeout_seconds=args.llm_timeout_seconds,
            max_llm_review_events=args.max_llm_review_events,
        ),
    )
    markdown = format_catalyst_notification(report)
    print(markdown)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
    if args.notify:
        _dispatch_notification_once(
            markdown,
            mode=load_runtime_config().notify_mode,
            prefix="news notify",
            kind=f"news-catalysts:{today_shanghai().isoformat()}",
        )
    return 0


def _parse_symbol_names(raw: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        if ":" in text:
            symbol, name = text.split(":", 1)
        elif "=" in text:
            symbol, name = text.split("=", 1)
        else:
            continue
        symbol = symbol.strip()
        name = name.strip()
        if symbol and name:
            names[symbol] = name
    return names


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
        "status": "proposal_only",
        "applied": False,
        "run_date": now_shanghai().isoformat(timespec="seconds"),
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果已保存到: {output_path}")

    if args.apply:
        print("\n已生成参数优化提案，但不会自动写入 thresholds.yaml")
        print("原因: 阈值变更必须经过 walk-forward 与人工审核后单独提交")

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
                "status": "research_candidate",
                "proposal_only": True,
                "applied": False,
                "uses_forward_returns": True,
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
        evaluation = r.get("evaluation", {})
        factor_name = r.get("name") or r.get("factor_name") or "unknown"
        ic_mean = float(evaluation.get("ic_mean", r.get("ic_mean", 0.0)))
        ic_ir = float(evaluation.get("ic_ir", r.get("ic_ir", 0.0)))
        sample_size = int(evaluation.get("sample_size", r.get("sample_size", 0)))
        print(
            f"  - {factor_name}: IC={ic_mean:.4f}, IR={ic_ir:.2f}, 样本={sample_size}, 状态=研究候选"
        )

    inactive_results = [
        {**r, "is_active": False, "status": "research_candidate"} for r in results
    ]
    library = FactorLibrary()
    library.load()
    added_count = 0
    for r in inactive_results:
        if library.add_factor(r):
            added_count += 1
    library.save()

    print(f"\n已将 {added_count} 个新因子添加到因子库（默认不启用，需人工复核）")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(inactive_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
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

        if args.apply:
            print("\n已生成参数进化提案，但不会自动写入 thresholds.yaml")
            print("原因: 阈值变更必须经过 walk-forward 与人工审核后单独提交")
        elif result.confidence >= evolution.config.confidence_threshold:
            print("\n置信度达标，但仅输出研究提案，未自动应用参数")
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
            "status": "proposal_only" if result else "no_change",
            "applied": False,
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

    print("运行早盘强势股观察...")
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
    print("分析早盘强势股观察信号...")

    signals = strategy.analyze_pre_market(frames)

    report = format_morning_signals(signals, top_n=args.top)
    print(report)

    if args.notify and signals:
        try:
            print_notify_results(
                _notify_via_config(
                    build_morning_breakout_notification(
                        signals,
                        mode=load_runtime_config().notify_mode,
                        top_n=args.top,
                    ),
                    mode=load_runtime_config().notify_mode,
                ),
                prefix="morning notify",
            )
        except Exception as exc:
            print(f"morning notify failed: {exc}")

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
            print_notify_results(
                _notify_via_config(
                    build_closing_premium_notification(
                        signals,
                        mode=load_runtime_config().notify_mode,
                        top_n=args.top,
                    ),
                    mode=load_runtime_config().notify_mode,
                ),
                prefix="closing notify",
            )
        except Exception as exc:
            print(f"closing notify failed: {exc}")

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
            print_notify_results(
                _notify_via_config(
                    build_closing_review_notification(
                        review=review if not args.weekly else None,
                        weekly_summary=summary if args.weekly else None,
                        mode=load_runtime_config().notify_mode,
                    ),
                    mode=load_runtime_config().notify_mode,
                ),
                prefix="review notify",
            )
        except Exception as exc:
            print(f"review notify failed: {exc}")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"\n报告已保存到: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
