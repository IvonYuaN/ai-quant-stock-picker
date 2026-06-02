#!/usr/bin/env python3
"""Server doctor for AQSP runtime, auth, LLM, and notification readiness."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from aqsp.data.registry import get_registry_entry
from aqsp.data.source_readiness import inspect_source_readiness
from aqsp.utils.llm_safe import llm_call_or_fallback


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str


def _runtime_path(env_name: str, default: str) -> Path:
    raw = os.getenv(env_name, default).strip() or default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _load_env_file() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _mask_secret(value: str) -> str:
    if not value:
        return "-"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}***{value[-4:]}"


def _artifact_checks() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for name, path in (
        ("env", PROJECT_ROOT / ".env"),
        ("venv", PROJECT_ROOT / ".venv"),
        ("sqlite_db", _runtime_path("AQSP_SQLITE_DB_PATH", "data/astocks_qfq.db")),
        (
            "dashboard",
            _runtime_path("AQSP_DASHBOARD_HTML", "dist/dashboard/index.html"),
        ),
        ("report", _runtime_path("AQSP_REPORT", "reports/latest.md")),
    ):
        exists = path.exists()
        detail = str(path)
        if exists and path.is_file():
            detail = f"{detail} ({path.stat().st_size} bytes)"
        checks.append(
            DoctorCheck(
                name=name,
                status="ok" if exists else "missing",
                detail=detail,
            )
        )
    return checks


def _source_auth_checks(*, probe_auth: bool) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for source_id in ("baostock", "tushare"):
        entry = get_registry_entry(source_id)
        if entry is None:
            checks.append(DoctorCheck(source_id, "missing", "registry entry not found"))
            continue
        snapshot = inspect_source_readiness(entry, probe_auth=probe_auth)
        checks.append(
            DoctorCheck(
                name=source_id,
                status=snapshot.auth_status,
                detail=snapshot.auth_message,
            )
        )
    return checks


def _provider_api_key(provider: str) -> str:
    return (
        os.getenv(f"{provider.upper()}_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )


def _provider_model(provider: str) -> str:
    env_name = f"{provider.upper()}_MODEL"
    if os.getenv(env_name, "").strip():
        return os.getenv(env_name, "").strip()
    return os.getenv("LLM_MODEL", "").strip()


def _llm_check(provider: str, *, probe: bool) -> DoctorCheck:
    api_key = _provider_api_key(provider)
    model = _provider_model(provider) or "(default)"
    if not api_key:
        return DoctorCheck(
            name=f"llm:{provider}",
            status="missing_env",
            detail=f"model={model} api_key=-",
        )

    if not probe:
        return DoctorCheck(
            name=f"llm:{provider}",
            status="configured",
            detail=f"model={model} api_key={_mask_secret(api_key)}",
        )

    old_provider = os.getenv("LLM_PROVIDER")
    old_briefing = os.getenv("ENABLE_LLM_BRIEFING")
    try:
        os.environ["LLM_PROVIDER"] = provider
        os.environ["ENABLE_LLM_BRIEFING"] = "1"
        result = llm_call_or_fallback(
            prompt="你是联通测试助手，只回复：OK",
            fallback="FALLBACK",
            enable_llm=True,
            caller=f"server-doctor-{provider}",
        )
    finally:
        if old_provider is None:
            os.environ.pop("LLM_PROVIDER", None)
        else:
            os.environ["LLM_PROVIDER"] = old_provider
        if old_briefing is None:
            os.environ.pop("ENABLE_LLM_BRIEFING", None)
        else:
            os.environ["ENABLE_LLM_BRIEFING"] = old_briefing

    if result.degraded:
        return DoctorCheck(
            name=f"llm:{provider}",
            status="degraded",
            detail=f"model={result.model or model} reason={result.reason or '-'}",
        )
    return DoctorCheck(
        name=f"llm:{provider}",
        status="ok",
        detail=f"model={result.model or model} text={result.text[:32]}",
    )


def _llm_checks(*, probe_llm: bool) -> list[DoctorCheck]:
    providers: list[str] = []
    for provider in ("glm", "agnes", "qwen", "siliconflow"):
        if _provider_api_key(provider):
            providers.append(provider)
    if not providers:
        active = os.getenv("LLM_PROVIDER", "glm").strip().lower() or "glm"
        providers.append(active)
    deduped = list(dict.fromkeys(providers))
    return [_llm_check(provider, probe=probe_llm) for provider in deduped]


def _notify_checks() -> list[DoctorCheck]:
    channel_envs = {
        "telegram": (
            os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        ),
        "wechat": (os.getenv("WECHAT_WEBHOOK_URL", "").strip(),),
        "feishu": (os.getenv("FEISHU_WEBHOOK_URL", "").strip(),),
        "dingtalk": (os.getenv("DINGTALK_WEBHOOK_URL", "").strip(),),
        "pushplus": (os.getenv("PUSHPLUS_TOKEN", "").strip(),),
        "discord": (os.getenv("DISCORD_WEBHOOK_URL", "").strip(),),
        "slack": (os.getenv("SLACK_WEBHOOK_URL", "").strip(),),
        "generic_webhook": (os.getenv("GENERIC_WEBHOOK_URL", "").strip(),),
    }
    checks: list[DoctorCheck] = []
    for name, values in channel_envs.items():
        configured = all(bool(value) for value in values)
        checks.append(
            DoctorCheck(
                name=f"notify:{name}",
                status="configured" if configured else "disabled",
                detail=name if configured else "not configured",
            )
        )
    return checks


def _format_section(title: str, checks: list[DoctorCheck]) -> list[str]:
    lines = [f"## {title}"]
    for check in checks:
        lines.append(f"- {check.name}: status={check.status} detail={check.detail}")
    lines.append("")
    return lines


def _has_hard_failures(checks: list[DoctorCheck]) -> bool:
    failing = {
        "missing",
        "missing_env",
        "login_failed",
        "auth_failed",
        "missing_package",
    }
    return any(check.status in failing for check in checks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="server_doctor",
        description="Diagnose AQSP server runtime and auth readiness.",
    )
    parser.add_argument(
        "--probe-auth",
        action="store_true",
        help="主动探测 baostock / tushare 登录或 token 可用性",
    )
    parser.add_argument(
        "--probe-llm",
        action="store_true",
        help="主动探测已配置的 LLM provider 联通性",
    )
    args = parser.parse_args(argv)

    _load_env_file()

    artifact_checks = _artifact_checks()
    source_checks = _source_auth_checks(probe_auth=args.probe_auth)
    llm_checks = _llm_checks(probe_llm=args.probe_llm)
    notify_checks = _notify_checks()

    lines = ["# AQSP Server Doctor", ""]
    lines.extend(_format_section("Artifacts", artifact_checks))
    lines.extend(_format_section("Source Auth", source_checks))
    lines.extend(_format_section("LLM", llm_checks))
    lines.extend(_format_section("Notify", notify_checks))
    print("\n".join(lines).rstrip())

    return 1 if _has_hard_failures(artifact_checks + source_checks + llm_checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
