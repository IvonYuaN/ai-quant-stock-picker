from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_server_sync_script_supports_custom_runner() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_sync_and_run.sh").read_text(
        encoding="utf-8"
    )

    assert 'RUNNER_SCRIPT="${AQSP_RUNNER_SCRIPT:-}"' in script
    assert "AQSP_RUNNER_SCRIPT is required" in script
    assert 'log "开始运行任务: ${RUNNER_PATH}"' in script
    assert 'bash "${RUNNER_PATH}"' in script


def test_intraday_refresh_script_uses_isolated_outputs() -> None:
    script = (PROJECT_ROOT / "scripts" / "intraday_refresh.sh").read_text(
        encoding="utf-8"
    )

    assert 'export AQSP_RUN_TASK_ID="${AQSP_RUN_TASK_ID:-intraday}"' in script
    assert 'INTRADAY_MODE="${AQSP_INTRADAY_MODE:-open}"' in script
    assert 'INTRADAY_NOTIFY="${AQSP_INTRADAY_NOTIFY:-false}"' in script
    assert 'NOTIFY_ARGS=(--notify)' in script
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
    assert "AQSP_ENABLE_MIDDAY_CRON" in script
    assert "AQSP_ENABLE_DAILY_CRON" in script
    assert "AQSP_ENABLE_MONITOR_CRON" in script
    assert "*/10 9-11 * * 1-5" in script
    assert "5 12 * * 1-5" in script
    assert "*/10 13-14 * * 1-5" in script
    assert "0 18 * * 1-5" in script
    assert "*/15 * * * 1-5" in script
    assert "bt_task.sh intraday" in script
    assert "bt_task.sh midday" in script
    assert "bt_task.sh daily" in script
    assert "bt_task.sh monitor" in script


def test_midday_refresh_reuses_intraday_chain_without_formal_ledger_pollution() -> None:
    script = (PROJECT_ROOT / "scripts" / "midday_refresh.sh").read_text(
        encoding="utf-8"
    )

    assert "午盘回看" in script
    assert "AQSP_MIDDAY_REQUIRE_WINDOW" in script
    assert "1135" in script
    assert "1230" in script
    assert "AQSP_INTRADAY_REQUIRE_MARKET_HOURS=false" in script
    assert 'AQSP_RUN_TASK_ID="${AQSP_RUN_TASK_ID:-midday}"' in script
    assert 'AQSP_NOTIFY_TITLE_LABEL="${AQSP_NOTIFY_TITLE_LABEL:-午盘分析}"' in script
    assert 'AQSP_INTRADAY_NOTIFY="${AQSP_INTRADAY_NOTIFY:-true}"' in script
    assert "scripts/intraday_refresh.sh" in script


def test_bt_task_script_exposes_panel_safe_actions() -> None:
    script = (PROJECT_ROOT / "scripts" / "bt_task.sh").read_text(encoding="utf-8")

    assert "宝塔面板计划任务统一入口" in script
    assert 'ACTION="${1:-}"' in script
    assert 'if [ -z "$ACTION" ]' in script
    assert "daily|intraday|midday|coldstart|monitor|news|status" in script
    assert "AQSP_RUNNER_SCRIPT=scripts/daily_pipeline.sh" in script
    assert "AQSP_RUNNER_SCRIPT=scripts/intraday_refresh.sh" in script
    assert "AQSP_RUNNER_SCRIPT=scripts/midday_refresh.sh" in script
    assert "should_bridge_intraday_to_midday" in script
    assert "AQSP_INTRADAY_MIDDAY_BRIDGE" in script
    assert "midday-$(date +%Y-%m-%d).done" in script
    assert "scripts/server_sync_and_run.sh" in script
    assert "scripts/coldstart_daily.sh" in script
    assert "scripts/server_monitor.sh" in script
    assert "scripts/news_catalysts.sh" in script
    assert "scripts/server_status.sh" in script
    assert "logs/bt" in script


