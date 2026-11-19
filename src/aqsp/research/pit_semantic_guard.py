"""前视语义守卫（行为级金标准，根治「语法级守卫对语义前视全盲」）。

背景
----
既有前视守卫是**语法级**（只查 ``shift(-N)`` / ``rolling(center=True)``），对
**语义前视全盲**——典型事故 §24：行业内 PB 的 ``_within_rank``/``_group_mean``
把 ``(T×N)`` 面板 **ravel 后只按行业码 ``bincount``/``lexsort``、漏了日期维** ⇒
全期池化、**含未来**。语法扫描查不出（没有任何 ``shift``/``center`` 字样），
却在生产口径里虚高 23% IC、把「行业内相对 PB」错判成可用。

本模块给出**行为级**判据（金标准）：
    一个截面变换 ``transform(values, aux) -> out`` 在时点 ``t`` 是 PIT 安全的，
    当且仅当 **out[t] 只依赖 values[0..t]**（及 aux[0..t]）。

判定方法（截断一致性 / truncation probe）
------------------------------------------
对每个探测时点 ``t``：
- 全量算 ``out_full = transform(values, aux)``；
- 屏蔽未来：``values[:t+1]`` 不变、``values[t+1:]=NaN``（aux 同理），再算
  ``out_trunc = transform(values_trunc, aux_trunc)``；
- 若 ``out_full[:t+1] == out_trunc[:t+1]``（含 NaN 对齐）⇒ 该时点没用未来；
  只要有**任一**时点不等 ⇒ 该变换**用了未来数据（前视）**。

为何能抓住 §24：全期池化分位把整段当一个 pool，屏蔽未来后 pool 变小 ⇒ 之前
时点的分位值**改变** ⇒ 被抓。逐日横截面（按 date groupby）屏蔽未来不影响
当前及之前各日 ⇒ 通过。它**不依赖任何语法规则**，是「用了未来就变」的充要判据。

前置假设
--------
- ``transform`` 必须是**纯函数**（同输入 ⇒ 同输出、无状态、无缓存副作用、
  不读全局/不联网），否则截断探测无意义（调用方须保证）。
- 面板形状 ``(T, N)``，``T`` 轴是时间升序、``N`` 轴是标的。``aux`` 形状须同
  ``(T, N)``（如行业编码面板），可省略。

定位
----
这是**研究可信度基建**：不产 alpha、不改生产打分，只让**任何**截面变换在进
生产/gate 前能被确定性判定「是否前视」，护住整个研究的口径纪律。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

# 一个截面变换：输入值面板 (values, aux)（可选），输出同形状面板。
Transform = Callable[[np.ndarray, np.ndarray], np.ndarray]


@dataclass
class PitAuditReport:
    """前视审计结果。``pit_safe`` 为 False ⇒ 该变换用了未来数据（前视）。"""

    pit_safe: bool
    n_probe: int = 0
    violated_at: list[int] = field(default_factory=list)      # 出了问题的时点索引
    detail: str = ""

    def summary(self) -> str:
        if self.pit_safe:
            return (
                f"PIT 安全：{self.n_probe} 个探测时点截断后 0..t 输出逐位不变"
            )
        worst = self.violated_at[:8]
        return (
            f"前视：{len(self.violated_at)}/{self.n_probe} 个探测时点截断后输出改变，"
            f"最早出问题的时点 = {worst}"
        )


def _mask_future(arr: np.ndarray, up_to: int) -> np.ndarray:
    """把 ``up_to`` 之后（不含）的**所有未来行**置 NaN，保留 0..up_to。"""
    out = np.array(arr, dtype=float, copy=True)
    if up_to + 1 < out.shape[0]:
        out[out.shape[0] - 1 if up_to < 0 else up_to + 1 :, :] = np.nan
    return out


def check_pit_safe(
    transform: Transform,
    values: np.ndarray,
    aux: Optional[np.ndarray] = None,
    probe_dates: Optional[list[int]] = None,
) -> PitAuditReport:
    """行为级前视判定（截断一致性金标准）。

    参数：
    - ``transform``：截面变换 ``(values, aux) -> out``，**必须纯函数**。
    - ``values``：``(T, N)`` 浮点面板（时间升序）。
    - ``aux``：同形 ``(T, N)`` 面板（如行业编码），可为 None（内部补 0）。
    - ``probe_dates``：探测时点索引（升序）；缺省采样若干（覆盖前/中/后）。

    返回 :class:`PitAuditReport`；``pit_safe=False`` 且 ``violated_at`` 非空
    即证明该变换**用了未来数据**（如 §24 的全期池化 groupby）。
    """
    values = np.asarray(values, dtype=float)
    if values.ndim != 2:
        raise ValueError(f"values 须为 (T,N) 二维面板，got {values.shape}")
    T, N = values.shape
    if aux is None:
        aux = np.zeros((T, N), dtype=float)
    aux = np.asarray(aux, dtype=float)
    if aux.shape != values.shape:
        raise ValueError(f"aux 形状 {aux.shape} ≠ values 形状 {values.shape}")

    if probe_dates is None:
        # 缺省采样：前 1/4、中点、后 1/4 等 5 个点 + 倒数第二点（边界）
        idx = {max(0, T // 4), max(0, T // 2), max(0, 3 * T // 4), min(T - 1, 3 * T // 4 + 1), T - 2}
        probe_dates = sorted(i for i in idx if 0 <= i < T)
    probe_dates = sorted(set(int(i) for i in probe_dates if 0 <= int(i) < T))

    out_full = np.asarray(transform(values, aux), dtype=float)
    if out_full.shape != (T, N):
        raise ValueError(f"transform 输出形状 {out_full.shape} ≠ {(T, N)}")

    violated: list[int] = []
    for t in probe_dates:
        v_trunc = _mask_future(values, t)
        a_trunc = _mask_future(aux, t)
        out_trunc = np.asarray(transform(v_trunc, a_trunc), dtype=float)
        # 比较 0..t（含当前时点）逐位；NaN 对齐（两者都 NaN 视为一致）
        seg_full = out_full[: t + 1]
        seg_trunc = out_trunc[: t + 1]
        same = np.isclose(seg_full, seg_trunc, rtol=0.0, atol=0.0, equal_nan=True)
        if not bool(np.all(same)):
            violated.append(t)

    pit_safe = len(violated) == 0
    report = PitAuditReport(pit_safe=pit_safe, n_probe=len(probe_dates), violated_at=violated)
    report.detail = report.summary()
    return report


# ------------------------------------------------------------------ 复刻 §24 的两种形态
def _make_panel(T: int, N: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """造一个随机值面板 + 行业编码面板（§24 反例/正例的公共输入）。"""
    rng = np.random.default_rng(seed)
    values = rng.uniform(0.5, 5.0, size=(T, N))
    aux = rng.integers(0, 5, size=(T, N)).astype(float)  # 行业码 0..4
    return values, aux


def pooled_within_rank(values: np.ndarray, aux: np.ndarray) -> np.ndarray:
    """复刻 §24 的 ``_within_rank`` 病根：**全期池化**行业内分位（漏日期维）。

    把 (T×N) ravel 后只按行业码 pool、行业内排名 ⇒ 每个时点的分位取决于
    **全期**该行业所有票 ⇒ **含未来**（前视）。此函数应当被 :func:`check_pit_safe`
    判为前视（``pit_safe=False``）。

    截断探测会喂含未来被置 NaN 的输入，故对 NaN 健壮（被屏蔽行不入 pool）；
    这**不改变前视本质**——它仍把整段当 pool，不逐日切。
    """
    T, N = values.shape
    ind = np.asarray(aux, dtype=float).ravel()
    val = values.ravel()
    valid = np.isfinite(val) & np.isfinite(ind)
    # NaN-safe 行业编码（-1 标记无效，不入 pool）
    ind_int = np.nan_to_num(ind, nan=-1.0).astype(int)
    if valid.any():
        order = np.lexsort((val[valid], ind_int[valid]))
        sorted_ind = ind_int[valid][order]
        sorted_val = val[valid][order]
        ranks = np.empty(len(sorted_val), dtype=float)
        start = 0
        for i in range(1, len(sorted_ind) + 1):
            if i == len(sorted_ind) or sorted_ind[i] != sorted_ind[start]:
                ranks[start:i] = np.arange(1, i - start + 1)
                start = i
        max_ind = int(sorted_ind.max()) if len(sorted_ind) else 0
        cnts = np.bincount(ind_int[valid], minlength=max(max_ind + 1, 1))
        pct = ranks / np.maximum(cnts[ind_int[valid]], 1)
    else:
        pct = np.array([], dtype=float)
    out = np.full(T * N, np.nan)
    out[np.flatnonzero(valid)] = pct
    return out.reshape(T, N)


def daily_within_rank(values: np.ndarray, aux: np.ndarray) -> np.ndarray:
    """逐日横截面行业内分位（**生产可实现、无前视**的正例）。

    每个截面日**独立**按 (date, industry) 组内排名 ⇒ 每个时点只用当天，
    屏蔽未来不影响 0..t ⇒ :func:`check_pit_safe` 应判为 PIT 安全。
    """
    T, N = values.shape
    out = np.full((T, N), np.nan)
    for t in range(T):
        row_val = values[t]
        row_ind = np.nan_to_num(np.asarray(aux[t], dtype=float), nan=-1.0)
        mask = np.isfinite(row_val) & (row_ind >= 0)
        if not mask.any():
            continue
        for grp in np.unique(row_ind[mask]):
            gpos = mask & (row_ind == grp)
            gvals = row_val[gpos]
            # 组内 rank(pct)：升序，越高越靠后
            order = np.argsort(gvals, kind="stable")
            rank_in = np.empty_like(order, dtype=float)
            rank_in[order] = np.arange(1, len(order) + 1)
            out[t, np.flatnonzero(gpos)] = rank_in / len(order)
    return out
