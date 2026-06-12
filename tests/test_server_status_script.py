from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_server_status_script_covers_runtime_sections() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_status.sh").read_text(encoding="utf-8")

    assert 'print_section "GIT"' in script
    assert 'print_section "CRON"' in script
    assert 'print_section "CRON AQSP AUDIT"' in script
    assert 'print_section "LOCKS"' in script
    assert 'print_section "ARTIFACTS"' in script
    assert 'print_section "RUNTIME"' in script
    assert 'print_section "DOCTOR"' in script
    assert 'print_section "DEPLOY LOG"' in script
    assert 'print_section "INTRADAY LOG"' in script
    assert 'print_section "DAILY LOG"' in script
    assert "scripts/diagnose_runtime.py" in script
    assert "server-runtime.lock" in script
    assert "server-monitor.lock" in script
    assert "pid-active" in script
    assert "direct-aqsp-cron-needs-review" in script
    assert "bt-wrapper action=" in script
    assert 'cron="' in script
    assert 'gate="' in script
    assert "found_direct" in script
    assert 'echo "none"' in script
    assert 'python3" -m aqsp doctor' in script or "-m aqsp doctor" in script
