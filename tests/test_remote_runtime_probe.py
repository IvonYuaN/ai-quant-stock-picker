from __future__ import annotations

import urllib.error

from scripts import remote_runtime_probe


def test_resolve_ssh_target_uses_ssh_config(monkeypatch) -> None:
    monkeypatch.setattr(
        remote_runtime_probe,
        "_parse_ssh_config",
        lambda _alias: {"hostname": "8.8.8.8", "port": "2222", "user": "root"},
    )

    host, port, user = remote_runtime_probe._resolve_ssh_target("aqsp-server")

    assert (host, port, user) == ("8.8.8.8", 2222, "root")


def test_resolve_ssh_target_defaults_localhost_for_server_self_probe(
    monkeypatch,
) -> None:
    monkeypatch.setattr(remote_runtime_probe, "_parse_ssh_config", lambda _alias: {})

    host, port, user = remote_runtime_probe._resolve_ssh_target("aqsp-server")

    assert (host, port, user) == ("127.0.0.1", 22, "")


def test_build_report_surfaces_layered_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        remote_runtime_probe,
        "_tcp_probe",
        lambda host, port, timeout: remote_runtime_probe.ProbeCheck(
            "tcp", "ok", f"{host}:{port}:{timeout}"
        ),
    )
    monkeypatch.setattr(
        remote_runtime_probe,
        "_ssh_banner_probe",
        lambda host, port, timeout: remote_runtime_probe.ProbeCheck(
            "ssh_banner", "timeout", f"{host}:{port}:{timeout}"
        ),
    )
    monkeypatch.setattr(
        remote_runtime_probe,
        "_tls_probe",
        lambda host, port, server_name, timeout: remote_runtime_probe.ProbeCheck(
            "tls", "timeout", f"{server_name}:{port}:{timeout}"
        ),
    )
    monkeypatch.setattr(
        remote_runtime_probe,
        "_http_probe",
        lambda url, timeout: remote_runtime_probe.ProbeCheck(
            "http", "failed", f"{url}:{timeout}"
        ),
    )

    checks = remote_runtime_probe.build_report(
        ssh_alias="aqsp-server",
        ssh_host="8.130.124.238",
        ssh_port=22,
        ssh_user="root",
        http_url="https://lh.ifidy.cn/_stcore/health",
        timeout=5.0,
    )

    assert checks[0].name == "ssh_target"
    assert any(item.name == "ssh_banner" and item.status == "timeout" for item in checks)
    assert any(item.name == "tls" and item.status == "timeout" for item in checks)
    assert any(item.name == "http" and item.status == "failed" for item in checks)
    assert remote_runtime_probe._has_failures(checks) is True


def test_format_checks_includes_layered_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        remote_runtime_probe,
        "_tcp_probe",
        lambda host, port, timeout: remote_runtime_probe.ProbeCheck(
            "tcp", "ok", f"{host}:{port}:{timeout}"
        ),
    )
    monkeypatch.setattr(
        remote_runtime_probe,
        "_ssh_banner_probe",
        lambda host, port, timeout: remote_runtime_probe.ProbeCheck(
            "ssh_banner", "timeout", f"{host}:{port}:{timeout}"
        ),
    )
    monkeypatch.setattr(
        remote_runtime_probe,
        "_tls_probe",
        lambda host, port, server_name, timeout: remote_runtime_probe.ProbeCheck(
            "tls", "timeout", f"{server_name}:{port}:{timeout}"
        ),
    )
    monkeypatch.setattr(
        remote_runtime_probe,
        "_http_probe",
        lambda url, timeout: remote_runtime_probe.ProbeCheck(
            "http", "failed", f"{url}:{timeout}"
        ),
    )

    checks = remote_runtime_probe.build_report(
        ssh_alias="aqsp-server",
        ssh_host="8.130.124.238",
        ssh_port=22,
        ssh_user="root",
        http_url="https://lh.ifidy.cn/_stcore/health",
        timeout=5.0,
    )
    rendered = remote_runtime_probe._format_checks(checks)

    assert "## Summary" in rendered
    assert "SSH 入口异常" in rendered
    assert "HTTPS 入口异常" in rendered
    assert "应用健康检查失败" in rendered


def test_http_probe_reports_http_error(monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            url="https://lh.ifidy.cn/_stcore/health",
            code=502,
            msg="Bad Gateway",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(remote_runtime_probe.urllib.request, "urlopen", _boom)

    result = remote_runtime_probe._http_probe(
        "https://lh.ifidy.cn/_stcore/health", 5.0
    )

    assert result.status == "failed"
    assert "HTTP 502" in result.detail


def test_tls_probe_reports_ssl_timeout(monkeypatch) -> None:
    class _FakeSocket:
        def settimeout(self, _timeout: float) -> None:
            return None

        def connect(self, _target: tuple[str, int]) -> None:
            return None

        def close(self) -> None:
            return None

    class _FakeContext:
        def wrap_socket(self, _sock, server_hostname: str):
            raise TimeoutError(f"{server_hostname} ssl timeout")

    monkeypatch.setattr(remote_runtime_probe.socket, "socket", lambda: _FakeSocket())
    monkeypatch.setattr(
        remote_runtime_probe.ssl,
        "create_default_context",
        lambda: _FakeContext(),
    )

    result = remote_runtime_probe._tls_probe("lh.ifidy.cn", 443, "lh.ifidy.cn", 5.0)

    assert result.status == "timeout"
    assert "handshake timeout" in result.detail
