import os
import ssl
import smtplib
import asyncio
import logging
from email.message import EmailMessage
from typing import Optional, Sequence

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _smtp_settings() -> dict:
    return {
        "host": os.getenv("EMAIL_HOST"),
        "port": int(os.getenv("EMAIL_PORT") or 587),
        "user": os.getenv("EMAIL_USER"),
        "password": os.getenv("EMAIL_PASSWORD"),
        "from_addr": os.getenv("EMAIL_FROM") or os.getenv("EMAIL_USER"),
        "to_default": os.getenv("EMAIL_TO") or "lena@nestlogic.com",
        "use_tls": _env_bool("EMAIL_USE_TLS", True),
        "enabled": _env_bool("EMAIL_ENABLED", True),
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
