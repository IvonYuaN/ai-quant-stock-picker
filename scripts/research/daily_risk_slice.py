#!/usr/bin/env python3
"""组合风险切片生成器（路线②，接在路线①筛选器清单之后）。

定位：路线① :mod:`aqsp.strategies.screener` 产出「可行筛选器」每日清单（不追 alpha、
不塞下单逻辑）。本 CLI 接在清单**之后**，回答「这批票若要拿，怎么切」——把筛选器
快照喂给 :func:`aqsp.portfolio.risk_overlay.apply_risk_overlay`，输出**组合权重切片 +
行业/市值分层暴露 + 现金兜底 + 风险标注**，目标是**降尾部风险**，不新增任何 alpha
信号、不改打分方向。

红线（与路线①同族）：
- **纯计算、只读、PIT 安全、确定性**：只吃入参 JSON 快照 + 逐票富化映射，**绝不取未来、
  不联网**；重跑逐位一致。
- **不牵动生产下单链**：不碰 :class:`PickResult`/``executor``，只产「切片 + 暴露 + 标注」。
- **fail-soft**：缺市值/波动/换手的票走中性（不加不罚、不崩、不造假信号）。

用法（本机小 universe 轻量验证）::

    # 1) 先跑路线①生成快照（可选，也可用已落盘的 JSON）
    PYTHONPATH=src python scripts/research/daily_screener.py \
      --db "A股量化分析数据/astocks_raw.db" --as-of 20261106 \
      --max-symbols 300 --top-n 20 \
      --out-json outputs/screener/snap.json

    # 2) 本 CLI 把快照切成风险切片（--top-n 缺省用快照全量；富化映射可选）
    PYTHONPATH=src python scripts/research/daily_risk_slice.py \
      --from-json outputs/screener/snap.json \
      --market-caps outputs/enrich/market_caps.json \
      --annual-vols  outputs/enrich/annual_vols.json \
      --turnovers    outputs/enrich/turnovers.json \
      --sectors      outputs/enrich/sectors.json \
      --max-single-weight 0.15 --max-sector-weight 0.35 \
      --cap-large 0.50 --cap-mid 0.35 --cap-small 0.20 \
      --min-cash 0.10 \
      --out-json outputs/slice/slice.json --out-md outputs/slice/slice.md

富化映射都是**可选**的 ``{symbol: value}`` JSON；缺哪类，该类就全走中性 fail-soft
（缺行业 ⇒ 行业不限、缺市值 ⇒ 市值层 unknown、缺波动 ⇒ 不做低波加分、缺换手 ⇒ 不罚）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from aqsp.portfolio.risk_overlay import (
    RiskOverlayConfig,
    apply_risk_overlay,
    holdings_from_screener,
    summarize_risk_overlay,
)


def _load_json_map(path: Optional[str]) -> Optional[dict]:
    """读 ``{symbol: value}`` 富化映射；路径为空 ⇒ None（走中性）。"""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # fail-soft：映射读失败 ⇒ 该类走中性，不崩
        print(f"[warn] 富化映射读取失败 {path}: {exc}（该类走中性）", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(f"[warn] 富化映射须是 {{symbol: value}} 对象：{path}（该类走中性）", file=sys.stderr)
        return None
    # 值统一成 float（数值型映射）；非数值（如行业字符串）原样保留
    out = {}
    for k, v in data.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            out[str(k)] = v
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="组合风险切片生成器（接路线①筛选器快照；纯计算、只读、PIT、确定性）"
    )
    ap.add_argument("--from-json", required=True, help="路线① build_screener_snapshot 落盘的 JSON 快照")
    ap.add_argument("--market-caps", default=None, help="逐票总市值 JSON {symbol: 元}")
    ap.add_argument("--annual-vols", default=None, help="逐票年化波动 JSON {symbol: 0~1}")
    ap.add_argument("--turnovers", default=None, help="逐票换手强度 JSON {symbol: 值}")
    ap.add_argument("--sectors", default=None, help="逐票行业 JSON {symbol: 行业名}")
    ap.add_argument("--top-n", type=int, default=None, help="再截前 N 只（缺省用快照全量）")
    ap.add_argument("--as-of", default="", help="切片时点标注（缺省取快照 as_of）")
    # 叠加层参数（缺省用 RiskOverlayConfig 默认值）
    ap.add_argument("--max-single-weight", type=float, default=0.15, help="单票权重上限")
    ap.add_argument("--max-sector-weight", type=float, default=0.35, help="行业权重上限")
    ap.add_argument("--cap-large", type=float, default=0.50, help="大盘市值层上限")
    ap.add_argument("--cap-mid", type=float, default=0.35, help="中盘市值层上限")
    ap.add_argument("--cap-small", type=float, default=0.20, help="小盘市值层上限")
    ap.add_argument("--low-vol-tilt", type=float, default=0.3, help="低波加分强度（0=关）")
    ap.add_argument("--turnover-penalty", type=float, default=0.5, help="换手惩罚强度（0=关）")
    ap.add_argument("--min-cash", type=float, default=0.10, help="现金兜底下限")
    ap.add_argument("--out-json", default=None, help="切片 JSON 输出路径")
    ap.add_argument("--out-md", default=None, help="切片 Markdown 输出路径")
    args = ap.parse_args(argv)

    # ① 读筛选器快照
    snap_path = Path(args.from_json)
    if not snap_path.exists():
        print(f"[error] 快照不存在：{args.from_json}", file=sys.stderr)
        return 2
    snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    as_of = args.as_of or str(snapshot.get("as_of", ""))

    # ② 逐票富化映射（均可选，缺失走中性）
    market_caps = _load_json_map(args.market_caps)
    annual_vols = _load_json_map(args.annual_vols)
    turnovers = _load_json_map(args.turnovers)
    sectors = _load_json_map(args.sectors)

    # ③ 接线 + 切片
    holdings = holdings_from_screener(
        snapshot,
        market_caps=market_caps,
        annual_vols=annual_vols,
        turnovers=turnovers,
        sectors=sectors,
        top_n=args.top_n,
    )
    cfg = RiskOverlayConfig(
        max_single_weight=args.max_single_weight,
        max_sector_weight=args.max_sector_weight,
        cap_tier_limits={
            "large": args.cap_large,
            "mid": args.cap_mid,
            "small": args.cap_small,
        },
        low_vol_tilt=args.low_vol_tilt,
        turnover_penalty=args.turnover_penalty,
        min_cash=args.min_cash,
    )
    result = apply_risk_overlay(holdings, cfg, as_of=as_of)

    # ④ 输出（确定性 dict，可直接 json.dumps）
    out_json = {
        "as_of": as_of,
        "n_holdings": result.n_holdings,
        "cash": result.cash,
        "invested": result.invested,
        "weights": result.weights,
        "sector_exposure": result.sector_exposure,
        "cap_tier_exposure": result.cap_tier_exposure,
        "risk_flags": result.risk_flags,
        "notes": result.notes,
        "config": {
            "max_single_weight": cfg.max_single_weight,
            "max_sector_weight": cfg.max_sector_weight,
            "cap_tier_limits": cfg.cap_tier_limits,
            "low_vol_tilt": cfg.low_vol_tilt,
            "turnover_penalty": cfg.turnover_penalty,
            "min_cash": cfg.min_cash,
        },
    }
    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(
            json.dumps(out_json, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    md = f"# 组合风险切片（接筛选器清单，as_of={as_of}）\n\n{summarize_risk_overlay(result)}"
    if args.out_md:
        Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_md).write_text(md, encoding="utf-8")

    # stdout 摘要（供 cron/自动化抓取）
    top = sorted(result.weights.items(), key=lambda kv: -kv[1])[:3]
    top_txt = "、".join(f"{s} {w:.1%}" for s, w in top) or "-"
    print(
        f"[risk-slice] as_of={as_of} holdings={result.n_holdings} "
        f"invested={result.invested:.1%} cash={result.cash:.1%} "
        f"top3=[{top_txt}] flags={len(result.risk_flags)}"
    )
    if args.out_json:
        print(f"[written] {args.out_json}")
    if args.out_md:
        print(f"[written] {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
