"""生产网络环境下 kline / individual_info / disclosure 的「降级备源」回归测（全离线）。

背景（2026-09-26 prod 取证）：
- ``kline`` 主源 mootdx TDX 在 prod 网络取不到数（bars 恒空）→ 降级东财 ``stock_zh_a_hist``；
- ``individual_info`` 主源东财 push2 ``stock_individual_info_em`` 被按出口 IP 反爬拒 → 降级巨潮 ``stock_profile_cninfo``；
- ``disclosure`` 上游 akshare cninfo 源自身解析 bug（str 当 dict 索引）→ 降级空列表（不抛 502）。

三条备源都在 prod 实测可达。这里用合成 DataFrame + monkeypatch 验证：
  * 主源正常时不走备源；
  * 主源失败/空时正确走备源且字段归一化；
  * 依赖缺失（DependencyMissing）仍向上传（上层据此 501，不被备源吞掉）。
"""

import pandas as pd

import astock


class _FakeAk:
    """可控的 akshare 替身：各方法由测试按名注入。"""

    def __init__(self, **methods) -> None:
        for name, fn in methods.items():
            setattr(self, name, fn)

    def __getattr__(self, name):
        raise AttributeError(name)


def _make_akshare(ak) -> None:
    """把 _FakeAk 挂到 astock._akshare 上（保持无参返回 ak 的签名）。"""

    def _fake():
        return ak

    astock._akshare = _fake


# ── individual_info：主源被拒 → 降级巨潮 profile_cninfo ──────────────


def test_individual_info_falls_back_to_cninfo_when_main_refused(monkeypatch):
    calls = {"main": 0, "cninfo": 0}

    def main_refused(symbol):
        calls["main"] += 1
        raise ConnectionError("RemoteDisconnected: prod egress IP refused by WAF")

    cninfo_df = pd.DataFrame(
        [
            {
                "公司名称": "贵州茅台酒股份有限公司",
                "A股代码": "600519",
                "A股简称": "贵州茅台",
            }
        ]
    )

    def cninfo_profile(symbol):
        calls["cninfo"] += 1
        return cninfo_df

    _make_akshare(
        _FakeAk(
            stock_individual_info_em=main_refused, stock_profile_cninfo=cninfo_profile
        )
    )
    result = astock.individual_info("600519")
    assert calls["main"] == 1 and calls["cninfo"] == 1
    assert result["公司名称"] == "贵州茅台酒股份有限公司"
    assert result["A股代码"] == "600519"


def test_individual_info_uses_main_source_when_available(monkeypatch):
    calls = {"main": 0, "cninfo": 0}

    def main_ok(symbol):
        calls["main"] += 1
        return pd.DataFrame(
            [{"item": "行业", "value": "白酒"}, {"item": "总股本", "value": 1256000}]
        )

    def cninfo_profile(symbol):
        calls["cninfo"] += 1
        return pd.DataFrame([{"公司名称": "贵州茅台酒股份有限公司"}])

    _make_akshare(
        _FakeAk(stock_individual_info_em=main_ok, stock_profile_cninfo=cninfo_profile)
    )
    result = astock.individual_info("600519")
    assert calls["main"] == 1 and calls["cninfo"] == 0  # 主源命中 → 不走备源
    assert result["行业"] == "白酒"
    assert result["总股本"] == 1256000


def test_individual_info_raises_dependency_missing(monkeypatch):
    def boom():
        raise astock.DependencyMissing("akshare 未安装")

    astock._akshare = boom
    try:
        astock.individual_info("600519")
        raise AssertionError("应抛 DependencyMissing")
    except astock.DependencyMissing:
        pass


# ── disclosure：上游解析 bug → 降级空列表（不 502）─────────────────────


def test_disclosure_degrades_to_empty_list_on_upstream_bug(monkeypatch):
    def buggy_cninfo(symbol, market):
        # 复刻上游 stock_disclosure_cninfo.py:172 的 TypeError
        raise TypeError("string indices must be integers, not 'str'")

    _make_akshare(_FakeAk(stock_zh_a_disclosure_report_cninfo=buggy_cninfo))
    assert astock.disclosure("600519") == []


