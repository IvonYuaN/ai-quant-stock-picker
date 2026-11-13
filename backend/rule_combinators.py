"""Monitor 规则组合引擎 —— 纯函数判定、三值逻辑、fail-closed。

职责边界（务必遵守）：
- 只做规则树求值、severity 归并、冷却判定、告警载荷构造；
- 不读时钟（时间一律由调用方传入）、不自动外发通知、不改评分、
  不下单、不写库；
- 数据不可用（缺 metric 或观测值非有限数值）一律产生 UNKNOWN，
  绝不折叠成命中；规则树定义非法一律抛异常（fail-closed）。

三值逻辑采用 Kleene 强三值：
    AND: 任一 FALSE => FALSE；否则含 UNKNOWN => UNKNOWN；否则 TRUE
    OR : 任一 TRUE  => TRUE ；否则含 UNKNOWN => UNKNOWN；否则 FALSE
    NOT: TRUE=>FALSE, FALSE=>TRUE, UNKNOWN=>UNKNOWN
UNKNOWN 表示数据不足无法判定，任何组合下都不会变成 TRUE（命中）。
"""
from __future__ import annotations

import math
import operator as _op
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "TruthValue",
    "LeafResult",
    "Verdict",
    "RuleTreeError",
    "MAX_DEPTH",
    "VALID_SEVERITIES",
    "SEVERITY_ORDER",
    "evaluate_combination",
    "severity_of",
    "should_emit",
    "to_alert_payload",
]

# 组合节点最大嵌套层数，超过即抛异常，防止递归爆栈
MAX_DEPTH = 12
VALID_SEVERITIES = ("info", "warn", "critical")
SEVERITY_ORDER = {"info": 0, "warn": 1, "critical": 2}
# severity 越高越紧急：critical 冷却减半以便更快重发，info/warn 用原值
_COOLDOWN_FACTOR = {"info": 1.0, "warn": 1.0, "critical": 0.5}
_OPERATORS = {
    ">": _op.gt,
    ">=": _op.ge,
    "<": _op.lt,
    "<=": _op.le,
    "==": _op.eq,
}


class RuleTreeError(ValueError):
    """规则树定义非法，或数据不足以构造命中载荷时抛出（fail-closed）。"""


