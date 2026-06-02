from __future__ import annotations

import os
from dataclasses import dataclass


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeConfig:
    symbols: tuple[str, ...]
    walkforward_symbols: tuple[str, ...]
    mode: str
    limit: int
    max_universe: int
    min_avg_amount: float
    max_data_lag_days: int
    enable_online_factors: bool
    allow_online_fallback: bool
    enable_debate: bool
    notify: bool
    enable_auto_evolution: bool


@dataclass(frozen=True)
class DebateRuntimeConfig:
    enabled: bool
    enable_llm: bool
    max_rounds: int
    language: str
    roles: tuple[str, ...]


def online_fallback_allowed() -> bool:
    return _env_flag("AQSP_ALLOW_ONLINE_FALLBACK", "true")


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
        mode=os.getenv("AQSP_MODE", "close").strip() or "close",
        limit=int(os.getenv("AQSP_LIMIT", "10")),
        max_universe=int(os.getenv("AQSP_MAX_UNIVERSE", "100")),
        min_avg_amount=float(os.getenv("AQSP_MIN_AVG_AMOUNT", "50000000")),
        max_data_lag_days=int(os.getenv("AQSP_MAX_DATA_LAG_DAYS", "3")),
        enable_online_factors=_env_flag("AQSP_ENABLE_ONLINE_FACTORS"),
        allow_online_fallback=online_fallback_allowed(),
        enable_debate=_env_flag("AQSP_ENABLE_DEBATE"),
        notify=_env_flag("AQSP_NOTIFY"),
        enable_auto_evolution=_env_flag("AQSP_ENABLE_AUTO_EVOLUTION"),
    )


def load_debate_runtime_config() -> DebateRuntimeConfig:
    roles = tuple(
        item.strip().lower()
        for item in os.getenv(
            "AQSP_DEBATE_ROLES",
            "bull,bear,risk_control,sector_leader,policy_sensitive,northbound",
        ).split(",")
        if item.strip()
    )
    return DebateRuntimeConfig(
        enabled=_env_flag("AQSP_ENABLE_DEBATE"),
        enable_llm=_env_flag("AQSP_DEBATE_ENABLE_LLM"),
        max_rounds=max(1, int(os.getenv("AQSP_DEBATE_MAX_ROUNDS", "2"))),
        language=os.getenv("AQSP_DEBATE_LANGUAGE", "zh-CN").strip() or "zh-CN",
        roles=roles,
    )
