from __future__ import annotations

from unittest.mock import MagicMock, patch

from aqsp.briefing.email_notifier import (
    EmailConfig,
    load_email_config_from_env,
    send_briefing_email,
)


def test_load_email_config_from_env_returns_none_when_missing(monkeypatch):
    for k in [
        "AQSP_SMTP_HOST",
        "AQSP_SMTP_PORT",
        "AQSP_SMTP_USER",
        "AQSP_SMTP_PASSWORD",
        "AQSP_EMAIL_FROM",
        "AQSP_EMAIL_TO",
    ]:
        monkeypatch.delenv(k, raising=False)
    assert load_email_config_from_env() is None


def test_load_email_config_from_env_returns_cfg_when_complete(monkeypatch):
    monkeypatch.setenv("AQSP_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("AQSP_SMTP_PORT", "587")
    monkeypatch.setenv("AQSP_SMTP_USER", "u")
    monkeypatch.setenv("AQSP_SMTP_PASSWORD", "p")
    monkeypatch.setenv("AQSP_EMAIL_FROM", "from@example.com")
    monkeypatch.setenv("AQSP_EMAIL_TO", "to1@example.com,to2@example.com")
    cfg = load_email_config_from_env()
    assert cfg is not None
    assert cfg.smtp_host == "smtp.example.com"
    assert cfg.smtp_port == 587
    assert cfg.to_addrs == ["to1@example.com", "to2@example.com"]


def test_load_email_config_empty_to_returns_none(monkeypatch):
    monkeypatch.setenv("AQSP_SMTP_HOST", "h")
    monkeypatch.setenv("AQSP_SMTP_PORT", "1")
    monkeypatch.setenv("AQSP_SMTP_USER", "u")
    monkeypatch.setenv("AQSP_SMTP_PASSWORD", "p")
    monkeypatch.setenv("AQSP_EMAIL_FROM", "f@x.com")
    monkeypatch.setenv("AQSP_EMAIL_TO", "  ,  ")
    assert load_email_config_from_env() is None


def test_send_briefing_email_calls_smtp(monkeypatch):
    cfg = EmailConfig(
        smtp_host="h",
        smtp_port=587,
        smtp_user="u",
        smtp_password="p",
        from_addr="f@x.com",
        to_addrs=["t@x.com"],
        use_tls=True,
    )
    fake_server = MagicMock()
    with patch(
        "aqsp.briefing.email_notifier.smtplib.SMTP", return_value=fake_server
    ) as smtp_class:
        ok = send_briefing_email(cfg, "subj", "body")
    assert ok is True
    smtp_class.assert_called_once_with("h", 587, timeout=30)
    fake_server.starttls.assert_called_once()
    fake_server.login.assert_called_once_with("u", "p")
    fake_server.send_message.assert_called_once()


def test_send_briefing_email_returns_false_on_exception(monkeypatch):
    cfg = EmailConfig(
        smtp_host="h",
        smtp_port=587,
        smtp_user="u",
        smtp_password="p",
        from_addr="f@x.com",
        to_addrs=["t@x.com"],
        use_tls=True,
    )
    with patch(
        "aqsp.briefing.email_notifier.smtplib.SMTP",
        side_effect=ConnectionError("network"),
    ):
        ok = send_briefing_email(cfg, "subj", "body")
    assert ok is False
