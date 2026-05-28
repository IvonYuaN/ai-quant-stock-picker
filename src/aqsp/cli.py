from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from aqsp.config import load_runtime_config
from aqsp.data import fetch_akshare, load_csv, fetch_with_source
from aqsp.data.akshare_source import AkshareSource
from aqsp.filters_lethal.pipeline import LethalFilterPipeline
from aqsp.freshness import assert_fresh_data
from aqsp.ledger import (
    ExecutionConfig,
    append_predictions,
    strategy_weights_from_ledger,
    validate_predictions,
)
from aqsp.models import ScreeningConfig
from aqsp.notifier import notify_markdown
from aqsp.regime.detector import RegimeDetector
from aqsp.report import to_dataframe, to_markdown
from aqsp.risk.circuit_breaker import CircuitBreaker
from aqsp.strategy import screen_universe
from aqsp.strategies.thresholds import load_thresholds
from aqsp.universe import DEFAULT_SYMBOLS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aqsp")
    sub = parser.add_subparsers(dest="command", required=True)

    screen = sub.add_parser("screen", help="screen stock candidates")
    screen.add_argument("--mode", choices=["open", "close"], default="close")
    screen.add_argument("--symbols", default="", help="comma separated A-share symbols")
    screen.add_argument("--csv", default="", help="local OHLCV csv path")
    screen.add_argument(
        "--source", choices=["akshare"], default="akshare", help="data source"
    )
    screen.add_argument("--limit", type=int, default=20)
    screen.add_argument("--min-avg-amount", type=float, default=50_000_000)
    screen.add_argument("--report", default="", help="write markdown report")
    screen.add_argument("--output-csv", default="", help="write result csv")
    screen.add_argument("--benchmark-symbol", default="000300")

    run = sub.add_parser(
        "run", help="scheduled screen with freshness check and optional notification"
    )
    run.add_argument("--mode", choices=["open", "close"], default="")
    run.add_argument("--symbols", default="")
    run.add_argument("--csv", default="")
    run.add_argument(
        "--source", choices=["akshare"], default="akshare", help="data source"
    )
    run.add_argument("--limit", type=int, default=0)
    run.add_argument("--min-avg-amount", type=float, default=0)
    run.add_argument("--max-data-lag-days", type=int, default=0)
    run.add_argument("--report", default="reports/latest.md")
    run.add_argument("--output-csv", default="reports/latest.csv")
    run.add_argument("--ledger", default="data/predictions.jsonl")
    run.add_argument("--horizon-days", type=int, default=3)
    run.add_argument("--fee-bps", type=float, default=8.0)
    run.add_argument("--slippage-bps", type=float, default=5.0)
    run.add_argument("--benchmark-symbol", default="000300")
    run.add_argument("--skip-validation", action="store_true")
    run.add_argument("--notify", action="store_true")

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
        "--source", choices=["akshare", "mootdx", "sina"], default="akshare"
    )

    monitor_cmd = sub.add_parser("monitor", help="run monitoring checks")
    monitor_cmd.add_argument("--config", default="config/monitors.yaml")
    monitor_cmd.add_argument("--notify", action="store_true")
    monitor_cmd.add_argument("--dry-run", action="store_true")

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

    args = parser.parse_args(argv)
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
    return 1


def _get_source(source_name: str):
    if source_name == "akshare":
        return AkshareSource()
    raise ValueError(f"Unknown data source: {source_name}")


def _count_independent_signal_days(ledger_path: str) -> int:
    from aqsp.ledger.base import read_ledger

    rows = read_ledger(ledger_path)
    signal_dates = set()
    for row in rows:
        if row.get("status") in ("validated", "pending"):
            signal_dates.add(row.get("signal_date", ""))
    return len(signal_dates)


def _get_hs300_symbols() -> list[str]:
    """沪深300成分股的近似快照（手工维护，去重后保序）。

    真实成分股会随季度调整，正式 walk-forward 应从 akshare 拉
    `index_stock_cons_csindex(symbol="000300")` 获取 point-in-time 成分。
    这里只用于无 --symbols 传入时的便利默认值。
    """
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


