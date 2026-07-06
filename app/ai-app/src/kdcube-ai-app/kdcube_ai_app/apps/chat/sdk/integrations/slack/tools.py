# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Slack tools backed by Connection Hub connected accounts."""

from __future__ import annotations

import mimetypes
import pathlib
import re
from typing import Annotated, Any

import httpx
import semantic_kernel as sk

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

from kdcube_ai_app.apps.chat.sdk.integrations.connected_accounts import (
    ConnectedAccountCredential,
    resolve_connected_account_claim,
)
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for, resolve_artifact_path
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    ARTIFACT_NAMESPACE_FILES,
    build_physical_artifact_path,
    physical_path_to_logical_path,
    split_logical_artifact_ref,
)


SLACK_PROVIDER_ID = "slack"
SLACK_CONNECTOR_APP_ID = "demo"
SLACK_SEARCH_CLAIM = "slack:search"
SLACK_POST_CLAIM = "slack:post"
SLACK_CHANNELS_CLAIM = "slack:channels"
SLACK_HISTORY_CLAIM = "slack:history"
SLACK_FILES_READ_CLAIM = "slack:files:read"
SLACK_FILES_WRITE_CLAIM = "slack:files:write"
SLACK_ASSISTANT_SEARCH_CLAIM = "slack:assistant:search"
SLACK_API = "https://slack.com/api"
MAX_SLACK_FILE_BYTES = 25 * 1024 * 1024
MAX_SLACK_TEXT_PREVIEW_CHARS = 12000

_SERVICE = None
_INTEGRATIONS: dict[str, Any] = {}


def bind_service(svc: Any) -> None:
    global _SERVICE
    _SERVICE = svc


def bind_integrations(integrations: dict[str, Any] | None) -> None:
    global _INTEGRATIONS
    _INTEGRATIONS = dict(integrations or {})


def _ok_ret_result(ret: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, "ret": ret}


def _error_result(*, code: str, message: str, where: str, ret: Any = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "where": where,
            "managed": True,
        },
        "ret": ret,
    }


def _slack_error(data: Any, *, fallback: str) -> str:
    if isinstance(data, dict):
        return str(data.get("error") or data.get("warning") or fallback)
    return fallback


def _is_auth_failure(data: Any, status_code: int = 200) -> bool:
    if status_code in {401, 403}:
        return True
    if isinstance(data, dict):
        return str(data.get("error") or "") in {"invalid_auth", "not_authed", "token_revoked", "account_inactive"}
    return False


def _provider_auth_envelope(
    credential: ConnectedAccountCredential,
    *,
    where: str,
    message: str = "",
) -> dict[str, Any]:
    envelope = credential.consent_required_envelope(
        where=where,
        message="Slack authorization needs to be renewed. Open Connection Hub and reconnect or approve Slack.",
    )
    provider_message = str(message or "").strip()
    if provider_message:
        ret = envelope.get("ret")
        if isinstance(ret, dict):
            ret["provider_error"] = provider_message
    return envelope


def _safe_segment(raw: str, *, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(raw or "").strip()).strip(".-")
    return cleaned[:120] or fallback


