"""除权除息保护：不复权跳空不得把持仓误判成「该卖」。

场景：10 送 10 当天不复权价腰斩、前复权价基本不动 ⇒ L1 会假触发。
守卫只在「已触发」的 L1/L5 上做一次不复权/前复权日收益核对，差额 ≥0.5pp 时
把「该卖」降级为「观察」并写入提示；取不到数据则保持原提醒（不静默吞风险）。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "backend"))

import live_risk_service as service  # noqa: E402
from aqsp.risk.live_risk import SellReminder  # noqa: E402


def _reminder(level="L1", hint="该卖", symbol="600000", pnl=-0.1):
    return SellReminder(
        symbol=symbol, level=level, current_price=9.0, threshold_price=9.7,
        pnl_pct=pnl, sellable_now=True, action_hint=hint,
        basis={"rule": "成本价 10.00 × (1−3%) = 9.70", "actual": "现价 9.00",
               "evidence": "L1 硬止损"},
    )


def _frames(raw_closes, qfq_closes):
    def frame(values):
        return pd.DataFrame({"date": pd.date_range("2026-11-05", periods=len(values)),
                             "close": values})
    return {"600000": frame(raw_closes)}, {"600000": frame(qfq_closes)}


class _Source:
    def __init__(self, raw, qfq, fail=False):
        self.raw, self.qfq, self.fail = raw, qfq, fail

    def fetch_daily(self, symbols, start, end, adjust):
        if self.fail:
            raise RuntimeError("source down")
        return self.raw if adjust == "" else self.qfq


def _run(monkeypatch, *, raw, qfq, fail=False, reminders=None, holdings=None):
    monkeypatch.setattr(service, "EX_RIGHT_GUARD_ENABLED", True)
    source = _Source(raw, qfq, fail)
    monkeypatch.setattr(
        "aqsp.data.source_factory.build_data_source", lambda *a, **k: source
    )
    return service._downgrade_on_corporate_action(
        reminders if reminders is not None else [_reminder()],
        holdings if holdings is not None else [{"code": "600000", "opened_at": "2026-11-05"}],
        today=date(2026, 11, 9),
        cache_path=Path("/tmp/x.db"),
    )


def test_downgrades_when_raw_drops_much_more_than_qfq(monkeypatch):
    raw, qfq = _frames([20.0, 10.0], [10.0, 10.0])  # 送转：不复权腰斩，前复权不动
    out = _run(monkeypatch, raw=raw, qfq=qfq,
               reminders=[_reminder(pnl=-0.5)])
    assert out[0].action_hint == "观察"
    assert "疑似除权除息" in out[0].basis["evidence"]
    assert "除权除息日保护" in out[0].t1_note


def test_keeps_alert_when_returns_match(monkeypatch):
    raw, qfq = _frames([10.5, 9.9], [10.5, 9.9])
    out = _run(monkeypatch, raw=raw, qfq=qfq,
               reminders=[_reminder(pnl=9.9 / 10.5 - 1)])
    assert out[0].action_hint == "该卖"
    assert "疑似除权除息" not in out[0].basis["evidence"]


def test_keeps_alert_when_source_fails(monkeypatch):
    raw, qfq = _frames([20.0, 10.0], [10.0, 10.0])
    out = _run(monkeypatch, raw=raw, qfq=qfq, fail=True)
    assert out[0].action_hint == "该卖"


def test_ignores_non_price_levels(monkeypatch):
    raw, qfq = _frames([20.0, 10.0], [10.0, 10.0])
    out = _run(monkeypatch, raw=raw, qfq=qfq, reminders=[_reminder(level="L6")])
    assert out[0].action_hint == "该卖"


def test_returns_early_without_l1_l5(monkeypatch):
    monkeypatch.setattr(service, "EX_RIGHT_GUARD_ENABLED", True)
    monkeypatch.setattr(
        "aqsp.data.source_factory.build_data_source",
        lambda *a, **k: pytest.fail("无 L1/L5 时不应发起网络请求"),
    )
    out = service._downgrade_on_corporate_action(
        [], [{"code": "600000", "opened_at": "2026-11-05"}],
        today=date(2026, 11, 9), cache_path=Path("/tmp/x.db"),
    )
    assert out == []
