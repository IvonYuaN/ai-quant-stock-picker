from __future__ import annotations

from datetime import date

import pytest

from aqsp.core.errors import DataError
from scripts.data.backfill_adjustment_factors import backfill_adjustment_factors


class _RecordingService:
    def __init__(self, failing_symbols: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.failing_symbols = failing_symbols or set()

    def fetch_and_cache_factors(self, symbol: str, start: date, end: date) -> None:
        assert start == date(2026, 5, 1)
        assert end == date(2026, 5, 30)
        self.calls.append(symbol)
        if symbol in self.failing_symbols:
            raise DataError("provider unavailable")


def test_backfill_adjustment_factors_resumes_bounded_batch_and_reports_failures():
    service = _RecordingService(failing_symbols={"000003"})

    summary = backfill_adjustment_factors(
        ["000001", "000002", "000003", "000002"],
        start=date(2026, 5, 1),
        end=date(2026, 5, 30),
        service=service,
        offset=1,
        limit=2,
        delay_seconds=0,
    )

    assert service.calls == ["000002", "000003"]
    assert summary.selected == 2
    assert summary.succeeded == 1
    assert summary.failed_symbols == ("000003",)


@pytest.mark.parametrize(
    ("offset", "limit", "delay_seconds"),
    [(-1, 1, 0), (0, 0, 0), (0, 1, -0.1)],
)
def test_backfill_adjustment_factors_rejects_invalid_batch_options(
    offset: int, limit: int, delay_seconds: float
) -> None:
    with pytest.raises(ValueError):
        backfill_adjustment_factors(
            ["000001"],
            start=date(2026, 5, 1),
            end=date(2026, 5, 30),
            service=_RecordingService(),
            offset=offset,
            limit=limit,
            delay_seconds=delay_seconds,
        )
