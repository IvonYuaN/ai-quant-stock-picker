"""ic_dual_verdict（每日双窗因子 IC 滚动判决）单测。

全部用 tmp sqlite 合成数据，不碰本地真实库（本地库残缺/停更，批跑只在
runner 完整库上——裁定红线）。覆盖：
  - _window_a_as_of：A 窗右端 = MAX 前 window_days 个交易日（相邻不重叠）
  - verdict_for_factor：同号 且 双|t|≥2 且 两窗 n≥MIN_SECTIONS ⇒ hit
  - factor_streak：按 as_of_b 去重计数（静态期保护）、未达标即截断、fail-soft
  - run_dual_verdict：红线守卫（step<horizon / 超 3 年）、产物结构、
    ledger 追加 + 连续 N 日达标事件、scratch 不污染生产单窗产物
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from scripts.analysis.ic_dual_verdict import (
    DEFAULT_STREAK_N,
    _read_ledger,
    _window_a_as_of,
    factor_streak,
    run_dual_verdict,
    verdict_for_factor,
)


def _make_db(
    path: Path,
    n_symbols: int = 80,
    n_days: int = 1160,
    amount_spread: bool = True,
) -> str:
    """合成日线库（与 test_ic_diagnosis 同构；日期=连续自然日伪交易日）。"""
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
    con.executemany("INSERT INTO daily_qfq VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return str(path)


class TestWindowAAsOf:
    def test_window_a_is_window_days_before_max(self, tmp_path):
        db = _make_db(tmp_path / "p.db", n_days=1160)
        as_of_b = _as_of_check(db)
        as_of_a = _window_a_as_of(db, as_of_b, 365)
        con = sqlite3.connect(db)
        dates = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT trade_date FROM daily_qfq WHERE trade_date <= ? "
                "ORDER BY trade_date DESC LIMIT 366",
                (as_of_b.replace("-", ""),),
            )
        ]
        con.close()
        # 最旧的那 1 天（index -1）= A 窗右端；B 窗 = 其后 365 天（不重叠）
        assert as_of_a == dates[-1][:4] + "-" + dates[-1][4:6] + "-" + dates[-1][6:]
        assert as_of_a < as_of_b

    def test_insufficient_history_raises(self, tmp_path):
        db = _make_db(tmp_path / "p.db", n_days=10)
        as_of_b = _as_of_check(db)
        try:
            _window_a_as_of(db, as_of_b, 365)
            assert False, "应拒：库深度不足"
        except ValueError:
            pass


def _as_of_check(db: str) -> str:
    con = sqlite3.connect(db)
    raw = con.execute("SELECT MAX(trade_date) FROM daily_qfq").fetchone()[0]
    con.close()
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def _stats_like(n: int, mean: float, t: float) -> dict:
    return {"mean": mean, "std": 1.0, "icir": mean, "t": t, "pos_rate": 0.5, "n": n}


class TestVerdictForFactor:
    def test_hit_requires_same_sign_and_both_abs_t_ge_2(self):
        v = verdict_for_factor(
            _stats_like(73, -0.04, -2.5), _stats_like(73, -0.04, -2.1)
        )
        assert v["hit"] is True
        assert v["sign_match"] is True
        assert v["dual_significant"] is True

    def test_one_window_not_significant_is_no_hit(self):
        v = verdict_for_factor(
            _stats_like(73, -0.02, -1.0), _stats_like(73, -0.04, -2.1)
        )
        assert v["hit"] is False  # A 窗 |t|<2
        assert v["sign_match"] is True  # 方向一致但功效不足（09-28 判决同型）

    def test_opp_sign_is_no_hit_even_both_significant(self):
        v = verdict_for_factor(
            _stats_like(73, 0.02, 2.4), _stats_like(73, -0.02, -2.4)
        )
        assert v["hit"] is False
        assert v["sign_match"] is False

    def test_min_sections_guard(self):
        v = verdict_for_factor(
            _stats_like(1, 0.02, 3.0), _stats_like(73, 0.02, 2.4)
        )
        assert v["hit"] is False  # A 窗 n<MIN_SECTIONS（_stats 全 NaN 同型）

    def test_nan_stats_is_no_hit(self):
        nan = float("nan")
        v = verdict_for_factor(
            _stats_like(73, nan, nan), _stats_like(73, 0.02, 2.4)
        )
        assert v["hit"] is False
        assert v["sign_match"] is False


class TestVerdictForFactorNumpyTypes:
    """numpy 型 stats 回归（首跑实证 2026-09-30 暴露的真缺陷）。

    上游 ``ic_diagnosis.run()`` 的 per-factor stats 是 numpy 标量：``t`` 在
    std>0 时为 np.float64，比较式 ``na>=MIN`` 产 np.bool_。``np.bool_`` 不是
    Python ``bool`` 子类，直接进 ``json.dumps`` 会
    ``TypeError: Object of type bool is not JSON serializable``。合成单测用
    Python 型 stats 故未暴露——这里以 numpy 型输入 + 端到端 json.dumps 兜住。
    """

    def test_numpy_stats_produce_native_types_and_serialize(self):
        # B 窗某因子 |t|<2 ⇒ dual_significant 走「短路为 np.bool_(False)」分支
        a = {"mean": np.float64(-0.04), "t": np.float64(-2.5), "n": np.int64(73)}
        b = {"mean": np.float64(-0.01), "t": np.float64(-0.8), "n": np.int64(73)}
        v = verdict_for_factor(a, b)
        # 类型必须落到**内置** native（np.bool_ 不是 bool 子类，type(...) is bool 才
        # 能在所有 numpy 版本上区分 np.bool_ 与 built-in bool——isinstance 在旧版
        # numpy（np.bool_ 曾是 bool 子类）上会假绿，故用 type 同一性断言）。
        assert type(v["sign_match"]) is bool
        assert type(v["dual_significant"]) is bool
        assert type(v["hit"]) is bool
        assert type(v["mean_a"]) is float
        assert type(v["t_a"]) is float
        assert type(v["n_a"]) is int
        # 端到端可序列化（首跑正是栽在这一步）
        json.dumps({"factors": {"momentum": v}}, ensure_ascii=False)
        # 语义不变：A 窗 |t|≥2 但 B 窗 |t|<2 ⇒ 方向同号但未双显著 ⇒ no hit
        assert v["sign_match"] is True
        assert v["dual_significant"] is False
        assert v["hit"] is False

    def test_hit_with_numpy_stats_serializes_true(self):
        a = {"mean": np.float64(0.03), "t": np.float64(2.4), "n": np.int64(73)}
        b = {"mean": np.float64(0.035), "t": np.float64(2.6), "n": np.int64(73)}
        v = verdict_for_factor(a, b)
        assert v["hit"] is True
        assert type(v["dual_significant"]) is bool  # np.bool_(True) 也须 native 化
        json.dumps(v, ensure_ascii=False)

    def test_nan_numpy_mean_no_hit_and_serializes(self):
        nan = np.float64("nan")
        a = {"mean": nan, "t": nan, "n": np.int64(73)}
        b = {"mean": np.float64(0.02), "t": np.float64(2.4), "n": np.int64(73)}
        v = verdict_for_factor(a, b)
        assert v["sign_match"] is False
        assert v["hit"] is False
        json.dumps(v, ensure_ascii=False)


class TestFactorStreak:
    def test_streak_counts_from_latest_day_and_breaks_on_miss(self):
        rows = [
            {"as_of_b": "d1", "run_at": "t1", "factors": {"htf": {"hit": False}}},
            {"as_of_b": "d2", "run_at": "t2", "factors": {"htf": {"hit": True}}},
            {"as_of_b": "d3", "run_at": "t3", "factors": {"htf": {"hit": True}}},
            {"as_of_b": "d4", "run_at": "t4", "factors": {"htf": {"hit": False}}},
        ]
        assert factor_streak(rows, "htf") == 0  # 最新一日未达标

    def test_streak_with_hit_tail(self):
        rows = [
            {"as_of_b": "d1", "run_at": "t1", "factors": {"htf": {"hit": True}}},
            {"as_of_b": "d2", "run_at": "t2", "factors": {"htf": {"hit": True}}},
            {"as_of_b": "d3", "run_at": "t3", "factors": {"htf": {"hit": True}}},
        ]
        assert factor_streak(rows, "htf") == 3

    def test_static_period_dedup_same_as_of_counts_once(self):
        # 数据面停更：同 as_of_b 三次跑批只算 1 个数据日
        rows = [
            {"as_of_b": "d1", "run_at": "t1", "factors": {"htf": {"hit": True}}},
            {"as_of_b": "d1", "run_at": "t2", "factors": {"htf": {"hit": True}}},
            {"as_of_b": "d1", "run_at": "t3", "factors": {"htf": {"hit": True}}},
        ]
        assert factor_streak(rows, "htf") == 1

    def test_unknown_factor_is_zero(self):
        rows = [{"as_of_b": "d1", "run_at": "t1", "factors": {"mr": {"hit": True}}}]
        assert factor_streak(rows, "htf") == 0

    def test_empty_ledger_is_zero(self):
        assert factor_streak([], "htf") == 0


class TestReadLedger:
    def test_missing_file_is_empty(self, tmp_path):
        assert _read_ledger(tmp_path / "nope.jsonl") == []

    def test_corrupt_lines_are_skipped(self, tmp_path):
        p = tmp_path / "l.jsonl"
        p.write_text(
            '{"as_of_b": "d1", "run_at": "t1", "factors": {"htf": {"hit": true}}}\n'
            "not json\n"
            '{"as_of_b": "d2", "run_at": "t2", "factors": {"htf": {"hit": true}}}\n',
            encoding="utf-8",
        )
        rows = _read_ledger(p)
        assert [r["as_of_b"] for r in rows] == ["d1", "d2"]


class TestRunDualVerdict:
    def _small_db(self, tmp_path: Path) -> str:
        return _make_db(tmp_path / "p.db", n_symbols=80, n_days=1160)

    def test_redline_step_lt_horizon_rejected(self, tmp_path):
        db = self._small_db(tmp_path)
        try:
            run_dual_verdict(
                db,
                out_dir=str(tmp_path / "out"),
                window_days=40,
                step=2,
                horizon=3,
            )
            assert False, "应拒：step<horizon（截面重叠虚高 t）"
        except ValueError:
            pass

    def test_redline_dual_window_over_3y_rejected(self, tmp_path):
        db = self._small_db(tmp_path)
        try:
            run_dual_verdict(
                db,
                out_dir=str(tmp_path / "out"),
                window_days=400,  # 2×400=800 > 730
                step=5,
            )
            assert False, "应拒：双窗总深超 3 年红线"
        except ValueError:
            pass

    def test_run_writes_ledger_latest_report_and_no_pollution(self, tmp_path):
        db = self._small_db(tmp_path)
        out = tmp_path / "out"
        result = run_dual_verdict(
            db,
            out_dir=str(out),
            window_days=40,
            step=5,
            extra_factors=["mr"],
        )
        # 产物齐全
        assert (out / "dual_window_history.jsonl").exists()
        assert (out / "dual_window_latest.json").exists()
        assert (out / "dual_report.md").exists()
        assert (out / "IC_READY_DUAL").exists()
        # ledger 首行结构
        row = json.loads((out / "dual_window_history.jsonl").read_text().splitlines()[0])
        assert row["as_of_a"] < row["as_of_b"]
        assert row["n_sections_a"] >= 2 and row["n_sections_b"] >= 2
        assert set(row["factors"]) >= {"momentum", "triple_rise", "composite", "mr"}
        assert row["hits"] == [n for n, v in row["factors"].items() if v["hit"]]
        # latest.json 与 ledger 行同构 + 判决字段
        latest = json.loads((out / "dual_window_latest.json").read_text())
        assert latest["streak_n"] == DEFAULT_STREAK_N
        assert latest["event"] in (None, "revisit_family")
        # B 窗 73 截面口径互验：同 run() ⇒ 与单窗「最近 40 日」子集零漂移（构造保证）
        assert result["as_of_b"] == latest["as_of_b"]
        # 报告红线声明
        report = (out / "dual_report.md").read_text()
        assert "不写回打分/排序/下单" in report
        # scratch 隔离：生产单窗 ic_history.jsonl 未被追加
        assert not (out / "ic_history.jsonl").exists()

    def test_streak_event_fires_after_n_days(self, tmp_path):
        db = self._small_db(tmp_path)
        out = tmp_path / "out"
        # 同 as_of_b 连续 5 次跑批（静态期保护：去重后只算 1 个数据日）
        for i in range(DEFAULT_STREAK_N):
            run_dual_verdict(
                db,
                out_dir=str(out),
                window_days=40,
                step=5,
                write_ready=False,
            )
        ledger = _read_ledger(out / "dual_window_history.jsonl")
        assert len(ledger) == DEFAULT_STREAK_N  # 每次跑批都追加一行
        dedup_days = {r["as_of_b"] for r in ledger}
        assert len(dedup_days) == 1  # 数据面静态 ⇒ 只有 1 个数据日
        # 未达标因子的 streak ≤1；事件触发须 N 个不同数据日（此处不会假触发）
        latest = json.loads((out / "dual_window_latest.json").read_text())
        for n, s in latest["streaks"].items():
            assert s <= 1, f"{n} 静态期 streak 应 ≤1，实为 {s}"
        assert latest["event"] in (None, "revisit_family")
