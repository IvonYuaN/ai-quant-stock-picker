from __future__ import annotations

from pathlib import Path

from scripts.headless_dashboard_check import (
    DEFAULT_BROWSER_CANDIDATES,
    DEFAULT_FORBIDDEN_TEXT,
    build_headless_browser_command,
    check_text,
    find_browser_executable,
    run_check,
    run_browser_capture,
    resolve_headless_lock_path,
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


def test_headless_dashboard_check_accepts_current_home_and_rejects_legacy_pages() -> (
    None
):
    errors = check_text(
        "AQSP 日期任务研究台 短线决策看板",
        forbidden_text=("新手看板", "agents.html", "离线归档"),
        expected_text=("AQSP 日期任务研究台", "短线决策看板"),
    )
    assert errors == ()

    errors = check_text(
        "新手看板 agents.html",
        forbidden_text=("新手看板", "agents.html"),
        expected_text=("AQSP 日期任务研究台",),
    )
    assert errors == (
        "forbidden text found: 新手看板",
        "forbidden text found: agents.html",
        "expected text missing: AQSP 日期任务研究台",
    )


def test_headless_dashboard_check_defaults_to_rejecting_legacy_markers() -> None:
    assert "新手看板" in DEFAULT_FORBIDDEN_TEXT
    assert "agents.html" in DEFAULT_FORBIDDEN_TEXT
    assert "dashboard_beginner.py" in DEFAULT_FORBIDDEN_TEXT
    assert "archive.html" in DEFAULT_FORBIDDEN_TEXT
    assert check_text(
        "dashboard_beginner.py archive.html",
        forbidden_text=DEFAULT_FORBIDDEN_TEXT,
        expected_text=(),
    ) == (
        "forbidden text found: dashboard_beginner.py",
        "forbidden text found: archive.html",
    )


def test_headless_dashboard_check_uses_isolated_profile_and_random_debug_port(
    monkeypatch,
    tmp_path: Path,
) -> None:
    screenshot_path = tmp_path / "screen.png"
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.os.geteuid",
        lambda: 501,
        raising=False,
    )

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
    assert "--no-sandbox" not in command
    assert "--remote-debugging-port=0" in command
    assert f"--user-data-dir={tmp_path / 'profile'}" in command
    assert f"--screenshot={screenshot_path}" in command
    assert command[-1] == "https://lh.ifidy.cn"


def test_headless_dashboard_check_adds_no_sandbox_for_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.os.geteuid",
        lambda: 0,
        raising=False,
    )

    command = build_headless_browser_command(
        browser="/bin/chromium",
        url="https://lh.ifidy.cn",
        profile_dir=tmp_path / "profile",
        screenshot_path=None,
        dump_dom=True,
        window_size="1440,1100",
        virtual_time_budget_ms=8000,
    )

    assert "--no-sandbox" in command


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


def test_headless_dashboard_check_discovers_playwright_cached_chromium(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    browser_path = (
        tmp_path
        / ".cache"
        / "ms-playwright"
        / "chromium-1223"
        / "chrome-linux64"
        / "chrome"
    )
    browser_path.parent.mkdir(parents=True)
    browser_path.write_text("#!/bin/sh\n", encoding="utf-8")

    assert find_browser_executable(candidates=()) == str(browser_path)


def test_headless_dashboard_check_discovers_playwright_cached_chrome_for_testing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    browser_path = (
        tmp_path
        / "Library"
        / "Caches"
        / "ms-playwright"
        / "chromium-1228"
        / "chrome-mac-arm64"
        / "Google Chrome for Testing.app"
        / "Contents"
        / "MacOS"
        / "Google Chrome for Testing"
    )
    browser_path.parent.mkdir(parents=True)
    browser_path.write_text("#!/bin/sh\n", encoding="utf-8")

    assert find_browser_executable(candidates=()) == str(browser_path)


def test_headless_dashboard_check_uses_aqsp_specific_lock_by_default() -> None:
    lock_path = resolve_headless_lock_path()

    assert lock_path.name == "aqsp-headless-dashboard.lock"


def test_headless_dashboard_check_allows_isolated_project_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "locks" / "visual-check.lock"

    assert resolve_headless_lock_path(lock_path) == lock_path


def test_headless_dashboard_check_prefers_playwright_capture(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def fake_playwright(**kwargs) -> str:
        del kwargs
        calls.append("playwright")
        return "AQSP 页面正文"

    def fake_legacy(**kwargs) -> str:
        del kwargs
        calls.append("legacy")
        return "legacy"

    monkeypatch.setattr(
        "scripts.headless_dashboard_check.run_playwright_browser",
        fake_playwright,
    )
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.run_headless_browser",
        fake_legacy,
    )

    text = run_browser_capture(
        browser="/bin/chromium",
        url="https://lh.ifidy.cn",
        profile_dir=tmp_path / "profile",
        screenshot_path=tmp_path / "screen.png",
        timeout_seconds=10,
        window_size="1440,1100",
        virtual_time_budget_ms=8000,
        lock_path=tmp_path / "lock",
    )

    assert text == "AQSP 页面正文"
    assert calls == ["playwright"]


def test_headless_dashboard_check_falls_back_when_playwright_capture_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_playwright(**kwargs) -> str:
        del kwargs
        calls.append("playwright")
        raise RuntimeError("playwright timeout")

    def fake_legacy(**kwargs) -> str:
        del kwargs
        calls.append("legacy")
        return "legacy dashboard"

    monkeypatch.setattr(
        "scripts.headless_dashboard_check.run_playwright_browser",
        fake_playwright,
    )
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.run_headless_browser",
        fake_legacy,
    )

    text = run_browser_capture(
        browser="/bin/chromium",
        url="https://lh.ifidy.cn",
        profile_dir=tmp_path / "profile",
        screenshot_path=tmp_path / "screen.png",
        timeout_seconds=10,
        window_size="1440,1100",
        virtual_time_budget_ms=8000,
        lock_path=tmp_path / "lock",
    )

    assert text == "legacy dashboard"
    assert calls == ["playwright", "legacy"]


