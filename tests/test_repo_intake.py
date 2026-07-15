from __future__ import annotations

import json

from aqsp.research.repo_intake import (
    build_repo_backlog,
    classify_repo,
    load_repo_intake,
    render_repo_backlog_markdown,
    summarize_repo_intake,
)


def test_classify_repo_routes_data_backtest_and_execution_boundaries() -> None:
    data_item = classify_repo(
        {
            "fullName": "OpenBB-finance/OpenBB",
            "description": "Financial data platform for analysts and AI agents.",
            "stargazersCount": 70000,
        }
    )
    backtest_item = classify_repo(
        {
            "fullName": "mementum/backtrader",
            "description": "Python Backtesting library for trading strategies",
        }
    )
    execution_item = classify_repo(
        {
            "fullName": "freqtrade/freqtrade",
            "description": "Free crypto trading bot with live trading",
        }
    )

    assert data_item.lane == "data_source"
    assert data_item.stage == "substrate_candidate"
    assert backtest_item.lane == "backtest_validation"
    assert execution_item.lane == "execution_boundary"
    assert execution_item.stage == "reject_boundary"


def test_load_repo_intake_dedupes_and_summarizes_sources(tmp_path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        json.dumps(
            [
                {
                    "fullName": "OpenBB-finance/OpenBB",
                    "description": "Financial data platform",
                    "stargazersCount": 10,
                },
                {
                    "fullName": "freqtrade/freqtrade",
                    "description": "crypto trading bot",
                    "stargazersCount": 20,
                },
            ]
        ),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            [
                {
                    "fullName": "OpenBB-finance/OpenBB",
                    "description": "Financial data platform",
                    "stargazersCount": 30,
                }
            ]
        ),
        encoding="utf-8",
    )

    items = load_repo_intake((first, second))
    summary = summarize_repo_intake(items)

    assert len(items) == 2
    assert {item.full_name: item.stars for item in items}["OpenBB-finance/OpenBB"] == 30
    assert summary.total == 2
    assert summary.lane_counts["data_source"] == 1
    assert summary.stage_counts["reject_boundary"] == 1


def test_build_repo_backlog_caps_each_lane_and_assigns_landing() -> None:
    items = tuple(
        classify_repo(raw)
        for raw in (
            {
                "fullName": "OpenBB-finance/OpenBB",
                "description": "Financial data platform",
                "stargazersCount": 70000,
                "url": "https://github.com/OpenBB-finance/OpenBB",
            },
            {
                "fullName": "mementum/backtrader",
                "description": "Python Backtesting library",
                "stargazersCount": 20000,
            },
            {
                "fullName": "akfamily/akshare",
                "description": "financial data interface",
                "stargazersCount": 19000,
            },
        )
    )

    backlog = build_repo_backlog(items, limit_per_lane=1)

    by_repo = {item.repo: item for item in backlog}

    assert set(by_repo) == {"OpenBB-finance/OpenBB", "mementum/backtrader"}
    assert by_repo["OpenBB-finance/OpenBB"].priority == "P1"
    assert (
        by_repo["OpenBB-finance/OpenBB"].landing
        == "config/data_sources.yaml + aqsp.data.source_catalog"
    )


def test_render_repo_backlog_markdown_outputs_reviewable_table() -> None:
    items = (
        classify_repo(
            {
                "fullName": "OpenBB-finance/OpenBB",
                "description": "Financial data platform",
                "stargazersCount": 70000,
                "url": "https://github.com/OpenBB-finance/OpenBB",
            }
        ),
    )

    markdown = render_repo_backlog_markdown(items)

    assert "# Repo Intake Backlog" in markdown
    assert "| P1 | data_source | [OpenBB-finance/OpenBB]" in markdown


def test_repo_intake_loads_existing_scan_files() -> None:
    items = load_repo_intake(
        (
            "docs/research/repo_radar_raw.json",
            "_external/archive/repo-scout-2026-06-04/recent_repos_manifest_2026-06-04.json",
        )
    )
    summary = summarize_repo_intake(items)

    assert summary.total >= 250
    assert summary.stage_counts["substrate_candidate"] > 100
    assert summary.stage_counts["reject_boundary"] > 0
    assert any(item.full_name == "OpenBB-finance/OpenBB" for item in items)