def _safe_filename(raw: str, *, fallback: str = "slack-file.bin") -> str:
    cleaned = pathlib.PurePosixPath(str(raw or "").strip()).name
    cleaned = re.sub(r"[\x00-\x1f/\\]+", "-", cleaned).strip(". ")
    return cleaned[:180] or fallback


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _bool_param(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _current_artifact_context() -> tuple[pathlib.Path | None, str]:
    from kdcube_ai_app.apps.chat.sdk.runtime import run_ctx
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_user_identity

    outdir_raw = str(run_ctx.OUTDIR_CV.get("") or "").strip()
    turn_id = str((get_current_user_identity() or {}).get("turn_id") or "").strip()
    if not outdir_raw or not turn_id:
        return None, turn_id
    return artifact_outdir_for(pathlib.Path(outdir_raw), create=True), turn_id


def _resolve_input_artifact(path_value: str, artifact_root: pathlib.Path) -> pathlib.Path | None:
    raw = str(path_value or "").strip()
    if not raw:
        return None
    if raw.startswith("conv:fi:"):
        _conversation_id, turn_id, namespace, rel = split_logical_artifact_ref(raw)
        if turn_id and namespace and rel:
            physical = build_physical_artifact_path(turn_id=turn_id, namespace=namespace, relpath=rel)
            return resolve_artifact_path(artifact_root, physical)
        return None
    if raw.startswith("fi:"):
        body = raw[3:]
        turn_id, dot, rest = body.partition(".")
        if dot and "/" in rest:
            namespace, _, rel = rest.partition("/")
            physical = build_physical_artifact_path(turn_id=turn_id, namespace=namespace, relpath=rel)
            return resolve_artifact_path(artifact_root, physical)
    candidate = pathlib.Path(raw)
    if candidate.is_absolute():
        try:
            resolved = candidate.resolve()
            resolved.relative_to(artifact_root.resolve())
        except Exception:
            return None
        return resolved if resolved.exists() and resolved.is_file() else None
    return resolve_artifact_path(artifact_root, raw)


def _load_upload_file(file_path: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    artifact_root, _turn_id = _current_artifact_context()
    if artifact_root is None:
        return None, {
            "code": "artifact_workspace_unavailable",
            "message": "Current ReAct artifact workspace is unavailable; cannot upload local files.",
        }
    resolved = _resolve_input_artifact(file_path, artifact_root)
    if resolved is None or not resolved.exists() or not resolved.is_file():
        return None, {
            "code": "file_not_found",
            "message": "Slack upload file path was not found in the current artifact workspace.",
            "path": file_path,
        }
    data = resolved.read_bytes()
    if len(data) > MAX_SLACK_FILE_BYTES:
        return None, {
            "code": "file_too_large",
            "message": f"Slack upload file is larger than the configured limit of {MAX_SLACK_FILE_BYTES} bytes.",
            "path": file_path,
            "size_bytes": len(data),
        }
    filename = _safe_filename(resolved.name)
    return {
        "filename": filename,
        "mime_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
        "data": data,
        "source_path": file_path,
        "size_bytes": len(data),
    }, None


def _compact_file(file_obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(file_obj.get("id") or ""),
        "name": str(file_obj.get("name") or ""),
        "title": str(file_obj.get("title") or ""),
        "mimetype": str(file_obj.get("mimetype") or ""),
        "filetype": str(file_obj.get("filetype") or ""),
        "pretty_type": str(file_obj.get("pretty_type") or ""),
        "size": int(file_obj.get("size") or 0),
        "user": str(file_obj.get("user") or ""),
        "created": file_obj.get("created"),
        "timestamp": file_obj.get("timestamp"),
        "url_private": str(file_obj.get("url_private") or ""),
        "url_private_download": str(file_obj.get("url_private_download") or ""),
        "permalink": str(file_obj.get("permalink") or ""),
        "is_external": bool(file_obj.get("is_external")),
        "channels": list(file_obj.get("channels") or []),
        "groups": list(file_obj.get("groups") or []),
        "ims": list(file_obj.get("ims") or []),
    }


def _compact_message(item: dict[str, Any]) -> dict[str, Any]:
    files = [_compact_file(file_obj) for file_obj in (item.get("files") or []) if isinstance(file_obj, dict)]
    return {
        "type": str(item.get("type") or ""),
        "subtype": str(item.get("subtype") or ""),
        "user": str(item.get("user") or ""),
        "username": str(item.get("username") or ""),
        "bot_id": str(item.get("bot_id") or ""),
        "text": str(item.get("text") or ""),
        "timestamp": str(item.get("ts") or ""),
        "thread_ts": str(item.get("thread_ts") or ""),
        "reply_count": item.get("reply_count"),
        "files": files,
        "file_count": len(files),
    }


async def _download_file_to_artifact(
    *,
    client: httpx.AsyncClient,
    token: str,
    file_obj: dict[str, Any],
    credential: ConnectedAccountCredential,
    max_bytes: int,
) -> dict[str, Any]:
    artifact_root, turn_id = _current_artifact_context()
    if artifact_root is None or not turn_id:
        return _error_result(
            code="artifact_workspace_unavailable",
            message="Current ReAct turn id or artifact workspace is unavailable.",
            where="slack.download_slack_file",
        )
    download_url = str(file_obj.get("url_private_download") or file_obj.get("url_private") or "").strip()
    if not download_url:
        return _error_result(
            code="slack_file_not_downloadable",
            message="Slack file does not expose a private download URL for this token.",
            where="slack.download_slack_file",
            ret={"file": _compact_file(file_obj)},
        )
    response = await client.get(download_url, headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        if response.status_code in {401, 403}:
            return _provider_auth_envelope(
                credential,
                where="slack.download_slack_file",
                message=f"Slack file download failed with HTTP {response.status_code}.",
            )
        return _error_result(
            code="slack_file_download_failed",
            message=f"Slack file download failed with HTTP {response.status_code}.",
            where="slack.download_slack_file",
        )
    data = response.content or b""
    if len(data) > max_bytes:
        return _error_result(
            code="slack_file_too_large",
            message=f"Slack file is larger than the configured limit of {max_bytes} bytes.",
            where="slack.download_slack_file",
            ret={"file": _compact_file(file_obj), "size_bytes": len(data)},
        )
    filename = _safe_filename(str(file_obj.get("name") or file_obj.get("title") or file_obj.get("id") or "slack-file.bin"))
    account_key = _safe_segment(str(credential.account_id or "slack"), fallback="slack")
    file_key = _safe_segment(str(file_obj.get("id") or "file"), fallback="file")
    rel = pathlib.PurePosixPath("slack-files") / account_key / file_key / filename
    physical = build_physical_artifact_path(turn_id=turn_id, namespace=ARTIFACT_NAMESPACE_FILES, relpath=rel.as_posix())
    target = resolve_artifact_path(artifact_root, physical, prefer_existing=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    logical = physical_path_to_logical_path(physical)
    mime_type = str(file_obj.get("mimetype") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    text_preview = ""
    if mime_type.startswith("text/") or filename.endswith((".txt", ".md", ".csv", ".json", ".log")):
        text_preview = data[:MAX_SLACK_TEXT_PREVIEW_CHARS].decode("utf-8", errors="replace")
    return _ok_ret_result(
        {
            "file": _compact_file(file_obj),
            "artifact_path": logical,
            "logical_path": logical,
            "path": physical,
            "physical_path": physical,
            "filename": filename,
            "mime": mime_type,
            "mime_type": mime_type,
            "size": len(data),
            "size_bytes": len(data),
            "text_preview": text_preview,
            "account_id": credential.account_id,
        }
    )


class SlackTools:
    async def _credential(
        self,
        *,
        claim: str,
        tool_name: str,
        account_id: str = "",
    ) -> ConnectedAccountCredential:
        return await resolve_connected_account_claim(
            globals(),
            provider_id=SLACK_PROVIDER_ID,
            connector_app_id=SLACK_CONNECTOR_APP_ID,
            claim=claim,
            account_id=account_id,
            tool_name=tool_name,
        )

    async def _call_json(
        self,
        *,
        credential: ConnectedAccountCredential,
        method: str,
        http_method: str = "GET",
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        where: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if http_method.upper() == "POST":
                response = await client.post(
                    f"{SLACK_API}/{method}",
                    headers={"Authorization": f"Bearer {credential.access_token}"},
                    json=json_payload,
                    params=params,
                )
            else:
                response = await client.get(
                    f"{SLACK_API}/{method}",
                    headers={"Authorization": f"Bearer {credential.access_token}"},
                    params=params,
                )
        try:
            data = response.json()
        except Exception:
            data = {}
        if response.status_code >= 400 or not (isinstance(data, dict) and data.get("ok")):
            if _is_auth_failure(data, response.status_code):
                return None, _provider_auth_envelope(
                    credential,
                    where=where,
                    message=_slack_error(data, fallback=f"Slack {method} failed."),
                )
            return None, _error_result(
                code="slack_api_error",
                message=_slack_error(data, fallback=f"Slack {method} failed."),
                where=where,
                ret=data if isinstance(data, dict) else None,
            )
        return data if isinstance(data, dict) else {}, None

    @kernel_function(
        name="search_slack",
        description=(
            "Search Slack messages visible to the current user's connected Slack account. "
            "Requires the user to connect Slack with the slack:search claim in Connection Hub. "
            "Returns {ok, error, ret}; ret contains matching messages."
        ),
    )
    async def search_slack(
        self,
        query: Annotated[str, "Slack search query."] = "",
        count: Annotated[int, "Maximum results to return, 1-20.", {"min": 1, "max": 20}] = 10,
        account_id: Annotated[str, "Optional connected account id when the user has several Slack workspaces/accounts."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        if not str(query or "").strip():
            return _error_result(
                code="query_required",
                message="Slack search query is required.",
                where="slack.search_slack",
            )
        credential = await self._credential(claim=SLACK_SEARCH_CLAIM, account_id=account_id, tool_name="slack.search_slack")
        if not credential.ok:
            return credential.error_envelope(where="slack.search_slack")
        if not credential.access_token:
            return _error_result(
                code="credential_missing_access_token",
                message="Connected Slack credential has no access token.",
                where="slack.search_slack",
            )
        limit = max(1, min(int(count or 10), 20))
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{SLACK_API}/search.messages",
                headers={"Authorization": f"Bearer {credential.access_token}"},
                params={"query": query, "count": limit},
            )
        try:
            data = response.json()
        except Exception:
            data = {}
        if response.status_code >= 400 or not (isinstance(data, dict) and data.get("ok")):
            return _error_result(
                code="slack_api_error",
                message=_slack_error(data, fallback="Slack search failed."),
                where="slack.search_slack",
                ret=data if isinstance(data, dict) else None,
            )
        messages = data.get("messages") if isinstance(data, dict) else {}
        matches = messages.get("matches") if isinstance(messages, dict) else []
        rows: list[dict[str, Any]] = []
        for item in matches or []:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "channel_id": str(item.get("channel", {}).get("id") if isinstance(item.get("channel"), dict) else item.get("channel") or ""),
                    "channel_name": str(item.get("channel", {}).get("name") if isinstance(item.get("channel"), dict) else ""),
                    "user": str(item.get("user") or item.get("username") or ""),
                    "text": str(item.get("text") or ""),
                    "permalink": str(item.get("permalink") or ""),
                    "timestamp": str(item.get("ts") or ""),
                }
            )
        return _ok_ret_result({"messages": rows, "count": len(rows), "account_id": credential.account_id})

    @kernel_function(
        name="list_slack_channels",
        description=(
            "List Slack channels/conversations visible to the current user's connected Slack account. "
            "Requires slack:channels in Connection Hub."
        ),
    )
    async def list_slack_channels(
        self,
        types: Annotated[str, "Comma-separated Slack conversation types: public_channel,private_channel,mpim,im."] = "public_channel,private_channel",
        limit: Annotated[int, "Maximum channels to return, 1-200.", {"min": 1, "max": 200}] = 100,
        cursor: Annotated[str, "Optional Slack pagination cursor."] = "",
        exclude_archived: Annotated[bool, "Whether to exclude archived conversations."] = True,
        account_id: Annotated[str, "Optional connected account id when the user has several Slack workspaces/accounts."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        credential = await self._credential(claim=SLACK_CHANNELS_CLAIM, account_id=account_id, tool_name="slack.list_slack_channels")
        if not credential.ok:
            return credential.error_envelope(where="slack.list_slack_channels")
        if not credential.access_token:
            return _error_result(
                code="credential_missing_access_token",
                message="Connected Slack credential has no access token.",
                where="slack.list_slack_channels",
            )
        params: dict[str, Any] = {
            "types": ",".join(_string_list(types)) or "public_channel,private_channel",
            "limit": max(1, min(int(limit or 100), 200)),
            "exclude_archived": "true" if _bool_param(exclude_archived) else "false",
        }
        if str(cursor or "").strip():
            params["cursor"] = str(cursor).strip()
        data, error = await self._call_json(
            credential=credential,
            method="conversations.list",
            params=params,
            where="slack.list_slack_channels",
        )
        if error:
            return error
        channels = []
        for item in (data or {}).get("channels") or []:
            if not isinstance(item, dict):
                continue
            channels.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or item.get("user") or ""),
                    "is_channel": bool(item.get("is_channel")),
                    "is_group": bool(item.get("is_group")),
                    "is_im": bool(item.get("is_im")),
                    "is_mpim": bool(item.get("is_mpim")),
                    "is_private": bool(item.get("is_private")),
                    "is_archived": bool(item.get("is_archived")),
                    "is_member": bool(item.get("is_member")),
                    "num_members": item.get("num_members"),
                    "topic": (item.get("topic") or {}).get("value") if isinstance(item.get("topic"), dict) else "",
                    "purpose": (item.get("purpose") or {}).get("value") if isinstance(item.get("purpose"), dict) else "",
                }
            )
        response_metadata = (data or {}).get("response_metadata") if isinstance((data or {}).get("response_metadata"), dict) else {}
        return _ok_ret_result(
            {
                "channels": channels,
                "count": len(channels),
                "next_cursor": str((response_metadata or {}).get("next_cursor") or ""),
                "account_id": credential.account_id,
            }
        )

    @kernel_function(
        name="read_slack_channel_history",
        description=(
            "Read Slack conversation history for a channel/conversation id visible to the current user's connected Slack account. "
            "Requires slack:history in Connection Hub."
        ),
    )
    async def read_slack_channel_history(
        self,
        channel: Annotated[str, "Slack channel/conversation id."] = "",
        limit: Annotated[int, "Maximum messages to return, 1-100.", {"min": 1, "max": 100}] = 20,
        cursor: Annotated[str, "Optional Slack pagination cursor."] = "",
        oldest: Annotated[str, "Optional oldest Slack timestamp."] = "",
        latest: Annotated[str, "Optional latest Slack timestamp."] = "",
        inclusive: Annotated[bool, "Whether oldest/latest bounds are inclusive."] = False,
        account_id: Annotated[str, "Optional connected account id when the user has several Slack workspaces/accounts."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        if not str(channel or "").strip():
            return _error_result(code="channel_required", message="Slack channel id is required.", where="slack.read_slack_channel_history")
        credential = await self._credential(claim=SLACK_HISTORY_CLAIM, account_id=account_id, tool_name="slack.read_slack_channel_history")
        if not credential.ok:
            return credential.error_envelope(where="slack.read_slack_channel_history")
        if not credential.access_token:
            return _error_result(
                code="credential_missing_access_token",
                message="Connected Slack credential has no access token.",
                where="slack.read_slack_channel_history",
            )
        params: dict[str, Any] = {
            "channel": str(channel).strip(),
            "limit": max(1, min(int(limit or 20), 100)),
            "inclusive": "true" if _bool_param(inclusive) else "false",
        }
        for key, value in {"cursor": cursor, "oldest": oldest, "latest": latest}.items():
            if str(value or "").strip():
                params[key] = str(value).strip()
        data, error = await self._call_json(
            credential=credential,
            method="conversations.history",
            params=params,
            where="slack.read_slack_channel_history",
        )
        if error:
            return error
        messages = [_compact_message(item) for item in (data or {}).get("messages") or [] if isinstance(item, dict)]
        response_metadata = (data or {}).get("response_metadata") if isinstance((data or {}).get("response_metadata"), dict) else {}
        return _ok_ret_result(
            {
                "channel": str(channel).strip(),
                "messages": messages,
                "count": len(messages),
                "has_more": bool((data or {}).get("has_more")),
                "next_cursor": str((response_metadata or {}).get("next_cursor") or ""),
                "account_id": credential.account_id,
            }
        )

    @kernel_function(
        name="download_slack_file",
        description=(
            "Read Slack file metadata and optionally materialize the private file bytes as a KDCube artifact. "
            "Requires slack:files:read in Connection Hub."
        ),
    )
    async def download_slack_file(
        self,
        file_id: Annotated[str, "Slack file id, for example F123ABC."] = "",
        save: Annotated[bool, "Whether to download the file to the current KDCube artifact workspace."] = True,
        max_bytes: Annotated[int, "Maximum file bytes to download."] = MAX_SLACK_FILE_BYTES,
        account_id: Annotated[str, "Optional connected account id when the user has several Slack workspaces/accounts."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        if not str(file_id or "").strip():
            return _error_result(code="file_required", message="Slack file id is required.", where="slack.download_slack_file")
        credential = await self._credential(claim=SLACK_FILES_READ_CLAIM, account_id=account_id, tool_name="slack.download_slack_file")
        if not credential.ok:
            return credential.error_envelope(where="slack.download_slack_file")
        if not credential.access_token:
            return _error_result(
                code="credential_missing_access_token",
                message="Connected Slack credential has no access token.",
                where="slack.download_slack_file",
            )
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                f"{SLACK_API}/files.info",
                headers={"Authorization": f"Bearer {credential.access_token}"},
                params={"file": str(file_id).strip()},
            )
            try:
                data = response.json()
            except Exception:
                data = {}
            if response.status_code >= 400 or not (isinstance(data, dict) and data.get("ok")):
                if _is_auth_failure(data, response.status_code):
                    return _provider_auth_envelope(
                        credential,
                        where="slack.download_slack_file",
                        message=_slack_error(data, fallback="Slack file lookup failed."),
                    )
                return _error_result(
                    code="slack_api_error",
                    message=_slack_error(data, fallback="Slack file lookup failed."),
                    where="slack.download_slack_file",
                    ret=data if isinstance(data, dict) else None,
                )
            file_obj = data.get("file") if isinstance(data.get("file"), dict) else {}
            if not _bool_param(save):
                return _ok_ret_result({"file": _compact_file(file_obj), "account_id": credential.account_id})
            return await _download_file_to_artifact(
                client=client,
                token=credential.access_token,
                file_obj=file_obj,
                credential=credential,
                max_bytes=max(1, min(int(max_bytes or MAX_SLACK_FILE_BYTES), MAX_SLACK_FILE_BYTES)),
            )

    @kernel_function(
        name="upload_slack_file",
        description=(
            "Upload a KDCube artifact file to Slack using Slack's external upload flow. "
            "Requires slack:files:write in Connection Hub."
        ),
    )
    async def upload_slack_file(
        self,
        channel: Annotated[str, "Slack channel id where the file should be shared. Leave blank to keep private."] = "",
        file_path: Annotated[str, "KDCube artifact logical path to upload, such as fi:<turn>.files/report.pdf."] = "",
        title: Annotated[str, "Optional Slack file title."] = "",
        initial_comment: Annotated[str, "Optional message introducing the file."] = "",
        thread_ts: Annotated[str, "Optional Slack thread timestamp."] = "",
        filename: Annotated[str, "Optional filename override."] = "",
        account_id: Annotated[str, "Optional connected account id when the user has several Slack workspaces/accounts."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        if not str(file_path or "").strip():
            return _error_result(code="file_path_required", message="KDCube artifact file path is required.", where="slack.upload_slack_file")
        upload_file, load_error = _load_upload_file(file_path)
        if load_error:
            return _error_result(
                code=str(load_error.get("code") or "file_load_failed"),
                message=str(load_error.get("message") or "File could not be loaded."),
                where="slack.upload_slack_file",
                ret=load_error,
            )
        assert upload_file is not None
        upload_filename = _safe_filename(filename or upload_file["filename"])
        credential = await self._credential(claim=SLACK_FILES_WRITE_CLAIM, account_id=account_id, tool_name="slack.upload_slack_file")
        if not credential.ok:
            return credential.error_envelope(where="slack.upload_slack_file")
        if not credential.access_token:
            return _error_result(
                code="credential_missing_access_token",
                message="Connected Slack credential has no access token.",
                where="slack.upload_slack_file",
            )
        async with httpx.AsyncClient(timeout=120.0) as client:
            start_response = await client.post(
                f"{SLACK_API}/files.getUploadURLExternal",
                headers={"Authorization": f"Bearer {credential.access_token}"},
                json={"filename": upload_filename, "length": upload_file["size_bytes"]},
            )
            try:
                start_data = start_response.json()
            except Exception:
                start_data = {}
            if start_response.status_code >= 400 or not (isinstance(start_data, dict) and start_data.get("ok")):
                if _is_auth_failure(start_data, start_response.status_code):
                    return _provider_auth_envelope(
                        credential,
                        where="slack.upload_slack_file",
                        message=_slack_error(start_data, fallback="Slack upload start failed."),
                    )
                return _error_result(
                    code="slack_api_error",
                    message=_slack_error(start_data, fallback="Slack upload start failed."),
                    where="slack.upload_slack_file",
                    ret=start_data if isinstance(start_data, dict) else None,
                )
            upload_url = str(start_data.get("upload_url") or "").strip()
            file_id = str(start_data.get("file_id") or "").strip()
            if not upload_url or not file_id:
                return _error_result(
                    code="slack_upload_url_missing",
                    message="Slack did not return an upload URL and file id.",
                    where="slack.upload_slack_file",
                    ret=start_data,
                )
            upload_response = await client.post(
                upload_url,
                content=upload_file["data"],
                headers={"Content-Type": upload_file["mime_type"]},
            )
            if upload_response.status_code >= 400:
                return _error_result(
                    code="slack_upload_bytes_failed",
                    message=f"Slack file upload failed with HTTP {upload_response.status_code}.",
                    where="slack.upload_slack_file",
                )
            complete_payload: dict[str, Any] = {"files": [{"id": file_id, "title": str(title or upload_filename).strip() or upload_filename}]}
            if str(channel or "").strip():
                complete_payload["channel_id"] = str(channel).strip()
            if str(initial_comment or "").strip():
                complete_payload["initial_comment"] = str(initial_comment).strip()
            if str(thread_ts or "").strip():
                complete_payload["thread_ts"] = str(thread_ts).strip()
            complete_response = await client.post(
                f"{SLACK_API}/files.completeUploadExternal",
                headers={"Authorization": f"Bearer {credential.access_token}"},
                json=complete_payload,
            )
            try:
                complete_data = complete_response.json()
            except Exception:
                complete_data = {}
            if complete_response.status_code >= 400 or not (isinstance(complete_data, dict) and complete_data.get("ok")):
                if _is_auth_failure(complete_data, complete_response.status_code):
                    return _provider_auth_envelope(
                        credential,
                        where="slack.upload_slack_file",
                        message=_slack_error(complete_data, fallback="Slack upload finalize failed."),
                    )
                return _error_result(
                    code="slack_api_error",
                    message=_slack_error(complete_data, fallback="Slack upload finalize failed."),
                    where="slack.upload_slack_file",
                    ret=complete_data if isinstance(complete_data, dict) else None,
                )
        return _ok_ret_result(
            {
                "file_id": file_id,
                "files": complete_data.get("files") if isinstance(complete_data.get("files"), list) else [],
                "channel": str(channel or ""),
                "thread_ts": str(thread_ts or ""),
                "filename": upload_filename,
                "size_bytes": upload_file["size_bytes"],
                "source_path": upload_file["source_path"],
                "account_id": credential.account_id,
            }
        )

    @kernel_function(
        name="slack_assistant_search_info",
        description=(
            "Check whether Slack AI semantic assistant search is enabled for the connected workspace. "
            "Requires slack:assistant:search in Connection Hub."
        ),
    )
    async def slack_assistant_search_info(
        self,
        account_id: Annotated[str, "Optional connected account id when the user has several Slack workspaces/accounts."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        credential = await self._credential(claim=SLACK_ASSISTANT_SEARCH_CLAIM, account_id=account_id, tool_name="slack.slack_assistant_search_info")
        if not credential.ok:
            return credential.error_envelope(where="slack.slack_assistant_search_info")
        if not credential.access_token:
            return _error_result(
                code="credential_missing_access_token",
                message="Connected Slack credential has no access token.",
                where="slack.slack_assistant_search_info",
            )
        data, error = await self._call_json(
            credential=credential,
            method="assistant.search.info",
            http_method="POST",
            json_payload={},
            where="slack.slack_assistant_search_info",
        )
        if error:
            return error
        return _ok_ret_result({"is_ai_search_enabled": bool((data or {}).get("is_ai_search_enabled")), "account_id": credential.account_id})

    @kernel_function(
        name="slack_assistant_search",
        description=(
            "Use Slack assistant semantic search across messages, files, channels, or users. "
            "Requires slack:assistant:search in Connection Hub."
        ),
    )
    async def slack_assistant_search(
        self,
        query: Annotated[str, "Natural language Slack search query."] = "",
        content_types: Annotated[str, "Comma-separated content types: messages,files,channels,users."] = "messages,files",
        channel_types: Annotated[str, "Comma-separated channel types: public_channel,private_channel,mpim,im."] = "public_channel,private_channel",
        limit: Annotated[int, "Maximum results to return, 1-20.", {"min": 1, "max": 20}] = 10,
        cursor: Annotated[str, "Optional Slack pagination cursor."] = "",
        context_channel_id: Annotated[str, "Optional channel id to scope search context."] = "",
        include_context_messages: Annotated[bool, "Include surrounding context messages when Slack supports it."] = False,
        sort: Annotated[str, "Slack sort field: score or timestamp."] = "score",
        sort_dir: Annotated[str, "Slack sort direction: asc or desc."] = "desc",
        account_id: Annotated[str, "Optional connected account id when the user has several Slack workspaces/accounts."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        if not str(query or "").strip():
            return _error_result(code="query_required", message="Slack assistant search query is required.", where="slack.slack_assistant_search")
        credential = await self._credential(claim=SLACK_ASSISTANT_SEARCH_CLAIM, account_id=account_id, tool_name="slack.slack_assistant_search")
        if not credential.ok:
            return credential.error_envelope(where="slack.slack_assistant_search")
        if not credential.access_token:
            return _error_result(
                code="credential_missing_access_token",
                message="Connected Slack credential has no access token.",
                where="slack.slack_assistant_search",
            )
        payload: dict[str, Any] = {
            "query": str(query).strip(),
            "content_types": _string_list(content_types) or ["messages"],
            "channel_types": _string_list(channel_types) or ["public_channel"],
            "limit": max(1, min(int(limit or 10), 20)),
            "include_context_messages": _bool_param(include_context_messages),
            "sort": str(sort or "score").strip() if str(sort or "").strip() in {"score", "timestamp"} else "score",
            "sort_dir": str(sort_dir or "desc").strip() if str(sort_dir or "").strip() in {"asc", "desc"} else "desc",
        }
        if str(cursor or "").strip():
            payload["cursor"] = str(cursor).strip()
        if str(context_channel_id or "").strip():
            payload["context_channel_id"] = str(context_channel_id).strip()
        data, error = await self._call_json(
            credential=credential,
            method="assistant.search.context",
            http_method="POST",
            json_payload=payload,
            where="slack.slack_assistant_search",
        )
        if error:
            return error
        return _ok_ret_result(
            {
                "results": data.get("results") or data.get("items") or [],
                "raw": data,
                "next_cursor": str(((data.get("response_metadata") or {}) if isinstance(data.get("response_metadata"), dict) else {}).get("next_cursor") or data.get("next_cursor") or ""),
                "account_id": credential.account_id,
            }
        )

    @kernel_function(
        name="post_slack_message",
        description=(
            "Post a message to a Slack channel using the current user's connected Slack account. "
            "Requires the user to connect Slack with the slack:post claim in Connection Hub. "
            "The channel can be a Slack channel id or an allowed channel name accepted by Slack."
        ),
    )
    async def post_slack_message(
        self,
        channel: Annotated[str, "Slack channel id or name accepted by Slack."] = "",
        text: Annotated[str, "Message text to post."] = "",
        thread_ts: Annotated[str, "Optional Slack thread timestamp to reply in a thread."] = "",
        account_id: Annotated[str, "Optional connected account id when the user has several Slack workspaces/accounts."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        if not str(channel or "").strip():
            return _error_result(
                code="channel_required",
                message="Slack channel is required.",
                where="slack.post_slack_message",
            )
        if not str(text or "").strip():
            return _error_result(
                code="text_required",
                message="Slack message text is required.",
                where="slack.post_slack_message",
            )
        credential = await self._credential(claim=SLACK_POST_CLAIM, account_id=account_id, tool_name="slack.post_slack_message")
        if not credential.ok:
            return credential.error_envelope(where="slack.post_slack_message")
        if not credential.access_token:
            return _error_result(
                code="credential_missing_access_token",
                message="Connected Slack credential has no access token.",
                where="slack.post_slack_message",
            )
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if str(thread_ts or "").strip():
            payload["thread_ts"] = str(thread_ts).strip()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{SLACK_API}/chat.postMessage",
                headers={"Authorization": f"Bearer {credential.access_token}"},
                json=payload,
            )
        try:
            data = response.json()
        except Exception:
            data = {}
        if response.status_code >= 400 or not (isinstance(data, dict) and data.get("ok")):
            return _error_result(
                code="slack_api_error",
                message=_slack_error(data, fallback="Slack post failed."),
                where="slack.post_slack_message",
                ret=data if isinstance(data, dict) else None,
            )
        return _ok_ret_result(
            {
                "channel": str(data.get("channel") or channel),
                "timestamp": str(data.get("ts") or ""),
                "message": data.get("message") if isinstance(data.get("message"), dict) else {},
                "account_id": credential.account_id,
            }
        )


kernel = sk.Kernel()
tools = SlackTools()
kernel.add_plugin(tools, "slack")


__all__ = [
    "SLACK_API",
    "SLACK_ASSISTANT_SEARCH_CLAIM",
    "SLACK_CHANNELS_CLAIM",
    "SLACK_CONNECTOR_APP_ID",
    "SLACK_FILES_READ_CLAIM",
    "SLACK_FILES_WRITE_CLAIM",
    "SLACK_HISTORY_CLAIM",
    "SLACK_POST_CLAIM",
    "SLACK_PROVIDER_ID",
    "SLACK_SEARCH_CLAIM",
    "SlackTools",
    "kernel",
    "tools",
]
