from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import pathlib
import re
from typing import Any, Dict, Iterable, Mapping

from .accounts import (
    EmailAccountStore,
    fetch_email_attachment,
    fetch_email_message,
    fetch_email_messages,
)


MAX_ATTACHMENTS = 25
MAX_MESSAGES = 25
MAX_BYTES_PER_ATTACHMENT = 10 * 1024 * 1024
VISIBILITIES = {"external", "internal"}
logger = logging.getLogger("kdcube.integrations.email.attachments")


def _safe_segment(raw: str, *, fallback: str = "item") -> str:
    value = re.sub(r"[^A-Za-z0-9._@ -]+", "_", str(raw or "")).strip(" ._")
    return value or fallback


def _safe_filename(raw: str, *, fallback: str = "attachment.bin") -> str:
    name = pathlib.PurePosixPath(str(raw or "")).name
    return _safe_segment(name, fallback=fallback)


def _jsonish_list(value: Any) -> list[Any]:
    if value in ("", None):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    try:
        parsed = json.loads(str(value))
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    return []


def _string_list(value: Any) -> list[str]:
    if value in ("", None):
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        parsed = _jsonish_list(stripped)
        if parsed:
            return [str(item or "").strip() for item in parsed if str(item or "").strip()]
        return [item.strip() for item in stripped.split(",") if item.strip()]
    if isinstance(value, Iterable):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    return []


def _selection_map(selection_json: Any) -> dict[str, set[str]]:
    return {
        message_id: set(items)
        for message_id, items in _selection_detail_map(selection_json).items()
    }


def _selection_detail_map(selection_json: Any) -> dict[str, dict[str, Dict[str, Any]]]:
    details: dict[str, dict[str, Dict[str, Any]]] = {}
    for item in _jsonish_list(selection_json):
        if isinstance(item, str):
            continue
        if not isinstance(item, Mapping):
            continue
        message_id = str(item.get("message_id") or item.get("messageId") or "").strip()
        attachment_id = str(item.get("attachment_id") or item.get("attachmentId") or "").strip()
        if not message_id or not attachment_id:
            continue
        details.setdefault(message_id, {})[attachment_id] = {
            "attachment_id": attachment_id,
            "filename": str(item.get("filename") or item.get("name") or "").strip(),
            "mime_type": str(item.get("mime_type") or item.get("mime") or "").strip(),
            "size_bytes": int(item.get("size_bytes") or item.get("size") or 0),
            "part_id": str(item.get("part_id") or item.get("partId") or "").strip(),
        }
    return details


def _attachment_matches(
    attachment: Mapping[str, Any],
    *,
    filename_contains: str,
    mime_type_prefix: str,
) -> bool:
    filename_filter = str(filename_contains or "").strip().lower()
    if filename_filter and filename_filter not in str(attachment.get("filename") or "").lower():
        return False
    mime_filter = str(mime_type_prefix or "").strip().lower()
    if mime_filter and not str(attachment.get("mime_type") or "").strip().lower().startswith(mime_filter):
        return False
    return True


async def _select_accounts(
    *,
    store: EmailAccountStore,
    account: str,
) -> tuple[list[Dict[str, Any]], Dict[str, Any] | None]:
    connected = [item for item in await store.list_accounts_async() if item.get("status") == "connected"]
    if account:
        selected = await store.get_account_async(account)
        if selected is None:
            return [], {
                "code": "email_account_not_found",
                "message": f"Email account {account!r} was not found.",
                "accounts": connected,
            }
        return [selected], None
    if not connected:
        return [], {
            "code": "email_account_not_connected",
            "message": "No connected email account is available for this user scope.",
            "accounts": [],
        }
    return connected, None


def _account_summary(account: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "account_id": account.get("account_id"),
        "provider": account.get("provider"),
        "email": account.get("email"),
        "display_name": account.get("display_name"),
    }


def _message_sort_key(message: Mapping[str, Any]) -> int:
    raw = str(message.get("internal_date") or "").strip()
    if raw.isdigit():
        return int(raw)
    return 0


