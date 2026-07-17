from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

from scripts.run_production_walkforward_gate import (
    CoverageSummary,
    annotate_production_gate_metadata,
    build_walkforward_command,
    diagnostic_report_path,
    formal_report_backup_path,
    inspect_raw_coverage,
    preserve_formal_report_snapshot,
    repair_production_gate_metadata,
    repair_stale_running_status,
    select_covered_symbols,
    warn_if_report_path_not_writable,
    write_minimal_pbo_diagnostics,
)


def test_terminate_process_group_kills_only_verified_group(monkeypatch) -> None:
    import scripts.run_production_walkforward_gate as gate

    class _Process:
        pid = 4321

        def __init__(self) -> None:
            self.wait_calls: list[float] = []

        def wait(self, timeout: float) -> None:
            self.wait_calls.append(timeout)
            if len(self.wait_calls) == 1:
                raise subprocess.TimeoutExpired(cmd=["child"], timeout=timeout)

        def terminate(self) -> None:
            raise AssertionError("verified process groups must use killpg")

        def kill(self) -> None:
            raise AssertionError("verified process groups must use killpg")

    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(gate.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        gate.os,
        "killpg",
        lambda pgid, sig: signals.append((pgid, sig)),
    )

    process = _Process()
    gate._terminate_process_group(process, terminate_timeout=1, kill_timeout=2)

    assert signals == [(4321, signal.SIGTERM), (4321, signal.SIGKILL)]
    assert process.wait_calls == [1, 2]


def test_terminate_process_group_falls_back_to_child_when_group_is_not_owned(
    monkeypatch,
) -> None:
    import scripts.run_production_walkforward_gate as gate

    class _Process:
        pid = 4321

        def __init__(self) -> None:
            self.terminated = False
            self.killed = False
            self.wait_calls = 0

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float) -> None:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired(cmd=["child"], timeout=timeout)

    monkeypatch.setattr(gate.os, "getpgid", lambda _pid: (_ for _ in ()).throw(OSError))
    process = _Process()
    gate._terminate_process_group(process, terminate_timeout=1, kill_timeout=2)

    assert process.terminated is True
    assert process.killed is True
    assert process.wait_calls == 2


def test_main_blocks_child_walkforward_on_low_memory_host(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import scripts.run_production_walkforward_gate as gate

    status_path = tmp_path / "walkforward_production_status.json"
    monkeypatch.setattr(gate, "_total_memory_gib", lambda: 1.6)
    monkeypatch.delenv("AQSP_ALLOW_LOW_MEMORY_WALKFORWARD", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--status-path",
            str(status_path),
            "--min-memory-gib",
            "4",
            "--no-streaming",
        ],
    )

    assert gate.main() == 2
    output = capsys.readouterr().out
    payload = json.loads(status_path.read_text(encoding="utf-8"))

    assert "server memory 1.6GiB < required 4.0GiB" in output
    assert "bounded streaming workflow was disabled" in output
    assert payload["status"] == "blocked_resources"
    assert "bounded streaming workflow was disabled" in payload["detail"]


def test_low_memory_guard_blocks_when_memory_detection_is_unavailable(
    monkeypatch,
) -> None:
    import scripts.run_production_walkforward_gate as gate

    monkeypatch.setattr(gate, "_total_memory_gib", lambda: None)

    detail = gate._low_memory_blocker(4.0)

    assert "could not be detected" in detail
    assert "fail-closed" in detail


def test_total_memory_uses_sysconf_when_proc_meminfo_is_missing(monkeypatch) -> None:
    import scripts.run_production_walkforward_gate as gate

    class _MissingProcPath:
        def __init__(self, _value: str) -> None:
            pass

        def exists(self) -> bool:
            return False

    monkeypatch.setattr(gate, "Path", _MissingProcPath)
    monkeypatch.setattr(
        gate.os,
        "sysconf",
        lambda key: {"SC_PHYS_PAGES": 1024, "SC_PAGE_SIZE": 1024**2}[key],
    )

    assert gate._total_memory_gib() == 1.0


def test_main_reports_missing_db_before_coverage_cache(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import scripts.run_production_walkforward_gate as gate

    status_path = tmp_path / "walkforward_production_status.json"
    missing_db = tmp_path / "missing_raw.db"
    monkeypatch.setattr(gate, "_total_memory_gib", lambda: 8.0)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--dry-run",
            "--db",
            str(missing_db),
            "--status-path",
            str(status_path),
        ],
    )

    assert gate.main() == 2
    output = capsys.readouterr().out
    payload = json.loads(status_path.read_text(encoding="utf-8"))

    assert f"raw sqlite db missing: {missing_db}" in output
    assert payload["status"] == "blocked_db"


def _make_raw_db(path: Path, symbols: int = 3) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE stocks(ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """
            CREATE TABLE daily_qfq(
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                close_qfq REAL,
                volume INTEGER,
                amount REAL
            )
            """
        )
        for idx in range(symbols):
            market = "SH" if idx % 2 == 0 else "SZ"
            code = f"600{idx:03d}.{market}"
            conn.execute("INSERT INTO stocks(ts_code, name) VALUES(?, ?)", (code, code))
            for day in range(1, 31):
                conn.execute(
                    """
                    INSERT INTO daily_qfq(
                        ts_code, trade_date, open, high, low, close, close_qfq, volume, amount
                    ) VALUES(?, ?, 10, 11, 9, 10, 10, 1000000, 10000000)
                    """,
                    (code, f"202401{day:02d}"),
                )
        conn.commit()


def test_production_cutoff_guard_rejects_sidecar_beyond_raw_database(
    tmp_path: Path,
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    _make_raw_db(db, symbols=1)
    gate_path = tmp_path / "walkforward_gate.json"
    gate_path.write_text(
        json.dumps({"data_end": "2024-02-01"}),
        encoding="utf-8",
    )

    detail = gate.validate_production_cutoff_consistency(
        db_path=db,
        requested_end="2024-02-01",
        gate_path=gate_path,
    )

    assert "exceeds raw sqlite MAX(trade_date)=2024-01-30" in detail


def test_cached_symbols_require_the_configured_cache_path(tmp_path: Path) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    cache_path = tmp_path / "symbols-cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "cache_path": str(tmp_path / "pytest-other-cache.json"),
                "db_path": str(db),
                "db_mtime_epoch": int(db.stat().st_mtime),
                "start": "2024-01-01",
                "end": "2024-01-30",
                "min_symbols": 1,
                "coverage_mode": "auto_recent_window",
                "lookback_years": 3,
                "summary": {
                    "stock_symbols": 1,
                    "covered_symbols": 1,
                    "rows": 1,
                    "first_trade_date": "20240101",
                    "last_trade_date": "20240130",
                },
                "covered_symbols": ["600000"],
            }
        ),
        encoding="utf-8",
    )

    assert (
        gate.load_cached_coverage_symbols(
            cache_path,
            db_path=db,
            start="2024-01-01",
            end="2024-01-30",
            min_symbols=1,
            coverage_mode="auto_recent_window",
            lookback_years=3,
        )
        is None
    )


