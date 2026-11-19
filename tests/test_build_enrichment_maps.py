"""路线② 富化映射生成器（scripts/research/build_enrichment_maps.py）测试。

红线：纯计算 / 只读 / 确定性 / PIT（不取未来）/ fail-soft（短序列不写入、不崩）。
用**合成数据 + FakeSource**（不碰 2.5GB 库、CI 可复跑、不依赖真实 DB schema）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "research" / "build_enrichment_maps.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_enrichment_maps_under_test", str(_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeSource:
    """替身：fetch_daily 直接返回预置 frames（调用方已保证 PIT 切片）。"""

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self._frames = frames

    def fetch_daily(self, universe, start=None, end=None, adjust=""):
        return {c: self._frames[c] for c in universe if c in self._frames}


# ── 纯函数：年化波动 ─────────────────────────────────────────────────


def test_annual_vol_matches_manual_numpy():
    mod = _load()
    rng = np.random.default_rng(3)
    close = 10 + np.cumsum(rng.normal(0, 0.1, 120)) + 1
    expect = float(np.std(np.diff(close) / close[:-1]) * np.sqrt(252.0))
    assert mod._annual_vol(close) == pytest.approx(expect, rel=1e-9)


def test_annual_vol_short_series_is_nan():
    mod = _load()
    assert np.isnan(mod._annual_vol(np.array([5.0, 6.0])))  # <2 点
    assert np.isnan(mod._annual_vol(np.array([])))
    assert np.isnan(mod._annual_vol(None))


# ── 纯函数：日均量 ───────────────────────────────────────────────────


def test_avg_volume_ignores_nan_and_empty():
    mod = _load()
    assert mod._avg_volume(np.array([1.0, 2.0, np.nan, 3.0])) == pytest.approx(2.0)
    assert np.isnan(mod._avg_volume(np.array([np.nan])))
    assert np.isnan(mod._avg_volume(None))


# ── build_maps 端到端（FakeSource）──────────────────────────────────


def _frame(close, volume):
    return pd.DataFrame({"close": close, "volume": volume})


def test_build_maps_values_and_failsoft():
    mod = _load()
    rng = np.random.default_rng(11)
    good = _frame(
        10 + np.cumsum(rng.normal(0, 0.2, 80)),
        rng.uniform(5e6, 2e7, 80),
    )
    short = _frame([10.0, 11.0], [1000.0, 1200.0])  # <2 点波动 ⇒ 不写 vol
    frames = {"A.SZ": good, "B.SZ": short, "EMPTY.SZ": pd.DataFrame()}
    src = FakeSource(frames)
    vols, avgs = mod.build_maps(src, ["A.SZ", "B.SZ", "EMPTY.SZ"], None, None, vol_days=60)

    # 好票：vol/量都算出（vol_days=60 ⇒ 模块取 close[-60:] 窗口，期望同窗口对齐）
    assert "A.SZ" in vols and "A.SZ" in avgs
    close_win = good["close"].to_numpy()[-60:]
    expect_vol = float(np.std(np.diff(close_win) / close_win[:-1]) * np.sqrt(252.0))
    assert vols["A.SZ"] == pytest.approx(round(expect_vol, 6), abs=1e-9)
    # 短序列：波动 <2 点不写，但量（mean 可算）仍写
    assert "B.SZ" not in vols
    assert "B.SZ" in avgs
    assert avgs["B.SZ"] == pytest.approx(1100.0)
    # 空 frame：两类都不写
    assert "EMPTY.SZ" not in vols and "EMPTY.SZ" not in avgs


def test_build_maps_vol_days_window_truncation():
    """vol_days 截断：只取最近 N 根算波动（窗口语义，确定性）。"""
    mod = _load()
    rng = np.random.default_rng(7)
    close = 10 + np.cumsum(rng.normal(0, 0.3, 100))
    volume = rng.uniform(1e6, 5e6, 100)
    src = FakeSource({"X.SZ": _frame(close, volume)})
    vols, avgs = mod.build_maps(src, ["X.SZ"], None, None, vol_days=20)
    tail = close[-20:]
    expect = float(np.std(np.diff(tail) / tail[:-1]) * np.sqrt(252.0))
    assert vols["X.SZ"] == pytest.approx(round(expect, 6), abs=1e-9)


def test_build_maps_deterministic_on_rerun():
    mod = _load()
    rng = np.random.default_rng(42)
    close = 10 + np.cumsum(rng.normal(0, 0.15, 60))
    volume = rng.uniform(1e6, 3e6, 60)
    src = FakeSource({"P.SZ": _frame(close, volume), "Q.SZ": _frame(close * 0.8, volume)})
    a = mod.build_maps(src, ["P.SZ", "Q.SZ"], None, None, vol_days=30)
    b = mod.build_maps(src, ["P.SZ", "Q.SZ"], None, None, vol_days=30)
    assert a == b  # (vols, avgs) 逐位一致
