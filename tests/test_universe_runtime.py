from __future__ import annotations

import json
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


def test_resolve_run_symbols_filters_explicit_symbols_by_sqlite_coverage() -> None:
    seen: dict[str, object] = {}

    class Source:
        def get_symbols_with_daily_coverage(
            self,
            symbols: list[str],
            start: date,
            end: date,
            *,
            min_rows: int | None = None,
        ) -> list[str]:
            seen["symbols"] = symbols
            seen["end"] = end
            seen["min_rows"] = min_rows
            return ["600519"]

    assert resolve_run_symbols(
        "sqlite_db",
        "600519, 301999",
        get_source_fn=lambda _name: Source(),
        default_symbols=("000001",),
        as_of=date(2026, 7, 6),
        max_universe=0,
        min_avg_amount=0,
    ) == ["600519"]
    assert seen == {
        "symbols": ["600519", "301999"],
        "end": date(2026, 7, 6),
        "min_rows": None,
    }


def test_resolve_run_symbols_raises_when_sqlite_explicit_coverage_is_empty() -> None:
    class Source:
        def get_symbols_with_daily_coverage(
            self,
            symbols: list[str],
            start: date,
            end: date,
            *,
            min_rows: int | None = None,
        ) -> list[str]:
            return []

    with pytest.raises(DataError, match="覆盖过滤后无可用标的"):
        resolve_run_symbols(
            "sqlite_db",
            "301999",
            get_source_fn=lambda _name: Source(),
            default_symbols=("000001",),
            as_of=date(2026, 7, 6),
            max_universe=0,
            min_avg_amount=0,
        )


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


def test_resolve_run_symbols_marks_live_discovery_as_live_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workload_calls: list[str | None] = []
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")

    class Source:
        def set_workload(self, workload: str | None) -> None:
            workload_calls.append(workload)

        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            return ["600000"]

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=lambda _name: Source(),
        default_symbols=("000001",),
        max_universe=0,
        min_avg_amount=50.0,
    ) == ["600000"]
    assert workload_calls == ["live_short", None]


def test_resolve_run_symbols_marks_full_live_pool_as_live_short_when_amount_filter_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workload_calls: list[str | None] = []

    class Source:
        def set_workload(self, workload: str | None) -> None:
            workload_calls.append(workload)

        def get_available_symbols(self) -> list[str]:
            return ["600000"]

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv("AQSP_INTRADAY_FULL_UNIVERSE", "true")
    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=lambda _name: Source(),
        default_symbols=("000001",),
        max_universe=0,
        min_avg_amount=0.0,
    ) == ["600000"]
    assert workload_calls == ["live_short", None]


def test_resolve_run_symbols_filters_auto_universe_by_daily_coverage() -> None:
    seen: dict[str, object] = {}

    class Source:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            return []

        def get_available_symbols(self) -> list[str]:
            return ["600000", "000001", "300750"]

        def get_symbols_with_daily_coverage(
            self,
            symbols: list[str],
            start: date,
            end: date,
            *,
            min_rows: int | None = None,
        ) -> list[str]:
            seen["symbols"] = symbols
            seen["end"] = end
            seen["min_rows"] = min_rows
            return ["600000", "300750"]

    assert resolve_run_symbols(
        "sqlite_db",
        "",
        get_source_fn=lambda _name: Source(),
        default_symbols=("600519",),
        as_of=date(2026, 7, 7),
        max_universe=0,
        min_avg_amount=50.0,
    ) == ["600000", "300750"]
    assert seen == {
        "symbols": ["600000", "000001", "300750"],
        "end": date(2026, 7, 7),
        "min_rows": None,
    }


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


def test_resolve_run_symbols_online_first_skips_available_when_liquid_errors() -> None:
    class LiveSource:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            raise DataError("liquid unavailable")

        def get_available_symbols(self) -> list[str]:
            return ["000001", "000002", "600519"]

    class SqliteSource:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            return ["600519", "300750"]

    def get_source(name: str) -> object:
        if name == "online_first":
            return LiveSource()
        if name == "sqlite_db":
            return SqliteSource()
        raise DataError(name)

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=get_source,
        default_symbols=("000001",),
        max_universe=2,
        min_avg_amount=50.0,
    ) == ["600519", "300750"]


def test_resolve_run_symbols_falls_back_to_defaults_when_source_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AQSP_RUNTIME_SYMBOL_CACHE", "/missing/symbols.json")

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


