"""Pure personal boundaries for a position worksheet, never strategy thresholds.

A loss allowance is anchored to an explicitly confirmed position and quote.
Refreshes describe its consumption; they cannot renew the user's loss budget.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
import math
from typing import Any

from position_action_plan import ScenarioInput

_ATTENTION = frozenset({"available", "occasional", "unavailable", "unknown"})
_POSITION_FIELDS = ("position_id", "cost", "shares", "sellable_shares")


@dataclass(frozen=True)
class RiskBaseline:
    price: float
    shares: float
    market_value: float
    as_of: str
    confirmed_at: str


def default_constraints() -> dict[str, Any]:
    """Return fresh unknown conditions; unknown money is never zero."""
    return {"attention": "unknown", "extra_cash": None, "loss_limit": None,
            "risk_baseline": None}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _snapshot(holding: dict[str, Any]) -> dict[str, Any]:
    return {key: holding.get(key) for key in _POSITION_FIELDS}


def _aware(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _quote(basis: dict[str, Any]) -> tuple[float, datetime] | None:
    price, stamp = _number(basis.get("price")), _aware(basis.get("quote_as_of"))
    if basis.get("quote_issue") or price is None or price <= 0 or stamp is None:
        return None
    checked = _aware(basis.get("checked_at"))
    if checked is not None and stamp > checked:
        return None
    return price, stamp


def _baseline_valid(constraints: dict[str, Any], holding: dict[str, Any]) -> bool:
    baseline = constraints.get("risk_baseline")
    if not isinstance(baseline, dict) or constraints.get("position_snapshot") != _snapshot(holding):
        return False
    price, shares, value = (_number(baseline.get(key)) for key in ("price", "shares", "market_value"))
    if price is None or shares is None or value is None or price <= 0 or shares <= 0:
        return False
    return bool(shares == holding.get("shares") and math.isclose(price * shares, value)
                and _aware(baseline.get("as_of")) and _aware(baseline.get("confirmed_at")))


def parse_constraints(
    raw: dict[str, Any], previous: dict[str, Any] | None,
    holding: dict[str, Any], basis: dict[str, Any], *, current: datetime,
) -> dict[str, Any]:
    """Validate explicit confirmation and anchor a finite loss allowance once.

    Omitted money fields mean unknown. A finite unchanged allowance keeps its
    original baseline across quote refreshes and attention-only confirmations.
    A changed position requires confirmation here before its allowance is used.
    """
    if not isinstance(raw, dict) or set(raw) - {"attention", "extra_cash", "loss_limit", "reset_risk_baseline"}:
        raise ValueError("个人条件包含未知字段")
    if not isinstance(raw.get("attention"), str) or raw["attention"] not in _ATTENTION:
        raise ValueError("盯盘时间必须为 available、occasional、unavailable 或 unknown")
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("个人条件确认时间必须包含时区")
    reset = raw.get("reset_risk_baseline", False)
    if type(reset) is not bool:
        raise ValueError("reset_risk_baseline 必须是布尔值")
    result = default_constraints()
    result["attention"] = raw["attention"]
    for key in ("extra_cash", "loss_limit"):
        value = raw.get(key)
        if value is not None:
            number = _number(value)
            if number is None or number < 0:
                raise ValueError(f"{key} 必须是未知 null 或有限非负数字")
            value = number
        result[key] = value
    result["position_snapshot"] = _snapshot(holding)
    prior = previous or default_constraints()
    if result["loss_limit"] is None:
        return result
    if (not reset and prior.get("loss_limit") == result["loss_limit"]
            and _baseline_valid(prior, holding)):
        result["risk_baseline"] = deepcopy(prior["risk_baseline"])
        return result
    quote = _quote(basis)
    shares = _number(holding.get("shares"))
    if quote is None or quote[1] > current or shares is None or shares <= 0:
        raise ValueError("确认新增损失预算需要有效现价、行情时点与当前持仓数量，请刷新后重试")
    price, stamp = quote
    if not math.isfinite(price * shares):
        raise ValueError("持仓市值超出有效数值范围")
    result["risk_baseline"] = asdict(RiskBaseline(
        price, shares, price * shares, stamp.isoformat(), current.isoformat(),
    ))
    return result


def describe_constraints(
    constraints: dict[str, Any], holding: dict[str, Any], basis: dict[str, Any],
) -> dict[str, Any]:
    """Describe evidence-backed quantity bounds, without suggesting a quantity."""
    shares = _number(holding.get("shares"))
    sellable = _number(holding.get("sellable_shares"))
    sellable_known = shares is not None and shares >= 0 and sellable is not None and sellable >= 0
    available = min(shares, sellable) if sellable_known else 0
    code = str(holding.get("code") or holding.get("symbol") or "")
    minimum, unit = (200, 1) if code.startswith("688") else ((100, 1) if code.startswith(("4", "8", "92")) else (100, 100))
    issues: list[str] = []
    loss = _number(constraints.get("loss_limit"))
    valid = _baseline_valid(constraints, holding)
    remaining = None
    if loss is not None:
        if not valid:
            issues.append("持仓数量、成本或可卖底仓已变化，或损失预算基准缺失；请重新确认，旧风险预算不作为当前约束")
        elif (quote := _quote(basis)) is None:
            issues.append("当前行情无效，无法核对确认以来已发生的新增损失")
        elif quote[1] < _aware(constraints["risk_baseline"]["as_of"]):
            issues.append("当前行情早于预算确认基准，不能核对新增损失")
        else:
            baseline = constraints["risk_baseline"]
            consumed = max(0.0, baseline["market_value"] - quote[0] * baseline["shares"])
            remaining = max(0.0, loss - consumed)
            if consumed >= loss:
                issues.append("已达到确认的新增损失预算，请复核整个余仓风险")
    if not sellable_known:
        issues.append("当前可卖股份未知，请核对 T+1 与成交记录")
    note = (f"最多按当前可卖底仓 {available:g} 股核对；回补至少 {minimum} 股、递增单位 {unit} 股。"
            "这只是申报与资金边界，不是建议数量；未有可信失效价，风险数量尚未验证。"
            "减仓后的余仓仍承受价格风险，做 T 回补后恢复原有敞口。")
    extra = constraints.get("extra_cash")
    if extra == 0:
        note += "不追加资金：仍可在实际卖出净现金内回补，须覆盖两腿费用。"
    elif extra is None:
        note += "追加资金上限未知，不能视为零追加或无限资金。"
    else:
        note += f"回补净资金缺口不得超过确认上限 {extra:g} 元。"
    return {"constraints_issue": "；".join(issues), "risk_remaining": remaining,
            "quantity_bounds": {"sellable_shares": available, "buy_lot": minimum,
                                "extra_cash_limit": extra, "note": note}}


def apply_route_constraints(
    review: dict[str, Any], constraints: dict[str, Any],
    holding: dict[str, Any], basis: dict[str, Any],
) -> dict[str, Any]:
    """Add explicit personal gaps to route facts without claiming suitability."""
    result = deepcopy(review)
    description = describe_constraints(constraints, holding, basis)
    result.update(description)
    gaps: list[str] = []
    attention = constraints.get("attention", "unknown")
    if attention == "unknown":
        gaps.append("能否盯盘尚未确认")
    elif attention == "occasional":
        gaps.append("只能偶尔盯盘，需确认两腿到价时能否及时复核及买不回的安排")
    if constraints.get("extra_cash") is None:
        gaps.append("新增资金上限未知")
    if constraints.get("loss_limit") is None:
        gaps.append("可承受的新增损失未知")
    elif description["constraints_issue"]:
        gaps.append(description["constraints_issue"])
    generic = {"个人风险与资金约束", "能否盯盘", "你可承受的后续损失"}
    result["missing_evidence"] = list(dict.fromkeys(
        [item for item in review.get("missing_evidence", ()) if item not in generic] + gaps
        + ["可信失效价缺失，风险数量未验证；需一并复核余仓风险"]
    ))
    routes = []
    for original in review.get("routes", ()):
        row = dict(original)
        needs = [item for item in row.get("needs", ()) if item not in generic]
        choice = row.get("choice")
        if constraints.get("loss_limit") is None and choice in {"hold", "reduce"}:
            needs.append("可承受的新增损失未知")
        if choice == "t_watch":
            needs.extend(gaps)
            if attention == "unavailable":
                row["status"] = "paused"
                row["reason"] = f"当前不能盯盘，暂停做 T 测算；仍可记录观察意向。{row.get('reason', '')}"
            elif attention == "occasional":
                row["reason"] = f"只能偶尔盯盘，及时复核两腿的条件未确认。{row.get('reason', '')}"
            elif attention == "available":
                row["reason"] = f"已确认能盯盘，但不等于适合做 T。{row.get('reason', '')}"
            cash = constraints.get("extra_cash")
            row["reason"] += (" 不追加资金，回补仍可使用实际卖出净现金，需覆盖往返费用。" if cash == 0 else
                              " 新增资金上限未知，不能推定可追加。" if cash is None else
                              f" 两腿净资金缺口上限为 {cash:g} 元。")
        row["needs"] = list(dict.fromkeys(needs))
        routes.append(row)
    result["routes"] = routes
    return result


def validate_personal_scenario(
    choice: str, parameters: dict[str, Any], scenario: dict[str, Any],
    constraints: dict[str, Any],
) -> None:
    """Enforce attention and net funding, retaining existing scenario validation.

    The server supplies ``scenario`` from calculate_scenario, not client cash.
    Positive net proceeds can fund a same-quantity repurchase with zero extra
    cash; a loss allowance without a verified invalidation price cannot validate
    risk-based position sizing.
    """
    if choice != "t_watch":
        return
    if constraints.get("attention") == "unavailable":
        raise ValueError("当前不能盯盘，暂停做 T 测算；可保留观察意向")
    ScenarioInput.parse(parameters, choice=choice)
    cash = constraints.get("extra_cash")
    net = _number(scenario.get("net_cash"))
    if net is None:
        raise ValueError("两腿净现金未核验，不能确认资金边界")
    if cash is not None:
        limit = _number(cash)
        if limit is None or limit < 0:
            raise ValueError("新增资金约束无效，请重新确认")
        if net < -limit:
            raise ValueError(f"两腿净资金缺口 {-net:g} 元超过新增资金上限 {limit:g} 元；零追加仍可使用实际卖出净现金回补")
