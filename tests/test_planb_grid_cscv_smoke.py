"""方案 B planb 网格的 gate CSCV 管道端到端 smoke。

契约层（test_walkforward_grid_variant_factor_enable）已钉死 planb 的
「7 维精确注入 + 8 臂真打分互异」。本文件补最后一块：证明 planb 的 N=8 臂规模
**正好喂满 gate CSCV 的 PBO 可信门槛**（``MIN_CSCV_VARIANTS=8``，
``walkforward_gate.cscv_reliable = n_variants >= 8``）——即 planb 三 profile 臂数
= 8 = 可信下限，喂 ``WalkForwardTester.calculate_cscv_pbo`` 得 ``cscv_reliability="ok"``；
而 7 臂（砍一臂的对照）会降级 ``degraded`` 并触发「n_variants < 8」警告。

为何必要：裁决单「gate 侧直接 ``--grid-profile planb_v*``」这句的置信来源——
planb 不像 stable_plus 那样有历史跑批背书，它是全新 profile，必须钉死
「它的 8 臂真的能被 CSCV 统计管道按可信下限消费」，否则触发日 gate 侧
会算出一个 cscv_reliability=degraded 的 PBO、被 fail-closed 守卫直接判不通过。

不真跑 walkforward engine（8 臂 × 3y 截面是分钟级，属 runner 跑批，不进 CI）；
本 smoke 只验证「臂数 = MIN_CSCV_VARIANTS + 喂 CSCV 管道的 N 门槛判定」这一层。
"""

from __future__ import annotations

import numpy as np

from aqsp.cli import (
    _PLANB_VARIANTS,
    _walkforward_grid_variants,
)
from aqsp.backtest.walk_forward import WalkForwardTester
from aqsp.walkforward_gate import MIN_CSCV_VARIANTS

PLANB_PROFILES = ("planb_v1", "planb_v2", "planb_v3")


def _synthetic_returns_matrix(n_variants: int, n_periods: int = 40, seed: int = 7) -> np.ndarray:
    """造一个 n_periods × n_variants 的合成收益矩阵（喂 calculate_cscv_pbo）。

    n_periods=40 ⇒ 默认 s=min(10, 40//2)=10、block_size=40//10=4（≥4，不触发块警告），
    隔离出「N 门槛」这一唯一变量。
    """
    rng = np.random.default_rng(seed)
    return rng.normal(0.001, 0.01, size=(n_periods, n_variants))


def test_planb_profiles_each_have_exactly_min_cscv_variants() -> None:
    """planb 三 profile 臂数都必须 == MIN_CSCV_VARIANTS（8），踩可信下限不多不少。

    多于 8 无害（更可信）但偏离裁决单「8 臂」预注册口径；少于 8 会触发
    cscv_reliability=degraded、gate 直接 fail-closed ⇒ 必须恰好 8。
    """
    for profile in PLANB_PROFILES:
        variants = _walkforward_grid_variants(profile)
        assert len(variants) == MIN_CSCV_VARIANTS, (
            f"[{profile}] 臂数 {len(variants)} != MIN_CSCV_VARIANTS={MIN_CSCV_VARIANTS}；"
            "少于 8 gate PBO 会 degraded，裁决单 §四 成本表按 8 臂估算"
        )
        # 与模块级表一致（防 _walkforward_grid_variants 路由回归）
        assert variants == _PLANB_VARIANTS[profile], f"[{profile}] 路由与 _PLANB_VARIANTS 不一致"
        # 8 臂 variant_id 全互异（top_n × horizon 展开，无重名）
        ids = [v.variant_id for v in variants]
        assert len(set(ids)) == len(ids), f"[{profile}] variant_id 重复: {ids}"


def test_planb_n8_feeds_cscv_pipe_reliably() -> None:
    """planb 的 N=8 喂 calculate_cscv_pbo ⇒ cscv_reliability=ok（可信下限达标）。"""
    for profile in PLANB_PROFILES:
        n = len(_walkforward_grid_variants(profile))
        assert n == MIN_CSCV_VARIANTS
        pbo, details = WalkForwardTester.calculate_cscv_pbo(
            _synthetic_returns_matrix(n), s=10
        )
        assert 0.0 <= pbo <= 1.0, f"[{profile}] PBO={pbo} 越界 [0,1]"
        assert details["n_variants"] == MIN_CSCV_VARIANTS
        assert details["cscv_reliability"] == "ok", (
            f"[{profile}] N=8 应 cscv_reliability=ok，实为 {details['cscv_reliability']}：{details['cscv_warnings']}"
        )
        # 8 不触发「< 8」N 门槛警告（degraded 的唯一可能来源是 block 警告，此处 block_size=4 已排除）
        assert not any(" < 8" in w for w in details["cscv_warnings"]), (
            f"[{profile}] N=8 不应触发 n_variants<8 警告: {details['cscv_warnings']}"
        )


def test_seven_variants_degrades_cscv_reliability() -> None:
    """对照：砍到 7 臂（< MIN_CSCV_VARIANTS）⇒ cscv_reliability=degraded + 「< 8」警告。

    这条把「8 是可信下限」钉成可执行断言：planb 任何一臂因数据不足掉期被
    ``_run_walkforward_grid_cscv`` 的 ``no usable periods`` 跳过，N 就掉到 7 ⇒ PBO 降级
    ⇒ gate fail-closed 直接不通过。提前把「8 臂必须全活」这个隐含依赖显式化。
    """
    pbo, details = WalkForwardTester.calculate_cscv_pbo(
        _synthetic_returns_matrix(MIN_CSCV_VARIANTS - 1), s=10
    )
    assert 0.0 <= pbo <= 1.0
    assert details["n_variants"] == MIN_CSCV_VARIANTS - 1
    assert details["cscv_reliability"] == "degraded", (
        f"N={MIN_CSCV_VARIANTS - 1} 应 degraded，实为 {details['cscv_reliability']}"
    )
    assert any(" < 8" in w for w in details["cscv_warnings"]), (
        f"N=7 应触发 n_variants<8 警告: {details['cscv_warnings']}"
    )
