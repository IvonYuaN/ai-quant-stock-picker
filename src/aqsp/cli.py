from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

from aqsp.config import load_runtime_config
from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai
from aqsp.core.types import RunMetadata
from aqsp.data.registry import (
    local_data_status,
    registry_entry_dict,
    sort_registry_entries,
)
from aqsp.data.registry import get_registry_entry
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
from aqsp.notifier import notify_markdown
from aqsp.notifier import prepend_source_status_banner
from aqsp.research.summary import load_research_summary
from aqsp.regime.detector import RegimeDetector
from aqsp.report import to_dataframe, to_markdown
from aqsp.risk.circuit_breaker import CircuitBreaker
from aqsp.strategy import screen_universe
from aqsp.strategies.thresholds import load_thresholds
from aqsp.universe import DEFAULT_SYMBOLS

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

    monitor_cmd = sub.add_parser("monitor", help="run monitoring checks")
    monitor_cmd.add_argument("--config", default="config/monitors.yaml")
    monitor_cmd.add_argument("--notify", action="store_true")
    monitor_cmd.add_argument("--dry-run", action="store_true")

    sources_cmd = sub.add_parser(
        "sources", help="show data source readiness and freshness tiers"
    )
    sources_cmd.add_argument("--ready-only", action="store_true")
    sources_cmd.add_argument("--json", action="store_true")

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

    args = parser.parse_args(argv)
    # 宪法启动门：检查不变量，任一失败会直接 SystemExit
    from aqsp._constitution_check import assert_constitution_invariants

    assert_constitution_invariants()
    try:
        if args.command == "screen":
            return run_screen(args)
        if args.command == "run":
            return run_scheduled(args)
        if args.command == "walkforward":
            return run_walkforward(args)
        if args.command == "briefing":
            return run_briefing(args)
        if args.command == "monitor":
            return run_monitor(args)
        if args.command == "sources":
            return run_sources(args)
        if args.command == "research":
            return run_research(args)
        if args.command == "pit":
            return run_pit(args)
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
            item["health_successes"] = int(stats.get("successes", 0))
            item["health_failures"] = int(stats.get("failures", 0))
            item["health_last_success"] = stats.get("last_success", "")
            payload.append(item)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for entry in entries:
        ready = "yes" if entry.runtime_ready else "no"
        account = "yes" if entry.requires_account else "no"
        stats = source_health.get(entry.id, {})
        print(
            f"- {entry.id}: ready={ready} local={local_data_status(entry)} "
            f"fresh={entry.freshness_tier} cover={entry.coverage_tier} "
            f"daily={'yes' if entry.supports_daily else 'no'} "
            f"intraday={'yes' if entry.supports_intraday else 'no'} "
            f"realtime={'yes' if entry.supports_realtime else 'no'} "
            f"health={int(stats.get('successes', 0))}/{int(stats.get('failures', 0))} "
            f"account={account}"
        )
        print(f"  uses: {', '.join(entry.default_for)}")
        print(f"  setup: {entry.setup}")
    return 0


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


def _source_runtime_metadata(source_id: str) -> tuple[str, str, str]:
    entry = get_registry_entry(source_id)
    if entry is None:
        return "unknown", "unknown", "unknown"
    return (
        entry.freshness_tier,
        entry.coverage_tier,
        local_data_status(entry),
    )


def _reorder_source_refs(source_refs: list[object]) -> list[object]:
    order = prioritize_source_ids(
        [str(getattr(item, "name", "")) for item in source_refs]
    )
    by_name = {str(getattr(item, "name", "")): item for item in source_refs}
    return [by_name[name] for name in order if name in by_name]


