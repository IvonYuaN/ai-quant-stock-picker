"""Tests for cross-run rate-limit fallback frequency / recovery tracking.

These exercises run on a temp cache dir (AQSP_INTRADAY_CACHE_DIR) so they never
touch the production cache and stay offline (no live source needed).
"""
import json
from datetime import timedelta

from aqsp.data.intraday import (
    now_shanghai,
    record_rate_limit_fallback,
    summarize_rate_limit_window,
    _rate_limit_state_path,
)


def test_record_then_summarize_counts_current_event(monkeypatch, tmp_path):
    monkeypatch.setenv("AQSP_INTRADAY_CACHE_DIR", str(tmp_path))
    assert summarize_rate_limit_window(30) == {
        "events": 0,
        "last_ts": None,
        "recovered": False,
        "window_minutes": 30,
    }
    record_rate_limit_fallback(["600000"])
    s = summarize_rate_limit_window(30)
    assert s["events"] == 1
    assert s["last_ts"]
    assert s["recovered"] is False
    raw = json.loads(_rate_limit_state_path().read_text(encoding="utf-8"))
    assert len(raw) == 1 and raw[0]["symbols"] == ["600000"]


def test_summarize_recent_window_and_recovery(monkeypatch, tmp_path):
    monkeypatch.setenv("AQSP_INTRADAY_CACHE_DIR", str(tmp_path))
    now = now_shanghai()
    # 2 条近(<30min) + 1 条 2h 前(窗口内排除,但影响 last_ts)
    state = [
        {"ts": (now - timedelta(minutes=5)).isoformat(), "symbols": ["600000"]},
        {"ts": (now - timedelta(minutes=10)).isoformat(), "symbols": ["600036"]},
        {"ts": (now - timedelta(hours=2)).isoformat(), "symbols": ["600001"]},
    ]
    _rate_limit_state_path().write_text(json.dumps(state), encoding="utf-8")
    s = summarize_rate_limit_window(30)
    assert s["events"] == 2
    assert s["recovered"] is False
    # 全部 >30min → 窗口内无事件,但有过往记录 → recovered=True(自愈)
    old = [{"ts": (now - timedelta(hours=1)).isoformat(), "symbols": ["600000"]}]
    _rate_limit_state_path().write_text(json.dumps(old), encoding="utf-8")
    s2 = summarize_rate_limit_window(30)
    assert s2["events"] == 0
    assert s2["recovered"] is True


def test_record_truncates_beyond_24h(monkeypatch, tmp_path):
    monkeypatch.setenv("AQSP_INTRADAY_CACHE_DIR", str(tmp_path))
    now = now_shanghai()
    state = [
        {"ts": (now - timedelta(minutes=1)).isoformat(), "symbols": ["600000"]},
        {"ts": (now - timedelta(hours=25)).isoformat(), "symbols": ["600001"]},
    ]
    _rate_limit_state_path().write_text(json.dumps(state), encoding="utf-8")
    record_rate_limit_fallback(["600036"])  # 追加当前,应剔除 25h 前
    raw = json.loads(_rate_limit_state_path().read_text(encoding="utf-8"))
    assert len(raw) == 2  # 1min前 + 当前,25h前已剔除
    assert {r["symbols"][0] for r in raw} == {"600000", "600036"}
