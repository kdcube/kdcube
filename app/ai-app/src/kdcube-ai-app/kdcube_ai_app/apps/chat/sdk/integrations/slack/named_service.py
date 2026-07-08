# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Slack named-service integration.

The ``slack`` namespace exposes user-connected Slack workspaces through the
standard named-service operations. It is intentionally a thin adapter over
``integrations.slack.tools``: provider-specific API calls and connected-account
claim checks stay there, while this module gives MCP/named-service clients a
stable object model.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.slack.tools import (
    SLACK_ASSISTANT_SEARCH_CLAIM,
    SLACK_CHANNELS_CLAIM,
    SLACK_CONNECTOR_APP_ID,
    SLACK_FILES_READ_CLAIM,
    SLACK_FILES_WRITE_CLAIM,
    SLACK_HISTORY_CLAIM,
    SLACK_POST_CLAIM,
    SLACK_PROVIDER_ID,
    SLACK_SEARCH_CLAIM,
    SlackTools,
    bind_integrations as bind_slack_integrations,
    bind_service as bind_slack_service,
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


LOGGER = logging.getLogger("kdcube.sdk.integrations.slack.named_service")

SLACK_NAMESPACE = "slack"
PROVIDER_ID = "sdk.integrations.slack"
SLACK_ACCOUNT_KIND = "slack.account"
SLACK_CHANNEL_KIND = "slack.channel"
SLACK_MESSAGE_KIND = "slack.message"
SLACK_FILE_KIND = "slack.file"
SLACK_SEARCH_RESULT_KIND = "slack.search_result"
SLACK_TRANSPORTS = (TRANSPORT_LOCAL, TRANSPORT_API)

ACTION_POST_MESSAGE = "post_message"
ACTION_UPLOAD_FILE = "upload_file"
ACTION_DOWNLOAD_FILE = "download_file"
ACTION_ASSISTANT_SEARCH_INFO = "assistant_search_info"
ACTION_REQUEST_UPLOAD = "request_upload"
ACTION_DISCARD_UPLOAD = "discard_upload"

SLACK_GRANT_HINTS = {
    "object.list": ["slack:read"],
    "object.search": ["slack:read"],
    "object.get": ["slack:read"],
    "object.action.download_file": ["slack:read"],
    "object.action.assistant_search_info": ["slack:read"],
    "object.action.post_message": ["slack:write"],
    "object.action.upload_file": ["slack:write"],
}

SLACK_CONNECTED_ACCOUNT_CLAIMS = {
    "search": SLACK_SEARCH_CLAIM,
    "channels": SLACK_CHANNELS_CLAIM,
    "history": SLACK_HISTORY_CLAIM,
    "files_read": SLACK_FILES_READ_CLAIM,
    "files_write": SLACK_FILES_WRITE_CLAIM,
    "post": SLACK_POST_CLAIM,
    "assistant_search": SLACK_ASSISTANT_SEARCH_CLAIM,
}

# Machine-readable connected-account requirements for catalog consumers (the
# composer menu's proactive consent). One flat claim set: the Slack realm
# does not differentiate claims per operation, so consumers show the whole
# set. Same constants `_resolve_claim` uses.
SLACK_CONNECTED_ACCOUNT_REQUIREMENTS = [
    {
        "provider_id": SLACK_PROVIDER_ID,
        "connector_app_id": SLACK_CONNECTOR_APP_ID,
        "provider_label": "Slack",
        "claims": sorted(set(SLACK_CONNECTED_ACCOUNT_CLAIMS.values())),
        "claim_labels": {
            SLACK_SEARCH_CLAIM: "search messages",
            SLACK_CHANNELS_CLAIM: "list channels",
            SLACK_HISTORY_CLAIM: "read history",
            SLACK_FILES_READ_CLAIM: "read files",
            SLACK_FILES_WRITE_CLAIM: "upload files",
            SLACK_POST_CLAIM: "post messages",
            SLACK_ASSISTANT_SEARCH_CLAIM: "assistant search",
        },
    }
]

SLACK_SEARCH_FILTERS = {
    "account_id": {
        "type": "string",
        "description": "Optional connected Slack account/workspace id. Omit to use any account that has the required claim.",
    },
    "search_api": {
        "type": "string",
        "description": "Search API to use: messages for Slack message search, assistant for Slack AI assistant search.",
        "default": "messages",
    },
    "content_types": {
        "type": "string",
        "description": "Assistant search only. Comma-separated content types: messages,files,channels,users.",
        "default": "messages,files",
    },
    "channel_types": {
        "type": "string",
        "description": "Assistant search/listing filter. Comma-separated: public_channel,private_channel,mpim,im.",
        "default": "public_channel,private_channel",
    },
    "context_channel_id": {
        "type": "string",
        "description": "Assistant search only. Optional Slack channel id to scope search context.",
    },
}

SLACK_SEARCH_SCOPES = (
    NamedServiceSearchScope(
        namespace=SLACK_NAMESPACE,
        label="Slack messages and files",
        object_kind=SLACK_SEARCH_RESULT_KIND,
        description="Search Slack messages/files visible to a user-connected Slack account.",
        filters_schema=SLACK_SEARCH_FILTERS,
    ),
)

SLACK_INTRO = (
    "Use namespace `slack` for user-connected Slack workspaces. Start with "
    "object.list to see connected Slack accounts or channels, object.search to "
    "search messages/files, object.get to read channel history or download a "
    "file, and object.action to post messages or upload files."
)

# Human layer of the realm's self-description — the same contract the agent
# reads via provider.about/schema, in user terms. The picker renders these
# verbatim; missing text here is a realm defect, never a UI invention.
SLACK_PRESENTATION = {
    "about": "Search, read, and post in the Slack workspaces you connect.",
    "third_party": "Works with your Slack workspace through your connected Slack account.",
    "operations": {
        "provider.about": {"label": "Service overview", "description": "What this Slack service does and how to use it."},
        "provider.capabilities": {"label": "Capabilities", "description": "The operations and behaviors this service declares."},
        "object.list": {"label": "List accounts & channels", "description": "List your connected Slack accounts and the channels they can see."},
        "object.search": {"label": "Search messages", "description": "Search messages across channels you can see."},
        "object.get": {"label": "Read history & files", "description": "Read channel history or fetch a shared file."},
        "object.schema": {"label": "Object reference", "description": "The shapes and refs of this service's objects."},
    },
    "actions": {
        ACTION_POST_MESSAGE: {"label": "Post a message", "description": "Post a message to a Slack channel as you."},
        ACTION_UPLOAD_FILE: {"label": "Upload a file", "description": "Upload a file into a Slack channel."},
        ACTION_DOWNLOAD_FILE: {"label": "Download a file", "description": "Save a shared Slack file."},
        ACTION_ASSISTANT_SEARCH_INFO: {"label": "Assistant search availability", "description": "Check whether Slack AI assistant search is available in your workspace."},
        ACTION_REQUEST_UPLOAD: {"label": "Attach a file", "description": "Stage one outbound file for an upload."},
        ACTION_DISCARD_UPLOAD: {"label": "Discard staged file", "description": "Remove a staged outbound file before it is used."},
    },
}

SLACK_SCHEMA = {
    "namespace": SLACK_NAMESPACE,
    "refs": {
        "account": "slack:<account_id>",
        "channel": "slack:<account_id>:channel:<channel_id>",
        "message": "slack:<account_id>:message:<channel_id>:<timestamp>",
        "file": "slack:<account_id>:file:<file_id>",
    },
    "object_kinds": {
        SLACK_ACCOUNT_KIND: {
            "description": "One Slack workspace/account connected by the current KDCube user.",
            "fields": ["ref", "account_id", "label", "workspace", "email", "claims", "credential_status"],
        },
        SLACK_CHANNEL_KIND: {
            "description": "One Slack conversation/channel visible to the connected account.",
            "fields": ["ref", "account_id", "account_label", "channel_id", "name", "is_private", "is_member", "topic", "purpose"],
        },
        SLACK_MESSAGE_KIND: {
            "description": "One Slack message returned from channel history.",
            "fields": ["ref", "account_id", "channel_id", "timestamp", "user", "text", "files"],
        },
        SLACK_FILE_KIND: {
            "description": "One Slack file metadata object, materialized to a KDCube artifact in chat or delivered as a short-lived download url on turn-less transports.",
            "fields": ["ref", "account_id", "file_id", "name", "mime_type", "size_bytes", "artifact_path", "download"],
        },
        SLACK_SEARCH_RESULT_KIND: {
            "description": "One Slack search result from message search or Slack AI assistant search.",
            "fields": ["ref", "account_id", "account_label", "text", "channel_id", "timestamp", "permalink", "raw"],
        },
    },
    "account_selection": ACCOUNT_SELECTION_CONTRACT,
    "consent_errors": CONSENT_ERROR_CONTRACT,
    "files": {
        "get": (
            "object.get on a file ref materializes the file as a KDCube artifact in chat; "
            "on transports without a chat turn it returns download {encoding, url, expires_at} — "
            "fetch the short-lived url over plain HTTP out-of-band."
        ),
    },
    "search": {"filters": SLACK_SEARCH_FILTERS},
    "actions": {
        ACTION_POST_MESSAGE: {
            "description": "Post a message to a Slack channel.",
            "object_ref": "slack:<account_id>:channel:<channel_id> or omit and pass payload.channel",
            "payload": ["channel", "text", "thread_ts", "account_id"],
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
                "Reserve an upload slot for one file destined for Slack. Returns "
                "{upload_url, staged_ref, expires_at}: PUT/POST the raw file bytes to "
                "upload_url over plain HTTP, then pass the staged_ref to upload_file. "
                "This is THE way to bring files in — bytes never ride inside tool calls."
            ),
            "object_ref": "slack:<account_id> (any connected account ref)",
            "payload": ["filename", "mime"],
        },
        ACTION_UPLOAD_FILE: {
            "description": (
                "Upload a file to Slack. In chat pass a KDCube artifact file_path; "
                "elsewhere pass staged_ref from request_upload (preferred) or tiny "
                "inline content_base64 with a filename (10MB limit)."
            ),
            "object_ref": "slack:<account_id>:channel:<channel_id> or omit and pass payload.channel",
            "payload": ["channel", "file_path", "staged_ref", "content_base64", "title", "initial_comment", "thread_ts", "filename", "account_id"],
        },
        ACTION_DOWNLOAD_FILE: {
            "description": "Download a Slack file into KDCube artifacts.",
            "object_ref": "slack:<account_id>:file:<file_id>",
            "payload": ["save", "max_bytes", "account_id"],
        },
        ACTION_ASSISTANT_SEARCH_INFO: {
            "description": "Check whether Slack AI assistant search is enabled for the connected workspace.",
            "object_ref": "slack:<account_id> or omit and pass payload.account_id",
            "payload": ["account_id"],
        },
    },
    "grant_hints": SLACK_GRANT_HINTS,
    "connected_account_claims": SLACK_CONNECTED_ACCOUNT_CLAIMS,
}


