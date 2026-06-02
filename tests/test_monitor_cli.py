from __future__ import annotations

import argparse
import types

from aqsp.monitor.checker import MonitorResult

import aqsp.cli as cli


def test_run_monitor_notifies_critical_only_when_enabled(monkeypatch, capsys) -> None:
    sent: list[list[MonitorResult]] = []

    class FakeChecker:
        def __init__(self, config_path: str) -> None:
            self.config_path = config_path

        def check_all(self) -> list[MonitorResult]:
            return [
                MonitorResult("warn_case", True, "warning", "warning hit"),
                MonitorResult("crit_case", True, "critical", "critical hit"),
            ]

    fake_notifier = types.SimpleNamespace(
        format_alert=lambda results: "\n".join(r.name for r in results),
        send_alerts=lambda results: sent.append(results),
    )

    monkeypatch.setitem(__import__("sys").modules, "aqsp.monitor.notifier", fake_notifier)
    monkeypatch.setitem(
        __import__("sys").modules,
        "aqsp.monitor.checker",
        types.SimpleNamespace(MonitorChecker=FakeChecker),
    )

    args = argparse.Namespace(
        config="config/monitors.yaml",
        notify=True,
        notify_critical_only=True,
        dry_run=False,
    )

    exit_code = cli.run_monitor(args)

    assert exit_code == 1
    assert len(sent) == 1
    assert [r.name for r in sent[0]] == ["crit_case"]
    assert "warn_case" in capsys.readouterr().out
