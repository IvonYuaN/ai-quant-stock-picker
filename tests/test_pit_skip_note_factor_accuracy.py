"""行为守卫：`--skip-pit-financials` 的披露文案必须与**实测**一致。

🔴 2026-10-09 订正的事实错误
---------------------------
`src/aqsp/services/walkforward_data.py` 与 `src/aqsp/cli.py` 曾各写一份文案，均称
「跳过 PIT 财务 ⇒ **quality / value / mean_reversion 三维**恒为常数」。
**第三维是错的**：`mean_reversion` 只读 `close` / `volume`（RSI 超卖、乖离率、量能确认，
见 `strategies/mean_reversion.py`），与 `pe`/`roe` 无关。它在 `enabled=False` 时才恒为
`0.0`，那是**开关语义**、不是「缺财务列」。

危害落在生产 gate 上：`stable_plus` 里 **WF-MR1 是唯一 `enable_mr=True` 且
`mr_weight=0.4` 的臂**。旧文案会让判读者认定「mr 权重无作用 ⇒ WF-MR1 退化 ⇒
有效臂只有 7 个 ⇒ `MIN_CSCV_VARIANTS=8` 被破坏 ⇒ PBO/DSR 结论不可信」——
整条推理链是假的，却正好落在 #199（DSR/PBO 双失败）的判读路径上。

本文件用**行为断言**（真跑 7 个因子）替代「文案自己说自己对」，口径与
`.workbuddy-ai/tools/probe_factor_constant_without_fundamentals.py` 一致：
在**不含任何财务列**的合成 OHLCV 上，某因子的 40 只标的分若互异值=1 且 std=0，
即「恒为常数 ⇒ 权重对选股无作用」。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aqsp.services.walkforward_data import (  # noqa: E402
    PIT_SKIP_IDLE_FACTORS,
    PIT_SKIP_PRICE_VOLUME_FACTORS,
    pit_skip_note,
)
from aqsp.strategies.base import StrategyConfig  # noqa: E402
from aqsp.strategies.candidates import HighTightFlagCandidate  # noqa: E402
from aqsp.strategies.mean_reversion import MeanReversionStrategy  # noqa: E402
from aqsp.strategies.momentum import MomentumStrategy  # noqa: E402
from aqsp.strategies.quality import QualityStrategy  # noqa: E402
from aqsp.strategies.thresholds import load_thresholds  # noqa: E402
from aqsp.strategies.triple_rise import TripleRiseStrategy  # noqa: E402
from aqsp.strategies.value import ValueStrategy  # noqa: E402
from aqsp.strategies.volume import VolumeBreakoutStrategy  # noqa: E402

N_SYMBOLS = 40
N_DAYS = 160
# 只要出现任何一个，说明 fixture 泄漏了财务数据，本文件全部断言即失效。
FUNDAMENTAL_COLS = (
    "roe",
    "roa",
    "debt_ratio",
    "operating_margin",
    "pe",
    "pb",
    "dividend_yield",
)
_CFG = StrategyConfig(name="probe")


def _synthetic_frames() -> dict[str, pd.DataFrame]:
    """价格/量各不相同、且**不含任何财务列**的 frames（固定种子 ⇒ 可复现）。"""
    rng = np.random.default_rng(20261009)
    dates = pd.bdate_range("2025-01-02", periods=N_DAYS)
    frames: dict[str, pd.DataFrame] = {}
    for i in range(N_SYMBOLS):
        rets = rng.normal(rng.uniform(-0.002, 0.002), rng.uniform(0.012, 0.045), N_DAYS)
        close = 10.0 * np.exp(np.cumsum(rets))
        frames[f"SYN{i:03d}"] = pd.DataFrame(
            {
                "date": dates,
                "open": close * (1 + rng.normal(0, 0.004, N_DAYS)),
                "high": close * (1 + np.abs(rng.normal(0, 0.008, N_DAYS))),
                "low": close * (1 - np.abs(rng.normal(0, 0.008, N_DAYS))),
                "close": close,
                "volume": np.abs(rng.normal(2.0e6, 6.0e5, N_DAYS))
                * (1 + np.abs(rets) * 20),
            }
        )
    return frames


def _signature(strategy, frames: dict[str, pd.DataFrame]) -> tuple[int, float]:
    scores = strategy.calculate_score(frames)
    vals = np.asarray(list(scores.values()), dtype=float)
    return int(len(np.unique(np.round(vals, 10)))), float(np.std(vals))


def _is_constant(strategy, frames: dict[str, pd.DataFrame]) -> bool:
    n_unique, std = _signature(strategy, frames)
    return n_unique == 1 and std == 0.0


def _active_factor_probes(thresholds) -> dict[str, object]:
    """7 个因子，**各自处于其「启用」态** —— 以区分「缺数据」与「开关关掉」。"""
    return {
        "momentum": MomentumStrategy(_CFG, thresholds=thresholds),
        "triple_rise": TripleRiseStrategy(_CFG, thresholds=thresholds),
        "quality": QualityStrategy(_CFG, thresholds=thresholds),
        "value": ValueStrategy(_CFG, thresholds=thresholds),
        "volume": VolumeBreakoutStrategy(_CFG, thresholds=thresholds),
        "high_tight_flag": HighTightFlagCandidate(_CFG, thresholds=thresholds),
        "mean_reversion": MeanReversionStrategy(
            _CFG,
            thresholds=replace(
                thresholds, mean_reversion=replace(thresholds.mean_reversion, enabled=True)
            ),
        ),
    }


# --------------------------------------------------------------------------- #
# 1) fixture 自身非空跑
# --------------------------------------------------------------------------- #
def test_fixture_has_no_fundamental_columns() -> None:
    """fixture 一旦泄漏财务列，本文件所有断言都不再证明任何事。"""
    frames = _synthetic_frames()
    cols: set[str] = set()
    for df in frames.values():
        cols |= set(df.columns)
    leaked = cols & set(FUNDAMENTAL_COLS)
    assert not leaked, f"合成 fixture 泄漏了财务列：{sorted(leaked)}"


def test_fixture_is_discriminating_not_degenerate() -> None:
    """fixture 必须让价量因子产生明显区分度，否则「常数」结论毫无信息量。"""
    frames = _synthetic_frames()
    thresholds = load_thresholds()
    probes = _active_factor_probes(thresholds)
    for name in ("momentum", "volume", "high_tight_flag"):
        n_unique, _ = _signature(probes[name], frames)
        assert n_unique >= 10, (
            f"fixture 太贫瘠：{name} 只有 {n_unique} 个互异分，"
            "无法用它证明别的因子「常数」"
        )


# --------------------------------------------------------------------------- #
# 2) 核心断言：只有依赖财务列的因子才恒为常数
# --------------------------------------------------------------------------- #
def test_only_fundamentals_dependent_factors_are_constant() -> None:
    """无财务列时，恒为常数的因子集合必须**恰好**等于 `PIT_SKIP_IDLE_FACTORS`。"""
    frames = _synthetic_frames()
    probes = _active_factor_probes(load_thresholds())
    measured = {
        name: _is_constant(probe, frames) for name, probe in probes.items()
    }
    idle = sorted(n for n, is_const in measured.items() if is_const)
    assert idle == sorted(PIT_SKIP_IDLE_FACTORS), (
        f"实测空转维度 {idle} 与声明 {sorted(PIT_SKIP_IDLE_FACTORS)} 不一致。\n"
        f"逐项实测：{measured}\n"
        "若新增/移除了依赖财务列的因子，请同步 PIT_SKIP_IDLE_FACTORS 与披露文案"
    )


def test_mean_reversion_is_not_constant_when_enabled() -> None:
    """回归守卫：`mean_reversion` 是**价量**因子，启用后必须有真实区分度。

    这正是 2026-10-09 订正的事实错误 —— 旧文案把它与 quality/value 并列成
    「缺 pe/roe 即恒为常数」，会让 `stable_plus` 的 WF-MR1（唯一 mr 臂）被误判为退化。
    """
    frames = _synthetic_frames()
    thresholds = load_thresholds()
    mr_on = replace(
        thresholds, mean_reversion=replace(thresholds.mean_reversion, enabled=True)
    )
    n_unique, std = _signature(MeanReversionStrategy(_CFG, thresholds=mr_on), frames)
    assert n_unique > 1 and std > 0, (
        f"mean_reversion 启用后仍恒为常数（互异值={n_unique}, std={std}）——"
        "若这是**有意**改成依赖财务列，则须同步更新 PIT_SKIP_IDLE_FACTORS 与文案"
    )


def test_mean_reversion_disabled_constant_is_switch_semantics() -> None:
    """对照：`enabled=False` 时恒为 0.0 —— 成因是**开关**，不是「缺 pe/roe」。

    这条把两种「常数」的成因钉开，防止后人再拿「mr 也是常数」当缺财务列的证据。
    """
    frames = _synthetic_frames()
    thresholds = load_thresholds()
    mr_off = replace(
        thresholds, mean_reversion=replace(thresholds.mean_reversion, enabled=False)
    )
    scores = MeanReversionStrategy(_CFG, thresholds=mr_off).calculate_score(frames)
    assert set(scores.values()) == {0.0}, (
        "enabled=False 的 mean_reversion 应一律返 0.0（开关语义），"
        f"实测取值集合 {sorted(set(scores.values()))[:5]}"
    )


# --------------------------------------------------------------------------- #
# 3) 文案与实测对齐
# --------------------------------------------------------------------------- #
def test_note_idle_list_matches_measured_idle_set() -> None:
    """文案里点名的空转维度必须与实测集合一致，且不得把价量因子列为空转。"""
    frames = _synthetic_frames()
    probes = _active_factor_probes(load_thresholds())
    measured_idle = {n for n, p in probes.items() if _is_constant(p, frames)}
    note = pit_skip_note()

    assert set(PIT_SKIP_IDLE_FACTORS) == measured_idle, (
        "PIT_SKIP_IDLE_FACTORS 与实测不一致，文案会撒谎"
    )
    for dim in measured_idle:
        assert dim in note, f"文案漏掉真实空转维度 {dim}"

    # 价量因子必须被显式排除，而不是被列进空转清单
    for dim in PIT_SKIP_PRICE_VOLUME_FACTORS:
        assert dim in note, f"文案未提及价量因子 {dim}（应显式说明其不受影响）"
        assert dim not in PIT_SKIP_IDLE_FACTORS, (
            f"{dim} 是价量因子，不得列入 PIT_SKIP_IDLE_FACTORS"
        )
    assert "不受影响" in note


@pytest.mark.parametrize("dim", ["quality", "value"])
def test_note_mentions_each_idle_dim_by_name(dim: str) -> None:
    """每个空转维度都要在文案里被点名（否则判读者会以为该维度在起作用）。"""
    assert dim in pit_skip_note()
