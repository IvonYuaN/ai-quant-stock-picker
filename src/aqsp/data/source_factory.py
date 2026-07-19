from __future__ import annotations

import os
import inspect
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aqsp.config import online_fallback_allowed
from aqsp.data.cache import DataCache
from aqsp.data.multi_source import MultiSource, SourceFactory
from aqsp.data.source_health import prioritize_source_ids
from aqsp.data.source import DataSource


_logger = logging.getLogger("aqsp.data.source_factory")
_OPTIONAL_SOURCE_NAMES = frozenset({"akshare", "baostock", "efinance", "mootdx"})


SourceBuilder = Callable[..., DataSource]


def resolve_sqlite_db_path() -> str | None:
    from aqsp.utils.env import read_env_value

    db_candidates = [
        os.getenv("AQSP_SQLITE_DB_PATH", "").strip(),
        read_env_value(".env", "AQSP_SQLITE_DB_PATH"),
        "/opt/market-data/astocks_raw.db",
        "A股量化分析数据/astocks_raw.db",
    ]
    return next(
        (str(path) for path in db_candidates if path and Path(str(path)).exists()), None
    )


def build_sqlite_db_source(*, cache: DataCache | None) -> DataSource:
    return build_sqlite_db_source_with(cache=cache)


def build_sqlite_db_source_with(
    *,
    cache: DataCache | None,
    builder: SourceBuilder | None = None,
    db_path_resolver: Callable[[], str | None] = resolve_sqlite_db_path,
) -> DataSource:
    from aqsp.data.sqlite_db_source import SqliteDbSource

    source_builder = builder or SqliteDbSource
    db_path = db_path_resolver()
    if db_path:
        if _callable_accepts_keyword(source_builder, "db_path"):
            return source_builder(db_path=db_path, cache=cache)
        return source_builder(cache=cache)
    return source_builder(cache=cache)


def _callable_accepts_keyword(fn: SourceBuilder, keyword: str) -> bool:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == keyword and parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            return True
    return False


def load_sqlite_symbol_name_map(symbols: list[str]) -> dict[str, str]:
    if not symbols:
        return {}
    db_path = resolve_sqlite_db_path()
    try:
        from aqsp.data.sqlite_db_source import SqliteDbSource

        source = SqliteDbSource(db_path=db_path) if db_path else SqliteDbSource()
    except Exception:
        return {}

    name_map: dict[str, str] = {}
    for symbol in symbols:
        name = str(source.get_symbol_name(symbol)).strip()
        if name and name != symbol:
            name_map[symbol] = name
    return name_map


def sqlite_price_mode(db_path: str) -> str:
    from aqsp.data.sqlite_db_source import SqliteDbSource

    return SqliteDbSource(db_path=db_path, cache=None).price_mode()


def build_data_source(
    source_name: str,
    *,
    cache: DataCache | None = None,
    overrides: dict[str, SourceBuilder] | None = None,
) -> DataSource:
    source_cache = cache or DataCache()
    override_builders = overrides or {}
    if source_name == "sqlite_db":
        return build_sqlite_db_source_with(
            cache=cache,
            builder=override_builders.get("sqlite_db"),
        )
    builders = _source_builders(override_builders)
    if source_name in {"auto", "local_first"}:
        if not online_fallback_allowed():
            return builders["tdx_vipdoc"]()
        fallbacks = _reorder_source_refs(
            _build_source_refs(
                ("eastmoney", "sina", "tencent", "akshare"),
                builders,
                source_cache,
            ),
            pinned_last=("akshare",),
        )
        return MultiSource(
            SourceFactory("tdx_vipdoc", builders["tdx_vipdoc"]),
            fallbacks,
            validate_consistency=False,
        )
    if source_name == "online_first":
        online_sources = _reorder_source_refs(
            _build_source_refs(
                ("eastmoney", "sina", "tencent", "akshare"),
                builders,
                source_cache,
            ),
            pinned_last=("akshare",),
        )
        return MultiSource(
            online_sources[0],
            online_sources[1:] + [SourceFactory("tdx_vipdoc", builders["tdx_vipdoc"])],
            validate_consistency=False,
        )
    if source_name == "multi":
        sources = _reorder_source_refs(
            _build_source_refs(
                ("akshare", "sina", "eastmoney", "tencent"),
                builders,
                source_cache,
            ),
            pinned_last=("akshare",),
        )
        return MultiSource(sources[0], sources[1:])
    if source_name in builders:
        return _build_with_cache(builders[source_name], source_cache)
    raise ValueError(f"Unknown data source: {source_name}")


def _build_with_cache(builder: SourceBuilder, cache: DataCache) -> DataSource:
    try:
        return builder(cache=cache)
    except TypeError as exc:
        if "cache" not in str(exc):
            raise
        return builder()


def _build_source_refs(
    names: tuple[str, ...],
    builders: dict[str, SourceBuilder],
    cache: DataCache,
) -> list[DataSource]:
    """Build available source adapters without letting optional imports block live data."""
    refs: list[DataSource] = []
    for name in names:
        builder = builders[name]
        try:
            refs.append(_build_with_cache(builder, cache))
        except (ImportError, ModuleNotFoundError, RuntimeError) as exc:
            if name not in _OPTIONAL_SOURCE_NAMES or not _is_missing_optional_source(exc):
                raise
            _logger.warning("跳过未安装的可选数据源 %s: %s", name, exc)
    if not refs:
        raise RuntimeError("没有可用的实时数据源")
    return refs


def _is_missing_optional_source(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "not installed" in message or "no module named" in message


def _source_builders(overrides: dict[str, SourceBuilder]) -> dict[str, SourceBuilder]:
    from aqsp.data.akshare_source import AkshareSource
    from aqsp.data.eastmoney_source import EastmoneySource
    from aqsp.data.efinance_source import EfinanceSource
    from aqsp.data.mootdx_source import MootdxSource
    from aqsp.data.sina_source import SinaSource
    from aqsp.data.tdx_vipdoc_source import TdxVipdocSource
    from aqsp.data.tencent_source import TencentSource

    builders: dict[str, SourceBuilder] = {
        "akshare": AkshareSource,
        "sina": SinaSource,
        "eastmoney": EastmoneySource,
        "tencent": TencentSource,
        "mootdx": MootdxSource,
        "efinance": EfinanceSource,
        "tdx_vipdoc": TdxVipdocSource,
    }
    try:
        from aqsp.data.baostock_source import BaostockSource
    except ModuleNotFoundError:
        pass
    else:
        builders["baostock"] = BaostockSource
    for name, builder in overrides.items():
        if builder is not None:
            builders[name] = builder
    return builders


def _reorder_source_refs(
    source_refs: list[Any],
    *,
    pinned_last: tuple[str, ...] = (),
) -> list[Any]:
    order = prioritize_source_ids(
        [str(getattr(item, "name", "")) for item in source_refs]
    )
    by_name = {str(getattr(item, "name", "")): item for item in source_refs}
    prioritized = [by_name[name] for name in order if name in by_name]
    if not pinned_last:
        return prioritized
    keep: list[Any] = []
    tail: list[Any] = []
    pinned = set(pinned_last)
    for item in prioritized:
        if str(getattr(item, "name", "")) in pinned:
            tail.append(item)
        else:
            keep.append(item)
    return keep + tail
