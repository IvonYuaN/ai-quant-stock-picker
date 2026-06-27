from __future__ import annotations

import json
import sqlite3
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
    select_covered_symbols,
    warn_if_report_path_not_writable,
    write_minimal_pbo_diagnostics,
)


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


def test_inspect_raw_coverage_counts_covered_symbols(tmp_path: Path) -> None:
    db = tmp_path / "astocks_raw.db"
    _make_raw_db(db, symbols=4)

    coverage = inspect_raw_coverage(db, start="2024-01-01", end="2024-01-30")

    assert coverage.stock_symbols == 4
    assert coverage.covered_symbols == 4
    assert coverage.rows == 120
    assert coverage.first_trade_date == "20240101"
    assert coverage.last_trade_date == "20240130"


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

    def fake_run(command, *, check, env, cwd, timeout):
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
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(gate_mod.subprocess, "run", fake_run)

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
        ),
        effective_symbols=3200,
    )

    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    assert payload["both_pass"] is False
    assert payload["deflated_sharpe"] == 0.8275
    assert payload["pbo"] == 0.7778
    assert payload["source"] == "sqlite_db"
    assert payload["price_mode"] == "raw"
    assert payload["effective_symbols"] == 3200
    assert payload["production_gate_coverage"] == {
        "stock_symbols": 5533,
        "covered_symbols": 3200,
        "rows": 123456,
        "first_trade_date": "20180102",
        "last_trade_date": "20241231",
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
            ),
            effective_symbols=3200,
        )
    except SystemExit as exc:
        assert "effective_symbols mismatch" in str(exc)
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
    monkeypatch.setattr("scripts.run_production_walkforward_gate.os.access", lambda *_args: False)

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
        "rows": 123456,
        "first_trade_date": "20180102",
        "last_trade_date": "20241231",
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

    def fake_run(command, *, check, env, cwd, timeout):
        seen["command"] = command
        seen["check"] = check
        seen["db"] = env.get("AQSP_SQLITE_DB_PATH")
        seen["cwd"] = cwd
        seen["timeout"] = timeout
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        gate.sys,
        "argv",
        ["run_production_walkforward_gate.py", "--db", str(db)],
    )

    assert gate.main() == 0
    assert seen == {
        "command": ["python", "-m", "aqsp"],
        "check": False,
        "db": str(db),
        "cwd": gate.PROJECT_ROOT,
        "timeout": 7200,
    }
    assert stamped["db_path"] == db
    assert stamped["effective_symbols"] == 3200


def test_production_walkforward_gate_writes_minimal_diagnostic_after_child_failure(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
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
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 7})(),
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

    def fake_run(command, *, check, env, cwd, timeout):
        symbol_file = Path(command[command.index("--symbols-file") + 1])
        seen["symbols"] = symbol_file.read_text(encoding="utf-8").splitlines()
        seen["exists_during_run"] = symbol_file.exists()
        seen["cwd"] = cwd
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--min-symbols",
            "3",
        ],
    )

    assert gate.main() == 0
    assert seen == {
        "symbols": ["600000", "000001", "300750"],
        "exists_during_run": True,
        "cwd": gate.PROJECT_ROOT,
    }


def test_production_walkforward_gate_returns_timeout_code(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    import subprocess
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
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

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=1)

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    monkeypatch.setattr(
        gate.sys,
        "argv",
        [
            "run_production_walkforward_gate.py",
            "--db",
            str(db),
            "--timeout-seconds",
            "1",
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
    assert payload["command"] == ["python"]


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
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 0})(),
    )
    monkeypatch.setattr(gate, "annotate_production_gate_metadata", lambda **_kwargs: None)
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
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
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
