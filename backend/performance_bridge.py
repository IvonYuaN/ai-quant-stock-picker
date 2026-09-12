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
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

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

# 与 LearnerConfig 保持一致；这里显式写出，避免前端拿不到常量时无法解释"为什么是 30"
MIN_INDEPENDENT_SIGNAL_DAYS = LearnerConfig().min_independent_signal_days


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
            "max_independent_signal_days": 0,
        },
        "overall": None,
        "strategies": [],
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
        "not_executable 记录不计入胜率（§5.3）。",
        f"独立信号日 < {MIN_INDEPENDENT_SIGNAL_DAYS} 时不展示胜率（§5.4 冷启动期）。",
        "avg_return / sharpe / max_drawdown 为 PnL 派生指标，仅作观测与告警，不作为主指标（§8）。",
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
        "overall": overall,
        "strategies": strategies,
        "decay_alerts": decay_alerts,
        "status_counts": status_counts,
        "notes": notes,
    }