def test_resolve_run_symbols_falls_back_to_sqlite_pool_when_live_discovery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AQSP_RUNTIME_SYMBOL_CACHE", "/missing/symbols.json")

    class LiveSource:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            raise DataError("live snapshot unavailable")

        def get_available_symbols(self) -> list[str]:
            raise DataError("live available unavailable")

    class SqliteSource:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            assert limit == 2
            assert min_amount == 50.0
            return ["600000", "000001", "300750"]

    def get_source(name: str) -> object:
        if name == "online_first":
            return LiveSource()
        if name == "sqlite_db":
            return SqliteSource()
        raise DataError(name)

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=get_source,
        default_symbols=("600519",),
        max_universe=2,
        min_avg_amount=50.0,
    ) == ["600000", "000001"]


def test_resolve_run_symbols_uses_cached_pool_before_slow_sqlite_for_non_liquid_source(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LiveSource:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            raise DataError("live snapshot unavailable")

        def get_available_symbols(self) -> list[str]:
            raise DataError("live available unavailable")

    cache_path = tmp_path / "symbols.json"
    cache_path.write_text(
        json.dumps({"covered_symbols": ["600000", "000001", "BAD", "300750"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUNTIME_SYMBOL_CACHE", str(cache_path))

    def get_source(name: str) -> object:
        if name == "auto":
            return LiveSource()
        raise AssertionError("slow sqlite fallback should not be opened")

    assert resolve_run_symbols(
        "auto",
        "",
        get_source_fn=get_source,
        default_symbols=("600519",),
        max_universe=2,
        min_avg_amount=50.0,
    ) == ["600000", "000001"]


def test_resolve_run_symbols_intraday_uses_live_liquidity_before_fast_cache(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LiveSource:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            assert limit == 2
            assert min_amount == 50.0
            return ["600519", "300750"]

    cache_path = tmp_path / "intraday-symbols.json"
    cache_path.write_text(
        json.dumps({"covered_symbols": ["688981", "002025", "BAD", "000938"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CSVS", "")
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CACHE", str(cache_path))

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=lambda _name: LiveSource(),
        default_symbols=("600519",),
        max_universe=2,
        min_avg_amount=50.0,
    ) == ["600519", "300750"]


def test_resolve_run_symbols_intraday_full_universe_uses_all_live_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LiveSource:
        def get_available_symbols(self) -> list[str]:
            return ["600000", "000001", "300750"]

        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            raise AssertionError("full live rotation must not use liquid head")

    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv("AQSP_INTRADAY_FULL_UNIVERSE", "true")

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=lambda _name: LiveSource(),
        default_symbols=("600519",),
        max_universe=0,
        min_avg_amount=50_000_000,
    ) == ["600000", "000001", "300750"]


def test_resolve_run_symbols_intraday_full_universe_rejects_partial_cache(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "symbols.json"
    cache_path.write_text(
        json.dumps({"covered_symbols": ["600000", "000001"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv("AQSP_INTRADAY_FULL_UNIVERSE", "true")
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CACHE", str(cache_path))
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CSVS", "")

    class UnavailableLiveSource:
        def get_available_symbols(self) -> list[str]:
            raise DataError("live snapshot unavailable")

        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            raise AssertionError("full live rotation must not use liquid fallback")

    with pytest.raises(DataError, match="实时全池解析失败"):
        resolve_run_symbols(
            "online_first",
            "",
            get_source_fn=lambda _name: UnavailableLiveSource(),
            default_symbols=("600519",),
            max_universe=0,
            min_avg_amount=0.0,
        )


def test_resolve_run_symbols_intraday_falls_back_to_fast_cache_when_live_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path = tmp_path / "intraday-symbols.json"
    cache_path.write_text(
        json.dumps({"covered_symbols": ["688981", "002025", "BAD", "000938"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CSVS", "")
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CACHE", str(cache_path))

    def fail_source(_name: str) -> object:
        raise DataError("live unavailable")

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=fail_source,
        default_symbols=("600519",),
        max_universe=2,
        min_avg_amount=50.0,
    ) == ["688981", "002025"]


def test_resolve_run_symbols_intraday_uses_candidate_csv_without_cache_fill_by_default(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = tmp_path / "intraday_latest.csv"
    csv_path.write_text(
        "symbol,name,score,rating\n"
        "000017,深中华A,88.8,strong_buy_candidate\n"
        "000021,深科技,30.8,avoid\n"
        "688981,中芯国际,48.9,watch\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "symbols.json"
    cache_path.write_text(
        json.dumps({"covered_symbols": ["000001", "000002", "000004"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CSVS", str(csv_path))
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CACHE", str(cache_path))

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=lambda _name: object(),
        default_symbols=("600519",),
        max_universe=4,
        min_avg_amount=50.0,
    ) == ["000017", "688981", "000021"]


def test_resolve_run_symbols_intraday_can_fill_candidate_csv_from_cache(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = tmp_path / "latest.csv"
    csv_path.write_text(
        "symbol,name,score,rating\n688981,中芯国际,48.9,watch\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "symbols.json"
    cache_path.write_text(
        json.dumps({"covered_symbols": ["000001", "000002", "000004"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CSVS", str(csv_path))
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CACHE", str(cache_path))
    monkeypatch.setenv("AQSP_INTRADAY_FAST_FILL_CACHE", "true")

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=lambda _name: object(),
        default_symbols=("600519",),
        max_universe=3,
        min_avg_amount=50.0,
    ) == ["688981", "000001", "000002"]


def test_resolve_run_symbols_intraday_does_not_let_small_csv_cap_global_scan(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = tmp_path / "latest.csv"
    csv_path.write_text(
        "symbol,name,score,rating\n688981,中芯国际,48.9,watch\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "symbols.json"
    cache_path.write_text(
        json.dumps({"covered_symbols": ["688981", "000001", "000002", "000004"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CSVS", str(csv_path))
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CACHE", str(cache_path))
    monkeypatch.setenv("AQSP_INTRADAY_FAST_FILL_CACHE", "true")

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=lambda _name: object(),
        default_symbols=("600519",),
        max_universe=4,
        min_avg_amount=50.0,
    ) == ["688981", "000001", "000002", "000004"]


def test_resolve_run_symbols_intraday_respects_candidate_csv_priority(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intraday_csv = tmp_path / "intraday_latest.csv"
    latest_csv = tmp_path / "latest.csv"
    intraday_csv.write_text(
        "symbol,name,score,rating\n"
        "000017,深中华A,88.8,strong_buy_candidate\n"
        "000002,万科A,44.8,watch\n",
        encoding="utf-8",
    )
    latest_csv.write_text(
        "symbol,name,score,rating\n"
        "600900,长江电力,51.8,watch\n"
        "688981,中芯国际,48.9,watch\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv(
        "AQSP_INTRADAY_FAST_SYMBOL_CSVS",
        f"{latest_csv},{intraday_csv}",
    )
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CACHE", "")

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=lambda _name: object(),
        default_symbols=("600519",),
        max_universe=4,
        min_avg_amount=50.0,
    ) == ["600900", "688981", "000017", "000002"]


def test_resolve_run_symbols_intraday_fast_cache_can_be_disabled(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LiveSource:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            assert limit == 2
            assert min_amount == 50.0
            return ["600519", "300750"]

    cache_path = tmp_path / "intraday-symbols.json"
    cache_path.write_text(
        json.dumps({"covered_symbols": ["688981", "002025"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUN_TASK_ID", "intraday")
    monkeypatch.setenv("AQSP_INTRADAY_FAST_SYMBOL_CACHE", str(cache_path))
    monkeypatch.setenv("AQSP_INTRADAY_DISABLE_FAST_SYMBOL_CACHE", "true")

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=lambda _name: LiveSource(),
        default_symbols=("000001",),
        max_universe=2,
        min_avg_amount=50.0,
    ) == ["600519", "300750"]


def test_resolve_run_symbols_online_first_skips_cached_pool_for_liquid_universe(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LiveSource:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            raise DataError("live snapshot unavailable")

        def get_available_symbols(self) -> list[str]:
            return ["000001", "000002"]

    class SqliteSource:
        def get_liquid_symbols(self, *, limit: int, min_amount: float) -> list[str]:
            return ["600519", "300750"]

    cache_path = tmp_path / "symbols.json"
    cache_path.write_text(
        json.dumps({"covered_symbols": ["000001", "000002", "600519"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUNTIME_SYMBOL_CACHE", str(cache_path))

    def get_source(name: str) -> object:
        if name == "online_first":
            return LiveSource()
        if name == "sqlite_db":
            return SqliteSource()
        raise DataError(name)

    assert resolve_run_symbols(
        "online_first",
        "",
        get_source_fn=get_source,
        default_symbols=("000001",),
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