async def materialize_email_attachments_for_current_turn(
    *,
    entrypoint: Any,
    storage_root: str | pathlib.Path,
    outdir: str | pathlib.Path,
    turn_id: str,
    user_id: str,
    bundle_id: str,
    account: str = "",
    mailbox: str = "",
    unread_only: bool = False,
    limit: int = 10,
    search_query: str = "",
    message_ids_json: str = "",
    attachment_selection_json: str = "",
    from_email: str = "",
    to_email: str = "",
    subject: str = "",
    since: str = "",
    before: str = "",
    text: str = "",
    filename_contains: str = "",
    mime_type_prefix: str = "",
    max_attachments: int = MAX_ATTACHMENTS,
    max_bytes_per_attachment: int = MAX_BYTES_PER_ATTACHMENT,
    visibility: str = "external",
) -> Dict[str, Any]:
    turn = str(turn_id or "").strip()
    outdir_path = pathlib.Path(str(outdir or "")).resolve() if str(outdir or "").strip() else None
    if not turn or outdir_path is None:
        return {
            "ok": False,
            "error": {
                "code": "email_attachment_materialization_unavailable",
                "message": "Current React turn id or output directory is unavailable.",
                "category": "internal_runtime",
            },
        }
    visibility_norm = str(visibility or "external").strip().lower()
    if visibility_norm not in VISIBILITIES:
        return {
            "ok": False,
            "error": {
                "code": "email_attachment_invalid_visibility",
                "message": "visibility must be 'external' or 'internal'.",
                "category": "bad_request",
            },
        }

    store = EmailAccountStore(storage_root, user_id=user_id, bundle_id=bundle_id)
    selected_accounts, account_error = await _select_accounts(store=store, account=account)
    if account_error:
        return {
            "ok": False,
            "error": {
                "category": "user_action_required",
                "user_action_required": True,
                **{k: v for k, v in account_error.items() if k != "accounts"},
            },
            "accounts": account_error.get("accounts") or [],
        }

    selection_details = _selection_detail_map(attachment_selection_json)
    selection = {
        message_id: set(items)
        for message_id, items in selection_details.items()
    }
    requested_message_ids = _string_list(message_ids_json)
    for message_id in selection:
        if message_id not in requested_message_ids:
            requested_message_ids.append(message_id)

    max_messages = max(1, min(int(limit or 10), MAX_MESSAGES))
    max_files = max(1, min(int(max_attachments or MAX_ATTACHMENTS), MAX_ATTACHMENTS))
    max_bytes = max(1, min(int(max_bytes_per_attachment or MAX_BYTES_PER_ATTACHMENT), MAX_BYTES_PER_ATTACHMENT))
    provider_query = str(search_query or "").strip()

    files: list[Dict[str, Any]] = []
    errors: list[Dict[str, Any]] = []
    warnings: list[Dict[str, Any]] = []
    candidates: list[tuple[Dict[str, Any], str, int]] = []

    if requested_message_ids:
        for selected in selected_accounts:
            for message_id in requested_message_ids[:max_messages]:
                candidates.append((selected, message_id, 0))
    else:
        for selected in selected_accounts:
            provider = str(selected.get("provider") or "google").strip().lower()
            mailbox_norm = str(mailbox or ("INBOX" if provider == "icloud" else "inbox")).strip()
            searched = await fetch_email_messages(
                store=store,
                entrypoint=entrypoint,
                account=selected,
                mailbox=mailbox_norm,
                unread_only=unread_only,
                limit=max_messages,
                query=provider_query,
                gmail_query=provider_query,
                from_email=from_email,
                to_email=to_email,
                subject=subject,
                since=since,
                before=before,
                text=text,
            )
            if not searched.get("ok"):
                errors.append(
                    {
                        "account": str(selected.get("email") or selected.get("account_id") or ""),
                        "error": searched.get("error") or searched,
                    }
                )
                continue
            for item in searched.get("messages") or []:
                if not isinstance(item, Mapping):
                    continue
                message_id = str(item.get("message_id") or "").strip()
                if message_id:
                    candidates.append((selected, message_id, _message_sort_key(item)))
        candidates.sort(key=lambda item: item[2], reverse=True)

    candidate_limit = len(candidates) if requested_message_ids else max_messages
    for selected, message_id, _sort_key in candidates[:candidate_limit]:
        if len(files) >= max_files:
            break
        provider = str(selected.get("provider") or "google").strip().lower()
        mailbox_norm = str(mailbox or ("INBOX" if provider == "icloud" else "inbox")).strip()
        message = await fetch_email_message(
            store=store,
            entrypoint=entrypoint,
            account=selected,
            message_id=message_id,
            body_limit=1000,
            mailbox=mailbox_norm,
        )
        if not message.get("ok"):
            errors.append(
                {
                    "account": str(selected.get("email") or selected.get("account_id") or ""),
                    "message_id": message_id,
                    "error": message.get("error") or message,
                }
            )
            continue
        row = message.get("message") if isinstance(message.get("message"), Mapping) else {}
        attachments = [item for item in (row.get("attachments") or []) if isinstance(item, Mapping)]
        selected_attachment_ids = selection.get(message_id)
        if selected_attachment_ids:
            by_id = {
                str(item.get("attachment_id") or "").strip(): item
                for item in attachments
                if str(item.get("attachment_id") or "").strip()
            }
            available_preview = [
                {
                    "part_id": item.get("part_id"),
                    "filename": item.get("filename"),
                    "mime_type": item.get("mime_type"),
                    "attachment_id_prefix": str(item.get("attachment_id") or "")[:40],
                }
                for item in attachments[:10]
            ]
            selected_rows: list[Mapping[str, Any]] = []
            for attachment_id in selected_attachment_ids:
                if attachment_id in by_id:
                    selected_rows.append(by_id[attachment_id])
                    continue
                warning = {
                    "code": "email_attachment_metadata_mismatch",
                    "message": (
                        "Exact attachment id was requested, but refetched message metadata did not list it. "
                        "The tool will still try the provider attachment endpoint for the exact id."
                    ),
                    "account": str(selected.get("email") or selected.get("account_id") or ""),
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "subject": str(row.get("subject") or ""),
                    "available_attachment_count": len(attachments),
                    "available_attachments": available_preview,
                }
                warnings.append(warning)
                logger.warning(
                    "[email.attachments] exact attachment id missing from refetched metadata; trying direct fetch | "
                    "account=%s message_id=%s attachment_id=%s available=%s",
                    warning["account"],
                    message_id,
                    attachment_id[:80],
                    available_preview,
                )
                selected_rows.append(
                    {
                        "attachment_id": attachment_id,
                        "_metadata_mismatch": True,
                        **(selection_details.get(message_id, {}).get(attachment_id) or {}),
                    }
                )
            attachments = selected_rows
        else:
            attachments = [
                item for item in attachments
                if _attachment_matches(item, filename_contains=filename_contains, mime_type_prefix=mime_type_prefix)
            ]
        if not attachments:
            logger.warning(
                "[email.attachments] no matching attachments after message fetch | "
                "account=%s message_id=%s subject=%s selected_ids=%s filename_contains=%r "
                "mime_type_prefix=%r row_has_attachments=%s raw_attachment_count=%s raw_attachments=%s",
                str(selected.get("email") or selected.get("account_id") or ""),
                message_id,
                str(row.get("subject") or ""),
                sorted(selected_attachment_ids or []),
                filename_contains,
                mime_type_prefix,
                bool(row.get("has_attachments")),
                len(row.get("attachments") or []) if isinstance(row.get("attachments"), list) else 0,
                [
                    {
                        "part_id": item.get("part_id"),
                        "filename": item.get("filename"),
                        "mime_type": item.get("mime_type"),
                        "attachment_id_prefix": str(item.get("attachment_id") or "")[:40],
                    }
                    for item in (row.get("attachments") or [])[:10]
                    if isinstance(item, Mapping)
                ] if isinstance(row.get("attachments"), list) else [],
            )
            errors.append(
                {
                    "account": str(selected.get("email") or selected.get("account_id") or ""),
                    "message_id": message_id,
                    "error": {
                        "code": "email_attachment_no_matching_attachments",
                        "message": "Message had no attachments matching the filename/MIME filters.",
                        "subject": str(row.get("subject") or ""),
                    },
                }
            )
            continue
        for attachment in attachments:
            if len(files) >= max_files:
                break
            attachment_id = str(attachment.get("attachment_id") or "").strip()
            if not attachment_id:
                continue
            fetched = await fetch_email_attachment(
                store=store,
                entrypoint=entrypoint,
                account=selected,
                message_id=message_id,
                attachment_id=attachment_id,
                max_bytes=max_bytes,
                mailbox=mailbox_norm,
            )
            if not fetched.get("ok"):
                errors.append(
                    {
                        "account": str(selected.get("email") or selected.get("account_id") or ""),
                        "message_id": message_id,
                        "attachment_id": attachment_id,
                        "filename": attachment.get("filename") or "",
                        "metadata_mismatch": bool(attachment.get("_metadata_mismatch")),
                        "error": fetched.get("error") or fetched,
                    }
                )
                continue
            try:
                data = base64.b64decode(str(fetched.get("base64") or ""), validate=True)
            except Exception:
                errors.append(
                    {
                        "account": str(selected.get("email") or selected.get("account_id") or ""),
                        "message_id": message_id,
                        "attachment_id": attachment_id,
                        "filename": fetched.get("filename") or attachment.get("filename") or "",
                        "error": {"code": "email_attachment_decode_failed", "message": "Attachment payload was not valid base64."},
                    }
                )
                continue
            filename = _safe_filename(str(fetched.get("filename") or attachment.get("filename") or "attachment.bin"))
            account_key = _safe_segment(str(selected.get("email") or selected.get("account_id") or "email"), fallback="email")
            message_key = _safe_segment(message_id, fallback="message")
            rel = pathlib.PurePosixPath("email-attachments") / account_key / message_key / filename
            target_rel = pathlib.PurePosixPath(turn) / "outputs" / rel
            target = outdir_path / target_rel
            if await asyncio.to_thread(target.exists):
                stem = pathlib.PurePosixPath(filename).stem or "attachment"
                suffix = pathlib.PurePosixPath(filename).suffix
                rel = pathlib.PurePosixPath("email-attachments") / account_key / message_key / f"{stem}-{len(files) + 1}{suffix}"
                target_rel = pathlib.PurePosixPath(turn) / "outputs" / rel
                target = outdir_path / target_rel
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_bytes, data)
            logical = f"fi:{turn}.outputs/{rel.as_posix()}"
            physical = target_rel.as_posix()
            mime_type = str(fetched.get("mime_type") or attachment.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
            files.append(
                {
                    "type": "file",
                    "kind": "file",
                    "source_type": "file",
                    "visibility": visibility_norm,
                    "artifact_path": logical,
                    "logical_path": logical,
                    "path": physical,
                    "physical_path": physical,
                    "filename": filename,
                    "mime": mime_type,
                    "mime_type": mime_type,
                    "size": len(data),
                    "size_bytes": len(data),
                    "description": f"Email attachment from {row.get('subject') or message_id}",
                    "source": {
                        "account": str(selected.get("email") or selected.get("account_id") or ""),
                        "message_id": message_id,
                        "attachment_id": attachment_id,
                        "from": str(row.get("from") or ""),
                        "to": str(row.get("to") or ""),
                        "subject": str(row.get("subject") or ""),
                        "date": str(row.get("date") or ""),
                    },
                }
            )

    return {
        "ok": True,
        "artifact_type": "files",
        "account": _account_summary(selected_accounts[0]) if len(selected_accounts) == 1 else None,
        "accounts": [_account_summary(item) for item in selected_accounts],
        "mailbox": str(mailbox or "").strip(),
        "search_query": provider_query,
        "visibility": visibility_norm,
        "message_count": len(candidates[:candidate_limit]),
        "file_count": len(files),
        "files": files,
        "errors": errors,
        "warnings": warnings,
        "usage": {
            "read": "Use returned logical_path values with react.read.",
            "deliver": (
                "This result explicitly declares deliverable files; React rehosts them as normal conversation artifacts."
                if visibility_norm == "external"
                else "Files are internal current-turn artifacts for analysis/code. Do not deliver the original attachments unless the user asks for them."
            ),
            "code": "Use returned physical_path values from exec/rendering code. Zip them only when the user explicitly asks for a ZIP/archive.",
            "contract": "This tool materializes selected email attachments. It does not auto-create ZIP files.",
        },
    }
