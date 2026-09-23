"""Unit tests for the news-catalyst artifact preservation guard.

一次瞬时来源失败（failed/timeout）不得抹掉当天已落盘的有效产物，否则下游
（/api/catalyst、首页快照）会长时间空数据。守护 `_should_preserve_existing_catalyst_artifact`。
"""

from __future__ import annotations

from aqsp.cli import _should_preserve_existing_catalyst_artifact as keep


def test_preserves_when_new_run_failed_and_existing_usable_same_day() -> None:
    assert keep("failed", "2026-09-23", "ok", "2026-09-23") is True
    assert keep("timeout", "2026-09-23", "partial", "2026-09-23") is True
    assert keep("failed", "2026-09-23", "partial", "2026-09-23") is True


def test_overwrites_when_new_run_is_usable() -> None:
    assert keep("ok", "2026-09-23", "ok", "2026-09-23") is False
    assert keep("partial", "2026-09-23", "failed", "2026-09-23") is False


def test_overwrites_when_no_existing_artifact() -> None:
    assert keep("timeout", "2026-09-23", "", "") is False
    assert keep("failed", "2026-09-23", "", "") is False


def test_overwrites_when_existing_is_not_usable() -> None:
    assert keep("timeout", "2026-09-23", "failed", "2026-09-23") is False
    assert keep("timeout", "2026-09-23", "timeout", "2026-09-23") is False


def test_overwrites_when_existing_is_a_different_day() -> None:
    assert keep("timeout", "2026-09-23", "ok", "2026-09-22") is False
