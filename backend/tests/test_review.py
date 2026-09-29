"""复盘笔记系统测试 —— CRUD 和 API 端点。

测试覆盖：
1. 数据层 CRUD 函数
2. API 端点（GET/POST/PUT/DELETE）
3. 过滤与聚合功能
4. 边界条件与错误处理
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ==================== 数据层测试 ====================


def test_add_review_creates_record():
    """添加复盘记录应成功创建并返回ID。"""
    from aqsp.review import add_review, get_reviews

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        review_id = add_review(
            signal_id="test_signal_1",
            date="2026-09-20",
            symbol="000001",
            rating=4,
            tags=["趋势突破", "止盈"],
            notes="## 复盘\n\n突破后快速拉升",
            reviews_path=path,
        )

        assert review_id is not None
        assert len(review_id) == 36  # UUID format

        # 验证可以读取
        reviews = get_reviews(reviews_path=path)
        assert len(reviews) == 1
        assert reviews[0].signal_id == "test_signal_1"
        assert reviews[0].symbol == "000001"
        assert reviews[0].rating == 4
        assert set(reviews[0].tags) == {"趋势突破", "止盈"}
        assert reviews[0].notes == "## 复盘\n\n突破后快速拉升"
    finally:
        path.unlink(missing_ok=True)


def test_add_review_validates_rating():
    """添加复盘记录应验证评分范围（1-5）。"""
    from aqsp.review import add_review

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        # 评分过低
        with pytest.raises(ValueError, match="rating 必须在 1-5 范围内"):
            add_review(
                signal_id="test_signal",
                date="2026-09-20",
                symbol="000001",
                rating=0,
                reviews_path=path,
            )

        # 评分过高
        with pytest.raises(ValueError, match="rating 必须在 1-5 范围内"):
            add_review(
                signal_id="test_signal",
                date="2026-09-20",
                symbol="000001",
                rating=6,
                reviews_path=path,
            )
    finally:
        path.unlink(missing_ok=True)


def test_get_reviews_filters_by_symbol():
    """查询复盘记录应能按股票代码过滤。"""
    from aqsp.review import add_review, get_reviews

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        add_review("sig1", "2026-09-20", "000001", 4, reviews_path=path)
        add_review("sig2", "2026-09-20", "000002", 3, reviews_path=path)
        add_review("sig3", "2026-09-21", "000001", 5, reviews_path=path)

        reviews = get_reviews(symbol="000001", reviews_path=path)
        assert len(reviews) == 2
        assert all(r.symbol == "000001" for r in reviews)
    finally:
        path.unlink(missing_ok=True)


def test_get_reviews_filters_by_date():
    """查询复盘记录应能按日期过滤。"""
    from aqsp.review import add_review, get_reviews

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        add_review("sig1", "2026-09-20", "000001", 4, reviews_path=path)
        add_review("sig2", "2026-09-21", "000002", 3, reviews_path=path)

        reviews = get_reviews(date="2026-09-20", reviews_path=path)
        assert len(reviews) == 1
        assert reviews[0].date == "2026-09-20"
    finally:
        path.unlink(missing_ok=True)


def test_get_reviews_filters_by_tags():
    """查询复盘记录应能按标签过滤（任一匹配）。"""
    from aqsp.review import add_review, get_reviews

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        add_review("sig1", "2026-09-20", "000001", 4, tags=["趋势突破"], reviews_path=path)
        add_review("sig2", "2026-09-20", "000002", 3, tags=["止损"], reviews_path=path)
        add_review("sig3", "2026-09-20", "000003", 5, tags=["趋势突破", "止盈"], reviews_path=path)

        reviews = get_reviews(tags=["趋势突破"], reviews_path=path)
        assert len(reviews) == 2
        assert all(any("趋势突破" in r.tags for _ in [1]) for r in reviews)
    finally:
        path.unlink(missing_ok=True)


def test_get_reviews_filters_by_min_rating():
    """查询复盘记录应能按最低评分过滤。"""
    from aqsp.review import add_review, get_reviews

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        add_review("sig1", "2026-09-20", "000001", 2, reviews_path=path)
        add_review("sig2", "2026-09-20", "000002", 4, reviews_path=path)
        add_review("sig3", "2026-09-20", "000003", 5, reviews_path=path)

        reviews = get_reviews(min_rating=4, reviews_path=path)
        assert len(reviews) == 2
        assert all(r.rating >= 4 for r in reviews)
    finally:
        path.unlink(missing_ok=True)


def test_get_reviews_sorted_by_created_at_desc():
    """查询复盘记录应按创建时间倒序返回。"""
    from aqsp.review import add_review, get_reviews

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        id1 = add_review("sig1", "2026-09-20", "000001", 4, reviews_path=path)
        id2 = add_review("sig2", "2026-09-21", "000002", 3, reviews_path=path)
        id3 = add_review("sig3", "2026-09-22", "000003", 5, reviews_path=path)

        reviews = get_reviews(reviews_path=path)
        assert len(reviews) == 3
        # 最新创建的在前
        assert reviews[0].id == id3
        assert reviews[1].id == id2
        assert reviews[2].id == id1
    finally:
        path.unlink(missing_ok=True)


def test_update_review_modifies_fields():
    """更新复盘记录应只修改指定字段。"""
    from aqsp.review import add_review, get_reviews, update_review

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        review_id = add_review(
            "sig1", "2026-09-20", "000001", 3, tags=["标签1"], notes="原始笔记", reviews_path=path
        )

        # 只更新评分
        found = update_review(review_id, rating=5, reviews_path=path)
        assert found is True

        reviews = get_reviews(reviews_path=path)
        assert len(reviews) == 1
        assert reviews[0].rating == 5
        assert reviews[0].tags == ("标签1",)  # 未改变
        assert reviews[0].notes == "原始笔记"  # 未改变

        # 更新标签和笔记
        update_review(review_id, tags=["标签2", "标签3"], notes="新笔记", reviews_path=path)

        reviews = get_reviews(reviews_path=path)
        assert reviews[0].rating == 5  # 未改变
        assert set(reviews[0].tags) == {"标签2", "标签3"}
        assert reviews[0].notes == "新笔记"
    finally:
        path.unlink(missing_ok=True)


def test_update_review_validates_rating():
    """更新复盘记录应验证评分范围。"""
    from aqsp.review import add_review, update_review

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        review_id = add_review("sig1", "2026-09-20", "000001", 3, reviews_path=path)

        with pytest.raises(ValueError, match="rating 必须在 1-5 范围内"):
            update_review(review_id, rating=0, reviews_path=path)

        with pytest.raises(ValueError, match="rating 必须在 1-5 范围内"):
            update_review(review_id, rating=6, reviews_path=path)
    finally:
        path.unlink(missing_ok=True)


def test_update_review_returns_false_when_not_found():
    """更新不存在的复盘记录应返回 False。"""
    from aqsp.review import update_review

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        found = update_review("nonexistent_id", rating=5, reviews_path=path)
        assert found is False
    finally:
        path.unlink(missing_ok=True)


def test_delete_review_removes_record():
    """删除复盘记录应移除指定记录。"""
    from aqsp.review import add_review, delete_review, get_reviews

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        id1 = add_review("sig1", "2026-09-20", "000001", 4, reviews_path=path)
        id2 = add_review("sig2", "2026-09-21", "000002", 3, reviews_path=path)

        found = delete_review(id1, reviews_path=path)
        assert found is True

        reviews = get_reviews(reviews_path=path)
        assert len(reviews) == 1
        assert reviews[0].id == id2
    finally:
        path.unlink(missing_ok=True)


def test_delete_review_returns_false_when_not_found():
    """删除不存在的复盘记录应返回 False。"""
    from aqsp.review import delete_review

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        found = delete_review("nonexistent_id", reviews_path=path)
        assert found is False
    finally:
        path.unlink(missing_ok=True)


def test_get_all_tags_returns_sorted_by_frequency():
    """获取所有标签应按使用频率倒序返回。"""
    from aqsp.review import add_review, get_all_tags

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    try:
        add_review("sig1", "2026-09-20", "000001", 4, tags=["趋势突破"], reviews_path=path)
        add_review("sig2", "2026-09-20", "000002", 3, tags=["止损"], reviews_path=path)
        add_review("sig3", "2026-09-20", "000003", 5, tags=["趋势突破", "止盈"], reviews_path=path)
        add_review("sig4", "2026-09-20", "000004", 4, tags=["趋势突破"], reviews_path=path)

        tags = get_all_tags(reviews_path=path)
        # 趋势突破: 3次, 止盈: 1次, 止损: 1次
        assert tags[0] == "趋势突破"
        assert set(tags[1:]) == {"止盈", "止损"}
    finally:
        path.unlink(missing_ok=True)


# ==================== API 端点测试 ====================


@pytest.fixture
def client():
    """创建测试客户端。"""
    from app import app

    return TestClient(app)


@pytest.fixture
def temp_reviews_path(monkeypatch):
    """提供临时 reviews.jsonl 路径。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    # Monkey patch DEFAULT_REVIEWS_PATH
    import aqsp.review
    monkeypatch.setattr(aqsp.review, "DEFAULT_REVIEWS_PATH", path)

    yield path

    path.unlink(missing_ok=True)


