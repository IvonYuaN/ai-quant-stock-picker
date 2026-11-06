"""Run one isolated custom-strategy raw/PIT replay worker."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import date, timedelta
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

import tenant  # noqa: E402
import strategy_backtests  # noqa: E402
from aqsp.backtest.custom_strategy import (  # noqa: E402
    StrategyReplayCancelled,
    StrategyReplayPolicy,
    StrategyReplayTimedOut,
    replay_strategy,
)
from aqsp.backtest.strategy_portfolio import replay_strategy_portfolio  # noqa: E402
from aqsp.core.errors import DataError  # noqa: E402
from aqsp.core.time import now_shanghai  # noqa: E402
from aqsp.data.source_factory import build_data_source  # noqa: E402
from aqsp.research.strategy_expression import (  # noqa: E402
    compile_strategy_expression,
    compile_strategy_score_expression,
)
from aqsp.utils.jsonl_io import atomic_write_text  # noqa: E402

_CANCEL_REQUESTED = False


def _request_cancel(_signum: int, _frame: object) -> None:
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = True


def run_worker(run_id: str, state_path: Path, tenant_id: str) -> int:
    global _CANCEL_REQUESTED
    _CANCEL_REQUESTED = False
    signal.signal(signal.SIGTERM, _request_cancel)
    tenant.current_tenant.set(tenant_id)
    deadline = time.monotonic() + 10
    state: dict = {}
    while time.monotonic() < deadline:
        state = strategy_backtests._read(state_path)
        if state.get("run_id") == run_id and state.get("pid") == os.getpid():
            break
        time.sleep(0.05)
    else:
        return 2

    run_dir = state_path.parent
    config = strategy_backtests._read(run_dir / "config.json")
    strategy: dict = config.get("strategy") or {}
    mode = str(config.get("mode", "single"))
    expected_hash = strategy_backtests.strategy_definitions.strategy_expression_hash(
        str(strategy.get("expression", "")),
        str(strategy.get("exit_expression", "")),
        str(strategy.get("score_expression", "")),
    )
    if expected_hash != config.get("strategy_hash"):
        raise DataError("策略定义哈希与回放快照不一致")
    entry = compile_strategy_expression(str(strategy.get("expression", "")))
    exit_text = str(strategy.get("exit_expression", "")).strip()
    exit_expression = compile_strategy_expression(exit_text) if exit_text else None
    score_text = str(strategy.get("score_expression", "")).strip()
    score_expression = (
        compile_strategy_score_expression(score_text)
        if score_text and mode == "portfolio"
        else None
    )
    symbols = [str(item) for item in config.get("symbols", [])]
    if not symbols and config.get("symbol"):
        symbols = [str(config["symbol"])]
    signal_start = date.fromisoformat(config["signal_start"])
    signal_end = date.fromisoformat(config["signal_end"])
    outcome_as_of = date.fromisoformat(config["outcome_as_of"])
    deadline = time.monotonic() + int(config["max_runtime_seconds"])

    def check_replay_state() -> None:
        if _CANCEL_REQUESTED:
            raise StrategyReplayCancelled("用户取消了策略回放")
        if time.monotonic() >= deadline:
            raise StrategyReplayTimedOut("策略回放超过 300 秒运行上限")

    lookback = max(
        entry.max_lookback,
        exit_expression.max_lookback if exit_expression else 1,
        score_expression.max_lookback if score_expression else 1,
    )
    warmup_start = signal_start - timedelta(days=max(90, lookback * 4))
    source = build_data_source("sqlite_db", cache=None)
    if getattr(source, "name", "") != "sqlite_db" or source.price_mode() != "raw":
        raise DataError("用户策略回放仅允许 sqlite_db raw 不复权数据源")

    state.update({
        "status": "running",
        "phase": "loading_raw_pit",
        "message": "正在读取 raw 日线与 point-in-time 因子",
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
    })
    strategy_backtests._write(state_path, state)
    data_symbols = [str(item) for item in config.get("available_symbols", symbols)]
    prefiltered = os.environ.get("AQSP_SQLITE_PREFILTERED_SYMBOLS")
    os.environ["AQSP_SQLITE_PREFILTERED_SYMBOLS"] = "1"
    try:
        frames = source.fetch_daily(data_symbols, warmup_start, outcome_as_of, adjust="")
    finally:
        if prefiltered is None:
            os.environ.pop("AQSP_SQLITE_PREFILTERED_SYMBOLS", None)
        else:
            os.environ["AQSP_SQLITE_PREFILTERED_SYMBOLS"] = prefiltered
    market_sessions = source.get_available_daily_dates(warmup_start, outcome_as_of)
    available_frames = {symbol: frame for symbol, frame in frames.items() if frame is not None and not frame.empty}
    if not available_frames:
        raise DataError("所选标的在回放窗口都没有 raw 日线")
    replay_dates = sorted({
        item.date()
        for frame in available_frames.values()
        for item in pd.to_datetime(frame["date"], errors="coerce").dropna()
    })
    pit = {}
    if (
        entry.requires_pit_price_basis
        or (exit_expression and exit_expression.requires_pit_price_basis)
        or (score_expression and score_expression.requires_pit_price_basis)
    ):
        pit = source.get_point_in_time_adjustment_factors(list(available_frames), replay_dates)
    if _CANCEL_REQUESTED:
        state.update({
            "status": "cancelled",
            "phase": "cancelled",
            "message": "回放在数据准备后被取消",
            "finished_at": now_shanghai().isoformat(timespec="seconds"),
        })
        strategy_backtests._write(state_path, state)
        return 0

    policy_data = config["policy"]
    policy = StrategyReplayPolicy(
        commission_rate=float(policy_data["commission_rate"]),
        stamp_tax_rate=float(policy_data["stamp_tax_rate"]),
        slippage=float(policy_data["slippage"]),
        fallback_limit_main_pct=float(policy_data["fallback_limit_main_pct"]),
        fallback_limit_growth_pct=float(policy_data["fallback_limit_growth_pct"]),
        fallback_limit_bse_pct=float(policy_data["fallback_limit_bse_pct"]),
        fallback_limit_st_pct=float(policy_data["fallback_limit_st_pct"]),
        stop_loss_pct=float(policy_data["stop_loss_pct"]),
        max_hold_days=int(policy_data["max_hold_days"]),
    )
    try:
        if mode == "portfolio":
            result = replay_strategy_portfolio(
                strategy=strategy,
                entry_expression=entry,
                exit_expression=exit_expression,
                score_expression=score_expression,
                frames=available_frames,
                pit_factors=pit,
                symbols=data_symbols,
                signal_start=signal_start,
                signal_end=signal_end,
                outcome_as_of=outcome_as_of,
                names={symbol: source.get_symbol_name(symbol) for symbol in data_symbols},
                policy=policy,
                market_sessions=market_sessions,
                initial_capital=float(config["initial_capital"]),
                max_positions=int(policy_data["max_positions"]),
                max_exposure_pct=float(policy_data["max_exposure_pct"]),
                max_single_position_pct=float(policy_data["max_single_position_pct"]),
                min_cash_reserve_pct=float(policy_data["min_cash_reserve_pct"]),
                position_sizing=str(config.get("position_sizing", "equal")),
                check_cancel=check_replay_state,
            )
        else:
            symbol = symbols[0]
            result = replay_strategy(
                strategy=strategy,
                entry_expression=entry,
                exit_expression=exit_expression,
                frames={symbol: available_frames[symbol]},
                pit_factors=pit,
                symbols=[symbol],
                signal_start=signal_start,
                signal_end=signal_end,
                outcome_as_of=outcome_as_of,
                names={symbol: source.get_symbol_name(symbol)},
                policy=policy,
                market_sessions=market_sessions,
                check_cancel=check_replay_state,
            )
        stale_symbols = config.get("stale_symbols") or {}
        if stale_symbols:
            updates: dict[str, Any] = {
                "blocked_symbols": tuple(result.blocked_symbols) + tuple(
                    {"symbol": str(symbol), "reason": f"stale_data:{actual or 'missing'}"}
                    for symbol, actual in sorted(stale_symbols.items())
                )
            }
            if mode == "portfolio":
                updates["symbols"] = tuple(symbols)
            result = replace(result, **updates)
        check_replay_state()
    except StrategyReplayCancelled:
        state.update({
            "status": "cancelled", "phase": "cancelled",
            "message": "策略回放在计算中被取消；未保存部分结果",
            "finished_at": now_shanghai().isoformat(timespec="seconds"),
        })
        strategy_backtests._write(state_path, state)
        return 0
    except StrategyReplayTimedOut:
        state.update({
            "status": "timed_out", "phase": "timed_out",
            "message": "策略回放超过运行上限；未保存部分结果",
            "finished_at": now_shanghai().isoformat(timespec="seconds"),
        })
        strategy_backtests._write(state_path, state)
        return 0
    result_payload = asdict(result)
    result_payload["trades"] = [asdict(trade) for trade in result.trades]
    atomic_write_text(
        run_dir / "result.json",
        json.dumps(result_payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
    )
    portfolio_report = mode == "portfolio"
    report = (
        f"# {strategy.get('name', strategy['id'])} 策略回放\n\n"
        "独立用户策略模拟；不属于 composite Walk-forward，不生成 DSR/PBO/CSCV/OOS 证据，"
        "不写正式 ledger。\n\n"
        f"- 模式：{'策略组合' if portfolio_report else '单标'}\n"
        f"- 标的池：{'本机 raw 历史行情行 ' + str(config.get('universe_as_of')) if config.get('universe_mode') == 'local_history' else '用户显式代码'}\n"
        f"- 标的池数量：{config.get('universe_symbol_count', len(symbols))}\n"
        f"- 标的池哈希：{config.get('universe_hash', '未记录')}\n"
        f"- 策略：{strategy['id']} r{strategy['revision']}\n"
        f"- 策略哈希：{config['strategy_hash']}\n"
        f"- 标的：{', '.join(symbols)}\n"
        f"- 入场窗口：{signal_start.isoformat()} 至 {signal_end.isoformat()}\n"
        f"- 结果数据截至：{outcome_as_of.isoformat()}\n"
        f"- 撮合：{policy.fill_rule}\n"
        f"- 收益口径：{'共享现金账户净值' if portfolio_report else '逐笔全额顺序复利估算，不是组合净值'}\n"
        f"- 阈值版本：{policy_data['thresholds_version']}\n"
        f"- 已平仓：{result.closed_trades}\n"
        f"- 胜率：{result.win_rate if result.win_rate is not None else '样本不足'}\n"
        f"- 不可成交：{result.not_executable}\n"
        f"- 待确认入场：{result.pending_entries}\n"
        f"- 期末未平仓/退出待执行：{len(result.open_positions) if portfolio_report else result.open_positions}\n"
        + (f"- 期末权益：{result.final_equity:.2f}\n- 最大回撤：{result.max_drawdown_pct:.2f}%\n- 年化 Sharpe：{result.sharpe_ratio if result.sharpe_ratio is not None else '样本不足'}\n" if portfolio_report else "")
    )
    if portfolio_report:
        allocation_text = (
            "评分加权" if config.get("position_sizing") == "score_weight" else "等权"
        )
        details = f"- 组合仓位分配：{allocation_text}\n"
        if score_text:
            details += f"- 评分表达式：{score_text}\n"
        report = report.replace(
            "- 收益口径：",
            f"{details}- 收益口径：",
            1,
        )
    if result.blocked_symbols:
        report += "\n## 数据阻断\n\n" + "\n".join(
            f"- {item['symbol']}：{item['reason']}" for item in result.blocked_symbols
        ) + "\n"
    atomic_write_text(run_dir / "report.md", report)
    state.update({
        "status": "completed",
        "phase": "complete",
        "message": "用户策略回放完成；未修改正式评分/台账",
        "closed_trades": result.closed_trades,
        "not_executable": result.not_executable,
        "pending_entries": result.pending_entries,
        "open_positions": len(result.open_positions) if portfolio_report else result.open_positions,
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
        "finished_at": now_shanghai().isoformat(timespec="seconds"),
    })
    strategy_backtests._write(state_path, state)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--state-path", required=True, type=Path)
    parser.add_argument("--tenant-id", required=True)
    args = parser.parse_args(argv)
    try:
        return run_worker(args.run_id, args.state_path, args.tenant_id)
    except Exception as exc:
        state = strategy_backtests._read(args.state_path)
        state.update({
            "status": "failed",
            "phase": "failed",
            "message": f"用户策略回放失败：{str(exc)[:500]}",
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
            "finished_at": now_shanghai().isoformat(timespec="seconds"),
        })
        strategy_backtests._write(args.state_path, state)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
