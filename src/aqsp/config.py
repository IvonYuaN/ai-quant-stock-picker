from __future__ import annotations

import os
from dataclasses import dataclass


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
    enabled: bool
    enable_llm: bool
    max_rounds: int
    language: str
    roles: tuple[str, ...]
    role_runtime: tuple["DebateRoleRuntime", ...]


@dataclass(frozen=True)
class DebateRoleRuntime:
    role: str
    enable_llm: bool
    provider: str
    model: str


def online_fallback_allowed() -> bool:
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
        enable_debate=_env_flag("AQSP_ENABLE_DEBATE"),
        notify=_env_flag("AQSP_NOTIFY"),
        notify_mode=os.getenv("AQSP_NOTIFY_MODE", "summary").strip().lower()
        or "summary",
        enable_auto_evolution=_env_flag("AQSP_ENABLE_AUTO_EVOLUTION"),
    )


def load_debate_runtime_config() -> DebateRuntimeConfig:
    from aqsp.briefing.agent_roles import DEFAULT_RUNTIME_AGENT_ROLE_NAMES

    roles = tuple(
        item.strip().lower()
        for item in os.getenv(
            "AQSP_DEBATE_ROLES",
            ",".join(DEFAULT_RUNTIME_AGENT_ROLE_NAMES),
        ).split(",")
        if item.strip()
    )
    global_enable_llm = _env_flag("AQSP_DEBATE_ENABLE_LLM")
    role_enable_map = _parse_role_mapping(os.getenv("AQSP_DEBATE_ROLE_LLM", ""))
    role_provider_map = _parse_role_mapping(os.getenv("AQSP_DEBATE_ROLE_PROVIDERS", ""))
    role_model_map = _parse_role_mapping(os.getenv("AQSP_DEBATE_ROLE_MODELS", ""))
    role_runtime = tuple(
        DebateRoleRuntime(
            role=role,
            enable_llm=(
                global_enable_llm
                if role not in role_enable_map
                else role_enable_map[role].strip().lower() in {"1", "true", "yes", "on"}
            ),
            provider=role_provider_map.get(role, "").strip().lower(),
            model=role_model_map.get(role, "").strip(),
        )
        for role in roles
    )
    return DebateRuntimeConfig(
        enabled=_env_flag("AQSP_ENABLE_DEBATE"),
        enable_llm=global_enable_llm,
        max_rounds=max(1, _env_int("AQSP_DEBATE_MAX_ROUNDS", 2, minimum=1)),
        language=os.getenv("AQSP_DEBATE_LANGUAGE", "zh-CN").strip() or "zh-CN",
        roles=roles,
        role_runtime=role_runtime,
    )
