"""回测报告统计：纯计算模块（无 IO、无网络、不写未来数据）。

定位
----
把回测产物里的净值曲线、逐笔交易、基准序列换算成可复盘的统计 dict 与
CSV 字符串。本模块只做确定性计算，不读生产库、不落盘、不触发回测、不碰评分。

输入契约（与 src/aqsp/backtest/strategy_portfolio.py 的产物对齐）
---------------------------------------------------------------
- equity_curve: [{"date": "YYYY-MM-DD", "equity": 正数float}, ...]
  严格按交易日单调递增；即 PortfolioReplayResult.equity_curve 的 date/equity 键。
- benchmark:    与 equity_curve 同构的净值序列；None 或空表示无基准。
- trades:       [{"symbol": str, "pnl_amount": float,
                  "entry_date": "YYYY-MM-DD", "exit_date": "YYYY-MM-DD"}, ...]
  仅接受已平仓交易；pnl_amount 正为盈、负为亏，对应 PortfolioReplayTrade.pnl_amount。

单一事实源（Sharpe / 回撤 / 年化口径）
------------------------------------
仓库里已有的实现有三处，本模块选定并复刻其口径，不另造一套算法：
- src/aqsp/backtest/strategy_portfolio.py:516-521（run_portfolio_replay 内联）：
  日收益率 r = diff(equity) / equity[:-1]；Sharpe = mean(r) / std(r, ddof=1) * sqrt(252)，
  且波动为 0 时返回 None（本身就是 fail-closed）；回撤 = (equity - peak) / peak（负值），
  最大回撤取 min。这是 PortfolioReplayResult 自身的 Sharpe/回撤算法，与本模块
  消费的是同一份 equity_curve，语义上是最贴近的单一事实源。
- src/aqsp/backtest/walk_forward.py:1575（_compute_aggregate_metrics）：
  年化 = (1 + 总收益) ** (252 / 收益点数) - 1。
- 该文件的 1577 行与 src/aqsp/ledger/learner.py:279-280 用等价的正幅度回撤
  1 - equity / running_max，只是符号相反；本模块为与产物 drawdown_pct 对齐取负值。

为什么不能直接 import 复用：上述计算都内联在大函数或私有方法里，没有可独立
调用的纯函数；walk_forward._sample_sharpe_ratio 虽是模块级函数，但属于私有 API、
所在模块会拉起 numpy/pandas/scipy 与整套 aqsp 运行时（含配置/文件读取），且它是
fail-open 的（样本不足或标准差为 0 时静默返回 0.0）。直接引入会破坏本模块
纯计算、无 IO 与 fail-closed 三条约束。因此这里用标准库按同一口径复刻，
并补上严格的输入校验。

fail-closed
-----------
样本不足 / 空输入 / 净值或盈亏含 NaN 或 inf / 净值非正 / 日期非法或非单调 /
交易日期缺失或倒挂 -> 抛 StatsInputError。基准缺失 / 长度不匹配 / 日期不对齐 /
基准脏数据 -> benchmark_comparison 返回 status=unavailable，所有统计字段为 None，
绝不静默补 0、1.0 或 False。盈亏比总亏损为 0 时返回 None 并标注 status，
不返回假数。
"""
from __future__ import annotations

import csv
import io
import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

# 与 strategy_portfolio / walk_forward / learner 一致的年化交易日数。
TRADING_DAYS_PER_YEAR = 252
# 至少 3 个净值点才能产生 2 个日收益率；ddof=1 的样本标准差至少要 2 个点。
MIN_EQUITY_POINTS = 3


class StatsInputError(ValueError):
    """输入不满足 fail-closed 约束时抛出的明确异常。"""


def _as_float(value: Any, *, label: str, index: int) -> float:
    """把一个输入值转成有限 float；NaN / inf / 布尔 / 非数值一律明确拒绝。"""
    if isinstance(value, bool):
        raise StatsInputError(f"{label}[{index}] 不能是布尔值")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StatsInputError(f"{label}[{index}] 不是数值：{value!r}") from exc
    if not math.isfinite(number):
        raise StatsInputError(f"{label}[{index}] 含 NaN 或 inf")
    return number


