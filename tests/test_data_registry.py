from __future__ import annotations

from aqsp.data.registry import list_registry_entries, local_data_status


def test_data_registry_contains_multiple_independent_source_tiers() -> None:
    entries = list_registry_entries()
    ids = {entry.id for entry in entries}
    tiers = {entry.tier for entry in entries}

    assert {
        "tdx_vipdoc",
        "eastmoney",
        "sina",
        "tencent",
        "akshare",
        "baostock",
        "tushare",
        "adata",
        "efinance",
        "qstock",
        "xtquant_qmt",
    } <= ids
    assert {"local_offline", "free_online", "token_api", "local_terminal"} <= tiers


def test_data_registry_marks_future_sources_not_runtime_ready() -> None:
    entries = {entry.id: entry for entry in list_registry_entries()}

    assert entries["tdx_vipdoc"].runtime_ready is True
    assert entries["tushare"].runtime_ready is False
    assert entries["xtquant_qmt"].requires_account is True
    assert entries["efinance"].supports_realtime is True


def test_sqlite_db_local_data_status_uses_env_path(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "astocks_qfq.db"
    db_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("AQSP_SQLITE_DB_PATH", str(db_path))
    entry = {item.id: item for item in list_registry_entries()}["sqlite_db"]

    assert local_data_status(entry) == "present"
