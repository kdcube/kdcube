import ssl
import smtplib
import asyncio
import logging
from email.message import EmailMessage
from typing import Optional, Sequence

from kdcube_ai_app.apps.chat.sdk.config import get_settings, get_secret

logger = logging.getLogger(__name__)


def _smtp_settings() -> dict:
    s = get_settings()
    return {
        "host": s.EMAIL_HOST,
        "port": s.EMAIL_PORT,
        "user": s.EMAIL_USER,
        "password": get_secret("services.email.password"),
        "from_addr": s.EMAIL_FROM or s.EMAIL_USER,
        "to_default": s.EMAIL_TO,
        "use_tls": s.EMAIL_USE_TLS,
        "enabled": s.EMAIL_ENABLED,
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
        logger.info("Email disabled (EMAIL_ENABLED=false). Skipping send.")
        return False
    if not cfg["host"]:
        logger.warning("EMAIL_HOST not configured; skipping email send.")
        return False
    if not cfg["from_addr"]:
        logger.warning("EMAIL_FROM/EMAIL_USER not configured; skipping email send.")
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
