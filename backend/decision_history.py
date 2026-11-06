"""Read-only personal decision history projection.

The projection deliberately keeps candidate, position, and formal signal
records separate. It is a search surface, not a replacement for any domain's
write model or the trade journal.
"""
from __future__ import annotations

import json
from datetime import date
from math import isfinite
from typing import Any
from urllib.parse import quote

import portfolio as pf
import tenant


_KINDS = {"candidate", "position", "signal", "trade", "research"}


def _date_ok(value: str | None) -> bool:
    if value is None:
        return True
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _text(value: object) -> str:
    return str(value or "").strip()


def _candidate_items() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import candidate_observations

    try:
        observations = candidate_observations.list_observations()
        decisions = candidate_observations.list_decisions()
    except (OSError, ValueError) as exc:
        return [], {"available": False, "error": str(exc), "count": 0}
    items: list[dict[str, Any]] = []
    for row in observations:
        items.append({
            "id": _text(row.get("id")),
            "kind": "candidate",
            "subkind": "observation",
            "symbol": _text(row.get("symbol")),
            "name": _text(row.get("name")) or _text(row.get("symbol")),
            "status": _text(row.get("state")) or "unknown",
            "decision_at": _text(row.get("updated_at")) or _text(row.get("created_at")),
            "source_snapshot_id": _text(row.get("source_snapshot_id")),
            "summary": _text(row.get("condition")),
            "detail": _text(row.get("invalidation")),
            "revision": row.get("revision"),
            "href": f"/stock/{_text(row.get('symbol'))}" if row.get("symbol") else "/review",
        })
    for row in decisions:
        choice = _text(row.get("choice")) or "unknown"
        items.append({
            "id": _text(row.get("id")),
            "kind": "candidate",
            "subkind": "decision",
            "symbol": _text(row.get("symbol")),
            "name": _text(row.get("name")) or _text(row.get("symbol")),
            "status": choice,
            "decision_at": _text(row.get("updated_at")) or _text(row.get("created_at")),
            "source_snapshot_id": _text(row.get("source_snapshot_id")),
            "summary": _text(row.get("note")),
            "detail": _text((row.get("personal_context") or {}).get("personal_intent")),
            "revision": row.get("revision"),
            "href": f"/stock/{_text(row.get('symbol'))}" if row.get("symbol") else "/review",
        })
    return items, {"available": True, "error": "", "count": len(items)}


def _position_items() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        with open(pf._pf_file(), encoding="utf-8") as stream:
            payload = json.load(stream)
    except FileNotFoundError:
        payload = {"holdings": [], "position_reviews": {}}
    except (OSError, ValueError) as exc:
        return [], {"available": False, "error": str(exc), "count": 0}
    holdings = {row.get("position_id"): row for row in payload.get("holdings", []) if row.get("position_id")}
    roots = payload.get("position_reviews", {})
    items: list[dict[str, Any]] = []
    for position_id, root in roots.items():
        if not isinstance(root, dict):
            continue
        holding = holdings.get(position_id, {})
        symbol = _text(holding.get("code") or root.get("symbol"))
        plan = root.get("review_plan") or {}
        status = "active" if position_id in holdings else "archived"
        choice = _text(plan.get("choice"))
        items.append({
            "id": _text(position_id),
            "kind": "position",
            "subkind": "plan",
            "symbol": symbol,
            "name": _text(holding.get("name")) or symbol,
            "status": status,
            "decision_at": _text(root.get("updated_at")) or _text(plan.get("updated_at")),
            "source_snapshot_id": _text(root.get("version")),
            "summary": choice or "尚未选择处理路线",
            "detail": "持仓计划" if status == "active" else "已清仓，只读历史",
            "revision": root.get("version"),
            "href": f"/my/holdings/{position_id}/plan",
        })
    return items, {"available": True, "error": "", "count": len(items)}


def _signal_items() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if tenant.current_tenant.get() != "local":
        return [], {
            "available": False,
            "error": "正式信号复盘存储当前是单用户 reviews.jsonl，未证明可按当前租户隔离",
            "count": 0,
        }
    try:
        from aqsp.review import get_reviews
        from performance_bridge import signals_payload

        reviews = get_reviews()
        signals = {row["id"]: row for row in signals_payload(limit=1000).get("signals", [])}
    except Exception as exc:  # noqa: BLE001 -- a source failure is visible in the projection
        return [], {"available": False, "error": str(exc), "count": 0}
    items: list[dict[str, Any]] = []
    for review in reviews:
        signal = signals.get(review.signal_id, {})
        items.append({
            "id": review.id,
            "kind": "signal",
            "subkind": "formal_review",
            "symbol": review.symbol,
            "name": _text(signal.get("name")) or review.symbol,
            "status": _text(signal.get("status")) or "reviewed",
            "decision_at": review.updated_at or review.created_at,
            "source_snapshot_id": review.signal_id,
            "summary": review.notes,
            "detail": f"个人评分 {review.rating}/5",
            "revision": None,
            "href": "/review#formal-signals",
        })
    return items, {"available": True, "error": "", "count": len(items)}


