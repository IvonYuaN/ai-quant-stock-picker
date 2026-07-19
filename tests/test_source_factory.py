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


def test_build_sqlite_db_source_with_reraises_builder_type_error(monkeypatch) -> None:
    from aqsp.data import source_factory as sf

    def broken_builder(*, db_path=None, cache=None):
        raise TypeError("schema broken")

    monkeypatch.setattr(sf, "resolve_sqlite_db_path", lambda: "/tmp/astocks_raw.db")

    try:
        sf.build_sqlite_db_source_with(cache=None, builder=broken_builder)
    except TypeError as exc:
        assert "schema broken" in str(exc)
    else:
        raise AssertionError("expected builder TypeError")


def test_build_sqlite_db_source_with_omits_db_path_when_builder_lacks_keyword(
    monkeypatch,
) -> None:
    from aqsp.data import source_factory as sf

    seen: dict[str, object] = {}

    def legacy_builder(*, cache=None):
        seen["cache"] = cache
        return "legacy"

    monkeypatch.setattr(sf, "resolve_sqlite_db_path", lambda: "/tmp/astocks_raw.db")

    assert (
        sf.build_sqlite_db_source_with(cache=None, builder=legacy_builder) == "legacy"
    )
    assert seen["cache"] is None


def test_build_data_source_tdx_vipdoc_does_not_require_baostock_dependency(
    monkeypatch,
) -> None:
    from aqsp.data import source_factory as sf
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "aqsp.data.baostock_source":
            raise ModuleNotFoundError("No module named 'baostock'")
        return real_import(name, globals, locals, fromlist, level)

    class DummyTdx:
        name = "tdx_vipdoc"

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    source = sf.build_data_source("tdx_vipdoc", overrides={"tdx_vipdoc": DummyTdx})

    assert source.name == "tdx_vipdoc"


def test_build_source_refs_skips_missing_optional_adapter(monkeypatch) -> None:
    from aqsp.data import source_factory as sf

    class DummySource:
        name = "tencent"

    def missing_builder(*, cache=None):
        raise RuntimeError("akshare is not installed")

    def working_builder(*, cache=None):
        return DummySource()

    refs = sf._build_source_refs(
        ("akshare", "tencent"),
        {"akshare": missing_builder, "tencent": working_builder},
        object(),
    )

    assert [item.name for item in refs] == ["tencent"]
