"""回测报告统计只读 API（APIRouter，路由由主 agent 统一接线）。

只读边界
--------
- 只有一个 POST 计算端点和一个 GET 契约端点；请求体经 pydantic 校验。
- 不写文件、不读生产库、不触发回测、不碰评分；所有计算委托给纯函数模块
  backtest_report_stats.py。
- 输入不满足 fail-closed 约束时返回 HTTP 422（绝不静默补默认值）。

本模块刻意不 import backend/app.py，router 可独立挂载；这样既能被主应用
include，也能在测试里用最小 FastAPI 实例直接挂载验证契约。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import backtest_report_stats as stats

router = APIRouter(prefix="/api/lab/report-stats", tags=["回测报告"])


class EquityPoint(BaseModel):
    date: str = Field(..., description="交易日，格式 YYYY-MM-DD")
    equity: float = Field(..., description="当日净值，必须为正的有限数")


class Trade(BaseModel):
    symbol: str = Field(..., description="标的标识")
    pnl_amount: float = Field(..., description="该笔已实现盈亏金额，正为盈负为亏")
    entry_date: str = Field(..., description="入场日期 YYYY-MM-DD")
    exit_date: str = Field(..., description="出场日期 YYYY-MM-DD")


class ReportStatsRequest(BaseModel):
    equity_curve: list[EquityPoint] = Field(
        ..., description="逐日净值曲线，日期严格单调递增"
    )
    trades: list[Trade] = Field(
        default_factory=list, description="逐笔已平仓交易；可为空"
    )
    benchmark: list[EquityPoint] | None = Field(
        default=None, description="基准净值曲线；需与 equity_curve 严格按日期对齐"
    )


CONTRACT: dict[str, Any] = {
    "prefix": "/api/lab/report-stats",
    "read_only": True,
    "description": "回测报告统计只读计算端点；不写文件、不读生产库、不触发回测、不碰评分。",
    "endpoints": [
        {
            "method": "POST",
            "path": "/compute",
            "description": "对请求体做只读统计，返回各统计 dict 与净值 CSV 字符串",
        },
        {
            "method": "GET",
            "path": "/contract",
            "description": "返回本契约说明（输入字段、输出项、口径与 fail-closed 规则）",
        },
    ],
    "request_fields": {
        "equity_curve": "必填，[{date, equity}, ...]，净值须为正且有限，日期严格递增",
        "trades": "可选，[{symbol, pnl_amount, entry_date, exit_date}, ...]，仅已平仓交易",
        "benchmark": "可选，与 equity_curve 同构；缺失或不对齐时返回 unavailable",
    },
    "response_keys": [
        "summary",
        "benchmark_comparison",
        "per_symbol_stats",
        "monthly_returns",
        "drawdown_series",
        "equity_csv",
    ],
    "metric_sources": [
        "Sharpe / 回撤口径来自 src/aqsp/backtest/strategy_portfolio.py:516-521",
        "年化口径来自 src/aqsp/backtest/walk_forward.py:1575",
    ],
    "fail_closed": [
        "样本不足、空输入、NaN/inf、净值非正、日期非法或非单调 -> HTTP 422",
        "基准缺失、长度不匹配、日期不对齐 -> status=unavailable 且统计字段为 None",
        "盈亏比总亏损为 0 -> profit_factor=None 并标注 profit_factor_status",
    ],
}


@router.post("/compute")
def compute(request: ReportStatsRequest) -> dict[str, Any]:
    """只读计算：汇总全部统计并返回 dict。"""
    payload = request.model_dump()
    equity_curve = payload["equity_curve"]
    trades = payload["trades"]
    benchmark = payload["benchmark"]
    try:
        return {
            "summary": stats.summary(equity_curve, trades),
            "benchmark_comparison": stats.benchmark_comparison(
                equity_curve, benchmark
            ),
            "per_symbol_stats": stats.per_symbol_stats(trades),
            "monthly_returns": stats.monthly_returns(equity_curve),
            "drawdown_series": stats.drawdown_series(equity_curve),
            "equity_csv": stats.to_csv(equity_curve),
        }
    except stats.StatsInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/contract")
def contract() -> dict[str, Any]:
    """返回请求/响应契约与口径说明。"""
    return CONTRACT