def _operations() -> dict[str, Any]:
    return {
        PROVIDER_ABOUT: {"transports": SLACK_TRANSPORTS},
        PROVIDER_CAPABILITIES: {"transports": SLACK_TRANSPORTS},
        OBJECT_LIST: {"transports": SLACK_TRANSPORTS},
        OBJECT_SEARCH: {"transports": SLACK_TRANSPORTS},
        OBJECT_GET: {"transports": SLACK_TRANSPORTS},
        OBJECT_SCHEMA: {"transports": SLACK_TRANSPORTS},
        OBJECT_ACTION: {"transports": SLACK_TRANSPORTS},
    }


def slack_named_service_spec(*, bundle_id: str | None = None) -> NamedServiceProviderSpec:
    return NamedServiceProviderSpec(
        provider_id=PROVIDER_ID,
        bundle_id=bundle_id,
        namespace=SLACK_NAMESPACE,
        refs=("slack:*",),
        object_kinds=(
            SLACK_ACCOUNT_KIND,
            SLACK_CHANNEL_KIND,
            SLACK_MESSAGE_KIND,
            SLACK_FILE_KIND,
            SLACK_SEARCH_RESULT_KIND,
        ),
        search_scopes=SLACK_SEARCH_SCOPES,
        operations=_operations(),
        label="Slack",
        description="Slack namespace over user-connected Slack workspaces.",
        intro=SLACK_INTRO,
        metadata={
            "grant_hints": SLACK_GRANT_HINTS,
            "connected_account_claims": SLACK_CONNECTED_ACCOUNT_CLAIMS,
            "connected_accounts": SLACK_CONNECTED_ACCOUNT_REQUIREMENTS,
            "actions": {
                name: str((meta or {}).get("description") or "").strip()
                for name, meta in (SLACK_SCHEMA.get("actions") or {}).items()
            },
            "presentation": SLACK_PRESENTATION,
            "object_kinds": {
                kind: str((meta or {}).get("description") or "").strip()
                for kind, meta in (SLACK_SCHEMA.get("object_kinds") or {}).items()
            },
            "canonical_refs": SLACK_SCHEMA["refs"],
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


def _bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = _text(value).lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def account_ref(account_id: str) -> str:
    return f"{SLACK_NAMESPACE}:{_text(account_id)}"


def channel_ref(account_id: str, channel_id: str) -> str:
    return f"{account_ref(account_id)}:channel:{_text(channel_id)}"


def message_ref(account_id: str, channel_id: str, timestamp: str) -> str:
    return f"{account_ref(account_id)}:message:{_text(channel_id)}:{_text(timestamp)}"


def file_ref(account_id: str, file_id: str) -> str:
    return f"{account_ref(account_id)}:file:{_text(file_id)}"


def parse_slack_ref(ref: str) -> dict[str, str]:
    parts = _text(ref).split(":")
    if len(parts) < 2 or parts[0] != SLACK_NAMESPACE:
        return {}
    parsed = {"account_id": parts[1], "kind": "account"}
    if len(parts) >= 4 and parts[2] == "channel":
        parsed.update({"kind": "channel", "channel_id": ":".join(parts[3:])})
    elif len(parts) >= 5 and parts[2] == "message":
        parsed.update({"kind": "message", "channel_id": parts[3], "timestamp": ":".join(parts[4:])})
    elif len(parts) >= 4 and parts[2] == "file":
        parsed.update({"kind": "file", "file_id": ":".join(parts[3:])})
    return parsed


def _account_object(account: ConnectedAccount) -> dict[str, Any]:
    label = account.display_name or account.workspace or account.email or account.external_subject or account.account_id
    return {
        "ref": account_ref(account.account_id),
        "object_ref": account_ref(account.account_id),
        "object_kind": SLACK_ACCOUNT_KIND,
        "id": account.account_id,
        "account_id": account.account_id,
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


def _channel_object(row: Mapping[str, Any], *, account_id: str, account_label: str = "") -> dict[str, Any]:
    channel_id = _text(row.get("id") or row.get("channel_id"))
    ref = channel_ref(account_id, channel_id) if account_id and channel_id else ""
    return {
        "ref": ref,
        "object_ref": ref,
        "object_kind": SLACK_CHANNEL_KIND,
        "id": channel_id,
        "channel_id": channel_id,
        "account_id": account_id,
        "account_label": _text(account_label) or account_id,
        "name": _text(row.get("name")),
        "is_channel": bool(row.get("is_channel")),
        "is_group": bool(row.get("is_group")),
        "is_im": bool(row.get("is_im")),
        "is_mpim": bool(row.get("is_mpim")),
        "is_private": bool(row.get("is_private")),
        "is_archived": bool(row.get("is_archived")),
        "is_member": bool(row.get("is_member")),
        "num_members": row.get("num_members"),
        "topic": _text(row.get("topic")),
        "purpose": _text(row.get("purpose")),
    }


def _file_object(row: Mapping[str, Any], *, account_id: str) -> dict[str, Any]:
    file_id = _text(row.get("id") or row.get("file_id"))
    ref = file_ref(account_id, file_id) if account_id and file_id else ""
    return {
        "ref": ref,
        "object_ref": ref,
        "object_kind": SLACK_FILE_KIND,
        "id": file_id,
        "file_id": file_id,
        "account_id": account_id,
        "name": _text(row.get("name") or row.get("filename")),
        "title": _text(row.get("title")),
        "mime_type": _text(row.get("mimetype") or row.get("mime_type") or row.get("mime")),
        "filetype": _text(row.get("filetype")),
        "size_bytes": row.get("size_bytes", row.get("size", 0)),
        "permalink": _text(row.get("permalink")),
        "artifact_path": _text(row.get("artifact_path") or row.get("logical_path")),
        "text_preview": row.get("text_preview", ""),
        "raw": dict(row),
    }


def _message_object(row: Mapping[str, Any], *, account_id: str, channel_id: str = "") -> dict[str, Any]:
    resolved_channel_id = _text(channel_id or row.get("channel_id") or row.get("channel"))
    timestamp = _text(row.get("timestamp") or row.get("ts"))
    ref = message_ref(account_id, resolved_channel_id, timestamp) if account_id and resolved_channel_id and timestamp else ""
    files = [_file_object(file_row, account_id=account_id) for file_row in (row.get("files") or []) if isinstance(file_row, Mapping)]
    return {
        "ref": ref,
        "object_ref": ref,
        "object_kind": SLACK_MESSAGE_KIND,
        "id": timestamp,
        "timestamp": timestamp,
        "channel_id": resolved_channel_id,
        "account_id": account_id,
        "user": _text(row.get("user")),
        "username": _text(row.get("username")),
        "bot_id": _text(row.get("bot_id")),
        "text": _text(row.get("text")),
        "thread_ts": _text(row.get("thread_ts")),
        "reply_count": row.get("reply_count"),
        "files": files,
        "file_count": len(files),
        "raw": dict(row),
    }


def _search_result_object(row: Mapping[str, Any], *, account_id: str, index: int, account_label: str = "") -> dict[str, Any]:
    channel_id = _text(row.get("channel_id") or row.get("channel"))
    timestamp = _text(row.get("timestamp") or row.get("ts"))
    file_id = _text(row.get("file_id") or row.get("id")) if _text(row.get("type")).lower() == "file" else ""
    ref = (
        file_ref(account_id, file_id)
        if file_id
        else message_ref(account_id, channel_id, timestamp)
        if channel_id and timestamp
        else f"{account_ref(account_id)}:search:{index}"
    )
    return {
        "ref": ref,
        "object_ref": ref,
        "object_kind": SLACK_SEARCH_RESULT_KIND,
        "id": _text(row.get("id") or timestamp or index),
        "account_id": account_id,
        "account_label": _text(account_label) or account_id,
        "channel_id": channel_id,
        "channel_name": _text(row.get("channel_name")),
        "timestamp": timestamp,
        "user": _text(row.get("user")),
        "text": _text(row.get("text") or row.get("snippet") or row.get("title")),
        "permalink": _text(row.get("permalink")),
        "raw": dict(row),
    }


def _error_from_tool(
    result: Mapping[str, Any],
    *,
    request: NamedServiceRequest,
    default_code: str = "slack_operation_failed",
) -> NamedServiceResponse:
    return tool_error_response(
        result,
        request=request,
        namespace=SLACK_NAMESPACE,
        provider_identity={"provider_id": PROVIDER_ID},
        default_code=default_code,
        fallback_message="Slack operation failed.",
    )


@named_service_provider(
    provider_id=PROVIDER_ID,
    namespace=SLACK_NAMESPACE,
    refs=("slack:*",),
    object_kinds=(
        SLACK_ACCOUNT_KIND,
        SLACK_CHANNEL_KIND,
        SLACK_MESSAGE_KIND,
        SLACK_FILE_KIND,
        SLACK_SEARCH_RESULT_KIND,
    ),
    search_scopes=SLACK_SEARCH_SCOPES,
    operations=_operations(),
    label="Slack",
    description="Slack namespace over user-connected Slack workspaces.",
    intro=SLACK_INTRO,
    metadata={"grant_hints": SLACK_GRANT_HINTS, "connected_account_claims": SLACK_CONNECTED_ACCOUNT_CLAIMS},
)
class SlackNamedServiceProvider(NamedServiceProvider):
    def __init__(
        self,
        *,
        entrypoint: Any = None,
        bundle_id: str | None = None,
        connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
        file_url_factory: Any = None,
        upload_slot_factory: Any = None,
    ) -> None:
        super().__init__(slack_named_service_spec(bundle_id=bundle_id))
        self._entrypoint = entrypoint
        self._connection_hub_bundle_id = connection_hub_bundle_id
        self._file_url_factory = file_url_factory
        self._upload_slot_factory = upload_slot_factory
        self._slack = SlackTools()
        if entrypoint is not None:
            bind_slack_service(entrypoint)
            bind_slack_integrations({"comm_context": getattr(entrypoint, "comm_context", None)})

    def _provider_identity(self) -> dict[str, Any]:
        return {"provider_id": PROVIDER_ID, "bundle_id": self.spec.bundle_id}

    async def _download_url(self, ctx: NamedServiceContext, *, ref: str) -> dict[str, Any] | None:
        """Short-lived signed download URL for one file ref, or None when the
        hosting bundle provides no delivery path (no factory / no secret /
        unknown public origin)."""
        if self._file_url_factory is None:
            return None
        try:
            out = self._file_url_factory(ctx, {"ref": ref})
            if hasattr(out, "__await__"):
                out = await out
        except Exception:
            LOGGER.exception("slack download url factory failed for %s", ref)
            return None
        return dict(out) if isinstance(out, Mapping) and out.get("url") else None

    async def _file_as_url_object(
        self,
        ctx: NamedServiceContext,
        request: NamedServiceRequest,
        *,
        account_id: str,
        file_id: str,
        tool_result: Mapping[str, Any],
    ) -> NamedServiceResponse:
        """URL delivery for a Slack file on turn-less transports.

        Falls back to the original tool error when no delivery path exists."""
        ref = file_ref(account_id, file_id)
        url_info = await self._download_url(ctx, ref=ref)
        if url_info is None:
            return _error_from_tool(dict(tool_result), request=request, default_code="slack_file_download_failed")
        obj = {
            "ref": ref,
            "object_ref": ref,
            "object_kind": SLACK_FILE_KIND,
            "id": file_id,
            "file_id": file_id,
            "account_id": account_id,
            "download": {"encoding": "url", **url_info},
        }
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SLACK_NAMESPACE,
            object_ref=ref,
            object=obj,
            extra={
                "delivery": "url",
                "note": "No chat turn on this transport; fetch download.url over HTTP out-of-band.",
            },
        )

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
                namespace=request.namespace or SLACK_NAMESPACE,
                object_ref=request.object_ref,
            )
        root = self._staging_root()
        if root is not None:
            delete_staged(root, staged_ref)
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SLACK_NAMESPACE,
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
                namespace=request.namespace or SLACK_NAMESPACE,
                object_ref=request.object_ref,
            )
        slot = None
        if self._upload_slot_factory is not None:
            try:
                slot = self._upload_slot_factory(ctx, {"filename": filename, "mime": _text(payload.get("mime"))})
                if hasattr(slot, "__await__"):
                    slot = await slot
            except Exception:
                LOGGER.exception("slack upload slot factory failed")
                slot = None
        if not isinstance(slot, Mapping) or not slot.get("upload_url"):
            return NamedServiceResponse.error_response(
                code="upload_not_configured",
                message="This deployment has no upload path configured; use tiny inline content_base64 instead.",
                status=503,
                provider=self._provider_identity(),
                namespace=request.namespace or SLACK_NAMESPACE,
                object_ref=request.object_ref,
            )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SLACK_NAMESPACE,
            object_ref=request.object_ref,
            extra={
                "action": ACTION_REQUEST_UPLOAD,
                **dict(slot),
                "how": (
                    "POST the raw file bytes to upload_url (body = file, no form encoding), "
                    "then pass staged_ref to the upload_file action."
                ),
            },
        )

    @staticmethod
    def _workspace_unavailable(result: Any) -> bool:
        error = result.get("error") if isinstance(result, Mapping) else None
        return isinstance(error, Mapping) and _text(error.get("code")) == "artifact_workspace_unavailable"

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

    async def _slack_accounts(self, ctx: NamedServiceContext, *, claim: str = "") -> list[ConnectedAccount]:
        client = await self._client(ctx)
        if client is None:
            return []
        accounts = await client.list_accounts(provider_id=SLACK_PROVIDER_ID)
        return [
            account for account in accounts
            if account.connector_app_id == SLACK_CONNECTOR_APP_ID
            and account.connected
            and (not claim or account.allows(claim))
        ]

    async def _resolve_claim(
        self,
        ctx: NamedServiceContext,
        *,
        claim: str,
        account_id: str = "",
    ) -> ClaimResolution:
        """Resolve one Slack claim through the broker.

        The broker mints the distinct resolution reason (connect vs upgrade vs
        reconnect vs account choice) with labeled candidates; this adapter
        never re-derives that. Without a platform user or entrypoint the only
        honest answer is connect_required.
        """
        client = await self._client(ctx)
        if client is None:
            return ClaimResolution(
                ok=False,
                provider_id=SLACK_PROVIDER_ID,
                claim=claim,
                connector_app_id=SLACK_CONNECTOR_APP_ID,
                account_id=account_id,
                error=REASON_CONNECT_REQUIRED,
                message="Connect a Slack account in Connection Hub.",
                retry_hint=True,
            )
        return await client.ensure_claim(
            provider_id=SLACK_PROVIDER_ID,
            connector_app_id=SLACK_CONNECTOR_APP_ID,
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
            namespace=SLACK_NAMESPACE,
            provider_identity=self._provider_identity(),
            connection_hub_bundle_id=self._connection_hub_bundle_id,
            tool_name=f"named_services.{SLACK_NAMESPACE}.{request.operation}",
        )

    def _connect_hint(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> dict[str, Any]:
        """Consent block shipped with an EMPTY account list, so clients learn
        where to connect without treating the empty list as an error."""
        payload = resolution_consent_payload(
            resolution=ClaimResolution(
                ok=False,
                provider_id=SLACK_PROVIDER_ID,
                claim="",
                connector_app_id=SLACK_CONNECTOR_APP_ID,
                error=REASON_CONNECT_REQUIRED,
                message="Connect a Slack account in Connection Hub.",
                retry_hint=True,
            ),
            ctx=ctx,
            connection_hub_bundle_id=self._connection_hub_bundle_id,
            tool_name=f"named_services.{SLACK_NAMESPACE}.{request.operation}",
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
        eligible = await self._slack_accounts(ctx, claim=claim)
        if account_id:
            account = next((item for item in eligible if item.account_id == account_id), None)
            if account is None:
                resolution = await self._resolve_claim(ctx, claim=claim, account_id=account_id)
                if not resolution.ok:
                    return [], self._consent_error(ctx=ctx, request=request, resolution=resolution)
                account = ConnectedAccount(
                    account_id=account_id,
                    provider_id=SLACK_PROVIDER_ID,
                    connector_app_id=SLACK_CONNECTOR_APP_ID,
                )
            return [account], None
        if not eligible:
            resolution = await self._resolve_claim(ctx, claim=claim)
            if not resolution.ok:
                return [], self._consent_error(ctx=ctx, request=request, resolution=resolution)
            return [
                ConnectedAccount(
                    account_id=resolution.account_id,
                    provider_id=SLACK_PROVIDER_ID,
                    connector_app_id=SLACK_CONNECTOR_APP_ID,
                )
            ], None
        return eligible, None

    async def provider_about(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SLACK_NAMESPACE,
            extra={
                "title": "KDCube Slack",
                "description": "Slack namespace over user-connected Slack workspaces.",
                "workflow": [
                    "Call object.list to see connected Slack accounts.",
                    "Call object.list with filters.kind='channels' to list channels.",
                    "Call object.search to search Slack messages/files.",
                    "Call object.get with a slack:<account_id>:channel:<channel_id> ref to read channel history.",
                    "Call object.get with a slack:<account_id>:file:<file_id> ref to download a Slack file.",
                    "Call object.action post_message or upload_file for bounded write actions.",
                ],
                "schema": SLACK_SCHEMA,
            },
        )

    async def provider_capabilities(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SLACK_NAMESPACE,
            capabilities={
                "list": True,
                "search": True,
                "get": True,
                "upsert": False,
                "delete": False,
                "actions": [
                    ACTION_POST_MESSAGE,
                    ACTION_UPLOAD_FILE,
                    ACTION_DOWNLOAD_FILE,
                    ACTION_ASSISTANT_SEARCH_INFO,
                    ACTION_REQUEST_UPLOAD,
                    ACTION_DISCARD_UPLOAD,
                ],
                "grant_hints": SLACK_GRANT_HINTS,
                "connected_account_claims": SLACK_CONNECTED_ACCOUNT_CLAIMS,
            },
        )

    async def object_schema(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SLACK_NAMESPACE,
            extra={"schema": SLACK_SCHEMA},
        )

    async def object_list(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        filters = dict(request.filters or {})
        kind = _text(filters.get("kind") or request.collection or "accounts").lower()
        if kind in {"channel", "channels", "conversation", "conversations"}:
            account_id = _text(filters.get("account_id") or request.payload.get("account_id"))
            accounts, consent = await self._accounts_for_claim(
                ctx, request, claim=SLACK_CHANNELS_CLAIM, account_id=account_id
            )
            if consent is not None:
                return consent
            items: list[dict[str, Any]] = []
            next_cursors: dict[str, str] = {}
            for account in accounts:
                account_label = account.display_name or account.workspace or account.account_id
                result = await self._slack.list_slack_channels(
                    types=_text(filters.get("types") or "public_channel,private_channel"),
                    limit=_int(request.limit or filters.get("limit"), default=50, maximum=200),
                    cursor=_text(request.cursor or filters.get("cursor")),
                    exclude_archived=_bool(filters.get("exclude_archived"), default=True),
                    account_id=account.account_id,
                )
                if not isinstance(result, Mapping) or not result.get("ok"):
                    return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="slack_channel_list_failed")
                ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
                next_cursors[account.account_id] = _text(ret.get("next_cursor"))
                items.extend(
                    _channel_object(row, account_id=account.account_id, account_label=account_label)
                    for row in ret.get("channels") or []
                    if isinstance(row, Mapping)
                )
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or SLACK_NAMESPACE,
                items=items,
                extra={"kind": "channels", "count": len(items), "next_cursors": next_cursors},
            )

        accounts = await self._slack_accounts(ctx)
        extra: dict[str, Any] = {"kind": "accounts", "count": len(accounts)}
        if not accounts:
            extra["consent"] = self._connect_hint(ctx, request)
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SLACK_NAMESPACE,
            items=[_account_object(account) for account in accounts],
            extra=extra,
        )

    async def object_search(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        filters = dict(request.filters or {})
        query = _text(request.query or filters.get("query"))
        if not query:
            return NamedServiceResponse.error_response(
                code="query_required",
                message="Slack search query is required.",
                status=400,
                provider=self._provider_identity(),
                namespace=request.namespace or SLACK_NAMESPACE,
            )
        account_id = _text(filters.get("account_id") or request.payload.get("account_id"))
        search_api = _text(filters.get("search_api") or filters.get("mode") or "messages").lower()
        if search_api in {"assistant", "ai", "semantic"}:
            accounts, consent = await self._accounts_for_claim(
                ctx, request, claim=SLACK_ASSISTANT_SEARCH_CLAIM, account_id=account_id
            )
            if consent is not None:
                return consent
            items: list[dict[str, Any]] = []
            for account in accounts:
                account_label = account.display_name or account.workspace or account.account_id
                result = await self._slack.slack_assistant_search(
                    query=query,
                    content_types=_text(filters.get("content_types") or "messages,files"),
                    channel_types=_text(filters.get("channel_types") or "public_channel,private_channel"),
                    limit=_int(request.limit, default=10, maximum=20),
                    cursor=_text(request.cursor or filters.get("cursor")),
                    context_channel_id=_text(filters.get("context_channel_id")),
                    include_context_messages=_bool(filters.get("include_context_messages")),
                    sort=_text(filters.get("sort") or "score"),
                    sort_dir=_text(filters.get("sort_dir") or "desc"),
                    account_id=account.account_id,
                )
                if not isinstance(result, Mapping) or not result.get("ok"):
                    return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="slack_assistant_search_failed")
                ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
                rows = ret.get("results") or []
                items.extend(
                    _search_result_object(row, account_id=account.account_id, index=index, account_label=account_label)
                    for index, row in enumerate(rows)
                    if isinstance(row, Mapping)
                )
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or SLACK_NAMESPACE,
                items=items[: _int(request.limit, default=10, maximum=50)],
                extra={"query": query, "search_api": "assistant", "count": len(items)},
            )

        accounts, consent = await self._accounts_for_claim(
            ctx, request, claim=SLACK_SEARCH_CLAIM, account_id=account_id
        )
        if consent is not None:
            return consent
        items = []
        for account in accounts:
            account_label = account.display_name or account.workspace or account.account_id
            result = await self._slack.search_slack(
                query=query,
                count=_int(request.limit, default=10, maximum=20),
                account_id=account.account_id,
            )
            if not isinstance(result, Mapping) or not result.get("ok"):
                return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="slack_search_failed")
            ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
            items.extend(
                _search_result_object(row, account_id=account.account_id, index=index, account_label=account_label)
                for index, row in enumerate(ret.get("messages") or [])
                if isinstance(row, Mapping)
            )
        return NamedServiceResponse.ok_response(
            provider=self._provider_identity(),
            namespace=request.namespace or SLACK_NAMESPACE,
            items=items[: _int(request.limit, default=10, maximum=50)],
            extra={"query": query, "search_api": "messages", "count": len(items)},
        )

    async def object_get(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        parsed = parse_slack_ref(request.object_ref or "")
        if parsed.get("kind") == "account":
            accounts = await self._slack_accounts(ctx)
            account = next((item for item in accounts if item.account_id == parsed.get("account_id")), None)
            if account is None:
                return NamedServiceResponse.error_response(
                    code="slack_account_not_found",
                    message="Connected Slack account was not found.",
                    status=404,
                    provider=self._provider_identity(),
                    namespace=request.namespace or SLACK_NAMESPACE,
                    object_ref=request.object_ref,
                )
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or SLACK_NAMESPACE,
                object_ref=request.object_ref,
                object=_account_object(account),
            )

        if parsed.get("kind") == "channel":
            result = await self._slack.read_slack_channel_history(
                channel=parsed["channel_id"],
                limit=_int(request.limit or request.filters.get("limit"), default=20, maximum=100),
                cursor=_text(request.cursor or request.filters.get("cursor")),
                oldest=_text(request.filters.get("oldest")),
                latest=_text(request.filters.get("latest")),
                inclusive=_bool(request.filters.get("inclusive")),
                account_id=parsed["account_id"],
            )
            if not isinstance(result, Mapping) or not result.get("ok"):
                return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="slack_history_failed")
            ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
            messages = [
                _message_object(row, account_id=parsed["account_id"], channel_id=parsed["channel_id"])
                for row in ret.get("messages") or []
                if isinstance(row, Mapping)
            ]
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or SLACK_NAMESPACE,
                object_ref=request.object_ref,
                object={
                    "ref": request.object_ref,
                    "object_ref": request.object_ref,
                    "object_kind": SLACK_CHANNEL_KIND,
                    "account_id": parsed["account_id"],
                    "channel_id": parsed["channel_id"],
                    "messages": messages,
                    "message_count": len(messages),
                    "has_more": bool(ret.get("has_more")),
                    "next_cursor": _text(ret.get("next_cursor")),
                },
            )

        if parsed.get("kind") == "file":
            result = await self._slack.download_slack_file(
                file_id=parsed["file_id"],
                save=_bool(request.filters.get("save"), default=True),
                max_bytes=_int(request.filters.get("max_bytes"), default=25 * 1024 * 1024, maximum=25 * 1024 * 1024),
                account_id=parsed["account_id"],
            )
            if self._workspace_unavailable(result):
                return await self._file_as_url_object(
                    ctx,
                    request,
                    account_id=parsed["account_id"],
                    file_id=parsed["file_id"],
                    tool_result=result,
                )
            if not isinstance(result, Mapping) or not result.get("ok"):
                return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="slack_file_download_failed")
            ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
            file_payload = ret.get("file") if isinstance(ret.get("file"), Mapping) else ret
            obj = _file_object(file_payload if isinstance(file_payload, Mapping) else {}, account_id=parsed["account_id"])
            obj.update({key: value for key, value in ret.items() if key not in {"file"}})
            obj["ref"] = request.object_ref
            obj["object_ref"] = request.object_ref
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or SLACK_NAMESPACE,
                object_ref=request.object_ref,
                object=obj,
            )

        return NamedServiceResponse.error_response(
            code="slack_ref_not_supported",
            message="object_ref must be slack:<account_id>, slack:<account_id>:channel:<channel_id>, or slack:<account_id>:file:<file_id>.",
            status=400,
            provider=self._provider_identity(),
            namespace=request.namespace or SLACK_NAMESPACE,
            object_ref=request.object_ref,
        )

    async def object_action(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        action = _text(request.action or request.payload.get("action")).lower()
        payload = dict(request.payload or {})
        parsed = parse_slack_ref(request.object_ref or "")
        account_id = _text(payload.get("account_id") or parsed.get("account_id"))
        channel_id = _text(payload.get("channel") or parsed.get("channel_id"))

        if action == ACTION_POST_MESSAGE:
            result = await self._slack.post_slack_message(
                channel=channel_id,
                text=_text(payload.get("text") or payload.get("message")),
                thread_ts=_text(payload.get("thread_ts")),
                account_id=account_id,
            )
            if not isinstance(result, Mapping) or not result.get("ok"):
                return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="slack_post_failed")
            ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
            obj = _message_object(ret.get("message") if isinstance(ret.get("message"), Mapping) else ret, account_id=_text(ret.get("account_id") or account_id), channel_id=_text(ret.get("channel") or channel_id))
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or SLACK_NAMESPACE,
                object_ref=obj.get("ref") or request.object_ref,
                object=obj,
                extra={"action": action, "result": ret},
            )

        if action == ACTION_REQUEST_UPLOAD:
            return await self._request_upload(ctx, request)

        if action == ACTION_DISCARD_UPLOAD:
            return self._discard_upload(ctx, request)

        if action == ACTION_UPLOAD_FILE:
            staged_ref = _text(payload.get("staged_ref"))
            inline_content = _text(payload.get("content_base64"))

            async def _upload(file_path: str, filename: str) -> Any:
                return await self._slack.upload_slack_file(
                    channel=channel_id,
                    file_path=file_path,
                    title=_text(payload.get("title")),
                    initial_comment=_text(payload.get("initial_comment") or payload.get("comment")),
                    thread_ts=_text(payload.get("thread_ts")),
                    filename=filename,
                    account_id=account_id,
                )

            if staged_ref or inline_content:
                entry: dict[str, Any] = {
                    "filename": _text(payload.get("filename") or payload.get("title")),
                    "mime": _text(payload.get("mime") or payload.get("mime_type")),
                }
                if staged_ref:
                    entry["staged_ref"] = staged_ref
                else:
                    entry["content_base64"] = inline_content
                try:
                    resolved, consumed = resolve_payload_file_entries([entry], staging_root=self._staging_root())
                    with inline_files_workspace() as artifact_root:
                        staged = materialize_inline_files(artifact_root, resolved)
                        result = await _upload(staged[0]["relpath"], staged[0]["filename"])
                except InlineFileError as exc:
                    return NamedServiceResponse.error_response(
                        code="slack_inline_file_invalid",
                        message=str(exc),
                        status=400,
                        provider=self._provider_identity(),
                        namespace=request.namespace or SLACK_NAMESPACE,
                        object_ref=request.object_ref,
                    )
                if isinstance(result, Mapping) and result.get("ok"):
                    root = self._staging_root()
                    for ref in consumed if root is not None else []:
                        delete_staged(root, ref)
            else:
                result = await _upload(
                    _text(payload.get("file_path") or payload.get("path")),
                    _text(payload.get("filename")),
                )
            if not isinstance(result, Mapping) or not result.get("ok"):
                return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="slack_upload_failed")
            ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
            obj = _file_object(ret, account_id=_text(ret.get("account_id") or account_id))
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or SLACK_NAMESPACE,
                object_ref=obj.get("ref") or request.object_ref,
                object=obj,
                extra={"action": action, "result": ret},
            )

        if action == ACTION_DOWNLOAD_FILE:
            file_id = _text(payload.get("file_id") or parsed.get("file_id"))
            result = await self._slack.download_slack_file(
                file_id=file_id,
                save=_bool(payload.get("save"), default=True),
                max_bytes=_int(payload.get("max_bytes"), default=25 * 1024 * 1024, maximum=25 * 1024 * 1024),
                account_id=account_id,
            )
            if self._workspace_unavailable(result):
                return await self._file_as_url_object(
                    ctx,
                    request,
                    account_id=account_id,
                    file_id=file_id,
                    tool_result=result,
                )
            if not isinstance(result, Mapping) or not result.get("ok"):
                return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="slack_file_download_failed")
            ret = result.get("ret") if isinstance(result.get("ret"), Mapping) else {}
            file_payload = ret.get("file") if isinstance(ret.get("file"), Mapping) else ret
            obj = _file_object(file_payload if isinstance(file_payload, Mapping) else {}, account_id=_text(ret.get("account_id") or account_id))
            obj.update({key: value for key, value in ret.items() if key not in {"file"}})
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or SLACK_NAMESPACE,
                object_ref=obj.get("ref") or request.object_ref,
                object=obj,
                extra={"action": action, "result": ret},
            )

        if action == ACTION_ASSISTANT_SEARCH_INFO:
            result = await self._slack.slack_assistant_search_info(account_id=account_id)
            if not isinstance(result, Mapping) or not result.get("ok"):
                return _error_from_tool(result if isinstance(result, Mapping) else {}, request=request, default_code="slack_assistant_search_info_failed")
            return NamedServiceResponse.ok_response(
                provider=self._provider_identity(),
                namespace=request.namespace or SLACK_NAMESPACE,
                object_ref=request.object_ref,
                extra={"action": action, "result": result.get("ret") or result},
            )

        return NamedServiceResponse.error_response(
            code="slack_action_not_supported",
            message=f"Unsupported Slack action: {action or '<missing>'}.",
            status=400,
            provider=self._provider_identity(),
            namespace=request.namespace or SLACK_NAMESPACE,
            object_ref=request.object_ref,
        )


