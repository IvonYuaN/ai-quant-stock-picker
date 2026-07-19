from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_fake_trading_day_date(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
case "${1:-}" in
  "+%u")
    printf '%s\\n' "${FAKE_COLDSTART_WEEKDAY:?}"
    exit 0
    ;;
  "+%Y-%m-%d")
    printf '%s\\n' "${FAKE_COLDSTART_TODAY:?}"
    exit 0
    ;;
esac
exec /bin/date "$@"
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_fake_coldstart_python(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "-u" ]; then
  shift
fi
if [ "${1:-}" = "-" ]; then
  payload="$(cat)"
  if [ -n "${TARGET_DATE_FOR_RUN:-}" ]; then
    printf '%s\\n' "$TARGET_DATE_FOR_RUN"
    exit 0
  fi
  if [ -n "${SQLITE_DB_PATH_FOR_COVERAGE:-}" ]; then
    if [ -n "${FAKE_COVERAGE_SEQUENCE:-}" ]; then
      state_path="${FAKE_COVERAGE_STATE_PATH:?}"
      index=0
      if [ -f "$state_path" ]; then
        index="$(cat "$state_path")"
      fi
      value="$(printf '%s\\n' "$FAKE_COVERAGE_SEQUENCE" | cut -d, -f$((index + 1)))"
      if [ -z "$value" ]; then
        value="$(printf '%s\\n' "$FAKE_COVERAGE_SEQUENCE" | awk -F, '{print $NF}')"
      fi
      echo $((index + 1)) >"$state_path"
      printf '%s\\n' "$value"
      exit 0
    fi
    printf '%s\\n' "${FAKE_COVERAGE:-0}"
    exit 0
  fi
  if [ -n "${LEDGER_PATH_FOR_PROGRESS:-}" ]; then
    printf '冷启动: 34/30\\n'
    exit 0
  fi
  if [ -n "${LEDGER_PATH_FOR_COLDSTART_STATUS:-}" ]; then
    printf '%s\\n' "${FAKE_COLDSTART_SIGNAL_PROGRESS:-0/30}"
    exit 0
  fi
  if [ -n "${HANDOFF_STATUS_PATH_FOR_COLDSTART:-}" ]; then
    mkdir -p "$(dirname "$HANDOFF_STATUS_PATH_FOR_COLDSTART")"
    printf '{"status":"ready","progress":"%s","next_step":"run_production_walkforward_gate","next_command":"bash scripts/bt_task.sh walkforward-gate","blocker":"%s"}\\n' \
      "${COLDSTART_PROGRESS_FOR_STATUS:-}" "${COLDSTART_REASON_FOR_STATUS:-}" \
      >"$HANDOFF_STATUS_PATH_FOR_COLDSTART"
    exit 0
  fi
  if [[ "$payload" == *"is_trading_day"* ]]; then
    if [ "${FAKE_COLDSTART_IS_TRADING_DAY:?}" = "true" ]; then
      exit 0
    fi
    exit 1
  fi
  exit 0
fi
if [[ "${1:-}" == *"update_sqlite_daily.py" ]]; then
  echo "fake sqlite update"
  exit "${FAKE_UPDATE_EXIT:-0}"
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "aqsp.cli" ]; then
  printf '%s\\n' "$*" >"${FAKE_CLI_ARGS_PATH:?}"
  exit "${FAKE_CLI_EXIT:-0}"
fi
printf 'unexpected fake python args: %s\\n' "$*" >&2
exit 42
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _build_coldstart_runtime(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "project"
    scripts_dir = root / "scripts"
    data_dir = root / "data"
    scripts_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "scripts" / "coldstart_daily.sh", scripts_dir)
    shutil.copy2(PROJECT_ROOT / "scripts" / "runtime_python.sh", scripts_dir)
    (scripts_dir / "update_sqlite_daily.py").write_text("# fake updater\n")
    db_path = data_dir / "astocks_raw.db"
    db_path.write_text("", encoding="utf-8")
    fake_python = tmp_path / "fake-python"
    _write_fake_coldstart_python(fake_python)
    return root, db_path, fake_python


def _run_fake_coldstart(
    tmp_path: Path,
    *,
    coverage: int | str,
    update_exit: int,
    aqsp_source: str | None = None,
    coldstart_progress: str = "0/30",
    today: str = "2026-07-07",
    weekday: int = 2,
    is_trading_day: bool = True,
) -> subprocess.CompletedProcess[str]:
    root, db_path, fake_python = _build_coldstart_runtime(tmp_path)
    cli_args_path = tmp_path / "cli-args.txt"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_trading_day_date(fake_bin / "date")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "AQSP_PROJECT_ROOT": str(root),
        "AQSP_PYTHON": str(fake_python),
        "AQSP_COLDSTART_DB_PATH": str(db_path),
        "AQSP_SQLITE_DB_PATH": str(db_path),
        "AQSP_COLDSTART_TARGET_DATE": today,
        "AQSP_COLDSTART_ALLOW_INTRADAY": "true",
        "AQSP_COLDSTART_MIN_TARGET_COVERAGE": "3000",
        "AQSP_COLDSTART_LOG_DIR": str(tmp_path / "logs"),
        "FAKE_COLDSTART_TODAY": today,
        "FAKE_COLDSTART_WEEKDAY": str(weekday),
        "FAKE_COLDSTART_IS_TRADING_DAY": str(is_trading_day).lower(),
        "FAKE_COVERAGE": str(coverage),
        "FAKE_COVERAGE_SEQUENCE": str(coverage) if "," in str(coverage) else "",
        "FAKE_COVERAGE_STATE_PATH": str(tmp_path / "coverage-state.txt"),
        "FAKE_UPDATE_EXIT": str(update_exit),
        "FAKE_CLI_EXIT": "0",
        "FAKE_CLI_ARGS_PATH": str(cli_args_path),
        "FAKE_COLDSTART_SIGNAL_PROGRESS": coldstart_progress,
    }
    if aqsp_source is not None:
        env["AQSP_SOURCE"] = aqsp_source
    return subprocess.run(
        ["bash", str(root / "scripts" / "coldstart_daily.sh")],
        env=env,
        cwd=str(root),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_server_sync_script_supports_custom_runner() -> None:
    script = (PROJECT_ROOT / "scripts" / "server_sync_and_run.sh").read_text(
        encoding="utf-8"
    )

    assert 'RUNNER_SCRIPT="${AQSP_RUNNER_SCRIPT:-}"' in script
    assert 'if [ "${IMMUTABLE_RELEASE}" != "true" ] && [ ! -d "${PROJECT_ROOT}/.git" ]; then' in script
    assert "AQSP_RUNNER_SCRIPT is required" in script
    assert 'log "开始运行任务: ${RUNNER_PATH}"' in script
    assert 'bash "${RUNNER_PATH}"' in script
    assert "printf 'GIT_SYNC_LOCK_STARTED_AT=%q\\n'" in script
    assert "printf 'LOCK_STARTED_AT=%q\\n'" in script
    assert 'RUNNER_TIMEOUT_SECONDS="${AQSP_RUNNER_TIMEOUT_SECONDS:-0}"' in script
    assert (
        'RUNTIME_OVERLAY_MANIFEST="${AQSP_RUNTIME_OVERLAY_MANIFEST:-${STATE_DIR}/runtime-sync-overlay.json}"'
        in script
    )
    assert 'write_result "blocked_dirty"' in script
    assert 'write_result "missing_runner"' in script
    assert 'DIRTY_STATE_FILE="${STATE_DIR}/server-sync-dirty.env"' in script
    assert "dirty_state_hash()" in script
    assert "dirty_state_count()" in script
    assert "write_dirty_state()" in script
    assert "managed_overlay_allows_dirty_state()" in script
    assert "file_hashes" in script
    assert "hashlib.sha256" in script
    assert "actual_hash != expected_hash" in script
    assert "检测到受控 runtime overlay，跳过 Git 同步后继续运行" in script
    assert "仍未清理；明细未变化" in script
    assert 'write_result "blocked_dirty" 1' in script
    assert "ALLOW_STABLE_DIRTY_RUN" not in script
    assert "本次跳过 Git fetch/pull；等待仓库回归 clean 后再恢复自动同步" in script
    assert "PIPESTATUS[0]" in script
    assert "printf 'exit_code=%s\\n'" in script
    assert (
        'timeout --foreground "${RUNNER_TIMEOUT_SECONDS}" bash "${RUNNER_PATH}"'
        in script
    )
    assert "主链路执行超时，被保护性终止" in script


def _init_git_repo(root: Path, *tracked_files: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "runtime tests"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "add", *(str(path.relative_to(root)) for path in tracked_files)],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "initial runtime"],
        cwd=root,
        check=True,
    )