class TruthValue(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LeafResult:
    """单个叶子条件的求值明细，供 severity 归并和载荷依据使用。"""

    id: str
    metric: str
    operator: str
    threshold: float
    severity: str
    value: float | None
    truth: TruthValue
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "metric": self.metric,
            "operator": self.operator,
            "threshold": self.threshold,
            "severity": self.severity,
            "value": self.value,
            "truth": self.truth.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Verdict:
    """规则树整体判定结果。"""

    truth: TruthValue
    leaves: tuple[LeafResult, ...]

    @property
    def hit(self) -> bool:
        return self.truth is TruthValue.TRUE

    @property
    def matched(self) -> tuple[LeafResult, ...]:
        # 命中叶子取自身判定为 TRUE 的叶子，定义最直接、无歧义
        return tuple(leaf for leaf in self.leaves if leaf.truth is TruthValue.TRUE)

    @property
    def unknown(self) -> tuple[LeafResult, ...]:
        return tuple(leaf for leaf in self.leaves if leaf.truth is TruthValue.UNKNOWN)

    def as_dict(self) -> dict[str, Any]:
        return {
            "truth": self.truth.value,
            "hit": self.hit,
            "matched": [leaf.as_dict() for leaf in self.matched],
            "unknown": [leaf.as_dict() for leaf in self.unknown],
            "leaves": [leaf.as_dict() for leaf in self.leaves],
        }


def evaluate_combination(rule_tree: Any, observations: dict[str, Any]) -> Verdict:
    """对组合规则树做只读三值求值，返回 Verdict。

    rule_tree 支持组合节点 {"op": "AND"|"OR"|"NOT", "children": [...]} 与
    叶子 {"id", "metric", "operator", "threshold", "severity"}。
    observations 为 metric 到数值的映射，缺 metric 的叶子为 UNKNOWN。
    规则树定义非法（非法 op、未知 operator、阈值非数值、嵌套过深、
    循环结构等）一律抛 RuleTreeError。
    """
    if not isinstance(observations, dict):
        raise RuleTreeError("observations 必须是 dict[metric -> 数值]")
    leaves: list[LeafResult] = []
    truth = _eval_node(rule_tree, observations, depth=0, active=set(), leaves=leaves)
    return Verdict(truth=truth, leaves=tuple(leaves))


def _eval_node(
    node: Any,
    observations: dict[str, Any],
    depth: int,
    active: set[int],
    leaves: list[LeafResult],
) -> TruthValue:
    # 深度在进入节点前检查，避免深层结构触发 Python 递归上限
    if depth > MAX_DEPTH:
        raise RuleTreeError(f"规则树嵌套过深（上限 {MAX_DEPTH} 层）")
    if not isinstance(node, dict):
        raise RuleTreeError(f"规则树节点必须是 dict，实际为 {type(node).__name__}")
    # active 只记录当前递归路径，正常共享引用（DAG）不会误判为循环
    node_id = id(node)
    if node_id in active:
        raise RuleTreeError("规则树存在循环引用，拒绝求值")
    active.add(node_id)
    try:
        if "op" in node:
            return _eval_combination(node, observations, depth, active, leaves)
        return _eval_leaf(node, observations, leaves)
    finally:
        active.discard(node_id)


def _eval_combination(
    node: dict[str, Any],
    observations: dict[str, Any],
    depth: int,
    active: set[int],
    leaves: list[LeafResult],
) -> TruthValue:
    op = node["op"]
    if op not in {"AND", "OR", "NOT"}:
        raise RuleTreeError(f"非法组合 op: {op!r}，仅支持 AND / OR / NOT")
    children = node.get("children")
    if not isinstance(children, list) or not children:
        raise RuleTreeError(f"组合节点 {op} 的 children 必须是非空 list")
    if op == "NOT" and len(children) != 1:
        raise RuleTreeError("NOT 节点必须恰好包含 1 个 child")
    # 全量求值所有子节点：既收集明细，也保证被吸收分支里的非法定义不被漏检
    child_truths = [
        _eval_node(child, observations, depth + 1, active, leaves)
        for child in children
    ]
    false = TruthValue.FALSE
    true = TruthValue.TRUE
    unknown = TruthValue.UNKNOWN
    if op == "AND":
        if any(t is false for t in child_truths):
            return false
        if any(t is unknown for t in child_truths):
            return unknown
        return true
    if op == "OR":
        if any(t is true for t in child_truths):
            return true
        if any(t is unknown for t in child_truths):
            return unknown
        return false
    # NOT：UNKNOWN 原样传播，绝不折叠成命中
    only = child_truths[0]
    return {true: false, false: true, unknown: unknown}[only]


def _eval_leaf(
    node: dict[str, Any],
    observations: dict[str, Any],
    leaves: list[LeafResult],
) -> TruthValue:
    for key in ("metric", "operator", "threshold", "severity"):
        if key not in node:
            raise RuleTreeError(f"叶子缺少必需字段 {key!r}")
    metric = node["metric"]
    op_name = node["operator"]
    threshold = node["threshold"]
    severity = node["severity"]
    leaf_id = node.get("id", metric)

    if not isinstance(metric, str) or not metric:
        raise RuleTreeError("叶子 metric 必须是非空字符串")
    if not isinstance(leaf_id, str) or not leaf_id:
        raise RuleTreeError("叶子 id 必须是非空字符串")
    if op_name not in _OPERATORS:
        raise RuleTreeError(
            f"未知 operator: {op_name!r}，仅支持 > >= < <= =="
        )
    if severity not in SEVERITY_ORDER:
        raise RuleTreeError(
            f"非法 severity: {severity!r}，仅支持 info / warn / critical"
        )
    # 阈值必须是有限数值，bool 视作非法（fail-closed）
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
    ):
        raise RuleTreeError(f"叶子 {leaf_id} 的 threshold 必须是有限数值")
    threshold = float(threshold)

    # 观测数据问题属于数据不可用：一律 UNKNOWN，不抛异常、绝不产生命中
    if metric not in observations:
        result = LeafResult(
            id=leaf_id, metric=metric, operator=op_name, threshold=threshold,
            severity=severity, value=None, truth=TruthValue.UNKNOWN,
            reason="缺少 metric 观测值",
        )
    else:
        raw = observations[metric]
        if (
            isinstance(raw, bool)
            or not isinstance(raw, (int, float))
            or not math.isfinite(raw)
        ):
            result = LeafResult(
                id=leaf_id, metric=metric, operator=op_name, threshold=threshold,
                severity=severity, value=None, truth=TruthValue.UNKNOWN,
                reason="观测值非有限数值",
            )
        else:
            value = float(raw)
            hit = bool(_OPERATORS[op_name](value, threshold))
            result = LeafResult(
                id=leaf_id, metric=metric, operator=op_name, threshold=threshold,
                severity=severity, value=value,
                truth=TruthValue.TRUE if hit else TruthValue.FALSE,
                reason="",
            )
    leaves.append(result)
    return result.truth


def severity_of(verdict: Verdict) -> str:
    """取命中叶子里最高级 severity；未命中或 UNKNOWN 一律返回 info。

    fail-closed：整体没命中（含数据不可用）绝不报出高级别。
    纯 NOT 反转命中时没有自身为 TRUE 的叶子，保守取最低级 info。
    """
    if not isinstance(verdict, Verdict):
        raise RuleTreeError("verdict 必须是 Verdict 实例")
    if not verdict.hit:
        return "info"
    matched = verdict.matched
    if not matched:
        return "info"
    return max(
        (leaf.severity for leaf in matched),
        key=lambda level: SEVERITY_ORDER[level],
    )


