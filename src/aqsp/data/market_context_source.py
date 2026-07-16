from __future__ import annotations

import json
import math
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from json import JSONDecodeError
from urllib.error import URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from aqsp.core.time import now_shanghai, to_shanghai
from aqsp.market_context import (
    REALTIME_CROSS_MARKET_INSTRUMENTS,
    RealtimeCrossMarketContext,
    RealtimeCrossMarketPolicy,
    build_realtime_cross_market_context,
)

JsonPath = str | tuple[str | int, ...]
JsonTransport = Callable[[str, Mapping[str, str], float], object]

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "aqsp-market-context/1.0",
}
_UNIX_MILLISECONDS_THRESHOLD = 10_000_000_000
_MAX_MARKET_CONTEXT_CONCURRENCY = 2
_INSTRUMENT_ALIASES = {
    "SPX": "SPX",
    "SP500": "SPX",
    "S&P500": "SPX",
    "NASDAQ100": "NASDAQ100",
    "NDX": "NASDAQ100",
    "HSI": "HSI",
    "DXY": "DXY",
    "US10Y": "US10Y",
    "US10": "US10Y",
    "WTI": "WTI",
}
_DEFAULT_YAHOO_SYMBOLS = {
    "SPX": "^GSPC",
    "NASDAQ100": "^NDX",
    "HSI": "^HSI",
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "WTI": "CL=F",
}
_DEFAULT_EASTMONEY_SECIDS = {
    "SPX": "100.SPX",
    "NASDAQ100": "100.NDX100",
    "HSI": "100.HSI",
    "DXY": "100.UDI",
    "US10Y": "171.US10Y",
    "WTI": "102.CL00Y",
}


