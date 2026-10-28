"""Pure identities of source-validated direction facts, excluding quote noise."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any


def direction_identity(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Compare premises, never precise prices, counts or refresh timestamps.

    Callers supply already source/time-validated evidence. Zero separates index
    gain/loss/flat as a sign identity; this is not an entry/exit threshold.
    """
    result: dict[str, Any] = {}
    for key in ('market', 'sector', 'stock'):
        fact = evidence.get(key)
        fact = fact if isinstance(fact, Mapping) else {}
        result[key] = {field: fact.get(field) for field in ('status', 'source', 'industry')}
        result[key]['status'] = fact.get('status') or 'unknown'
        if key == 'market':
            result[key]['breadth'] = fact.get('breadth')
            indices = fact.get('indices')
            rows = []
            for item in indices if isinstance(indices, list) else []:
                if not isinstance(item, Mapping):
                    continue
                pct = item.get('change_pct')
                valid = type(pct) in (int, float) and math.isfinite(pct)
                rows.append({'name': item.get('name'), 'source': item.get('source'),
                             'direction': ('up' if pct > 0 else 'down' if pct < 0 else 'flat') if valid else 'unknown'})
            result[key]['indices'] = sorted(rows, key=lambda row: (str(row['name']), str(row['source']), row['direction']))
    return result
