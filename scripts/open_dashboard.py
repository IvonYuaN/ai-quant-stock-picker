#!/usr/bin/env python3
"""Refresh artifacts and open the current Streamlit dashboard.

Static HTML remains available through ``--render-only`` for pipeline artifacts;
the normal local entry must use the same Streamlit app as production.
"""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.export_dashboard_db import export_db  # noqa: E402
from scripts.render_dashboard import (
    read_debate_results,
    read_preferred_candidates,
    read_ledger_rows,
    read_paper_rows,
    render_dashboard,
)  # noqa: E402
from aqsp.research.summary import load_research_summary  # noqa: E402
from aqsp.web.entrypoint import write_dashboard_artifact  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8501
ALLOW_FOREGROUND_BROWSER_ENV = "AQSP_ALLOW_FOREGROUND_BROWSER"
STREAMLIT_APP = PROJECT_ROOT / "src" / "aqsp" / "web" / "dashboard.py"
CURRENT_DASHBOARD_MARKERS = (
    "AQSP 日期任务研究台",
    "短线决策看板",
)
PROCESS_GROUP_TERMINATE_TIMEOUT_SECONDS = 2.0
PROCESS_GROUP_KILL_TIMEOUT_SECONDS = 2.0
DashboardPortStatus = Literal["free", "current", "occupied"]


@dataclass(frozen=True)
class DashboardLaunchResult:
    url: str
    output_path: Path
    db_path: Path
    server_started: bool
    pid: int | None
    browser_opened: bool


@dataclass(frozen=True)
class DashboardPortProbe:
    status: DashboardPortStatus
    detail: str


def render_dashboard_bundle(
    *,
    csv_path: Path,
    ledger_path: Path,
    paper_ledger_path: Path,
    output_path: Path,
    db_path: Path,
    title: str,
    intraday_csv_path: Path | None = None,
    debate_path: Path = Path("data/debate_results.jsonl"),
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_selection = read_preferred_candidates(
        csv_path,
        intraday_csv_path=intraday_csv_path,
    )
    html_text = render_dashboard(
        candidate_selection.candidates,
        read_ledger_rows(ledger_path),
        title,
        read_paper_rows(paper_ledger_path),
        load_research_summary(),
        read_debate_results(debate_path),
    )
    write_dashboard_artifact(output_path, html_text)
    export_db(candidate_selection.path, ledger_path, db_path)


def _dashboard_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _read_url_text(url: str) -> str | None:
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "aqsp-dashboard-launcher/1.0"},
        )
        with urllib.request.urlopen(request, timeout=1.0) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _url_reachable(url: str) -> bool:
    return _read_url_text(url) is not None


