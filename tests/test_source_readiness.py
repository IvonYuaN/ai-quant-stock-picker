from __future__ import annotations

from aqsp.data.registry import get_registry_entry
from aqsp.data.source_health import read_source_health, record_source_auth
from aqsp.data.source_readiness import (
    inspect_source_readiness,
    recommended_sources_for_workload,
    source_supports_workload,
    source_role_for_workload,
    workload_guard_message,
    workload_fit_for_source,
)


def test_workload_fit_for_source_marks_short_term_and_walkforward_differently() -> None:
    assert workload_fit_for_source("eastmoney") == {
        "live_short": "primary",
        "walkforward": "fallback_only",
        "pit": "avoid",
    }
    assert workload_fit_for_source("sqlite_db") == {
        "live_short": "avoid",
        "walkforward": "primary",
        "pit": "avoid",
    }


def test_source_supports_workload_blocks_historical_source_for_live_short() -> None:
    assert source_supports_workload("sqlite_db", "live_short") is False
    assert source_supports_workload("eastmoney", "live_short") is True
    assert source_supports_workload("online_first", "live_short") is True


def test_candidate_live_source_is_observation_only() -> None:
    assert source_role_for_workload("eastmoney", "live_short") == "realtime"
    assert source_role_for_workload("akshare", "live_short") == "observation"
    assert source_role_for_workload("sqlite_db", "live_short") is None


def test_workload_guard_message_suggests_realtime_sources_for_live_short() -> None:
    message = workload_guard_message("sqlite_db", "live_short")

    assert "sqlite_db" in message
    assert "live_short" in message
    assert "eastmoney" in message
    assert "online_first" in message


def test_recommended_sources_for_live_short_contains_realtime_candidates() -> None:
    recommended = recommended_sources_for_workload("live_short")

    assert "eastmoney" in recommended
    assert "online_first" in recommended
    assert "sqlite_db" not in recommended


def test_live_short_boundary_guard_cannot_be_disabled(monkeypatch, tmp_path) -> None:
    goal_switch_path = tmp_path / "goal_switches.yaml"
    goal_switch_path.write_text(
        """
version: "test"
mode: short_term_realtime
switches:
  enforce_live_vs_history_boundary:
    enabled: false
    purpose: allow local experiment
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))

    assert source_supports_workload("sqlite_db", "live_short") is False
    assert "live_short" in workload_guard_message("sqlite_db", "live_short")


def test_inspect_source_readiness_when_tushare_token_missing(
    monkeypatch, tmp_path
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("AQSP_ENV_FILE", str(env_path))
    entry = get_registry_entry("tushare")
    assert entry is not None

    snapshot = inspect_source_readiness(entry, probe_auth=False)

    assert snapshot.auth_kind == "env_token"
    assert snapshot.auth_status == "missing_env"
    assert "TUSHARE_TOKEN" in snapshot.auth_message


def test_inspect_source_readiness_reads_tushare_token_from_project_env(
    monkeypatch, tmp_path
) -> None:
    env_path = tmp_path / ".env"
    health_path = tmp_path / "source_health.json"
    env_path.write_text("TUSHARE_TOKEN=demo-token\n", encoding="utf-8")
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("AQSP_ENV_FILE", str(env_path))
    monkeypatch.setenv("AQSP_SOURCE_HEALTH", str(health_path))
    entry = get_registry_entry("tushare")
    assert entry is not None

    snapshot = inspect_source_readiness(entry, probe_auth=False)

    assert snapshot.auth_status in {"configured", "ok", "auth_failed"}
    assert snapshot.auth_status != "missing_env"


def test_inspect_source_readiness_ignores_stale_missing_env_cache_when_token_exists(
    monkeypatch, tmp_path
) -> None:
    env_path = tmp_path / ".env"
    health_path = tmp_path / "source_health.json"
    env_path.write_text("TUSHARE_TOKEN=demo-token\n", encoding="utf-8")
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("AQSP_ENV_FILE", str(env_path))
    monkeypatch.setenv("AQSP_SOURCE_HEALTH", str(health_path))
    record_source_auth(
        "tushare", "missing_env", "缺少 TUSHARE_TOKEN。", path=health_path
    )
    entry = get_registry_entry("tushare")
    assert entry is not None

    snapshot = inspect_source_readiness(entry, probe_auth=False)

    assert snapshot.auth_status == "configured"
    assert snapshot.auth_message == "TUSHARE_TOKEN 已配置；尚未执行远程校验。"


def test_inspect_source_readiness_when_baostock_not_yet_checked() -> None:
    entry = get_registry_entry("baostock")
    assert entry is not None

    snapshot = inspect_source_readiness(entry, probe_auth=False)

    assert snapshot.auth_kind == "login_session"
    assert snapshot.auth_status in {"not_checked", "ok", "login_failed"}


def test_read_source_health_returns_empty_when_json_corrupt(tmp_path) -> None:
    health_path = tmp_path / "source_health.json"
    health_path.write_text("{broken", encoding="utf-8")

    health = read_source_health(health_path)

    assert health["consecutive_failures"] == 0
    assert health["sources"] == {}
