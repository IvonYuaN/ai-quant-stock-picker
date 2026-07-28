from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reclaim_sqlite_cache.py"
spec = importlib.util.spec_from_file_location("reclaim_sqlite_cache", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def _cache(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE ohlcv (symbol TEXT, date TEXT, price_mode TEXT, "
            "workload TEXT, PRIMARY KEY (symbol, date, price_mode, workload))"
        )
        conn.execute("CREATE INDEX idx_ohlcv_symbol_date ON ohlcv(symbol, date)")
        conn.execute(
            "CREATE UNIQUE INDEX idx_ohlcv_symbol_date_price_mode_workload "
            "ON ohlcv(symbol, date, price_mode, workload)"
        )


def test_inspect_cache_reports_known_redundant_indexes(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    _cache(path)

    report = mod.inspect_cache(path)

    assert report.removable_indexes == (
        "idx_ohlcv_symbol_date",
        "idx_ohlcv_symbol_date_price_mode_workload",
    )


def test_reclaim_cache_drops_redundant_indexes_without_vacuum(tmp_path: Path) -> None:
    path = tmp_path / "cache.db"
    _cache(path)

    report = mod.reclaim_cache(path, vacuum=False, free_space_multiplier=2)

    assert report.removable_indexes == ()
    with sqlite3.connect(path) as conn:
        names = {row[1] for row in conn.execute("PRAGMA index_list(ohlcv)")}
    assert "idx_ohlcv_symbol_date" not in names


def test_reclaim_cache_rejects_vacuum_without_enough_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cache.db"
    _cache(path)
    monkeypatch.setattr(mod.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))

    with pytest.raises(ValueError, match="insufficient free space"):
        mod.reclaim_cache(path, vacuum=True, free_space_multiplier=2)
