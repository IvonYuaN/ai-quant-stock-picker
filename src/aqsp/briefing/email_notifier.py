"""增强版邮件通知 - 富文本HTML + 摘要 + 优先级标记。

替代原 email_notifier.py 的纯文本markdown发送。
"""

from __future__ import annotations

import os
import smtplib
import time
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
import logging
from pathlib import Path

from aqsp.briefing.schema import BriefingData, Pick
from aqsp.presentation import normalize_research_tone

_logger = logging.getLogger(__name__)


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
    """从环境变量加载邮件配置（密码只从环境变量读，不持久化）。"""
    host = os.environ.get("AQSP_SMTP_HOST")
    port = os.environ.get("AQSP_SMTP_PORT")
    user = os.environ.get("AQSP_SMTP_USER")
    password = os.environ.get("AQSP_SMTP_PASSWORD")
    from_addr = os.environ.get("AQSP_EMAIL_FROM")
    to_raw = os.environ.get("AQSP_EMAIL_TO", "")
    if not all([host, port, user, password, from_addr, to_raw]):
        return None
    try:
        smtp_port = int(port)
    except (TypeError, ValueError):
        return None
    to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
    if not to_addrs:
        return None
    return EmailConfig(
        smtp_host=host,
        smtp_port=smtp_port,
        smtp_user=user,
        smtp_password=password,
        from_addr=from_addr,
        to_addrs=to_addrs,
    )


def _render_pick_card_html(pick: Pick, rank: int) -> str:
    """生成单个候选标的的HTML卡片，带优先级颜色。"""
    # 根据评分定优先级颜色
    if pick.score >= 75:
        color = "#16a34a"  # green-600 强信号
        badge = "🔥 重点跟踪"
    elif pick.score >= 60:
        color = "#0891b2"  # cyan-600 纸面观察
        badge = "✅ 继续观察"
    else:
        color = "#94a3b8"  # slate-400 观察
        badge = "👀 观察"

    reasons_html = "".join(
        f'<li style="margin: 4px 0;">{escape(normalize_research_tone(r))}</li>'
        for r in pick.reasons[:4]
    )
    risks_html = ""
    if pick.risks:
        risks_text = escape(
            normalize_research_tone(", ".join(str(risk) for risk in pick.risks[:2]))
        )
        risks_html = (
            '<div style="margin-top: 8px; padding: 6px 10px; background: #fef3c7; border-left: 3px solid #f59e0b; border-radius: 4px;">'
            "<strong style='color: #92400e;'>⚠️ 风险:</strong> "
            f"{risks_text}"
            "</div>"
        )
    display_name = escape(normalize_research_tone(f"{pick.symbol} {pick.name}".strip()))
    safe_badge = escape(badge)

    return f"""
    <div style="margin: 12px 0; padding: 14px; border-radius: 8px; border: 1px solid #e5e7eb; background: #ffffff; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
        <div>
          <span style="display: inline-block; padding: 2px 8px; background: {color}; color: white; border-radius: 4px; font-size: 11px; font-weight: bold;">#{rank} {safe_badge}</span>
          <strong style="margin-left: 8px; font-size: 16px;">{display_name}</strong>
        </div>
        <div style="font-size: 18px; font-weight: bold; color: {color};">{pick.score:.1f}</div>
      </div>
      <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 8px 0; font-size: 13px; color: #475569;">
        <div>💰 参考价: <strong>{pick.ideal_buy:.2f}</strong></div>
        <div>🛡️ 最多亏到: <strong>{pick.stop_loss:.2f}</strong></div>
        <div>🎯 先看目标: <strong>{pick.take_profit:.2f}</strong></div>
      </div>
      <div style="font-size: 13px; color: #334155;">
        <strong>📊 研究依据:</strong>
        <ul style="margin: 4px 0; padding-left: 20px;">{reasons_html}</ul>
      </div>
      {risks_html}
    </div>
    """


