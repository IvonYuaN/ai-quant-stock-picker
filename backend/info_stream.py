"""小道信息流聚合（issue #317 W2）：财联社快讯 + 概念板块异动。

- 只读外部公开源，不落库、不参与选股评分（proposal-only 数据面）。
- 每个源独立降级：单源失败 ⇒ 该区为空 + ``sources`` 显式标注错误（缺材料可见，
  不静默渲染成正常空数据）。
- 进程内 TTL 缓存：信息流读多写少，避免每次请求都打外部接口（prod 1.6G 红线）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from aqsp.core.time import now_shanghai
from aqsp.data.cls_news import ClsNewsSource
from aqsp.data.concept_board import ConceptBoardSource

_TTL_SECONDS = 300
_NEWS_LIMIT = 30
_CONCEPT_LIMIT = 20

_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_at: float = 0.0


def _fetch_news(source: ClsNewsSource | None) -> tuple[list[dict[str, Any]], str]:
    """返回 (news items, 状态)；状态 = "ok" 或 "error: ..."。

    注入源（测试/预加载）走 ``items()`` 纯内存读取；默认源走 ``load(force=True)``
    强制抓网——``load`` 即使已预注入也会无条件重新 fetch，注入语义会被破坏。
    """
    try:
        if source is None:
            items = ClsNewsSource().load(force=True, limit=_NEWS_LIMIT)
        else:
            items = source.items(limit=_NEWS_LIMIT)
        return [
            {
                "item_id": item.item_id,
                "title": item.title,
                "summary": item.summary,
                "ctime": item.ctime,
                "level": item.level,
                "subjects": list(item.subjects),
                "source": "财联社电报",
            }
            for item in items
        ], "ok"
    except Exception as exc:  # noqa: BLE001 - 单源失败降级，不阻断另一源
        return [], f"error: {exc}"


def _fetch_concepts(source: ConceptBoardSource | None) -> tuple[list[dict[str, Any]], str]:
    """返回 (概念异动榜按涨跌幅降序, 状态)；注入语义同 _fetch_news。"""
    try:
        if source is None:
            items = ConceptBoardSource().load(force=True)
        else:
            items = source.items()
        ranked = sorted(items, key=lambda item: item.change_pct, reverse=True)
        return [
            {
                "board_code": item.board_code,
                "board_name": item.board_name,
                "up_count": item.up_count,
                "down_count": item.down_count,
                "change_pct": item.change_pct,
                "main_net_inflow": item.main_net_inflow,
                "main_net_ratio": item.main_net_ratio,
            }
            for item in ranked[:_CONCEPT_LIMIT]
        ], "ok"
    except Exception as exc:  # noqa: BLE001 - 单源失败降级
        return [], f"error: {exc}"


def get_info_stream(
    *,
    force: bool = False,
    news_source: ClsNewsSource | None = None,
    concept_source: ConceptBoardSource | None = None,
) -> dict[str, Any]:
    """聚合小道信息流；注入 source 即跳过缓存（测试/手动刷新用）。"""
    global _cache, _cache_at
    if not force and (news_source is None and concept_source is None):
        with _lock:
            if _cache is not None and (time.monotonic() - _cache_at) < _TTL_SECONDS:
                return _cache
    news, news_status = _fetch_news(news_source)
    concepts, concept_status = _fetch_concepts(concept_source)
    payload: dict[str, Any] = {
        "generated_at": now_shanghai().isoformat(),
        "news": news,
        "concepts": concepts,
        "sources": {"cls_news": news_status, "concept_board": concept_status},
    }
    if news_source is None and concept_source is None:
        with _lock:
            _cache = payload
            _cache_at = time.monotonic()
    return payload
