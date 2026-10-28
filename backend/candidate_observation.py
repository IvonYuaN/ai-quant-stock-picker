"""Tenant-owned candidate observation resources; never creates a position.

Research and quotes are server facts. Intent uses resource CAS; model references
are frozen. Quote ticks are excluded from semantic versions, direction/source
availability and research revisions are included. No scoring or order writes.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import fcntl
import json
import math
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import aqsp_bridge
import astock
import decision_context as dc
import portfolio as pf
import tenant
from aqsp.core.errors import DataError
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text
from position_route_review import evidence_digest
import position_strategy_evidence as sources
from strategy_direction import direction_identity


@dataclass(frozen=True)
class CandidateLimits:
    # Storage/network resource budgets, not selection/price strategy thresholds.
    max_resources: int = 100
    max_store_bytes: int = 4_000_000
    max_audit: int = 100


class CandidateConflict(ValueError):
    """Ownership, source validity or resource CAS changed."""


def symbol(value: str) -> str:
    """Only canonical six digit A-share candidate identities are supported."""
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{6}', value):
        raise ValueError('候选代码必须为六位数字')
    return value


def root() -> Path:
    """Resolve only the active authenticated tenant, without legacy migration."""
    tid = tenant.current_tenant.get()
    if tid != 'local' and not re.fullmatch(r'[uk]_[0-9a-f]{20}', tid):
        raise DataError('候选租户标识无效')
    base = Path(pf.CACHE_DIR)
    path = base if tid == 'local' else base / 'users' / tid
    if base.is_symlink() or path.is_symlink() or (path.parent != base and path.parent.is_symlink()):
        raise DataError('候选存储路径不可用')
    return path


def store_path() -> Path:
    """Return the private candidate store; no portfolio files are created."""
    path = root() / 'candidate-observations.json'
    if path.is_symlink():
        raise DataError('候选存储路径不可用')
    return path


def discussion_lease_path() -> Path:
    """Use the same global lease as existing position model discussions."""
    path = Path(pf.CACHE_DIR) / '.position-discussion.lock'
    if path.is_symlink():
        raise DataError('策略讨论锁路径不可用')
    return path


def recovered_analysis(row: dict[str, Any]) -> dict[str, Any]:
    """An orphaned running flag must never permanently disable UI recovery."""
    state = deepcopy(row.get('analysis') or {'status': 'idle', 'stage': '', 'run_id': None})
    if state.get('status') != 'running':
        return state
    path = discussion_lease_path()
    if path.exists():
        with path.open('a') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return state
    return {**state, 'status': 'cancelled', 'stage': 'interrupted'}


def load_store() -> dict[str, Any]:
    """Read fail-closed JSON state without repairing damaged user resources."""
    path = store_path()
    try:
        if path.stat().st_size > CandidateLimits().max_store_bytes:
            raise DataError('候选观察文件超过读取预算')
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'schema_version': 1, 'resources': {}}
    except (OSError, ValueError, UnicodeError) as exc:
        raise DataError('候选观察存储不可读，未覆盖原记录') from exc
    try:
        assert isinstance(value, dict) and value.get('schema_version') == 1
        resources = value['resources']
        assert isinstance(resources, dict) and len(resources) <= CandidateLimits().max_resources
        for code, row in resources.items():
            symbol(code)
            assert isinstance(row, dict) and row['symbol'] == code
            assert type(row['version']) is int and row['version'] >= 1
            assert isinstance(row.get('operations', {}), dict)
            assert row.get('watch') is None or isinstance(row['watch'], dict)
            assert row.get('discussion') is None or isinstance(row['discussion'], dict)
            watch = row.get('watch')
            if watch:
                assert watch.get('status') in {'active', 'stopped'}
                assert watch.get('choice') in {'pullback', 'confirmation', 'defer'}
                assert all(isinstance(watch.get(key), str) and watch[key] for key in ('id', 'condition', 'invalidation', 'evidence_version', 'discussion_id', 'adopted_at'))
                if watch['choice'] != 'defer':
                    assert _positive(watch.get('price')) is not None
                    assert watch.get('comparator') in {'at_or_above', 'at_or_below'}
                    assert isinstance(watch.get('source_ref'), dict)
        return value
    except (AssertionError, KeyError, ValueError, TypeError) as exc:
        raise DataError('候选观察结构损坏，未覆盖原记录') from exc


def save_store(data: dict[str, Any]) -> None:
    """Atomic writes under the caller's advisory lease."""
    text = json.dumps(data, ensure_ascii=False, allow_nan=False)
    if len(text.encode()) > CandidateLimits().max_store_bytes:
        raise DataError('候选观察文件超过保存预算')
    atomic_write_text(store_path(), text)


