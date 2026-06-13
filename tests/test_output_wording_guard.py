from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

OUTPUT_MODULES = (
    "src/aqsp/notify_templates.py",
    "src/aqsp/briefing/generator.py",
    "src/aqsp/briefing/renderer.py",
    "src/aqsp/briefing/email_notifier.py",
    "src/aqsp/cli.py",
    "src/aqsp/report.py",
    "src/aqsp/reports/v2.py",
)

FORBIDDEN_OUTPUT_WORDING = (
    "今日无重点跟踪",
    "暂无重点跟踪",
    "重点跟踪名单",
    "重点跟踪对象",
    "🎯 重点跟踪",
    "抬升成重点跟踪",
)


def test_core_output_modules_do_not_reintroduce_action_like_focus_wording() -> None:
    offenders: list[str] = []
    for module_path in OUTPUT_MODULES:
        text = (PROJECT_ROOT / module_path).read_text(encoding="utf-8")
        for phrase in FORBIDDEN_OUTPUT_WORDING:
            if phrase in text:
                offenders.append(f"{module_path}: {phrase}")

    assert offenders == []
