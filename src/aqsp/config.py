from __future__ import annotations

import os
from dataclasses import dataclass

from aqsp.goal_switches import goal_switch_enabled


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None and value < minimum:
        return default
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None and value < minimum:
        return default
    return value


@dataclass(frozen=True)
class RuntimeConfig:
    symbols: tuple[str, ...]
    walkforward_symbols: tuple[str, ...]
    research_engine: str
    mode: str
    limit: int
    max_universe: int
    min_avg_amount: float
    max_data_lag_days: int
    enable_online_factors: bool
    allow_online_fallback: bool
    enable_debate: bool
    notify: bool
    notify_mode: str
    enable_auto_evolution: bool


@dataclass(frozen=True)
class DebateRuntimeConfig:
    task_id: str
    enabled: bool
    enable_llm: bool
    max_rounds: int
    max_candidates: int
    language: str
    requested_roles: tuple[str, ...]
    focus_roles: tuple[str, ...]
    disabled_roles: tuple[str, ...]
    roles: tuple[str, ...]
    role_runtime: tuple["DebateRoleRuntime", ...]
    explicit_roles: bool

    @property
    def context_roles_locked(self) -> bool:
        return self.explicit_roles


@dataclass(frozen=True)
class DebateRoleRuntime:
    role: str
    enable_llm: bool
    provider: str
    model: str


_TASK_DEBATE_ROLE_PRESETS: dict[str, tuple[str, ...]] = {
    "main_chain": (
        "bull",
        "risk_control",
        "sector_leader",
        "cross_market",
        "northbound",
    ),
    "morning_breakout": (
        "bull",
        "risk_control",
        "sector_leader",
        "cross_market",
        "northbound",
    ),
    "intraday": (
        "bull",
        "bear",
        "risk_control",
        "sector_leader",
        "cross_market",
        "policy_sensitive",
        "margin_trading",
        "northbound",
        "retail_mood",
    ),
    "closing_premium": (
        "bull",
        "bear",
        "risk_control",
        "sector_leader",
        "cross_market",
        "retail_mood",
    ),
    "closing_review": (
        "bull",
        "bear",
        "risk_control",
        "sector_leader",
        "cross_market",
        "policy_sensitive",
        "northbound",
    ),
    "briefing": (
        "bull",
        "bear",
        "risk_control",
        "sector_leader",
        "cross_market",
        "policy_sensitive",
        "northbound",
    ),
}


def online_fallback_allowed() -> bool:
    if not goal_switch_enabled("realtime_fallback_chain", default=True):
        return False
    return _env_flag("AQSP_ALLOW_ONLINE_FALLBACK", "true")


