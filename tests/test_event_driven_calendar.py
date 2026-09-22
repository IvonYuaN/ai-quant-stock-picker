"""事件驱动策略 × 事件日历 集成测试（`aqsp.strategies.event_driven`）。

守三条接线纪律：
1. `event_calendar=None`（默认）时行为与接入前**逐字一致**；
2. 日历**只补证据，不改分**（score / 目标价 / 仓位必须完全不变）；
3. 日历是 best-effort：任何内部异常都不得让选股链路失败。

测试全部离线，不触网。
"""

from __future__ import annotations

import pandas as pd
import pytest

from aqsp.data.lockup import LockupItem
from aqsp.data.longhubang import LongHubangItem
from aqsp.features.event_calendar import EventCalendar
from aqsp.strategies import EventDrivenStrategy, format_event_signals

_CALENDAR_TAG = EventDrivenStrategy.CALENDAR_TAG


def _make_df(prices: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    """构造测试用 K 线 DataFrame（`date` 为 `%Y-%m-%d` 字符串）。"""
    n = len(prices)
    if volumes is None:
        volumes = [1_000_000] * n
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "symbol": ["600000"] * n,
            "name": ["测试股"] * n,
            "open": prices,
            "high": [p * 1.02 for p in prices],
            "low": [p * 0.98 for p in prices],
            "close": prices,
            "volume": volumes,
            "amount": [p * v for p, v in zip(prices, volumes)],
        }
    )


def _breakout_df() -> pd.DataFrame:
    """29 日横盘 + 末日放量突破上沿 → 触发 `sudden_breakout`（末日 = 2024-01-30）。"""
    return _make_df([10.0] * 29 + [11.5], [1_000_000] * 29 + [2_500_000])


def _surge_df() -> pd.DataFrame:
    """29 日横盘 + 末日放巨量大涨脱离均线 → 触发 `earnings_surge`。"""
    return _make_df([10.0] * 29 + [11.0], [1_000_000] * 29 + [5_000_000])


def _unlock(
    symbol: str = "600000", plan_date: str = "2024-02-10", ratio: float = 0.05
) -> LockupItem:
    return LockupItem(
        symbol=symbol,
        name="测试股",
        plan_date=plan_date,
        lockup_shares=1000.0,
        ratio=ratio,
        lockup_type="首发原股东限售股份",
    )


def _lhb(symbol: str = "600000", trade_date: str = "2024-01-29") -> LongHubangItem:
    return LongHubangItem(
        trade_date=trade_date,
        symbol=symbol,
        name="测试股",
        close_price=11.5,
        change_rate=9.9,
        buy_amount=10000.0,
        sell_amount=2000.0,
        net_amount=8000.0,
        interpretation="机构专用席位买入",
    )


def _only_signal(strategy: EventDrivenStrategy, df: pd.DataFrame, **kwargs):
    signals = strategy.generate_signals({"600000": df}, **kwargs)
    assert len(signals) == 1, "本测试夹具应恰好产出一条信号"
    return signals[0]


# ============================================================
# 1. 默认不注入日历 → 行为不变
# ============================================================


def test_event_type_and_score_when_breakout_fixture_used():
    """先固定夹具语义：避免后续断言建立在错误的事件分类上。"""
    sig = _only_signal(EventDrivenStrategy(), _breakout_df())
    assert sig.event_type == "sudden_breakout"
    assert sig.confidence == pytest.approx(0.8)


def test_no_calendar_evidence_when_calendar_not_injected():
    sig = _only_signal(EventDrivenStrategy(), _breakout_df())
    assert all(_CALENDAR_TAG not in line for line in sig.reasons + sig.risks)
    assert EventDrivenStrategy.NEEDS_LHB_CONFIRM in sig.needs_external_data


def test_no_calendar_evidence_when_empty_calendar_injected():
    sig = _only_signal(
        EventDrivenStrategy(event_calendar=EventCalendar()), _breakout_df()
    )
    assert all(_CALENDAR_TAG not in line for line in sig.reasons + sig.risks)


# ============================================================
# 2. 前瞻预警：限售解禁
# ============================================================


def test_upcoming_unlock_appended_to_risks_when_within_horizon():
    cal = EventCalendar(unlocks=[_unlock(plan_date="2024-02-10", ratio=0.05)])
    sig = _only_signal(EventDrivenStrategy(event_calendar=cal), _breakout_df())
    unlock_risks = [r for r in sig.risks if "解禁预警" in r]
    assert len(unlock_risks) == 1
    assert "2024-02-10" in unlock_risks[0]
    assert "中" in unlock_risks[0]  # ratio 0.05 → medium → 中文标签「中」


def test_past_unlock_not_reported_as_risk_when_event_already_happened():
    cal = EventCalendar(unlocks=[_unlock(plan_date="2024-01-05")])
    sig = _only_signal(EventDrivenStrategy(event_calendar=cal), _breakout_df())
    assert not [r for r in sig.risks if "解禁预警" in r]


def test_no_unlock_risk_when_calendar_lacks_unlock_face():
    """只有龙虎榜面时不得对解禁下任何结论（没数据 ≠ 没事件）。"""
    cal = EventCalendar(longhubang=[_lhb()])
    sig = _only_signal(EventDrivenStrategy(event_calendar=cal), _breakout_df())
    assert not [r for r in sig.risks if "解禁预警" in r]


# ============================================================
# 3. 回溯佐证：龙虎榜
# ============================================================


