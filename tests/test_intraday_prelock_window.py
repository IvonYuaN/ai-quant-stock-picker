from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_intraday(
    tmp_path: Path,
    hm: str,
    *,
    require_hours: str = "true",
    bridge: bool = False,
    midday_done: bool = False,
    trading_day: bool = True,
    worker: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the actual panel entry, with no network, real clocks or heavy runner."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("bt_task.sh", "runtime_python.sh", "intraday_window.sh", "intraday_refresh.sh"):
        source = PROJECT_ROOT / "scripts" / name
        shutil.copy2(source, scripts / name)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_date = bindir / "date"
    fake_date.write_text(
        '#!/bin/bash\ncase "${1:-}" in\n'
        '  +%H%M) echo "$TEST_HM";;\n'
        '  +%u) echo 2;;\n'
        '  +%Y-%m-%d) echo 2026-10-27;;\n'
        '  *) exec /bin/date "$@";;\nesac\n',
        encoding="utf-8",
    )
    fake_date.chmod(0o755)
    fake_python = bindir / "calendar-python"
    fake_python.write_text(
        '#!/bin/bash\ncat >/dev/null\nexit "$TEST_CALENDAR_EXIT"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    reached = tmp_path / "sync-entry"
    (scripts / "server_sync_and_run.sh").write_text(
        '#!/bin/bash\nset -eu\n'
        'echo "$AQSP_RUNNER_SCRIPT" >> "$TEST_SYNC_ENTRY"\n'
        'mkdir -p "$(dirname "$AQSP_SYNC_RESULT_FILE")"\n'
        'printf "status=completed\\nexit_code=0\\n" > "$AQSP_SYNC_RESULT_FILE"\n',
        encoding="utf-8",
    )
    if midday_done:
        marker = tmp_path / "data/.state/midday-2026-10-27.done"
        marker.parent.mkdir(parents=True)
        marker.touch()
    result = subprocess.run(
        ["bash", str(scripts / "intraday_refresh.sh")] if worker else ["bash", str(scripts / "bt_task.sh"), "intraday"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bindir}:{os.defpath}",
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_RUNTIME_ROOT": str(tmp_path),
            "AQSP_RUNTIME_DATA_ROOT": str(tmp_path / "data"),
            "AQSP_RUNTIME_LOCK_DIR": str(tmp_path / "data/.locks"),
            "AQSP_RUNTIME_STATE_DIR": str(tmp_path / "data/.state"),
            "AQSP_BT_LOGS_DIR": str(tmp_path / "logs"),
            "AQSP_IMMUTABLE_RELEASE": "true",
            "AQSP_PYTHON": str(fake_python),
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": require_hours,
            "AQSP_INTRADAY_MIDDAY_BRIDGE": str(bridge).lower(),
            "TEST_HM": hm,
            "TEST_CALENDAR_EXIT": "0" if trading_day else "1",
            "TEST_SYNC_ENTRY": str(reached),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result, reached


@pytest.mark.parametrize("hm", ["0934", "1131", "1304", "1458", "2230"])
def test_bt_intraday_skips_before_main_lock_when_window_closed(tmp_path: Path, hm: str) -> None:
    result, reached = _run_intraday(tmp_path, hm)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not reached.exists(), "out-of-hours no-op reached the main-lock runner"
    assert "不在盘中刷新时段" in result.stdout
    assert not (tmp_path / "data/.locks").exists()


@pytest.mark.parametrize("hm", ["0935", "1000", "1130", "1305", "1400", "1457"])
def test_bt_intraday_runs_when_inside_inclusive_window(tmp_path: Path, hm: str) -> None:
    result, reached = _run_intraday(tmp_path, hm)
    assert result.returncode == 0, result.stdout + result.stderr
    assert reached.read_text().strip() == "scripts/intraday_refresh.sh"


@pytest.mark.parametrize("hm", ["1135", "1200", "1230"])
def test_bt_intraday_bridges_before_window_gate_when_midday_due(tmp_path: Path, hm: str) -> None:
    result, reached = _run_intraday(tmp_path, hm, bridge=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert reached.read_text().strip() == "scripts/midday_refresh.sh"
    assert (tmp_path / "data/.state/midday-2026-10-27.done").is_file()


@pytest.mark.parametrize("hm", ["1134", "1231"])
def test_bt_intraday_skips_when_outside_bridge_and_scan_windows(tmp_path: Path, hm: str) -> None:
    result, reached = _run_intraday(tmp_path, hm, bridge=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not reached.exists()


def test_bt_intraday_skips_when_midday_bridge_already_completed(tmp_path: Path) -> None:
    result, reached = _run_intraday(tmp_path, "1200", bridge=True, midday_done=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not reached.exists()


@pytest.mark.parametrize("override", ["false", "0", "off", "no"])
def test_bt_intraday_runs_offhours_when_diagnostic_override_set(tmp_path: Path, override: str) -> None:
    result, reached = _run_intraday(tmp_path, "2230", require_hours=override)
    assert result.returncode == 0, result.stdout + result.stderr
    assert reached.read_text().strip() == "scripts/intraday_refresh.sh"


def test_bt_intraday_keeps_calendar_guard_when_diagnostic_override_set(tmp_path: Path) -> None:
    result, reached = _run_intraday(tmp_path, "2230", require_hours="false", trading_day=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "非交易日" in result.stdout
    assert not reached.exists()


@pytest.mark.parametrize("hm", ["0934", "1131", "1304", "1458", "2230"])
def test_intraday_worker_rechecks_window_when_invoked_standalone(tmp_path: Path, hm: str) -> None:
    result, reached = _run_intraday(tmp_path, hm, worker=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "不在盘中刷新时段" in result.stdout
    assert not reached.exists()
    assert not (tmp_path / "data/.locks/intraday-refresh.lock").exists()
