from __future__ import annotations

from aqsp.data.registry import get_registry_entry
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
    entry = get_registry_entry("tushare")
    assert entry is not None

    snapshot = inspect_source_readiness(entry, probe_auth=False)

    assert snapshot.auth_kind == "env_token"
    assert snapshot.auth_status == "missing_env"
    assert "TUSHARE_TOKEN" in snapshot.auth_message


def test_inspect_source_readiness_when_baostock_not_yet_checked() -> None:
    entry = get_registry_entry("baostock")
    assert entry is not None

    snapshot = inspect_source_readiness(entry, probe_auth=False)

    assert snapshot.auth_kind == "login_session"
    assert snapshot.auth_status in {"not_checked", "ok", "login_failed"}
