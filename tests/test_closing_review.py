from __future__ import annotations

import json

from aqsp.briefing.closing_review import (
    ClosingReviewer,
    DailyReview,
    WeeklySummary,
    format_daily_review,
    format_weekly_summary,
)


def _write_jsonl(path, rows) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestClosingReviewerInit:
    def test_init_default(self) -> None:
        reviewer = ClosingReviewer()

        assert reviewer.ledger_path == "data/predictions.jsonl"
        assert reviewer.paper_ledger_path == "data/paper_trades.jsonl"

    def test_init_custom_paths(self, tmp_path) -> None:
        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"

        reviewer = ClosingReviewer(
            ledger_path=str(ledger),
            paper_ledger_path=str(paper),
        )

        assert reviewer.ledger_path == str(ledger)
        assert reviewer.paper_ledger_path == str(paper)


class TestReviewToday:
    def test_quality_state_blocks_rating_from_main_watch_list(self) -> None:
        reviewer = ClosingReviewer()

        summary = reviewer._build_main_chain_summary(
            [
                {
                    "symbol": "600000",
                    "name": "测试",
                    "rating": "strong_buy_candidate",
                    "quality_gate_action": "observe",
                    "paper_review_eligible": False,
                    "observation_only": True,
                    "portfolio_action": "observation_only",
                }
            ]
        )

        assert "主看名单" not in "\n".join(summary)
        assert "观察名单" in "\n".join(summary)

    def test_review_uses_paper_ledger_as_trade_fact_source(self, tmp_path) -> None:
        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        _write_jsonl(
            ledger,
            [
                {
                    "id": "sig-a",
                    "symbol": "600000",
                    "name": "测试A",
                    "strategies": ["morning_breakout"],
                    "sub_strategy": "涨停打板",
                    "signal_date": "2025-06-01",
                    "return_pct": 99.0,
                    "candidate_blocker": "板块集中度过高",
                    "candidate_next_step": "等量能确认后再处理",
                    "candidate_review_window": "午后",
                    "candidate_review_priority": "high",
                    "portfolio_action": "downgrade",
                    "rating": "watch",
                },
                {
                    "id": "sig-b",
                    "symbol": "600001",
                    "name": "测试B",
                    "strategies": ["closing_premium"],
                    "sub_strategy": "量价突破",
                    "signal_date": "2025-06-01",
                    "return_pct": -99.0,
                    "portfolio_action": "promote",
                    "rating": "strong_buy_candidate",
                },
            ],
        )
        _write_jsonl(
            paper,
            [
                {
                    "signal_id": "sig-a",
                    "symbol": "600000",
                    "name": "测试A",
                    "signal_date": "2025-06-01",
                    "entry_date": "2025-06-02",
                    "exit_date": "2025-06-03",
                    "entry_price": 10.0,
                    "exit_price": 9.6,
                    "status": "closed",
                    "return_pct": -4.0,
                    "exit_reason": "stop_loss",
                },
                {
                    "signal_id": "sig-b",
                    "symbol": "600001",
                    "name": "测试B",
                    "signal_date": "2025-06-01",
                    "entry_date": "2025-06-02",
                    "exit_date": "2025-06-04",
                    "entry_price": 20.0,
                    "exit_price": 21.0,
                    "status": "closed",
                    "return_pct": 5.0,
                    "exit_reason": "take_profit",
                },
                {
                    "signal_id": "sig-c",
                    "symbol": "600002",
                    "name": "测试C",
                    "signal_date": "2025-06-01",
                    "status": "not_executable",
                    "not_executable_reason": "limit_up_at_open",
                },
                {
                    "signal_id": "sig-d",
                    "symbol": "600003",
                    "name": "测试D",
                    "signal_date": "2025-06-01",
                    "status": "pending_entry",
                },
            ],
        )

        reviewer = ClosingReviewer(
            ledger_path=str(ledger),
            paper_ledger_path=str(paper),
        )
        review = reviewer.review_today("2025-06-01")

        assert isinstance(review, DailyReview)
        assert review.total_signals == 2
        assert review.executed_signals == 2
        assert review.win_count == 1
        assert review.loss_count == 1
        assert review.win_rate == 0.5
        assert review.total_return == 1.0
        assert review.max_single_win == 5.0
        assert review.max_single_loss == -4.0
        assert review.avg_holding_days == 2.5
        assert "早盘打板·涨停打板" in review.strategy_breakdown
        assert "尾盘溢价·量价突破" in review.strategy_breakdown
        assert any("不可成交样本" in item for item in review.key_lessons)
        assert any("等待纸面入场或纸面结束" in item for item in review.key_lessons)
        assert any("不可成交原因" in item for item in review.improvement_suggestions)
        assert "观察名单: 600000 测试A" in review.main_chain_summary
        assert "阻塞: 600000 测试A: 板块集中度过高" in review.main_chain_summary

    def test_review_counts_signals_when_only_pending_or_blocked_rows_exist(
        self, tmp_path
    ) -> None:
        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        _write_jsonl(
            ledger,
            [
                {
                    "id": "sig-a",
                    "symbol": "600000",
                    "name": "A",
                    "signal_date": "2025-06-01",
                },
                {
                    "id": "sig-b",
                    "symbol": "600001",
                    "name": "B",
                    "signal_date": "2025-06-01",
                },
            ],
        )
        _write_jsonl(
            paper,
            [
                {
                    "signal_id": "sig-a",
                    "symbol": "600000",
                    "signal_date": "2025-06-01",
                    "status": "pending_entry",
                },
                {
                    "signal_id": "sig-b",
                    "symbol": "600001",
                    "signal_date": "2025-06-01",
                    "status": "not_executable",
                },
            ],
        )

        review = ClosingReviewer(
            ledger_path=str(ledger),
            paper_ledger_path=str(paper),
        ).review_today("2025-06-01")

        assert review.total_signals == 2
        assert review.executed_signals == 0
        assert review.win_rate == 0
        assert any("暂无 closed 虚拟盘结果" in item for item in review.key_lessons)

    def test_review_uses_paper_context_when_ledger_prediction_missing(
        self, tmp_path
    ) -> None:
        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        _write_jsonl(ledger, [])
        _write_jsonl(
            paper,
            [
                {
                    "signal_id": "sig-x",
                    "symbol": "600010",
                    "name": "包钢股份",
                    "signal_date": "2025-06-02",
                    "status": "closed",
                    "entry_date": "2025-06-03",
                    "exit_date": "2025-06-04",
                    "entry_price": 5.0,
                    "exit_price": 5.4,
                    "return_pct": 8.0,
                    "portfolio_action": "promote",
                    "candidate_status": "延续上升",
                    "candidate_next_step": "放量时继续跟踪",
                    "candidate_review_window": "开盘前后",
                    "candidate_review_priority": "high",
                    "strategies": ["morning_breakout"],
                }
            ],
        )

        review = ClosingReviewer(
            ledger_path=str(ledger),
            paper_ledger_path=str(paper),
        ).review_today("2025-06-02")

        assert review.executed_signals == 1
        assert "主看名单: 600010 包钢股份" in review.main_chain_summary
        assert (
            "后续关注: 600010 包钢股份 | 高优先级 / 开盘前后 | 放量时继续跟踪"
            in review.main_chain_summary
        )

    def test_review_defaults_to_latest_date_from_paper_ledger(self, tmp_path) -> None:
        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        _write_jsonl(
            ledger,
            [
                {"id": "sig-a", "symbol": "600000", "signal_date": "2025-06-01"},
            ],
        )
        _write_jsonl(
            paper,
            [
                {
                    "signal_id": "sig-b",
                    "symbol": "600001",
                    "signal_date": "2025-06-03",
                    "entry_date": "2025-06-04",
                    "exit_date": "2025-06-05",
                    "status": "closed",
                    "return_pct": 3.0,
                }
            ],
        )

        review = ClosingReviewer(
            ledger_path=str(ledger),
            paper_ledger_path=str(paper),
        ).review_today()

        assert review.date == "2025-06-03"
        assert review.executed_signals == 1
        assert review.total_return == 3.0

    def test_empty_review_when_no_predictions_and_no_paper_rows(self, tmp_path) -> None:
        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        paper.write_text("", encoding="utf-8")

        review = ClosingReviewer(
            ledger_path=str(ledger),
            paper_ledger_path=str(paper),
        ).review_today("2025-06-01")

        assert review.total_signals == 0
        assert review.executed_signals == 0
        assert review.market_environment == "无数据"
        assert "今日无交易信号" in review.key_lessons


