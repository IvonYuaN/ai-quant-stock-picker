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
    assert 'RUNNER_TIMEOUT_SECONDS="${AQSP_RUNNER_TIMEOUT_SECONDS:-0}"' in script
    assert (
        'timeout --foreground "${RUNNER_TIMEOUT_SECONDS}" bash "${RUNNER_PATH}"'
        in script
    )
    assert "主链路执行超时，被保护性终止" in script


def test_intraday_refresh_script_uses_isolated_outputs() -> None:
    script = (PROJECT_ROOT / "scripts" / "intraday_refresh.sh").read_text(
        encoding="utf-8"
    )

    assert 'export AQSP_RUN_TASK_ID="intraday"' in script
    assert 'export AQSP_NOTIFY="false"' in script
    assert 'INTRADAY_MODE="${AQSP_INTRADAY_MODE:-open}"' in script
    assert 'INTRADAY_ALLOW_NOTIFY="${AQSP_INTRADAY_ALLOW_NOTIFY:-false}"' in script
    assert 'INTRADAY_NOTIFY="${AQSP_INTRADAY_NOTIFY:-false}"' in script
    assert "NOTIFY_ARGS=(--notify)" in script
    assert (
        'if is_truthy "$INTRADAY_ALLOW_NOTIFY" && is_truthy "$INTRADAY_NOTIFY"; then'
        in script
    )
    assert "盘中通知未显式放行，忽略 AQSP_INTRADAY_NOTIFY=true" in script
    assert "data/intraday_predictions.jsonl" in script
    assert "reports/intraday_latest.md" in script
    assert "reports/intraday_latest.csv" in script
    assert "--skip-validation" in script
    assert '--benchmark-symbol ""' in script
    assert "--render-only" in script
    assert "today_shanghai" in script
    assert "今日非交易日，跳过盘中刷新" in script


def test_install_server_cron_script_installs_standard_jobs() -> None:
    script = (PROJECT_ROOT / "scripts" / "install_server_cron.sh").read_text(
        encoding="utf-8"
    )

    assert "AQSP_ENABLE_INTRADAY_CRON" in script
    assert "AQSP_ENABLE_MIDDAY_CRON" in script
    assert "AQSP_ENABLE_DAILY_CRON" in script
    assert "AQSP_ENABLE_MONITOR_CRON" in script
    assert "AQSP_ENABLE_NEWS_CRON" in script
    assert "AQSP_ENABLE_COLDSTART_CRON" in script
    assert "*/10 9-11 * * 1-5" in script
    assert "5 12 * * 1-5" in script
    assert "*/10 13-14 * * 1-5" in script
    assert "35 8 * * 1-5" in script
    assert "5 9 * * 6,0" in script
    assert "0 18 * * 1-5" in script
    assert "40 19 * * 1-5" in script
    assert "*/15 * * * 1-5" in script
    assert "bt_task.sh intraday" in script
    assert "bt_task.sh midday" in script
    assert "bt_task.sh daily" in script
    assert "bt_task.sh coldstart" in script
    assert "bt_task.sh news" in script
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
    assert 'AQSP_RUN_TASK_ID="midday"' in script
    assert 'AQSP_NOTIFY="false"' in script
    assert 'AQSP_NOTIFY_TITLE_LABEL="${AQSP_NOTIFY_TITLE_LABEL:-午盘分析}"' in script
    assert 'AQSP_INTRADAY_NOTIFY="false"' in script
    assert 'AQSP_INTRADAY_ALLOW_NOTIFY="false"' in script
    assert "scripts/intraday_refresh.sh" in script
    assert 'PYTHON_BIN="${VENV_DIR}/bin/python3"' in script
    assert "today_shanghai" in script
    assert "今日非交易日，跳过午盘回看" in script


def test_bt_task_script_exposes_panel_safe_actions() -> None:
    script = (PROJECT_ROOT / "scripts" / "bt_task.sh").read_text(encoding="utf-8")

    assert "宝塔面板计划任务统一入口" in script
    assert 'ACTION="${1:-}"' in script
    assert 'if [ -z "$ACTION" ]' in script
    assert "daily|intraday|midday|coldstart|monitor|news|status" in script
    assert "AQSP_RUNNER_TIMEOUT_SECONDS=5400" in script
    assert "AQSP_MONITOR_TIMEOUT_SECONDS=600" in script
    assert "AQSP_LOCK_STALE_MINUTES=360" in script
    assert "Recommended BT schedule (Asia/Shanghai)" in script
    assert "news      08:35 Mon-Fri trading days only; 09:05 Sat/Sun" in script
    assert "daily     18:00 Mon-Fri" in script
    assert "coldstart 19:40 Mon-Fri" in script
    assert '"正常跳过/互斥保护"' in script
    assert "It is not a failed run." in script
    assert "is_market_trading_day" in script
    assert "AQSP_TRADING_DAY_OVERRIDE_DATE" in script
    assert "skip_non_trading_day" in script
    assert "is_calendar_weekend" in script
    assert "skip_weekday_market_holiday" in script
    assert "AQSP_WEEKEND_PY" in script
    assert "今日非交易日，跳过 ${ACTION} 任务" in script
    assert "AQSP_RUNNER_SCRIPT=scripts/daily_pipeline.sh" in script
    assert "AQSP_RUNNER_SCRIPT=scripts/intraday_refresh.sh" in script
    assert "AQSP_RUNNER_SCRIPT=scripts/midday_refresh.sh" in script
    assert 'export AQSP_RUN_TASK_ID="intraday"' in script
    assert 'export AQSP_NOTIFY="false"' in script
    assert 'export AQSP_RUN_TASK_ID="midday"' in script
    assert "should_bridge_intraday_to_midday" in script
    assert "AQSP_INTRADAY_MIDDAY_BRIDGE" in script
    assert "midday-$(date +%Y-%m-%d).done" in script
    assert "scripts/server_sync_and_run.sh" in script
    assert "scripts/coldstart_daily.sh" in script
    assert "scripts/server_monitor.sh" in script
    assert "scripts/news_catalysts.sh" in script
    assert script.index("news)") < script.index("scripts/news_catalysts.sh")
    assert script.index("skip_weekday_market_holiday") < script.index(
        "scripts/news_catalysts.sh"
    )
    assert "scripts/server_status.sh" in script
    assert "logs/bt" in script

    daily_script = (PROJECT_ROOT / "scripts" / "daily_pipeline.sh").read_text(
        encoding="utf-8"
    )
    assert "周一至周五 18:00" in daily_script
    assert "run_data_cleanup()" in daily_script
    assert daily_script.index("run_data_cleanup") < daily_script.index("周末(周${DOW})")
    assert "today_shanghai" in daily_script
    assert "今日非交易日，跳过跑批" in daily_script


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
    assert 'MAX_LLM_REVIEW_EVENTS="${AQSP_NEWS_MAX_LLM_REVIEW_EVENTS:-1}"' in script
    assert 'SOURCE_TIMEOUT_SECONDS="${AQSP_NEWS_SOURCE_TIMEOUT_SECONDS:-4}"' in script
    assert 'TASK_TIMEOUT_SECONDS="${AQSP_NEWS_TASK_TIMEOUT_SECONDS:-300}"' in script
    assert 'timeout "${TASK_TIMEOUT_SECONDS}"' in script
    assert "消息面雷达超时:" in script
    assert "-m aqsp news-catalysts" in script
    assert "--notify" in script
    assert "--enable-llm-review" in script
    assert "无有效结论：消息源超时" in script
    assert "## ✅ 开盘怎么用" not in script


