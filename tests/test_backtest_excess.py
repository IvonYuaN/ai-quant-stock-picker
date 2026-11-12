"""`aqsp.backtest.excess` 的口径守卫。

锁住两件会直接毁掉判读的事：
1. 基准必须是**等权买入持有**（一涨一倍、一跌一半 ⇒ 净值 1.25），
   不是「中位日收益累乘」（同样数据会算出完全不同的量级）；
2. 不同 run 窗口的臂必须**分组**，不得混排成一张表。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from aqsp.backtest.excess import (
    equal_weight_curve,
    excess_for_arm,
    load_arm_periods,
    render_markdown,
)


def _db_with_two_paths(tmp_path: Path) -> Path:
    """两只票：一只翻倍、一只腰斩 ⇒ 等权买入持有净值应为 1.25。"""
    db = tmp_path / "two.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE daily_qfq (ts_code TEXT, trade_date TEXT, close REAL)")
    rows = []
    for d in range(300):
        day = f"2026{(d // 28) + 1:02d}{d % 28 + 1:02d}"
        rows.append(("600000.SH", day, 10.0 * (1 + d / 299)))       # 10 → 20
        rows.append(("600001.SH", day, 10.0 * (1 - 0.5 * d / 299)))  # 10 → 5
    con.executemany("INSERT INTO daily_qfq VALUES (?,?,?)", rows)
    con.commit()
    con.close()
    return db


def test_equal_weight_curve_is_buy_and_hold(tmp_path: Path) -> None:
    curve = equal_weight_curve(str(_db_with_two_paths(tmp_path)), sample=100)
    assert not curve.empty
    assert curve.iloc[0] == pytest.approx(1.0, abs=1e-9)
    # 等权买入持有 = (2.0 + 0.5) / 2
    assert curve.iloc[-1] == pytest.approx(1.25, abs=1e-6), (
        "必须是买入持有口径；median 日收益累乘会给出完全不同的值"
    )


def test_load_arm_periods_groups_by_run(tmp_path: Path) -> None:
    d = tmp_path / "arms"
    d.mkdir()
    for run, arm in (("2023-11-07_2026-11-06", "WO-A"), ("2023-09-05_2026-09-18", "WF-A")):
        f = d / f"walkforward_gate_resume_{run}.jsonl.{arm}"
        f.write_text(
            json.dumps(
                {"window": "2024-01-02..2024-02-02", "result": {"total_return": 0.05}}
            )
            + "\n",
            encoding="utf-8",
        )
    arms = load_arm_periods(d)
    assert set(arms) == {"2023-11-07_2026-11-06", "2023-09-05_2026-09-18"}
    md = render_markdown(arms, equal_weight_curve(str(_db_with_two_paths(tmp_path)), sample=100))
    assert "分别出表" in md, "多个 run 必须提示不可比"


def test_excess_for_arm_uses_paired_periods() -> None:
    idx = np.array(["20240101", "20240201", "20240301", "20240401"])
    curve = __import__("pandas").Series([1.0, 1.10, 1.20, 1.30], index=idx)
    rows = [
        {"start": "2024-01-02", "end": "2024-02-01", "ret": 0.30},
        {"start": "2024-02-02", "end": "2024-03-01", "ret": 0.05},
        {"start": "2024-03-02", "end": "2024-04-01", "ret": 0.10},
    ]
    st = excess_for_arm(rows, curve)
    assert st is not None
    assert st.periods == 3
    assert st.excess_mean > 0
    assert st.beat_periods >= 2
