"""复盘直连账本 + 市场环境/周度展望数据驱动。

背景：原先 `_evaluate_market_environment` 直接 `return "震荡市"`，
`WeeklySummary` 的 `market_trend="震荡"` / `next_week_outlook="观望为主"`
为写死值，导致复盘报告不反映真实数据。本文件锁定修复后的行为。
"""

from __future__ import annotations

import json

from aqsp.briefing.closing_review import (
    ClosingReviewer,
    DailyReview,
    MarketEnvironmentBands,
)

HARDCODED_ENV = "震荡市"
HARDCODED_TREND = "震荡"
HARDCODED_OUTLOOK = "观望为主"


def _write_jsonl(path, rows) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _validated_row(
    signal_id: str,
    *,
    signal_date: str = "2025-06-01",
    return_pct: float,
    excess_return_pct: float,
    win: bool,
) -> dict:
    """构造一条账本已验证行（字段对齐 validate_predictions 的写入口径）。"""
    return {
        "id": signal_id,
        "symbol": "600000",
        "name": "测试",
        "strategies": ["morning_breakout"],
        "signal_date": signal_date,
        "status": "validated",
        "rating": "strong_buy_candidate",
        "return_pct": return_pct,
        "excess_return_pct": excess_return_pct,
        "win": win,
    }


def _make_reviewer(tmp_path, ledger_rows, paper_rows) -> ClosingReviewer:
    ledger = tmp_path / "predictions.jsonl"
    paper = tmp_path / "paper_trades.jsonl"
    _write_jsonl(ledger, ledger_rows)
    _write_jsonl(paper, paper_rows)
    return ClosingReviewer(
        ledger_path=str(ledger),
        paper_ledger_path=str(paper),
    )


class TestMarketEnvironmentIsDataDriven:
    def test_market_environment_prefers_ledger_excess_over_paper(self) -> None:
        reviewer = ClosingReviewer()

        env = reviewer._evaluate_market_environment(
            "2025-06-01",
            win_rate=0.9,  # 纸面口径很强，但应被账本口径覆盖
            avg_return=10.0,
            executed=10,
            ledger_avg_excess_return=3.0,
        )

        assert "跑赢大盘" in env
        assert "+3.00%" in env
        assert env != HARDCODED_ENV

    def test_market_environment_flags_significant_underperformance(self) -> None:
        reviewer = ClosingReviewer()

        env = reviewer._evaluate_market_environment(
            ledger_avg_excess_return=-2.5,
        )

        assert "显著跑输大盘" in env

    def test_market_environment_falls_back_to_paper_without_validated_rows(
        self,
    ) -> None:
        reviewer = ClosingReviewer()

        env = reviewer._evaluate_market_environment(
            win_rate=0.7,
            avg_return=2.0,
            executed=5,
        )

        assert "纸面偏强" in env
        assert env != HARDCODED_ENV

    def test_market_environment_reports_insufficient_sample_instead_of_guessing(
        self,
    ) -> None:
        reviewer = ClosingReviewer()

        env = reviewer._evaluate_market_environment(
            win_rate=0.0,
            avg_return=0.0,
            executed=0,
        )

        assert "样本不足" in env
        assert env != HARDCODED_ENV

    def test_bands_thresholds_are_configurable_not_magic_numbers(self) -> None:
        reviewer = ClosingReviewer()
        tight = MarketEnvironmentBands(strong_outperform=10.0)

        # 超额 3% 在默认档位下算显著跑赢，在收紧档位下只算温和跑赢
        default_env = reviewer._evaluate_market_environment(
            ledger_avg_excess_return=3.0
        )
        tight_env = reviewer._evaluate_market_environment(
            ledger_avg_excess_return=3.0, bands=tight
        )

        assert "显著跑赢" in default_env
        assert "温和跑赢" in tight_env


