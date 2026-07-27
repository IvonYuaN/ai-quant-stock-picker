from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import aqsp.core.http as http


class _Response:
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_urlopen_no_macos_proxy_uses_empty_proxy_handler_on_darwin(monkeypatch) -> None:
    calls: list[tuple[str, Any]] = []
    response = _Response()

    class Opener:
        def open(self, request: object, *, timeout: float) -> _Response:
            calls.append(("open", request, timeout))
            return response

    def fake_proxy_handler(payload: dict[str, str]) -> object:
        calls.append(("proxy", payload))
        return SimpleNamespace(payload=payload)

    def fake_build_opener(handler: object) -> Opener:
        calls.append(("build", handler))
        return Opener()

    monkeypatch.setattr(http.sys, "platform", "darwin")
    monkeypatch.setattr(http.urllib.request, "ProxyHandler", fake_proxy_handler)
    monkeypatch.setattr(http.urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw urlopen")),
    )

    assert http.urlopen_no_macos_proxy("https://example.com", timeout=1.5) is response
    assert calls[0] == ("proxy", {})
    assert calls[-1] == ("open", "https://example.com", 1.5)


def test_urlopen_no_macos_proxy_keeps_platform_default_off_macos(monkeypatch) -> None:
    response = _Response()
    calls: list[tuple[str, object, float]] = []

    def fake_urlopen(request: object, *, timeout: float) -> _Response:
        calls.append(("urlopen", request, timeout))
        return response

    monkeypatch.setattr(http.sys, "platform", "linux")
    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)

    assert http.urlopen_no_macos_proxy("https://example.com", timeout=2.0) is response
    assert calls == [("urlopen", "https://example.com", 2.0)]
