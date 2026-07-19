#!/usr/bin/env python3
"""Check the AQSP dashboard without touching the user's foreground browser."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from collections.abc import Iterator
from pathlib import Path

from aqsp.web.entrypoint import (
    LEGACY_HEALTH_PATH,
    classify_entry_text,
    classify_health_text,
    public_research_health_url,
)


DEFAULT_URL = "https://lh.ifidy.cn"
# Hydrated React pages may replace the static product title with the compact
# visible workspace heading; classify_entry_text handles the full marker groups.
DEFAULT_EXPECTED_TEXT = ("AQSP",)
DEFAULT_FORBIDDEN_TEXT = (
    "candidate_blocker",
    "next open",
    "数据滞后: - 天",
    "risks",
    "新手看板",
    "agents.html",
    "dashboard_beginner.py",
    "archive.html",
)
DEDICATED_BROWSER_ENV = "AQSP_HEADLESS_BROWSER"
HEADLESS_LOCK_ENV = "AQSP_HEADLESS_LOCK"
DEFAULT_HEADLESS_LOCK_PATH = (
    Path(tempfile.gettempdir()) / "aqsp-headless-dashboard.lock"
)
DEFAULT_BROWSER_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
PLAYWRIGHT_BROWSER_PATTERNS = (
    "chromium-*/chrome-linux*/chrome",
    "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
    "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell",
)


@dataclass(frozen=True)
class DashboardCheckResult:
    url: str
    health_url: str
    mode: str
    browser: str | None
    headless_lock_path: Path | None
    checked_bytes: int
    screenshot_path: Path | None
    entry_kind: str
    health_kind: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors


def _derive_health_url(url: str) -> str:
    return public_research_health_url(base_url=url)


def fetch_text(url: str, *, timeout_seconds: float) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "aqsp-headless-check/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 400:
            raise RuntimeError(f"{url} returned HTTP {status}")
        return response.read().decode("utf-8", errors="replace")


def _resolve_executable(candidate: str) -> str | None:
    if "/" in candidate:
        return candidate if Path(candidate).exists() else None
    return shutil.which(candidate)


def _playwright_browser_candidates() -> tuple[str, ...]:
    roots = (
        Path.home() / ".cache" / "ms-playwright",
        Path.home() / "Library" / "Caches" / "ms-playwright",
    )
    candidates: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in PLAYWRIGHT_BROWSER_PATTERNS:
            for path in sorted(root.glob(pattern), reverse=True):
                candidates.append(str(path))
    return tuple(candidates)


def resolve_headless_lock_path(explicit_lock_path: Path | None = None) -> Path:
    """Return an AQSP-only lock path for isolated browser checks."""
    if explicit_lock_path is not None:
        return explicit_lock_path
    env_value = os.getenv(HEADLESS_LOCK_ENV, "").strip()
    if env_value:
        return Path(env_value)
    return DEFAULT_HEADLESS_LOCK_PATH


@contextlib.contextmanager
def acquire_headless_browser_lock(lock_path: Path | None = None) -> Iterator[Path]:
    """Serialize AQSP browser checks without sharing browser profiles or ports."""
    resolved_lock_path = resolve_headless_lock_path(lock_path)
    resolved_lock_path.parent.mkdir(parents=True, exist_ok=True)
    with resolved_lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={os.getpid()}\n")
        lock_file.flush()
        try:
            yield resolved_lock_path
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def find_browser_executable(
    *,
    explicit_browser: str | None = None,
    candidates: tuple[str, ...] = DEFAULT_BROWSER_CANDIDATES,
) -> str | None:
    dedicated_browser = explicit_browser or os.getenv(DEDICATED_BROWSER_ENV, "")
    if dedicated_browser:
        return _resolve_executable(dedicated_browser)
    for candidate in (*candidates, *_playwright_browser_candidates()):
        resolved = _resolve_executable(candidate)
        if resolved:
            return resolved
    return None


def build_headless_browser_command(
    *,
    browser: str,
    url: str,
    profile_dir: Path,
    screenshot_path: Path | None,
    dump_dom: bool,
    window_size: str,
    virtual_time_budget_ms: int,
) -> list[str]:
    command = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-sync",
        "--mute-audio",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
        "--use-mock-keychain",
        f"--user-data-dir={profile_dir}",
        "--remote-debugging-port=0",
        f"--window-size={window_size}",
        f"--virtual-time-budget={virtual_time_budget_ms}",
    ]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        command.append("--no-sandbox")
    if dump_dom:
        command.append("--dump-dom")
    if screenshot_path is not None:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        command.append(f"--screenshot={screenshot_path}")
    command.append(url)
    return command


def run_headless_browser(
    *,
    browser: str,
    url: str,
    profile_dir: Path,
    screenshot_path: Path | None,
    timeout_seconds: float,
    window_size: str,
    virtual_time_budget_ms: int,
    lock_path: Path | None = None,
) -> str:
    command = build_headless_browser_command(
        browser=browser,
        url=url,
        profile_dir=profile_dir,
        screenshot_path=screenshot_path,
        dump_dom=True,
        window_size=window_size,
        virtual_time_budget_ms=virtual_time_budget_ms,
    )
    with acquire_headless_browser_lock(lock_path):
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
    if result.returncode != 0:
        stderr = result.stderr.strip().splitlines()
        detail = stderr[-1] if stderr else f"exit={result.returncode}"
        raise RuntimeError(f"headless browser failed: {detail}")
    return result.stdout


def run_playwright_browser(
    *,
    browser: str,
    url: str,
    profile_dir: Path,
    screenshot_path: Path | None,
    timeout_seconds: float,
    window_size: str,
    virtual_time_budget_ms: int,
    lock_path: Path | None = None,
) -> str:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright is unavailable") from exc

    width_str, height_str = window_size.split(",", 1)
    width = int(width_str)
    height = int(height_str)
    timeout_ms = max(1, int(timeout_seconds * 1000))
    settle_ms = max(0, virtual_time_budget_ms)

    with acquire_headless_browser_lock(lock_path):
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                executable_path=browser,
                headless=True,
                args=["--remote-debugging-port=0"],
                viewport={"width": width, "height": height},
                ignore_https_errors=True,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except PlaywrightTimeoutError:
                    pass
                if settle_ms > 0:
                    page.wait_for_timeout(settle_ms)
                if screenshot_path is not None:
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(screenshot_path), full_page=True)
                locator = page.locator('[data-testid="stMainBlockContainer"]')
                if locator.count():
                    return locator.inner_text()
                return page.locator("body").inner_text()
            finally:
                context.close()


def run_browser_capture(
    *,
    browser: str,
    url: str,
    profile_dir: Path,
    screenshot_path: Path | None,
    timeout_seconds: float,
    window_size: str,
    virtual_time_budget_ms: int,
    lock_path: Path | None = None,
) -> str:
    playwright_error: Exception | None = None
    try:
        return run_playwright_browser(
            browser=browser,
            url=url,
            profile_dir=profile_dir,
            screenshot_path=screenshot_path,
            timeout_seconds=timeout_seconds,
            window_size=window_size,
            virtual_time_budget_ms=virtual_time_budget_ms,
            lock_path=lock_path,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        playwright_error = exc

    try:
        return run_headless_browser(
            browser=browser,
            url=url,
            profile_dir=profile_dir,
            screenshot_path=screenshot_path,
            timeout_seconds=timeout_seconds,
            window_size=window_size,
            virtual_time_budget_ms=virtual_time_budget_ms,
            lock_path=lock_path,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        if playwright_error is None:
            raise
        raise RuntimeError(
            f"playwright failed: {playwright_error}; chrome fallback failed: {exc}"
        ) from exc


def check_text(
    text: str,
    *,
    forbidden_text: tuple[str, ...],
    expected_text: tuple[str, ...],
) -> tuple[str, ...]:
    haystack = text.lower()
    errors: list[str] = []
    for needle in forbidden_text:
        if needle and needle.lower() in haystack:
            errors.append(f"forbidden text found: {needle}")
    for needle in expected_text:
        if needle and needle.lower() not in haystack:
            errors.append(f"expected text missing: {needle}")
    return tuple(errors)


def run_check(
    *,
    url: str,
    health_url: str,
    mode: str,
    forbidden_text: tuple[str, ...],
    expected_text: tuple[str, ...],
    screenshot_path: Path | None,
    timeout_seconds: float,
    window_size: str,
    virtual_time_budget_ms: int,
    browser_executable: str | None = None,
    lock_path: Path | None = None,
    require_browser: bool = False,
    require_canonical: bool = False,
) -> DashboardCheckResult:
    errors: list[str] = []
    warnings: list[str] = []
    browser: str | None = None
    browser_rendered = False
    headless_lock_path: Path | None = None
    text = ""
    health_text = ""

    try:
        health_text = fetch_text(health_url, timeout_seconds=timeout_seconds)
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        errors.append(f"health check failed: {exc}")

    health_kind = classify_health_text(health_text)
    if require_canonical and health_kind != "canonical":
        legacy_hint = f" (legacy endpoint: {LEGACY_HEALTH_PATH})"
        errors.append(
            f"health endpoint identified as {health_kind}; expected canonical "
            f"AQSP /api/health{legacy_hint}"
        )

    if mode in {"auto", "browser"}:
        browser = find_browser_executable(explicit_browser=browser_executable)
        if browser is None:
            if mode == "browser" or screenshot_path is not None:
                errors.append(
                    "dedicated headless browser executable not found; install Chromium "
                    f"or set {DEDICATED_BROWSER_ENV} to an isolated browser binary"
                )
            else:
                warnings.append(
                    "dedicated headless browser not found; using raw HTTP HTML"
                )
        else:
            headless_lock_path = resolve_headless_lock_path(lock_path)
            with tempfile.TemporaryDirectory(prefix="aqsp-headless-") as temp_dir:
                try:
                    text = run_browser_capture(
                        browser=browser,
                        url=url,
                        profile_dir=Path(temp_dir),
                        screenshot_path=screenshot_path,
                        timeout_seconds=timeout_seconds,
                        window_size=window_size,
                        virtual_time_budget_ms=virtual_time_budget_ms,
                        lock_path=headless_lock_path,
                    )
                    browser_rendered = True
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                    if mode == "browser" or screenshot_path is not None:
                        errors.append(str(exc))
                    else:
                        warnings.append(f"headless browser unavailable: {exc}")

    if not text and (mode in {"auto", "raw"}):
        try:
            text = fetch_text(url, timeout_seconds=timeout_seconds)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            errors.append(f"dashboard fetch failed: {exc}")

    actual_mode = "browser" if text and browser_rendered else "raw"
    entry_kind = classify_entry_text(text)
    if require_canonical and entry_kind != "canonical":
        errors.append(
            f"dashboard entry identified as {entry_kind}; expected canonical "
            "AQSP research entry"
        )
    if require_browser and actual_mode != "browser":
        errors.append(
            "browser render required but unavailable; use --mode browser or set "
            f"{DEDICATED_BROWSER_ENV}/--browser to an isolated Chromium binary"
        )

    errors.extend(
        check_text(
            text,
            forbidden_text=forbidden_text,
            expected_text=expected_text,
        )
    )

    return DashboardCheckResult(
        url=url,
        health_url=health_url,
        mode=actual_mode,
        browser=browser if actual_mode == "browser" else None,
        headless_lock_path=headless_lock_path if actual_mode == "browser" else None,
        checked_bytes=len(text.encode("utf-8")),
        screenshot_path=screenshot_path,
        entry_kind=entry_kind,
        health_kind=health_kind,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _split_values(values: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return tuple(result)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check AQSP dashboard with isolated headless browser boundaries.",
    )
    parser.add_argument("--url", default=os.getenv("AQSP_DASHBOARD_URL", DEFAULT_URL))
    parser.add_argument("--health-url", default="")
    parser.add_argument(
        "--mode",
        choices=("auto", "browser", "raw"),
        default="auto",
        help="auto tries isolated headless browser and falls back to raw HTTP.",
    )
    parser.add_argument(
        "--forbid",
        action="append",
        default=list(DEFAULT_FORBIDDEN_TEXT),
        help="Forbidden text. Can be repeated or comma-separated.",
    )
    parser.add_argument(
        "--expect",
        action="append",
        default=None,
        help="Expected text. Can be repeated or comma-separated.",
    )
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--window-size", default="1440,1100")
    parser.add_argument("--virtual-time-budget-ms", type=int, default=10000)
    parser.add_argument(
        "--headless-lock",
        type=Path,
        default=None,
        help=(
            "AQSP-only lock path for serialized isolated headless checks. Defaults "
            f"to ${HEADLESS_LOCK_ENV} or {DEFAULT_HEADLESS_LOCK_PATH}."
        ),
    )
    parser.add_argument(
        "--browser",
        default="",
        help=(
            "Dedicated headless browser executable. Defaults to "
            f"${DEDICATED_BROWSER_ENV}, then Chromium only."
        ),
    )
    parser.add_argument(
        "--require-browser",
        action="store_true",
        help=(
            "Fail unless an isolated headless browser actually renders the page. "
            "Use this for deploy/UI acceptance; raw HTTP fallback is not enough."
        ),
    )
    parser.add_argument(
        "--allow-legacy",
        action="store_true",
        help=(
            "Rollback/pre-cutover mode: report legacy Streamlit health and entry "
            "instead of failing the canonical-entry gate."
        ),
    )
    args = parser.parse_args()

    expected_values = (
        _split_values(args.expect)
        if args.expect is not None
        else (() if args.allow_legacy else DEFAULT_EXPECTED_TEXT)
    )

    result = run_check(
        url=args.url,
        health_url=args.health_url or _derive_health_url(args.url),
        mode=args.mode,
        forbidden_text=_split_values(args.forbid),
        expected_text=expected_values,
        screenshot_path=args.screenshot,
        timeout_seconds=args.timeout,
        window_size=args.window_size,
        virtual_time_budget_ms=args.virtual_time_budget_ms,
        browser_executable=args.browser or None,
        lock_path=args.headless_lock,
        require_browser=args.require_browser,
        require_canonical=not args.allow_legacy,
    )

    print(f"status={'pass' if result.passed else 'fail'}")
    print(f"url={result.url}")
    print(f"health_url={result.health_url}")
    print(f"mode={result.mode}")
    print(f"entry_kind={result.entry_kind}")
    print(f"health_kind={result.health_kind}")
    print(f"browser={result.browser or '-'}")
    print(f"headless_lock={result.headless_lock_path or '-'}")
    print(f"checked_bytes={result.checked_bytes}")
    if result.screenshot_path is not None:
        print(f"screenshot={result.screenshot_path}")
    for warning in result.warnings:
        print(f"warning={warning}", file=sys.stderr)
    for error in result.errors:
        print(f"error={error}", file=sys.stderr)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
