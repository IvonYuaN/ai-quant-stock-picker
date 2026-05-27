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


def append_predictions(path: str | Path, picks: list[PickResult], horizon_days: int = 1) -> None:
    rows = read_ledger(path)
    now = datetime.now().isoformat(timespec="seconds")
    for pick in picks:
        rows.append(
            {
                "id": uuid4().hex,
                "created_at": now,
                "pick_date": pick.date,
                "symbol": pick.symbol,
                "name": pick.name,
                "entry_price": pick.close,
                "score": pick.score,
                "rating": pick.rating,
                "strategies": list(pick.strategies),
                "reasons": list(pick.reasons),
                "risks": list(pick.risks),
                "horizon_days": horizon_days,
                "status": "pending",
            }
        )
    write_ledger(path, rows)


def validate_predictions(path: str | Path, frames: dict[str, pd.DataFrame]) -> ValidationSummary:
    rows = read_ledger(path)
    checked = 0
    wins = 0
    returns: list[float] = []

    for row in rows:
        if row.get("status") != "pending":
            continue
        symbol = str(row.get("symbol", ""))
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            continue
        frame = frame.sort_values("date").reset_index(drop=True)
        future = frame[frame["date"] > row.get("pick_date", "")]
        horizon = int(row.get("horizon_days") or 1)
        if len(future) < horizon:
            continue
        exit_bar = future.iloc[horizon - 1]
        exit_price = float(exit_bar["close"])
        entry_price = float(row["entry_price"])
        ret = (exit_price - entry_price) / entry_price * 100
        row["status"] = "validated"
        row["exit_date"] = str(exit_bar["date"])
        row["exit_price"] = round(exit_price, 4)
        row["return_pct"] = round(ret, 4)
        row["win"] = ret > 0
        checked += 1
        wins += 1 if ret > 0 else 0
        returns.append(ret)

    write_ledger(path, rows)
    avg = sum(returns) / len(returns) if returns else 0.0
    return ValidationSummary(checked=checked, wins=wins, avg_return_pct=round(avg, 4))


def strategy_weights_from_ledger(path: str | Path) -> dict[str, float]:
    stats: dict[str, list[float]] = {}
    for row in read_ledger(path):
        if row.get("status") != "validated":
            continue
        ret = float(row.get("return_pct") or 0)
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