def test_server_status_surfaces_bt_task_logs() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_status.sh").read_text(encoding="utf-8")

    assert 'print_section "LOCKS"' in script
    assert "config runner_timeout=%ss monitor_timeout=%ss stale_after=%smin" in script
    assert "runner=%s pid=%s started_at=%s age=%smin %s" in script
    assert 'print_section "BT TASK LOG"' in script
    assert "logs/bt/bt-${action}-$(date +%Y-%m-%d).log" in script
    assert "intraday midday daily coldstart monitor news" in script
    assert "bt-status-" not in script


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
    assert "pid-active" in script
    assert "runner=" in script
    assert "com.aqsp.daily.plist" not in script
    assert "AQSP_SCHEDULER_STRICT" in script


def test_clear_locks_is_conservative_by_default() -> None:
    script = (PROJECT_ROOT / "scripts" / "clear_locks.sh").read_text(encoding="utf-8")

    assert "AQSP_LOCK_STALE_MINUTES" in script
    assert "AQSP_CLEAR_LOCKS_FORCE" in script
    assert "meta.env" in script
    assert "保留活跃锁" in script
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
    assert 'LOCK_INFO_FILE="${LOCK_FILE}/meta.env"' in script
    assert 'LOCK_STALE_MINUTES="${AQSP_LOCK_STALE_MINUTES:-360}"' in script
    assert "lock_is_stale" in script
    assert "检测到陈旧主锁，自动回收" in script
    assert "LOCK_RUNNER" in script
    assert "LOCK_STARTED_AT" in script
    assert "主链路仍在运行，本次任务正常跳过；这是互斥保护，不是失败" in script
    assert "AQSP_SYNC_RESULT_FILE" in script
    assert 'write_result "skipped_lock"' in script
    assert 'write_result "completed"' in script


def test_server_monitor_script_has_lock_guard() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_monitor.sh").read_text(
        encoding="utf-8"
    )

    assert "server-monitor.lock" in script
    assert 'LOCK_INFO_FILE="${LOCK_FILE}/meta.env"' in script
    assert 'MONITOR_TIMEOUT_SECONDS="${AQSP_MONITOR_TIMEOUT_SECONDS:-0}"' in script
    assert 'timeout --foreground "${MONITOR_TIMEOUT_SECONDS}" "${PYTHON_BIN}"' in script
    assert "lock_is_stale" in script
    assert "检测到陈旧监控锁，自动回收" in script
    assert "监控执行超时，被保护性终止" in script
    assert "上一轮监控仍在运行，本次监控正常跳过；这是互斥保护，不是失败" in script


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
    assert 'LOCK_STALE_MINUTES="${AQSP_LOCK_STALE_MINUTES:-360}"' in script
    assert "lock_is_stale" in script
    assert "检测到陈旧主锁，自动回收" in script
    assert "主链路仍在运行，本次冷启动正常跳过；这是互斥保护，不是失败" in script
    assert "LOCK_RUNNER=scripts/coldstart_daily.sh" in script
    assert "LOCK_STARTED_AT=$(date '+%Y-%m-%d %H:%M:%S')" in script
    assert '"${PYTHON_BIN}" -u "${UPDATE_SCRIPT}" "${SQLITE_DB_PATH}"' in script
    assert '"${PYTHON_BIN}" -u -m aqsp.cli run' in script
    assert "--source sqlite_db" in script
    assert '--benchmark-symbol ""' in script
    assert "冷启动:" in script
    assert "today_shanghai" in script
    assert "今日非交易日，跳过冷启动任务" in script


def test_install_coldstart_cron_script_installs_single_daily_job() -> None:
    script = (PROJECT_ROOT / "scripts" / "install_coldstart_cron.sh").read_text(
        encoding="utf-8"
    )

    assert "AQSP_COLDSTART_CRON_SCHEDULE" in script
    assert "30 17 * * 1-5" in script
    assert "/scripts/coldstart_daily.sh" in script
