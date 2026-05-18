import ssl
import smtplib
import asyncio
import logging
from email.message import EmailMessage
from typing import Optional, Sequence

from kdcube_ai_app.apps.chat.sdk.config import get_settings, get_secret, read_plain, _plain_or_settings

logger = logging.getLogger(__name__)


def _smtp_settings() -> dict:
    user = _plain_or_settings("notifications.email.user", "EMAIL_USER")
    return {
        "host": _plain_or_settings("notifications.email.host", "EMAIL_HOST"),
        "port": int(_plain_or_settings("notifications.email.port", "EMAIL_PORT", 587) or 587),
        "user": user,
        "password": get_secret("services.email.password"),
        "from_addr": _plain_or_settings("notifications.email.from", "EMAIL_FROM") or user,
        "to_default": _plain_or_settings("notifications.email.to", "EMAIL_TO", "ops@example.com"),
        "use_tls": bool(_plain_or_settings("notifications.email.use_tls", "EMAIL_USE_TLS", True)),
        "enabled": bool(_plain_or_settings("notifications.email.enabled", "EMAIL_ENABLED", True)),
    }


def _send_email_sync(
    *,
    to_addrs: Sequence[str],
    subject: str,
    body: str,
    cc: Optional[Sequence[str]] = None,
) -> bool:
    cfg = _smtp_settings()
    if not cfg["enabled"]:
        logger.info("Email disabled (notifications.email.enabled=false). Skipping send.")
        return False
    if not cfg["host"]:
        logger.warning("notifications.email.host not configured; skipping email send.")
        return False
    if not cfg["from_addr"]:
        logger.warning("notifications.email.from or notifications.email.user not configured; skipping email send.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(to_addrs)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content(body)

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
            if cfg["use_tls"]:
                server.starttls(context=context)
            if cfg["user"] and cfg["password"]:
                server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("Failed to send email")
        return False


async def send_admin_email(
    *,
    subject: str,
    body: str,
    to_addrs: Optional[Sequence[str]] = None,
    cc: Optional[Sequence[str]] = None,
) -> bool:
    cfg = _smtp_settings()
    to_list = list(to_addrs) if to_addrs else [cfg["to_default"]]
    return await asyncio.to_thread(
        _send_email_sync,
        to_addrs=to_list,
        subject=subject,
        body=body,
        cc=cc,
    )