@dataclass(frozen=True)
class HttpJsonProvider:
    """One configurable HTTP/JSON endpoint for one market instrument."""

    name: str
    url: str
    value_path: JsonPath = "value"
    change_pct_path: JsonPath | None = "change_pct"
    observed_at_path: JsonPath = "observed_at"
    source_path: JsonPath | None = None
    source_url_path: JsonPath | None = None
    timestamp_source: str = "vendor"
    request_symbol: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Mapping[str, str | int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("HTTP JSON provider name 不能为空")
        if not self.url.strip():
            raise ValueError("HTTP JSON provider url 不能为空")
        if not self.timestamp_source.strip():
            raise ValueError("HTTP JSON provider timestamp_source 不能为空")


class _ProviderTimeout(Exception):
    pass


class MarketContextSource:
    """Read-only realtime cross-market source with per-instrument fallback."""

    name = "market_context_http"

    def __init__(
        self,
        providers: Mapping[str, Sequence[HttpJsonProvider | Mapping[str, object]]]
        | None = None,
        *,
        transport: JsonTransport | None = None,
        clock: Callable[[], datetime] = now_shanghai,
    ) -> None:
        self.providers = _normalize_provider_config(
            providers if providers is not None else default_market_context_providers()
        )
        self._transport = transport or _fetch_json
        self._clock = clock

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, object],
        *,
        transport: JsonTransport | None = None,
        clock: Callable[[], datetime] = now_shanghai,
    ) -> "MarketContextSource":
        raw = config.get("providers", config)
        if not isinstance(raw, Mapping):
            raise ValueError("market context providers 配置必须是 mapping")
        return cls(raw, transport=transport, clock=clock)

    def fetch(
        self,
        *,
        now: datetime | None = None,
        policy: RealtimeCrossMarketPolicy = RealtimeCrossMarketPolicy(),
    ) -> RealtimeCrossMarketContext:
        """Fetch payloads and let the existing pure builder apply freshness."""

        current = to_shanghai(now or self._clock())
        payload = self.fetch_payload(now=current, policy=policy)
        return build_realtime_cross_market_context(payload, now=current, policy=policy)

    fetch_context = fetch

    def fetch_payload(
        self,
        *,
        now: datetime | None = None,
        policy: RealtimeCrossMarketPolicy = RealtimeCrossMarketPolicy(),
    ) -> dict[str, dict[str, object]]:
        current = to_shanghai(now or self._clock())
        return {
            instrument: self._fetch_instrument(instrument, current, policy)
            for instrument in REALTIME_CROSS_MARKET_INSTRUMENTS
        }

    def fetch_payload_concurrent(
        self,
        *,
        now: datetime | None = None,
        policy: RealtimeCrossMarketPolicy = RealtimeCrossMarketPolicy(),
    ) -> dict[str, dict[str, object]]:
        """Fetch independent instruments concurrently within provider deadlines."""
        current = to_shanghai(now or self._clock())
        with ThreadPoolExecutor(
            max_workers=_MAX_MARKET_CONTEXT_CONCURRENCY,
            thread_name_prefix="aqsp-market-context",
        ) as executor:
            futures = {
                instrument: executor.submit(
                    self._fetch_instrument,
                    instrument,
                    current,
                    policy,
                )
                for instrument in REALTIME_CROSS_MARKET_INSTRUMENTS
            }
            return {
                instrument: futures[instrument].result()
                for instrument in REALTIME_CROSS_MARKET_INSTRUMENTS
            }

    def _fetch_instrument(
        self,
        instrument: str,
        fetched_at: datetime,
        policy: RealtimeCrossMarketPolicy,
    ) -> dict[str, object]:
        configured = self.providers.get(instrument, ())
        if not configured:
            return _failure_record(
                instrument, "unavailable", "未配置实时 provider", fetched_at
            )

        failures: list[str] = []
        all_timeouts = True
        for provider in configured:
            try:
                return self._fetch_provider(instrument, provider, fetched_at, policy)
            except _ProviderTimeout as exc:
                failures.append(f"{provider.name}: timeout ({exc})")
            except Exception as exc:
                all_timeouts = False
                failures.append(f"{provider.name}: unavailable ({exc})")

        status = "timeout" if all_timeouts else "unavailable"
        return _failure_record(
            instrument,
            status,
            "; ".join(failures) or "所有 provider 均不可用",
            fetched_at,
            source=configured[-1].name,
            source_url=_render_url(configured[-1], instrument),
        )

    def _fetch_provider(
        self,
        instrument: str,
        provider: HttpJsonProvider,
        fetched_at: datetime,
        policy: RealtimeCrossMarketPolicy,
    ) -> dict[str, object]:
        url = _render_url(provider, instrument)
        headers = {**_DEFAULT_HEADERS, **dict(provider.headers)}
        started = time.monotonic()
        try:
            body = self._transport(url, headers, policy.timeout_seconds)
        except (TimeoutError, socket.timeout) as exc:
            raise _ProviderTimeout(str(exc) or "request timeout") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise _ProviderTimeout(str(exc.reason) or "request timeout") from exc
            raise
        elapsed = time.monotonic() - started
        if elapsed > policy.timeout_seconds:
            raise _ProviderTimeout(
                f"耗时 {elapsed:.3f}s > {policy.timeout_seconds:.3f}s"
            )
        if not isinstance(body, (Mapping, list)):
            raise ValueError("JSON 根节点必须是 object 或 array")

        value = _finite_positive(_read_path(body, provider.value_path))
        observed_at = _normalize_observed_at(
            _read_path(body, provider.observed_at_path),
        )
        if value is None or not observed_at:
            raise ValueError("JSON 缺少有限正值 value 或带时区 observed_at")
        source = _text(_read_path(body, provider.source_path)) or provider.name
        source_url = _text(_read_path(body, provider.source_url_path)) or url
        change_pct = _finite(_read_path(body, provider.change_pct_path))
        provenance = {
            "source": source,
            "source_url": source_url,
            "observed_at": observed_at,
            "fetched_at": fetched_at.isoformat(timespec="seconds"),
            "timestamp_source": provider.timestamp_source,
        }
        return {
            "value": value,
            "change_pct": change_pct,
            "source": source,
            "source_url": source_url,
            "observed_at": observed_at,
            "fetched_at": provenance["fetched_at"],
            "timestamp_source": provider.timestamp_source,
            "provenance": provenance,
        }


def fetch_live_market_context_payload(
    *,
    timeout_seconds: float = 1.0,
    now: datetime | None = None,
) -> dict[str, dict[str, object]]:
    """Fetch the live cross-market payload for a short-term runtime task."""
    policy = RealtimeCrossMarketPolicy(timeout_seconds=timeout_seconds)
    return MarketContextSource().fetch_payload_concurrent(now=now, policy=policy)


def default_market_context_providers() -> dict[str, tuple[HttpJsonProvider, ...]]:
    """Return keyless public defaults; stale data is rejected by the policy."""

    providers: dict[str, tuple[HttpJsonProvider, ...]] = {}
    for instrument, symbol in _DEFAULT_YAHOO_SYMBOLS.items():
        providers[instrument] = (
            _eastmoney_provider(
                _DEFAULT_EASTMONEY_SECIDS[instrument],
                name="eastmoney_push2",
            ),
            _eastmoney_provider(
                _DEFAULT_EASTMONEY_SECIDS[instrument],
                name="eastmoney_push2_retry",
            ),
            _yahoo_provider("yahoo_chart_primary", "query1.finance.yahoo.com", symbol),
        )
    return providers


