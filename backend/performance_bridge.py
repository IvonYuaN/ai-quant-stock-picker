"""绩效只读桥接 —— 把「选得准不准」暴露给前端。

架构位置
--------
`aqsp_bridge.py` 是**研究快照**的只读桥接（刻意不碰 ledger）。
本模块是**绩效**的只读桥接，两者职责分开：一个回答"今天选了什么"，
一个回答"选得准不准" —— 后者才是复盘的核心。

铁律
----
1. **只读**：绝不写 ledger、绝不触发权重落盘（`record_history=False`）。
2. **口径唯一**：命中率一律复用 `aqsp.ledger.learner.PerformanceLearner`，
   不在这里另算一套 —— 否则前端数字和策略权重学习会分叉。
3. **遵守宪法**：
   - §5.2 整体命中率按 **signal_date 聚合成 1 个观察**（同日多 pick 合并），
     不按每笔交易算。
   - §5.4 **冷启动期（独立信号日 < 30）不展示胜率**，只显示积累进度。
   - §5.3 `not_executable` 不进胜率统计（learner 内部已处理）。
   - §8 学习对象是命中率分布，**不是 PnL**；`avg_return / sharpe / max_drawdown`
     仅作为**观测与告警**字段返回，并在字段上明确标注，前端不得当作主指标。
"""
from __future__ import annotations

import os
from datetime import date as CalendarDate
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from aqsp.core.time import is_trading_day, now_shanghai, to_shanghai
from aqsp.ledger.base import read_ledger
from aqsp.ledger.learner import (
    LearnerConfig,
    PerformanceLearner,
    StrategyDecayDetector,
)

# 正式台账路径与 cli.py `_formal_runtime_ledger_path` 保持一致：
# 环境变量 AQSP_LEDGER 优先，默认 data/predictions.jsonl。
# 注意：data/ledger.jsonl 与 data/paper_trades.jsonl 是**另外两套**状态体系
# （closed/open 而非 validated），不是学习器口径，不要指错。
DEFAULT_LEDGER_PATH = "data/predictions.jsonl"
DEFAULT_WEIGHT_HISTORY_PATH = "data/weight_history.jsonl"
PERFORMANCE_SCHEMA_VERSION = "v1"

# 停滞判定：最新信号日之后已过去这么多个**交易日**就算停滞。
# 用交易日而不是自然日 —— 春节/长假里停几天是正常的，按自然日会误报。
STALE_AFTER_TRADING_DAYS = 5

# 与 LearnerConfig 保持一致；这里显式写出，避免前端拿不到常量时无法解释"为什么是 30"
MIN_INDEPENDENT_SIGNAL_DAYS = LearnerConfig().min_independent_signal_days

# 票级复盘明细（recent_picks）的返回上限。
# 这是**展示侧**截断，与任何统计口径无关：台账里 248+ 笔全量推给前端只为渲染一张
# "上次选了哪只票、事后如何"的表格，没必要。截断不影响 overall / strategies 的
# 聚合口径（它们直接消费整份 settled frame，与这里无关）。
RECENT_PICKS_LIMIT = 12


# 与 aqsp_bridge 同源：项目根 = backend/ 的上一级。
# 官方部署是 `cd backend && uvicorn app:app`（见 deploy/systemd 的 WorkingDirectory），
# 所以相对路径必须按**项目根**解析，不能按 cwd —— 否则上线后找不到 data/。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve(raw: str, default: str) -> Path:
    path = Path(raw or default).expanduser()
    return path if path.is_absolute() else _PROJECT_ROOT / path


def _ledger_path() -> Path:
    """
    正式台账路径，与 cli.py `_formal_runtime_ledger_path` 对齐。

    注意：data/ledger.jsonl 与 data/paper_trades.jsonl 是**另外两套**状态体系
    （用 closed/open 而非 validated），不是学习器口径，不要指错。
    """
    return _resolve(os.environ.get("AQSP_LEDGER", "").strip(), DEFAULT_LEDGER_PATH)


def _weight_history_path() -> Path:
    return _resolve(os.environ.get("AQSP_WEIGHT_HISTORY_PATH", "").strip(), DEFAULT_WEIGHT_HISTORY_PATH)


