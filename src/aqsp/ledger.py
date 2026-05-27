from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

from aqsp.models import PickResult


@dataclass(frozen=True)
class ValidationSummary:
    checked: int
    wins: int
    avg_return_pct: float
    avg_excess_pct: float


@dataclass(frozen=True)
class ExecutionConfig:
    horizon_days: int = 3
    fee_bps: float = 8.0
    slippage_bps: float = 5.0
    benchmark_symbol: str = "000300"


def read_ledger(path: str | Path) -> list[dict]:
    ledger = Path(path)
    if not ledger.exists():
        return []
    rows = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_ledger(path: str | Path, rows: list[dict]) -> None:
    ledger = Path(path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    ledger.write_text((text + "\n") if text else "", encoding="utf-8")


def append_predictions(path: str | Path, picks: list[PickResult], execution: ExecutionConfig | None = None) -> None:
    execution = execution or ExecutionConfig()
    rows = read_ledger(path)
    now = datetime.now().isoformat(timespec="seconds")
    for pick in picks:
        rows.append(
            {
                "id": uuid4().hex,
                "created_at": now,
                "signal_date": pick.date,
                "symbol": pick.symbol,
                "name": pick.name,
                "signal_close": pick.close,
                "intended_entry": "next_open",
                "score": pick.score,
                "rating": pick.rating,
                "strategies": list(pick.strategies),
                "reasons": list(pick.reasons),
                "risks": list(pick.risks),
                "stop_loss": pick.stop_loss,
                "take_profit": pick.take_profit,
                "horizon_days": execution.horizon_days,
                "fee_bps": execution.fee_bps,
                "slippage_bps": execution.slippage_bps,
                "benchmark_symbol": execution.benchmark_symbol,
                "status": "pending",
            }
        )
    write_ledger(path, rows)


def validate_predictions(path: str | Path, frames: dict[str, pd.DataFrame]) -> ValidationSummary:
    rows = read_ledger(path)
    checked = 0
    wins = 0
    returns: list[float] = []
    excess_returns: list[float] = []

    for row in rows:
        if row.get("status") != "pending":
            continue
        symbol = str(row.get("symbol", ""))
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            continue
        frame = frame.sort_values("date").reset_index(drop=True)
        future = frame[frame["date"] > row.get("signal_date", row.get("pick_date", ""))]
        horizon = int(row.get("horizon_days") or 1)
        if len(future) < horizon:
            continue
        entry_bar = future.iloc[0]
        eval_window = future.iloc[:horizon]
        entry_price = float(entry_bar["open"]) * (1 + float(row.get("slippage_bps") or 0) / 10000)
        fee_pct = float(row.get("fee_bps") or 0) / 100
        exit_bar, exit_price, exit_reason = _resolve_exit(eval_window, row)
        ret = (exit_price - entry_price) / entry_price * 100 - fee_pct
        benchmark_ret = _benchmark_return(frames, row, entry_bar, exit_bar)
        excess = ret - benchmark_ret if benchmark_ret is not None else 0.0
        row["status"] = "validated"
        row["entry_date"] = str(entry_bar["date"])
        row["entry_price"] = round(entry_price, 4)
        row["exit_date"] = str(exit_bar["date"])
        row["exit_price"] = round(exit_price, 4)
        row["exit_reason"] = exit_reason
        row["return_pct"] = round(ret, 4)
        row["benchmark_return_pct"] = round(benchmark_ret, 4) if benchmark_ret is not None else None
        row["excess_return_pct"] = round(excess, 4)
        row["win"] = ret > 0
        checked += 1
        wins += 1 if ret > 0 else 0
        returns.append(ret)
        excess_returns.append(excess)

    write_ledger(path, rows)
    avg = sum(returns) / len(returns) if returns else 0.0
    avg_excess = sum(excess_returns) / len(excess_returns) if excess_returns else 0.0
    return ValidationSummary(
        checked=checked,
        wins=wins,
        avg_return_pct=round(avg, 4),
        avg_excess_pct=round(avg_excess, 4),
    )


def strategy_weights_from_ledger(path: str | Path) -> dict[str, float]:
    stats: dict[str, list[float]] = {}
    for row in read_ledger(path):
        if row.get("status") != "validated":
            continue
        ret = float(row.get("excess_return_pct") if row.get("excess_return_pct") is not None else row.get("return_pct") or 0)
        for strategy in row.get("strategies") or []:
            stats.setdefault(strategy, []).append(ret)

    weights: dict[str, float] = {}
    for strategy, returns in stats.items():
        if len(returns) < 3:
            continue
        win_rate = sum(1 for ret in returns if ret > 0) / len(returns)
        avg_ret = sum(returns) / len(returns)
        weights[strategy] = round(max(0.65, min(1.45, 1 + (win_rate - 0.5) * 0.7 + avg_ret / 20)), 3)
    return weights


def _resolve_exit(window: pd.DataFrame, row: dict) -> tuple[pd.Series, float, str]:
    stop_loss = float(row.get("stop_loss") or 0)
    take_profit = float(row.get("take_profit") or 0)
    slippage = float(row.get("slippage_bps") or 0) / 10000
    for _, bar in window.iterrows():
        low = float(bar["low"]) if "low" in bar else float(bar["close"])
        high = float(bar["high"]) if "high" in bar else float(bar["close"])
        if stop_loss > 0 and low <= stop_loss:
            return bar, stop_loss * (1 - slippage), "stop_loss"
        if take_profit > 0 and high >= take_profit:
            return bar, take_profit * (1 - slippage), "take_profit"
    last = window.iloc[-1]
    return last, float(last["close"]) * (1 - slippage), "horizon_close"


def _benchmark_return(frames: dict[str, pd.DataFrame], row: dict, entry_bar: pd.Series, exit_bar: pd.Series) -> float | None:
    benchmark = frames.get(str(row.get("benchmark_symbol") or ""))
    if benchmark is None or benchmark.empty:
        return None
    benchmark = benchmark.sort_values("date").reset_index(drop=True)
    entry_rows = benchmark[benchmark["date"] >= entry_bar["date"]]
    exit_rows = benchmark[benchmark["date"] <= exit_bar["date"]]
    if entry_rows.empty or exit_rows.empty:
        return None
    entry = float(entry_rows.iloc[0]["open"])
    exit_price = float(exit_rows.iloc[-1]["close"])
    if entry <= 0:
        return None
    return (exit_price - entry) / entry * 100
