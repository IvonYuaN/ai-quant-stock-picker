from __future__ import annotations

from scripts.remote_runtime_probe import ProbeCheck, _ssh_banner_probe, _summarize_checks


def test_remote_runtime_probe_does_not_accept_non_ssh_banner(monkeypatch) -> None:
    class _Socket:
        def settimeout(self, _timeout: float) -> None:
            return None

        def connect(self, _address: tuple[str, int]) -> None:
            return None

        def recv(self, _size: int) -> bytes:
            return b"HTTP/1.1 200 OK"

        def close(self) -> None:
            return None

    monkeypatch.setattr("scripts.remote_runtime_probe.socket.socket", _Socket)

    check = _ssh_banner_probe("127.0.0.1", 22, 1.0)

    assert check.status == "failed"
    assert check.detail.endswith("invalid banner")


def test_remote_runtime_probe_summary_reports_missing_https_listener() -> None:
    checks = [
        ProbeCheck("ssh_target", "info", "alias=aqsp-server host=8.8.8.8 port=22"),
        ProbeCheck("tcp", "ok", "8.8.8.8:22"),
        ProbeCheck("ssh_banner", "ok", "SSH-2.0-OpenSSH"),
        ProbeCheck(
            "http_target",
            "info",
            "url=https://lh.ifidy.cn/api/health host=lh.ifidy.cn port=443",
        ),
        ProbeCheck("tcp", "failed", "lh.ifidy.cn:443 [Errno 61] Connection refused"),
        ProbeCheck("tls", "failed", "lh.ifidy.cn:443 [Errno 61] Connection refused"),
        ProbeCheck("http", "failed", "https://lh.ifidy.cn/api/health connection refused"),
    ]

    summary = _summarize_checks(checks)

    assert summary[0] == "HTTPS 入口异常：443 端口未正常监听，优先检查 Nginx/安全组/防火墙。"


def test_remote_runtime_probe_summary_reports_tls_handshake_only_when_tcp_ok() -> None:
    checks = [
        ProbeCheck("ssh_target", "info", "alias=aqsp-server host=8.8.8.8 port=22"),
        ProbeCheck("tcp", "ok", "8.8.8.8:22"),
        ProbeCheck("ssh_banner", "ok", "SSH-2.0-OpenSSH"),
        ProbeCheck(
            "http_target",
            "info",
            "url=https://lh.ifidy.cn/api/health host=lh.ifidy.cn port=443",
        ),
        ProbeCheck("tcp", "ok", "lh.ifidy.cn:443"),
        ProbeCheck("tls", "failed", "lh.ifidy.cn:443 handshake failure"),
        ProbeCheck("http", "failed", "https://lh.ifidy.cn/api/health EOF"),
    ]

    summary = _summarize_checks(checks)

    assert summary[0] == "HTTPS 入口异常：443 可达但 TLS 握手失败，优先检查 Nginx/证书/反向代理链路。"
