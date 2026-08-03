from __future__ import annotations

import contextlib
import importlib.util
import json
import sqlite3
from argparse import Namespace
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import run_variant_suite as variant_suite

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
    assert mod.SQL_CHUNK_SIZE == 80


def test_validate_market_db_rejects_empty_or_incompatible_database(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty.db"
    empty.touch()
    with pytest.raises(ValueError, match="不可用或为空"):
        mod.validate_market_db(empty)

    incomplete = tmp_path / "incomplete.db"
    with sqlite3.connect(incomplete) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT, name TEXT)")
    with pytest.raises(ValueError, match="daily_qfq"):
        mod.validate_market_db(incomplete)


def test_validate_market_db_accepts_required_readable_tables(tmp_path: Path) -> None:
    market_db = tmp_path / "market.db"
    with sqlite3.connect(market_db) as conn:
        conn.execute("CREATE TABLE stocks (ts_code TEXT, name TEXT)")
        conn.execute("CREATE TABLE daily_qfq (ts_code TEXT, trade_date TEXT)")

    mod.validate_market_db(market_db)


def test_write_variant_refresh_status_is_bounded_and_timestamped(
    tmp_path: Path,
) -> None:
    path = tmp_path / "variant_refresh_status.json"

    mod.write_variant_refresh_status(
        path,
        status="staged",
        message="x" * 600,
        profiles_staged=32,
        profiles_total=128,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "variant-refresh-status-v1"
    assert payload["status"] == "staged"
    assert len(payload["message"]) == 500
    assert payload["generated_at"].endswith("+08:00")


def test_copy_market_rows_rejects_non_positive_sql_chunk_size(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sql_chunk_size"):
        mod.copy_market_rows(
            source_db=tmp_path / "source.db",
            target_db=tmp_path / "target.db",
            symbols=(),
            start="2026-07-01",
            end="2026-07-02",
            sql_chunk_size=0,
        )


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
    monkeypatch.setattr(mod, "validate_market_db", lambda _path: None)
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
    assert writes == [output.with_name("variant_refresh_status.json")]
    assert commits == []
    assert not output.exists()
    assert not cursor.exists()


def test_refresh_skips_lock_conflict_without_writing_or_advancing_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "variant_results.json"
    cursor = tmp_path / "variant.cursor.json"
    monkeypatch.setattr(
        mod,
        "parse_args",
        lambda: Namespace(
            market_db=tmp_path / "market.db",
            output=output,
            temp_db=None,
            start=None,
            end=None,
            lookback_calendar_days=180,
            max_symbols=160,
            max_fills_per_variant=24,
            max_runtime_seconds=300,
            lock_file=tmp_path / "variant.lock",
            cursor_file=cursor,
            lock_wait_seconds=0.0,
        ),
    )

    @contextlib.contextmanager
    def locked(*_args: object, **_kwargs: object):
        raise mod.VariantRefreshLocked("already running")
        yield

    monkeypatch.setattr(mod, "refresh_lock", locked)

    assert mod.main() == 0
    assert not output.exists()
    assert not cursor.exists()


@pytest.mark.parametrize(
    ("failure", "expected_code", "expected_text"),
    [
        (mod.VariantRefreshTimeout("budget exhausted"), 124, "timeout"),
        (RuntimeError("runner failed"), 1, "failed"),
    ],
)
def test_refresh_failure_preserves_last_qualified_artifact_and_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    expected_code: int,
    expected_text: str,
) -> None:
    output = tmp_path / "variant_results.json"
    cursor = tmp_path / "variant.cursor.json"
    original = '{"last":"qualified"}\n'
    output.write_text(original, encoding="utf-8")
    cursor.write_text('{"next_offset": 80}\n', encoding="utf-8")
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
            lock_file=tmp_path / "variant.lock",
            cursor_file=cursor,
            lock_wait_seconds=0.0,
            sql_chunk_size=80,
        ),
    )
    monkeypatch.setattr(mod, "validate_variant_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mod, "validate_market_db", lambda _path: None)
    symbols = (mod.MarketSymbol("000001.SZ", "000001", "样本", "深市主板"),)
    batch = mod.VariantUniverseBatch(
        symbols=symbols,
        universe_version="test",
        universe_count=121,
        offset=0,
        cycle_id=1,
    )
    monkeypatch.setattr(mod, "load_supported_symbols", lambda _path: symbols)
    monkeypatch.setattr(mod, "select_variant_batch", lambda *_args: batch)
    monkeypatch.setattr(mod, "copy_market_rows", lambda **_kwargs: ("000001",))
    monkeypatch.setattr(mod, "load_frames", lambda *_args: {})
    monkeypatch.setattr(
        mod,
        "generate_variant_profiles",
        lambda _frames: (
            variant_suite.VariantProfile(
                "test", "测试", 10, 1.0, 2.0, "trend", 2, 0.5, "测试"
            ),
        ),
    )
    monkeypatch.setattr(
        mod, "run_suite", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure)
    )

    assert mod.main() == expected_code
    assert output.read_text(encoding="utf-8") == original
    assert cursor.read_text(encoding="utf-8") == '{"next_offset": 80}\n'
    message = capsys.readouterr().out
    assert expected_text in message
    assert "preserved qualified artifact" in message


