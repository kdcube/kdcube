from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import aiofiles
import aiofiles.os
import httpx

try:
    from kdcube_ai_app.apps.chat.sdk.config import (
        delete_user_secret,
        get_secret,
        set_user_secret,
    )
except Exception:
    delete_user_secret = None  # type: ignore[assignment]
    get_secret = None  # type: ignore[assignment]
    set_user_secret = None  # type: ignore[assignment]

from kdcube_ai_app.apps.chat.sdk.integrations.integration_config import (
    integration_definition_value,
    integration_secret_value,
)


DEFAULT_LINKEDIN_BUNDLE_ID = "task-and-memo-app@1-0"
BUNDLE_ID = DEFAULT_LINKEDIN_BUNDLE_ID
logger = logging.getLogger("kdcube.integrations.linkedin")

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"
LINKEDIN_ASSETS_URL = "https://api.linkedin.com/v2/assets"
LINKEDIN_DOCUMENTS_URL = "https://api.linkedin.com/v2/documents"
LINKEDIN_SOCIAL_ACTIONS_URL = "https://api.linkedin.com/v2/socialActions"

LINKEDIN_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})
# PDF upload requires LinkedIn Marketing API partner access (not available to standard apps).
LINKEDIN_DOCUMENT_EXTENSIONS = frozenset({".pdf"})

DEFAULT_LINKEDIN_SCOPES = ("openid", "profile", "email", "w_member_social")