def render_html_email(data: BriefingData) -> str:
    """从结构化BriefingData渲染HTML邮件。"""
    # 顶部摘要
    tradable_count = len(data.tradable_picks)
    candidate_count = data.candidate_count

    # 状态徽章颜色
    if data.has_protection:
        status_color = "#dc2626"
        status_text = "🛡️ 组合保护中"
    elif tradable_count > 0:
        status_color = "#16a34a"
        status_text = f"✅ {tradable_count} 只重点跟踪"
    else:
        status_color = "#64748b"
        status_text = "👀 仅观察"

    # 头部摘要卡片
    header = f"""
    <div style="background: linear-gradient(135deg, #1e293b 0%, #334155 100%); padding: 20px; border-radius: 12px; color: white; margin-bottom: 16px;">
      <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
        <div>
          <h1 style="margin: 0; font-size: 20px;">📊 每日研究复盘</h1>
          <p style="margin: 4px 0 0; font-size: 13px; opacity: 0.8;">{escape(data.date)}</p>
        </div>
        <span style="background: {status_color}; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: bold;">{escape(status_text)}</span>
      </div>
      <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 12px;">
        <div style="background: rgba(255,255,255,0.1); padding: 10px; border-radius: 6px; text-align: center;">
          <div style="font-size: 22px; font-weight: bold;">{candidate_count}</div>
          <div style="font-size: 11px; opacity: 0.8;">候选标的</div>
        </div>
        <div style="background: rgba(255,255,255,0.1); padding: 10px; border-radius: 6px; text-align: center;">
          <div style="font-size: 22px; font-weight: bold;">{tradable_count}</div>
          <div style="font-size: 11px; opacity: 0.8;">重点跟踪</div>
        </div>
        <div style="background: rgba(255,255,255,0.1); padding: 10px; border-radius: 6px; text-align: center;">
          <div style="font-size: 22px; font-weight: bold;">{len(data.risk_points)}</div>
          <div style="font-size: 11px; opacity: 0.8;">风险提示</div>
        </div>
      </div>
    </div>
    """

    # 市场态势
    regime_html = f"""
    <div style="margin: 12px 0; padding: 12px; background: #f1f5f9; border-radius: 8px; border-left: 4px solid #0ea5e9;">
      <strong>🌐 市场态势:</strong> {escape(normalize_research_tone(data.regime_info.description))}
    </div>
    """

    # 风险警告区
    risk_html = ""
    if data.risk_points:
        risks_list = "".join(
            f'<li style="margin: 4px 0;">{escape(normalize_research_tone(r))}</li>'
            for r in data.risk_points[:3]
        )
        risk_html = f"""
        <div style="margin: 12px 0; padding: 12px; background: #fef2f2; border-radius: 8px; border-left: 4px solid #dc2626;">
          <strong style="color: #dc2626;">⚠️ 风险提示</strong>
          <ul style="margin: 6px 0; padding-left: 20px; color: #7f1d1d;">{risks_list}</ul>
        </div>
        """

    # TOP3 重点推荐
    top_picks = data.tradable_picks[:3] if data.tradable_picks else data.top_picks
    if top_picks:
        picks_html = (
            "<h2 style='font-size: 18px; margin: 16px 0 8px;'>🎯 TOP 3 首先关注</h2>"
        )
        for i, p in enumerate(top_picks, 1):
            picks_html += _render_pick_card_html(p, i)
    else:
        picks_html = """
        <div style="padding: 16px; background: #f1f5f9; border-radius: 8px; text-align: center; color: #64748b;">
          今日暂无重点跟踪对象，继续等待信号
        </div>
        """

    # 完整候选列表（折叠/简略版）
    if len(data.picks) > 3:
        more_picks = "".join(
            "<li style='margin: 4px 0;'>"
            f"<strong>{escape(normalize_research_tone(f'{p.symbol} {p.name}'.strip()))}</strong>"
            f" - {p.score:.1f}分 - {escape(normalize_research_tone(', '.join(p.strategies)))}"
            "</li>"
            for p in data.picks[3:8]
        )
        more_html = f"""
        <details style="margin: 12px 0; padding: 10px; background: #f8fafc; border-radius: 6px;">
          <summary style="cursor: pointer; font-weight: bold; color: #475569;">📋 其他 {len(data.picks) - 3} 只候选（展开查看）</summary>
          <ul style="margin-top: 8px; padding-left: 20px; font-size: 13px;">{more_picks}</ul>
        </details>
        """
    else:
        more_html = ""

    # 数据源状态
    source_html = ""
    if data.source_status and data.source_status.is_degraded:
        source_html = f"""
        <div style="margin: 12px 0; padding: 10px; background: #fffbeb; border-radius: 6px; border-left: 3px solid #f59e0b; font-size: 13px;">
          📉 <strong>数据源降级:</strong> {escape(data.source_status.route)} ({escape(data.source_status.health_label)})
        </div>
        """

    # 底部免责
    footer = """
    <div style="margin-top: 24px; padding-top: 16px; border-top: 1px solid #e5e7eb; font-size: 11px; color: #94a3b8; text-align: center;">
      <p>⚠️ 仅供研究参考，不构成交易指令或投资建议。纸面跟踪结果需人工复核。</p>
      <p style="margin: 4px 0 0;">AI 量化选股系统 · 基于 walk-forward 双门验证</p>
    </div>
    """

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>每日研究复盘-{escape(data.date)}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 680px; margin: 0 auto; padding: 16px; background: #f8fafc; color: #1e293b; line-height: 1.6;">
  {header}
  {regime_html}
  {risk_html}
  {picks_html}
  {more_html}
  {source_html}
  {footer}
</body>
</html>
"""


def send_briefing_email(
    cfg: EmailConfig,
    subject: str,
    markdown_body: str,
    data: BriefingData | None = None,
    attachment_path: Path | None = None,
) -> bool:
    """发送邮件。如有 BriefingData，使用富文本HTML；否则降级到纯文本。"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.from_addr
    msg["To"] = ", ".join(cfg.to_addrs)

    # 纯文本部分（兼容性）
    msg.attach(MIMEText(markdown_body, "plain", "utf-8"))

    # HTML部分（如果有结构化数据）
    if data is not None:
        try:
            html_body = render_html_email(data)
            msg.attach(MIMEText(html_body, "html", "utf-8"))
        except Exception as e:  # noqa: BLE001
            # HTML 渲染失败不影响纯文本发送
            _logger.warning("HTML 渲染失败，降级到纯文本: %s", e)

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
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
            _logger.warning("邮件发送失败（第 %s/%s 次）: %s", attempt, max_attempts, e)
            if attempt == max_attempts:
                break
            time.sleep(min(2 ** (attempt - 1), 4))
    return False
