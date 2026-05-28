from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeConfig:
    symbols: tuple[str, ...]
    mode: str
    limit: int
    min_avg_amount: float
    max_data_lag_days: int


def load_runtime_config() -> RuntimeConfig:
    symbols = tuple(
        item.strip()
        for item in os.getenv("AQSP_SYMBOLS", "").split(",")
        if item.strip()
    )
    return RuntimeConfig(
        symbols=symbols,
        mode=os.getenv("AQSP_MODE", "close").strip() or "close",
        limit=int(os.getenv("AQSP_LIMIT", "10")),
        min_avg_amount=float(os.getenv("AQSP_MIN_AVG_AMOUNT", "50000000")),
        max_data_lag_days=int(os.getenv("AQSP_MAX_DATA_LAG_DAYS", "3")),
    )
