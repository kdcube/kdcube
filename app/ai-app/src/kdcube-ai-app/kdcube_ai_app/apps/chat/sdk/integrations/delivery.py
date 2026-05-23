from __future__ import annotations

import base64
import json
import logging
import mimetypes
import pathlib
import urllib.parse
from typing import Any, Dict, Iterable, Mapping

import httpx

from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.apps.chat.sdk.integrations.email import send_icloud_message
from kdcube_ai_app.apps.chat.sdk.integrations.email.accounts import (
    GOOGLE_GMAIL_API,
    BUNDLE_ID as DEFAULT_EMAIL_BUNDLE_ID,
    EmailAccountStore,
    ProviderHttpError,
    _google_error_payload,
    _parse_json_object,
    ensure_google_access_token,
)
from kdcube_ai_app.apps.chat.sdk.integrations.email.delivery import (
    build_email_message as _build_email_message,
    email_html_to_text as _email_html_to_text,
    inline_markdown_to_email_html as _email_inline_markdown_to_html,
    markdown_to_email_html as _markdown_to_email_html,
    safe_email_filename as _safe_filename,
    split_email_addresses as _split_addresses,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    TelegramMessage,
    _file_item_bytes,
    _file_item_filename,
    _markdown_to_telegram_html,
    send_telegram_messages,
    TelegramUserAdminStorage,
)


GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
GMAIL_FULL_MAIL_SCOPE = "https://mail.google.com/"
EMAIL_ATTACHMENT_MAX_BYTES = 18 * 1024 * 1024
EMAIL_ATTACHMENT_TOTAL_MAX_BYTES = 22 * 1024 * 1024
log = logging.getLogger(__name__)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")



def _select_account(accounts: Iterable[Mapping[str, Any]], wanted: str) -> dict[str, Any] | None:
    wanted_norm = str(wanted or "").strip().lower()
    rows = [dict(item) for item in accounts if isinstance(item, Mapping)]
    if wanted_norm:
        for item in rows:
            if (
                str(item.get("account_id") or "").strip().lower() == wanted_norm
                or str(item.get("email") or "").strip().lower() == wanted_norm
            ):
                return item
        return None
    return rows[0] if len(rows) == 1 else None


async def _telegram_bot_token(bundle_id: str = "") -> str:
    resolved_bundle_id = str(bundle_id or DEFAULT_EMAIL_BUNDLE_ID).strip() or DEFAULT_EMAIL_BUNDLE_ID
    return (
        await get_secret("b:integrations.telegram.bot_token")
        or await get_secret(f"bundles.{resolved_bundle_id}.secrets.integrations.telegram.bot_token")
        or ""
    )


def _telegram_recipient_for_user(storage_root: str | pathlib.Path, *, user_id: str) -> dict[str, Any] | None:
    target = str(user_id or "").strip()
    if not target:
        return None
    try:
        registry = TelegramUserAdminStorage(storage_root)
        rows = registry.list_users()
    except Exception:
        log.exception("[delivery.telegram] failed to read telegram registry user_id=%s", target)
        return None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        role = str(row.get("role") or "").strip().lower()
        if role not in {"registered", "admin"}:
            continue
        telegram_user_id = str(row.get("telegram_user_id") or "").strip()
        kdcube_user_id = str(row.get("kdcube_user_id") or "").strip()
        bundle_user_scope = kdcube_user_id or (f"telegram_{telegram_user_id}" if telegram_user_id else "")
        if bundle_user_scope != target:
            continue
        chat_id = str(row.get("telegram_chat_id") or telegram_user_id or "").strip()
        if not chat_id:
            return None
        return {
            "chat_id": chat_id,
            "telegram_user_id": telegram_user_id,
            "role": role,
            "conversation_id": str(row.get("conversation_id") or "").strip(),
        }
    return None


