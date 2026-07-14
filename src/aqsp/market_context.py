from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from aqsp.core.time import now_shanghai, today_shanghai, to_shanghai
from aqsp.core.types import PickResult
from aqsp.goal_switches import goal_switch_enabled
from aqsp.news.catalysts import CatalystEvent, CatalystReport, Impact

_NORTHBOUND_STRONG_Z = 1.0
_MARGIN_STRONG_CHANGE = 0.03
_CROSS_MARKET_STACK_SUPPORT_BONUS = 2
_CROSS_MARKET_STACK_CONFLICT_PENALTY = 2
_CROSS_MARKET_STRONG_SCORE = 3
_CROSS_MARKET_MEDIUM_SCORE = 1
_NEWS_DIRECT_STRONG_SCORE = 3
_NEWS_DIRECT_MEDIUM_SCORE = 2
_NEWS_DIRECT_WEAK_SCORE = 1
_DEFAULT_ACTIONABLE_NEWS_AGE_MINUTES = 12 * 60
_ACTIONABLE_NEWS_MIN_SOURCE_QUALITY = 2
_AUTHORITATIVE_SOURCE_TOKENS = (
    "公告",
    "交易所",
    "巨潮",
    "公司",
    "证监会",
    "SEC",
    "FederalReserve",
    "Federal Reserve",
    "ECB",
    "NASA",
)
_PRIORITY_MEDIA_SOURCE_TOKENS = ("新华社", "央视", "国常会", "发改委", "工信部")
_MAINSTREAM_MEDIA_SOURCE_TOKENS = (
    "财联社",
    "证券报",
    "东财",
    "同花顺",
    "新浪",
    "路透",
    "彭博",
    "Reuters",
    "Bloomberg",
    "NVIDIA",
    "MarketWatch",
)

# Deterministic issuer tags fill the sector gap of realtime quote sources.
# They only enable event-to-industry relevance; score changes remain governed
# by the existing evidence-quality and threshold gates below.
_SYMBOL_THEME_TAGS: dict[str, tuple[str, ...]] = {
    "603019": ("算力", "ai", "边缘计算"),
    "600879": ("商业航天", "卫星", "军工电子"),
    "603893": ("ai芯片", "边缘计算", "芯片"),
    "600276": ("创新药",),
    "600150": ("军工", "船舶"),
    "000034": ("算力", "ai", "云计算"),
    "000066": ("信创", "国产算力", "军工电子"),
    "000977": ("算力", "ai", "服务器"),
    "000938": ("算力", "ai", "云计算"),
    "688981": ("半导体", "芯片", "先进制程"),
    "000063": ("通信设备", "算力", "服务器"),
    "300604": ("半导体设备", "芯片", "设备"),
}


@dataclass(frozen=True)
class CrossMarketImplicationRule:
    rule_id: str
    keywords: tuple[str, ...]
    theme: str
    linkage_basis: str
    supportive_impacts: tuple[Impact, ...]
    a_share_targets: tuple[str, ...]
    first_order_targets: tuple[str, ...]
    second_order_targets: tuple[str, ...]
    pressure_targets: tuple[str, ...]
    execution_watchpoints: tuple[str, ...]
    relevance_keywords: tuple[str, ...]
    lead_window: str
    observation_window: str
    transmission_path: tuple[str, ...]
    validation_signals: tuple[str, ...]
    invalidation_signals: tuple[str, ...]
    confirmation_hint: str
    required_keyword_groups: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class CrossMarketImplication:
    rule_id: str
    theme: str
    linkage_basis: str
    a_share_targets: tuple[str, ...]
    first_order_targets: tuple[str, ...]
    second_order_targets: tuple[str, ...]
    pressure_targets: tuple[str, ...]
    execution_watchpoints: tuple[str, ...]
    relevance_keywords: tuple[str, ...]
    lead_window: str
    observation_window: str
    transmission_path: tuple[str, ...]
    validation_signals: tuple[str, ...]
    invalidation_signals: tuple[str, ...]
    confirmation_hint: str
    strength: Literal["强", "中", "弱"]
    action: str
    source_title: str
    source_category: str
    source_quality_label: str
    source_quality_score: int
    source_published_at: str
    support_event_count: int
    conflict_event_count: int
    evidence_stack_summary: str
    evidence_points: tuple[str, ...]
    summary_line: str
    affected_sectors: tuple[str, ...] = ()
    affected_symbols: tuple[str, ...] = ()
    transmission_hypothesis: str = ""
    confidence: float = 0.0
    time_horizon: str = ""
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    source_regions: tuple[str, ...] = ()
    impact_direction: Literal["positive", "negative", "mixed", "neutral"] = "neutral"
    source_url: str = ""
    source_fetched_at: str = ""


@dataclass(frozen=True)
class MarketContextArtifact:
    date: str
    generated_at: str
    source_status: str
    summary_lines: tuple[str, ...]
    cross_market_implications: tuple[CrossMarketImplication, ...] = ()
    cross_market_overview: str = ""
    warnings: tuple[str, ...] = ()
    catalyst_events: tuple[CatalystEvent, ...] = ()
    news_status: str = ""
    realtime_cross_market: RealtimeCrossMarketContext | None = None


@dataclass(frozen=True)
class PickMarketContext:
    symbol: str
    primary_theme: str
    linkage_basis: str
    primary_action: str
    primary_strength: str
    primary_source_quality_label: str
    primary_source_quality_score: int
    lead_window: str
    observation_window: str
    priority_score: int
    themes: tuple[str, ...]
    rule_ids: tuple[str, ...]
    first_order_targets: tuple[str, ...]
    second_order_targets: tuple[str, ...]
    pressure_targets: tuple[str, ...]
    execution_watchpoints: tuple[str, ...]
    transmission_path: tuple[str, ...]
    validation_signals: tuple[str, ...]
    invalidation_signals: tuple[str, ...]
    chain_summary: str
    support_event_count: int
    conflict_event_count: int
    evidence_stack_summary: str
    summary_lines: tuple[str, ...]
    affected_sectors: tuple[str, ...] = ()
    affected_symbols: tuple[str, ...] = ()
    transmission_hypothesis: str = ""
    confidence: float = 0.0
    time_horizon: str = ""
    supporting_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    source_regions: tuple[str, ...] = ()
    impact_direction: Literal["positive", "negative", "mixed", "neutral"] = "neutral"
    source_url: str = ""
    source_fetched_at: str = ""


@dataclass(frozen=True)
class CrossMarketRuleRuntimeSummary:
    domestic_enabled: bool
    global_enabled: bool
    rule_count: int
    core_rule_ids: tuple[str, ...]
    rule_themes: tuple[str, ...]
    advisory_boundary: str


