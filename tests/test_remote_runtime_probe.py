from __future__ import annotations

from scripts.remote_runtime_probe import (
    ProbeCheck,
    _resolve_ssh_target,
    _ssh_banner_probe,
    _summarize_checks,
)


def test_resolve_ssh_target_falls_back_to_self_when_alias_undefined(monkeypatch) -> None:
    """未定义 `aqsp-server` 别名时，必须回退为自检 127.0.0.1。

    2026-09-28 实测：`ssh -G <alias>` **总会**返回一份配置 —— 别名未定义时只是把
    hostname 原样回声成别名本身。因此旧判据 `if not config` 永假、回退成死代码：
    在没配该别名的 prod 上，探针会去连 `aqsp-server:22` ⇒ DNS 失败 ⇒ server_status
    每次都报 `critical check failed: remote_runtime_probe`（假 critical、掩盖真报警）。
    """
    monkeypatch.setattr(
        "scripts.remote_runtime_probe._parse_ssh_config",
        lambda alias: {"hostname": alias, "port": "22"},  # 模拟 ssh -G 的默认回声
    )

    host, port, _user = _resolve_ssh_target("aqsp-server")

    assert host == "127.0.0.1"
    assert port == 22


def test_resolve_ssh_target_keeps_real_hostname_when_alias_defined(monkeypatch) -> None:
    """别名已定义时必须用真实 hostname（不得误回退成 127.0.0.1）。"""
    monkeypatch.setattr(
        "scripts.remote_runtime_probe._parse_ssh_config",
        lambda alias: {"hostname": "10.0.0.5", "port": "2222", "user": "deploy"},
    )

    host, port, user = _resolve_ssh_target("aqsp-server")

    assert (host, port, user) == ("10.0.0.5", 2222, "deploy")


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
        ProbeCheck(
            "http", "failed", "https://lh.ifidy.cn/api/health connection refused"
        ),
    ]

    summary = _summarize_checks(checks)

    assert (
        summary[0]
        == "HTTPS 入口异常：443 端口未正常监听，优先检查 Nginx/安全组/防火墙。"
    )


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

    assert (
        summary[0]
        == "HTTPS 入口异常：443 可达但 TLS 握手失败，优先检查 Nginx/证书/反向代理链路。"
    )
