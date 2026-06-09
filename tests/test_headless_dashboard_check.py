from __future__ import annotations

from pathlib import Path

from scripts.headless_dashboard_check import (
    DEFAULT_BROWSER_CANDIDATES,
    build_headless_browser_command,
    check_text,
    find_browser_executable,
)


def test_headless_dashboard_check_detects_forbidden_text_when_present() -> None:
    errors = check_text(
        "今日面板包含 candidate_blocker 这类内部字段",
        forbidden_text=("candidate_blocker",),
        expected_text=(),
    )

    assert errors == ("forbidden text found: candidate_blocker",)


def test_headless_dashboard_check_requires_expected_text_when_missing() -> None:
    errors = check_text(
        "AQSP dashboard",
        forbidden_text=(),
        expected_text=("纸面验证",),
    )

    assert errors == ("expected text missing: 纸面验证",)


def test_headless_dashboard_check_uses_isolated_profile_and_random_debug_port(
    tmp_path: Path,
) -> None:
    screenshot_path = tmp_path / "screen.png"

    command = build_headless_browser_command(
        browser="/bin/chromium",
        url="https://lh.ifidy.cn",
        profile_dir=tmp_path / "profile",
        screenshot_path=screenshot_path,
        dump_dom=True,
        window_size="1440,1100",
        virtual_time_budget_ms=8000,
    )

    assert "--dump-dom" in command
    assert "--remote-debugging-port=0" in command
    assert f"--user-data-dir={tmp_path / 'profile'}" in command
    assert f"--screenshot={screenshot_path}" in command
    assert command[-1] == "https://lh.ifidy.cn"


def test_headless_dashboard_check_does_not_default_to_foreground_browsers() -> None:
    candidates = " ".join(DEFAULT_BROWSER_CANDIDATES).lower()

    assert "google chrome.app" not in candidates
    assert "brave browser.app" not in candidates
    assert "google-chrome" not in DEFAULT_BROWSER_CANDIDATES
    assert "brave-browser" not in DEFAULT_BROWSER_CANDIDATES


def test_headless_dashboard_check_allows_explicit_dedicated_browser(
    tmp_path: Path,
) -> None:
    browser_path = tmp_path / "chromium-for-aqsp"
    browser_path.write_text("#!/bin/sh\n", encoding="utf-8")

    assert find_browser_executable(explicit_browser=str(browser_path)) == str(
        browser_path
    )