def ownership(code: str) -> dict[str, Any]:
    """Use raw same-tenant ownership; missing store is unowned, corrupt is unknown."""
    path = root() / 'portfolio.json'
    try:
        if path.is_symlink():
            raise ValueError('symlink')
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('holdings'), list):
            raise ValueError('invalid portfolio')
        if any(not isinstance(row, dict) or not isinstance(row.get('code'), str) for row in data['holdings']):
            raise ValueError('invalid holding')
        row = next((row for row in data['holdings'] if row['code'] == code), None)
        if row:
            return {'status': 'owned', 'position_id': row.get('position_id')}
    except FileNotFoundError:
        pass
    except (OSError, ValueError, UnicodeError):
        return {'status': 'unavailable', 'position_id': None}
    return {'status': 'unowned', 'position_id': None}


def research(code: str, current: datetime) -> tuple[dict[str, Any], list[str]]:
    """Use current authoritative research only; archives never become entry plans."""
    issues: list[str] = []
    try:
        response = aqsp_bridge.snapshot_response()
        snap, meta = response['data'], response['meta']
        candidate = next((row for row in snap['candidates'] if row['symbol'] == code), None)
        if candidate is None:
            raise ValueError('candidate absent')
        completed = dc._session(current, completed=True)
        deadline = datetime.fromisoformat(snap.get('stale_after') or '')
        generated = datetime.fromisoformat(snap['generated_at'])
        if generated.tzinfo is None or generated > current:
            issues.append('候选研究生成时点无效或在未来')
        if deadline.tzinfo is None or current >= deadline or meta.get('historical') or meta.get('stale') or not completed or not completed.isoformat() <= snap['selected_date'] <= current.date().isoformat():
            issues.append('当前候选研究已过期，需更新研究后再分析')
        freshness = str(candidate.get('freshness') or snap.get('source', {}).get('status') or '').lower()
        if freshness not in {'fresh', 'current', 'realtime', '实时', '新鲜'}:
            issues.append('候选研究未标为当前有效来源')
        stamp = candidate.get('data_fetched_at')
        try:
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None or parsed.utcoffset() is None or parsed > current or (completed and parsed.date() < completed):
                raise ValueError('invalid source time')
        except (ValueError, TypeError):
            issues.append('候选研究缺少有效来源时点')
        if not candidate.get('data_source') or not candidate.get('data_timestamp_source'):
            issues.append('候选研究来源信息不完整')
        gate = snap.get('recommendation_gate') or {}
        candidate_change = next((row for row in snap.get('candidate_changes', [])
                                 if row.get('symbol') == code and row.get('status') == 'invalidated'), None)
        invalidated = candidate.get('research_status') == '日内已失效' or candidate_change is not None
        result = {'candidate': candidate,
                  'candidate_invalidation': (candidate_change or {'status': 'invalidated'}) if invalidated else None,
                  'date': snap['selected_date'], 'generated_at': snap['generated_at'],
                  'stale_after': snap.get('stale_after'), 'source': snap.get('source'),
                  'parameter_snapshot': snap.get('parameter_snapshot'),
                  'recommendation_gate': gate,
                  'debate': next((row for row in snap.get('debates', []) if row.get('symbol') == code), None)}
        return result, issues
    except (aqsp_bridge.AQSPBridgeError, OSError, ValueError, KeyError, TypeError):
        return {}, ['当前候选研究不可用；历史研究不能作为新的观察依据']


