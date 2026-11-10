"""holder overlay 的**语义级**守卫（2026-11-10 审计补测）。

背景：此前测试只锁了 `_has_holder_concentration()`（enabled AND weight>0）与
「total 不变」，**完全没锁**下面三条语义 —— 而 CI 4/4 绿也因此毫无覆盖力：

1. **PIT**：可用时点必须是**披露日** `notice_date`，不是季末日。
   实证披露滞后中位 31 天 / P90 114 天 / 最大 294 天 ⇒ 用季末日就是前视。
2. **全中性退化**：数据缺失/未披露时分数恒 0.5 ⇒ 重排等价于保持原序
   ⇒ overlay **完全无效但不报错**。历史事故：只覆盖 37% 窗口时 ΔSharpe≈0，
   会被误读成「因子无用」。
3. **重排真的改变选股**：因子有区分度时必须选出一组**不同**的票
   （否则 `len(filtered) > n` 等前置条件会让 overlay 静默失效）。

另注：生产红线的前视守卫是**语法级**（只查字面 `shift(-N)` / `rolling(center=True)`），
对「语义上用了未披露数据」完全盲 ⇒ 只能靠本文件的语义测试兜。
"""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aqsp.strategies.base import StrategyConfig
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.holder_concentration import HolderConcentrationStrategy
from aqsp.strategies.thresholds import load_thresholds


def _write_holder_csv(path: Path, rows: list[tuple[str, str, str, float, str]]) -> None:
    """写兼容 HolderNumSource._read_cache 的 CSV。"""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["symbol", "name", "quarter", "holder_count", "notice_date"])
        for symbol, quarter, notice, count, name in rows:
            writer.writerow([symbol, name, quarter, count, notice])


def _frame(end: str, bars: int = 80, drift: float = 0.0) -> pd.DataFrame:
    dates = pd.bdate_range(end=end, periods=bars)
    base = 10.0 * np.cumprod(np.full(bars, 1.0 + drift))
    return pd.DataFrame(
        {
            "date": dates,
            "open": base,
            "high": base * 1.01,
            "low": base * 0.99,
            "close": base,
            "volume": np.full(bars, 1_000_000.0),
            "amount": base * 1_000_000.0,
        }
    )


def _thresholds_overlay(min_total_score: float = 0.0):
    th = load_thresholds()
    return replace(
        th,
        holder_concentration=replace(
            th.holder_concentration, enabled=True, spread=2.0, invert_signal=False
        ),
        composite=replace(
            th.composite,
            holder_concentration_weight=0.3,
            min_total_score=min_total_score,
        ),
    )


# --------------------------------------------------------------------------- #
# 1) PIT：未披露的季度不得使用
# --------------------------------------------------------------------------- #


