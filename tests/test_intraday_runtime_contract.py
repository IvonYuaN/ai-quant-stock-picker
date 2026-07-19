from __future__ import annotations

import csv
import json
import os
import stat
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "intraday_refresh.sh"


def test_intraday_runtime_contract_uses_configured_benchmark_and_quality_gate() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'DEFAULT_INTRADAY_BENCHMARK_SYMBOL="000300"' in script
    assert "AQSP_INTRADAY_BENCHMARK_SYMBOL" in script
    assert (
        'INTRADAY_BENCHMARK_SYMBOL="${AQSP_INTRADAY_BENCHMARK_SYMBOL:-${AQSP_BENCHMARK_SYMBOL:-$DEFAULT_INTRADAY_BENCHMARK_SYMBOL}}"'
        in script
    )
    assert '--benchmark-symbol "${INTRADAY_BENCHMARK_SYMBOL}"' in script
    assert '--benchmark-symbol ""' not in script
    assert "quality_gate_action" in script
    assert "paper_review_eligible" in script
    assert '"benchmark_symbol": os.environ["INTRADAY_STATUS_BENCHMARK"]' in script
    assert '"task_id": os.environ["INTRADAY_STATUS_TASK_ID"]' in script
    assert 'payload["freshness"]' in script
    assert 'payload["quality_gate"]' in script
    assert "不改写确定性评分，不触发自动下单" in script
    assert 'export AQSP_PROVISIONAL_REPORT="${TMP_INTRADAY_REPORT}"' in script
    assert 'export AQSP_PROVISIONAL_OUTPUT_CSV="${TMP_INTRADAY_OUTPUT_CSV}"' in script
    assert "unset AQSP_PROVISIONAL_REPORT AQSP_PROVISIONAL_OUTPUT_CSV" in script
    assert 'export AQSP_ENABLE_DEBATE="${AQSP_INTRADAY_ENABLE_DEBATE:-false}"' in script
    assert (
        'export AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER="${AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER:-false}"'
        in script
    )
    assert (
        'export AQSP_DISABLE_CIRCUIT_BREAKER="${AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER}"'
        in script
    )
    assert (
        'export AQSP_DEBATE_ENABLE_LLM="${AQSP_INTRADAY_DEBATE_ENABLE_LLM}"' in script
    )
    assert '"AQSP_ENABLE_DEBATE=true"' in script
    assert (
        'QUALITY_GATE_TIMEOUT_SECONDS="${AQSP_INTRADAY_QUALITY_GATE_TIMEOUT_SECONDS:-30}"'
        in script
    )
    assert '"${QUALITY_GATE_TIMEOUT_SECONDS}s"' in script
    assert (
        'INTRADAY_NEWS_TASK_TIMEOUT_SECONDS="${AQSP_INTRADAY_NEWS_TASK_TIMEOUT_SECONDS:-20}"'
        in script
    )
    assert (
        'INTRADAY_NEWS_SOURCE_TIMEOUT_SECONDS="${AQSP_INTRADAY_NEWS_SOURCE_TIMEOUT_SECONDS:-6}"'
        in script
    )
    assert 'INTRADAY_NEWS_MAX_EVENTS="${AQSP_INTRADAY_NEWS_MAX_EVENTS:-3}"' in script
    assert 'INTRADAY_NEWS_MAX_SYMBOLS="${AQSP_INTRADAY_NEWS_MAX_SYMBOLS:-3}"' in script
    assert "count < limit && seen[$symbol_column]++ == 0" in script
    assert 'export AQSP_MARKET_CONTEXT_LIVE_SOURCE="false"' in script
    assert (
        'export AQSP_INTRADAY_CATALYST_FETCH_MODE="${AQSP_INTRADAY_CATALYST_FETCH_MODE:-thread}"'
        in script
    )
    assert 'export AQSP_CATALYST_TASK_CONTEXT="${AQSP_RUN_TASK_ID}"' in script
    assert (
        'AQSP_INTRADAY_CATALYST_FETCH_MODE="$AQSP_INTRADAY_CATALYST_FETCH_MODE"'
        in script
    )
    assert 'AQSP_CATALYST_TASK_CONTEXT="$AQSP_CATALYST_TASK_CONTEXT"' in script
    assert "refresh_realtime_cross_market_context" in script
    assert (
        'INTRADAY_NEWS_MAX_NEWS_AGE_DAYS="${AQSP_INTRADAY_NEWS_MAX_NEWS_AGE_DAYS:-0}"'
        in script
    )
    assert 'AQSP_NEWS_MAX_NEWS_AGE_DAYS="$INTRADAY_NEWS_MAX_NEWS_AGE_DAYS"' in script
    assert "load_catalyst_report_artifact" in script
    assert "消息脚本返回成功但当前消息产物无效或已过期" in script
    assert 'AQSP_NEWS_ENABLE_LLM_REVIEW="false"' in script
    assert 'AQSP_NEWS_NOTIFY="false"' in script
    assert 'if [ ! -f "$INTRADAY_NEWS_SCRIPT" ]; then' in script
    assert 'bash "$INTRADAY_NEWS_SCRIPT"' in script
    assert 'NEWS_CATALYST_STATUS="warning"' in script
    assert "继续首页快照" in script
    assert script.index("refresh_intraday_news_catalysts\n") > script.index(
        'replace_intraday_artifact "$TMP_INTRADAY_OUTPUT_CSV" "$INTRADAY_OUTPUT_CSV"'
    )
    assert 'LOCK_INFO_FILE="${LOCK_DIR}/meta.env"' in script
    assert "lock_is_stale" in script
    assert 'rm -f "$LOCK_INFO_FILE"' in script
    assert "历史源不得进入盘中主链" in script
    assert "DEBATE_BACKFILL_STALE_MINUTES" in script
    assert "DEBATE_LOCK_PID=%q" in script
    assert '"${BASHPID:-$$}"' in script
    assert "AQSP_INTRADAY_DEBATE_BACKFILL_BACKGROUND:-false" in script
    assert script.index(
        'export AQSP_PROVISIONAL_REPORT="${TMP_INTRADAY_REPORT}"'
    ) < script.index('if apply_intraday_quality_gate "$TMP_INTRADAY_OUTPUT_CSV"')


