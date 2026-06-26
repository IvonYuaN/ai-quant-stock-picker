from __future__ import annotations

from datetime import date
from pathlib import Path
import json

from aqsp.data.trading_calendar import resolve_previous_trading_day


T1_BLOCKING_PAPER_STATUSES = frozenset({"open", "pending_entry"})


def get_yesterday_buys(ledger_path: str | Path, today: date) -> set[str]:
    """读 paper ledger，返回昨日真实纸面持有/待入场的 symbol。"""
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
            if not _is_yesterday_blocking_paper_row(rec, yesterday_str):
                continue
            sym = rec.get("symbol")
            if sym:
                buys.add(str(sym))
    return buys


def _previous_trading_day(d: date) -> date:
    """优先使用可选交易日历，缺失时退回本地简化逻辑。"""
    return resolve_previous_trading_day(d)


def _is_yesterday_blocking_paper_row(rec: dict, yesterday_str: str) -> bool:
    status = str(rec.get("status") or "").strip()
    if status not in T1_BLOCKING_PAPER_STATUSES:
        return False
    if status == "open":
        return _date_prefix(rec.get("entry_date")) == yesterday_str
    return _date_prefix(rec.get("signal_date")) == yesterday_str


def _date_prefix(value: object) -> str:
    raw = str(value or "").strip()
    return raw[:10] if len(raw) >= 10 else ""


def filter_t1_held(
    candidates: list[str],
    ledger_path: str | Path,
    today: date,
) -> tuple[list[str], list[str]]:
    """从 candidates 中剔除昨日仍受 T+1 约束的 paper symbol。"""
    held = get_yesterday_buys(ledger_path, today)
    kept = [s for s in candidates if s not in held]
    removed = [s for s in candidates if s in held]
    return kept, removed