def _parse_role_mapping(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in raw.split(","):
        text = item.strip()
        if not text or ":" not in text:
            continue
        key, value = text.split(":", 1)
        role = key.strip().lower()
        mapped = value.strip()
        if role and mapped:
            mapping[role] = mapped
    return mapping


def _normalize_debate_task_id(task_id: str | None) -> str:
    value = str(task_id or os.getenv("AQSP_RUN_TASK_ID", "") or "").strip().lower()
    alias_map = {
        "morning": "morning_breakout",
        "midday": "intraday",
        "closing": "closing_premium",
        "daily": "closing_review",
        "scheduled": "closing_review",
    }
    return alias_map.get(value, value)


def _default_debate_roles_for_task(
    task_id: str | None,
    default_roles: tuple[str, ...],
) -> tuple[str, ...]:
    normalized_task_id = _normalize_debate_task_id(task_id)
    return _TASK_DEBATE_ROLE_PRESETS.get(normalized_task_id, default_roles)


def load_runtime_config() -> RuntimeConfig:
    symbols = tuple(
        item.strip()
        for item in os.getenv("AQSP_SYMBOLS", "").split(",")
        if item.strip()
    )
    walkforward_symbols = tuple(
        item.strip()
        for item in os.getenv("AQSP_WALKFORWARD_SYMBOLS", "").split(",")
        if item.strip()
    )
    return RuntimeConfig(
        symbols=symbols,
        walkforward_symbols=walkforward_symbols,
        research_engine=os.getenv("AQSP_RESEARCH_ENGINE", "auto").strip().lower()
        or "auto",
        mode=os.getenv("AQSP_MODE", "close").strip() or "close",
        limit=_env_int("AQSP_LIMIT", 10, minimum=1),
        max_universe=_env_int("AQSP_MAX_UNIVERSE", 0, minimum=0),
        min_avg_amount=_env_float("AQSP_MIN_AVG_AMOUNT", 50000000, minimum=0.0),
        max_data_lag_days=_env_int("AQSP_MAX_DATA_LAG_DAYS", 3, minimum=0),
        enable_online_factors=_env_flag("AQSP_ENABLE_ONLINE_FACTORS"),
        allow_online_fallback=online_fallback_allowed(),
        enable_debate=goal_switch_enabled(
            "multi_agent_advisory_layer",
            default=True,
        )
        and _env_flag("AQSP_ENABLE_DEBATE", "true"),
        notify=_env_flag("AQSP_NOTIFY"),
        notify_mode=os.getenv("AQSP_NOTIFY_MODE", "summary").strip().lower()
        or "summary",
        enable_auto_evolution=goal_switch_enabled(
            "auto_optimization_proposals",
            default=True,
        )
        and _env_flag("AQSP_ENABLE_AUTO_EVOLUTION"),
    )


def load_debate_runtime_config(task_id: str | None = None) -> DebateRuntimeConfig:
    from aqsp.briefing.agent_roles import (
        DEFAULT_RUNTIME_AGENT_ROLE_NAMES,
        select_runtime_agent_roles,
    )

    enabled = goal_switch_enabled(
        "multi_agent_advisory_layer", default=True
    ) and _env_flag("AQSP_ENABLE_DEBATE", "true")
    global_enable_llm = enabled and _env_flag("AQSP_DEBATE_ENABLE_LLM")
    max_rounds = max(1, _env_int("AQSP_DEBATE_MAX_ROUNDS", 2, minimum=1))
    max_candidates = max(1, _env_int("AQSP_DEBATE_MAX_CANDIDATES", 5, minimum=1))
    language = os.getenv("AQSP_DEBATE_LANGUAGE", "zh-CN").strip() or "zh-CN"
    normalized_task_id = _normalize_debate_task_id(task_id)
    explicit_roles = os.getenv("AQSP_DEBATE_ROLES")
    explicit_roles_enabled = explicit_roles is not None
    if explicit_roles is None:
        requested_roles = _default_debate_roles_for_task(
            normalized_task_id,
            DEFAULT_RUNTIME_AGENT_ROLE_NAMES,
        )
    else:
        requested_roles = tuple(
            item.strip().lower() for item in explicit_roles.split(",") if item.strip()
        )
    focus_roles = tuple(
        item.strip().lower()
        for item in os.getenv("AQSP_DEBATE_FOCUS_ROLES", "").split(",")
        if item.strip()
    )
    disabled_roles = tuple(
        item.strip().lower()
        for item in os.getenv("AQSP_DEBATE_DISABLED_ROLES", "").split(",")
        if item.strip()
    )
    roles = tuple(
        role.value
        for role in select_runtime_agent_roles(
            requested_roles,
            focus_roles=focus_roles,
            disabled_roles=disabled_roles,
        )
    )
    role_enable_map = _parse_role_mapping(os.getenv("AQSP_DEBATE_ROLE_LLM", ""))
    role_provider_map = _parse_role_mapping(os.getenv("AQSP_DEBATE_ROLE_PROVIDERS", ""))
    role_model_map = _parse_role_mapping(os.getenv("AQSP_DEBATE_ROLE_MODELS", ""))
    role_runtime = tuple(
        DebateRoleRuntime(
            role=role,
            enable_llm=(
                False
                if not global_enable_llm
                else (
                    global_enable_llm
                    if role not in role_enable_map
                    else role_enable_map[role].strip().lower()
                    in {"1", "true", "yes", "on"}
                )
            ),
            provider=role_provider_map.get(role, "").strip().lower(),
            model=role_model_map.get(role, "").strip(),
        )
        for role in roles
    )
    return DebateRuntimeConfig(
        task_id=normalized_task_id,
        enabled=enabled,
        enable_llm=global_enable_llm,
        max_rounds=max_rounds,
        max_candidates=max_candidates,
        language=language,
        requested_roles=requested_roles,
        focus_roles=focus_roles,
        disabled_roles=disabled_roles,
        roles=roles,
        role_runtime=role_runtime,
        explicit_roles=explicit_roles_enabled,
    )
