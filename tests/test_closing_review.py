from __future__ import annotations

import json

from pathlib import Path

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

    def test_empty_review_still_carries_factor_ic_section(
        self, tmp_path, monkeypatch
    ) -> None:
        """回归：无信号/无纸面交易走 _empty_review 早退时，IC 健康段不得被丢弃。

        09-26 实锤：收评 CLI 因当日无信号走早退分支，factor_ic_section 未被塞入
        DailyReview ⇒ 落盘报告里 IC 段静默消失（单独调 build_factor_ic_section 却能
        拿到内容）。IC 是健康诊断、独立于有无信号，只要有回流产物就必须渲染。
        """
        from aqsp.briefing.closing_review import build_factor_ic_section

        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        paper.write_text("", encoding="utf-8")

        ic_dir = tmp_path / "pit_cache" / "factor_ic"
        ic_dir.mkdir(parents=True)
        ic_dir.joinpath("factor_ic_latest.json").write_text(
            json.dumps(
                _ic_payload(
                    {
                        "momentum": {
                            "mean": -0.0604,
                            "std": 0.28,
                            "icir": -0.216,
                            "t": -0.65,
                            "pos_rate": 0.56,
                            "n": 9,
                            "recent": {"mean": -0.0604, "span": 20},
                        },
                        "composite": {
                            "mean": -0.0204,
                            "std": 0.27,
                            "icir": -0.076,
                            "t": -0.23,
                            "pos_rate": 0.56,
                            "n": 9,
                            "recent": {"mean": -0.0204, "span": 20},
                        },
                    },
                    as_of="2026-09-24",
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))

        review = ClosingReviewer(
            ledger_path=str(ledger),
            paper_ledger_path=str(paper),
        ).review_today("2025-06-01")

        # 早退空复盘路径也必须带上 IC 段（非空串）
        assert "因子 IC 健康" in review.factor_ic_section, (
            "无信号早退分支不得丢弃 IC 段"
        )
        # 全链路：format_daily_review 渲染后的落盘文本里必须有 IC 段
        assert "因子 IC 健康" in format_daily_review(review)
        # 交叉一致性：与直接调 build_factor_ic_section 的结果一致
        assert review.factor_ic_section == build_factor_ic_section(
            str(ic_dir / "factor_ic_latest.json")
        )



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


def _ic_payload(factors: dict, as_of: str = "2026-09-18") -> dict:
    return {"as_of": as_of, "window_days": 90, "factors": factors}


class TestFactorICSection:
    def test_render_when_present(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_factor_ic_section

        path = tmp_path / "factor_ic_latest.json"
        path.write_text(
            json.dumps(
                _ic_payload(
                    {
                        "momentum": {
                            "mean": -0.018,
                            "std": 0.05,
                            "icir": -0.36,
                            "t": -1.9,
                            "pos_rate": 0.31,
                            "n": 9,
                            "recent": {"mean": 0.011, "span": 20},
                        },
                        "composite": {
                            "mean": 0.003,
                            "std": 0.02,
                            "icir": 0.15,
                            "t": 0.4,
                            "pos_rate": 0.5,
                            "n": 9,
                            "recent": {"mean": 0.002, "span": 20},
                        },
                    }
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        section = build_factor_ic_section(str(path))
        assert "因子 IC 健康" in section
        assert "as-of 2026-09-18" in section
        # momentum：mean 负 + 短期为正 ⇒ 判读含「短期翻向」
        assert "反向有效" in section or "无预测力" in section
        # composite：|mean|<0.02 且 |t|<2 ⇒ 无预测力（噪音）
        assert "无预测力（噪音）" in section

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_factor_ic_section

        assert build_factor_ic_section(str(tmp_path / "nope.json")) == ""

    def test_corrupt_json_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_factor_ic_section

        path = tmp_path / "factor_ic_latest.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert build_factor_ic_section(str(path)) == ""

    def test_empty_factors_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_factor_ic_section

        path = tmp_path / "factor_ic_latest.json"
        path.write_text(json.dumps({"as_of": "2026-09-18", "factors": {}}), encoding="utf-8")
        assert build_factor_ic_section(str(path)) == ""

    def test_flip_verdict_when_recent_opposite(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_factor_ic_section

        path = tmp_path / "factor_ic_latest.json"
        path.write_text(
            json.dumps(
                _ic_payload(
                    {
                        "momentum": {
                            "mean": 0.04,
                            "std": 0.02,
                            "icir": 2.0,
                            "t": 4.0,
                            "pos_rate": 0.8,
                            "n": 12,
                            "recent": {"mean": -0.03, "span": 20},
                        }
                    }
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        section = build_factor_ic_section(str(path))
        assert "正向有效" in section
        assert "短期翻向" in section

    def test_default_fallback_is_repo_root_not_tmp(self, monkeypatch) -> None:
        """回归（09-26 读/写同源排查）：未设 AQSP_RUNTIME_DATA_ROOT 时，读侧须回落
        到仓库根（= closing_review.py 上溯 3 层，与写侧 daily_pipeline 的 project_root
        同源），**而非 /tmp**。若回落 /tmp，pull 到 <根>/pit_cache/ 的产物读不到，
        IC 段会在非 entrypoint 入口静默消失。"""
        from pathlib import Path

        from aqsp.briefing import closing_review
        from aqsp.briefing.closing_review import _factor_ic_runtime_root

        monkeypatch.delenv("AQSP_RUNTIME_DATA_ROOT", raising=False)
        repo_root = Path(closing_review.__file__).resolve().parents[3]
        # 核心断言：回落基准 == 仓库根，且不是系统临时目录
        assert _factor_ic_runtime_root() == str(repo_root)
        assert str(repo_root) != Path("/tmp").as_posix()
        # 由此推出的默认 json 路径相对布局正确
        default = Path(_factor_ic_runtime_root(), "pit_cache", "factor_ic", "factor_ic_latest.json")
        assert default.parts[-3:] == ("pit_cache", "factor_ic", "factor_ic_latest.json")

    def test_env_var_wins_over_fallback(self, monkeypatch, tmp_path) -> None:
        """设了 AQSP_RUNTIME_DATA_ROOT 时，读侧严格走该 env（写读同源的核心路径）。"""
        from aqsp.briefing.closing_review import _factor_ic_runtime_root

        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        assert _factor_ic_runtime_root() == str(tmp_path)


# ---- 板块资金面段（pit_cache/concept_board.csv，0 消费者断链打通）----

_CB_HEADER = "board_code,board_name,up_count,down_count,change_pct,main_net_inflow,main_net_ratio"

_CB_BODY_LINES = [
    "BK0001,人工智能,120,30,1.23,56789.0,2.5",
    "BK0002,机器人,95,20,2.10,32000.0,3.1",
    "BK0003,低空经济,60,10,0.80,15000.0,1.9",
    "BK0004,数据要素,40,12,-0.30,-8000.0,-1.2",
    "BK0005,房地产,5,40,-2.10,-26000.0,-4.0",
    "BK0006,白酒,3,35,-1.50,-19000.0,-3.3",
    "BK0007,银行,80,40,-0.20,-6000.0,-0.8",
]


def _write_cb(tmp_path, mtime_offset_days: float = 0.0) -> Path:
    """写 concept_board.csv 夹具（7 板块：3 正流入 / 3 负流入 / 1 零流入）。"""
    p = tmp_path / "concept_board.csv"
    p.write_text(_CB_HEADER + "\n" + "\n".join(_CB_BODY_LINES) + "\n", encoding="utf-8")
    if mtime_offset_days:
        import os as _os
        import time as _t

        old = _t.time() - mtime_offset_days * 86400
        _os.utime(p, (old, old))
    return p


class TestBoardFundSection:
    def test_render_when_present(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_board_fund_section

        path = _write_cb(tmp_path)
        section = build_board_fund_section(str(path))
        assert "板块资金面" in section
        assert "共 7 板块" in section
        # 上涨板块 = change_pct>0：BK0001/2/3
        assert "上涨板块 3" in section
        # 主力净流入：正流入板块 = BK0001/2/3 共 3 个，默认 top_in=5 只取到 3
        assert "主力净流入 Top3" in section
        # 最大流入板块居首
        assert "人工智能：主力净流入 +56789 万" in section
        assert "机器人：主力净流入 +32000 万" in section
        # 主力净流出 Top3（负流入中 3 个）
        assert "主力净流出 Top3" in section
        assert "房地产：主力净流入 -26000 万" in section
        # 全板块合计：56789+32000+15000-8000-26000-19000-6000 ≈ 86889 万
        assert "全板块主力净流入合计" in section
        # 红线条款
        assert "不改变打分/排序/下单" in section

    def test_top_in_out_count_params(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_board_fund_section

        path = _write_cb(tmp_path)
        section = build_board_fund_section(str(path), top_in=1, top_out=1)
        assert "主力净流入 Top1" in section
        assert "主力净流出 Top1" in section
        # Top1 时只有最大流入/最大流出入选
        assert "人工智能" in section and "房地产" in section
        assert "机器人" not in section

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_board_fund_section

        assert build_board_fund_section(str(tmp_path / "nope.csv")) == ""

    def test_wrong_header_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_board_fund_section

        path = tmp_path / "concept_board.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        assert build_board_fund_section(str(path)) == ""

    def test_empty_body_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_board_fund_section

        path = tmp_path / "concept_board.csv"
        path.write_text(_CB_HEADER + "\n", encoding="utf-8")
        assert build_board_fund_section(str(path)) == ""

    def test_malformed_rows_dropped_not_fatally(self, tmp_path) -> None:
        """坏行（缺 code/坏数值）只剔除该行，不拖垮整段。"""
        from aqsp.briefing.closing_review import build_board_fund_section

        path = tmp_path / "concept_board.csv"
        path.write_text(
            "\n".join(
                [
                    _CB_HEADER,
                    ",无名板,1,1,0.1,100.0,0.1",       # code 空 ⇒ 剔除
                    "BK1,坏数值板,1,1,0.1,not-a-number,0.1",  # inflow 坏 ⇒ 剔除
                    "BK2,正常板,10,2,1.0,5000000.0,0.5",
                    "BK3,零流入板,5,5,0.0,0.0,0.0",      # 0 不进 Top（两侧均不入）
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        section = build_board_fund_section(str(path))
        assert "共 2 板块" in section  # 只有 BK2/BK3 存活
        assert "正常板" in section
        assert "无名板" not in section and "坏数值板" not in section
        assert "只读资金面" in section

    def test_stale_data_annotated(self, tmp_path) -> None:
        """CSV mtime 超 stale_hours ⇒ 段尾标「非实时」。"""
        from aqsp.briefing.closing_review import build_board_fund_section

        path = _write_cb(tmp_path, mtime_offset_days=10.0)
        section = build_board_fund_section(str(path))
        assert "非实时" in section
        # 未陈旧（默认 48h 内）时不标注
        fresh = _write_cb(tmp_path)
        assert "非实时" not in build_board_fund_section(str(fresh))

    def test_empty_review_still_carries_board_fund_section(
        self, tmp_path, monkeypatch
    ) -> None:
        """回归（与 09-26 IC 段同类缺陷防回归）：无信号走 _empty_review 早退时，
        板块资金段不得被静默丢弃。"""
        from aqsp.briefing.closing_review import (
            ClosingReviewer,
            build_board_fund_section,
            format_daily_review,
        )

        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        paper.write_text("", encoding="utf-8")
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_cb(tmp_path / "pit_cache")

        review = ClosingReviewer(
            ledger_path=str(ledger), paper_ledger_path=str(paper)
        ).review_today("2025-06-01")
        assert "板块资金面" in review.board_fund_section
        assert "板块资金面" in format_daily_review(review)
        # 交叉一致性：与直接调 build_board_fund_section 一致
        assert review.board_fund_section == build_board_fund_section(
            str(tmp_path / "pit_cache" / "concept_board.csv")
        )

    def test_review_today_path_carries_board_fund_section(self, tmp_path, monkeypatch) -> None:
        """正常 review_today 路径（有信号）也塞资金段。"""
        from aqsp.briefing.closing_review import ClosingReviewer

        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        paper.write_text("", encoding="utf-8")
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_cb(tmp_path / "pit_cache")

        review = ClosingReviewer(
            ledger_path=str(ledger), paper_ledger_path=str(paper)
        ).review_today("2025-06-01")
        assert "板块资金面" in review.board_fund_section

    def test_default_path_resolves_via_runtime_root(self, tmp_path, monkeypatch) -> None:
        """csv_path=None ⇒ 走 <runtime>/pit_cache/concept_board.csv（写读同源）。"""
        from aqsp.briefing.closing_review import build_board_fund_section

        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_cb(tmp_path / "pit_cache")
        assert "板块资金面" in build_board_fund_section()
        monkeypatch.delenv("AQSP_RUNTIME_DATA_ROOT", raising=False)


# ---- 龙虎榜关注段（pit_cache/longhubang.csv，0 读者段打通）----

_LHB_HEADER = "trade_date,symbol,name,close_price,change_rate,buy_amount,sell_amount,net_amount,interpretation"

_LHB_BODY_LINES = [
    "2026-09-25,600000,浦发银行,10.50,1.20,50000.0,30000.0,20000.0,机构买入",
    "2026-09-24,600000,浦发银行,10.40,0.50,40000.0,25000.0,15000.0,机构持续买入",
    "2026-09-25,000001,平安银行,12.30,-2.10,30000.0,50000.0,-20000.0,游资出逃",
    "2026-09-25,300750,宁德时代,250.00,3.50,120000.0,80000.0,40000.0,主力净流入，机构席位买入",
    "2026-09-24,300750,宁德时代,242.00,2.00,90000.0,60000.0,30000.0,机构加仓",
]


def _write_lhb(tmp_path, mtime_offset_days: float = 0.0) -> Path:
    """写 longhubang.csv 夹具（3 个股：600000 2 次/净额+35000/机构；
    000001 1 次/净额-20000/游资；300750 2 次/净额+70000/机构+主力）。"""
    p = tmp_path / "longhubang.csv"
    p.write_text(_LHB_HEADER + "\n" + "\n".join(_LHB_BODY_LINES) + "\n", encoding="utf-8")
    if mtime_offset_days:
        import os as _os
        import time as _t

        old = _t.time() - mtime_offset_days * 86400
        _os.utime(p, (old, old))
    return p


class TestLonghubangSection:
    def test_render_when_present(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_longhubang_section

        path = _write_lhb(tmp_path)
        section = build_longhubang_section(str(path))
        assert "龙虎榜关注" in section
        # 共 3 个股，净买入合计 = 20000+15000-20000+40000+30000 = 85000 万
        assert "共 3 只个股上榜" in section
        assert "净买入合计 +85000 万" in section
        assert "机构解读出现 2 次 / 游资提及 1 次" in section
        # 净买入 Top（按净额降序）：宁德时代 70000 > 浦发银行 35000 > 平安银行 -20000
        assert "净买入 Top3" in section
        assert (
            "宁德时代(300750)：净买入 +70000 万｜上榜 2 次｜最新 +3.50%｜解读：机构/主力"
            in section
        )
        assert "浦发银行(600000)：净买入 +35000 万" in section
        assert (
            "平安银行(000001)：净买入 -20000 万｜上榜 1 次｜最新 -2.10%｜解读：游资"
            in section
        )
        # 红线条款
        assert "不改变打分/排序/下单" in section

    def test_top_n_param(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_longhubang_section

        path = _write_lhb(tmp_path)
        section = build_longhubang_section(str(path), top_n=1)
        assert "净买入 Top1" in section
        assert "宁德时代" in section
        assert "浦发银行" not in section

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_longhubang_section

        assert build_longhubang_section(str(tmp_path / "nope.csv")) == ""

    def test_wrong_header_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_longhubang_section

        path = tmp_path / "longhubang.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        assert build_longhubang_section(str(path)) == ""

    def test_empty_body_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_longhubang_section

        path = tmp_path / "longhubang.csv"
        path.write_text(_LHB_HEADER + "\n", encoding="utf-8")
        assert build_longhubang_section(str(path)) == ""

    def test_malformed_rows_dropped_not_fatally(self, tmp_path) -> None:
        """坏行（缺 symbol / net 坏数值）只剔除该行，不拖垮整段。"""
        from aqsp.briefing.closing_review import build_longhubang_section

        path = tmp_path / "longhubang.csv"
        path.write_text(
            "\n".join(
                [
                    _LHB_HEADER,
                    "2026-09-25,,无名股,10.0,1.0,100.0,50.0,50.0,机构",            # symbol 空 ⇒ 剔除
                    "2026-09-25,600111,坏数值股,10.0,1.0,100.0,50.0,not-a-number,游资",  # net 坏 ⇒ 剔除
                    "2026-09-25,600222,正常股,10.0,1.0,100.0,50.0,50000.0,机构买入",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        section = build_longhubang_section(str(path))
        assert "共 1 只个股上榜" in section
        assert "正常股" in section
        assert "无名股" not in section and "坏数值股" not in section
        assert "只读参考" in section

    def test_stale_data_annotated(self, tmp_path) -> None:
        """CSV mtime 超 stale_hours ⇒ 段尾标「非实时」。"""
        from aqsp.briefing.closing_review import build_longhubang_section

        path = _write_lhb(tmp_path, mtime_offset_days=10.0)
        section = build_longhubang_section(str(path))
        assert "非实时" in section
        # 未陈旧（默认 72h 内）时不标注
        fresh = _write_lhb(tmp_path)
        assert "非实时" not in build_longhubang_section(str(fresh))

    def test_empty_review_still_carries_longhubang_section(
        self, tmp_path, monkeypatch
    ) -> None:
        """回归（与 09-26 IC 段同类缺陷防回归）：无信号走 _empty_review 早退时，
        龙虎榜段不得被静默丢弃。"""
        from aqsp.briefing.closing_review import (
            ClosingReviewer,
            build_longhubang_section,
            format_daily_review,
        )

        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        paper.write_text("", encoding="utf-8")
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_lhb(tmp_path / "pit_cache")

        review = ClosingReviewer(
            ledger_path=str(ledger), paper_ledger_path=str(paper)
        ).review_today("2025-06-01")
        assert "龙虎榜关注" in review.longhubang_section
        assert "龙虎榜关注" in format_daily_review(review)
        # 交叉一致性：与直接调 build_longhubang_section 一致
        assert review.longhubang_section == build_longhubang_section(
            str(tmp_path / "pit_cache" / "longhubang.csv")
        )

    def test_review_today_path_carries_longhubang_section(self, tmp_path, monkeypatch) -> None:
        """正常 review_today 路径（有信号）也塞龙虎榜段。"""
        from aqsp.briefing.closing_review import ClosingReviewer

        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        paper.write_text("", encoding="utf-8")
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_lhb(tmp_path / "pit_cache")

        review = ClosingReviewer(
            ledger_path=str(ledger), paper_ledger_path=str(paper)
        ).review_today("2025-06-01")
        assert "龙虎榜关注" in review.longhubang_section

    def test_default_path_resolves_via_runtime_root(self, tmp_path, monkeypatch) -> None:
        """csv_path=None ⇒ 走 <runtime>/pit_cache/longhubang.csv（写读同源）。"""
        from aqsp.briefing.closing_review import build_longhubang_section

        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_lhb(tmp_path / "pit_cache")
        assert "龙虎榜关注" in build_longhubang_section()


# ---- 财经快讯段（pit_cache/cls_news.csv，0 读者断链打通）----

_NEWS_HEADER = "item_id,title,summary,ctime,level,subjects"

_NEWS_BODY_LINES = [
    "n1,央行宣布降准0.5个百分点,释放长期资金约1万亿元,2026-09-27T09:30:00+08:00,A,\"('货币政策', '降准')\"",
    "n2,某新能源龙头获大单海外订单,订单金额创新高,2026-09-27T10:15:00+08:00,B,\"('新能源', '出口')\"",
    "n3,半导体板块盘中异动拉升,多股涨停,2026-09-27T11:00:00+08:00,C,\"('半导体',)\"",
    "n4,证监会发文规范量化交易,明确高频交易限制,2026-09-26T18:45:00+08:00,A,()",
    "n5,北向资金今日净买入,外资加仓核心资产,2026-09-27T14:20:00+08:00,B,\"('北向资金', '核心资产')\"",
]


def _write_news(tmp_path, mtime_offset_days: float = 0.0) -> Path:
    """写 cls_news.csv 夹具（5 条：A×2 / B×2 / C×1，ctime 跨 09-26~09-27）。"""
    p = tmp_path / "cls_news.csv"
    p.write_text(_NEWS_HEADER + "\n" + "\n".join(_NEWS_BODY_LINES) + "\n", encoding="utf-8")
    if mtime_offset_days:
        import os as _os
        import time as _t

        old = _t.time() - mtime_offset_days * 86400
        _os.utime(p, (old, old))
    return p


class TestNewsSection:
    def test_render_when_present(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_news_section

        path = _write_news(tmp_path)
        section = build_news_section(str(path))
        assert "财经快讯" in section
        # 共 5 条，A 级 2 / B 级 2 / C 级 1
        assert "共 5 条｜A 级 2 / B 级 2 / C 级 1" in section
        assert "最新快讯：" in section
        # 近到远：n5(14:20) 居首
        assert "[A] 09-27 09:30 央行宣布降准0.5个百分点（货币政策、降准）" in section
        assert "[A] 09-26 18:45 证监会发文规范量化交易" in section
        #  subjects 元组串被正确展开（去引号、转顿号）
        assert "（货币政策、降准）" in section
        # 红线条款
        assert "不改变打分/排序/下单" in section

    def test_top_n_param(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_news_section

        path = _write_news(tmp_path)
        section = build_news_section(str(path), top_n=3)
        # 最新 3 条 = n5 / n3 / n2（按 ctime desc）
        assert "[B] 09-27 14:20 北向资金今日净买入" in section
        assert "[C] 09-27 11:00 半导体板块盘中异动拉升" in section
        assert "[B] 09-27 10:15 某新能源龙头获大单海外订单" in section
        # 较旧的不进 Top3
        assert "央行宣布降准" not in section
        assert "证监会发文规范量化交易" not in section

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_news_section

        assert build_news_section(str(tmp_path / "nope.csv")) == ""

    def test_wrong_header_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_news_section

        path = tmp_path / "cls_news.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        assert build_news_section(str(path)) == ""

    def test_empty_body_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_news_section

        path = tmp_path / "cls_news.csv"
        path.write_text(_NEWS_HEADER + "\n", encoding="utf-8")
        assert build_news_section(str(path)) == ""

    def test_malformed_rows_dropped_not_fatally(self, tmp_path) -> None:
        """坏行（缺 title / 缺 ctime）只剔除该行，不拖垮整段。"""
        from aqsp.briefing.closing_review import build_news_section

        path = tmp_path / "cls_news.csv"
        path.write_text(
            "\n".join(
                [
                    _NEWS_HEADER,
                    "x1,,摘要,2026-09-27T10:00:00+08:00,B,()",          # title 空 ⇒ 剔除
                    "x2,标题坏ctime,,,B,()",                              # ctime 空 ⇒ 剔除
                    "x3,正常标题,摘要,2026-09-27T10:00:00+08:00,A,('主题',)",  # 有效
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        section = build_news_section(str(path))
        assert "共 1 条" in section
        assert "正常标题" in section
        assert "标题坏ctime" not in section
        assert "只读参考" in section

    def test_stale_data_annotated(self, tmp_path) -> None:
        """CSV mtime 超 stale_hours ⇒ 段尾标「非实时」。"""
        from aqsp.briefing.closing_review import build_news_section

        path = _write_news(tmp_path, mtime_offset_days=10.0)
        section = build_news_section(str(path))
        assert "非实时" in section
        # 未陈旧（默认 48h 内）时不标注
        fresh = _write_news(tmp_path)
        assert "非实时" not in build_news_section(str(fresh))

    def test_empty_review_still_carries_news_section(
        self, tmp_path, monkeypatch
    ) -> None:
        """回归（与 09-26 IC 段同类缺陷防回归）：无信号走 _empty_review 早退时，
        财经快讯段不得被静默丢弃。"""
        from aqsp.briefing.closing_review import (
            ClosingReviewer,
            build_news_section,
            format_daily_review,
        )

        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        paper.write_text("", encoding="utf-8")
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_news(tmp_path / "pit_cache")

        review = ClosingReviewer(
            ledger_path=str(ledger), paper_ledger_path=str(paper)
        ).review_today("2025-06-01")
        assert "财经快讯" in review.news_section
        assert "财经快讯" in format_daily_review(review)
        # 交叉一致性：与直接调 build_news_section 一致
        assert review.news_section == build_news_section(
            str(tmp_path / "pit_cache" / "cls_news.csv")
        )

    def test_review_today_path_carries_news_section(self, tmp_path, monkeypatch) -> None:
        """正常 review_today 路径（有信号）也塞财经快讯段。"""
        from aqsp.briefing.closing_review import ClosingReviewer

        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        paper.write_text("", encoding="utf-8")
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_news(tmp_path / "pit_cache")

        review = ClosingReviewer(
            ledger_path=str(ledger), paper_ledger_path=str(paper)
        ).review_today("2025-06-01")
        assert "财经快讯" in review.news_section

    def test_default_path_resolves_via_runtime_root(self, tmp_path, monkeypatch) -> None:
        """csv_path=None ⇒ 走 <runtime>/pit_cache/cls_news.csv（写读同源）。"""
        from aqsp.briefing.closing_review import build_news_section

        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_news(tmp_path / "pit_cache")
        assert "财经快讯" in build_news_section()


# ===========================================================================
# 重大公告动态段（PR #253，打通 announcements.csv「被排雷层吃掉、未呈现」断链）
# ===========================================================================
_ANNOUNCE_HEADER = "symbol,name,notice_date,text"

# 6 条近窗口内（2026-09-21~09-27）+ 1 条超窗口（2026-09-01），覆盖各分类。
_ANNOUNCE_BODY_LINES = [
    "600095,湘财股份,2026-09-25,湘财股份:关于子公司收到中国证券监督管理委员会行政处罚事先告知书的公告",
    "000001,平安银行,2026-09-26,平安银行:关于以集中竞价方式回购公司股份的公告",
    "300750,宁德时代,2026-09-25,宁德时代:关于筹划重大资产重组暨停牌的公告",
    "600519,贵州茅台,2026-09-27,贵州茅台:2026年半年度利润分配及现金分红派息实施公告",
    "002594,比亚迪,2026-09-24,比亚迪:关于持股5%以上股东减持计划期限届满暨实施情况的公告",
    "601318,中国平安,2026-09-22,中国平安:关于中标某重大项目合同的公告",
    "600036,招商银行,2026-09-01,招商银行:2026年半年度业绩快报（营收同比正增长）",  # 超 7 日窗口 ⇒ 过滤
]


def _write_announcements(tmp_path, mtime_offset_days: float = 0.0) -> Path:
    """写 announcements.csv 夹具（6 近窗 + 1 超窗，覆盖监管处罚/回购/重组/分红/减持/中标）。"""
    p = tmp_path / "announcements.csv"
    p.write_text(
        _ANNOUNCE_HEADER + "\n" + "\n".join(_ANNOUNCE_BODY_LINES) + "\n",
        encoding="utf-8",
    )
    if mtime_offset_days:
        import os as _os
        import time as _t

        old = _t.time() - mtime_offset_days * 86400
        _os.utime(p, (old, old))
    return p


def _fixed_now(monkeypatch):
    """把 now_shanghai 钉到 2026-09-27，使 recent_days=7 窗 = 2026-09-20~09-27。"""
    from datetime import datetime

    from aqsp.briefing import closing_review

    monkeypatch.setattr(
        closing_review, "now_shanghai", lambda: datetime(2026, 9, 27, 16, 0, 0)
    )


class TestAnnouncementsSection:
    def test_render_when_present(self, tmp_path, monkeypatch) -> None:
        from aqsp.briefing.closing_review import build_announcements_section

        _fixed_now(monkeypatch)
        path = _write_announcements(tmp_path)
        section = build_announcements_section(str(path))
        assert "重大公告动态" in section
        # 超窗的 600036(09-01) 被过滤 ⇒ 仅 6 条
        assert "近 7 日共 6 条公告" in section
        # 分类计数（按关注度顺序）
        assert "监管处罚 1" in section
        assert "重组/并购 1" in section
        assert "减持 1" in section
        assert "增持/回购 1" in section
        assert "业绩" not in section  # 09-01 那条业绩被窗口过滤
        # 样本行含 symbol + 分类徽标
        assert "[监管处罚] 09-25 湘财股份（600095）" in section
        # 红线条款
        assert "不改变打分/排序/下单" in section

    def test_category_priority_regulatory_first(self, tmp_path, monkeypatch) -> None:
        """监管处罚类排在最前展示（关注度优先）。"""
        from aqsp.briefing.closing_review import build_announcements_section

        _fixed_now(monkeypatch)
        path = _write_announcements(tmp_path)
        section = build_announcements_section(str(path))
        reg_idx = section.find("监管处罚")
        div_idx = section.find("分红/送转")
        assert reg_idx != -1 and div_idx != -1
        assert reg_idx < div_idx

    def test_recent_days_filter(self, tmp_path, monkeypatch) -> None:
        """recent_days=2 ⇒ cutoff=09-25，含 09-25/26/27（湘财/宁德/平安/茅台=4 条），
        09-24/09-22 被过滤。"""
        from aqsp.briefing.closing_review import build_announcements_section

        _fixed_now(monkeypatch)
        path = _write_announcements(tmp_path)
        section = build_announcements_section(str(path), recent_days=2)
        assert "近 2 日共 4 条公告" in section
        assert "比亚迪" not in section  # 09-24 被过滤
        assert "中国平安" not in section  # 09-22 被过滤
        assert "湘财股份" in section  # 09-25 在窗内
        assert "平安银行" in section
        assert "贵州茅台" in section

    def test_top_n_param(self, tmp_path, monkeypatch) -> None:
        from aqsp.briefing.closing_review import build_announcements_section

        _fixed_now(monkeypatch)
        path = _write_announcements(tmp_path)
        section = build_announcements_section(str(path), top_n=3)
        # 每类最多 top_n//3=1 条，6 类各 1 条共 6 条样本，但顶部提示仅展示重点 3 条
        assert "仅展示重点 3 条" in section

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_announcements_section

        assert build_announcements_section(str(tmp_path / "nope.csv")) == ""

    def test_wrong_header_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_announcements_section

        path = tmp_path / "announcements.csv"
        path.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
        assert build_announcements_section(str(path)) == ""

    def test_empty_body_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_announcements_section

        path = tmp_path / "announcements.csv"
        path.write_text(_ANNOUNCE_HEADER + "\n", encoding="utf-8")
        assert build_announcements_section(str(path)) == ""

    def test_malformed_rows_dropped_not_fatally(self, tmp_path, monkeypatch) -> None:
        """坏行（缺 text / 缺 notice_date）只剔除该行，不拖垮整段。"""
        from aqsp.briefing.closing_review import build_announcements_section

        _fixed_now(monkeypatch)
        path = tmp_path / "announcements.csv"
        path.write_text(
            "\n".join(
                [
                    _ANNOUNCE_HEADER,
                    "600001,坏公司,,标题但缺披露日",            # notice_date 空 ⇒ 剔除
                    "600002,,2026-09-25,有日缺名字:股份回购实施情况公告",  # name 空但 symbol/text/notice 全 ⇒ 仍收录
                    "600003,好公司,2026-09-25,好公司:收到行政处罚事先告知书",  # 有效
                    ",空代码,2026-09-25,无代码公告",           # symbol 空 ⇒ 剔除
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        section = build_announcements_section(str(path))
        assert "近 7 日共 2 条公告" in section
        assert "好公司" in section
        assert "有日缺名字" in section
        assert "无代码公告" not in section
        assert "只读参考" in section

    def test_stale_data_annotated(self, tmp_path, monkeypatch) -> None:
        """CSV mtime 超 stale_hours ⇒ 段尾标「非实时」。"""
        from aqsp.briefing.closing_review import build_announcements_section

        _fixed_now(monkeypatch)
        path = _write_announcements(tmp_path, mtime_offset_days=10.0)
        section = build_announcements_section(str(path))
        assert "非实时" in section
        # 未陈旧（默认 72h 内）时不标注
        fresh = _write_announcements(tmp_path)
        assert "非实时" not in build_announcements_section(str(fresh))

    def test_empty_review_still_carries_announcements_section(
        self, tmp_path, monkeypatch
    ) -> None:
        """回归（与 09-26 IC 段同类缺陷防回归）：无信号走 _empty_review 早退时，
        重大公告段不得被静默丢弃。"""
        from aqsp.briefing.closing_review import (
            ClosingReviewer,
            format_daily_review,
        )

        _fixed_now(monkeypatch)
        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        paper.write_text("", encoding="utf-8")
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_announcements(tmp_path / "pit_cache")

        review = ClosingReviewer(
            ledger_path=str(ledger), paper_ledger_path=str(paper)
        ).review_today("2025-06-01")
        assert "重大公告动态" in review.announcements_section
        rendered = format_daily_review(review)
        assert "重大公告动态" in rendered

    def test_default_path_resolves_via_runtime_root(self, tmp_path, monkeypatch) -> None:
        """csv_path=None ⇒ 走 <runtime>/pit_cache/announcements.csv（写读同源）。"""
        from aqsp.briefing.closing_review import build_announcements_section

        _fixed_now(monkeypatch)
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_announcements(tmp_path / "pit_cache")
        assert "重大公告动态" in build_announcements_section()

    def test_routine_other_category_counted_but_not_listed(
        self, tmp_path, monkeypatch
    ) -> None:
        """#258 降噪：常规「其他」类只计入分类计数、不铺样本行；
        重点档仍铺行并在尾部注明隐藏量（含其他计数）。"""
        from aqsp.briefing.closing_review import build_announcements_section

        _fixed_now(monkeypatch)
        path = tmp_path / "announcements.csv"
        path.write_text(
            "\n".join(
                [
                    _ANNOUNCE_HEADER,
                    "600001,好公司,2026-09-25,好公司:收到行政处罚事先告知书",
                    "600002,杂公司A,2026-09-26,杂公司A:关于召开2026年第二次临时股东大会的通知",
                    "600003,杂公司B,2026-09-26,杂公司B:关于日常经营合同的公告",
                    "600004,杂公司C,2026-09-27,杂公司C:年度报告摘要",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        section = build_announcements_section(str(path))
        assert "近 7 日共 4 条公告" in section
        assert "监管处罚 1" in section
        assert "其他 2" in section
        # 重点行铺出，常规「其他」不铺样本行
        assert "[监管处罚] 09-25 好公司（600001）" in section
        assert "[其他]" not in section
        assert "股东大会" not in section
        assert "杂公司C" not in section  # 年度报告摘要 ⇒ 其他类，不铺行
        assert "仅展示重点 2 条，其余 2 条（含常规「其他」2 条）" in section

    def test_new_event_categories_matched(self, tmp_path, monkeypatch) -> None:
        """#258 扩词典：诉讼/仲裁、募资/再融资、高管/股东变动 命中对应新类。"""
        from aqsp.briefing.closing_review import (
            _ANNOUNCE_ROUTINE,
            _categorize_announcement,
        )

        assert _categorize_announcement("X:关于涉及重大诉讼暨公司收到起诉状的公告") == "诉讼/仲裁"
        assert _categorize_announcement("X:关于向特定对象发行股票募集资金的公告") == "募资/再融资"
        assert _categorize_announcement("X:关于副董事长辞职暨聘任新高管的公告") == "高管/股东变动"
        assert _categorize_announcement("X:关于控股股东质押部分股份的公告") == "高管/股东变动"
        # 优先级：监管处罚仍压过新类；未命中任何类 ⇒ 常规档
        assert _categorize_announcement("X:涉嫌违法违规被立案调查暨涉诉讼") == "监管处罚"
        assert _categorize_announcement("X:关于召开股东大会的通知") == "其他"
        assert "其他" in _ANNOUNCE_ROUTINE


# ===========================================================================
# 筹码集中度异动段（PR #257，holder_count 切历史报表后数据已具备 ≥2 连续季度）
# ===========================================================================
_HOLDER_HEADER = "symbol,name,quarter,holder_count,notice_date"

# 2 季样本：集中×2（茅台 -20%/宁德 -10%）、分散×2（平安 +20%/中芯 +10%）、
# 仅最新季×1（招行，无环比 → 跳过）、仅旧季×1（万科，停更 → 剔除）、
# 旧季 0 户×1（工行，除零 → 跳过）。
_HOLDER_BODY_LINES = [
    "600519,贵州茅台,2026-03-31,100000,2026-04-25",
    "600519,贵州茅台,2026-06-30,80000,2026-08-20",
    "000001,平安银行,2026-03-31,50000,2026-04-20",
    "000001,平安银行,2026-06-30,60000,2026-08-18",
    "300750,宁德时代,2026-03-31,40000,2026-04-26",
    "300750,宁德时代,2026-06-30,36000,2026-08-22",
    "688981,中芯国际,2026-03-31,30000,2026-04-28",
    "688981,中芯国际,2026-06-30,33000,2026-08-15",
    "600036,招商银行,2026-06-30,45000,2026-08-19",
    "000002,万科A,2026-03-31,99999,2026-04-22",
    "601398,工商银行,2026-03-31,0,2026-04-24",
    "601398,工商银行,2026-06-30,50000,2026-08-16",
]


def _write_holder(tmp_path, mtime_offset_days: float = 0.0) -> Path:
    p = tmp_path / "holder_count.csv"
    p.write_text(
        _HOLDER_HEADER + "\n" + "\n".join(_HOLDER_BODY_LINES) + "\n",
        encoding="utf-8",
    )
    if mtime_offset_days:
        import os as _os
        import time as _t

        old = _t.time() - mtime_offset_days * 86400
        _os.utime(p, (old, old))
    return p


class TestHolderConcentrationSection:
    def test_render_when_present(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_holder_concentration_section

        path = _write_holder(tmp_path)
        section = build_holder_concentration_section(str(path))
        assert "股东户数异动（筹码集中度 · 近披露窗口）" in section
        assert (
            "可比 4 只（连续 2 季）｜筹码集中（户数降）2 / 筹码分散（户数升）2"
            "｜最新季度 2026-06-30" in section
        )
        assert "▼ 贵州茅台（600519）｜-20.0%｜100,000 → 80,000 户" in section
        assert "▼ 宁德时代（300750）｜-10.0%" in section
        assert "▲ 平安银行（000001）｜+20.0%" in section
        assert "筹码集中 Top（户数降幅）：" in section
        assert "筹码分散 Top（户数增幅）：" in section
        assert "不改变打分/排序/下单" in section

    def test_stale_and_edge_symbols_excluded(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_holder_concentration_section

        path = _write_holder(tmp_path)
        section = build_holder_concentration_section(str(path))
        # 招行仅最新季（无环比）、万科停更（最新季 ≠ 数据集最新季）、
        # 工行上一季 0 户（除零）均不进可比样本
        assert "招商银行" not in section
        assert "万科A" not in section
        assert "工商银行" not in section

    def test_top_n_param(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_holder_concentration_section

        path = _write_holder(tmp_path)
        section = build_holder_concentration_section(str(path), top_n=1)
        # 集中 Top 只剩降幅最大的茅台；分散 Top = max(1, 1//3)=1 只（平安）
        assert "贵州茅台" in section
        assert "宁德时代" not in section
        assert "平安银行" in section
        assert "中芯国际" not in section

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_holder_concentration_section

        assert build_holder_concentration_section(str(tmp_path / "nope.csv")) == ""

    def test_wrong_header_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_holder_concentration_section

        path = tmp_path / "holder_count.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        assert build_holder_concentration_section(str(path)) == ""

    def test_empty_body_returns_empty(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_holder_concentration_section

        path = tmp_path / "holder_count.csv"
        path.write_text(_HOLDER_HEADER + "\n", encoding="utf-8")
        assert build_holder_concentration_section(str(path)) == ""

    def test_malformed_rows_dropped_not_fatally(self, tmp_path) -> None:
        """坏行（缺 symbol / quarter / count 非数）只剔除该行，不拖垮整段。"""
        from aqsp.briefing.closing_review import build_holder_concentration_section

        path = tmp_path / "holder_count.csv"
        path.write_text(
            "\n".join(
                [
                    _HOLDER_HEADER,
                    ",坏行缺代码,2026-06-30,100,2026-08-01",          # symbol 空 ⇒ 剔除
                    "600001,坏行缺季度,,100,2026-08-01",               # quarter 空 ⇒ 剔除
                    "600002,坏行count非数,2026-06-30,abc,2026-08-01",  # count 坏 ⇒ 剔除
                    "600003,好公司,2026-03-31,1000,2026-04-01",
                    "600003,好公司,2026-06-30,900,2026-08-01",         # 有效：-10.0%
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        section = build_holder_concentration_section(str(path))
        assert "可比 1 只" in section
        assert "▼ 好公司（600003）｜-10.0%｜1,000 → 900 户" in section
        assert "只读参考" in section

    def test_stale_data_annotated(self, tmp_path) -> None:
        from aqsp.briefing.closing_review import build_holder_concentration_section

        path = _write_holder(tmp_path, mtime_offset_days=10.0)
        section = build_holder_concentration_section(str(path))
        assert "非实时" in section
        fresh = _write_holder(tmp_path)
        assert "非实时" not in build_holder_concentration_section(str(fresh))

    def test_empty_review_still_carries_holder_section(
        self, tmp_path, monkeypatch
    ) -> None:
        """回归：无信号走 _empty_review 早退时，筹码段不得被静默丢弃。"""
        from aqsp.briefing.closing_review import (
            ClosingReviewer,
            format_daily_review,
        )

        ledger = tmp_path / "predictions.jsonl"
        paper = tmp_path / "paper_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        paper.write_text("", encoding="utf-8")
        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_holder(tmp_path / "pit_cache")

        review = ClosingReviewer(
            ledger_path=str(ledger), paper_ledger_path=str(paper)
        ).review_today("2025-06-01")
        assert "股东户数异动" in review.holder_concentration_section
        rendered = format_daily_review(review)
        assert "股东户数异动" in rendered

    def test_default_path_resolves_via_runtime_root(self, tmp_path, monkeypatch) -> None:
        """csv_path=None ⇒ 走 <runtime>/pit_cache/holder_count.csv（写读同源）。"""
        from aqsp.briefing.closing_review import build_holder_concentration_section

        monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
        (tmp_path / "pit_cache").mkdir()
        _write_holder(tmp_path / "pit_cache")
        assert "股东户数异动" in build_holder_concentration_section()
