"""选股快照对比 - 记录和对比每日候选股变化"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aqsp.core.time import now_shanghai


@dataclass(frozen=True)
class PickSnapshot:
    symbol: str
    name: str
    score: float
    rank: int
    adjusted_score: float
    recommended_adjustment: str


@dataclass(frozen=True)
class SnapshotDiff:
    date_current: str
    date_previous: str
    new_picks: tuple[PickSnapshot, ...]
    removed_picks: tuple[PickSnapshot, ...]
    rank_changes: tuple[tuple[str, int, int], ...]  # (symbol, old_rank, new_rank)
    score_changes: tuple[
        tuple[str, float, float], ...
    ]  # (symbol, old_score, new_score)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.new_picks
            or self.removed_picks
            or self.rank_changes
            or self.score_changes
        )


def save_snapshot(
    picks: list[Any],
    snapshot_path: str = "data/pick_snapshots.jsonl",
    date: str | None = None,
) -> None:
    """保存每日选股快照"""

    if date is None:
        date = now_shanghai().date().isoformat()

    snapshot = {
        "date": date,
        "created_at": now_shanghai().isoformat(timespec="seconds"),
        "picks": [
            {
                "symbol": p.symbol,
                "name": p.name,
                "score": p.score,
                "adjusted_score": getattr(p, "adjusted_score", p.score),
                "recommended_adjustment": getattr(p, "recommended_adjustment", "keep"),
            }
            for p in picks
        ],
    }

    path = Path(snapshot_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 读取现有快照，按日期去重
    snapshots: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    data = json.loads(line)
                    snapshots[data["date"]] = data
                except (json.JSONDecodeError, KeyError):
                    pass

    snapshots[date] = snapshot

    # 只保留最近30天
    cutoff = (now_shanghai() - timedelta(days=30)).strftime("%Y-%m-%d")
    snapshots = {k: v for k, v in snapshots.items() if k >= cutoff}

    with open(path, "w", encoding="utf-8") as f:
        for data in sorted(snapshots.values(), key=lambda x: x["date"]):
            f.write(json.dumps(data, ensure_ascii=False) + "\n")


def load_snapshot(
    date: str,
    snapshot_path: str = "data/pick_snapshots.jsonl",
) -> list[PickSnapshot] | None:
    """加载指定日期的快照"""
    path = Path(snapshot_path)
    if not path.exists():
        return None

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            if data.get("date") == date:
                return [
                    PickSnapshot(
                        symbol=p["symbol"],
                        name=p["name"],
                        score=p["score"],
                        rank=idx + 1,
                        adjusted_score=p.get("adjusted_score", p["score"]),
                        recommended_adjustment=p.get("recommended_adjustment", "keep"),
                    )
                    for idx, p in enumerate(data["picks"])
                ]
        except (json.JSONDecodeError, KeyError):
            pass

    return None


def compare_snapshots(
    current_date: str,
    previous_date: str | None = None,
    snapshot_path: str = "data/pick_snapshots.jsonl",
) -> SnapshotDiff | None:
    """对比两个日期的快照"""
    if previous_date is None:
        previous_date = (
            datetime.strptime(current_date, "%Y-%m-%d") - timedelta(days=7)
        ).strftime("%Y-%m-%d")

    current = load_snapshot(current_date, snapshot_path)
    previous = load_snapshot(previous_date, snapshot_path)

    if current is None or previous is None:
        return None

    current_symbols = {p.symbol: p for p in current}
    previous_symbols = {p.symbol: p for p in previous}

    new_picks = tuple(p for p in current if p.symbol not in previous_symbols)

    removed_picks = tuple(p for p in previous if p.symbol not in current_symbols)

    rank_changes = []
    score_changes = []
    for p in current:
        if p.symbol in previous_symbols:
            prev = previous_symbols[p.symbol]
            if prev.rank != p.rank:
                rank_changes.append((p.symbol, prev.rank, p.rank))
            if abs(prev.score - p.score) > 0.01:
                score_changes.append((p.symbol, prev.score, p.score))

    return SnapshotDiff(
        date_current=current_date,
        date_previous=previous_date,
        new_picks=new_picks,
        removed_picks=removed_picks,
        rank_changes=tuple(rank_changes),
        score_changes=tuple(score_changes),
    )


def format_snapshot_diff(diff: SnapshotDiff) -> str:
    """格式化快照对比结果"""
    lines = []
    lines.append(f"📊 候选股变化（{diff.date_previous} → {diff.date_current}）")
    lines.append("━" * 40)

    if diff.new_picks:
        lines.append("")
        lines.append("🆕 新增候选:")
        for p in diff.new_picks:
            adj = ""
            if p.recommended_adjustment == "raise":
                adj = " ↑"
            elif p.recommended_adjustment == "lower":
                adj = " ↓"
            lines.append(f"   + {p.symbol} {p.name} ({p.score:.1f}分){adj}")

    if diff.removed_picks:
        lines.append("")
        lines.append("❌ 移出候选:")
        for p in diff.removed_picks:
            lines.append(f"   - {p.symbol} {p.name} (原{p.score:.1f}分)")

    if diff.rank_changes:
        lines.append("")
        lines.append("📈 排名变化:")
        for symbol, old_rank, new_rank in diff.rank_changes:
            direction = "↑" if new_rank < old_rank else "↓"
            lines.append(f"   {symbol} #{old_rank} → #{new_rank} {direction}")

    if diff.score_changes:
        lines.append("")
        lines.append("📊 评分变化:")
        for symbol, old_score, new_score in diff.score_changes:
            direction = "↑" if new_score > old_score else "↓"
            lines.append(f"   {symbol} {old_score:.1f} → {new_score:.1f} {direction}")

    if not diff.has_changes:
        lines.append("")
        lines.append("   无变化")

    return "\n".join(lines)
