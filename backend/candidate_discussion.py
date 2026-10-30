"""Bounded, evidence-citing AI analysis for an unheld AQSP candidate.

The analysis is an advisory artifact. It cannot change the deterministic score,
portfolio, risk rules, or place an order. A later adoption request must refer to
the exact saved analysis and its source snapshot.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

import aqsp_bridge
import astock
import candidate_observations
import portfolio as pf
from aqsp.core.time import now_shanghai
from decision_context import position_quote_issue
from position_discussion import (
    DiscussionLimits,
    ModelEndpoint,
    call_model,
    discussion_limits,
    resolve_endpoint,
)


class CandidateAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["observe", "buy_review", "pause"]
    price_ref: str | None = None
    condition: str = Field(min_length=1, max_length=350)
    invalidation: str = Field(min_length=1, max_length=350)


class CandidateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion: str = Field(min_length=1, max_length=800)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    actions: list[CandidateAction] = Field(max_length=3)
    missing_evidence: list[str] = Field(max_length=8)


class CandidateDiscussionError(ValueError):
    """The candidate cannot be analysed from a current, verifiable basis."""


SYSTEM = """你为未持仓候选提供人工复核材料，不下单、不改量化评分、不改风险阈值。
只依据输入 evidence；所有 evidence_ids 必须来自 allowed_evidence_ids。
价格只能引用 price_refs 中的 price_ref，不能自行编造价位或收益预测。
可以明确说明证据不足，pause 表示暂不建立观察条件。
只有 observe 或 buy_review 且引用了价位的 action 才能被用户考虑采用。
condition 写清楚下一步需要观察的事实，invalidation 写清楚何时条件失效。
输出符合 output_schema 的 JSON，不加 Markdown 围栏，不输出思考过程。"""


def _snapshot_id(candidate: dict[str, Any]) -> str:
    raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "candidate-snapshot:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _as_of(quote: dict[str, Any]) -> str | None:
    raw = quote.get("as_of")
    if isinstance(raw, str) and raw.strip():
        return raw
    raw_quote_time = quote.get("quote_time")
    if raw_quote_time:
        from astock import parse_gtimg_time

        return parse_gtimg_time(raw_quote_time)
    return None


def _price_ref(quote: dict[str, Any], checked_at: datetime) -> dict[str, Any]:
    price = quote.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise CandidateDiscussionError("候选缺少有效现价，暂不能分析入场条件")
    issue = position_quote_issue(quote, checked_at=checked_at)
    if issue:
        raise CandidateDiscussionError(f"候选行情不可核验：{issue}")
    as_of = _as_of(quote)
    if not as_of:
        raise CandidateDiscussionError("候选行情缺少来源时点，暂不能分析入场条件")
    return {"price": float(price), "label": "当前实时行情", "source": str(quote.get("source") or "腾讯行情"), "as_of": as_of}


def _evidence(candidate: dict[str, Any], quote_ref: dict[str, Any]) -> dict[str, Any]:
    debate = candidate.get("debate")
    return {
        "candidate_snapshot": {
            "date": candidate.get("date"),
            "symbol": candidate.get("symbol"),
            "name": candidate.get("display_name"),
            "score": candidate.get("score"),
            "research_status": candidate.get("research_status"),
            "evidence_status": candidate.get("evidence_status"),
            "deterministic_reasons": candidate.get("deterministic_reasons", []),
            "strategies": candidate.get("strategies", []),
            "score_breakdown": candidate.get("score_breakdown", []),
            "data_source": candidate.get("data_source"),
            "data_fetched_at": candidate.get("data_fetched_at"),
        },
        "candidate_debate": debate if isinstance(debate, dict) else {"status": "missing"},
        "quote_current": quote_ref,
    }


def _parse_result(raw: str, evidence: dict[str, Any], price_refs: dict[str, dict[str, Any]]) -> CandidateSummary:
    text = raw.strip()
    fence = chr(96) * 3
    if text.startswith(fence) and text.endswith(fence):
        text = text.split("\n", 1)[1].rsplit(fence, 1)[0]
    result = CandidateSummary.model_validate_json(text)
    if any(key not in evidence for key in result.evidence_ids):
        raise CandidateDiscussionError("模型引用了不存在的候选证据")
    for action in result.actions:
        if action.action in {"observe", "buy_review"}:
            if not action.price_ref or action.price_ref not in price_refs:
                raise CandidateDiscussionError("可跟踪候选条件缺少来源价位")
        elif action.price_ref is not None and action.price_ref not in price_refs:
            raise CandidateDiscussionError("模型引用了不存在的观察价")
    return result


async def run_candidate_discussion(
    symbol: str,
    selected_date: str | None = None,
    config: dict[str, str] | None = None,
    server_model: str | None = None,
) -> dict[str, Any]:
    """Analyse one current candidate and persist the exact source identity."""
    checked_at = now_shanghai()
    if selected_date is not None:
        try:
            current_selected_date = aqsp_bridge.load_surface().current.selected_date
        except aqsp_bridge.AQSPBridgeError as exc:
            raise CandidateDiscussionError(str(exc)) from exc
        if selected_date != current_selected_date:
            raise CandidateDiscussionError("候选 AI 分析只接受当前研究快照，历史快照不能混用实时行情")
    try:
        candidate = aqsp_bridge.candidate_payload(symbol, selected_date)
    except aqsp_bridge.AQSPBridgeError as exc:
        raise CandidateDiscussionError(str(exc)) from exc
    normalized = str(candidate.get("symbol") or "").strip()
    if normalized != symbol.strip():
        raise CandidateDiscussionError("候选代码与研究快照不一致，请刷新")
    with pf._LOCK:
        holdings = pf._load().get("holdings", [])
    if any(row.get("code") == normalized for row in holdings):
        raise CandidateDiscussionError("该标的已在持仓中，请到持仓策略区复核")
    try:
        quotes = astock.tencent_quote([normalized])
    except Exception as exc:  # noqa: BLE001 -- source failures are a visible non-adoptable state
        raise CandidateDiscussionError("候选实时行情读取失败，暂不能分析") from exc
    quote = quotes.get(normalized)
    if not isinstance(quote, dict):
        raise CandidateDiscussionError("候选实时行情不可用，暂不能分析")
    quote_ref = _price_ref(quote, checked_at)
    evidence = _evidence(candidate, quote_ref)
    price_refs = {"current": quote_ref}
    try:
        endpoint = resolve_endpoint(config)
    except (ValueError, RuntimeError, OSError) as exc:
        raise CandidateDiscussionError(str(exc)) from exc
    if server_model:
        if config:
            raise CandidateDiscussionError("本次自定义接口与服务器型号选择不能同时使用")
        from pathlib import Path
        from position_model_catalog import read_catalog

        catalog = await read_catalog(endpoint, Path(candidate_observations.pf.CACHE_DIR))
        if catalog.get("directory_complete", catalog.get("listed") is not None) and server_model not in catalog["models"]:
            raise CandidateDiscussionError("所选型号未在当前提供商目录中，请刷新型号后重选；未自动替换")
        endpoint = ModelEndpoint(endpoint.url, endpoint.key, server_model, endpoint.anthropic)
    snapshot_id = _snapshot_id(candidate)
    payload = {
        "symbol": normalized,
        "name": candidate.get("display_name") or normalized,
        "selected_date": candidate.get("date"),
        "source_snapshot_id": snapshot_id,
        "evidence": evidence,
        "price_refs": price_refs,
        "allowed_evidence_ids": sorted(evidence),
        "output_schema": CandidateSummary.model_json_schema(),
        "task": "分析这只未持仓候选是否值得继续观察；如证据足够，给出引用当前价位的条件和失效条件，否则明确暂停。",
    }
    limits: DiscussionLimits = discussion_limits()
    analysis_id = "candidate-analysis:" + uuid4().hex
    try:
        raw = await call_model(endpoint, payload, limits.synthesis_timeout, limits, system_prompt=SYSTEM)
        summary = _parse_result(raw, evidence, price_refs)
        status = "complete"
        issue = ""
    except Exception as exc:  # bounded model failures are returned as visible, non-adoptable state
        summary = None
        status = "failed"
        issue = str(exc) if isinstance(exc, CandidateDiscussionError) else "模型分析未完成，未形成可采用条件"
    generated_at = now_shanghai().isoformat()
    result: dict[str, Any] = {
        "analysis_id": analysis_id,
        "symbol": normalized,
        "name": candidate.get("display_name") or normalized,
        "selected_date": candidate.get("date"),
        "generated_at": generated_at,
        "source_snapshot_id": snapshot_id,
        "source_as_of": quote_ref["as_of"],
        "source_price": quote_ref["price"],
        "status": status,
        "model": endpoint.model,
        "evidence": evidence,
        "price_refs": price_refs,
        "conclusion": summary.conclusion if summary else "",
        "evidence_ids": summary.evidence_ids if summary else [],
        "actions": [action.model_dump() for action in summary.actions] if summary else [],
        "missing_evidence": summary.missing_evidence if summary else [],
        "issue": issue,
        "notice": "模型材料仅供人工复核；采用后只建立观察条件，不自动下单或改写评分。",
    }
    if status == "complete":
        candidate_observations.register_analysis(result)
    return result


__all__ = ["CandidateAction", "CandidateDiscussionError", "CandidateSummary", "run_candidate_discussion"]
