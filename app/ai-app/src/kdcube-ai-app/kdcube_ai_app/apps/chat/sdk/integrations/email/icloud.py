from __future__ import annotations

import asyncio
import base64
import email.utils
import imaplib
import logging
import re
import smtplib
import ssl
import urllib.parse
from datetime import datetime, timezone
from email import policy as email_policy
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from typing import Any, Dict, Iterable, Mapping, Optional


ICLOUD_IMAP_HOST = "imap.mail.me.com"
ICLOUD_IMAP_PORT = 993
ICLOUD_SMTP_HOST = "smtp.mail.me.com"
ICLOUD_SMTP_PORT = 587

log = logging.getLogger("kdcube.integrations.email.icloud")


def default_icloud_account_settings() -> Dict[str, Any]:
    return {
        "imap_host": ICLOUD_IMAP_HOST,
        "imap_port": ICLOUD_IMAP_PORT,
        "imap_ssl": True,
        "smtp_host": ICLOUD_SMTP_HOST,
        "smtp_port": ICLOUD_SMTP_PORT,
        "smtp_starttls": True,
    }


def _setting(account: Mapping[str, Any], key: str, default: Any) -> Any:
    settings = account.get("settings") if isinstance(account.get("settings"), Mapping) else {}
    return settings.get(key, default)


