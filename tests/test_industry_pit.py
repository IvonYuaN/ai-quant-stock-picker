"""申万行业 point-in-time 测试。

核心验证「前视偏差消除」：同一只票在不同时点返回不同历史行业，
上市前的时点返回 None。覆盖 _normalize、IndustryPitSource.from_dataframe、
_industry_as_of 纯函数；另锁死 #335 三项：缓存落 pit_cache、前导零往返、
truststore 会话级注入。
"""

from __future__ import annotations

import io
import ssl

import pandas as pd
import pytest
import requests

from aqsp.core.errors import DataError
from aqsp.data import industry_pit
from aqsp.data.industry_pit import (
    IndustryPitSource,
    _normalize,
    industry_as_of,
)


def _raw_sw() -> pd.DataFrame:
    """模拟申万原始表（中文列名），平安银行三次行业调整。"""
    return pd.DataFrame(
        [
            ["1", "000001", "1991-04-03", "440101", "2099-12-31"],
            ["1", "000001", "2014-02-21", "480101", "2099-12-31"],
            ["1", "000001", "2021-07-30", "480301", "2099-12-31"],
        ],
        columns=["序号", "股票代码", "计入日期", "行业代码", "更新日期"],
    )


def _normalized() -> pd.DataFrame:
    return _normalize(_raw_sw())


def test_normalize_zero_pads_and_builds_l1_l2():
    df = _normalized()
    assert (df["code"] == "000001").all()
    assert (df["industry_code"] == "440101").any()
    # 一级 480000、二级 480300（来自 480301）
    row = df[df["industry_code"] == "480301"].iloc[0]
    assert row["l1_code"] == "480000"
    assert row["l2_code"] == "480300"


def test_normalize_raises_when_schema_changed():
    bad = pd.DataFrame({"foo": [1]})
    with pytest.raises(DataError):
        _normalize(bad)


def test_industry_as_of_point_in_time():
    df = _normalized()
    # 平安银行历史行业变迁（上游实测）
    assert industry_as_of(df, "000001", "2013-01-01").industry_code == "440101"
    assert industry_as_of(df, "000001", "2016-01-01").industry_code == "480101"
    assert industry_as_of(df, "000001", "2026-08-18").industry_code == "480301"


def test_industry_as_of_none_before_listing():
    df = _normalized()
    # 首次行业调整 start_date=1991-04-03，早于该日的时点视为尚未上市
    assert industry_as_of(df, "000001", "1990-01-01") is None


def test_industry_as_of_picks_latest_not_later_than_as_of():
    df = _normalized()
    # 2014-02-21 之后的第一次调整为 480101，2015 年仍应属 480101 而非更早
    assert industry_as_of(df, "000001", "2015-06-01").industry_code == "480101"


def test_source_from_dataframe_query():
    src = IndustryPitSource().from_dataframe(_normalized())
    label = src.industry_as_of("000001", "2026-08-18", autoload=False)
    assert label is not None
    assert label.industry_code == "480301"
    assert label.l1_code == "480000"


def test_source_autoload_raises_if_disabled_and_unloaded():
    src = IndustryPitSource()
    with pytest.raises(DataError):
        src.industry_as_of("000001", "2026-08-18", autoload=False)


# ---------------------------------------------------------------------------
# #335 断裂2：缓存必须落 runtime_data_root()/pit_cache/（release 目录不可变）
# ---------------------------------------------------------------------------
def test_default_cache_path_under_runtime_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    src = IndustryPitSource()
    assert src._default_cache_path() == str(tmp_path / "pit_cache" / "industry_pit.csv")
    # 落点目录由 source 懒建（写缓存不因目录缺失而静默失败）
    assert (tmp_path / "pit_cache").is_dir()


