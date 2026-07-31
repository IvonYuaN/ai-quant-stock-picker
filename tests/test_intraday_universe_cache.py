from __future__ import annotations

import json
from datetime import date, datetime

from aqsp.core.time import SHANGHAI_TZ
from aqsp.universe.intraday_universe_cache import (
    load_intraday_universe_cache,
    write_intraday_universe_cache,
)


def _supported_symbols() -> list[str]:
    return [
        *(f"{index:06d}" for index in range(1000)),
        *(f"{300000 + index:06d}" for index in range(1000)),
        *(f"{600000 + index:06d}" for index in range(1000)),
    ]


def test_intraday_universe_cache_loads_previous_trading_day_when_current_day_unavailable(
    tmp_path,
) -> None:
    path = tmp_path / "universe.json"
    symbols = _supported_symbols()
    write_intraday_universe_cache(
        path,
        symbols=symbols,
        source="online_first",
        trade_date=date(2026, 7, 30),
        resolved_at=datetime(2026, 7, 30, 14, 50, tzinfo=SHANGHAI_TZ),
    )

    cached = load_intraday_universe_cache(
        path,
        trade_date=date(2026, 7, 31),
        source="online_first",
        now=datetime(2026, 7, 31, 10, 0, tzinfo=SHANGHAI_TZ),
    )

    assert cached is not None
    assert cached.trade_date == "2026-07-30"
    assert len(cached.symbols) == 3000


def test_intraday_universe_cache_rejects_bad_hash_or_old_trade_date(tmp_path) -> None:
    path = tmp_path / "universe.json"
    symbols = _supported_symbols()
    write_intraday_universe_cache(
        path,
        symbols=symbols,
        source="online_first",
        trade_date=date(2026, 7, 29),
        resolved_at=datetime(2026, 7, 29, 14, 50, tzinfo=SHANGHAI_TZ),
    )
    assert (
        load_intraday_universe_cache(
            path,
            trade_date=date(2026, 7, 31),
            source="online_first",
            now=datetime(2026, 7, 31, 10, 0, tzinfo=SHANGHAI_TZ),
        )
        is None
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["trade_date"] = "2026-07-30"
    payload["universe_hash"] = "sha256:invalid"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert (
        load_intraday_universe_cache(
            path,
            trade_date=date(2026, 7, 31),
            source="online_first",
            now=datetime(2026, 7, 31, 10, 0, tzinfo=SHANGHAI_TZ),
        )
        is None
    )