def _get_source(source_name: str):
    cache = DataCache()
    if source_name in {"auto", "local_first"}:
        fallbacks = _reorder_source_refs(
            [
                EastmoneySource(cache=cache),
                SinaSource(cache=cache),
                TencentSource(cache=cache),
                AkshareSource(cache=cache),
            ]
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
            ]
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
            ]
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
            signal_dates.add(row.get("signal_date", ""))
    return len(signal_dates)


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

    payload = {
        "run_date": run_date,
        "deflated_sharpe": dsr,
        "pbo": pbo,
        "dsr_pass": dsr > 1.0,
        "pbo_pass": pbo < 0.5,
        "both_pass": (dsr > 1.0) and (pbo < 0.5),
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
      1. 冷启动 >=30 个独立信号日
      2. DSR >1.0
      3. PBO <0.5
    sidecar 缺失/解析失败/过期 → fail-closed（不放行）。
    """
    import json
    from datetime import date

    reasons: list[str] = []

    if cold_start_days < 30:
        reasons.append(f"冷启动未满: {cold_start_days}/30 个独立信号日")

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
    picks = screen_universe(screen_frames, config)[: args.limit]
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
    symbols = _resolve_run_symbols(
        args.source,
        explicit_symbols,
        pool_name=getattr(args, "pool", ""),
        as_of=today_shanghai(),
        max_universe=max_universe,
        min_avg_amount=min_avg_amount,
    )

    if args.csv:
        frames = load_csv(args.csv)
        actual_source = "csv"
    else:
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

    cold_start_days = _count_independent_signal_days(args.ledger)
    is_cold_start = cold_start_days < 30

    screen_frames = _drop_benchmark_frame(frames, args.benchmark_symbol)
    thresholds = load_thresholds()
    config = ScreeningConfig(
        mode=mode,
        min_avg_amount=min_avg_amount,
        min_price=thresholds.filter.min_price,
        max_price=thresholds.filter.max_price,
        strategy_weights=weights,
    )
    screened_picks = screen_universe(screen_frames, config)
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
    bench_frame = frames.get(args.benchmark_symbol) if args.benchmark_symbol else None
    if bench_frame is not None and not bench_frame.empty:
        regime = RegimeDetector().detect({args.benchmark_symbol: bench_frame}).name
    else:
        regime = ""

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

    validation = None
    if not args.skip_validation:
        validation = validate_predictions(args.ledger, frames)

    breaker = CircuitBreaker()
    daily_pnl = 0.0
    weekly_pnl = 0.0
    monthly_pnl = 0.0
    if validation and validation.checked:
        daily_pnl = validation.avg_return_pct
        weekly_pnl = validation.avg_return_pct * 5
        monthly_pnl = validation.avg_return_pct * 20
    status = breaker.check(
        daily_pnl_pct=daily_pnl,
        weekly_pnl_pct=weekly_pnl,
        monthly_pnl_pct=monthly_pnl,
    )

    table = to_dataframe(picks)
    markdown = to_markdown(
        picks,
        title=f"AI 量化选股报告({mode}, 数据日期 {latest.isoformat()})",
        metadata=run_metadata,
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
            validation_text += (
                f"- ⏳ 冷启动期:已积累 {cold_start_days}/30 个独立信号日\n"
            )
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
            prepend_source_status_banner(
                markdown,
                source_status={
                    "requested_source": args.source,
                    "actual_source": actual_source,
                    "health_label": source_health_label,
                    "health_message": source_health_message,
                },
            )
        )
        if not results:
            print("No notification channel configured.")
        for result in results:
            status = "ok" if result.ok else "failed"
            print(f"notify {result.channel}: {status} ({result.detail})")
    return 2 if status.triggered else 0


def run_walkforward(args: argparse.Namespace) -> int:
    import logging
    import sys
    from aqsp.backtest.walk_forward import WalkForwardTester
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

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
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
    elif args.source == "sqlite_db":
        from datetime import date as _date
        from aqsp.data.pit_financial import enrich_ohlcv_with_pit_financials

        src = SqliteDbSource(cache=DataCache())
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

    tester = WalkForwardTester(
        strategy=strategy,
        train_period_days=args.train_days,
        test_period_days=args.test_days,
        purge_days=args.purge_days,
        horizon_days=args.horizon_days or 3,
        use_tiered_stop=getattr(args, "tiered_stop", False),
    )

    print("开始 walk-forward 回测...")
    if logger:
        logger.info("Walk-forward 回测开始...")

    result = tester.run(filtered, start_date=args.start, end_date=args.end)

    regime_counts: dict[str, int] = {}
    if hasattr(result, "regime_winrates") and result.regime_winrates:
        for regime in result.regime_winrates:
            regime_counts[regime] = regime_counts.get(regime, 0) + 1

    tl_dr = []
    dsr_pass = result.deflated_sharpe > 1.0
    pbo_pass = result.pbo < 0.5
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
    from aqsp.models import PickResult

    rows = read_ledger(args.ledger)
    latest_date = ""
    for row in reversed(rows):
        if row.get("status") in ("pending", "validated"):
            latest_date = row.get("signal_date", "")
            break

    picks: list[PickResult] = []
    for row in rows:
        if row.get("signal_date") != latest_date:
            continue
        if row.get("status") not in ("pending", "validated"):
            continue
        picks.append(
            PickResult(
                symbol=str(row.get("symbol", "")),
                name=str(row.get("name", "")),
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

    generator = BriefingGenerator()
    research_summary = load_research_summary()
    briefing = generator.generate(
        picks=picks,
        frames={},
        regime=regime_str,
        source_status=source_status,
        research_summary=research_summary,
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
        send_alerts(triggered)

    return 1 if any(r.severity == "critical" for r in triggered) else 0


if __name__ == "__main__":
    raise SystemExit(main())