class TestReviewTodayLinksLedger:
    def test_review_populates_ledger_fields_from_validated_rows(self, tmp_path) -> None:
        reviewer = _make_reviewer(
            tmp_path,
            ledger_rows=[
                _validated_row(
                    "sig-a", return_pct=5.0, excess_return_pct=3.0, win=True
                ),
                _validated_row(
                    "sig-b", return_pct=-1.0, excess_return_pct=1.0, win=False
                ),
            ],
            paper_rows=[],
        )

        review = reviewer.review_today("2025-06-01")

        assert isinstance(review, DailyReview)
        assert review.ledger_validated_count == 2
        assert review.ledger_win_rate == 0.5
        assert review.ledger_avg_return == 2.0
        assert review.ledger_avg_excess_return == 2.0
        # 市场环境必须来自账本超额收益，不得是写死的假设值
        assert review.market_environment != HARDCODED_ENV
        assert "跑赢大盘" in review.market_environment

    def test_review_reports_no_data_when_nothing_available(self, tmp_path) -> None:
        """账本与纸面全空时走既有空复盘分支，仍不得返回写死的假设值。"""
        reviewer = _make_reviewer(tmp_path, ledger_rows=[], paper_rows=[])

        review = reviewer.review_today("2025-06-01")

        assert review.ledger_validated_count == 0
        assert review.market_environment == "无数据"
        assert review.market_environment != HARDCODED_ENV

    def test_review_reports_insufficient_when_signals_but_no_execution(
        self, tmp_path
    ) -> None:
        """有信号但无成交样本时，明确标注样本不足而非猜测。"""
        pending = _validated_row(
            "sig-a", return_pct=5.0, excess_return_pct=3.0, win=True
        )
        pending["status"] = "pending"
        reviewer = _make_reviewer(tmp_path, ledger_rows=[pending], paper_rows=[])

        review = reviewer.review_today("2025-06-01")

        assert review.total_signals == 1
        assert review.executed_signals == 0
        assert "样本不足" in review.market_environment
        assert review.market_environment != HARDCODED_ENV

    def test_review_ignores_non_validated_rows_for_ledger_stats(self, tmp_path) -> None:
        pending = _validated_row(
            "sig-a", return_pct=5.0, excess_return_pct=3.0, win=True
        )
        pending["status"] = "pending"
        reviewer = _make_reviewer(tmp_path, ledger_rows=[pending], paper_rows=[])

        review = reviewer.review_today("2025-06-01")

        assert review.ledger_validated_count == 0
        assert review.ledger_avg_excess_return == 0.0


class TestWeeklySummaryIsDataDriven:
    def test_weekly_trend_reflects_returns_not_hardcoded(self) -> None:
        reviewer = ClosingReviewer()

        up = reviewer._evaluate_weekly_trend(
            win_rate=0.8, total_return=6.0, total_trades=10
        )
        down = reviewer._evaluate_weekly_trend(
            win_rate=0.2, total_return=-4.0, total_trades=10
        )

        assert up != HARDCODED_TREND
        assert "上行" in up
        assert "下行" in down

    def test_weekly_trend_reports_unknown_without_trades(self) -> None:
        reviewer = ClosingReviewer()

        trend = reviewer._evaluate_weekly_trend(
            win_rate=0.0, total_return=0.0, total_trades=0
        )

        assert "趋势不明" in trend

    def test_weekly_outlook_tightens_on_large_drawdown(self) -> None:
        reviewer = ClosingReviewer()

        outlook = reviewer._evaluate_next_week_outlook(
            win_rate=0.9, max_drawdown=12.0, total_trades=10
        )

        assert outlook != HARDCODED_OUTLOOK
        assert "回撤偏大" in outlook

    def test_weekly_outlook_normal_when_healthy(self) -> None:
        reviewer = ClosingReviewer()

        outlook = reviewer._evaluate_next_week_outlook(
            win_rate=0.8, max_drawdown=1.0, total_trades=10
        )

        assert "正常参与" in outlook

    def test_weekly_outlook_unknown_without_trades(self) -> None:
        reviewer = ClosingReviewer()

        outlook = reviewer._evaluate_next_week_outlook(
            win_rate=0.0, max_drawdown=0.0, total_trades=0
        )

        assert "样本不足" in outlook
        assert outlook != HARDCODED_OUTLOOK
