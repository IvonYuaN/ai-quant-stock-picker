"""Synthetic completed raw windows; no quotes or model network requests."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pandas as pd
import pytest

from aqsp.core.time import _is_basic_trading_day
from position_price_path import (
    build_completed_price_path,
    completed_price_path_from_dict,
    price_path_explanations,
)
from strategy_review_context import validate_position_action_numbers
from test_position_action_plan import NOW
from test_position_discussion import OPINION


def frame(count: int = 20) -> pd.DataFrame:
    days, day = [], NOW.date() - timedelta(days=1)
    while len(days) < count:
        if _is_basic_trading_day(day):
            days.append(day.isoformat())
        day -= timedelta(days=1)
    days.reverse()
    result = pd.DataFrame({'date': days, 'high': [11 + index / 10 for index in range(count)],
                           'low': [9 + index / 10 for index in range(count)],
                           'close': [10 + index / 10 for index in range(count)],
                           'volume': [100 + index * 10 for index in range(count)],
                           'source': ['synthetic'] * count, 'fetched_at': [NOW.isoformat()] * count,
                           'timestamp_source': ['received_at'] * count, 'suspended': [0] * count})
    result.attrs['price_mode'] = 'raw'
    return result


def test_completed_price_path_preserves_period_source_and_recomputed_facts_when_window_valid() -> None:
    raw = frame()
    before = raw.copy(deep=True)
    result = build_completed_price_path(raw, current=NOW)
    assert result.status == 'available' and result.source == 'synthetic'
    assert [row.window for row in result.windows] == [5, 20]
    latest = result.windows[0]
    assert latest.return_pct == pytest.approx((11.9 / 11.5 - 1) * 100)
    assert latest.volume_ratio == pytest.approx(290 / 265)
    assert latest.max_drawdown_pct == 0 and latest.high == 12.9 and latest.low == 10.5
    assert completed_price_path_from_dict(result.to_dict(), current=NOW).windows == result.windows
    assert '非实时量比' in price_path_explanations(result)[0]
    pd.testing.assert_frame_equal(raw, before)


@pytest.mark.parametrize('kind,reason', [('duplicate', '重复'), ('stale', '过期'), ('future', '未完成'),
                                       ('volume', '成交量'), ('suspension', '停牌'), ('invalid_price', '价格关系'),
                                       ('source', '混源'), ('fetch', '抓取时点')])
def test_completed_price_path_explains_unusable_window_when_raw_history_incomplete(kind: str, reason: str) -> None:
    raw = frame(5)
    if kind == 'duplicate':
        raw.loc[0, 'date'] = raw.loc[1, 'date']
    elif kind == 'stale':
        raw = raw.iloc[:-1]
    elif kind == 'future':
        raw.loc[4, 'date'] = NOW.date().isoformat()
    elif kind == 'volume':
        raw.loc[3, 'volume'] = 0
    elif kind == 'suspension':
        raw.loc[3, 'suspended'] = 1
    elif kind == 'invalid_price':
        raw.loc[3, 'high'] = 1
    elif kind == 'source':
        raw.loc[3, 'source'] = 'other'
    elif kind == 'fetch':
        raw.loc[3, 'fetched_at'] = (NOW + timedelta(hours=1)).isoformat()
    result = build_completed_price_path(raw, current=NOW)
    assert result.status == 'unavailable' and not result.windows
    assert reason in '；'.join(result.issues)


def test_completed_price_path_keeps_short_window_when_older_history_has_gap() -> None:
    raw = frame(20).drop(index=0)
    result = build_completed_price_path(raw, current=NOW)
    assert [row.window for row in result.windows] == [5]
    assert '样本不足' in result.issues[0]
    raw = frame(21).drop(index=10)
    result = build_completed_price_path(raw, current=NOW)
    assert [row.window for row in result.windows] == [5]
    assert '缺失交易日' in result.issues[0]


def test_completed_price_path_preserves_valid_short_window_when_older_source_metadata_is_missing() -> None:
    raw = frame(20)
    raw.loc[0, 'fetched_at'] = None
    result = build_completed_price_path(raw, current=NOW)
    assert result.status == 'available' and [row.window for row in result.windows] == [5]
    assert '抓取时点无效' in result.issues[0]
    assert completed_price_path_from_dict(result.to_dict(), current=NOW).issues == result.issues


@pytest.mark.parametrize('patch', [{'source': ''}, {'price_mode': 'qfq'}, {'as_of': '2026-10-21'},
                                  {'windows': []}, {'schema': 'other'}])
def test_completed_price_path_rejects_saved_facts_when_source_contract_invalid(patch: dict) -> None:
    result = build_completed_price_path(frame(), current=NOW).to_dict()
    assert completed_price_path_from_dict({**result, **patch}, current=NOW).status == 'unavailable'


@pytest.mark.parametrize('patch', [{'window': 10}, {'volume_ratio': float('nan')}, {'max_drawdown_pct': 5},
                                  {'start_date': '2026-10-01'}, {'low': 15}, {'volume_ratio': True}])
def test_completed_price_path_rejects_window_when_period_or_values_invalid(patch: dict) -> None:
    result = build_completed_price_path(frame(), current=NOW).to_dict()
    result['windows'] = [{**result['windows'][0], **patch}]
    assert completed_price_path_from_dict(result, current=NOW).status == 'unavailable'


def path_evidence() -> dict:
    return {'stock': {'price_path': build_completed_price_path(frame(), current=NOW).to_dict()}}


def test_completed_price_path_allows_exact_numeric_explanation_when_unit_and_window_match(monkeypatch) -> None:
    monkeypatch.setattr('aqsp.core.time.now_shanghai', lambda: NOW)
    result = build_completed_price_path(frame(), current=NOW)
    row = deepcopy(OPINION)
    row['summary'] = '；'.join(price_path_explanations(result))
    row['actions'] = []
    validate_position_action_numbers(row, path_evidence(), {})


@pytest.mark.parametrize('text', ['5根完成日线末日量比 1.09%', '10根完成日线末日量比 1.09倍',
                                  '5根完成日线末日量比 2.00倍', '5根完成日线最大回撤 0.00倍',
                                  '5根完成日线涨跌幅 3.48', '5根完成日线涨跌幅 9.20元',
                                  '当前量比1.09倍', 'volume_ratio=1.09', '20日涨跌幅999%',
                                  '最大回撤99%', '近20日收益999%', '当前5日量比1.09倍',
                                  '5日实时量比1.09倍', '板块5日量比1.09倍'])
def test_completed_price_path_rejects_explanation_when_statistics_change_unit_or_window(monkeypatch, text: str) -> None:
    monkeypatch.setattr('aqsp.core.time.now_shanghai', lambda: NOW)
    row = deepcopy(OPINION)
    row['summary'], row['actions'] = text, []
    with pytest.raises(ValueError, match='完成日量价统计'):
        validate_position_action_numbers(row, path_evidence(), {})


def test_completed_price_path_cannot_supply_new_action_threshold_when_price_has_same_number(monkeypatch) -> None:
    monkeypatch.setattr('aqsp.core.time.now_shanghai', lambda: NOW)
    row = deepcopy(OPINION)
    row['actions'][0]['condition'] = '5根完成日线末日量比 1.09倍后买回'
    with pytest.raises(ValueError, match='买卖门槛'):
        validate_position_action_numbers(row, path_evidence(), {'same': {'price': 1.09, 'source': 'synthetic', 'as_of': NOW.isoformat()}})


def test_completed_price_path_does_not_renew_explanation_when_latest_completed_day_changes(monkeypatch) -> None:
    monkeypatch.setattr('aqsp.core.time.now_shanghai', lambda: NOW + timedelta(days=1))
    row = deepcopy(OPINION)
    row['summary'] = '5根完成日线末日量比 1.09倍'
    row['actions'] = []
    with pytest.raises(ValueError, match='有源窗口'):
        validate_position_action_numbers(row, path_evidence(), {})


def test_completed_price_path_rejects_full_day_bar_when_fetched_before_that_session_closed() -> None:
    raw = frame(5)
    raw.loc[4, 'fetched_at'] = '2026-10-22T14:00:00+08:00'
    result = build_completed_price_path(raw, current=NOW)
    assert result.status == 'unavailable' and '早于该日收盘' in result.issues[0]


@pytest.mark.parametrize('text', ['5日历史涨跌幅3.48%', '近5个交易日涨跌幅约3.5%，最大回撤0%，末日量比1.09倍',
                                  '过去5根日线的收益率是3.48%，量比1.09x，非实时量比'])
def test_completed_price_path_accepts_natural_period_aliases_when_values_and_units_match(monkeypatch, text: str) -> None:
    monkeypatch.setattr('aqsp.core.time.now_shanghai', lambda: NOW)
    row = deepcopy(OPINION)
    row['summary'], row['actions'] = text, []
    validate_position_action_numbers(row, path_evidence(), {})