def test_headless_dashboard_check_auto_raw_fallback_can_pass_without_browser(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.find_browser_executable",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.fetch_text",
        lambda url, **_kwargs: "ok" if url.endswith("_stcore/health") else "AQSP shell",
    )

    result = run_check(
        url="https://lh.ifidy.cn",
        health_url="https://lh.ifidy.cn/_stcore/health",
        mode="auto",
        forbidden_text=(),
        expected_text=(),
        screenshot_path=None,
        timeout_seconds=5,
        window_size="1440,1100",
        virtual_time_budget_ms=1000,
    )

    assert result.passed is True
    assert result.mode == "raw"
    assert result.browser is None
    assert result.warnings == (
        "dedicated headless browser not found; using raw HTTP HTML",
    )


def test_headless_dashboard_check_require_browser_fails_on_raw_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.find_browser_executable",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.fetch_text",
        lambda url, **_kwargs: "ok" if url.endswith("_stcore/health") else "AQSP shell",
    )

    result = run_check(
        url="https://lh.ifidy.cn",
        health_url="https://lh.ifidy.cn/_stcore/health",
        mode="auto",
        forbidden_text=(),
        expected_text=(),
        screenshot_path=None,
        timeout_seconds=5,
        window_size="1440,1100",
        virtual_time_budget_ms=1000,
        require_browser=True,
    )

    assert result.passed is False
    assert result.mode == "raw"
    assert result.browser is None
    assert any("browser render required" in error for error in result.errors)


def test_headless_dashboard_check_require_browser_fails_when_browser_capture_falls_back_to_raw(
    monkeypatch,
    tmp_path: Path,
) -> None:
    browser = tmp_path / "chromium"
    browser.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.find_browser_executable",
        lambda **_kwargs: str(browser),
    )
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.run_browser_capture",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("browser crashed")),
    )
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.fetch_text",
        lambda url, **_kwargs: "ok" if url.endswith("_stcore/health") else "AQSP shell",
    )

    result = run_check(
        url="https://lh.ifidy.cn",
        health_url="https://lh.ifidy.cn/_stcore/health",
        mode="auto",
        forbidden_text=(),
        expected_text=(),
        screenshot_path=None,
        timeout_seconds=5,
        window_size="1440,1100",
        virtual_time_budget_ms=1000,
        lock_path=tmp_path / "lock",
        require_browser=True,
    )

    assert result.passed is False
    assert result.mode == "raw"
    assert result.browser is None
    assert result.headless_lock_path is None
    assert "headless browser unavailable: browser crashed" in result.warnings
    assert any("browser render required" in error for error in result.errors)


def test_headless_dashboard_check_require_browser_passes_when_browser_renders(
    monkeypatch,
    tmp_path: Path,
) -> None:
    browser = tmp_path / "chromium"
    browser.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.find_browser_executable",
        lambda **_kwargs: str(browser),
    )
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.fetch_text",
        lambda *_args, **_kwargs: "ok",
    )
    monkeypatch.setattr(
        "scripts.headless_dashboard_check.run_browser_capture",
        lambda **_kwargs: "AQSP rendered body",
    )

    result = run_check(
        url="https://lh.ifidy.cn",
        health_url="https://lh.ifidy.cn/_stcore/health",
        mode="auto",
        forbidden_text=(),
        expected_text=("rendered body",),
        screenshot_path=None,
        timeout_seconds=5,
        window_size="1440,1100",
        virtual_time_budget_ms=1000,
        lock_path=tmp_path / "lock",
        require_browser=True,
    )

    assert result.passed is True
    assert result.mode == "browser"
    assert result.browser == str(browser)
    assert result.headless_lock_path == tmp_path / "lock"