def _eastmoney_provider(secid: str, *, name: str) -> HttpJsonProvider:
    return HttpJsonProvider(
        name=name,
        url="https://push2.eastmoney.com/api/qt/stock/get",
        value_path="data.f43",
        change_pct_path="data.f170",
        observed_at_path="data.f86",
        timestamp_source="vendor",
        params={
            "secid": secid,
            "fltt": 2,
            "fields": "f43,f170,f86",
        },
    )


def _yahoo_provider(name: str, host: str, symbol: str) -> HttpJsonProvider:
    return HttpJsonProvider(
        name=name,
        url=f"https://{host}/v8/finance/chart/{{symbol}}",
        request_symbol=symbol,
        value_path="chart.result.0.meta.regularMarketPrice",
        change_pct_path="chart.result.0.meta.regularMarketChangePercent",
        observed_at_path="chart.result.0.meta.regularMarketTime",
        params={"range": "1d", "interval": "1m"},
    )


def _normalize_provider_config(
    raw: Mapping[str, Sequence[HttpJsonProvider | Mapping[str, object]]],
) -> dict[str, tuple[HttpJsonProvider, ...]]:
    normalized: dict[str, tuple[HttpJsonProvider, ...]] = {}
    for raw_instrument, raw_providers in raw.items():
        instrument = _canonical_instrument(raw_instrument)
        if not instrument:
            continue
        if isinstance(raw_providers, (str, bytes)) or not isinstance(
            raw_providers, Sequence
        ):
            raise ValueError(f"{raw_instrument} providers 必须是 sequence")
        converted = tuple(_coerce_provider(item) for item in raw_providers)
        normalized[instrument] = converted
    return normalized


def _coerce_provider(
    raw: HttpJsonProvider | Mapping[str, object],
) -> HttpJsonProvider:
    if isinstance(raw, HttpJsonProvider):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError("provider 必须是 HttpJsonProvider 或 mapping")
    allowed = {
        "name",
        "url",
        "value_path",
        "change_pct_path",
        "observed_at_path",
        "source_path",
        "source_url_path",
        "timestamp_source",
        "request_symbol",
        "headers",
        "params",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"provider 包含未知配置: {sorted(unknown)}")
    return HttpJsonProvider(**dict(raw))


def _fetch_json(url: str, headers: Mapping[str, str], timeout: float) -> object:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except JSONDecodeError as exc:
        raise ValueError("响应不是有效 JSON") from exc


def _render_url(provider: HttpJsonProvider, instrument: str) -> str:
    symbol = provider.request_symbol or instrument
    try:
        rendered = provider.url.format(instrument=instrument, symbol=symbol)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"provider url 模板无效: {provider.url}") from exc
    if provider.params:
        separator = "&" if urlparse(rendered).query else "?"
        rendered += separator + urlencode(provider.params)
    return rendered


def _read_path(body: object, path: JsonPath | None) -> object:
    if path is None:
        return None
    parts = path.split(".") if isinstance(path, str) else path
    current = body
    for part in parts:
        if isinstance(current, Mapping):
            current = current.get(part)
        elif (
            isinstance(current, list)
            and isinstance(part, int)
            and 0 <= part < len(current)
        ):
            current = current[part]
        else:
            return None
    return current


def _normalize_observed_at(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return ""
        seconds = (
            float(value) / 1000
            if abs(float(value)) >= _UNIX_MILLISECONDS_THRESHOLD
            else float(value)
        )
        return to_shanghai(datetime.fromtimestamp(seconds, tz=timezone.utc)).isoformat(
            timespec="seconds"
        )
    return str(value).strip()


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _finite_positive(value: object) -> float | None:
    parsed = _finite(value)
    return parsed if parsed is not None and parsed > 0 else None


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical_instrument(value: object) -> str:
    token = "".join(str(value or "").strip().upper().split())
    return _INSTRUMENT_ALIASES.get(token, "")


def _failure_record(
    instrument: str,
    status: str,
    detail: str,
    fetched_at: datetime,
    *,
    source: str = "",
    source_url: str = "",
) -> dict[str, object]:
    fetched = fetched_at.isoformat(timespec="seconds")
    provenance = {
        "source": source,
        "source_url": source_url,
        "observed_at": "",
        "fetched_at": fetched,
        "timestamp_source": "",
    }
    return {
        "instrument": instrument,
        "status": status,
        "value": None,
        "change_pct": None,
        "source": source,
        "source_url": source_url,
        "observed_at": "",
        "fetched_at": fetched,
        "timestamp_source": "",
        "provenance": provenance,
        "detail": detail,
    }


__all__ = [
    "HttpJsonProvider",
    "JsonPath",
    "JsonTransport",
    "MarketContextSource",
    "default_market_context_providers",
]
