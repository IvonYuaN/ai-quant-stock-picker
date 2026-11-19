"""组合风险叠加层（路线②，纯计算骨架）。

定位（与路线①筛选器配套）：
- 路线① :mod:`aqsp.strategies.screener` 产出「可行筛选器」清单（双反向 2f，
  定位=筛选工具，**不追 alpha、不塞下单逻辑**）。
- 本模块接在清单**之后**，只回答「这批票若要拿，怎么切」——给组合权重 +
  风险暴露指标，目标是**降尾部风险**，不新增任何 alpha 信号、不改打分方向。

设计红线（与 screener 同族）：
- **纯函数、只读、无网络**：输入一批 :class:`HoldingInput`（symbol + total_score
  + 市值 + 年化波动 + 换手 + 行业），输出权重/暴露。不碰 :class:`PickResult`、
  不牵动生产下单链（``optimizer``/``executor``）。
- **PIT 安全**：所有字段来自入参（调用方已切片 ``date<=as_of``），本模块绝不取未来。
- **确定性**：同输入同配置 ⇒ 权重逐位一致；不掺 ``now()``/时间戳。
- **fail-soft**：缺市值/波动/换手 ⇒ 走中性（不加不罚、不崩、不造假信号）。
- **不塞下单逻辑**：只给「切片权重 + 暴露 + 风险标注」，不给买卖指令、止损、仓位操作。

三个真正的增量（既有 portfolio 组件已覆盖的**不复造**）：
- 既有 :func:`check_sector_concentration`（含「其他」误杀修复）/ ``optimizer._cap_weights``
  （相关性+板块压制）/ ``summarize_portfolio_risk``（HHI/相关/现金）**已存在**。
- 本模块只补 **市值分层** + **低波加分** + **换手惩罚** 三块，并以一个中性
  编排层把它们 + 行业上限 + 单票上限 拼成「权重和 ≤1、现金兜底」的切片结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── 市值分层阈值（A股经验值，单位=元；可被 config 覆盖）──────────────
_CAP_LARGE_MIN = 100.0e8    # 市值 ≥ 100亿 ⇒ 大盘
_CAP_MID_MIN = 20.0e8       # 20亿 ≤ 市值 < 100亿 ⇒ 中盘


@dataclass(frozen=True)
class HoldingInput:
    """一只入选票的切片输入（全部来自调用方已 PIT 切片的入参）。

    - ``market_cap`` / ``annual_vol`` / ``turnover`` 允许 ``None``（缺失走中性）。
    - ``total_score`` 建议传 :func:`screener.build_screener_snapshot` 里已算好的
      生产口径总分（0~1）。
    """

    symbol: str
    total_score: float = 0.0
    market_cap: Optional[float] = None   # 总市值（元）
    annual_vol: Optional[float] = None   # 年化波动（0~1，如 0.35）
    turnover: Optional[float] = None      # 近期换手强度（日均成交额/流通市值，或日均换手率）
    sector: str = ""                      # 行业/板块（空=未知）


@dataclass(frozen=True)
class RiskOverlayConfig:
    """叠加层参数（全部有默认值，不依赖 thresholds.yaml ⇒ 不碰生产配置）。

    行业上限复用 ``check_sector_concentration`` 的语义（默认 0.40，与
    ``RiskThresholds.max_sector_concentration`` 同值）；本骨架用 0.35 更保守。
    """

    max_single_weight: float = 0.15
    max_sector_weight: float = 0.35
    cap_tier_limits: Dict[str, float] = field(
        default_factory=lambda: {"large": 0.50, "mid": 0.35, "small": 0.20}
    )
    # 低波加分 / 换手惩罚 强度（0=关闭，越大越往低波低换手表倾）
    low_vol_tilt: float = 0.3
    turnover_penalty: float = 0.5
    min_cash: float = 0.10


@dataclass(frozen=True)
class RiskOverlayResult:
    """切片结果（确定性、可序列化、fail-soft）。"""

    as_of: str
    n_holdings: int
    weights: Dict[str, float]                 # 已按上限约束、和 ≤ (1-min_cash)
    cash: float
    sector_exposure: Dict[str, float]
    cap_tier_exposure: Dict[str, float]
    risk_flags: List[str]
    notes: List[str]

    @property
    def invested(self) -> float:
        return round(1.0 - self.cash, 4)


def assign_cap_tier(
    market_cap: Optional[float],
    large_min: float = _CAP_LARGE_MIN,
    mid_min: float = _CAP_MID_MIN,
) -> str:
    """市值分层：large / mid / small / unknown（缺失=unknown，不猜）。"""
    if market_cap is None or market_cap <= 0:
        return "unknown"
    if market_cap >= large_min:
        return "large"
    if market_cap >= mid_min:
        return "mid"
    return "small"


def apply_risk_overlay(
    holdings: List[HoldingInput],
    config: Optional[RiskOverlayConfig] = None,
    *,
    as_of: str = "",
) -> RiskOverlayResult:
    """把一批入选票切成「带风险约束的组合权重 + 暴露指标」（纯计算、确定性、fail-soft）。

    流程（每步都在既有组件之上只补增量，不重造轮子）：
    1. **有效分**：``effective = total_score``，叠加
       - 低波加分：``× (1 + low_vol_tilt × lowvol_factor)``（波动越低 factor 越高）
       - 换手惩罚：``× (1 - turnover_penalty × turnover_factor)``（换手越高 factor 越高）
       缺失字段 factor=0（中性，不加不罚）。
    2. **初配**：``raw_w = effective``（分数越高分越高），同分按 symbol 稳定排序。
    3. **上限约束迭代**（单票 → 行业 → 市值分层，套用既有 ``_cap_weights`` 的
       「超上限者钉死 + 剩余额在余票间按分重分」模式，保证权重和不漂移）。
    4. **归一 + 现金兜底**：权重和 ≤ ``1 - min_cash``，超出部分留现金。
    5. **暴露统计**：行业/市值分层的权重占比 + 风险标注（只标注、不下指令）。
    """
    cfg = config or RiskOverlayConfig()
    notes: List[str] = []

    # fail-soft：空清单
    if not holdings:
        return RiskOverlayResult(
            as_of=as_of,
            n_holdings=0,
            weights={},
            cash=1.0,
            sector_exposure={},
            cap_tier_exposure={},
            risk_flags=[],
            notes=["universe 为空 ⇒ 全额现金（fail-soft）"],
        )

    missing_cap = sum(1 for h in holdings if h.market_cap is None)
    missing_vol = sum(1 for h in holdings if h.annual_vol is None)
    missing_turn = sum(1 for h in holdings if h.turnover is None)
    if missing_cap:
        notes.append(f"{missing_cap} 只缺市值 ⇒ 市值分层走 unknown（中性）")
    if missing_vol:
        notes.append(f"{missing_vol} 只缺波动 ⇒ 不做低波加分（中性）")
    if missing_turn:
        notes.append(f"{missing_turn} 只缺换手 ⇒ 不做换手惩罚（中性）")

    # ① 有效分（低波加分 + 换手惩罚；缺失=中性）
    vols = [h.annual_vol for h in holdings if h.annual_vol is not None]
    turns = [h.turnover for h in holdings if h.turnover is not None]
    v_lo, v_hi = _range(vols)
    t_lo, t_hi = _range(turns)

    effective: Dict[str, float] = {}
    for h in holdings:
        base = max(0.0, float(h.total_score))
        mult = 1.0
        if cfg.low_vol_tilt > 0 and h.annual_vol is not None:
            factor = 1.0 - _norm01(h.annual_vol, v_hi, v_lo)  # 波动越低 factor 越高（加分越多）
            mult *= 1.0 + cfg.low_vol_tilt * factor
        if cfg.turnover_penalty > 0 and h.turnover is not None:
            factor = _norm01(h.turnover, t_hi, t_lo)     # 换手越高 factor 越高（罚得越重）
            mult *= 1.0 - cfg.turnover_penalty * factor
        effective[h.symbol] = base * mult

    # ② 行业 / 市值分层 映射（缺失走中性标签）
    sector_of = {h.symbol: (h.sector or "unknown") for h in holdings}
    tier_of = {
        h.symbol: assign_cap_tier(h.market_cap) for h in holdings
    }
    if "unknown" in set(tier_of.values()):
        notes.append("存在 unknown 市值分层（缺市值票），该层不设上限")

    # ③ 相对切片 + 上限约束（单票 → 行业 → 市值分层，迭代削顶、释放成现金）
    weights = _slice_and_cap(
        effective,
        sector_of=sector_of,
        tier_of=tier_of,
        cfg=cfg,
    )

    # ④ 归一 + 现金兜底（权重和 ≤ 1 - min_cash；超出部分留现金）
    total = sum(weights.values())
    max_invested = max(0.0, 1.0 - cfg.min_cash)
    if total > max_invested + 1e-9:
        scale = max_invested / total if total > 0 else 0.0
        weights = {s: round(w * scale, 6) for s, w in weights.items()}
    cash = round(max(0.0, 1.0 - sum(weights.values())), 6)

    # ⑤ 暴露统计 + 风险标注
    sector_exposure = _group_exposure(weights, sector_of)
    tier_exposure = _group_exposure(weights, tier_of)

    flags: List[str] = []
    if max(weights.values(), default=0.0) > cfg.max_single_weight + 1e-6:
        flags.append("单票权重超上限")
    for sec, exp in sector_exposure.items():
        if sec != "unknown" and exp > cfg.max_sector_weight + 1e-6:
            flags.append(f"行业[{sec}]暴露{exp:.0%}超上限")
    for tier, exp in tier_exposure.items():
        limit = cfg.cap_tier_limits.get(tier)
        if tier in cfg.cap_tier_limits and limit is not None and exp > limit + 1e-6:
            flags.append(f"市值[{tier}]暴露{exp:.0%}超上限{limit:.0%}")

    return RiskOverlayResult(
        as_of=as_of,
        n_holdings=len(holdings),
        weights=weights,
        cash=cash,
        sector_exposure={k: round(v, 4) for k, v in sorted(sector_exposure.items())},
        cap_tier_exposure={k: round(v, 4) for k, v in sorted(tier_exposure.items())},
        risk_flags=flags,
        notes=notes,
    )


def holdings_from_screener(
    snapshot: Dict[str, Any],
    *,
    market_caps: Optional[Dict[str, float]] = None,
    annual_vols: Optional[Dict[str, float]] = None,
    turnovers: Optional[Dict[str, float]] = None,
    sectors: Optional[Dict[str, str]] = None,
    top_n: Optional[int] = None,
) -> List[HoldingInput]:
    """把 :func:`aqsp.strategies.screener.build_screener_snapshot` 的快照接成本模块输入。

    路线①（筛选器清单）与路线②（风险叠加层）在此接上：``snapshot["selected"]`` 里每只票
    已有生产口径 ``total_score``；本函数把「市值 / 年化波动 / 换手 / 行业」从调用方的
    逐票映射补进去（这些不在 screener 快照里，且都允许缺失 ⇒ 走中性 fail-soft）。

    - 纯计算、确定性（同 snapshot + 同映射 ⇒ 逐位一致）。
    - ``top_n``：可选再截前 N 只（默认全用 snapshot 已选的）。
    - 缺映射的票字段置 None（apply_risk_overlay 走中性，不崩、不造假信号）。
    """
    market_caps = market_caps or {}
    annual_vols = annual_vols or {}
    turnovers = turnovers or {}
    sectors = sectors or {}

    selected = snapshot.get("selected", []) or []
    if top_n is not None:
        selected = selected[: max(0, int(top_n))]

    holdings: List[HoldingInput] = []
    for entry in selected:
        symbol = str(entry.get("symbol", ""))
        if not symbol:
            continue
        holdings.append(
            HoldingInput(
                symbol=symbol,
                total_score=float(entry.get("total_score", 0.0) or 0.0),
                market_cap=market_caps.get(symbol),
                annual_vol=annual_vols.get(symbol),
                turnover=turnovers.get(symbol),
                sector=sectors.get(symbol, ""),
            )
        )
    return holdings


# ── 内部工具 ─────────────────────────────────────────────────────────


def _range(vals: List[float]) -> tuple[float, float]:
    if not vals:
        return 0.0, 0.0
    return float(min(vals)), float(max(vals))


def _norm01(x: float, hi: float, lo: float) -> float:
    """把 x 归一到 [0,1]（hi==lo ⇒ 0.5 中性）。"""
    if hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _slice_and_cap(
    effective: Dict[str, float],
    *,
    sector_of: Dict[str, str],
    tier_of: Dict[str, str],
    cfg: RiskOverlayConfig,
) -> Dict[str, float]:
    """先给相对切片（按有效分归一），再迭代削顶到满足「单票 + 行业 + 市值分层」上限。

    机制=**削顶、不重分配**：某票超上限时钉到上限，超出的量**直接释放成现金**
    （不塞给余票）。理由：
    - 把超量再分配给余票可能把**同一个行业/市值层**的组上限又顶破（重分配的票未必
      跨组），且迭代不保证收敛；
    - 风险叠加层的目标是**降尾部暴露**，把削掉的量留现金是**保守且安全**的默认，
      权重和只减不增、必然终止、必然满足所有组上限。
    最终现金由调用方 min_cash 再兜一次底。
    """
    # ① 相对切片：权重 ∝ 有效分（全量先归一，未考虑现金兜底）
    total_eff = sum(effective.values())
    weights: Dict[str, float] = {}
    if total_eff > 0:
        weights = {s: max(0.0, e) / total_eff for s, e in effective.items()}
    else:
        n = max(1, len(effective))
        weights = {s: 1.0 / n for s in effective}  # 全零分 ⇒ 等权兜底（fail-soft）

    # ② 迭代削顶（单票 → 行业 → 市值分层）
    for _round in range(len(weights) + 2):  # 有界：每轮至少钉死或移除一个
        changed = False

        # 单票上限
        for s in sorted(weights):
            if weights[s] > cfg.max_single_weight + 1e-9:
                weights[s] = cfg.max_single_weight
                changed = True

        # 行业上限（unknown 不限）
        for sec in sorted(set(sector_of.values())):
            if sec == "unknown":
                continue
            members = [s for s in weights if sector_of.get(s) == sec]
            sec_w = sum(weights[s] for s in members)
            if sec_w > cfg.max_sector_weight + 1e-9:
                scale = cfg.max_sector_weight / sec_w
                for s in members:
                    weights[s] = weights[s] * scale
                changed = True

        # 市值分层上限（unknown 不限）
        for tier in sorted(set(tier_of.values())):
            limit = cfg.cap_tier_limits.get(tier)
            if tier == "unknown" or limit is None:
                continue
            members = [s for s in weights if tier_of.get(s) == tier]
            tier_w = sum(weights[s] for s in members)
            if tier_w > limit + 1e-9:
                scale = limit / tier_w
                for s in members:
                    weights[s] = weights[s] * scale
                changed = True

        if not changed:
            break

    # ③ 清理近零权重（数值噪声，避免 0 权重占位）
    weights = {s: round(w, 6) for s, w in weights.items() if w > 1e-6}
    return weights


def _group_exposure(
    weights: Dict[str, float],
    group_of: Dict[str, str],
) -> Dict[str, float]:
    """某分组的权重占比（相对总投资，不含现金）。"""
    total = sum(weights.values())
    if total <= 0:
        return {}
    acc: Dict[str, float] = {}
    for s, w in weights.items():
        g = group_of.get(s, "unknown")
        acc[g] = acc.get(g, 0.0) + w / total
    return acc


def summarize_risk_overlay(
    result: RiskOverlayResult,
) -> str:
    """把 :func:`apply_risk_overlay` 结果渲染成几行中文说明（只读、无指令词）。"""
    lines: List[str] = []
    if result.n_holdings == 0:
        lines.append("- 无入选标的 ⇒ 全额现金。")
        for n in result.notes:
            lines.append(f"  - {n}")
        return "\n".join(lines)

    invested = result.invested
    lines.append(f"- 组合投入 {invested:.1%}，现金留存 {result.cash:.1%}（{result.n_holdings} 只）")
    if result.sector_exposure:
        sec_txt = "、".join(
            f"{k} {v:.0%}" for k, v in sorted(result.sector_exposure.items(), key=lambda x: -x[1])
        )
        lines.append(f"- 行业暴露：{sec_txt}")
    if result.cap_tier_exposure:
        tier_txt = "、".join(
            f"{k} {v:.0%}" for k, v in sorted(result.cap_tier_exposure.items(), key=lambda x: -x[1])
        )
        lines.append(f"- 市值分层：{tier_txt}")
    if result.risk_flags:
        lines.append("- 风险标注：" + "；".join(result.risk_flags))
    for n in result.notes:
        lines.append(f"  - {n}")
    lines.append("**说明**：本切片为风险叠加层输出，只给权重与暴露，不含任何交易指令。")
    return "\n".join(lines)
