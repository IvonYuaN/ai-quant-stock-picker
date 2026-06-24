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

    monkeypatch.setitem(
        __import__("sys").modules, "aqsp.monitor.notifier", fake_notifier
    )
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


def test_run_monitor_returns_zero_for_warning_only(monkeypatch) -> None:
    class FakeChecker:
        def __init__(self, config_path: str) -> None:
            self.config_path = config_path

        def check_all(self) -> list[MonitorResult]:
            return [
                MonitorResult("warn_case", True, "warning", "warning hit"),
            ]

    fake_notifier = types.SimpleNamespace(
        format_alert=lambda results: "\n".join(r.name for r in results),
        send_alerts=lambda results: None,
    )

    monkeypatch.setitem(
        __import__("sys").modules, "aqsp.monitor.notifier", fake_notifier
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "aqsp.monitor.checker",
        types.SimpleNamespace(MonitorChecker=FakeChecker),
    )

    args = argparse.Namespace(
        config="config/monitors.yaml",
        notify=False,
        notify_critical_only=False,
        dry_run=False,
    )

    exit_code = cli.run_monitor(args)

    assert exit_code == 0


def test_run_monitor_returns_zero_when_only_warning_and_notify_enabled(
    monkeypatch,
) -> None:
    sent: list[list[MonitorResult]] = []

    class FakeChecker:
        def __init__(self, config_path: str) -> None:
            self.config_path = config_path

        def check_all(self) -> list[MonitorResult]:
            return [
                MonitorResult("warn_case", False, "warning", "warning only"),
            ]

    fake_notifier = types.SimpleNamespace(
        format_alert=lambda results: "\n".join(r.name for r in results),
        send_alerts=lambda results: sent.append(results),
    )

    monkeypatch.setitem(
        __import__("sys").modules, "aqsp.monitor.notifier", fake_notifier
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "aqsp.monitor.checker",
        types.SimpleNamespace(MonitorChecker=FakeChecker),
    )

    args = argparse.Namespace(
        config="config/monitors.yaml",
        notify=True,
        notify_critical_only=False,
        dry_run=False,
    )

    exit_code = cli.run_monitor(args)

    assert exit_code == 0
    assert sent == []


def test_run_monitor_quiet_healthy_suppresses_ok_output(monkeypatch, capsys) -> None:
    class FakeChecker:
        def __init__(self, config_path: str) -> None:
            self.config_path = config_path

        def check_all(self) -> list[MonitorResult]:
            return []

    fake_notifier = types.SimpleNamespace(
        format_alert=lambda results: "\n".join(r.name for r in results),
        send_alerts=lambda results: None,
    )

    monkeypatch.setitem(
        __import__("sys").modules, "aqsp.monitor.notifier", fake_notifier
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "aqsp.monitor.checker",
        types.SimpleNamespace(MonitorChecker=FakeChecker),
    )

    exit_code = cli.run_monitor(
        argparse.Namespace(
            config="config/monitors.yaml",
            notify=False,
            notify_critical_only=False,
            quiet_healthy=True,
            dry_run=False,
        )
    )

    assert exit_code == 0
    assert capsys.readouterr().out == ""


def test_run_monitor_sends_warning_push_when_warning_notify_enabled(
    monkeypatch, capsys
) -> None:
    sent: list[list[MonitorResult]] = []

    class FakeChecker:
        def __init__(self, config_path: str) -> None:
            self.config_path = config_path

        def check_all(self) -> list[MonitorResult]:
            return [
                MonitorResult("warn_case", True, "warning", "warning hit"),
            ]

    fake_notifier = types.SimpleNamespace(
        format_alert=lambda results: "\n".join(r.name for r in results),
        send_alerts=lambda results: sent.append(results),
    )

    monkeypatch.setitem(
        __import__("sys").modules, "aqsp.monitor.notifier", fake_notifier
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "aqsp.monitor.checker",
        types.SimpleNamespace(MonitorChecker=FakeChecker),
    )

    exit_code = cli.run_monitor(
        argparse.Namespace(
            config="config/monitors.yaml",
            notify=True,
            notify_critical_only=False,
            dry_run=False,
        )
    )

    assert exit_code == 0
    assert sent and sent[0][0].name == "warn_case"
    assert "warning alerts enabled" in capsys.readouterr().out
