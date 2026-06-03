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


def test_install_server_cron_script_installs_standard_jobs() -> None:
    script = (PROJECT_ROOT / "scripts" / "install_server_cron.sh").read_text(
        encoding="utf-8"
    )

    assert "AQSP_ENABLE_INTRADAY_CRON" in script
    assert "AQSP_ENABLE_DAILY_CRON" in script
    assert "AQSP_ENABLE_MONITOR_CRON" in script
    assert "*/10 9-11 * * 1-5" in script
    assert "*/10 13-14 * * 1-5" in script
    assert "0 18 * * 1-5" in script
    assert "*/15 * * * 1-5" in script


def test_coldstart_daily_script_updates_db_then_runs_cli() -> None:
    script = (PROJECT_ROOT / "scripts" / "coldstart_daily.sh").read_text(
        encoding="utf-8"
    )

    assert 'dirname "$SQLITE_DB_PATH"' in script
    assert 'A股量化分析数据/update_daily.py' in script
    assert 'AQSP_COLDSTART_UPDATE_SCRIPT' in script
    assert '"${PYTHON_BIN}" -u "${UPDATE_SCRIPT}" "${SQLITE_DB_PATH}"' in script
    assert '"${PYTHON_BIN}" -u -m aqsp.cli run' in script
    assert "--source sqlite_db" in script
    assert '--benchmark-symbol ""' in script
    assert "冷启动:" in script


def test_install_coldstart_cron_script_installs_single_daily_job() -> None:
    script = (PROJECT_ROOT / "scripts" / "install_coldstart_cron.sh").read_text(
        encoding="utf-8"
    )

    assert "AQSP_COLDSTART_CRON_SCHEDULE" in script
    assert "30 17 * * 1-5" in script
    assert "/scripts/coldstart_daily.sh" in script