def test_news_catalysts_script_sends_research_notification() -> None:
    script = (PROJECT_ROOT / "scripts" / "news_catalysts.sh").read_text(
        encoding="utf-8"
    )

    assert "消息面雷达" in script
    assert "AQSP_NEWS_SYMBOLS" in script
    assert "AQSP_NEWS_ENABLE_LLM_REVIEW" in script
    assert "AQSP_NEWS_SOURCE_TIMEOUT_SECONDS" in script
    assert "AQSP_NEWS_LLM_TIMEOUT_SECONDS" in script
    assert "AQSP_NEWS_MAX_LLM_REVIEW_EVENTS" in script
    assert "AQSP_NEWS_TASK_TIMEOUT_SECONDS" in script
    assert 'timeout "${TASK_TIMEOUT_SECONDS}"' in script
    assert "消息面雷达超时降级" in script
    assert "-m aqsp news-catalysts" in script
    assert "--notify" in script
    assert "--enable-llm-review" in script
    assert "不替代主报告结论" in script


def test_server_status_surfaces_bt_task_logs() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_status.sh").read_text(encoding="utf-8")

    assert 'print_section "BT TASK LOG"' in script
    assert "logs/bt/bt-${action}-$(date +%Y-%m-%d).log" in script


def test_scheduler_diagnosis_is_read_only_and_bt_first() -> None:
    script = (PROJECT_ROOT / "scripts" / "check_scheduler.py").read_text(
        encoding="utf-8"
    )

    assert (
        "Diagnose AQSP scheduled tasks without touching system configuration" in script
    )
    assert "now_shanghai" in script
    assert "datetime.now" not in script
    assert "shell=True" not in script
    assert "bt_task.sh" in script
    assert '"news"' in script
    assert "BT Panel jobs may be managed outside crontab" in script
    assert "BT Panel logs" in script
    assert "com.aqsp.daily.plist" not in script
    assert "AQSP_SCHEDULER_STRICT" in script


def test_clear_locks_is_conservative_by_default() -> None:
    script = (PROJECT_ROOT / "scripts" / "clear_locks.sh").read_text(encoding="utf-8")

    assert "AQSP_LOCK_STALE_MINUTES" in script
    assert "AQSP_CLEAR_LOCKS_FORCE" in script
    assert 'find "$LOCK_DIR" -maxdepth 1 -type d -name "*.lock"' in script
    assert 'rm -rf -- "$lock_path"' in script
    assert "pkill" not in script
    assert "/tmp/aqsp" not in script
    assert "~/Documents" not in script
    assert "/www/server/panel/tmp" not in script


def test_server_sync_script_has_lock_guard() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_sync_and_run.sh").read_text(
        encoding="utf-8"
    )

    assert "server-runtime.lock" in script
    assert "已有服务器主任务在运行，跳过本次同步与跑批" in script


def test_server_monitor_script_has_lock_guard() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_monitor.sh").read_text(
        encoding="utf-8"
    )

    assert "server-monitor.lock" in script
    assert "已有监控任务在运行，跳过本次监控" in script


def test_coldstart_daily_script_updates_db_then_runs_cli() -> None:
    script = (PROJECT_ROOT / "scripts" / "coldstart_daily.sh").read_text(
        encoding="utf-8"
    )

    assert 'dirname "$SQLITE_DB_PATH"' in script
    assert "A股量化分析数据/update_daily.py" in script
    assert "AQSP_COLDSTART_UPDATE_SCRIPT" in script
    assert "AQSP_COLDSTART_ALLOW_INTRADAY" in script
    assert 'LEDGER_PATH_FOR_PROGRESS="$LEDGER_PATH"' in script
    assert 'os.environ["LEDGER_PATH_FOR_PROGRESS"]' in script
    assert "收盘前，跳过冷启动" in script
    assert "bt_task.sh intraday" in script
    assert "server-runtime.lock" in script
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
