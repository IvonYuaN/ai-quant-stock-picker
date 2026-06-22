from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.run_production_walkforward_gate import (
    build_walkforward_command,
    inspect_raw_coverage,
    select_covered_symbols,
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
    assert "--symbols-file" in command
    assert "/tmp/symbols.txt" in command


def test_production_walkforward_gate_passes_raw_db_to_child_process(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage",
        lambda *_args, **_kwargs: gate.CoverageSummary(
            stock_symbols=5533,
            covered_symbols=3200,
            rows=1,
            first_trade_date="20180102",
            last_trade_date="20241231",
        ),
    )
    monkeypatch.setattr(
        gate, "build_walkforward_command", lambda _args: ["python", "-m", "aqsp"]
    )
    monkeypatch.setattr(
        gate,
        "select_covered_symbols",
        lambda *_args, **_kwargs: [f"600{idx:03d}" for idx in range(3200)],
    )

    def fake_run(command, *, check, env, timeout):
        seen["command"] = command
        seen["check"] = check
        seen["db"] = env.get("AQSP_SQLITE_DB_PATH")
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
        "timeout": 7200,
    }


def test_production_walkforward_gate_passes_selected_symbols_file(
    monkeypatch, tmp_path: Path
) -> None:
    import scripts.run_production_walkforward_gate as gate

    db = tmp_path / "raw.db"
    db.write_text("", encoding="utf-8")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        gate,
        "inspect_raw_coverage",
        lambda *_args, **_kwargs: gate.CoverageSummary(
            stock_symbols=5533,
            covered_symbols=3,
            rows=1,
            first_trade_date="20180102",
            last_trade_date="20241231",
        ),
    )
    monkeypatch.setattr(
        gate,
        "select_covered_symbols",
        lambda *_args, **_kwargs: ["600000", "000001", "300750"],
    )

    def fake_run(command, *, check, env, timeout):
        symbol_file = Path(command[command.index("--symbols-file") + 1])
        seen["symbols"] = symbol_file.read_text(encoding="utf-8").splitlines()
        seen["exists_during_run"] = symbol_file.exists()
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
        "inspect_raw_coverage",
        lambda *_args, **_kwargs: gate.CoverageSummary(
            stock_symbols=5533,
            covered_symbols=3200,
            rows=1,
            first_trade_date="20180102",
            last_trade_date="20241231",
        ),
    )
    monkeypatch.setattr(gate, "build_walkforward_command", lambda _args: ["python"])
    monkeypatch.setattr(
        gate,
        "select_covered_symbols",
        lambda *_args, **_kwargs: [f"600{idx:03d}" for idx in range(3200)],
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
