"""市场风险快照的边界与分级测试。

锁住三件事：
1. 下跌 + 广度差 ⇒ high；上涨 + 广度高 ⇒ low（分级方向不能反）；
2. **只做风险提示**：模块不得声称能提升收益 —— 文档里必须保留
   2026-11-12 的反证（叠加曝光后 Sharpe 反而从 +0.66 降到 +0.53）；
3. 数据不足时返回 None（不得抛异常、也不得瞎给分级）。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aqsp.market_risk import compute_market_risk  # noqa: E402


def _make_db(path: Path, n_days: int, drift: float, n_symbols: int = 400) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE daily_qfq (ts_code TEXT, trade_date TEXT, close REAL)")
    rows = []
    for s in range(n_symbols):
        px = 10.0
        for d in range(n_days):
            px *= 1 + drift + 0.002 * np.sin((d + s) / 3.0)
            rows.append((f"{600000 + s}.SH", f"2026{(d // 28) + 1:02d}{d % 28 + 1:02d}", px))
    con.executemany("INSERT INTO daily_qfq VALUES (?,?,?)", rows)
    con.commit()
    con.close()


def test_downtrend_market_is_high_risk(tmp_path: Path) -> None:
    db = tmp_path / "down.db"
    _make_db(db, 320, drift=-0.01)
    snap = compute_market_risk(str(db), "20261231")
    assert snap is not None
    assert snap.level == "high", snap
    assert snap.trend_20 < 0
    assert snap.reasons, "高风险必须给出原因"


def test_uptrend_market_is_low_risk(tmp_path: Path) -> None:
    db = tmp_path / "up.db"
    _make_db(db, 320, drift=0.01)
    snap = compute_market_risk(str(db), "20261231")
    assert snap is not None
    assert snap.level == "low", snap
    assert snap.trend_20 > 0


def test_insufficient_data_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "tiny.db"
    _make_db(db, 30, drift=0.0, n_symbols=400)
    assert compute_market_risk(str(db), "20261231") is None


def test_docstring_keeps_the_disproof() -> None:
    """防止后人把本模块当 alpha 用：文档必须留反证与用途限制。"""
    src = (REPO_ROOT / "src" / "aqsp" / "market_risk.py").read_text(encoding="utf-8")
    assert "只能" in src and "不能" in src
    assert "+0.66" in src and "+0.53" in src, "2026-11-12 的 Sharpe 反证必须保留在文档里"


def test_index_is_equal_weight_buy_and_hold_not_median_of_daily_returns(tmp_path: Path) -> None:
    """回归守卫：等权指数必须等于「买入持有」口径，不能是「中位日收益累乘」。

    历史缺陷（2026-11-12，已上线过）：写成
    `eq = (1 + wide.pct_change().median(axis=1)).cumprod()`，
    同一段窗口实测 —— 等权买入持有 `+2.30%`、中位票 `−9.07%`、
    median 累乘 **`−71.89%`**。截面中位日收益系统性低于组合增长率，
    长期复利把误差放大到几十个点，导致 UI 显示"回撤 −49.8%"（真值 −27.4%）。

    本测试直接对拍：用价格自己算等权买入持有指数，与模块输出比对。
    """
    import numpy as np
    import pandas as pd
    import sqlite3

    from aqsp.market_risk import compute_market_risk

    db = tmp_path / "mix.db"
    n_days, n_sym = 320, 400
    rng = np.random.default_rng(7)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE daily_qfq (ts_code TEXT, trade_date TEXT, close REAL)")
    prices = {}
    rows = []
    for s in range(n_sym):
        # 一半长期上涨、一半长期下跌，制造右偏收益分布（正是 median 口径失真的条件）
        drift = 0.0015 if s % 2 == 0 else -0.0010
        px = 10.0
        series = []
        for d in range(n_days):
            px *= 1 + drift + rng.normal(0, 0.02)
            series.append(px)
            rows.append((f"{600000 + s}.SH", f"2026{(d // 28) + 1:02d}{d % 28 + 1:02d}", px))
        prices[f"{600000 + s}.SH"] = series
    con.executemany("INSERT INTO daily_qfq VALUES (?,?,?)", rows)
    con.commit()
    con.close()

    snap = compute_market_risk(str(db), "20261231")
    assert snap is not None

    # 期望值：每票按首价归一再取均值（等权买入持有）
    mat = pd.DataFrame(prices)
    expected = (mat / mat.iloc[0]).mean(axis=1)
    exp_20 = float(expected.iloc[-1] / expected.iloc[-21] - 1)
    exp_dd = float(expected.iloc[-1] / expected.max() - 1)

    assert abs(snap.trend_20 - exp_20) < 1e-6, (
        f"trend_20 应为等权买入持有 {exp_20:+.4f}，实际 {snap.trend_20:+.4f}"
    )
    assert abs(snap.drawdown_252 - exp_dd) < 1e-6
    assert snap.drawdown_252 > -0.9, "median 累乘会把回撤放大到荒谬量级"
