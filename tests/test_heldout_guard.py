"""Held-out guard tests, including hardened date parsing edge cases."""

from __future__ import annotations

import pytest

from aqsp.cli import _assert_not_heldout, HELDOUT_TRAIN_CUTOFF


# ---- 边界内：放行 ----


def test_within_train_passes():
    """end 正好等于边界 → 放行（不抛）。"""
    _assert_not_heldout("2024-12-31", allow=False)


def test_before_cutoff_passes():
    _assert_not_heldout("2023-06-30", allow=False)


# ---- 越界：默认拦截 ----


def test_heldout_blocked_by_default():
    """end 越过边界且未开 --allow-heldout → SystemExit。"""
    with pytest.raises(SystemExit) as exc:
        _assert_not_heldout("2026-04-30", allow=False)
    assert "§1.3 #9" in str(exc.value)


def test_boundary_just_past_blocks():
    """边界后一天 2025-01-01 → 拦截。"""
    with pytest.raises(SystemExit):
        _assert_not_heldout("2025-01-01", allow=False)


# ---- 越界 + allow：放行并告警 ----


def test_heldout_allowed_with_flag(capsys):
    """显式 --allow-heldout → 放行（不抛），但打印告警。"""
    _assert_not_heldout("2026-04-30", allow=True)
    out = capsys.readouterr().out
    assert "allow-heldout" in out or "放行" in out


# ---- 加固：日期解析边缘情况 ----


def test_whitespace_date_passes():
    """带空格的合法日期 → strip 后正常放行。
    （旧版字符串字典序比较会把 ' 2024-12-31 ' 误判，这是回归测试。）"""
    _assert_not_heldout(" 2024-12-31 ", allow=False)


def test_non_iso_format_fail_loud():
    """非 ISO 格式（斜杠）→ fail loud，不静默放行。"""
    with pytest.raises(SystemExit) as exc:
        _assert_not_heldout("2024/12/31", allow=False)
    assert "合法 ISO 日期" in str(exc.value)


def test_garbage_date_fail_loud():
    with pytest.raises(SystemExit):
        _assert_not_heldout("notadate", allow=False)


def test_empty_date_fail_loud():
    with pytest.raises(SystemExit):
        _assert_not_heldout("", allow=False)


def test_cutoff_constant_is_iso():
    """边界常量本身必须是合法 ISO，否则护栏会自爆。"""
    from datetime import date

    assert date.fromisoformat(HELDOUT_TRAIN_CUTOFF) == date(2024, 12, 31)
