"""Realtime cross-market observation normalization and freshness gating.

Extracted from ``market_context.py`` to isolate the pure-function boundary
that converts injected realtime macro payloads into validated
``RealtimeCrossMarketContext`` objects.  The data layer owns network access;
this module owns freshness, status, and provenance validation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from aqsp.core.time import now_shanghai, to_shanghai

REALTIME_CROSS_MARKET_INSTRUMENTS: tuple[str, ...] = (
    "SPX",
    "NASDAQ100",
    "HSI",
    "DXY",
    "US10Y",
    "WTI",
    "GOLD",
)
RealtimeCrossMarketStatus = Literal["fresh", "stale", "timeout", "unavailable"]
RealtimeCrossMarketOverallStatus = Literal[
    "fresh", "partial", "stale", "timeout", "unavailable"
]


@dataclass(frozen=True)
class RealtimeCrossMarketPolicy:
    """Freshness and timeout gates for injected realtime market observations."""

    max_age_seconds: int = 15 * 60
    timeout_seconds: float = 5.0
    max_future_seconds: int = 5

    def __post_init__(self) -> None:
        if self.max_age_seconds <= 0:
            raise ValueError("max_age_seconds 必须大于 0")
        if self.timeout_seconds < 0:
            raise ValueError("timeout_seconds 不能小于 0")
        if self.max_future_seconds < 0:
            raise ValueError("max_future_seconds 不能小于 0")


@dataclass(frozen=True)
class RealtimeCrossMarketProvenance:
    source: str
    source_url: str
    observed_at: str
    fetched_at: str
    timestamp_source: str


@dataclass(frozen=True)
class RealtimeCrossMarketObservation:
    instrument: str
    status: RealtimeCrossMarketStatus
    value: float | None
    change_pct: float | None
    provenance: RealtimeCrossMarketProvenance
    age_seconds: int | None = None
    detail: str = ""

    @property
    def source(self) -> str:
        return self.provenance.source

    @property
    def observed_at(self) -> str:
        return self.provenance.observed_at

    @property
    def fetched_at(self) -> str:
        return self.provenance.fetched_at


@dataclass(frozen=True)
class RealtimeCrossMarketContext:
    generated_at: str
    status: RealtimeCrossMarketOverallStatus
    observations: tuple[RealtimeCrossMarketObservation, ...]
    warnings: tuple[str, ...] = ()

    @property
    def available_instruments(self) -> tuple[str, ...]:
        return tuple(
            item.instrument
            for item in self.observations
            if item.status == "fresh" and item.value is not None
        )


_DEFAULT_REALTIME_CROSS_MARKET_POLICY = RealtimeCrossMarketPolicy()
_REALTIME_CROSS_MARKET_ALIASES: dict[str, str] = {
    "SPX": "SPX",
    "SP500": "SPX",
    "S&P500": "SPX",
    "NASDAQ100": "NASDAQ100",
    "NASDAQ100INDEX": "NASDAQ100",
    "NDX": "NASDAQ100",
    "HSI": "HSI",
    "恒生": "HSI",
    "恒生指数": "HSI",
    "DXY": "DXY",
    "美元指数": "DXY",
    "US10Y": "US10Y",
    "US10": "US10Y",
    "UST10Y": "US10Y",
    "10Y": "US10Y",
    "美国10年期国债": "US10Y",
    "WTI": "WTI",
    "原油": "WTI",
    "WTICRUDE": "WTI",
    "GOLD": "GOLD",
    "COMEXGOLD": "GOLD",
    "COMEXGC": "GOLD",
    "GC=F": "GOLD",
    "SHANGHAIGOLD": "GOLD",
    "AU9999": "GOLD",
    "黄金": "GOLD",
    "上海金": "GOLD",
}


def build_realtime_cross_market_context(
    payload: Mapping[str, object] | None,
    *,
    now: datetime | None = None,
    policy: RealtimeCrossMarketPolicy = _DEFAULT_REALTIME_CROSS_MARKET_POLICY,
) -> RealtimeCrossMarketContext:
    """Normalize injected realtime macro observations without fetching data.

    ``payload`` is deliberately an input boundary: the data layer owns network
    access and timeout handling, while this pure function owns freshness,
    status, and provenance validation. Missing or unavailable values stay
    ``None`` and never become a numeric zero.
    """

    current = to_shanghai(now or now_shanghai())
    observations: list[RealtimeCrossMarketObservation] = []
    warnings: list[str] = []
    normalized_payload = _normalize_realtime_payload(payload)
    for instrument in REALTIME_CROSS_MARKET_INSTRUMENTS:
        observation, warning = _normalize_realtime_observation(
            instrument,
            normalized_payload.get(instrument),
            current=current,
            policy=policy,
        )
        observations.append(observation)
        if warning:
            warnings.append(warning)

    statuses = tuple(item.status for item in observations)
    fresh_count = sum(status == "fresh" for status in statuses)
    if fresh_count == len(observations):
        overall_status: RealtimeCrossMarketOverallStatus = "fresh"
    elif fresh_count > 0:
        overall_status = "partial"
    elif "timeout" in statuses:
        overall_status = "timeout"
    elif "stale" in statuses:
        overall_status = "stale"
    else:
        overall_status = "unavailable"
    return RealtimeCrossMarketContext(
        generated_at=current.isoformat(timespec="seconds"),
        status=overall_status,
        observations=tuple(observations),
        warnings=tuple(warnings),
    )


def _normalize_realtime_payload(
    payload: Mapping[str, object] | None,
) -> dict[str, object]:
    normalized: dict[str, object] = {}
    if payload is None:
        return normalized
    for key, value in payload.items():
        instrument = _canonical_realtime_instrument(key)
        if instrument and instrument not in normalized:
            normalized[instrument] = value
    return normalized


def _canonical_realtime_instrument(value: object) -> str:
    text = "".join(str(value or "").strip().upper().split())
    return _REALTIME_CROSS_MARKET_ALIASES.get(text, "")


def _normalize_realtime_observation(
    instrument: str,
    raw: object,
    *,
    current: datetime,
    policy: RealtimeCrossMarketPolicy,
) -> tuple[RealtimeCrossMarketObservation, str]:
    empty_provenance = RealtimeCrossMarketProvenance("", "", "", "", "")
    if not isinstance(raw, Mapping):
        detail = "未提供实时记录" if raw is None else "实时记录格式不可用"
        return (
            RealtimeCrossMarketObservation(
                instrument=instrument,
                status="unavailable",
                value=None,
                change_pct=None,
                provenance=empty_provenance,
                detail=detail,
            ),
            f"{instrument}: unavailable（{detail}）",
        )

    source = _text_value(raw, "source", "source_name")
    source_url = _text_value(raw, "source_url", "url")
    observed_at = _text_value(
        raw,
        "observed_at",
        "vendor_ts",
        "timestamp",
        "ts",
    )
    fetched_at = _text_value(raw, "fetched_at", "received_at")
    timestamp_source = _text_value(raw, "timestamp_source")
    if not timestamp_source:
        if raw.get("vendor_ts"):
            timestamp_source = "vendor"
        elif raw.get("received_at"):
            timestamp_source = "received_at"
        elif raw.get("observed_at"):
            timestamp_source = "observed_at"
    provenance = RealtimeCrossMarketProvenance(
        source=source,
        source_url=source_url,
        observed_at=observed_at,
        fetched_at=fetched_at,
        timestamp_source=timestamp_source,
    )
    requested_status = str(raw.get("status", "") or "").strip().casefold()
    if requested_status in {"timeout", "timed_out", "timedout"}:
        return _unavailable_observation(
            instrument,
            "timeout",
            provenance,
            detail="实时源超时",
        )
    if requested_status in {
        "unavailable",
        "failed",
        "failure",
        "error",
        "missing",
    }:
        return _unavailable_observation(
            instrument,
            "unavailable",
            provenance,
            detail="实时源不可用",
        )
    explicitly_stale = requested_status in {"stale", "expired", "old"}

    elapsed = _finite_float(
        raw.get("fetch_elapsed_seconds", raw.get("elapsed_seconds"))
    )
    if elapsed is not None and elapsed > policy.timeout_seconds:
        return _unavailable_observation(
            instrument,
            "timeout",
            provenance,
            detail=f"实时源耗时 {elapsed:.3f}s 超过 {policy.timeout_seconds:.3f}s",
        )

    value = _finite_float(raw.get("value", raw.get("price")))
    change_pct = _finite_float(raw.get("change_pct", raw.get("pct_change")))
    if value is None:
        return _unavailable_observation(
            instrument,
            "unavailable",
            provenance,
            detail="缺少有限数值",
        )
    observed_dt = _parse_realtime_timestamp(observed_at)
    fetched_dt = _parse_realtime_timestamp(fetched_at)
    if not source or observed_dt is None or fetched_dt is None:
        return _unavailable_observation(
            instrument,
            "unavailable",
            provenance,
            detail="来源或带时区时间戳缺失",
        )

    age_seconds = int((current - observed_dt).total_seconds())
    if age_seconds < -policy.max_future_seconds:
        return _unavailable_observation(
            instrument,
            "unavailable",
            provenance,
            detail=f"观测时间领先当前时间 {abs(age_seconds)}s",
        )
    if explicitly_stale or age_seconds > policy.max_age_seconds:
        observation = RealtimeCrossMarketObservation(
            instrument=instrument,
            status="stale",
            value=value,
            change_pct=change_pct,
            provenance=provenance,
            age_seconds=age_seconds,
            detail=f"观测数据已滞后 {age_seconds}s",
        )
        return observation, f"{instrument}: stale（滞后 {age_seconds}s）"

    observation = RealtimeCrossMarketObservation(
        instrument=instrument,
        status="fresh",
        value=value,
        change_pct=change_pct,
        provenance=provenance,
        age_seconds=max(0, age_seconds),
    )
    return observation, ""


def _unavailable_observation(
    instrument: str,
    status: Literal["timeout", "unavailable"],
    provenance: RealtimeCrossMarketProvenance,
    *,
    detail: str,
) -> tuple[RealtimeCrossMarketObservation, str]:
    observation = RealtimeCrossMarketObservation(
        instrument=instrument,
        status=status,
        value=None,
        change_pct=None,
        provenance=provenance,
        detail=detail,
    )
    return observation, f"{instrument}: {status}（{detail}）"


def _text_value(raw: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = str(raw.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_realtime_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return to_shanghai(parsed)
