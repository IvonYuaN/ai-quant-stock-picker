"""回测报告统计：纯函数 fail-closed 与只读 API 契约测试。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import backtest_report_stats as stats
import backtest_report_stats_api as api

PREFIX = "/api/aqsp/report-stats"


def _equity(values: list[float], start: str = "2026-01-05") -> list[dict]:
    """从净值列表构造日期严格递增的 equity_curve。"""
    day = date.fromisoformat(start)
    points = []
    for value in values:
        points.append({"date": day.isoformat(), "equity": value})
        day += timedelta(days=1)
    return points


def _trades() -> list[dict]:
    return [
        {
            "symbol": "600519", "pnl_amount": 100.0,
            "entry_date": "2026-01-05", "exit_date": "2026-01-08",
        },
        {
            "symbol": "600519", "pnl_amount": -50.0,
            "entry_date": "2026-01-09", "exit_date": "2026-01-12",
        },
        {
            "symbol": "000001", "pnl_amount": 20.0,
            "entry_date": "2026-01-06", "exit_date": "2026-01-07",
        },
    ]


def _body(values: list[float], *, with_benchmark: bool = False) -> dict:
    body = {
        "equity_curve": _equity(values),
        "trades": _trades(),
    }
    if with_benchmark:
        body["benchmark"] = _equity([1.0, 1.02, 1.01, 1.03, 1.04, 1.05])
    return body


@pytest.fixture(scope="module")
def client() -> TestClient:
    """把 router 挂到最小 FastAPI 实例，不依赖 backend/app.py。"""
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# 纯函数：正常路径
# ---------------------------------------------------------------------------

def test_summary_normal_path() -> None:
    result = stats.summary(_equity([1.0, 1.1, 1.05, 1.2, 1.15, 1.3]), _trades())
    assert result["total_return"] == pytest.approx(0.3, abs=1e-6)
    assert result["annual_return"] > 0
    assert result["sharpe_ratio"] is not None
    assert result["max_drawdown"] <= 0
    assert result["trade_count"] == 3
    assert result["win_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert result["profit_factor"] == pytest.approx(2.4, abs=1e-6)  # 120 / 50
    assert result["profit_factor_status"] == "ok"


def test_benchmark_normal_path() -> None:
    curve = _equity([1.0, 1.1, 1.05, 1.2])
    bench = _equity([1.0, 1.02, 1.01, 1.03], start="2026-01-05")
    result = stats.benchmark_comparison(curve, bench)
    assert result["status"] == "ok"
    assert result["aligned_points"] == 4
    assert result["excess_return"] == pytest.approx(0.17, abs=1e-6)  # 0.20 - 0.03
    assert result["information_ratio"] is not None
    assert result["relative_drawdown"] <= 0


def test_per_symbol_stats() -> None:
    by_symbol = {item["symbol"]: item for item in stats.per_symbol_stats(_trades())}
    assert set(by_symbol) == {"600519", "000001"}
    first = by_symbol["600519"]
    assert first["trade_count"] == 2
    assert first["win_rate"] == pytest.approx(0.5, abs=1e-6)
    assert first["cumulative_pnl"] == pytest.approx(50.0, abs=1e-6)
    assert first["avg_hold_days"] == pytest.approx(3.0, abs=1e-6)
    second = by_symbol["000001"]
    assert second["trade_count"] == 1
    assert second["avg_hold_days"] == pytest.approx(1.0, abs=1e-6)


def test_monthly_returns() -> None:
    curve = [
        {"date": "2026-01-05", "equity": 1.0},
        {"date": "2026-01-20", "equity": 1.1},
        {"date": "2026-02-03", "equity": 1.05},
        {"date": "2026-02-18", "equity": 1.2},
    ]
    result = stats.monthly_returns(curve)
    assert [item["month"] for item in result] == ["2026-01", "2026-02"]
    assert result[0]["return"] == pytest.approx(0.1, abs=1e-6)
    assert result[1]["return"] == pytest.approx(1.2 / 1.05 - 1, abs=1e-6)


def test_drawdown_series() -> None:
    result = stats.drawdown_series(_equity([1.0, 1.2, 1.1, 1.3]))
    assert len(result) == 4
    assert result[0]["drawdown"] == 0.0
    assert all(item["drawdown"] <= 0 for item in result)
    assert result[2]["drawdown"] == pytest.approx(1.1 / 1.2 - 1, abs=1e-6)


def test_csv_header_and_row_count() -> None:
    curve = _equity([1.0, 1.1, 1.05, 1.2, 1.15])
    lines = stats.to_csv(curve).splitlines()
    assert lines[0] == "date,equity,drawdown"
    assert len(lines) == len(curve) + 1
    assert lines[1].split(",")[0] == curve[0]["date"]


# ---------------------------------------------------------------------------
# 纯函数：fail-closed
# ---------------------------------------------------------------------------

def test_empty_input_raises() -> None:
    with pytest.raises(stats.StatsInputError):
        stats.summary([], [])
    with pytest.raises(stats.StatsInputError):
        stats.to_csv(None)


def test_insufficient_samples_raises() -> None:
    with pytest.raises(stats.StatsInputError, match="样本不足"):
        stats.summary(_equity([1.0, 1.1]), [])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_inf_in_equity_raise(bad: float) -> None:
    curve = _equity([1.0, 1.1, 1.2])
    curve[1]["equity"] = bad
    with pytest.raises(stats.StatsInputError):
        stats.summary(curve, [])


@pytest.mark.parametrize(
    "dates",
    [
        ["2026-01-05", "2026-01-04", "2026-01-06"],  # 递减
        ["2026-01-05", "2026-01-05", "2026-01-06"],  # 重复
    ],
)
def test_dates_not_monotonic_raise(dates: list[str]) -> None:
    curve = [
        {"date": day, "equity": 1.0 + 0.01 * index}
        for index, day in enumerate(dates)
    ]
    with pytest.raises(stats.StatsInputError, match="单调递增"):
        stats.summary(curve, [])


def test_benchmark_missing_unavailable() -> None:
    result = stats.benchmark_comparison(_equity([1.0, 1.1, 1.2, 1.15]), None)
    assert result["status"] == "unavailable"
    assert result["reason"] == "benchmark_missing"
    for key in (
        "excess_return", "information_ratio", "relative_drawdown", "aligned_points"
    ):
        assert result[key] is None  # 明确不可用，绝不返回 0


def test_benchmark_length_mismatch_unavailable() -> None:
    result = stats.benchmark_comparison(
        _equity([1.0, 1.1, 1.2, 1.15]), _equity([1.0, 1.05, 1.1])
    )
    assert result["status"] == "unavailable"
    assert "length_mismatch" in result["reason"]
    assert result["excess_return"] is None


def test_benchmark_date_misaligned_unavailable() -> None:
    bench = _equity([1.0, 1.05, 1.1, 1.08], start="2026-02-01")
    result = stats.benchmark_comparison(_equity([1.0, 1.1, 1.2, 1.15]), bench)
    assert result["status"] == "unavailable"
    assert "date_misaligned" in result["reason"]


def test_profit_factor_zero_denominator() -> None:
    all_wins = [
        {
            "symbol": "600519", "pnl_amount": 10.0,
            "entry_date": "2026-01-05", "exit_date": "2026-01-06",
        },
        {
            "symbol": "000001", "pnl_amount": 5.0,
            "entry_date": "2026-01-06", "exit_date": "2026-01-07",
        },
    ]
    result = stats.summary(_equity([1.0, 1.1, 1.2]), all_wins)
    assert result["profit_factor"] is None  # 不是 0 / 1.0 / False
    assert result["profit_factor"] is not False
    assert result["profit_factor_status"] == "no_losses"


def test_no_trades_status() -> None:
    result = stats.summary(_equity([1.0, 1.1, 1.2]), [])
    assert result["trade_count"] == 0
    assert result["win_rate"] is None
    assert result["profit_factor"] is None
    assert result["profit_factor_status"] == "no_trades"


def test_trade_with_nan_pnl_raises() -> None:
    trades = [
        {
            "symbol": "600519", "pnl_amount": float("nan"),
            "entry_date": "2026-01-05", "exit_date": "2026-01-06",
        },
    ]
    with pytest.raises(stats.StatsInputError):
        stats.summary(_equity([1.0, 1.1, 1.2]), trades)


# ---------------------------------------------------------------------------
# API 只读端点契约
# ---------------------------------------------------------------------------

def test_api_post_compute_returns_all_keys(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/compute", json=_body([1.0, 1.1, 1.05, 1.2, 1.15, 1.3])
    )
    assert response.status_code == 200
    data = response.json()
    for key in (
        "summary", "benchmark_comparison", "per_symbol_stats",
        "monthly_returns", "drawdown_series", "equity_csv",
    ):
        assert key in data
    assert data["summary"]["trade_count"] == 3
    assert data["equity_csv"].splitlines()[0] == "date,equity,drawdown"


def test_api_post_with_benchmark_ok(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/compute", json=_body([1.0, 1.1, 1.05, 1.2, 1.15, 1.3], with_benchmark=True)
    )
    assert response.status_code == 200
    assert response.json()["benchmark_comparison"]["status"] == "ok"


def test_api_post_without_benchmark_unavailable(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/compute", json=_body([1.0, 1.1, 1.05, 1.2]))
    assert response.status_code == 200
    assert response.json()["benchmark_comparison"]["status"] == "unavailable"


def test_api_get_contract(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/contract")
    assert response.status_code == 200
    data = response.json()
    assert data["prefix"] == PREFIX
    assert data["read_only"] is True
    paths = [item["path"] for item in data["endpoints"]]
    assert "/compute" in paths and "/contract" in paths


def test_api_rejects_insufficient_samples_422(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/compute", json=_body([1.0, 1.1]))
    assert response.status_code == 422


def test_api_rejects_nan_422(client: TestClient) -> None:
    # httpx 在客户端禁用裸 NaN 的 JSON 序列化，用字符串 "NaN" 让 pydantic 转成
    # float nan，再由纯计算层 fail-closed 校验拦下并返回 422。
    body = _body([1.0, 1.1, 1.2])
    body["equity_curve"][1]["equity"] = "NaN"
    response = client.post(f"{PREFIX}/compute", json=body)
    assert response.status_code == 422


def test_api_compute_direct_nan_raises_422() -> None:
    """直接调 router 函数：裸 float nan 也应被 fail-closed 拦成 HTTPException 422。"""
    request = api.ReportStatsRequest(
        equity_curve=[
            {"date": "2026-01-05", "equity": 1.0},
            {"date": "2026-01-06", "equity": float("nan")},
            {"date": "2026-01-07", "equity": 1.2},
        ],
        trades=[],
    )
    with pytest.raises(HTTPException) as exc_info:
        api.compute(request)
    assert exc_info.value.status_code == 422


def test_api_pydantic_rejects_missing_equity_curve_422(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/compute", json={"trades": []})
    assert response.status_code == 422
