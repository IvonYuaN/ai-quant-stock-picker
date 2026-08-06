from __future__ import annotations

from types import SimpleNamespace

import astock
import cli_runtime
import newsradar


def test_cli_runtime_uses_spawn_safe_kwargs_on_macos(monkeypatch) -> None:
    env = {"PATH": "/usr/bin"}
    monkeypatch.setattr(cli_runtime.sys, "platform", "darwin")

    kwargs = cli_runtime._subprocess_launch_kwargs("/tmp/vibe-cli-x", env)

    assert kwargs == {"env": env, "close_fds": False}
    assert "cwd" not in kwargs
    assert "preexec_fn" not in kwargs
    assert "start_new_session" not in kwargs


def test_cli_runtime_keeps_temp_cwd_off_macos(monkeypatch) -> None:
    env = {"PATH": "/usr/bin"}
    monkeypatch.setattr(cli_runtime.sys, "platform", "linux")

    assert cli_runtime._subprocess_launch_kwargs("/tmp/vibe-cli-x", env) == {
        "cwd": "/tmp/vibe-cli-x",
        "env": env,
    }


def test_newsradar_fetch_uses_proxy_safe_urlopen(monkeypatch) -> None:
    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"""<?xml version='1.0'?><rss><channel>
            <item><title>AI infrastructure order</title><link>https://x.test/a</link>
            <pubDate>Mon, 27 Jul 2026 01:00:00 GMT</pubDate></item>
            </channel></rss>"""

    calls: list[object] = []

    def fake_urlopen(request: object, *, timeout: float) -> Response:
        calls.append((request, timeout))
        return Response()

    monkeypatch.setattr(newsradar, "urlopen_no_macos_proxy", fake_urlopen)

    rows = newsradar._fetch_source(
        {"url": "https://x.test/rss", "name": "X"},
        3,
        None,
        [],
    )

    assert calls and calls[0][1] == 14
    assert rows and rows[0]["title"] == "AI infrastructure order"


def test_astock_gtimg_uses_proxy_safe_urlopen(monkeypatch) -> None:
    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'v_sh600000="1~x~600000~10";'

    calls: list[object] = []

    def fake_urlopen(request: object, *, timeout: float) -> Response:
        calls.append((request, timeout))
        return Response()

    monkeypatch.setattr(astock, "urlopen_no_macos_proxy", fake_urlopen)

    assert astock._fetch_gtimg(["sh600000"]) == 'v_sh600000="1~x~600000~10";'
    assert calls and calls[0][1] == 10


def test_astock_requests_post_disables_environment_proxy(monkeypatch) -> None:
    class Session:
        instances: list["Session"] = []

        def __init__(self) -> None:
            self.trust_env = True
            self.__class__.instances.append(self)

        def post(self, url: str, **kwargs: object) -> SimpleNamespace:
            assert self.trust_env is False
            return SimpleNamespace(url=url, kwargs=kwargs)

        def close(self) -> None:
            return None

    monkeypatch.delenv("AQSP_DATA_TRUST_ENV", raising=False)
    monkeypatch.setattr(astock.requests, "Session", Session)

    response = astock._requests_post("https://x.test", timeout=1)

    assert response.url == "https://x.test"
    assert len(Session.instances) == 1
