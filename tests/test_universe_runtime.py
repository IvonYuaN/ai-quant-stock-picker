from __future__ import annotations

from datetime import date

import pytest

from aqsp.core.errors import DataError
from aqsp.universe.runtime import resolve_run_symbols


def test_resolve_run_symbols_prefers_explicit_symbols() -> None:
    def fail_source(_name: str) -> object:
        raise AssertionError("source should not be built")

    assert resolve_run_symbols(
        "auto",
        "600519, 300750",
        get_source_fn=fail_source,
        default_symbols=("000001",),
        max_universe=0,
        min_avg_amount=0,
    ) == ["600519", "300750"]


def test_resolve_run_symbols_uses_liquid_universe_before_available() -> None:
    class Source:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            assert limit == 2
            assert min_amount == 50.0
            return ["600000", "000001"]

        def get_available_symbols(self) -> list[str]:
            return ["300750"]

    assert resolve_run_symbols(
        "auto",
        "",
        get_source_fn=lambda _name: Source(),
        default_symbols=("600519",),
        max_universe=2,
        min_avg_amount=50.0,
    ) == ["600000", "000001"]


def test_resolve_run_symbols_falls_back_to_available_when_liquid_errors() -> None:
    class Source:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            raise DataError("liquid unavailable")

        def get_available_symbols(self) -> list[str]:
            return ["600000", "000001", "300750"]

    assert resolve_run_symbols(
        "auto",
        "",
        get_source_fn=lambda _name: Source(),
        default_symbols=("600519",),
        max_universe=2,
        min_avg_amount=50.0,
    ) == ["600000", "000001"]


def test_resolve_run_symbols_falls_back_to_defaults_when_source_fails() -> None:
    def fail_source(_name: str) -> object:
        raise DataError("source missing")

    assert resolve_run_symbols(
        "auto",
        "",
        get_source_fn=fail_source,
        default_symbols=("600519", "300750", "000001"),
        max_universe=2,
        min_avg_amount=50.0,
    ) == ["600519", "300750"]


def test_resolve_run_symbols_uses_named_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class Pool:
        def get_symbols(self, *, as_of: date) -> list[str]:
            seen["as_of"] = as_of
            return ["000001", "600519"]

    class UniversePool:
        @staticmethod
        def from_default(name: str) -> Pool:
            seen["pool"] = name
            return Pool()

    monkeypatch.setattr("aqsp.universe.pool.UniversePool", UniversePool)

    assert resolve_run_symbols(
        "auto",
        "",
        get_source_fn=lambda _name: object(),
        default_symbols=("600519",),
        pool_name="zz500",
        as_of=date(2026, 6, 1),
        max_universe=0,
        min_avg_amount=0,
    ) == ["000001", "600519"]
    assert seen == {"pool": "zz500", "as_of": date(2026, 6, 1)}
