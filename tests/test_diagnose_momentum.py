from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from scripts import diagnose_momentum
from scripts.diagnose_momentum import (
    build_parser,
    classify_conclusion,
    compute_rolling_scores,
    conclusion_lines,
    fetch_data,
    quantile_table,
    run_analysis,
    spearman_status_line,
    split_by_regime,
)


def _make_ohlcv(n: int, trend: float = 0.0, seed: int = 42) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    close = 10.0 + np.arange(n) * trend + rng.randn(n) * 0.3
    close = np.maximum(close, 1.0)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": close,
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "volume": rng.randint(100000, 1000000, n).astype(float),
        }
    )


def test_spearman_sign_flip() -> None:
    rng = np.random.RandomState(0)
    n = 200
    scores = rng.uniform(0, 1, n)
    forward_ret = -scores * 0.1 + rng.randn(n) * 0.02
    rho, _ = spearmanr(scores, forward_ret)
    assert rho < -0.05, f"expected negative rho, got {rho}"


def test_quantile_bucket_monotonic() -> None:
    rng = np.random.RandomState(1)
    n = 500
    scores = rng.uniform(0, 1, n)
    forward_ret = scores * 0.2 + rng.randn(n) * 0.05
    df = pd.DataFrame({"score": scores, "forward_ret": forward_ret})
    qt = quantile_table(df)
    assert len(qt) >= 2
    q1_mean = qt.iloc[0]["mean"]
    q5_mean = qt.iloc[-1]["mean"]
    assert str(qt.iloc[0]["q"]).startswith("Q")
    assert str(qt.iloc[-1]["q"]).startswith("Q")
    assert q5_mean > q1_mean, f"Q5 ({q5_mean:.4f}) should be > Q1 ({q1_mean:.4f})"


def test_quantile_table_handles_constant_scores() -> None:
    qt = quantile_table(pd.DataFrame({"score": [0.5] * 10, "forward_ret": [0.01] * 10}))

    assert qt.empty
    assert list(qt.columns) == ["q", "mean", "std", "count"]


