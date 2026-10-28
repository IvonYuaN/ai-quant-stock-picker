"""Public Eastmoney industry dataapi contract shared by display and analysis.

No network, cache or trading decisions. Industry hierarchy counts must never be
summed into all-A breadth. Source timestamps are passed through, never generated.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class IndustrySourceLimits:
    # Optional explanation I/O budgets, not trading thresholds.
    max_industry_rows: int = 800
    max_response_bytes: int = 500_000
    timeout_seconds: int = 4


# Same public page contract as aqsp.data.concept_board (#333), industry subset.
INDUSTRY_URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
INDUSTRY_PARAMS = {"key": "f3,f104,f105,f124", "code": "m:90+t:2"}
INDUSTRY_REFERER = "https://data.eastmoney.com/bkzj/hy.html"
INDUSTRY_SOURCE = "东方财富行业行情 dataapi"


def parse_industry_rows(payload: Any, *, max_rows: int) -> list[dict]:
    """Validate a complete response and convert f3 hundredths to percentage.

    Invalid percentages remain unavailable instead of being turned into zero.
    Matching uses exact unique names: different industry hierarchy levels may
    have identical economic figures and must retain their separate identities.
    """
    data = payload.get('data') if isinstance(payload, Mapping) else None
    total = data.get('total') if isinstance(data, Mapping) else None
    rows = data.get('diff') if isinstance(data, Mapping) else None
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if (type(total) is not int or not 0 < total <= max_rows or not isinstance(rows, list)
            or len(rows) != total or any(not isinstance(row, dict) for row in rows)):
        raise ValueError('行业依据总量不完整或超过预算')
    names = [row.get('f14') for row in rows]
    if any(not isinstance(name, str) or not name.strip() for name in names) or len(set(names)) != len(names):
        raise ValueError('行业名称重复或无效')
    codes = [row['f12'] for row in rows if isinstance(row.get('f12'), str) and row['f12']]
    if len(set(codes)) != len(codes):
        raise ValueError('行业代码重复')
    result = []
    for row in rows:
        change = None
        raw = row.get('f3')
        if type(raw) in (int, float):
            try:
                value = float(raw)
                if math.isfinite(value):
                    # Source contract, not a strategy threshold: f3=891 is 8.91%.
                    change = value / 100.0
            except (OverflowError, ValueError):
                pass
        result.append({**row, 'f3': change})
    return result