def _decode_header_value(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return raw


def _quote(value: str) -> str:
    cleaned = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{cleaned}"'


def _imap_date(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value[:10], fmt)
            return parsed.strftime("%d-%b-%Y")
        except Exception:
            pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%d-%b-%Y")
    except Exception:
        return value[:20]


def _extract_filter(pattern: str, query: str) -> tuple[str, str]:
    match = re.search(pattern, query, flags=re.IGNORECASE)
    if not match:
        return "", query
    value = str(match.group(1) or match.group(2) or "").strip()
    return value, (query[: match.start()] + " " + query[match.end() :]).strip()


def _imap_search_criteria(
    *,
    query: str,
    unread_only: bool,
    from_email: str = "",
    to_email: str = "",
    subject: str = "",
    since: str = "",
    before: str = "",
    text: str = "",
) -> list[str]:
    remaining = str(query or "").strip()
    parsed_from, remaining = _extract_filter(r"\bfrom:(?:\"([^\"]+)\"|(\S+))", remaining)
    parsed_to, remaining = _extract_filter(r"\bto:(?:\"([^\"]+)\"|(\S+))", remaining)
    parsed_subject, remaining = _extract_filter(r"\bsubject:(?:\"([^\"]+)\"|(\S+))", remaining)
    parsed_after, remaining = _extract_filter(r"\b(?:after|since):(?:\"([^\"]+)\"|(\S+))", remaining)
    parsed_before, remaining = _extract_filter(r"\bbefore:(?:\"([^\"]+)\"|(\S+))", remaining)

    criteria: list[str] = ["UNSEEN" if unread_only else "ALL"]
    sender = str(from_email or parsed_from or "").strip()
    recipient = str(to_email or parsed_to or "").strip()
    subject_value = str(subject or parsed_subject or "").strip()
    since_value = str(since or parsed_after or "").strip()
    before_value = str(before or parsed_before or "").strip()
    text_value = str(text or remaining or "").strip()
    if sender:
        criteria.extend(["FROM", _quote(sender)])
    if recipient:
        criteria.extend(["TO", _quote(recipient)])
    if subject_value:
        criteria.extend(["SUBJECT", _quote(subject_value)])
    if since_value:
        criteria.extend(["SINCE", _imap_date(since_value)])
    if before_value:
        criteria.extend(["BEFORE", _imap_date(before_value)])
    if text_value:
        criteria.extend(["TEXT", _quote(text_value[:200])])
    return criteria


def _message_id(*, mailbox: str, uid: str) -> str:
    encoded_mailbox = base64.urlsafe_b64encode(str(mailbox or "INBOX").encode("utf-8")).decode("ascii").rstrip("=")
    return f"imap:{encoded_mailbox}:{str(uid or '').strip()}"


def _parse_message_id(raw: str, *, default_mailbox: str) -> tuple[str, str]:
    value = str(raw or "").strip()
    if value.startswith("imap:"):
        _, encoded_mailbox, uid = value.split(":", 2)
        padded = encoded_mailbox + ("=" * (-len(encoded_mailbox) % 4))
        mailbox = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        return mailbox or default_mailbox or "INBOX", uid
    return default_mailbox or "INBOX", value


def _imap_credentials(store: Any, account: Mapping[str, Any], tokens: Mapping[str, Any]) -> Dict[str, Any]:
    username = str(tokens.get("username") or account.get("email") or "").strip()
    password = str(tokens.get("password") or tokens.get("app_password") or "").strip()
    if not username or not password:
        return {
            "ok": False,
            "error": {
                "code": "icloud_account_not_connected",
                "message": "iCloud account is missing username or app-specific password.",
                "category": "user_action_required",
                "user_action_required": True,
                "provider": "icloud",
            },
        }
    return {
        "ok": True,
        "username": username,
        "password": password,
        "imap_host": str(_setting(account, "imap_host", ICLOUD_IMAP_HOST) or ICLOUD_IMAP_HOST),
        "imap_port": int(_setting(account, "imap_port", ICLOUD_IMAP_PORT) or ICLOUD_IMAP_PORT),
        "smtp_host": str(_setting(account, "smtp_host", ICLOUD_SMTP_HOST) or ICLOUD_SMTP_HOST),
        "smtp_port": int(_setting(account, "smtp_port", ICLOUD_SMTP_PORT) or ICLOUD_SMTP_PORT),
        "smtp_starttls": bool(_setting(account, "smtp_starttls", True)),
    }


async def ensure_icloud_credentials(*, store: Any, account: Mapping[str, Any]) -> Dict[str, Any]:
    account_id = str(account.get("account_id") or "").strip()
    tokens = await store.get_tokens_async(account_id)
    return _imap_credentials(store, account, tokens)


def _connect_imap(creds: Mapping[str, Any]) -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(
        str(creds.get("imap_host") or ICLOUD_IMAP_HOST),
        int(creds.get("imap_port") or ICLOUD_IMAP_PORT),
        ssl_context=ssl.create_default_context(),
    )
    conn.login(str(creds.get("username") or ""), str(creds.get("password") or ""))
    return conn


def _fetch_bytes(data: Iterable[Any]) -> bytes:
    chunks: list[bytes] = []
    for item in data or []:
        if isinstance(item, tuple):
            for child in item:
                if isinstance(child, bytes):
                    chunks.append(child)
        elif isinstance(item, bytes) and item not in {b")"}:
            chunks.append(item)
    return b"\r\n".join(chunks)


def _fetch_payload_bytes(data: Iterable[Any]) -> bytes:
    chunks: list[bytes] = []
    for item in data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            chunks.append(item[1])
        elif isinstance(item, bytes) and item not in {b")"}:
            chunks.append(item)
    return b"\r\n".join(chunks)


def _parse_internal_date_ms(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="ignore")
    match = re.search(r'INTERNALDATE "([^"]+)"', text)
    if not match:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(match.group(1))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return str(int(parsed.timestamp() * 1000))
    except Exception:
        return ""


def _plain_text_from_message(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            if part.get_content_disposition() == "attachment":
                continue
            if str(part.get_content_type()).lower() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        for part in msg.walk():
            if part.is_multipart() or part.get_content_disposition() == "attachment":
                continue
            if str(part.get_content_type()).lower() == "text/html":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                html = payload.decode(charset, errors="replace")
                return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
        return ""
    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _attachment_metadata(msg: Message) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    for index, part in enumerate(msg.walk()):
        if part.is_multipart():
            continue
        filename = _decode_header_value(part.get_filename() or "")
        disposition = str(part.get_content_disposition() or "").lower()
        if not filename and disposition != "attachment":
            continue
        payload = part.get_payload(decode=True) or b""
        out.append(
            {
                "part_id": str(index),
                "attachment_id": f"part:{index}",
                "filename": filename or f"attachment-{index}",
                "mime_type": str(part.get_content_type() or "application/octet-stream"),
                "size_bytes": len(payload),
            }
        )
    return out


def _summary_from_headers(
    *,
    mailbox: str,
    uid: str,
    header_bytes: bytes,
    fetch_meta: bytes,
    size_bytes: int = 0,
) -> Dict[str, Any]:
    msg = BytesParser(policy=email_policy.default).parsebytes(header_bytes)
    row = {
        "message_id": _message_id(mailbox=mailbox, uid=uid),
        "provider_message_id": str(msg.get("Message-ID") or "").strip(),
        "thread_id": "",
        "from": _decode_header_value(msg.get("From") or ""),
        "to": _decode_header_value(msg.get("To") or ""),
        "cc": _decode_header_value(msg.get("Cc") or ""),
        "subject": _decode_header_value(msg.get("Subject") or ""),
        "date": _decode_header_value(msg.get("Date") or ""),
        "internal_date": _parse_internal_date_ms(fetch_meta),
        "snippet": "",
        "body_excerpt": "",
        "body_truncated": False,
        "label_ids": [mailbox],
        "size_estimate": int(size_bytes or 0),
        "attachments": [],
        "has_attachments": False,
    }
    if not row["internal_date"]:
        try:
            parsed = email.utils.parsedate_to_datetime(row["date"])
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            row["internal_date"] = str(int(parsed.timestamp() * 1000))
        except Exception:
            pass
    return row


def _message_summary(*, mailbox: str, uid: str, raw: bytes, body_limit: int) -> Dict[str, Any]:
    msg = BytesParser(policy=email_policy.default).parsebytes(raw)
    body = _plain_text_from_message(msg)
    attachments = _attachment_metadata(msg)
    limit = max(0, int(body_limit or 0))
    return {
        "message_id": _message_id(mailbox=mailbox, uid=uid),
        "provider_message_id": str(msg.get("Message-ID") or "").strip(),
        "thread_id": "",
        "from": _decode_header_value(msg.get("From") or ""),
        "to": _decode_header_value(msg.get("To") or ""),
        "cc": _decode_header_value(msg.get("Cc") or ""),
        "subject": _decode_header_value(msg.get("Subject") or ""),
        "date": _decode_header_value(msg.get("Date") or ""),
        "internal_date": "",
        "snippet": body[:280].replace("\n", " ").strip(),
        "body_excerpt": body[: min(limit or 4000, 4000)],
        "body_truncated": bool(limit and len(body) > limit),
        "body": body[:limit] if limit else body,
        "label_ids": [mailbox],
        "size_estimate": len(raw),
        "attachments": attachments,
        "has_attachments": bool(attachments),
    }


def _provider_error(exc: Exception, *, operation: str, account: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "icloud_provider_request_failed",
            "message": str(exc),
            "category": "provider_error",
            "user_action_required": False,
            "provider": "icloud",
            "operation": operation,
        },
        "account": account,
    }


def _search_sync(
    *,
    creds: Mapping[str, Any],
    account: Mapping[str, Any],
    mailbox: str,
    unread_only: bool,
    limit: int,
    query: str,
    from_email: str,
    to_email: str,
    subject: str,
    since: str,
    before: str,
    text: str,
) -> Dict[str, Any]:
    conn = _connect_imap(creds)
    try:
        mailbox_norm = str(mailbox or "INBOX").strip() or "INBOX"
        typ, _ = conn.select(mailbox_norm, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"Could not select iCloud mailbox {mailbox_norm!r}.")
        criteria = _imap_search_criteria(
            query=query,
            unread_only=unread_only,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            since=since,
            before=before,
            text=text,
        )
        typ, data = conn.uid("SEARCH", None, *criteria)
        if typ != "OK":
            raise RuntimeError("iCloud IMAP search failed.")
        uids = [item.decode("ascii") for item in (data[0].split() if data and data[0] else [])]
        selected_uids = list(reversed(uids))[: max(1, min(int(limit or 20), 50))]
        rows: list[Dict[str, Any]] = []
        for uid in selected_uids:
            typ, fetched = conn.uid(
                "FETCH",
                uid,
                "(INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM TO CC SUBJECT DATE)])",
            )
            if typ != "OK":
                continue
            header_bytes = _fetch_payload_bytes(fetched)
            meta = b" ".join(item[0] for item in fetched if isinstance(item, tuple) and isinstance(item[0], bytes))
            size_match = re.search(rb"RFC822\.SIZE\s+(\d+)", meta)
            size_bytes = int(size_match.group(1)) if size_match else 0
            rows.append(_summary_from_headers(mailbox=mailbox_norm, uid=uid, header_bytes=header_bytes, fetch_meta=meta, size_bytes=size_bytes))
        return {
            "ok": True,
            "messages": rows,
            "result_size_estimate": len(uids),
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass


async def fetch_icloud_messages(
    *,
    store: Any,
    account: Mapping[str, Any],
    mailbox: str = "",
    unread_only: bool = True,
    limit: int = 20,
    query: str = "",
    gmail_query: str = "",
    from_email: str = "",
    to_email: str = "",
    subject: str = "",
    since: str = "",
    before: str = "",
    text: str = "",
    **_: Any,
) -> Dict[str, Any]:
    creds = await ensure_icloud_credentials(store=store, account=account)
    if not creds.get("ok"):
        return creds
    try:
        return await asyncio.to_thread(
            _search_sync,
            creds=creds,
            account=account,
            mailbox=mailbox or "INBOX",
            unread_only=unread_only,
            limit=limit,
            query=str(query or gmail_query or ""),
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            since=since,
            before=before,
            text=text,
        )
    except Exception as exc:
        log.warning("[email.icloud] search failed account=%s error=%s", account.get("email") or account.get("account_id"), exc)
        return _provider_error(exc, operation="imap_search", account=account)


def _fetch_message_sync(*, creds: Mapping[str, Any], mailbox: str, uid: str, body_limit: int) -> Dict[str, Any]:
    conn = _connect_imap(creds)
    try:
        typ, _ = conn.select(mailbox, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"Could not select iCloud mailbox {mailbox!r}.")
        typ, data = conn.uid("FETCH", uid, "(RFC822)")
        if typ != "OK":
            raise RuntimeError("iCloud IMAP message fetch failed.")
        raw = _fetch_payload_bytes(data)
        if not raw:
            raise RuntimeError("iCloud IMAP returned an empty message payload.")
        return {"ok": True, "message": _message_summary(mailbox=mailbox, uid=uid, raw=raw, body_limit=body_limit)}
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass


async def fetch_icloud_message(
    *,
    store: Any,
    account: Mapping[str, Any],
    message_id: str,
    body_limit: int = 20000,
    mailbox: str = "",
    **_: Any,
) -> Dict[str, Any]:
    wanted = str(message_id or "").strip()
    if not wanted:
        return {"ok": False, "error": {"code": "email_message_id_required", "message": "message_id is required."}}
    creds = await ensure_icloud_credentials(store=store, account=account)
    if not creds.get("ok"):
        return creds
    try:
        mailbox_norm, uid = _parse_message_id(wanted, default_mailbox=mailbox or "INBOX")
        return await asyncio.to_thread(_fetch_message_sync, creds=creds, mailbox=mailbox_norm, uid=uid, body_limit=body_limit)
    except Exception as exc:
        return _provider_error(exc, operation="imap_message_fetch", account=account)


def _fetch_attachment_sync(*, creds: Mapping[str, Any], mailbox: str, uid: str, attachment_id: str, max_bytes: int) -> Dict[str, Any]:
    raw_message = None
    conn = _connect_imap(creds)
    try:
        conn.select(mailbox, readonly=True)
        typ, data = conn.uid("FETCH", uid, "(RFC822)")
        if typ != "OK":
            raise RuntimeError("iCloud IMAP attachment fetch failed.")
        raw_message = _fetch_payload_bytes(data)
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass
    msg = BytesParser(policy=email_policy.default).parsebytes(raw_message or b"")
    wanted_index = str(attachment_id or "").replace("part:", "", 1)
    for index, part in enumerate(msg.walk()):
        if str(index) != wanted_index or part.is_multipart():
            continue
        payload = part.get_payload(decode=True) or b""
        max_size = max(1, min(int(max_bytes or 0), 10 * 1024 * 1024))
        if len(payload) > max_size:
            return {
                "ok": False,
                "error": {
                    "code": "email_attachment_too_large",
                    "message": f"Attachment is {len(payload)} bytes, above the {max_size} byte MCP read limit.",
                    "size_bytes": len(payload),
                    "max_bytes": max_size,
                },
            }
        mime_type = str(part.get_content_type() or "application/octet-stream")
        text = ""
        if mime_type.startswith("text/") or mime_type in {"application/json", "application/xml"}:
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return {
            "ok": True,
            "message_id": _message_id(mailbox=mailbox, uid=uid),
            "attachment": {
                "part_id": str(index),
                "attachment_id": f"part:{index}",
                "filename": _decode_header_value(part.get_filename() or "") or f"attachment-{index}",
                "mime_type": mime_type,
                "size_bytes": len(payload),
            },
            "size_bytes": len(payload),
            "mime_type": mime_type,
            "filename": _decode_header_value(part.get_filename() or "") or f"attachment-{index}",
            "base64": base64.b64encode(payload).decode("ascii"),
            "text": text,
        }
    return {"ok": False, "error": {"code": "email_attachment_not_found", "message": "Attachment was not found on this message."}}


async def fetch_icloud_attachment(
    *,
    store: Any,
    account: Mapping[str, Any],
    message_id: str,
    attachment_id: str,
    max_bytes: int = 5 * 1024 * 1024,
    mailbox: str = "",
    **_: Any,
) -> Dict[str, Any]:
    creds = await ensure_icloud_credentials(store=store, account=account)
    if not creds.get("ok"):
        return creds
    try:
        mailbox_norm, uid = _parse_message_id(message_id, default_mailbox=mailbox or "INBOX")
        return await asyncio.to_thread(
            _fetch_attachment_sync,
            creds=creds,
            mailbox=mailbox_norm,
            uid=uid,
            attachment_id=attachment_id,
            max_bytes=max_bytes,
        )
    except Exception as exc:
        return _provider_error(exc, operation="imap_attachment_fetch", account=account)


def _smtp_send_sync(*, creds: Mapping[str, Any], msg: EmailMessage) -> Dict[str, Any]:
    recipients = [
        address
        for _, address in email.utils.getaddresses(
            [str(msg.get("To") or ""), str(msg.get("Cc") or ""), str(msg.get("Bcc") or "")]
        )
        if address
    ]
    if not recipients:
        raise RuntimeError("No SMTP recipients were provided.")
    if msg.get("Bcc"):
        del msg["Bcc"]
    with smtplib.SMTP(str(creds.get("smtp_host") or ICLOUD_SMTP_HOST), int(creds.get("smtp_port") or ICLOUD_SMTP_PORT), timeout=45) as smtp:
        if bool(creds.get("smtp_starttls", True)):
            smtp.starttls(context=ssl.create_default_context())
        smtp.login(str(creds.get("username") or ""), str(creds.get("password") or ""))
        refused = smtp.send_message(msg, from_addr=str(creds.get("username") or ""), to_addrs=recipients)
    return {"ok": True, "provider_message_id": str(msg.get("Message-ID") or ""), "refused": refused}


async def send_icloud_message(*, store: Any, account: Mapping[str, Any], msg: EmailMessage) -> Dict[str, Any]:
    creds = await ensure_icloud_credentials(store=store, account=account)
    if not creds.get("ok"):
        return creds
    try:
        return await asyncio.to_thread(_smtp_send_sync, creds=creds, msg=msg)
    except Exception as exc:
        return _provider_error(exc, operation="smtp_send", account=account)