def run_screen(args: argparse.Namespace) -> int:
    if args.csv:
        frames = load_csv(args.csv)
    else:
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
        if not symbols:
            raise SystemExit("--symbols or --csv is required")
        if args.source == "akshare":
            frames = fetch_akshare(symbols, benchmark_symbol=args.benchmark_symbol)
        else:
            source = _get_source(args.source)
            frames = fetch_with_source(
                source, symbols, benchmark_symbol=args.benchmark_symbol
            )

    config = ScreeningConfig(mode=args.mode, min_avg_amount=args.min_avg_amount)
    picks = screen_universe(frames, config)[: args.limit]
    table = to_dataframe(picks)
    if table.empty:
        print("No candidates.")
    else:
        print(table.to_string(index=False))

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            to_markdown(picks, title=f"AI 量化选股报告({args.mode})"),
            encoding="utf-8",
        )
    if args.output_csv:
        Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(args.output_csv, index=False)
    return 0


def run_scheduled(args: argparse.Namespace) -> int:
    env = load_runtime_config()
    mode = args.mode or env.mode
    symbols = [
        item.strip() for item in args.symbols.split(",") if item.strip()
    ] or list(env.symbols or DEFAULT_SYMBOLS)
    limit = args.limit or env.limit
    min_avg_amount = args.min_avg_amount or env.min_avg_amount
    max_data_lag_days = args.max_data_lag_days or env.max_data_lag_days

    if args.csv:
        frames = load_csv(args.csv)
    elif args.source == "akshare":
        frames = fetch_akshare(symbols, benchmark_symbol=args.benchmark_symbol)
    else:
        source = _get_source(args.source)
        frames = fetch_with_source(
            source, symbols, benchmark_symbol=args.benchmark_symbol
        )

    latest = assert_fresh_data(frames, max_data_lag_days)

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
    if status.triggered:
        print(f"⚠️  熔断触发: {status.reason}")
        print("   本期信号仅供参考，不建议新建仓位")
        return 2

    weights = strategy_weights_from_ledger(args.ledger)

    cold_start_days = _count_independent_signal_days(args.ledger)
    is_cold_start = cold_start_days < 30

    config = ScreeningConfig(
        mode=mode, min_avg_amount=min_avg_amount, strategy_weights=weights
    )
    picks = screen_universe(frames, config)[:limit]

    lethal_pipeline = LethalFilterPipeline()
    filtered_picks = []
    for pick in picks:
        df = frames.get(pick.symbol, pd.DataFrame())
        passed, rejected_by = lethal_pipeline.run(pick.symbol, df)
        if passed:
            filtered_picks.append(pick)
    if len(filtered_picks) < len(picks):
        print(
            f"排雷过滤: {len(picks)} → {len(filtered_picks)} (过滤 {len(picks) - len(filtered_picks)} 只)"
        )
    picks = filtered_picks

    from aqsp.core.time import today_shanghai
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
    thresholds = load_thresholds()
    bench_frame = frames.get(args.benchmark_symbol) if args.benchmark_symbol else None
    if bench_frame is not None and not bench_frame.empty:
        regime = RegimeDetector().detect({args.benchmark_symbol: bench_frame}).name
    else:
        regime = ""

    nb_z = 0.0
    margin_z = 0.0
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

    append_predictions(
        args.ledger,
        picks,
        execution=execution,
        thresholds_version=thresholds.version,
        regime=regime,
        northbound_flow_5d_z=nb_z,
        margin_balance_change_5d=margin_z,
    )

    table = to_dataframe(picks)
    markdown = to_markdown(
        picks, title=f"AI 量化选股报告({mode}, 数据日期 {latest.isoformat()})"
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
    Path(args.report).write_text(markdown, encoding="utf-8")
    Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.output_csv, index=False)
    print(markdown)

    if args.notify:
        results = notify_markdown(markdown)
        if not results:
            print("No notification channel configured.")
        for result in results:
            status = "ok" if result.ok else "failed"
            print(f"notify {result.channel}: {status} ({result.detail})")
    return 0


