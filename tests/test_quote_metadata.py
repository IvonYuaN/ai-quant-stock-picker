from __future__ import annotations

from aqsp.data.quote_metadata import (
    LIVE_SHORT_MAX_FUTURE_SECONDS,
    parse_legacy_quote_timestamp,
    parse_vendor_timestamp,
    quote_timestamp_metadata,
)


def test_live_short_quote_timestamp_does_not_allow_future_vendor_time() -> None:
    assert LIVE_SHORT_MAX_FUTURE_SECONDS == 0


def test_parse_legacy_quote_timestamp_uses_vendor_date_and_time() -> None:
    parts = [""] * 32
    parts[30] = "2026-07-13"
    parts[31] = "10:35:00"

    assert parse_legacy_quote_timestamp(parts) == "2026-07-13T10:35:00+08:00"


def test_quote_timestamp_metadata_falls_back_explicitly_when_vendor_time_missing() -> (
    None
):
    metadata = quote_timestamp_metadata(
        "",
        "2026-07-13T10:35:01+08:00",
    )

    assert metadata == {
        "ts": "2026-07-13T10:35:01+08:00",
        "vendor_ts": "",
        "received_at": "2026-07-13T10:35:01+08:00",
        "timestamp_source": "received_at",
    }


def test_parse_vendor_timestamp_rejects_date_only_value() -> None:
    assert parse_vendor_timestamp("2026-07-13") == ""
