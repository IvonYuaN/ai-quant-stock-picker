from __future__ import annotations

from dataclasses import asdict

import pandas as pd

from aqsp.core.types import RunMetadata
from aqsp.models import PickResult

RESULT_COLUMNS = [
    "symbol",
    "name",
    "date",
    "close",
    "score",
    "rating",
    "entry_type",
    "ideal_buy",
    "stop_loss",
    "take_profit",
    "position",
    "strategies",
    "reasons",
    "risks",
]


def to_dataframe(picks: list[PickResult]) -> pd.DataFrame:
    rows = []
    for pick in picks:
        row = asdict(pick)
        row["strategies"] = ",".join(pick.strategies)
        row["reasons"] = "；".join(pick.reasons)
        row["risks"] = "；".join(pick.risks)
        row.update(pick.metrics)
        del row["metrics"]
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    return pd.DataFrame(rows)


def to_markdown(
    picks: list[PickResult],
    title: str = "AI 量化选股报告",
    metadata: RunMetadata | None = None,
) -> str:
    lines = [f"# {title}", ""]
    if metadata is not None:
        lines.extend(_metadata_lines(metadata))
    if not picks:
        lines.append("无符合条件的候选。")
        return "\n".join(lines)

    for idx, pick in enumerate(picks, 1):
        display = f"{pick.symbol} {pick.name}".strip()
        lines.extend(
            [
                f"## {idx}. {display}",
                f"- 日期: {pick.date}",
                f"- 评分: {pick.score} / {pick.rating}",
                f"- 收盘/参考买点: {pick.close} / {pick.ideal_buy}",
                f"- 策略入口: {pick.entry_type}",
                f"- 命中策略: {', '.join(pick.strategies) or '无'}",
                f"- 仓位建议: {pick.position}",
                f"- 止损/止盈: {pick.stop_loss} / {pick.take_profit}",
                f"- 理由: {'；'.join(pick.reasons) or '无'}",
                f"- 风险: {'；'.join(pick.risks) or '无'}",
                "",
            ]
        )
    lines.append("> 仅供研究，不构成投资建议。")
    return "\n".join(lines)


def _metadata_lines(metadata: RunMetadata) -> list[str]:
    actual = metadata.actual_source or "unknown"
    source_text = (
        actual
        if metadata.requested_source == actual
        else f"{metadata.requested_source} -> {actual}"
    )
    return [
        "## 运行参数",
        f"- 数据源: {source_text}",
        (
            "- 数据层级: "
            f"fresh={metadata.source_freshness_tier or 'unknown'} / "
            f"cover={metadata.source_coverage_tier or 'unknown'} / "
            f"local={metadata.source_local_status or 'unknown'}"
        ),
        (
            "- 数据时效: "
            f"latest={metadata.data_latest_trade_date or 'unknown'} / "
            f"lag={metadata.data_lag_days}d"
        ),
        (
            "- 数据健康: "
            f"{metadata.source_health_label or 'unknown'}"
            + (
                f" / {metadata.source_health_message}"
                if metadata.source_health_message
                else ""
            )
        ),
        (
            "- 候选池: "
            f"显式 {metadata.explicit_symbol_count} / "
            f"解析 {metadata.resolved_symbol_count} / "
            f"取数 {metadata.fetched_frame_count} / "
            f"筛选前 {metadata.screened_count} / "
            f"最终 {metadata.final_count}"
        ),
        f"- max_universe: {metadata.max_universe}",
        f"- 价格边界: {metadata.min_price} - {metadata.max_price}",
        f"- 20日均成交额下限: {metadata.min_avg_amount:.0f}",
        f"- 在线观察因子: {'on' if metadata.online_factors_enabled else 'off'}",
        f"- thresholds.version: {metadata.thresholds_version}",
        f"- regime: {metadata.regime or 'unknown'}",
        "",
    ]