def _write_python_stub(path: Path, repo_root: Path, args_path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import csv
import json
import os
import subprocess
import sys
from pathlib import Path


def value(args, name):
    return args[args.index(name) + 1]


args = sys.argv[1:]
if args and Path(args[0]).name == "prepare_intraday_batch.py":
    calls_path = os.getenv("AQSP_TEST_PREPARE_CALLS", "")
    if calls_path:
        call = "commit" if "--commit" in args else "fail" if "--fail" in args else "select"
        with Path(calls_path).open("a", encoding="utf-8") as handle:
            handle.write(call + "\\n")
    if os.getenv("AQSP_TEST_PREPARE_BATCH") and "--commit" not in args:
        if os.getenv("AQSP_TEST_PREPARE_FAIL") and "--fail" not in args:
            raise SystemExit(3)
        print("600000,000001")
    raise SystemExit(0)

if args and Path(args[0]).name == "write_home_snapshot.py":
    marker = os.getenv("AQSP_TEST_HOME_SNAPSHOT_MARKER", "")
    if marker:
        Path(marker).write_text("refreshed", encoding="utf-8")
    raise SystemExit(0)

if args and Path(args[0]).name == "backfill_intraday_debate.py":
    marker = os.getenv("AQSP_TEST_BACKFILL_ENV", "")
    if marker:
        Path(marker).write_text(
            json.dumps(
                {
                    "enable_debate": os.getenv("AQSP_ENABLE_DEBATE"),
                    "enable_llm": os.getenv("AQSP_DEBATE_ENABLE_LLM"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    raise SystemExit(0)

if args[:1] == ["-"]:
    source = sys.stdin.read()
    if "is_trading_day" in source:
        raise SystemExit(0)
    if "load_catalyst_report_artifact" in source:
        raise SystemExit(0)
    if (
        os.getenv("AQSP_TEST_FORCE_QUALITY_GATE_FAILURE")
        and "AQSP_INTRADAY_QUALITY_GATE" in source
    ):
        Path(os.environ["INTRADAY_QUALITY_SUMMARY"]).write_text(
            json.dumps(
                {
                    "availability_status": "available",
                    "status": "blocked",
                    "freshness_status": "fresh",
                    "lag_days": 0,
                    "source_freshness_tier": "realtime",
                    "checked_count": 1,
                    "watch_count": 0,
                    "blocked_count": 1,
                    "downgraded_count": 1,
                    "max_allowed_lag_days": 1,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        raise SystemExit(1)
    result = subprocess.run(
        [sys.executable, "-", *args[1:]],
        input=source,
        text=True,
        env={**os.environ, "PYTHONPATH": os.environ["AQSP_TEST_REPO"] + "/src"},
    )
    raise SystemExit(result.returncode)

if args[:2] == ["-m", "aqsp"]:
    csv_path = Path(value(args, "--output-csv"))
    report_path = Path(value(args, "--report"))
    ledger_path = Path(value(args, "--ledger"))
    benchmark = value(args, "--benchmark-symbol")
    Path(os.environ["AQSP_TEST_ARGS"]).write_text(
        json.dumps(
            {
                "benchmark": benchmark,
                "source": value(args, "--source"),
                "max_universe": value(args, "--max-universe"),
                "enable_debate": os.getenv("AQSP_ENABLE_DEBATE"),
                "enable_online_factors": os.getenv("AQSP_ENABLE_ONLINE_FACTORS"),
                "enable_llm": os.getenv("AQSP_DEBATE_ENABLE_LLM"),
                "disable_circuit_breaker": os.getenv(
                    "AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER"
                ),
                "provisional_report": os.getenv("AQSP_PROVISIONAL_REPORT", ""),
                "provisional_csv": os.getenv("AQSP_PROVISIONAL_OUTPUT_CSV", ""),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fieldnames = [
        "symbol",
        "name",
        "date",
        "score",
        "rating",
        "candidate_review_priority",
        "data_quality_status",
        "data_quality_alerts",
        "run_data_lag_days",
        "run_requested_source",
        "run_actual_source",
        "run_source_coverage_tier",
        "run_source_local_status",
        "run_fallback_used",
        "run_workload",
        "run_data_latest_trade_date",
        "run_source_freshness_tier",
        "run_resolved_symbol_count",
        "run_fetched_frame_count",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "__RUN__",
                "name": "run_event",
                "run_data_lag_days": "0",
                "run_requested_source": "online_first",
                "run_actual_source": "eastmoney",
                "run_source_coverage_tier": "multi_dimensional",
                "run_source_local_status": "not_required",
                "run_fallback_used": "false",
                "run_workload": "live_short",
                "run_data_latest_trade_date": "2026-07-15",
                "run_source_freshness_tier": ""
                if os.getenv("AQSP_TEST_UNKNOWN_FRESHNESS")
                else "realtime",
                "run_resolved_symbol_count": os.getenv(
                    "AQSP_TEST_RESOLVED_COUNT", "2"
                ),
                "run_fetched_frame_count": os.getenv(
                    "AQSP_TEST_FETCHED_COUNT", "2"
                ),
            }
        )
        writer.writerow(
            {
                "symbol": "600000",
                "name": "质量观察样本",
                "date": "2026-07-13",
                "score": "88",
                "rating": "buy_candidate",
                "candidate_review_priority": "high",
                "data_quality_status": "watch",
                "data_quality_alerts": "quote warning",
            }
        )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(
            {
                "symbol": "600000",
                "status": "pending",
                "candidate_review_priority": "high",
                "data_quality_status": "watch",
            },
            ensure_ascii=False,
        )
        + "\\n",
        encoding="utf-8",
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# report\\n\\n## 1. 600000 质量观察样本\\n"
        "- 再看优先级/时机: 高优先级\\n",
        encoding="utf-8",
    )
    raise SystemExit(int(os.getenv("AQSP_TEST_CLI_EXIT_CODE", "0")))

raise SystemExit(0)
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_news_stub(path: Path, marker: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s|%s|%s|%s|%s|%s|%s|%s|%s\\n' "
        '"$AQSP_NEWS_TASK_TIMEOUT_SECONDS" '
        '"$AQSP_NEWS_SOURCE_TIMEOUT_SECONDS" '
        '"$AQSP_NEWS_MAX_EVENTS" '
        '"$AQSP_NEWS_ENABLE_LLM_REVIEW" '
        '"$AQSP_NEWS_NOTIFY" '
        '"$AQSP_NEWS_SYMBOLS" '
        '"$AQSP_NEWS_JSON_OUTPUT" '
        '"$AQSP_INTRADAY_CATALYST_FETCH_MODE" '
        '"$AQSP_CATALYST_TASK_CONTEXT" > '
        f'"{marker}"\n'
        'mkdir -p "$(dirname "$AQSP_NEWS_OUTPUT")" "$(dirname "$AQSP_NEWS_JSON_OUTPUT")"\n'
        'printf "# intraday news\\n" > "$AQSP_NEWS_OUTPUT"\n'
        'printf \'{"date":"%s","generated_at":"%s","source_status":"ok","event_status":"no_valid_news","events":[],"source_statuses":[],"region_statuses":[]}\\n\' "$(date +%Y-%m-%d)" "$(date +%Y-%m-%dT%H:%M:%S%z)" > "$AQSP_NEWS_JSON_OUTPUT"\n'
        'exit "${AQSP_TEST_NEWS_EXIT_CODE:-0}"\n',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_timeout_stub(path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        'if [ -n "${AQSP_TEST_TIMEOUT_SLEEP:-}" ]; then sleep "$AQSP_TEST_TIMEOUT_SLEEP"; fi\n'
        "shift\n"
        'while [ "$#" -gt 0 ] && [ "${1#-}" != "$1" ]; do shift; done\n'
        "shift\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _intraday_test_env(
    tmp_path: Path,
    args_path: Path,
    *,
    news_script: Path,
    home_marker: Path,
    news_exit_code: str = "0",
) -> dict[str, str]:
    return {
        **os.environ,
        "AQSP_PROJECT_ROOT": str(tmp_path),
        "AQSP_TEST_REPO": str(PROJECT_ROOT),
        "AQSP_TEST_ARGS": str(args_path),
        "AQSP_INTRADAY_NEWS_SCRIPT": str(news_script),
        "AQSP_TEST_NEWS_EXIT_CODE": news_exit_code,
        "AQSP_TEST_HOME_SNAPSHOT_MARKER": str(home_marker),
        "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
        "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
        "AQSP_HOME_SNAPSHOT_ENABLED": "true",
    }


def test_intraday_refresh_releases_lock_when_run_completes(tmp_path: Path) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    _write_timeout_stub(utility_bin / "timeout")
    args_path = tmp_path / "cli_args.json"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)

    env = os.environ.copy()
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
            "AQSP_HOME_SNAPSHOT_ENABLED": "false",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )

    first = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    second = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert "已有盘中刷新任务在运行，跳过" not in second.stdout
    assert not (tmp_path / ".locks" / "intraday-refresh.lock").exists()
    assert not list((tmp_path / ".tmp").glob("intraday-refresh.*"))


def test_intraday_runtime_quality_gate_downgrades_watch_candidate_and_records_state(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    timeout_stub = utility_bin / "timeout"
    timeout_stub.write_text(
        "#!/bin/sh\n"
        "shift\n"
        'while [ "$#" -gt 0 ] && [ "${1#-}" != "$1" ]; do shift; done\n'
        "shift\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    timeout_stub.chmod(timeout_stub.stat().st_mode | stat.S_IXUSR)
    args_path = tmp_path / "cli_args.json"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)

    env = os.environ.copy()
    env.pop("AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER", None)
    env.pop("AQSP_DISABLE_CIRCUIT_BREAKER", None)
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_INTRADAY_BENCHMARK_SYMBOL": "399001",
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
            "AQSP_HOME_SNAPSHOT_ENABLED": "false",
            "AQSP_INTRADAY_NOTIFY": "true",
            "AQSP_INTRADAY_ALLOW_NOTIFY": "true",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    cli_args = json.loads(args_path.read_text(encoding="utf-8"))
    assert cli_args["disable_circuit_breaker"] == "false"
    assert cli_args["benchmark"] == "399001"
    assert cli_args["provisional_report"].startswith(
        str(tmp_path / ".tmp" / "intraday-refresh.")
    )
    assert cli_args["provisional_report"].endswith("/intraday_latest.md")
    assert cli_args["provisional_csv"].startswith(
        str(tmp_path / ".tmp" / "intraday-refresh.")
    )
    assert cli_args["provisional_csv"].endswith("/intraday_latest.csv")

    output_csv = tmp_path / "reports" / "intraday_latest.csv"
    with output_csv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    candidate = next(row for row in rows if row["symbol"] == "600000")
    assert candidate["quality_gate_action"] == "observe"
    assert candidate["paper_review_eligible"] == "false"
    assert candidate["candidate_review_priority"] == "low"
    assert candidate["candidate_status"] == "质量观察"
    assert candidate["score"] == "88"
    assert candidate["rating"] == "buy_candidate"

    ledger_rows = [
        json.loads(line)
        for line in (tmp_path / "data" / "intraday_predictions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert ledger_rows[0]["quality_gate_action"] == "observe"
    assert ledger_rows[0]["candidate_review_priority"] == "low"

    status = json.loads(
        (tmp_path / "data" / "intraday_refresh_status.json").read_text(encoding="utf-8")
    )
    assert status["benchmark_symbol"] == "399001"
    assert status["task_id"] == "intraday"
    assert status["freshness"]["status"] == "fresh"
    assert status["freshness"]["lag_days"] == 0
    assert status["quality_gate"]["status"] == "degraded"
    assert status["quality_gate"]["watch_count"] == 1
    assert status["quality_gate"]["blocked_count"] == 0
    assert status["quality_gate"]["provenance_status"] == "verified"
    assert status["provenance"]["actual_source"] == "eastmoney"
    assert status["provenance"]["workload"] == "live_short"
    assert status["source_provenance"] == {
        "status": "available",
        "requested_source": "online_first",
        "actual_source": "eastmoney",
        "source_freshness_tier": "realtime",
        "source_coverage_tier": "multi_dimensional",
        "source_local_status": "not_required",
        "fallback_used": False,
        "latest_trade_date": "2026-07-15",
        "lag_days": 0,
    }


def test_intraday_runtime_production_env_disables_main_debate_and_falls_back_from_eastmoney(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    timeout_stub = utility_bin / "timeout"
    timeout_stub.write_text(
        "#!/bin/sh\n"
        "shift\n"
        'while [ "$#" -gt 0 ] && [ "${1#-}" != "$1" ]; do shift; done\n'
        "shift\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    timeout_stub.chmod(timeout_stub.stat().st_mode | stat.S_IXUSR)
    args_path = tmp_path / "cli_args.json"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)
    (tmp_path / ".env").write_text(
        "AQSP_ENABLE_DEBATE=true\n"
        "AQSP_DEBATE_ENABLE_LLM=true\n"
        "AQSP_INTRADAY_SOURCE=eastmoney\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_INTRADAY_SOURCE": "eastmoney",
            "AQSP_INTRADAY_DISABLE_CIRCUIT_BREAKER": "true",
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
            "AQSP_HOME_SNAPSHOT_ENABLED": "false",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    cli_args = json.loads(args_path.read_text(encoding="utf-8"))
    assert cli_args["disable_circuit_breaker"] == "true"
    assert cli_args["source"] == "online_first"
    assert cli_args["enable_debate"] == "false"
    assert cli_args["enable_llm"] == "false"
    assert "自动切换为 online_first" in result.stdout


def test_intraday_runtime_refreshes_news_after_candidates_and_keeps_home_snapshot(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    _write_timeout_stub(utility_bin / "timeout")
    args_path = tmp_path / "cli_args.json"
    home_marker = tmp_path / "home_snapshot.refreshed"
    news_marker = tmp_path / "news.args"
    news_script = tmp_path / "scripts" / "news_catalysts.sh"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)
    _write_news_stub(news_script, news_marker)

    env = _intraday_test_env(
        tmp_path,
        args_path,
        news_script=news_script,
        home_marker=home_marker,
    )
    env["PATH"] = f"{utility_bin}:{os.environ['PATH']}"
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert home_marker.read_text(encoding="utf-8") == "refreshed"
    news_args = news_marker.read_text(encoding="utf-8").strip().split("|")
    assert news_args[:6] == ["20", "6", "3", "false", "false", "600000"]
    assert news_args[7:] == ["thread", "intraday"]
    assert (tmp_path / "reports" / "news_catalysts.md").exists()
    assert (tmp_path / "data" / "runtime" / "news_catalysts_latest.json").exists()
    status = json.loads(
        (tmp_path / "data" / "intraday_refresh_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "completed"
    assert status["news_catalysts"]["status"] == "refreshed"
    assert status["execution"]["catalyst_fetch_mode"] == "thread"
    assert status["execution"]["runner_timeout_seconds"] == 420
    assert status["execution"]["news_task_timeout_seconds"] == 20
    assert status["execution"]["duration_seconds"] >= 0


def test_intraday_runtime_passes_explicit_thread_catalyst_mode_to_sidecar(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    _write_timeout_stub(utility_bin / "timeout")
    args_path = tmp_path / "cli_args.json"
    home_marker = tmp_path / "home_snapshot.refreshed"
    news_marker = tmp_path / "news.args"
    news_script = tmp_path / "scripts" / "news_catalysts.sh"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)
    _write_news_stub(news_script, news_marker)

    env = _intraday_test_env(
        tmp_path,
        args_path,
        news_script=news_script,
        home_marker=home_marker,
    )
    env["AQSP_INTRADAY_CATALYST_FETCH_MODE"] = "thread"
    env["PATH"] = f"{utility_bin}:{os.environ['PATH']}"
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    news_args = news_marker.read_text(encoding="utf-8").strip().split("|")
    assert news_args[7:] == ["thread", "intraday"]
    status = json.loads(
        (tmp_path / "data" / "intraday_refresh_status.json").read_text(encoding="utf-8")
    )
    assert status["execution"]["catalyst_fetch_mode"] == "thread"
    assert status["execution"]["timed_out"] is False


def test_intraday_runtime_news_failure_warns_without_blocking_candidates_or_home(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    _write_timeout_stub(utility_bin / "timeout")
    args_path = tmp_path / "cli_args.json"
    home_marker = tmp_path / "home_snapshot.refreshed"
    news_marker = tmp_path / "news.args"
    news_script = tmp_path / "scripts" / "news_catalysts.sh"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)
    _write_news_stub(news_script, news_marker)

    env = _intraday_test_env(
        tmp_path,
        args_path,
        news_script=news_script,
        home_marker=home_marker,
        news_exit_code="23",
    )
    env["PATH"] = f"{utility_bin}:{os.environ['PATH']}"
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[WARN] 消息面刷新失败" in result.stdout
    assert home_marker.read_text(encoding="utf-8") == "refreshed"
    assert (tmp_path / "reports" / "intraday_latest.csv").exists()
    status = json.loads(
        (tmp_path / "data" / "intraday_refresh_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "partial_failed"
    assert status["news_catalysts"]["status"] == "warning"
    assert status["news_catalysts"]["exit_code"] == 23


def test_intraday_runtime_backfill_enables_advisory_runtime_after_main_chain_is_disabled(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    timeout_stub = utility_bin / "timeout"
    timeout_stub.write_text(
        "#!/bin/sh\n"
        "shift\n"
        'while [ "$#" -gt 0 ] && [ "${1#-}" != "$1" ]; do shift; done\n'
        "shift\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    timeout_stub.chmod(timeout_stub.stat().st_mode | stat.S_IXUSR)
    args_path = tmp_path / "cli_args.json"
    backfill_env_path = tmp_path / "backfill_env.json"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)

    env = os.environ.copy()
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_TEST_BACKFILL_ENV": str(backfill_env_path),
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "true",
            "AQSP_INTRADAY_DEBATE_BACKFILL_BACKGROUND": "false",
            "AQSP_HOME_SNAPSHOT_ENABLED": "false",
            "AQSP_ENABLE_DEBATE": "true",
            "AQSP_DEBATE_ENABLE_LLM": "true",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    cli_args = json.loads(args_path.read_text(encoding="utf-8"))
    backfill_env = json.loads(backfill_env_path.read_text(encoding="utf-8"))
    assert cli_args["enable_debate"] == "false"
    assert cli_args["enable_llm"] == "false"
    assert backfill_env == {"enable_debate": "true", "enable_llm": "false"}


def test_intraday_runtime_promotes_fresh_failed_gate_as_observation_only_and_refreshes_home(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    timeout_stub = utility_bin / "timeout"
    timeout_stub.write_text(
        "#!/bin/sh\n"
        "shift\n"
        'while [ "$#" -gt 0 ] && [ "${1#-}" != "$1" ]; do shift; done\n'
        "shift\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    timeout_stub.chmod(timeout_stub.stat().st_mode | stat.S_IXUSR)
    args_path = tmp_path / "cli_args.json"
    home_marker = tmp_path / "home_snapshot.refreshed"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)

    old_csv = tmp_path / "reports" / "intraday_latest.csv"
    old_report = tmp_path / "reports" / "intraday_latest.md"
    old_ledger = tmp_path / "data" / "intraday_predictions.jsonl"
    old_csv.parent.mkdir(parents=True)
    old_ledger.parent.mkdir(parents=True)
    old_csv.write_text("symbol\nOLD\n", encoding="utf-8")
    old_report.write_text("old snapshot\n", encoding="utf-8")
    old_ledger.write_text('{"symbol":"OLD"}\n', encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_TEST_FORCE_QUALITY_GATE_FAILURE": "true",
            "AQSP_TEST_HOME_SNAPSHOT_MARKER": str(home_marker),
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert home_marker.read_text(encoding="utf-8") == "refreshed"
    status = json.loads(
        (tmp_path / "data" / "intraday_refresh_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "partial_failed"
    assert status["observation_only"] is True
    assert status["freshness"]["status"] == "fresh"
    assert status["quality_gate"]["status"] == "blocked"

    with old_csv.open(encoding="utf-8", newline="") as handle:
        candidate = next(
            row for row in csv.DictReader(handle) if row["symbol"] == "600000"
        )
    assert candidate["intraday_artifact_mode"] == "observation_only"
    assert candidate["observation_only"] == "true"
    assert candidate["paper_review_eligible"] == "false"
    assert candidate["quality_gate_action"] == "observe"
    assert candidate["candidate_status"] == "盘中观察"
    assert "OLD" not in old_csv.read_text(encoding="utf-8")
    assert "observation_only: true" in old_report.read_text(encoding="utf-8")
    ledger_row = json.loads(old_ledger.read_text(encoding="utf-8").splitlines()[0])
    assert ledger_row["observation_only"] is True
    assert ledger_row["paper_review_eligible"] is False


def test_intraday_runtime_timeout_uses_command_exit_code_and_finishes_partial_observation(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    timeout_stub = utility_bin / "timeout"
    timeout_stub.write_text(
        "#!/bin/sh\n"
        "shift\n"
        'while [ "$#" -gt 0 ] && [ "${1#-}" != "$1" ]; do shift; done\n'
        "shift\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    timeout_stub.chmod(timeout_stub.stat().st_mode | stat.S_IXUSR)
    args_path = tmp_path / "cli_args.json"
    home_marker = tmp_path / "home_snapshot.refreshed"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)

    env = os.environ.copy()
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_TEST_CLI_EXIT_CODE": "124",
            "AQSP_TEST_HOME_SNAPSHOT_MARKER": str(home_marker),
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 124, result.stdout + result.stderr
    assert home_marker.read_text(encoding="utf-8") == "refreshed"
    status = json.loads(
        (tmp_path / "data" / "intraday_refresh_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "partial_failed"
    assert status["exit_code"] == 124
    assert status["observation_only"] is True
    assert status["quality_gate"]["status"] == "degraded"
    assert "timeout" in result.stdout
    assert status["blocked_count"] == 0


def test_intraday_runtime_classifies_kill_after_137_at_deadline_as_timeout(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    _write_timeout_stub(utility_bin / "timeout")
    args_path = tmp_path / "cli_args.json"
    home_marker = tmp_path / "home_snapshot.refreshed"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)

    env = os.environ.copy()
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_TEST_CLI_EXIT_CODE": "137",
            "AQSP_TEST_TIMEOUT_SLEEP": "1",
            "AQSP_INTRADAY_RUN_TIMEOUT_SECONDS": "1",
            "AQSP_TEST_HOME_SNAPSHOT_MARKER": str(home_marker),
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 137, result.stdout + result.stderr
    status = json.loads(
        (tmp_path / "data" / "intraday_refresh_status.json").read_text(encoding="utf-8")
    )
    assert status["execution"]["timed_out"] is True
    assert status["execution"]["resource_killed"] is False
    assert status["execution"]["exit_class"] == "timeout_kill_after"
    assert "按超时处理" in result.stdout


def test_intraday_batch_mode_ignores_env_limited_universe_by_default() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    batch_guard = script.index("盘中批次轮转忽略配置最大股票数")
    assert 'is_truthy "$INTRADAY_BATCH_SCAN"' in script[batch_guard - 400 : batch_guard]
    assert 'INTRADAY_MAX_UNIVERSE="0"' in script[batch_guard : batch_guard + 600]
    assert (
        "AQSP_INTRADAY_ALLOW_LIMITED_UNIVERSE"
        in script[batch_guard - 200 : batch_guard + 600]
    )
    assert 'export AQSP_ENABLE_ONLINE_FACTORS="false"' in script
    assert (
        'export AQSP_INTRADAY_FULL_UNIVERSE="${AQSP_INTRADAY_FULL_UNIVERSE:-true}"'
        in script
    )


def test_intraday_full_pool_does_not_inherit_daily_liquidity_cap() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'INTRADAY_MIN_AVG_AMOUNT="${AQSP_INTRADAY_MIN_AVG_AMOUNT:-0}"' in script
    assert "AQSP_MIN_AVG_AMOUNT:-50000000" not in script


def test_intraday_batch_mode_does_not_let_aqsp_max_universe_40_bypass_rotation(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    _write_timeout_stub(utility_bin / "timeout")
    args_path = tmp_path / "cli_args.json"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "prepare_intraday_batch.py").write_text(
        "# test stub\n", encoding="utf-8"
    )

    env = os.environ.copy()
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_TEST_PREPARE_BATCH": "true",
            "AQSP_MAX_UNIVERSE": "40",
            "AQSP_INTRADAY_BATCH_SIZE": "2",
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
            "AQSP_HOME_SNAPSHOT_ENABLED": "false",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    cli_args = json.loads(args_path.read_text(encoding="utf-8"))
    assert cli_args["max_universe"] == "0"
    assert "忽略配置最大股票数 40" in result.stdout


def test_intraday_batch_resolution_failure_records_cursor_failure(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    _write_timeout_stub(utility_bin / "timeout")
    args_path = tmp_path / "cli_args.json"
    calls_path = tmp_path / "prepare.calls"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "prepare_intraday_batch.py").write_text(
        "# test stub\n", encoding="utf-8"
    )

    env = os.environ.copy()
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_TEST_PREPARE_BATCH": "true",
            "AQSP_TEST_PREPARE_FAIL": "true",
            "AQSP_TEST_PREPARE_CALLS": str(calls_path),
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
            "AQSP_HOME_SNAPSHOT_ENABLED": "false",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert calls_path.read_text(encoding="utf-8").splitlines() == ["select", "fail"]
    status = json.loads(
        (tmp_path / "data" / "intraday_refresh_status.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["status"] == "failed"
    assert "universe_resolution_failed" in status["reason"]
    assert status["execution"]["batch_failure_recorded"] == "true"


def test_intraday_batch_does_not_commit_when_output_metadata_is_partial(
    tmp_path: Path,
) -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "validate_intraday_batch_output()" in script
    check_start = script.index("validate_intraday_batch_output()")
    check_end = script.index("debate_backfill_lock_age_minutes()")
    check = script[check_start:check_end]
    assert "run_resolved_symbol_count" in check
    assert "run_fetched_frame_count" in check
    assert "resolved/fetched/skipped" in check
    assert "minimum = max(1, math.ceil(expected * ratio))" in check
    assert "fetched < minimum" in check
    assert "fetched != resolved" not in check
    assert "拒绝提交 cursor" in check
    commit_start = script.index('AQSP_INTRADAY_BATCH_SCANNED="$INTRADAY_BATCH_SCANNED"')
    assert (
        script.index("validate_intraday_batch_output", commit_start - 500)
        < commit_start
    )
    assert 'BATCH_FAILURE_REASON="intraday_batch_output_incomplete"' in script


def test_intraday_batch_validates_exact_final_batch_and_only_fresh_output() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    check_start = script.index("validate_intraday_batch_output()")
    check_end = script.index("debate_backfill_lock_age_minutes()")
    check = script[check_start:check_end]

    assert 'local expected_count="${INTRADAY_BATCH_EXPECTED_COUNT:-$INTRADAY_BATCH_SIZE}"' in check
    assert 'local batch_output_path="$TMP_INTRADAY_OUTPUT_CSV"' in check
    assert "public CSV is the previous successful run" in check
    assert 'INTRADAY_BATCH_EXPECTED_COUNT="${#INTRADAY_BATCH_SYMBOL_ARRAY[@]}"' in script


def test_intraday_batch_rewrites_completed_status_after_cursor_commit() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    commit_block_start = script.rindex(
        'if [ "$INTRADAY_BATCH_ACTIVE" = "true" ] &&'
    )
    commit_block_end = script.index("else", commit_block_start)
    commit_block = script[commit_block_start:commit_block_end]

    assert 'BATCH_COMMITTED="true"' in commit_block
    assert 'write_intraday_status "completed"' in commit_block
    assert commit_block.index('BATCH_COMMITTED="true"') < commit_block.index(
        'write_intraday_status "completed"'
    )


def test_intraday_batch_status_records_data_coverage_and_skipped_symbols() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert '"resolved_count": resolved_count' in script
    assert '"fetched_count": fetched_count' in script
    assert '"skipped_count": int(batch_detail.get("skipped_count") or 0)' in script
    assert '"skipped_symbols": list(batch_detail.get("skipped_symbols") or [])' in script
    assert '"data_coverage_pct": data_coverage_pct' in script
    assert '"intraday_batch_commit_failed"' in script
    assert "不能保留 completed" in script


def test_intraday_batch_cursor_is_not_committed_after_137_timeout(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    _write_timeout_stub(utility_bin / "timeout")
    args_path = tmp_path / "cli_args.json"
    calls_path = tmp_path / "prepare.calls"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "prepare_intraday_batch.py").write_text(
        "# test stub\n", encoding="utf-8"
    )

    env = os.environ.copy()
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_TEST_PREPARE_BATCH": "true",
            "AQSP_TEST_PREPARE_CALLS": str(calls_path),
            "AQSP_TEST_CLI_EXIT_CODE": "137",
            "AQSP_TEST_TIMEOUT_SLEEP": "1",
            "AQSP_INTRADAY_RUN_TIMEOUT_SECONDS": "1",
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
            "AQSP_HOME_SNAPSHOT_ENABLED": "false",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 137, result.stdout + result.stderr
    calls = calls_path.read_text(encoding="utf-8").splitlines()
    assert calls[0] == "select"
    assert "fail" in calls
    assert "commit" not in calls


def test_intraday_runtime_timeout_records_batch_failure_before_status(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    _write_timeout_stub(utility_bin / "timeout")
    args_path = tmp_path / "cli_args.json"
    calls_path = tmp_path / "prepare.calls"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "prepare_intraday_batch.py").write_text(
        "# test stub\n", encoding="utf-8"
    )
    env = os.environ.copy()
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_TEST_PREPARE_BATCH": "true",
            "AQSP_TEST_PREPARE_CALLS": str(calls_path),
            "AQSP_TEST_CLI_EXIT_CODE": "124",
            "AQSP_INTRADAY_RUN_TIMEOUT_SECONDS": "1",
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
            "AQSP_HOME_SNAPSHOT_ENABLED": "false",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 124, result.stdout + result.stderr
    calls = calls_path.read_text(encoding="utf-8").splitlines()
    assert calls[:2] == ["select", "fail"]
    assert "commit" not in calls
    status = json.loads(
        (tmp_path / "data" / "intraday_refresh_status.json").read_text(encoding="utf-8")
    )
    assert status["execution"]["batch_failure_recorded"] == "true"


def test_intraday_runtime_quality_gate_blocks_unknown_freshness(
    tmp_path: Path,
) -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'freshness_status in {"unknown", "stale"}' in script
    assert "all_candidates_blocked = bool(candidate_rows)" in script
    assert "or all_candidates_blocked" in script
    assert 'reasons.append("freshness_unknown")' in script
    assert 'reasons.append("freshness_watch")' in script
    assert "AQSP_INTRADAY_DEBATE_BACKFILL_TIMEOUT_SECONDS:-120" in script
    assert '"${DEBATE_BACKFILL_TIMEOUT_SECONDS}s"' in script


def test_intraday_runtime_publishes_candidates_before_slow_sidecars() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    early_publish = script.index("盘中候选首页已先行刷新")
    market_context_start = script.index(
        "refresh_realtime_cross_market_context", early_publish
    )
    news_start = script.index("refresh_intraday_news_catalysts", early_publish)
    sidecar_start = script.index('if [ "$OBSERVATION_ONLY" = "true" ]', early_publish)
    final_publish = script.index(
        "if ! refresh_home_dashboard_snapshot; then", sidecar_start
    )

    assert (
        early_publish
        < market_context_start
        < news_start
        < sidecar_start
        < final_publish
    )


def test_intraday_runtime_unknown_freshness_keeps_previous_artifacts(
    tmp_path: Path,
) -> None:
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    utility_bin = tmp_path / "bin"
    utility_bin.mkdir()
    timeout_stub = utility_bin / "timeout"
    timeout_stub.write_text(
        "#!/bin/sh\n"
        "shift\n"
        'while [ "$#" -gt 0 ] && [ "${1#-}" != "$1" ]; do shift; done\n'
        "shift\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    timeout_stub.chmod(timeout_stub.stat().st_mode | stat.S_IXUSR)
    args_path = tmp_path / "cli_args.json"
    _write_python_stub(venv_bin / "python3", PROJECT_ROOT, args_path)
    env = os.environ.copy()
    env.update(
        {
            "AQSP_PROJECT_ROOT": str(tmp_path),
            "AQSP_TEST_REPO": str(PROJECT_ROOT),
            "AQSP_TEST_ARGS": str(args_path),
            "AQSP_TEST_UNKNOWN_FRESHNESS": "true",
            "AQSP_INTRADAY_REQUIRE_MARKET_HOURS": "false",
            "AQSP_INTRADAY_DEBATE_BACKFILL": "false",
            "AQSP_HOME_SNAPSHOT_ENABLED": "false",
            "PATH": f"{utility_bin}:{os.environ['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode != 0
    status = json.loads(
        (tmp_path / "data" / "intraday_refresh_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "failed"
    assert status["freshness"]["status"] == "unknown"
    assert status["quality_gate"]["status"] == "blocked"


def test_intraday_runtime_contract_preserves_empty_candidate_reason_in_outputs() -> (
    None
):
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert '"run_no_candidate_reason"' in script
    assert (
        '"no_candidate_reason": runtime_metadata.get("run_no_candidate_reason", "")'
        in script
    )
    assert '+ "；无候选原因: "' in script
