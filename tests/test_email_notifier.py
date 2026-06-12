from __future__ import annotations

from unittest.mock import MagicMock, patch

from aqsp.briefing.email_notifier import (
    EmailConfig,
    load_email_config_from_env,
    render_html_email,
    send_briefing_email,
)
from aqsp.briefing.schema import BriefingData, Pick, RegimeInfo, SourceStatus


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


def test_load_email_config_invalid_port_returns_none(monkeypatch):
    monkeypatch.setenv("AQSP_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("AQSP_SMTP_PORT", "abc")
    monkeypatch.setenv("AQSP_SMTP_USER", "u")
    monkeypatch.setenv("AQSP_SMTP_PASSWORD", "p")
    monkeypatch.setenv("AQSP_EMAIL_FROM", "from@example.com")
    monkeypatch.setenv("AQSP_EMAIL_TO", "to@example.com")
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
    with (
        patch(
            "aqsp.briefing.email_notifier.smtplib.SMTP",
            side_effect=ConnectionError("network"),
        ) as smtp_class,
        patch("aqsp.briefing.email_notifier.time.sleep") as sleep_mock,
    ):
        ok = send_briefing_email(cfg, "subj", "body")
    assert ok is False
    assert smtp_class.call_count == 3
    assert sleep_mock.call_count == 2


def test_render_html_email_uses_research_language_and_escapes_dynamic_text() -> None:
    data = BriefingData(
        date="2026-06-09",
        picks=(
            Pick(
                symbol="600519<script>",
                name="贵州茅台<img src=x>",
                score=82.0,
                rating="buy_candidate",
                strategies=("trend_pullback",),
                reasons=("立即买入后等待下单<script>alert(1)</script>",),
                risks=("真实持仓暴露过高<img src=x>",),
                metrics={},
                date="2026-06-09",
                ideal_buy=1490.0,
                stop_loss=1450.0,
                take_profit=1600.0,
                position="half",
            ),
        ),
        regime_info=RegimeInfo(
            regime="stable_bull",
            description="稳定上涨，可执行主链回暖<script>",
            circuit_breaker_triggered=False,
            circuit_breaker_reason="",
        ),
        source_status=SourceStatus(
            requested_source="auto<script>",
            actual_source="eastmoney",
            freshness_tier="end_of_day",
            coverage_tier="history_core",
            health_label="degraded",
            health_message="fallback",
            fallback_used=True,
        ),
        research_summary=None,
        portfolio_summary=None,
    )

    html = render_html_email(data)

    for forbidden in (
        "强烈推荐",
        "推荐依据",
        "可执行",
        "入场:",
        "立即买入",
        "下单",
        "真实持仓",
    ):
        assert forbidden not in html
    assert "重点跟踪" in html
    assert "研究依据" in html
    assert "参考价" in html
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    assert "<img src=x>" not in html