def test_api_create_review(client, temp_reviews_path):
    """POST /api/reviews 应创建复盘记录。"""
    response = client.post(
        "/api/reviews",
        json={
            "signal_id": "test_signal_1",
            "date": "2026-09-20",
            "symbol": "000001",
            "rating": 4,
            "tags": ["趋势突破", "止盈"],
            "notes": "## 复盘\n\n成功案例",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "id" in data["data"]
    assert len(data["data"]["id"]) == 36


def test_api_create_review_validates_rating(client, temp_reviews_path):
    """POST /api/reviews 应验证评分范围。"""
    response = client.post(
        "/api/reviews",
        json={
            "signal_id": "test_signal",
            "date": "2026-09-20",
            "symbol": "000001",
            "rating": 6,
        },
    )

    assert response.status_code == 400
    assert "rating" in response.json()["detail"]


def test_api_get_reviews_without_filters(client, temp_reviews_path):
    """GET /api/reviews 无过滤条件应返回全部记录。"""
    # 创建测试数据
    client.post(
        "/api/reviews",
        json={"signal_id": "sig1", "date": "2026-09-20", "symbol": "000001", "rating": 4},
    )
    client.post(
        "/api/reviews",
        json={"signal_id": "sig2", "date": "2026-09-21", "symbol": "000002", "rating": 3},
    )

    response = client.get("/api/reviews")
    assert response.status_code == 200
    data = response.json()
    assert len(data["data"]) == 2


def test_api_get_reviews_with_filters(client, temp_reviews_path):
    """GET /api/reviews 应支持多种过滤条件。"""
    # 创建测试数据
    client.post(
        "/api/reviews",
        json={
            "signal_id": "sig1",
            "date": "2026-09-20",
            "symbol": "000001",
            "rating": 4,
            "tags": ["趋势突破"],
        },
    )
    client.post(
        "/api/reviews",
        json={
            "signal_id": "sig2",
            "date": "2026-09-20",
            "symbol": "000002",
            "rating": 2,
            "tags": ["止损"],
        },
    )

    # 按股票代码过滤
    response = client.get("/api/reviews?symbol=000001")
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1

    # 按评分过滤
    response = client.get("/api/reviews?min_rating=4")
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1

    # 按标签过滤
    response = client.get("/api/reviews?tags=趋势突破")
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1


def test_api_update_review(client, temp_reviews_path):
    """PUT /api/reviews/{review_id} 应更新复盘记录。"""
    # 创建记录
    create_response = client.post(
        "/api/reviews",
        json={"signal_id": "sig1", "date": "2026-09-20", "symbol": "000001", "rating": 3},
    )
    review_id = create_response.json()["data"]["id"]

    # 更新记录
    response = client.put(
        f"/api/reviews/{review_id}",
        json={"rating": 5, "notes": "更新后的笔记"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["ok"] is True

    # 验证更新
    get_response = client.get("/api/reviews")
    reviews = get_response.json()["data"]
    assert reviews[0]["rating"] == 5
    assert reviews[0]["notes"] == "更新后的笔记"


def test_api_update_review_not_found(client, temp_reviews_path):
    """PUT /api/reviews/{review_id} 不存在的记录应返回 404。"""
    response = client.put(
        "/api/reviews/nonexistent_id",
        json={"rating": 5},
    )

    assert response.status_code == 404


def test_api_delete_review(client, temp_reviews_path):
    """DELETE /api/reviews/{review_id} 应删除复盘记录。"""
    # 创建记录
    create_response = client.post(
        "/api/reviews",
        json={"signal_id": "sig1", "date": "2026-09-20", "symbol": "000001", "rating": 3},
    )
    review_id = create_response.json()["data"]["id"]

    # 删除记录
    response = client.delete(f"/api/reviews/{review_id}")
    assert response.status_code == 200
    assert response.json()["data"]["ok"] is True

    # 验证删除
    get_response = client.get("/api/reviews")
    assert len(get_response.json()["data"]) == 0


def test_api_delete_review_not_found(client, temp_reviews_path):
    """DELETE /api/reviews/{review_id} 不存在的记录应返回 404。"""
    response = client.delete("/api/reviews/nonexistent_id")
    assert response.status_code == 404


def test_api_get_tags(client, temp_reviews_path):
    """GET /api/reviews/tags 应返回所有标签（按频率倒序）。"""
    # 创建测试数据
    client.post(
        "/api/reviews",
        json={
            "signal_id": "sig1",
            "date": "2026-09-20",
            "symbol": "000001",
            "rating": 4,
            "tags": ["趋势突破"],
        },
    )
    client.post(
        "/api/reviews",
        json={
            "signal_id": "sig2",
            "date": "2026-09-20",
            "symbol": "000002",
            "rating": 3,
            "tags": ["止损"],
        },
    )
    client.post(
        "/api/reviews",
        json={
            "signal_id": "sig3",
            "date": "2026-09-20",
            "symbol": "000003",
            "rating": 5,
            "tags": ["趋势突破", "止盈"],
        },
    )

    response = client.get("/api/reviews/tags")
    assert response.status_code == 200
    tags = response.json()["data"]
    assert tags[0] == "趋势突破"  # 出现2次，排第一
    assert set(tags[1:]) == {"止盈", "止损"}


# ---------------------------------------------------------------------------
# 默认路径解析（运行时 overlay 优先）
# ---------------------------------------------------------------------------


def test_default_reviews_path_prefers_runtime_overlay(monkeypatch, tmp_path) -> None:
    """复盘默认路径必须优先运行时 overlay。

    背景：不可变 release 下 `<release>/data` 归 root、服务用户只读 ⇒ 若默认路径
    指向 release 内，ReviewPage 保存复盘会直接 502（2026-09-29 prod 实测：
    `[Errno 13] Permission denied: '.../data/reviews.jsonl'`）。
    """
    import aqsp.review as review_module

    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("AQSP_REVIEWS_PATH", raising=False)

    assert review_module._default_reviews_path() == tmp_path / "reviews.jsonl"


def test_default_reviews_path_honors_explicit_env(monkeypatch, tmp_path) -> None:
    import aqsp.review as review_module

    monkeypatch.setenv("AQSP_REVIEWS_PATH", str(tmp_path / "custom.jsonl"))
    monkeypatch.delenv("AQSP_RUNTIME_DATA_ROOT", raising=False)

    assert review_module._default_reviews_path() == tmp_path / "custom.jsonl"


def test_default_reviews_path_falls_back_to_repo(monkeypatch) -> None:
    import aqsp.review as review_module

    monkeypatch.delenv("AQSP_REVIEWS_PATH", raising=False)
    monkeypatch.delenv("AQSP_RUNTIME_DATA_ROOT", raising=False)

    p = review_module._default_reviews_path()
    assert p.name == "reviews.jsonl"
    # 回退目标仍是仓库内 data/（parents[3] = 仓库根）
    assert (p.parent.parent / "pyproject.toml").is_file()


# ---------------------------------------------------------------------------
# 复盘洞察聚合（summarize_reviews + GET /api/reviews/insights）
# ---------------------------------------------------------------------------


def _write_reviews_file(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_summarize_reviews_aggregates(tmp_path) -> None:
    import aqsp.review as review_module

    p = tmp_path / "reviews.jsonl"
    _write_reviews_file(
        p,
        [
            {
                "id": "1", "signal_id": "s1", "date": "2026-09-20", "symbol": "000001",
                "rating": 5, "tags": ["突破回踩", "纪律"], "notes": "",
                "created_at": "t", "updated_at": "t",
            },
            {
                "id": "2", "signal_id": "s2", "date": "2026-09-21", "symbol": "000001",
                "rating": 2, "tags": ["追高被套"], "notes": "",
                "created_at": "t", "updated_at": "t",
            },
            {
                "id": "3", "signal_id": "s3", "date": "2026-09-22", "symbol": "600519",
                "rating": 4, "tags": ["追高被套"], "notes": "",
                "created_at": "t", "updated_at": "t",
            },
        ],
    )

    summary = review_module.summarize_reviews(p)
    assert summary["total"] == 3
    assert summary["covered_symbols"] == 2
    assert summary["avg_rating"] == 3.67
    assert summary["latest_date"] == "2026-09-22"
    assert summary["rating_histogram"]["5"] == 1
    assert summary["rating_histogram"]["2"] == 1
    tags = {t["tag"]: t for t in summary["tag_insights"]}
    assert tags["追高被套"]["count"] == 2
    assert tags["追高被套"]["avg_rating"] == 3.0
    # 个股维度：复盘次数最多的票排第一
    assert summary["symbol_insights"][0] == {"symbol": "000001", "count": 2}


def test_summarize_reviews_empty_is_fail_soft(tmp_path) -> None:
    import aqsp.review as review_module

    p = tmp_path / "reviews.jsonl"
    p.touch()
    summary = review_module.summarize_reviews(p)
    assert summary["total"] == 0
    assert summary["covered_symbols"] == 0
    assert summary["avg_rating"] is None
    assert summary["tag_insights"] == []


def test_reviews_insights_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    _write_reviews_file(
        tmp_path / "reviews.jsonl",
        [
            {
                "id": "1", "signal_id": "s1", "date": "2026-09-20", "symbol": "000001",
                "rating": 3, "tags": ["纪律"], "notes": "按计划执行",
                "created_at": "t", "updated_at": "t",
            }
        ],
    )
    from fastapi.testclient import TestClient

    import app as app_module

    client = TestClient(app_module.app)
    r = client.get("/api/reviews/insights")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["covered_symbols"] == 1
    assert data["avg_rating"] == 3
    assert data["tag_insights"][0]["tag"] == "纪律"
