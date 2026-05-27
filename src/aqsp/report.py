from __future__ import annotations

from dataclasses import asdict

import pandas as pd

from aqsp.models import PickResult


def to_dataframe(picks: list[PickResult]) -> pd.DataFrame:
    rows = []
    for pick in picks:
        row = asdict(pick)
        row["reasons"] = "；".join(pick.reasons)
        row["risks"] = "；".join(pick.risks)
        row.update(pick.metrics)
        del row["metrics"]
        rows.append(row)
    return pd.DataFrame(rows)


def to_markdown(picks: list[PickResult], title: str = "AI 量化选股报告") -> str:
    lines = [f"# {title}", ""]
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
                f"- 仓位建议: {pick.position}",
                f"- 止损/止盈: {pick.stop_loss} / {pick.take_profit}",
                f"- 理由: {'；'.join(pick.reasons) or '无'}",
                f"- 风险: {'；'.join(pick.risks) or '无'}",
                "",
            ]
        )
    lines.append("> 仅供研究，不构成投资建议。")
    return "\n".join(lines)
