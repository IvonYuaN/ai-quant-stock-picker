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


def test_build_data_source_sqlite_db_preserves_explicit_no_cache(monkeypatch) -> None:
    from aqsp.data import source_factory as sf

    seen: dict[str, object] = {}

    def fake_build_sqlite_db_source_with(**kwargs):
        seen.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        sf, "build_sqlite_db_source_with", fake_build_sqlite_db_source_with
    )

    result = sf.build_data_source("sqlite_db", cache=None, overrides=None)

    assert result["cache"] is None
    assert seen["cache"] is None