def test_refresh_stages_profile_chunks_before_publishing_first_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "variant_results.json"
    cursor = tmp_path / "variant.cursor.json"
    stage = tmp_path / "variant.stage.json"
    profiles = tuple(
        mod.VariantProfile(
            f"profile-{index}",
            f"策略 {index}",
            10,
            1.0,
            2.0,
            "trend",
            2,
            0.5,
            "测试",
        )
        for index in range(4)
    )
    symbols = tuple(
        mod.MarketSymbol(f"0000{index:02d}.SZ", f"0000{index:02d}", "样本", "深市主板")
        for index in range(121)
    )
    batch = mod.VariantUniverseBatch(symbols, "test", 121, 0, 1)
    calls: list[tuple[str, ...]] = []
    commits: list[Path] = []

    def args() -> Namespace:
        return Namespace(
            market_db=tmp_path / "market.db",
            output=output,
            temp_db=tmp_path / "input.db",
            start="2026-01-01",
            end="2026-07-27",
            lookback_calendar_days=180,
            max_symbols=121,
            max_fills_per_variant=24,
            max_runtime_seconds=0,
            lock_file=tmp_path / "variant.lock",
            cursor_file=cursor,
            staging_file=stage,
            profile_batch_size=2,
            lock_wait_seconds=0.0,
            sql_chunk_size=80,
        )

    def fake_run_suite(*values: object, **_kwargs: object) -> dict[str, object]:
        selected = values[4]
        assert isinstance(selected, tuple)
        calls.append(tuple(profile.variant_id for profile in selected))
        return {
            "schema_version": "variant-suite-v2",
            "end_date": "2026-07-27",
            "start_date": "2026-01-01",
            "symbols": [item.symbol for item in symbols],
            "initial_cash": 100_000.0,
            "optimization": {},
            "variants": [
                {
                    "variant_id": profile.variant_id,
                    "strategy_signature": profile.variant_id,
                    "holdings_signature": profile.variant_id,
                    "final_equity": float(index),
                }
                for index, profile in enumerate(selected)
            ],
        }

    def validate(payload: dict[str, object], **_kwargs: object) -> None:
        if len(payload["variants"]) < 4:
            raise ValueError("variant count too small")

    monkeypatch.setattr(mod, "parse_args", args)
    monkeypatch.setattr(mod, "validate_market_db", lambda _path: None)
    monkeypatch.setattr(mod, "load_supported_symbols", lambda _path: symbols)
    monkeypatch.setattr(mod, "select_variant_batch", lambda *_args: batch)
    monkeypatch.setattr(
        mod,
        "copy_market_rows",
        lambda **_kwargs: tuple(item.symbol for item in symbols),
    )
    monkeypatch.setattr(mod, "load_frames", lambda *_args: {})
    monkeypatch.setattr(mod, "generate_variant_profiles", lambda _frames: profiles)
    monkeypatch.setattr(mod, "run_suite", fake_run_suite)
    monkeypatch.setattr(mod, "validate_variant_payload", validate)
    monkeypatch.setattr(
        mod,
        "diversity_ranked_variants",
        lambda values: [
            dict(item, rank=index + 1) for index, item in enumerate(values)
        ],
    )
    monkeypatch.setattr(
        mod, "commit_variant_batch", lambda path, _batch: commits.append(path)
    )

    assert mod.main() == 0
    assert not output.exists()
    assert not cursor.exists()
    assert json.loads(stage.read_text(encoding="utf-8"))["completed_variant_ids"] == [
        "profile-0",
        "profile-1",
    ]

    assert mod.main() == 0
    assert calls == [("profile-0", "profile-1"), ("profile-2", "profile-3")]
    assert {
        item["variant_id"]
        for item in json.loads(output.read_text(encoding="utf-8"))["variants"]
    } == {
        "profile-0",
        "profile-1",
        "profile-2",
        "profile-3",
    }
    assert commits == [cursor]
