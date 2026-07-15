from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "strategy_sources.yaml"
)


@dataclass(frozen=True)
class StrategyCatalogEntry:
    id: str
    name: str
    hypothesis: str
    current_status: str
    absorbed_from: tuple[str, ...] = field(default_factory=tuple)
    signals: tuple[str, ...] = field(default_factory=tuple)
    validation_required: tuple[str, ...] = field(default_factory=tuple)
    runtime_gate: tuple[str, ...] = field(default_factory=tuple)
    references: tuple[str, ...] = field(default_factory=tuple)

    @property
    def runtime_ready(self) -> bool:
        return self.current_status in {"implemented", "implemented_partial", "runtime"}


@dataclass(frozen=True)
class StrategyCatalog:
    version: str
    notes: str
    families: tuple[StrategyCatalogEntry, ...]

    def by_id(self) -> dict[str, StrategyCatalogEntry]:
        return {item.id: item for item in self.families}

    def absorbed_runtime_families(self) -> tuple[StrategyCatalogEntry, ...]:
        return tuple(item for item in self.families if item.runtime_ready)


def load_strategy_catalog(path: str | Path | None = None) -> StrategyCatalog:
    catalog_path = Path(path) if path is not None else _DEFAULT_CATALOG_PATH
    try:
        raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"strategy catalog not found: {catalog_path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("strategy catalog must be a mapping")
    families_raw = raw.get("families", ())
    if not isinstance(families_raw, list):
        raise ValueError("strategy catalog families must be a list")
    families = tuple(_entry_from_mapping(item) for item in families_raw)
    _validate_unique_ids(families)
    return StrategyCatalog(
        version=str(raw.get("version", "") or "").strip(),
        notes=str(raw.get("notes", "") or "").strip(),
        families=families,
    )


def _entry_from_mapping(raw: Any) -> StrategyCatalogEntry:
    if not isinstance(raw, dict):
        raise ValueError("strategy catalog entry must be a mapping")
    entry = StrategyCatalogEntry(
        id=_required_text(raw, "id"),
        name=_required_text(raw, "name"),
        hypothesis=_required_text(raw, "hypothesis"),
        current_status=_required_text(raw, "current_status"),
        absorbed_from=_text_tuple(raw.get("absorbed_from")),
        signals=_text_tuple(raw.get("signals")),
        validation_required=_text_tuple(raw.get("validation_required")),
        runtime_gate=_text_tuple(raw.get("runtime_gate")),
        references=_text_tuple(raw.get("references")),
    )
    if not entry.absorbed_from:
        raise ValueError(f"strategy catalog entry {entry.id} missing absorbed_from")
    if not entry.validation_required:
        raise ValueError(
            f"strategy catalog entry {entry.id} missing validation_required"
        )
    return entry


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = str(raw.get(key, "") or "").strip()
    if not value:
        raise ValueError(f"strategy catalog entry missing {key}")
    return value


def _text_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise ValueError("strategy catalog list field must be a list or string")


def _validate_unique_ids(families: tuple[StrategyCatalogEntry, ...]) -> None:
    seen: set[str] = set()
    for item in families:
        if item.id in seen:
            raise ValueError(f"duplicate strategy catalog id: {item.id}")
        seen.add(item.id)