def make_slack_named_service_provider(
    *,
    entrypoint: Any = None,
    bundle_id: str | None = None,
    connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    file_url_factory: Any = None,
    upload_slot_factory: Any = None,
) -> SlackNamedServiceProvider:
    return SlackNamedServiceProvider(
        entrypoint=entrypoint,
        bundle_id=bundle_id,
        connection_hub_bundle_id=connection_hub_bundle_id,
        file_url_factory=file_url_factory,
        upload_slot_factory=upload_slot_factory,
    )


__all__ = [
    "ACTION_ASSISTANT_SEARCH_INFO",
    "ACTION_DOWNLOAD_FILE",
    "ACTION_POST_MESSAGE",
    "ACTION_REQUEST_UPLOAD",
    "ACTION_DISCARD_UPLOAD",
    "ACTION_UPLOAD_FILE",
    "SLACK_ACCOUNT_KIND",
    "SLACK_CHANNEL_KIND",
    "SLACK_CONNECTED_ACCOUNT_CLAIMS",
    "SLACK_FILE_KIND",
    "SLACK_GRANT_HINTS",
    "SLACK_MESSAGE_KIND",
    "SLACK_NAMESPACE",
    "SLACK_SCHEMA",
    "SLACK_SEARCH_RESULT_KIND",
    "SlackNamedServiceProvider",
    "account_ref",
    "channel_ref",
    "file_ref",
    "make_slack_named_service_provider",
    "message_ref",
    "parse_slack_ref",
    "slack_named_service_spec",
]
