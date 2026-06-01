from __future__ import annotations

from datetime import date
from pathlib import Path
import json

from aqsp.data.trading_calendar import resolve_previous_trading_day
from aqsp.ratings import is_tradable_rating


def get_yesterday_buys(ledger_path: str | Path, today: date) -> set[str]:
    """读 ledger，返回昨日可进入持仓观察的 symbol。"""
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
            if not is_tradable_rating(rec.get("rating")):
                continue
            sym = rec.get("symbol")
            if sym:
                buys.add(str(sym))
    return buys


def _previous_trading_day(d: date) -> date:
    """优先使用可选交易日历，缺失时退回本地简化逻辑。"""
    return resolve_previous_trading_day(d)


def filter_t1_held(
    candidates: list[str],
    ledger_path: str | Path,
    today: date,
) -> tuple[list[str], list[str]]:
    """从 candidates 中剔除昨日可进入持仓观察的 symbol。Returns (kept, removed)"""
    held = get_yesterday_buys(ledger_path, today)
    kept = [s for s in candidates if s not in held]
    removed = [s for s in candidates if s in held]
    return kept, removed