def test_disclosure_returns_records_when_healthy(monkeypatch):
    ok_df = pd.DataFrame(
        [
            {"公告标题": "2025年半年度报告", "公告时间": "2025-08-20"},
            {"公告标题": "利润分配预案", "公告时间": "2025-05-12"},
        ]
    )

    _make_akshare(
        _FakeAk(stock_zh_a_disclosure_report_cninfo=lambda symbol, market: ok_df)
    )
    rows = astock.disclosure("600519")
    assert len(rows) == 2
    assert rows[0]["公告标题"] == "2025年半年度报告"


def test_disclosure_raises_dependency_missing(monkeypatch):
    def boom():
        raise astock.DependencyMissing("akshare 未安装")

    astock._akshare = boom
    try:
        astock.disclosure("600519")
        raise AssertionError("应抛 DependencyMissing")
    except astock.DependencyMissing:
        pass


# ── kline：mootdx TDX 取不到数 → 降级腾讯 K 线（键名归一化，离线 mock urllib）────


class _FakeMootdxClient:
    def __init__(self, bars_result) -> None:
        self._bars_result = bars_result

    def bars(self, symbol, category=4, offset=60):
        return self._bars_result


def _make_mootdx(monkeypatch, client) -> None:
    monkeypatch.setattr(astock, "_mootdx_client", lambda: client)


class _FakeResp:
    """模拟 urllib urlopen 上下文管理器的响应体。"""

    def __init__(self, payload: dict) -> None:
        import json as _json

        self._body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _make_urlopen(monkeypatch, payload: dict, code: str = "600519") -> None:
    """把 astock.urllib.request.urlopen 替身为返回固定 payload 的 fake。"""

    def _fake_open(req, *a, **kw):
        return _FakeResp(payload)

    monkeypatch.setattr(astock.urllib.request, "urlopen", _fake_open)


def _tencent_payload(rows: list) -> dict:
    """构造 web.ifzq.gtimg.cn fqkline 返回结构（单根 K 线 = 6 元组 [date,o,c,h,l,v]）。"""
    code_key = "600519"
    return {"data": {f"sh{code_key}": {"qfqday": rows, "qt": {}, "version": "1"}}}


def test_kline_falls_back_to_tencent_when_mootdx_empty(monkeypatch):
    """mootdx 空 → 腾讯备源命中，键名归一化到 KlineRow，字段顺序正确（close 在 high/low 前）。"""
    _make_mootdx(monkeypatch, _FakeMootdxClient(bars_result=pd.DataFrame()))
    rows = [
        ["2025-09-22", "1465.090", "1453.350", "1470.110", "1448.000", "34947.000"],
        ["2025-09-23", "1450.500", "1447.420", "1460.000", "1442.010", "29800.000"],
    ]
    _make_urlopen(monkeypatch, _tencent_payload(rows))
    out = astock.kline("600519", category=4, offset=60)
    assert len(out) == 2
    r0 = out[0]
    assert r0["date"] == "2025-09-22"
    assert r0["open"] == 1465.09
    assert r0["close"] == 1453.35
    assert r0["high"] == 1470.11
    assert r0["low"] == 1448.00
    assert r0["vol"] == 34947.0
    assert r0["amount"] == 0.0  # 腾讯 K 线不提供成交额，归一化为 0


def test_kline_fallback_handles_missing_amount_position(monkeypatch):
    """腾讯 6 元组只有 6 位时 amount 安全归零，不 IndexError。"""
    _make_mootdx(monkeypatch, _FakeMootdxClient(bars_result=pd.DataFrame()))
    rows = [["2025-09-22", "1.0", "2.0", "3.0", "0.5", "100"]]
    _make_urlopen(monkeypatch, _tencent_payload(rows))
    out = astock.kline("600519", category=4, offset=60)
    assert out[0]["amount"] == 0.0


def test_kline_fallback_truncates_to_offset(monkeypatch):
    """offset 截断：接口返回全量，kline() 只取最后 offset 根。"""
    _make_mootdx(monkeypatch, _FakeMootdxClient(bars_result=pd.DataFrame()))
    rows = [[f"2025-09-{i:02d}", "1", "2", "3", "0.5", "10"] for i in range(1, 21)]
    _make_urlopen(monkeypatch, _tencent_payload(rows))
    out = astock.kline("600519", category=4, offset=5)
    assert len(out) == 5
    assert out[-1]["date"] == "2025-09-20"