class TestGenerateWeeklySummary:
    def test_weekly_summary_uses_closed_paper_trades(self, tmp_path) -> None:
        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        _write_jsonl(
            ledger,
            [
                {
                    "id": "sig-a",
                    "symbol": "600000",
                    "name": "A",
                    "strategies": ["morning_breakout"],
                    "signal_date": "2025-05-29",
                },
                {
                    "id": "sig-b",
                    "symbol": "600001",
                    "name": "B",
                    "strategies": ["closing_premium"],
                    "signal_date": "2025-05-30",
                },
            ],
        )
        _write_jsonl(
            paper,
            [
                {
                    "signal_id": "sig-a",
                    "symbol": "600000",
                    "name": "A",
                    "signal_date": "2025-05-29",
                    "entry_date": "2025-05-30",
                    "exit_date": "2025-06-02",
                    "status": "closed",
                    "return_pct": 3.0,
                },
                {
                    "signal_id": "sig-b",
                    "symbol": "600001",
                    "name": "B",
                    "signal_date": "2025-05-30",
                    "entry_date": "2025-06-02",
                    "exit_date": "2025-06-03",
                    "status": "closed",
                    "return_pct": -1.0,
                },
                {
                    "signal_id": "sig-c",
                    "symbol": "600002",
                    "signal_date": "2025-05-31",
                    "status": "not_executable",
                },
            ],
        )

        summary = ClosingReviewer(
            ledger_path=str(ledger),
            paper_ledger_path=str(paper),
        ).generate_weekly_summary("2025-06-03")

        assert isinstance(summary, WeeklySummary)
        assert summary.week_start == "2025-05-28"
        assert summary.week_end == "2025-06-03"
        assert summary.total_trades == 2
        assert summary.win_rate == 0.5
        assert summary.total_return == 2.0
        assert summary.best_strategy == "早盘打板"
        assert summary.worst_strategy == "尾盘溢价"


