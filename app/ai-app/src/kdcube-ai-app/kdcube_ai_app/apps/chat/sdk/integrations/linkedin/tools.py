# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""LinkedIn tools backed by Connection Hub connected accounts.

Acts on the connected member's own account through the versioned `/rest` API
in :mod:`.rest_api`. Does not use the bundle-owned OAuth layer in
:mod:`.accounts`.
"""

from __future__ import annotations

import logging
import mimetypes
import pathlib
import re
from typing import Annotated, Any, Sequence

import httpx

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

from kdcube_ai_app.apps.chat.sdk.integrations.connected_accounts import (
    ConnectedAccountCredential,
    connected_account_auth_failure,
    resolve_connected_account_claim,
    run_with_connected_account_retry,
)
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin import rest_api
from kdcube_ai_app.apps.chat.sdk.integrations.linkedin.delivery import format_post_text
from kdcube_ai_app.apps.chat.sdk.integrations.provider_errors import (
    ProviderFailure,
    log_provider_failure,
    provider_failure_from_exception,
    provider_failure_from_payload,
)
from kdcube_ai_app.apps.chat.sdk.runtime.harness.workspace import artifact_outdir_for, resolve_artifact_path
from kdcube_ai_app.apps.chat.sdk.runtime.harness.workspace.references import (
    build_physical_artifact_path,
    split_logical_artifact_ref,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.connector_app_resolution import resolve_connector_app_id


LINKEDIN_PROVIDER_ID = "linkedin"
LINKEDIN_PROFILE_CLAIM = "linkedin:profile"
# Covers posts and comments: LinkedIn gates both on w_member_social.
LINKEDIN_POST_CLAIM = "linkedin:post"

LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
API_VERSION_PROP = "integrations.linkedin.api_version"

_SERVICE = None
_INTEGRATIONS: dict[str, Any] = {}
LOGGER = logging.getLogger(__name__)


def bind_service(svc: Any) -> None:
    global _SERVICE
    _SERVICE = svc


def bind_integrations(integrations: dict[str, Any] | None) -> None:
    global _INTEGRATIONS
    _INTEGRATIONS = dict(integrations or {})


def linkedin_api_version() -> str:
    """Effective LinkedIn API version: bundle prop, else the shipped default."""
    reader = getattr(_SERVICE, "bundle_prop", None)
    if callable(reader):
        try:
            value = str(reader(API_VERSION_PROP, rest_api.DEFAULT_LINKEDIN_API_VERSION) or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return rest_api.DEFAULT_LINKEDIN_API_VERSION


def _subject_from_id_token(id_token: str) -> str:
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.providers.linkedin import (
        _decode_id_token_claims,
    )

    return str(_decode_id_token_claims(id_token).get("sub") or "").strip()


async def _accounts_client(*, tenant: str = "", project: str = "", hub_bundle_id: str = ""):
    """Connection Hub client for the current user, None outside a bound scope.

    Account records live in the KDCube store; reading them needs no provider
    claim.
    """
    from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import get_current_user_identity
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.connection_edges import (
        DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    )
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import (
        DelegatedToKdcubeClient,
    )

    identity = get_current_user_identity() or {}
    user_id = str(identity.get("user_id") or "").strip()
    if not user_id or _SERVICE is None:
        return None
    return await DelegatedToKdcubeClient.from_connection_hub(
        _SERVICE,
        user_id=user_id,
        tenant=str(tenant or identity.get("tenant_id") or ""),
        project=str(project or identity.get("project_id") or ""),
        connection_hub_bundle_id=str(hub_bundle_id or DEFAULT_CONNECTION_HUB_BUNDLE_ID),
    )


async def connected_linkedin_accounts(
    *, tenant: str = "", project: str = "", hub_bundle_id: str = ""
) -> list[Any]:
    """Connected LinkedIn accounts for the current user."""
    client = await _accounts_client(tenant=tenant, project=project, hub_bundle_id=hub_bundle_id)
    if client is None:
        return []
    return await client.list_accounts(provider_id=LINKEDIN_PROVIDER_ID)


def _ok_ret_result(ret: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, "ret": ret}


def _error_result(*, code: str, message: str, where: str, ret: Any = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message, "where": where, "managed": True},
        "ret": ret,
    }


def _linkedin_failure(
    response: httpx.Response,
    *,
    operation: str,
    fallback: str,
    where: str,
    stage: str = "",
    mutating: bool = False,
) -> ProviderFailure:
    """Map a LinkedIn REST error onto the shared provider contract.

    LinkedIn returns a flat ``{"message", "status", "serviceErrorCode"}`` body;
    the shared reader expects code/message under ``error``.
    """
    try:
        body = response.json()
    except Exception:
        body = {}
    body = dict(body) if isinstance(body, dict) else {}
    payload = {
        "error": {
            "code": str(body.get("code") or body.get("serviceErrorCode") or ""),
            "message": str(body.get("message") or ""),
            "status": str(body.get("status") or ""),
        }
    }
    failure = provider_failure_from_payload(
        payload,
        provider_status=int(getattr(response, "status_code", 0) or 0),
        provider=LINKEDIN_PROVIDER_ID,
        service="linkedin",
        operation=operation,
        fallback=fallback,
        stage=stage,
        mutating=mutating,
        retry_after=str((getattr(response, "headers", {}) or {}).get("Retry-After") or ""),
    )
    log_provider_failure(LOGGER, failure, where=where)
    return failure


def _auth_failure_message(failure: ProviderFailure) -> str:
    reason = failure.provider_reason or failure.provider_code
    if reason and reason.lower() not in failure.message.lower():
        return f"{failure.message} [{reason}]"
    return failure.message


async def _run_provider_call(
    *,
    where: str,
    operation: str,
    run: Any,
    mutating: bool = False,
) -> dict[str, Any]:
    try:
        return await run_with_connected_account_retry(globals(), where=where, run=run)
    except rest_api.LinkedInPayloadError as exc:
        return _error_result(code="invalid_payload", message=str(exc), where=where)
    except httpx.HTTPError as exc:
        failure = provider_failure_from_exception(
            exc,
            provider=LINKEDIN_PROVIDER_ID,
            service="linkedin",
            operation=operation,
            fallback="LinkedIn did not return a response.",
            mutating=mutating,
        )
        log_provider_failure(LOGGER, failure, where=where, exc=exc)
        return failure.error_result(where=where)


def _safe_filename(raw: str, *, fallback: str = "linkedin-image.bin") -> str:
    cleaned = pathlib.PurePosixPath(str(raw or "").strip()).name
    cleaned = re.sub(r"[\x00-\x1f/\\]+", "-", cleaned).strip(". ")
    return cleaned[:180] or fallback


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
    candidate = pathlib.Path(raw)
    if candidate.is_absolute():
        try:
            resolved = candidate.resolve()
            resolved.relative_to(artifact_root.resolve())
        except Exception:
            return None
        return resolved if resolved.exists() and resolved.is_file() else None
    return resolve_artifact_path(artifact_root, raw)


def validate_image(file_obj: dict[str, Any]) -> dict[str, Any] | None:
    """Check one image against LinkedIn's size/format limits. Returns an error
    dict or None."""
    data = file_obj.get("data") or b""
    filename = _safe_filename(str(file_obj.get("filename") or ""))
    mime = str(file_obj.get("mime_type") or file_obj.get("mime") or "").strip()
    mime = mime or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if len(data) > rest_api.MAX_IMAGE_BYTES:
        return {
            "code": "file_too_large",
            "message": f"LinkedIn images must be at most {rest_api.MAX_IMAGE_BYTES} bytes.",
            "filename": filename,
            "size_bytes": len(data),
        }
    if mime not in rest_api.SUPPORTED_IMAGE_MIME:
        return {
            "code": "unsupported_image_type",
            "message": f"LinkedIn accepts {', '.join(rest_api.SUPPORTED_IMAGE_MIME)}; got {mime}.",
            "filename": filename,
            "mime_type": mime,
        }
    file_obj["filename"] = filename
    file_obj["mime_type"] = mime
    return None


def staging_root_for_service():
    """Staging directory derived from the bound entrypoint's STORAGE_PATH.

    The upload route and action resolution must agree on this directory.
    """
    from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import staging_root

    storage = str(getattr(getattr(_SERVICE, "settings", None), "STORAGE_PATH", "") or "")
    try:
        return staging_root(storage)
    except OSError:
        return None


def load_staged_image(staged_ref: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Read one staged image by ref. Returns ``(file, error)``."""
    from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import load_staged

    root = staging_root_for_service()
    if root is None:
        return None, {
            "code": "upload_not_configured",
            "message": "This deployment has no upload staging configured.",
        }
    try:
        filename, data = load_staged(root, staged_ref)
    except (FileNotFoundError, ValueError) as exc:
        return None, {"code": "staged_file_missing", "message": str(exc), "staged_ref": staged_ref}
    file_obj = {"filename": filename, "data": data, "staged_ref": staged_ref}
    error = validate_image(file_obj)
    if error is not None:
        return None, error
    return file_obj, None


