"""Normalize quote timestamp metadata without changing quote economics."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from aqsp.core.time import SHANGHAI_TZ

LIVE_SHORT_MAX_FUTURE_SECONDS = 0


def parse_vendor_timestamp(value: Any) -> str:
    """Return a timezone-aware vendor timestamp, or empty when unverifiable."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return ""
    if raw.isdigit():
        numeric = int(raw)
        if numeric > 10**12:
            numeric //= 1000
        if numeric > 10**9:
            return datetime.fromtimestamp(numeric, tz=SHANGHAI_TZ).isoformat()
    normalized = raw.replace("/", "-").replace("Z", "+00:00")
    for parser in (
        lambda: datetime.fromisoformat(normalized),
        lambda: datetime.strptime(raw, "%Y-%m-%d %H:%M:%S"),
        lambda: datetime.strptime(raw, "%Y-%m-%d %H:%M"),
    ):
        try:
            parsed = parser()
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
        return parsed.astimezone(SHANGHAI_TZ).isoformat()
    return ""


def parse_legacy_quote_timestamp(
    parts: Iterable[Any],
    *,
    date_index: int = 30,
    time_index: int = 31,
) -> str:
    """Parse the date/time fields used by Sina/Tencent legacy quote payloads."""
    values = list(parts)
    if len(values) <= max(date_index, time_index):
        return ""
    date_value = str(values[date_index] or "").strip()
    time_value = str(values[time_index] or "").strip()
    if not date_value or not time_value:
        return ""
    return parse_vendor_timestamp(f"{date_value} {time_value}")


def quote_timestamp_metadata(vendor_ts: str, received_at: str) -> dict[str, str]:
    """Build explicit provenance fields; received time is never vendor time."""
    normalized_vendor = parse_vendor_timestamp(vendor_ts)
    source = "vendor" if normalized_vendor else "received_at"
    return {
        "ts": normalized_vendor or received_at,
        "vendor_ts": normalized_vendor,
        "received_at": received_at,
        "timestamp_source": source,
    }