class TestFormatReviewOutput:
    def test_format_daily_review_contains_main_sections(self) -> None:
        review = DailyReview(
            date="2025-06-01",
            total_signals=2,
            executed_signals=1,
            win_count=1,
            loss_count=0,
            win_rate=1.0,
            total_return=3.0,
            max_single_win=3.0,
            max_single_loss=3.0,
            avg_holding_days=2.0,
            strategy_breakdown={
                "早盘打板": {
                    "total": 1,
                    "wins": 1,
                    "losses": 0,
                    "total_return": 3.0,
                    "win_rate": 1.0,
                }
            },
            market_environment="震荡市",
            main_chain_summary=("PM主裁决: 上调 1 / 降级 0 / 维持 1",),
            key_lessons=("存在不可成交样本，已按阻塞处理，不计入胜率。",),
            improvement_suggestions=(
                "复核不可成交原因，确认是否属于流动性或涨停限制。",
            ),
        )

        result = format_daily_review(review)

        assert "每日纸面验证复盘" in result
        assert "主链总览" in result
        assert "总体统计" in result
        assert "策略分类统计" in result
        assert "关键经验教训" in result
        assert "改进建议" in result

    def test_format_daily_review_uses_observation_tone_when_no_closed_trades(
        self,
    ) -> None:
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
            avg_holding_days=0.0,
            strategy_breakdown={},
            market_environment="震荡市",
            main_chain_summary=("继续观察名单: 600519 贵州茅台",),
            key_lessons=("今日信号仍在跟踪，暂无 closed 虚拟盘结果。",),
            improvement_suggestions=("对未完成验证的样本保留跟踪，避免过早下结论。",),
        )

        result = format_daily_review(review)

        assert "🧭 今日以观察为主，等待右侧确认后再行动。" in result

    def test_format_weekly_summary_returns_string(self) -> None:
        summary = WeeklySummary(
            week_start="2025-05-26",
            week_end="2025-06-01",
            total_trades=2,
            win_rate=0.5,
            total_return=2.0,
            sharpe_ratio=0.5,
            max_drawdown=1.0,
            best_strategy="早盘打板",
            worst_strategy="尾盘溢价",
            market_trend="震荡",
            next_week_outlook="观望为主",
        )

        result = format_weekly_summary(summary)

        assert isinstance(result, str)
        assert "周度纸面验证总结" in result
        assert "早盘打板" in result
        assert "尾盘溢价" in result
