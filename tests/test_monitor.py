from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from aqsp.monitor.checker import MonitorChecker, MonitorResult, MonitorConfig
from aqsp.monitor.notifier import format_alert, send_alerts, _monitor_notify_state_path


@pytest.fixture
def sample_config(tmp_path: Path) -> Path:
    config = {
        "version": "1.0.0",
        "monitors": [
            {
                "name": "test_monitor",
                "description": "Test monitor",
                "enabled": True,
                "check": "data_freshness",
                "params": {"max_lag_days": 3},
                "severity": "warning",
            },
            {
                "name": "disabled_monitor",
                "description": "Disabled monitor",
                "enabled": False,
                "check": "data_freshness",
                "params": {},
                "severity": "info",
            },
        ],
    }
    config_path = tmp_path / "monitors.yaml"
    config_path.write_text(yaml.dump(config, allow_unicode=True), encoding="utf-8")
    return config_path


@pytest.fixture
def sample_results() -> list[MonitorResult]:
    return [
        MonitorResult(
            name="critical_alert",
            triggered=True,
            severity="critical",
            message="Critical issue",
            details={"key": "value"},
        ),
        MonitorResult(
            name="warning_alert",
            triggered=True,
            severity="warning",
            message="Warning issue",
        ),
        MonitorResult(
            name="normal_check",
            triggered=False,
            severity="info",
            message="Normal",
        ),
    ]


