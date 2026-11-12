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
