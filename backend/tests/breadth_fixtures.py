"""Synthetic complete breadth evidence, independent of limit-pool dates."""
from datetime import datetime


def complete_breadth(as_of: datetime | str, *, up: int = 800, down: int = 3000,
                     flat: int = 0) -> dict:
    import market

    stamp = as_of.isoformat() if isinstance(as_of, datetime) else as_of
    return {"up": up, "down": down, "flat": flat, "total": up + down + flat,
            "coverage_complete": True, "as_of": stamp, "latest_source_at": stamp,
            "source": "东方财富全A逐只行情宽度", "breadth": market.breadth_label(up, down)}
