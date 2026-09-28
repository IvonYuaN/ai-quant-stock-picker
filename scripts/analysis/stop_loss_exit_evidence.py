#!/usr/bin/env python3
"""
stop_loss_exit_evidence.py — 策略红线项「独立样本验证」之止损规则对比（item 3, Part B）

设计原则（来自红线纪律）：
  - **只读分析 / 只产证据**，不修改任何 live 策略参数（不下参数、不碰 thresholds 默认值）。
  - 复用 prod gate（item 2）的**同一批样本**（symbols / dates / cache），即同一 OOS walk-forward
    样本，只改变「出场规则」这一个维度，做到 apples-to-apples 对比。
  - 直接构造 WalkForwardTester（与 gate 的 _build_tester 参数完全一致，仅额外显式传入
    stop_loss_pct），不改动 research_engine / cli 的 grid 代码。

对比 WF-001 配置（top_n=10, horizon=3, lookback=60, momentum/triple_rise=0.3/0.3）下三种出场规则：
  - current_soft: stop_loss_pct=None   现状动态软止损（=live 默认，等价于 gate 的 _build_tester 不传止损）
  - hard_8pct  : stop_loss_pct=0.08  硬 8% 止损 + 最多持有 horizon
  - hold_3d    : stop_loss_pct=1.0   纯持有至 horizon（3 天），永不触发止损

为干净隔离「止损规则」这一变量，三档统一 take_profit_pct=1.0（即止盈永不触发，
等价于关闭 TP），故本实验比较的是「纯止损政策」对 OOS 的影响，绝对收益与 live（含默认
止盈）不完全一致，但相对差异可干净回答「8% 硬止损 vs 持有 3 天」的取舍。

输出：markdown 报告（OOS 总收益 / Sharpe / 胜率 / PBO / DSR / 各市场状态胜率 + 逐周期收益）。

用法（在 prod 上，复用 gate 已 warm 的 cache）：
  python3 scripts/stop_loss_exit_evidence.py \
      --start 2023-09-09 --end 2026-09-08 \
      --cache-path /opt/aqsp/data/gate_run/walkforward_raw_production_cache.db \
      --symbols-file /opt/aqsp/data/gate_run/evidence_symbols.txt \
      --output /opt/aqsp/data/gate_run/stop_loss_evidence.md

并行拆分 + 断点续跑（gate_finalize.sh 使用，计算口径与串行完全一致）：
  # 每档一个独立进程，结果原子落 json（被 OOM 杀掉也不会丢已完成档位）
  python3 scripts/stop_loss_exit_evidence.py --start ... --end ... --cache-path ... \
      --symbols-file ... --only hard_8pct --json-out evidence_parts/hard_8pct.json --output /dev/null
  # 三档 json 齐全后合并生成 md（跳过回测，秒级）
  python3 scripts/stop_loss_exit_evidence.py --from-json evidence_parts/*.json \
      --output stop_loss_evidence.md

为什么必须能拆分：三档串行约 4.5h，远超 cron 15 分钟轮询间隔；旧版同步阻塞导致
每 15 分钟叠加一个新进程，16+ 实例抢满 8GB 内存互相 OOM（2026-09-10 事故）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import types
from pathlib import Path

# 复用 gate 的流式上下文构造 + 阈值，保证与 item 2 同口径
from aqsp.cli import (
    WalkForwardGridVariant,
    _apply_walkforward_grid_variant,
    _build_streaming_sqlite_context,
    _execution_cost_bps_from_thresholds,
    load_thresholds,
)
from aqsp.strategies.composite import CompositeStrategy
from aqsp.backtest.walk_forward import WalkForwardTester

# WF-001 配置（与 cli.py _WALKFORWARD_VALIDATED_GRID_VARIANTS 第一条完全一致）
WF001 = WalkForwardGridVariant("WF-001", 0.3, 0.3, 60, 3, 10, "momentum")

# 三种出场规则：stop_loss_pct 是唯一被比较的维度；take_profit_pct=1.0 统一关闭止盈以隔离止损
STOP_SETTINGS = {
    "current_soft": {"stop_loss_pct": None, "take_profit_pct": 1.0},  # 现状动态软止损
    "hard_8pct": {"stop_loss_pct": 0.08, "take_profit_pct": 1.0},  # 硬 8% 止损
    "hold_3d": {
        "stop_loss_pct": 1.0,
        "take_profit_pct": 1.0,
    },  # 纯持有 horizon（无止损无止盈）
}

TRAIN_DAYS = 120
TEST_DAYS = 30
PURGE_DAYS = 5
BATCH_SIZE = 200


def _fmt_pct(x: float | None) -> str:
    return "N/A" if x is None else f"{x:.2%}"


def _fmt_pbo(x: float | None) -> str:
    """单序列（n_variants=1）无法做 CSCV，PBO 必为 None——须显式标注，
    避免把「无法计算」误读成「无过拟合」（宪法 §17.7）。"""
    return "N/A（单序列无法做 CSCV）" if x is None else f"{x:.2%}"


def _fmt_stop_label(stop: float | None) -> str:
    if stop is None:
        return "None（现状动态软止损）"
    if stop >= 1.0:
        return "1.0（永不触发＝纯持有 horizon）"
    return f"{stop:.0%}"


def _optin_prefiltered_universe() -> None:
    """复用 gate 已筛选/已覆盖的标的池 → 必须显式 opt-in 两个开关。

    原因（sqlite_db_source.py:434-450）：`fetch_daily` 在未设 `PREFILTERED_SYMBOLS=1`
    时会**逐批**跑覆盖预检，遇到晚上市标的导致某批次 covered=0 就直接
    `raise DataError`，整轮 walk-forward 研究的中间阶段会被中断。gate 由 cron
    注入这两个 env 绕过；本脚本直接用 ssh 跑时没有，故在此固化，避免误跑白费 1.5h。
    二者须成对出现（`ALLOW_EMPTY` 单独设不生效）。
    """
    os.environ.setdefault("PREFILTERED_SYMBOLS", "1")
    os.environ.setdefault("AQSP_SQLITE_ALLOW_EMPTY_SYMBOLS", "1")


def main() -> int:
    _optin_prefiltered_universe()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--cache-path")
    ap.add_argument(
        "--symbols-file",
        help="gate 的 --symbols-file（每行一个 ts_code，纯文本）",
    )
    ap.add_argument("--benchmark-symbol", default="000300")
    ap.add_argument("--stream-batch-size", type=int, default=BATCH_SIZE)
    ap.add_argument("--output", required=True)
    ap.add_argument(
        "--only",
        choices=tuple(STOP_SETTINGS),
        default=None,
        help="只跑指定出场规则（并行拆档用）；结果写 --json-out，不生成 md",
    )
    ap.add_argument(
        "--json-out",
        default=None,
        help="单档结果 json 落盘路径（原子写；被 OOM 杀掉也不丢已完成档位）",
    )
    ap.add_argument(
        "--from-json",
        nargs="+",
        default=None,
        metavar="PATH",
        help="从若干 --json-out 结果合并生成 md（跳过回测）",
    )
    args = ap.parse_args()

    if args.from_json:
        rows = _load_rows(args.from_json)
        if not rows:
            print("[ERR] --from-json 未载入任何有效档位", file=sys.stderr)
            return 2
        _write_report(args, rows)
        print(
            f"[ok] 合并报告已写出: {args.output}（{len(rows)}/{len(STOP_SETTINGS)} 档）"
        )
        return 0

    if not (args.start and args.end and args.cache_path and args.symbols_file):
        print(
            "[ERR] 回测模式需 --start/--end/--cache-path/--symbols-file",
            file=sys.stderr,
        )
        return 2

    symbols_path = Path(args.symbols_file)
    if not symbols_path.exists():
        print(f"[ERR] symbols 文件不存在: {args.symbols_file}", file=sys.stderr)
        return 2
    symbols = [ln.strip() for ln in symbols_path.read_text().splitlines() if ln.strip()]
    print(f"[info] 载入 {len(symbols)} 只标的（来自 item 2 同一样本）")

    sargs = types.SimpleNamespace(
        source="sqlite_db",
        skip_pit_financials=True,
        stream_batch_size=args.stream_batch_size,
        cache_path=args.cache_path,
        start=args.start,
        end=args.end,
        benchmark_symbol=args.benchmark_symbol,
    )
    symbols, load_batch, all_dates, fixed_frames, _market_summary = (
        _build_streaming_sqlite_context(sargs, symbols)
    )
    print(f"[info] 流式上下文就绪: {len(symbols)} 只标的 / {len(all_dates)} 个交易日")

    thresholds = load_thresholds()
    variant_thresholds = _apply_walkforward_grid_variant(thresholds, WF001)
    fee_bps, slippage_bps = _execution_cost_bps_from_thresholds(thresholds)
    print(f"[info] 成本口径: fee={fee_bps:.1f}bps slippage={slippage_bps:.1f}bps")

    todo = [args.only] if args.only else list(STOP_SETTINGS)
    rows: list[dict] = []
    for name in todo:
        cfg = STOP_SETTINGS[name]
        strategy = CompositeStrategy(thresholds=variant_thresholds)
        tester = WalkForwardTester(
            strategy=strategy,
            train_period_days=TRAIN_DAYS,
            test_period_days=TEST_DAYS,
            purge_days=PURGE_DAYS,
            horizon_days=WF001.horizon_days,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            top_n=WF001.top_n,
            stop_loss_pct=cfg["stop_loss_pct"],
            take_profit_pct=cfg["take_profit_pct"],
            use_tiered_stop=False,
            n_variants=1,
            benchmark_symbol=args.benchmark_symbol or None,
            crash_protection=False,
        )
        print(
            f"[run ] {name}: stop_loss_pct={cfg['stop_loss_pct']} tp={cfg['take_profit_pct']} ...",
            flush=True,
        )
        res = tester.run_streaming(
            symbols,
            load_batch,
            all_dates,
            start_date=args.start,
            end_date=args.end,
            fixed_frames=fixed_frames,
            batch_size=args.stream_batch_size,
        )
        period_returns = [float(p.total_return) for p in res.periods]
        wins = sum(1 for r in period_returns if r > 0)
        row = {
            "name": name,
            "window": {"start": args.start, "end": args.end},
            "stop_loss_pct": cfg["stop_loss_pct"],
            "take_profit_pct": cfg["take_profit_pct"],
            "overall_total_return": float(res.overall.total_return),
            "overall_sharpe": float(res.overall.sharpe_ratio),
            "overall_win_rate": float(res.overall.win_rate),
            "pbo": res.pbo,
            "dsr": float(res.deflated_sharpe),
            "n_periods": len(res.periods),
            "period_win_rate": (wins / len(period_returns)) if period_returns else None,
            "avg_period_return": (sum(period_returns) / len(period_returns))
            if period_returns
            else None,
            "regime_winrates": dict(res.regime_winrates or {}),
            "period_returns": period_returns,
        }
        rows.append(row)
        if args.json_out:
            _write_json(args.json_out, row)
            print(f"[save] {name} -> {args.json_out}", flush=True)
        print(
            f"[done] {name}: total={_fmt_pct(row['overall_total_return'])} "
            f"sharpe={res.overall.sharpe_ratio:.2f} "
            f"win={_fmt_pct(row['overall_win_rate'])} "
            f"pbo={res.pbo} dsr={res.deflated_sharpe:.3f}",
            flush=True,
        )

    if args.only is not None:
        print(
            f"[ok] 单档 {args.only} 完成 -> {args.json_out or '(未落 json)'}；md 由 --from-json 合并生成"
        )
        return 0

    _write_report(args, rows)
    print(f"[ok] 报告已写出: {args.output}")
    return 0


def _write_json(path: str, row: dict) -> None:
    """原子写单档结果：先写 .tmp 再 rename，避免进程被 OOM 杀掉时留下半截 json。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _load_rows(paths: list[str]) -> list[dict]:
    """按 STOP_SETTINGS 固定顺序装配档位，缺失的档位跳过（不打断合并）。"""
    by_name: dict[str, dict] = {}
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            print(f"[warn] 跳过缺失的 json: {raw}", file=sys.stderr)
            continue
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[warn] 跳过损坏的 json {raw}: {exc}", file=sys.stderr)
            continue
        by_name[str(row.get("name"))] = row
    rows = [by_name[n] for n in STOP_SETTINGS if n in by_name]
    missing = [n for n in STOP_SETTINGS if n not in by_name]
    if missing:
        print(f"[warn] 缺失档位: {', '.join(missing)}", file=sys.stderr)
    return rows


