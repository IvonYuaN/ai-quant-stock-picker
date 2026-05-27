from __future__ import annotations

import argparse
from pathlib import Path

from aqsp.config import load_runtime_config
from aqsp.data import fetch_akshare, load_csv
from aqsp.freshness import assert_fresh_data
from aqsp.models import ScreeningConfig
from aqsp.notifier import notify_markdown
from aqsp.report import to_dataframe, to_markdown
from aqsp.strategy import screen_universe
from aqsp.universe import DEFAULT_SYMBOLS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aqsp")
    sub = parser.add_subparsers(dest="command", required=True)

    screen = sub.add_parser("screen", help="screen stock candidates")
    screen.add_argument("--mode", choices=["open", "close"], default="close")
    screen.add_argument("--symbols", default="", help="comma separated A-share symbols for akshare")
    screen.add_argument("--csv", default="", help="local OHLCV csv path")
    screen.add_argument("--limit", type=int, default=20)
    screen.add_argument("--min-avg-amount", type=float, default=50_000_000)
    screen.add_argument("--report", default="", help="write markdown report")
    screen.add_argument("--output-csv", default="", help="write result csv")

    run = sub.add_parser("run", help="scheduled screen with freshness check and optional notification")
    run.add_argument("--mode", choices=["open", "close"], default="")
    run.add_argument("--symbols", default="")
    run.add_argument("--csv", default="")
    run.add_argument("--limit", type=int, default=0)
    run.add_argument("--min-avg-amount", type=float, default=0)
    run.add_argument("--max-data-lag-days", type=int, default=0)
    run.add_argument("--report", default="reports/latest.md")
    run.add_argument("--output-csv", default="reports/latest.csv")
    run.add_argument("--notify", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "screen":
        return run_screen(args)
    if args.command == "run":
        return run_scheduled(args)
    return 1


def run_screen(args: argparse.Namespace) -> int:
    if args.csv:
        frames = load_csv(args.csv)
    else:
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
        if not symbols:
            raise SystemExit("--symbols or --csv is required")
        frames = fetch_akshare(symbols)

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
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()] or list(env.symbols or DEFAULT_SYMBOLS)
    limit = args.limit or env.limit
    min_avg_amount = args.min_avg_amount or env.min_avg_amount
    max_data_lag_days = args.max_data_lag_days or env.max_data_lag_days

    frames = load_csv(args.csv) if args.csv else fetch_akshare(symbols)
    latest = assert_fresh_data(frames, max_data_lag_days)
    config = ScreeningConfig(mode=mode, min_avg_amount=min_avg_amount)
    picks = screen_universe(frames, config)[:limit]

    table = to_dataframe(picks)
    markdown = to_markdown(picks, title=f"AI 量化选股报告({mode}, 数据日期 {latest.isoformat()})")
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


if __name__ == "__main__":
    raise SystemExit(main())
