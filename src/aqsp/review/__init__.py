"""复盘笔记系统 —— 对历史信号进行标签、笔记和评分。

提供 CRUD 功能，与 predictions.jsonl 关联，支持根据 signal_id 查询复盘记录。
所有时间戳使用 now_shanghai()。

示例：
    from aqsp.review import add_review, get_reviews

    # 添加复盘记录
    review_id = add_review(
        signal_id="abc123",
        date="2026-09-20",
        symbol="000001",
        rating=4,
        tags=["趋势突破", "止盈"],
        notes="## 复盘\n\n突破颈线位后快速拉升...",
    )

    # 查询复盘记录
    reviews = get_reviews(symbol="000001")
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from aqsp.core.time import now_shanghai, to_iso8601


def _default_reviews_path() -> Path:
    """reviews.jsonl 默认路径：**运行时数据 overlay 优先**。

    不可变 release 下 `<release>/data` 归 root、服务用户（aqsp-vibe）只读 ⇒
    生产上复盘必须落到运行时数据目录（AQSP_RUNTIME_DATA_ROOT，如 /opt/aqsp/data），
    否则写入报 `Permission denied`（2026-09-29 在 prod 实测踩中：ReviewPage 保存即 502）。

    优先级：
      1. ``AQSP_REVIEWS_PATH``（显式指定文件路径）
      2. ``AQSP_RUNTIME_DATA_ROOT``/reviews.jsonl（运行时 overlay）
      3. 仓库内 ``data/reviews.jsonl``（本机开发，无 env 时）
    """
    explicit = os.environ.get("AQSP_REVIEWS_PATH", "").strip()
    if explicit:
        return Path(explicit)
    runtime_root = os.environ.get("AQSP_RUNTIME_DATA_ROOT", "").strip()
    if runtime_root:
        return Path(runtime_root) / "reviews.jsonl"
    return Path(__file__).resolve().parents[3] / "data" / "reviews.jsonl"


# reviews.jsonl 默认路径（import 时解析一次；服务进程的 env 由 systemd 注入，
# 本机开发无 env 时回退仓库内路径，行为与历史版本一致）
DEFAULT_REVIEWS_PATH = _default_reviews_path()


@dataclass(frozen=True)
class Review:
    """复盘记录数据结构。

    Attributes:
        id: 复盘记录唯一标识
        signal_id: 关联的信号ID（来自 predictions.jsonl）
        date: 信号日期（YYYY-MM-DD）
        symbol: 股票代码
        rating: 评分（1-5星）
        tags: 标签列表
        notes: 复盘笔记（Markdown格式）
        created_at: 创建时间（ISO 8601 with timezone）
        updated_at: 更新时间（ISO 8601 with timezone）
    """
    id: str
    signal_id: str
    date: str
    symbol: str
    rating: int
    tags: tuple[str, ...]
    notes: str
    created_at: str
    updated_at: str


def _ensure_reviews_file(path: Path) -> None:
    """确保 reviews.jsonl 文件存在。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()


def _read_reviews(path: Path) -> list[dict]:
    """读取所有复盘记录。"""
    _ensure_reviews_file(path)
    reviews = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                reviews.append(json.loads(line))
    return reviews


def _write_reviews(reviews: list[dict], path: Path) -> None:
    """写入所有复盘记录（覆盖）。"""
    _ensure_reviews_file(path)
    with path.open("w", encoding="utf-8") as f:
        for review in reviews:
            f.write(json.dumps(review, ensure_ascii=False) + "\n")