def _normalize_equity(
    points: Any, *, label: str = "equity_curve"
) -> tuple[list[str], list[float]]:
    """校验并规整净值序列，返回 (日期列表, 净值列表)。任何脏数据都抛异常。"""
    if points is None:
        raise StatsInputError(f"{label} 为空")
    seq = list(points)
    if len(seq) < MIN_EQUITY_POINTS:
        raise StatsInputError(
            f"{label} 样本不足：至少需要 {MIN_EQUITY_POINTS} 个净值点，实际 {len(seq)} 个"
        )
    dates: list[str] = []
    equities: list[float] = []
    previous: date | None = None
    for index, point in enumerate(seq):
        if not isinstance(point, Mapping):
            raise StatsInputError(f"{label}[{index}] 必须是含 date/equity 的对象")
        raw_date = point.get("date")
        if not isinstance(raw_date, str) or not raw_date:
            raise StatsInputError(f"{label}[{index}].date 缺失或非字符串")
        try:
            day = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise StatsInputError(
                f"{label}[{index}].date 不是合法 YYYY-MM-DD：{raw_date}"
            ) from exc
        if day.isoformat() != raw_date:
            raise StatsInputError(
                f"{label}[{index}].date 必须是标准 YYYY-MM-DD：{raw_date}"
            )
        if previous is not None and day <= previous:
            raise StatsInputError(
                f"{label} 日期非单调递增：{raw_date} 未晚于 {previous.isoformat()}"
            )
        previous = day
        if "equity" not in point:
            raise StatsInputError(f"{label}[{index}].equity 缺失")
        equity = _as_float(point["equity"], label=f"{label}.equity", index=index)
        if equity <= 0:
            raise StatsInputError(f"{label}[{index}].equity 必须为正数")
        dates.append(raw_date)
        equities.append(equity)
    return dates, equities


def _daily_returns(equities: Sequence[float]) -> list[float]:
    """由净值曲线得到逐日简单收益率，口径同 strategy_portfolio.py:517。"""
    return [
        equities[index] / equities[index - 1] - 1.0
        for index in range(1, len(equities))
    ]


def _sharpe(returns: Sequence[float]) -> float | None:
    """年化 Sharpe：mean / std(ddof=1) * sqrt(252)。

    口径来自 strategy_portfolio.py:518-519。标准差为 0 或样本不足时返回 None，
    绝不静默补 0（这是 fail-closed，不是该模块的 fail-open 0.0 分支）。
    """
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0.0:
        return None
    return mean / std * math.sqrt(TRADING_DAYS_PER_YEAR)


def _drawdown_series(equities: Sequence[float]) -> list[float]:
    """逐日回撤（负值或 0）：equity / running_peak - 1。

    与 strategy_portfolio.py:480 的 (equity - peak) / peak 完全等价（负值口径），
    -0.15 表示相对历史高点回撤 15%。
    """
    peak = equities[0]
    result: list[float] = []
    for equity in equities:
        peak = max(peak, equity)
        result.append(equity / peak - 1.0)
    return result


def _normalize_trades(trades: Any) -> list[dict[str, Any]]:
    """校验逐笔交易，返回规整后的列表。缺失/非法/含脏数据一律抛异常。"""
    seq = list(trades) if trades is not None else []
    cleaned: list[dict[str, Any]] = []
    for index, trade in enumerate(seq):
        if not isinstance(trade, Mapping):
            raise StatsInputError(f"trades[{index}] 必须是对象")
        symbol = trade.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise StatsInputError(f"trades[{index}].symbol 缺失或非字符串")
        if "pnl_amount" not in trade:
            raise StatsInputError(f"trades[{index}].pnl_amount 缺失")
        pnl = _as_float(
            trade["pnl_amount"], label="trades.pnl_amount", index=index
        )
        hold_days = _hold_days(trade, index=index)
        cleaned.append(
            {"symbol": symbol, "pnl_amount": pnl, "hold_days": hold_days}
        )
    return cleaned


