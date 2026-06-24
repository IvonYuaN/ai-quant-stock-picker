from __future__ import annotations


def test_build_data_source_sqlite_db_handles_missing_overrides(monkeypatch) -> None:
    from aqsp.data import source_factory as sf

    class DummySqliteSource:
        def __init__(self, db_path=None, cache=None):
            self.db_path = db_path
            self.cache = cache
            self.name = "sqlite_db"

    monkeypatch.setattr(sf, "resolve_sqlite_db_path", lambda: "/tmp/astocks_raw.db")

    source = sf.build_data_source(
        "sqlite_db",
        overrides={"sqlite_db": DummySqliteSource},
    )
    assert source.name == "sqlite_db"

    monkeypatch.setattr(sf, "build_sqlite_db_source_with", lambda **kwargs: kwargs)
    result = sf.build_data_source("sqlite_db", overrides=None)

    assert result["builder"] is None
