"""短线影子账户只读桥接 —— 把 runner 上的前向验证账本暴露给前端。

架构位置
--------
与 `performance_bridge.py`（选股台账绩效）、`aqsp_bridge.py`（研究快照）并列：
本模块只负责**短线多策略纸面账户**这一份产物，不碰正式台账、不碰打分。

铁律
----
1. **只读**：只读 runner 推过来的 JSON，绝不写账本、绝不触发任何计算。
2. **口径唯一**：数字全部由 runner 侧 `research/paper_ledger.py` 算好，
   这里**不做二次统计** —— 否则前端显示和账本会分叉。
3. **fail-closed**：产物缺失/损坏/过期时返回 `available=False` + 原因，
   前端显示"暂无"，绝不显示 0 冒充"收益为零"。
4. **诚实**：`excess_*` 一律是相对全市场等权基准的超额；绝对收益字段同时给出，
   但是否能叫 alpha 只看超额那一列。

产物格式见 `research/paper_export.py`（schema_version=v1）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "v1"
DEFAULT_PATH = "data/paper_arena.json"
# 超过这么多天没更新就标记过期（周末+节假日不该误报，所以给 5 天）
STALE_AFTER_DAYS = 5

# 与 performance_bridge 同源：项目根 = backend/ 的上一级。
# 官方部署是 `cd backend && uvicorn app:app`，所以相对路径必须按**项目根**解析，
# 不能按 cwd —— 否则上线后找不到 data/。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _path() -> Path:
    raw = os.environ.get("AQSP_PAPER_ARENA", "").strip() or DEFAULT_PATH
    path = Path(raw).expanduser()
    return path if path.is_absolute() else _PROJECT_ROOT / path


def _unavailable(reason: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "available": False,
        "reason": reason,
        "generated_at": None,
        "stale": False,
        "sleeves": [],
        "nav": {"dates": [], "port": [], "bench": []},
        "positions": [],
        "recent_trades": [],
    }


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def payload() -> dict:
    """读取并校验 runner 推来的产物；任何异常都降级为 available=False。"""
    path = _path()
    if not path.exists():
        return _unavailable("尚未收到影子账户产物（%s），需 runner 推送后才有数据" % path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _unavailable("影子账户产物读取失败：%s" % type(exc).__name__)
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return _unavailable("影子账户产物 schema 不匹配，等待 runner 升级后重推")

    stale = False
    generated = raw.get("generated_at")
    if generated:
        try:
            ts = datetime.fromisoformat(str(generated))
            stale = (datetime.now(ts.tzinfo) - ts) > timedelta(days=STALE_AFTER_DAYS)
        except ValueError:
            stale = False

    sleeves = []
    for item in raw.get("sleeves") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        sleeves.append({
            "id": str(item["id"])[:8],
            "name": str(item.get("name") or "")[:40],
            "hypothesis": str(item.get("hypothesis") or "")[:300],
            "n": int(_num(item.get("n")) or 0),
            "days": int(_num(item.get("days")) or 0),
            "exc_mean": _num(item.get("exc_mean")),
            "exc_win": _num(item.get("exc_win")),
            "t_exc": _num(item.get("t_exc")),
            "q_exc": _num(item.get("q_exc")),
            "n_need_days": int(_num(item.get("n_need_days")) or 0) or None,
            "funded_nav": _num(item.get("funded_nav")),
            "funded_ret": _num(item.get("funded_ret")),
            "funded_n": int(_num(item.get("funded_n")) or 0),
            "funded_win": _num(item.get("funded_win")),
            "positions": int(_num(item.get("positions")) or 0),
            "sealed_n": int(_num(item.get("sealed_n")) or 0),
            "unfunded": item.get("unfunded") if isinstance(item.get("unfunded"), dict) else {},
        })

    nav = raw.get("nav") or {}
    dates = [str(d)[:10] for d in (nav.get("dates") or [])][-120:]
    port = [_num(v) for v in (nav.get("port") or [])][-120:]
    bench = [_num(v) for v in (nav.get("bench") or [])][-120:]

    # 持仓与最近成交同样逐字段归一：前端按类型直接渲染，
    # 缺字段留 None，绝不用 0 兜底（0 会被读成"收益为零"）。
    positions = []
    for item in raw.get("positions") or []:
        if not isinstance(item, dict) or not item.get("code"):
            continue
        positions.append({
            "sleeve": str(item.get("sleeve") or "")[:8],
            "code": str(item["code"])[:12],
            "name": str(item.get("name") or "")[:20],
            "buy_date": str(item.get("buy_date") or "")[:10],
            "buy_price": _num(item.get("buy_price")),
            "shares": int(_num(item.get("shares")) or 0),
            "note": str(item.get("note") or "")[:60],
        })
    positions = positions[:40]

    trades = []
    for item in raw.get("recent_trades") or []:
        if not isinstance(item, dict) or not item.get("code"):
            continue
        trades.append({
            "sleeve": str(item.get("sleeve") or "")[:8],
            "code": str(item["code"])[:12],
            "name": str(item.get("name") or "")[:20],
            "sell_date": str(item.get("sell_date") or "")[:10],
            "ret_pct": _num(item.get("ret_pct")),
            "excess_pct": _num(item.get("excess_pct")),
            "reason": str(item.get("reason") or "")[:40],
            "funded": bool(item.get("funded")),
        })
    trades = trades[:40]

    return {
        "schema_version": SCHEMA_VERSION,
        "available": True,
        "reason": "",
        "generated_at": generated,
        "stale": stale,
        "as_of": raw.get("as_of"),
        "initial_capital": _num(raw.get("initial_capital")),
        "total_capital": _num(raw.get("total_capital")),
        "total_nav": _num(raw.get("total_nav")),
        "total_ret_pct": _num(raw.get("total_ret_pct")),
        "trading_days": int(_num(raw.get("trading_days")) or 0),
        "snapshot_days": int(_num(raw.get("snapshot_days")) or 0),
        "cost_round_trip_pct": _num(raw.get("cost_round_trip_pct")),
        "sleeves": sleeves,
        "nav": {"dates": dates, "port": port, "bench": bench},
        "positions": positions,
        "recent_trades": trades,
    }
