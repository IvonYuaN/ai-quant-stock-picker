"""Monitor 规则组合引擎只读 API —— 只做求值与契约说明，绝不触发通知。

路由由主 agent 在 app.py 统一接线，本模块只暴露 router，不 import app、
不 import 任何通知通道，也不写库、不下单、不改评分。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import rule_combinators as rc

router = APIRouter(prefix="/api/lab/rule-combinators", tags=["监控规则"])

# 字段契约来自 alert_events.py 的 upsert_price_observation_events 事件 schema
_ALERT_FIELDS = (
    "id", "kind", "level", "severity", "symbol", "position_id",
    "trigger_at", "threshold_at", "sellable_now", "action_hint", "t1_note",
    "basis_json", "status", "handled_at", "handled_note", "ignore_streak",
    "created_at", "updated_at", "rule_id", "observation_rule_id",
    "rule_revision", "condition_active", "freq_mode", "last_notified_at",
    "last_emitted_at", "quote_as_of", "quote_source", "user_note",
    "cooldown_minutes",
)

_CONTRACT: dict[str, Any] = {
    "name": "Monitor 规则组合引擎",
    "version": 1,
    "note": "只读求值：不外发通知、不改评分、不下单、不写库；时间由调用方传入，模块不读时钟。",
    "truth_values": ["TRUE", "FALSE", "UNKNOWN"],
    "three_valued_logic": {
        "AND": "任一 FALSE => FALSE；否则含 UNKNOWN => UNKNOWN；否则 TRUE",
        "OR": "任一 TRUE => TRUE；否则含 UNKNOWN => UNKNOWN；否则 FALSE",
        "NOT": "TRUE => FALSE, FALSE => TRUE, UNKNOWN => UNKNOWN",
    },
    "rule_tree": {
        "combination": {
            "op": "AND | OR | NOT",
            "children": "非空节点数组；NOT 恰好 1 个 child",
        },
        "leaf": {
            "id": "可选字符串，缺省用 metric",
            "metric": "字符串，对应 observations 的键",
            "operator": "> | >= | < | <= | ==",
            "threshold": "有限数值",
            "severity": "info | warn | critical",
        },
    },
    "observations": "dict[metric -> 数值]；缺 metric 或观测值非有限数值 => 该叶子 UNKNOWN，绝不折叠成命中",
    "severity": "severity_of 取命中叶子里最高级；未命中或 UNKNOWN 一律 info",
    "should_emit": "纯函数冷却路由：elapsed >= 有效冷却才放行；critical 冷却减半；首次发送直接放行；模块不读时钟",
    "fail_closed": [
        "非法 op", "未知 operator", "阈值非数值", "嵌套过深", "循环结构",
        "children 结构错误", "NOT 元数错误", "非法 severity",
    ],
    "max_depth": rc.MAX_DEPTH,
    "alert_payload_fields": list(_ALERT_FIELDS),
    "basis_json_keys": ["rule", "actual", "evidence"],
    "endpoints": {
        "POST /api/lab/rule-combinators/evaluate": "只读三值求值",
        "GET /api/lab/rule-combinators/contract": "本契约说明",
    },
}


class EvaluateRequest(BaseModel):
    rule_tree: dict[str, Any] = Field(..., description="组合规则树")
    observations: dict[str, float] = Field(
        default_factory=dict,
        description="metric 到数值；缺 metric 的叶子为 UNKNOWN",
    )
    last_emitted_ts: float | None = Field(
        default=None, description="上次发送时间戳（秒），调用方传入"
    )
    now_ts: float | None = Field(
        default=None, description="当前时间戳（秒），调用方传入；模块不读时钟"
    )
    cooldown_seconds: float = Field(default=0.0, ge=0, description="冷却秒数")


class EvaluateResponse(BaseModel):
    truth: str
    hit: bool
    severity: str
    matched: list[dict[str, Any]]
    unknown: list[dict[str, Any]]
    leaves: list[dict[str, Any]]
    should_emit: bool | None = None


@router.get("/contract", summary="规则组合引擎契约说明")
def get_contract() -> dict[str, Any]:
    return _CONTRACT


@router.post(
    "/evaluate",
    summary="对规则树做只读三值求值",
    response_model=EvaluateResponse,
)
def evaluate(req: EvaluateRequest) -> EvaluateResponse:
    try:
        verdict = rc.evaluate_combination(req.rule_tree, req.observations)
    except rc.RuleTreeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    emit: bool | None = None
    # 只在命中且调用方显式给时间时才给冷却建议，数据不可用绝不产生发送建议
    if req.now_ts is not None and verdict.hit:
        try:
            emit = rc.should_emit(
                req.last_emitted_ts,
                req.now_ts,
                req.cooldown_seconds,
                rc.severity_of(verdict),
            )
        except rc.RuleTreeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return EvaluateResponse(
        truth=verdict.truth.value,
        hit=verdict.hit,
        severity=rc.severity_of(verdict),
        matched=[leaf.as_dict() for leaf in verdict.matched],
        unknown=[leaf.as_dict() for leaf in verdict.unknown],
        leaves=[leaf.as_dict() for leaf in verdict.leaves],
        should_emit=emit,
    )
