from __future__ import annotations

from datetime import datetime
from threading import Barrier

import pytest

from aqsp.data.market_context_source import (
    HttpJsonProvider,
    MarketContextSource,
    default_market_context_providers,
    fetch_live_market_context_payload,
)
from aqsp.market_context import (
    REALTIME_CROSS_MARKET_INSTRUMENTS,
    RealtimeCrossMarketPolicy,
)


NOW = datetime.fromisoformat("2026-07-14T10:00:00+08:00")


def _provider(name: str, url: str) -> HttpJsonProvider:
    return HttpJsonProvider(
        name=name,
        url=url,
        value_path="data.value",
        change_pct_path="data.change_pct",
        observed_at_path="data.observed_at",
    )


def test_market_context_source_fetches_json_and_preserves_provenance() -> None:
    calls: list[tuple[str, float]] = []

    def transport(url: str, _headers: object, timeout: float) -> object:
        calls.append((url, timeout))
        return {
            "data": {
                "value": 5_500.0,
                "change_pct": 0.8,
                "observed_at": "2026-07-14T09:59:30+08:00",
            }
        }

    source = MarketContextSource(
        {"SPX": [_provider("mock-spx", "https://mock.test/spx")]},
        transport=transport,
    )
    context = source.fetch(
        now=NOW,
        policy=RealtimeCrossMarketPolicy(timeout_seconds=2.0),
    )

    spx = context.observations[0]
    assert context.status == "partial"
    assert spx.status == "fresh"
    assert spx.value == pytest.approx(5_500.0)
    assert spx.change_pct == pytest.approx(0.8)
    assert spx.source == "mock-spx"
    assert spx.observed_at == "2026-07-14T09:59:30+08:00"
    assert spx.fetched_at == "2026-07-14T10:00:00+08:00"
    assert calls == [("https://mock.test/spx", 2.0)]


def test_market_context_source_accepts_shanghai_gold_alias_and_keeps_freshness() -> (
    None
):
    def transport(_url: str, _headers: object, _timeout: float) -> object:
        return {
            "data": {
                "value": 2_400.0,
                "observed_at": "2026-07-14T09:59:30+08:00",
            }
        }

    source = MarketContextSource(
        {"上海金": [_provider("shanghai-gold-fallback", "https://mock.test/gold")]},
        transport=transport,
    )

    context = source.fetch(now=NOW)
    gold = next(item for item in context.observations if item.instrument == "GOLD")

    assert gold.status == "fresh"
    assert gold.value == pytest.approx(2_400.0)
    assert gold.source == "shanghai-gold-fallback"


def test_market_context_source_falls_back_per_instrument() -> None:
    requested: list[str] = []

    def transport(url: str, _headers: object, _timeout: float) -> object:
        requested.append(url)
        if "primary-spx" in url:
            raise ConnectionError("primary down")
        return {
            "data": {
                "value": 100.0,
                "observed_at": "2026-07-14T09:59:00+08:00",
            }
        }

    source = MarketContextSource(
        {
            "SPX": [
                _provider("primary", "https://mock.test/primary-spx"),
                _provider("fallback", "https://mock.test/fallback-spx"),
            ],
            "HSI": [_provider("hsi", "https://mock.test/hsi")],
        },
        transport=transport,
    )
    context = source.fetch(now=NOW)

    assert context.observations[0].source == "fallback"
    assert context.observations[0].status == "fresh"
    assert context.observations[1].status == "unavailable"
    assert requested == [
        "https://mock.test/primary-spx",
        "https://mock.test/fallback-spx",
        "https://mock.test/hsi",
    ]


def test_market_context_source_keeps_timeout_unavailable_without_zero() -> None:
    def transport(_url: str, _headers: object, _timeout: float) -> object:
        raise TimeoutError("slow provider")

    source = MarketContextSource(
        {"DXY": [_provider("timeout", "https://mock.test/dxy")]},
        transport=transport,
    )
    context = source.fetch(now=NOW)

    dxy = context.observations[3]
    assert dxy.status == "timeout"
    assert dxy.value is None
    assert dxy.value != 0
    assert dxy.provenance.fetched_at == "2026-07-14T10:00:00+08:00"


def test_market_context_source_delegates_staleness_to_existing_policy() -> None:
    def transport(_url: str, _headers: object, _timeout: float) -> object:
        return {
            "data": {
                "value": 100.0,
                "observed_at": "2026-07-14T09:00:00+08:00",
            }
        }

    source = MarketContextSource(
        {"WTI": [_provider("mock-wti", "https://mock.test/wti")]},
        transport=transport,
    )
    context = source.fetch(
        now=NOW,
        policy=RealtimeCrossMarketPolicy(max_age_seconds=60),
    )

    assert context.observations[5].status == "stale"
    assert context.observations[5].value == pytest.approx(100.0)


def test_market_context_source_rejects_invalid_provider_config() -> None:
    with pytest.raises(ValueError, match="未知配置"):
        MarketContextSource.from_config(
            {"SPX": [{"name": "mock", "url": "https://mock.test", "api_key": "x"}]}
        )


def test_default_market_context_providers_use_verified_eastmoney_secids() -> None:
    providers = default_market_context_providers()
    expected = {
        "SPX": "100.SPX",
        "NASDAQ100": "100.NDX100",
        "HSI": "100.HSI",
        "DXY": "100.UDI",
        "US10Y": "171.US10Y",
        "WTI": "102.CL00Y",
    }

    assert {
        instrument: str(items[0].params["secid"])
        for instrument, items in providers.items()
        if instrument != "GOLD"
    } == expected
    assert providers["GOLD"][0].name == "yahoo_comex_gold_fallback"
    assert providers["GOLD"][0].request_symbol == "GC=F"
    assert providers["GOLD"][0].params == {"range": "1d", "interval": "1m"}
    assert all(
        items[0].name == "eastmoney_push2"
        for instrument, items in providers.items()
        if instrument != "GOLD"
    )
    assert all(
        [item.name for item in items]
        == ["eastmoney_push2", "eastmoney_push2_retry", "yahoo_chart_primary"]
        for instrument, items in providers.items()
        if instrument != "GOLD"
    )


def test_fetch_live_market_context_payload_uses_short_term_timeout(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_fetch_payload(self, *, now=None, policy):
        seen["now"] = now
        seen["timeout"] = policy.timeout_seconds
        return {"SPX": {"status": "unavailable", "value": None}}

    monkeypatch.setattr(
        MarketContextSource,
        "fetch_payload_concurrent",
        fake_fetch_payload,
    )

    payload = fetch_live_market_context_payload(timeout_seconds=0.75, now=NOW)

    assert payload["SPX"]["status"] == "unavailable"
    assert seen == {"now": NOW, "timeout": 0.75}


def test_market_context_live_payload_fetches_instruments_concurrently() -> None:
    barrier = Barrier(2)

    def transport(_url: str, _headers: object, _timeout: float) -> object:
        barrier.wait(timeout=1.0)
        return {
            "data": {
                "value": 100.0,
                "observed_at": "2026-07-14T09:59:30+08:00",
            }
        }

    providers = {
        instrument: [_provider(instrument, f"https://mock.test/{instrument}")]
        for instrument in REALTIME_CROSS_MARKET_INSTRUMENTS
    }
    source = MarketContextSource(providers, transport=transport)

    payload = source.fetch_payload_concurrent(now=NOW)

    assert set(payload) == set(providers)
    assert all("value" in item for item in payload.values())