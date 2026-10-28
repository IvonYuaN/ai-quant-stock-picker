"""Industry analysis and homepage use one complete source and unit contract."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import astock
from industry_source_contract import IndustrySourceLimits, parse_industry_rows
import position_context_source as source
from test_position_context_source import NOW, STAMP


def industry(name: str, value: object, **patch) -> dict:
    return {'f14': name, 'f3': value, 'f124': STAMP, **patch}


def payload(rows: list[dict]) -> dict:
    return {'data': {'total': len(rows), 'diff': rows}}


@pytest.mark.parametrize('raw,expected', [(891, 8.91), (-35, -.35), (0, 0),
    (True, None), ('120', None), ('-', None), (float('nan'), None),
    (float('inf'), None), (10 ** 400, None)])
def test_industry_contract_keeps_missing_separate_from_zero_when_percentage_invalid(raw, expected) -> None:
    value = parse_industry_rows(payload([industry('银行Ⅱ', raw)]), max_rows=800)[0]
    assert value['f3'] == expected and value['f124'] == STAMP


@pytest.mark.parametrize('rows', [
    [industry('银行', 1), industry('银行', -1)],
    [industry('银行', 1, f12='BK1'), industry('银行Ⅱ', -1, f12='BK1')],
    [industry('银行', 1), None],
])
def test_industry_contract_rejects_ambiguous_or_broken_rows_when_complete_source_claimed(rows) -> None:
    with pytest.raises(ValueError):
        parse_industry_rows(payload(rows), max_rows=800)


def test_industry_analysis_retains_exact_hierarchy_membership_when_same_figures() -> None:
    rows = parse_industry_rows(payload([industry('银行', -35), industry('银行Ⅱ', -35)]), max_rows=800)
    result = source._combine_rows(('600000',), [{'f12': '600000', 'f100': '银行Ⅱ', 'f124': STAMP}], rows)
    assert result['600000']['industry'] == '银行Ⅱ'
    assert result['600000']['change_pct'] == -.35
    assert result['600000']['as_of'] == NOW.isoformat()
    assert result['600000']['membership_as_of'] == NOW.isoformat()
    with pytest.raises(ValueError, match='归属重复'):
        source._combine_rows(('600000',), [{'f12': '600000', 'f100': name} for name in ('银行', '银行Ⅱ')], rows)


def test_industry_homepage_sorts_complete_result_when_source_is_unsorted(monkeypatch) -> None:
    class Response:
        content = json.dumps(payload([industry('中间行业', 0), industry('弱行业', -35),
            industry('缺数据行业', '-'), industry('强行业', 891)])).encode()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(astock, 'em_get', lambda *args, **kwargs: Response())
    result = astock._industry_comparison_once(1)
    assert result['total'] == 3
    assert result['top'][0]['name'] == '强行业' and result['top'][0]['change_pct'] == 8.91
    assert result['bottom'][0]['name'] == '弱行业' and result['bottom'][0]['change_pct'] == -.35


@pytest.mark.parametrize('body', [b'not json', json.dumps(payload([industry('银行', 120)])).encode() * 20000])
def test_industry_homepage_stays_unavailable_when_source_invalid_or_oversized(monkeypatch, body) -> None:
    class Response:
        content = body

        def raise_for_status(self):
            pass

    monkeypatch.setattr(astock, 'em_get', lambda *args, **kwargs: Response())
    assert astock._industry_comparison_once(5) == {'top': [], 'bottom': [], 'total': 0}


def test_industry_async_fetch_retains_unverified_source_clock_when_missing() -> None:
    def handle(request):
        rows = [{'f12': '600000', 'f100': '银行Ⅱ', 'f124': STAMP}] if '/ulist.' in request.url.path else [industry('银行Ⅱ', -35, f124=None)]
        return httpx.Response(200, json=payload(rows))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await source.fetch_position_sectors(['600000'], client,
                max_response_bytes=IndustrySourceLimits().max_response_bytes)

    value = asyncio.run(run())
    assert value['600000']['as_of'] is None
    assert value['600000']['change_pct'] == -.35