def _trading_days_between(start: CalendarDate, end: CalendarDate) -> int:
    """start（不含）到 end（含）之间的交易日数。"""
    if end <= start:
        return 0
    count = 0
    cursor = start + timedelta(days=1)
    while cursor <= end:
        if is_trading_day(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def _freshness(rows: list[dict], path: Path) -> dict[str, Any]:
    """台账新鲜度。

    为什么必须有：**"正在积累"和"已经停止更新"是两件完全不同的事**。
    没有这个字段，流水线停了以后页面会永远显示"27/30 冷启动期"，
    用户会以为系统在正常攒样本 —— 那是误导，不是诚实。
    """
    dates = [str(row.get("signal_date") or "")[:10] for row in rows]
    dates = [text for text in dates if len(text) == 10]
    latest = max(dates) if dates else ""

    try:
        updated_at = to_shanghai(
            datetime.fromtimestamp(path.stat().st_mtime)
        ).strftime("%Y-%m-%d %H:%M")
    except OSError:
        updated_at = ""

    trading_days: int | None = None
    if latest:
        try:
            trading_days = _trading_days_between(
                CalendarDate.fromisoformat(latest), now_shanghai().date()
            )
        except ValueError:
            trading_days = None

    return {
        "latest_signal_date": latest,
        "ledger_updated_at": updated_at,
        "trading_days_since_latest": trading_days,
        "stale": trading_days is not None and trading_days > STALE_AFTER_TRADING_DAYS,
        "stale_after_trading_days": STALE_AFTER_TRADING_DAYS,
    }


def _freshness_unavailable() -> dict[str, Any]:
    return {
        "latest_signal_date": "",
        "ledger_updated_at": "",
        "trading_days_since_latest": None,
        "stale": False,
        "stale_after_trading_days": STALE_AFTER_TRADING_DAYS,
    }


def _unavailable(reason: str) -> dict[str, Any]:
    """台账不可用时的诚实返回。

    刻意**不抛异常**：新装环境没有台账是正常的，不该让前端显示"服务错误"。
    """
    return {
        "schema_version": PERFORMANCE_SCHEMA_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "available": False,
        "reason": reason,
        "cold_start": {
            "is_cold_start": True,
            "min_independent_signal_days": MIN_INDEPENDENT_SIGNAL_DAYS,
            # 字段名必须与正常分支一致：曾经这里是 max_independent_signal_days，
            # 与主返回的 independent_signal_days 不同名，前端会静默读到 0。
            "independent_signal_days": 0,
            "max_strategy_signal_days": 0,
        },
        "freshness": _freshness_unavailable(),
        "overall": None,
        "strategies": [],
        # 字段名必须与正常分支一致（与 cold_start 字段同理），前端按同一套键读取。
        "recent_picks": _recent_picks_unavailable(),
        "decay_alerts": [],
        "status_counts": {},
        "notes": [],
    }


def _return_series(df: pd.DataFrame) -> pd.Series:
    """取观测收益：优先超额收益（有基准时），否则绝对收益，统一成小数。"""
    if "excess_return_pct" in df.columns:
        raw = pd.to_numeric(df["excess_return_pct"], errors="coerce")
        fallback = pd.to_numeric(df.get("return_pct"), errors="coerce")
        values = raw.fillna(fallback)
    else:
        values = pd.to_numeric(df.get("return_pct"), errors="coerce")
    return values.fillna(0.0) / 100.0


def _settled_frame(rows: list[dict]) -> pd.DataFrame:
    """过滤出真正可用于统计的行：已结算(status=validated) 且非模拟。"""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if "status" not in df.columns:
        return df.iloc[0:0]
    status = df["status"].fillna("").astype(str).str.strip()
    keep = status == "validated"
    if "is_simulated" in df.columns:
        simulated = df["is_simulated"].fillna(False)
        if simulated.dtype == object:
            simulated = (
                simulated.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})
            )
        keep &= ~simulated.astype(bool)
    return df.loc[keep].copy()


