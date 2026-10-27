"""Current source evidence for position strategy; ordinary reads never refresh.

Explicit refresh shares file caches across tenants and uses one nonblocking lease.
Network work is async, has a total deadline and finishes cancellation before the
lease is released. No portfolio/alert/plan writes or new background workers.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
import fcntl
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

import httpx
import pandas as pd

from aqsp.core.time import now_shanghai

import astock
import decision_context as dc
import market
import position_context_source as sectors_source
from strategy_market_source import fetch_strategy_breadth, valid_strategy_breadth


@dataclass(frozen=True)
class StrategyEvidenceLimits:
    # Operational I/O limits, not market/strategy thresholds.
    total_timeout_seconds: float = 8.0
    max_response_bytes: int = 500_000
    history_rows: int = 2
    sqlite_timeout_seconds: float = 0.1
    max_cache_bytes: int = 500_000


@dataclass(frozen=True)
class StrategyRefresh:
    state: str
    issue: str = ''

    def to_dict(self) -> dict[str, str]:
        """Serialize only safe status, never upstream response bodies."""
        return asdict(self)


def _symbols(codes: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({code for code in codes if isinstance(code, str) and len(code) == 6 and code.isascii() and code.isdigit()}))


def _sector_key(codes: Sequence[str]) -> str:
    return 'position_sectors_' + hashlib.sha256(','.join(_symbols(codes)).encode()).hexdigest()


def _cached(key: str, current: datetime, *, negative: bool = False,
            limits: StrategyEvidenceLimits | None = None) -> Any:
    limits = limits or StrategyEvidenceLimits()
    try:
        path = Path(market._cache_path(key))
        if path.is_symlink():
            return None
        with path.open('rb') as handle:
            raw = handle.read(limits.max_cache_bytes + 1)
        if len(raw) > limits.max_cache_bytes:
            return None
        value = json.loads(raw)
        if not isinstance(value, dict) or type(value.get('ts')) not in (int, float):
            return None
        age = current.timestamp() - value['ts']
        if not math.isfinite(age):
            return None
        if 0 <= age < (market._NEG_TTL if negative or not value.get('val') else market._TTL):
            return value['val']
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        pass
    return None


def _sector_fact(row: Any, current: datetime | None = None) -> dict[str, Any] | None:
    """Whitelist public facts; validate source times on every current read.

    Projection writes preserve source timestamps as received. A quote can have
    advanced during network I/O, beyond the caller's pre-request checked_at;
    only a read's actual reference time can decide whether that fact is usable.
    """
    fields = ('industry', 'name', 'membership_as_of', 'as_of', 'change_pct', 'source')
    if not isinstance(row, Mapping) or any(
            not isinstance(row.get(key), str) or not row[key].strip()
            for key in fields if key != 'change_pct'):
        return None
    if row['industry'] != row['name'] or dc._number(row.get('change_pct')) is None:
        return None
    if current is not None and any(dc.position_quote_issue({'as_of': row[key]}, checked_at=current)
                                   for key in ('membership_as_of', 'as_of')):
        return None
    return {key: row[key] for key in fields}


def load_cached_strategy_sectors(codes: Sequence[str], *, checked_at: datetime,
                                limits: StrategyEvidenceLimits | None = None) -> dict[str, dict[str, Any]]:
    """Read at most one bounded public-fact cache per symbol, without network.

    Explicit refresh projects its batch into these single-symbol entries. No
    directory discovery or tenant portfolio reads occur. Cache TTL and original
    source/membership timestamps must both remain valid; missing facts are absent.
    """
    limits = limits or StrategyEvidenceLimits()
    symbols = _symbols(codes)
    if not symbols or len(symbols) > sectors_source.PositionContextSourceConfig().batch_size:
        return {}
    result = {}
    for code in symbols:
        cached = _cached(_sector_key([code]), checked_at, limits=limits)
        fact = _sector_fact(cached.get(code), checked_at) if isinstance(cached, Mapping) else None
        if fact is not None:
            result[code] = fact
    return result


def _history(path: Path, code: str, current: datetime, limits: StrategyEvidenceLimits) -> pd.DataFrame | None:
    """Read only the last two completed raw bars from the existing tenant cache.

    Never initialize/migrate a DB or fetch missing history during a page read.
    Schema/provenance failures leave the references absent rather than inferred.
    """
    completed = dc._session(current, completed=True)
    if completed is None or not path.is_file():
        return None
    try:
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True,
                             timeout=limits.sqlite_timeout_seconds) as connection:
            frame = pd.read_sql_query(
                "SELECT date, high, low, close, source, fetched_at, timestamp_source FROM ohlcv "
                "WHERE symbol=? AND price_mode='raw' AND workload='historical' AND date<=? "
                "ORDER BY date DESC LIMIT ?", connection,
                params=(code, completed.isoformat(), limits.history_rows),
            )
        if frame.empty or any(not isinstance(value, str) or not value.strip()
                              for column in ('source', 'fetched_at', 'timestamp_source') for value in frame[column]):
            return None
        if len(set(frame['source'])) != 1:
            return None
        for value in frame['fetched_at']:
            stamp = datetime.fromisoformat(value)
            if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > current:
                return None
        frame = frame.sort_values('date').reset_index(drop=True)
        frame.attrs.update(price_mode='raw', source_name=frame['source'].iloc[-1])
        return frame
    except (OSError, ValueError, TypeError, sqlite3.Error, pd.errors.DatabaseError):
        return None


def _indices(current: datetime) -> dict[str, Any] | None:
    raw = _cached('strategy_indices', current)
    if not isinstance(raw, Mapping):
        return None
    values = []
    for code in astock.A_INDICES:
        quote = raw.get(code[2:])
        if not isinstance(quote, Mapping) or dc.position_quote_issue(quote, checked_at=current):
            continue
        change = dc._number(quote.get('change_pct'))
        stamp = quote.get('as_of') or astock.parse_gtimg_time(quote.get('quote_time'))
        if change is not None and stamp:
            values.append({'name': str(quote.get('name') or code), 'change_pct': change, 'as_of': stamp})
    if not values:
        return None
    facts = [f"{row['name']} {row['change_pct']:+.2f}%" for row in values]
    return {'status': 'unknown', 'summary': '指数背景：' + '；'.join(facts),
            'as_of': min(row['as_of'] for row in values), 'source': '腾讯指数行情',
            'evidence': facts, 'indices': values}


def load_strategy_context(holding: Mapping[str, Any], codes: Sequence[str], history_path: Path, *,
                          checked_at: datetime, limits: StrategyEvidenceLimits | None = None) -> dict[str, Any]:
    """Assemble current cached facts independently of any old risk reminder."""
    limits = limits or StrategyEvidenceLimits()
    try:
        overview = dc.load_cached_market_overview(checked_at=checked_at)
    except (OSError, ValueError, TypeError):
        overview = None
    code = str(holding['code'])
    sector = load_cached_strategy_sectors([code], checked_at=checked_at, limits=limits).get(code)
    if sector is None:
        # Keep exact-batch legacy reads until the next explicit refresh projects
        # individual symbols. Never scan another batch/tenant or renew its TTL.
        cached_sectors = _cached(_sector_key(codes), checked_at, limits=limits)
        sector = _sector_fact(cached_sectors.get(code), checked_at) if isinstance(cached_sectors, Mapping) else None
    current_holding = dict(holding)
    if isinstance(sector, Mapping) and not dc.position_quote_issue({'as_of': sector.get('membership_as_of')}, checked_at=checked_at):
        current_holding['industry'] = sector.get('industry')
    else:
        current_holding.pop('industry', None)
        current_holding.pop('sector', None)
        sector = None
    history = _history(history_path, str(holding['code']), checked_at, limits)
    context = dc.build_position_context(current_holding, [], quote=holding.get('quote'), stock_history=history,
                                        market_overview=overview, sector_snapshot=sector,
                                        checked_at=checked_at).to_dict()
    # ContextEvidence's display schema omits numeric/identity fields. Project
    # only source-validated typed facts here, never recover them from its prose.
    market_fact = context['market']
    if market_fact.get('source') and market_fact.get('as_of') and isinstance(overview, Mapping):
        sentiment = overview.get('sentiment') or {}
        counts = {key: dc._number(sentiment.get(key)) for key in ('up', 'down')} if isinstance(sentiment, Mapping) else {}
        if counts and all(value is not None and value >= 0 for value in counts.values()):
            market_fact['breadth_counts'] = counts
    if sector is not None and context['sector']['status'] != 'unknown':
        context['sector'].update(industry=sector['industry'], change_pct=dc._number(sector['change_pct']))
    indices = _indices(checked_at)
    if indices:
        if context['market']['status'] == 'unknown':
            if market_fact.get('breadth_counts') is not None:
                indices['breadth_counts'] = market_fact['breadth_counts']
            context['market'] = indices
        else:
            context['market']['indices'] = indices['indices']
            context['market']['evidence'] = [*context['market']['evidence'], *indices['evidence']]
    return context


async def _fetch_indices(client: httpx.AsyncClient, limits: StrategyEvidenceLimits) -> dict[str, Any]:
    chunks, size = [], 0
    async with client.stream('GET', 'https://qt.gtimg.cn/q=' + ','.join(astock.A_INDICES),
                             headers={'User-Agent': astock.UA}) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limits.max_response_bytes:
                raise ValueError('指数依据响应超过大小预算')
            chunks.append(chunk)
    return astock._parse_gtimg(b''.join(chunks).decode('gbk'))


async def _fetch_sources(codes: tuple[str, ...], limits: StrategyEvidenceLimits,
                         completed: dict[str, Any]) -> list[str]:
    async with httpx.AsyncClient(timeout=limits.total_timeout_seconds, trust_env=False,
                                 follow_redirects=False) as client:
        async def sector() -> None:
            completed[_sector_key(codes)] = await sectors_source.fetch_position_sectors(codes, client, max_response_bytes=limits.max_response_bytes)
        async def indices() -> None:
            completed['strategy_indices'] = await _fetch_indices(client, limits)
        async def breadth() -> None:
            completed['strategy_breadth'] = await fetch_strategy_breadth(client, max_response_bytes=limits.max_response_bytes)
        results = await asyncio.gather(sector(), indices(), breadth(), return_exceptions=True)
    return ["部分行情源未完成；已取得的依据仍按源时点核验" for result in results if isinstance(result, BaseException)]


async def refresh_strategy_sources(codes: Sequence[str], *, checked_at: datetime,
                                   limits: StrategyEvidenceLimits | None = None) -> StrategyRefresh:
    """Refresh one shared batch; total timeout cancels actual network coroutines."""
    limits = limits or StrategyEvidenceLimits()
    symbols = _symbols(codes)
    if not symbols or len(symbols) > sectors_source.PositionContextSourceConfig().batch_size:
        return StrategyRefresh('unavailable', '当前股票代码不完整或超出单批依据预算')
    key = _sector_key(symbols)
    if len(load_cached_strategy_sectors(symbols, checked_at=checked_at, limits=limits)) == len(symbols) and _indices(checked_at) and valid_strategy_breadth(_cached('strategy_breadth', checked_at), current=checked_at):
        return StrategyRefresh('cached')
    attempt_key = 'strategy_attempt_' + key
    attempt_keys = tuple('strategy_attempt_' + _sector_key([code]) for code in symbols)
    if (_cached(attempt_key, checked_at, negative=True) is not None
            or all(_cached(item, checked_at, negative=True) is not None for item in attempt_keys)):
        return StrategyRefresh('cached', '刚刚已检查来源；暂用本次可取得依据，缺失项仍待补')
    cache_dir = Path(market._MARKET_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    with (cache_dir / '.strategy-evidence.lock').open('a') as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return StrategyRefresh('busy', '已有策略依据正在刷新，当前保留可核验缓存')
        # A different request may have filled the same batch before this lease.
        if len(load_cached_strategy_sectors(symbols, checked_at=checked_at, limits=limits)) == len(symbols) and _indices(checked_at) and valid_strategy_breadth(_cached('strategy_breadth', checked_at), current=checked_at):
            return StrategyRefresh('cached')
        if (_cached(attempt_key, checked_at, negative=True) is not None
                or all(_cached(item, checked_at, negative=True) is not None for item in attempt_keys)):
            return StrategyRefresh('cached', '刚刚已检查来源；暂用本次可取得依据，缺失项仍待补')
        completed: dict[str, Any] = {}
        issue = ''
        try:
            failures = await asyncio.wait_for(_fetch_sources(symbols, limits, completed), timeout=limits.total_timeout_seconds)
            issue = failures[0] if failures else ''
        except (TimeoutError, httpx.HTTPError, OSError, ValueError, TypeError):
            issue = '行情源在本次时间预算内未完成，缺失依据保持未知'
        # wait_for has awaited cancellation; no source task survives lease exit.
        raw_sectors = completed.get(key)
        sector_facts = {code: fact for code in symbols
                        if isinstance(raw_sectors, Mapping)
                        and (fact := _sector_fact(raw_sectors.get(code))) is not None}
        if sector_facts:
            market._write_cache(key, sector_facts)
            for code, fact in sector_facts.items():
                market._write_cache(_sector_key([code]), {code: fact})
        if 'strategy_indices' in completed:
            market._write_cache('strategy_indices', completed['strategy_indices'])
        if 'strategy_breadth' in completed:
            market._write_cache('strategy_breadth', completed['strategy_breadth'])
        # A failed/partial batch cools subset retries too. Only this attempt
        # timestamp changes; failed sources never renew an old fact's TTL.
        for cache_key in {attempt_key, *attempt_keys}:
            market._write_cache(cache_key, {'checked_at': checked_at.isoformat()})
        # Cache writes occur after I/O; validate against completion time, while
        # retaining each fact's original source clock. The request's start time
        # would reject every newly written cache as being in the future.
        finished_at = now_shanghai()
        complete = (len(load_cached_strategy_sectors(symbols, checked_at=finished_at, limits=limits)) == len(symbols)
                    and _indices(finished_at) is not None
                    and valid_strategy_breadth(_cached('strategy_breadth', finished_at), current=finished_at))
        return StrategyRefresh('partial' if issue or not complete else 'updated',
                               issue or ('部分来源未返回有效数据，缺失依据保持未知' if not complete else ''))
