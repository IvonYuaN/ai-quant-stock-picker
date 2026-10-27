"""Bounded all-A breadth with its own source clock, never an emotion-pool date."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

import httpx

import astock
import decision_context as dc
import market


@dataclass(frozen=True)
class BreadthSourceLimits:
    # Reuse the existing all-A universe/page budget; no new trading thresholds.
    page_size: int = astock._BREADTH_PAGE_SIZE
    max_pages: int = astock._BREADTH_MAX_PAGES


async def fetch_strategy_breadth(client: httpx.AsyncClient, *, max_response_bytes: int,
                                 limits: BreadthSourceLimits | None = None) -> dict[str, Any]:
    """Count each symbol once, rejecting incomplete or unstable pagination.

    Pages sort by symbol instead of live percentage rank, which moves between
    requests. No retries, threads, emotion/flow fetches or local-clock fallback.
    The caller supplies the enclosing cancellable total deadline.
    """
    limits = limits or BreadthSourceLimits()
    total: int | None = None
    seen: set[tuple[int, str]] = set()
    counts = {'up': 0, 'down': 0, 'flat': 0}
    stamps: list[str] = []
    received_bytes = 0
    for page in range(1, limits.max_pages + 1):
        params = {'pn': page, 'pz': limits.page_size, 'po': 0, 'np': 1, 'fltt': 2,
                  'invt': 2, 'fid': 'f12', 'fs': astock._BREADTH_FS, 'fields': 'f12,f13,f3,f124'}
        chunks = []
        async with client.stream('GET', 'https://push2.eastmoney.com/api/qt/clist/get',
                                 params=params, headers={'User-Agent': astock.UA}) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                received_bytes += len(chunk)
                if received_bytes > max_response_bytes:
                    raise ValueError('市场宽度响应超过总大小预算')
                chunks.append(chunk)
        payload = json.loads(b''.join(chunks))
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, dict) or type(data.get('total')) is not int:
            raise ValueError('市场宽度缺少源总量')
        reported = data['total']
        if reported <= 0 or reported > limits.page_size * limits.max_pages or (total is not None and total != reported):
            raise ValueError('市场宽度总量不完整或在分页间变化')
        total = reported
        rows = data.get('diff')
        rows = list(rows.values()) if isinstance(rows, dict) else rows
        expected = min(limits.page_size, total - len(seen))
        if not isinstance(rows, list) or len(rows) != expected:
            raise ValueError('市场宽度分页不完整')
        for row in rows:
            if not isinstance(row, dict) or type(row.get('f13')) is not int or row['f13'] not in (0, 1):
                raise ValueError('市场宽度缺少源身份')
            code = row.get('f12')
            if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
                raise ValueError('市场宽度股票代码无效')
            key = (row['f13'], code)
            if key in seen:
                raise ValueError('市场宽度出现重复股票')
            seen.add(key)
            change = dc._number(row.get('f3'))
            if change is None:
                if row.get('f3') not in (None, '-'):  # suspension is absent, not flat
                    raise ValueError('市场宽度涨幅字段无效')
                continue
            stamp = astock.epoch_iso(row.get('f124'))
            if not stamp:
                raise ValueError('市场宽度缺少原始行情时点')
            stamps.append(stamp)
            counts['up' if change > 0 else 'down' if change < 0 else 'flat'] += 1
        if len(seen) == total:
            if not stamps or counts['up'] + counts['down'] + counts['flat'] <= 0:
                raise ValueError('市场宽度没有有效报价')
            return {**counts, 'total': total, 'as_of': min(stamps), 'latest_source_at': max(stamps),
                    'source': '东方财富全A逐只行情宽度', 'breadth': market.breadth_label(counts['up'], counts['down'])}
    raise ValueError('市场宽度超过分页预算')


def valid_strategy_breadth(value: Any, *, current: datetime) -> bool:
    """Use the same source/session age checks as strategy quotes on every read."""
    if not isinstance(value, dict) or value.get('source') != '东方财富全A逐只行情宽度':
        return False
    if any(type(value.get(key)) is not int or value[key] < 0 for key in ('up', 'down', 'flat', 'total')):
        return False
    active = value['up'] + value['down'] + value['flat']
    if active <= 0 or active > value['total']:
        return False
    if value.get('breadth') != market.breadth_label(value['up'], value['down']):
        return False
    first, last = value.get('as_of'), value.get('latest_source_at')
    if any(dc.position_quote_issue({'as_of': stamp}, checked_at=current) for stamp in (first, last)):
        return False
    return datetime.fromisoformat(first) <= datetime.fromisoformat(last)