class TestMonitorChecker:
    def test_load_config(self, sample_config: Path) -> None:
        checker = MonitorChecker(config_path=str(sample_config))
        assert len(checker.config) == 2
        assert checker.config[0].name == "test_monitor"
        assert checker.config[0].enabled is True
        assert checker.config[1].name == "disabled_monitor"
        assert checker.config[1].enabled is False

    def test_load_config_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            MonitorChecker(config_path="nonexistent.yaml")

    def test_check_all_skips_disabled(self, sample_config: Path) -> None:
        checker = MonitorChecker(config_path=str(sample_config))

        with patch.object(checker, "_check_data_freshness") as mock_check:
            mock_check.return_value = MonitorResult(
                name="test_monitor",
                triggered=False,
                severity="info",
                message="ok",
            )
            results = checker.check_all()

            assert len(results) == 1
            assert results[0].name == "test_monitor"
            assert results[0].severity == "warning"

    def test_check_all_applies_configured_severity_to_source_health(
        self, tmp_path: Path
    ) -> None:
        config = {
            "monitors": [
                {
                    "name": "data_source_failure",
                    "description": "数据源连续失败",
                    "enabled": True,
                    "check": "source_health",
                    "params": {"max_consecutive_failures": 3},
                    "severity": "critical",
                }
            ]
        }
        config_path = tmp_path / "monitors.yaml"
        config_path.write_text(yaml.dump(config, allow_unicode=True), encoding="utf-8")
        (tmp_path / "data").mkdir()
        (tmp_path / "data/source_health.json").write_text(
            json.dumps({"consecutive_failures": 4}),
            encoding="utf-8",
        )
        with patch("aqsp.monitor.checker.Path", side_effect=lambda raw: Path(raw)):
            checker = MonitorChecker(config_path=str(config_path))

        with patch("pathlib.Path.exists", return_value=True):
            with patch(
                "pathlib.Path.read_text",
                return_value=json.dumps({"consecutive_failures": 4}),
            ):
                result = checker.check_all()[0]

        assert result.name == "data_source_failure"
        assert result.triggered is True
        assert result.severity == "critical"

    def test_check_data_freshness(self, sample_config: Path) -> None:
        checker = MonitorChecker(config_path=str(sample_config))

        with patch("sqlite3.connect") as mock_connect:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = ("2026-05-20",)
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value.__enter__.return_value = mock_conn

            result = checker._check_data_freshness({"max_lag_days": 3})

            assert result.name == "stale_data"
            assert result.severity == "critical"
            mock_connect.assert_called_once_with("data/cache.db", timeout=30.0)

    def test_check_data_freshness_uses_trading_day_lag(
        self, sample_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checker = MonitorChecker(config_path=str(sample_config))
        monkeypatch.setattr(
            "aqsp.monitor.checker.today_shanghai", lambda: date(2026, 6, 22)
        )

        with patch("sqlite3.connect") as mock_connect:
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = ("2026-06-18",)
            mock_conn = MagicMock()
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value.__enter__.return_value = mock_conn

            result = checker._check_data_freshness({"max_lag_days": 1})

        assert result.triggered is False
        assert result.details["trading_lag_days"] == 1

    def test_check_data_freshness_skips_when_cache_missing_and_optional(
        self, sample_config: Path
    ) -> None:
        checker = MonitorChecker(config_path=str(sample_config))

        result = checker._check_data_freshness(
            {
                "cache_path": "data/missing_cache.db",
                "max_lag_days": 3,
                "required": False,
            }
        )

        assert result.name == "stale_data"
        assert result.triggered is False
        assert result.severity == "warning"
        assert "跳过本地缓存新鲜度检查" in result.message

    def test_check_data_freshness_fails_when_cache_missing_and_required(
        self, sample_config: Path
    ) -> None:
        checker = MonitorChecker(config_path=str(sample_config))

        result = checker._check_data_freshness(
            {"cache_path": "data/missing_cache.db", "max_lag_days": 3, "required": True}
        )

        assert result.name == "stale_data"
        assert result.triggered is True
        assert result.severity == "critical"
        assert "数据缓存文件不存在" in result.message

    def test_check_circuit_breaker(self, sample_config: Path) -> None:
        checker = MonitorChecker(config_path=str(sample_config))

        with patch("aqsp.risk.circuit_breaker.CircuitBreaker") as mock_breaker:
            mock_instance = MagicMock()
            mock_instance.is_in_cooldown.return_value = True
            mock_breaker.return_value = mock_instance

            result = checker._check_circuit_breaker({})

            assert result.name == "circuit_breaker"
            assert result.triggered is True
            assert result.severity == "critical"
            assert mock_breaker.call_args.kwargs["config"].daily_loss_pct > 0

    def test_check_win_rate(self, sample_config: Path) -> None:
        checker = MonitorChecker(config_path=str(sample_config))

        with patch("aqsp.monitor.checker.read_ledger") as mock_read:
            mock_read.return_value = [
                {"status": "validated", "win": True},
                {"status": "validated", "win": False},
                {"status": "validated", "win": True},
                {"status": "pending", "win": None},
            ]

            result = checker._check_win_rate({"min_win_rate": 0.5, "min_samples": 3})

            assert result.name == "win_rate_drop"
            assert result.triggered is False
            assert result.details["win_rate"] == pytest.approx(2 / 3)

    def test_check_win_rate_below_threshold(self, sample_config: Path) -> None:
        checker = MonitorChecker(config_path=str(sample_config))

        with patch("aqsp.monitor.checker.read_ledger") as mock_read:
            mock_read.return_value = [
                {"status": "validated", "win": True},
                {"status": "validated", "win": False},
                {"status": "validated", "win": False},
            ]

            result = checker._check_win_rate({"min_win_rate": 0.5, "min_samples": 3})

            assert result.triggered is True
            assert result.severity == "warning"

    def test_check_source_health(self, sample_config: Path) -> None:
        checker = MonitorChecker(config_path=str(sample_config))

        health_data = {"consecutive_failures": 4, "last_failure": "2026-05-27"}

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_text", return_value=json.dumps(health_data)):
                result = checker._check_source_health({"max_consecutive_failures": 3})

            assert result.name == "data_source_failure"
            assert result.triggered is True
            assert result.severity == "warning"


class TestNotifier:
    @pytest.fixture(autouse=True)
    def _isolated_monitor_notify_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "AQSP_MONITOR_NOTIFY_STATE_PATH", str(tmp_path / "monitor_state.json")
        )

    def test_format_alert(self, sample_results: list[MonitorResult]) -> None:
        alert = format_alert(sample_results)

        assert "系统监控告警" in alert
        assert "## 严重" in alert
        assert "警告" in alert
        assert "critical_alert" in alert
        assert "warning_alert" in alert
        assert "normal_check" not in alert
        assert "## 🔴" not in alert
        assert "## 🟡" not in alert

    def test_format_alert_no_triggered(self) -> None:
        results = [
            MonitorResult(name="ok", triggered=False, severity="info", message="ok"),
        ]
        alert = format_alert(results)

        assert "总体状态: 正常" in alert

    def test_send_alerts(self, sample_results: list[MonitorResult]) -> None:
        with patch("aqsp.monitor.notifier.notify_markdown_via_config") as mock_notify:
            mock_notify.return_value = [
                MagicMock(channel="serverchan", ok=True, detail="HTTP 200")
            ]
            send_alerts(sample_results)

            mock_notify.assert_called_once()
            alert_msg = mock_notify.call_args[0][0]
            assert "系统监控告警" in alert_msg
            assert mock_notify.call_args.kwargs["mode"] == "summary"

    def test_send_alerts_prints_channel_results(
        self, sample_results: list[MonitorResult], capsys
    ) -> None:
        with patch("aqsp.monitor.notifier.notify_markdown_via_config") as mock_notify:
            mock_notify.return_value = [
                MagicMock(channel="serverchan", ok=True, detail="HTTP 200"),
                MagicMock(channel="wechat", ok=False, detail="HTTP 500"),
            ]

            send_alerts(sample_results)

        output = capsys.readouterr().out
        assert "monitor notify serverchan: ok (HTTP 200)" in output
        assert "monitor notify wechat: failed (HTTP 500)" in output

    def test_send_alerts_dedupes_same_critical_alert(
        self, sample_results: list[MonitorResult], tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setenv(
            "AQSP_MONITOR_NOTIFY_STATE_PATH", str(tmp_path / "monitor_state.json")
        )
        with patch("aqsp.monitor.notifier.notify_markdown_via_config") as mock_notify:
            mock_notify.return_value = [
                MagicMock(channel="serverchan", ok=True, detail="HTTP 200")
            ]

            send_alerts(sample_results)
            send_alerts(sample_results)

        assert mock_notify.call_count == 1
        assert "skipped duplicate alert" in capsys.readouterr().out

    def test_monitor_notify_state_path_uses_project_root_when_relative(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("AQSP_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setenv(
            "AQSP_MONITOR_NOTIFY_STATE_PATH", "data/monitor_notify_state.json"
        )

        assert _monitor_notify_state_path() == (
            tmp_path / "data/monitor_notify_state.json"
        )

    def test_send_alerts_dedupes_across_cwd_changes(
        self, sample_results: list[MonitorResult], tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setenv("AQSP_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setenv(
            "AQSP_MONITOR_NOTIFY_STATE_PATH", "data/monitor_notify_state.json"
        )
        first_cwd = tmp_path / "first"
        second_cwd = tmp_path / "second"
        first_cwd.mkdir()
        second_cwd.mkdir()

        with patch("aqsp.monitor.notifier.notify_markdown_via_config") as mock_notify:
            mock_notify.return_value = [
                MagicMock(channel="serverchan", ok=True, detail="HTTP 200")
            ]
            monkeypatch.chdir(first_cwd)
            send_alerts(sample_results)
            monkeypatch.chdir(second_cwd)
            send_alerts(sample_results)

        assert mock_notify.call_count == 1
        assert (tmp_path / "data/monitor_notify_state.json").exists()
        assert "skipped duplicate alert" in capsys.readouterr().out

    def test_send_alerts_reserves_before_delivery(
        self, sample_results: list[MonitorResult], tmp_path: Path, monkeypatch
    ) -> None:
        state_path = tmp_path / "monitor_state.json"
        monkeypatch.setenv("AQSP_MONITOR_NOTIFY_STATE_PATH", str(state_path))

        def _notify(_markdown: str, **_kwargs) -> list[MagicMock]:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            assert state["status"] == "pending"
            return [MagicMock(channel="serverchan", ok=True, detail="HTTP 200")]

        with patch(
            "aqsp.monitor.notifier.notify_markdown_via_config", side_effect=_notify
        ):
            send_alerts(sample_results)

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["status"] == "sent"

    def test_send_alerts_suppresses_duplicate_retries_when_delivery_fails(
        self, sample_results: list[MonitorResult], tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv(
            "AQSP_MONITOR_NOTIFY_STATE_PATH", str(tmp_path / "monitor_state.json")
        )
        with patch("aqsp.monitor.notifier.notify_markdown_via_config") as mock_notify:
            mock_notify.return_value = [
                MagicMock(channel="serverchan", ok=False, detail="HTTP 500")
            ]

            send_alerts(sample_results)
            send_alerts(sample_results)

        assert mock_notify.call_count == 1

    def test_send_alerts_sends_new_alert_name_once(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv(
            "AQSP_MONITOR_NOTIFY_STATE_PATH", str(tmp_path / "monitor_state.json")
        )
        first = [
            MonitorResult(
                name="win_rate_drop",
                triggered=True,
                severity="warning",
                message="胜率 20.0% 低于阈值 30.0%",
                details={"win_rate": 0.2},
            )
        ]
        second = [
            MonitorResult(
                name="data_source_failure",
                triggered=True,
                severity="warning",
                message="数据源连续失败 4 次",
                details={"consecutive_failures": 4},
            )
        ]

        with patch("aqsp.monitor.notifier.notify_markdown_via_config") as mock_notify:
            mock_notify.return_value = [
                MagicMock(channel="serverchan", ok=True, detail="HTTP 200")
            ]

            send_alerts(first)
            send_alerts(second)
            send_alerts(second)

        assert mock_notify.call_count == 2

    def test_send_alerts_dedupes_flapping_alert_set_per_alert(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv(
            "AQSP_MONITOR_NOTIFY_STATE_PATH", str(tmp_path / "monitor_state.json")
        )
        alert_a = MonitorResult(
            name="stale_data",
            triggered=True,
            severity="critical",
            message="数据滞后",
        )
        alert_b = MonitorResult(
            name="notify_failure",
            triggered=True,
            severity="warning",
            message="通知失败",
        )

        with patch("aqsp.monitor.notifier.notify_markdown_via_config") as mock_notify:
            mock_notify.return_value = [
                MagicMock(channel="serverchan", ok=True, detail="HTTP 200")
            ]
            send_alerts([alert_a])
            send_alerts([alert_a, alert_b])
            send_alerts([alert_a])

        assert mock_notify.call_count == 2
        assert "stale_data" in mock_notify.call_args_list[0].args[0]
        assert "notify_failure" in mock_notify.call_args_list[1].args[0]
        assert "stale_data" not in mock_notify.call_args_list[1].args[0]

    def test_send_alerts_dedupes_same_alert_names_when_message_changes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv(
            "AQSP_MONITOR_NOTIFY_STATE_PATH", str(tmp_path / "monitor_state.json")
        )
        first = [
            MonitorResult(
                name="stale_data",
                triggered=True,
                severity="critical",
                message="数据滞后 4 天，超过阈值 3 天",
            )
        ]
        second = [
            MonitorResult(
                name="stale_data",
                triggered=True,
                severity="critical",
                message="数据滞后 5 天，超过阈值 3 天",
            )
        ]

        with patch("aqsp.monitor.notifier.notify_markdown_via_config") as mock_notify:
            mock_notify.return_value = [
                MagicMock(channel="serverchan", ok=True, detail="HTTP 200")
            ]
            send_alerts(first)
            send_alerts(second)

        assert mock_notify.call_count == 1

    def test_send_alerts_no_triggered(self) -> None:
        results = [
            MonitorResult(name="ok", triggered=False, severity="info", message="ok"),
        ]

        with patch("aqsp.monitor.notifier.notify_markdown_via_config") as mock_notify:
            send_alerts(results)
            mock_notify.assert_not_called()

    def test_send_alerts_keeps_today_dedupe_state_after_normal_round(
        self, sample_results: list[MonitorResult], tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv(
            "AQSP_MONITOR_NOTIFY_STATE_PATH", str(tmp_path / "monitor_state.json")
        )
        with patch("aqsp.monitor.notifier.notify_markdown_via_config") as mock_notify:
            mock_notify.return_value = [
                MagicMock(channel="serverchan", ok=True, detail="HTTP 200")
            ]
            send_alerts(sample_results)
            send_alerts(
                [
                    MonitorResult(
                        name="ok",
                        triggered=False,
                        severity="info",
                        message="ok",
                    )
                ]
            )
            send_alerts(sample_results)

        assert mock_notify.call_count == 1


class TestMonitorResult:
    def test_monitor_result_creation(self) -> None:
        result = MonitorResult(
            name="test",
            triggered=True,
            severity="critical",
            message="test message",
            details={"key": "value"},
        )

        assert result.name == "test"
        assert result.triggered is True
        assert result.severity == "critical"
        assert result.message == "test message"
        assert result.details == {"key": "value"}

    def test_monitor_result_defaults(self) -> None:
        result = MonitorResult(
            name="test",
            triggered=False,
            severity="info",
            message="test",
        )

        assert result.details == {}


class TestMonitorConfig:
    def test_monitor_config_creation(self) -> None:
        config = MonitorConfig(
            name="test",
            description="test description",
            enabled=True,
            check="test_check",
            params={"key": "value"},
            severity="warning",
        )

        assert config.name == "test"
        assert config.description == "test description"
        assert config.enabled is True
        assert config.check == "test_check"
        assert config.params == {"key": "value"}
        assert config.severity == "warning"


def test_default_data_source_failure_monitor_is_critical() -> None:
    payload = yaml.safe_load(Path("config/monitors.yaml").read_text(encoding="utf-8"))
    monitors = {item["name"]: item for item in payload["monitors"]}

    assert monitors["data_source_failure"]["severity"] == "critical"
