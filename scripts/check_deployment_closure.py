#!/usr/bin/env python3
"""Fail-closed deployment closure gate for AQSP releases.

This command is read-only.  It exists to prevent a pushed or locally tested
commit from being reported as deployed before GitHub CI, remote reachability,
and the public snapshot contract have all been verified.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date as CalendarDate
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aqsp.core.http import urlopen_no_macos_proxy  # noqa: E402
from aqsp.core.time import latest_completed_trading_day, today_shanghai  # noqa: E402
from scripts.remote_runtime_probe import build_report as build_probe_report  # noqa: E402


COMMAND_TIMEOUT_SECONDS = 20.0
DEFAULT_BASE_URL = "https://lh.ifidy.cn"
DEFAULT_SSH_TARGET = "aqsp-server"
MIN_SNAPSHOT_VARIANTS = 24
REQUIRED_TECHNICAL_METRICS = ("macd_hist", "kdj_j", "volume_ratio", "atr_pct")
_IGNORED_LOCAL_WORKTREE_PATHS = frozenset({".codex/project-profile.md"})


@dataclass(frozen=True)
class ClosureCheck:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "skipped"}


@dataclass(frozen=True)
class ClosureReport:
    status: str
    commit: str
    branch: str
    checks: tuple[ClosureCheck, ...]

    @property
    def ok(self) -> bool:
        return self.status == "verified"


def _run(root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        output = " ".join(
            str(item.decode(errors="replace") if isinstance(item, bytes) else item)
            for item in (exc.stdout, exc.stderr)
            if item
        )
        return subprocess.CompletedProcess(
            command,
            124,
            "",
            f"command timed out after {COMMAND_TIMEOUT_SECONDS:g}s: {output}".strip(),
        )


def _first_output(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout or result.stderr or "").strip()


def _local_commit(root: Path) -> tuple[str, ClosureCheck]:
    result = _run(root, ["git", "rev-parse", "HEAD"])
    commit = result.stdout.strip()
    if result.returncode != 0 or not commit:
        return "", ClosureCheck("local_commit", "failed", _first_output(result))
    return commit, ClosureCheck("local_commit", "ok", commit)


def _worktree_clean(root: Path) -> ClosureCheck:
    result = _run(root, ["git", "status", "--porcelain", "--untracked-files=all"])
    if result.returncode != 0:
        return ClosureCheck("worktree_clean", "failed", _first_output(result))
    dirty_lines = [line for line in result.stdout.splitlines() if line.strip()]
    release_input_lines = [
        line
        for line in dirty_lines
        if line[3:].strip() not in _IGNORED_LOCAL_WORKTREE_PATHS
    ]
    if release_input_lines:
        return ClosureCheck(
            "worktree_clean", "failed", "\n".join(release_input_lines)[:500]
        )
    if dirty_lines:
        return ClosureCheck(
            "worktree_clean",
            "ok",
            "release inputs clean; ignored local project profile",
        )
    return ClosureCheck(
        "worktree_clean", "ok", "tracked and untracked release inputs clean"
    )


def _remote_branch_commit(
    root: Path, *, remote: str, branch: str
) -> tuple[str, ClosureCheck]:
    result = _run(root, ["git", "ls-remote", remote, f"refs/heads/{branch}"])
    output = _first_output(result)
    remote_commit = result.stdout.split(maxsplit=1)[0] if result.stdout.strip() else ""
    if result.returncode != 0 or not remote_commit:
        return "", ClosureCheck("remote_commit", "failed", output)
    return remote_commit, ClosureCheck("remote_commit", "ok", remote_commit)


def _github_ci(root: Path, *, branch: str, commit: str, skip: bool) -> ClosureCheck:
    if skip:
        return ClosureCheck("github_ci", "skipped", "explicit --skip-github-ci")
    result = _run(
        root,
        [
            "gh",
            "run",
            "list",
            "--branch",
            branch,
            "--limit",
            "12",
            "--json",
            "databaseId,status,conclusion,headSha,workflowName",
        ],
    )
    output = _first_output(result)
    if result.returncode != 0:
        return ClosureCheck("github_ci", "failed", output)
    try:
        runs = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return ClosureCheck("github_ci", "failed", f"invalid gh json: {exc}")
    if not isinstance(runs, list):
        return ClosureCheck("github_ci", "failed", "gh output must be a list")
    matching = [
        item
        for item in runs
        if isinstance(item, dict)
        and str(item.get("workflowName") or "") == "CI"
        and str(item.get("headSha") or "") == commit
    ]
    if not matching:
        return ClosureCheck("github_ci", "failed", f"no CI run found for {commit}")
    run = matching[0]
    status = str(run.get("status") or "")
    conclusion = str(run.get("conclusion") or "")
    run_id = run.get("databaseId")
    if status != "completed" or conclusion != "success":
        return ClosureCheck(
            "github_ci",
            "failed",
            f"run={run_id} status={status or '-'} conclusion={conclusion or '-'}",
        )
    return ClosureCheck("github_ci", "ok", f"run={run_id} conclusion=success")


def _probe_remote(*, ssh_target: str, base_url: str, timeout: float) -> ClosureCheck:
    from scripts.remote_runtime_probe import _resolve_ssh_target

    ssh_host, ssh_port, ssh_user = _resolve_ssh_target(ssh_target)
    checks = build_probe_report(
        ssh_alias=ssh_target,
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        http_url=f"{base_url.rstrip('/')}/api/health",
        timeout=max(timeout, 1.0),
    )
    failed = [item for item in checks if item.status in {"failed", "timeout"}]
    if failed:
        detail = "; ".join(
            f"{item.name}={item.status}:{item.detail}" for item in failed
        )
        return ClosureCheck("remote_health", "failed", detail[:600])
    return ClosureCheck("remote_health", "ok", "ssh banner, tls and /api/health passed")


def _read_json_url(url: str, *, timeout: float) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": "aqsp-deploy-closure/1.0"}
    )
    with urlopen_no_macos_proxy(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 400:
            raise RuntimeError(f"HTTP {status}")
        return json.loads(response.read().decode("utf-8"))


def _snapshot_contract(
    *, base_url: str, expected_end: str, timeout: float
) -> ClosureCheck:
    url = f"{base_url.rstrip('/')}/api/aqsp/snapshot"
    try:
        payload = _read_json_url(url, timeout=max(timeout, 1.0))
    except (OSError, RuntimeError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return ClosureCheck("snapshot_contract", "failed", f"{url}: {exc}")
    data = payload.get("data", payload) if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return ClosureCheck(
            "snapshot_contract", "failed", "snapshot payload must be an object"
        )
    selected = str(data.get("selected_date") or "").strip()
    available = data.get("available_dates")
    if not selected:
        return ClosureCheck("snapshot_contract", "failed", "selected_date missing")
    if not _valid_date(selected):
        return ClosureCheck("snapshot_contract", "failed", "selected_date invalid")
    if not isinstance(available, list) or selected not in available:
        return ClosureCheck(
            "snapshot_contract", "failed", "available_dates missing selected_date"
        )
    source = data.get("source")
    if not isinstance(source, dict):
        return ClosureCheck("snapshot_contract", "failed", "source missing")
    latest_trade_date = str(source.get("latest_trade_date") or "").strip()
    if not latest_trade_date:
        return ClosureCheck(
            "snapshot_contract", "failed", "source latest_trade_date missing"
        )
    if not _valid_date(latest_trade_date):
        return ClosureCheck(
            "snapshot_contract", "failed", "source latest_trade_date invalid"
        )
    is_current_intraday_snapshot = (
        selected == today_shanghai().isoformat() and latest_trade_date == selected
    )
    if expected_end and selected != expected_end and not is_current_intraday_snapshot:
        return ClosureCheck(
            "snapshot_contract",
            "failed",
            f"selected_date {selected} != expected {expected_end}",
        )
    if (
        expected_end
        and expected_end not in available
        and not is_current_intraday_snapshot
    ):
        return ClosureCheck(
            "snapshot_contract", "failed", "available_dates missing expected_end"
        )
    if expected_end and latest_trade_date != expected_end:
        if not is_current_intraday_snapshot:
            return ClosureCheck(
                "snapshot_contract",
                "failed",
                f"source latest_trade_date {latest_trade_date} != expected {expected_end}",
            )
    variant_suite = data.get("variant_suite")
    if not isinstance(variant_suite, dict):
        return ClosureCheck("snapshot_contract", "failed", "variant_suite missing")
    if variant_suite.get("schema_version") != "variant-suite-v2":
        return ClosureCheck(
            "snapshot_contract", "failed", "variant_suite schema_version mismatch"
        )
    variants = data.get("variants")
    if not variants and str(variant_suite.get("last_error") or "").startswith(
        "变体等待："
    ):
        return ClosureCheck(
            "snapshot_contract",
            "ok",
            f"selected_date={selected} variant_suite=pending",
        )
    suite_end = str(variant_suite.get("end_date") or "").strip()
    if suite_end and not _valid_date(suite_end):
        return ClosureCheck("snapshot_contract", "failed", "variant_suite end invalid")
    if expected_end and suite_end != expected_end:
        return ClosureCheck(
            "snapshot_contract",
            "failed",
            f"variant_suite end_date {suite_end or '-'} != expected {expected_end}",
        )
    selected_symbols = variant_suite.get("selected_symbols")
    if not isinstance(selected_symbols, int) or selected_symbols < 121:
        return ClosureCheck(
            "snapshot_contract", "failed", "variant_suite selected_symbols < 121"
        )
    if not isinstance(variants, list) or not variants:
        return ClosureCheck("snapshot_contract", "failed", "variants missing")
    first = variants[0]
    if not isinstance(first, dict):
        return ClosureCheck(
            "snapshot_contract", "failed", "first variant must be an object"
        )
    for key in (
        "holdings_date",
        "previous_holdings_date",
        "holdings",
        "previous_holdings",
        "recent_actions",
        "adjustments",
        "technical_evidence",
    ):
        if key not in first:
            return ClosureCheck(
                "snapshot_contract", "failed", f"first variant missing {key}"
            )
    holdings_date = str(first.get("holdings_date") or "").strip()
    previous_holdings_date = str(first.get("previous_holdings_date") or "").strip()
    if expected_end and holdings_date != expected_end:
        return ClosureCheck(
            "snapshot_contract",
            "failed",
            f"first variant holdings_date {holdings_date or '-'} != expected {expected_end}",
        )
    if not _valid_date(previous_holdings_date) or (
        holdings_date and previous_holdings_date >= holdings_date
    ):
        return ClosureCheck(
            "snapshot_contract",
            "failed",
            "first variant previous_holdings_date invalid",
        )
    if previous_holdings_date not in available:
        return ClosureCheck(
            "snapshot_contract",
            "failed",
            "available_dates missing previous_holdings_date",
        )
    if not first.get("adjustments"):
        return ClosureCheck(
            "snapshot_contract", "failed", "first variant adjustments empty"
        )
    if not _has_complete_technical_evidence(first.get("technical_evidence")):
        return ClosureCheck(
            "snapshot_contract",
            "failed",
            "first variant technical_evidence incomplete",
        )
    declared_variant_count = variant_suite.get("variant_count")
    if (
        not isinstance(declared_variant_count, int)
        or declared_variant_count < MIN_SNAPSHOT_VARIANTS
    ):
        return ClosureCheck(
            "snapshot_contract",
            "failed",
            f"variant_suite variant_count < {MIN_SNAPSHOT_VARIANTS}",
        )
    if len(variants) < MIN_SNAPSHOT_VARIANTS:
        return ClosureCheck(
            "snapshot_contract",
            "failed",
            f"snapshot variants < {MIN_SNAPSHOT_VARIANTS}",
        )
    for index, variant in enumerate(variants[1:], start=1):
        failure = _variant_snapshot_failure(
            variant,
            expected_end=expected_end,
            available_dates=available,
        )
        if failure:
            return ClosureCheck(
                "snapshot_contract", "failed", f"variant[{index}] {failure}"
            )
    return ClosureCheck(
        "snapshot_contract",
        "ok",
        f"selected_date={selected} suite_end={suite_end} selected_symbols={selected_symbols} variants={len(variants)}",
    )


def _variant_snapshot_failure(
    value: object,
    *,
    expected_end: str,
    available_dates: list[object],
) -> str:
    if not isinstance(value, dict):
        return "must be an object"
    for key in (
        "holdings_date",
        "previous_holdings_date",
        "holdings",
        "previous_holdings",
        "recent_actions",
        "adjustments",
        "technical_evidence",
    ):
        if key not in value:
            return f"missing {key}"
    holdings_date = str(value.get("holdings_date") or "").strip()
    previous_date = str(value.get("previous_holdings_date") or "").strip()
    if expected_end and holdings_date != expected_end:
        return f"holdings_date {holdings_date or '-'} != expected {expected_end}"
    if not _valid_date(previous_date) or previous_date >= holdings_date:
        return "previous_holdings_date invalid"
    if previous_date not in available_dates:
        return "available_dates missing previous_holdings_date"
    if not value.get("adjustments"):
        return "adjustments empty"
    if not _has_complete_technical_evidence(value.get("technical_evidence")):
        return "technical_evidence incomplete"
    return ""


def _has_complete_technical_evidence(value: object) -> bool:
    if not isinstance(value, list):
        return False
    for evidence in value:
        if not isinstance(evidence, dict):
            continue
        if all(
            _is_finite_number(evidence.get(key)) for key in REQUIRED_TECHNICAL_METRICS
        ):
            return True
    return False


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _valid_date(value: str) -> bool:
    try:
        CalendarDate.fromisoformat(value)
    except ValueError:
        return False
    return True


def assess(
    *,
    root: Path,
    remote: str,
    branch: str,
    ssh_target: str,
    base_url: str,
    expected_end: str,
    timeout: float,
    skip_github_ci: bool,
    skip_remote: bool,
    skip_snapshot: bool,
) -> ClosureReport:
    checks: list[ClosureCheck] = []
    local_commit, local = _local_commit(root)
    checks.append(local)
    commit = ""
    if local_commit:
        checks.append(_worktree_clean(root))
        commit, remote_check = _remote_branch_commit(root, remote=remote, branch=branch)
        checks.append(remote_check)
    if commit:
        checks.append(
            _github_ci(root, branch=branch, commit=commit, skip=skip_github_ci)
        )
    if skip_remote:
        checks.append(
            ClosureCheck("remote_health", "skipped", "explicit --skip-remote")
        )
    else:
        checks.append(
            _probe_remote(ssh_target=ssh_target, base_url=base_url, timeout=timeout)
        )
    if skip_snapshot:
        checks.append(
            ClosureCheck("snapshot_contract", "skipped", "explicit --skip-snapshot")
        )
    else:
        checks.append(
            _snapshot_contract(
                base_url=base_url, expected_end=expected_end, timeout=timeout
            )
        )
    status = "verified" if all(item.ok for item in checks) else "failed"
    return ClosureReport(
        status=status, commit=commit, branch=branch, checks=tuple(checks)
    )


def _format(report: ClosureReport) -> str:
    lines = [
        "# AQSP Deployment Closure",
        "",
        f"status={report.status} commit={report.commit or '-'} branch={report.branch}",
        "",
    ]
    for check in report.checks:
        lines.append(f"- {check.name}: status={check.status} detail={check.detail}")
    if not report.ok:
        lines.extend(
            ["", "deployment_closure=failed; do not claim deployment verified"]
        )
    else:
        lines.extend(["", "deployment_closure=verified"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_deployment_closure")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="codex/monitor-walkforward")
    parser.add_argument("--ssh-target", default=DEFAULT_SSH_TARGET)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--expected-end",
        default="",
        help="Expected completed trading day; defaults to the latest completed A-share day.",
    )
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument("--skip-github-ci", action="store_true")
    parser.add_argument("--skip-remote", action="store_true")
    parser.add_argument("--skip-snapshot", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    expected_end = args.expected_end or latest_completed_trading_day().isoformat()
    report = assess(
        root=args.root.resolve(),
        remote=args.remote,
        branch=args.branch,
        ssh_target=args.ssh_target,
        base_url=args.base_url,
        expected_end=expected_end,
        timeout=args.timeout,
        skip_github_ci=args.skip_github_ci,
        skip_remote=args.skip_remote,
        skip_snapshot=args.skip_snapshot,
    )
    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False))
    else:
        print(_format(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