def _positive(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def evidence_basis(report: dict[str, Any], evidence: dict[str, Any],
                   own: dict[str, Any], quote: dict[str, Any], quote_valid: bool) -> dict[str, str]:
    """Hash substantive facts, retaining the full original evidence for audit.

    Snapshot publication/expiry and source refresh times are checked separately
    on every read. They are not a new thesis. All candidate economic fields,
    including technical/reference prices and news publication dates, remain in
    the identity; refresh time and display-only coverage notes are omitted.
    Source validity and the current risk gate are checked independently.
    """
    candidate = {key: value for key, value in (report.get('candidate') or {}).items()
                 if key not in {'data_fetched_at', 'coverage_note'}}
    parts = {
        'research_date': report.get('date'),
        'parameters': report.get('parameter_snapshot'),
        'candidate': candidate,
        'risk_gate': report.get('recommendation_gate'),
        'research_source': report.get('source'),
        'debate': report.get('debate'),
        'ownership': own,
        'quote_source': {'valid': quote_valid, 'source': quote.get('source') or '腾讯行情'},
        'directions': direction_identity(evidence),
    }
    if report.get('candidate_invalidation'):
        parts['candidate_invalidation'] = True
    return {'schema': 'candidate-basis-v3', **{key: evidence_digest(value) for key, value in parts.items()}}


def current_evidence(code: str, quote: dict[str, Any], *, current: datetime) -> dict[str, Any]:
    """Build sourced facts without inventing shares, costs or holding identities."""
    report, issues = research(code, current)
    gate = report.get('recommendation_gate') or {}
    entry_blocked = (gate.get('recommendation_allowed') is not True
                     or str((report.get('source') or {}).get('status') or '').lower()
                     not in {'fresh', 'current', 'realtime', '实时', '新鲜'})
    own = ownership(code)
    if own['status'] != 'unowned':
        issues.append('已有持仓，请使用持仓策略' if own['status'] == 'owned' else '持仓状态不可读，先核对持仓数据')
    quote_issue = dc.position_quote_issue(quote, checked_at=current)
    if _positive(quote.get('price')) is None:
        quote_issue = quote_issue or '缺少有效现价'
    if quote_issue:
        issues.append(quote_issue)
    stamp = quote.get('as_of') or astock.parse_gtimg_time(quote.get('quote_time'))
    prices = {}
    if not quote_issue:
        for key, label in (('price', '当前有源报价'), ('last_close', '行情源前收盘价'), ('high', '本交易日已见高点'), ('low', '本交易日已见低点')):
            value = _positive(quote.get(key))
            if value is not None:
                prices[key] = {'label': label, 'price': value, 'as_of': stamp, 'source': str(quote.get('source') or '腾讯行情')}
    overview = dc.load_cached_market_overview(checked_at=current)
    from dataclasses import asdict
    market_fact = asdict(dc._market_context(overview, current))
    if market_fact['status'] == 'unknown':
        market_fact = sources._indices(current) or market_fact
    sector = sources.load_cached_strategy_sectors([code], checked_at=current).get(code)
    identity = {}
    if isinstance(sector, dict) and not dc.position_quote_issue({'as_of': sector.get('membership_as_of')}, checked_at=current):
        identity = {'industry': sector.get('industry')}
    sector_fact = asdict(dc._sector_context(identity, overview, sector, current))
    sector_fact['industry'] = identity.get('industry')
    sector_fact['change_pct'] = dc._number(sector.get('change_pct')) if sector and sector_fact['status'] != 'unknown' else None
    if market_fact['status'] != 'unknown' and overview:
        sentiment = overview.get('sentiment') or {}
        market_fact['breadth'] = sentiment.get('breadth')
        market_fact['breadth_counts'] = {key: dc._number(sentiment.get(key)) for key in ('up', 'down')}
    indices = sources._indices(current)
    if indices and market_fact['status'] != 'unknown':
        market_fact['indices'] = indices['indices']
    stock_fact = asdict(dc._stock_context(quote, None, current))
    evidence = {'research': report, 'quote': {'price': quote.get('price'), 'change_pct': quote.get('change_pct'), 'as_of': stamp, 'source': quote.get('source') or '腾讯行情'},
                'market': market_fact, 'sector': sector_fact, 'stock': stock_fact}
    basis = evidence_basis(report, evidence, own, quote, not bool(quote_issue))
    coverage_note = str((report.get('candidate') or {}).get('coverage_note') or '')
    candidate_invalidation = report.get('candidate_invalidation')
    entry_reason = (
        '候选已有明确失效状态，旧观察需复核，不能采用新的入场条件' if candidate_invalidation else
        coverage_note if coverage_note else
        '当前研究门禁阻塞，先补齐研究验证或暂缓' if entry_blocked else ''
    )
    return {'symbol': code, 'name': report.get('candidate', {}).get('display_name') or quote.get('name') or code,
            'research_date': report.get('date'), 'evidence_version': evidence_digest(basis), 'evidence_basis': basis, 'ownership': own,
            'available': not issues, 'entry_blocked': entry_blocked,
            'new_entry_blocked': entry_blocked or bool(coverage_note) or bool(candidate_invalidation),
            'new_entry_block_reason': entry_reason, 'coverage_note': coverage_note,
            'candidate_invalidation': candidate_invalidation,
            'issues': issues, 'quote': quote, 'quote_issue': quote_issue,
            'evidence': evidence, 'price_refs': prices}


def _empty(code: str) -> dict[str, Any]:
    return {'symbol': code, 'version': 0, 'discussion': None, 'watch': None, 'observation': None,
            'analysis': {'status': 'idle', 'stage': '', 'run_id': None}, 'operations': {}}


def workspace(code: str, *, quote: dict[str, Any] | None = None, current: datetime | None = None) -> dict[str, Any]:
    """Return current facts and durable observation; GET does not mutate intent."""
    code, current = symbol(code), current or now_shanghai()
    if quote is None:
        try:
            quote = astock.tencent_quote([code]).get(code, {})
        except Exception:  # noqa: BLE001 -- optional quote failures are explicit unknown
            quote = {}
    value = current_evidence(code, quote, current=current)
    row = load_store()['resources'].get(code, _empty(code))
    observed = observation(value, row, current=current) if row.get('watch') else row.get('observation')
    row = {**row, 'observation': observed, 'analysis': recovered_analysis(row)}
    return {**value, **{key: deepcopy(row.get(key)) for key in ('version', 'discussion', 'watch', 'observation', 'analysis')}}


def list_workspaces() -> dict[str, Any]:
    """Read persisted states only; no quotes/models for the home list."""
    rows = load_store()['resources']
    return {'items': [{**deepcopy(row.get('last_workspace') or {'symbol': code, 'name': code}),
                       **{key: deepcopy(row.get(key)) for key in ('version', 'discussion', 'watch', 'observation')},
                       'analysis': recovered_analysis(row),
                       'ownership': ownership(code)} for code, row in rows.items()]}


async def refresh_sources(code: str) -> dict[str, Any]:
    """Explicit bounded public-source refresh, independent of holdings/LLM work."""
    code = symbol(code)
    report, _ = research(code, now_shanghai())
    if not report:
        raise CandidateConflict('当前候选研究不可用，请先更新研究；未请求外部来源')
    if ownership(code)['status'] != 'unowned':
        raise CandidateConflict('持仓归属已变化，请使用持仓策略核对')
    refreshed = await sources.refresh_strategy_sources([code], checked_at=now_shanghai())
    value = await asyncio.to_thread(workspace, code)
    return {**value, 'source_refresh': refreshed.to_dict()}


def checked(value: dict[str, Any], version: int, evidence_version: str) -> None:
    """Require current source, ownership and CAS before model work or adoption."""
    if not value['available']:
        raise CandidateConflict('；'.join(value['issues']))
    if value['version'] != version or value['evidence_version'] != evidence_version:
        raise CandidateConflict('候选研究、风险或观察方案已变化，请刷新后重新分析')


def new_row(data: dict[str, Any], code: str) -> dict[str, Any]:
    """Get a resource in a caller-owned store; enforce a finite tenant budget."""
    if code not in data['resources']:
        if len(data['resources']) >= CandidateLimits().max_resources:
            retired = next((key for key, row in data['resources'].items()
                            if (not row.get('watch') or row['watch'].get('status') == 'stopped')
                            and row.get('analysis', {}).get('status') != 'running'), None)
            if retired is None:
                raise CandidateConflict('候选活跃观察数量已达本次资源预算，请先停止旧观察')
            del data['resources'][retired]
        data['resources'][code] = _empty(code)
    return data['resources'][code]


def remember(row: dict[str, Any], value: dict[str, Any]) -> None:
    """Persist display facts separately from intent and model results."""
    row['last_workspace'] = {key: deepcopy(item) for key, item in value.items()
                             if key not in {'discussion', 'watch', 'observation', 'analysis', 'version'}}


def _operation(row: dict[str, Any], operation_id: str, body: dict[str, Any]) -> bool:
    if not operation_id.strip() or len(operation_id) > 128:
        raise ValueError('操作标识无效')
    old = row.get('operations', {}).get(operation_id)
    digest = evidence_digest(body)
    if old:
        if old != digest:
            raise CandidateConflict('重复操作标识对应不同内容')
        return True
    row.setdefault('operations', {})[operation_id] = digest
    if len(row['operations']) > CandidateLimits().max_audit:
        del row['operations'][next(iter(row['operations']))]
    return False


def adopt(code: str, body: dict[str, Any]) -> dict[str, Any]:
    """Adopt an exact model condition with a human-chosen quote comparator."""
    code = symbol(code)
    value = workspace(code)
    with advisory_lock(store_path()):
        data = load_store()
        row = data['resources'].get(code, _empty(code))
        if _operation(row, body['operation_id'], body):
            return {**value, **{key: deepcopy(row.get(key)) for key in ('version', 'discussion', 'watch', 'observation', 'analysis')}}
        value['version'] = row['version']
        # Research files can be atomically replaced while quote I/O is pending.
        # Re-read their authority at the intent commit, without more network.
        refreshed = current_evidence(code, value['quote'], current=now_shanghai())
        value.update(refreshed)
        checked(value, body['expected_version'], body['expected_evidence_version'])
        # Ownership can change during quote/research I/O.
        if ownership(code)['status'] != 'unowned':
            raise CandidateConflict('候选已成为持仓或持仓状态不可读，请刷新')
        result = row.get('discussion') or {}
        if result.get('id') != body['discussion_id'] or result.get('generated_at') != body['generated_at'] or result.get('status') != 'complete' or result.get('evidence_version') != value['evidence_version']:
            raise CandidateConflict('原模型结果已变化或不完整，请重新分析')
        current = now_shanghai()
        result_stamp = dc._source_date(result.get('generated_at'), current)
        if result_stamp is None or result_stamp[0] != dc._session(current):
            raise CandidateConflict('模型结果不是本交易日分析，请重新分析')
        if dc.position_quote_issue(result.get('evidence_snapshot', {}).get('quote'), checked_at=current):
            raise CandidateConflict('原模型使用的行情源时点已过期，请重新分析')
        choice, index, comparator = body['choice'], body.get('action_index'), body.get('comparator')
        if choice not in {'pullback', 'confirmation', 'defer'}:
            raise ValueError('未知候选路线')
        if refreshed['new_entry_blocked'] and choice != 'defer' and refreshed['candidate_invalidation']:
            raise CandidateConflict(refreshed['new_entry_block_reason'])
        if refreshed['coverage_note'] and choice != 'defer':
            raise CandidateConflict('本轮无新研究，保留已有观察；补齐本轮依据后再采用新的入场条件')
        if value['new_entry_blocked'] and choice != 'defer':
            raise CandidateConflict(value['new_entry_block_reason'] or '当前研究门禁阻塞，只能暂缓或补证，不能采用入场观察')
        action, ref = None, None
        if choice == 'defer':
            if index is not None or comparator is not None:
                raise ValueError('暂缓路线不设置到价条件')
        else:
            actions = result.get('summary', {}).get('actions', [])
            route = next((item for item in result.get('summary', {}).get('routes', []) if item.get('choice') == choice), {})
            if route.get('judgment') != 'investigate':
                raise ValueError('这份分析尚不支持采用该入场路线，请补证或暂缓')
            if type(index) is not int or not 0 <= index < len(actions) or comparator not in {'at_or_above', 'at_or_below'}:
                raise ValueError('请选择模型观察条件并明确比较方向')
            action = actions[index]
            ref = result.get('price_refs', {}).get(action.get('price_ref'))
            if action.get('choice') != choice or not isinstance(ref, dict) or _positive(ref.get('price')) is None:
                raise ValueError('所选动作没有对应路线的有源观察价')
        row = new_row(data, code)
        _operation(row, body['operation_id'], body)
        row['version'] += 1
        row['watch'] = {'id': uuid4().hex, 'choice': choice, 'condition': action['condition'] if action else result['summary']['focus_reason'],
                        'invalidation': action['invalidation'] if action else '当前研究或风险变化后重新核对',
                        'price': ref['price'] if ref else None, 'price_ref': action['price_ref'] if action else None,
                        'source_ref': deepcopy(ref),
                        'comparator': comparator, 'adopted_at': current.isoformat(), 'discussion_id': result['id'],
                        'evidence_snapshot': deepcopy(value['evidence']), 'price_refs': deepcopy(value['price_refs']),
                        'evidence_identity': {'symbol': code},
                        'ownership': deepcopy(value['ownership']),
                        'evidence_version': value['evidence_version'], 'evidence_basis': deepcopy(value['evidence_basis']), 'status': 'active'}
        row['observation'] = observation(value, row, current=current)
        remember(row, value)
        save_store(data)
    return workspace(code, quote=value['quote'], current=current)


def stop(code: str, body: dict[str, Any]) -> dict[str, Any]:
    """Stop candidate observations, retry-safe, without affecting position risk."""
    code = symbol(code)
    with advisory_lock(store_path()):
        data = load_store()
        row = data['resources'].get(code)
        if not row:
            raise CandidateConflict('没有可停止的候选观察')
        if not _operation(row, body['operation_id'], body):
            if row['version'] != body['expected_version']:
                raise CandidateConflict('观察方案已变化，请刷新')
            row['version'] += 1
            if row.get('watch'):
                row['watch']['status'] = 'stopped'
            row['observation'] = {'state': 'stopped', 'title': '已停止候选观察', 'detail': '不再发送此候选的到价提醒',
                                  'observed_at': now_shanghai().isoformat(), 'quote_as_of': None, 'reason': 'stopped'}
            save_store(data)
    return next(item for item in list_workspaces()['items'] if item['symbol'] == code)


def observation(value: dict[str, Any], row: dict[str, Any], *, current: datetime) -> dict[str, Any]:
    """Compare only the adopted quote predicate; text conditions stay unverified."""
    watch = row.get('watch') or {}
    state, title, detail, reason = 'waiting', '等待已采用的候选条件', '未到保存的观察价，其他条件仍需人工核对', 'waiting'
    if watch.get('status') != 'active':
        state, title, detail, reason = 'stopped', '已停止候选观察', '不再发送此候选的到价提醒', 'stopped'
    elif value['ownership']['status'] != 'unowned':
        state, title, detail, reason = 'needs_review', '持仓状态变化，重核候选计划', '已有持仓或状态不可读，请使用持仓策略核对', 'ownership'
    elif not value['available']:
        state, title, detail, reason = 'evidence_unavailable', '当前依据不可用，不能判断到价', '；'.join(value['issues']), 'source'
    elif watch.get('evidence_version') != value['evidence_version']:
        labels = {'research_date': '研究日期', 'parameters': '策略参数版本', 'candidate': '个股评分或研究证据',
                  'candidate_invalidation': '候选明确失效',
                  'risk_gate': '研究风险门禁', 'research_source': '研究来源', 'debate': '研究讨论结论',
                  'ownership': '持仓归属', 'quote_source': '行情来源', 'directions': '大盘、板块或个股方向'}
        previous, latest = watch.get('evidence_basis') or {}, value.get('evidence_basis') or {}
        changes = [label for key, label in labels.items() if previous.get(key) != latest.get(key)]
        detail = ('变化：' + '、'.join(changes) if previous.get('schema') == latest.get('schema') else '原观察使用旧版依据标识，需重新核对一次') + '；原观察条件暂停作为当前入场依据'
        state, title, reason = 'needs_review', '研究或风险依据变化，重新分析', 'research_changed'
    elif value.get('entry_blocked') and watch['choice'] != 'defer':
        state, title, detail, reason = 'needs_review', '当前研究门禁阻塞', '先补齐研究验证，原入场条件不作为当前依据', 'risk_gate'
    elif watch['choice'] == 'defer':
        state, title, detail, reason = 'deferred', '已暂缓这只候选', watch['condition'], 'defer'
    else:
        target, price = _positive(watch.get('price')), _positive(value['quote'].get('price'))
        if target is None or price is None or watch.get('comparator') not in {'at_or_above', 'at_or_below'}:
            state, title, detail, reason = 'needs_review', '观察价不可核验', '请重新分析后采用有源条件', 'invalid'
        else:
            reached = price >= target if watch['comparator'] == 'at_or_above' else price <= target
            direction = '达到或高于' if watch['comparator'] == 'at_or_above' else '达到或低于'
            detail = f"当前 {price:.2f} 元；等待报价{direction} {target:.2f} 元后核验：{watch['condition']}。失效条件：{watch['invalidation']}。到价只代表价格条件，其他前提尚未自动确认。"
            if reached:
                state, title, reason = 'price_watch', '已到候选观察价，核验入场前提', 'price'
    return {'state': state, 'title': title, 'detail': detail, 'reason': reason,
            'observed_at': current.isoformat(), 'quote_as_of': value['evidence']['quote']['as_of']}
