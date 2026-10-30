# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Telegram notify: send text and images to the caller's connected account.

No chat id lives in any descriptor and no per-app registry exists here. The
user links their Telegram account to their KDCube account once, through the
deployment bot's Mini App (its Connect tab embeds the Connection Hub widget),
and the hub stores the link as a connection edge
(``telegram:<telegram user id> -> platform user``). This module resolves that
edge for the AUTHENTICATED caller and uses its subject as the chat id (a
private chat's id IS the telegram user id once the user has opened the bot).

The bot is the deployment bot the workspace app runs on. This bundle's
integration row points ``secret_refs.bot_token`` at the Connection Hub
authenticator secret and declares NO webhook: one webhook exists per bot and
the workspace bundle owns it. This bundle only sends.

The image contract mirrors the Slack/LinkedIn named-services paradigm:

- the text operation refuses file-shaped keys (files ride the images op);
- images arrive as single-use ``staged:`` refs (bytes were PUT to a signed
  upload slot, never through a model context), as public ``url`` values, or
  as capped inline ``content_base64`` (last resort).
"""

from __future__ import annotations

import base64
import mimetypes
from typing import Any

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_operation
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connection_edges import (
    connection_hub_bundle_id,
)
from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import (
    delete_staged,
    load_staged,
    staging_root,
)
from kdcube_ai_app.apps.chat.sdk.integrations.integration_config import (
    integration_secret_value,
    select_integration,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram.bot import (
    TelegramMessage,
    send_telegram_messages,
)

DEEPLINK_BASE = "https://t.me/"
TELEGRAM_CAPTION_LIMIT = 1024
INLINE_IMAGE_MAX_BYTES = 10 * 1024 * 1024
MAX_IMAGES_PER_SEND = 10

# The Slack named service's exact refusal list: any of these on the text
# operation means the caller wanted the images operation.
_FILE_SHAPED_KEYS = (
    "attachment_path",
    "attachment_paths",
    "attachments",
    "file_path",
    "file_paths",
    "file_ref",
    "file_refs",
    "files",
    "images",
    "path",
    "staged_ref",
    "content_base64",
)


class TelegramNotConfigured(RuntimeError):
    """No enabled telegram integration row / no bot token resolvable."""


class TelegramNotConnected(RuntimeError):
    """The caller has no telegram connection edge in the hub."""


def telegram_identity_from_family(payload: Any) -> dict[str, Any] | None:
    """The linked Telegram identity out of an identity_family_resolve answer.

    Unwraps the operation envelope shapes the hub answers with and returns
    ``{"telegram_user_id", "username"}`` for the first Telegram identity, or
    None when the family carries no Telegram link.
    """
    candidates = [payload]
    if isinstance(payload, dict):
        candidates.extend([payload.get("identity_family_resolve"), payload.get("result")])
    identities: list[Any] = []
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("identities"), list):
            identities = candidate["identities"]
            break
    for identity in identities:
        if not isinstance(identity, dict):
            continue
        if str(identity.get("provider") or "").strip().lower() != "telegram":
            continue
        subject = str(identity.get("provider_subject") or "").strip()
        if not subject:
            continue
        return {
            "telegram_user_id": subject,
            "username": str(identity.get("label") or "").strip(),
        }
    return None


def bot_deeplink(definition: Any) -> str:
    """The t.me link to the deployment bot, or "" when no username is known."""
    if not isinstance(definition, dict):
        return ""
    username = str(definition.get("bot_username") or "").strip().lstrip("@")
    return f"{DEEPLINK_BASE}{username}" if username else ""


async def resolve_user_telegram(
    entrypoint: Any, *, user_id: str, tenant: str, project: str
) -> dict[str, Any] | None:
    """The user's linked Telegram identity, from the hub's connection edges.

    Returns None both when no link exists and when the hub is unreachable;
    the caller reports "not connected" either way, and the status operation
    is the place that explains what to do about it (the bot deep link)."""
    target = str(user_id or "").strip()
    if not target:
        return None
    try:
        result = await call_bundle_operation(
            bundle_id=connection_hub_bundle_id(entrypoint),
            operation="identity_family_resolve",
            data={"input_user_id": target, "platform_user_id": target},
            tenant=tenant,
            project=project,
            route="operations",
        )
    except Exception:
        return None
    return telegram_identity_from_family(result)


async def _bot_token(entrypoint: Any) -> tuple[str, dict[str, Any]]:
    """(token, integration definition) for the enabled telegram row, or raise."""
    integration = select_integration(entrypoint, provider="telegram")
    if not integration:
        raise TelegramNotConfigured("no enabled telegram integration row")
    definition = integration.get("definition") if isinstance(integration.get("definition"), dict) else {}
    token = await integration_secret_value(
        entrypoint,
        provider="telegram",
        field="bot_token",
        integration_id=str(integration.get("id") or ""),
    )
    token = str(token or "").strip()
    if not token:
        raise TelegramNotConfigured("telegram bot token is not configured")
    return token, definition


async def telegram_status(entrypoint: Any, *, identity: dict[str, Any]) -> dict[str, Any]:
    """Whether the caller can be messaged: integration, bot link, hub edge."""
    integration = select_integration(entrypoint, provider="telegram")
    definition = {}
    configured = False
    if integration:
        definition = integration.get("definition") if isinstance(integration.get("definition"), dict) else {}
        try:
            await _bot_token(entrypoint)
            configured = True
        except TelegramNotConfigured:
            configured = False
    telegram = await resolve_user_telegram(
        entrypoint,
        user_id=str(identity.get("user_id") or ""),
        tenant=str(identity.get("tenant") or ""),
        project=str(identity.get("project") or ""),
    )
    return {
        "ok": True,
        "user": str(identity.get("user_id") or ""),
        "integration_configured": configured,
        "connected": telegram is not None,
        "telegram": telegram or {},
        "bot": {
            "username": str(definition.get("bot_username") or "").strip().lstrip("@"),
            "link": bot_deeplink(definition),
        },
    }


async def _resolved_chat_or_raise(entrypoint: Any, identity: dict[str, Any]) -> str:
    telegram = await resolve_user_telegram(
        entrypoint,
        user_id=str(identity.get("user_id") or ""),
        tenant=str(identity.get("tenant") or ""),
        project=str(identity.get("project") or ""),
    )
    if not telegram:
        raise TelegramNotConnected(
            "no Telegram account is linked to this user in the Connection Hub"
        )
    return str(telegram["telegram_user_id"])


async def send_text(
    entrypoint: Any, *, identity: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """One text message to the caller's connected Telegram account.

    Text only, by contract: any file-shaped key is refused with a pointer to
    the images operation, the same rule the Slack named service enforces on
    ``post_message``."""
    payload = payload if isinstance(payload, dict) else {}
    offending = [key for key in _FILE_SHAPED_KEYS if payload.get(key)]
    if offending:
        return {
            "ok": False,
            "error": "telegram_send_carries_no_files",
            "message": (
                "telegram_send is text-only; use telegram_send_images for "
                f"images (rejected keys: {', '.join(sorted(offending))})"
            ),
        }
    text = str(payload.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "invalid_request", "message": "body.data.text is required"}
    parse_mode = str(payload.get("parse_mode") or "").strip()
    try:
        token, _definition = await _bot_token(entrypoint)
        chat_id = await _resolved_chat_or_raise(entrypoint, identity)
    except TelegramNotConfigured as exc:
        return {"ok": False, "error": "not_configured", "message": str(exc)}
    except TelegramNotConnected as exc:
        return {"ok": False, "error": "not_connected", "message": str(exc)}
    sent = await send_telegram_messages(
        bot_token=token,
        chat_id=chat_id,
        messages=[TelegramMessage(kind="text", text=text, parse_mode=parse_mode)],
    )
    return {"ok": bool(sent.get("ok")), "sent": sent.get("sent", 0), "error": sent.get("error", "")}


def _image_file_item(
    image: dict[str, Any], *, staging_base: Any, owner
) -> tuple[dict[str, Any], str | None]:
    """(file item for the Telegram sender, staged ref to consume on success).

    Lanes, in the named-services preference order: ``staged_ref`` (bytes were
    PUT to the signed slot), public ``url``, inline ``content_base64`` capped
    at INLINE_IMAGE_MAX_BYTES."""
    staged_ref = str(image.get("staged_ref") or "").strip()
    if staged_ref:
        filename, data = load_staged(staging_base, staged_ref, owner=owner)
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return (
            {
                "filename": filename,
                "mime_type": mime,
                "base64": base64.b64encode(data).decode("ascii"),
            },
            staged_ref,
        )
    url = str(image.get("url") or "").strip()
    if url:
        filename = str(image.get("filename") or "").strip() or url.rsplit("/", 1)[-1] or "image"
        mime = str(image.get("mime_type") or "").strip() or (
            mimetypes.guess_type(filename)[0] or "image/jpeg"
        )
        return ({"filename": filename, "mime_type": mime, "url": url}, None)
    inline = str(image.get("content_base64") or "").strip()
    if inline:
        raw = base64.b64decode(inline, validate=False)
        if len(raw) > INLINE_IMAGE_MAX_BYTES:
            raise ValueError(
                f"inline image exceeds {INLINE_IMAGE_MAX_BYTES} bytes; "
                "use telegram_request_upload and pass staged_ref"
            )
        filename = str(image.get("filename") or "").strip() or "image.png"
        mime = str(image.get("mime_type") or "").strip() or (
            mimetypes.guess_type(filename)[0] or "image/png"
        )
        return ({"filename": filename, "mime_type": mime, "base64": inline}, None)
    raise ValueError("each image needs staged_ref, url, or content_base64")


async def send_images(
    entrypoint: Any,
    *,
    identity: dict[str, Any],
    payload: dict[str, Any],
    storage_path: str,
) -> dict[str, Any]:
    """Images (with an optional caption) to the caller's connected account.

    ``body.data.images`` is a list of ``{staged_ref | url | content_base64,
    filename?, mime_type?, caption?}``; ``body.data.caption`` rides on the
    first image (Telegram's caption cap applies). A non-image mime is sent as
    a document rather than refused, so screenshots and PDFs share one door.
    Staged refs are single-use: consumed only after a successful send."""
    payload = payload if isinstance(payload, dict) else {}
    images = payload.get("images")
    if not isinstance(images, list) or not images:
        return {
            "ok": False,
            "error": "invalid_request",
            "message": "body.data.images must be a non-empty list",
        }
    if len(images) > MAX_IMAGES_PER_SEND:
        return {
            "ok": False,
            "error": "invalid_request",
            "message": f"at most {MAX_IMAGES_PER_SEND} images per send",
        }
    caption = str(payload.get("caption") or "").strip()[:TELEGRAM_CAPTION_LIMIT]
    staging_base = staging_root(storage_path)
    messages: list[TelegramMessage] = []
    consumed: list[str] = []
    try:
        for position, image in enumerate(images):
            image = image if isinstance(image, dict) else {}
            file_item, staged = _image_file_item(image, staging_base=staging_base, owner=identity)
            if staged:
                consumed.append(staged)
            mime = str(file_item.get("mime_type") or "")
            kind = "photo" if mime.startswith("image/") else "document"
            text = caption if position == 0 else str(image.get("caption") or "").strip()[:TELEGRAM_CAPTION_LIMIT]
            messages.append(TelegramMessage(kind=kind, text=text, files=(file_item,)))
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": "invalid_request", "message": str(exc)}
    try:
        token, _definition = await _bot_token(entrypoint)
        chat_id = await _resolved_chat_or_raise(entrypoint, identity)
    except TelegramNotConfigured as exc:
        return {"ok": False, "error": "not_configured", "message": str(exc)}
    except TelegramNotConnected as exc:
        return {"ok": False, "error": "not_connected", "message": str(exc)}
    sent = await send_telegram_messages(bot_token=token, chat_id=chat_id, messages=messages)
    if sent.get("ok"):
        for staged_ref in consumed:
            try:
                delete_staged(staging_base, staged_ref, owner=identity)
            except Exception:  # the sweep collects leftovers; a send is not failed by cleanup
                pass
    return {
        "ok": bool(sent.get("ok")),
        "sent": sent.get("sent", 0),
        "total": len(messages),
        "error": sent.get("error", ""),
    }