class ProviderHttpError(RuntimeError):
    def __init__(
        self,
        *,
        status: int,
        reason: str,
        body: str,
        parsed: Mapping[str, Any] | None = None,
        url: str = "",
    ):
        self.status = int(status or 0)
        self.reason = str(reason or "").strip()
        self.body = str(body or "")
        self.parsed = dict(parsed or {})
        self.url = str(url or "")
        super().__init__(self.message)

    @property
    def message(self) -> str:
        msg = str(self.parsed.get("message") or self.parsed.get("error_description") or "").strip()
        if msg:
            return msg
        if self.reason:
            return f"HTTP {self.status}: {self.reason}"
        return f"HTTP {self.status}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_segment(raw: str, *, fallback: str = "default") -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(raw or "")).strip("-")
    return value or fallback


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_json(data: Mapping[str, Any]) -> str:
    return _b64url(json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _unb64url_json(data: str) -> Dict[str, Any]:
    padded = data + ("=" * (-len(data) % 4))
    raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("state payload is invalid")
    return parsed


def _parse_json_object(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _secret_lookup(*keys: str) -> str:
    if get_secret is None:
        return ""
    for key in keys:
        value = await get_secret(key)
        if value:
            return value
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


async def oauth_state_secret(entrypoint: Any) -> str:
    return await integration_secret_value(entrypoint, provider="linkedin", field="oauth_state_secret")


def linkedin_client_id(entrypoint: Any) -> str:
    return str(integration_definition_value(entrypoint, provider="linkedin", key="client_id", default="") or "").strip()


async def linkedin_client_secret(entrypoint: Any) -> str:
    return await integration_secret_value(entrypoint, provider="linkedin", field="client_secret")


def linkedin_scopes(entrypoint: Any) -> List[str]:
    def _with_required(raw: Iterable[Any]) -> List[str]:
        scopes: List[str] = []
        seen: set[str] = set()
        for item in list(raw or []) + list(DEFAULT_LINKEDIN_SCOPES):
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            scopes.append(value)
        return scopes

    configured = integration_definition_value(entrypoint, provider="linkedin", key="scopes", default=None)
    if isinstance(configured, str) and configured.strip():
        return _with_required(configured.replace(",", " ").split())
    if isinstance(configured, list):
        return _with_required(configured)
    return list(DEFAULT_LINKEDIN_SCOPES)


def _request_public_base_url(request: Any) -> str:
    if request is None:
        return ""
    headers = getattr(request, "headers", {}) or {}
    proto = str(headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    host = str(headers.get("x-forwarded-host") or headers.get("host") or "").split(",", 1)[0].strip()
    if proto and host:
        return f"{proto}://{host}".rstrip("/")
    try:
        url = request.url
        return f"{url.scheme}://{url.netloc}".rstrip("/")
    except Exception:
        return ""


def callback_url(entrypoint: Any, *, request: Any = None) -> str:
    configured = str(
        integration_definition_value(entrypoint, provider="linkedin", key="oauth.redirect_uri", default="")
        or ""
    ).strip()
    if configured:
        return configured
    base = str(
        integration_definition_value(entrypoint, provider="linkedin", key="oauth.public_base_url", default="")
        or ""
    ).strip().rstrip("/")
    if not base:
        base = _request_public_base_url(request)
    if not base:
        raise ValueError("LinkedIn OAuth public base URL is unavailable")
    comm_context = getattr(entrypoint, "comm_context", None)
    actor = getattr(comm_context, "actor", None)
    tenant = str(getattr(actor, "tenant_id", "") or getattr(getattr(entrypoint, "settings", None), "TENANT", "") or "").strip()
    project = str(getattr(actor, "project_id", "") or getattr(getattr(entrypoint, "settings", None), "PROJECT", "") or "").strip()
    if not tenant or not project:
        raise ValueError("tenant/project are unavailable for LinkedIn OAuth callback URL")
    bundle_id = _entrypoint_bundle_id(entrypoint)
    return f"{base}/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/linkedin_oauth_callback"


class LinkedInAccountStore:
    def __init__(self, root: str | Path, *, user_id: str, bundle_id: str = BUNDLE_ID):
        self.root = Path(root).resolve()
        self.user_id = str(user_id or "anonymous").strip() or "anonymous"
        self.bundle_id = str(bundle_id or BUNDLE_ID).strip() or BUNDLE_ID
        self.safe_user_id = _safe_segment(self.user_id, fallback="anonymous")
        self.linkedin_dir = self.root / "linkedin" / self.safe_user_id
        self.accounts_path = self.linkedin_dir / "accounts.json"
        self.states_dir = self.root / "linkedin" / "_oauth_states"
        self.linkedin_dir.mkdir(parents=True, exist_ok=True)
        self.states_dir.mkdir(parents=True, exist_ok=True)

    def _empty_accounts(self) -> Dict[str, Any]:
        return {"schema_version": "linkedin-accounts.v1", "updated_at": _utc_now(), "accounts": []}

    async def _read_accounts_doc_async(self) -> Dict[str, Any]:
        if not self.accounts_path.exists():
            return self._empty_accounts()
        try:
            async with aiofiles.open(self.accounts_path, "r", encoding="utf-8") as fh:
                data = json.loads(await fh.read())
        except Exception:
            return self._empty_accounts()
        if not isinstance(data, dict):
            return self._empty_accounts()
        if not isinstance(data.get("accounts"), list):
            data["accounts"] = []
        return data

    async def _write_accounts_doc_async(self, data: Dict[str, Any]) -> Dict[str, Any]:
        data["schema_version"] = "linkedin-accounts.v1"
        data["updated_at"] = _utc_now()
        tmp = self.accounts_path.with_suffix(".json.tmp")
        async with aiofiles.open(tmp, "w", encoding="utf-8") as fh:
            await fh.write(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
        tmp.replace(self.accounts_path)
        return data

    async def list_accounts_async(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for raw in (await self._read_accounts_doc_async()).get("accounts") or []:
            if not isinstance(raw, dict):
                continue
            account_id = str(raw.get("account_id") or "").strip()
            if not account_id:
                continue
            item = dict(raw)
            item["has_token"] = bool(await self.get_tokens_async(account_id))
            rows.append(item)
        return sorted(rows, key=lambda item: str(item.get("display_name") or item.get("account_id") or ""))

    async def upsert_account_async(self, account: Mapping[str, Any]) -> Dict[str, Any]:
        now = _utc_now()
        provider = "linkedin"
        person_id = str(account.get("person_id") or "").strip()
        account_id = str(account.get("account_id") or "").strip()
        if not account_id:
            account_id = f"linkedin_{_safe_segment(person_id or uuid.uuid4().hex, fallback='account')}"
        row = {
            "account_id": account_id,
            "provider": provider,
            "person_id": person_id,
            "email": str(account.get("email") or "").strip(),
            "display_name": str(account.get("display_name") or person_id or account_id).strip(),
            "status": str(account.get("status") or "connected").strip().lower() or "connected",
            "scope": list(account.get("scope") or []),
            "connected_at": str(account.get("connected_at") or now),
            "updated_at": now,
            "last_error": str(account.get("last_error") or "").strip(),
        }
        data = await self._read_accounts_doc_async()
        rows = [item for item in data.get("accounts") or [] if isinstance(item, dict)]
        existing = next((item for item in rows if str(item.get("account_id") or "") == account_id), None)
        if existing is None and person_id:
            existing = next(
                (item for item in rows if str(item.get("person_id") or "") == person_id),
                None,
            )
        if existing:
            row["account_id"] = str(existing.get("account_id") or account_id)
            existing.update({key: value for key, value in row.items() if value not in (None, "") or key in {"last_error"}})
            row = existing
        else:
            rows.append(row)
        data["accounts"] = rows
        await self._write_accounts_doc_async(data)
        return dict(row)

    async def delete_account_async(self, account_id: str) -> bool:
        wanted = str(account_id or "").strip()
        data = await self._read_accounts_doc_async()
        rows = [item for item in data.get("accounts") or [] if isinstance(item, dict)]
        kept = [item for item in rows if str(item.get("account_id") or "") != wanted]
        deleted = len(kept) != len(rows)
        data["accounts"] = kept
        await self._write_accounts_doc_async(data)
        if deleted:
            await self.delete_tokens_async(wanted)
        return deleted

    @staticmethod
    def token_secret_key(account_id: str) -> str:
        return f"linkedin.accounts.{_safe_segment(account_id, fallback='account')}.tokens"

    async def set_tokens_async(self, account_id: str, tokens: Mapping[str, Any]) -> None:
        if set_user_secret is None:
            raise RuntimeError("async user-scoped secret storage is unavailable")
        await set_user_secret(
            self.token_secret_key(account_id),
            json.dumps(dict(tokens), sort_keys=True, ensure_ascii=True),
            user_id=self.user_id,
            bundle_id=self.bundle_id,
        )

    async def get_tokens_async(self, account_id: str) -> Dict[str, Any]:
        if get_secret is None:
            return {}
        raw = await get_secret(
            f"u:{self.token_secret_key(account_id)}",
            user_id=self.user_id,
            bundle_id=self.bundle_id,
        )
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def delete_tokens_async(self, account_id: str) -> None:
        if delete_user_secret is None:
            return
        try:
            await delete_user_secret(self.token_secret_key(account_id), user_id=self.user_id, bundle_id=self.bundle_id)
        except Exception:
            pass

    def _state_path(self, state: str) -> Path:
        digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
        return self.states_dir / f"{digest}.json"

    async def create_oauth_state_async(
        self,
        *,
        secret: str,
        source: str,
        return_hint: str = "",
        ttl_seconds: int = 900,
    ) -> Dict[str, Any]:
        if not str(secret or "").strip():
            raise ValueError("LinkedIn OAuth state secret is not configured")
        now = int(time.time())
        payload = {
            "v": 1,
            "provider": "linkedin",
            "user_id": self.user_id,
            "account_id": f"linkedin_{uuid.uuid4().hex[:12]}",
            "nonce": uuid.uuid4().hex,
            "source": str(source or "settings").strip() or "settings",
            "return_hint": str(return_hint or "").strip(),
            "iat": now,
            "exp": now + int(ttl_seconds or 900),
        }
        encoded = _b64url_json(payload)
        sig = hmac.new(str(secret).encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
        state = f"{encoded}.{sig}"
        async with aiofiles.open(self._state_path(state), "w", encoding="utf-8") as fh:
            await fh.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return {"state": state, "payload": payload}

    async def consume_oauth_state_async(self, *, state: str, secret: str) -> Dict[str, Any]:
        raw = str(state or "").strip()
        if "." not in raw:
            raise ValueError("LinkedIn OAuth state is invalid")
        encoded, received_sig = raw.rsplit(".", 1)
        expected = hmac.new(str(secret).encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received_sig, expected):
            raise ValueError("LinkedIn OAuth state signature is invalid")
        payload = _unb64url_json(encoded)
        if int(payload.get("exp") or 0) < int(time.time()):
            raise ValueError("LinkedIn OAuth state expired")
        path = self._state_path(raw)
        if not await aiofiles.os.path.exists(path):
            raise ValueError("LinkedIn OAuth state was not found or already used")
        await aiofiles.os.remove(path)
        return payload


async def build_linkedin_authorize_url(
    *,
    entrypoint: Any,
    store: LinkedInAccountStore,
    request: Any = None,
    source: str = "settings",
    return_hint: str = "",
) -> Dict[str, Any]:
    client_id = linkedin_client_id(entrypoint)
    if not client_id:
        raise ValueError("integrations[id=linkedin.*].definition.client_id is not configured")
    state = await store.create_oauth_state_async(
        secret=await oauth_state_secret(entrypoint),
        source=source,
        return_hint=return_hint,
    )
    redirect_uri = callback_url(entrypoint, request=request)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state["state"],
        "scope": " ".join(linkedin_scopes(entrypoint)),
    }
    return {
        "provider": "linkedin",
        "authorize_url": f"{LINKEDIN_AUTH_URL}?{urllib.parse.urlencode(params)}",
        "state_id": hashlib.sha256(state["state"].encode("utf-8")).hexdigest(),
        "account_id": state["payload"]["account_id"],
        "redirect_uri": redirect_uri,
    }


async def exchange_linkedin_code(*, code: str, redirect_uri: str, client_id: str, client_secret: str) -> Dict[str, Any]:
    if not client_id or not client_secret:
        raise ValueError("LinkedIn OAuth client id/secret are not configured")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                LINKEDIN_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LinkedIn token exchange failed: {exc}") from exc
    raw = response.text
    if response.status_code >= 400:
        raise ProviderHttpError(
            status=response.status_code,
            reason=str(response.reason_phrase or ""),
            body=raw[:8000],
            parsed=_parse_json_object(raw),
            url=LINKEDIN_TOKEN_URL,
        )
    token = _parse_json_object(raw)
    if "expires_in" in token:
        try:
            token["expires_at"] = int(time.time()) + int(token["expires_in"])
        except Exception:
            pass
    return token


async def fetch_linkedin_profile(*, access_token: str) -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                LINKEDIN_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LinkedIn userinfo request failed: {exc}") from exc
    raw = response.text
    if response.status_code >= 400:
        raise ProviderHttpError(
            status=response.status_code,
            reason=str(response.reason_phrase or ""),
            body=raw[:8000],
            parsed=_parse_json_object(raw),
            url=LINKEDIN_USERINFO_URL,
        )
    return _parse_json_object(raw)


async def create_linkedin_post(*, access_token: str, person_id: str, text: str) -> Dict[str, Any]:
    """Post a share to LinkedIn using the UGC Posts API."""
    payload = {
        "author": f"urn:li:person:{person_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                LINKEDIN_UGC_POSTS_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LinkedIn post request failed: {exc}") from exc
    raw = response.text
    if response.status_code >= 400:
        raise ProviderHttpError(
            status=response.status_code,
            reason=str(response.reason_phrase or ""),
            body=raw[:8000],
            parsed=_parse_json_object(raw),
            url=LINKEDIN_UGC_POSTS_URL,
        )
    post_id = (
        response.headers.get("x-restli-id")
        or response.headers.get("X-RestLi-Id")
        or ""
    )
    body = _parse_json_object(raw) if raw.strip() else {}
    return {"post_id": post_id, **body}


async def register_image_upload(*, access_token: str, person_id: str) -> Dict[str, Any]:
    """Register an image upload with LinkedIn and return upload_url + asset_urn."""
    request_body = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": f"urn:li:person:{person_id}",
            "serviceRelationships": [
                {"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}
            ],
        }
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{LINKEDIN_ASSETS_URL}?action=registerUpload",
                json=request_body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LinkedIn register image upload failed: {exc}") from exc
    raw = response.text
    if response.status_code >= 400:
        raise ProviderHttpError(
            status=response.status_code, reason=str(response.reason_phrase or ""),
            body=raw[:8000], parsed=_parse_json_object(raw), url=LINKEDIN_ASSETS_URL,
        )
    value = (_parse_json_object(raw).get("value") or {})
    mechanism = (value.get("uploadMechanism") or {}).get(
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ) or {}
    return {
        "upload_url": str(mechanism.get("uploadUrl") or ""),
        "upload_headers": dict(mechanism.get("headers") or {}),
        "asset_urn": str(value.get("asset") or ""),
    }


async def register_document_upload(*, access_token: str, person_id: str) -> Dict[str, Any]:
    """Register a document (PDF) upload.

    NOTE: LinkedIn's Documents Share API (/v2/documents) requires LinkedIn Marketing
    API partner access and is NOT available to standard OAuth apps with w_member_social.
    This will raise a ProviderHttpError(404) for regular developer apps.
    """
    request_body = {
        "initializeUploadRequest": {"owner": f"urn:li:person:{person_id}"}
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{LINKEDIN_DOCUMENTS_URL}?action=initializeUpload",
                json=request_body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LinkedIn register document upload failed: {exc}") from exc
    raw = response.text
    if response.status_code == 404:
        raise ProviderHttpError(
            status=404,
            reason="Not Found",
            body=raw[:8000],
            parsed={"message": (
                "LinkedIn Documents API is not available for this app. "
                "PDF uploads require LinkedIn Marketing API partner access. "
                "Use images (JPEG/PNG/GIF/WebP) instead."
            )},
            url=LINKEDIN_DOCUMENTS_URL,
        )
    if response.status_code >= 400:
        raise ProviderHttpError(
            status=response.status_code, reason=str(response.reason_phrase or ""),
            body=raw[:8000], parsed=_parse_json_object(raw), url=LINKEDIN_DOCUMENTS_URL,
        )
    value = (_parse_json_object(raw).get("value") or {})
    return {
        "upload_url": str(value.get("uploadUrl") or ""),
        "document_urn": str(value.get("document") or ""),
    }


async def upload_media_binary(
    *,
    upload_url: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    extra_headers: Optional[Dict[str, str]] = None,
) -> None:
    """PUT binary data to a LinkedIn upload URL (images or documents)."""
    if not upload_url:
        raise ValueError("upload_url is required")
    headers = {"Content-Type": content_type}
    headers.update(extra_headers or {})
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.put(upload_url, content=data, headers=headers)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LinkedIn media upload failed: {exc}") from exc
    if response.status_code >= 400:
        raise ProviderHttpError(
            status=response.status_code, reason=str(response.reason_phrase or ""),
            body=response.text[:8000], parsed={}, url=upload_url,
        )


async def create_linkedin_media_post(
    *,
    access_token: str,
    person_id: str,
    text: str,
    asset_urns: List[str],
    media_category: str = "IMAGE",
    media_titles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a LinkedIn post that references pre-uploaded media or document assets."""
    if not asset_urns:
        raise ValueError("asset_urns must not be empty")
    titles = list(media_titles or [])
    media = []
    for i, urn in enumerate(asset_urns):
        item: Dict[str, Any] = {"status": "READY", "media": urn}
        if i < len(titles) and titles[i]:
            item["title"] = {"text": str(titles[i])}
        media.append(item)
    payload = {
        "author": f"urn:li:person:{person_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": media_category,
                "media": media,
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                LINKEDIN_UGC_POSTS_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LinkedIn media post request failed: {exc}") from exc
    raw = response.text
    if response.status_code >= 400:
        raise ProviderHttpError(
            status=response.status_code, reason=str(response.reason_phrase or ""),
            body=raw[:8000], parsed=_parse_json_object(raw), url=LINKEDIN_UGC_POSTS_URL,
        )
    post_id = response.headers.get("x-restli-id") or response.headers.get("X-RestLi-Id") or ""
    body = _parse_json_object(raw) if raw.strip() else {}
    return {"post_id": post_id, **body}


async def add_linkedin_comment(
    *,
    access_token: str,
    person_id: str,
    post_urn: str,
    text: str,
    reply_to_comment_urn: str = "",
    asset_urn: str = "",
) -> Dict[str, Any]:
    """Post a comment on a LinkedIn share/ugcPost/activity.

    asset_urn is optional — pass a urn:li:digitalmediaAsset: from register_image_upload
    to attach an image. reply_to_comment_urn is a composite comment URN
    (urn:li:comment:(urn:li:activity:...,commentId)) for nested replies.
    """
    # TODO(linkedin): validate this helper against the current LinkedIn Comments API
    # before exposing it as production-supported. Current LinkedIn docs describe
    # /rest/socialActions with a Linkedin-Version header and a different image
    # attachment shape than the legacy /v2 endpoint below.
    import urllib.parse as _urlparse
    url = f"{LINKEDIN_SOCIAL_ACTIONS_URL}/{_urlparse.quote(post_urn, safe='')}/comments"
    payload: Dict[str, Any] = {
        "actor": f"urn:li:person:{person_id}",
        "message": {"text": text},
        "object": post_urn,
    }
    if reply_to_comment_urn.strip():
        payload["parentComment"] = reply_to_comment_urn.strip()
    if asset_urn.strip():
        payload["content"] = [{"entity": {"digitalmediaAsset": asset_urn.strip()}}]
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LinkedIn comment request failed: {exc}") from exc
    raw = response.text
    if response.status_code >= 400:
        raise ProviderHttpError(
            status=response.status_code, reason=str(response.reason_phrase or ""),
            body=raw[:8000], parsed=_parse_json_object(raw), url=url,
        )
    comment_id = response.headers.get("x-restli-id") or response.headers.get("X-RestLi-Id") or ""
    body = _parse_json_object(raw) if raw.strip() else {}
    comment_urn = body.get("$URN") or (f"urn:li:comment:({post_urn},{comment_id})" if comment_id else "")
    return {"comment_id": comment_id, "comment_urn": comment_urn, **body}