def _write_report(args: argparse.Namespace, rows: list[dict]) -> None:
    lines: list[str] = []
    win = rows[0].get("window", {}) if rows else {}
    start = win.get("start") or args.start or "N/A"
    end = win.get("end") or args.end or "N/A"
    lines.append("# 止损出场规则独立样本验证（item 3 / Part B）\n")
    lines.append(f"- 样本区间: `{start}` ~ `{end}`（与 item 2 gate 同一样本）")
    lines.append(
        "- 配置: WF-001（top_n=10, horizon=3, lookback=60, momentum/triple_rise=0.3/0.3）"
    )
    lines.append(f"- 训练/测试/Purge: {TRAIN_DAYS}/{TEST_DAYS}/{PURGE_DAYS} 天")
    lines.append(
        "- 唯一被比较维度: `stop_loss_pct`（出场规则）；三档统一 `take_profit_pct=1.0`（止盈已关闭，仅比止损政策）\n"
    )

    lines.append("## 汇总对比\n")
    lines.append(
        "| 出场规则 | stop_loss_pct | OOS 总收益 | Sharpe | 总胜率 | 周期胜率 | PBO | DSR | 周期数 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        sl = _fmt_stop_label(r["stop_loss_pct"])
        lines.append(
            f"| {r['name']} | {sl} | {_fmt_pct(r['overall_total_return'])} | "
            f"{r['overall_sharpe']:.2f} | {_fmt_pct(r['overall_win_rate'])} | "
            f"{_fmt_pct(r['period_win_rate'])} | {_fmt_pbo(r['pbo'])} | "
            f"{r['dsr']:.3f} | {r['n_periods']} |"
        )

    lines.append("\n## 各市场状态胜率\n")
    regimes = sorted({rg for r in rows for rg in r["regime_winrates"]})
    lines.append("| 出场规则 | " + " | ".join(regimes) + " |")
    lines.append("|---" * (len(regimes) + 1) + "|")
    for r in rows:
        cells = [r["name"]] + [
            f"{_fmt_pct(r['regime_winrates'].get(rg))}" for rg in regimes
        ]
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("\n## 逐周期 OOS 收益\n")
    n = max(len(r["period_returns"]) for r in rows)
    header = "| # | " + " | ".join(r["name"] for r in rows) + " |"
    sep = "|---" * (len(rows) + 1) + "|"
    lines.append(header)
    lines.append(sep)
    for i in range(n):
        cells = [str(i + 1)]
        for r in rows:
            pr = r["period_returns"][i] if i < len(r["period_returns"]) else None
            cells.append(_fmt_pct(pr))
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("\n## 结论（证据，非决策）\n")
    lines.append(
        "- ⚠️ **口径警示**：本脚本每个出场规则都是**单序列**回测（`n_variants=1`），因此"
    )
    lines.append(
        "  - `PBO` 恒为 N/A —— 单序列无法做 CSCV（宪法 §17.7）；本实验**不作过拟合判级**；"
    )
    lines.append(
        "  - `DSR` 未做多重检验修正（`n_trials=1`），**不可与 gate 的 grid CSCV 读数横向比较**；"
    )
    lines.append(
        "  - 本实验的有效结论只来自 **同一 OOS 样本下的相对比较**：OOS 总收益 / Sharpe / 胜率。"
    )
    lines.append(
        "- 本表为同一 OOS 样本、仅改变出场规则（止损）的对比，可用于佐证「8% 硬止损 vs 持有 3 天」的取舍。"
    )
    lines.append(
        "- 实验设计：三档统一关闭止盈（take_profit_pct=1.0），故比较的是「纯止损政策」对 OOS 的影响；"
    )
    lines.append(
        "  绝对收益与 live（含默认止盈）不完全一致，但相对差异干净隔离了止损维度。"
    )
    lines.append(
        "- `current_soft`(None) 即 live 现状（动态 ATR 软止损）；`hard_8pct` 为固定 8% 硬止损；`hold_3d`(1.0) 为纯持有 horizon。"
    )
    lines.append("- 若要上线任一规则，须走正式 PR（不直接改 live 参数）。")
    Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
