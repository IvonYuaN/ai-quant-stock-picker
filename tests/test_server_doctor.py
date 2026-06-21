from __future__ import annotations

from aqsp.data.source_readiness import SourceReadinessSnapshot
from aqsp.utils.llm_safe import LlmResult
from scripts import server_doctor


def test_server_doctor_formats_sections_when_no_optional_env(
    monkeypatch, capsys
) -> None:
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(server_doctor, "_load_env_file", lambda: None)

    exit_code = server_doctor.main([])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "# AQSP Server Doctor" in output
    assert "## Artifacts" in output
    assert "## Source Auth" in output
    assert "## LLM" in output
    assert "## Notify" in output


def test_server_doctor_reports_llm_probe_status(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GLM_API_KEY", "glm-test-key")
    monkeypatch.setattr(server_doctor, "_load_env_file", lambda: None)
    monkeypatch.setattr(
        server_doctor,
        "llm_call_or_fallback",
        lambda **_kwargs: LlmResult(text="OK", degraded=False, model="glm-4.7-flash"),
    )

    exit_code = server_doctor.main(["--probe-llm"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "llm:glm: status=ok" in output
    assert "text=OK" in output


def test_server_doctor_reports_source_auth_probe(monkeypatch, capsys) -> None:
    monkeypatch.setattr(server_doctor, "_load_env_file", lambda: None)
    monkeypatch.setattr(
        server_doctor,
        "inspect_source_readiness",
        lambda entry, probe_auth=False: SourceReadinessSnapshot(
            source_id=entry.id,
            auth_kind="login_session",
            auth_status="ok" if entry.id == "baostock" else "missing_env",
            auth_message="ready" if entry.id == "baostock" else "缺少 TUSHARE_TOKEN",
            auth_checked_at="2026-06-02T18:00:00+08:00",
            active_probe=probe_auth,
            workload_fit={
                "live_short": "avoid",
                "walkforward": "primary",
                "pit": "candidate",
            },
        ),
    )

    exit_code = server_doctor.main(["--probe-auth"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "baostock: status=ok detail=ready" in output
    assert "tushare: status=missing_env detail=缺少 TUSHARE_TOKEN" in output


def test_server_doctor_masks_configured_llm_key_when_not_probing(monkeypatch) -> None:
    monkeypatch.setenv("AGNES_API_KEY", "abcdefgh12345678")

    check = server_doctor._llm_check("agnes", probe=False)

    assert check.status == "configured"
    assert "abcd***5678" in check.detail


def test_server_doctor_artifact_check_uses_runtime_paths(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AQSP_SQLITE_DB_PATH=/tmp/demo.db\n", encoding="utf-8")
    monkeypatch.setattr(server_doctor, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(server_doctor, "_load_env_file", lambda: None)

    checks = server_doctor._artifact_checks()
    names = {item.name for item in checks}

    assert "env" in names
    assert "sqlite_db" in names


def test_server_doctor_reports_serverchan_notify_channel(monkeypatch) -> None:
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "sctp_test_key")

    checks = server_doctor._notify_checks()
    by_name = {item.name: item for item in checks}

    assert by_name["notify:serverchan"].status == "configured"
    assert by_name["notify:serverchan"].detail == "serverchan"


def test_server_doctor_env_file_overrides_blank_process_env(
    tmp_path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('SERVERCHAN_SENDKEY="sctp_from_file"\n', encoding="utf-8")
    monkeypatch.setattr(server_doctor, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "")

    server_doctor._load_env_file()

    assert server_doctor.os.getenv("SERVERCHAN_SENDKEY") == "sctp_from_file"


def test_server_doctor_reports_notify_mode_and_channels(monkeypatch) -> None:
    monkeypatch.setenv("AQSP_NOTIFY_MODE", "fanout")
    monkeypatch.setenv("SERVERCHAN_SENDKEY", "sctp_test_key")
    monkeypatch.setenv(
        "WECHAT_WEBHOOK_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x"
    )

    checks = server_doctor._notify_checks()
    by_name = {item.name: item for item in checks}

    assert by_name["notify:mode"].status == "ok"
    assert "mode=fanout" in by_name["notify:mode"].detail
    assert "serverchan" in by_name["notify:mode"].detail
    assert "wechat" in by_name["notify:mode"].detail
