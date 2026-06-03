from __future__ import annotations

import json

from aqsp.briefing.closing_review import (
    ClosingReviewer,
    DailyReview,
    WeeklySummary,
    format_daily_review,
    format_weekly_summary,
)


def _write_predictions(path, predictions):
    with open(path, "w", encoding="utf-8") as f:
        for pred in predictions:
            f.write(json.dumps(pred, ensure_ascii=False) + "\n")


class TestClosingReviewerInit:
    def test_init_default(self):
        reviewer = ClosingReviewer()
        assert reviewer.ledger_path == "data/predictions.jsonl"

    def test_init_custom_path(self, tmp_path):
        ledger = tmp_path / "custom.jsonl"
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        assert reviewer.ledger_path == str(ledger)


class TestReviewToday:
    def test_returns_daily_review(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        _write_predictions(
            str(ledger),
            [
                {
                    "symbol": "600000",
                    "name": "测试股票A",
                    "strategy_type": "早盘打板",
                    "signal_date": "2025-06-01",
                    "entry_price": 10.0,
                    "current_price": 10.5,
                    "return_pct": 5.0,
                    "holding_days": 1,
                },
            ],
        )
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        review = reviewer.review_today("2025-06-01")

        assert isinstance(review, DailyReview)
        assert review.date == "2025-06-01"
        assert review.total_signals == 1

    def test_review_has_required_fields(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        _write_predictions(
            str(ledger),
            [
                {
                    "symbol": "600000",
                    "name": "测试A",
                    "strategy_type": "早盘打板",
                    "signal_date": "2025-06-01",
                    "entry_price": 10.0,
                    "current_price": 10.5,
                    "return_pct": 5.0,
                    "holding_days": 1,
                },
                {
                    "symbol": "600001",
                    "name": "测试B",
                    "strategy_type": "尾盘溢价",
                    "signal_date": "2025-06-01",
                    "entry_price": 20.0,
                    "current_price": 19.5,
                    "return_pct": -2.5,
                    "holding_days": 2,
                },
            ],
        )
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        review = reviewer.review_today("2025-06-01")

        assert review.total_signals == 2
        assert review.executed_signals == 2
        assert review.win_count == 1
        assert review.loss_count == 1
        assert review.win_rate == 0.5
        assert review.total_return == 2.5
        assert review.max_single_win == 5.0
        assert review.max_single_loss == -2.5
        assert review.avg_holding_days == 1.5
        assert isinstance(review.strategy_breakdown, dict)
        assert isinstance(review.market_environment, str)
        assert isinstance(review.key_lessons, tuple)
        assert isinstance(review.improvement_suggestions, tuple)

    def test_empty_review_when_no_predictions(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        ledger.write_text("")
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        review = reviewer.review_today("2025-06-01")

        assert isinstance(review, DailyReview)
        assert review.total_signals == 0
        assert review.executed_signals == 0
        assert review.win_count == 0
        assert review.loss_count == 0
        assert review.win_rate == 0
        assert review.total_return == 0
        assert review.market_environment == "无数据"
        assert "今日无交易信号" in review.key_lessons

    def test_empty_review_when_file_missing(self, tmp_path):
        ledger = tmp_path / "nonexistent.jsonl"
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        review = reviewer.review_today("2025-06-01")

        assert isinstance(review, DailyReview)
        assert review.total_signals == 0

    def test_filters_by_date(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        _write_predictions(
            str(ledger),
            [
                {
                    "symbol": "600000",
                    "name": "A",
                    "strategy_type": "早盘打板",
                    "signal_date": "2025-06-01",
                    "entry_price": 10.0,
                    "current_price": 10.5,
                    "return_pct": 5.0,
                    "holding_days": 1,
                },
                {
                    "symbol": "600001",
                    "name": "B",
                    "strategy_type": "尾盘溢价",
                    "signal_date": "2025-06-02",
                    "entry_price": 20.0,
                    "current_price": 20.5,
                    "return_pct": 2.5,
                    "holding_days": 1,
                },
            ],
        )
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        review = reviewer.review_today("2025-06-01")

        assert review.total_signals == 1

    def test_defaults_to_latest_signal_date_when_date_omitted(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        _write_predictions(
            str(ledger),
            [
                {
                    "symbol": "600000",
                    "name": "A",
                    "signal_date": "2025-06-01",
                    "entry_price": 10.0,
                    "current_price": 10.1,
                    "return_pct": 1.0,
                    "holding_days": 1,
                },
                {
                    "symbol": "600001",
                    "name": "B",
                    "signal_date": "2025-06-03",
                    "entry_price": 20.0,
                    "current_price": 21.0,
                    "return_pct": 5.0,
                    "holding_days": 1,
                },
            ],
        )
        reviewer = ClosingReviewer(ledger_path=str(ledger))

        review = reviewer.review_today()

        assert review.date == "2025-06-03"
        assert review.total_signals == 1
        assert review.total_return == 5.0

    def test_strategy_breakdown(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        _write_predictions(
            str(ledger),
            [
                {
                    "symbol": "600000",
                    "name": "A",
                    "strategy_type": "早盘打板",
                    "signal_date": "2025-06-01",
                    "entry_price": 10.0,
                    "current_price": 10.5,
                    "return_pct": 5.0,
                    "holding_days": 1,
                },
                {
                    "symbol": "600001",
                    "name": "B",
                    "strategy_type": "早盘打板",
                    "signal_date": "2025-06-01",
                    "entry_price": 20.0,
                    "current_price": 19.5,
                    "return_pct": -2.5,
                    "holding_days": 1,
                },
                {
                    "symbol": "600002",
                    "name": "C",
                    "strategy_type": "尾盘溢价",
                    "signal_date": "2025-06-01",
                    "entry_price": 15.0,
                    "current_price": 15.5,
                    "return_pct": 3.3,
                    "holding_days": 2,
                },
            ],
        )
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        review = reviewer.review_today("2025-06-01")

        assert "早盘打板" in review.strategy_breakdown
        assert "尾盘溢价" in review.strategy_breakdown
        assert review.strategy_breakdown["早盘打板"]["total"] == 2
        assert review.strategy_breakdown["早盘打板"]["wins"] == 1
        assert review.strategy_breakdown["早盘打板"]["losses"] == 1
        assert review.strategy_breakdown["尾盘溢价"]["total"] == 1
        assert review.strategy_breakdown["尾盘溢价"]["wins"] == 1

    def test_uses_strategy_and_sub_strategy_when_strategy_type_missing(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        _write_predictions(
            str(ledger),
            [
                {
                    "symbol": "600000",
                    "name": "A",
                    "strategies": ["morning_breakout"],
                    "sub_strategy": "涨停打板",
                    "signal_date": "2025-06-01",
                    "entry_price": 10.0,
                    "current_price": 9.5,
                    "return_pct": -5.0,
                    "holding_days": 1,
                },
                {
                    "symbol": "600001",
                    "name": "B",
                    "strategies": ["closing_premium"],
                    "sub_strategy": "量价突破",
                    "signal_date": "2025-06-01",
                    "entry_price": 20.0,
                    "current_price": 21.0,
                    "return_pct": 5.0,
                    "holding_days": 2,
                },
            ],
        )
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        review = reviewer.review_today("2025-06-01")

        assert "早盘打板·涨停打板" in review.strategy_breakdown
        assert "尾盘溢价·量价突破" in review.strategy_breakdown
        assert any("打板成功率偏低" in item for item in review.key_lessons)

    def test_uses_entry_type_when_strategies_missing(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        _write_predictions(
            str(ledger),
            [
                {
                    "symbol": "600000",
                    "name": "A",
                    "entry_type": "relative_strength",
                    "signal_date": "2025-06-01",
                    "entry_price": 10.0,
                    "current_price": 10.1,
                    "return_pct": 1.0,
                    "holding_days": 1,
                },
                {
                    "symbol": "600001",
                    "name": "B",
                    "entry_type": "reversal_watch",
                    "signal_date": "2025-06-01",
                    "entry_price": 20.0,
                    "current_price": 19.0,
                    "return_pct": -5.0,
                    "holding_days": 2,
                },
            ],
        )
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        review = reviewer.review_today("2025-06-01")

        assert "相对强度" in review.strategy_breakdown
        assert "反转观察" in review.strategy_breakdown

    def test_uses_rating_label_when_strategy_and_entry_type_missing(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        _write_predictions(
            str(ledger),
            [
                {
                    "symbol": "600000",
                    "name": "A",
                    "rating": "watch",
                    "signal_date": "2025-06-01",
                    "entry_price": 10.0,
                    "current_price": 10.1,
                    "return_pct": 1.0,
                    "holding_days": 1,
                },
            ],
        )
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        review = reviewer.review_today("2025-06-01")

        assert "候选观察池" in review.strategy_breakdown


class TestGenerateWeeklySummary:
    def test_returns_weekly_summary(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        ledger.write_text("")
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        summary = reviewer.generate_weekly_summary("2025-06-01")

        assert isinstance(summary, WeeklySummary)

    def test_weekly_summary_has_required_fields(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        ledger.write_text("")
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        summary = reviewer.generate_weekly_summary("2025-06-01")

        assert isinstance(summary.week_start, str)
        assert isinstance(summary.week_end, str)
        assert summary.week_end == "2025-06-01"
        assert isinstance(summary.total_trades, int)
        assert isinstance(summary.win_rate, (int, float))
        assert isinstance(summary.total_return, (int, float))
        assert isinstance(summary.sharpe_ratio, (int, float))
        assert isinstance(summary.max_drawdown, (int, float))
        assert isinstance(summary.best_strategy, str)
        assert isinstance(summary.worst_strategy, str)
        assert isinstance(summary.market_trend, str)
        assert isinstance(summary.next_week_outlook, str)

    def test_weekly_summary_with_empty_data(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        ledger.write_text("")
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        summary = reviewer.generate_weekly_summary("2025-06-01")

        assert summary.total_trades == 0
        assert summary.win_rate == 0
        assert summary.total_return == 0


class TestFormatDailyReview:
    def test_returns_string(self):
        review = DailyReview(
            date="2025-06-01",
            total_signals=0,
            executed_signals=0,
            win_count=0,
            loss_count=0,
            win_rate=0,
            total_return=0,
            max_single_win=0,
            max_single_loss=0,
            avg_holding_days=0,
            strategy_breakdown={},
            market_environment="无数据",
            main_chain_summary=(),
            key_lessons=("今日无交易信号",),
            improvement_suggestions=("继续观察市场",),
        )
        result = format_daily_review(review)
        assert isinstance(result, str)

    def test_contains_key_sections(self):
        review = DailyReview(
            date="2025-06-01",
            total_signals=2,
            executed_signals=2,
            win_count=1,
            loss_count=1,
            win_rate=0.5,
            total_return=2.5,
            max_single_win=5.0,
            max_single_loss=-2.5,
            avg_holding_days=1.5,
            strategy_breakdown={
                "早盘打板": {
                    "total": 1,
                    "wins": 1,
                    "losses": 0,
                    "total_return": 5.0,
                    "win_rate": 1.0,
                },
            },
            market_environment="震荡市",
            main_chain_summary=(
                "PM主裁决: 上调 1 / 降级 1 / 维持 0",
                "可执行主链: 600519 贵州茅台",
                "候选观察池: 300750 宁德时代",
            ),
            key_lessons=("测试教训",),
            improvement_suggestions=("测试建议",),
        )
        result = format_daily_review(review)

        assert "每日交易复盘" in result
        assert "2025-06-01" in result
        assert "主链总览" in result
        assert "PM主裁决: 上调 1 / 降级 1 / 维持 0" in result
        assert "总体统计" in result
        assert "策略分类统计" in result
        assert "市场环境" in result
        assert "关键经验教训" in result
        assert "改进建议" in result
        assert "早盘打板" in result
        assert "测试教训" in result
        assert "测试建议" in result

    def test_uses_observation_tone_when_signals_exist_but_no_execution(self):
        review = DailyReview(
            date="2025-06-01",
            total_signals=3,
            executed_signals=0,
            win_count=0,
            loss_count=0,
            win_rate=0.0,
            total_return=0.0,
            max_single_win=0.0,
            max_single_loss=0.0,
            avg_holding_days=1.0,
            strategy_breakdown={},
            market_environment="震荡市",
            main_chain_summary=("候选观察池: 600519 贵州茅台",),
            key_lessons=("今日无交易信号",),
            improvement_suggestions=("继续观察市场",),
        )

        result = format_daily_review(review)

        assert "🧭 今日以观察为主，等待右侧确认后再行动。" in result
        assert "今日表现不佳" not in result


class TestFormatWeeklySummary:
    def test_returns_string(self):
        summary = WeeklySummary(
            week_start="2025-05-26",
            week_end="2025-06-01",
            total_trades=0,
            win_rate=0,
            total_return=0,
            sharpe_ratio=0,
            max_drawdown=0,
            best_strategy="",
            worst_strategy="",
            market_trend="震荡",
            next_week_outlook="观望为主",
        )
        result = format_weekly_summary(summary)
        assert isinstance(result, str)

    def test_contains_key_sections(self):
        summary = WeeklySummary(
            week_start="2025-05-26",
            week_end="2025-06-01",
            total_trades=10,
            win_rate=0.6,
            total_return=5.5,
            sharpe_ratio=1.2,
            max_drawdown=3.0,
            best_strategy="早盘打板",
            worst_strategy="尾盘溢价",
            market_trend="震荡",
            next_week_outlook="观望为主",
        )
        result = format_weekly_summary(summary)

        assert "周度交易总结" in result
        assert "2025-05-26" in result
        assert "2025-06-01" in result
        assert "周度统计" in result
        assert "策略表现" in result
        assert "市场趋势" in result
        assert "下周展望" in result
        assert "早盘打板" in result
        assert "尾盘溢价" in result


class TestTradeReview:
    def test_lessons_on_win(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        _write_predictions(
            str(ledger),
            [
                {
                    "symbol": "600000",
                    "name": "A",
                    "strategy_type": "早盘打板",
                    "signal_date": "2025-06-01",
                    "entry_price": 10.0,
                    "current_price": 10.8,
                    "return_pct": 8.0,
                    "holding_days": 1,
                },
            ],
        )
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        review = reviewer.review_today("2025-06-01")

        assert review.win_count == 1
        assert review.max_single_win == 8.0

    def test_lessons_on_loss(self, tmp_path):
        ledger = tmp_path / "predictions.jsonl"
        _write_predictions(
            str(ledger),
            [
                {
                    "symbol": "600000",
                    "name": "A",
                    "strategy_type": "尾盘溢价",
                    "signal_date": "2025-06-01",
                    "entry_price": 10.0,
                    "current_price": 9.2,
                    "return_pct": -8.0,
                    "holding_days": 1,
                },
            ],
        )
        reviewer = ClosingReviewer(ledger_path=str(ledger))
        review = reviewer.review_today("2025-06-01")

        assert review.loss_count == 1
        assert review.max_single_loss == -8.0