# ---------------------------------------------------------------------------
# #335 断裂3：load_cached() 缓存只读预热（绝不联网）
# ---------------------------------------------------------------------------
def test_load_cached_no_cache_never_fetches(tmp_path, monkeypatch):
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))

    def _boom(url=industry_pit.SW_URL):  # noqa: ANN001, ANN202
        raise AssertionError("load_cached 绝不允许联网")

    monkeypatch.setattr(industry_pit, "sw_industry_history", _boom)
    assert IndustryPitSource().load_cached() is False


def test_load_cached_roundtrip_reaches_query(tmp_path, monkeypatch):
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    # 生产者落盘形态：规范表 to_csv（同 IndustryPitSource.load 写盘）
    cache = tmp_path / "pit_cache" / "industry_pit.csv"
    cache.parent.mkdir(parents=True, exist_ok=True)
    _normalized().to_csv(cache, index=False)
    src = IndustryPitSource()
    assert src.load_cached() is True
    label = src.industry_as_of("000001", "2026-08-18", autoload=False)
    assert label is not None and label.industry_code == "480301"


# ---------------------------------------------------------------------------
# #335 断裂4：load() 读缓存不得吃掉前导零（"000001" ≠ 1）
# ---------------------------------------------------------------------------
def test_load_preserves_leading_zeros(tmp_path, monkeypatch):
    monkeypatch.setenv("AQSP_RUNTIME_DATA_ROOT", str(tmp_path))
    cache = tmp_path / "pit_cache" / "industry_pit.csv"
    cache.parent.mkdir(parents=True, exist_ok=True)
    _normalized().to_csv(cache, index=False)
    df = IndustryPitSource().load()  # 缓存命中分支（force=False）
    assert (df["code"] == "000001").all(), "前导零不得被 pandas 推断吃掉"
    assert (df["l1_code"] == "480000").any() and (df["l1_code"] == "440000").any()
    # 前导零在位 ⇒ 时点查询命中
    label = industry_as_of(df, "000001", "2026-08-18")
    assert label is not None and label.industry_code == "480301"


# ---------------------------------------------------------------------------
# #335 断裂5：truststore 会话级注入（swsresearch 只发叶子证书）
# ---------------------------------------------------------------------------
def _fake_xls_bytes() -> bytes:
    """openpyxl 现场造一份申万表头 xlsx（read_excel 按内容签名选引擎）。"""
    from openpyxl import Workbook

    buf = io.BytesIO()
    wb = Workbook()
    ws = wb.active
    ws.append(["序号", "股票代码", "计入日期", "行业代码", "更新日期"])
    ws.append(["1", "000001", "2021-07-30", "480301", "2099-12-31"])
    wb.save(buf)
    return buf.getvalue()


class _FakeResponse:
    content = b""  # 由子类/实例注入

    def raise_for_status(self) -> None:
        return None


def test_sw_session_mounts_truststore_adapter():
    session = industry_pit._sw_session()
    adapter = session.get_adapter("https://www.swsresearch.com")
    assert isinstance(adapter, requests.adapters.HTTPAdapter)
    ssl_ctx = adapter.poolmanager.connection_pool_kw["ssl_context"]
    assert isinstance(ssl_ctx, ssl.SSLContext), (
        "申万会话连接池必须带自定义 SSLContext（否则退回 certifi，缺中间证书必验不过）"
    )
    # 钉中间证书 ≠ 关闭校验：必须仍是完整校验。
    assert ssl_ctx.verify_mode == ssl.CERT_REQUIRED
    assert ssl_ctx.check_hostname is True


