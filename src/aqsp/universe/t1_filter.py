from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import json


def get_yesterday_buys(ledger_path: str | Path, today: date) -> set[str]:
    """读 ledger，返回昨日所有有 buy 信号的 symbol。"""
    path = Path(ledger_path)
    if not path.exists():
        return set()
    yesterday_str = _previous_trading_day(today).isoformat()
    buys: set[str] = set()
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("signal_date") != yesterday_str:
                continue
            sym = rec.get("symbol")
            if sym:
                buys.add(str(sym))
    return buys


def _previous_trading_day(d: date) -> date:
    """简化版：跳过周末。"""
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev


def filter_t1_held(
    candidates: list[str],
    ledger_path: str | Path,
    today: date,
) -> tuple[list[str], list[str]]:
    """从 candidates 中剔除昨日有信号的 symbol。Returns (kept, removed)"""
    held = get_yesterday_buys(ledger_path, today)
    kept = [s for s in candidates if s not in held]
    removed = [s for s in candidates if s in held]
    return kept, removed
