# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Provider-neutral mail named service.

The ``mail`` namespace models mail as a user realm, not as a specific OAuth
provider. A platform user can connect several mail accounts (Gmail, iCloud,
Yahoo, ...). This provider exposes those accounts and messages through the
standard named-service operations; provider-specific transport remains in the
provider packages such as ``integrations.google.gmail_tools``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.google.gmail_tools import (
    GMAIL_CONNECTOR_APP_ID,
    GMAIL_PROVIDER_ID,
    GMAIL_READ_CLAIM,
    GMAIL_SEND_CLAIM,
    GmailTools,
    bind_integrations as bind_gmail_integrations,
    bind_service as bind_gmail_service,
)
from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import (
    delete_staged,
    staging_root,
)
from kdcube_ai_app.apps.chat.sdk.integrations.inline_files import (
    InlineFileError,
    inline_files_workspace,
    materialize_inline_files,
    resolve_payload_file_entries,
)
from kdcube_ai_app.apps.chat.sdk.integrations.named_service_consent import (
    ACCOUNT_SELECTION_CONTRACT,
    CONSENT_ERROR_CONTRACT,
    account_credential_status,
    consent_error_response,
    resolution_consent_payload,
    tool_error_response,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges import (
    DEFAULT_CONNECTION_HUB_BUNDLE_ID,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import (
    DelegatedToKdcubeClient,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    REASON_CONNECT_REQUIRED,
    ClaimResolution,
    ConnectedAccount,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceProvider,
    NamedServiceProviderSpec,
    NamedServiceRequest,
    NamedServiceResponse,
    NamedServiceSearchScope,
    TRANSPORT_API,
    TRANSPORT_LOCAL,
    named_service_provider,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    OBJECT_ACTION,
    OBJECT_GET,
    OBJECT_LIST,
    OBJECT_SCHEMA,
    OBJECT_SEARCH,
    PROVIDER_ABOUT,
    PROVIDER_CAPABILITIES,
)


LOGGER = logging.getLogger("kdcube.sdk.integrations.mail.named_service")

MAIL_NAMESPACE = "mail"
PROVIDER_ID = "sdk.integrations.mail"
MAIL_ACCOUNT_KIND = "mail.account"
MAIL_MESSAGE_KIND = "mail.message"
MAIL_ATTACHMENT_KIND = "mail.attachment"
MAIL_TRANSPORTS = (TRANSPORT_LOCAL, TRANSPORT_API)

ACTION_DOWNLOAD_ATTACHMENTS = "download_attachments"
ACTION_SEND = "send"
ACTION_FORWARD = "forward"
ACTION_REQUEST_UPLOAD = "request_upload"
ACTION_DISCARD_UPLOAD = "discard_upload"

MAIL_GRANT_HINTS = {
    "object.list": ["mail:read"],
    "object.search": ["mail:read"],
    "object.get": ["mail:read"],
    "object.action.download_attachments": ["mail:read"],
    "object.action.send": ["mail:send"],
    "object.action.forward": ["mail:read", "mail:send"],
}

# Machine-readable connected-account requirements for catalog consumers (the
# composer menu's proactive consent). The mail realm DIFFERENTIATES claims per
# operation — read operations need the read claim; send-class actions need the
# send claim — so consumers can scope the shown claims to the operations a
# configuration actually allows. Same constants `_resolve_claim` uses.
MAIL_CONNECTED_ACCOUNT_REQUIREMENTS = [
    {
        "provider_id": GMAIL_PROVIDER_ID,
        "connector_app_id": GMAIL_CONNECTOR_APP_ID,
        "provider_label": "Google",
        "claims": [GMAIL_READ_CLAIM, GMAIL_SEND_CLAIM],
        "claim_labels": {
            GMAIL_READ_CLAIM: "read mail",
            GMAIL_SEND_CLAIM: "send mail",
        },
        "claims_by_operation": {
            "object.list": [GMAIL_READ_CLAIM],
            "object.search": [GMAIL_READ_CLAIM],
            "object.get": [GMAIL_READ_CLAIM],
            "object.action.download_attachments": [GMAIL_READ_CLAIM],
            "object.action.send": [GMAIL_SEND_CLAIM],
            "object.action.forward": [GMAIL_READ_CLAIM, GMAIL_SEND_CLAIM],
        },
    }
]

MAIL_PROVIDER_CATALOG = {
    "gmail": {
        "provider_id": GMAIL_PROVIDER_ID,
        "connector_app_id": GMAIL_CONNECTOR_APP_ID,
        "label": "Gmail",
        "claims": {
            "read": GMAIL_READ_CLAIM,
            "send": GMAIL_SEND_CLAIM,
        },
    },
    # Shape reserved for providers implemented next. They share the ``mail``
    # namespace and object model, but resolve through their own connector app.
    "icloud": {
        "provider_id": "email",
        "connector_app_id": "icloud-mail",
        "label": "iCloud Mail",
        "claims": {"read": "mail:read", "send": "mail:send"},
        "implemented": False,
    },
    "yahoo": {
        "provider_id": "email",
        "connector_app_id": "yahoo-mail",
        "label": "Yahoo Mail",
        "claims": {"read": "mail:read", "send": "mail:send"},
        "implemented": False,
    },
}

MAIL_SEARCH_FILTERS = {
    "account_id": {
        "type": "string",
        "description": "Optional connected mail account id. Omit to search every connected Gmail account with mail read access.",
    },
    "provider": {
        "type": "string",
        "description": "Optional mail provider key. Currently implemented: gmail.",
        "default": "gmail",
    },
    "gmail_query": {
        "type": "string",
        "description": "Gmail-native query. Defaults to the named-service query.",
    },
}

MAIL_SEARCH_SCOPES = (
    NamedServiceSearchScope(
        namespace=MAIL_NAMESPACE,
        label="mail messages",
        object_kind=MAIL_MESSAGE_KIND,
        description=(
            "Search messages across connected mail accounts. Omit account_id to "
            "search every connected account that already approved the read claim."
        ),
        filters_schema=MAIL_SEARCH_FILTERS,
    ),
)

MAIL_INTRO = (
    "Use namespace `mail` for user-connected email accounts. Start with "
    "object.list to see connected accounts, object.search to find messages, "
    "object.get to read a message, and object.action with download_attachments, "
    "send, or forward for bounded mail actions."
)

# Human layer of the realm's self-description — the same contract the agent
# reads via provider.about/schema, in user terms. The picker renders these
# verbatim; missing text here is a realm defect, never a UI invention.
MAIL_PRESENTATION = {
    "about": "Read, search, and send email from the mail accounts you connect.",
    "third_party": "Works with your mailbox through your connected Google account.",
    "operations": {
        "provider.about": {"label": "Service overview", "description": "What this mail service does and how to use it."},
        "provider.capabilities": {"label": "Capabilities", "description": "The operations and behaviors this service declares."},
        "object.list": {"label": "List accounts", "description": "List your connected mail accounts."},
        "object.search": {"label": "Search mail", "description": "Search messages across your connected mail accounts."},
        "object.get": {"label": "Read a message", "description": "Read one message or attachment from your mailbox."},
        "object.schema": {"label": "Object reference", "description": "The shapes and refs of this service's objects."},
    },
    "actions": {
        ACTION_SEND: {"label": "Send email", "description": "Send an email from your connected mail account."},
        ACTION_FORWARD: {"label": "Forward email", "description": "Forward a message from your mailbox, optionally with its attachments."},
        ACTION_DOWNLOAD_ATTACHMENTS: {"label": "Download attachments", "description": "Save a message's attachments as files."},
        ACTION_REQUEST_UPLOAD: {"label": "Attach a file", "description": "Stage one outbound file for a send or forward."},
        ACTION_DISCARD_UPLOAD: {"label": "Discard staged file", "description": "Remove a staged outbound file before it is used."},
    },
}

MAIL_SCHEMA = {
    "namespace": MAIL_NAMESPACE,
    "refs": {
        "account": "mail:<provider>:<account_id>",
        "message": "mail:<provider>:<account_id>:message:<message_id>",
        "attachment": (
            "mail:<provider>:<account_id>:attachment:<message_id>:<part_id> — "
            "the part id is stable across reads (Gmail attachment ids rotate per fetch)"
        ),
    },
    "object_kinds": {
        MAIL_ACCOUNT_KIND: {
            "description": "One connected mail account belonging to the current KDCube user.",
            "fields": ["ref", "provider", "provider_id", "connector_app_id", "account_id", "label", "email", "claims", "credential_status"],
        },
        MAIL_MESSAGE_KIND: {
            "description": "One mail message found or read from a connected account.",
            "fields": ["ref", "provider", "account_id", "account_label", "message_id", "thread_id", "subject", "from", "date", "snippet"],
        },
        MAIL_ATTACHMENT_KIND: {
            "description": "One attachment on a mail message.",
            "fields": ["ref", "account_id", "message_id", "attachment_id", "filename", "mime_type", "size_bytes", "download"],
        },
    },
    "files": {
        "get": (
            "object.get on an attachment ref returns its metadata plus download "
            "{encoding, url, expires_at}. encoding=url means fetch the short-lived "
            "url over plain HTTP out-of-band — bytes never ride in the tool result. "
            "encoding=none means no delivery path is configured; ask in chat instead."
        ),
    },
    "search": {"filters": MAIL_SEARCH_FILTERS},
    "actions": {
        ACTION_DOWNLOAD_ATTACHMENTS: {
            "description": (
                "Download message attachments. In chat they land as KDCube files; "
                "on transports without a chat turn (MCP) the action returns one "
                "short-lived download url per attachment instead."
            ),
            "object_ref": "mail:<provider>:<account_id>:message:<message_id>",
            "payload": ["attachment_ids", "include_inline", "max_attachments", "visibility"],
        },
        ACTION_DISCARD_UPLOAD: {
            "description": (
                "Remove one staged upload before it is used (idempotent). Unused staged "
                "files also expire on their own within about an hour."
            ),
            "payload": ["staged_ref"],
        },
        ACTION_REQUEST_UPLOAD: {
            "description": (
                "Reserve an upload slot for one outbound attachment. Returns "
                "{upload_url, staged_ref, expires_at}: PUT/POST the raw file bytes to "
                "upload_url over plain HTTP, then reference the staged_ref in a send/"
                "forward attachments entry. This is THE way to attach files — bytes "
                "never ride inside tool calls."
            ),
            "object_ref": "mail:<provider>:<account_id> (any connected account ref)",
            "payload": ["filename", "mime"],
        },
        ACTION_SEND: {
            "description": (
                "Send a new email from a connected mail account. Attach files via "
                "attachments=[{staged_ref}] after request_upload (preferred); tiny "
                "files may ride inline as {filename, content_base64} (10MB/file, 25MB total)."
            ),
            "object_ref": "mail:<provider>:<account_id> or omit account_id in payload when only one account can send",
            "payload": ["to", "subject", "body_markdown", "cc", "bcc", "body_html", "attachments", "attachment_paths", "account_id"],
        },
        ACTION_FORWARD: {
            "description": (
                "Forward an existing message. include_original_attachments=true carries "
                "the original files on any transport; extra files ride via "
                "attachments=[{staged_ref}] (after request_upload) or tiny inline entries."
            ),
            "object_ref": "mail:<provider>:<account_id>:message:<message_id>",
            "payload": ["to", "note_markdown", "cc", "bcc", "include_original_attachments", "attachments", "attachment_paths"],
        },
    },
    "account_selection": ACCOUNT_SELECTION_CONTRACT,
    "consent_errors": CONSENT_ERROR_CONTRACT,
    "grant_hints": MAIL_GRANT_HINTS,
    "connected_account_claims": {
        "gmail": {
            "read": GMAIL_READ_CLAIM,
            "send": GMAIL_SEND_CLAIM,
        }
    },
}


def _operations() -> dict[str, Any]:
    return {
        PROVIDER_ABOUT: {"transports": MAIL_TRANSPORTS},
        PROVIDER_CAPABILITIES: {"transports": MAIL_TRANSPORTS},
        OBJECT_LIST: {"transports": MAIL_TRANSPORTS},
        OBJECT_SEARCH: {"transports": MAIL_TRANSPORTS},
        OBJECT_GET: {"transports": MAIL_TRANSPORTS},
        OBJECT_SCHEMA: {"transports": MAIL_TRANSPORTS},
        OBJECT_ACTION: {"transports": MAIL_TRANSPORTS},
    }


def mail_named_service_spec(*, bundle_id: str | None = None) -> NamedServiceProviderSpec:
    return NamedServiceProviderSpec(
        provider_id=PROVIDER_ID,
        bundle_id=bundle_id,
        namespace=MAIL_NAMESPACE,
        refs=("mail:*",),
        object_kinds=(MAIL_ACCOUNT_KIND, MAIL_MESSAGE_KIND, MAIL_ATTACHMENT_KIND),
        search_scopes=MAIL_SEARCH_SCOPES,
        operations=_operations(),
        label="Mail",
        description="Provider-neutral mail namespace over user-connected Gmail, iCloud, Yahoo, and related accounts.",
        intro=MAIL_INTRO,
        metadata={
            "provider_catalog": MAIL_PROVIDER_CATALOG,
            "grant_hints": MAIL_GRANT_HINTS,
            "canonical_ref": "mail:<provider>:<account_id>:message:<message_id>",
        },
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, *, default: int, minimum: int = 1, maximum: int = 50) -> int:
    try:
        parsed = int(value if value is not None else default)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def account_ref(provider: str, account_id: str) -> str:
    return f"{MAIL_NAMESPACE}:{_text(provider) or 'gmail'}:{_text(account_id)}"


def message_ref(provider: str, account_id: str, message_id: str) -> str:
    return f"{account_ref(provider, account_id)}:message:{_text(message_id)}"


def attachment_ref(provider: str, account_id: str, message_id: str, attachment_id: str) -> str:
    return f"{account_ref(provider, account_id)}:attachment:{_text(message_id)}:{_text(attachment_id)}"


def parse_mail_ref(ref: str) -> dict[str, str]:
    parts = _text(ref).split(":")
    if len(parts) < 3 or parts[0] != MAIL_NAMESPACE:
        return {}
    parsed = {"provider": parts[1], "account_id": parts[2], "kind": "account"}
    if len(parts) >= 5 and parts[3] == "message":
        parsed.update({"kind": "message", "message_id": ":".join(parts[4:])})
    elif len(parts) >= 6 and parts[3] == "attachment":
        parsed.update({"kind": "attachment", "message_id": parts[4], "attachment_id": ":".join(parts[5:])})
    return parsed


def _account_object(account: ConnectedAccount, *, provider_key: str = "gmail") -> dict[str, Any]:
    label = account.display_name or account.email or account.external_subject or account.account_id
    return {
        "ref": account_ref(provider_key, account.account_id),
        "object_ref": account_ref(provider_key, account.account_id),
        "object_kind": MAIL_ACCOUNT_KIND,
        "id": account.account_id,
        "account_id": account.account_id,
        "provider": provider_key,
        "provider_id": account.provider_id,
        "connector_app_id": account.connector_app_id,
        "label": label,
        "display_name": account.display_name,
        "email": account.email,
        "external_subject": account.external_subject,
        "workspace": account.workspace,
        "claims": list(account.claims or ()),
        "connected": account.connected,
        "status": account.status,
        "credential_status": account_credential_status(account),
        "connected_at": account.connected_at,
        "updated_at": account.updated_at,
        "metadata": dict(account.metadata or {}),
    }


def _message_object(
    row: Mapping[str, Any],
    *,
    provider_key: str = "gmail",
    account_id: str = "",
    account_label: str = "",
) -> dict[str, Any]:
    message_id = _text(row.get("id") or row.get("message_id"))
    headers = row.get("headers") if isinstance(row.get("headers"), Mapping) else {}
    subject = _text(row.get("subject") or headers.get("subject"))
    sender = _text(row.get("from") or headers.get("from"))
    date = _text(row.get("date") or headers.get("date"))
    ref = message_ref(provider_key, account_id, message_id) if message_id and account_id else ""
    return {
        "ref": ref,
        "object_ref": ref,
        "object_kind": MAIL_MESSAGE_KIND,
        "id": message_id,
        "message_id": message_id,
        "thread_id": _text(row.get("thread_id")),
        "provider": provider_key,
        "account_id": account_id,
        "account_label": _text(account_label) or account_id,
        "subject": subject,
        "from": sender,
        "date": date,
        "snippet": _text(row.get("snippet")),
        "headers": dict(headers or {}),
        "attachment_count": row.get("attachment_count", 0),
        "inline_attachment_count": row.get("inline_attachment_count", 0),
        "attachments": list(row.get("attachments") or []),
        "inline_attachments": list(row.get("inline_attachments") or []),
    }


def _error_from_tool(result: Mapping[str, Any], *, request: NamedServiceRequest, default_code: str = "mail_operation_failed") -> NamedServiceResponse:
    return tool_error_response(
        result,
        request=request,
        namespace=MAIL_NAMESPACE,
        provider_identity={"provider_id": PROVIDER_ID},
        default_code=default_code,
        fallback_message="Mail operation failed.",
    )


@named_service_provider(
    provider_id=PROVIDER_ID,
    namespace=MAIL_NAMESPACE,
    refs=("mail:*",),
    object_kinds=(MAIL_ACCOUNT_KIND, MAIL_MESSAGE_KIND, MAIL_ATTACHMENT_KIND),
    search_scopes=MAIL_SEARCH_SCOPES,
    operations=_operations(),
    label="Mail",
    description="Provider-neutral mail namespace over user-connected accounts.",
    intro=MAIL_INTRO,
    metadata={
        "provider_catalog": MAIL_PROVIDER_CATALOG,
        "grant_hints": MAIL_GRANT_HINTS,
        "connected_accounts": MAIL_CONNECTED_ACCOUNT_REQUIREMENTS,
        "actions": {
            name: str((meta or {}).get("description") or "").strip()
            for name, meta in (MAIL_SCHEMA.get("actions") or {}).items()
        },
        "presentation": MAIL_PRESENTATION,
        "object_kinds": {
            kind: str((meta or {}).get("description") or "").strip()
            for kind, meta in (MAIL_SCHEMA.get("object_kinds") or {}).items()
        },
    },
)
class MailNamedServiceProvider(NamedServiceProvider):
    def __init__(
        self,
        *,
        entrypoint: Any = None,
        bundle_id: str | None = None,
        connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
        file_url_factory: Any = None,
        upload_slot_factory: Any = None,
    ) -> None:
        super().__init__(mail_named_service_spec(bundle_id=bundle_id))
        self._entrypoint = entrypoint
        self._connection_hub_bundle_id = connection_hub_bundle_id
        self._file_url_factory = file_url_factory
        self._upload_slot_factory = upload_slot_factory
        self._gmail = GmailTools()
        if entrypoint is not None:
            bind_gmail_service(entrypoint)
            bind_gmail_integrations({"comm_context": getattr(entrypoint, "comm_context", None)})

    def _provider_identity(self) -> dict[str, Any]:
        return {"provider_id": PROVIDER_ID, "bundle_id": self.spec.bundle_id}

    async def _download_url(self, ctx: NamedServiceContext, *, ref: str) -> dict[str, Any] | None:
        """Short-lived signed download URL for one attachment ref, or None when
        the hosting bundle provides no delivery path (no factory / no secret /
        unknown public origin)."""
        if self._file_url_factory is None:
            return None
        try:
            out = self._file_url_factory(ctx, {"ref": ref})
            if hasattr(out, "__await__"):
                out = await out
        except Exception:
            LOGGER.exception("mail download url factory failed for %s", ref)
            return None
        return dict(out) if isinstance(out, Mapping) and out.get("url") else None

    def _attachment_download_field(self, url_info: dict[str, Any] | None) -> dict[str, Any]:
        if url_info:
            return {"encoding": "url", **url_info}
        return {
            "encoding": "none",
            "note": "No out-of-band delivery is configured; download attachments in chat instead.",
        }

    def _staging_root(self):
        storage = str(getattr(getattr(self._entrypoint, "settings", None), "STORAGE_PATH", "") or "")
        try:
            return staging_root(storage)
        except OSError:
            return None

    def _discard_upload(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        payload = dict(request.payload or {})
        staged_ref = _text(payload.get("staged_ref"))
        if not staged_ref:
            return NamedServiceResponse.error_response(
                code="staged_ref_required",
                message="discard_upload needs payload.staged_ref.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or MAIL_NAMESPACE,
                object_ref=request.object_ref,
            )
        root = self._staging_root()
        if root is not None:
            delete_staged(root, staged_ref)
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or MAIL_NAMESPACE,
            object_ref=request.object_ref,
            extra={"action": ACTION_DISCARD_UPLOAD, "staged_ref": staged_ref, "removed": True},
        )

    async def _request_upload(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        payload = dict(request.payload or {})
        filename = _text(payload.get("filename"))
        if not filename:
            return NamedServiceResponse.error_response(
                code="filename_required",
                message="request_upload needs payload.filename.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or MAIL_NAMESPACE,
                object_ref=request.object_ref,
            )
        slot = None
        if self._upload_slot_factory is not None:
            try:
                slot = self._upload_slot_factory(ctx, {"filename": filename, "mime": _text(payload.get("mime"))})
                if hasattr(slot, "__await__"):
                    slot = await slot
            except Exception:
                LOGGER.exception("mail upload slot factory failed")
                slot = None
        if not isinstance(slot, Mapping) or not slot.get("upload_url"):
            return NamedServiceResponse.error_response(
                code="upload_not_configured",
                message="This deployment has no upload path configured; use tiny inline content_base64 attachments instead.",
                status=503,
                provider=self._provider_identity(),
                namespace=request.namespace or MAIL_NAMESPACE,
                object_ref=request.object_ref,
            )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or MAIL_NAMESPACE,
            object_ref=request.object_ref,
            extra={
                "action": ACTION_REQUEST_UPLOAD,
                **dict(slot),
                "how": (
                    "POST the raw file bytes to upload_url (body = file, no form encoding), "
                    "then pass {\"staged_ref\": ...} in the attachments list of send/forward."
                ),
            },
        )

    def _inline_error(self, request: NamedServiceRequest, exc: InlineFileError) -> NamedServiceResponse:
        return NamedServiceResponse.error_response(
            code="mail_inline_files_invalid",
            message=str(exc),
            status=400,
            provider=self._provider_identity(),
            namespace=request.namespace or MAIL_NAMESPACE,
            object_ref=request.object_ref,
        )

    @staticmethod
    def _merged_attachment_paths(payload: Mapping[str, Any], staged: list[dict[str, Any]]) -> str:
        paths = [_text(item) for item in _as_list(payload.get("attachment_paths")) if _text(item)]
        paths.extend(item["relpath"] for item in staged)
        return json.dumps(paths)

    async def _download_attachments_as_urls(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
        *,
        parsed: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> NamedServiceResponse:
        """URL delivery for download_attachments on turn-less transports."""
        result = await self._gmail.read_gmail_message(
            message_id=parsed["message_id"],
            include_html=False,
            max_body_chars=1,
            account_id=parsed["account_id"],
        )
        if not isinstance(result, Mapping) or not result.get("ok"):
            return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="gmail_download_failed")
        ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
        selected = {_text(item) for item in _as_list(payload.get("attachment_ids")) if _text(item)}
        rows = list(ret.get("attachments") or [])
        if bool(payload.get("include_inline")):
            rows.extend(ret.get("inline_attachments") or [])
        if selected:
            rows = [row for row in rows if isinstance(row, Mapping) and _text(row.get("attachment_id")) in selected]
        rows = rows[: _int(payload.get("max_attachments"), default=10, maximum=20)]
        files: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            # Mint refs on the stable part id — Gmail attachment ids rotate
            # per fetch, so an id-bearing ref would be dead by download time.
            selector = _text(row.get("part_id")) or _text(row.get("attachment_id"))
            ref = attachment_ref("gmail", parsed["account_id"], parsed["message_id"], selector)
            url_info = await self._download_url(ctx, ref=ref)
            files.append(
                {
                    "ref": ref,
                    "object_kind": MAIL_ATTACHMENT_KIND,
                    "part_id": _text(row.get("part_id")),
                    "attachment_id": _text(row.get("attachment_id")),
                    "filename": _text(row.get("filename")) or "attachment.bin",
                    "mime_type": _text(row.get("mime_type")),
                    "size_bytes": row.get("size_bytes", 0),
                    "download": self._attachment_download_field(url_info),
                }
            )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or MAIL_NAMESPACE,
            object_ref=request.object_ref,
            items=files,
            extra={
                "action": ACTION_DOWNLOAD_ATTACHMENTS,
                "delivery": "url",
                "count": len(files),
                "note": "No chat turn on this transport; fetch each download.url over HTTP out-of-band.",
            },
        )

    async def _client(self, ctx: NamedServiceContext) -> DelegatedToKdcubeClient | None:
        user_id = _text(ctx.user_id)
        if not user_id or self._entrypoint is None:
            return None
        return await DelegatedToKdcubeClient.from_connection_hub(
            self._entrypoint,
            user_id=user_id,
            tenant=ctx.tenant,
            project=ctx.project,
            connection_hub_bundle_id=self._connection_hub_bundle_id,
        )

    async def _gmail_accounts(self, ctx: NamedServiceContext, *, claim: str = "") -> list[ConnectedAccount]:
        client = await self._client(ctx)
        if client is None:
            return []
        accounts = await client.list_accounts(provider_id=GMAIL_PROVIDER_ID)
        out = [
            account for account in accounts
            if account.connector_app_id == GMAIL_CONNECTOR_APP_ID
            and account.connected
            and (not claim or account.allows(claim))
        ]
        return out

    async def _resolve_claim(
        self,
        ctx: NamedServiceContext,
        *,
        claim: str,
        account_id: str = "",
    ) -> ClaimResolution:
        """Resolve one Gmail claim through the broker.

        The broker mints the distinct resolution reason (connect vs upgrade vs
        reconnect vs account choice) with labeled candidates; this adapter
        never re-derives that. Without a platform user or entrypoint the only
        honest answer is connect_required.
        """
        client = await self._client(ctx)
        if client is None:
            return ClaimResolution(
                ok=False,
                provider_id=GMAIL_PROVIDER_ID,
                claim=claim,
                connector_app_id=GMAIL_CONNECTOR_APP_ID,
                account_id=account_id,
                error=REASON_CONNECT_REQUIRED,
                message="Connect a mail account in Connection Hub.",
                retry_hint=True,
            )
        return await client.ensure_claim(
            provider_id=GMAIL_PROVIDER_ID,
            connector_app_id=GMAIL_CONNECTOR_APP_ID,
            claim=claim,
            account_id=account_id or None,
        )

    def _consent_error(
        self,
        *,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
        resolution: ClaimResolution,
    ) -> NamedServiceResponse:
        return consent_error_response(
            resolution=resolution,
            ctx=ctx,
            request=request,
            namespace=MAIL_NAMESPACE,
            provider_identity=self._provider_identity(),
            connection_hub_bundle_id=self._connection_hub_bundle_id,
            tool_name=f"named_services.{MAIL_NAMESPACE}.{request.operation}",
        )

    def _connect_hint(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> dict[str, Any]:
        """Consent block shipped with an EMPTY account list, so clients learn
        where to connect without treating the empty list as an error."""
        payload = resolution_consent_payload(
            resolution=ClaimResolution(
                ok=False,
                provider_id=GMAIL_PROVIDER_ID,
                claim="",
                connector_app_id=GMAIL_CONNECTOR_APP_ID,
                error=REASON_CONNECT_REQUIRED,
                message="Connect a mail account in Connection Hub.",
                retry_hint=True,
            ),
            ctx=ctx,
            connection_hub_bundle_id=self._connection_hub_bundle_id,
            tool_name=f"named_services.{MAIL_NAMESPACE}.{request.operation}",
        )
        return dict(payload.get("consent") or {})

    async def _accounts_for_claim(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
        *,
        claim: str,
        account_id: str = "",
    ) -> tuple[list[ConnectedAccount], NamedServiceResponse | None]:
        """Accounts to operate on, or the structured consent error.

        Explicit ``account_id`` pins one account (broker explains any failure);
        otherwise every account holding the claim participates. Empty means
        the broker minted connect/upgrade/reconnect — never a silent guess.
        """
        eligible = await self._gmail_accounts(ctx, claim=claim)
        if account_id:
            account = next((item for item in eligible if item.account_id == account_id), None)
            if account is None:
                resolution = await self._resolve_claim(ctx, claim=claim, account_id=account_id)
                if not resolution.ok:
                    return [], self._consent_error(ctx=ctx, request=request, resolution=resolution)
                account = ConnectedAccount(
                    account_id=account_id,
                    provider_id=GMAIL_PROVIDER_ID,
                    connector_app_id=GMAIL_CONNECTOR_APP_ID,
                )
            return [account], None
        if not eligible:
            resolution = await self._resolve_claim(ctx, claim=claim)
            if not resolution.ok:
                return [], self._consent_error(ctx=ctx, request=request, resolution=resolution)
            return [
                ConnectedAccount(
                    account_id=resolution.account_id,
                    provider_id=GMAIL_PROVIDER_ID,
                    connector_app_id=GMAIL_CONNECTOR_APP_ID,
                )
            ], None
        return eligible, None

    async def provider_about(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or MAIL_NAMESPACE,
            extra={
                "title": "KDCube Mail",
                "description": (
                    "Provider-neutral mail namespace. Connected accounts can be Gmail, "
                    "iCloud, Yahoo, or another mail provider; Gmail is implemented now."
                ),
                "workflow": [
                    "Call object.list to see connected accounts.",
                    "Call object.search with namespace='mail' to search messages.",
                    "Call object.get with a mail:<provider>:<account_id>:message:<id> ref to read a message.",
                    "Call object.action download_attachments/send/forward for bounded mail actions.",
                ],
                "providers": MAIL_PROVIDER_CATALOG,
                "schema": MAIL_SCHEMA,
            },
        )

    async def provider_capabilities(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or MAIL_NAMESPACE,
            capabilities={
                "list": True,
                "search": True,
                "get": True,
                "upsert": False,
                "delete": False,
                "actions": [ACTION_DOWNLOAD_ATTACHMENTS, ACTION_SEND, ACTION_FORWARD, ACTION_REQUEST_UPLOAD, ACTION_DISCARD_UPLOAD],
                "providers": MAIL_PROVIDER_CATALOG,
                "grant_hints": MAIL_GRANT_HINTS,
                "connected_account_claims": MAIL_SCHEMA["connected_account_claims"],
            },
        )

    async def object_schema(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or MAIL_NAMESPACE,
            extra={"schema": MAIL_SCHEMA},
        )

    async def object_list(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        filters = dict(request.filters or {})
        provider_filter = _text(filters.get("provider")).lower()
        items: list[dict[str, Any]] = []
        if not provider_filter or provider_filter == "gmail":
            for account in await self._gmail_accounts(ctx):
                items.append(_account_object(account, provider_key="gmail"))
        extra: dict[str, Any] = {"count": len(items), "providers": ["gmail"]}
        if not items:
            extra["consent"] = self._connect_hint(ctx, request)
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or MAIL_NAMESPACE,
            items=items,
            extra=extra,
        )

    async def object_search(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        filters = dict(request.filters or {})
        provider_key = _text(filters.get("provider") or "gmail").lower()
        if provider_key != "gmail":
            return NamedServiceResponse.error_response(
                code="mail_provider_not_implemented",
                message=f"Mail provider is not implemented yet: {provider_key}",
                status=501,
                provider=self._provider_identity(),
                namespace=request.namespace or MAIL_NAMESPACE,
            )
        query = _text(filters.get("gmail_query") or request.query)
        account_id = _text(filters.get("account_id") or request.payload.get("account_id"))
        limit = _int(request.limit, default=5, maximum=10)
        accounts, consent = await self._accounts_for_claim(
            ctx, request, claim=GMAIL_READ_CLAIM, account_id=account_id
        )
        if consent is not None:
            return consent

        items: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        per_account_limit = max(1, min(limit, 10))
        for account in accounts:
            account_label = account.display_name or account.email or account.account_id
            result = await self._gmail.search_gmail(
                query=query,
                max_results=per_account_limit,
                account_id=account.account_id,
            )
            if not isinstance(result, Mapping) or not result.get("ok"):
                errors.append({
                    "account_id": account.account_id,
                    "account_label": account_label,
                    "error": result.get("error") if isinstance(result, Mapping) else "gmail_search_failed",
                    "ret": result.get("ret") if isinstance(result, Mapping) else None,
                })
                continue
            ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
            resolved_account_id = _text(ret.get("account_id") or account.account_id)
            for row in ret.get("messages") or []:
                if isinstance(row, Mapping):
                    items.append(
                        _message_object(
                            row,
                            provider_key="gmail",
                            account_id=resolved_account_id,
                            account_label=account_label,
                        )
                    )

        if not items and errors:
            first = errors[0]
            return tool_error_response(
                {"error": first.get("error"), "ret": first.get("ret")},
                request=request,
                namespace=MAIL_NAMESPACE,
                provider_identity=self._provider_identity(),
                default_code="gmail_search_failed",
                fallback_message="Gmail search failed.",
                extra_details={"account_errors": errors},
            )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or MAIL_NAMESPACE,
            items=items[:limit],
            warnings=[{"code": "mail_account_error", "message": str(err)} for err in errors] or None,
            extra={"query": query, "provider": "gmail", "count": len(items[:limit]), "searched_accounts": len(accounts)},
        )

    async def object_get(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        parsed = parse_mail_ref(request.object_ref or "")
        if parsed.get("kind") == "account":
            accounts = await self._gmail_accounts(ctx)
            account = next((item for item in accounts if item.account_id == parsed.get("account_id")), None)
            if account is None:
                return NamedServiceResponse.error_response(
                    code="mail_account_not_found",
                    message="Connected mail account was not found.",
                    status=404,
                    provider=self._provider_identity(),
                    namespace=request.namespace or MAIL_NAMESPACE,
                    object_ref=request.object_ref,
                )
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or MAIL_NAMESPACE,
                object_ref=request.object_ref,
                object=_account_object(account, provider_key=parsed.get("provider") or "gmail"),
            )
        if parsed.get("kind") == "attachment" and parsed.get("provider") == "gmail":
            result = await self._gmail.read_gmail_message(
                message_id=parsed["message_id"],
                include_html=False,
                max_body_chars=1,
                account_id=parsed["account_id"],
            )
            if not isinstance(result, Mapping) or not result.get("ok"):
                return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="gmail_read_failed")
            ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
            rows = [*(ret.get("attachments") or []), *(ret.get("inline_attachments") or [])]
            # Refs carry the stable part id; accept a same-fetch attachment id too.
            row = next(
                (item for item in rows if isinstance(item, Mapping) and _text(item.get("part_id")) == parsed["attachment_id"]),
                None,
            ) or next(
                (item for item in rows if isinstance(item, Mapping) and _text(item.get("attachment_id")) == parsed["attachment_id"]),
                None,
            )
            if row is None:
                return NamedServiceResponse.error_response(
                    code="mail_attachment_not_found",
                    message="The message does not carry the requested attachment.",
                    status=404,
                    provider=self._provider_identity(),
                    namespace=request.namespace or MAIL_NAMESPACE,
                    object_ref=request.object_ref,
                )
            url_info = await self._download_url(ctx, ref=request.object_ref)
            obj = {
                "ref": request.object_ref,
                "object_ref": request.object_ref,
                "object_kind": MAIL_ATTACHMENT_KIND,
                "provider": "gmail",
                "account_id": parsed["account_id"],
                "message_id": parsed["message_id"],
                "attachment_id": parsed["attachment_id"],
                "filename": _text(row.get("filename")) or "attachment.bin",
                "mime_type": _text(row.get("mime_type")),
                "size_bytes": row.get("size_bytes", 0),
                "inline": bool(row.get("inline")),
                "download": self._attachment_download_field(url_info),
            }
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or MAIL_NAMESPACE,
                object_ref=request.object_ref,
                object=obj,
            )
        if parsed.get("kind") != "message" or parsed.get("provider") != "gmail":
            return NamedServiceResponse.error_response(
                code="mail_message_ref_required",
                message="object_ref must be mail:gmail:<account_id>:message:<message_id>.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or MAIL_NAMESPACE,
                object_ref=request.object_ref,
            )
        include_html = bool(request.filters.get("include_html") or request.payload.get("include_html"))
        max_body_chars = _int(request.filters.get("max_body_chars") or request.payload.get("max_body_chars"), default=12000, maximum=24000)
        result = await self._gmail.read_gmail_message(
            message_id=parsed["message_id"],
            include_html=include_html,
            max_body_chars=max_body_chars,
            account_id=parsed["account_id"],
        )
        if not isinstance(result, Mapping) or not result.get("ok"):
            return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="gmail_read_failed")
        ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
        resolved_account_id = _text(ret.get("account_id") or parsed["account_id"])
        known = next(
            (item for item in await self._gmail_accounts(ctx) if item.account_id == resolved_account_id),
            None,
        )
        account_label = (known.display_name or known.email) if known else ""
        obj = _message_object(
            ret,
            provider_key="gmail",
            account_id=resolved_account_id,
            account_label=account_label,
        )
        obj.update(
            {
                "body_text": ret.get("body_text", ""),
                "body_text_truncated": bool(ret.get("body_text_truncated")),
                "usage": ret.get("usage") or {},
            }
        )
        if include_html:
            obj["body_html"] = ret.get("body_html", "")
            obj["body_html_truncated"] = bool(ret.get("body_html_truncated"))
        for row in obj.get("attachments") or []:
            if isinstance(row, dict):
                selector = _text(row.get("part_id")) or _text(row.get("attachment_id"))
                row.setdefault("ref", attachment_ref("gmail", obj["account_id"], obj["message_id"], selector))
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or MAIL_NAMESPACE,
            object_ref=obj.get("ref") or request.object_ref,
            object=obj,
        )

    async def object_action(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        action = _text(request.action or request.payload.get("action")).lower()
        payload = dict(request.payload or {})
        parsed = parse_mail_ref(request.object_ref or "")
        if action == ACTION_DOWNLOAD_ATTACHMENTS:
            if parsed.get("kind") != "message" or parsed.get("provider") != "gmail":
                return NamedServiceResponse.error_response(
                    code="mail_message_ref_required",
                    message="download_attachments requires object_ref mail:gmail:<account_id>:message:<message_id>.",
                    status=400,
                    provider=self._provider_identity(),
                    namespace=request.namespace or MAIL_NAMESPACE,
                    object_ref=request.object_ref,
                )
            result = await self._gmail.download_gmail_attachments(
                message_id=parsed["message_id"],
                attachment_ids=payload.get("attachment_ids") or "",
                include_inline=bool(payload.get("include_inline")),
                max_attachments=_int(payload.get("max_attachments"), default=10, maximum=20),
                max_bytes_per_attachment=_int(payload.get("max_bytes_per_attachment"), default=25 * 1024 * 1024, maximum=25 * 1024 * 1024),
                visibility=_text(payload.get("visibility") or "external"),
                account_id=parsed["account_id"],
            )
            if isinstance(result, Mapping) and not result.get("ok"):
                error = result.get("error") if isinstance(result.get("error"), Mapping) else {}
                if _text(error.get("code")) == "artifact_workspace_unavailable":
                    # Transports without a chat turn (MCP) cannot host KDCube
                    # files; deliver every requested attachment as a signed URL.
                    return await self._download_attachments_as_urls(ctx, request, parsed=parsed, payload=payload)
            if not isinstance(result, Mapping) or not result.get("ok"):
                return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="gmail_download_failed")
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or MAIL_NAMESPACE,
                object_ref=request.object_ref,
                extra={"action": action, "result": result},
                ret={"attrs": {"action": action}, "extra": result.get("ret") or result},
            )

        if action == ACTION_REQUEST_UPLOAD:
            return await self._request_upload(ctx, request)

        if action == ACTION_DISCARD_UPLOAD:
            return self._discard_upload(ctx, request)

        if action == ACTION_SEND:
            account_id = _text(payload.get("account_id") or parsed.get("account_id"))
            entries = [item for item in _as_list(payload.get("attachments")) if isinstance(item, Mapping)]

            async def _send(attachment_paths: Any) -> Any:
                return await self._gmail.send_gmail(
                    to=_text(payload.get("to")),
                    subject=_text(payload.get("subject") or "KDCube message"),
                    body_markdown=_text(payload.get("body_markdown") or payload.get("body")),
                    cc=_text(payload.get("cc")),
                    bcc=_text(payload.get("bcc")),
                    body_html=_text(payload.get("body_html")),
                    attachment_paths=attachment_paths,
                    account_id=account_id,
                )

            if entries:
                try:
                    resolved, consumed = resolve_payload_file_entries(entries, staging_root=self._staging_root())
                    with inline_files_workspace() as artifact_root:
                        staged = materialize_inline_files(artifact_root, resolved)
                        result = await _send(self._merged_attachment_paths(payload, staged))
                except InlineFileError as exc:
                    return self._inline_error(request, exc)
                if isinstance(result, Mapping) and result.get("ok"):
                    root = self._staging_root()
                    for ref in consumed if root is not None else []:
                        delete_staged(root, ref)
            else:
                result = await _send(payload.get("attachment_paths") or "")
            if not isinstance(result, Mapping) or not result.get("ok"):
                return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="gmail_send_failed")
            ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
            obj = _message_object(ret, provider_key="gmail", account_id=_text(ret.get("account_id") or account_id))
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or MAIL_NAMESPACE,
                object_ref=obj.get("ref") or request.object_ref,
                object=obj,
                extra={"action": action, "result": ret},
            )

        if action == ACTION_FORWARD:
            if parsed.get("kind") != "message" or parsed.get("provider") != "gmail":
                return NamedServiceResponse.error_response(
                    code="mail_message_ref_required",
                    message="forward requires object_ref mail:gmail:<account_id>:message:<message_id>.",
                    status=400,
                    provider=self._provider_identity(),
                    namespace=request.namespace or MAIL_NAMESPACE,
                    object_ref=request.object_ref,
                )
            entries = [item for item in _as_list(payload.get("attachments")) if isinstance(item, Mapping)]

            async def _forward(attachment_paths: Any) -> Any:
                return await self._gmail.forward_gmail_message(
                    message_id=parsed["message_id"],
                    to=_text(payload.get("to")),
                    note_markdown=_text(payload.get("note_markdown") or payload.get("note")),
                    cc=_text(payload.get("cc")),
                    bcc=_text(payload.get("bcc")),
                    include_original_attachments=bool(payload.get("include_original_attachments")),
                    attachment_paths=attachment_paths,
                    account_id=parsed["account_id"],
                )

            if entries:
                try:
                    resolved, consumed = resolve_payload_file_entries(entries, staging_root=self._staging_root())
                    with inline_files_workspace() as artifact_root:
                        staged = materialize_inline_files(artifact_root, resolved)
                        result = await _forward(self._merged_attachment_paths(payload, staged))
                except InlineFileError as exc:
                    return self._inline_error(request, exc)
                if isinstance(result, Mapping) and result.get("ok"):
                    root = self._staging_root()
                    for ref in consumed if root is not None else []:
                        delete_staged(root, ref)
            else:
                result = await _forward(payload.get("attachment_paths") or "")
            if not isinstance(result, Mapping) or not result.get("ok"):
                return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="gmail_forward_failed")
            ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
            obj = _message_object(ret, provider_key="gmail", account_id=_text(ret.get("account_id") or parsed["account_id"]))
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or MAIL_NAMESPACE,
                object_ref=obj.get("ref") or request.object_ref,
                object=obj,
                extra={"action": action, "result": ret},
            )

        return NamedServiceResponse.error_response(
            code="mail_action_not_supported",
            message=f"Unsupported mail action: {action or '<missing>'}.",
            status=400,
            provider=self._provider_identity(),
            namespace=request.namespace or MAIL_NAMESPACE,
            object_ref=request.object_ref,
        )


