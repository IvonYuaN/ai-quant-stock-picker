"""Pure exact source-time recognition for model prose, never a freshness gate.

Full timestamps may omit offset/seconds but retain the source's own wall clock.
Offsets must match the source offset; no timezone conversion is inferred. A bare
clock is accepted only when that clock resolves to one source calendar date.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import re

_SOURCE = re.compile(r'\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2}))?')
_TIME = re.compile(r'\d{4}-\d{1,2}-\d{1,2}(?:[T ]\d{1,2}:\d{1,2}(?::\d{1,2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?|\d{1,2}:\d{1,2}(?::\d{1,2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?')
_UNITS = re.compile(r'\s*[元块角股手%％分家点倍]')


def trusted_source_stamps(values: Iterable[object]) -> frozenset[str]:
    """Keep canonical dates or aware ISO source timestamps only."""
    result = set()
    for value in values:
        if not isinstance(value, str) or not _SOURCE.fullmatch(value):
            continue
        try:
            datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            continue
        result.add(value)
    return frozenset(result)


def without_source_times(text: str, stamps: Iterable[str]) -> str:
    """Remove entire verified time tokens; reject unknown/ambiguous times first.

    Validation is independent of allowed prices so coincidental numeric values
    cannot authorize unknown dates, suffixes or date-as-price expressions.
    """
    allowed: set[str] = set()
    clocks: dict[str, set[str]] = {}
    for stamp in trusted_source_stamps(stamps):
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        day = parsed.date().isoformat()
        allowed.add(day)
        if parsed.tzinfo is None:
            continue
        allowed.update((stamp, parsed.isoformat()))
        offset = parsed.strftime('%z')
        offset = offset[:3] + ':' + offset[3:]
        for separator in ('T', ' '):
            for pattern in ('%H:%M', '%H:%M:%S'):
                wall = parsed.strftime(f'%Y-%m-%d{separator}{pattern}')
                allowed.update((wall, wall + offset))
                if stamp.endswith('Z'):
                    allowed.add(wall + 'Z')
        for pattern in ('%H:%M', '%H:%M:%S'):
            clocks.setdefault(parsed.strftime(pattern), set()).add(day)
    allowed.update(clock for clock, dates in clocks.items() if len(dates) == 1)

    def replace(match: re.Match[str]) -> str:
        token = match[0]
        before, after = text[:match.start()], text[match.end():]
        if (token not in allowed or (before and re.search(r'[\dA-Za-z:._+\-]$', before))
                or (after and re.match(r'[\dA-Za-z:._+\-]', after)) or _UNITS.match(after)
                or re.match(r'\s+(?:UTC|GMT|CST|Z|[+-]\d)', after)):
            raise ValueError('模型日期或时间无唯一有源依据，或被用作数字单位')
        return ''

    return _TIME.sub(replace, text)
