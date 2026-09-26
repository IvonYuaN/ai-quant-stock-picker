"""ic_diagnosis（滚动窗口因子 IC 诊断）单测。

全部用 tmp sqlite 合成数据，不碰本地真实库（本地库残缺/停更，
批跑只在 runner 完整库上——裁定红线）。覆盖：
_as_of / _top_universe / _window_dates / run() 产物结构 / as-of 截断不读未来。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from scripts.ic_diagnosis import _as_of, _top_universe, _window_dates, run


def _make_db(
    path: Path,
    n_symbols: int = 60,
    n_days: int = 160,
    amount_spread: bool = True,
) -> str:
    """合成日线库：ts_code/ trade_date(YYYYMMDD)/ ohlcv + amount。
    日期 = 连续自然日（伪交易日）；amount_spread=True 时给每只票固定
    amount 档（top-N 可区分）。"""
    rng = np.random.default_rng(7)
    d0 = date(2025, 10, 1)
    dates = [(d0 + timedelta(days=i)).strftime("%Y%m%d") for i in range(n_days)]
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE daily_qfq (
            ts_code TEXT, trade_date TEXT,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, amount REAL,
            open_qfq REAL, high_qfq REAL, low_qfq REAL, close_qfq REAL)"""
    )
    rows: list[tuple] = []
    for s in range(n_symbols):
        base = 10.0 + rng.standard_normal() * 2
        amount = (s + 1) * 1e6 if amount_spread else 1e7
        for i, d in enumerate(dates):
            px = base + 0.01 * i + rng.standard_normal() * 0.2
            rows.append(
                (
                    f"{s:06d}.SZ",
                    d,
                    px,
                    px + 0.2,
                    px - 0.2,
                    px,
                    1000.0,
                    amount,
                    px,
                    px + 0.2,
                    px - 0.2,
                    px,
                )
            )
    con.executemany(
        "INSERT INTO daily_qfq VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    con.commit()
    con.close()
    return str(path)


class TestAsOf:
    def test_as_of_is_max_trade_date(self, tmp_path):
        db = _make_db(tmp_path / "p.db")
        as_of = _as_of(db)
        con = sqlite3.connect(db)
        raw = con.execute("SELECT MAX(trade_date) FROM daily_qfq").fetchone()[0]
        con.close()
        assert as_of == f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


class TestTopUniverse:
    def test_top_universe_orders_by_avg_amount_desc(self, tmp_path):
        db = _make_db(tmp_path / "p.db", n_symbols=60)
        as_of = _as_of(db)
        tops = _top_universe(db, as_of, top_n=5, min_avg_amount=1e5)
        assert len(tops) == 5
        # amount_spread=True ⇒ 第 s 只票 amount=(s+1)*1e6，top5 = 最贵的 5 只
        assert tops == ["000059", "000058", "000057", "000056", "000055"]

    def test_top_universe_min_amount_filters_all(self, tmp_path):
        db = _make_db(tmp_path / "p.db", n_symbols=60)
        as_of = _as_of(db)
        assert _top_universe(db, as_of, top_n=10, min_avg_amount=1e18) == []


class TestWindowDates:
    def test_window_dates_ascending_and_capped(self, tmp_path):
        db = _make_db(tmp_path / "p.db", n_days=200)
        as_of = _as_of(db)
        dates = _window_dates(db, as_of, window_days=90, lookback=60)
        assert dates == sorted(dates)
        assert len(dates) == 90 + 60 + 30
        assert dates[-1] == as_of  # 右端 = as_of，不读未来


class TestRun:
    def test_run_writes_artifacts_and_struct(self, tmp_path):
        db = _make_db(tmp_path / "p.db", n_symbols=80)
        out = tmp_path / "ic"
        result = run(
            db,
            window_days=60,
            lookback=60,
            horizon=3,
            step=10,
            top_n=80,
            min_avg_amount=1e5,
            out_dir=str(out),
        )
        # JSON 产物
        latest = json.loads((out / "factor_ic_latest.json").read_text())
        assert latest["as_of"] == result["as_of"]
        assert set(latest["factors"]) == {
            "momentum",
            "triple_rise",
            "composite",
        }
        for name, s in latest["factors"].items():
            assert s["n"] > 0, f"{name} 无截面（合成数据应可打分）"
            assert "recent" in s and s["recent"]["span"] == 20
        # 历史追加
        hist = (out / "ic_history.jsonl").read_text().strip().splitlines()
        assert len(hist) == 1
        assert json.loads(hist[0])["as_of"] == result["as_of"]
        # 人读报告
        report = (out / "report.md").read_text()
        assert "滚动窗口因子 IC 诊断" in report
        assert "未读未来数据" in report
        # run 返回值与 JSON 同构
        assert result["factors"] == latest["factors"]
        # 截面数落在 step 步进、窗口内（60 天窗 / step10 ⇒ ≤7 截面，且 ≥2）
        assert 2 <= result["n_sections"] <= 7

    def test_run_rejects_insufficient_history(self, tmp_path):
        db = _make_db(tmp_path / "p.db", n_symbols=80, n_days=50)
        with pytest.raises(ValueError, match="不足窗口"):
            run(db, window_days=60, lookback=60, out_dir=str(tmp_path / "ic"))

    def test_run_zero_sections_in_window_raises(self, tmp_path):
        # 库恰好够 lookback+窗口+horizon 但 step 切不出 2 个截面时，
        # 至少 run 不崩（n_sections=1）——守护 tail_start 越界静默 0 截面的边界。
        db = _make_db(tmp_path / "p.db", n_symbols=80, n_days=200)
        out = tmp_path / "ic"
        result = run(db, window_days=30, lookback=60, horizon=3, step=30,
                     out_dir=str(out), write_ready=False)
        assert result["n_sections"] >= 1

    def test_run_symbols_file_universe(self, tmp_path):
        db = _make_db(tmp_path / "p.db", n_symbols=80)
        symfile = tmp_path / "syms.txt"
        symfile.write_text("000001\n000002\n")
        out = tmp_path / "ic"
        result = run(
            db,
            window_days=60,
            lookback=60,
            horizon=3,
            step=10,
            symbols_file=str(symfile),
            out_dir=str(out),
        )
        assert "symbols-file(2)" in result["universe"]["note"]
        latest = json.loads((out / "factor_ic_latest.json").read_text())
        assert latest["universe"]["note"] == "symbols-file(2)"
