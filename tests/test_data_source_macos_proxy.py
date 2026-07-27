from __future__ import annotations

from aqsp.data.eastmoney_source import EastmoneySource
from aqsp.data.sina_source import SinaSource
from aqsp.data.tencent_source import TencentSource


def test_public_data_sources_disable_environment_proxy_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AQSP_DATA_TRUST_ENV", raising=False)

    assert EastmoneySource()._session.trust_env is False
    assert SinaSource()._session.trust_env is False
    assert TencentSource()._session.trust_env is False


def test_public_data_sources_can_opt_into_environment_proxy(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_DATA_TRUST_ENV", "1")

    assert EastmoneySource()._session.trust_env is True
    assert SinaSource()._session.trust_env is True
    assert TencentSource()._session.trust_env is True
