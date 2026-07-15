from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_DEFAULT_SOURCE_CATALOG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "data_sources.yaml"
)


@dataclass(frozen=True)
class DataSourceCatalogEntry:
    id: str
    name: str
    category: str
    markets: tuple[str, ...]
    access: str
    cost: str
    runtime_ready: bool
    research_status: str
    strengths: tuple[str, ...] = field(default_factory=tuple)
    risks: tuple[str, ...] = field(default_factory=tuple)
    adoption_gate: tuple[str, ...] = field(default_factory=tuple)
    absorbed_from: tuple[str, ...] = field(default_factory=tuple)
    reference: str = ""


@dataclass(frozen=True)
class DataSourceCatalog:
    version: str
    default_policy: str
    fallback_order: dict[str, tuple[str, ...]]
    sources: tuple[DataSourceCatalogEntry, ...]

    def by_id(self) -> dict[str, DataSourceCatalogEntry]:
        return {item.id: item for item in self.sources}

    def runtime_sources(self) -> tuple[DataSourceCatalogEntry, ...]:
        return tuple(item for item in self.sources if item.runtime_ready)

    def research_candidates(self) -> tuple[DataSourceCatalogEntry, ...]:
        return tuple(item for item in self.sources if not item.runtime_ready)

    def fallback_sources(self, channel: str) -> tuple[DataSourceCatalogEntry, ...]:
        """Return the configured, runtime-usable fallback chain for a channel."""

        source_by_id = self.by_id()
        return tuple(
            source_by_id[source_id]
            for source_id in self.fallback_order.get(str(channel), ())
        )


def load_data_source_catalog(path: str | Path | None = None) -> DataSourceCatalog:
    catalog_path = Path(path) if path is not None else _DEFAULT_SOURCE_CATALOG_PATH
    try:
        raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"data source catalog not found: {catalog_path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("data source catalog must be a mapping")
    sources_raw = raw.get("sources", ())
    if not isinstance(sources_raw, list):
        raise ValueError("data source catalog sources must be a list")
    sources = tuple(_source_from_mapping(item) for item in sources_raw)
    _validate_unique_ids(sources)
    version = str(raw.get("version", "") or "").strip()
    default_policy = str(raw.get("default_policy", "") or "").strip()
    if not version:
        raise ValueError("data source catalog missing version")
    if not default_policy:
        raise ValueError("data source catalog missing default_policy")
    fallback_order = _fallback_order(raw.get("fallback_order"))
    _validate_fallback_order(fallback_order, sources)
    return DataSourceCatalog(
        version=version,
        default_policy=default_policy,
        fallback_order=fallback_order,
        sources=sources,
    )


def _source_from_mapping(raw: Any) -> DataSourceCatalogEntry:
    if not isinstance(raw, dict):
        raise ValueError("data source catalog entry must be a mapping")
    entry = DataSourceCatalogEntry(
        id=_required_text(raw, "id"),
        name=_required_text(raw, "name"),
        category=_required_text(raw, "category"),
        markets=_text_tuple(raw.get("markets")),
        access=_required_text(raw, "access"),
        cost=_required_text(raw, "cost"),
        runtime_ready=_strict_bool(raw.get("runtime_ready", False), "runtime_ready"),
        research_status=_required_text(raw, "research_status"),
        strengths=_text_tuple(raw.get("strengths")),
        risks=_text_tuple(raw.get("risks")),
        adoption_gate=_text_tuple(raw.get("adoption_gate")),
        absorbed_from=_text_tuple(raw.get("absorbed_from")),
        reference=str(raw.get("reference", "") or "").strip(),
    )
    if not entry.markets:
        raise ValueError(f"data source catalog entry {entry.id} missing markets")
    if not entry.runtime_ready and not entry.adoption_gate:
        raise ValueError(f"data source catalog entry {entry.id} missing adoption_gate")
    return entry


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = str(raw.get(key, "") or "").strip()
    if not value:
        raise ValueError(f"data source catalog entry missing {key}")
    return value


def _text_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise ValueError("data source catalog list field must be a list or string")


def _fallback_order(value: Any) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("data source catalog fallback_order must be a mapping")
    return {str(key): _text_tuple(items) for key, items in value.items()}


def _validate_unique_ids(sources: tuple[DataSourceCatalogEntry, ...]) -> None:
    seen: set[str] = set()
    for item in sources:
        if item.id in seen:
            raise ValueError(f"duplicate data source catalog id: {item.id}")
        seen.add(item.id)


def _strict_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(
        f"data source catalog field {field_name} must be a boolean, got {type(value).__name__}"
    )


def _validate_fallback_order(
    fallback_order: dict[str, tuple[str, ...]],
    sources: tuple[DataSourceCatalogEntry, ...],
) -> None:
    source_by_id = {source.id: source for source in sources}
    for channel, source_ids in fallback_order.items():
        if not str(channel).strip():
            raise ValueError("data source catalog fallback channel must not be empty")
        if len(source_ids) != len(set(source_ids)):
            raise ValueError(f"duplicate fallback source in channel: {channel}")
        for source_id in source_ids:
            if source_id not in source_by_id:
                raise ValueError(
                    f"unknown fallback source {source_id} in channel {channel}"
                )
            if not source_by_id[source_id].runtime_ready:
                raise ValueError(
                    f"fallback source {source_id} in channel {channel} is not runtime_ready"
                )
