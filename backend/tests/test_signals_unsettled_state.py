"""「台账有信号但还没结算」不得复用「台账不可用」的占位字段。

`performance_payload()` 的 `settled.empty` 分支原先把 `_unavailable()` 整包铺开、
只把 `available` 改成 `True`，于是同一份载荷里出现了三句假话：

- `freshness` 仍是 `_freshness_unavailable()`（`latest_signal_date: ""`）
  ⇒ 页面写「最新信号日 未记录」，而 `signal_date` 明明就在 `rows` 里；
- `overall` 仍是 `None` ⇒ 标题写「条数未知」，而「0 条已结算」是**确定的零**，
  不是算不出来（`_overall()` 对空集返回 `None`，那是另一个语义）；
- `reason` 仍是「台账中还没有已结算的信号」⇒ `available=True` 却带着
  「为什么不可用」的原因，下游任何「reason 非空即异常」的判断都会误报。

本文件锁住：这一分支必须给**真实**新鲜度、**确定为零**的 overall、**空** reason，
并保留 `status_counts`；对照组是「台账文件不存在」——那才是真不可用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import performance_bridge

_CST = timezone(timedelta(hours=8))


@pytest.fixture
def unsettled(monkeypatch, tmp_path):
    """台账文件存在、有记录，但没有一行是 validated。"""
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text('{"status": "pending"}\n', encoding="utf-8")
    rows = [
        {"symbol": "600000", "status": "pending", "signal_date": "2026-10-20"},
        {"symbol": "600001", "status": "pending", "signal_date": "2026-10-21"},
        {"symbol": "600002", "status": "closed", "signal_date": "2026-10-21"},
    ]
    monkeypatch.setattr(performance_bridge, "_ledger_path", lambda: ledger)
    monkeypatch.setattr(performance_bridge, "read_ledger", lambda path: rows)
    monkeypatch.setattr(
        performance_bridge,
        "now_shanghai",
        lambda: datetime(2026, 10, 23, 15, 0, tzinfo=_CST),
    )
    return ledger, rows


def test_unsettled_ledger_is_available(unsettled) -> None:
    payload = performance_bridge.performance_payload()
    assert payload["available"] is True
    assert payload["status_counts"] == {"pending": 2, "closed": 1}


def test_unsettled_ledger_reports_real_freshness(unsettled) -> None:
    """🔴 关键：新鲜度必须来自 rows，不能沿用「不可用」占位。"""
    payload = performance_bridge.performance_payload()
    freshness = payload["freshness"]
    assert freshness["latest_signal_date"] == "2026-10-21", "signal_date 就在 rows 里"
    assert freshness["ledger_updated_at"] != "", "台账读到了，mtime 就可知"


def test_unsettled_ledger_reports_known_zero_overall(unsettled) -> None:
    """0 条是确定的零，不是「算不出来」——后者由 `_overall()` 的 None 表示。"""
    payload = performance_bridge.performance_payload()
    assert payload["overall"] == {
        "observations": 0,
        "win_count": 0,
        "hit_rate": 0.0,
        "displayable": False,
    }


def test_unsettled_ledger_carries_no_unavailable_reason(unsettled) -> None:
    """`reason` 只解释「为什么不可用」；available=True 时必须为空。"""
    payload = performance_bridge.performance_payload()
    assert payload["reason"] == ""


def test_missing_ledger_still_reports_unavailable(monkeypatch, tmp_path) -> None:
    """对照组：文件不存在才是真不可用 —— 这三项必须与上面的分支可区分。"""
    monkeypatch.setattr(performance_bridge, "_ledger_path", lambda: tmp_path / "nope.jsonl")
    monkeypatch.setattr(
        performance_bridge,
        "now_shanghai",
        lambda: datetime(2026, 10, 23, 15, 0, tzinfo=_CST),
    )
    payload = performance_bridge.performance_payload()
    assert payload["available"] is False
    assert payload["reason"] != ""
    assert payload["freshness"]["latest_signal_date"] == ""
    assert payload["overall"] is None


def test_overall_none_means_cannot_compute_not_zero() -> None:
    """固定上面用到的语义前提：`_overall()` 对空集返回 None。"""
    assert performance_bridge._overall(pd.DataFrame()) is None