REALTIME_CROSS_MARKET_INSTRUMENTS: tuple[str, ...] = (
    "SPX",
    "NASDAQ100",
    "HSI",
    "DXY",
    "US10Y",
    "WTI",
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


_CROSS_MARKET_RULES: tuple[CrossMarketImplicationRule, ...] = (
    CrossMarketImplicationRule(
        rule_id="commercial_space",
        keywords=(
            "spacex",
            "space x",
            "starlink",
            "星链",
            "商业航天",
            "卫星",
            "火箭",
            "发射",
            "低轨",
        ),
        required_keyword_groups=(
            (
                "spacex",
                "space x",
                "starlink",
                "星链",
                "商业航天",
                "卫星",
                "火箭",
                "低轨",
            ),
        ),
        theme="海外商业航天催化",
        linkage_basis="题材映射",
        supportive_impacts=("positive",),
        a_share_targets=("商业航天", "卫星互联网", "军工电子"),
        first_order_targets=("商业航天龙头", "卫星互联网/低轨组网", "火箭发射配套"),
        second_order_targets=("军工电子", "通信设备", "高端制造"),
        pressure_targets=(),
        execution_watchpoints=(
            "商业航天龙头竞价强度与换手承接",
            "卫星互联网扩散家数",
            "军工电子和通信设备是否跟随补涨",
        ),
        relevance_keywords=("航天", "卫星", "军工", "火箭", "低轨", "通信设备"),
        lead_window="隔夜-2日",
        observation_window="2-5日",
        transmission_path=(
            "SpaceX IPO、估值融资或重大发射先抬升海外商业航天风险偏好",
            "A股商业航天、卫星互联网与低轨组网龙头先反应",
            "若扩散到军工电子、通信设备和高端制造，持续性更强",
        ),
        validation_signals=(
            "商业航天龙头高开后仍有放量换手承接",
            "卫星互联网、低轨组网与火箭配套同步扩散",
            "军工电子出现跟随补涨而非单点脉冲",
        ),
        invalidation_signals=(
            "只有 SpaceX 新闻刺激但A股商业航天家数不扩散",
            "龙头高开低走且换手承接衰减",
            "主线资金迅速切回别的热门题材",
        ),
        confirmation_hint="先看商业航天龙头换手承接、卫星链扩散和军工电子跟随。",
    ),
    CrossMarketImplicationRule(
        rule_id="domestic_policy_stimulus",
        keywords=(
            "国常会",
            "发改委",
            "工信部",
            "专项债",
            "财政政策",
            "财政加力",
            "稳增长",
            "设备更新",
            "以旧换新",
            "低空经济",
            "消费刺激",
            "政策细则",
            "地方跟进",
        ),
        required_keyword_groups=(
            (
                "设备更新",
                "工业母机",
                "机器人",
                "自动化",
                "以旧换新",
                "家电",
                "汽车",
                "低空经济",
                "专项债",
                "财政政策",
                "财政加力",
                "稳增长",
                "消费刺激",
                "基建",
            ),
        ),
        theme="国内政策催化",
        linkage_basis="政策预期差映射",
        supportive_impacts=("positive",),
        a_share_targets=("设备更新", "低空经济", "汽车家电", "基建链", "机器人"),
        first_order_targets=("设备更新", "低空经济", "汽车家电以旧换新"),
        second_order_targets=("工业母机/机器人", "工程机械", "基建链", "充电桩"),
        pressure_targets=("纯防御高股息",),
        execution_watchpoints=(
            "政策受益龙头竞价强度与封单/换手承接",
            "设备更新、低空经济和以旧换新是否扩散",
            "地方细则或部委后续文件是否继续跟进",
        ),
        relevance_keywords=(
            "设备更新",
            "工业母机",
            "机器人",
            "自动化",
            "低空经济",
            "家电",
            "汽车",
            "工程机械",
            "基建",
            "充电桩",
            "消费",
        ),
        lead_window="当日-次日",
        observation_window="1-5日",
        transmission_path=(
            "国常会、部委或财政政策释放稳增长和产业支持预期",
            "A股设备更新、低空经济、汽车家电以旧换新先获得映射资金",
            "若地方细则和资金安排跟进，行情从龙头扩散到工程机械、机器人和基建链",
        ),
        validation_signals=(
            "政策受益龙头竞价强且开盘后仍有换手承接",
            "设备更新、低空经济、汽车家电至少两个方向同步扩散",
            "部委细则、地方方案或资金安排继续跟进",
        ),
        invalidation_signals=(
            "只有口号没有细则或资金安排",
            "龙头高开低走且板块家数不扩散",
            "资金仍停留在防御方向，政策线未形成风险偏好切换",
        ),
        confirmation_hint="先看政策受益龙头承接，再看细则跟进和板块扩散。",
    ),
    CrossMarketImplicationRule(
        rule_id="physical_ai",
        keywords=(
            "英伟达",
            "nvidia",
            "physical ai",
            "物理ai",
            "具身",
            "机器人",
            "humanoid",
            "embodied",
        ),
        required_keyword_groups=(
            (
                "physical ai",
                "物理ai",
                "具身",
                "humanoid",
                "embodied",
            ),
        ),
        theme="海外物理AI叙事升温",
        linkage_basis="产业映射",
        supportive_impacts=("positive",),
        a_share_targets=("机器人", "AI算力", "传感器", "丝杠", "减速器", "工控链"),
        first_order_targets=("机器人整机", "AI算力/边缘计算", "丝杠/减速器", "传感器"),
        second_order_targets=("工控", "机器视觉", "伺服", "算力芯片"),
        pressure_targets=(),
        execution_watchpoints=(
            "机器人龙头放量强度",
            "丝杠减速器是否同步走强",
            "是否有订单或产业催化继续跟进",
        ),
        relevance_keywords=(
            "机器人",
            "传感器",
            "丝杠",
            "减速器",
            "工控",
            "算力",
            "ai芯片",
            "边缘计算",
            "自动化",
            "具身",
            "机器视觉",
            "伺服",
        ),
        lead_window="隔夜-3日",
        observation_window="2-5日",
        transmission_path=(
            "海外大厂发布 Physical AI 或具身新平台",
            "A股机器人、边缘算力与核心零部件先获得映射资金",
            "若订单逻辑、算力链与工控链共振，主题可延续数日",
        ),
        validation_signals=(
            "机器人龙头放量上攻且核心零部件同步走强",
            "AI算力或边缘计算分支同步放量扩散",
            "丝杠减速器传感器不是单一分支独涨",
            "盘中有产业催化或订单消息继续验证",
        ),
        invalidation_signals=(
            "只有海外叙事但A股机器人板块不共振",
            "映射只停留在高开冲动，午后承接消失",
            "零部件和整机分化严重，难形成主线接力",
        ),
        confirmation_hint="优先看有订单、放量和产业催化验证的环节。",
    ),
    CrossMarketImplicationRule(
        rule_id="us_risk_on",
        keywords=(
            "美股大涨",
            "纳斯达克",
            "nasdaq",
            "标普",
            "spx",
            "风险资产反弹",
            "科技股反弹",
            "risk-on",
        ),
        required_keyword_groups=(
            ("美股", "纳斯达克", "nasdaq", "标普", "spx", "科技股", "风险资产"),
            ("大涨", "反弹", "修复", "走强", "risk-on"),
        ),
        theme="外盘风险偏好修复",
        linkage_basis="风险偏好映射",
        supportive_impacts=("positive",),
        a_share_targets=("成长", "高弹性", "AI链"),
        first_order_targets=("AI链高弹性", "算力/芯片", "机器人成长"),
        second_order_targets=("软件", "半导体设备", "科创弹性标的"),
        pressure_targets=("高股息防御",),
        execution_watchpoints=(
            "次日竞价成长方向是否强于防御",
            "北向回流力度",
            "科技权重承接与量能是否同步",
        ),
        relevance_keywords=(
            "成长",
            "高弹性",
            "ai",
            "人工智能",
            "科技",
            "算力",
            "芯片",
            "半导体",
            "软件",
            "机器人",
        ),
        lead_window="次日竞价-1日",
        observation_window="次日-3日",
        transmission_path=(
            "美股科技与风险资产先修复风险偏好",
            "A股高弹性成长与AI链在竞价和早盘先反馈",
            "若北向回流并伴随量能放大，修复可延续到2-3日",
        ),
        validation_signals=(
            "次日竞价高弹性方向明显强于防御方向",
            "北向资金回流且科技权重承接稳定",
            "AI链和高弹性成长出现板块级放量",
        ),
        invalidation_signals=(
            "美股强但A股竞价无明显风险偏好跟随",
            "北向继续流出导致开盘后快速回落",
            "高弹性方向仅个股脉冲，缺少板块扩散",
        ),
        confirmation_hint="优先看竞价情绪、北向反馈和高弹性方向承接。",
    ),
    CrossMarketImplicationRule(
        rule_id="global_liquidity_easing",
        keywords=(
            "降息",
            "降息预期升温",
            "降息交易",
            "美债收益率下行",
            "美债利率下行",
            "美元走弱",
            "美联储鸽派",
            "鸽派表态",
            "rate cut",
            "dovish",
            "treasury yields fall",
            "dollar weakens",
        ),
        required_keyword_groups=(),
        theme="全球流动性宽松交易",
        linkage_basis="贴现率与风险偏好映射",
        supportive_impacts=("positive",),
        a_share_targets=("成长", "AI链", "黄金", "有色金属", "创新药"),
        first_order_targets=("高弹性成长", "AI链/算力", "黄金/有色"),
        second_order_targets=("创新药", "港股映射", "券商弹性"),
        pressure_targets=("银行息差", "高股息防御"),
        execution_watchpoints=(
            "成长和AI链竞价是否强于防御",
            "黄金有色是否跟随美元走弱同步走强",
            "银行与高股息是否相对承压",
        ),
        relevance_keywords=(
            "成长",
            "ai",
            "人工智能",
            "算力",
            "芯片",
            "半导体",
            "黄金",
            "有色",
            "贵金属",
            "创新药",
            "券商",
        ),
        lead_window="隔夜-2日",
        observation_window="1-3日",
        transmission_path=(
            "美联储鸽派、降息交易或美债收益率下行先改善全球流动性预期",
            "A股高弹性成长、AI链与黄金有色先获得估值和商品双重映射",
            "若银行高股息相对承压且北向回流，流动性交易更容易延续",
        ),
        validation_signals=(
            "成长和AI链竞价强于高股息防御",
            "黄金有色跟随美元走弱和美债收益率下行同步放量",
            "北向资金回流且科技权重承接稳定",
        ),
        invalidation_signals=(
            "降息交易未传导到A股，成长方向竞价弱于防御",
            "美元或美债收益率反向走强，黄金有色冲高回落",
            "银行高股息继续强于成长，说明市场仍在防御定价",
        ),
        confirmation_hint="先看成长/AI链相对强度，再看黄金有色和北向资金是否共振。",
    ),
    CrossMarketImplicationRule(
        rule_id="chip_export_controls",
        keywords=(
            "出口管制",
            "禁售",
            "实体清单",
            "断供",
            "关税",
            "tariff",
            "export control",
            "entity list",
            "sanction",
            "restriction",
        ),
        required_keyword_groups=(
            (
                "芯片",
                "半导体",
                "算力",
                "gpu",
                "h20",
                "h100",
                "先进制程",
                "光刻",
                "eda",
                "服务器",
                "server",
                "ai",
            ),
        ),
        theme="海外芯片限制升级",
        linkage_basis="供应链重定价",
        supportive_impacts=("negative",),
        a_share_targets=("半导体设备", "半导体材料", "国产算力", "信创"),
        first_order_targets=("半导体设备", "半导体材料", "国产算力"),
        second_order_targets=("EDA/IP", "先进封装", "军工电子"),
        pressure_targets=("苹果链", "出口代工"),
        execution_watchpoints=(
            "半导体设备与国产算力是否同步放量",
            "苹果链与出口代工是否明显承压",
            "自主可控是否从设备扩散到材料与信创",
        ),
        relevance_keywords=(
            "半导体",
            "芯片",
            "设备",
            "材料",
            "国产算力",
            "信创",
            "先进封装",
            "eda",
            "ip",
            "军工电子",
        ),
        lead_window="隔夜-3日",
        observation_window="2-5日",
        transmission_path=(
            "海外芯片限制或关税升级先扰动全球科技供应链预期",
            "A股半导体设备材料与国产算力先获得自主可控映射资金",
            "若苹果链与出口代工承压，自主可控主线延续性更强",
        ),
        validation_signals=(
            "半导体设备材料与国产算力同步放量而非单点脉冲",
            "自主可控从设备扩散到材料、先进封装或信创",
            "苹果链与出口代工承压，说明资金完成切换",
        ),
        invalidation_signals=(
            "只有消息刺激但半导体设备材料不扩散",
            "自主可控高开后快速回落，苹果链并未承压",
            "市场把消息仅当情绪噪音，未形成板块级共振",
        ),
        confirmation_hint="先看设备材料与国产算力是否同步共振。",
    ),
    CrossMarketImplicationRule(
        rule_id="global_supply_tightening",
        keywords=(
            "涨价",
            "提价",
            "报价上调",
            "缺货",
            "供不应求",
            "供给收缩",
            "停产",
            "限产",
            "库存低位",
        ),
        required_keyword_groups=(
            (
                "dram",
                "nand",
                "hbm",
                "wafer",
                "panel",
                "memory",
                "存储",
                "半导体",
                "芯片",
                "封装",
                "pcb",
                "覆铜板",
                "面板",
            ),
        ),
        theme="海外供给收缩映射",
        linkage_basis="供需缺口映射",
        supportive_impacts=("positive",),
        a_share_targets=("存储", "半导体材料", "先进封装", "PCB"),
        first_order_targets=("存储", "半导体材料", "先进封装"),
        second_order_targets=("PCB", "覆铜板", "面板"),
        pressure_targets=("消费电子代工", "下游整机"),
        execution_watchpoints=(
            "存储与半导体材料是否同步放量",
            "先进封装与PCB是否出现扩散",
            "消费电子代工和下游整机是否承压",
        ),
        relevance_keywords=(
            "存储",
            "半导体材料",
            "先进封装",
            "封装",
            "pcb",
            "覆铜板",
            "面板",
            "消费电子",
        ),
        lead_window="隔夜-2日",
        observation_window="2-5日",
        transmission_path=(
            "海外供给收缩或涨价先抬升相关原件与材料报价预期",
            "A股存储、半导体材料与先进封装先获得映射资金",
            "若扩散到PCB覆铜板且消费电子承压，主题持续性更强",
        ),
        validation_signals=(
            "存储与半导体材料同步放量而非单一环节独涨",
            "先进封装与PCB出现扩散，说明成本传导被市场认可",
            "消费电子代工与下游整机承压，资金切向上游弹性",
        ),
        invalidation_signals=(
            "只有消息刺激但存储材料不扩散",
            "上游高开后快速回落，消费电子链并未承压",
            "市场把涨价消息当成短脉冲，缺少板块级共振",
        ),
        confirmation_hint="先看上游材料、存储与封装是否一起共振。",
    ),
    CrossMarketImplicationRule(
        rule_id="oil_price_shock",
        keywords=(
            "油价大涨",
            "油价飙升",
            "原油大涨",
            "原油价格上涨",
            "布伦特原油",
            "wti",
            "brent",
            "crude oil",
            "opec",
            "减产",
            "原油供应",
            "能源价格",
        ),
        required_keyword_groups=(),
        theme="国际油价冲击",
        linkage_basis="商品价格与成本映射",
        supportive_impacts=("positive", "negative"),
        a_share_targets=("油气", "煤化工", "航运", "资源品"),
        first_order_targets=("油气开采", "油服", "煤炭/煤化工"),
        second_order_targets=("航运", "资源品", "通胀受益链"),
        pressure_targets=("航空", "下游化工", "消费运输"),
        execution_watchpoints=(
            "油气和油服是否同步放量",
            "航空与下游化工是否相对承压",
            "煤炭煤化工是否跟随能源价格扩散",
        ),
        relevance_keywords=(
            "油气",
            "石油",
            "油服",
            "煤炭",
            "煤化工",
            "航运",
            "资源品",
            "航空",
            "化工",
            "能源",
        ),
        lead_window="当日-次日",
        observation_window="1-3日",
        transmission_path=(
            "国际油价或OPEC减产消息先改变能源价格预期",
            "A股油气、油服和煤化工先获得价格弹性映射",
            "若航空与下游化工承压，资金更容易向上游能源链集中",
        ),
        validation_signals=(
            "油气和油服同步放量而非单一龙头脉冲",
            "煤炭煤化工跟随走强，能源链形成扩散",
            "航空和下游化工相对承压，说明成本传导被市场定价",
        ),
        invalidation_signals=(
            "油价冲高回落或减产预期被证伪",
            "A股油气只有高开冲动，油服煤化工不扩散",
            "航空和下游化工不承压，说明成本压力未被交易",
        ),
        confirmation_hint="先看油气油服共振，再看煤化工扩散和航空化工承压。",
    ),
    CrossMarketImplicationRule(
        rule_id="geopolitics",
        keywords=(
            "打仗",
            "战争",
            "冲突",
            "袭击",
            "中东",
            "停火破裂",
            "地缘",
            "middle east",
            "geopolitical",
            "geopolitical risk",
            "military",
            "attack",
            "war",
        ),
        required_keyword_groups=(
            (
                "打仗",
                "战争",
                "冲突",
                "袭击",
                "中东",
                "停火破裂",
                "地缘",
                "middle east",
                "geopolitical",
                "geopolitical risk",
                "military",
                "attack",
                "war",
            ),
            (
                "黄金",
                "贵金属",
                "军工",
                "油气",
                "能源",
                "避险",
                "原油",
                "航运",
                "资源品",
                "gold",
                "precious metals",
                "defense",
                "defence",
                "safe haven",
                "oil",
                "crude",
                "energy",
            ),
        ),
        theme="地缘冲突升温",
        linkage_basis="避险定价映射",
        supportive_impacts=("negative",),
        a_share_targets=("黄金", "军工", "能源链"),
        first_order_targets=("黄金", "军工", "油气"),
        second_order_targets=("航运", "资源品"),
        pressure_targets=("高beta成长", "风险偏好题材"),
        execution_watchpoints=(
            "黄金军工油气是否至少两个方向共振",
            "成长高beta是否明显承压",
            "外盘避险资产或商品价格是否继续强化",
        ),
        relevance_keywords=(
            "黄金",
            "贵金属",
            "军工",
            "油气",
            "能源",
            "航运",
            "gold",
            "defense",
            "defence",
            "safe haven",
            "oil",
            "energy",
        ),
        lead_window="当日-次日",
        observation_window="1-3日",
        transmission_path=(
            "地缘冲突先抬升避险与资源品定价",
            "A股黄金军工油气先成为情绪承接方向",
            "若成长承压且避险链扩散，短线持续性提升",
        ),
        validation_signals=(
            "黄金军工油气三个方向至少两个同步走强",
            "成长高beta开盘承压，资金明显切向避险链",
            "商品价格或海外避险资产继续强化",
        ),
        invalidation_signals=(
            "消息很快降温或停火预期回升",
            "A股避险链只有单一板块脉冲",
            "成长方向未受压制，说明资金未完成切换",
        ),
        confirmation_hint="先看避险链强度，再防范成长和高beta承压。",
    ),
)


def cross_market_rule_runtime_summary(
    *,
    enable_domestic_intelligence: bool | None = None,
    enable_global_intelligence: bool | None = None,
) -> CrossMarketRuleRuntimeSummary:
    domestic_enabled = (
        enable_domestic_intelligence
        if enable_domestic_intelligence is not None
        else goal_switch_enabled("domestic_market_intelligence", default=True)
    )
    global_enabled = (
        enable_global_intelligence
        if enable_global_intelligence is not None
        else goal_switch_enabled("global_market_intelligence", default=True)
    )
    rules_by_id = {rule.rule_id: rule for rule in _CROSS_MARKET_RULES}
    core_rule_ids = tuple(
        rule_id
        for rule_id in (
            "commercial_space",
            "physical_ai",
            "geopolitics",
            "us_risk_on",
            "global_liquidity_easing",
            "oil_price_shock",
        )
        if rule_id in rules_by_id
    )
    rule_themes = tuple(rules_by_id[rule_id].theme for rule_id in core_rule_ids)
    boundary = (
        "deterministic_context_priority_only"
        if global_enabled
        else "global_market_intelligence_disabled"
    )
    return CrossMarketRuleRuntimeSummary(
        domestic_enabled=domestic_enabled,
        global_enabled=global_enabled,
        rule_count=len(_CROSS_MARKET_RULES) if global_enabled else 0,
        core_rule_ids=core_rule_ids if global_enabled else (),
        rule_themes=rule_themes if global_enabled else (),
        advisory_boundary=boundary,
    )


def cross_market_rule_runtime_lines() -> tuple[str, ...]:
    summary = cross_market_rule_runtime_summary()
    return (
        f"- market_context_domestic_enabled: {summary.domestic_enabled}",
        f"- market_context_global_enabled: {summary.global_enabled}",
        f"- cross_market_rule_count: {summary.rule_count}",
        f"- cross_market_core_rules: {','.join(summary.core_rule_ids) or '-'}",
        f"- cross_market_rule_themes: {'；'.join(summary.rule_themes) or '-'}",
        f"- cross_market_boundary: {summary.advisory_boundary}",
    )


def build_market_context_artifact(
    *,
    catalyst_report: CatalystReport | None,
    northbound_flow_5d_z: float = 0.0,
    margin_balance_change_5d: float = 0.0,
    enable_domestic_intelligence: bool | None = None,
    enable_global_intelligence: bool | None = None,
    max_actionable_news_age_minutes: int = _DEFAULT_ACTIONABLE_NEWS_AGE_MINUTES,
    realtime_cross_market: Mapping[str, object] | None = None,
    realtime_now: datetime | None = None,
    realtime_policy: RealtimeCrossMarketPolicy = _DEFAULT_REALTIME_CROSS_MARKET_POLICY,
) -> MarketContextArtifact:
    domestic_enabled = (
        enable_domestic_intelligence
        if enable_domestic_intelligence is not None
        else goal_switch_enabled("domestic_market_intelligence", default=True)
    )
    global_enabled = (
        enable_global_intelligence
        if enable_global_intelligence is not None
        else goal_switch_enabled("global_market_intelligence", default=True)
    )
    lines: list[str] = []
    warnings: tuple[str, ...] = ()
    source_status = "not_loaded"
    global_events: list[CatalystEvent] = []
    domestic_events: list[CatalystEvent] = []
    symbol_events: list[CatalystEvent] = []
    cross_market_implications: tuple[CrossMarketImplication, ...] = ()
    realtime_context = (
        build_realtime_cross_market_context(
            realtime_cross_market,
            now=realtime_now,
            policy=realtime_policy,
        )
        if realtime_cross_market is not None
        else None
    )

    if catalyst_report is not None:
        source_status = catalyst_report.source_status
        warnings = tuple(
            str(item) for item in catalyst_report.warnings if str(item).strip()
        )
        actionable_events, gate_warnings = _actionable_catalyst_events(
            catalyst_report.events,
            generated_at=catalyst_report.generated_at,
            max_age_minutes=max_actionable_news_age_minutes,
        )
        warnings = tuple(_dedupe_texts((*warnings, *gate_warnings)))
        symbol_events = [
            event for event in actionable_events if event.symbol and domestic_enabled
        ]
        domestic_events = [
            event
            for event in actionable_events
            if not event.symbol
            and str(getattr(event, "source_region", "mixed") or "mixed")
            .strip()
            .casefold()
            == "domestic"
            and domestic_enabled
        ]
        global_events = [
            event
            for event in actionable_events
            if not event.symbol
            and str(getattr(event, "source_region", "mixed") or "mixed")
            .strip()
            .casefold()
            != "domestic"
            and global_enabled
        ]
        if symbol_events:
            lines.append(
                "个股催化: "
                + "；".join(_event_brief(event) for event in symbol_events[:2])
            )
        if global_events:
            lines.append(
                "全局雷达: "
                + "；".join(_event_brief(event) for event in global_events[:2])
            )
            source_quality_line = _source_quality_summary_line(global_events)
            if source_quality_line:
                lines.append(source_quality_line)
            global_risk_line = _global_risk_line(global_events)
            if global_risk_line:
                lines.append(global_risk_line)
        if domestic_events:
            lines.append(
                "国内雷达: "
                + "；".join(_event_brief(event) for event in domestic_events[:2])
            )
        cross_market_events = [*domestic_events, *global_events]
        if cross_market_events:
            cross_market_implications = _cross_market_implications(
                cross_market_events,
                generated_at=catalyst_report.generated_at,
            )
            lines.extend(
                implication.summary_line
                for implication in cross_market_implications[:3]
            )
        if catalyst_report.source_status != "ok":
            lines.append(
                f"消息状态: {_source_status_text(catalyst_report.source_status)}"
            )
        if not actionable_events:
            lines.append(
                f"消息结果: {_catalyst_result_status_text(catalyst_report.news_status, warnings)}"
            )
        warning_line = _market_context_warning_line(warnings)
        if warning_line:
            lines.append(warning_line)
        freshness_line = _event_freshness_line(
            events=tuple((*symbol_events, *domestic_events, *global_events)),
            generated_at=catalyst_report.generated_at,
        )
        if freshness_line:
            lines.append(freshness_line)

    northbound_line = (
        _northbound_signal_line(northbound_flow_5d_z) if domestic_enabled else ""
    )
    if northbound_line:
        lines.append(northbound_line)

    margin_line = (
        _margin_signal_line(margin_balance_change_5d) if domestic_enabled else ""
    )
    if margin_line:
        lines.append(margin_line)

    combined_line = _combined_context_line(
        symbol_events=symbol_events,
        domestic_events=domestic_events,
        global_events=global_events,
        northbound_flow_5d_z=northbound_flow_5d_z if domestic_enabled else 0.0,
        margin_balance_change_5d=margin_balance_change_5d if domestic_enabled else 0.0,
    )
    if combined_line:
        lines.append(combined_line)

    coverage_line = _coverage_line(
        symbol_events=symbol_events,
        domestic_events=domestic_events,
        global_events=global_events,
        northbound_flow_5d_z=northbound_flow_5d_z if domestic_enabled else 0.0,
        margin_balance_change_5d=margin_balance_change_5d if domestic_enabled else 0.0,
    )
    if coverage_line:
        lines.append(coverage_line)

    if realtime_context is not None:
        lines.append(_realtime_cross_market_summary_line(realtime_context))
        warnings = tuple(_dedupe_texts((*warnings, *realtime_context.warnings)))

    if not lines:
        if not domestic_enabled and not global_enabled:
            lines.append("市场上下文: 当前已关闭国内外信息融合，维持价格与成交主导。")
        elif catalyst_report is None:
            warnings = ("消息源未加载：不得将空结果视为无消息。",)
            lines.append("消息状态: 未加载，不能据此判断暂无消息；维持价格与成交主导。")
        else:
            lines.append("市场上下文: 暂无强外部信号，维持价格与成交主导。")

    return MarketContextArtifact(
        date=(
            catalyst_report.date
            if catalyst_report is not None
            else today_shanghai().isoformat()
        ),
        generated_at=(
            catalyst_report.generated_at
            if catalyst_report is not None
            else now_shanghai().isoformat(timespec="seconds")
        ),
        source_status=source_status,
        summary_lines=tuple(lines[:11]),
        cross_market_implications=cross_market_implications[:5],
        cross_market_overview=_cross_market_overview_from_implications(
            cross_market_implications[:5]
        ),
        warnings=warnings[:3],
        catalyst_events=tuple((*symbol_events, *domestic_events, *global_events)),
        news_status=(
            catalyst_report.news_status if catalyst_report is not None else ""
        ),
        realtime_cross_market=realtime_context,
    )


def _realtime_cross_market_summary_line(
    context: RealtimeCrossMarketContext,
) -> str:
    status_text = "；".join(
        f"{item.instrument} {item.status}" for item in context.observations
    )
    return f"实时跨市: {context.status}｜{status_text}"


def _market_context_warning_line(warnings: tuple[str, ...]) -> str:
    for warning in warnings:
        text = str(warning or "").strip()
        if not text:
            continue
        if "情报门禁" in text or "消息缓存过期" in text:
            return text
        if "消息缓存回退" in text:
            return text
        if "超时" in text or "连接中断" in text:
            return "消息补位: 部分来源超时，已按可用摘要继续。"
    return ""


def _actionable_catalyst_events(
    events: tuple[CatalystEvent, ...],
    *,
    generated_at: str,
    max_age_minutes: int,
) -> tuple[tuple[CatalystEvent, ...], tuple[str, ...]]:
    if not events:
        return (), ()
    generated_dt = _parse_iso_datetime(generated_at)
    if generated_dt is None:
        return (), (f"情报门禁: 报告生成时间不可解析，已排除 {len(events)} 条消息",)

    actionable: list[CatalystEvent] = []
    stale_count = 0
    undated_count = 0
    future_count = 0
    source_missing_count = 0
    low_quality_count = 0
    max_age = max(0, int(max_age_minutes))
    for event in events:
        age_minutes = _event_age_minutes(
            event.published_at,
            generated_dt=generated_dt,
        )
        if age_minutes is None:
            published_dt = _parse_iso_datetime(event.published_at)
            if published_dt is not None and published_dt > generated_dt:
                future_count += 1
            else:
                undated_count += 1
            continue
        if max_age > 0 and age_minutes > max_age:
            stale_count += 1
            continue
        if not str(event.source or "").strip():
            source_missing_count += 1
            continue
        if _event_source_quality_score(event) < _ACTIONABLE_NEWS_MIN_SOURCE_QUALITY:
            low_quality_count += 1
            continue
        actionable.append(event)

    warnings: list[str] = []
    if stale_count > 0:
        warnings.append(f"情报门禁: 已排除 {stale_count} 条超出短线窗口的旧消息")
    if undated_count > 0:
        warnings.append(f"情报门禁: 已排除 {undated_count} 条无有效时间戳消息")
    if future_count > 0:
        warnings.append(f"情报门禁: 已排除 {future_count} 条未来时间戳消息")
    if source_missing_count > 0:
        warnings.append(f"情报门禁: 已排除 {source_missing_count} 条无可追踪来源消息")
    if low_quality_count > 0:
        warnings.append(f"情报门禁: 已排除 {low_quality_count} 条普通单源消息")
    return tuple(actionable), tuple(warnings)


def _dedupe_texts(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return tuple(deduped)


def market_context_lines_for_pick(
    pick: PickResult,
    artifact: MarketContextArtifact,
) -> tuple[str, ...]:
    relevant = relevant_cross_market_implications_for_pick(
        pick,
        artifact.cross_market_implications,
    )
    relevant_lines = {item.summary_line for item in relevant}
    lines: list[str] = []
    for line in artifact.summary_lines:
        if line.startswith("传导推演["):
            if line in relevant_lines:
                lines.append(line)
                implication = next(
                    (item for item in relevant if item.summary_line == line),
                    None,
                )
                if implication is not None:
                    lines.extend(_pick_implication_detail_lines(implication))
            continue
        lines.append(line)
    direct_line = _pick_news_judgement_line(pick, artifact)
    if direct_line:
        lines.append(direct_line)
    return tuple(lines[:11])


def relevant_cross_market_implications_for_pick(
    pick: PickResult,
    implications: tuple[CrossMarketImplication, ...],
) -> tuple[CrossMarketImplication, ...]:
    haystack = _pick_relevance_text(pick)
    matched: list[CrossMarketImplication] = []
    for implication in implications:
        if any(
            str(keyword or "").casefold() in haystack
            for keyword in implication.relevance_keywords
        ):
            matched.append(implication)
    return tuple(matched[:2])


def build_pick_market_context(
    pick: PickResult,
    artifact: MarketContextArtifact,
) -> PickMarketContext:
    implications = relevant_cross_market_implications_for_pick(
        pick,
        artifact.cross_market_implications,
    )
    if not implications:
        return PickMarketContext(
            symbol=pick.symbol,
            primary_theme="",
            linkage_basis="",
            primary_action="",
            primary_strength="",
            primary_source_quality_label="",
            primary_source_quality_score=0,
            lead_window="",
            observation_window="",
            priority_score=0,
            themes=(),
            rule_ids=(),
            first_order_targets=(),
            second_order_targets=(),
            pressure_targets=(),
            execution_watchpoints=(),
            transmission_path=(),
            validation_signals=(),
            invalidation_signals=(),
            chain_summary="",
            support_event_count=0,
            conflict_event_count=0,
            evidence_stack_summary="",
            summary_lines=(),
        )
    ordered = sorted(
        implications,
        key=lambda item: (
            _implication_priority_score(item),
            item.theme,
        ),
        reverse=True,
    )
    primary = ordered[0]
    return PickMarketContext(
        symbol=pick.symbol,
        primary_theme=primary.theme,
        linkage_basis=primary.linkage_basis,
        primary_action=primary.action,
        primary_strength=primary.strength,
        primary_source_quality_label=primary.source_quality_label,
        primary_source_quality_score=primary.source_quality_score,
        lead_window=primary.lead_window,
        observation_window=primary.observation_window,
        priority_score=_implication_priority_score(primary),
        themes=tuple(item.theme for item in ordered),
        rule_ids=tuple(item.rule_id for item in ordered),
        first_order_targets=primary.first_order_targets,
        second_order_targets=primary.second_order_targets,
        pressure_targets=primary.pressure_targets,
        execution_watchpoints=primary.execution_watchpoints,
        transmission_path=primary.transmission_path,
        validation_signals=primary.validation_signals,
        invalidation_signals=primary.invalidation_signals,
        chain_summary=_pick_chain_summary(primary),
        support_event_count=primary.support_event_count,
        conflict_event_count=primary.conflict_event_count,
        evidence_stack_summary=primary.evidence_stack_summary,
        summary_lines=tuple(item.summary_line for item in ordered),
        affected_sectors=primary.affected_sectors,
        affected_symbols=primary.affected_symbols,
        transmission_hypothesis=primary.transmission_hypothesis,
        confidence=primary.confidence,
        time_horizon=primary.time_horizon,
        supporting_evidence=primary.supporting_evidence,
        contradicting_evidence=primary.contradicting_evidence,
        source_regions=primary.source_regions,
        impact_direction=primary.impact_direction,
        source_url=primary.source_url,
        source_fetched_at=primary.source_fetched_at,
    )


def market_context_metrics_for_pick(
    pick: PickResult,
    artifact: MarketContextArtifact,
) -> dict[str, object]:
    context = build_pick_market_context(pick, artifact)
    news_metrics = _pick_news_judgement_metrics(pick, artifact)
    if not context.summary_lines and not news_metrics:
        return {}
    structured_rule_match = bool(context.rule_ids)
    metrics: dict[str, object] = {
        "cross_market_primary_theme": context.primary_theme,
        "cross_market_linkage_basis": context.linkage_basis,
        "cross_market_action": context.primary_action,
        "cross_market_strength": context.primary_strength,
        "cross_market_source_quality_label": context.primary_source_quality_label,
        "cross_market_source_quality_score": context.primary_source_quality_score,
        "cross_market_lead_window": context.lead_window,
        "cross_market_observation_window": context.observation_window,
        "cross_market_priority_score": context.priority_score,
        "cross_market_themes": context.themes,
        "cross_market_rule_ids": context.rule_ids,
        "cross_market_first_order_targets": context.first_order_targets,
        "cross_market_second_order_targets": context.second_order_targets,
        "cross_market_pressure_targets": context.pressure_targets,
        "cross_market_execution_watchpoints": context.execution_watchpoints,
        "cross_market_transmission_path": context.transmission_path,
        "cross_market_validation_signals": context.validation_signals,
        "cross_market_invalidation_signals": context.invalidation_signals,
        "cross_market_chain_summary": context.chain_summary,
        "cross_market_support_event_count": context.support_event_count,
        "cross_market_conflict_event_count": context.conflict_event_count,
        "cross_market_evidence_stack_summary": context.evidence_stack_summary,
        "cross_market_summaries": context.summary_lines,
        "cross_market_affected_sectors": context.affected_sectors,
        "cross_market_affected_symbols": context.affected_symbols,
        "cross_market_transmission_hypothesis": context.transmission_hypothesis,
        "cross_market_confidence": context.confidence,
        "cross_market_time_horizon": context.time_horizon,
        "cross_market_supporting_evidence": (context.supporting_evidence),
        "cross_market_contradicting_evidence": (context.contradicting_evidence),
        "cross_market_source_regions": context.source_regions,
        "cross_market_impact_direction": context.impact_direction,
        "cross_market_source_url": context.source_url,
        "cross_market_source_fetched_at": context.source_fetched_at,
        "cross_market_score_adjustment_allowed": structured_rule_match,
        "cross_market_priority_boost": structured_rule_match,
        "cross_market_context_only": not structured_rule_match,
    }
    if news_metrics:
        metrics.update(news_metrics)
        if not context.summary_lines:
            metrics.update(_cross_market_fallback_from_news(news_metrics))
    return metrics


def format_pick_market_context_summary(
    pick: PickResult,
    *,
    compact: bool = False,
) -> str:
    metrics = pick.metrics or {}
    theme = str(metrics.get("cross_market_primary_theme", "") or "").strip()
    action = str(metrics.get("cross_market_action", "") or "").strip()
    window = str(metrics.get("cross_market_observation_window", "") or "").strip()
    if not theme:
        return ""
    if compact:
        if action:
            return f"{theme}({action})"
        return theme
    parts = [part for part in (action, theme) if part]
    if window:
        parts.append(f"观察窗 {window}")
    return "｜".join(parts)


def format_pick_market_context_chain_summary(pick: PickResult) -> str:
    metrics = pick.metrics or {}
    basis = str(metrics.get("cross_market_linkage_basis", "") or "").strip()
    lead_window = str(metrics.get("cross_market_lead_window", "") or "").strip()
    validation = _as_text_tuple(metrics.get("cross_market_validation_signals"))
    invalidation = _as_text_tuple(metrics.get("cross_market_invalidation_signals"))
    first_order_targets = _as_text_tuple(
        metrics.get("cross_market_first_order_targets")
    )
    pressure_targets = _as_text_tuple(metrics.get("cross_market_pressure_targets"))
    execution_watchpoints = _as_text_tuple(
        metrics.get("cross_market_execution_watchpoints")
    )
    evidence_stack_summary = str(
        metrics.get("cross_market_evidence_stack_summary", "") or ""
    ).strip()
    parts: list[str] = []
    if basis:
        parts.append(basis)
    if lead_window:
        parts.append(f"领先窗 {lead_window}")
    if first_order_targets:
        parts.append(f"先看 {first_order_targets[0]}")
    if execution_watchpoints:
        parts.append(f"锚点 {execution_watchpoints[0]}")
    if validation:
        parts.append(f"确认 {validation[0]}")
    if invalidation:
        parts.append(f"失效 {invalidation[0]}")
    if pressure_targets:
        parts.append(f"承压 {pressure_targets[0]}")
    if evidence_stack_summary:
        parts.append(evidence_stack_summary)
    return "｜".join(parts)


def combine_cross_market_overview(
    candidate_overview: str,
    artifact: MarketContextArtifact,
) -> str:
    candidate_text = str(candidate_overview or "").strip()
    market_text = str(artifact.cross_market_overview or "").strip()
    if not candidate_text:
        return market_text
    if not market_text:
        return candidate_text
    candidate_theme = candidate_text.split("，", 1)[0].strip()
    matched = next(
        (
            implication
            for implication in artifact.cross_market_implications
            if implication.theme == candidate_theme
        ),
        None,
    )
    if matched is not None:
        targets = "、".join(matched.a_share_targets[:3])
        if targets:
            return f"{candidate_text}；方向 {targets}"
    return f"{candidate_text}；全局 {market_text}"


def _event_brief(event: CatalystEvent) -> str:
    target = f"{event.symbol} {event.name}".strip() if event.symbol else "全市场"
    title = str(event.inference or event.title or "").strip()
    title = " ".join(title.split())
    if len(title) > 26:
        title = title[:25].rstrip() + "…"
    impact = {"positive": "偏多", "negative": "偏空", "neutral": "中性"}.get(
        event.impact,
        "中性",
    )
    return f"{target} {impact}｜{event.category}｜{title}"


def _source_status_text(status: str) -> str:
    return {
        "ok": "可用",
        "partial": "部分可用",
        "empty": "无强事件",
        "failed": "抓取失败",
        "not_loaded": "未加载",
    }.get(status, status or "未知")


def _catalyst_result_status_text(status: str, warnings: tuple[str, ...]) -> str:
    if any("超出短线窗口" in str(item) for item in warnings):
        return "旧新闻已排除"
    return {
        "high_impact": "已筛出高影响事件",
        "no_high_impact": "抓取成功但未筛出高影响事件",
        "stale_only": "仅发现旧新闻，已排除",
        "no_valid_news": "无可用新闻记录",
        "source_failed": "来源失败，无有效事件",
        "stale_cache": "来源失败，使用受限旧缓存",
    }.get(status, status or "未知")


def _northbound_signal_line(value: float) -> str:
    if value >= _NORTHBOUND_STRONG_Z:
        return f"北向资金: 偏强（5日 z={value:.2f}），外资风险偏好改善。"
    if value <= -_NORTHBOUND_STRONG_Z:
        return f"北向资金: 偏弱（5日 z={value:.2f}），需防范系统性回撤。"
    return ""


def _margin_signal_line(value: float) -> str:
    if value >= _MARGIN_STRONG_CHANGE:
        return f"融资情绪: 升温（5日变化 {value:.1%}），短线拥挤度上升。"
    if value <= -_MARGIN_STRONG_CHANGE:
        return f"融资情绪: 降温（5日变化 {value:.1%}），杠杆风险偏好回落。"
    return ""


def _global_risk_line(events: list[CatalystEvent]) -> str:
    if not events:
        return ""
    positive = sum(1 for event in events if event.impact == "positive")
    negative = sum(1 for event in events if event.impact == "negative")
    if positive == 0 and negative == 0:
        return ""
    categories = _top_categories(events)
    category_text = f"｜{categories}" if categories else ""
    if negative > positive:
        return (
            f"海外风险: 偏空（正面 {positive} / 负面 {negative}）"
            f"{category_text}｜海外风险偏好回落。"
        )
    if positive > negative:
        return (
            f"海外风险: 偏多（正面 {positive} / 负面 {negative}）"
            f"{category_text}｜海外风险偏好回暖。"
        )
    return (
        f"海外风险: 分化（正面 {positive} / 负面 {negative}）"
        f"{category_text}｜外部线索未形成单边共识。"
    )


def _source_quality_summary_line(events: list[CatalystEvent]) -> str:
    if not events:
        return ""
    high_value = sum(1 for event in events if _event_source_quality_score(event) >= 4)
    authoritative = sum(
        1 for event in events if _event_source_quality_score(event) == 3
    )
    mainstream = sum(1 for event in events if _event_source_quality_score(event) == 2)
    if high_value <= 0 and authoritative <= 0 and mainstream <= 0:
        return ""
    parts: list[str] = []
    if high_value > 0:
        parts.append(f"高价值 {high_value} 条")
    if authoritative > 0:
        parts.append(f"多源/权威 {authoritative} 条")
    if mainstream > 0:
        parts.append(f"主流媒体 {mainstream} 条")
    return "来源质量: " + "｜".join(parts)


def _event_source_quality_score(event: CatalystEvent) -> int:
    score = int(getattr(event, "source_quality_score", 0) or 0)
    if score > 1:
        return score
    source = str(getattr(event, "source", "") or "").strip()
    source_count = int(getattr(event, "source_count", 1) or 1)
    if any(token in source for token in _AUTHORITATIVE_SOURCE_TOKENS):
        return 4
    if (
        any(token in source for token in _PRIORITY_MEDIA_SOURCE_TOKENS)
        or source_count >= 2
    ):
        return 3
    if any(token in source for token in _MAINSTREAM_MEDIA_SOURCE_TOKENS):
        return 2
    return max(1, score)


def _event_source_quality_label(event: CatalystEvent) -> str:
    label = str(getattr(event, "source_quality_label", "") or "").strip()
    if label and label != "普通来源":
        return label
    return {
        4: "高价值来源",
        3: "多源/权威媒体",
        2: "主流媒体",
    }.get(_event_source_quality_score(event), "普通来源")


def _cross_market_implications(
    events: list[CatalystEvent],
    *,
    generated_at: str,
) -> tuple[CrossMarketImplication, ...]:
    matched_events: dict[str, list[CatalystEvent]] = {}
    generated_dt = _parse_iso_datetime(generated_at)
    for event in events:
        text = " ".join(
            part.strip().lower()
            for part in (event.title, event.inference, event.category)
            if str(part).strip()
        )
        if not text:
            continue
        for rule in _CROSS_MARKET_RULES:
            if _rule_matches_event(rule, text):
                matched_events.setdefault(rule.rule_id, []).append(event)
    matched: list[CrossMarketImplication] = []
    for rule in _CROSS_MARKET_RULES:
        events_for_rule = matched_events.get(rule.rule_id, [])
        if not events_for_rule:
            continue
        if rule.required_keyword_groups:
            # A precise trigger starts the theme; broader keyword matches then
            # contribute corroborating or conflicting evidence only.
            events_for_rule = _expand_rule_evidence_events(
                rule,
                seed_events=events_for_rule,
                events=events,
            )
        matched.append(
            _implication_for_events(
                rule,
                tuple(events_for_rule),
                generated_dt=generated_dt,
            )
        )
    ordered = sorted(
        matched,
        key=lambda item: (
            _implication_priority_score(item),
            item.support_event_count,
            -item.conflict_event_count,
            item.theme,
        ),
        reverse=True,
    )
    return tuple(ordered[:5])


def _expand_rule_evidence_events(
    rule: CrossMarketImplicationRule,
    *,
    seed_events: list[CatalystEvent],
    events: list[CatalystEvent],
) -> list[CatalystEvent]:
    selected = list(seed_events)
    selected_ids = {id(event) for event in selected}
    for event in events:
        if id(event) in selected_ids:
            continue
        text = " ".join(
            part.strip().lower()
            for part in (event.title, event.inference, event.category)
            if str(part).strip()
        )
        if any(keyword in text for keyword in rule.keywords):
            selected.append(event)
            selected_ids.add(id(event))
    return selected


def _implication_for_events(
    rule: CrossMarketImplicationRule,
    events: tuple[CatalystEvent, ...],
    *,
    generated_dt: datetime | None,
) -> CrossMarketImplication:
    ranked_events = sorted(
        events,
        key=lambda item: _rule_event_rank_key(rule, item, generated_dt=generated_dt),
        reverse=True,
    )
    primary_event = ranked_events[0]
    support_event_count, conflict_event_count = _implication_event_bias_counts(
        rule,
        events,
    )
    evidence_stack_summary = _implication_evidence_stack_summary(
        support_event_count=support_event_count,
        conflict_event_count=conflict_event_count,
    )
    strength = _implication_strength(
        rule,
        events,
        generated_dt=generated_dt,
    )
    action = _implication_action(strength)
    targets = "、".join(rule.a_share_targets[:5])
    evidence_points = _implication_evidence_points(
        primary_event,
        generated_dt=generated_dt,
    )
    evidence_suffix = _format_implication_evidence_suffix(evidence_points)
    stack_suffix = f"；{evidence_stack_summary}" if evidence_stack_summary else ""
    supporting_evidence, contradicting_evidence = _implication_evidence_lists(
        rule,
        events,
    )
    affected_symbols = _implication_affected_symbols(events)
    source_regions = _text_values(
        event.source_region for event in events if event.source_region
    )
    impact_direction = _implication_impact_direction(events)
    confidence = _implication_confidence(
        strength=strength,
        support_event_count=support_event_count,
        conflict_event_count=conflict_event_count,
    )
    summary_line = (
        f"传导推演[{strength}]: {rule.theme} -> A股{targets}；"
        f"动作 {action}；观察窗 {rule.observation_window}{stack_suffix}；"
        f"{rule.confirmation_hint}{evidence_suffix}"
    )
    return CrossMarketImplication(
        rule_id=rule.rule_id,
        theme=rule.theme,
        linkage_basis=rule.linkage_basis,
        a_share_targets=rule.a_share_targets,
        first_order_targets=rule.first_order_targets,
        second_order_targets=rule.second_order_targets,
        pressure_targets=rule.pressure_targets,
        execution_watchpoints=rule.execution_watchpoints,
        relevance_keywords=rule.relevance_keywords,
        lead_window=rule.lead_window,
        observation_window=rule.observation_window,
        transmission_path=rule.transmission_path,
        validation_signals=rule.validation_signals,
        invalidation_signals=rule.invalidation_signals,
        confirmation_hint=rule.confirmation_hint,
        strength=strength,
        action=action,
        source_title=str(primary_event.title or "").strip(),
        source_category=str(primary_event.category or "").strip(),
        source_quality_label=_event_source_quality_label(primary_event),
        source_quality_score=_event_source_quality_score(primary_event),
        source_published_at=str(primary_event.published_at or "").strip(),
        support_event_count=support_event_count,
        conflict_event_count=conflict_event_count,
        evidence_stack_summary=evidence_stack_summary,
        evidence_points=evidence_points,
        summary_line=summary_line,
        affected_sectors=rule.a_share_targets,
        affected_symbols=affected_symbols,
        transmission_hypothesis=" -> ".join(rule.transmission_path),
        confidence=confidence,
        time_horizon=(f"领先 {rule.lead_window}；观察 {rule.observation_window}"),
        supporting_evidence=supporting_evidence,
        contradicting_evidence=contradicting_evidence,
        source_regions=source_regions,
        impact_direction=impact_direction,
        source_url=str(primary_event.url or "").strip(),
        source_fetched_at=str(primary_event.source_fetched_at or "").strip(),
    )


def _implication_priority_score(implication: CrossMarketImplication) -> int:
    return {"强": 3, "中": 2, "弱": 1}.get(implication.strength, 0)


def _text_values(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        text = values.strip()
        return (text,) if text else ()
    try:
        iterator = iter(values)  # type: ignore[arg-type]
    except TypeError:
        return ()
    result: list[str] = []
    for value in iterator:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _implication_affected_symbols(
    events: tuple[CatalystEvent, ...],
) -> tuple[str, ...]:
    values: list[str] = []
    for event in events:
        candidates = tuple(event.affected_symbols) or (
            (event.symbol,) if event.symbol else ()
        )
        for symbol in candidates:
            clean = str(symbol or "").strip()
            if clean and clean not in values:
                values.append(clean)
    return tuple(values)


def _evidence_label(event: CatalystEvent) -> str:
    source = str(event.source or "未标注来源").strip()
    title = str(event.title or "").strip()
    return f"{source}: {title}" if title else source


def _implication_evidence_lists(
    rule: CrossMarketImplicationRule,
    events: tuple[CatalystEvent, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    supporting: list[str] = []
    contradicting: list[str] = []
    for event in events:
        if _event_supports_rule(rule, event):
            target = supporting
            evidence = (event.title, *event.supporting_evidence)
        elif str(event.impact or "").strip() == "neutral":
            target = supporting
            evidence = event.supporting_evidence
        else:
            target = contradicting
            evidence = (event.title, *event.contradicting_evidence)
        for item in evidence:
            text = str(item or "").strip()
            if text and text not in target:
                target.append(text)
        if not event.supporting_evidence and event.title:
            label = _evidence_label(event)
            if label not in target:
                target.append(label)
    return tuple(supporting[:6]), tuple(contradicting[:6])


def _implication_confidence(
    *,
    strength: str,
    support_event_count: int,
    conflict_event_count: int,
) -> float:
    base = {"强": 0.82, "中": 0.64, "弱": 0.42}.get(strength, 0.0)
    if support_event_count >= 2:
        base += 0.06
    if conflict_event_count > 0:
        base -= 0.12
    return max(0.0, min(1.0, round(base, 2)))


def _implication_impact_direction(
    events: tuple[CatalystEvent, ...],
) -> Literal["positive", "negative", "mixed", "neutral"]:
    directions = {
        str(event.impact or "").strip()
        for event in events
        if str(event.impact or "").strip() in {"positive", "negative"}
    }
    if len(directions) > 1:
        return "mixed"
    if directions:
        return next(iter(directions))  # type: ignore[return-value]
    return "neutral"


def _cross_market_overview_from_implications(
    implications: tuple[CrossMarketImplication, ...],
) -> str:
    if not implications:
        return ""
    primary = sorted(
        implications,
        key=lambda item: (
            _implication_priority_score(item),
            item.support_event_count,
            -item.conflict_event_count,
            item.theme,
        ),
        reverse=True,
    )[0]
    targets = "、".join(primary.a_share_targets[:3])
    action = _cross_market_overview_action(primary.action)
    if targets:
        return f"{primary.theme}，{action} A股{targets}"
    return f"{primary.theme}，{action}"


def _cross_market_overview_action(action: str) -> str:
    if action == "优先复核":
        return "优先看"
    if action == "重点跟踪":
        return "重点看"
    if action == "观察为主":
        return "先观察"
    return "先看"


def _pick_implication_detail_lines(
    implication: CrossMarketImplication,
) -> tuple[str, ...]:
    lines: list[str] = []
    lines.append(
        "传导链: "
        f"{implication.linkage_basis}｜领先窗 {implication.lead_window}｜"
        + " -> ".join(implication.transmission_path[:2])
    )
    if implication.first_order_targets:
        lines.append(f"先看链条: {'、'.join(implication.first_order_targets[:3])}")
    if implication.second_order_targets:
        lines.append(f"扩散链条: {'、'.join(implication.second_order_targets[:3])}")
    if implication.pressure_targets:
        lines.append(f"承压方向: {'、'.join(implication.pressure_targets[:2])}")
    if implication.execution_watchpoints:
        lines.append(f"盘中锚点: {implication.execution_watchpoints[0]}")
    if implication.validation_signals:
        lines.append(f"确认信号: {implication.validation_signals[0]}")
    if implication.invalidation_signals:
        lines.append(f"失效条件: {implication.invalidation_signals[0]}")
    if implication.evidence_stack_summary:
        lines.append(f"证据堆栈: {implication.evidence_stack_summary}")
    return tuple(lines)


def _pick_chain_summary(implication: CrossMarketImplication) -> str:
    parts = [implication.linkage_basis]
    if implication.lead_window:
        parts.append(f"领先窗 {implication.lead_window}")
    if implication.first_order_targets:
        parts.append(f"先看 {implication.first_order_targets[0]}")
    if implication.execution_watchpoints:
        parts.append(f"锚点 {implication.execution_watchpoints[0]}")
    if implication.validation_signals:
        parts.append(f"确认 {implication.validation_signals[0]}")
    if implication.invalidation_signals:
        parts.append(f"失效 {implication.invalidation_signals[0]}")
    if implication.pressure_targets:
        parts.append(f"承压 {implication.pressure_targets[0]}")
    if implication.evidence_stack_summary:
        parts.append(implication.evidence_stack_summary)
    return "｜".join(parts)


def _implication_strength(
    rule: CrossMarketImplicationRule,
    events: tuple[CatalystEvent, ...],
    *,
    generated_dt: datetime | None,
) -> str:
    primary_event = max(
        events,
        key=lambda item: _rule_event_rank_key(rule, item, generated_dt=generated_dt),
    )
    score = 1  # 已命中明确跨市场规则，先给基础证据分
    if float(primary_event.confidence) >= 0.75:
        score += 1
    if _event_source_quality_score(primary_event) >= 3:
        score += 1
    if int(primary_event.source_count) >= 2:
        score += 1
    age_minutes = _event_age_minutes(
        primary_event.published_at,
        generated_dt=generated_dt,
    )
    if age_minutes is not None:
        if age_minutes <= 180:
            score += 1
        elif age_minutes > 720:
            score -= 1
    support_event_count, conflict_event_count = _implication_event_bias_counts(
        rule,
        events,
    )
    if support_event_count >= 2:
        score += _CROSS_MARKET_STACK_SUPPORT_BONUS
    if conflict_event_count > 0:
        score -= _CROSS_MARKET_STACK_CONFLICT_PENALTY
    if score >= _CROSS_MARKET_STRONG_SCORE:
        return "强"
    if score >= _CROSS_MARKET_MEDIUM_SCORE:
        return "中"
    return "弱"


def _rule_event_rank_key(
    rule: CrossMarketImplicationRule,
    event: CatalystEvent,
    *,
    generated_dt: datetime | None,
) -> tuple[int, int, float, int, int]:
    support_score = 1 if _event_supports_rule(rule, event) else 0
    age_minutes = _event_age_minutes(event.published_at, generated_dt=generated_dt)
    freshness_score = -age_minutes if age_minutes is not None else -(10**9)
    return (
        support_score,
        _event_source_quality_score(event),
        float(event.confidence),
        int(event.source_count),
        freshness_score,
    )


def _rule_matches_event(
    rule: CrossMarketImplicationRule,
    text: str,
) -> bool:
    if rule.keywords and not any(keyword in text for keyword in rule.keywords):
        return False
    for group in rule.required_keyword_groups:
        if group and not any(keyword in text for keyword in group):
            return False
    return True


def _event_supports_rule(
    rule: CrossMarketImplicationRule,
    event: CatalystEvent,
) -> bool:
    return str(event.impact or "").strip() in rule.supportive_impacts


def _implication_event_bias_counts(
    rule: CrossMarketImplicationRule,
    events: tuple[CatalystEvent, ...],
) -> tuple[int, int]:
    support_event_count = 0
    conflict_event_count = 0
    for event in events:
        impact = str(event.impact or "").strip()
        if not impact or impact == "neutral":
            continue
        if impact in rule.supportive_impacts:
            support_event_count += 1
        else:
            conflict_event_count += 1
    return support_event_count, conflict_event_count


def _implication_evidence_stack_summary(
    *,
    support_event_count: int,
    conflict_event_count: int,
) -> str:
    if support_event_count <= 1 and conflict_event_count <= 0:
        return ""
    return f"同向 {support_event_count} 条｜反向 {conflict_event_count} 条"


def _implication_action(strength: str) -> str:
    if strength == "强":
        return "优先复核"
    if strength == "中":
        return "重点跟踪"
    return "观察为主"


def _implication_evidence_points(
    event: CatalystEvent,
    *,
    generated_dt: datetime | None,
) -> tuple[str, ...]:
    parts: list[str] = []
    if float(event.confidence) > 0:
        parts.append(f"置信 {event.confidence:.2f}")
    if int(event.source_count) > 1:
        parts.append(f"{event.source_count} 源共振")
    if _event_source_quality_score(event) >= 3:
        parts.append(_event_source_quality_label(event))
    age_minutes = _event_age_minutes(event.published_at, generated_dt=generated_dt)
    if age_minutes is not None:
        parts.append(f"最新 {age_minutes} 分钟前")
    fetched_age_minutes = _event_age_minutes(
        event.source_fetched_at,
        generated_dt=generated_dt,
    )
    if fetched_age_minutes is not None:
        parts.append(f"抓取 {fetched_age_minutes} 分钟前")
    return tuple(parts)


def _format_implication_evidence_suffix(parts: tuple[str, ...]) -> str:
    if not parts:
        return ""
    return "｜证据: " + " / ".join(parts) + "。"


def _as_text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _event_age_minutes(
    published_at: str,
    *,
    generated_dt: datetime | None,
) -> int | None:
    if generated_dt is None:
        return None
    published_dt = _parse_iso_datetime(published_at)
    if published_dt is None:
        return None
    delta_seconds = (generated_dt - published_dt).total_seconds()
    if delta_seconds < 0:
        return None
    return int(delta_seconds // 60)


def _combined_context_line(
    *,
    symbol_events: list[CatalystEvent],
    domestic_events: list[CatalystEvent],
    global_events: list[CatalystEvent],
    northbound_flow_5d_z: float,
    margin_balance_change_5d: float,
) -> str:
    reasons: list[str] = []
    score = 0

    symbol_score = _impact_balance(symbol_events)
    if symbol_score > 0:
        score += 1
        reasons.append("个股催化偏多")
    elif symbol_score < 0:
        score -= 1
        reasons.append("个股催化偏空")

    domestic_score = _impact_balance(domestic_events)
    if domestic_score > 0:
        score += 1
        reasons.append("国内催化偏多")
    elif domestic_score < 0:
        score -= 1
        reasons.append("国内催化偏空")

    global_score = _impact_balance(global_events)
    if global_score > 0:
        score += 1
        reasons.append("海外线索偏多")
    elif global_score < 0:
        score -= 1
        reasons.append("海外线索偏空")

    if northbound_flow_5d_z >= _NORTHBOUND_STRONG_Z:
        score += 1
        reasons.append("北向改善")
    elif northbound_flow_5d_z <= -_NORTHBOUND_STRONG_Z:
        score -= 1
        reasons.append("北向走弱")

    if margin_balance_change_5d >= _MARGIN_STRONG_CHANGE:
        reasons.append("融资升温")
    elif margin_balance_change_5d <= -_MARGIN_STRONG_CHANGE:
        score -= 1
        reasons.append("融资降温")

    if not reasons:
        return ""
    if score >= 2:
        bias = "偏多"
    elif score <= -2:
        bias = "偏空"
    else:
        bias = "分化"
    return f"综合风向: {bias}｜{'；'.join(reasons[:3])}。"


def _coverage_line(
    *,
    symbol_events: list[CatalystEvent],
    domestic_events: list[CatalystEvent],
    global_events: list[CatalystEvent],
    northbound_flow_5d_z: float,
    margin_balance_change_5d: float,
) -> str:
    coverage: list[str] = []
    if symbol_events:
        coverage.append("个股催化")
    if domestic_events:
        coverage.append("国内政策/行业")
    if global_events:
        coverage.append("海外线索")
    if abs(northbound_flow_5d_z) >= _NORTHBOUND_STRONG_Z:
        coverage.append("北向资金")
    if abs(margin_balance_change_5d) >= _MARGIN_STRONG_CHANGE:
        coverage.append("融资情绪")
    if not coverage:
        return ""
    return f"情报覆盖: {' + '.join(coverage[:4])}。"


def _event_freshness_line(
    *,
    events: tuple[CatalystEvent, ...],
    generated_at: str,
) -> str:
    if not events:
        return ""
    generated_dt = _parse_iso_datetime(generated_at)
    if generated_dt is None:
        return ""
    ages: list[int] = []
    undated_count = 0
    for event in events:
        published_dt = _parse_iso_datetime(event.published_at)
        if published_dt is None:
            undated_count += 1
            continue
        delta_seconds = (generated_dt - published_dt).total_seconds()
        if delta_seconds < 0:
            continue
        ages.append(int(delta_seconds // 60))
    if not ages and undated_count <= 0:
        return ""
    if not ages:
        return (
            f"情报时效: 未能确认具体时间（无时间戳 {undated_count} 条），仅作辅助参考。"
        )
    freshest = min(ages)
    if freshest <= 120:
        freshness = "偏新"
        hint = "可优先进入短线复核。"
    elif freshest <= 720:
        freshness = "可用"
        hint = "适合作为次日预案参考。"
    else:
        freshness = "偏旧"
        hint = "更适合解释背景，不宜单独驱动短线判断。"
    suffix = f"；无时间戳 {undated_count} 条" if undated_count > 0 else ""
    return f"情报时效: {freshness}（最新 {freshest} 分钟前）{suffix}｜{hint}"


def _parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return to_shanghai(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _impact_balance(events: list[CatalystEvent]) -> int:
    positive = sum(1 for event in events if event.impact == "positive")
    negative = sum(1 for event in events if event.impact == "negative")
    if positive > negative:
        return 1
    if negative > positive:
        return -1
    return 0


def _top_categories(events: list[CatalystEvent]) -> str:
    counts: dict[str, int] = {}
    for event in events:
        category = str(event.category or "").strip()
        if not category:
            continue
        counts[category] = counts.get(category, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return "、".join(category for category, _count in ordered[:2])


def _pick_news_judgement_metrics(
    pick: PickResult,
    artifact: MarketContextArtifact,
) -> dict[str, object]:
    matched = _pick_relevant_catalyst_events(pick, artifact.catalyst_events)
    if not matched:
        return {}
    supports = tuple(event for event in matched if event.impact == "positive")
    opposes = tuple(event for event in matched if event.impact == "negative")
    needs_review = tuple(
        event
        for event in matched
        if event.impact == "neutral" or event.confidence < 0.55
    )
    judgement = _news_judgement_label(
        support_count=len(supports),
        oppose_count=len(opposes),
        review_count=len(needs_review),
    )
    priority_score = _news_priority_score(supports=supports, opposes=opposes)
    lead = _lead_news_event(supports=supports, opposes=opposes, matched=matched)
    return {
        "news_catalyst_judgement": judgement,
        "news_catalyst_priority_score": priority_score,
        "news_catalyst_support_count": len(supports),
        "news_catalyst_oppose_count": len(opposes),
        "news_catalyst_review_count": len(needs_review),
        "news_catalyst_supports": tuple(_event_brief(event) for event in supports[:3]),
        "news_catalyst_opposes": tuple(_event_brief(event) for event in opposes[:3]),
        "news_catalyst_needs_review": tuple(
            _event_brief(event) for event in needs_review[:3]
        ),
        "news_catalyst_lead": _event_brief(lead) if lead is not None else "",
        "news_catalyst_source": str(lead.source if lead is not None else ""),
        "news_catalyst_url": str(lead.url if lead is not None else ""),
        "news_catalyst_title": str(lead.title if lead is not None else ""),
        "news_catalyst_published_at": str(
            lead.published_at if lead is not None else ""
        ),
        "news_catalyst_source_quality_label": str(
            _event_source_quality_label(lead) if lead is not None else ""
        ),
        "news_catalyst_source_quality_score": int(
            _event_source_quality_score(lead) if lead is not None else 0
        ),
        "news_catalyst_confidence": float(lead.confidence if lead is not None else 0.0),
        "news_catalyst_deterministic_score": int(
            lead.deterministic_score if lead is not None else 0
        ),
        "news_catalyst_symbol": str(lead.symbol if lead is not None else ""),
        "news_catalyst_name": str(lead.name if lead is not None else ""),
        "news_catalyst_category": str(lead.category if lead is not None else ""),
        "news_catalyst_affected_sectors": (
            tuple(lead.affected_sectors) if lead is not None else ()
        ),
        "news_catalyst_affected_symbols": (
            tuple(lead.affected_symbols) if lead is not None else ()
        ),
        "news_catalyst_transmission_hypothesis": str(
            lead.transmission_hypothesis if lead is not None else ""
        ),
        "news_catalyst_time_horizon": str(
            lead.time_horizon if lead is not None else ""
        ),
        "news_catalyst_supporting_evidence": (
            tuple(lead.supporting_evidence) if lead is not None else ()
        ),
        "news_catalyst_contradicting_evidence": (
            tuple(lead.contradicting_evidence) if lead is not None else ()
        ),
        "news_catalyst_sector": str(
            (pick.metrics or {}).get("sector", "") if pick.metrics else ""
        ),
        "news_catalyst_industry": str(
            (pick.metrics or {}).get("industry", "") if pick.metrics else ""
        ),
    }


def _cross_market_fallback_from_news(
    metrics: dict[str, object],
) -> dict[str, object]:
    judgement = str(metrics.get("news_catalyst_judgement", "") or "")
    priority_score = int(metrics.get("news_catalyst_priority_score", 0) or 0)
    if priority_score <= 0:
        return {}
    action = "观察为主"
    if judgement == "supports":
        action = (
            "优先复核" if priority_score >= _NEWS_DIRECT_STRONG_SCORE else "重点跟踪"
        )
    elif judgement == "opposes":
        action = "风险复核"
    target = (
        " ".join(
            value
            for value in (
                str(metrics.get("news_catalyst_symbol", "") or "").strip(),
                str(metrics.get("news_catalyst_name", "") or "").strip(),
            )
            if value
        )
        or "消息直接对象"
    )
    sector = str(metrics.get("news_catalyst_sector", "") or "").strip()
    industry = str(metrics.get("news_catalyst_industry", "") or "").strip()
    second_order = tuple(
        value
        for value in (industry, sector, "同主题竞品/上下游")
        if value and value != target
    )
    first_order = (target,)
    transmission_path = (
        f"{metrics.get('news_catalyst_source', '') or '可追踪来源'}消息 -> {target}",
        f"{target} -> {industry or sector or '所属行业/上下游'}",
        "价格与成交确认后再判断催化是否延续",
    )
    validation_signals = (
        "原文来源与发布时间可复核",
        "竞价及首小时价格、成交同步确认",
        f"{industry or sector or '所属板块'}出现至少两只跟随标的",
    )
    invalidation_signals = (
        "来源无法复核或后续公告澄清",
        "高开低走或放量不涨",
        f"{industry or sector or '所属板块'}没有扩散而仅单点脉冲",
    )
    evidence_points = tuple(
        value
        for value in (
            str(metrics.get("news_catalyst_source", "") or "").strip(),
            str(metrics.get("news_catalyst_published_at", "") or "").strip(),
            str(metrics.get("news_catalyst_url", "") or "").strip(),
        )
        if value
    )
    return {
        "cross_market_primary_theme": "消息面直接催化",
        "cross_market_linkage_basis": "新闻催化",
        "cross_market_action": action,
        "cross_market_strength": "强"
        if priority_score >= _NEWS_DIRECT_STRONG_SCORE
        else "中",
        "cross_market_priority_score": priority_score,
        "cross_market_lead_window": "消息发布-当日",
        "cross_market_observation_window": "当日-2日",
        "cross_market_source_quality_label": str(
            metrics.get("news_catalyst_source_quality_label", "") or ""
        ),
        "cross_market_source_quality_score": int(
            metrics.get("news_catalyst_source_quality_score", 0) or 0
        ),
        "cross_market_source_title": str(metrics.get("news_catalyst_title", "") or ""),
        "cross_market_source_published_at": str(
            metrics.get("news_catalyst_published_at", "") or ""
        ),
        "cross_market_affected_sectors": _as_text_tuple(
            metrics.get("news_catalyst_affected_sectors")
        ),
        "cross_market_affected_symbols": _as_text_tuple(
            metrics.get("news_catalyst_affected_symbols")
        ),
        "cross_market_transmission_hypothesis": str(
            metrics.get("news_catalyst_transmission_hypothesis", "") or ""
        ),
        "cross_market_confidence": float(
            metrics.get("news_catalyst_confidence", 0.0) or 0.0
        ),
        "cross_market_time_horizon": str(
            metrics.get("news_catalyst_time_horizon", "当日-2日") or "当日-2日"
        ),
        "cross_market_supporting_evidence": _as_text_tuple(
            metrics.get("news_catalyst_supporting_evidence")
        ),
        "cross_market_contradicting_evidence": _as_text_tuple(
            metrics.get("news_catalyst_contradicting_evidence")
        ),
        "cross_market_first_order_targets": first_order,
        "cross_market_second_order_targets": second_order,
        "cross_market_transmission_path": transmission_path,
        "cross_market_validation_signals": validation_signals,
        "cross_market_invalidation_signals": invalidation_signals,
        "cross_market_execution_watchpoints": (
            "竞价强度与首小时成交承接",
            "板块扩散与相对强度",
        ),
        "cross_market_chain_summary": (
            f"{target} -> {industry or sector or '所属行业/上下游'} -> 价格/成交确认"
        ),
        "cross_market_evidence_points": evidence_points,
        "cross_market_support_event_count": int(
            metrics.get("news_catalyst_support_count", 0) or 0
        ),
        "cross_market_conflict_event_count": int(
            metrics.get("news_catalyst_oppose_count", 0) or 0
        ),
        "cross_market_evidence_stack_summary": _news_evidence_stack_summary(metrics),
        "cross_market_summaries": (str(metrics.get("news_catalyst_lead", "") or ""),),
        "cross_market_score_adjustment_allowed": False,
        "cross_market_context_only": True,
    }


def _pick_news_judgement_line(
    pick: PickResult,
    artifact: MarketContextArtifact,
) -> str:
    metrics = _pick_news_judgement_metrics(pick, artifact)
    if not metrics:
        return ""
    judgement = str(metrics.get("news_catalyst_judgement", "") or "")
    label = {
        "supports": "消息支持",
        "opposes": "消息反对",
        "needs_review": "消息待复核",
        "mixed": "消息分歧",
    }.get(judgement, "消息观察")
    lead = str(metrics.get("news_catalyst_lead", "") or "")
    stack = _news_evidence_stack_summary(metrics)
    suffix = f"｜{stack}" if stack else ""
    return f"{label}: {lead}{suffix}"


def _pick_relevant_catalyst_events(
    pick: PickResult,
    events: tuple[CatalystEvent, ...],
) -> tuple[CatalystEvent, ...]:
    matched: list[CatalystEvent] = []
    pick_tokens = _pick_relevance_tokens(pick)
    for event in events:
        if event.symbol and event.symbol == pick.symbol:
            matched.append(event)
            continue
        text = " ".join(
            str(part or "").lower()
            for part in (event.title, event.inference, event.category, event.name)
        )
        if pick.symbol and pick.symbol in text:
            matched.append(event)
            continue
        if pick.name and pick.name.lower() in text:
            matched.append(event)
            continue
        if pick_tokens and any(token in text for token in pick_tokens):
            matched.append(event)
    return tuple(sorted(matched, key=_news_event_rank_key, reverse=True)[:5])


def _pick_relevance_tokens(pick: PickResult) -> tuple[str, ...]:
    metrics = pick.metrics or {}
    raw_tokens = (
        str(metrics.get("sector", "") or ""),
        str(metrics.get("industry", "") or ""),
        *tuple(str(strategy) for strategy in pick.strategies),
    )
    tokens: list[str] = []
    for token in raw_tokens:
        clean = token.strip().lower()
        if len(clean) >= 2 and clean not in tokens:
            tokens.append(clean)
    return tuple(tokens)


def _news_event_rank_key(event: CatalystEvent) -> tuple[int, int, float, int]:
    return (
        int(event.weight),
        int(event.source_quality_score),
        float(event.confidence),
        int(event.source_count),
    )


def _lead_news_event(
    *,
    supports: tuple[CatalystEvent, ...],
    opposes: tuple[CatalystEvent, ...],
    matched: tuple[CatalystEvent, ...],
) -> CatalystEvent | None:
    if opposes:
        return sorted(opposes, key=_news_event_rank_key, reverse=True)[0]
    if supports:
        return sorted(supports, key=_news_event_rank_key, reverse=True)[0]
    if matched:
        return sorted(matched, key=_news_event_rank_key, reverse=True)[0]
    return None


def _news_judgement_label(
    *,
    support_count: int,
    oppose_count: int,
    review_count: int,
) -> str:
    if support_count > 0 and oppose_count > 0:
        return "mixed"
    if oppose_count > 0:
        return "opposes"
    if support_count > 0:
        return "supports"
    if review_count > 0:
        return "needs_review"
    return ""


def _news_priority_score(
    *,
    supports: tuple[CatalystEvent, ...],
    opposes: tuple[CatalystEvent, ...],
) -> int:
    events = supports or opposes
    if not events:
        return _NEWS_DIRECT_WEAK_SCORE
    lead = sorted(events, key=_news_event_rank_key, reverse=True)[0]
    if lead.source_quality_score >= 3 or lead.source_count >= 2:
        return _NEWS_DIRECT_STRONG_SCORE
    if lead.confidence >= 0.45:
        return _NEWS_DIRECT_MEDIUM_SCORE
    return _NEWS_DIRECT_WEAK_SCORE


def _news_evidence_stack_summary(metrics: dict[str, object]) -> str:
    support_count = int(metrics.get("news_catalyst_support_count", 0) or 0)
    oppose_count = int(metrics.get("news_catalyst_oppose_count", 0) or 0)
    review_count = int(metrics.get("news_catalyst_review_count", 0) or 0)
    parts: list[str] = []
    if support_count:
        parts.append(f"支持 {support_count} 条")
    if oppose_count:
        parts.append(f"反对 {oppose_count} 条")
    if review_count:
        parts.append(f"待复核 {review_count} 条")
    return "｜".join(parts)


def _pick_relevance_text(pick: PickResult) -> str:
    values: list[str] = [pick.symbol, pick.name]
    values.extend(str(reason) for reason in pick.reasons)
    values.extend(str(strategy) for strategy in pick.strategies)
    metrics = pick.metrics or {}
    values.extend(
        str(metrics.get(key, "")) for key in ("sector", "industry", "candidate_status")
    )
    values.extend(_SYMBOL_THEME_TAGS.get(pick.symbol, ()))
    text = " ".join(value.strip().lower() for value in values if str(value).strip())
    return text
