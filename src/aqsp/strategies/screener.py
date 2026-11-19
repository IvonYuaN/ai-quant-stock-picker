"""筛选器产品化核心：把已判定的「可行筛选器」变成可交付的选股快照。

定位（11-16 拍板）：AQSP 是**选股筛选工具，不追正 alpha**。本模块把
「双反向 2f」生产打分口径（momentum 0.3 invert + price_volume_corr 0.3 invert +
min_total_score 0.70，见 config/thresholds.yaml v1.1.21）从「研究判定」落到
「可复跑的每日选股清单 + 理由 + 风险标注」。

设计约束（红线）：
- **只读、不碰生产打分**：复用现有 :class:`CompositeStrategy`（单一事实源），
  本模块只做「组织数据 → 调打分 → 解释 → 渲染」，不改任何 weights/方向。
- **PIT 安全**：所有分数来自入参 ``data``（调用方已保证 ``date <= as_of``），
  本模块**绝不**自己取未来数据；``as_of`` 只用于标注快照时点。
- **确定性**：同一 ``data`` + 同一 ``regime`` + 同一 ``thresholds`` ⇒ 输出逐位
  一致；打分里**不掺** ``now()``/时间戳（快照时点仅记录入参 ``as_of``），重跑
  可复现（供回归/验收）。
- **fail-soft**：空数据 / 打分异常 ⇒ 空清单 + 原因标注，**不崩、不造假信号**。
- **不塞下单逻辑**：只给「筛选 + 理由 + 风险」，不做仓位/止损/买卖指令
  （定位=筛选工具）。
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

import pandas as pd

from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import Thresholds

# 标注用常量（不进打分，纯展示；阈值与 thresholds.yaml 保持一致语义）
_RISK_VOL_LIMIT = 0.40          # risk.volatility_limit（年化波动上限）
_RISK_VOL_LOOKBACK = 60         # 年化波动回看窗（交易日）
_MIN_LIQUIDITY = 1_000_000      # filter.min_avg_volume（日均成交额/量下限）
_TOP_REASONS = 3               # 每只票列几条「主要贡献维度」


def build_screener_snapshot(
    data: Dict[str, pd.DataFrame],
    *,
    regime: str = "unknown",
    thresholds: Optional[Thresholds] = None,
    top_n: int = 20,
    as_of: str = "",
) -> Dict[str, Any]:
    """生成一份**确定性**选股快照（纯计算、只读、PIT 安全）。

    参数：
    - ``data``：``{symbol: DataFrame}``，调用方保证每只票 ``date <= as_of``
      （PIT 切片在调用方做，本模块不碰未来数据）。
    - ``regime``：市场状态（传给 :class:`CompositeStrategy` 做 regime 加权）；
      未知填 ``"unknown"``（走中性权重，与「动态条件化不做」一致）。
    - ``thresholds``：生产口径（默认 :func:`load_thresholds` = thresholds.yaml）。
    - ``top_n``：清单长度上限（仍受 ``min_total_score`` 门槛过滤）。
    - ``as_of``：快照时点标注（``YYYYMMDD``/``YYYY-MM-DD``），不参与计算。

    返回结构（供 CLI 落 JSON + 渲染 Markdown）::

        {
          "as_of": "...",
          "regime": "unknown",
          "thresholds_version": "1.1.21",
          "universe_count": 1234,
          "selected_count": 18,
          "snapshot_hash": "sha256:...",   # 确定性指纹（重跑可比对）
          "selected": [
            {
              "symbol": "000001.SZ",
              "total_score": 0.78,
              "rank": 1,
              "dim_scores": {"momentum": 0.6, "price_volume_corr": 0.7, ...},
              "reasons": ["momentum 贡献高(0.60, invert)", ...],
              "risk_flags": ["high_volatility"],
            },
            ...
          ],
          "notes": ["industry 中性化: 未接入(v1)", ...],
        }
    """
    data = data or {}
    th = thresholds or CompositeStrategy(None, None).thresholds
    strategy = CompositeStrategy(None, th)

    snapshot: Dict[str, Any] = {
        "as_of": str(as_of),
        "regime": regime,
        "thresholds_version": str(getattr(th, "version", "")),
        "universe_count": 0,
        "selected_count": 0,
        "selected": [],
        "notes": [],
    }

    # fail-soft：无数据或全空 ⇒ 空清单（不崩、不造假）
    usable = {s: df for s, df in data.items() if df is not None and not df.empty}
    snapshot["universe_count"] = len(usable)
    if not usable:
        snapshot["notes"].append("universe 为空（无有效日线）⇒ 空清单（fail-soft）")
        snapshot["snapshot_hash"] = _snapshot_hash(snapshot)
        return snapshot

    # ① 单一事实源打分（生产口径，regime 加权）
    try:
        detailed = strategy.calculate_detailed_scores(usable, regime=regime)
    except Exception as exc:  # fail-soft：打分异常不落半截结果
        snapshot["notes"].append(f"打分异常({exc.__class__.__name__})⇒ 空清单")
        snapshot["snapshot_hash"] = _snapshot_hash(snapshot)
        return snapshot

    min_score = float(th.composite.min_total_score)
    # ② 门槛过滤（total >= min_total_score）+ 降序排名 + 截 top_n
    ranked = sorted(
        (s for s, d in detailed.items() if d.get("total", 0.0) >= min_score),
        key=lambda s: (-float(detailed[s]["total"]), s),  # 同分按 symbol 稳定
    )
    picked = ranked[: max(0, int(top_n))]

    # ③ 逐只票解释（理由 + 风险标注）
    for rank, symbol in enumerate(picked, start=1):
        dims = detailed[symbol]
        entry: Dict[str, Any] = {
            "symbol": symbol,
            "rank": rank,
            "total_score": round(float(dims.get("total", 0.0)), 4),
            "dim_scores": {k: round(float(v), 4) for k, v in dims.items() if k != "total"},
            "reasons": _top_reasons(dims, th),
            "risk_flags": _risk_flags(usable[symbol], dims),
        }
        snapshot["selected"].append(entry)

    snapshot["selected_count"] = len(picked)
    snapshot["notes"].append(
        f"门槛 total≥{min_score}；universe {len(usable)} → 入选 {len(picked)}"
    )
    # v1 未接入的增强（如实标注，不造假）
    snapshot["notes"].append("industry 中性化: 未接入(v1)")
    snapshot["notes"].append("定位=筛选工具：不含仓位/买卖指令（不追 alpha）")
    snapshot["snapshot_hash"] = _snapshot_hash(snapshot)
    return snapshot


def _top_reasons(
    dims: Dict[str, float], th: Thresholds
) -> List[str]:
    """按「对总分的偏离贡献」列前 3 条主因（只解释、不下指令）。

    贡献 = |维度分 − 0.5| × 该维度权重（权重取 thresholds.composite 配置值，
    与 :class:`CompositeStrategy` 的累加口径一致；invert 维度在文字里标注）。
    """
    base = th.composite
    inv = {
        "momentum": bool(base.momentum_weight > 0 and _mom_invert(th)),
        "price_volume_corr": bool(
            base.price_volume_corr_weight > 0 and th.price_volume_corr.invert_signal
        ),
    }
    contribs: List[tuple[str, float, bool]] = []
    for key, val in dims.items():
        if key == "total":
            continue
        weight = _dim_weight(base, key)
        if weight <= 0:
            continue
        contribs.append((key, abs(float(val) - 0.5) * weight, inv.get(key, False)))
    contribs.sort(key=lambda t: (-t[1], t[0]))
    out: List[str] = []
    for key, c, is_invert in contribs[:_TOP_REASONS]:
        tag = "，反向维" if is_invert else ""
        out.append(f"{key} 分位 {float(dims.get(key, 0.5)):.2f}（权重 {c:.2f}{tag}）")
    return out or ["各维度均近中性"]


def _dim_weight(base: Any, key: str) -> float:
    """维度权重（与 CompositeStrategy._dimension_specs 的累加权重同口径）。"""
    mapping = {
        "momentum": base.momentum_weight,
        "quality": base.quality_weight,
        "value": base.value_weight,
        "volume": base.volume_weight,
        "mean_reversion": base.mean_reversion_weight,
        "triple_rise": base.triple_rise_weight,
        "high_tight_flag": base.high_tight_flag_weight,
        "price_volume_corr": base.price_volume_corr_weight,
        "volume_surge": base.volume_surge_weight,
        "volatility_position": base.volatility_position_weight,
        "macd_hist": base.macd_hist_weight,
        "lower_shadow_ratio": base.lower_shadow_ratio_weight,
    }
    return float(mapping.get(key, 0.0) or 0.0)


def _mom_invert(th: Thresholds) -> bool:
    return bool(getattr(th.momentum, "invert_signal", False))


def _risk_flags(df: pd.DataFrame, dims: Dict[str, float]) -> List[str]:
    """风险标注（只标注、不下指令）：高波动 / 低流动性 / 高分位。"""
    flags: List[str] = []
    try:
        close = pd.to_numeric(df.get("close"), errors="coerce").dropna()
        if len(close) >= _RISK_VOL_LOOKBACK:
            ret = close.pct_change().dropna().tail(_RISK_VOL_LOOKBACK)
            ann_vol = float(ret.std() * (252 ** 0.5)) if len(ret) > 2 else 0.0
            if ann_vol > _RISK_VOL_LIMIT:
                flags.append("high_volatility")
        vol_col = pd.to_numeric(df.get("volume"), errors="coerce").dropna()
        if len(vol_col) and float(vol_col.mean()) < _MIN_LIQUIDITY:
            flags.append("low_liquidity")
    except Exception:
        pass  # 风险标注失败不影响清单（fail-soft）
    if float(dims.get("total", 0.0)) >= 0.9:
        flags.append("top_decile")
    return flags


def _snapshot_hash(snapshot: Dict[str, Any]) -> str:
    """确定性指纹：对「影响判定的字段」做 sha256（重跑同一 as_of 须逐位一致）。

    含：as_of / regime / thresholds_version / universe_count / selected 全量。
    不含 notes（解释性文字可演进）与 selected_count（可由 selected 推得）。
    """
    canonical = {
        "as_of": snapshot.get("as_of"),
        "regime": snapshot.get("regime"),
        "version": snapshot.get("thresholds_version"),
        "universe_count": snapshot.get("universe_count"),
        "selected": [
            {k: e[k] for k in ("symbol", "rank", "total_score", "dim_scores", "risk_flags")}
            for e in snapshot.get("selected", [])
        ],
    }
    return "sha256:" + hashlib.sha256(_stable_repr(canonical).encode("utf-8")).hexdigest()


def _stable_repr(x: Any) -> str:
    """稳定可序列化表示（dict 按 key 排序），用于确定性 hash。"""
    import json as _json

    return _json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)


def render_markdown(snapshot: Dict[str, Any], title: str = "每日选股筛选清单") -> str:
    """把 :func:`build_screener_snapshot` 结果渲染成 Markdown 表（只读、无指令）。"""
    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(
        f"- **as_of**: {snapshot.get('as_of', '')}  |  **regime**: {snapshot.get('regime', '')}"
        f"  |  **thresholds**: v{snapshot.get('thresholds_version', '')}"
    )
    lines.append(f"- **universe**: {snapshot.get('universe_count', 0)} 只 → **入选**: "
                 f"{snapshot.get('selected_count', 0)} 只")
    sel = snapshot.get("selected", [])
    if not sel:
        lines.append("")
        lines.append("> 无入选标的。")
        for note in snapshot.get("notes", []):
            lines.append(f"> {note}")
        return "\n".join(lines)
    lines.append("")
    lines.append("| # | 代码 | 总分 | 主要维度分 | 风险标注 | 理由 |")
    lines.append("|---|------|------|-----------|---------|------|")
    for e in sel:
        dims = e.get("dim_scores", {})
        dim_txt = " ".join(f"{k} {v:.2f}" for k, v in dims.items())
        flags = ", ".join(e.get("risk_flags", [])) or "-"
        reasons = "; ".join(e.get("reasons", [])) or "-"
        lines.append(
            f"| {e.get('rank')} | {e.get('symbol')} | {e.get('total_score')} | "
            f"{dim_txt} | {flags} | {reasons} |"
        )
    lines.append("")
    lines.append("**说明**：本清单为筛选工具输出，不含仓位/买卖指令；"
                 "「主要维度分」= 各维度截面分位（反向维度已按 invert 口径）。")
    for note in snapshot.get("notes", []):
        lines.append(f"- {note}")
    return "\n".join(lines)