# ---------------------------------------------------------------------------
# #556 修复：requests 走代理时 proxy_manager_for() 另建连接池且不带 ssl_context
# ⇒ truststore 被静默丢弃、退回 certifi ⇒ CERTIFICATE_VERIFY_FAILED（实测根因）
# ---------------------------------------------------------------------------
def test_sw_session_proxy_pool_carries_ssl_context(monkeypatch):
    """代理池也必须带 ssl_context —— 否则走系统代理时申万抓取必然失败。"""
    captured: dict[str, object] = {}

    def _fake_proxy_manager_for(self, proxy, **kwargs):  # noqa: ANN001, ANN001
        captured["proxy"] = proxy
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        requests.adapters.HTTPAdapter, "proxy_manager_for", _fake_proxy_manager_for
    )
    adapter = industry_pit._make_sw_adapter()
    adapter.proxy_manager_for("http://127.0.0.1:7890")
    assert captured.get("proxy") == "http://127.0.0.1:7890"
    assert isinstance(captured.get("ssl_context"), ssl.SSLContext), (
        "代理连接池必须透传 ssl_context（否则 truststore 被丢弃 → 退回 certifi → 验不过）"
    )


def test_pinned_intermediate_lands_in_trust_store():
    """钉住的中间证书必须真的进了信任库（补链，不是装饰）。"""
    ctx = ssl.create_default_context()
    industry_pit._pin_intermediate(ctx)
    names = set(_common_names(ctx))
    assert "GeoTrust G2 TLS CN RSA4096 SHA256 2022 CA1" in names, (
        "钉住的中间证书未进信任库 ⇒ 链仍然断（unable to get local issuer certificate）"
    )


def test_pinned_intermediate_identity_is_pinned(tmp_path):
    """钉住的这张证书必须是预期的 DigiCert 中间证书（防被替换/损坏/过期）。"""
    import datetime as _dt

    pem_path = tmp_path / "geotrust_inter.pem"
    pem_path.write_text(industry_pit._GEO_TRUST_G2_TLS_CN_RSA4096_SHA256_2022_CA1_PEM)
    info = ssl._ssl._test_decode_cert(str(pem_path))  # noqa: SLF001 - CPython 稳定私有接口

    def _cn(block: tuple) -> str:
        for rdn in block:
            for key, value in rdn:
                if key == "commonName":
                    return value
        return ""

    assert _cn(info["subject"]) == "GeoTrust G2 TLS CN RSA4096 SHA256 2022 CA1"
    assert _cn(info["issuer"]) == "DigiCert Global Root G2"
    # 有效期必须覆盖当下（钉过期证书等于没钉）
    now = _dt.datetime.now(_dt.timezone.utc)
    not_before = _dt.datetime.strptime(info["notBefore"], "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=_dt.timezone.utc
    )
    not_after = _dt.datetime.strptime(info["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=_dt.timezone.utc
    )
    assert not_before <= now <= not_after


def test_new_ssl_context_survives_missing_truststore(monkeypatch):
    """truststore 缺失时必须退回 stdlib 上下文，而不是抛错让行业 PIT 恒空。"""
    import builtins as _builtins

    real_import = _builtins.__import__

    def _fake_import(name, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if name == "truststore":
            raise ImportError("simulated: truststore not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(_builtins, "__import__", _fake_import)
    ctx = industry_pit._new_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def _common_names(ctx: ssl.SSLContext) -> list[str]:
    """从信任库里抽出所有 commonName（truststore 的 get_ca_certs 会抛，故容错）。"""
    try:
        certs = ctx.get_ca_certs()
    except NotImplementedError:  # pragma: no cover - truststore 实现差异
        return []
    names: list[str] = []
    for cert in certs:
        for rdn in cert.get("subject", ()):  # type: ignore[union-attr]
            for key, value in rdn:
                if key == "commonName":
                    names.append(value)
    return names


def test_sw_industry_history_routes_through_truststore_session(monkeypatch):
    calls = {"get": 0}

    class _FakeSession:
        def get(self, url, **kwargs):  # noqa: ANN001, ANN003
            calls["get"] += 1
            resp = _FakeResponse()
            resp.content = _fake_xls_bytes()
            return resp

    monkeypatch.setattr(industry_pit, "_sw_session", _FakeSession)
    df = industry_pit.sw_industry_history()
    assert calls["get"] == 1, "联网路径必须经 _sw_session()（truststore 注入点）"
    assert (df["code"] == "000001").all()