def _recent_picks(settled: pd.DataFrame, limit: int = RECENT_PICKS_LIMIT) -> list[dict[str, Any]]:
    """票级复盘明细：最近 N 笔已结算（validated）信号，按 signal_date 新→旧。

    回答的是"上次我具体选了哪只票、事后结果如何"——这是策略级聚合回答不了的
    颗粒度。字段与台账行一一对应，不做任何二次计算（return_pct 直接取台账值）。

    §8 诚实边界：
    - 只读 validated 行（_settled_frame 已过滤 pending / not_executable / 模拟行），
      复盘表里看到的每一笔都是真实结算过的观测。
    - excess_return_pct 缺失时如实给 None，不回填 0 冒充"没跑赢基准"。
    - exit_reason 原样透传（horizon_close / take_profit / stop_loss），前端据此
      区分"到期了结"与"止盈止损触发"。
    """
    if settled.empty or "signal_date" not in settled.columns:
        return []
    work = settled.copy()
    work["signal_date"] = pd.to_datetime(work["signal_date"], errors="coerce")
    work = work.dropna(subset=["signal_date"])
    if work.empty:
        return []
    work = work.sort_values("signal_date", ascending=False).head(limit)

    picks: list[dict[str, Any]] = []
    for row in work.itertuples(index=False):
        excess = getattr(row, "excess_return_pct", None)
        return_pct = getattr(row, "return_pct", None)
        try:
            excess_val = None if pd.isna(excess) else round(float(excess), 4)
        except (TypeError, ValueError):
            excess_val = None
        try:
            return_val = None if pd.isna(return_pct) else round(float(return_pct), 4)
        except (TypeError, ValueError):
            return_val = None
        strategies_raw = getattr(row, "strategies", None)
        if strategies_raw is None:
            strategies: list[str] = []
        elif isinstance(strategies_raw, str):
            strategies = [s for s in strategies_raw.split(",") if s]
        else:
            strategies = [str(s) for s in strategies_raw]
        picks.append(
            {
                "symbol": str(getattr(row, "symbol", "") or ""),
                "name": str(getattr(row, "name", "") or ""),
                "signal_date": row.signal_date.strftime("%Y-%m-%d"),
                "exit_date": str(getattr(row, "exit_date", "") or "")[:10],
                "return_pct": return_val,
                "excess_return_pct": excess_val,
                "win": bool(getattr(row, "win", False)),
                "exit_reason": str(getattr(row, "exit_reason", "") or ""),
                "strategies": strategies,
            }
        )
    return picks


def _recent_picks_unavailable() -> list[dict[str, Any]]:
    return []


def _overall(df: pd.DataFrame) -> dict[str, Any] | None:
    """整体命中率 —— §5.2：同一 signal_date 的多个 pick 合成 1 个观察。"""
    if df.empty or "signal_date" not in df.columns:
        return None
    work = df.copy()
    work["return_decimal"] = _return_series(work)
    work["signal_date"] = pd.to_datetime(work["signal_date"], errors="coerce")
    work = work.dropna(subset=["signal_date"])
    if work.empty:
        return None

    by_day = work.groupby("signal_date")["return_decimal"].mean()
    wins = int((by_day > 0).sum())
    total = int(len(by_day))
    return {
        "observations": total,
        "win_count": wins,
        "hit_rate": round(wins / total, 4) if total else 0.0,
        # §5.4：整体冷启动按"独立信号日"判定，与策略级分开看
        "displayable": total >= MIN_INDEPENDENT_SIGNAL_DAYS,
    }