def test_longhubang_record_appended_to_reasons_and_resolves_needs():
    cal = EventCalendar(longhubang=[_lhb(trade_date="2024-01-29")])
    sig = _only_signal(EventDrivenStrategy(event_calendar=cal), _breakout_df())
    lhb_reasons = [r for r in sig.reasons if "龙虎榜" in r]
    assert len(lhb_reasons) == 1
    assert "机构专用席位买入" in lhb_reasons[0]
    # 日历已回答该维度 → 从待验证清单里消解
    assert EventDrivenStrategy.NEEDS_LHB_CONFIRM not in sig.needs_external_data


def test_missing_longhubang_adds_risk_and_resolves_needs_for_breakout():
    cal = EventCalendar(longhubang=[_lhb(symbol="000001")])
    sig = _only_signal(EventDrivenStrategy(event_calendar=cal), _breakout_df())
    miss = [r for r in sig.risks if "未上龙虎榜" in r]
    assert len(miss) == 1
    assert "5" in miss[0]  # 默认 lookback 5 个自然日
    assert EventDrivenStrategy.NEEDS_LHB_CONFIRM not in sig.needs_external_data


def test_missing_longhubang_does_not_add_risk_when_event_is_earnings_surge():
    """`earnings_surge` 的立论不是「资金埋伏」，不该因未上榜而加风险。"""
    cal = EventCalendar(longhubang=[_lhb(symbol="000001")])
    sig = _only_signal(EventDrivenStrategy(event_calendar=cal), _surge_df())
    assert sig.event_type == "earnings_surge"
    assert not [r for r in sig.risks if "未上龙虎榜" in r]
    # 财报维度仍未接入 → 不应被消解
    assert EventDrivenStrategy.NEEDS_EARNINGS_DATA in sig.needs_external_data


def test_future_longhubang_record_is_not_used_when_after_signal_date():
    """末日之后的记录属于未来信息，必须被排除。"""
    cal = EventCalendar(longhubang=[_lhb(trade_date="2024-02-05")])
    sig = _only_signal(EventDrivenStrategy(event_calendar=cal), _breakout_df())
    assert not [r for r in sig.reasons if "龙虎榜" in r]


# ============================================================
# 4. as_of 锚点
# ============================================================


def test_explicit_as_of_override_changes_unlock_window():
    """同一份解禁数据：按 K 线末日看已过期，按显式 as_of 看则命中窗口。"""
    cal = EventCalendar(unlocks=[_unlock(plan_date="2024-01-20")])
    strategy = EventDrivenStrategy(event_calendar=cal)

    derived = _only_signal(strategy, _breakout_df())
    assert not [r for r in derived.risks if "解禁预警" in r]  # 末日 2024-01-30 → 已过期

    explicit = _only_signal(strategy, _breakout_df(), as_of="2024-01-01")
    assert [
        r for r in explicit.risks if "解禁预警" in r
    ]  # 2024-01-01 起 19 天内 → 命中


def test_annotations_skipped_when_df_date_unparsable():
    df = _breakout_df()
    df["date"] = "bad-date"
    cal = EventCalendar(unlocks=[_unlock(plan_date="2024-02-10")], longhubang=[_lhb()])
    sig = _only_signal(EventDrivenStrategy(event_calendar=cal), df)
    assert all(_CALENDAR_TAG not in line for line in sig.reasons + sig.risks)


# ============================================================
# 5. 只补证据，绝不改分
# ============================================================


def test_calendar_annotation_does_not_change_score_or_targets():
    plain = _only_signal(EventDrivenStrategy(), _breakout_df())
    cal = EventCalendar(
        unlocks=[_unlock(plan_date="2024-02-10")],
        longhubang=[_lhb(trade_date="2024-01-29")],
    )
    enriched = _only_signal(EventDrivenStrategy(event_calendar=cal), _breakout_df())

    assert enriched.score == plain.score
    assert enriched.confidence == plain.confidence
    assert enriched.current_price == plain.current_price
    assert enriched.entry_price == plain.entry_price
    assert enriched.stop_loss == plain.stop_loss
    assert enriched.take_profit == plain.take_profit
    assert enriched.position_pct == plain.position_pct
    assert enriched.holding_period == plain.holding_period
    assert enriched.event_type == plain.event_type


def test_calculate_score_is_unaffected_by_calendar():
    """`calculate_score` 是纯计算函数，日历不得介入。"""
    df = _breakout_df()
    cal = EventCalendar(unlocks=[_unlock()], longhubang=[_lhb()])
    plain = EventDrivenStrategy().calculate_score({"600000": df})
    enriched = EventDrivenStrategy(event_calendar=cal).calculate_score({"600000": df})
    assert plain == enriched


# ============================================================
# 6. fail-soft
# ============================================================


def test_signal_still_produced_when_calendar_raises_internally():
    class _BoomCalendar(EventCalendar):
        def has_unlock_data(self) -> bool:
            raise RuntimeError("boom")

    cal = _BoomCalendar(unlocks=[_unlock()])
    sig = _only_signal(EventDrivenStrategy(event_calendar=cal), _breakout_df())
    # 不抛异常、信号照常产出，只是没有日历证据
    assert all(_CALENDAR_TAG not in line for line in sig.reasons + sig.risks)


# ============================================================
# 7. 端到端展示
# ============================================================


def test_formatted_output_surfaces_calendar_evidence():
    cal = EventCalendar(
        unlocks=[_unlock(plan_date="2024-02-10")],
        longhubang=[_lhb(trade_date="2024-01-29")],
    )
    sig = _only_signal(EventDrivenStrategy(event_calendar=cal), _breakout_df())
    text = format_event_signals([sig])
    assert "事件日历" in text
    assert "解禁" in text
    assert "龙虎榜" in text