def test_kline_fallback_returns_empty_on_network_error(monkeypatch):
    """网络错误（urlopen 抛异常）→ 备源安全返回空列表，不向上传 502。"""
    _make_mootdx(monkeypatch, _FakeMootdxClient(bars_result=pd.DataFrame()))

    import urllib.request

    def _raise(req, *a, **kw):
        raise urllib.error.URLError("prod egress refused")

    monkeypatch.setattr(astock.urllib.request, "urlopen", _raise)
    assert astock.kline("600519", category=4, offset=60) == []


def test_kline_fallback_handles_malformed_row(monkeypatch):
    """单根 K 线行数不足 6 位或类型错 → 跳过该行不崩。"""
    _make_mootdx(monkeypatch, _FakeMootdxClient(bars_result=pd.DataFrame()))
    rows = [
        ["2025-09-22", "1.0", "2.0"],  # 短行，跳过
        ["2025-09-23", "1.0", "2.0", "3.0", "0.5", "100"],  # 正常
    ]
    _make_urlopen(monkeypatch, _tencent_payload(rows))
    out = astock.kline("600519", category=4, offset=60)
    assert len(out) == 1
    assert out[0]["date"] == "2025-09-23"


def test_kline_prefers_mootdx_when_nonempty(monkeypatch):
    """mootdx 有数 → 不走备源（备源函数体不该被执行到，mock urlopen 若被调就 AssertionError）。"""
    mootdx_df = pd.DataFrame(
        [
            {
                "datetime": "2025-09-22 15:00:00",
                "open": 1.1,
                "close": 1.2,
                "high": 1.3,
                "low": 1.0,
                "vol": 100,
                "amount": 1000,
            },
        ]
    )
    _make_mootdx(monkeypatch, _FakeMootdxClient(bars_result=mootdx_df))

    def urlopen_should_not_run(req, *a, **kw):
        raise AssertionError("主源有数时不应调用腾讯备源")

    monkeypatch.setattr(astock.urllib.request, "urlopen", urlopen_should_not_run)
    rows = astock.kline("600519", category=4, offset=60)
    assert rows[0]["close"] == 1.2  # 来自 mootdx 主源


def test_kline_minute_category_has_no_fallback(monkeypatch):
    """60 分钟线（category=11）腾讯日/周/月备源不覆盖 → mootdx 空时返回空。"""
    _make_mootdx(monkeypatch, _FakeMootdxClient(bars_result=pd.DataFrame()))
    _make_urlopen(monkeypatch, _tencent_payload([]))
    assert astock.kline("600519", category=11, offset=60) == []


def test_kline_fallback_survives_mootdx_factory_failure(monkeypatch):
    """mootdx 主源 bars 抛任意异常（如重启后首选调服 ValueError）→ 仍走备源，不 502。"""
    _fake_mootdx = _FakeMootdxClient(bars_result=pd.DataFrame())

    def _boom_bars(symbol, category=4, offset=60):
        raise ValueError("Quotes.factory server-selection exploded")

    _fake_mootdx.bars = _boom_bars
    _make_mootdx(monkeypatch, _fake_mootdx)

    rows = [
        ["2025-09-22", "1.0", "2.0", "3.0", "0.5", "100"],
    ]
    _make_urlopen(monkeypatch, _tencent_payload(rows))
    out = astock.kline("600519", category=4, offset=60)
    assert out and out[0]["date"] == "2025-09-22"


def test_kline_raises_dependency_missing_when_mootdx_absent(monkeypatch):
    def boom():
        raise astock.DependencyMissing("mootdx 未安装：pip install mootdx")

    monkeypatch.setattr(astock, "_mootdx_client", boom)
    try:
        astock.kline("600519")
        raise AssertionError(
            "DependencyMissing 应继续向上抛（上层据此 501），不被备源吞"
        )
    except astock.DependencyMissing:
        pass
