from __future__ import annotations

import importlib.util
from argparse import Namespace
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts" / "refresh_variant_results_from_market_db.py"
spec = importlib.util.spec_from_file_location(
    "refresh_variant_results_from_market_db", SCRIPT
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_market_symbol_filter_keeps_main_and_chinext_when_non_st() -> None:
    assert mod.is_supported_symbol("000001.SZ", "平安银行")
    assert mod.is_supported_symbol("300750.SZ", "宁德时代")
    assert mod.is_supported_symbol("600519.SH", "贵州茅台")
    assert mod.is_supported_symbol("605499.SH", "东鹏饮料")


def test_market_symbol_filter_drops_st_delisted_star_board_and_b_share() -> None:
    assert not mod.is_supported_symbol("688001.SH", "华兴源创")
    assert not mod.is_supported_symbol("000005.SZ", "ST星源")
    assert not mod.is_supported_symbol("000004.SZ", "国华退")
    assert not mod.is_supported_symbol("200001.SZ", "深物业B")


def test_balanced_symbols_interleaves_boards_when_capped() -> None:
    symbols = (
        mod.MarketSymbol("000001.SZ", "000001", "A", "深市主板"),
        mod.MarketSymbol("000002.SZ", "000002", "B", "深市主板"),
        mod.MarketSymbol("300001.SZ", "300001", "C", "创业板"),
        mod.MarketSymbol("300002.SZ", "300002", "D", "创业板"),
        mod.MarketSymbol("600001.SH", "600001", "E", "沪市主板"),
        mod.MarketSymbol("600002.SH", "600002", "F", "沪市主板"),
    )
    picked = mod.balanced_symbols(symbols, 3)
    assert [item.group for item in picked] == ["深市主板", "创业板", "沪市主板"]


def test_variant_batch_rotates_balanced_universe_only_after_commit(
    tmp_path: Path,
) -> None:
    symbols = (
        mod.MarketSymbol("000001.SZ", "000001", "A", "深市主板"),
        mod.MarketSymbol("000002.SZ", "000002", "B", "深市主板"),
        mod.MarketSymbol("300001.SZ", "300001", "C", "创业板"),
        mod.MarketSymbol("300002.SZ", "300002", "D", "创业板"),
        mod.MarketSymbol("600001.SH", "600001", "E", "沪市主板"),
        mod.MarketSymbol("600002.SH", "600002", "F", "沪市主板"),
    )
    cursor = tmp_path / "variant.cursor.json"

    first = mod.select_variant_batch(symbols, 3, cursor)
    retry = mod.select_variant_batch(symbols, 3, cursor)

    assert [item.group for item in first.symbols] == ["深市主板", "创业板", "沪市主板"]
    assert retry.symbols == first.symbols
    mod.commit_variant_batch(cursor, first)

    second = mod.select_variant_batch(symbols, 3, cursor)

    assert {item.symbol for item in first.symbols}.isdisjoint(
        item.symbol for item in second.symbols
    )
    assert [item.group for item in second.symbols] == ["深市主板", "创业板", "沪市主板"]


def test_refresh_defaults_keep_production_refresh_bounded() -> None:
    assert mod.DEFAULT_MAX_SYMBOLS == 300
    assert mod.DEFAULT_LOOKBACK_CALENDAR_DAYS == 180
    assert mod.DEFAULT_MAX_RUNTIME_SECONDS == 600
    assert mod.DEFAULT_LOCK_WAIT_SECONDS == 0.0


def test_runtime_budget_raises_timeout_when_work_exceeds_limit() -> None:
    with pytest.raises(mod.VariantRefreshTimeout):
        with mod.runtime_budget(1):
            time.sleep(2)


def test_refresh_lock_records_pid_and_releases_file(tmp_path: Path) -> None:
    lock = tmp_path / "variant.lock"

    with mod.refresh_lock(lock, 0.0):
        assert f"pid={mod.os.getpid()}" in lock.read_text(encoding="utf-8")

    assert lock.read_text(encoding="utf-8") == ""


def test_refresh_lock_rejects_second_process_when_wait_is_zero(tmp_path: Path) -> None:
    lock = tmp_path / "variant.lock"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,pathlib,sys,time;"
                "p=pathlib.Path(sys.argv[1]);"
                "p.parent.mkdir(parents=True,exist_ok=True);"
                "h=p.open('a+',encoding='utf-8');"
                "fcntl.flock(h.fileno(),fcntl.LOCK_EX);"
                "h.seek(0);h.truncate();h.write('child_locked\\n');h.flush();"
                "time.sleep(5)"
            ),
            str(lock),
        ]
    )
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if lock.exists() and "child_locked" in lock.read_text(encoding="utf-8"):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("child did not acquire lock")

        with pytest.raises(RuntimeError, match="already running"):
            with mod.refresh_lock(lock, 0.0):
                raise AssertionError("lock should not be acquired")
    finally:
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)


def test_refresh_rejects_invalid_payload_before_write_or_cursor_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "variant_results.json"
    cursor = tmp_path / "variant.cursor.json"
    lock = tmp_path / "variant.lock"
    symbols = tuple(
        mod.MarketSymbol(f"{index:06d}.SZ", f"{index:06d}", str(index), "深市主板")
        for index in range(121)
    )
    batch = mod.VariantUniverseBatch(
        symbols=symbols,
        universe_version="test",
        universe_count=len(symbols),
        offset=0,
        cycle_id=0,
    )
    monkeypatch.setattr(
        mod,
        "parse_args",
        lambda: Namespace(
            market_db=tmp_path / "market.db",
            output=output,
            temp_db=tmp_path / "input.db",
            start="2026-01-01",
            end="2026-07-27",
            lookback_calendar_days=180,
            max_symbols=300,
            max_fills_per_variant=24,
            max_runtime_seconds=0,
            lock_file=lock,
            cursor_file=cursor,
            lock_wait_seconds=0.0,
        ),
    )
    monkeypatch.setattr(mod, "load_supported_symbols", lambda _path: symbols)
    monkeypatch.setattr(mod, "select_variant_batch", lambda *_args: batch)
    monkeypatch.setattr(
        mod,
        "copy_market_rows",
        lambda **_kwargs: tuple(item.symbol for item in symbols),
    )
    monkeypatch.setattr(mod, "run_suite", lambda *_args: {"variants": []})
    monkeypatch.setattr(mod, "compact_variant_fills", lambda *_args: None)
    monkeypatch.setattr(
        mod,
        "validate_variant_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("technical evidence missing")
        ),
    )
    writes: list[Path] = []
    commits: list[Path] = []
    monkeypatch.setattr(
        mod, "atomic_write_text", lambda path, _text: writes.append(path)
    )
    monkeypatch.setattr(
        mod, "commit_variant_batch", lambda path, _batch: commits.append(path)
    )

    assert mod.main() == 1
    assert writes == []
    assert commits == []
    assert not output.exists()
    assert not cursor.exists()
