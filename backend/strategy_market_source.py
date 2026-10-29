"""Bounded all-A breadth with its own source clock, never an emotion-pool date."""
from __future__ import annotations

from dataclasses import dataclass, field
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


BREADTH_SOURCE = '东方财富全A逐只行情宽度'


@dataclass
class BreadthAccumulator:
    """Shared pure source contract for the synchronous and async producers."""
    limits: BreadthSourceLimits = field(default_factory=BreadthSourceLimits)
    total: int | None = None
    seen: set[tuple[int, str]] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=lambda: {'up': 0, 'down': 0, 'flat': 0})
    stamps: list[str] = field(default_factory=list)

    def add_page(self, payload: Any) -> dict[str, Any] | None:
        """Return a complete fact, or None while additional pages are needed.

        Missing, short, duplicated and inconsistent pages raise ValueError.
        """
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, dict) or type(data.get('total')) is not int:
            raise ValueError('市场宽度缺少源总量')
        reported = data['total']
        if reported <= 0 or reported > self.limits.page_size * self.limits.max_pages or (self.total is not None and self.total != reported):
            raise ValueError('市场宽度总量不完整或在分页间变化')
        self.total = reported
        rows = data.get('diff')
        rows = list(rows.values()) if isinstance(rows, dict) else rows
        expected = min(self.limits.page_size, self.total - len(self.seen))
        if not isinstance(rows, list) or len(rows) != expected:
            raise ValueError('市场宽度分页不完整')
        for row in rows:
            if not isinstance(row, dict) or type(row.get('f13')) is not int or row['f13'] not in (0, 1):
                raise ValueError('市场宽度缺少源身份')
            code = row.get('f12')
            if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
                raise ValueError('市场宽度股票代码无效')
            key = (row['f13'], code)
            if key in self.seen:
                raise ValueError('市场宽度出现重复股票')
            self.seen.add(key)
            change = dc._number(row.get('f3'))
            if change is None:
                if row.get('f3') not in (None, '-'):  # suspension is absent, not flat
                    raise ValueError('市场宽度涨幅字段无效')
                continue
            stamp = astock.epoch_iso(row.get('f124'))
            if not stamp:
                raise ValueError('市场宽度缺少原始行情时点')
            self.stamps.append(stamp)
            self.counts['up' if change > 0 else 'down' if change < 0 else 'flat'] += 1
        if len(self.seen) == self.total:
            if not self.stamps or self.counts['up'] + self.counts['down'] + self.counts['flat'] <= 0:
                raise ValueError('市场宽度没有有效报价')
            return {**self.counts, 'total': self.total, 'as_of': min(self.stamps), 'latest_source_at': max(self.stamps),
                    'coverage_complete': True, 'source': BREADTH_SOURCE, 'breadth': market.breadth_label(self.counts['up'], self.counts['down'])}
        return None


async def fetch_strategy_breadth(client: httpx.AsyncClient, *, max_response_bytes: int,
                                 limits: BreadthSourceLimits | None = None) -> dict[str, Any]:
    """Read bounded stable-symbol pages; caller owns the cancellable deadline."""
    accumulator = BreadthAccumulator(limits or BreadthSourceLimits())
    received_bytes = 0
    for page in range(1, accumulator.limits.max_pages + 1):
        params = {'pn': page, 'pz': accumulator.limits.page_size, 'po': 0, 'np': 1, 'fltt': 2,
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
        result = accumulator.add_page(json.loads(b''.join(chunks)))
        if result is not None:
            return result
    raise ValueError('市场宽度超过分页预算')


def valid_strategy_breadth(value: Any, *, current: datetime) -> bool:
    """Use the same source/session age checks as strategy quotes on every read."""
    if not isinstance(value, dict) or value.get('source') != BREADTH_SOURCE or value.get('coverage_complete') is not True:
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
