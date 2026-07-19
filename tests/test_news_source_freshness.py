from __future__ import annotations

from aqsp.data.news_source import _normalize_rss_time


def test_normalize_rss_time_converts_utc_to_shanghai_before_snapshot_storage() -> None:
    assert _normalize_rss_time("Sun, 19 Jul 2026 16:30:00 GMT") == (
        "2026-07-20T00:30:00+08:00"
    )


def test_normalize_rss_time_converts_iso_utc_to_shanghai() -> None:
    assert _normalize_rss_time("2026-07-19T16:30:00Z") == (
        "2026-07-20T00:30:00+08:00"
    )