def _tcp_port_reachable(host: str, port: int) -> bool:
    import socket

    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        with socket.create_connection((probe_host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _listener_command(port: int) -> str:
    """Return the command listening on a local TCP port when lsof is available."""
    lsof = shutil.which("lsof")
    if lsof is None:
        return ""
    try:
        result = subprocess.run(
            [lsof, "-nP", "-a", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    pids = [line[1:] for line in result.stdout.splitlines() if line.startswith("p")]
    commands: list[str] = []
    for pid in pids:
        try:
            process = subprocess.run(
                ["ps", "-p", pid, "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        command = process.stdout.strip()
        if command:
            commands.append(command)
    return "\n".join(commands)


def probe_dashboard_port(host: str, port: int) -> DashboardPortProbe:
    """Identify whether a port is free, current AQSP, or another application."""
    url = _dashboard_url(host, port)
    page = _read_url_text(url)
    if page is not None:
        if any(marker in page for marker in CURRENT_DASHBOARD_MARKERS):
            return DashboardPortProbe("current", "current AQSP Streamlit dashboard")
        health = _read_url_text(f"{url.rstrip('/')}/_stcore/health")
        listener = _listener_command(port)
        if health is not None and any(
            token in listener
            for token in (str(STREAMLIT_APP), "src/aqsp/web/dashboard.py")
        ):
            return DashboardPortProbe("current", "current AQSP Streamlit dashboard")
        if health is not None:
            return DashboardPortProbe("occupied", "another Streamlit application")
        return DashboardPortProbe("occupied", "another HTTP application")
    if _tcp_port_reachable(host, port):
        return DashboardPortProbe(
            "occupied", "an application accepting TCP connections"
        )
    return DashboardPortProbe("free", "no application is listening")


def _port_conflict_error(host: str, port: int, detail: str) -> RuntimeError:
    return RuntimeError(
        f"dashboard port {host}:{port} is occupied by {detail}, not the current "
        f"production app ({STREAMLIT_APP}). AQSP will not silently reuse it. "
        f"Stop the old app or explicitly switch ports with "
        f"`python3 scripts/open_dashboard.py --port 8502` or "
        f"`DASHBOARD_PORT=8502 bash scripts/start_dashboard.sh`."
    )


def foreground_browser_allowed() -> bool:
    """Require an explicit human opt-in before touching the foreground browser."""
    value = os.getenv(ALLOW_FOREGROUND_BROWSER_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    terminate_timeout: float = PROCESS_GROUP_TERMINATE_TIMEOUT_SECONDS,
    kill_timeout: float = PROCESS_GROUP_KILL_TIMEOUT_SECONDS,
) -> None:
    """Stop a child and its descendants without targeting an unrelated group."""
    process_group_id: int | None = None
    child_pid = getattr(process, "pid", None)
    if isinstance(child_pid, int) and child_pid > 0:
        try:
            if os.getpgid(child_pid) == child_pid:
                process_group_id = child_pid
        except OSError:
            pass

    if process_group_id is not None:
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except OSError:
            pass
    else:
        process.terminate()

    try:
        process.wait(timeout=terminate_timeout)
        return
    except subprocess.TimeoutExpired:
        pass

    if process_group_id is not None:
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except OSError:
            pass
    else:
        process.kill()
    try:
        process.wait(timeout=kill_timeout)
    except subprocess.TimeoutExpired:
        pass


def ensure_dashboard_server(
    *,
    directory: Path,
    host: str,
    port: int,
    log_path: Path,
) -> tuple[bool, int | None]:
    url = _dashboard_url(host, port)
    initial_probe = probe_dashboard_port(host, port)
    if initial_probe.status == "current":
        return False, None
    if initial_probe.status == "occupied":
        raise _port_conflict_error(host, port, initial_probe.detail)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not STREAMLIT_APP.is_file():
        raise RuntimeError(f"current Streamlit dashboard is missing: {STREAMLIT_APP}")

    env = os.environ.copy()
    python_path = [str(PROJECT_ROOT / "src"), str(PROJECT_ROOT)]
    if env.get("PYTHONPATH"):
        python_path.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path)
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(STREAMLIT_APP),
                "--server.address",
                host,
                "--server.port",
                str(port),
                "--server.headless",
                "true",
            ],
            stdout=log_file,
            stderr=log_file,
            cwd=str(PROJECT_ROOT),
            env=env,
            start_new_session=True,
        )

    for _ in range(20):
        time.sleep(0.25)
        probe = probe_dashboard_port(host, port)
        if probe.status == "current":
            return True, process.pid
        if probe.status == "occupied":
            _terminate_process_group(process)
            raise _port_conflict_error(host, port, probe.detail)

    _terminate_process_group(process)
    raise RuntimeError(f"dashboard server failed to start: {url}")


def open_dashboard(
    *,
    csv_path: Path,
    ledger_path: Path,
    paper_ledger_path: Path,
    output_path: Path,
    db_path: Path,
    intraday_csv_path: Path | None = None,
    debate_path: Path = Path("data/debate_results.jsonl"),
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    title: str = "AQSP 量化选股面板",
    open_browser: bool = False,
    render_only: bool = False,
    render_static_artifact: bool = False,
    log_path: Path | None = None,
) -> DashboardLaunchResult:
    if render_only or render_static_artifact:
        render_dashboard_bundle(
            csv_path=csv_path,
            ledger_path=ledger_path,
            paper_ledger_path=paper_ledger_path,
            intraday_csv_path=intraday_csv_path,
            output_path=output_path,
            db_path=db_path,
            title=title,
            debate_path=debate_path,
        )

    pid: int | None = None
    server_started = False
    browser_opened = False
    url = _dashboard_url(host, port)
    if not render_only:
        server_started, pid = ensure_dashboard_server(
            directory=output_path.parent,
            host=host,
            port=port,
            log_path=log_path or Path("logs/dashboard/server.log"),
        )
        if open_browser and foreground_browser_allowed():
            webbrowser.open(url)
            browser_opened = True

    return DashboardLaunchResult(
        url=url,
        output_path=output_path,
        db_path=db_path,
        server_started=server_started,
        pid=pid,
        browser_opened=browser_opened,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="reports/latest.csv")
    parser.add_argument("--ledger", default="data/predictions.jsonl")
    parser.add_argument("--paper-ledger", default="data/paper_trades.jsonl")
    parser.add_argument("--intraday-csv", default="reports/intraday_latest.csv")
    parser.add_argument("--debate", default="data/debate_results.jsonl")
    parser.add_argument("--output", default="dist/dashboard/index.html")
    parser.add_argument("--db", default="dist/dashboard/aqsp.db")
    parser.add_argument("--title", default="AQSP 量化选股面板")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log", default="logs/dashboard/server.log")
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument(
        "--check-port",
        action="store_true",
        help="Check the port identity without rendering or starting a server.",
    )
    browser_group = parser.add_mutually_exclusive_group()
    browser_group.add_argument(
        "--open-browser",
        dest="open_browser",
        action="store_true",
        help="Open the dashboard in the system browser. Disabled by default.",
    )
    browser_group.add_argument(
        "--no-open-browser",
        dest="open_browser",
        action="store_false",
        help="Do not open a foreground browser. This is the default.",
    )
    parser.set_defaults(open_browser=False)
    args = parser.parse_args()

    if args.check_port:
        probe = probe_dashboard_port(args.host, args.port)
        if probe.status == "occupied":
            print(
                f"error={_port_conflict_error(args.host, args.port, probe.detail)}",
                file=sys.stderr,
            )
            return 2
        print(f"port_status={probe.status}")
        return 0

    try:
        result = open_dashboard(
            csv_path=Path(args.csv),
            ledger_path=Path(args.ledger),
            paper_ledger_path=Path(args.paper_ledger),
            intraday_csv_path=Path(args.intraday_csv),
            output_path=Path(args.output),
            db_path=Path(args.db),
            debate_path=Path(args.debate),
            host=args.host,
            port=args.port,
            title=args.title,
            open_browser=args.open_browser,
            render_only=args.render_only,
            render_static_artifact=args.render_only,
            log_path=Path(args.log),
        )
    except RuntimeError as exc:
        print(f"error={exc}", file=sys.stderr)
        return 2
    if args.render_only:
        print(f"dashboard={result.output_path}")
        print(f"dashboard_db={result.db_path}")
    if not args.render_only:
        print(f"url={result.url}")
        if result.server_started:
            print(f"server_pid={result.pid}")
        else:
            print("server_pid=reused")
        print(f"browser_opened={'yes' if result.browser_opened else 'no'}")
        if args.open_browser and not result.browser_opened:
            print(
                "browser_open_blocked="
                f"set {ALLOW_FOREGROUND_BROWSER_ENV}=1 to open a foreground browser",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