def should_emit(
    last_emitted_ts: float | None,
    now_ts: float,
    cooldown_seconds: float,
    severity: str,
) -> bool:
    """纯函数冷却路由：达到有效冷却时长才放行；时间由调用方传入，模块不读时钟。

    - last_emitted_ts 为 None（从未发送）=> True；
    - severity 决定冷却倍率：critical 减半，info / warn 用原值；
    - 边界：elapsed < 有效冷却 => False；elapsed >= 有效冷却 => True；
    - 时钟回拨（elapsed < 0）=> False（fail-closed，不产生发送）。
    本函数只回答冷却是否放行，是否外发由调用方结合命中结果决定。
    """
    if severity not in _COOLDOWN_FACTOR:
        raise RuleTreeError(f"非法 severity: {severity!r}")
    if (
        now_ts is None
        or isinstance(now_ts, bool)
        or not isinstance(now_ts, (int, float))
        or not math.isfinite(now_ts)
    ):
        raise RuleTreeError("now_ts 必须是有限数值时间戳")
    if last_emitted_ts is not None and (
        isinstance(last_emitted_ts, bool)
        or not isinstance(last_emitted_ts, (int, float))
        or not math.isfinite(last_emitted_ts)
    ):
        raise RuleTreeError("last_emitted_ts 必须是有限数值时间戳或 None")
    if (
        isinstance(cooldown_seconds, bool)
        or not isinstance(cooldown_seconds, (int, float))
        or not math.isfinite(cooldown_seconds)
        or cooldown_seconds < 0
    ):
        raise RuleTreeError("cooldown_seconds 必须是非负有限数值")
    if last_emitted_ts is None:
        return True
    elapsed = now_ts - last_emitted_ts
    effective = cooldown_seconds * _COOLDOWN_FACTOR[severity]
    return elapsed >= effective


def to_alert_payload(
    verdict: Verdict,
    *,
    created_at: str,
    rule_id: str = "",
    symbol: str = "",
    trigger_at: float | None = None,
    threshold_at: float | None = None,
    source: str = "",
    note: str = "",
    cooldown_minutes: int = 0,
    rule_revision: int = 0,
    event_id: str | None = None,
) -> dict[str, Any]:
    """构造与 alert_events 事件簿兼容的载荷骨架（纯函数，不读时钟、不写库）。

    字段集复刻 alert_events.py 里 upsert_price_observation_events 的
    observation 事件 schema；basis_json 含 rule / actual / evidence，满足
    "无依据不入库" 门禁。时间戳与 id 由调用方注入，本模块不读时钟、
    不引入随机数以保持确定性。

    fail-closed：verdict 未命中（FALSE / UNKNOWN）一律抛异常，绝不产生命中载荷。
    """
    if not isinstance(verdict, Verdict):
        raise RuleTreeError("verdict 必须是 Verdict 实例")
    if not verdict.hit:
        raise RuleTreeError("数据不可用或未命中时不得构造告警载荷（fail-closed）")
    if not isinstance(created_at, str) or not created_at:
        raise RuleTreeError("created_at 必须是非空 ISO 时间戳字符串，由调用方传入")

    matched = verdict.matched
    # 依据只取命中叶子，UNKNOWN / 未命中叶子不写入，避免夸大命中
    rule_parts = [
        f"{leaf.metric} {leaf.operator} {leaf.threshold:g}" for leaf in matched
    ]
    rule_text = "、".join(rule_parts) if rule_parts else "组合条件命中"
    actual_parts = [
        f"{leaf.metric}={leaf.value:g}"
        for leaf in matched
        if leaf.value is not None
    ]
    actual_text = "；".join(actual_parts) if actual_parts else "观测值不可用"
    basis = {
        "rule": f"规则组合命中（{len(matched)}/{len(verdict.leaves)} 叶）：{rule_text}",
        "actual": f"命中观测：{actual_text}",
        "evidence": source.strip() or "规则组合引擎只读求值；数据不可用绝不折叠为命中",
    }
    stamp = created_at
    eid = event_id or rule_id or "rule-combination"
    return {
        "id": eid,
        "kind": "price_observation",
        "level": "OBSERVATION",
        "severity": severity_of(verdict),
        "symbol": symbol,
        "position_id": None,
        "trigger_at": trigger_at,
        "threshold_at": threshold_at,
        "sellable_now": False,
        "action_hint": "组合监控条件命中，请人工复核",
        "t1_note": "观察条件不代表买卖信号或成交",
        "basis_json": basis,
        "status": "OPEN",
        "handled_at": None,
        "handled_note": "",
        "ignore_streak": 0,
        "created_at": stamp,
        "updated_at": stamp,
        "rule_id": rule_id,
        "observation_rule_id": rule_id,
        "rule_revision": rule_revision,
        "condition_active": True,
        "freq_mode": "once",
        "last_notified_at": None,
        "last_emitted_at": stamp,
        "quote_as_of": "",
        "quote_source": source,
        "user_note": note,
        "cooldown_minutes": cooldown_minutes,
    }