def run_walkforward(args: argparse.Namespace) -> int:
    import logging
    import sys
    from aqsp.backtest.walk_forward import WalkForwardTester
    from aqsp.core.time import now_shanghai, today_shanghai
    from aqsp.data import fetch_akshare, load_csv
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

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        symbols = _get_hs300_symbols()
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

    if args.source == "akshare":
        frames = fetch_akshare(
            symbols, benchmark_symbol=None, cache_path=args.cache_path
        )
    elif args.source == "mootdx":
        from datetime import date as _date
        from aqsp.data.mootdx_source import MootdxSource

        src = MootdxSource()
        start_d = _date.fromisoformat(args.start)
        end_d = _date.fromisoformat(args.end)
        # 7 年 ~1700 交易日，count=2000 留余量；mootdx 实际上限可能 < 2000，
        # 拿不满会按实际返回。第一次跑后看 logs 确认覆盖了 args.start。
        frames = src.fetch_daily(
            symbols, start_d, end_d, adjust="", count=2000
        )
    elif args.source == "sina":
        from datetime import date as _date
        from aqsp.data.sina_source import SinaSource

        src = SinaSource()
        start_d = _date.fromisoformat(args.start)
        end_d = _date.fromisoformat(args.end)
        frames = src.fetch_daily(symbols, start_d, end_d, adjust="")
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

    # Fetch fundamental data (PE/PB) and merge into OHLCV
    try:
        from aqsp.data.sina_fundamental import SinaFundamentalSource

        fund_src = SinaFundamentalSource()
        fundamentals = fund_src.fetch_realtime_fundamentals(list(filtered.keys()))
        filtered = fund_src.merge_fundamentals_into_ohlcv(filtered, fundamentals)
        print(f"基本面数据: {len(fundamentals)} 只有 PE/PB")
    except Exception as e:
        print(f"⚠️  基本面数据获取失败: {e}，quality/value 因子将返回中性值")

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
    if result.deflated_sharpe > 1.0 and result.pbo < 0.5:
        tl_dr.append("✅ **策略通过 walk-forward 验证**: DSR > 1.0 且 PBO < 50%")
    elif result.deflated_sharpe > 0.5:
        tl_dr.append("⚠️ **策略表现一般**: DSR > 0.5 但未达到 1.0")
    elif result.deflated_sharpe < 0:
        tl_dr.append("❌ **DSR < 0**: 策略收益为负或严重过拟合，不建议实盘")
    else:
        tl_dr.append("❌ **策略未通过验证**: DSR < 0.5")

    if result.pbo > 0.5:
        tl_dr.append(f"⚠️ **PBO = {result.pbo:.2%} > 50%**: 过拟合风险高")

    report_lines = [
        "# Walk-Forward 回测报告",
        "",
        "## TL;DR",
        "",
        *tl_dr,
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

    if result.deflated_sharpe > 1.0 and result.pbo < 0.5:
        report_lines.append(
            "✅ **策略通过 walk-forward 验证**: DSR > 1.0 且 PBO < 50%,可以考虑实盘使用。"
        )
    elif result.deflated_sharpe > 0.5:
        report_lines.append(
            "⚠️ **策略表现一般**: DSR > 0.5 但未达到 1.0,建议进一步优化或增加样本。"
        )
    else:
        report_lines.append(
            "❌ **策略未通过验证**: DSR < 0.5,策略可能无效或严重过拟合,不建议实盘使用。"
        )

    report = "\n".join(report_lines)

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(report, encoding="utf-8")
    print(report)
    print(f"\n报告已保存到: {args.report}")

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
            break

    generator = BriefingGenerator()
    briefing = generator.generate(
        picks=picks,
        frames={},
        regime=regime_str,
    )
    briefing = enhance_briefing(briefing, enable_llm=args.enable_llm)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(briefing.to_markdown(), encoding="utf-8")
    print(briefing.to_markdown())

    if args.notify:
        send_briefing(briefing)

    if getattr(args, "email", False):
        from aqsp.briefing.email_notifier import (
            load_email_config_from_env,
            send_briefing_email,
        )

        cfg = load_email_config_from_env()
        if cfg is None:
            print(
                "⚠️  --email 已开启但 AQSP_SMTP_* 环境变量不全，跳过邮件发送"
            )
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