def load_image_artifact(file_path: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Read one workspace image for upload. Returns ``(file, error)``."""
    artifact_root, _turn_id = _current_artifact_context()
    if artifact_root is None:
        return None, {
            "code": "artifact_workspace_unavailable",
            "message": "Current artifact workspace is unavailable; cannot read local images.",
        }
    resolved = _resolve_input_artifact(file_path, artifact_root)
    if resolved is None or not resolved.exists() or not resolved.is_file():
        return None, {
            "code": "file_not_found",
            "message": "Image path was not found in the current artifact workspace.",
            "path": file_path,
        }
    file_obj = {
        "filename": resolved.name,
        "data": resolved.read_bytes(),
        "source_path": file_path,
    }
    error = validate_image(file_obj)
    if error is not None:
        error.setdefault("path", file_path)
        return None, error
    return file_obj, None


class LinkedInTools:
    async def _credential(
        self,
        *,
        claim: str,
        tool_name: str,
        account_id: str = "",
    ) -> ConnectedAccountCredential:
        return await resolve_connected_account_claim(
            globals(),
            provider_id=LINKEDIN_PROVIDER_ID,
            connector_app_id=resolve_connector_app_id(LINKEDIN_PROVIDER_ID),
            claim=claim,
            account_id=account_id,
            tool_name=tool_name,
        )

    async def _author_urn(self, credential: ConnectedAccountCredential) -> str:
        """Author URN from the connected account's ``external_subject``.

        The credential is a token record and carries no subject; its
        ``id_token`` is the fallback.
        """
        subject = ""
        accounts = await connected_linkedin_accounts(
            tenant=credential.tenant,
            project=credential.project,
            hub_bundle_id=credential.connection_hub_bundle_id,
        )
        for account in accounts:
            if account.account_id == credential.account_id:
                subject = str(account.external_subject or "").strip()
                break
        if not subject:
            raw = dict(credential.raw_credential or {})
            subject = _subject_from_id_token(str(raw.get("id_token") or ""))
        return rest_api.person_urn(subject)

    async def _upload_images(
        self,
        client: httpx.AsyncClient,
        *,
        credential: ConnectedAccountCredential,
        author_urn: str,
        files: Sequence[dict[str, Any]],
        alt_texts: Sequence[str],
        where: str,
    ) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
        api_version = linkedin_api_version()
        uploaded: list[dict[str, Any]] = []
        for index, file_obj in enumerate(files):
            init_response = await rest_api.initialize_image_upload(
                client,
                access_token=credential.access_token,
                api_version=api_version,
                owner_urn=author_urn,
            )
            if init_response.status_code >= 400:
                failure = _linkedin_failure(
                    init_response,
                    operation="images.initializeUpload",
                    fallback="LinkedIn image upload could not be initialized.",
                    where=where,
                    stage="initialize_upload",
                )
                if failure.credential_failure:
                    return None, connected_account_auth_failure(credential, _auth_failure_message(failure))
                return None, failure.error_result(where=where)
            try:
                init = rest_api.parse_image_upload_init(init_response.json())
            except Exception:
                init = {}
            if not init.get("upload_url") or not init.get("image_urn"):
                return None, _error_result(
                    code="image_upload_init_incomplete",
                    message="LinkedIn did not return an upload URL and image URN.",
                    where=where,
                )
            upload_response = await rest_api.upload_image_bytes(
                client,
                upload_url=init["upload_url"],
                access_token=credential.access_token,
                data=file_obj["data"],
                content_type=file_obj["mime_type"],
            )
            if upload_response.status_code >= 400:
                failure = _linkedin_failure(
                    upload_response,
                    operation="images.upload",
                    fallback="LinkedIn image upload failed.",
                    where=where,
                    stage="upload_bytes",
                )
                if failure.credential_failure:
                    return None, connected_account_auth_failure(
                        credential, _auth_failure_message(failure)
                    )
                return None, failure.error_result(where=where)
            alt = str(alt_texts[index]) if index < len(alt_texts) else ""
            uploaded.append({"image_urn": init["image_urn"], "alt_text": alt})
        return uploaded, None

    async def _publish_from_workspace(
        self,
        *,
        text: str,
        image_paths: Sequence[str],
        alt_texts: Sequence[str],
        account_id: str,
        visibility: str,
        where: str,
    ) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        for path in image_paths:
            file_obj, error = load_image_artifact(path)
            if error is not None:
                return _error_result(
                    code=str(error.get("code") or "image_unavailable"),
                    message=str(error.get("message") or "Image could not be read."),
                    where=where,
                    ret=error,
                )
            files.append(file_obj)
        return await self._publish(
            text=text,
            files=files,
            alt_texts=alt_texts,
            account_id=account_id,
            visibility=visibility,
            where=where,
        )

    async def publish(
        self,
        *,
        text: str,
        files: Sequence[dict[str, Any]] = (),
        alt_texts: Sequence[str] = (),
        account_id: str = "",
        visibility: str = "PUBLIC",
        where: str = "linkedin.publish",
    ) -> dict[str, Any]:
        """Publish loaded image bytes through credential recovery.

        This is the safe programmatic entrypoint used by named services and
        staged-upload MCP tools. Provider-auth markers are an internal retry
        protocol and must never be serialized to either caller.
        """
        return await _run_provider_call(
            where=where,
            operation="posts.create",
            mutating=True,
            run=lambda: self._publish(
                text=text,
                files=files,
                alt_texts=alt_texts,
                account_id=account_id,
                visibility=visibility,
                where=where,
            ),
        )

    async def _publish(
        self,
        *,
        text: str,
        files: Sequence[dict[str, Any]] = (),
        alt_texts: Sequence[str] = (),
        account_id: str = "",
        visibility: str = "PUBLIC",
        where: str = "linkedin.publish",
    ) -> dict[str, Any]:
        """Publish a post from already-loaded image bytes.

        ``files`` entries are ``{filename, data, mime_type}``.
        """
        commentary = format_post_text(text or "")
        if not commentary.strip():
            return _error_result(code="text_required", message="LinkedIn post text is required.", where=where)

        files = [dict(item) for item in files or ()]
        for file_obj in files:
            error = validate_image(file_obj)
            if error is not None:
                return _error_result(
                    code=str(error.get("code") or "image_unavailable"),
                    message=str(error.get("message") or "Image could not be used."),
                    where=where,
                    ret=error,
                )

        credential = await self._credential(claim=LINKEDIN_POST_CLAIM, account_id=account_id, tool_name=where)
        if not credential.ok:
            return credential.error_envelope(where=where)
        if not credential.access_token:
            return _error_result(
                code="credential_missing_access_token",
                message="Connected LinkedIn credential has no access token.",
                where=where,
            )
        author_urn = await self._author_urn(credential)

        async with httpx.AsyncClient(timeout=120.0) as client:
            images: list[dict[str, Any]] = []
            if files:
                images, error = await self._upload_images(
                    client,
                    credential=credential,
                    author_urn=author_urn,
                    files=files,
                    alt_texts=alt_texts,
                    where=where,
                )
                if error is not None:
                    return error
            response = await rest_api.create_post(
                client,
                access_token=credential.access_token,
                api_version=linkedin_api_version(),
                author_urn=author_urn,
                commentary=commentary,
                images=images,
                visibility=visibility,
            )
        if response.status_code >= 400:
            failure = _linkedin_failure(
                response,
                operation="posts.create",
                fallback="LinkedIn post failed.",
                where=where,
                mutating=True,
            )
            if failure.credential_failure:
                return connected_account_auth_failure(credential, _auth_failure_message(failure))
            return failure.error_result(where=where)

        post_urn = rest_api.created_urn_from_response(response)
        if not post_urn:
            return _error_result(
                code="linkedin_response_incomplete",
                message=(
                    "LinkedIn accepted the post request but did not return its "
                    "identifier. The outcome is unknown; search LinkedIn before retrying."
                ),
                where=where,
                ret={
                    "provider": LINKEDIN_PROVIDER_ID,
                    "operation": "posts.create",
                    "provider_status": int(response.status_code or 0),
                    "outcome_unknown": True,
                },
            )
        return _ok_ret_result(
            {
                "post_urn": post_urn,
                "permalink": rest_api.post_permalink(post_urn),
                "account_id": credential.account_id,
                "author": author_urn,
                "image_count": len(images),
                "commentary_chars": len(commentary),
            }
        )

    @kernel_function(
        name="list_linkedin_accounts",
        description=(
            "List the current user's connected LinkedIn accounts. Reads KDCube's own connection "
            "records, so it needs no LinkedIn claim. Use it to obtain an account_id before "
            "publishing when several accounts are connected. Returns {ok, error, ret}; "
            "ret={accounts:[{account_id,display_name,email,status,claims,author_urn}],count}."
        ),
    )
    async def list_linkedin_accounts(self) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        where = "linkedin.list_linkedin_accounts"
        client = await _accounts_client()
        if client is None:
            return _error_result(
                code="user_scope_unavailable",
                message="No bound platform user; connected accounts cannot be listed here.",
                where=where,
            )
        rows = [
            {
                "account_id": account.account_id,
                "display_name": account.display_name,
                "email": account.email,
                "status": account.status,
                "claims": list(account.claims or []),
                "author_urn": (
                    rest_api.person_urn(account.external_subject) if account.external_subject else ""
                ),
            }
            for account in await client.list_accounts(provider_id=LINKEDIN_PROVIDER_ID)
        ]
        return _ok_ret_result({"accounts": rows, "count": len(rows)})

    @kernel_function(
        name="get_linkedin_profile",
        description=(
            "Read the connected LinkedIn member's own profile. Requires the user to connect "
            "LinkedIn with the linkedin:profile claim in Connection Hub. "
            "Returns {ok, error, ret}; ret={subject,name,email,account_id,author_urn}. "
            "LinkedIn does not expose other members' profiles or a people search to this integration."
        ),
    )
    async def get_linkedin_profile(
        self,
        account_id: Annotated[str, "Optional connected account id when several LinkedIn accounts are connected."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        return await _run_provider_call(
            where="linkedin.get_linkedin_profile",
            operation="userinfo",
            run=lambda: self._get_linkedin_profile(account_id=account_id),
        )

    async def _get_linkedin_profile(self, *, account_id: str) -> dict[str, Any]:
        where = "linkedin.get_linkedin_profile"
        credential = await self._credential(claim=LINKEDIN_PROFILE_CLAIM, account_id=account_id, tool_name=where)
        if not credential.ok:
            return credential.error_envelope(where=where)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                LINKEDIN_USERINFO_URL,
                headers={"Authorization": f"Bearer {credential.access_token}"},
            )
        if response.status_code >= 400:
            failure = _linkedin_failure(
                response,
                operation="userinfo",
                fallback="LinkedIn profile read failed.",
                where=where,
            )
            if failure.credential_failure:
                return connected_account_auth_failure(credential, _auth_failure_message(failure))
            return failure.error_result(where=where)
        try:
            data = response.json()
        except Exception:
            data = {}
        data = dict(data) if isinstance(data, dict) else {}
        subject = str(data.get("sub") or "").strip()
        return _ok_ret_result(
            {
                "subject": subject,
                "name": str(data.get("name") or "").strip(),
                "email": str(data.get("email") or "").strip(),
                "account_id": credential.account_id,
                "author_urn": rest_api.person_urn(subject) if subject else "",
            }
        )

    @kernel_function(
        name="post_linkedin_update",
        description=(
            "Publish a text post to the connected LinkedIn member's feed. Requires the user to "
            "connect LinkedIn with the linkedin:post claim in Connection Hub. Markdown is stripped "
            "and the text is truncated to LinkedIn's 3000-character limit. "
            "Returns {ok, error, ret}; ret={post_urn,permalink,account_id,author,image_count}. "
            "Pass post_urn to comment_on_linkedin_post to comment on it."
        ),
    )
    async def post_linkedin_update(
        self,
        text: Annotated[str, "Post body. Markdown is stripped; LinkedIn renders plain text only."] = "",
        visibility: Annotated[str, "PUBLIC or CONNECTIONS. Defaults to PUBLIC."] = "PUBLIC",
        account_id: Annotated[str, "Optional connected account id when several LinkedIn accounts are connected."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        return await _run_provider_call(
            where="linkedin.post_linkedin_update",
            operation="posts.create",
            mutating=True,
            run=lambda: self._publish_from_workspace(
                text=text,
                image_paths=(),
                alt_texts=(),
                account_id=account_id,
                visibility=str(visibility or "PUBLIC").strip().upper() or "PUBLIC",
                where="linkedin.post_linkedin_update",
            ),
        )

    @kernel_function(
        name="post_linkedin_image_update",
        description=(
            "Publish a LinkedIn post with one attached image from the current workspace. Requires "
            "the user to connect LinkedIn with the linkedin:post claim in Connection Hub. The image "
            "path is a conv:fi: reference or a workspace-relative path; accepted types are JPEG, PNG "
            "and GIF. Returns {ok, error, ret}; ret={post_urn,permalink,account_id,author,image_count}."
        ),
    )
    async def post_linkedin_image_update(
        self,
        text: Annotated[str, "Post body. Markdown is stripped; LinkedIn renders plain text only."] = "",
        image_path: Annotated[str, "conv:fi: reference or workspace-relative path of the image to attach."] = "",
        alt_text: Annotated[str, "Alt text for screen readers. Recommended under 120 characters."] = "",
        visibility: Annotated[str, "PUBLIC or CONNECTIONS. Defaults to PUBLIC."] = "PUBLIC",
        account_id: Annotated[str, "Optional connected account id when several LinkedIn accounts are connected."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        where = "linkedin.post_linkedin_image_update"
        if not str(image_path or "").strip():
            return _error_result(code="image_path_required", message="Image path is required.", where=where)
        return await _run_provider_call(
            where=where,
            operation="posts.create",
            mutating=True,
            run=lambda: self._publish_from_workspace(
                text=text,
                image_paths=[str(image_path).strip()],
                alt_texts=[str(alt_text or "")],
                account_id=account_id,
                visibility=str(visibility or "PUBLIC").strip().upper() or "PUBLIC",
                where=where,
            ),
        )

    async def publish_staged(
        self,
        *,
        text: str,
        staged_refs: Sequence[str] = (),
        alt_texts: Sequence[str] = (),
        account_id: str = "",
        visibility: str = "PUBLIC",
        where: str = "linkedin.publish_staged",
    ) -> dict[str, Any]:
        """Publish a post from images already uploaded to a signed slot."""
        files: list[dict[str, Any]] = []
        consumed: list[str] = []
        for staged_ref in staged_refs or ():
            file_obj, error = load_staged_image(str(staged_ref or "").strip())
            if error is not None:
                return _error_result(
                    code=str(error.get("code") or "staged_file_unavailable"),
                    message=str(error.get("message") or "Staged image could not be read."),
                    where=where,
                    ret=error,
                )
            files.append(file_obj)
            consumed.append(str(staged_ref).strip())

        result = await self.publish(
            text=text,
            files=files,
            alt_texts=alt_texts,
            account_id=account_id,
            visibility=visibility,
            where=where,
        )
        if isinstance(result, dict) and result.get("ok"):
            from kdcube_ai_app.apps.chat.sdk.integrations.file_staging import delete_staged

            root = staging_root_for_service()
            if root is not None:
                for staged_ref in consumed:
                    delete_staged(root, staged_ref)
        return result

    @kernel_function(
        name="comment_on_linkedin_post",
        description=(
            "Add a comment to a LinkedIn post as the connected member. Requires the user to connect "
            "LinkedIn with the linkedin:post claim in Connection Hub. post_urn is the value returned "
            "by post_linkedin_update, for example urn:li:share:7123456789. "
            "Returns {ok, error, ret}; ret={comment_id,comment_urn,post_urn,account_id}."
        ),
    )
    async def comment_on_linkedin_post(
        self,
        post_urn: Annotated[str, "Target post URN, e.g. urn:li:share:7123456789 or urn:li:ugcPost:7123456789."] = "",
        text: Annotated[str, "Comment text."] = "",
        account_id: Annotated[str, "Optional connected account id when several LinkedIn accounts are connected."] = "",
    ) -> Annotated[dict, "Envelope: {ok, error, ret}."]:
        return await _run_provider_call(
            where="linkedin.comment_on_linkedin_post",
            operation="socialActions.comments.create",
            mutating=True,
            run=lambda: self._comment(post_urn=post_urn, text=text, account_id=account_id),
        )

    async def _comment(self, *, post_urn: str, text: str, account_id: str) -> dict[str, Any]:
        where = "linkedin.comment_on_linkedin_post"
        target = str(post_urn or "").strip()
        if not target:
            return _error_result(code="post_urn_required", message="LinkedIn post URN is required.", where=where)
        if not str(text or "").strip():
            return _error_result(code="text_required", message="Comment text is required.", where=where)
        credential = await self._credential(claim=LINKEDIN_POST_CLAIM, account_id=account_id, tool_name=where)
        if not credential.ok:
            return credential.error_envelope(where=where)
        actor_urn = await self._author_urn(credential)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await rest_api.create_comment(
                client,
                access_token=credential.access_token,
                actor_urn=actor_urn,
                object_urn=target,
                text=str(text),
            )
        if response.status_code >= 400:
            failure = _linkedin_failure(
                response,
                operation="socialActions.comments.create",
                fallback="LinkedIn comment failed.",
                where=where,
                mutating=True,
            )
            if failure.credential_failure:
                return connected_account_auth_failure(credential, _auth_failure_message(failure))
            return failure.error_result(where=where)
        try:
            body = response.json()
        except Exception:
            body = {}
        comment_id = rest_api.created_urn_from_response(response)
        # Comments carry two independent identifier sources: the x-restli-id
        # header and the URN in the body. /rest/posts has only the header, so
        # its guard reads one field; this endpoint is unversioned /v2, where the
        # body carries the created object. The outcome is unknown only when
        # neither source produced anything.
        comment_urn = rest_api.comment_urn_from_body(body, comment_id=comment_id)
        if not comment_id and not comment_urn:
            return _error_result(
                code="linkedin_response_incomplete",
                message=(
                    "LinkedIn accepted the comment request but did not return its "
                    "identifier. The outcome is unknown; inspect the post before retrying."
                ),
                where=where,
                ret={
                    "provider": LINKEDIN_PROVIDER_ID,
                    "operation": "socialActions.comments.create",
                    "provider_status": int(response.status_code or 0),
                    "outcome_unknown": True,
                },
            )
        return _ok_ret_result(
            {
                "comment_id": comment_id,
                "comment_urn": comment_urn,
                "post_urn": target,
                "account_id": credential.account_id,
            }
        )


def create_linkedin_plugin() -> LinkedInTools:
    return LinkedInTools()


__all__ = [
    "API_VERSION_PROP",
    "LINKEDIN_POST_CLAIM",
    "LINKEDIN_PROFILE_CLAIM",
    "LINKEDIN_PROVIDER_ID",
    "LinkedInTools",
    "bind_integrations",
    "bind_service",
    "connected_linkedin_accounts",
    "create_linkedin_plugin",
    "linkedin_api_version",
    "load_image_artifact",
]
