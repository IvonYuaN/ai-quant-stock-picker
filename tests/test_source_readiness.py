from __future__ import annotations

from aqsp.data.registry import get_registry_entry
from aqsp.data.source_health import record_source_auth
from aqsp.data.source_readiness import (
    inspect_source_readiness,
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


def test_inspect_source_readiness_when_tushare_token_missing(monkeypatch) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("AQSP_ENV_FILE", raising=False)
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
    env_path.write_text("TUSHARE_TOKEN=demo-token\n", encoding="utf-8")
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("AQSP_ENV_FILE", str(env_path))
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
    record_source_auth("tushare", "missing_env", "缺少 TUSHARE_TOKEN。", path=health_path)
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
