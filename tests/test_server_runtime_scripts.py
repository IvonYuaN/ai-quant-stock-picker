from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_server_sync_script_supports_custom_runner() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_sync_and_run.sh").read_text(
        encoding="utf-8"
    )

    assert 'AQSP_RUNNER_SCRIPT:-scripts/daily_pipeline.sh' in script
    assert 'log "开始运行任务: ${RUNNER_PATH}"' in script
    assert 'bash "${RUNNER_PATH}"' in script


def test_intraday_refresh_script_uses_isolated_outputs() -> None:
    script = (PROJECT_ROOT / "scripts" / "intraday_refresh.sh").read_text(
        encoding="utf-8"
    )

    assert "data/intraday_predictions.jsonl" in script
    assert "reports/intraday_latest.md" in script
    assert "reports/intraday_latest.csv" in script
    assert "--skip-validation" in script
    assert '--benchmark-symbol ""' in script
    assert "--render-only" in script
