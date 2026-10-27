"""Pure current-evidence comparison before choosing a human position plan.

Availability describes facts and constraints, never investment suitability or a
new trading signal. No network, persistence, strategy weights or order actions.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from decision_context import _number, _session, _source_date, _stock_context, _timely_quote


@dataclass(frozen=True)
class RouteReview:
    choice: str
    status: str
    reason: str
    needs: tuple[str, ...]


@dataclass(frozen=True)
class PositionRouteReview:
    title: str
    summary: str
    unrealized_pnl_pct: float | None
    recovery_to_cost_pct: float | None
    routes: tuple[RouteReview, ...]
    evidence: dict[str, Any]
    missing_evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a display contract, without selecting or mutating a plan."""
        return asdict(self)


def evidence_digest(value: Any) -> str:
    """Identify source facts without hashing wall-clock read times or secrets."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def risk_evidence_revision(events: Sequence[Mapping[str, Any]]) -> str:
    """Exclude plans and discussions so analysis never invalidates itself."""
    fields = ("id", "level", "threshold_at", "trigger_at", "basis_json", "position_context", "updated_at")
    return evidence_digest([{key: row.get(key) for key in fields} for row in sorted(events, key=lambda row: str(row.get("id", "")))])


def current_position_evidence(context: Mapping[str, Any], quote: Mapping[str, Any], *, current: datetime) -> dict[str, Any]:
    """Use one source/time policy for both route display and joint-risk guards."""
    evidence: dict[str, Any] = {}
    for key, label in (("market", "大盘"), ("sector", "板块")):
        row = context.get(key) or {}
        row = row if isinstance(row, Mapping) else {}
        stamp = _source_date(row.get("as_of"), current)
        valid = bool(stamp and stamp[0] == _session(current)
                     and (key == "market" or _timely_quote(stamp, current))
                     and row.get("source") and row.get("status") in
                     ({"weak", "strong", "neutral"} if key == "market" else {"falling", "rising", "flat"}))
        # Current index facts can be useful to the model without proving a
        # broad-market stance. Preserve them as unknown, never as risk all-clear.
        facts = row.get('evidence')
        sourced_unknown = bool(row.get('status') == 'unknown' and row.get('source')
                                and isinstance(facts, (list, tuple)) and facts and all(isinstance(fact, str) for fact in facts)
                                and stamp and stamp[0] == _session(current) and _timely_quote(stamp, current))
        evidence[key] = {**row, "summary": f"本交易日检查快照：{row.get('summary', '')}"} if valid or sourced_unknown else {
            "status": "unknown", "summary": f"{label}当前依据待补：没有来源及时点均有效的证据",
            "as_of": None, "source": "", "evidence": [],
        }
    evidence["stock"] = asdict(_stock_context(quote, None, current))
    if evidence['stock']['status'] != 'unknown':
        evidence['stock']['change_pct'] = _number(quote.get('change_pct'))
    return evidence


def build_route_review(
    holding: Mapping[str, Any], quote: Mapping[str, Any],
    basis: Mapping[str, Any], context: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]], *, current: datetime, risk_facts: Sequence[Mapping[str, Any]] | None = None,
) -> PositionRouteReview:
    """Revalidate saved background times and compare constraints on one holding."""
    evidence = current_position_evidence(context, quote, current=current)
    missing = [f"{label}当前证据" for key, label in (("market", "大盘"), ("sector", "板块"), ("stock", "个股"))
               if evidence[key]["status"] == "unknown"]
    price, cost = basis.get("price"), holding.get("cost")
    pnl = (price / cost - 1) * 100 if price and cost and cost > 0 else None
    recovery = (cost / price - 1) * 100 if price and cost and cost > price else None
    sellable = holding.get("sellable_shares") or 0
    shares = holding.get("shares") or 0
    hard = any(row.get("level") == "L1" for row in events) if risk_facts is None else any(row.get("level") == "L1" and row.get("status") == "triggered" for row in risk_facts)
    issue = basis.get("quote_issue")
    joint = basis["joint_weakness"]
    title = "先核对风险，再决定怎么处理" if hard or joint else "先看这只持仓，再比较三条路"
    if issue:
        summary = f"当前行情尚不能用于判断：{issue}。可先查看持仓和风险记录，更新行情后再核对价位。"
    elif joint:
        summary = "大盘、所属板块和个股的同日弱势证据一致，先复核剩余仓位风险，暂停新增做 T 测算。"
    elif hard:
        summary = "风险线已触发，继续持有需要重新确认可承受损失；是否分批退出或观察，结合下面的当前证据决定。"
    else:
        summary = "先比较保留仓位、降低暴露和尝试差价的条件；当前涨跌或账面亏损都不足以单独决定路线。"
    if hard and not issue:
        threshold = (next((row.get("threshold_at") for row in events if row.get("level") == "L1"), None) if risk_facts is None else
                     next((row.get("threshold") for row in risk_facts if row.get("level") == "L1" and row.get("status") == "triggered"), None))
        if isinstance(threshold, (int, float)) and not isinstance(threshold, bool) and threshold > 0:
            summary = (f"上次检查触发风险线 {threshold:.2f} 元。" if risk_facts is None else f"当前有效风险线 {threshold:.2f} 元。") + (
                f"当前价 {price:.2f} 元仍低于该线，继续持有需核对可承受损失。" if price < threshold else
                f"当前价 {price:.2f} 元已回到该线上方，需重新检查规则；原提醒未自动解除。") + (
                "三维同日弱势证据一致，暂停新增做 T 测算。" if joint else "请结合下面证据比较处理方式。")
    if risk_facts is not None and any(row.get('status') == 'unknown' for row in risk_facts):
        missing.append('部分持仓风险规则缺少本次指标证据，未判定解除')
    if risk_facts is not None and any(row.get('level') == 'L5' and row.get('status') == 'triggered' for row in risk_facts):
        title = '当前触及回撤保护线，先核对风险'
        summary += '当前有效价已低于回撤保护线，需复核余仓；不等同自动卖出。'
        hard = True
    hold = RouteReview("hold", "risk_review" if hard or joint else "review",
                       f"继续保留 {shares:g} 股的后续涨跌敞口。" + ("已触发的风险仍需处理。" if hard else "需核对继续持有的依据。"),
                       ("你可承受的后续损失", "继续持有的趋势依据"))
    reduce = RouteReview("reduce", "paused" if issue or sellable <= 0 else "review",
                         str(issue) if issue else "当前没有可卖底仓，先核对 T+1 和实际成交日期。" if sellable <= 0
                         else f"当前 {shares:g} 股中可卖 {sellable:g} 股；卖出部分结算当前盈亏，并减少后续价格波动的影响。",
                         ("愿意退出的数量与费用", "反弹未出现或继续走弱时如何处理"))
    paused = bool(issue or sellable <= 0 or joint)
    t_reason = (str(issue) if issue else "当前没有可卖底仓，无法先卖后买。" if sellable <= 0
                else "三维同日走弱，先复核风险，暂停新增做 T 测算。" if joint
                else f"可卖底仓 {sellable:g} 股，但已见高低点尚不能证明适合做 T；还需判断波动、成交条件和往返费用。")
    t = RouteReview("t_watch", "paused" if paused else "unknown", t_reason,
                    ("能否盯盘", "可核验的日内与流动性条件", "两腿费用及买不回时的安排"))
    return PositionRouteReview(title, summary, pnl, recovery, (hold, reduce, t), evidence,
                               tuple(missing + ["个人风险与资金约束", "做 T 的流动性及费用条件"]))


def current_rule_facts(rules: Sequence[Mapping[str, Any]], basis: Mapping[str, Any], *, current: datetime) -> list[dict[str, Any]]:
    """Recheck source-priced user rules without creating an alert or a signal.

    L2/L4 need bar evidence, so quote-only reads cannot mark them cleared.
    Disabled or future-effective rules are explicit facts, not current triggers.
    """
    import math
    facts: list[dict[str, Any]] = []
    session = _session(current)
    for rule in rules:
        status, reason = 'unknown', '需要本次日线/指标证据，不能由现价或无提醒推定风险解除'
        threshold = rule.get('trigger_price')
        valid_threshold = (isinstance(threshold, (int, float)) and not isinstance(threshold, bool)
                           and math.isfinite(threshold) and threshold > 0)
        effective = rule.get('effective_from')
        if rule.get('enabled') is not True:
            status, reason = 'disabled', '此规则已停用；历史提醒不是当前启用规则的结论'
        elif effective and (session is None or str(effective) > session.isoformat()):
            status, reason = 'not_effective', '尚未到规则生效交易日'
        elif rule.get('level') in {'L1', 'L5'} and valid_threshold and not basis.get('quote_issue') and basis.get('price') is not None:
            status = 'triggered' if basis['price'] < threshold else 'not_triggered'
            reason = f"当前有效价 {basis['price']:.2f} 元{'低于' if status == 'triggered' else '未低于'}规则价 {threshold:.2f} 元；不自动解除原提醒或记录成交"
        facts.append({'rule_id': rule.get('id'), 'level': rule.get('level'), 'revision': rule.get('revision', 0),
                      'status': status, 'threshold': threshold if valid_threshold else None, 'reason': reason,
                      'effective_from': effective, 'as_of': basis.get('quote_as_of'), 'source': basis.get('quote_source')})
    return facts
