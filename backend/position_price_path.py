"""Completed raw daily facts for explanation, never intraday trade signals.

DataFrame schema: date, high, low, close, volume, source, fetched_at,
timestamp_source; optional suspended. The 5/20-bar display windows reuse the
existing briefing generator's price-path windows, not strategy thresholds.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import math
import re
from typing import Any

import pandas as pd

from aqsp.core.time import _is_basic_trading_day, market_hours
from aqsp.research.price_path import summarize_price_path
from decision_context import _calendar_known, _session


@dataclass(frozen=True)
class PricePathWindow:
    window: int
    start_date: str
    end_date: str
    return_pct: float
    max_drawdown_pct: float
    volume_ratio: float
    high: float
    low: float


@dataclass(frozen=True)
class CompletedPricePath:
    status: str = 'unavailable'
    schema: str = 'completed-price-path-v1'
    scope: str = 'completed_daily_history'
    price_mode: str = 'raw'
    source: str = ''
    as_of: str = ''
    windows: tuple[PricePathWindow, ...] = ()
    issues: tuple[str, ...] = ()
    notice: str = '已完成日线的历史事实，不是当前放量、止跌或买卖信号；历史高低点只作观察参考。'

    def to_dict(self) -> dict[str, Any]:
        """Expose one immutable contract to UI and the existing model payload."""
        return asdict(self)


WINDOWS = (5, 20)


def _days(completed: date, count: int) -> tuple[date, ...]:
    values: list[date] = []
    cursor = completed
    while len(values) < count:
        if not _calendar_known(cursor.year):
            return ()
        if _is_basic_trading_day(cursor):
            values.append(cursor)
        cursor -= timedelta(days=1)
    return tuple(reversed(values))


def build_completed_price_path(frame: pd.DataFrame | None, *, current: datetime) -> CompletedPricePath:
    """Summarize complete, same-source raw windows without filling missing bars.

    Missing/zero volume, suspension, duplicates and gaps stay distinct failures;
    a valid short window remains usable when the longer window is incomplete.
    """
    completed = _session(current, completed=True)
    if frame is None or frame.empty or completed is None:
        return CompletedPricePath(issues=('缺少已完成日不复权量价日线',))
    required = {'date', 'high', 'low', 'close', 'volume', 'source', 'fetched_at', 'timestamp_source'}
    if not required.issubset(frame.columns) or frame.attrs.get('price_mode') != 'raw':
        return CompletedPricePath(issues=('日线缺少成交量或不复权来源身份',))
    try:
        dates = tuple(date.fromisoformat(str(value)) for value in frame['date'])
        if any(day.isoformat() != str(value) or day > completed for day, value in zip(dates, frame['date'])):
            raise ValueError('日期无效或包含未完成日线')
        if len(set(dates)) != len(dates):
            raise ValueError('日线日期重复，不能计算量价窗口')
        data = frame.assign(_day=dates).sort_values('_day').reset_index(drop=True)
        if data['_day'].iloc[-1] != completed:
            raise ValueError('最近完成日线缺失或过期')
        windows, issues = [], []
        sources: set[str] = set()
        for window in WINDOWS:
            tail = data.tail(window)
            label = f'{window}根完成日线窗口'
            if len(tail) != window:
                issues.append(f'{label}样本不足')
                continue
            if tuple(tail['_day']) != _days(completed, window):
                issues.append(f'{label}存在缺失交易日，不补造连续性')
                continue
            source = set(tail['source'])
            if len(source) != 1 or any(not isinstance(value, str) or not value.strip()
                                       for key in ('source', 'timestamp_source') for value in tail[key]):
                issues.append(f'{label}来源缺失或混源')
                continue
            try:
                stamps = [datetime.fromisoformat(value) for value in tail['fetched_at']]
            except (ValueError, TypeError):
                issues.append(f'{label}抓取时点无效')
                continue
            if any(stamp.tzinfo is None or stamp.utcoffset() is None or stamp > current for stamp in stamps):
                issues.append(f'{label}抓取时点无效')
                continue
            if any(stamp < market_hours(current.replace(year=day.year, month=day.month, day=day.day))[1]
                   for stamp, day in zip(stamps, tail['_day'])):
                issues.append(f'{label}抓取早于该日收盘，不能认证为完成日线')
                continue
            values = tail[['high', 'low', 'close', 'volume']].apply(pd.to_numeric, errors='coerce')
            if any(isinstance(value, bool) for key in values for value in tail[key]) or not all(
                    math.isfinite(float(value)) for key in values for value in values[key]):
                issues.append(f'{label}量价数值无效')
                continue
            if (values['volume'] <= 0).any() or ('suspended' in tail and tail['suspended'].fillna(0).astype(bool).any()):
                issues.append(f'{label}存在停牌或无成交量，不能据此判断量能')
                continue
            if ((values['low'] <= 0) | (values['high'] < values['close'])
                    | (values['low'] > values['close'])).any():
                issues.append(f'{label}高低收价格关系无效')
                continue
            clean = values.assign(date=tail['date'])
            summary = summarize_price_path(clean, windows=(window,))[0]
            if not all(math.isfinite(value) for value in (summary.return_pct, summary.max_drawdown_pct, summary.volume_ratio)):
                issues.append(f'{label}统计结果无法有效复算')
                continue
            windows.append(PricePathWindow(window, str(tail['date'].iloc[0]), completed.isoformat(),
                                            summary.return_pct, summary.max_drawdown_pct,
                                            summary.volume_ratio, float(values['high'].max()),
                                            float(values['low'].min())))
            sources.update(source)
        if len(sources) > 1:
            raise ValueError('不同窗口来源不一致')
        return CompletedPricePath(status='available' if windows else 'unavailable',
                                  source=next(iter(sources), ''), as_of=completed.isoformat(),
                                  windows=tuple(windows), issues=tuple(issues))
    except (ValueError, TypeError, OverflowError, IndexError) as exc:
        return CompletedPricePath(issues=(str(exc),))


def completed_price_path_from_dict(value: object, *, current: datetime) -> CompletedPricePath:
    """Revalidate saved facts at read time; an old endpoint cannot renew them."""
    if isinstance(value, Mapping) and value.get('status') == 'unavailable':
        issues = value.get('issues')
        if isinstance(issues, (list, tuple)) and issues and len(issues) <= len(WINDOWS) and all(
                isinstance(issue, str) and 0 < len(issue) <= 180 for issue in issues):
            return CompletedPricePath(issues=tuple(issues))
    if not isinstance(value, Mapping) or value.get('status') != 'available':
        return CompletedPricePath(issues=('缺少有效的已完成日量价窗口',))
    completed = _session(current, completed=True)
    try:
        if (value.get('schema') != 'completed-price-path-v1' or value.get('scope') != 'completed_daily_history'
                or value.get('price_mode') != 'raw' or not isinstance(value.get('source'), str)
                or not value['source'].strip() or completed is None or value.get('as_of') != completed.isoformat()):
            raise ValueError('量价窗口来源、口径或完成日期无效')
        raw = value.get('windows')
        if not isinstance(raw, (list, tuple)) or not raw or len(raw) > len(WINDOWS):
            raise ValueError('量价窗口字段无效')
        rows = []
        for row in raw:
            if not isinstance(row, Mapping) or set(row) != set(PricePathWindow.__dataclass_fields__):
                raise ValueError('量价窗口字段不完整')
            window = row['window']
            days = _days(completed, window) if type(window) is int and window in WINDOWS else ()
            if not days or row['start_date'] != days[0].isoformat() or row['end_date'] != completed.isoformat():
                raise ValueError('量价窗口周期或日期无效')
            if any(type(row[key]) not in (int, float) or not math.isfinite(row[key])
                   for key in ('return_pct', 'max_drawdown_pct', 'volume_ratio', 'high', 'low')):
                raise ValueError('量价窗口数值无效')
            if (row['return_pct'] <= -100 or not -100 < row['max_drawdown_pct'] <= 0
                    or row['volume_ratio'] <= 0 or not 0 < row['low'] <= row['high']):
                raise ValueError('量价窗口数值越界')
            rows.append(PricePathWindow(**row))
        if len({row.window for row in rows}) != len(rows):
            raise ValueError('量价窗口周期重复')
        issues = value.get('issues', ())
        if not isinstance(issues, (tuple, list)) or len(issues) > len(WINDOWS) or any(
                not isinstance(issue, str) or not 0 < len(issue) <= 180 for issue in issues):
            raise ValueError('量价窗口缺失说明无效')
        return CompletedPricePath(status='available', source=value['source'], as_of=value['as_of'],
                                  windows=tuple(rows), issues=tuple(issues))
    except (ValueError, TypeError, KeyError, OverflowError):
        return CompletedPricePath(issues=('量价窗口重新校验未通过，不能作为当前研究事实',))


def price_path_explanations(value: CompletedPricePath) -> tuple[str, ...]:
    """Use explicit periods and units, keeping historical facts separate from ticks."""
    return tuple(f'{row.window}根完成日线涨跌幅 {row.return_pct:+.2f}%；'
                 f'{row.window}根完成日线最大回撤 {row.max_drawdown_pct:.2f}%；'
                 f'{row.window}根完成日线末日量比 {row.volume_ratio:.2f}倍'
                 f'（末日成交量/窗口此前日均量，截至{row.end_date}，非实时量比）'
                 for row in value.windows)


def validate_price_path_claims(text: str, value: CompletedPricePath, *, action: bool = False) -> None:
    """Validate window/metric/unit together; statistics never become thresholds.

    This supplements the existing action-price guard. It grants no numbers to
    its generic price namespace and does not certify other model prose.
    """
    window_pattern = (r'(?P<window>\d+)\s*(?:根(?:已)?完成日线|根日线|'
                      r'(?:个)?(?:已完成)?交易日|日线|日)(?:窗口)?')
    metric_pattern = (r'(?P<label>涨跌幅|收益率|收益|最大回撤|回撤|末日量比|成交量比|量比|'
                      r'volume_ratio|return_pct|max_drawdown_pct)\s*'
                      r'(?:为|是|约|[:：=<>≥≤])?\s*(?P<value>[-+]?\d+(?:\.\d+)?)\s*'
                      r'(?P<unit>[%％]|倍|[xX]|元|点|分)?')
    rows = {row.window: row for row in value.windows} if value.status == 'available' else {}
    fields = {label: ('return_pct', {'%', '％'}) for label in ('涨跌幅', '收益率', '收益', 'return_pct')}
    fields.update({label: ('max_drawdown_pct', {'%', '％'}) for label in ('最大回撤', '回撤', 'max_drawdown_pct')})
    fields.update({label: ('volume_ratio', {'倍', 'x'}) for label in ('末日量比', '成交量比', '量比', 'volume_ratio')})
    # A window may qualify several metrics in one ordinary sentence. A bare
    # current quote percentage retains its existing namespace; a windowed
    # percentage or any volume/drawdown claim must match this typed contract.
    for clause in re.split(r'[。；;\n]', text):
        windows = list(re.finditer(window_pattern, clause))
        for match in re.finditer(metric_pattern, clause, flags=re.I):
            label = match['label'].lower()
            previous = [item for item in windows if item.start() < match.start()]
            if not previous and label in {'涨跌幅', '收益率', '收益'}:
                continue
            if action:
                raise ValueError('完成日量价统计仅用于解释，不能设成新的买卖门槛')
            if not previous:
                raise ValueError('完成日量价统计须保留原窗口、指标、数值和单位')
            row = rows.get(int(previous[-1]['window']))
            field, units = fields[label]
            prose = re.sub(r'(?:非|并非|不是|不代表)(?:当前|实时|盘中)', '', clause)
            if re.search(r'当前|实时|盘中|(?:板块|行业|大盘|指数)', prose):
                raise ValueError('完成日量价统计不能冒充实时或其他主体的事实')
            decimals = len(match['value'].split('.', 1)[1]) if '.' in match['value'] else 0
            if (row is None or (match['unit'] or '').lower() not in units or float(match['value']) not in
                    {getattr(row, field), round(getattr(row, field), decimals)}):
                raise ValueError('完成日量价统计的周期、数值或单位与有源窗口不符')


def mask_action_reference_identity(text: str, value: CompletedPricePath,
                                   reference: Mapping[str, Any] | None) -> str:
    """Consume a sourced window's period identity, never a trading number.

    One action has one price_ref. Qualified day/window/high/low phrases must
    describe that reference; unknown prose can instead say 所引用观察价. This
    intentionally rejects mixed references rather than inferring which level a
    free-text clause means. Only exact numeric window identity is masked.
    """
    window = (r'(?<![\d.])(?:(?:最近完成日截至)|(?:近|最近|过去))?'
              r'(?P<window>\d+)\s*(?:根(?:已)?完成日线|根日线|(?:个)?交易日|日线|日)'
              r'(?:的)?(?:窗口)?(?:的)?(?P<kind>高点|低点)')
    current = r'(?:本交易日|本日|当日|今日|今天|盘中)(?:的)?(?:已见)?(?P<kind>高点|低点)'
    completed = r'(?:最近|最新)(?:一个|一)?(?:已完成|完成)(?:交易日|日)(?:的)?(?P<kind>高点|低点)'
    previous = r'(?:行情源)?前收盘价'
    label = reference.get('label', '') if isinstance(reference, Mapping) else ''
    selected: tuple[str, int | None, str] | None = None
    row = re.fullmatch(window, label) if isinstance(label, str) else None
    if row:
        identity = int(row['window'])
        source_window = next((item for item in value.windows if item.window == identity), None)
        price = reference.get('price')
        if (value.status != 'available' or source_window is None or reference.get('source') != value.source
                or reference.get('as_of') != value.as_of or type(price) not in (int, float)
                or price != (source_window.high if row['kind'] == '高点' else source_window.low)):
            raise ValueError('动作窗口参考价与本次已核验量价窗口不一致')
        selected = ('window', identity, row['kind'])
    else:
        for category, pattern in (('current', current), ('completed', completed), ('previous', previous)):
            row = re.fullmatch(pattern, label) if isinstance(label, str) else None
            if row:
                selected = (category, None, row['kind'] if category != 'previous' else '')
                break
    result = text
    for category, pattern in (('window', window), ('current', current), ('completed', completed), ('previous', previous)):
        def checked(match: re.Match[str]) -> str:
            identity = int(match['window']) if category == 'window' else None
            kind = match['kind'] if category != 'previous' else ''
            if selected != (category, identity, kind):
                raise ValueError('动作文字的日别、窗口或高低点与所引用观察价不一致')
            if category == 'window':
                prefix = result[max(0, match.start() - 4):match.start()]
                if re.search(r'(?:当前|实时|盘中)$', prefix):
                    raise ValueError('完成日窗口参考价不能冒充当前日内价位')
            return '所引用观察价'
        result = re.sub(pattern, checked, result)
    # A second bare high/low in a mixed phrase (高点与低点) must not escape the
    # qualified-reference check. Qualitative text with no selected reference
    # retains the pre-existing behavior and cannot consume a period number.
    if selected is not None and any(kind != selected[2] for kind in re.findall(r'高点|低点', result)):
        raise ValueError('动作文字的高低点与所引用观察价不一致')
    return result
