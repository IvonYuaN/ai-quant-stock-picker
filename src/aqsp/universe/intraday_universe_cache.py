"""Validated runtime cache for bounded intraday universe rotation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from aqsp.core.time import get_previous_trading_day, now_shanghai, parse_iso8601
from aqsp.utils.jsonl_io import atomic_write_text

SCHEMA_VERSION = "v1"
DEFAULT_MIN_SYMBOLS = 3000
_SUPPORTED_PREFIXES = ("000", "001", "002", "300", "301", "600", "601", "603", "605")


@dataclass(frozen=True)
class IntradayUniverseCache:
    trade_date: str
    resolved_at: str
    source: str
    symbols: tuple[str, ...]
    universe_hash: str


def write_intraday_universe_cache(
    path: str | Path,
    *,
    symbols: list[str],
    source: str,
    trade_date: date,
    resolved_at: datetime | None = None,
) -> IntradayUniverseCache:
    """Atomically persist a complete, normalized live-universe membership list."""
    normalized = _normalize_symbols(symbols)
    if not normalized:
        raise ValueError("intraday universe cache requires symbols")
    timestamp = resolved_at or now_shanghai()
    cache = IntradayUniverseCache(
        trade_date=trade_date.isoformat(),
        resolved_at=timestamp.isoformat(timespec="seconds"),
        source=source.strip(),
        symbols=normalized,
        universe_hash=_symbols_hash(normalized),
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "trade_date": cache.trade_date,
        "resolved_at": cache.resolved_at,
        "source": cache.source,
        "universe_count": len(cache.symbols),
        "universe_hash": cache.universe_hash,
        "symbols": list(cache.symbols),
    }
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        target, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    )
    return cache


def load_intraday_universe_cache(
    path: str | Path,
    *,
    trade_date: date,
    source: str,
    min_symbols: int = DEFAULT_MIN_SYMBOLS,
    now: datetime | None = None,
) -> IntradayUniverseCache | None:
    """Read a same-day or immediately previous-trading-day cache fail-closed."""
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return None
    try:
        cached_day = date.fromisoformat(str(payload.get("trade_date") or ""))
        resolved_at = parse_iso8601(str(payload.get("resolved_at") or ""))
    except (TypeError, ValueError):
        return None
    current = now or now_shanghai()
    if resolved_at > current or cached_day not in {
        trade_date,
        get_previous_trading_day(trade_date),
    }:
        return None
    cached_source = str(payload.get("source") or "").strip()
    raw_symbols = payload.get("symbols")
    if (
        not cached_source
        or cached_source != source.strip()
        or not isinstance(raw_symbols, list)
    ):
        return None
    symbols = _normalize_symbols(raw_symbols)
    if len(symbols) < max(1, min_symbols):
        return None
    if int(payload.get("universe_count") or 0) != len(symbols):
        return None
    expected_hash = str(payload.get("universe_hash") or "")
    actual_hash = _symbols_hash(symbols)
    if expected_hash != actual_hash:
        return None
    return IntradayUniverseCache(
        trade_date=cached_day.isoformat(),
        resolved_at=resolved_at.isoformat(timespec="seconds"),
        source=cached_source,
        symbols=symbols,
        universe_hash=actual_hash,
    )


def _normalize_symbols(raw_symbols: list[object]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                symbol
                for item in raw_symbols
                if (symbol := str(item).strip())
                and len(symbol) == 6
                and symbol.isdigit()
                and symbol.startswith(_SUPPORTED_PREFIXES)
            }
        )
    )


def _symbols_hash(symbols: tuple[str, ...]) -> str:
    digest = hashlib.sha256("\n".join(symbols).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