def make_mail_named_service_provider(
    *,
    entrypoint: Any = None,
    bundle_id: str | None = None,
    connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    file_url_factory: Any = None,
    upload_slot_factory: Any = None,
) -> MailNamedServiceProvider:
    return MailNamedServiceProvider(
        entrypoint=entrypoint,
        bundle_id=bundle_id,
        connection_hub_bundle_id=connection_hub_bundle_id,
        file_url_factory=file_url_factory,
        upload_slot_factory=upload_slot_factory,
    )


__all__ = [
    "ACTION_DOWNLOAD_ATTACHMENTS",
    "ACTION_FORWARD",
    "ACTION_REQUEST_UPLOAD",
    "ACTION_DISCARD_UPLOAD",
    "ACTION_SEND",
    "MAIL_ACCOUNT_KIND",
    "MAIL_ATTACHMENT_KIND",
    "MAIL_GRANT_HINTS",
    "MAIL_MESSAGE_KIND",
    "MAIL_NAMESPACE",
    "MAIL_PROVIDER_CATALOG",
    "MAIL_SCHEMA",
    "MailNamedServiceProvider",
    "account_ref",
    "attachment_ref",
    "mail_named_service_spec",
    "make_mail_named_service_provider",
    "message_ref",
    "parse_mail_ref",
]
