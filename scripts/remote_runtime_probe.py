#!/usr/bin/env python3
"""Probe AQSP remote runtime connectivity from the local machine."""

from __future__ import annotations

import argparse
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProbeCheck:
    name: str
    status: str
    detail: str


def _parse_ssh_config(host_alias: str) -> dict[str, str]:
    if not host_alias.strip():
        return {}
    try:
        result = subprocess.run(
            ["ssh", "-G", host_alias],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    payload: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts
        payload[key.lower()] = value.strip()
    return payload


def _resolve_ssh_target(host_alias: str) -> tuple[str, int, str]:
    config = _parse_ssh_config(host_alias)
    host = config.get("hostname", "").strip() or host_alias.strip()
    port_raw = config.get("port", "22").strip() or "22"
    user = config.get("user", "").strip()
    if not config and host_alias.strip() == "aqsp-server":
        host = "127.0.0.1"
    try:
        port = int(port_raw)
    except ValueError:
        port = 22
    return host, port, user


def _tcp_probe(host: str, port: int, timeout: float) -> ProbeCheck:
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
    except Exception as exc:  # noqa: BLE001
        return ProbeCheck("tcp", "failed", f"{host}:{port} {exc}")
    finally:
        sock.close()
    return ProbeCheck("tcp", "ok", f"{host}:{port}")


def _ssh_banner_probe(host: str, port: int, timeout: float) -> ProbeCheck:
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        banner = sock.recv(256)
    except TimeoutError:
        return ProbeCheck("ssh_banner", "timeout", f"{host}:{port} banner timeout")
    except Exception as exc:  # noqa: BLE001
        return ProbeCheck("ssh_banner", "failed", f"{host}:{port} {exc}")
    finally:
        sock.close()
    text = banner.decode("utf-8", errors="replace").strip()
    if not text:
        return ProbeCheck("ssh_banner", "timeout", f"{host}:{port} empty banner")
    return ProbeCheck("ssh_banner", "ok", text)


def _tls_probe(host: str, port: int, server_name: str, timeout: float) -> ProbeCheck:
    context = ssl.create_default_context()
    raw_sock = socket.socket()
    raw_sock.settimeout(timeout)
    try:
        raw_sock.connect((host, port))
        with context.wrap_socket(raw_sock, server_hostname=server_name) as wrapped:
            cert = wrapped.getpeercert()
            subject = cert.get("subject", ())
            label = subject[0][0][1] if subject and subject[0] else server_name
            return ProbeCheck("tls", "ok", f"{server_name} cert={label}")
    except TimeoutError:
        return ProbeCheck("tls", "timeout", f"{server_name}:{port} handshake timeout")
    except ssl.SSLError as exc:
        return ProbeCheck("tls", "failed", f"{server_name}:{port} {exc}")
    except Exception as exc:  # noqa: BLE001
        return ProbeCheck("tls", "failed", f"{server_name}:{port} {exc}")
    finally:
        raw_sock.close()


def _http_probe(url: str, timeout: float) -> ProbeCheck:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "aqsp-remote-probe/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read(160).decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        return ProbeCheck("http", "failed", f"{url} HTTP {exc.code}")
    except TimeoutError:
        return ProbeCheck("http", "timeout", f"{url} read timeout")
    except Exception as exc:  # noqa: BLE001
        return ProbeCheck("http", "failed", f"{url} {exc}")
    preview = body.replace("\n", " ")[:80] or "-"
    return ProbeCheck("http", "ok", f"{url} HTTP {status} body={preview}")


def build_report(
    *,
    ssh_alias: str,
    ssh_host: str,
    ssh_port: int,
    ssh_user: str,
    http_url: str,
    timeout: float,
) -> list[ProbeCheck]:
    checks: list[ProbeCheck] = [
        ProbeCheck(
            "ssh_target",
            "info",
            f"alias={ssh_alias or '-'} host={ssh_host} port={ssh_port} user={ssh_user or '-'} timeout={timeout}s",
        )
    ]
    checks.append(_tcp_probe(ssh_host, ssh_port, timeout))
    checks.append(_ssh_banner_probe(ssh_host, ssh_port, timeout))

    parsed = urlparse(http_url)
    http_host = parsed.hostname or ""
    http_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    checks.append(
        ProbeCheck(
            "http_target",
            "info",
            f"url={http_url} host={http_host or '-'} port={http_port}",
        )
    )
    checks.append(_tcp_probe(http_host, http_port, timeout))
    if parsed.scheme == "https":
        checks.append(_tls_probe(http_host, http_port, http_host, timeout))
    checks.append(_http_probe(http_url, timeout))
    return checks


def _format_checks(checks: list[ProbeCheck]) -> str:
    lines = ["# AQSP Remote Runtime Probe", ""]
    for check in checks:
        lines.append(f"- {check.name}: status={check.status} detail={check.detail}")
    return "\n".join(lines)


def _has_failures(checks: list[ProbeCheck]) -> bool:
    return any(check.status in {"failed", "timeout"} for check in checks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="remote_runtime_probe",
        description="Probe SSH banner, TLS handshake, and HTTP health for AQSP remote runtime.",
    )
    parser.add_argument("--ssh-target", default="aqsp-server")
    parser.add_argument("--http-url", default="https://lh.ifidy.cn/_stcore/health")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)

    ssh_host, ssh_port, ssh_user = _resolve_ssh_target(args.ssh_target)
    checks = build_report(
        ssh_alias=args.ssh_target,
        ssh_host=ssh_host,
        ssh_port=ssh_port,
        ssh_user=ssh_user,
        http_url=args.http_url,
        timeout=max(args.timeout, 1.0),
    )
    print(_format_checks(checks))
    return 1 if _has_failures(checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
