from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_server_status_script_loads_env() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_status.sh").read_text(
        encoding="utf-8"
    )

    assert 'source "${PROJECT_ROOT}/.env"' in script
    assert "set -a" in script
    assert "set +a" in script


def test_init_server_runtime_script_bootstraps_runtime_files() -> None:
    script = (PROJECT_ROOT / "scripts" / "init_server_runtime.sh").read_text(
        encoding="utf-8"
    )

    assert "data/paper_trades.jsonl" in script
    assert "data/risk_state.json" in script
    assert "data/intraday_predictions.jsonl" in script
    assert '"cooldown_until": null' in script
    assert '"last_triggered_date": null' in script
