from __future__ import annotations

from pathlib import Path

from scripts.headless_dashboard_check import (
    build_headless_browser_command,
    check_text,
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