def _trade_items() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import trade_journal

    try:
        receipts = trade_journal.read_receipts()
    except (OSError, ValueError) as exc:
        return [], {"available": False, "error": str(exc), "count": 0}
    items: list[dict[str, Any]] = []
    for receipt in receipts:
        receipt_id = _text(receipt.get("receipt_id"))
        symbol = _text(receipt.get("code"))
        direction = _text(receipt.get("direction"))
        position_id = _text(receipt.get("position_id"))
        receipt_tenant = _text(receipt.get("tenant"))
        if receipt_tenant and receipt_tenant != tenant.current_tenant.get():
            continue
        if not receipt_id or len(symbol) != 6 or direction not in {"buy", "sell"}:
            continue
        try:
            price = float(receipt["price"])
            shares = float(receipt["shares"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isfinite(price) or not isfinite(shares) or price <= 0 or shares <= 0:
            continue
        price_text = f"{price:,.4f}".rstrip("0").rstrip(".")
        shares_text = f"{shares:,.4f}".rstrip("0").rstrip(".")
        fee = receipt.get("actual_fees")
        if fee is None:
            fee_text = "费用待核对"
        else:
            try:
                fee_value = float(fee)
                fee_text = f"实际费用 {fee_value:g} 元" if isfinite(fee_value) and fee_value >= 0 else "费用记录无效"
            except (TypeError, ValueError):
                fee_text = "费用记录无效"
        action = "买入" if direction == "buy" else "卖出"
        items.append({
            "id": receipt_id,
            "kind": "trade",
            "subkind": direction,
            "symbol": symbol,
            "name": symbol,
            "status": direction,
            "decision_at": _text(receipt.get("recorded_at")) or _text(receipt.get("date")),
            "source_snapshot_id": receipt_id,
            "summary": f"{action} {shares_text} 股 @ {price_text} 元",
            "detail": (
                f"实际成交日 {_text(receipt.get('date')) or '未记录'} · "
                f"仓位 {position_id or '身份未记录'} · {fee_text} · 凭据 {receipt_id}"
            ),
            "revision": None,
            "href": f"/my/holdings/{position_id}/plan" if position_id else "/my/holdings",
        })
    return items, {"available": True, "error": "", "count": len(items)}


def _research_items() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        import stock_research

        reports = stock_research.list_saved_reports()
    except (OSError, ValueError) as exc:
        return [], {"available": False, "error": str(exc), "count": 0}
    items: list[dict[str, Any]] = []
    for report in reports:
        symbol = _text(report.get("symbol"))
        dimensions = report.get("dimensions") if isinstance(report.get("dimensions"), dict) else {}
        available = [
            name
            for name in ("technical", "fundamental", "financial", "news")
            if isinstance(dimensions.get(name), dict)
            and isinstance(dimensions[name].get("findings"), list)
            and dimensions[name]["findings"]
        ]
        items.append({
            "id": _text(report.get("report_id")),
            "kind": "research",
            "subkind": "four_dimension_report",
            "symbol": symbol,
            "name": _text(report.get("name")) or symbol,
            "status": "saved",
            "decision_at": _text(report.get("saved_at")) or _text(report.get("generated_at")),
            "source_snapshot_id": _text(report.get("report_id")),
            "summary": _text(report.get("summary")),
            "detail": "已引用 " + ("、".join(available) if available else "无") + " 维证据",
            "revision": report.get("revision"),
            "href": (
                f"/stock/{symbol}?report_id={quote(_text(report.get('report_id')), safe='')}"
                "#stock-research-analysis"
                if symbol else "/review"
            ),
        })
    return items, {"available": True, "error": "", "count": len(items)}


def _variant_decision_items() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import research_variant_decisions

    try:
        events = research_variant_decisions.list_events()
    except (OSError, ValueError) as exc:
        return [], {"available": False, "error": str(exc), "count": 0}
    items = []
    for event in events:
        variant = event.get("variant") or {}
        items.append({
            "id": _text(event.get("id")),
            "kind": "research",
            "subkind": "variant_decision",
            "symbol": "",
            "name": _text(variant.get("label")) or _text(event.get("variant_id")),
            "status": _text(event.get("choice")) or "unknown",
            "decision_at": _text(event.get("created_at")),
            "source_snapshot_id": _text(event.get("artifact_id")),
            "summary": _text(event.get("note")),
            "detail": (
                f"变体 {_text(event.get('variant_id'))} · 批次 {_text(event.get('batch_id'))} · "
                f"自动分层 {_text(variant.get('lifecycle_status')) or '未标注'}；仅记录个人研究决定"
            ),
            "revision": event.get("revision"),
            "href": "/backtest",
        })
    return items, {"available": True, "error": "", "count": len(items)}


def build_history(
    *,
    symbol: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Build a bounded tenant-aware read projection without network I/O."""
    if kind is not None and kind not in _KINDS:
        raise ValueError("kind 必须是 candidate、position、signal、trade 或 research")
    if limit < 1 or limit > 500:
        raise ValueError("limit 必须在 1..500 之间")
    if not _date_ok(since):
        raise ValueError("since 必须是 YYYY-MM-DD")

    candidate, candidate_source = _candidate_items()
    position, position_source = _position_items()
    signal, signal_source = _signal_items()
    trade, trade_source = _trade_items()
    research, research_source = _research_items()
    variant_decisions, variant_decision_source = _variant_decision_items()
    all_items = candidate + position + signal + trade + research + variant_decisions

    def keep(item: dict[str, Any]) -> bool:
        if kind and item["kind"] != kind:
            return False
        if symbol and item["symbol"] != symbol:
            return False
        if status and item["status"] != status:
            return False
        if since and item["decision_at"][:10] < since:
            return False
        return True

    filtered = [item for item in all_items if keep(item)]
    filtered.sort(key=lambda item: item.get("decision_at", ""), reverse=True)
    return {
        "items": filtered[:limit],
        "total": len(filtered),
        "sources": {
            "candidate": candidate_source,
            "position": position_source,
            "formal_signal_reviews": signal_source,
            "trade_receipts": trade_source,
            "stock_research_reports": research_source,
            "variant_decisions": variant_decision_source,
        },
        "tenant_scope": tenant.current_tenant.get(),
    }


__all__ = ["build_history"]