def _hold_days(trade: Mapping[str, Any], *, index: int) -> int:
    """由 entry_date / exit_date 计算持有天数（自然日）。"""
    entry = trade.get("entry_date")
    exit_day = trade.get("exit_date")
    if not isinstance(entry, str) or not isinstance(exit_day, str):
        raise StatsInputError(f"trades[{index}] 缺少 entry_date/exit_date")
    try:
        start = date.fromisoformat(entry)
        end = date.fromisoformat(exit_day)
    except ValueError as exc:
        raise StatsInputError(
            f"trades[{index}] 的 entry_date/exit_date 必须是 YYYY-MM-DD"
        ) from exc
    days = (end - start).days
    if days < 0:
        raise StatsInputError(f"trades[{index}] 的 exit_date 早于 entry_date")
    return days


def _trade_stats(trades: Any) -> dict[str, Any]:
    """逐笔交易层面的胜率与盈亏比（不依赖净值曲线）。"""
    cleaned = _normalize_trades(trades)
    count = len(cleaned)
    if count == 0:
        return {
            "trade_count": 0,
            "win_rate": None,
            "profit_factor": None,
            "profit_factor_status": "no_trades",
        }
    pnls = [item["pnl_amount"] for item in cleaned]
    wins = sum(1 for value in pnls if value > 0)
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = sum(value for value in pnls if value < 0)
    if gross_loss == 0.0:
        # 分母为 0：盈亏比数学上无定义，返回 None 并标注原因，绝不补 0/1.0/False。
        profit_factor = None
        status = "no_losses" if gross_profit > 0 else "no_nonzero_pnl"
    else:
        profit_factor = round(gross_profit / abs(gross_loss), 6)
        status = "ok"
    return {
        "trade_count": count,
        "win_rate": round(wins / count, 6),
        "profit_factor": profit_factor,
        "profit_factor_status": status,
    }


def summary(equity_curve: Any, trades: Any) -> dict[str, Any]:
    """汇总统计：总收益、年化、Sharpe、最大回撤、盈亏比、胜率、交易数。

    返回键：total_return / annual_return / sharpe_ratio / max_drawdown /
    profit_factor / profit_factor_status / win_rate / trade_count。
    收益为小数；max_drawdown 为负值幅度；样本不足或脏数据抛 StatsInputError。
    """
    _, equities = _normalize_equity(equity_curve)
    returns = _daily_returns(equities)
    total_return = equities[-1] / equities[0] - 1.0
    annual_return = (equities[-1] / equities[0]) ** (
        TRADING_DAYS_PER_YEAR / len(returns)
    ) - 1.0
    sharpe = _sharpe(returns)
    max_drawdown = min(_drawdown_series(equities))
    trade_stats = _trade_stats(trades)
    return {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6),
        "sharpe_ratio": None if sharpe is None else round(sharpe, 6),
        "max_drawdown": round(max_drawdown, 6),
        "profit_factor": trade_stats["profit_factor"],
        "profit_factor_status": trade_stats["profit_factor_status"],
        "win_rate": trade_stats["win_rate"],
        "trade_count": trade_stats["trade_count"],
    }


def _unavailable(reason: str) -> dict[str, Any]:
    """基准对比不可用时的诚实返回：所有统计字段为 None，绝不返回 0。"""
    return {
        "status": "unavailable",
        "reason": reason,
        "aligned_points": None,
        "excess_return": None,
        "information_ratio": None,
        "relative_drawdown": None,
    }


