"""debate ↔ 实盘战绩对账（只读）。

把 LLM/规则 debate 层的「行为影响」（debate_action_influence）与 ledger 里
**已实现**的 P&L 对齐，量化「debate 层到底在帮忙还是添乱」。纯只读、可降级：
- 读 ledger 既有列，绝不写回打分 / 排序 / 下单（红线）；
- 无 debate 字段 / 样本不足时显式输出「无可度量样本」，不崩、不造数。

核心判读轴 = ``debate_action_influence``（值域见 aqsp.portfolio.manager）：
    none / no_debate   —— debate 未介入（或 LLM 关闭走纯规则）
    rules_only         —— 仅规则层裁决
    blocked            —— debate 风险否决 → 信号被拦
    downgraded         —— debate 降级（如 bearish 共识 → 降置信）

判读逻辑：若「干预桶（blocked/downgraded）」的平均收益优于「未干预桶
（none/no_debate/rules_only）」⇒ debate 的防守动作在避险（价值为正）；
反之 ⇒ debate 在添乱。这是 R3 alpha 赤字诊断里「debate 层归因」的量化读数。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from aqsp.ledger.base import read_ledger

# 桶的展示顺序（业务语义：从无介入 → 干预递增）
_BUCKET_ORDER = [
    "none",
    "no_debate",
    "rules_only",
    "downgraded",
    "blocked",
]
_BUCKET_LABELS = {
    "none": "无介入（none）",
    "no_debate": "无 debate（no_debate）",
    "rules_only": "仅规则裁决（rules_only）",
    "downgraded": "降级（downgraded）",
    "blocked": "拦截（blocked）",
    "": "（未记录）",
}
# 「干预」桶：debate 实际对仓位做了防守动作
_INTERVENTION_BUCKETS = {"blocked", "downgraded"}


@dataclass(frozen=True)
class DebateReconciliationRow:
    bucket: str  # debate_action_influence 原值
    count: int  # 该桶已实现信号数
    win_count: int
    win_rate: float  # 0~1
    avg_return: float  # 平均 return_pct（%）
    total_return: float  # 累计 return_pct（%，简单加总，非复利）


@dataclass(frozen=True)
class DebateReconciliation:
    """一次对账结果：分桶行 + 干预/未干预对比结论。"""

    rows: tuple[DebateReconciliationRow, ...]
    intervention_avg: float | None  # 干预桶（blocked/downgraded）平均收益
    non_intervention_avg: float | None  # 未干预桶平均收益
    has_sample: bool


def _finite_float(value: object) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf 防御
        return None
    return f


def _bucket_key(raw: str) -> str:
    """归一桶键：已知 5 值用原值，未知/空统一为 ''（归「（未记录）」桶）。"""
    return raw if raw in _BUCKET_ORDER else ""


def reconcile_debate(
    ledger_df: pd.DataFrame,
    *,
    since_date: str | None = None,
) -> DebateReconciliation:
    """按 debate_action_influence 对已实现信号分桶统计战绩。"""
    rows = ledger_df.to_dict("records") if not ledger_df.empty else []
    if since_date:
        rows = [
            r
            for r in rows
            if str(r.get("signal_date", "") or "") >= since_date
        ]

    agg: dict[str, list[float]] = {}
    for row in rows:
        # 只统计「已实现」信号：有可解析 return_pct 且非 not_executable/pending
        if str(row.get("status", "") or "") in ("not_executable", "pending"):
            continue
        ret = _finite_float(row.get("return_pct"))
        if ret is None:
            continue
        raw = str(row.get("debate_action_influence", "") or "").strip()
        key = _bucket_key(raw if raw in _BUCKET_ORDER else "")
        agg.setdefault(key, []).append(ret)

    if not any(agg.values()):
        return DebateReconciliation(
            rows=(), intervention_avg=None, non_intervention_avg=None, has_sample=False
        )

    ordered_keys = [k for k in _BUCKET_ORDER if k in agg]
    # 未记录桶（空键）若存在，放最后
    if "" in agg:
        ordered_keys.append("")

    built: list[DebateReconciliationRow] = []
    for key in ordered_keys:
        rets = agg[key]
        n = len(rets)
        wins = sum(1 for r in rets if r > 0)
        avg = sum(rets) / n if n else 0.0
        built.append(
            DebateReconciliationRow(
                bucket=key,
                count=n,
                win_count=wins,
                win_rate=(wins / n) if n else 0.0,
                avg_return=round(avg, 3),
                total_return=round(sum(rets), 3),
            )
        )

    # 干预 vs 未干预 平均收益对比（判读 debate 防守动作是否真的避险）
    def _avg(keys: set[str]) -> float | None:
        pool = [r for k in keys for r in agg.get(k, [])]
        return round(sum(pool) / len(pool), 3) if pool else None

    return DebateReconciliation(
        rows=tuple(built),
        intervention_avg=_avg(_INTERVENTION_BUCKETS),
        non_intervention_avg=_avg(set(_BUCKET_ORDER) - _INTERVENTION_BUCKETS),
        has_sample=True,
    )


def reconcile_debate_from_file(
    ledger_path: str,
    *,
    since_date: str | None = None,
) -> DebateReconciliation:
    rows = read_ledger(ledger_path)
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    return reconcile_debate(df, since_date=since_date)


def format_debate_reconciliation(
    rec: DebateReconciliation, window_days: int | None = None
) -> str:
    """渲染成可直插报告的 markdown。无样本/无 debate 字段时显式说明，不造数。"""
    head = (
        f"## debate ↔ 实盘战绩对账（近 {window_days} 天窗口）"
        if window_days is not None
        else "## debate ↔ 实盘战绩对账"
    )
    if not rec.has_sample or not rec.rows:
        return f"{head}\n\n无可度量的已实现样本（ledger 中无带 return_pct 的终态信号，或窗口内无数据）。"

    lines = [head, ""]
    lines.append(
        "| debate 行为 | 信号数 | 胜率 | 平均收益 | 累计收益 |"
    )
    lines.append("|---|---|---|---|---|")
    for row in rec.rows:
        label = _BUCKET_LABELS.get(row.bucket, row.bucket or "（未记录）")
        lines.append(
            f"| {label} | {row.count} | {row.win_rate:.0%} "
            f"| {row.avg_return:+.2f}% | {row.total_return:+.2f}% |"
        )
    lines.append("")

    # 判读：干预桶 vs 未干预桶
    ia, nia = rec.intervention_avg, rec.non_intervention_avg
    if ia is None or nia is None:
        lines.append(
            "对账提示：debate 行为分布不足以做「干预 vs 未干预」对比（至少需两类各 1 条）。"
        )
    else:
        delta = round(ia - nia, 2)
        if delta > 0:
            verdict = (
                f"debate 干预桶（blocked/downgraded）平均收益 {ia:+.2f}% 优于未干预桶 "
                f"{nia:+.2f}%（Δ {delta:+.2f}pp）⇒ 防守动作在避险，价值为正。"
            )
        elif delta < 0:
            verdict = (
                f"debate 干预桶（blocked/downgraded）平均收益 {ia:+.2f}% 劣于未干预桶 "
                f"{nia:+.2f}%（Δ {delta:+.2f}pp）⇒ 需复核 debate 是否在误杀优质信号（添乱）。"
            )
        else:
            verdict = (
                f"debate 干预桶与未干预桶平均收益持平（均 {ia:+.2f}%）⇒ 该窗口内 debate 无显著增/减益。"
            )
        lines.append(f"**对账判读**：{verdict}")
    return "\n".join(lines)
