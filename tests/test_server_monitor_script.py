from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_server_monitor_script_runs_monitor_with_notify() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_monitor.sh").read_text(
        encoding="utf-8"
    )

    assert 'source "${PROJECT_ROOT}/.env"' in script
    assert 'PYTHON_BIN="${VENV_DIR}/bin/python3"' in script
    assert 'export AQSP_NOTIFY="false"' in script
    assert 'MONITOR_NOTIFY="${AQSP_MONITOR_NOTIFY:-false}"' in script
    assert 'MONITOR_ARGS=( -m aqsp monitor --config "${MONITOR_CONFIG}" )' in script
    assert 'MONITOR_ARGS+=( --notify )' in script
    assert "--notify-critical-only" in script
    assert 'QUIET_HEALTHY="${AQSP_MONITOR_QUIET_HEALTHY:-true}"' in script
    assert "--quiet-healthy" in script
    assert 'EXIT_ON_ALERT="${AQSP_MONITOR_EXIT_ON_ALERT:-false}"' in script
    assert "避免外层调度重复告警" in script
    assert "监控通知未放行，仅记录日志" in script
    assert "logs/monitor" in script
