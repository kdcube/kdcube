from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

try:
    from fastapi import HTTPException
except Exception:  # pragma: no cover - imported by tests without proc deps.
    HTTPException = None  # type: ignore[assignment]

try:
    from kdcube_ai_app.apps.chat.sdk.config import get_secret
except Exception:
    get_secret = None  # type: ignore[assignment]


DEFAULT_EMAIL_BUNDLE_ID = "task-and-memo-app@1-0"
BUNDLE_ID = DEFAULT_EMAIL_BUNDLE_ID
EMAIL_MCP_SERVER_NAME = "task_memo_email"
EMAIL_MCP_TOKEN_HEADER = "X-KDCube-TaskMemo-Email-MCP-Token"
EMAIL_MCP_TOKEN_SCOPE = "task-memo.email-processing"
EMAIL_MCP_ALLOWED_TOOLS = (
    f"mcp__{EMAIL_MCP_SERVER_NAME}__task_context",
    f"mcp__{EMAIL_MCP_SERVER_NAME}__restore_current_task_state",
    f"mcp__{EMAIL_MCP_SERVER_NAME}__search_messages",
    f"mcp__{EMAIL_MCP_SERVER_NAME}__list_new_messages",
    f"mcp__{EMAIL_MCP_SERVER_NAME}__get_message",
    f"mcp__{EMAIL_MCP_SERVER_NAME}__get_message_attachment",
    f"mcp__{EMAIL_MCP_SERVER_NAME}__store_current_task_state",
    f"mcp__{EMAIL_MCP_SERVER_NAME}__record_processing_result",
)
EMAIL_MCP_TASK_STATE_MAX_BYTES = 64 * 1024
EMAIL_MCP_MESSAGE_BODY_LIMIT = 30000
EMAIL_MCP_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_segment(raw: str, *, fallback: str = "default") -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(raw or "")).strip("-")
    return value or fallback


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_json(data: Mapping[str, Any]) -> str:
    return _b64url(json.dumps(dict(data), sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _unb64url_json(data: str) -> Dict[str, Any]:
    padded = data + ("=" * (-len(data) % 4))
    parsed = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("email MCP token payload is invalid")
    return parsed


async def _secret_lookup(*keys: str) -> str:
    if get_secret is None:
        return ""
    for key in keys:
        value = await get_secret(key)
        if value:
            return str(value)
    return ""


def _entrypoint_bundle_id(entrypoint: Any, default: str = BUNDLE_ID) -> str:
    for candidate in (
        getattr(getattr(getattr(entrypoint, "config", None), "ai_bundle_spec", None), "id", ""),
        getattr(getattr(entrypoint, "config", None), "bundle_id", ""),
        getattr(entrypoint, "bundle_id", ""),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return str(default or BUNDLE_ID).strip() or BUNDLE_ID


def email_mcp_token_header(entrypoint: Any) -> str:
    configured = str(entrypoint.bundle_prop("integrations.email.mcp.auth_header_name", "") or "").strip()
    return configured or EMAIL_MCP_TOKEN_HEADER


def email_mcp_token_ttl_seconds(entrypoint: Any) -> int:
    try:
        value = int(entrypoint.bundle_prop("integrations.email.mcp.token_ttl_seconds", 900) or 900)
    except Exception:
        value = 900
    return max(60, min(value, 3600))


async def email_mcp_auth_secret(entrypoint: Any) -> str:
    bundle_id = _entrypoint_bundle_id(entrypoint)
    return (
        await _secret_lookup(
            "b:integrations.email.mcp_auth_secret",
            f"bundles.{bundle_id}.secrets.integrations.email.mcp_auth_secret",
            "b:integrations.email.oauth_state_secret",
            f"bundles.{bundle_id}.secrets.integrations.email.oauth_state_secret",
            "b:integrations.telegram.webhook_secret",
            f"bundles.{bundle_id}.secrets.integrations.telegram.webhook_secret",
        )
        or str(entrypoint.bundle_prop("integrations.email.mcp.auth_secret", "") or "").strip()
    )


def sign_email_mcp_payload(payload: Mapping[str, Any], *, secret: str) -> str:
    if not str(secret or "").strip():
        raise ValueError("email MCP auth secret is not configured")
    encoded = _b64url_json(payload)
    sig = hmac.new(str(secret).encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def verify_email_mcp_token(token: str, *, secret: str) -> Dict[str, Any]:
    raw = str(token or "").strip()
    if "." not in raw:
        raise ValueError("email MCP token is invalid")
    encoded, received_sig = raw.rsplit(".", 1)
    expected = hmac.new(str(secret).encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_sig, expected):
        raise ValueError("email MCP token signature is invalid")
    payload = _unb64url_json(encoded)
    if payload.get("scope") != EMAIL_MCP_TOKEN_SCOPE:
        raise ValueError("email MCP token scope is invalid")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("email MCP token expired")
    return payload


def _message_id_set(messages: Iterable[Mapping[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in messages:
        message_id = str(item.get("message_id") or "").strip()
        if not message_id or message_id in seen:
            continue
        seen.add(message_id)
        out.append(message_id)
    return out


class EmailMCPRunStore:
    def __init__(self, root: str | Path, *, user_id: str):
        self.root = Path(root).resolve()
        self.user_id = str(user_id or "anonymous").strip() or "anonymous"
        self.safe_user_id = safe_segment(self.user_id, fallback="anonymous")
        self.run_dir = self.root / "email" / "mcp_runs" / self.safe_user_id
        self.state_dir = self.root / "email" / "mcp_task_state" / self.safe_user_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def run_path(self, run_id: str) -> Path:
        return self.run_dir / f"{safe_segment(run_id, fallback='run')}.json"

    def write_run(self, data: Mapping[str, Any]) -> Dict[str, Any]:
        payload = dict(data)
        payload["schema_version"] = "email-mcp-run.v1"
        payload["updated_at"] = utc_now()
        path = self.run_path(str(payload.get("run_id") or "run"))
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return payload

    def read_run(self, run_id: str) -> Dict[str, Any]:
        path = self.run_path(run_id)
        if not path.exists():
            raise ValueError(f"email MCP run {run_id!r} was not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("email MCP run payload is invalid")
        return data

    def update_result(self, *, run_id: str, result: Mapping[str, Any]) -> Dict[str, Any]:
        data = self.read_run(run_id)
        data["result"] = dict(result)
        data["status"] = "result-recorded"
        return self.write_run(data)

    def merge_messages(
        self,
        *,
        run_id: str,
        messages: Iterable[Mapping[str, Any]],
        search: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        data = self.read_run(run_id)
        by_id = {
            str(item.get("message_id") or ""): dict(item)
            for item in (data.get("messages") or [])
            if isinstance(item, Mapping) and str(item.get("message_id") or "")
        }
        candidate_ids = [
            str(item or "").strip()
            for item in (data.get("candidate_message_ids") or [])
            if str(item or "").strip()
        ]
        seen = set(candidate_ids)
        for item in messages:
            if not isinstance(item, Mapping):
                continue
            message_id = str(item.get("message_id") or "").strip()
            if not message_id:
                continue
            by_id[message_id] = dict(item)
            if message_id not in seen:
                seen.add(message_id)
                candidate_ids.append(message_id)
        data["messages"] = [by_id[mid] for mid in candidate_ids if mid in by_id]
        data["candidate_message_ids"] = candidate_ids
        if search:
            searches = [item for item in (data.get("searches") or []) if isinstance(item, Mapping)]
            searches.append(dict(search))
            data["searches"] = searches[-20:]
            data["last_search"] = dict(search)
        return self.write_run(data)

    def task_state_path(self, *, task_id: str, account_id: str) -> Path:
        task_key = safe_segment(task_id or "manual", fallback="manual")
        account_key = safe_segment(account_id or "account", fallback="account")
        state_task_dir = self.state_dir / task_key
        state_task_dir.mkdir(parents=True, exist_ok=True)
        return state_task_dir / f"{account_key}.json"

    def read_task_state(self, *, task_id: str, account_id: str) -> Dict[str, Any]:
        path = self.task_state_path(task_id=task_id, account_id=account_id)
        if not path.exists():
            return {
                "schema_version": "email-mcp-task-state.v1",
                "exists": False,
                "user_id": self.user_id,
                "task_id": task_id or "",
                "account_id": account_id or "",
                "state": {},
                "updated_at": "",
                "note": "",
            }
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("email MCP task state payload is invalid")
        data["exists"] = True
        return data

    def write_task_state(
        self,
        *,
        task_id: str,
        account_id: str,
        state: Mapping[str, Any],
        note: str = "",
        run_id: str = "",
        execution_id: str = "",
    ) -> Dict[str, Any]:
        payload = {
            "schema_version": "email-mcp-task-state.v1",
            "exists": True,
            "user_id": self.user_id,
            "task_id": task_id or "",
            "account_id": account_id or "",
            "state": dict(state),
            "note": str(note or "").strip(),
            "last_run_id": run_id or "",
            "last_execution_id": execution_id or "",
            "updated_at": utc_now(),
        }
        raw = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        if len(raw.encode("utf-8")) > EMAIL_MCP_TASK_STATE_MAX_BYTES:
            raise ValueError(
                f"email MCP task state exceeds {EMAIL_MCP_TASK_STATE_MAX_BYTES} bytes"
            )
        path = self.task_state_path(task_id=task_id, account_id=account_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(path)
        return payload


async def create_email_mcp_run(
    *,
    entrypoint: Any,
    storage_root: str | Path,
    user_id: str,
    task_id: str,
    account: Mapping[str, Any],
    mailbox: str,
    unread_only: bool,
    limit: int,
    gmail_query: str,
    task_definition: str,
    instruction: str,
    messages: list[Mapping[str, Any]],
    execution_id: str = "",
) -> Dict[str, Any]:
    now = int(time.time())
    ttl = email_mcp_token_ttl_seconds(entrypoint)
    run_id = f"email_mcp_{uuid.uuid4().hex}"
    message_ids = _message_id_set(messages)
    account_id = str(account.get("account_id") or "").strip()
    bundle_id = _entrypoint_bundle_id(entrypoint)
    run_store = EmailMCPRunStore(storage_root, user_id=user_id)
    run_doc = run_store.write_run(
        {
            "run_id": run_id,
            "status": "prepared",
            "user_id": user_id,
            "bundle_id": bundle_id,
            "task_id": task_id,
            "execution_id": execution_id,
            "account_id": account_id,
            "account": {
                "account_id": account_id,
                "provider": str(account.get("provider") or ""),
                "email": str(account.get("email") or ""),
                "display_name": str(account.get("display_name") or ""),
            },
            "mailbox": mailbox or "inbox",
            "unread_only": bool(unread_only),
            "limit": int(limit or 20),
            "gmail_query": str(gmail_query or "").strip(),
            "task_definition": task_definition,
            "instruction": instruction,
            "candidate_message_ids": message_ids,
            "messages": [dict(item) for item in messages],
            "searches": [],
            "last_search": None,
            "created_at": utc_now(),
            "expires_at": now + ttl,
            "result": None,
        }
    )
    payload = {
        "v": 1,
        "scope": EMAIL_MCP_TOKEN_SCOPE,
        "run_id": run_id,
        "user_id": user_id,
        "bundle_id": bundle_id,
        "task_id": task_id,
        "execution_id": execution_id,
        "account_id": account_id,
        "message_ids_sha256": hashlib.sha256("|".join(message_ids).encode("utf-8")).hexdigest(),
        "iat": now,
        "exp": now + ttl,
    }
    token = sign_email_mcp_payload(payload, secret=await email_mcp_auth_secret(entrypoint))
    return {
        "run": run_doc,
        "token": token,
        "token_header": email_mcp_token_header(entrypoint),
        "allowed_tools": list(EMAIL_MCP_ALLOWED_TOOLS),
        "server_name": EMAIL_MCP_SERVER_NAME,
    }


def _raise_unauthorized(message: str) -> None:
    if HTTPException is not None:
        raise HTTPException(status_code=401, detail=message)
    raise PermissionError(message)


def _parse_jsonish(value: Any) -> Any:
    if value in ("", None):
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return {"raw": str(value)}


def _email_accounts_api():
    from .accounts import (
        EmailAccountStore,
        fetch_email_attachment,
        fetch_email_message,
        fetch_email_messages,
    )
    return EmailAccountStore, fetch_email_messages, fetch_email_message, fetch_email_attachment


async def build_email_mcp_app(*, entrypoint: Any, request: Any, storage_root: str | Path):
    secret = await email_mcp_auth_secret(entrypoint)
    header_name = email_mcp_token_header(entrypoint)
    token = ""
    if request is not None:
        token = str((getattr(request, "headers", {}) or {}).get(header_name) or "").strip()
    if not token:
        _raise_unauthorized(f"Missing {header_name}")
    try:
        token_payload = verify_email_mcp_token(token, secret=secret)
    except Exception as exc:
        _raise_unauthorized(str(exc))

    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise ImportError("mcp server SDK is not installed") from exc

    store = EmailMCPRunStore(storage_root, user_id=str(token_payload.get("user_id") or "anonymous"))
    run_id = str(token_payload.get("run_id") or "")
    run_doc = store.read_run(run_id)
    allowed_ids = set(str(item) for item in run_doc.get("candidate_message_ids") or [])
    messages_by_id = {
        str(item.get("message_id") or ""): dict(item)
        for item in (run_doc.get("messages") or [])
        if isinstance(item, Mapping) and str(item.get("message_id") or "")
    }
    current_task_id = str(run_doc.get("task_id") or "").strip()
    current_account_id = str(run_doc.get("account_id") or "").strip()
    account = run_doc.get("account") if isinstance(run_doc.get("account"), Mapping) else {}
    EmailAccountStore, fetch_email_messages, fetch_email_message, fetch_email_attachment = _email_accounts_api()
    email_store = EmailAccountStore(
        storage_root,
        user_id=str(token_payload.get("user_id") or "anonymous"),
        bundle_id=str(run_doc.get("bundle_id") or _entrypoint_bundle_id(entrypoint)),
    )

    def _refresh_scope() -> tuple[Dict[str, Any], set[str], Dict[str, Dict[str, Any]]]:
        current = store.read_run(run_id)
        current_ids = {
            str(item or "").strip()
            for item in (current.get("candidate_message_ids") or [])
            if str(item or "").strip()
        }
        current_messages = {
            str(item.get("message_id") or ""): dict(item)
            for item in (current.get("messages") or [])
            if isinstance(item, Mapping) and str(item.get("message_id") or "")
        }
        return current, current_ids, current_messages

    mcp = FastMCP(EMAIL_MCP_SERVER_NAME, stateless_http=True)

    @mcp.tool(
        name="task_context",
        description=(
            "Return the saved-task context for this email-processing run. "
            "Use this before deciding which emails match the task."
        ),
    )
    async def _task_context() -> Dict[str, Any]:
        current_run_doc, _, current_messages = _refresh_scope()
        return {
            "task_id": current_run_doc.get("task_id"),
            "execution_id": current_run_doc.get("execution_id"),
            "task_definition": current_run_doc.get("task_definition") or "",
            "instruction": current_run_doc.get("instruction") or "",
            "account": current_run_doc.get("account") or {},
            "default_mailbox": current_run_doc.get("mailbox") or "inbox",
            "default_unread_only": bool(current_run_doc.get("unread_only")),
            "default_search_query": current_run_doc.get("gmail_query") or "",
            "default_gmail_query": current_run_doc.get("gmail_query") or "",
            "default_limit": int(current_run_doc.get("limit") or 20),
            "last_search": current_run_doc.get("last_search") or None,
            "candidate_message_count": len(current_messages),
            "candidate_message_ids": list(current_run_doc.get("candidate_message_ids") or []),
        }

    @mcp.tool(
        name="restore_current_task_state",
        description=(
            "Restore durable JSON state previously stored for this exact task/account. "
            "Use this early for recurring tasks before deciding what to process next. "
            "For noisy inboxes, prefer compact task-specific hints such as high_watermark_internal_date_ms, "
            "last successful query, durable matching rules, and a short prior-run summary."
        ),
    )
    async def _restore_current_task_state() -> Dict[str, Any]:
        try:
            state_doc = store.read_task_state(task_id=current_task_id, account_id=current_account_id)
        except Exception as exc:
            return {"ok": False, "error": {"code": "task_state_restore_failed", "message": str(exc)}}
        return {"ok": True, **state_doc}

    @mcp.tool(
        name="search_messages",
        description=(
            "Search/list messages for this scoped connected email account. "
            "Choose the provider query or explicit filters from the task goal and restored state. "
            "Supports Gmail query syntax for Gmail, and common from:/to:/subject:/after:/before: filters for iCloud IMAP. "
            "The returned short message rows become readable by get_message."
        ),
    )
    async def _search_messages(
        query: str = "",
        gmail_query: str = "",
        from_email: str = "",
        to_email: str = "",
        subject: str = "",
        since: str = "",
        before: str = "",
        text: str = "",
        mailbox: str = "",
        unread_only: Optional[bool] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        search_query = str(query or gmail_query or run_doc.get("gmail_query") or "").strip()
        mailbox_norm = str(mailbox or run_doc.get("mailbox") or "inbox").strip()
        effective_unread_only = bool(run_doc.get("unread_only")) if unread_only is None else bool(unread_only)
        max_items = max(1, min(int(limit or run_doc.get("limit") or 20), 50))
        result = await fetch_email_messages(
            store=email_store,
            entrypoint=entrypoint,
            account=account,
            mailbox=mailbox_norm,
            unread_only=effective_unread_only,
            limit=max_items,
            query=search_query,
            gmail_query=search_query,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            since=since,
            before=before,
            text=text,
        )
        if not result.get("ok"):
            return result
        rows = [
            dict(item)
            for item in (result.get("messages") or [])
            if isinstance(item, Mapping) and str(item.get("message_id") or "")
        ]
        search_doc = {
            "query": search_query,
            "gmail_query": search_query,
            "from_email": str(from_email or "").strip(),
            "to_email": str(to_email or "").strip(),
            "subject": str(subject or "").strip(),
            "since": str(since or "").strip(),
            "before": str(before or "").strip(),
            "text": str(text or "").strip(),
            "mailbox": mailbox_norm,
            "unread_only": effective_unread_only,
            "limit": max_items,
            "returned_count": len(rows),
            "result_size_estimate": result.get("result_size_estimate"),
            "searched_at": utc_now(),
        }
        store.merge_messages(run_id=run_id, messages=rows, search=search_doc)
        return {
            "ok": True,
            "count": len(rows),
            "result_size_estimate": result.get("result_size_estimate"),
            "search": search_doc,
            "messages": rows,
        }

    @mcp.tool(
        name="list_new_messages",
        description=(
            "List candidate email messages that have not yet been processed for this task/account run. "
            "Only task-scoped candidate messages are returned."
        ),
    )
    async def _list_new_messages(limit: int = 20) -> Dict[str, Any]:
        _, _, current_messages = _refresh_scope()
        max_items = max(1, min(int(limit or 20), 50))
        rows = list(current_messages.values())[:max_items]
        return {
            "count": len(rows),
            "messages": rows,
            "note": "" if rows else "No scoped messages yet. Call search_messages first.",
        }

    @mcp.tool(
        name="get_message",
        description=(
            "Read one email message by id after it has been returned by search_messages. "
            "Includes full bounded plain-text body and attachment metadata."
        ),
    )
    async def _get_message(message_id: str) -> Dict[str, Any]:
        wanted = str(message_id or "").strip()
        _, current_allowed_ids, current_messages = _refresh_scope()
        if wanted not in current_allowed_ids:
            return {"ok": False, "error": {"code": "message_not_in_scope", "message": "Message is not in this task run."}}
        result = await fetch_email_message(
            store=email_store,
            entrypoint=entrypoint,
            account=account,
            message_id=wanted,
            body_limit=EMAIL_MCP_MESSAGE_BODY_LIMIT,
            mailbox=str(run_doc.get("mailbox") or "inbox"),
        )
        if not result.get("ok"):
            cached = current_messages.get(wanted)
            if cached:
                return {"ok": True, "message": cached, "warning": result.get("error") or result}
            return result
        message = result.get("message") if isinstance(result.get("message"), Mapping) else {}
        if message:
            store.merge_messages(run_id=run_id, messages=[message])
        return {"ok": True, "message": message}

    @mcp.tool(
        name="get_message_attachment",
        description=(
            "Read one attachment for a scoped message. Use attachment_id from get_message attachment metadata. "
            "Returns base64 for binary attachments and text for small text-like attachments."
        ),
    )
    async def _get_message_attachment(message_id: str, attachment_id: str, max_bytes: int = EMAIL_MCP_ATTACHMENT_MAX_BYTES) -> Dict[str, Any]:
        wanted = str(message_id or "").strip()
        _, current_allowed_ids, _ = _refresh_scope()
        if wanted not in current_allowed_ids:
            return {"ok": False, "error": {"code": "message_not_in_scope", "message": "Message is not in this task run."}}
        return await fetch_email_attachment(
            store=email_store,
            entrypoint=entrypoint,
            account=account,
            message_id=wanted,
            attachment_id=attachment_id,
            max_bytes=max_bytes,
            mailbox=str(run_doc.get("mailbox") or "inbox"),
        )

    @mcp.tool(
        name="store_current_task_state",
        description=(
            "Store durable JSON state for this exact task/account so the next run can restore it. "
            "Use it for compact cursors, last analyzed ranges, durable classification rules, or compact summaries. "
            "Do not store full mailbox history. Prefer high_watermark_internal_date_ms, last_successful_search_query, "
            "total counts, and short summaries/rules. Store message ids only if the task explicitly needs exact tie-breaking."
        ),
    )
    async def _store_current_task_state(state_json: str = "", note: str = "") -> Dict[str, Any]:
        parsed = _parse_jsonish(state_json)
        if not isinstance(parsed, dict):
            return {
                "ok": False,
                "error": {
                    "code": "task_state_must_be_object",
                    "message": "state_json must decode to a JSON object.",
                },
            }
        try:
            state_doc = store.write_task_state(
                task_id=current_task_id,
                account_id=current_account_id,
                state=parsed,
                note=note,
                run_id=run_id,
                execution_id=str(run_doc.get("execution_id") or ""),
            )
        except Exception as exc:
            return {"ok": False, "error": {"code": "task_state_store_failed", "message": str(exc)}}
        return {
            "ok": True,
            "task_id": state_doc.get("task_id") or "",
            "account_id": state_doc.get("account_id") or "",
            "updated_at": state_doc.get("updated_at") or "",
            "bytes_limit": EMAIL_MCP_TASK_STATE_MAX_BYTES,
        }

    @mcp.tool(
        name="record_processing_result",
        description=(
            "Record the email-processing result before the final answer. "
            "Use processed_message_ids for every message inspected/handled and matched_message_ids for messages "
            "that satisfy the task condition."
        ),
    )
    async def _record_processing_result(
        processed_message_ids: Optional[list[str]] = None,
        matched_message_ids: Optional[list[str]] = None,
        summary: str = "",
        user_notification: str = "",
        details_json: str = "",
    ) -> Dict[str, Any]:
        current_run_doc, current_allowed_ids, _ = _refresh_scope()
        processed = [
            message_id
            for message_id in [str(item or "").strip() for item in (processed_message_ids or [])]
            if message_id and message_id in current_allowed_ids
        ]
        matched = [
            message_id
            for message_id in [str(item or "").strip() for item in (matched_message_ids or [])]
            if message_id and message_id in current_allowed_ids
        ]
        if not processed:
            processed = list(current_run_doc.get("candidate_message_ids") or [])
        result = {
            "processed_message_ids": processed,
            "matched_message_ids": matched,
            "summary": str(summary or "").strip(),
            "user_notification": str(user_notification or "").strip(),
            "details": _parse_jsonish(details_json),
            "recorded_at": utc_now(),
        }
        updated = store.update_result(run_id=run_id, result=result)
        return {
            "ok": True,
            "run_id": run_id,
            "processed_count": len(processed),
            "matched_count": len(matched),
            "status": updated.get("status"),
        }

    return mcp