def benchmark_comparison(equity_curve: Any, benchmark: Any) -> dict[str, Any]:
    """与基准按日期严格对齐后的超额收益、信息比、相对回撤。

    对齐采取最严格口径：要求基准与净值序列长度相同且逐日日期完全一致，
    不静默截断、重索引或前向填充。基准缺失 / 长度不匹配 / 日期不对齐 /
    基准脏数据 -> status=unavailable 且所有统计字段为 None。
    主净值曲线脏数据仍抛 StatsInputError（主数据问题不降级）。
    """
    dates, equities = _normalize_equity(equity_curve)
    if benchmark is None:
        return _unavailable("benchmark_missing")
    raw_benchmark = list(benchmark)
    if not raw_benchmark:
        return _unavailable("benchmark_missing")
    if len(raw_benchmark) != len(equities):
        return _unavailable(
            f"length_mismatch: benchmark={len(raw_benchmark)} equity={len(equities)}"
        )
    try:
        bench_dates, bench_equities = _normalize_equity(
            raw_benchmark, label="benchmark"
        )
    except StatsInputError as exc:
        return _unavailable(f"benchmark_invalid: {exc}")
    if bench_dates != dates:
        return _unavailable("date_misaligned")

    strategy_returns = _daily_returns(equities)
    benchmark_returns = _daily_returns(bench_equities)
    active_returns = [
        left - right
        for left, right in zip(strategy_returns, benchmark_returns)
    ]
    excess_return = (equities[-1] / equities[0] - 1.0) - (
        bench_equities[-1] / bench_equities[0] - 1.0
    )
    information_ratio = _sharpe(active_returns)
    # 相对净值 = 策略归一化净值 / 基准归一化净值；其回撤即相对回撤。
    relative_equity = [
        (equities[index] / equities[0]) / (bench_equities[index] / bench_equities[0])
        for index in range(len(equities))
    ]
    relative_drawdown = min(_drawdown_series(relative_equity))
    return {
        "status": "ok",
        "reason": None,
        "aligned_points": len(equities),
        "excess_return": round(excess_return, 6),
        "information_ratio": (
            None if information_ratio is None else round(information_ratio, 6)
        ),
        "relative_drawdown": round(relative_drawdown, 6),
    }


def per_symbol_stats(trades: Any) -> list[dict[str, Any]]:
    """逐标的统计：交易数、胜率、累计盈亏、平均持有天数。"""
    cleaned = _normalize_trades(trades)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in cleaned:
        grouped.setdefault(item["symbol"], []).append(item)
    result: list[dict[str, Any]] = []
    for symbol in sorted(grouped):
        items = grouped[symbol]
        pnls = [item["pnl_amount"] for item in items]
        wins = sum(1 for value in pnls if value > 0)
        result.append(
            {
                "symbol": symbol,
                "trade_count": len(items),
                "win_rate": round(wins / len(items), 6),
                "cumulative_pnl": round(sum(pnls), 6),
                "avg_hold_days": round(
                    sum(item["hold_days"] for item in items) / len(items), 6
                ),
            }
        )
    return result


def monthly_returns(equity_curve: Any) -> list[dict[str, Any]]:
    """按月聚合净值：每月收益 = 该月最后一个净值 / 该月第一个净值 - 1。"""
    dates, equities = _normalize_equity(equity_curve)
    months: dict[str, list[float]] = {}
    for day, equity in zip(dates, equities):
        key = day[:7]
        if key not in months:
            months[key] = [equity, equity]
        else:
            months[key][1] = equity
    return [
        {
            "month": key,
            "return": round(values[1] / values[0] - 1.0, 6),
            "start_equity": values[0],
            "end_equity": values[1],
        }
        for key, values in months.items()
    ]


def drawdown_series(equity_curve: Any) -> list[dict[str, Any]]:
    """逐日回撤序列：每项含 date / equity / drawdown（负值或 0）。"""
    dates, equities = _normalize_equity(equity_curve)
    drawdowns = _drawdown_series(equities)
    return [
        {
            "date": day,
            "equity": equity,
            "drawdown": round(value, 6),
        }
        for day, equity, value in zip(dates, equities, drawdowns)
    ]


def to_csv(equity_curve: Any) -> str:
    """逐日净值 + 回撤导出为 CSV 字符串；表头 date,equity,drawdown。"""
    points = drawdown_series(equity_curve)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["date", "equity", "drawdown"])
    for point in points:
        writer.writerow(
            [point["date"], point["equity"], point["drawdown"]]
        )
    return buffer.getvalue()