def _write_overlay_manifest(root: Path, relative_files: tuple[str, ...]) -> Path:
    manifest_path = root / ".state" / "runtime-sync-overlay.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    file_hashes = {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in relative_files
    }
    manifest_path.write_text(
        json.dumps(
            {"managed_files": list(relative_files), "file_hashes": file_hashes},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _build_sync_runtime(
    tmp_path: Path, *, runner_name: str = "runner.sh"
) -> tuple[Path, Path]:
    root = tmp_path / "runtime"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    sync_script = scripts_dir / "server_sync_and_run.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "server_sync_and_run.sh", sync_script)
    runner = root / runner_name
    runner.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "${RUNNER_MARKER:?}" > "${MARKER_PATH:?}"\nexit "${RUNNER_EXIT:-0}"\n',
        encoding="utf-8",
    )
    runner.chmod(0o755)
    _init_git_repo(root, sync_script, runner)
    return root, runner


def _run_server_sync(
    root: Path,
    *,
    runner: Path,
    result_path: Path,
    marker_path: Path,
    runner_exit: int = 0,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "AQSP_PROJECT_ROOT": str(root),
        "AQSP_RUNNER_SCRIPT": str(runner.relative_to(root)),
        "AQSP_SYNC_RESULT_FILE": str(result_path),
        "AQSP_GIT_REMOTE": "origin",
        "MARKER_PATH": str(marker_path),
        "RUNNER_MARKER": "ran",
        "RUNNER_EXIT": str(runner_exit),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(root / "scripts" / "server_sync_and_run.sh")],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_server_sync_runs_verified_runtime_overlay_without_overwriting_data(
    tmp_path: Path,
) -> None:
    root, runner = _build_sync_runtime(tmp_path)
    runner.write_text(
        runner.read_text(encoding="utf-8").replace("${RUNNER_MARKER:?}", "overlay-ran"),
        encoding="utf-8",
    )
    private_data = root / "data" / "server-owned.db"
    private_data.parent.mkdir()
    private_data.write_text("server data", encoding="utf-8")
    manifest = _write_overlay_manifest(root, ("runner.sh",))
    result_path = root / ".state" / "result.env"
    marker_path = root / "marker.txt"

    result = _run_server_sync(
        root,
        runner=runner,
        result_path=result_path,
        marker_path=marker_path,
        extra_env={"AQSP_RUNTIME_OVERLAY_MANIFEST": str(manifest)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert marker_path.read_text(encoding="utf-8").strip() == "overlay-ran"
    assert private_data.read_text(encoding="utf-8") == "server data"
    result_text = result_path.read_text(encoding="utf-8")
    assert "status=completed" in result_text
    assert "exit_code=0" in result_text
    assert "跳过 Git fetch/pull" in result.stdout


def test_server_sync_blocks_unknown_dirty_even_when_stable_override_is_set(
    tmp_path: Path,
) -> None:
    root, runner = _build_sync_runtime(tmp_path)
    runner.write_text(
        runner.read_text(encoding="utf-8") + "# unknown drift\n", encoding="utf-8"
    )
    result_path = root / ".state" / "result.env"
    marker_path = root / "marker.txt"

    result = _run_server_sync(
        root,
        runner=runner,
        result_path=result_path,
        marker_path=marker_path,
        extra_env={"AQSP_ALLOW_STABLE_DIRTY_RUN": "true"},
    )

    assert result.returncode == 1
    assert not marker_path.exists()
    assert "status=blocked_dirty" in result_path.read_text(encoding="utf-8")


def test_server_sync_propagates_runner_failure_and_records_exit_code(
    tmp_path: Path,
) -> None:
    root, runner = _build_sync_runtime(tmp_path)
    runner.write_text(
        runner.read_text(encoding="utf-8") + "# managed runtime overlay\n",
        encoding="utf-8",
    )
    manifest = _write_overlay_manifest(root, ("runner.sh",))
    result_path = root / ".state" / "result.env"
    marker_path = root / "marker.txt"

    result = _run_server_sync(
        root,
        runner=runner,
        result_path=result_path,
        marker_path=marker_path,
        runner_exit=17,
        extra_env={"AQSP_RUNTIME_OVERLAY_MANIFEST": str(manifest)},
    )

    assert result.returncode == 17
    assert marker_path.exists()
    result_text = result_path.read_text(encoding="utf-8")
    assert "status=failed" in result_text
    assert "exit_code=17" in result_text


def test_intraday_refresh_script_uses_isolated_outputs() -> None:
    script = (PROJECT_ROOT / "scripts" / "intraday_refresh.sh").read_text(
        encoding="utf-8"
    )

    assert 'export AQSP_RUN_TASK_ID="${AQSP_RUN_TASK_ID:-intraday}"' in script
    assert 'export AQSP_NOTIFY="false"' in script
    assert 'export AQSP_GATE_NOTIFY="false"' in script
    assert 'export AQSP_ENABLE_DEBATE="${AQSP_INTRADAY_ENABLE_DEBATE:-false}"' in script
    assert 'export AQSP_INTRADAY_DEBATE_ENABLE_LLM="${AQSP_INTRADAY_DEBATE_ENABLE_LLM:-false}"' in script
    assert 'export AQSP_DEBATE_ENABLE_LLM="${AQSP_INTRADAY_DEBATE_ENABLE_LLM}"' in script
    assert (
        'export AQSP_DISABLE_CIRCUIT_BREAKER="${AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER}"'
        in script
    )
    assert (
        'export AQSP_INTRADAY_FAST_SYMBOL_CACHE="${AQSP_INTRADAY_FAST_SYMBOL_CACHE:-${AQSP_RUNTIME_SYMBOL_CACHE:-data/walkforward_production_symbols.json}}"'
        in script
    )
    assert (
        'export AQSP_INTRADAY_FAST_SYMBOL_CSVS="${AQSP_INTRADAY_FAST_SYMBOL_CSVS:-reports/intraday_latest.csv,reports/latest.csv}"'
        in script
    )
    assert (
        'export AQSP_INTRADAY_FAST_FILL_CACHE="${AQSP_INTRADAY_FAST_FILL_CACHE:-true}"'
        in script
    )
    assert 'PRESET_AQSP_INTRADAY_SOURCE="${AQSP_INTRADAY_SOURCE:-}"' in script
    assert (
        'PRESET_AQSP_INTRADAY_MAX_UNIVERSE="${AQSP_INTRADAY_MAX_UNIVERSE:-${AQSP_MAX_UNIVERSE:-}}"'
        in script
    )
    assert script.index('PRESET_AQSP_INTRADAY_SOURCE="${AQSP_INTRADAY_SOURCE:-}"') < (
        script.index('source "${PROJECT_ROOT}/.env"')
    )
    assert 'INTRADAY_SOURCE="${AQSP_INTRADAY_SOURCE:-online_first}"' in script
    assert 'if [ -n "$PRESET_AQSP_INTRADAY_SOURCE" ]; then' in script
    assert 'INTRADAY_SOURCE="$PRESET_AQSP_INTRADAY_SOURCE"' in script
    assert 'if [ "$INTRADAY_SOURCE" = "eastmoney" ]' in script
    assert "盘中 eastmoney 单源分时不稳定，自动切换为 online_first" in script
    assert 'if [ -n "$PRESET_AQSP_INTRADAY_MAX_UNIVERSE" ]; then' in script
    assert 'INTRADAY_MAX_UNIVERSE="$PRESET_AQSP_INTRADAY_MAX_UNIVERSE"' in script
    assert (
        'INTRADAY_FAST_MAX_UNIVERSE="${AQSP_INTRADAY_FAST_MAX_UNIVERSE:-0}"' in script
    )
    assert (
        'INTRADAY_MAX_UNIVERSE="${AQSP_INTRADAY_MAX_UNIVERSE:-${INTRADAY_FAST_MAX_UNIVERSE}}"'
        in script
    )
    assert 'if ! [[ "$INTRADAY_MAX_UNIVERSE" =~ ^[0-9]+$ ]]' in script
    assert "盘中最大扫描范围无效" in script
    assert "使用完整实时流动性池" in script
    assert "AQSP_INTRADAY_ALLOW_HEAVY_UNIVERSE" in script
    assert "收紧为 120" in script
    assert "${AQSP_SOURCE:-eastmoney}" not in script
    assert 'INTRADAY_MODE="${AQSP_INTRADAY_MODE:-open}"' in script
    assert (
        'INTRADAY_RUN_TIMEOUT_SECONDS="${AQSP_INTRADAY_RUN_TIMEOUT_SECONDS:-420}"'
        in script
    )
    assert (
        'timeout --foreground --signal=TERM --kill-after=15s "${INTRADAY_RUN_TIMEOUT_SECONDS}s"'
        in script
    )
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
    assert (
        'DEBATE_RESULTS="$(resolve_path "${AQSP_DEBATE_RESULTS:-data/debate_results.jsonl}")"'
        in script
    )
    assert "launch_intraday_debate_backfill" in script
    assert "scripts/backfill_intraday_debate.py" in script
    assert "AQSP_INTRADAY_DEBATE_BACKFILL_BACKGROUND:-false" in script
    assert "AQSP_INTRADAY_DEBATE_BACKFILL_FORCE:-true" in script
    assert "AQSP_INTRADAY_DEBATE_BACKFILL_MAX_CANDIDATES:-5" in script
    assert '"AQSP_ENABLE_DEBATE=true"' in script
    assert 'AQSP_INTRADAY_DEBATE_ENABLE_LLM:-false' in script
    assert '"AQSP_DEBATE_ENABLE_LLM=${AQSP_INTRADAY_DEBATE_ENABLE_LLM}"' in script
    assert "intraday-debate-backfill.lock" in script
    assert "refresh_home_dashboard_snapshot" in script
    assert 'if "${run_backfill[@]}" >>"$DEBATE_BACKFILL_LOG" 2>&1; then' in script
    assert 'log "Agent 讨论回填完成，首页快照已刷新"' in script
    assert "AQSP_HOME_SNAPSHOT_ENABLED:-true" in script
    assert "scripts/write_home_snapshot.py" in script
    assert 'return 1' in script
    assert "首页快照刷新失败，保留上一版快照" in script
    assert "首页快照刷新失败，继续保留上一版首页" in script
    assert script.index("launch_intraday_debate_backfill") < script.index(
        'if ! refresh_home_dashboard_snapshot; then'
    )
    assert "data/intraday_refresh_status.json" in script
    assert (
        'rm -f "$INTRADAY_LEDGER" "$INTRADAY_REPORT" "$INTRADAY_OUTPUT_CSV"'
        not in script
    )
    assert 'TMP_DIR="$(mktemp -d "${TMP_ROOT}/intraday-refresh.XXXXXX")"' in script
    assert 'TMP_INTRADAY_LEDGER="${TMP_DIR}/intraday_predictions.jsonl"' in script
    assert '--ledger "${TMP_INTRADAY_LEDGER}"' in script
    assert (
        'replace_intraday_artifact "$TMP_INTRADAY_LEDGER" "$INTRADAY_LEDGER" "盘中 ledger"'
        in script
    )
    assert 'write_intraday_status "failed" "盘中选股失败，保留上一版盘中产物"' in script
    assert (
        'write_intraday_status "completed" "盘中刷新完成；保护状态仅提示，不重写候选队列"'
        in script
    )
    assert '"candidate_count": candidate_count' in script
    assert '"actionable_count": actionable_count' in script
    assert '"paper_review_count": actionable_count' in script
    assert '"focus_count": focus_count' in script
    assert '"watch_count": watch_count' in script
    assert '"blocked_count": blocked_count' in script
    assert '"protection_blocked": protection_blocked' in script
    assert (
        'payload["reason"] = "盘中刷新完成；组合保护生效，仅保留观察展示"' not in script
    )
    assert 'PARTIAL_SNAPSHOT_USED="false"' in script
    assert (
            'write_intraday_status "running" "盘中刷新运行中；正在解析实时股票池" "0"'
        in script
    )
    assert 'AQSP_PROVISIONAL_REPORT="${INTRADAY_REPORT}"' in script
    assert 'AQSP_PROVISIONAL_OUTPUT_CSV="${INTRADAY_OUTPUT_CSV}"' in script
    assert 'QUALITY_GATE_TIMEOUT_SECONDS="${AQSP_INTRADAY_QUALITY_GATE_TIMEOUT_SECONDS:-30}"' in script
    assert '"${QUALITY_GATE_TIMEOUT_SECONDS}s"' in script
    assert "盘中后处理未完整结束" in script
    assert "partial_failed" in script
    assert "盘中快照已生成；后处理失败，保留快照但生产状态需复核" in script
    assert 'SCRIPT_EXIT_CODE="$RUN_EXIT_CODE"' in script
    assert "RUN_EXIT_CODE=0" not in script
    assert 'exit "$SCRIPT_EXIT_CODE"' in script
    assert "未生成${label}，保留上一版" in script
    assert "--skip-validation" in script
    assert '--benchmark-symbol "${INTRADAY_BENCHMARK_SYMBOL}"' in script
    assert "open_dashboard.py" not in script
    assert "today_shanghai" in script
    assert "今日非交易日，跳过盘中刷新" in script


def test_news_catalysts_script_defaults_to_report_only() -> None:
    script = (PROJECT_ROOT / "scripts" / "news_catalysts.sh").read_text(
        encoding="utf-8"
    )

    assert 'export AQSP_RUN_TASK_ID="news"' in script
    assert "AQSP_NEWS_NOTIFY:-false" in script
    assert "GLOBAL_NOTIFY" not in script
    assert 'export AQSP_NOTIFY="false"' in script
    assert 'export AQSP_GATE_NOTIFY="false"' in script
    assert script.index("已加载 .env 配置") < script.index('export AQSP_NOTIFY="false"')
    assert "NOTIFY_ARGS=(--notify)" in script
    assert '"${NOTIFY_ARGS[@]}"' in script
    assert "消息面雷达默认不推送手机通知" in script
    assert "AQSP_ALLOW_NON_TRADING_NEWS_NOTIFY" in script
    assert "今日非交易日，消息面雷达仅写报告" in script


def test_daily_run_script_loads_env_before_runtime_exports() -> None:
    script = (PROJECT_ROOT / "scripts" / "daily_run.sh").read_text(encoding="utf-8")

    assert 'source "${PROJECT_ROOT}/.env"' in script
    assert "已加载 .env 配置" in script
    assert script.index('source "${PROJECT_ROOT}/.env"') < script.index(
        'export AQSP_SOURCE="${AQSP_SOURCE:-auto}"'
    )
    assert "is_trading_day" in script
    assert "今日非交易日，跳过" in script


def test_install_server_cron_script_defaults_to_noop_migration_guard() -> None:
    script = (PROJECT_ROOT / "scripts" / "install_server_cron.sh").read_text(
        encoding="utf-8"
    )

    assert "AQSP_INSTALL_SYSTEM_CRON" in script
    assert "system cron install skipped" in script
    assert "生产定时统一使用宝塔计划任务" in script
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
    assert 'AQSP_RUN_TASK_ID="${AQSP_RUN_TASK_ID:-intraday}"' not in script
    assert 'AQSP_NOTIFY="false"' in script
    assert 'AQSP_GATE_NOTIFY="false"' in script
    assert 'AQSP_NOTIFY_TITLE_LABEL="${AQSP_NOTIFY_TITLE_LABEL:-午盘分析}"' in script
    assert 'AQSP_INTRADAY_NOTIFY="false"' in script
    assert 'AQSP_INTRADAY_ALLOW_NOTIFY="false"' in script
    assert "scripts/intraday_refresh.sh" in script
    assert 'source "$RUNTIME_PYTHON_HELPER"' in script
    assert 'PYTHON_BIN="$(aqsp_runtime_python "$PROJECT_ROOT")"' in script
    assert "today_shanghai" in script
    assert "今日非交易日，跳过午盘回看" in script


def test_scheduled_scripts_share_release_runtime_python_resolution() -> None:
    helper = (PROJECT_ROOT / "scripts" / "runtime_python.sh").read_text(
        encoding="utf-8"
    )
    assert "AQSP_RUNTIME_VENV_DIR" in helper
    assert "AQSP_VIBE_VENV_DIR" in helper
    assert "AQSP_INTRADAY_VENV_DIR" in helper
    assert ".venv-vibe-research/bin/python3" in helper
    assert "aqsp_require_runtime_python" in helper
    for name in (
        "bt_task.sh",
        "coldstart_daily.sh",
        "daily_pipeline.sh",
        "intraday_refresh.sh",
        "midday_refresh.sh",
        "news_catalysts.sh",
    ):
        script = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert 'source "$RUNTIME_PYTHON_HELPER"' in script, name
        assert "aqsp_runtime_python" in script, name


def test_news_script_preserves_valid_same_day_report_on_source_failure() -> None:
    script = (PROJECT_ROOT / "scripts" / "news_catalysts.sh").read_text(
        encoding="utf-8"
    )
    assert "has_usable_current_news()" in script
    assert "不覆盖有效证据" in script
    assert "current_count += 1" in script
    assert "if current_count == 0" in script
    assert 'source_status not in {"ok", "partial"}' in script
    assert 'raw_count = int(payload.get("raw_news_count", 0) or 0)' in script
    assert 'event_status != "no_high_impact"' in script


def test_bt_task_script_exposes_panel_safe_actions() -> None:
    script = (PROJECT_ROOT / "scripts" / "bt_task.sh").read_text(encoding="utf-8")

    assert "宝塔面板计划任务统一入口" in script
    assert 'ACTION="${1:-}"' in script
    assert 'if [ -z "$ACTION" ]' in script
    assert (
        "daily|intraday|midday|coldstart|walkforward-gate|monitor|news|status" in script
    )
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
    assert "server-git-sync.lock" in script
    assert "printf 'GIT_SYNC_LOCK_STARTED_AT=%q\\n'" in script
    assert "AQSP_GIT_SYNC_WAIT_SECONDS" in script
    assert "AQSP_GIT_LOCK_STALE_MINUTES" in script
    assert "managed_overlay_allows_dirty_state()" in script
    assert "AQSP_RUNTIME_OVERLAY_MANIFEST" in script
    assert "hashlib.sha256" in script
    assert "检测到受控 runtime overlay，跳过 Git 同步后继续运行" in script
    assert "本次跳过 Git fetch/pull；等待仓库回归 clean 后再恢复自动同步" in script
    assert "PIPESTATUS[0]" in script
    assert "同步任务未成功完成 status=" in script
    assert "run_synced_task_with_result || true" not in script
    assert "Git 同步进行中，等待释放" in script
    assert "等待 Git 同步锁超时" in script
    assert 'export AQSP_RUN_TASK_ID="intraday"' in script
    assert 'export AQSP_NOTIFY="false"' in script
    assert 'export AQSP_GATE_NOTIFY="false"' in script
    assert 'export AQSP_RUN_TASK_ID="midday"' in script
    assert "should_bridge_intraday_to_midday" in script
    assert "AQSP_INTRADAY_MIDDAY_BRIDGE" in script
    assert "midday-$(date +%Y-%m-%d).done" in script
    assert "scripts/server_sync_and_run.sh" in script
    assert "scripts/coldstart_daily.sh" in script
    assert "scripts/server_monitor.sh" in script
    assert script.index("monitor)") < script.index("scripts/server_monitor.sh")
    monitor_block = script[script.index("monitor)") : script.index("news)")]
    assert "sync_code_only" in monitor_block
    assert 'export AQSP_GATE_NOTIFY="false"' in monitor_block
    assert "scripts/news_catalysts.sh" in script
    assert script.index("news)") < script.index("scripts/news_catalysts.sh")
    assert script.index("skip_weekday_market_holiday") < script.index(
        "scripts/news_catalysts.sh"
    )
    news_block = script[script.index("\n    news)") : script.index("\n    status)")]
    assert 'export AQSP_GATE_NOTIFY="false"' in news_block
    assert "scripts/server_status.sh" in script
    assert "logs/bt" in script


def test_bt_task_propagates_intraday_runner_failure_to_cron(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    bt_script = scripts_dir / "bt_task.sh"
    sync_script = scripts_dir / "server_sync_and_run.sh"
    shutil.copy2(PROJECT_ROOT / "scripts" / "bt_task.sh", bt_script)
    shutil.copy2(PROJECT_ROOT / "scripts" / "runtime_python.sh", scripts_dir)
    shutil.copy2(PROJECT_ROOT / "scripts" / "server_sync_and_run.sh", sync_script)
    runner = scripts_dir / "intraday_refresh.sh"
    runner.write_text(
        "#!/usr/bin/env bash\nprintf 'intraday runner reached\\n'\nexit 23\n",
        encoding="utf-8",
    )
    runner.chmod(0o755)
    _init_git_repo(root, bt_script, sync_script, runner)
    runner.write_text(
        runner.read_text(encoding="utf-8") + "# managed overlay\n",
        encoding="utf-8",
    )
    manifest = _write_overlay_manifest(root, ("scripts/intraday_refresh.sh",))
    fake_python = tmp_path / "trading-day-python"
    fake_python.write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n", encoding="utf-8"
    )
    fake_python.chmod(0o755)
    venv_python = root / ".venv" / "bin" / "python3"
    venv_python.parent.mkdir(parents=True)
    shutil.copy2(fake_python, venv_python)
    venv_python.chmod(0o755)

    result = subprocess.run(
        ["bash", str(bt_script), "intraday"],
        cwd=root,
        env={
            **os.environ,
            "AQSP_PROJECT_ROOT": str(root),
            "AQSP_PYTHON": str(fake_python),
            "AQSP_RUNTIME_OVERLAY_MANIFEST": str(manifest),
            "AQSP_GIT_REMOTE": "origin",
            "AQSP_INTRADAY_MIDDAY_BRIDGE": "false",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 23, result.stdout + result.stderr
    log_files = list((root / "logs" / "bt").glob("bt-intraday-*.log"))
    assert len(log_files) == 1
    log_text = log_files[0].read_text(encoding="utf-8")
    assert "status=failed" in log_text
    assert "exit_code=23" in log_text

    monitor_script = (PROJECT_ROOT / "scripts" / "server_monitor.sh").read_text(
        encoding="utf-8"
    )
    assert "printf 'LOCK_STARTED_AT=%q\\n'" in monitor_script

    coldstart_script = (PROJECT_ROOT / "scripts" / "coldstart_daily.sh").read_text(
        encoding="utf-8"
    )
    assert "printf 'LOCK_STARTED_AT=%q\\n'" in coldstart_script

    daily_script = (PROJECT_ROOT / "scripts" / "daily_pipeline.sh").read_text(
        encoding="utf-8"
    )
    assert "周一至周五 18:00" in daily_script
    assert "run_data_cleanup()" in daily_script
    assert daily_script.index("run_data_cleanup") < daily_script.index("周末(周${DOW})")
    assert "today_shanghai" in daily_script
    assert "今日非交易日，跳过跑批" in daily_script
    assert 'ENFORCE_DAILY_WINDOW="${AQSP_ENFORCE_DAILY_WINDOW:-true}"' in daily_script
    assert 'DAILY_WINDOW_START_HM="${AQSP_DAILY_WINDOW_START_HM:-1730}"' in daily_script
    assert 'DAILY_WINDOW_END_HM="${AQSP_DAILY_WINDOW_END_HM:-2300}"' in daily_script
    assert "不在 daily 允许窗口" in daily_script
    assert 'PIPELINE_EXIT_CODE="${PIPESTATUS[0]}"' in daily_script
    assert 'PIPELINE_EXIT_CODE=${PIPELINE_EXIT_CODE:-$?}' not in daily_script
    assert 'RUN_TASK_ID="${AQSP_RUN_TASK_ID:-}"' in daily_script
    assert "缺少 AQSP_RUN_TASK_ID" in daily_script
    assert "拒绝运行 daily_pipeline" in daily_script
    assert "请统一走 scripts/bt_task.sh" in daily_script
    assert (
        'DAILY_NOTIFY_RESOLVED="${AQSP_DAILY_NOTIFY:-${AQSP_NOTIFY:-true}}"'
        in daily_script
    )
    assert "daily 通知已关闭" in daily_script


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
    assert "AQSP_NEWS_JSON_OUTPUT" in script
    assert '--json-output "$JSON_OUTPUT"' in script
    assert 'MAX_LLM_REVIEW_EVENTS="${AQSP_NEWS_MAX_LLM_REVIEW_EVENTS:-1}"' in script
    assert 'SOURCE_TIMEOUT_SECONDS="${AQSP_NEWS_SOURCE_TIMEOUT_SECONDS:-8}"' in script
    assert 'TASK_TIMEOUT_SECONDS="${AQSP_NEWS_TASK_TIMEOUT_SECONDS:-300}"' in script
    assert 'timeout "${TASK_TIMEOUT_SECONDS}"' in script
    assert "消息面雷达超时:" in script
    assert "write_failed_report()" in script
    assert 'write_failed_report "消息面雷达命令失败: exit=${NEWS_EXIT}"' in script
    assert "-m aqsp news-catalysts" in script
    assert "--notify" in script
    assert "--enable-llm-review" in script
    assert "无有效结论：消息源失败" in script
    assert "## ✅ 开盘怎么用" not in script


def test_news_catalysts_archives_events_by_published_date(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    python_bin = project_root / ".venv" / "bin" / "python3"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [ \"${1:-}\" = \"-m\" ]; then\n"
        "  json_path=\"\"; report_path=\"\"\n"
        "  previous=\"\"\n"
        "  for argument in \"$@\"; do\n"
        "    if [ \"$previous\" = \"--json-output\" ]; then json_path=\"$argument\"; fi\n"
        "    if [ \"$previous\" = \"--output\" ]; then report_path=\"$argument\"; fi\n"
        "    previous=\"$argument\"\n"
        "  done\n"
        "  python3 - \"$json_path\" \"$report_path\" <<'PY'\n"
        "import json\n"
        "import sys\n"
        "from datetime import timedelta\n"
        "from pathlib import Path\n"
        "from aqsp.core.time import now_shanghai, today_shanghai\n"
        "today = today_shanghai()\n"
        "yesterday = today - timedelta(days=1)\n"
        "payload = {\n"
        "    'date': today.isoformat(),\n"
        "    'generated_at': now_shanghai().isoformat(),\n"
        "    'source_status': 'ok',\n"
        "    'event_status': 'high_impact',\n"
        "    'raw_news_count': 2,\n"
        "    'events': [\n"
        "        {'title': '今日事件', 'published_at': f'{today.isoformat()}T09:00:00+08:00'},\n"
        "        {'title': '昨日事件', 'published_at': f'{yesterday.isoformat()}T15:30:00Z'},\n"
        "    ],\n"
        "}\n"
        "Path(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)\n"
        "Path(sys.argv[1]).write_text(json.dumps(payload), encoding='utf-8')\n"
        "Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)\n"
        "Path(sys.argv[2]).write_text('# fake report\\n', encoding='utf-8')\n"
        "PY\n"
        "  exit 0\n"
        "fi\n"
        "python3 \"$@\"\n",
        encoding="utf-8",
    )
    python_bin.chmod(0o755)
    output = project_root / "reports" / "news.md"
    latest = project_root / "data" / "runtime" / "latest.json"
    archive_dir = project_root / "data" / "runtime" / "news_archive"

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "news_catalysts.sh")],
        cwd=project_root,
        env={
            **os.environ,
            "AQSP_PROJECT_ROOT": str(project_root),
            "AQSP_PYTHON": str(python_bin),
            "AQSP_NEWS_OUTPUT": str(output),
            "AQSP_NEWS_JSON_OUTPUT": str(latest),
            "AQSP_NEWS_ARCHIVE_DIR": str(archive_dir),
            "AQSP_NEWS_NOTIFY": "false",
            "PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{PROJECT_ROOT}",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    today = date.today()
    yesterday = today - timedelta(days=1)
    run_payload = json.loads(
        (archive_dir / f"news-run-{today.isoformat()}.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(run_payload["events"]) == 2
    today = today.isoformat()
    yesterday = yesterday.isoformat()
    today_payload = json.loads(
        (archive_dir / f"news-{today}.json").read_text(encoding="utf-8")
    )
    yesterday_payload = json.loads(
        (archive_dir / f"news-{yesterday}.json").read_text(encoding="utf-8")
    )
    assert today_payload["archive_scope"] == "published_date"
    assert today_payload["run_date"] == today
    assert [event["title"] for event in today_payload["events"]] == ["今日事件"]
    assert [event["title"] for event in yesterday_payload["events"]] == ["昨日事件"]
    assert yesterday_payload["date"] == yesterday


def test_news_catalysts_failure_replaces_old_report_and_json(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    python_bin = project_root / ".venv" / "bin" / "python3"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${1:-}\" = \"-m\" ]; then exit 23; fi\n"
        "exec \"$(command -v python3)\" \"$@\"\n",
        encoding="utf-8",
    )
    python_bin.chmod(0o755)
    report_path = project_root / "reports" / "news_catalysts.md"
    json_path = project_root / "data" / "runtime" / "news_catalysts_latest.json"
    report_path.parent.mkdir(parents=True)
    json_path.parent.mkdir(parents=True)
    report_path.write_text("OLD NEWS REPORT\n", encoding="utf-8")
    json_path.write_text('{"source_status":"ok"}\n', encoding="utf-8")

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "news_catalysts.sh")],
        cwd=project_root,
        env={
            **os.environ,
            "AQSP_PROJECT_ROOT": str(project_root),
            "AQSP_NEWS_OUTPUT": str(report_path),
            "AQSP_NEWS_JSON_OUTPUT": str(json_path),
            "AQSP_NEWS_TASK_TIMEOUT_SECONDS": "5",
            "AQSP_NEWS_NOTIFY": "false",
            "PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{PROJECT_ROOT}",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 23, result.stdout + result.stderr
    report = report_path.read_text(encoding="utf-8")
    assert "OLD NEWS REPORT" not in report
    assert "状态: failed" in report
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["source_status"] == "failed"
    assert payload["event_status"] == "source_failed"
    assert "exit=23" in payload["warnings"][0]


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
    assert "sys.path.insert(0, candidate_str)" in script
    assert 'for candidate in (PROJECT_ROOT / "src", PROJECT_ROOT)' in script
    assert "now_shanghai" in script
    assert "datetime.now" not in script
    assert "shell=True" not in script
    assert "bt_task.sh" in script
    assert '"news"' in script
    assert "duplicate AQSP system cron entries" in script
    assert "production should use BT Panel only" in script
    assert "production schedule should be managed by BT Panel" in script
    assert "BT Panel logs" in script
    assert "pid-active" in script
    assert "runner=" in script
    assert "com.aqsp.daily.plist" not in script
    assert "AQSP_SCHEDULER_STRICT" in script


def test_production_walkforward_gate_wrapper_requires_full_market_raw_coverage() -> (
    None
):
    script = (
        PROJECT_ROOT / "scripts" / "run_production_walkforward_gate.py"
    ).read_text(encoding="utf-8")

    assert "from aqsp.walkforward_gate import MIN_PRODUCTION_GATE_SYMBOLS" in script
    assert "production gate requires raw sqlite db" in script
    assert "--pool" in script
    assert "all" in script
    assert "--grid-cscv" in script
    assert "--skip-pit-financials" in script
    assert "walkforward_production_status.json" in script
    assert "--status-path" in script
    assert "scripts/update_sqlite_daily.py" in script
    assert "--price-mode raw" in script
    assert "Backfill missing raw history first" in script
    assert "Only for a clean rebuild" in script
    assert "MIN_PRODUCTION_MEMORY_GIB" in script
    assert "--min-memory-gib" in script
    assert "blocked_resources" in script
    # 低内存正式回测不允许通过环境变量绕过资源门槛。
    assert "AQSP_ALLOW_LOW_MEMORY_WALKFORWARD" not in script


def test_recover_walkforward_incident_script_is_narrowly_scoped() -> None:
    script = (PROJECT_ROOT / "scripts" / "recover_walkforward_incident.sh").read_text(
        encoding="utf-8"
    )

    assert "STATUS_PATH=" in script
    assert "status_pids()" in script
    assert 'status not in {"running", "blocked_running", "timeout"}' in script
    assert "proc_cmdline()" in script
    assert "pid_matches_status_role()" in script
    assert "scripts/run_production_walkforward_gate.py" in script
    assert "-m aqsp walkforward" in script
    assert "--source sqlite_db" in script
    assert "--pool all" in script
    assert "--grid-cscv" in script
    assert "--symbols-file" in script
    assert "--repair-only" in script
    assert "systemctl restart aqsp-vibe-research.target" in script
    assert "aqsp-vibe-research.target 重启失败" in script
    assert "aqsp-vibe-research.target 重启后未处于 active" in script
    assert "AQSP_RECOVER_RESTART_RESEARCH" in script
    assert "daily_pipeline.py" not in script
    assert "intraday_refresh.sh" not in script
    assert "pgrep" not in script
    assert "pkill" not in script


def test_sync_and_recover_walkforward_incident_script_uses_runtime_overlay() -> None:
    script = (
        PROJECT_ROOT / "scripts" / "sync_and_recover_walkforward_incident.sh"
    ).read_text(encoding="utf-8")

    assert "scripts/sync_runtime_files_to_server.py" in script
    assert "scripts/run_production_walkforward_gate.py" in script
    assert "scripts/recover_walkforward_incident.sh" in script
    assert "scripts/sync_and_recover_walkforward_incident.sh" in script
    assert "src/aqsp/web/data_provider.py" in script
    assert "src/aqsp/web/dashboard.py" not in script
    assert "AQSP_RECOVER_SSH_CONNECT_TIMEOUT" in script
    assert "AQSP_RECOVER_RESULT_FILE" in script
    assert 'write_result "blocked_ssh"' in script
    assert 'write_result "completed"' in script
    assert 'ssh -o ConnectTimeout="${SSH_CONNECT_TIMEOUT}"' in script
    assert "bash scripts/recover_walkforward_incident.sh" in script
    assert "--verify-overlay" in script
    assert "https://lh.ifidy.cn/api/health" in script
    assert "_stcore/health" not in script


def test_recovery_script_does_not_restart_legacy_dashboard_by_default() -> None:
    script = (PROJECT_ROOT / "scripts" / "recover_walkforward_incident.sh").read_text(
        encoding="utf-8"
    )

    assert 'AQSP_RECOVER_RESTART_RESEARCH:-false' in script
    assert "canonical AQSP research target" in script
    assert "git reset" not in script
    assert "checkout --" not in script


def test_sync_and_recover_walkforward_incident_blocks_when_ssh_unavailable(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(
        "#!/usr/bin/env bash\necho 'fake ssh unavailable' >&2\nexit 255\n",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o755)
    result_file = tmp_path / "recovery.env"

    result = subprocess.run(
        [
            "bash",
            str(PROJECT_ROOT / "scripts" / "sync_and_recover_walkforward_incident.sh"),
        ],
        cwd=str(PROJECT_ROOT),
        env={
            **os.environ,
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_RECOVER_RESULT_FILE": str(result_file),
            "AQSP_RECOVER_SSH_CONNECT_TIMEOUT": "1",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert "[blocked] SSH unavailable" in result.stdout
    assert "status=blocked_ssh" in result_file.read_text(encoding="utf-8")
    assert "[sync]" not in result.stdout


def test_launchd_daily_wrapper_loads_env_before_daily_run() -> None:
    script = (
        PROJECT_ROOT / "scripts" / "launchd" / "aqsp_daily_run_wrapper.sh"
    ).read_text(encoding="utf-8")

    assert 'source "${PROJECT_ROOT}/.env"' in script
    assert script.index('source "${PROJECT_ROOT}/.env"') < script.index(
        'exec /bin/bash --login "$PROJECT_ROOT/scripts/daily_run.sh"'
    )


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
    assert "server-git-sync.lock" in script
    assert 'LOCK_INFO_FILE="${LOCK_FILE}/meta.env"' in script
    assert 'GIT_SYNC_LOCK_INFO_FILE="${GIT_SYNC_LOCK_FILE}/meta.env"' in script
    assert 'LOCK_STALE_MINUTES="${AQSP_LOCK_STALE_MINUTES:-360}"' in script
    assert 'GIT_SYNC_WAIT_SECONDS="${AQSP_GIT_SYNC_WAIT_SECONDS:-180}"' in script
    assert 'GIT_LOCK_STALE_MINUTES="${AQSP_GIT_LOCK_STALE_MINUTES:-30}"' in script
    assert "lock_is_stale" in script
    assert "git_sync_lock_is_stale" in script
    assert "检测到陈旧主锁，自动回收" in script
    assert "检测到陈旧 Git 同步锁，自动回收" in script
    assert "LOCK_RUNNER" in script
    assert "LOCK_STARTED_AT" in script
    assert "主链路仍在运行，本次任务正常跳过；这是互斥保护，不是失败" in script
    assert "Git 同步进行中，等待释放" in script
    assert "等待 Git 同步锁超时" in script
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
    assert "MONITOR_ARGS+=( --suppress-console-alert )" in script


def test_coldstart_daily_script_updates_db_then_runs_cli() -> None:
    script = (PROJECT_ROOT / "scripts" / "coldstart_daily.sh").read_text(
        encoding="utf-8"
    )

    assert "detect_sqlite_price_mode" in script
    assert (
        'RUNTIME_SQLITE_DB_PATH="$(resolve_path "${AQSP_SQLITE_DB_PATH:-A股量化分析数据/astocks_raw.db}")"'
        in script
    )
    assert 'dirname "$SQLITE_DB_PATH"' in script
    assert "scripts/update_sqlite_daily.py" in script
    assert "A股量化分析数据/update_daily.py" in script
    assert "AQSP_COLDSTART_UPDATE_SCRIPT" in script
    assert "AQSP_COLDSTART_PRICE_MODE" in script
    assert 'TARGET_DATE="${AQSP_COLDSTART_TARGET_DATE:-}"' in script
    assert 'RUN_AS_OF="$(' in script
    assert "is_trading_day(today)" in script
    assert "get_previous_trading_day(today)" in script
    assert 'log "运行数据日: ${RUN_AS_OF}"' in script
    assert 'COLDSTART_RUNTIME_SOURCE="${AQSP_COLDSTART_SOURCE:-online_first}"' in script
    assert "历史源生成候选" in script
    assert 'recommendations-${RUN_AS_OF}.md' in script
    assert 'recommendations-${RUN_AS_OF}.csv' in script
    assert 'log "筛选数据源: ${COLDSTART_RUNTIME_SOURCE}"' in script
    assert "A股量化分析数据/astocks_raw.db" in script
    assert "A股量化分析数据/astocks_qfq.db" not in script
    assert 'UPDATE_ARGS+=(--price-mode "$SQLITE_PRICE_MODE")' in script
    assert '[ -z "$TARGET_DATE" ] || [ "$TARGET_DATE" = "$DATE" ]' in script
    assert 'UPDATE_ARGS+=(--target-date "$RUN_AS_OF")' in script
    assert "冷启动 sqlite 路径与运行时不一致" in script
    assert "sqlite_db 运行时要求 coldstart 更新 raw 历史库" in script
    assert "AQSP_COLDSTART_UPDATE_SLEEP_SECONDS" in script
    assert "AQSP_COLDSTART_BACKFILL_START_DATE" in script
    assert "AQSP_COLDSTART_BACKFILL_FORCE" in script
    assert "AQSP_COLDSTART_FILL_HISTORY_GAPS" in script
    assert (
        'COLDSTART_MAX_UNIVERSE="${AQSP_COLDSTART_MAX_UNIVERSE:-${AQSP_MAX_UNIVERSE:-3000}}"'
        in script
    )
    assert '[ "$COLDSTART_MAX_UNIVERSE" -le 0 ]' in script
    assert 'COLDSTART_MAX_UNIVERSE="3000"' in script
    assert (
        'COLDSTART_MIN_TARGET_COVERAGE="${AQSP_COLDSTART_MIN_TARGET_COVERAGE:-3000}"'
        in script
    )
    assert "COUNT(DISTINCT ts_code) FROM daily_qfq WHERE trade_date = ?" in script
    assert "coldstart_target_coverage()" in script
    assert "coldstart_signal_progress()" in script
    assert "冷启动样本已达标" in script
    assert "冷启动后续: 样本门已关闭" in script
    assert "跳过重复历史库更新" in script
    assert "UPDATE_EXIT_CODE=${PIPESTATUS[0]}" in script
    assert "历史库更新存在尾部缺口但覆盖达标" in script
    assert "继续使用 ${COLDSTART_RUNTIME_SOURCE} 生成冷启动候选" in script
    assert "历史库更新失败且目标日覆盖不足" in script
    assert "--force-from-start" in script
    assert "--fill-history-gaps" in script
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
    assert "printf 'LOCK_RUNNER=%q\\n' \"scripts/coldstart_daily.sh\"" in script
    assert "printf 'LOCK_STARTED_AT=%q\\n'" in script
    assert 'UPDATE_ARGS=("${SQLITE_DB_PATH}")' in script
    assert '"${PYTHON_BIN}" -u "${UPDATE_SCRIPT}" "${UPDATE_ARGS[@]}"' in script
    assert '"${PYTHON_BIN}" -u -m aqsp.cli run' in script
    assert '--source "$COLDSTART_RUNTIME_SOURCE"' in script
    assert "--source sqlite_db" not in script
    assert '--max-universe "$COLDSTART_MAX_UNIVERSE"' in script
    assert '--as-of "$RUN_AS_OF"' in script
    assert '--benchmark-symbol ""' in script
    assert "CLI_EXIT_CODE=${PIPESTATUS[0]}" in script
    assert 'if [ "$CLI_EXIT_CODE" -eq 2 ]; then' in script
    assert "冷启动筛选被组合保护正常阻塞" in script
    assert "[ERROR] 冷启动筛选失败" in script
    assert "冷启动:" in script
    assert "today_shanghai" in script
    assert "今日非交易日，跳过冷启动任务" in script


def test_coldstart_daily_continues_when_update_fails_but_target_coverage_is_enough(
    tmp_path: Path,
) -> None:
    result = _run_fake_coldstart(
        tmp_path,
        coverage="1200,3200",
        update_exit=1,
        aqsp_source="sqlite_db",
        today="2026-07-07",
        weekday=2,
        is_trading_day=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "历史库更新存在尾部缺口但覆盖达标" in result.stdout
    assert "筛选数据源: online_first" in result.stdout
    cli_args = (tmp_path / "cli-args.txt").read_text(encoding="utf-8")
    assert "--source online_first" in cli_args
    assert "--source sqlite_db" not in cli_args


def test_coldstart_daily_skips_when_signal_days_already_completed(
    tmp_path: Path,
) -> None:
    result = _run_fake_coldstart(
        tmp_path,
        coverage=0,
        update_exit=42,
        coldstart_progress="34/30",
        today="2026-07-07",
        weekday=2,
        is_trading_day=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "冷启动样本已达标: 34/30" in result.stdout
    assert "冷启动后续: 样本门已关闭" in result.stdout
    assert "DSR/PBO" in result.stdout
    handoff = json.loads(
        (tmp_path / "project" / "data" / "coldstart_handoff_status.json").read_text(
            encoding="utf-8"
        )
    )
    assert handoff["status"] == "ready"
    assert handoff["progress"] == "34/30"
    assert handoff["next_step"] == "run_production_walkforward_gate"
    assert handoff["next_command"] == "bash scripts/bt_task.sh walkforward-gate"
    assert "组合保护按解除日单独判断" in handoff["blocker"]
    assert "fake sqlite update" not in result.stdout
    assert not (tmp_path / "cli-args.txt").exists()


def test_bt_task_exposes_walkforward_gate_as_controlled_action() -> None:
    script = (PROJECT_ROOT / "scripts" / "bt_task.sh").read_text(encoding="utf-8")

    assert "walkforward-gate" in script
    assert 'export AQSP_RUN_TASK_ID="walkforward_gate"' in script
    assert "AQSP_WALKFORWARD_GATE_NOTIFY:-false" in script
    assert 'SYNC_TASK_SKIPPED="false"' in script
    assert 'status" = "skipped_lock"' in script
    assert "不写入完成标记" in script
    assert (
        'run_python_script "${PROJECT_ROOT}/scripts/run_production_walkforward_gate.py" "${@:2}"'
        in script
    )


def test_coldstart_daily_stops_when_update_fails_and_target_coverage_is_too_low(
    tmp_path: Path,
) -> None:
    result = _run_fake_coldstart(
        tmp_path,
        coverage="1200,1200",
        update_exit=1,
        aqsp_source="sqlite_db",
        today="2026-07-07",
        weekday=2,
        is_trading_day=True,
    )

    assert result.returncode == 1
    assert "历史库更新失败且目标日覆盖不足" in result.stdout
    assert not (tmp_path / "cli-args.txt").exists()


def test_install_coldstart_cron_script_installs_single_daily_job() -> None:
    script = (PROJECT_ROOT / "scripts" / "install_coldstart_cron.sh").read_text(
        encoding="utf-8"
    )

    assert "AQSP_COLDSTART_CRON_SCHEDULE" in script
    assert "30 17 * * 1-5" in script
    assert "/scripts/coldstart_daily.sh" in script


def test_production_walkforward_gate_wrapper_suggests_gap_filling_raw_backfill() -> (
    None
):
    script = (
        PROJECT_ROOT / "scripts" / "run_production_walkforward_gate.py"
    ).read_text(encoding="utf-8")

    assert "from aqsp.walkforward_gate import MIN_PRODUCTION_GATE_SYMBOLS" in script
    assert "--pool" in script
    assert '"all"' in script
    assert "--fill-history-gaps --limit 0" in script
    assert "300-symbol run is only a smoke test" in script


def test_daily_run_defaults_to_full_market_universe() -> None:
    script = (PROJECT_ROOT / "scripts" / "daily_run.sh").read_text(encoding="utf-8")

    assert "AQSP_ALLOW_LEGACY_ENTRY" in script
    assert "Use scripts/bt_task.sh daily in production" in script
    assert 'RUN_TASK_ID="${AQSP_RUN_TASK_ID:-}"' in script
    assert "AQSP_RUN_TASK_ID=${RUN_TASK_ID:-<empty>}" in script
    assert "仅允许 daily" in script
    assert 'export AQSP_RUN_TASK_ID="daily"' in script
    assert "export -f run_daily_pipeline" in script
    assert "run_with_timeout \"$DAILY_TIMEOUT_SECONDS\" /bin/bash -c 'run_daily_pipeline'" in script
    assert 'ENFORCE_DAILY_WINDOW="${AQSP_ENFORCE_DAILY_WINDOW:-true}"' in script
    assert 'DAILY_WINDOW_START_HM="${AQSP_DAILY_WINDOW_START_HM:-1730}"' in script
    assert 'DAILY_WINDOW_END_HM="${AQSP_DAILY_WINDOW_END_HM:-2300}"' in script
    assert "不在 legacy daily_run 允许窗口" in script
    assert 'export AQSP_MAX_UNIVERSE="${AQSP_MAX_UNIVERSE:-0}"' in script
    assert '--max-universe "$AQSP_MAX_UNIVERSE"' in script


def test_deploy_setup_env_template_matches_production_readiness() -> None:
    script = (PROJECT_ROOT / "deploy" / "setup.sh").read_text(encoding="utf-8")

    assert "AQSP_MAX_UNIVERSE=0" in script
    assert "AQSP_SOURCE=sqlite_db" in script
    assert "AQSP_ALLOW_ONLINE_FALLBACK=false" in script
    assert "AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db" in script


def test_env_example_defaults_match_production_readiness() -> None:
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "AQSP_SYMBOLS=\n" in text
    assert "AQSP_MAX_UNIVERSE=0" in text
    assert "AQSP_SOURCE=sqlite_db" in text
    assert "AQSP_ALLOW_ONLINE_FALLBACK=false" in text
    assert "AQSP_SQLITE_DB_PATH=/opt/market-data/astocks_raw.db" in text
    assert "astocks_qfq.db" not in text


def test_launchd_daily_wrapper_explicitly_opts_into_legacy_entry() -> None:
    script = (
        PROJECT_ROOT / "scripts" / "launchd" / "aqsp_daily_run_wrapper.sh"
    ).read_text(encoding="utf-8")

    assert 'export AQSP_ALLOW_LEGACY_ENTRY="${AQSP_ALLOW_LEGACY_ENTRY:-1}"' in script
    assert 'export AQSP_RUN_TASK_ID="${AQSP_RUN_TASK_ID:-daily}"' in script


def test_launchd_strategy_wrappers_set_explicit_task_ids() -> None:
    morning = (
        PROJECT_ROOT / "scripts" / "launchd" / "aqsp_morning_wrapper.sh"
    ).read_text(encoding="utf-8")
    closing = (
        PROJECT_ROOT / "scripts" / "launchd" / "aqsp_closing_wrapper.sh"
    ).read_text(encoding="utf-8")

    assert 'export AQSP_RUN_TASK_ID="morning"' in morning
    assert 'export AQSP_RUN_TASK_ID="closing"' in closing


def test_parallel_entry_scripts_have_portable_bounded_lock_and_timeout() -> None:
    scripts = [
        PROJECT_ROOT / "scripts" / "launchd" / "aqsp_morning_wrapper.sh",
        PROJECT_ROOT / "scripts" / "launchd" / "aqsp_closing_wrapper.sh",
        PROJECT_ROOT / "scripts" / "daily_run.sh",
    ]

    for path in scripts:
        script = path.read_text(encoding="utf-8")
        assert "flock -n 9" in script
        assert "while ! mkdir " in script
        assert "trap cleanup" in script or "trap cleanup_lock" in script
        assert "run_with_timeout()" in script
        assert "kill -TERM" in script
        assert "kill -KILL" in script
        assert 'kill -TERM "-$child_pid"' in script
        assert 'kill -KILL "-$child_pid"' in script
        assert 'owner_pid=%s\\nowner_start_time=%s\\n' in script
        assert "lock_owner_is_alive" in script
        assert "recover_stale_lock" in script
        assert 'mv "$LOCK_' in script
        assert "LOCK_STALE_SECONDS" in script
        assert "MAX_" in script
        assert 'touch "$LOCK_FILE"' not in script


def test_parallel_entry_scripts_pass_bash_syntax_check() -> None:
    scripts = [
        PROJECT_ROOT / "scripts" / "launchd" / "aqsp_morning_wrapper.sh",
        PROJECT_ROOT / "scripts" / "launchd" / "aqsp_closing_wrapper.sh",
        PROJECT_ROOT / "scripts" / "daily_run.sh",
    ]

    for path in scripts:
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"