def test_inspect_raw_coverage_counts_covered_symbols(tmp_path: Path) -> None:
    db = tmp_path / "astocks_raw.db"
    _make_raw_db(db, symbols=4)

    coverage = inspect_raw_coverage(db, start="2024-01-01", end="2024-01-30")

    assert coverage.stock_symbols == 4
    assert coverage.covered_symbols == 4
    assert coverage.rows == 120
    assert coverage.first_trade_date == "20240101"
    assert coverage.last_trade_date == "20240130"


def test_inspect_raw_coverage_accepts_raw_columns_even_if_filename_contains_qfq(
    tmp_path: Path,
) -> None:
    db = tmp_path / "astocks_qfq.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE stocks(ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """
            CREATE TABLE daily_qfq(
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                open_qfq REAL,
                high_qfq REAL,
                low_qfq REAL,
                close_qfq REAL,
                volume INTEGER,
                amount REAL
            )
            """
        )
        for idx in range(4):
            market = "SH" if idx % 2 == 0 else "SZ"
            code = f"600{idx:03d}.{market}"
            conn.execute("INSERT INTO stocks(ts_code, name) VALUES(?, ?)", (code, code))
            for day in range(1, 31):
                conn.execute(
                    """
                    INSERT INTO daily_qfq(
                        ts_code, trade_date, open, high, low, close,
                        open_qfq, high_qfq, low_qfq, close_qfq, volume, amount
                    ) VALUES(?, ?, 10, 11, 9, 10, 9, 10, 8, 9, 1000000, 10000000)
                    """,
                    (code, f"202401{day:02d}"),
                )
        conn.commit()

    coverage = inspect_raw_coverage(db, start="2024-01-01", end="2024-01-30")

    assert coverage.stock_symbols == 4
    assert coverage.covered_symbols == 4


def test_select_covered_symbols_returns_full_eligible_market(tmp_path: Path) -> None:
    db = tmp_path / "astocks_raw.db"
    _make_raw_db(db, symbols=5)

    assert select_covered_symbols(db, start="2024-01-01", end="2024-01-30") == [
        "600000",
        "600001",
        "600002",
        "600003",
        "600004",
    ]


def test_inspect_raw_coverage_listing_aware_recent_window_keeps_recent_ipo(
    tmp_path: Path,
) -> None:
    from scripts.run_production_walkforward_gate import (
        inspect_raw_coverage_window_with_symbols,
    )

    db = tmp_path / "astocks_raw.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE stocks(ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """
            CREATE TABLE daily_qfq(
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                close_qfq REAL,
                volume INTEGER,
                amount REAL
            )
            """
        )
        dates = ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109"]
        for code in ("600000.SH", "301000.SZ"):
            conn.execute("INSERT INTO stocks(ts_code, name) VALUES(?, ?)", (code, code))
        for trade_date in dates:
            conn.execute(
                """
                INSERT INTO daily_qfq(
                    ts_code, trade_date, open, high, low, close, close_qfq, volume, amount
                ) VALUES('600000.SH', ?, 10, 11, 9, 10, 10, 1000000, 10000000)
                """,
                (trade_date,),
            )
        for trade_date in dates[3:]:
            conn.execute(
                """
                INSERT INTO daily_qfq(
                    ts_code, trade_date, open, high, low, close, close_qfq, volume, amount
                ) VALUES('301000.SZ', ?, 20, 21, 19, 20, 20, 1000000, 10000000)
                """,
                (trade_date,),
            )
        conn.commit()

    inspection = inspect_raw_coverage_window_with_symbols(
        db,
        requested_start="2018-01-01",
        requested_end="2024-01-09",
        coverage_start="2024-01-01",
        coverage_end="2024-01-09",
        coverage_mode="auto_recent_window",
        lookback_years=1,
        listing_aware=True,
    )

    assert inspection.covered_symbols == ["301000", "600000"]
    assert inspection.summary.coverage_mode == "auto_recent_window"
    assert inspection.summary.coverage_window_start == "2024-01-01"
    assert inspection.summary.coverage_window_end == "2024-01-09"
    assert inspection.summary.listing_aware is True


def test_inspect_raw_coverage_legacy_full_span_excludes_recent_ipo(
    tmp_path: Path,
) -> None:
    from scripts.run_production_walkforward_gate import (
        inspect_raw_coverage_window_with_symbols,
    )

    db = tmp_path / "astocks_raw.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE stocks(ts_code TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            """
            CREATE TABLE daily_qfq(
                ts_code TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                close_qfq REAL,
                volume INTEGER,
                amount REAL
            )
            """
        )
        dates = ["20240102", "20240103", "20240104", "20240105", "20240108", "20240109"]
        for code in ("600000.SH", "301000.SZ"):
            conn.execute("INSERT INTO stocks(ts_code, name) VALUES(?, ?)", (code, code))
        for trade_date in dates:
            conn.execute(
                """
                INSERT INTO daily_qfq(
                    ts_code, trade_date, open, high, low, close, close_qfq, volume, amount
                ) VALUES('600000.SH', ?, 10, 11, 9, 10, 10, 1000000, 10000000)
                """,
                (trade_date,),
            )
        for trade_date in dates[3:]:
            conn.execute(
                """
                INSERT INTO daily_qfq(
                    ts_code, trade_date, open, high, low, close, close_qfq, volume, amount
                ) VALUES('301000.SZ', ?, 20, 21, 19, 20, 20, 1000000, 10000000)
                """,
                (trade_date,),
            )
        conn.commit()

    inspection = inspect_raw_coverage_window_with_symbols(
        db,
        requested_start="2024-01-01",
        requested_end="2024-01-09",
        coverage_start="2024-01-01",
        coverage_end="2024-01-09",
        coverage_mode="legacy_full_span",
        lookback_years=1,
        listing_aware=False,
    )

    assert inspection.covered_symbols == ["600000"]
    assert inspection.summary.coverage_mode == "legacy_full_span"
    assert inspection.summary.listing_aware is False


def test_build_walkforward_command_uses_full_market_raw_gate() -> None:
    class Args:
        start = "2018-01-01"
        end = "2024-12-31"
        grid_profile = "stable"
        report = "reports/prod.md"
        gate_path = "data/prod_gate.json"
        cache_path = "data/prod.db"
        log = "logs/prod.log"
        symbols_file = "/tmp/symbols.txt"

    command = build_walkforward_command(Args())

    assert "walkforward" in command
    assert "--pool" in command
    assert "all" in command
    assert "--grid-cscv" in command
    assert "--grid-profile" in command
    assert "stable" in command
    assert "--streaming" in command
    assert "--engine" in command
    assert "builtin" in command
    assert "--skip-pit-financials" in command
    assert "--gate-path" in command
    assert "data/prod_gate.json" in command
    assert "--symbols-file" in command
    assert "/tmp/symbols.txt" in command


def test_production_walkforward_gate_sets_prefiltered_symbols_env(
    monkeypatch, tmp_path: Path
) -> None:
    from scripts import run_production_walkforward_gate as gate_mod

    db = tmp_path / "astocks_raw.db"
    _make_raw_db(db, symbols=5)
    status_path = tmp_path / "status.json"
    report_path = tmp_path / "report.md"
    gate_path = tmp_path / "gate.json"
    log_path = tmp_path / "prod.log"
    cache_path = tmp_path / "cache.db"

    seen: dict[str, str] = {}

    def fake_execute_child_walkforward(
        *,
        command,
        env,
        cwd,
        timeout_seconds,
        status_path,
        args,
        coverage,
        effective_symbols,
    ):
        seen["prefiltered"] = env.get("AQSP_SQLITE_PREFILTERED_SYMBOLS", "")
        seen["db"] = env.get("AQSP_SQLITE_DB_PATH", "")
        gate_path.write_text(
            json.dumps(
                {
                    "run_date": "2026-06-27",
                    "deflated_sharpe": 1.1,
                    "pbo": 0.2,
                    "pbo_valid": True,
                    "dsr_pass": True,
                    "pbo_pass": True,
                    "both_pass": True,
                    "n_periods": 10,
                    "effective_symbols": 5,
                }
            ),
            encoding="utf-8",
        )
        return 0, 43210

    monkeypatch.setattr(
        gate_mod,
        "_execute_child_walkforward",
        fake_execute_child_walkforward,
    )

    code = gate_mod.main.__wrapped__ if hasattr(gate_mod.main, "__wrapped__") else None
    assert code is None

    argv = [
        "scripts/run_production_walkforward_gate.py",
        "--db",
        str(db),
        "--start",
        "2024-01-01",
        "--end",
        "2024-01-30",
        "--min-symbols",
        "5",
        "--report",
        str(report_path),
        "--gate-path",
        str(gate_path),
        "--log",
        str(log_path),
        "--cache-path",
        str(cache_path),
        "--status-path",
        str(status_path),
        "--timeout-seconds",
        "30",
    ]
    monkeypatch.setattr("sys.argv", argv)

    assert gate_mod.main() == 0
    assert seen["prefiltered"] == "1"
    assert seen["db"] == str(db)


def test_effective_timeout_seconds_raises_short_full_market_timeout() -> None:
    import scripts.run_production_walkforward_gate as gate

    assert (
        gate._effective_timeout_seconds(
            1500,
            effective_symbols=5209,
            min_production_symbols=3000,
        )
        == 10418
    )


def test_effective_timeout_seconds_keeps_small_smoke_timeout() -> None:
    import scripts.run_production_walkforward_gate as gate

    assert (
        gate._effective_timeout_seconds(
            1,
            effective_symbols=5,
            min_production_symbols=3000,
        )
        == 1
    )


def test_annotate_production_gate_metadata_preserves_gate_result(
    tmp_path: Path,
) -> None:
    gate_path = tmp_path / "walkforward_gate.json"
    gate_path.write_text(
        json.dumps(
            {
                "run_date": "2026-06-21",
                "both_pass": False,
                "deflated_sharpe": 0.8275,
                "pbo": 0.7778,
                "effective_symbols": 3200,
            }
        ),
        encoding="utf-8",
    )

    annotate_production_gate_metadata(
        gate_path=gate_path,
        db_path=tmp_path / "astocks_raw.db",
        coverage=CoverageSummary(
            stock_symbols=5533,
            covered_symbols=3200,
            rows=123456,
            first_trade_date="20180102",
            last_trade_date="20241231",
            coverage_mode="auto_recent_window",
            coverage_window_start="2023-01-01",
            coverage_window_end="2024-12-31",
            lookback_years=2,
            listing_aware=True,
            expected_trade_days=490,
        ),
        effective_symbols=3200,
    )

    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    assert payload["both_pass"] is False
    assert payload["deflated_sharpe"] == 0.8275
    assert payload["pbo"] == 0.7778
    assert payload["coverage_mode"] == "auto_recent_window"
    assert payload["coverage_window"] == {
        "start": "2023-01-01",
        "end": "2024-12-31",
        "lookback_years": 2,
        "listing_aware": True,
        "expected_trade_days": 490,
    }
    assert payload["source"] == "sqlite_db"
    assert payload["price_mode"] == "raw"
    assert payload["effective_symbols"] == 3200
    assert payload["production_gate_coverage"] == {
        "stock_symbols": 5533,
        "covered_symbols": 3200,
        "selected_symbols": 3200,
        "rows": 123456,
        "first_trade_date": "20180102",
        "last_trade_date": "20241231",
        "coverage_mode": "auto_recent_window",
        "coverage_window_start": "2023-01-01",
        "coverage_window_end": "2024-12-31",
        "lookback_years": 2,
        "listing_aware": True,
        "expected_trade_days": 490,
    }


def test_annotate_production_gate_metadata_rejects_child_universe_mismatch(
    tmp_path: Path,
) -> None:
    gate_path = tmp_path / "walkforward_gate.json"
    gate_path.write_text(
        json.dumps(
            {
                "run_date": "2026-06-21",
                "both_pass": False,
                "deflated_sharpe": 0.8275,
                "pbo": 0.7778,
                "effective_symbols": 300,
            }
        ),
        encoding="utf-8",
    )

    try:
        annotate_production_gate_metadata(
            gate_path=gate_path,
            db_path=tmp_path / "astocks_raw.db",
            coverage=CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3200,
                rows=123456,
                first_trade_date="20180102",
                last_trade_date="20241231",
                coverage_mode="legacy_full_span",
            ),
            effective_symbols=3200,
        )
    except SystemExit as exc:
        assert "require child <= wrapper" in str(exc)
    else:
        raise AssertionError("expected production metadata mismatch to block")

    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    assert payload["effective_symbols"] == 300
    assert "production_gate_coverage" not in payload


def test_write_minimal_pbo_diagnostics_for_failed_gate(
    tmp_path: Path,
) -> None:
    gate_path = tmp_path / "walkforward_gate.json"
    report_path = tmp_path / "walkforward-grid-raw-production-latest.md"
    gate_path.write_text(
        json.dumps(
            {
                "run_date": "2026-06-21",
                "both_pass": False,
                "deflated_sharpe": 0.0,
                "pbo": 0.0,
                "pbo_pass": False,
                "effective_symbols": 3200,
                "price_mode": "raw",
                "sqlite_db_path": "/opt/market-data/astocks_raw.db",
            }
        ),
        encoding="utf-8",
    )

    written = write_minimal_pbo_diagnostics(
        gate_path=gate_path,
        report_path=report_path,
        coverage=CoverageSummary(
            stock_symbols=5533,
            covered_symbols=3200,
            rows=123456,
            first_trade_date="20180102",
            last_trade_date="20241231",
        ),
    )

    assert written is True
    text = report_path.read_text(encoding="utf-8")
    assert "### PBO 失败定位" in text
    assert "CSCV 失败组合占比" in text
    assert "最差对齐周期" in text
    assert "训练选中变体" in text
    assert "**标的数量**: 3200" in text
    assert "effective_symbols | 3200" in text
    assert "coverage_mode | -" not in text


def test_write_minimal_pbo_diagnostics_uses_gate_grid_diagnostics(
    tmp_path: Path,
) -> None:
    gate_path = tmp_path / "walkforward_gate.json"
    report_path = tmp_path / "walkforward-grid-raw-production-latest.md"
    gate_path.write_text(
        json.dumps(
            {
                "run_date": "2026-06-21",
                "both_pass": False,
                "deflated_sharpe": 0.8275,
                "pbo": 0.7778,
                "pbo_pass": False,
                "effective_symbols": 3281,
                "price_mode": "raw",
                "sqlite_db_path": "/opt/market-data/astocks_raw.db",
                "grid_diagnostics": {
                    "n_combos": 252,
                    "n_lambda_le_0": 196,
                    "lambda_median": -0.2554,
                    "lambda_mean": -0.3293,
                    "variant_dispersion_sharpe": 0.386,
                    "variant_dispersion_return": 2.3688,
                    "best_variant": "WF-S06",
                    "worst_variant": "WF-S04",
                    "worst_periods": [
                        {
                            "period_index": 8,
                            "period": "2020-05-19 to 2020-07-01",
                            "mean_return": -0.2494,
                            "dispersion": 0.0794,
                            "negative_variant_count": 7,
                            "market_avg_return": 0.0673,
                            "market_negative_ratio": 0.33,
                            "market_sample_count": 300,
                        }
                    ],
                    "selection_inversions": [
                        {
                            "selected_variant": "WF-S05",
                            "train_blocks": "1,3,6,8,9",
                            "test_blocks": "2,4,5,7,10",
                            "train_sharpe": 0.6535,
                            "test_sharpe": -0.1236,
                            "test_rank_from_bottom": 1,
                            "test_best_variant": "WF-S06",
                            "lambda": -1.9459,
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    written = write_minimal_pbo_diagnostics(
        gate_path=gate_path,
        report_path=report_path,
    )

    assert written is True
    text = report_path.read_text(encoding="utf-8")
    assert "77.78%" in text
    assert "WF-S06" in text
    assert "WF-S05" in text
    assert "2020-05-19 to 2020-07-01" in text
    assert "196" in text


def test_write_minimal_pbo_diagnostics_overwrites_existing_diagnostic_when_requested(
    tmp_path: Path,
) -> None:
    gate_path = tmp_path / "walkforward_gate.json"
    report_path = tmp_path / "walkforward-grid-raw-production-diagnostic-latest.md"
    gate_path.write_text(
        json.dumps(
            {
                "run_date": "2026-06-21",
                "both_pass": False,
                "deflated_sharpe": 0.8275,
                "pbo": 0.7778,
                "pbo_pass": False,
                "effective_symbols": 3281,
                "price_mode": "raw",
                "sqlite_db_path": "/opt/market-data/astocks_raw.db",
                "grid_diagnostics": {
                    "n_combos": 252,
                    "n_lambda_le_0": 196,
                    "best_variant": "WF-B02",
                    "worst_variant": "WF-B06",
                    "worst_periods": [{"period": "2020-05-19 to 2020-07-01"}],
                    "selection_inversions": [{"selected_variant": "WF-B04"}],
                },
            }
        ),
        encoding="utf-8",
    )
    report_path.write_text("stale placeholder", encoding="utf-8")

    written = write_minimal_pbo_diagnostics(
        gate_path=gate_path,
        report_path=report_path,
        overwrite=True,
    )

    assert written is True
    text = report_path.read_text(encoding="utf-8")
    assert "stale placeholder" not in text
    assert "WF-B02" in text
    assert "WF-B04" in text


def test_write_minimal_pbo_diagnostics_prefers_gate_coverage_over_stale_summary(
    tmp_path: Path,
) -> None:
    gate_path = tmp_path / "walkforward_gate.json"
    report_path = tmp_path / "walkforward-grid-raw-production-diagnostic-latest.md"
    gate_path.write_text(
        json.dumps(
            {
                "run_date": "2026-06-29",
                "both_pass": False,
                "deflated_sharpe": -0.5708,
                "pbo": 0.6,
                "pbo_pass": False,
                "effective_symbols": 5157,
                "price_mode": "raw",
                "sqlite_db_path": "/opt/market-data/astocks_raw.db",
                "production_gate_coverage": {
                    "stock_symbols": 5797,
                    "covered_symbols": 5157,
                    "selected_symbols": 5193,
                    "rows": 3752714,
                    "first_trade_date": "20230627",
                    "last_trade_date": "20260529",
                    "coverage_mode": "auto_recent_window",
                    "coverage_window_start": "2023-06-27",
                    "coverage_window_end": "2026-06-26",
                    "lookback_years": 3,
                    "listing_aware": True,
                    "expected_trade_days": 708,
                },
            }
        ),
        encoding="utf-8",
    )

    written = write_minimal_pbo_diagnostics(
        gate_path=gate_path,
        report_path=report_path,
        coverage=CoverageSummary(
            stock_symbols=5533,
            covered_symbols=3,
            rows=1,
            first_trade_date="20180102",
            last_trade_date="20241231",
        ),
        overwrite=True,
    )

    assert written is True
    text = report_path.read_text(encoding="utf-8")
    assert "**标的数量**: 5157" in text
    assert "| covered_symbols | 5157 |" in text
    assert "| selected_symbols |" not in text
    assert "| rows | 3752714 |" in text


def test_diagnostic_report_path_keeps_formal_production_report_separate(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "walkforward-grid-raw-production-latest.md"

    assert diagnostic_report_path(report_path) == (
        tmp_path / "walkforward-grid-raw-production-diagnostic-latest.md"
    )


def test_formal_report_backup_path_keeps_formal_snapshot_separate(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "walkforward-grid-raw-production-latest.md"

    assert formal_report_backup_path(report_path) == (
        tmp_path / "walkforward-grid-raw-production-formal-latest.md"
    )


def test_preserve_formal_report_snapshot_copies_existing_report(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "walkforward-grid-raw-production-latest.md"
    report_path.write_text("# formal report\n", encoding="utf-8")

    preserve_formal_report_snapshot(report_path)

    assert formal_report_backup_path(report_path).read_text(encoding="utf-8") == (
        "# formal report\n"
    )


def test_warn_if_report_path_not_writable_prints_hint(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    report_path = tmp_path / "walkforward-grid-raw-production-latest.md"
    report_path.write_text("# formal report\n", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.run_production_walkforward_gate.os.access", lambda *_args: False
    )

    warn_if_report_path_not_writable(report_path)

    assert "not writable" in capsys.readouterr().out


def test_repair_production_gate_metadata_backfills_report_metadata(
    tmp_path: Path,
) -> None:
    gate_path = tmp_path / "walkforward_gate.json"
    report_path = tmp_path / "walkforward-grid-raw-production-latest.md"
    gate_path.write_text(
        json.dumps(
            {
                "run_date": "2026-06-21",
                "deflated_sharpe": 0.0,
                "pbo": 0.0,
                "pbo_valid": False,
                "dsr_pass": False,
                "pbo_pass": False,
                "both_pass": False,
            }
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        "\n".join(
            [
                "# Walk-Forward 生产门禁诊断",
                "",
                "**标的数量**: 3200",
                "",
                "| 项目 | 值 |",
                "|------|-----|",
                "| effective_symbols | 3200 |",
                "| price_mode | raw |",
                "| sqlite_db_path | /opt/market-data/astocks_raw.db |",
                "| stock_symbols | 5533 |",
                "| covered_symbols | 3200 |",
                "| rows | 123456 |",
                "| first_trade_date | 20180102 |",
                "| last_trade_date | 20241231 |",
                "| coverage_mode | auto_recent_window |",
                "| coverage_window_start | 2023-01-01 |",
                "| coverage_window_end | 2024-12-31 |",
                "| lookback_years | 2 |",
                "| listing_aware | True |",
                "| expected_trade_days | 490 |",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    repaired = repair_production_gate_metadata(
        gate_path=gate_path,
        report_path=report_path,
    )

    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    assert repaired is True
    assert payload["effective_symbols"] == 3200
    assert payload["price_mode"] == "raw"
    assert payload["sqlite_db_path"] == "/opt/market-data/astocks_raw.db"
    assert payload["both_pass"] is False
    assert payload["production_gate_coverage"] == {
        "stock_symbols": 5533,
        "covered_symbols": 3200,
        "selected_symbols": 3200,
        "rows": 123456,
        "first_trade_date": "20180102",
        "last_trade_date": "20241231",
        "coverage_mode": "auto_recent_window",
        "coverage_window_start": "2023-01-01",
        "coverage_window_end": "2024-12-31",
        "lookback_years": 2,
        "listing_aware": True,
        "expected_trade_days": 490,
    }


def test_repair_production_gate_metadata_returns_false_when_report_has_no_metadata(
    tmp_path: Path,
) -> None:
    gate_path = tmp_path / "walkforward_gate.json"
    report_path = tmp_path / "walkforward-grid-raw-production-latest.md"
    gate_path.write_text(
        json.dumps({"both_pass": False, "pbo": 0.0, "deflated_sharpe": 0.0}),
        encoding="utf-8",
    )
    report_path.write_text("# report without coverage table\n", encoding="utf-8")

    repaired = repair_production_gate_metadata(
        gate_path=gate_path,
        report_path=report_path,
    )

    assert repaired is False


def test_production_walkforward_gate_passes_raw_db_to_child_process(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "status.json"
    symbols_cache_path = tmp_path / "symbols-cache.json"
    seen: dict[str, object] = {}
    stamped: dict[str, object] = {}
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3200,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=[f"600{idx:03d}" for idx in range(3200)],
        ),
    )
    monkeypatch.setattr(
        gate, "build_walkforward_command", lambda _args: ["python", "-m", "aqsp"]
    )
    monkeypatch.setattr(
        gate,
        "select_covered_symbols",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("main should reuse coverage inspection")
        ),
    )
    monkeypatch.setattr(
        gate,
        "annotate_production_gate_metadata",
        lambda **kwargs: stamped.update(kwargs),
    )

    def fake_execute_child_walkforward(
        *,
        command,
        env,
        cwd,
        timeout_seconds,
        status_path,
        args,
        coverage,
        effective_symbols,
    ):
        seen["command"] = command
        seen["db"] = env.get("AQSP_SQLITE_DB_PATH")
        seen["cwd"] = cwd
        seen["timeout"] = timeout_seconds
        seen["status_path"] = status_path
        seen["effective_symbols"] = effective_symbols
        return 0, 43210

    monkeypatch.setattr(
        gate, "_execute_child_walkforward", fake_execute_child_walkforward
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--status-path",
            str(status_path),
            "--symbols-cache-path",
            str(symbols_cache_path),
        ],
    )

    assert gate.main() == 0
    assert seen == {
        "command": ["python", "-m", "aqsp"],
        "db": str(db),
        "cwd": gate.PROJECT_ROOT,
        "timeout": 7200,
        "status_path": status_path,
        "effective_symbols": 3200,
    }
    assert stamped["db_path"] == db
    assert stamped["effective_symbols"] == 3200


def test_production_walkforward_gate_writes_minimal_diagnostic_after_child_failure(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "status.json"
    symbols_cache_path = tmp_path / "symbols-cache.json"
    gate_path = tmp_path / "walkforward_gate.json"
    report_path = tmp_path / "walkforward-grid-raw-production-latest.md"
    report_path.write_text("# prior formal report\n", encoding="utf-8")
    diagnostic_path = tmp_path / "walkforward-grid-raw-production-diagnostic-latest.md"
    gate_path.write_text(
        json.dumps({"pbo_pass": False, "pbo": 0.0, "deflated_sharpe": 0.0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3200,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=[f"600{idx:03d}" for idx in range(3200)],
        ),
    )
    monkeypatch.setattr(gate, "build_walkforward_command", lambda _args: ["python"])
    monkeypatch.setattr(
        gate,
        "_execute_child_walkforward",
        lambda **_kwargs: (7, 43210),
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--status-path",
            str(status_path),
            "--symbols-cache-path",
            str(symbols_cache_path),
            "--gate-path",
            str(gate_path),
            "--report",
            str(report_path),
        ],
    )

    assert gate.main() == 7
    assert report_path.read_text(encoding="utf-8") == "# prior formal report\n"
    assert formal_report_backup_path(report_path).read_text(encoding="utf-8") == (
        "# prior formal report\n"
    )
    assert "### PBO 失败定位" in diagnostic_path.read_text(encoding="utf-8")


def test_production_walkforward_gate_passes_selected_symbols_file(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "status.json"
    symbols_cache_path = tmp_path / "symbols-cache.json"
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=["600000", "000001", "300750"],
        ),
    )
    monkeypatch.setattr(
        gate,
        "select_covered_symbols",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("main should reuse coverage inspection")
        ),
    )
    monkeypatch.setattr(
        gate, "annotate_production_gate_metadata", lambda **_kwargs: None
    )

    def fake_execute_child_walkforward(
        *,
        command,
        env,
        cwd,
        timeout_seconds,
        status_path,
        args,
        coverage,
        effective_symbols,
    ):
        symbol_file = Path(command[command.index("--symbols-file") + 1])
        seen["symbols"] = symbol_file.read_text(encoding="utf-8").splitlines()
        seen["exists_during_run"] = symbol_file.exists()
        seen["cwd"] = cwd
        seen["timeout"] = timeout_seconds
        return 0, 43210

    monkeypatch.setattr(
        gate, "_execute_child_walkforward", fake_execute_child_walkforward
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--min-symbols",
            "3",
            "--status-path",
            str(status_path),
            "--symbols-cache-path",
            str(symbols_cache_path),
        ],
    )

    assert gate.main() == 0
    assert seen == {
        "symbols": ["600000", "000001", "300750"],
        "exists_during_run": True,
        "cwd": gate.PROJECT_ROOT,
        "timeout": 7200,
    }


def test_production_walkforward_gate_reuses_cached_symbols(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "status.json"
    os.utime(db, None)
    cache_path = tmp_path / "symbols-cache.json"
    gate_path = tmp_path / "walkforward_gate.json"
    gate_path.write_text(json.dumps({"effective_symbols": 3}), encoding="utf-8")
    cache_path.write_text(
        json.dumps(
            {
                "cache_path": str(cache_path.resolve()),
                "db_path": str(db),
                "db_mtime_epoch": int(db.stat().st_mtime),
                "start": "2018-01-01",
                "end": "2024-12-31",
                "min_symbols": 3,
                "coverage_mode": "auto_recent_window",
                "lookback_years": 3,
                "coverage_window": {
                    "start": "2022-01-01",
                    "end": "2024-12-31",
                    "listing_aware": True,
                    "expected_trade_days": 720,
                },
                "summary": {
                    "stock_symbols": 5533,
                    "covered_symbols": 3,
                    "rows": 1,
                    "first_trade_date": "20180102",
                    "last_trade_date": "20241231",
                    "coverage_mode": "auto_recent_window",
                    "coverage_window_start": "2022-01-01",
                    "coverage_window_end": "2024-12-31",
                    "lookback_years": 3,
                    "listing_aware": True,
                    "expected_trade_days": 720,
                },
                "covered_symbols": ["600000", "000001", "300750"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should reuse cached coverage symbols")
        ),
    )
    monkeypatch.setattr(gate, "build_walkforward_command", lambda _args: ["python"])
    monkeypatch.setattr(
        gate, "annotate_production_gate_metadata", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        gate,
        "_execute_child_walkforward",
        lambda **_kwargs: (0, 43210),
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--min-symbols",
            "3",
            "--start",
            "2018-01-01",
            "--end",
            "2024-12-31",
            "--symbols-cache-path",
            str(cache_path),
            "--gate-path",
            str(gate_path),
            "--status-path",
            str(status_path),
        ],
    )

    assert gate.main() == 0


def test_production_walkforward_gate_refreshes_symbols_cache(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "status.json"
    os.utime(db, None)
    cache_path = tmp_path / "symbols-cache.json"
    gate_path = tmp_path / "walkforward_gate.json"
    gate_path.write_text(json.dumps({"effective_symbols": 3}), encoding="utf-8")
    inspection = gate.CoverageInspection(
        summary=gate.CoverageSummary(
            stock_symbols=5533,
            covered_symbols=3,
            rows=1,
            first_trade_date="20180102",
            last_trade_date="20241231",
            coverage_mode="auto_recent_window",
            coverage_window_start="2022-01-01",
            coverage_window_end="2024-12-31",
            lookback_years=3,
            listing_aware=True,
            expected_trade_days=720,
        ),
        covered_symbols=["600000", "000001", "300750"],
    )
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: inspection,
    )
    monkeypatch.setattr(gate, "build_walkforward_command", lambda _args: ["python"])
    monkeypatch.setattr(
        gate, "annotate_production_gate_metadata", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        gate,
        "_execute_child_walkforward",
        lambda **_kwargs: (0, 43210),
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--min-symbols",
            "3",
            "--symbols-cache-path",
            str(cache_path),
            "--gate-path",
            str(gate_path),
            "--status-path",
            str(status_path),
        ],
    )

    assert gate.main() == 0
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["db_path"] == str(db)
    assert payload["min_symbols"] == 3
    assert payload["coverage_mode"] == "auto_recent_window"
    assert payload["coverage_window"]["listing_aware"] is True
    assert payload["covered_symbols"] == ["600000", "000001", "300750"]


def test_production_walkforward_gate_returns_timeout_code(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    import subprocess
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "status.json"
    symbols_cache_path = tmp_path / "symbols-cache.json"
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3200,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=[f"600{idx:03d}" for idx in range(3200)],
        ),
    )
    monkeypatch.setattr(gate, "build_walkforward_command", lambda _args: ["python"])
    monkeypatch.setattr(
        gate,
        "select_covered_symbols",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("main should reuse coverage inspection")
        ),
    )
    monkeypatch.setattr(
        gate, "annotate_production_gate_metadata", lambda **_kwargs: None
    )

    def fake_execute_child_walkforward(**_kwargs):
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=1)

    monkeypatch.setattr(
        gate, "_execute_child_walkforward", fake_execute_child_walkforward
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--timeout-seconds",
            "1",
            "--status-path",
            str(status_path),
            "--symbols-cache-path",
            str(symbols_cache_path),
        ],
    )

    assert gate.main() == 124
    assert "production walk-forward timed out" in capsys.readouterr().out


def test_production_walkforward_gate_dry_run_writes_status(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "status.json"
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3200,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=[f"600{idx:03d}" for idx in range(3200)],
        ),
    )
    monkeypatch.setattr(gate, "build_walkforward_command", lambda _args: ["python"])
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--status-path",
            str(status_path),
            "--dry-run",
        ],
    )

    assert gate.main() == 0
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "dry_run"
    assert payload["effective_symbols"] == 3200
    assert payload["coverage_mode"] == "auto_recent_window"
    assert payload["command"] == ["python"]


def test_production_walkforward_gate_writes_inspecting_status_before_coverage_scan(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "status.json"

    def fake_inspect_raw_coverage_with_symbols(*_args, **_kwargs):
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        assert payload["status"] == "inspecting_coverage"
        assert payload["detail"] == "inspecting raw sqlite full-market coverage"
        return gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3200,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=[f"600{idx:03d}" for idx in range(3200)],
        )

    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        fake_inspect_raw_coverage_with_symbols,
    )
    monkeypatch.setattr(gate, "build_walkforward_command", lambda _args: ["python"])
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--status-path",
            str(status_path),
            "--dry-run",
        ],
    )

    assert gate.main() == 0
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "dry_run"


def test_production_walkforward_gate_blocks_second_active_production_run_before_scan(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    import scripts.run_production_walkforward_gate as gate

    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "running",
                "updated_at": "2026-07-09T10:00:00+08:00",
                "pid": 11111,
                "child_pid": 22222,
                "timeout_seconds": 14400,
                "detail": "child walkforward running; elapsed=60s",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "_pid_active", lambda value: value in {11111, 22222})
    monkeypatch.setattr(
        gate,
        "_pid_cmdline",
        lambda value: (
            (
                "python",
                "-m",
                "aqsp",
                "walkforward",
                "--source",
                "sqlite_db",
                "--pool",
                "all",
                "--grid-cscv",
                "--symbols-file",
                "/tmp/symbols.txt",
            )
            if value == 22222
            else ("python", "scripts/run_production_walkforward_gate.py")
        ),
    )
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("active run guard should block before coverage scan")
        ),
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--status-path",
            str(status_path),
        ],
    )

    assert gate.main() == 2
    output = capsys.readouterr().out
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert "already running" in output
    assert payload["status"] == "blocked_running"
    assert payload["child_pid"] == 22222
    assert payload["blocked_by_pid"] > 0


def test_production_walkforward_gate_blocks_repeated_probe_after_blocked_running_status(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "blocked_running",
                "updated_at": "2026-07-09T10:01:00+08:00",
                "pid": 11111,
                "child_pid": 22222,
                "detail": "active production walk-forward child already running: pid=22222",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "_pid_active", lambda value: value == 22222)
    monkeypatch.setattr(
        gate,
        "_pid_cmdline",
        lambda _value: (
            "python",
            "-m",
            "aqsp",
            "walkforward",
            "--source",
            "sqlite_db",
            "--pool",
            "all",
            "--grid-cscv",
            "--symbols-file",
            "/tmp/symbols.txt",
        ),
    )
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("blocked_running should still block repeated probes")
        ),
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--status-path",
            str(status_path),
        ],
    )

    assert gate.main() == 2
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked_running"
    assert payload["child_pid"] == 22222


def test_production_walkforward_gate_blocks_when_atomic_lock_is_active(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "walkforward-production.lock"
    lock_path.mkdir()
    (lock_path / "meta.json").write_text(
        json.dumps({"pid": 33333, "cmdline": ["python"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "_total_memory_gib", lambda: 8.0)
    monkeypatch.setattr(gate, "_pid_active", lambda value: value == 33333)
    monkeypatch.setattr(
        gate,
        "_pid_cmdline",
        lambda _value: ("python", "scripts/run_production_walkforward_gate.py"),
    )
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3200,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=[f"600{idx:03d}" for idx in range(3200)],
        ),
    )
    monkeypatch.setattr(
        gate,
        "build_walkforward_command",
        lambda _args: (_ for _ in ()).throw(
            AssertionError("active lock should block before child command")
        ),
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--status-path",
            str(status_path),
            "--lock-path",
            str(lock_path),
        ],
    )

    assert gate.main() == 2
    output = capsys.readouterr().out
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert "lock already held" in output
    assert payload["status"] == "blocked_running"
    assert payload["blocked_by_pid"] > 0


def test_production_walkforward_gate_recycles_stale_atomic_lock(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps({"effective_symbols": 3200}), encoding="utf-8")
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "walkforward-production.lock"
    lock_path.mkdir()
    (lock_path / "meta.json").write_text(
        json.dumps({"pid": 33333, "cmdline": ["python"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "_total_memory_gib", lambda: 8.0)
    monkeypatch.setattr(gate, "_pid_active", lambda _value: False)
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3200,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=[f"600{idx:03d}" for idx in range(3200)],
        ),
    )
    monkeypatch.setattr(gate, "build_walkforward_command", lambda _args: ["python"])
    monkeypatch.setattr(gate, "_execute_child_walkforward", lambda **_kwargs: (0, 42))
    monkeypatch.setattr(
        gate, "annotate_production_gate_metadata", lambda **_kwargs: 3200
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--gate-path",
            str(gate_path),
            "--status-path",
            str(status_path),
            "--lock-path",
            str(lock_path),
        ],
    )

    assert gate.main() == 0
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert not lock_path.exists()


def test_production_walkforward_gate_blocked_coverage_writes_status(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "status.json"
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=300,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=[f"600{idx:03d}" for idx in range(300)],
        ),
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--status-path",
            str(status_path),
            "--min-symbols",
            "3200",
        ],
    )

    assert gate.main() == 2
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "blocked_coverage"
    assert payload["coverage"]["covered_symbols"] == 300


def test_production_walkforward_gate_writes_preparing_child_status_before_child_run(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps({"effective_symbols": 3200}), encoding="utf-8")
    status_path = tmp_path / "status.json"
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3200,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=[f"600{idx:03d}" for idx in range(3200)],
        ),
    )
    monkeypatch.setattr(gate, "build_walkforward_command", lambda _args: ["python"])

    def fake_execute_child_walkforward(**_kwargs):
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        observed["status"] = payload["status"]
        observed["detail"] = payload["detail"]
        observed["effective_symbols"] = payload["effective_symbols"]
        return 0, 43210

    monkeypatch.setattr(
        gate, "_execute_child_walkforward", fake_execute_child_walkforward
    )
    monkeypatch.setattr(
        gate, "annotate_production_gate_metadata", lambda **_kwargs: 3200
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--gate-path",
            str(gate_path),
            "--status-path",
            str(status_path),
        ],
    )

    assert gate.main() == 0
    assert observed == {
        "status": "preparing_child",
        "detail": "selected covered symbols; preparing child walkforward",
        "effective_symbols": 3200,
    }


def test_production_walkforward_gate_completed_writes_status(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps({"effective_symbols": 3200}), encoding="utf-8")
    status_path = tmp_path / "status.json"
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3200,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=[f"600{idx:03d}" for idx in range(3200)],
        ),
    )
    monkeypatch.setattr(gate, "build_walkforward_command", lambda _args: ["python"])
    monkeypatch.setattr(
        gate,
        "_execute_child_walkforward",
        lambda **_kwargs: (0, 43210),
    )
    monkeypatch.setattr(
        gate, "annotate_production_gate_metadata", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--gate-path",
            str(gate_path),
            "--status-path",
            str(status_path),
        ],
    )

    assert gate.main() == 0
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["child_exit_code"] == 0


def test_production_walkforward_gate_timeout_writes_status(
    monkeypatch, tmp_path: Path
) -> None:
    import subprocess
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "status.json"
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage_with_symbols",
        lambda *_args, **_kwargs: gate.CoverageInspection(
            summary=gate.CoverageSummary(
                stock_symbols=5533,
                covered_symbols=3200,
                rows=1,
                first_trade_date="20180102",
                last_trade_date="20241231",
            ),
            covered_symbols=[f"600{idx:03d}" for idx in range(3200)],
        ),
    )
    monkeypatch.setattr(gate, "build_walkforward_command", lambda _args: ["python"])
    monkeypatch.setattr(
        gate,
        "_execute_child_walkforward",
        lambda **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd=["python"], timeout=1)
        ),
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--status-path",
            str(status_path),
            "--timeout-seconds",
            "1",
        ],
    )

    assert gate.main() == 124
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "timeout"
    assert payload["child_exit_code"] == 124


def test_repair_stale_running_status_rewrites_dead_pid_status(tmp_path: Path) -> None:
    status_path = tmp_path / "walkforward_production_status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "running",
                "updated_at": "2026-06-27T17:48:19+08:00",
                "pid": 999999,
                "detail": "child walkforward started",
            }
        ),
        encoding="utf-8",
    )

    repaired = repair_stale_running_status(status_path)

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert repaired is True
    assert payload["status"] == "timeout"
    assert payload["child_exit_code"] == 124
    assert "stale running status auto-repaired" in payload["detail"]


def test_repair_stale_running_status_prefers_dead_child_pid_over_reused_parent_pid(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    status_path = tmp_path / "walkforward_production_status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "running",
                "updated_at": "2026-06-27T17:48:19+08:00",
                "pid": 12345,
                "child_pid": 67890,
                "timeout_seconds": 1500,
                "detail": "child walkforward running; elapsed=0s",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        gate,
        "_pid_active",
        lambda value: bool(value == 12345),
    )

    repaired = gate.repair_stale_running_status(status_path)

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert repaired is True
    assert payload["status"] == "timeout"
    assert payload["child_exit_code"] == 124


def test_repair_stale_running_status_rejects_reused_child_pid_cmdline(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    status_path = tmp_path / "walkforward_production_status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "running",
                "updated_at": "2026-07-09T10:00:00+08:00",
                "pid": 12345,
                "child_pid": 67890,
                "timeout_seconds": 14400,
                "detail": "child walkforward running; elapsed=0s",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(gate, "_pid_active", lambda value: value == 67890)
    monkeypatch.setattr(
        gate, "_pid_cmdline", lambda _value: ("python", "-m", "http.server")
    )

    repaired = gate.repair_stale_running_status(status_path)

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert repaired is True
    assert payload["status"] == "timeout"
    assert payload["child_exit_code"] == 124


def test_production_walkforward_gate_repair_only_keeps_status_file_when_unchanged(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "walkforward_production_status.json"
    gate_path = tmp_path / "walkforward_gate.json"
    report_path = tmp_path / "walkforward-grid-raw-production-latest.md"
    status_path.write_text(
        json.dumps(
            {
                "status": "timeout",
                "updated_at": "2026-06-27T17:48:19+08:00",
                "child_exit_code": 124,
            }
        ),
        encoding="utf-8",
    )
    gate_path.write_text(json.dumps({"both_pass": False}), encoding="utf-8")
    report_path.write_text(
        "\n".join(
            [
                "# Walk-Forward 生产门禁诊断",
                "",
                "| 项目 | 值 |",
                "|------|-----|",
                "| effective_symbols | 3200 |",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--status-path",
            str(status_path),
            "--gate-path",
            str(gate_path),
            "--report",
            str(report_path),
            "--repair-only",
        ],
    )

    assert gate.main() == 0
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "timeout"
    assert payload["child_exit_code"] == 124
    assert "metadata repaired" in capsys.readouterr().out


def test_production_walkforward_gate_repair_only_rewrites_diagnostic_with_status_coverage(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    status_path = tmp_path / "walkforward_production_status.json"
    gate_path = tmp_path / "walkforward_gate.json"
    report_path = tmp_path / "walkforward-grid-raw-production-latest.md"
    diagnostic_path = tmp_path / "walkforward-grid-raw-production-diagnostic-latest.md"
    status_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "updated_at": "2026-06-29T16:06:29+08:00",
                "coverage": {
                    "stock_symbols": 5797,
                    "covered_symbols": 5193,
                    "rows": 3752714,
                    "first_trade_date": "20230627",
                    "last_trade_date": "20260529",
                    "coverage_mode": "auto_recent_window",
                    "coverage_window_start": "2023-06-27",
                    "coverage_window_end": "2026-06-26",
                    "lookback_years": 3,
                    "listing_aware": True,
                    "expected_trade_days": 708,
                },
                "effective_symbols": 5157,
            }
        ),
        encoding="utf-8",
    )
    gate_path.write_text(
        json.dumps(
            {
                "run_date": "2026-06-29",
                "both_pass": False,
                "deflated_sharpe": -0.5708,
                "pbo": 0.6,
                "pbo_pass": False,
                "effective_symbols": 5157,
                "production_gate_coverage": {
                    "stock_symbols": 5797,
                    "covered_symbols": 5157,
                    "selected_symbols": 5193,
                    "rows": 3752714,
                    "first_trade_date": "20230627",
                    "last_trade_date": "20260529",
                    "coverage_mode": "auto_recent_window",
                    "coverage_window_start": "2023-06-27",
                    "coverage_window_end": "2026-06-26",
                    "lookback_years": 3,
                    "listing_aware": True,
                    "expected_trade_days": 708,
                },
            }
        ),
        encoding="utf-8",
    )
    report_path.write_text("# formal report\n", encoding="utf-8")
    diagnostic_path.write_text(
        "\n".join(
            [
                "# Walk-Forward 生产门禁诊断",
                "",
                "**标的数量**: 3",
                "",
                "| 项目 | 值 |",
                "|------|-----|",
                "| effective_symbols | 3 |",
                "| covered_symbols | 3 |",
                "| rows | 1 |",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--status-path",
            str(status_path),
            "--gate-path",
            str(gate_path),
            "--report",
            str(report_path),
            "--repair-only",
        ],
    )

    assert gate.main() == 0
    text = diagnostic_path.read_text(encoding="utf-8")
    assert "**标的数量**: 5157" in text
    assert "| covered_symbols | 5157 |" in text
    assert "| rows | 3752714 |" in text
