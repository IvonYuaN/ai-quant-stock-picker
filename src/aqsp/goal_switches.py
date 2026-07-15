from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_GOAL_SWITCH_PATH = Path("config/goal_switches.yaml")


@dataclass(frozen=True)
class GoalSwitchState:
    switch_id: str
    enabled: bool
    purpose: str


@dataclass(frozen=True)
class GoalTrackState:
    track_id: str
    label: str
    enabled: bool
    priority: str
    owner_lens: str
    deliverables: tuple[str, ...]


@dataclass(frozen=True)
class GoalSwitchMatrix:
    version: str
    mode: str
    notes: str
    principles: tuple[tuple[str, bool], ...]
    switches: tuple[GoalSwitchState, ...]
    tracks: tuple[GoalTrackState, ...]

    def principle_enabled(self, principle_id: str, default: bool = False) -> bool:
        for key, value in self.principles:
            if key == principle_id:
                return value
        return default

    def switch(self, switch_id: str) -> GoalSwitchState | None:
        for item in self.switches:
            if item.switch_id == switch_id:
                return item
        return None

    def switch_enabled(self, switch_id: str, default: bool = False) -> bool:
        override = _goal_switch_override(switch_id)
        if override is not None:
            return override
        state = self.switch(switch_id)
        if state is None:
            return default
        return state.enabled

    def active_tracks(self) -> tuple[GoalTrackState, ...]:
        return tuple(item for item in self.tracks if item.enabled)

    def prioritized_tracks(self, limit: int | None = None) -> tuple[GoalTrackState, ...]:
        priority_order = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
        ordered = tuple(
            sorted(
                self.active_tracks(),
                key=lambda item: (priority_order.get(item.priority.lower(), 99), item.track_id),
            )
        )
        if limit is None or limit < 0:
            return ordered
        return ordered[:limit]


def _goal_switch_override_env_name(switch_id: str) -> str:
    normalized = "".join(
        char.upper() if char.isalnum() else "_"
        for char in switch_id.strip()
    )
    return f"AQSP_GOAL_SWITCH_{normalized}"


def _goal_switch_override(switch_id: str) -> bool | None:
    raw = os.getenv(_goal_switch_override_env_name(switch_id))
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _resolve_goal_switch_path(path: str = "") -> Path:
    configured = (
        path.strip()
        or os.getenv("AQSP_GOAL_SWITCHES", "").strip()
        or str(_DEFAULT_GOAL_SWITCH_PATH)
    )
    return Path(configured)


def load_goal_switches(path: str = "") -> GoalSwitchMatrix:
    resolved = _resolve_goal_switch_path(path)
    if not resolved.exists():
        return GoalSwitchMatrix(
            version="",
            mode="",
            notes="",
            principles=(),
            switches=(),
            tracks=(),
        )

    data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    principles = tuple(
        (str(key), bool(value))
        for key, value in dict(data.get("principles") or {}).items()
    )
    switches = tuple(
        GoalSwitchState(
            switch_id=str(key),
            enabled=bool((value or {}).get("enabled", False)),
            purpose=str((value or {}).get("purpose", "")).strip(),
        )
        for key, value in dict(data.get("switches") or {}).items()
    )
    tracks = tuple(
        GoalTrackState(
            track_id=str(item.get("id", "")).strip(),
            label=str(item.get("label", "") or item.get("id", "")).strip(),
            enabled=bool(item.get("enabled", False)),
            priority=str(item.get("priority", "")).strip(),
            owner_lens=str(item.get("owner_lens", "")).strip(),
            deliverables=tuple(
                str(deliverable).strip()
                for deliverable in item.get("deliverables", ())
                if str(deliverable).strip()
            ),
        )
        for item in tuple(data.get("tracks") or ())
        if str(item.get("id", "")).strip()
    )
    return GoalSwitchMatrix(
        version=str(data.get("version", "")).strip(),
        mode=str(data.get("mode", "")).strip(),
        notes=str(data.get("notes", "")).strip(),
        principles=principles,
        switches=switches,
        tracks=tracks,
    )


def goal_switch_enabled(
    switch_id: str,
    *,
    default: bool = False,
    path: str = "",
) -> bool:
    matrix = load_goal_switches(path)
    return matrix.switch_enabled(switch_id, default=default)


def goal_switch_runtime_summary(path: str = "") -> str:
    matrix = load_goal_switches(path)
    if not matrix.switches:
        return ""
    history_only_label = (
        "开"
        if matrix.switch_enabled("historical_validation_only", default=True)
        else "关"
    )
    fallback_label = (
        "开"
        if matrix.switch_enabled("realtime_fallback_chain", default=True)
        else "关"
    )
    domestic_label = (
        "开"
        if matrix.switch_enabled("domestic_market_intelligence", default=True)
        else "关"
    )
    global_label = (
        "开"
        if matrix.switch_enabled("global_market_intelligence", default=True)
        else "关"
    )
    pit_label = (
        "必需"
        if matrix.switch_enabled("pit_enrichment_runtime_required", default=False)
        else "可缺省"
    )
    return (
        "运行边界: "
        f"历史验证专用 {history_only_label} / 回退链 {fallback_label} / "
        f"国内情报 {domestic_label} / 海外情报 {global_label} / PIT {pit_label}。"
    )


def goal_switch_visibility_notes(
    path: str = "",
    *,
    limit: int | None = None,
) -> tuple[str, ...]:
    matrix = load_goal_switches(path)
    notes: list[str] = []
    if not matrix.switch_enabled("realtime_fallback_chain", default=True):
        notes.append("实时回退链已关闭；未降级不代表备用源可用。")
    if not matrix.switch_enabled("domestic_market_intelligence", default=True):
        notes.append("国内情报已关闭；题材/政策/资金空白不等于当天无催化。")
    if not matrix.switch_enabled("global_market_intelligence", default=True):
        notes.append("海外情报已关闭；跨市空白不等于外盘无变化。")
    if limit is not None and limit >= 0:
        return tuple(notes[:limit])
    return tuple(notes)
