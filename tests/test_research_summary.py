from __future__ import annotations

from pathlib import Path

from aqsp.research.summary import (
    load_research_summary,
    research_findings_badge,
    research_findings_display,
    research_findings_metric,
)


def test_load_research_summary_uses_config_when_absorption_json_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    strategy_sources = tmp_path / "strategy_sources.yaml"
    data_sources = tmp_path / "data_sources.yaml"
    strategy_sources.write_text(
        """
version: "test"
families:
  - id: multi_factor_quality_value_momentum
    name: 多因子质量/价值/动量
    current_status: research_absorbed
    absorbed_from: ["repo/a"]
    runtime_gate:
      - 先作为 report-only 因子，不参与总分
  - id: rps_relative_strength
    name: RPS 相对强度
    current_status: implemented_partial
""",
        encoding="utf-8",
    )
    data_sources.write_text(
        """
version: "test"
sources:
  - id: tushare
    name: Tushare Pro
    runtime_ready: false
    research_status: next_adapter
    adoption_gate:
      - token 只读 TUSHARE_TOKEN 环境变量
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    summary = load_research_summary(
        absorption_path=tmp_path / "missing_research_absorption.json",
        strategy_sources_path=strategy_sources,
        data_sources_path=data_sources,
    )

    assert summary is not None
    assert summary.total_findings == 0
    assert research_findings_display(summary) == "未落盘（按配置吸收队列展示）"
    assert research_findings_metric(summary) == "配置队列"
    assert research_findings_badge(summary) == "config-backed"
    assert summary.pipeline_summaries == ()
    assert summary.implemented_family_count == 1
    assert summary.report_only_family_count == 1
    assert summary.absorbed_families[0].name == "多因子质量/价值/动量"
    assert summary.next_actions[0].item_id == "tushare"
    assert summary.prereq_items[0].status == "needs_env"
    assert summary.prereq_items[0].missing_env_vars == ("TUSHARE_TOKEN",)


def test_load_research_summary_returns_none_when_core_configs_missing(
    tmp_path: Path,
) -> None:
    summary = load_research_summary(
        absorption_path=tmp_path / "missing.json",
        strategy_sources_path=tmp_path / "missing_strategy.yaml",
        data_sources_path=tmp_path / "missing_data.yaml",
    )

    assert summary is None
