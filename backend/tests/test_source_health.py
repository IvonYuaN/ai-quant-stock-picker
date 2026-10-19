"""数据源健康台账：把「源挂了」和「今天确实没数据」分开。

## 为什么要这层东西

源被封时各接口**静默返回空载荷**：`/api/industry` 给
`{"top":[],"bottom":[],"total":0}`、`/api/global/indices` 给 `{"data":[]}`。
页面显示「未返回」——但这句陈述同时覆盖两种完全不同的现实：

- 源不可达（运维该动手）；
- 今天确实没有数据（正常）。

2026-10-19 的现场就是第一种：prod 出口被东财按主机封禁（`push2`/`push2delay`/
`push2his` 全断、`push2ex` 正常），当时只能靠一条条手打探测才定位到范围。

## 本文件守什么

1. 台账语义：连续失败计数、「故障起点」只在首次失败记、恢复时清零；
2. 排序：**不健康的排前面**（运维第一眼要看到坏的那个）；
3. 清洗：错误摘要里的查询串必须打码（`push2ex` 的 `ut` 属凭据类参数）；
4. 接线：`em_get` 按主机名记账，且**4xx/5xx 也算失败**；
5. 边界：记账自己炸了，取数照常。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import astock
import market
import source_health


@pytest.fixture(autouse=True)
def _clean_ledger():
    """台账是模块级全局状态，每个用例前后清空，避免互相污染。"""
    source_health.reset()
    yield
    source_health.reset()


class _Clock:
    """可推进的假时钟。用来验证「故障起点不被后续失败覆盖」这类与时间有关的语义。"""

    def __init__(self, start: float = 1_000_000.0):
        self.t = start

    def time(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock()
    # 只替换 source_health 内部看到的 time 对象，不动全局 time 模块
    monkeypatch.setattr(source_health, "time", SimpleNamespace(time=c.time))
    return c


# --------------------------------------------------------------------------
# 台账语义
# --------------------------------------------------------------------------


def test_first_error_marks_down_since(clock):
    source_health.record_error("push2.eastmoney.com", "ConnectionError: aborted")
    row = source_health.snapshot()[0]
    assert row["source"] == "push2.eastmoney.com"
    assert row["healthy"] is False
    assert row["consecutive_errors"] == 1
    assert row["error_count"] == 1
    assert row["ok_count"] == 0
    assert row["down_since"] == source_health._iso(clock.t)


def test_down_since_is_not_overwritten_by_later_failures(clock):
    """「故障起点」若被每次失败覆盖，就永远算不出这个源坏了多久。"""
    source_health.record_error("push2.eastmoney.com", "x")
    first = source_health.snapshot()[0]["down_since"]
    clock.advance(600)
    source_health.record_error("push2.eastmoney.com", "y")
    row = source_health.snapshot()[0]
    assert row["down_since"] == first
    assert row["consecutive_errors"] == 2
    assert row["last_error_at"] == source_health._iso(clock.t)


def test_success_clears_consecutive_and_down_since_but_keeps_history(clock):
    source_health.record_error("push2.eastmoney.com", "x")
    source_health.record_error("push2.eastmoney.com", "y")
    clock.advance(60)
    source_health.record_ok("push2.eastmoney.com")
    row = source_health.snapshot()[0]
    assert row["healthy"] is True
    assert row["consecutive_errors"] == 0
    assert row["down_since"] is None
    # 累计失败数保留：恢复之后仍要看得出这个源今天坏过
    assert row["error_count"] == 2
    assert row["ok_count"] == 1


def test_empty_source_is_ignored():
    source_health.record_ok("")
    source_health.record_error("", "x")
    assert source_health.snapshot() == []


# --------------------------------------------------------------------------
# 排序与边界
# --------------------------------------------------------------------------


def test_snapshot_puts_unhealthy_sources_first():
    source_health.record_ok("push2ex.eastmoney.com")
    source_health.record_ok("qt.gtimg.cn")
    source_health.record_error("push2his.eastmoney.com", "ConnectionError")
    rows = source_health.snapshot()
    assert rows[0]["source"] == "push2his.eastmoney.com"
    assert rows[0]["healthy"] is False
    assert [r["healthy"] for r in rows[1:]] == [True, True]


def test_snapshot_orders_unhealthy_by_consecutive_errors_then_name():
    for _ in range(3):
        source_health.record_error("b.example", "x")
    source_health.record_error("a.example", "x")
    assert [r["source"] for r in source_health.snapshot()] == ["b.example", "a.example"]


def test_snapshot_returns_a_copy():
    source_health.record_ok("h.example")
    rows = source_health.snapshot()
    rows[0]["healthy"] = False
    rows[0]["ok_count"] = 999
    again = source_health.snapshot()
    assert again[0]["healthy"] is True
    assert again[0]["ok_count"] == 1


def test_ledger_is_bounded():
    """键空间本该有限；上限只是防御，满了之后新键进不来、老键照常累加。"""
    for i in range(source_health._MAX_SOURCES + 5):
        source_health.record_ok(f"h{i}.example")
    assert len(source_health.snapshot()) == source_health._MAX_SOURCES
    source_health.record_ok("h0.example")
    by_name = {r["source"]: r for r in source_health.snapshot()}
    assert by_name["h0.example"]["ok_count"] == 2
    assert f"h{source_health._MAX_SOURCES}.example" not in by_name


# --------------------------------------------------------------------------
# 错误摘要清洗
# --------------------------------------------------------------------------


def test_clean_error_redacts_query_string():
    """`push2ex` 的 `ut` 是凭据类参数，不能进台账。"""
    msg = (
        "HTTPError: 502 Server Error for url: "
        "https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
    )
    out = source_health.clean_error(msg)
    assert "7eea3edcaed734bea9cbfc24409ed989" not in out
    assert "?<redacted>" in out


def test_clean_error_keeps_only_first_line_and_truncates():
    assert source_health.clean_error("A\nB\nC") == "A"
    assert len(source_health.clean_error("x" * 500)) == source_health._MAX_ERROR_LEN


def test_clean_error_prefixes_exception_type():
    out = source_health.clean_error(ConnectionResetError("reset by peer"))
    assert out.startswith("ConnectionResetError: ")
    assert "reset by peer" in out


def test_clean_error_accepts_plain_string():
    assert source_health.clean_error("HTTP 403") == "HTTP 403"


def test_iso_returns_none_for_missing_timestamps():
    assert source_health._iso(None) is None
    assert source_health._iso(0) is None


def test_iso_uses_shanghai_offset():
    """台账里的时间必须带 +08:00，否则运维会把 UTC 当北京时间读（差 8 小时）。"""
    assert source_health._iso(1792380457) == "2026-10-19T11:27:37+08:00"


# --------------------------------------------------------------------------
# 接线：em_get 按主机名记账
# --------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, resp: _Resp | None = None, exc: BaseException | None = None):
        self.resp = resp
        self.exc = exc
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.resp


@pytest.fixture()
def fast_em(monkeypatch):
    """去掉限流等待并固定连接模式，避免用例真的 sleep 或去探测代理。"""
    monkeypatch.setattr(astock, "_EM_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(astock, "_em_mode", ["direct"])


def test_em_get_records_success_under_hostname(fast_em, monkeypatch):
    monkeypatch.setattr(astock, "_em_session", lambda direct: _Session(_Resp(200)))
    resp = astock.em_get(
        "https://push2ex.eastmoney.com/getTopicZTPool", params={"dpt": "wz.ztzt"}
    )
    assert resp.status_code == 200
    rows = source_health.snapshot()
    assert [r["source"] for r in rows] == ["push2ex.eastmoney.com"]
    assert rows[0]["healthy"] is True
    assert rows[0]["ok_count"] == 1


def test_em_get_treats_http_error_response_as_failure(fast_em, monkeypatch):
    """`requests` 不会因 5xx 抛异常 ⇒ 只包 try/except 会把「源在拒绝服务」记成健康。

    prod 被东财封禁时拿到的正是这类响应，这条断言就是守这个缺口。
    """
    monkeypatch.setattr(astock, "_em_session", lambda direct: _Session(_Resp(502)))
    resp = astock.em_get("https://push2.eastmoney.com/api/qt/clist/get")
    assert resp.status_code == 502
    row = source_health.snapshot()[0]
    assert row["healthy"] is False
    assert row["consecutive_errors"] == 1
    assert "502" in row["last_error"]


def test_em_get_records_network_error_and_reraises(fast_em, monkeypatch):
    boom = ConnectionError("Connection aborted")
    monkeypatch.setattr(astock, "_em_session", lambda direct: _Session(exc=boom))
    with pytest.raises(ConnectionError):
        astock.em_get("https://push2his.eastmoney.com/api/qt/stock/kline/get")
    row = source_health.snapshot()[0]
    assert row["source"] == "push2his.eastmoney.com"
    assert row["healthy"] is False
    assert "ConnectionError" in row["last_error"]


def test_auto_mode_records_only_the_final_outcome(monkeypatch):
    """直连失败、代理成功 ⇒ 记成功。

    中间那次探测失败不代表「源对本站不可用」；记下来只会让台账长期挂着一条假故障。
    """
    monkeypatch.setattr(astock, "_EM_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(astock, "_em_mode", ["auto"])

    def _session(direct: bool):
        return _Session(exc=ConnectionError("direct blocked")) if direct else _Session(_Resp(200))

    monkeypatch.setattr(astock, "_em_session", _session)
    assert astock.em_get("https://push2.eastmoney.com/x").status_code == 200
    row = source_health.snapshot()[0]
    assert row["healthy"] is True
    assert row["error_count"] == 0


def test_ledger_failure_never_breaks_fetch(fast_em, monkeypatch):
    """记账是旁路观测：它自己炸了也不能把取数带下水。"""

    def _boom(*args, **kwargs):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(astock.source_health, "record_ok", _boom)
    monkeypatch.setattr(astock.source_health, "record_error", _boom)
    monkeypatch.setattr(astock, "_em_session", lambda direct: _Session(_Resp(200)))
    assert astock.em_get("https://push2.eastmoney.com/x").status_code == 200


def test_ledger_failure_does_not_mask_original_error(fast_em, monkeypatch):
    """取数真失败时，抛出的必须是**原始**异常，不是记账抛的那个。"""

    def _boom(*args, **kwargs):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(astock.source_health, "record_error", _boom)
    monkeypatch.setattr(
        astock, "_em_session", lambda direct: _Session(exc=ConnectionError("aborted"))
    )
    with pytest.raises(ConnectionError):
        astock.em_get("https://push2.eastmoney.com/x")


def test_unparsable_url_does_not_record(fast_em, monkeypatch):
    monkeypatch.setattr(astock, "_em_session", lambda direct: _Session(_Resp(200)))
    astock.em_get("")
    assert source_health.snapshot() == []


# --------------------------------------------------------------------------
# 对外暴露
# --------------------------------------------------------------------------


def test_health_endpoint_exposes_sources():
    """台账必须能从 /api/health 读到——否则它只是一块没人看的内存。"""
    from fastapi.testclient import TestClient

    import app as app_module

    source_health.record_error("push2.eastmoney.com", "ConnectionError: aborted")
    body = TestClient(app_module.app).get("/api/health").json()
    # 源故障不等于本服务挂了：`ok` 不能被数据源状态带翻
    assert body["ok"] is True
    assert body["sources"][0]["source"] == "push2.eastmoney.com"
    assert body["sources"][0]["healthy"] is False


# --------------------------------------------------------------------------
# 接线：市场页的两个 akshare 源（失败时返回空载荷，不是抛异常）
# --------------------------------------------------------------------------

_LEGU_ROWS = [
    {"item": "上涨", "value": "3200"},
    {"item": "下跌", "value": "1500"},
    {"item": "平盘", "value": "210"},
    {"item": "涨停", "value": "88"},
    {"item": "真实涨停", "value": "72"},
]


def _fake_akshare(legu=None, fund_flow=None):
    """最小 akshare 替身：只提供这两个函数，其余属性缺省。"""
    import types

    mod = types.SimpleNamespace(
        stock_market_activity_legu=legu,
        stock_fund_flow_industry=fund_flow,
    )
    return mod


def _df_rows(rows):
    class _Df:
        def iterrows(self):
            return list(enumerate(rows))

    return _Df()


def test_sentiment_failure_records_legu(monkeypatch):
    """乐咕反爬（#417）：`pd.read_html` 抛错被 `except` 吞成 `{}`，台账必须留痕。"""
    def _boom():
        raise ValueError("No tables found")

    monkeypatch.setattr(astock, "_akshare", lambda: _fake_akshare(legu=_boom))
    assert market._sentiment() == {}
    row = source_health.snapshot()[0]
    assert row["source"] == market._AK_LEGU_HOST
    assert row["healthy"] is False
    assert "No tables found" in row["last_error"]


