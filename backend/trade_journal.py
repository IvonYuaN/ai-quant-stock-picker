"""Actual fills and explicit sell/buy allocations in the tenant portfolio file.

Cost-only holdings are never converted into fills. All mutation receipts survive
history hiding. Portfolio commits first; alert disposition is recoverable on retry.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

import alert_events as ae
import portfolio as pf
from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.utils.jsonl_io import advisory_lock
from trade_accounting import summarize_trades


class TradeConflictError(ValueError):
    """Refresh required; the stale request did not change the portfolio."""


@dataclass(frozen=True)
class TradeInput:
    operation_id: str
    direction: str
    code: str
    date: str
    price: float
    shares: float
    actual_fees: float | None
    expected_position_id: str | None
    expected_position_revision: str
    alert_id: str | None = None


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _data() -> dict[str, Any]:
    # A guessed identity must not trigger the legacy owner-copy migration.
    return pf._load() if Path(pf._pf_file()).is_file() else {'holdings': []}


def position_revision(data: dict[str, Any], code: str) -> str:
    """Quote-independent version including fills, fees, links and lot identity."""
    holding = next((h for h in data.get('holdings', []) if h['code'] == code), None)
    receipts = [r for r in data.get('trade_receipts', []) if r['code'] == code]
    ids = {r['receipt_id'] for r in receipts}
    links = [link for link in data.get('trade_links', []) if any(a['sell_receipt_id'] in ids or a['buy_receipt_id'] in ids for a in link['allocations'])]
    return _digest({'tenant': pf._tenant_id(), 'code': code,
                    'version': data.get('trade_code_versions', {}).get(code, 0),
                    'position': {k: holding.get(k) for k in ('position_id', 'shares', 'cost', 'opened_at', 'lots')} if holding else None,
                    'receipts': receipts, 'links': links})


def _revision(data: dict[str, Any]) -> str:
    return _digest({'tenant': pf._tenant_id(), 'version': data.get('trade_version', 0),
                    'receipts': data.get('trade_receipts', []), 'links': data.get('trade_links', [])})


def _view(data: dict[str, Any], code: str) -> dict[str, Any]:
    receipts = [r for r in data.get('trade_receipts', []) if r['code'] == code]
    ids = {r['receipt_id'] for r in receipts}
    links = [link for link in data.get('trade_links', []) if any(a['sell_receipt_id'] in ids or a['buy_receipt_id'] in ids for a in link['allocations'])]
    holding = next((h for h in data.get('holdings', []) if h['code'] == code), {})
    today = today_shanghai().isoformat()
    lots = holding.get('lots') or ([holding] if holding else [])
    return deepcopy({'revision': _revision(data), 'code': code,
        'position': {'position_id': holding.get('position_id'), 'revision': position_revision(data, code),
                     'shares': holding.get('shares', 0), 'cost': holding.get('cost', 0),
                     'sellable_shares': sum(float(lot['shares']) for lot in lots if not lot.get('opened_at') or lot['opened_at'] < today)},
        'receipts': receipts, 'links': links, 'summary': summarize_trades(receipts, links)})


def read_journal(code: str) -> dict[str, Any]:
    """Read recorded fills; never infer them from aggregate holding cost."""
    _code(code)
    with pf._LOCK:
        return _view(_data(), code)


def _code(code: str) -> None:
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        raise ValueError('代码必须是6位数字')


def _fee(value: float | None) -> None:
    if value is not None and (isinstance(value, bool) or not math.isfinite(value) or value < 0):
        raise ValueError('实际总费用须为非负有限数字；尚未核实请留空')


def _operation(operation_id: str) -> None:
    if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 128:
        raise ValueError('成交或修改编号须为1至128个字符')


def _retry(data: dict[str, Any], operation_id: str, request: dict[str, Any]) -> dict[str, Any] | None:
    prior = data.get('trade_mutations', {}).get(operation_id)
    if prior is not None:
        if prior['request'] != request:
            raise ValueError('重复操作标识的成交或修改内容不同')
        return deepcopy(prior['result'])
    # Legacy requests remain reserved after their visible history is hidden.
    if operation_id in data.get('operations', {}) or any(c.get('operation_id') == operation_id for c in data.get('closed', [])):
        raise ValueError('该操作编号已用于旧成交记录，不能复用')
    return None


def _commit(data: dict[str, Any], code: str, operation_id: str, request: dict[str, Any], *, kind: str, before: object = None) -> dict[str, Any]:
    data['trade_version'] = data.get('trade_version', 0) + 1
    versions = data.setdefault('trade_code_versions', {})
    versions[code] = versions.get(code, 0) + 1
    data.setdefault('trade_audit', []).append({'operation_id': operation_id, 'kind': kind, 'before': deepcopy(before),
                                             'request': deepcopy(request), 'recorded_at': now_shanghai().isoformat()})
    result = _view(data, code)
    data.setdefault('trade_mutations', {})[operation_id] = {'request': deepcopy(request), 'result': result}
    pf._save(data)
    return deepcopy(result)


def _reconcile_sale(data: dict[str, Any], operation_id: str) -> None:
    """Called with alerts -> portfolio locks; no second portfolio mutation."""
    receipt = next((r for r in data.get('trade_receipts', []) if r['receipt_id'] == operation_id and r['direction'] == 'sell'), None)
    if not receipt:
        return
    closed = next(c for c in data['closed'] if c.get('operation_id') == operation_id)
    # A late retry must not replace a newer sale shown on an OPEN alert.
    latest = next(r for r in reversed(data['trade_receipts']) if r['position_id'] == receipt['position_id'] and r['direction'] == 'sell')
    events = ae._load()
    changed = False
    for event in events:
        recoverable = event.get('status') == 'OPEN' or (closed.get('position_closed') and event.get('status') == 'EXPIRED')
        if event.get('symbol') != receipt['code'] or event.get('position_id') != receipt['position_id'] or not recoverable:
            continue
        sale = {key: receipt[key] for key in ('operation_id', 'date', 'price', 'shares')}
        if latest['receipt_id'] == operation_id and event.get('last_sale') != sale:
            event.update(last_sale=sale, updated_at=receipt['recorded_at'])
            changed = True
        if closed.get('position_closed'):
            event.update(status='HANDLED', condition_active=False, handled_at=receipt['recorded_at'],
                         handled_note='已回录全部卖出；按实际成交核对', operation_id=operation_id)
            changed = True
    if changed:
        ae._save(events)


def record_trade(value: TradeInput) -> dict[str, Any]:
    """Record an actual fill once, atomically with quantity/cost and durable retry."""
    _operation(value.operation_id)
    _code(value.code)
    _fee(value.actual_fees)
    if value.direction not in {'buy', 'sell'}:
        raise ValueError('成交方向须为buy或sell')
    if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in (value.price, value.shares)):
        raise ValueError('成交价与数量须为有限正数')
    pf._parse_opened_at(value.date, today_shanghai())
    request = {'kind': 'trade', **asdict(value)}
    with ae._LOCK, advisory_lock(ae._file()), pf._LOCK, advisory_lock(pf._pf_file()):
        data = _data()
        previous = _retry(data, value.operation_id, request)
        if previous is not None:
            _reconcile_sale(data, value.operation_id)
            return previous
        holding = next((h for h in data.get('holdings', []) if h['code'] == value.code), None)
        current_id = holding.get('position_id') if holding else None
        if value.expected_position_id != current_id or value.expected_position_revision != position_revision(data, value.code):
            raise TradeConflictError('持仓、成交或费用已变化，请刷新后核对；旧页面未记账')
        if holding and not current_id:
            raise TradeConflictError('旧持仓身份尚未建立，请先刷新持仓页后再回录')
        if value.alert_id:
            event = next((e for e in ae._load() if e['id'] == value.alert_id), None)
            if event is None or event.get('symbol') != value.code or event.get('position_id') != current_id or event.get('status') != 'OPEN':
                raise TradeConflictError('提醒对应的持仓已变化或已处理，请刷新后核对')
        if value.direction == 'buy':
            holding = pf._apply_holding(data, value.code, value.shares, value.price, value.date)
            identity = holding['position_id']
        else:
            sale = pf._apply_close(data, value.code, value.date, value.price, value.shares, 0,
                                   value.operation_id, require_holding=True, expected_position_id=current_id)
            identity = sale['position_id']
        receipt = {'receipt_id': value.operation_id, 'operation_id': value.operation_id, 'tenant': pf._tenant_id(),
                   'position_id': identity, 'code': value.code, 'direction': value.direction,
                   'date': value.date, 'price': value.price, 'shares': value.shares, 'actual_fees': value.actual_fees,
                   'recorded_at': now_shanghai().isoformat()}
        data.setdefault('trade_receipts', []).append(receipt)
        result = _commit(data, value.code, value.operation_id, request, kind='fill')
        _reconcile_sale(data, value.operation_id)
        return result


def update_fees(receipt_id: str, actual_fees: float | None, expected_revision: str, operation_id: str) -> dict[str, Any]:
    """CAS-protected fee correction with a separate append-only audit receipt."""
    _operation(operation_id)
    _fee(actual_fees)
    request = {'kind': 'fees', 'receipt_id': receipt_id, 'actual_fees': actual_fees, 'expected_revision': expected_revision}
    with pf._LOCK, advisory_lock(pf._pf_file()):
        data = _data()
        previous = _retry(data, operation_id, request)
        if previous is not None:
            return previous
        receipt = next((r for r in data.get('trade_receipts', []) if r['receipt_id'] == receipt_id), None)
        if receipt is None:
            raise ValueError('当前账户不存在这笔成交凭据')
        if expected_revision != _revision(data):
            raise TradeConflictError('成交费用或关联已变化，请刷新后核对')
        before = deepcopy(receipt)
        receipt['actual_fees'] = actual_fees
        return _commit(data, receipt['code'], operation_id, request, kind='fees', before=before)


def save_link(link_id: str, allocations: list[dict[str, Any]], expected_revision: str, operation_id: str) -> dict[str, Any]:
    """Replace one explicit allocation set; empty allocation retires it, not history."""
    _operation(operation_id)
    _operation(link_id)
    request = {'kind': 'link', 'link_id': link_id, 'allocations': deepcopy(allocations), 'expected_revision': expected_revision}
    with pf._LOCK, advisory_lock(pf._pf_file()):
        data = _data()
        previous = _retry(data, operation_id, request)
        if previous is not None:
            return previous
        if expected_revision != _revision(data):
            raise TradeConflictError('成交费用或关联已变化，请刷新后核对')
        links = data.setdefault('trade_links', [])
        prior = next((link for link in links if link['link_id'] == link_id), None)
        if prior is None and not allocations:
            raise ValueError('请先选择需要关联的实际卖出和买回成交')
        ids = {a.get('sell_receipt_id') for a in allocations} | {a.get('buy_receipt_id') for a in allocations}
        if prior:
            ids.update(a['sell_receipt_id'] for a in prior['allocations'])
        receipts = [r for r in data.get('trade_receipts', []) if r['receipt_id'] in ids]
        codes = {r['code'] for r in receipts}
        if len(codes) != 1 or len({r['receipt_id'] for r in receipts}) != len(ids):
            raise ValueError('关联必须指向本账户同一股票的真实成交凭据')
        code = next(iter(codes))
        if prior and prior.get('code', code) != code:
            raise ValueError('已有的关联编号不能改用于其他股票')
        before = deepcopy(prior)
        replacement = {'link_id': link_id, 'code': code, 'allocations': deepcopy(allocations), 'updated_at': now_shanghai().isoformat()}
        candidate = [link for link in links if link['link_id'] != link_id] + [replacement]
        summarize_trades(data.get('trade_receipts', []), candidate)
        data['trade_links'] = candidate
        return _commit(data, code, operation_id, request, kind='link', before=before)