def test_quantile_table_drops_non_finite_rows() -> None:
    qt = quantile_table(
        pd.DataFrame(
            {
                "score": [0.1, 0.2, np.nan, np.inf, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
                "forward_ret": [
                    0.01,
                    0.02,
                    0.03,
                    0.04,
                    np.nan,
                    0.06,
                    0.07,
                    0.08,
                    0.09,
                    np.inf,
                ],
            }
        )
    )

    assert int(qt["count"].sum()) == 6


def test_run_analysis_drops_non_finite_rows_before_statistics() -> None:
    df = pd.DataFrame(
        {
            "score": [0.1, 0.2, np.nan, np.inf, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1],
            "forward_ret": [
                0.01,
                0.02,
                0.03,
                0.04,
                np.nan,
                0.06,
                0.07,
                0.08,
                0.09,
                np.inf,
                0.11,
            ],
        }
    )

    result = run_analysis(df, "cleaned")

    assert result["n"] == 7
    assert result["error"] == "样本不足"


def test_run_analysis_reports_schema_missing_when_required_columns_absent() -> None:
    result = run_analysis(pd.DataFrame({"score": [0.1] * 20}), "missing")

    assert result["n"] == 20
    assert result["error"] == "schema missing"


def test_split_by_regime_handles_empty_scores() -> None:
    assert split_by_regime(pd.DataFrame()) == {}


def test_handles_suspended_stock() -> None:
    from unittest.mock import MagicMock

    strategy = MagicMock()
    strategy.thresholds.momentum.lookback_days = 60

    def fake_score(data: dict[str, pd.DataFrame]) -> dict[str, float]:
        for sym, df in data.items():
            return {sym: 0.5}
        return {}

    strategy.calculate_score = fake_score

    n = 120
    df = _make_ohlcv(n, trend=0.01)
    df.loc[50, "volume"] = 0
    df.loc[80, "volume"] = 0
    data = {"600000": df}
    scored = compute_rolling_scores(data, strategy)
    assert not scored.empty
    result = run_analysis(scored, "test")
    assert "rho" in result or "error" in result


def test_compute_rolling_scores_returns_schema_when_no_rows() -> None:
    from unittest.mock import MagicMock

    strategy = MagicMock()
    strategy.thresholds.momentum.lookback_days = 60

    scored = compute_rolling_scores({"600000": pd.DataFrame()}, strategy)
    result = run_analysis(scored, "empty")

    assert list(scored.columns) == [
        "symbol",
        "date",
        "score",
        "forward_ret",
        "close_idx",
    ]
    assert result["error"] == "样本不足"


def test_parser_accepts_documented_sina_source_argument() -> None:
    args = build_parser().parse_args(["--source", "sina"])

    assert args.source == "sina"


def test_fetch_data_rejects_unsupported_source() -> None:
    with pytest.raises(ValueError, match="unsupported source"):
        fetch_data(["600000"], date(2026, 1, 1), date(2026, 1, 2), "akshare")


def test_conclusion_lines_do_not_emit_stale_pr23_actions() -> None:
    text = "".join(conclusion_lines("A", -0.10))

    assert "PR23" not in text
    assert "RSI 正向" in text
    assert "return_score 下限" in text


def test_conclusion_lines_handles_insufficient_samples() -> None:
    text = "".join(conclusion_lines("INSUFFICIENT", None))

    assert "样本不足" in text
    assert "不判断 momentum 方向" in text


def test_conclusion_lines_handles_nan_rho_without_fake_comparison() -> None:
    text = "".join(conclusion_lines("B", float("nan")))

    assert "无法计算" in text
    assert "nan < 0.05" not in text


def test_classify_conclusion_treats_nan_rho_as_no_information() -> None:
    assert classify_conclusion({"rho": float("nan")}) == "B"


def test_classify_conclusion_treats_threshold_rho_as_no_information() -> None:
    assert classify_conclusion({"rho": 0.05}) == "B"
    assert classify_conclusion({"rho": -0.05}) == "B"


def test_classify_conclusion_treats_error_as_insufficient() -> None:
    assert classify_conclusion({"error": "样本不足"}) == "INSUFFICIENT"


def test_classify_conclusion_treats_schema_missing_as_invalid_schema() -> None:
    assert classify_conclusion({"error": "schema missing"}) == "INVALID_SCHEMA"


def test_conclusion_lines_handles_invalid_schema() -> None:
    text = "".join(conclusion_lines("INVALID_SCHEMA", None))

    assert "输入结构错误" in text
    assert "score" in text
    assert "forward_ret" in text


def test_spearman_status_line_handles_nan_rho() -> None:
    assert "无法计算" in spearman_status_line(float("nan"))


def test_spearman_status_line_classifies_direction() -> None:
    assert "signal 反向" in spearman_status_line(-0.10)
    assert "signal 无信息量" in spearman_status_line(-0.05)
    assert "signal 无信息量" in spearman_status_line(0.01)
    assert "signal 无信息量" in spearman_status_line(0.05)
    assert "signal 正向" in spearman_status_line(0.10)


def test_main_writes_insufficient_sample_report_when_fetch_has_no_scored_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "momentum-report.md"

    def fake_fetch_data(
        symbols: list[str], start: date, end: date, source: str = "sina"
    ) -> dict[str, pd.DataFrame]:
        return {symbols[0]: pd.DataFrame()}

    monkeypatch.setattr(diagnose_momentum, "fetch_data", fake_fetch_data)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "diagnose_momentum.py",
            "--symbols",
            "600000",
            "--output",
            str(output_path),
        ],
    )

    diagnose_momentum.main()

    report = output_path.read_text(encoding="utf-8")
    assert "样本不足" in report
    assert "## 4. 结论" in report