def performance_payload() -> dict[str, Any]:
    """汇总纸面交易台账的命中率与策略表现（只读）。"""
    path = _ledger_path()
    if not path.exists():
        return _unavailable(f"未找到台账文件：{path}")

    try:
        rows = read_ledger(path)
    except Exception as exc:  # noqa: BLE001 —— 边界统一兜底
        return _unavailable(f"台账读取失败：{exc}")

    if not rows:
        return _unavailable("台账为空，尚无已记录信号。")

    settled = _settled_frame(rows)
    status_counts = {
        str(row.get("status") or "unknown"): 0
        for row in rows
    }
    for row in rows:
        key = str(row.get("status") or "unknown")
        status_counts[key] = status_counts.get(key, 0) + 1

    if settled.empty:
        return {
            **_unavailable("台账中还没有已结算（validated）的信号。"),
            "available": True,
            "status_counts": status_counts,
        }

    # ---- 策略级：完全交给 learner，保证与权重学习同一口径 ----
    # 降级原因如实带出去：宁可让前端显示"策略级指标缺失"，也不要静默给出残缺数字
    degraded: list[str] = []
    performances: dict[str, Any] = {}
    try:
        # 构造也放进 try：缺权重历史目录 / 配置异常同样属于"绩效不可用"，
        # 不该让只读接口整体 500。
        learner = PerformanceLearner(
            config=LearnerConfig(),
            weight_history_path=_weight_history_path(),
        )
        performances = learner.learn_from_ledger(settled, record_history=False)
    except Exception as exc:  # noqa: BLE001 —— 边界统一兜底
        degraded.append(f"策略级学习器未完成：{exc}")
        performances = {}

    strategies: list[dict[str, Any]] = []
    max_days = 0
    for name, perf in sorted(performances.items()):
        recent = perf.recent_performance
        days = int(recent.independent_signal_days)
        max_days = max(max_days, days)
        strategies.append(
            {
                "name": name,
                "independent_signal_days": days,
                "total_picks": int(recent.total_picks),
                "win_count": int(recent.win_count),
                # 主指标：命中率（§8 允许）
                "hit_rate": round(float(recent.win_rate), 4),
                "displayable": days >= MIN_INDEPENDENT_SIGNAL_DAYS,
                "weight_base": round(float(perf.weights.get("base", 1.0)), 4),
                "weight_confidence": round(float(perf.weights.get("confidence", 0.0)), 4),
                # ↓ PnL 派生：仅观测/告警，禁止当作主指标（§8）
                "avg_return_pct": round(float(recent.avg_return) * 100, 4),
                "max_drawdown": round(float(recent.max_drawdown), 4),
                "sharpe_ratio": round(float(recent.sharpe_ratio), 4),
            }
        )

    overall = _overall(settled)
    # 整体冷启动按"独立信号日"数判定（§5.4），与策略级分别给 displayable
    total_signal_days = int(overall["observations"]) if overall else 0
    is_cold_start = total_signal_days < MIN_INDEPENDENT_SIGNAL_DAYS

    # ---- 衰减告警：PnL 派生指标只允许出现在这里（观测/告警）----
    try:
        alerts = StrategyDecayDetector().detect(settled)
        decay_alerts = [
            {
                "strategy": str(alert.strategy_name),
                "lookback_days": int(alert.lookback_days),
                "decay_days": int(alert.decay_days),
                "recent_win_rate": round(float(alert.recent_win_rate), 4),
                # PnL 派生，仅用于告警（§8）
                "recent_avg_return_pct": round(float(alert.recent_avg_return) * 100, 4),
                "severity": str(alert.severity),
                "recommendation": str(alert.recommendation),
            }
            for alert in alerts
        ]
    except Exception as exc:  # noqa: BLE001
        degraded.append(f"衰减检测未完成：{exc}")
        decay_alerts = []

    notes: list[str] = [
        "命中率 = 观测收益为正的比例；整体按 signal_date 聚合成 1 个观察（§5.2）。",
        f"最新信号日之后超过 {STALE_AFTER_TRADING_DAYS} 个交易日未更新即视为停滞（按交易日历，长假不算停滞）。",
        "not_executable 记录不计入胜率（§5.3）。",
        f"独立信号日 < {MIN_INDEPENDENT_SIGNAL_DAYS} 时不展示胜率（§5.4 冷启动期）。",
        "avg_return / sharpe / max_drawdown 为 PnL 派生指标，仅作观测与告警，不作为主指标（§8）。",
        f"recent_picks 为最近 {RECENT_PICKS_LIMIT} 笔已结算（validated）信号的票级复盘明细，"
        "仅展示台账原值，不参与任何统计口径。",
    ]
    notes.extend(degraded)

    return {
        "schema_version": PERFORMANCE_SCHEMA_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "available": True,
        "reason": "",
        "cold_start": {
            "is_cold_start": is_cold_start,
            "min_independent_signal_days": MIN_INDEPENDENT_SIGNAL_DAYS,
            "independent_signal_days": total_signal_days,
            "max_strategy_signal_days": max_days,
        },
        "freshness": _freshness(rows, path),
        "overall": overall,
        "strategies": strategies,
        # 票级复盘明细：回答"上次具体选了哪只票、事后如何"，与策略级聚合并列。
        # 数据全部取自 settled（validated 且非模拟行），只读、不落任何计算。
        "recent_picks": _recent_picks(settled),
        "decay_alerts": decay_alerts,
        "status_counts": status_counts,
        "notes": notes,
    }