def test_sentiment_success_records_legu(monkeypatch):
    def _ok():
        return _df_rows(_LEGU_ROWS)

    monkeypatch.setattr(astock, "_akshare", lambda: _fake_akshare(legu=_ok))
    got = market._sentiment()
    assert got["up"] == 3200
    row = source_health.snapshot()[0]
    assert row["source"] == market._AK_LEGU_HOST
    assert row["healthy"] is True


def test_sentiment_missing_dependency_still_records(monkeypatch):
    """akshare 未装也算「这个源现在拿不到」，不能因为异常类型不同就不记。"""
    def _boom():
        raise astock.DependencyMissing("akshare 未安装")

    monkeypatch.setattr(astock, "_akshare", lambda: _fake_akshare(legu=_boom))
    assert market._sentiment() == {}
    assert source_health.snapshot()[0]["healthy"] is False


def test_sectors_records_fund_flow_host(monkeypatch):
    """板块资金流是同花顺源，与乐咕不同主机——两块不能记到同一个键上。"""
    def _boom():
        raise RuntimeError("timeout")

    monkeypatch.setattr(astock, "_akshare", lambda: _fake_akshare(fund_flow=_boom))
    assert market._sectors() == []
    rows = {r["source"]: r for r in source_health.snapshot()}
    assert rows[market._AK_FUND_FLOW_HOST]["healthy"] is False
    assert market._AK_LEGU_HOST not in rows


def test_sectors_success_records_ok(monkeypatch):
    def _ok(symbol=None):
        rows = [
            {"行业": "电池", "行业-涨跌幅": "3.2", "净额": "1.25e8", "流入资金": "3e8", "流出资金": "1.75e8", "公司家数": "62"},
            {"行业": "银行", "行业-涨跌幅": "0.4", "净额": "2.1e8", "流入资金": "8e8", "流出资金": "5.9e8", "公司家数": "42"},
        ]

        class _Df:
            def iterrows(self):
                return list(enumerate(rows))

            def sort_values(self, *_a, **_kw):
                return self

        return _Df()

    monkeypatch.setattr(astock, "_akshare", lambda: _fake_akshare(fund_flow=_ok))
    got = market._sectors()
    assert [item["name"] for item in got] == ["电池", "银行"]
    assert source_health.snapshot()[0]["healthy"] is True
