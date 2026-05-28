from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


@dataclass
class EmailConfig:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    from_addr: str
    to_addrs: list[str]
    use_tls: bool = True


def load_email_config_from_env() -> EmailConfig | None:
    host = os.environ.get("AQSP_SMTP_HOST")
    port = os.environ.get("AQSP_SMTP_PORT")
    user = os.environ.get("AQSP_SMTP_USER")
    password = os.environ.get("AQSP_SMTP_PASSWORD")
    from_addr = os.environ.get("AQSP_EMAIL_FROM")
    to_raw = os.environ.get("AQSP_EMAIL_TO", "")
    if not all([host, port, user, password, from_addr, to_raw]):
        return None
    to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
    if not to_addrs:
        return None
    return EmailConfig(
        smtp_host=host,
        smtp_port=int(port),
        smtp_user=user,
        smtp_password=password,
        from_addr=from_addr,
        to_addrs=to_addrs,
    )


def send_briefing_email(
    cfg: EmailConfig,
    subject: str,
    markdown_body: str,
    attachment_path: Path | None = None,
) -> bool:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.from_addr
    msg["To"] = ", ".join(cfg.to_addrs)
    msg.attach(MIMEText(markdown_body, "plain", "utf-8"))
    try:
        if cfg.use_tls:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=30)
        server.login(cfg.smtp_user, cfg.smtp_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"邮件发送失败: {e}")
        return False
