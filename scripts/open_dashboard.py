#!/usr/bin/env python3
"""Render and serve the local AQSP dashboard without stealing focus."""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.export_dashboard_db import export_db  # noqa: E402
from scripts.render_dashboard import (
    read_candidates,
    read_ledger_rows,
    read_paper_rows,
    render_dashboard,
)  # noqa: E402
from aqsp.research.summary import load_research_summary  # noqa: E402
from aqsp.utils.jsonl_io import atomic_write_text  # noqa: E402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
ALLOW_FOREGROUND_BROWSER_ENV = "AQSP_ALLOW_FOREGROUND_BROWSER"


@dataclass(frozen=True)
class DashboardLaunchResult:
    url: str
    output_path: Path
    db_path: Path
    server_started: bool
    pid: int | None
    browser_opened: bool


def render_dashboard_bundle(
    *,
    csv_path: Path,
    ledger_path: Path,
    paper_ledger_path: Path,
    output_path: Path,
    db_path: Path,
    title: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_dashboard(
        read_candidates(csv_path),
        read_ledger_rows(ledger_path),
        title,
        read_paper_rows(paper_ledger_path),
        load_research_summary(),
    )
    atomic_write_text(output_path, html_text)
    export_db(csv_path, ledger_path, db_path)


def _dashboard_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _url_reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.0) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def foreground_browser_allowed() -> bool:
    """Require an explicit human opt-in before touching the foreground browser."""
    value = os.getenv(ALLOW_FOREGROUND_BROWSER_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def ensure_dashboard_server(
    *,
    directory: Path,
    host: str,
    port: int,
    log_path: Path,
) -> tuple[bool, int | None]:
    url = _dashboard_url(host, port)
    if _url_reachable(url):
        return False, None

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "http.server",
                str(port),
                "--bind",
                host,
                "-d",
                str(directory),
            ],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )

    for _ in range(20):
        time.sleep(0.25)
        if _url_reachable(url):
            return True, process.pid

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    raise RuntimeError(f"dashboard server failed to start: {url}")


def open_dashboard(
    *,
    csv_path: Path,
    ledger_path: Path,
    paper_ledger_path: Path,
    output_path: Path,
    db_path: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    title: str = "AQSP 量化选股面板",
    open_browser: bool = False,
    render_only: bool = False,
    log_path: Path | None = None,
) -> DashboardLaunchResult:
    render_dashboard_bundle(
        csv_path=csv_path,
        ledger_path=ledger_path,
        paper_ledger_path=paper_ledger_path,
        output_path=output_path,
        db_path=db_path,
        title=title,
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
    parser.add_argument("--output", default="dist/dashboard/index.html")
    parser.add_argument("--db", default="dist/dashboard/aqsp.db")
    parser.add_argument("--title", default="AQSP 量化选股面板")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log", default="logs/dashboard/server.log")
    parser.add_argument("--render-only", action="store_true")
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

    result = open_dashboard(
        csv_path=Path(args.csv),
        ledger_path=Path(args.ledger),
        paper_ledger_path=Path(args.paper_ledger),
        output_path=Path(args.output),
        db_path=Path(args.db),
        host=args.host,
        port=args.port,
        title=args.title,
        open_browser=args.open_browser,
        render_only=args.render_only,
        log_path=Path(args.log),
    )
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