def _infer_telegram_chat_id(*, explicit: str, conversation_id: str) -> str:
    value = str(explicit or "").strip()
    if value:
        return value
    conv = str(conversation_id or "").strip()
    if conv.startswith("telegram_chat_"):
        return conv.removeprefix("telegram_chat_").strip()
    return ""



def _attachment_items(*, attachment_paths: str, attachments_json: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    raw_json = str(attachments_json or "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except Exception as exc:
            raise ValueError(f"attachments_json is not valid JSON: {exc}") from exc
        rows = parsed if isinstance(parsed, list) else [parsed]
        for row in rows:
            if isinstance(row, Mapping):
                items.append(dict(row))
            elif isinstance(row, str) and row.strip():
                items.append({"physical_path": row.strip()})
    for line in str(attachment_paths or "").splitlines():
        value = line.strip()
        if value:
            items.append({"physical_path": value})
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        normalized.append(_normalize_attachment_item(item, index=index))
    return normalized


def _attachment_resolution_roots() -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir

        roots.append(resolve_output_dir())
    except Exception:
        pass
    return roots


def _resolve_relative_attachment_path(path_value: str) -> str:
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.workspace import resolve_artifact_path
    except Exception:
        resolve_artifact_path = None

    for root in _attachment_resolution_roots():
        if resolve_artifact_path is not None:
            candidate = resolve_artifact_path(root, path_value, create_root=False)
            if candidate.is_file():
                return str(candidate)
        candidate = root / path_value
        if candidate.is_file():
            return str(candidate)
    return ""


def _normalize_attachment_item(item: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    out = dict(item)
    path_value = str(
        out.get("physical_path")
        or out.get("local_path")
        or out.get("path")
        or out.get("hosted_uri")
        or out.get("url")
        or out.get("key")
        or ""
    ).strip()
    if path_value:
        parsed = urllib.parse.urlparse(path_value)
        if not parsed.scheme and not pathlib.Path(path_value).is_absolute():
            physical_path = _resolve_relative_attachment_path(path_value)
            if physical_path:
                out["physical_path"] = physical_path
                out.setdefault("logical_path", path_value)
            else:
                out.setdefault("physical_path", path_value)
        else:
            out.setdefault("physical_path", path_value)
    filename = _safe_filename(
        str(out.get("filename") or out.get("name") or path_value or ""),
        fallback=f"attachment-{index + 1}.bin",
    )
    mime_type = str(out.get("mime_type") or out.get("mime") or "").strip()
    if not mime_type:
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    out["filename"] = filename
    out["mime_type"] = mime_type
    return out


async def _resolved_attachments(*, attachment_paths: str, attachments_json: str) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    total = 0
    for item in _attachment_items(attachment_paths=attachment_paths, attachments_json=attachments_json):
        data = await _file_item_bytes(item)
        if data is None:
            raise FileNotFoundError(
                f"Could not resolve attachment bytes for {item.get('filename') or item.get('physical_path') or item}"
            )
        if len(data) > EMAIL_ATTACHMENT_MAX_BYTES:
            raise ValueError(
                f"Attachment {item.get('filename') or '<unnamed>'} is {len(data)} bytes; "
                f"limit is {EMAIL_ATTACHMENT_MAX_BYTES} bytes."
            )
        total += len(data)
        if total > EMAIL_ATTACHMENT_TOTAL_MAX_BYTES:
            raise ValueError(
                f"Total attachment bytes are {total}; limit is {EMAIL_ATTACHMENT_TOTAL_MAX_BYTES} bytes."
            )
        filename = _safe_filename(str(item.get("filename") or _file_item_filename(item)))
        mime_type = str(item.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
        resolved.append(
            {
                "filename": filename,
                "mime_type": mime_type,
                "size_bytes": len(data),
                "data": data,
                "file_item": {key: value for key, value in item.items() if key != "base64"},
            }
        )
    return resolved


def _delivery_attachment_metadata(item: Mapping[str, Any], *, target: str) -> dict[str, Any]:
    file_item = item.get("file_item") if isinstance(item.get("file_item"), Mapping) else {}
    clean_file_item = {
        key: value
        for key, value in dict(file_item).items()
        if key not in {"base64", "data_base64", "content_base64"} and value not in ("", None)
    }
    logical_path = str(
        clean_file_item.get("logical_path")
        or clean_file_item.get("artifact_path")
        or ""
    ).strip()
    source_physical_path = str(
        clean_file_item.get("physical_path")
        or clean_file_item.get("local_path")
        or ""
    ).strip()
    hosted_uri = str(
        clean_file_item.get("hosted_uri")
        or clean_file_item.get("url")
        or clean_file_item.get("key")
        or clean_file_item.get("rn")
        or ""
    ).strip()
    return {
        key: value
        for key, value in {
            "kind": "file",
            "source": "delivery.send_report",
            "delivery_target": target,
            "filename": item.get("filename"),
            "mime_type": item.get("mime_type"),
            "size_bytes": item.get("size_bytes"),
            "logical_path": logical_path,
            "source_physical_path": source_physical_path,
            "hosted_uri": hosted_uri,
            "visibility": "user",
            "description": f"Delivered via {target}",
            "file_item": clean_file_item,
        }.items()
        if value not in ("", None, {})
    }



async def send_report_to_email(
    *,
    entrypoint: Any,
    storage_root: str | pathlib.Path,
    user_id: str,
    bundle_id: str,
    account: str,
    recipient_email: str,
    cc: str = "",
    bcc: str = "",
    subject: str,
    body_markdown: str,
    body_html: str = "",
    attachment_paths: str = "",
    attachments_json: str = "",
) -> dict[str, Any]:
    store = EmailAccountStore(storage_root, user_id=user_id, bundle_id=bundle_id)
    accounts = await store.list_accounts_async()
    selected = _select_account(accounts, account)
    if not selected:
        code = "email_account_not_found" if str(account or "").strip() else "email_account_required"
        return {
            "ok": False,
            "error": {
                "code": code,
                "message": "Choose the connected email account to send from.",
                "category": "user_action_required",
                "user_action_required": True,
                "available_accounts": [
                    {"account_id": row.get("account_id"), "email": row.get("email"), "provider": row.get("provider")}
                    for row in accounts
                ],
            },
        }
    provider = str(selected.get("provider") or "").strip().lower()
    if provider not in {"google", "icloud"}:
        return {
            "ok": False,
            "error": {
                "code": "email_provider_unsupported_for_send",
                "message": f"Sending reports is not implemented for provider {provider or 'unknown'}.",
                "category": "unsupported_provider",
                "user_action_required": False,
            },
            "account": selected,
        }
    recipients = _split_addresses(recipient_email) or _split_addresses(str(selected.get("email") or ""))
    if not recipients:
        return {
            "ok": False,
            "error": {
                "code": "email_recipient_required",
                "message": "recipient_email is required when the connected account email is unavailable.",
                "category": "user_action_required",
                "user_action_required": True,
            },
            "account": selected,
        }
    attachments = await _resolved_attachments(attachment_paths=attachment_paths, attachments_json=attachments_json)
    msg = _build_email_message(
        sender_email=str(selected.get("email") or "").strip(),
        sender_name=str(selected.get("display_name") or "").strip(),
        recipients=recipients,
        cc=_split_addresses(cc),
        bcc=_split_addresses(bcc),
        subject=subject,
        body_text=body_markdown,
        body_html=body_html,
        attachments=attachments,
    )
    if provider == "icloud":
        send_result = await send_icloud_message(store=store, account=selected, msg=msg)
        if not send_result.get("ok"):
            return send_result
        log.info(
            "[delivery.email] sent | user_id=%s provider=icloud account=%s recipients=%s attachments=%s",
            user_id,
            selected.get("email") or selected.get("account_id"),
            recipients,
            len(attachments),
        )
        return {
            "ok": True,
            "target": "email",
            "account": {
                "account_id": selected.get("account_id"),
                "email": selected.get("email"),
                "provider": selected.get("provider"),
            },
            "recipients": recipients,
            "cc": _split_addresses(cc),
            "bcc": _split_addresses(bcc),
            "subject": msg["Subject"],
            "attachments": [
                _delivery_attachment_metadata(item, target="email")
                for item in attachments
            ],
            "provider_response": send_result,
        }

    scopes = {str(item or "").strip() for item in (selected.get("scope") or [])}
    if not (GMAIL_SEND_SCOPE in scopes or GMAIL_COMPOSE_SCOPE in scopes or GMAIL_FULL_MAIL_SCOPE in scopes):
        return {
            "ok": False,
            "error": {
                "code": "google_scope_insufficient",
                "message": (
                    f"The connected account {selected.get('email') or selected.get('account_id')} is missing Gmail send scope "
                    f"{GMAIL_SEND_SCOPE}. Reconnect Gmail and grant the updated email send permission."
                ),
                "category": "user_action_required",
                "user_action_required": True,
                "provider": "google",
                "operation": "gmail_messages_send",
                "required_scope": GMAIL_SEND_SCOPE,
            },
            "account": selected,
        }
    token_result = await ensure_google_access_token(store=store, entrypoint=entrypoint, account=selected)
    if not token_result.get("ok"):
        return token_result
    access_token = str(token_result.get("access_token") or "").strip()
    raw = _b64url(msg.as_bytes())
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                f"{GOOGLE_GMAIL_API}/messages/send",
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"raw": raw},
            )
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "error": {
                "code": "google_provider_request_failed",
                "message": f"Gmail send request failed: {exc}",
                "category": "provider_error",
                "user_action_required": False,
                "provider": "google",
                "operation": "gmail_messages_send",
            },
            "account": selected,
        }
    if response.status_code >= 400:
        exc = ProviderHttpError(
            status=response.status_code,
            reason=str(response.reason_phrase or ""),
            body=response.text[:8000],
            parsed=_parse_json_object(response.text),
            url=f"{GOOGLE_GMAIL_API}/messages/send",
        )
        return _google_error_payload(exc, operation="gmail_messages_send", account=selected)
    payload = _parse_json_object(response.text)
    log.info(
        "[delivery.email] sent | user_id=%s account=%s recipients=%s attachments=%s gmail_message_id=%s",
        user_id,
        selected.get("email") or selected.get("account_id"),
        recipients,
        len(attachments),
        payload.get("id"),
    )
    return {
        "ok": True,
        "target": "email",
        "account": {
            "account_id": selected.get("account_id"),
            "email": selected.get("email"),
            "provider": selected.get("provider"),
        },
        "recipients": recipients,
        "cc": _split_addresses(cc),
        "bcc": _split_addresses(bcc),
        "subject": msg["Subject"],
        "attachments": [
            _delivery_attachment_metadata(item, target="email")
            for item in attachments
        ],
        "provider_response": payload,
    }


async def send_report_to_telegram(
    *,
    entrypoint: Any,
    storage_root: str | pathlib.Path,
    user_id: str,
    bundle_id: str = DEFAULT_EMAIL_BUNDLE_ID,
    conversation_id: str,
    telegram_chat_id: str,
    subject: str,
    body_markdown: str,
    attachment_paths: str = "",
    attachments_json: str = "",
) -> dict[str, Any]:
    del entrypoint
    chat_id = _infer_telegram_chat_id(explicit=telegram_chat_id, conversation_id=conversation_id)
    recipient = None
    if not chat_id:
        recipient = _telegram_recipient_for_user(storage_root, user_id=user_id)
        chat_id = str((recipient or {}).get("chat_id") or "").strip()
    if not chat_id:
        return {
            "ok": False,
            "error": {
                "code": "telegram_chat_unavailable",
                "message": "Telegram chat id is unavailable for this user. Provide telegram_chat_id or run from a Telegram conversation.",
                "category": "user_action_required",
                "user_action_required": True,
            },
        }
    attachments = await _resolved_attachments(attachment_paths=attachment_paths, attachments_json=attachments_json)
    text_parts = [part for part in (str(subject or "").strip(), str(body_markdown or "").strip()) if part]
    text = "\n\n".join(text_parts).strip() or "Report is ready."
    messages: list[TelegramMessage] = [
        TelegramMessage(kind="text", text=_markdown_to_telegram_html(text), parse_mode="HTML")
    ]
    for item in attachments:
        file_item = {
            "filename": item["filename"],
            "mime_type": item["mime_type"],
            "size_bytes": item["size_bytes"],
            "base64": base64.b64encode(item["data"]).decode("ascii"),
        }
        messages.append(
            TelegramMessage(
                kind="photo" if str(item["mime_type"]).lower().startswith("image/") else "document",
                text=_markdown_to_telegram_html(str(item["filename"])),
                files=(file_item,),
                parse_mode="HTML",
            )
        )
    delivery = await send_telegram_messages(
        bot_token=await _telegram_bot_token(bundle_id),
        chat_id=chat_id,
        messages=messages,
    )
    log.info(
        "[delivery.telegram] sent | user_id=%s chat_id=%s messages=%s attachments=%s ok=%s",
        user_id,
        chat_id,
        len(messages),
        len(attachments),
        delivery.get("ok") if isinstance(delivery, Mapping) else None,
    )
    return {
        "ok": bool(delivery.get("ok")),
        "target": "telegram",
        "chat_id": chat_id,
        "recipient": recipient or {},
        "message_count": len(messages),
        "attachments": [
            _delivery_attachment_metadata(item, target="telegram")
            for item in attachments
        ],
        "delivery": delivery,
        **({"error": delivery.get("error")} if not delivery.get("ok") else {}),
    }


async def send_report(
    *,
    entrypoint: Any,
    storage_root: str | pathlib.Path,
    user_id: str,
    bundle_id: str,
    conversation_id: str,
    delivery_target: str,
    subject: str,
    body_markdown: str,
    body_html: str = "",
    email_account: str = "",
    recipient_email: str = "",
    cc: str = "",
    bcc: str = "",
    telegram_chat_id: str = "",
    attachment_paths: str = "",
    attachments_json: str = "",
) -> dict[str, Any]:
    target = str(delivery_target or "email").strip().lower()
    if target not in {"email", "telegram", "both"}:
        return {
            "ok": False,
            "error": {
                "code": "delivery_target_invalid",
                "message": "delivery_target must be email, telegram, or both.",
                "category": "user_action_required",
                "user_action_required": True,
            },
        }
    results: list[dict[str, Any]] = []
    if target in {"email", "both"}:
        results.append(
            await send_report_to_email(
                entrypoint=entrypoint,
                storage_root=storage_root,
                user_id=user_id,
                bundle_id=bundle_id,
                account=email_account,
                recipient_email=recipient_email,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body_markdown=body_markdown,
                body_html=body_html,
                attachment_paths=attachment_paths,
                attachments_json=attachments_json,
            )
        )
    if target in {"telegram", "both"}:
        results.append(
            await send_report_to_telegram(
                entrypoint=entrypoint,
                storage_root=storage_root,
                user_id=user_id,
                bundle_id=bundle_id,
                conversation_id=conversation_id,
                telegram_chat_id=telegram_chat_id,
                subject=subject,
                body_markdown=body_markdown,
                attachment_paths=attachment_paths,
                attachments_json=attachments_json,
            )
        )
    ok = all(bool(item.get("ok")) for item in results)
    return {
        "ok": ok,
        "target": target,
        "deliveries": results,
        **({"error": {"code": "delivery_failed", "message": "One or more delivery targets failed."}} if not ok else {}),
    }
