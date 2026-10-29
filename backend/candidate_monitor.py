"""Candidate observations in the existing monitor round, without LLM work.

Bounded batch quote I/O. Only frozen, current-source observations can notify;
semantic keys and durable receipts survive restarts, uncertain sends fail closed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

import astock
import candidate_observation as resources
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text
from position_route_review import evidence_digest
from position_followup import FollowupDefaults
import strategy_conditions as conditions


def _read(path: Path, empty: dict[str, Any]) -> dict[str, Any]:
    if path.is_symlink():
        raise DataError('候选通知存储路径不可用')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('not object')
        return data
    except FileNotFoundError:
        return deepcopy(empty)
    except (ValueError, OSError, UnicodeError) as exc:
        raise DataError('候选通知凭据不可读，停止外发') from exc


def _text(value: str) -> str:
    return re.sub(r'([\\`*_{}\[\]()<>#!|])', r'\\\1', ' '.join(value.split()))


def _render(batch: dict[str, Any]) -> str:
    row = batch['observation']
    labels = {'met': '已满足', 'unmet': '尚未满足', 'unknown': '缺少当前来源', 'manual_required': '仍需人工核验'}
    checks = (row.get('condition_checks') or {}).get('items', [])
    def status_text(item: dict[str, Any]) -> str:
        if item['role'] == 'invalidation':
            return {'met': '失效条件已触发，需复核', 'unmet': '失效条件未触发'}.get(item['status'], labels.get(item['status'], '待核验'))
        return labels.get(item['status'], '待核验')
    condition_lines = [f"{_text(item['label'])}：{status_text(item)}" for item in checks]
    # Old frozen journal messages must retain their byte-for-byte rendering.
    footer = ('保持暂缓，人工复核原前提与失效说明；当前记录不是参与或成交信号。'
              if row.get('condition_checks') and batch['watch']['choice'] == 'defer' else
              '报价只证明到达观察价；其他入场条件需人工核验，不是自动买入、成交或收益保证。')
    return '\n'.join(('# 候选观察有变化 · 待核验', '', f"**{_text(batch['name'])} {batch['symbol']}** · {_text(row['title'])}",
                      _text(row['detail']), *condition_lines, f"检查时点：{row['observed_at']}", f"行情源时点：{row.get('quote_as_of') or '不可用'}",
                      f"[查看候选研究](https://lh.ifidy.cn/stock/{batch['symbol']})", '',
                      footer))


def _validate(batch: dict[str, Any], *, source_quote: dict[str, Any] | None = None) -> bool:
    """Recheck frozen intent and current sources immediately before outbound I/O."""
    checked_at = now_shanghai()
    try:
        stamp = datetime.fromisoformat(batch['observation']['observed_at'])
        age = (checked_at - stamp).total_seconds()
        if stamp.utcoffset() is None or age < 0 or age > FollowupDefaults().max_check_age_seconds:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    data = resources.load_store()
    row = data['resources'].get(batch['symbol'])
    if not row or row['version'] != batch['version'] or row.get('watch') != batch['watch']:
        return False
    # A monitor round already fetched a bounded batch. Revalidate its original
    # source clock and current research/ownership instead of N extra quote calls.
    value = resources.workspace(batch['symbol'], quote=source_quote, current=checked_at)
    current = resources.observation(value, row, current=checked_at)
    if current['state'] != batch['observation']['state'] or current['reason'] != batch['observation']['reason']:
        return False
    frozen_checks = batch['observation'].get('condition_checks')
    current_checks = current.get('condition_checks')
    if frozen_checks is not None:
        if not isinstance(frozen_checks, dict) or not isinstance(current_checks, dict):
            return False
        contract = (row.get('watch') or {}).get('condition_contract')
        contract_digest = conditions.validate_contract(contract)['digest'] if contract is not None else None
        if frozen_checks.get('digest') != conditions.semantic_state(frozen_checks.get('items', []),
                contract_digest=contract_digest, mode=frozen_checks.get('mode', 'legacy')):
            return False
        if current_checks['digest'] != frozen_checks['digest']:
            return False
        current_sources = {(item['id'], item['role']): item.get('source') for item in current_checks['items']}
        if any(item.get('source') != current_sources.get((item['id'], item['role'])) for item in frozen_checks.get('items', [])):
            return False
        # Revalidation of new facts cannot renew a stale frozen message. Each
        # claimed automatic result retains and rechecks its original source time.
        from decision_context import position_quote_issue
        for check in frozen_checks['items']:
            if check['role'] != 'manual' and check['status'] in {'met', 'unmet'}:
                if not check.get('source') or position_quote_issue({'as_of': check.get('as_of')}, checked_at=now_shanghai()):
                    return False
    elif (row.get('watch') or {}).get('condition_contract') is not None:
        return False
    # An expired frozen quote is not made current by a new quote with same state.
    from decision_context import position_quote_issue
    if batch['observation']['state'] == 'price_watch' and position_quote_issue({'as_of': batch['observation'].get('quote_as_of')}, checked_at=now_shanghai()):
        return False
    return True


def deliver(batch: dict[str, Any]) -> bool:
    """Freeze message first; unknown send outcome never licenses a second send."""
    from aqsp.notification_runtime import dispatch_notification_once, notification_content_hash
    journal_path = resources.root() / 'candidate-observation-delivery.json'
    runtime_path = resources.root() / 'notify-state-candidate-observation.json'
    # This round's quote is validation input, never part of a durable message or
    # its identity. Old journals remain byte-stable and retain their source times.
    source_quote = batch.get('_source_quote')
    batch = {key: value for key, value in batch.items() if key != '_source_quote'}
    with advisory_lock(journal_path):
        journal = _read(journal_path, {'schema_version': 1, 'batches': {}})
        if journal.get('schema_version') != 1 or not isinstance(journal.get('batches'), dict):
            raise DataError('候选通知凭据结构无效')
        batches = journal['batches']
        key = batch['key']
        if key not in batches:
            batches[key] = {'candidate': deepcopy(batch), 'markdown': _render(batch), 'uncertain': False, 'sent': False, 'cancelled': False}
            atomic_write_text(journal_path, json.dumps(journal, ensure_ascii=False, allow_nan=False))
        frozen = batches[key]
        if not isinstance(frozen, dict) or frozen.get('markdown') != _render(frozen['candidate']) or any(type(frozen.get(name)) is not bool for name in ('uncertain', 'sent', 'cancelled')):
            raise DataError('候选冻结通知结构无效')
        runtime = _read(runtime_path, {})
        if any(not isinstance(runtime.get(name, {}), dict) for name in ('sent', 'pending', 'failed')):
            raise DataError('候选通知外发凭据结构无效')
        receipt = runtime.get('sent', {}).get(key)
        if receipt:
            if not isinstance(receipt, dict) or receipt.get('content_hash') != notification_content_hash(frozen['markdown']):
                raise DataError('候选成功凭据与冻结内容不一致')
            frozen.update(sent=True, uncertain=False)
            atomic_write_text(journal_path, json.dumps(journal, ensure_ascii=False, allow_nan=False))
        if frozen['sent'] or frozen['uncertain']:
            return False
        if frozen['cancelled']:
            if batch['observation']['observed_at'] <= frozen['candidate']['observation']['observed_at'] or not _validate(batch, source_quote=source_quote):
                return False
            frozen.update(candidate=deepcopy(batch), markdown=_render(batch), cancelled=False)
        if not _validate(frozen['candidate'], source_quote=source_quote):
            frozen['cancelled'] = True
            atomic_write_text(journal_path, json.dumps(journal, ensure_ascii=False, allow_nan=False))
            return False
        frozen['uncertain'] = True
        atomic_write_text(journal_path, json.dumps(journal, ensure_ascii=False, allow_nan=False))
        results = dispatch_notification_once(frozen['markdown'], mode='summary', prefix='candidate-observation',
                                            kind=key, state_path=runtime_path, print_fn=lambda value: None)
        runtime = _read(runtime_path, {})
        receipt = runtime.get('sent', {}).get(key)
        if receipt and receipt.get('content_hash') == notification_content_hash(frozen['markdown']):
            frozen.update(sent=True, uncertain=False)
        elif not any(result.ok for result in results):
            frozen['uncertain'] = False
        atomic_write_text(journal_path, json.dumps(journal, ensure_ascii=False, allow_nan=False))
        return frozen['sent']


def check_candidates(*, notify: bool = False) -> dict[str, int]:
    """Check a finite tenant batch, skip stopped rows, and persist stable state."""
    data = resources.load_store()
    active = {code: deepcopy(row) for code, row in data['resources'].items() if (row.get('watch') or {}).get('status') == 'active'}
    result = {'checked': 0, 'price_watch': 0, 'needs_review': 0, 'notified': 0}
    if not active:
        return result
    codes = sorted(active)[:resources.CandidateLimits().max_resources]
    try:
        quotes = astock.tencent_quote(codes)
    except Exception:  # noqa: BLE001 -- missing source is an explicit observation
        quotes = {}
    for code in codes:
        current = now_shanghai()
        value = resources.workspace(code, quote=quotes.get(code, {}), current=current)
        observed = resources.observation(value, active[code], current=current)
        with advisory_lock(resources.store_path()):
            latest = resources.load_store()
            row = latest['resources'].get(code)
            if not row or row['version'] != active[code]['version'] or row.get('watch') != active[code]['watch']:
                continue
            row['observation'] = observed
            resources.remember(row, value)
            resources.save_store(latest)
        result['checked'] += 1
        if observed['state'] == 'price_watch':
            result['price_watch'] += 1
        if observed['state'] in {'needs_review', 'evidence_unavailable'}:
            result['needs_review'] += 1
        if notify and observed['state'] in {'price_watch', 'needs_review', 'evidence_unavailable'}:
            # No quote/time/wording in the semantic identity: one alert for each
            # adopted intent + reason, even if price oscillates or process restarts.
            key = 'candidate-observation|' + evidence_digest({'symbol': code, 'watch_id': active[code]['watch']['id'],
                    'state': observed['state'], 'reason': observed['reason'],
                    **({'conditions': observed['condition_checks']['digest']} if (observed.get('condition_checks') or {}).get('mode') == 'registered' else {})})
            batch = {'key': key, 'symbol': code, 'name': value['name'], 'version': active[code]['version'],
                     'watch': active[code]['watch'], 'observation': observed, '_source_quote': deepcopy(quotes.get(code, {}))}
            result['notified'] += int(deliver(batch))
    return result