def test_undisclosed_quarter_is_not_used_before_notice_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notice_date=2026-04-25 的季度，在 as_of=2026-04-20 **不得**被使用。

    旧实现按季末日（2026-03-31）判定 ⇒ 会提前 25 天用上未披露的数据（前视）。
    """
    csv_path = tmp_path / "h.csv"
    _write_holder_csv(
        csv_path,
        [
            ("000001", "2025-12-31", "2026-03-21", 1000.0, "A"),
            ("000001", "2026-03-31", "2026-04-25", 800.0, "A"),  # 户数大降=集中
        ],
    )
    monkeypatch.setenv("AQSP_HOLDER_COUNT_CSV", str(csv_path))
    strat = HolderConcentrationStrategy(
        StrategyConfig(name="holder_concentration", enabled=True), _thresholds_overlay()
    )

    before = strat.calculate_score({"000001.SZ": _frame("2026-04-20")})
    after = strat.calculate_score({"000001.SZ": _frame("2026-05-30")})

    assert before["000001.SZ"] == pytest.approx(0.5), (
        "披露日之前不该有分数（旧实现按季末会提前用上，属前视）"
    )
    assert after["000001.SZ"] > 0.5, "披露之后才能用上该季度（户数下降→看多）"


def test_non_monotonic_notice_dates_do_not_drop_earlier_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """披露日非单调时，不得因某一季披露特别晚而丢掉其后本可用的值。

    旧实现按季末排序后 `break` ⇒ 遇到"披露晚"的季度会**丢弃其后全部**值
    （2026-11-10 实测：中间时段命中率被错误压到 0%）。
    """
    csv_path = tmp_path / "h.csv"
    _write_holder_csv(
        csv_path,
        [
            ("000001", "2025-06-30", "2025-10-20", 1000.0, "A"),
            ("000001", "2025-09-30", "2026-01-15", 900.0, "A"),   # 披露很晚
            ("000001", "2025-12-31", "2025-12-20", 800.0, "A"),   # 披露早于上一季
        ],
    )
    monkeypatch.setenv("AQSP_HOLDER_COUNT_CSV", str(csv_path))
    strat = HolderConcentrationStrategy(
        StrategyConfig(name="holder_concentration", enabled=True), _thresholds_overlay()
    )
    scores = strat.calculate_score({"000001.SZ": _frame("2025-12-25")})
    # 2025-12-25 时点上，2025-12-31 那季（notice 2025-12-20）已披露 ⇒ 应有分数
    assert scores["000001.SZ"] != pytest.approx(0.5), (
        "非单调披露日导致本可用季度被丢弃（break 早退 bug）"
    )


# --------------------------------------------------------------------------- #
# 2) 全中性退化：overlay 静默无效（必须能被识别，而不是被读成"因子无用"）
# --------------------------------------------------------------------------- #


def test_all_neutral_keeps_original_selection_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据全缺失 ⇒ 分数恒 0.5 ⇒ 选股顺序与纯 2f **完全一致**（无副作用）。"""
    empty = tmp_path / "empty.csv"
    _write_holder_csv(empty, [])  # 没有任何 holder 数据
    monkeypatch.setenv("AQSP_HOLDER_COUNT_CSV", str(empty))

    data = {f"{i:06d}.SZ": _frame("2026-05-30", drift=0.002 * (i + 1)) for i in range(40)}

    overlay = CompositeStrategy(thresholds=_thresholds_overlay())
    plain_th = replace(
        _thresholds_overlay(),
        composite=replace(_thresholds_overlay().composite, holder_concentration_weight=0.0),
    )
    plain = CompositeStrategy(thresholds=plain_th)

    assert overlay.select_stocks(data, n=10) == plain.select_stocks(data, n=10)
    # 且必须能被**观测到**是"没启用"而不是"没效果"
    assert overlay.last_overlay_stats.get("hit_ratio", -1) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# 3) 有区分度时必须真的改变选股
# --------------------------------------------------------------------------- #


def test_overlay_changes_selection_when_factor_discriminates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """因子有区分度时，选出的 top-n 必须与纯 2f **不同**（否则 overlay 是死代码）。

    构造：2f 排名靠前的票故意给"户数大增"（看空），排名靠后的给"户数大减"（看多）
    ⇒ overlay 应把后段票选进来，与纯 2f 的 top-n 形成明显差异。
    """
    csv_path = tmp_path / "h.csv"
    rows: list[tuple[str, str, str, float, str]] = []
    for i in range(40):
        symbol = f"{i:06d}"
        prev = 1000.0
        cur = prev * (1.5 if i < 10 else (0.5 if i >= 30 else 1.0))
        rows.append((symbol, "2025-12-31", "2026-03-21", prev, "X"))
        rows.append((symbol, "2026-03-31", "2026-04-25", cur, "X"))
    _write_holder_csv(csv_path, rows)
    monkeypatch.setenv("AQSP_HOLDER_COUNT_CSV", str(csv_path))

    data = {f"{i:06d}.SZ": _frame("2026-05-30", drift=0.002 * (i + 1)) for i in range(40)}

    overlay = CompositeStrategy(thresholds=_thresholds_overlay())
    plain_th = replace(
        _thresholds_overlay(),
        composite=replace(_thresholds_overlay().composite, holder_concentration_weight=0.0),
    )
    plain = CompositeStrategy(thresholds=plain_th)

    sel_overlay = overlay.select_stocks(data, n=10)
    sel_plain = plain.select_stocks(data, n=10)
    assert sel_overlay != sel_plain, "因子有区分度却没改变选股 ⇒ overlay 是死代码"
    assert overlay.last_overlay_stats.get("changed", 0) > 0
