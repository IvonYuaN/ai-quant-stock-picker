from __future__ import annotations

import pytest

from aqsp.strategies.catalog import load_strategy_catalog


def test_load_strategy_catalog_reads_absorbed_sources() -> None:
    catalog = load_strategy_catalog()

    by_id = catalog.by_id()

    assert catalog.version
    assert by_id["volume_breakout"].absorbed_from
    assert "次日开盘成交验证" in by_id["volume_breakout"].validation_required
    assert by_id["execution_risk_filter"].runtime_ready is False


def test_load_strategy_catalog_rejects_missing_validation(tmp_path) -> None:
    path = tmp_path / "strategy_sources.yaml"
    path.write_text(
        """
version: test
families:
  - id: horizon_news
    name: Horizon 消息雷达
    hypothesis: 消息只能作为候选上下文，不能直接覆盖分数。
    current_status: research_absorbed
    absorbed_from:
      - virattt/ai-hedge-fund
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing validation_required"):
        load_strategy_catalog(path)


def test_load_strategy_catalog_rejects_duplicate_ids(tmp_path) -> None:
    path = tmp_path / "strategy_sources.yaml"
    path.write_text(
        """
version: test
families:
  - id: duplicate
    name: A
    hypothesis: 有明确经济假设。
    current_status: research_absorbed
    absorbed_from: [repo/a]
    validation_required: [walk-forward]
  - id: duplicate
    name: B
    hypothesis: 有明确经济假设。
    current_status: research_absorbed
    absorbed_from: [repo/b]
    validation_required: [walk-forward]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate strategy catalog id"):
        load_strategy_catalog(path)
