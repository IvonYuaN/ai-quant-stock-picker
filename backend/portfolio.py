"""持仓数据层 —— 用户自己录入的持仓 + 实时行情叠加浮动盈亏。

合规：持仓是用户主动录入的自己的标的（存本地 ~/.vibe-research/portfolio.json，
不上传、不进仓库），不预置任何标的、不含 _SEED 兜底、不做推荐。
盈亏红涨绿跌（A股口径）。含每半小时后台定时刷新 + 手动刷新。

存储位置：默认用户目录 ~/.vibe-research/（可用 VR_DATA_DIR 覆盖）——
放仓库外，重新下载/覆盖项目文件夹不会丢数据（issue #12）。
≤v0.1.1 存在 backend/.cache/ 仓库内，首次启动自动迁移（复制，旧文件保留作备份）。
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
import threading
import time
from datetime import date, timezone, timedelta

from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.utils.jsonl_io import advisory_lock

import astock
import tenant as _tenant
import risk_rules

HERE = os.path.dirname(os.path.abspath(__file__))
_OLD_PF_FILE = os.path.join(HERE, ".cache", "portfolio.json")  # ≤v0.1.1 旧位置
# CACHE_DIR 名字保留（测试/外部按此名 monkeypatch），实际已是用户数据目录
CACHE_DIR = os.environ.get("VR_DATA_DIR") or os.path.join(
    os.path.expanduser("~"), ".vibe-research"
)
BEIJING = timezone(timedelta(hours=8))
_LOCK = threading.RLock()


def _tenant_id() -> str:
    return _tenant.current_tenant.get()


def _tenant_dir() -> str:
    """当前租户的数据根目录；local 用原始 CACHE_DIR，其余落到 users/<tid>/。"""
    tid = _tenant_id()
    if tid == "local":
        return CACHE_DIR
    return os.path.join(CACHE_DIR, "users", tid)


def _pf_file() -> str:
    return os.path.join(_tenant_dir(), "portfolio.json")


def _legacy_local_pf() -> str:
    """单用户时代（local 租户）的持仓文件位置，用作多用户迁移的源。"""
    return os.path.join(CACHE_DIR, "portfolio.json")


def _migrate_legacy() -> None:
    """旧版持仓在仓库内 .cache/ 里，重下载项目会丢；迁到用户目录（新位置已有则不动）。"""
    try:
        if not os.path.exists(_legacy_local_pf()) and os.path.exists(_OLD_PF_FILE):
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = _legacy_local_pf() + ".migrate.tmp"
            shutil.copy2(_OLD_PF_FILE, tmp)
            os.replace(
                tmp, _legacy_local_pf()
            )  # 原子落位：复制中断不会留半截 portfolio.json 挡住下次重试
    except OSError as e:
        # 迁移失败不阻塞启动，但要出声——旧数据原样保留在 _OLD_PF_FILE，可手工复制
        print(
            f"[vibe-research] 持仓数据迁移失败（旧数据仍在 {_OLD_PF_FILE}）: {e}",
            file=sys.stderr,
        )


_migrate_legacy()


def _now() -> str:
    return now_shanghai().strftime("%Y-%m-%d %H:%M")


# 已尝试过迁移的租户，避免每次读都 stat 旧文件
_MIGRATED_TENANTS: set[str] = set()


def _maybe_migrate_to_tenant() -> None:
    """非 local 租户首次访问时，若自身为空且本地 legacy 持仓存在，复制过来
    （让「从单用户升级到多用户」的老数据无缝落到主人自己的租户桶）。"""
    tid = _tenant_id()
    if tid == "local" or tid in _MIGRATED_TENANTS:
        return
    _MIGRATED_TENANTS.add(tid)
    tf = _pf_file()
    if os.path.exists(tf) or not os.path.exists(_legacy_local_pf()):
        return
    try:
        os.makedirs(_tenant_dir(), exist_ok=True)
        shutil.copy2(_legacy_local_pf(), tf)
    except OSError:
        pass


def _load() -> dict:
    _maybe_migrate_to_tenant()
    try:
        with open(_pf_file(), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"holdings": [], "last_refresh": None}


def _save(d: dict) -> None:
    # 先写临时文件再原子改名：并发读若撞上写中途的半截 JSON，会被 _load 静默当成空持仓
    os.makedirs(_tenant_dir(), exist_ok=True)
    tmp = _pf_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, _pf_file())


def _parse_opened_at(value: str, today: date) -> date:
    """只接受不晚于上海当日的 YYYY-MM-DD，避免无效日期绕过 T+1。"""
    if not isinstance(value, str):
        raise ValueError("开仓日期必须为 YYYY-MM-DD")
    try:
        opened = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("开仓日期必须为有效的 YYYY-MM-DD") from exc
    if opened.isoformat() != value:
        raise ValueError("开仓日期必须为 YYYY-MM-DD")
    if opened > today:
        raise ValueError("开仓日期不能晚于上海当日")
    return opened


def resolve_holding_days(
    holdings: list[dict], overrides: dict[str, int] | None = None,
) -> dict[str, int]:
    """生成 T+1 判定所需的 0/1 标记，再按代码覆盖显式持有天数。

    1 仅表示之前开仓，不表示实际持仓交易日数。缺日期的旧持仓按老仓兼容；
    已存但无效或未来的日期抛 DataError，禁止降级成可卖提醒。
    """
    today = today_shanghai()
    days: dict[str, int] = {}
    for holding in holdings:
        opened_at = holding.get("opened_at")
        if opened_at is None:
            continue
        code = holding["code"]
        try:
            opened = _parse_opened_at(opened_at, today)
        except ValueError as exc:
            raise DataError(f"持仓 {code} 的{exc}，请修正后重试") from exc
        days[code] = 0 if opened == today else 1
    for code, value in (overrides or {}).items():
        if type(value) is not int or value < 0:
            raise ValueError("持有交易日数必须为非负整数")
        days[code] = value
    return days


def add_holding(
    code: str, shares: float, cost: float, opened_at: str | None = None,
    operation_id: str | None = None,
) -> dict:
    """加一笔持仓；同代码则按加权平均成本合并（加仓）。

    Args:
        code: 6 位股票代码。
        shares: 持仓数量（> 0）。
        cost: 成本价（可负：融券 / 返息 / 摊薄场景，见 portfolio_add）。
        opened_at: 开仓日期 ``YYYY-MM-DD``（上海时区），缺省今天。用于 LIVE 风控的
            T+1 可卖性推导（当日买入今日不可卖，Q3=A）；P2-8 前录入的老持仓无此字段，
            按老仓（即时可卖）处理，不误伤。同代码合并时保留最早的 opened_at
            （加仓不改动原仓的开仓基准；合并行按老仓可卖，不把老仓误判成当日买入）。
    """
    if not math.isfinite(shares) or shares <= 0 or not math.isfinite(cost):
        raise ValueError("数量须为正数，成本须为有限数字")
    today = today_shanghai()
    opened_at = (
        today.isoformat() if opened_at is None
        else _parse_opened_at(opened_at, today).isoformat()
    )
    with _LOCK, advisory_lock(_pf_file()):
        d = _load()
        request = {"code": code, "shares": shares, "cost": cost, "opened_at": opened_at}
        if operation_id and operation_id in d.get("operations", {}):
            if d["operations"][operation_id] != request:
                raise ValueError("重复操作标识的成交内容不同")
            return get_portfolio()
        for h in d["holdings"]:
            if h["code"] == code:
                lots = h.setdefault("lots", [{"shares": h["shares"], "opened_at": h.get("opened_at")}])
                lots.append({"shares": shares, "opened_at": opened_at})
                total = h["shares"] + shares
                # 4 位小数：ETF/基金成本常见 3-4 位（issue #13），2-3 位会让市值/盈亏对不上账
                h["cost"] = (
                    round((h["shares"] * h["cost"] + shares * cost) / total, 4)
                    if total
                    else cost
                )
                h["shares"] = total
                # 合并保留最早开仓日：老数据无 opened_at 时保持缺省（老仓可卖），
                # 两边都有时取更早的一个（保守：不把老仓误降成「当日买入」）。
                prior = h.get("opened_at")
                h["opened_at"] = min(prior, opened_at) if prior else prior
                break
        else:
            d["holdings"].append(
                {"code": code, "shares": shares, "cost": cost, "opened_at": opened_at,
                 "lots": [{"shares": shares, "opened_at": opened_at}]}
            )
        risk_rules.ensure_rules(d)
        if operation_id:
            d.setdefault("operations", {})[operation_id] = request
        _save(d)
    return get_portfolio()


def remove_holding(code: str) -> dict:
    with _LOCK, advisory_lock(_pf_file()):
        d = _load()
        d["holdings"] = [h for h in d["holdings"] if h["code"] != code]
        d["risk_rules"] = [r for r in d.get("risk_rules", []) if r["symbol"] != code]
        _save(d)
    return get_portfolio()


def close_position(
    code: str, date: str, price: float, shares: float, cost: float,
    operation_id: str | None = None, *, require_holding: bool = False,
) -> dict:
    """Atomically record an actual sale and reduce existing holdings (FIFO T+1 lots).

    Legacy history-only entry is retained when no holding exists. Alert disposition
    requires a holding and uses a durable operation id for crash-safe retries.
    """
    from datetime import date as date_type

    try:
        sold_at = date_type.fromisoformat(date)
    except ValueError as exc:
        raise ValueError("清仓日期必须为 YYYY-MM-DD") from exc
    if sold_at.isoformat() != date or sold_at > today_shanghai():
        raise ValueError("清仓日期必须为不晚于今天的 YYYY-MM-DD")
    if not all(math.isfinite(v) for v in (price, shares, cost)) or price <= 0 or shares <= 0:
        raise ValueError("成交价与数量须为有限正数")
    with _LOCK, advisory_lock(_pf_file()):
        d = _load()
        d.setdefault("closed", [])
        existing = next((c for c in d["closed"] if operation_id and c.get("operation_id") == operation_id), None)
        if existing:
            if any(existing[k] != v for k, v in {"code": code, "date": date, "price": price, "shares": shares}.items()):
                raise ValueError("重复操作标识的成交内容不同")
            return get_portfolio()
        holding = next((h for h in d["holdings"] if h["code"] == code), None)
        if require_holding and holding is None:
            raise ValueError("持仓不存在，不能从提醒重复记账")
        if holding:
            lots = holding.get("lots") or [{"shares": holding["shares"], "opened_at": holding.get("opened_at")}]
            available = sum(float(lot["shares"]) for lot in lots if not lot.get("opened_at") or lot["opened_at"] < date)
            if shares > available:
                raise ValueError(f"T+1 可卖数量不足：当日可卖 {available:g} 股")
            cost = float(holding["cost"])
            remaining = shares
            updated_lots = []
            for lot in sorted(lots, key=lambda item: item.get("opened_at") or ""):
                volume = float(lot["shares"])
                if not lot.get("opened_at") or lot["opened_at"] < date:
                    consumed = min(volume, remaining)
                    volume -= consumed
                    remaining -= consumed
                if volume > 0:
                    updated_lots.append({**lot, "shares": volume})
            holding["shares"] -= shares
            holding["lots"] = updated_lots
            if holding["shares"] <= 0:
                d["holdings"].remove(holding)
                d["risk_rules"] = [r for r in d.get("risk_rules", []) if r["symbol"] != code]
            elif updated_lots:
                holding["opened_at"] = updated_lots[0].get("opened_at")
        pnl = (price - cost) * shares
        try:
            name = astock.tencent_quote([code]).get(code, {}).get("name", code)
        except Exception:
            name = code
        d["closed"].append(
            {
                "code": code,
                "name": name,
                "date": date,
                "price": price,
                "shares": shares,
                "cost": cost,
                "pnl": round(pnl, 2),
                "pnl_pct": round((price - cost) / cost * 100, 2) if cost else 0.0,
                "operation_id": operation_id,
            }
        )
        _save(d)
    return get_portfolio()


def remove_closed(index: int) -> dict:
    with _LOCK, advisory_lock(_pf_file()):
        d = _load()
        cl = d.get("closed", [])
        if 0 <= index < len(cl):
            cl.pop(index)
            _save(d)
    return get_portfolio()


def get_portfolio() -> dict:
    """读持仓 + 实时行情，算每笔与汇总的市值/浮动盈亏。"""
    with _LOCK:
        d = _load()
    hs = d.get("holdings", [])
    rows, tmv, tcost = [], 0.0, 0.0
    if hs:
        try:
            quotes = astock.tencent_quote([h["code"] for h in hs])
        except Exception:
            quotes = {}
        for h in hs:
            q = quotes.get(h["code"], {})
            price = q.get("price", 0.0)
            mv = price * h["shares"]
            cv = h["cost"] * h["shares"]
            pnl = mv - cv
            rows.append(
                {
                    "code": h["code"],
                    "name": q.get("name", h["code"]),
                    "price": price,
                    "shares": h["shares"],
                    "cost": h["cost"],
                    "opened_at": h.get("opened_at"),
                    "position_id": h.get("position_id"),
                    "sellable_shares": sum(float(lot["shares"]) for lot in (h.get("lots") or [{"shares": h["shares"], "opened_at": h.get("opened_at")}]) if not lot.get("opened_at") or lot["opened_at"] < today_shanghai().isoformat()),
                    "t1_locked_shares": sum(float(lot["shares"]) for lot in (h.get("lots") or [{"shares": h["shares"], "opened_at": h.get("opened_at")}]) if lot.get("opened_at") and lot["opened_at"] >= today_shanghai().isoformat()),
                    "market_value": round(mv, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl / cv * 100, 2) if cv else 0.0,
                }
            )
            tmv += mv
            tcost += cv
    total_pnl = tmv - tcost
    closed = d.get("closed", [])
    return {
        "holdings": rows,
        "totals": {
            "market_value": round(tmv, 2),
            "cost": round(tcost, 2),
            "pnl": round(total_pnl, 2),
            "pnl_pct": round(total_pnl / tcost * 100, 2) if tcost else 0.0,
        },
        "closed": closed,
        "realized_pnl": round(sum(c.get("pnl", 0) for c in closed), 2),
        "updated": _now(),
        "last_refresh": d.get("last_refresh"),
    }


def get_risk_rules() -> list[dict]:
    """Lazily attach defaults for old holdings and return only active positions."""
    with _LOCK, advisory_lock(_pf_file()):
        data = _load()
        before = json.dumps(data, sort_keys=True)
        rules = risk_rules.ensure_rules(data)
        symbols = {h["code"] for h in data.get("holdings", [])}
        if json.dumps(data, sort_keys=True) != before:
            _save(data)
        return [r.copy() for r in rules if r["symbol"] in symbols]


def update_risk_rule(rule_id: str, patch: dict) -> dict:
    """Persist user controls without resetting cost linkage or trailing peak."""
    with _LOCK, advisory_lock(_pf_file()):
        data = _load()
        rules = risk_rules.ensure_rules(data)
        rule = next((r for r in rules if r["id"] == rule_id), None)
        if rule is None:
            raise ValueError("持仓提醒规则不存在")
        risk_rules.patch_rule(rule, patch)
        risk_rules.ensure_rules(data)
        _save(data)
        return rule.copy()


def observe_risk_prices(prices: dict[str, float]) -> list[dict]:
    """Persist observed high-water prices; trailing stops never move down."""
    with _LOCK, advisory_lock(_pf_file()):
        data = _load()
        for rule in risk_rules.ensure_rules(data):
            price = prices.get(rule["symbol"], 0)
            if rule["level"] == "L5" and math.isfinite(price) and price > 0:
                rule["high_water_price"] = max(float(rule.get("high_water_price") or 0), price)
                rule["trigger_price"] = max(float(rule.get("trigger_price") or 0), rule["high_water_price"] * (1 - float(rule["param_value"])))
        _save(data)
        symbols = {h["code"] for h in data.get("holdings", [])}
        return [r.copy() for r in data["risk_rules"] if r["symbol"] in symbols]


def record_rule_disposition(rule_id: str, ignored: bool) -> None:
    """Repeated ignored episodes reduce frequency, never strategy sensitivity."""
    with _LOCK, advisory_lock(_pf_file()):
        data = _load()
        rule = next((r for r in data.get("risk_rules", []) if r["id"] == rule_id), None)
        if rule is None:
            return
        rule["ignore_streak"] = int(rule.get("ignore_streak", 0)) + 1 if ignored else 0
        if rule["ignore_streak"] >= risk_rules.PositionRuleDefaults().ignored_before_daily and rule["freq_mode"] == "always":
            rule["freq_mode"] = "daily"
            rule["frequency_note"] = "连续忽略两次，已降为每日一次；可手动修改"
        rule["updated_at"] = now_shanghai().isoformat(timespec="seconds")
        _save(data)


def _refresh_snapshot() -> None:
    """后台定时任务：刷新时间戳（GET 本就实时算，这里记录后台刷新点）。"""
    with _LOCK, advisory_lock(_pf_file()):
        d = _load()
        d["last_refresh"] = _now()
        _save(d)


def start_scheduler(interval: int = 1800) -> None:
    """每半小时后台刷新一次持仓数据（daemon 线程）。"""

    def loop():
        while True:
            time.sleep(interval)
            try:
                _refresh_snapshot()
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True).start()
