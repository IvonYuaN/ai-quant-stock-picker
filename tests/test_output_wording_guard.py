from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

OUTPUT_MODULES = (
    "src/aqsp/notify_templates.py",
    "src/aqsp/notifier.py",
    "src/aqsp/briefing/notifier.py",
    "src/aqsp/briefing/generator.py",
    "src/aqsp/briefing/renderer.py",
    "src/aqsp/briefing/email_notifier.py",
    "src/aqsp/cli.py",
    "src/aqsp/report.py",
    "src/aqsp/reports/v2.py",
    "src/aqsp/web/data_provider.py",
    "scripts/daily_pipeline.py",
    "scripts/render_dashboard.py",
)

DISCLAIMER_MODULES = OUTPUT_MODULES + (
    "README.md",
    "scripts/diagnose_momentum.py",
    "scripts/generate_cold_start_signals.py",
    "src/aqsp/briefing/templates/default.md.j2",
    "src/aqsp/news/catalysts.py",
    "src/aqsp/web/dashboard.py",
    "src/aqsp/web/dashboard_beginner.py",
)

FORBIDDEN_OUTPUT_WORDING = (
    "今日无重点跟踪",
    "暂无重点跟踪",
    "重点跟踪名单",
    "重点跟踪对象",
    "🎯 重点跟踪",
    "抬升成重点跟踪",
    "现在先看",
    "接下来怎么做",
    "降低信任度",
    "选股快报",
    "先看这个",
    "接下来先看",
    "现在卡在哪",
)

FORBIDDEN_STATIC_DASHBOARD_WORDING = (
    "今日重点名单",
    "重点跟踪与继续观察",
)

FORBIDDEN_DISCLAIMER_WORDING = (re.compile(r"(?<!交易指令或)不构成投资建议"),)


def test_core_output_modules_do_not_reintroduce_action_like_focus_wording() -> None:
    offenders: list[str] = []
    for module_path in OUTPUT_MODULES:
        text = (PROJECT_ROOT / module_path).read_text(encoding="utf-8")
        for phrase in FORBIDDEN_OUTPUT_WORDING:
            if phrase in text:
                offenders.append(f"{module_path}: {phrase}")

    assert offenders == []


def test_static_dashboard_does_not_reintroduce_action_like_focus_wording() -> None:
    offenders: list[str] = []
    text = (PROJECT_ROOT / "scripts/render_dashboard.py").read_text(encoding="utf-8")
    for phrase in FORBIDDEN_STATIC_DASHBOARD_WORDING:
        if phrase in text:
            offenders.append(f"scripts/render_dashboard.py: {phrase}")

    assert offenders == []


def test_user_visible_surfaces_do_not_use_legacy_disclaimer_wording() -> None:
    offenders: list[str] = []
    for module_path in DISCLAIMER_MODULES:
        text = (PROJECT_ROOT / module_path).read_text(encoding="utf-8")
        for pattern in FORBIDDEN_DISCLAIMER_WORDING:
            if pattern.search(text):
                offenders.append(f"{module_path}: {pattern.pattern}")

    assert offenders == []