def add_review(
    signal_id: str,
    date: str,
    symbol: str,
    rating: int,
    tags: list[str] | None = None,
    notes: str = "",
    reviews_path: Path | None = None,
) -> str:
    """添加复盘记录。

    Args:
        signal_id: 关联的信号ID
        date: 信号日期（YYYY-MM-DD）
        symbol: 股票代码
        rating: 评分（1-5星）
        tags: 标签列表
        notes: 复盘笔记（Markdown格式）
        reviews_path: reviews.jsonl 路径（None 使用默认路径）

    Returns:
        复盘记录ID

    Raises:
        ValueError: rating 不在 1-5 范围内
    """
    if not 1 <= rating <= 5:
        raise ValueError(f"rating 必须在 1-5 范围内，当前值: {rating}")

    path = reviews_path or DEFAULT_REVIEWS_PATH
    reviews = _read_reviews(path)

    review_id = str(uuid.uuid4())
    timestamp = to_iso8601(now_shanghai())

    review = {
        "id": review_id,
        "signal_id": signal_id,
        "date": date,
        "symbol": symbol,
        "rating": rating,
        "tags": tags or [],
        "notes": notes,
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    reviews.append(review)
    _write_reviews(reviews, path)

    return review_id


def get_reviews(
    symbol: str | None = None,
    date: str | None = None,
    signal_id: str | None = None,
    tags: list[str] | None = None,
    min_rating: int | None = None,
    reviews_path: Path | None = None,
) -> list[Review]:
    """查询复盘记录。

    Args:
        symbol: 股票代码过滤
        date: 日期过滤（YYYY-MM-DD）
        signal_id: 信号ID过滤
        tags: 标签过滤（任一标签匹配即返回）
        min_rating: 最低评分过滤
        reviews_path: reviews.jsonl 路径（None 使用默认路径）

    Returns:
        复盘记录列表，按创建时间倒序
    """
    path = reviews_path or DEFAULT_REVIEWS_PATH
    reviews = _read_reviews(path)

    # 应用过滤条件
    filtered = []
    for review in reviews:
        if symbol and review.get("symbol") != symbol:
            continue
        if date and review.get("date") != date:
            continue
        if signal_id and review.get("signal_id") != signal_id:
            continue
        if tags and not any(tag in review.get("tags", []) for tag in tags):
            continue
        if min_rating and review.get("rating", 0) < min_rating:
            continue

        filtered.append(Review(
            id=review["id"],
            signal_id=review["signal_id"],
            date=review["date"],
            symbol=review["symbol"],
            rating=review["rating"],
            tags=tuple(review.get("tags", [])),
            notes=review.get("notes", ""),
            created_at=review["created_at"],
            updated_at=review["updated_at"],
        ))

    # 按创建时间倒序；created_at 精确到秒，同一秒内以「后写入者更新」为次序（稳定逆序）
    indexed = list(enumerate(filtered))
    indexed.sort(key=lambda pair: (pair[1].created_at, pair[0]), reverse=True)
    return [item for _, item in indexed]


def update_review(
    review_id: str,
    rating: int | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
    reviews_path: Path | None = None,
) -> bool:
    """更新复盘记录。

    Args:
        review_id: 复盘记录ID
        rating: 新评分（1-5星，None 表示不更新）
        tags: 新标签列表（None 表示不更新）
        notes: 新笔记（None 表示不更新）
        reviews_path: reviews.jsonl 路径（None 使用默认路径）

    Returns:
        是否找到并更新记录

    Raises:
        ValueError: rating 不在 1-5 范围内
    """
    if rating is not None and not 1 <= rating <= 5:
        raise ValueError(f"rating 必须在 1-5 范围内，当前值: {rating}")

    path = reviews_path or DEFAULT_REVIEWS_PATH
    reviews = _read_reviews(path)

    found = False
    for review in reviews:
        if review["id"] == review_id:
            if rating is not None:
                review["rating"] = rating
            if tags is not None:
                review["tags"] = tags
            if notes is not None:
                review["notes"] = notes
            review["updated_at"] = to_iso8601(now_shanghai())
            found = True
            break

    if found:
        _write_reviews(reviews, path)

    return found


def delete_review(
    review_id: str,
    reviews_path: Path | None = None,
) -> bool:
    """删除复盘记录。

    Args:
        review_id: 复盘记录ID
        reviews_path: reviews.jsonl 路径（None 使用默认路径）

    Returns:
        是否找到并删除记录
    """
    path = reviews_path or DEFAULT_REVIEWS_PATH
    reviews = _read_reviews(path)

    original_count = len(reviews)
    reviews = [r for r in reviews if r["id"] != review_id]

    if len(reviews) < original_count:
        _write_reviews(reviews, path)
        return True

    return False


def get_all_tags(reviews_path: Path | None = None) -> list[str]:
    """获取所有使用过的标签（按使用频率倒序）。

    Args:
        reviews_path: reviews.jsonl 路径（None 使用默认路径）

    Returns:
        标签列表，按使用频率倒序
    """
    path = reviews_path or DEFAULT_REVIEWS_PATH
    reviews = _read_reviews(path)

    tag_counts: dict[str, int] = {}
    for review in reviews:
        for tag in review.get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # 按使用频率倒序
    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
    return [tag for tag, _ in sorted_tags]


__all__ = [
    "Review",
    "add_review",
    "get_reviews",
    "update_review",
    "delete_review",
    "get_all_tags",
    "DEFAULT_REVIEWS_PATH",
]
