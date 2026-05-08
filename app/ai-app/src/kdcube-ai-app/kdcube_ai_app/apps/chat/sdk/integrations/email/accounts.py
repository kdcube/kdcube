from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import aiofiles
import httpx

try:
    from kdcube_ai_app.apps.chat.sdk.config import (
        delete_user_secret,
        delete_user_secret_async,
        get_secret,
        get_secret_async,
        get_user_secret,
        get_user_secret_async,
        set_user_secret,
        set_user_secret_async,
    )
except Exception:
    delete_user_secret = None  # type: ignore[assignment]
    delete_user_secret_async = None  # type: ignore[assignment]
    get_secret = None  # type: ignore[assignment]
    get_secret_async = None  # type: ignore[assignment]
    get_user_secret = None  # type: ignore[assignment]
    get_user_secret_async = None  # type: ignore[assignment]
    set_user_secret = None  # type: ignore[assignment]
    set_user_secret_async = None  # type: ignore[assignment]


DEFAULT_EMAIL_BUNDLE_ID = "task-and-memo-app@1-0"
BUNDLE_ID = DEFAULT_EMAIL_BUNDLE_ID
logger = logging.getLogger("kdcube.integrations.email")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_GOOGLE_SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
)


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
        provider_error = self.parsed.get("error") if isinstance(self.parsed.get("error"), Mapping) else {}
        provider_message = str(provider_error.get("message") or "").strip()
        if provider_message:
            return provider_message
        if self.reason:
            return f"HTTP {self.status}: {self.reason}"
        return f"HTTP {self.status}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_segment(raw: str, *, fallback: str = "default") -> str:
    import re

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


async def _json_request(
    url: str,
    *,
    data: Optional[Mapping[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    request_headers = dict(headers or {})
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if data is not None:
                request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
                response = await client.post(url, data=dict(data), headers=request_headers)
            else:
                response = await client.get(url, headers=request_headers)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"provider request failed: {exc}") from exc

    raw = response.text
    if response.status_code >= 400:
        raise ProviderHttpError(
            status=response.status_code,
            reason=str(response.reason_phrase or ""),
            body=raw[:8000],
            parsed=_parse_json_object(raw),
            url=url,
        )
    parsed = _parse_json_object(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("provider returned non-object JSON")
    return parsed


def _google_error_info(exc: ProviderHttpError) -> Dict[str, Any]:
    provider_error = exc.parsed.get("error") if isinstance(exc.parsed.get("error"), Mapping) else {}
    details = provider_error.get("details") if isinstance(provider_error.get("details"), list) else []
    reason = ""
    domain = ""
    metadata: Dict[str, Any] = {}
    for detail in details:
        if not isinstance(detail, Mapping):
            continue
        if str(detail.get("@type") or "").endswith("google.rpc.ErrorInfo"):
            reason = str(detail.get("reason") or "").strip()
            domain = str(detail.get("domain") or "").strip()
            if isinstance(detail.get("metadata"), Mapping):
                metadata = {str(key): value for key, value in detail.get("metadata", {}).items()}
            break
    return {
        "http_status": exc.status,
        "http_reason": exc.reason,
        "provider_status": str(provider_error.get("status") or "").strip(),
        "provider_reason": reason,
        "provider_domain": domain,
        "provider_message": str(provider_error.get("message") or exc.message).strip(),
        "provider_metadata": metadata,
    }


def _google_error_payload(exc: ProviderHttpError, *, operation: str, account: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    info = _google_error_info(exc)
    reason_upper = " ".join(
        str(item or "").upper()
        for item in (
            info.get("provider_reason"),
            info.get("provider_status"),
            info.get("provider_message"),
            info.get("http_reason"),
        )
    )

    code = "google_provider_request_failed"
    category = "provider_error"
    user_action_required = False
    message = str(info.get("provider_message") or exc.message)

    if exc.status == 401:
        code = "email_oauth_token_invalid"
        category = "user_action_required"
        user_action_required = True
    elif exc.status == 403 and "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in reason_upper:
        required_scope = (
            "https://www.googleapis.com/auth/gmail.send"
            if "send" in str(operation or "").lower()
            else "https://www.googleapis.com/auth/gmail.readonly"
        )
        code = "google_scope_insufficient"
        category = "user_action_required"
        user_action_required = True
        message = (
            f"{message} The connected account token is missing required Gmail scope "
            f"{required_scope}."
        )
    elif exc.status == 403 and (
        "SERVICE_DISABLED" in reason_upper
        or "ACCESSNOTCONFIGURED" in reason_upper
        or "HAS NOT BEEN USED" in reason_upper
        or "IS DISABLED" in reason_upper
    ):
        code = "google_gmail_api_not_enabled"
        category = "deployment_config"
        message = (
            f"{message} Enable Gmail API for the Google Cloud project used by "
            "this OAuth client, then retry."
        )
    elif exc.status == 403:
        code = "google_provider_forbidden"
        category = "provider_policy"

    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "category": category,
            "user_action_required": user_action_required,
            "provider": "google",
            "operation": operation,
            **info,
        },
        "account": account or {},
    }


def _secret_lookup(*keys: str) -> str:
    if get_secret is None:
        return ""
    for key in keys:
        value = get_secret(key)
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


async def _secret_lookup_async(*keys: str) -> str:
    if get_secret_async is None:
        return ""
    for key in keys:
        value = await get_secret_async(key)
        if value:
            return value
    return ""


def oauth_state_secret(entrypoint: Any) -> str:
    bundle_id = _entrypoint_bundle_id(entrypoint)
    return (
        _secret_lookup(
            "b:integrations.email.oauth_state_secret",
            f"bundles.{bundle_id}.secrets.integrations.email.oauth_state_secret",
            "b:integrations.telegram.webhook_secret",
            f"bundles.{bundle_id}.secrets.integrations.telegram.webhook_secret",
            "b:integrations.telegram.bot_token",
            f"bundles.{bundle_id}.secrets.integrations.telegram.bot_token",
        )
        or str(entrypoint.bundle_prop("integrations.email.oauth.state_secret", "") or "").strip()
    )


def google_client_id(entrypoint: Any) -> str:
    return str(entrypoint.bundle_prop("integrations.email.google.client_id", "") or "").strip()


def google_client_secret(bundle_id: str = "") -> str:
    bundle = str(bundle_id or BUNDLE_ID).strip() or BUNDLE_ID
    return _secret_lookup(
        "b:integrations.email.google.client_secret",
        f"bundles.{bundle}.secrets.integrations.email.google.client_secret",
    )


async def google_client_secret_async(bundle_id: str = "") -> str:
    bundle = str(bundle_id or BUNDLE_ID).strip() or BUNDLE_ID
    return await _secret_lookup_async(
        "b:integrations.email.google.client_secret",
        f"bundles.{bundle}.secrets.integrations.email.google.client_secret",
    )


def google_scopes(entrypoint: Any) -> List[str]:
    def _with_required_scopes(raw: Iterable[Any]) -> List[str]:
        scopes: List[str] = []
        seen: set[str] = set()
        for item in list(raw or []) + list(DEFAULT_GOOGLE_SCOPES):
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            scopes.append(value)
        return scopes

    configured = entrypoint.bundle_prop("integrations.email.google.scopes", None)
    if isinstance(configured, str) and configured.strip():
        return _with_required_scopes(configured.replace(",", " ").split())
    if isinstance(configured, list):
        return _with_required_scopes(configured)
    return list(DEFAULT_GOOGLE_SCOPES)


def request_public_base_url(request: Any) -> str:
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
    configured = str(entrypoint.bundle_prop("integrations.email.oauth.redirect_uri", "") or "").strip()
    if configured:
        return configured
    base = str(entrypoint.bundle_prop("integrations.email.oauth.public_base_url", "") or "").strip().rstrip("/")
    if not base:
        base = request_public_base_url(request)
    if not base:
        raise ValueError("email OAuth public base URL is unavailable")
    comm_context = getattr(entrypoint, "comm_context", None)
    actor = getattr(comm_context, "actor", None)
    tenant = str(getattr(actor, "tenant_id", "") or getattr(getattr(entrypoint, "settings", None), "TENANT", "") or "").strip()
    project = str(getattr(actor, "project_id", "") or getattr(getattr(entrypoint, "settings", None), "PROJECT", "") or "").strip()
    if not tenant or not project:
        raise ValueError("tenant/project are unavailable for email OAuth callback URL")
    bundle_id = _entrypoint_bundle_id(entrypoint)
    return f"{base}/api/integrations/bundles/{tenant}/{project}/{bundle_id}/public/email_oauth_callback"


class EmailAccountStore:
    def __init__(self, root: str | Path, *, user_id: str, bundle_id: str = BUNDLE_ID):
        self.root = Path(root).resolve()
        self.user_id = str(user_id or "anonymous").strip() or "anonymous"
        self.bundle_id = str(bundle_id or BUNDLE_ID).strip() or BUNDLE_ID
        self.safe_user_id = _safe_segment(self.user_id, fallback="anonymous")
        self.email_dir = self.root / "email" / self.safe_user_id
        self.accounts_path = self.email_dir / "accounts.json"
        self.states_dir = self.root / "email" / "_oauth_states"
        self.run_dir = self.root / "email" / "runs" / self.safe_user_id
        self.email_dir.mkdir(parents=True, exist_ok=True)
        self.states_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def _empty_accounts(self) -> Dict[str, Any]:
        return {"schema_version": "email-accounts.v1", "updated_at": _utc_now(), "accounts": []}

    def _read_accounts_doc(self) -> Dict[str, Any]:
        if not self.accounts_path.exists():
            return self._empty_accounts()
        try:
            data = json.loads(self.accounts_path.read_text(encoding="utf-8"))
        except Exception:
            return self._empty_accounts()
        if not isinstance(data, dict):
            return self._empty_accounts()
        if not isinstance(data.get("accounts"), list):
            data["accounts"] = []
        return data

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

    def _write_accounts_doc(self, data: Dict[str, Any]) -> Dict[str, Any]:
        data["schema_version"] = "email-accounts.v1"
        data["updated_at"] = _utc_now()
        tmp = self.accounts_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
        tmp.replace(self.accounts_path)
        return data

    async def _write_accounts_doc_async(self, data: Dict[str, Any]) -> Dict[str, Any]:
        data["schema_version"] = "email-accounts.v1"
        data["updated_at"] = _utc_now()
        tmp = self.accounts_path.with_suffix(".json.tmp")
        async with aiofiles.open(tmp, "w", encoding="utf-8") as fh:
            await fh.write(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
        tmp.replace(self.accounts_path)
        return data

    def list_accounts(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for raw in self._read_accounts_doc().get("accounts") or []:
            if not isinstance(raw, dict):
                continue
            account_id = str(raw.get("account_id") or "").strip()
            if not account_id:
                continue
            item = dict(raw)
            item["has_token"] = bool(self.get_tokens(account_id))
            rows.append(item)
        return sorted(rows, key=lambda item: (str(item.get("provider") or ""), str(item.get("email") or item.get("account_id") or "")))

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
        return sorted(rows, key=lambda item: (str(item.get("provider") or ""), str(item.get("email") or item.get("account_id") or "")))

    def get_account(self, account_id: str) -> Dict[str, Any] | None:
        wanted = str(account_id or "").strip()
        if not wanted:
            return None
        for item in self.list_accounts():
            if str(item.get("account_id") or "") == wanted or str(item.get("email") or "").lower() == wanted.lower():
                return item
        return None

    async def get_account_async(self, account_id: str) -> Dict[str, Any] | None:
        wanted = str(account_id or "").strip()
        if not wanted:
            return None
        for item in await self.list_accounts_async():
            if str(item.get("account_id") or "") == wanted or str(item.get("email") or "").lower() == wanted.lower():
                return item
        return None

    def upsert_account(self, account: Mapping[str, Any]) -> Dict[str, Any]:
        now = _utc_now()
        provider = str(account.get("provider") or "google").strip().lower() or "google"
        email = str(account.get("email") or "").strip()
        account_id = str(account.get("account_id") or "").strip()
        if not account_id:
            account_id = f"{provider}_{_safe_segment(email or uuid.uuid4().hex, fallback='account')}"
        settings = account.get("settings") if isinstance(account.get("settings"), Mapping) else {}
        row = {
            "account_id": account_id,
            "provider": provider,
            "email": email,
            "display_name": str(account.get("display_name") or email or account_id).strip(),
            "status": str(account.get("status") or "connected").strip().lower() or "connected",
            "scope": list(account.get("scope") or []),
            "settings": {str(key): value for key, value in dict(settings).items()} if settings else {},
            "connected_at": str(account.get("connected_at") or now),
            "updated_at": now,
            "last_error": str(account.get("last_error") or "").strip(),
        }
        data = self._read_accounts_doc()
        rows = [item for item in data.get("accounts") or [] if isinstance(item, dict)]
        existing = next((item for item in rows if str(item.get("account_id") or "") == account_id), None)
        if existing is None and email:
            email_norm = email.lower()
            existing = next(
                (
                    item
                    for item in rows
                    if str(item.get("provider") or "").strip().lower() == provider
                    and str(item.get("email") or "").strip().lower() == email_norm
                ),
                None,
            )
        if existing:
            row["account_id"] = str(existing.get("account_id") or account_id)
            existing.update({key: value for key, value in row.items() if value not in (None, "") or key in {"last_error"}})
            row = existing
        else:
            rows.append(row)
        data["accounts"] = rows
        self._write_accounts_doc(data)
        return dict(row)

    def delete_account(self, account_id: str) -> bool:
        wanted = str(account_id or "").strip()
        data = self._read_accounts_doc()
        rows = [item for item in data.get("accounts") or [] if isinstance(item, dict)]
        kept = [item for item in rows if str(item.get("account_id") or "") != wanted]
        deleted = len(kept) != len(rows)
        data["accounts"] = kept
        self._write_accounts_doc(data)
        if deleted:
            self.delete_tokens(wanted)
        return deleted

    @staticmethod
    def token_secret_key(account_id: str) -> str:
        return f"email.accounts.{_safe_segment(account_id, fallback='account')}.tokens"

    def set_tokens(self, account_id: str, tokens: Mapping[str, Any]) -> None:
        if set_user_secret is None:
            raise RuntimeError("user-scoped secret storage is unavailable")
        set_user_secret(
            self.token_secret_key(account_id),
            json.dumps(dict(tokens), sort_keys=True, ensure_ascii=True),
            user_id=self.user_id,
            bundle_id=self.bundle_id,
        )

    async def set_tokens_async(self, account_id: str, tokens: Mapping[str, Any]) -> None:
        if set_user_secret_async is None:
            raise RuntimeError("async user-scoped secret storage is unavailable")
        await set_user_secret_async(
            self.token_secret_key(account_id),
            json.dumps(dict(tokens), sort_keys=True, ensure_ascii=True),
            user_id=self.user_id,
            bundle_id=self.bundle_id,
        )

    def get_tokens(self, account_id: str) -> Dict[str, Any]:
        if get_user_secret is None:
            return {}
        raw = get_user_secret(
            self.token_secret_key(account_id),
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

    async def get_tokens_async(self, account_id: str) -> Dict[str, Any]:
        if get_user_secret_async is None:
            return {}
        raw = await get_user_secret_async(
            self.token_secret_key(account_id),
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

    def delete_tokens(self, account_id: str) -> None:
        if delete_user_secret is None:
            return
        try:
            delete_user_secret(self.token_secret_key(account_id), user_id=self.user_id, bundle_id=self.bundle_id)
        except Exception:
            pass

    async def delete_tokens_async(self, account_id: str) -> None:
        if delete_user_secret_async is None:
            return
        try:
            await delete_user_secret_async(self.token_secret_key(account_id), user_id=self.user_id, bundle_id=self.bundle_id)
        except Exception:
            pass

    def _state_path(self, state: str) -> Path:
        digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
        return self.states_dir / f"{digest}.json"

    def create_oauth_state(
        self,
        *,
        secret: str,
        provider: str,
        source: str,
        return_hint: str = "",
        ttl_seconds: int = 900,
    ) -> Dict[str, Any]:
        if not str(secret or "").strip():
            raise ValueError("email OAuth state secret is not configured")
        now = int(time.time())
        payload = {
            "v": 1,
            "provider": str(provider or "google").strip().lower() or "google",
            "user_id": self.user_id,
            "account_id": f"{str(provider or 'google').strip().lower() or 'google'}_{uuid.uuid4().hex[:12]}",
            "nonce": uuid.uuid4().hex,
            "source": str(source or "settings").strip() or "settings",
            "return_hint": str(return_hint or "").strip(),
            "iat": now,
            "exp": now + int(ttl_seconds or 900),
        }
        encoded = _b64url_json(payload)
        sig = hmac.new(str(secret).encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
        state = f"{encoded}.{sig}"
        self._state_path(state).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"state": state, "payload": payload}

    def consume_oauth_state(self, *, state: str, secret: str) -> Dict[str, Any]:
        raw = str(state or "").strip()
        if "." not in raw:
            raise ValueError("email OAuth state is invalid")
        encoded, received_sig = raw.rsplit(".", 1)
        expected = hmac.new(str(secret).encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received_sig, expected):
            raise ValueError("email OAuth state signature is invalid")
        payload = _unb64url_json(encoded)
        if int(payload.get("exp") or 0) < int(time.time()):
            raise ValueError("email OAuth state expired")
        path = self._state_path(raw)
        if not path.exists():
            raise ValueError("email OAuth state was not found or already used")
        path.unlink()
        return payload

    def run_state_path(self, *, task_id: str, account_id: str) -> Path:
        safe_task = _safe_segment(task_id or "manual", fallback="manual")
        safe_account = _safe_segment(account_id or "account", fallback="account")
        path = self.run_dir / safe_task
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{safe_account}.json"

    def read_run_state(self, *, task_id: str, account_id: str) -> Dict[str, Any]:
        path = self.run_state_path(task_id=task_id, account_id=account_id)
        if not path.exists():
            return {"schema_version": "email-run-state.v1"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"schema_version": "email-run-state.v1"}
        return data if isinstance(data, dict) else {"schema_version": "email-run-state.v1"}

    async def read_run_state_async(self, *, task_id: str, account_id: str) -> Dict[str, Any]:
        path = self.run_state_path(task_id=task_id, account_id=account_id)
        if not path.exists():
            return {"schema_version": "email-run-state.v1"}
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as fh:
                data = json.loads(await fh.read())
        except Exception:
            return {"schema_version": "email-run-state.v1"}
        return data if isinstance(data, dict) else {"schema_version": "email-run-state.v1"}

    def write_run_state(self, *, task_id: str, account_id: str, data: Mapping[str, Any]) -> Dict[str, Any]:
        payload = dict(data)
        payload["schema_version"] = "email-run-state.v1"
        payload["updated_at"] = _utc_now()
        path = self.run_state_path(task_id=task_id, account_id=account_id)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
        return payload

    async def write_run_state_async(self, *, task_id: str, account_id: str, data: Mapping[str, Any]) -> Dict[str, Any]:
        payload = dict(data)
        payload["schema_version"] = "email-run-state.v1"
        payload["updated_at"] = _utc_now()
        path = self.run_state_path(task_id=task_id, account_id=account_id)
        async with aiofiles.open(path, "w", encoding="utf-8") as fh:
            await fh.write(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
        return payload


def build_google_authorize_url(
    *,
    entrypoint: Any,
    store: EmailAccountStore,
    request: Any = None,
    source: str = "settings",
    return_hint: str = "",
) -> Dict[str, Any]:
    client_id = google_client_id(entrypoint)
    if not client_id:
        raise ValueError("integrations.email.google.client_id is not configured")
    state = store.create_oauth_state(
        secret=oauth_state_secret(entrypoint),
        provider="google",
        source=source,
        return_hint=return_hint,
    )
    redirect_uri = callback_url(entrypoint, request=request)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(google_scopes(entrypoint)),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state["state"],
    }
    return {
        "provider": "google",
        "authorize_url": f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}",
        "state_id": hashlib.sha256(state["state"].encode("utf-8")).hexdigest(),
        "account_id": state["payload"]["account_id"],
        "redirect_uri": redirect_uri,
    }


async def exchange_google_code(*, code: str, redirect_uri: str, client_id: str, client_secret: str) -> Dict[str, Any]:
    if not client_id or not client_secret:
        raise ValueError("Google OAuth client id/secret are not configured")
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    token = await _json_request(GOOGLE_TOKEN_URL, data=data)
    if "expires_in" in token:
        try:
            token["expires_at"] = int(time.time()) + int(token.get("expires_in") or 0)
        except Exception:
            pass
    return token


async def refresh_google_token(*, token: Mapping[str, Any], client_id: str, client_secret: str) -> Dict[str, Any]:
    refresh_token = str(token.get("refresh_token") or "").strip()
    if not refresh_token:
        return dict(token)
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    refreshed = await _json_request(GOOGLE_TOKEN_URL, data=data)
    merged = dict(token)
    merged.update(refreshed)
    if "expires_in" in refreshed:
        try:
            merged["expires_at"] = int(time.time()) + int(refreshed.get("expires_in") or 0)
        except Exception:
            pass
    return merged


async def _google_get(url: str, *, access_token: str) -> Dict[str, Any]:
    return await _json_request(url, headers={"Authorization": f"Bearer {access_token}"})


async def fetch_google_profile(*, access_token: str) -> Dict[str, Any]:
    if not str(access_token or "").strip():
        return {}
    try:
        return await _google_get(GOOGLE_USERINFO_URL, access_token=access_token)
    except Exception:
        return {}


def _header_value(headers: Iterable[Mapping[str, Any]], name: str) -> str:
    wanted = name.lower()
    for header in headers:
        if str(header.get("name") or "").lower() == wanted:
            return str(header.get("value") or "").strip()
    return ""


def _plain_body(payload: Mapping[str, Any]) -> str:
    data = str((payload.get("body") or {}).get("data") or "")
    if data:
        try:
            return base64.urlsafe_b64decode(data + ("=" * (-len(data) % 4))).decode("utf-8", errors="replace")
        except Exception:
            return ""
    for part in payload.get("parts") or []:
        if isinstance(part, Mapping):
            text = _plain_body(part)
            if text:
                return text
    return ""


def _payload_attachments(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    attachments: List[Dict[str, Any]] = []

    def walk(part: Mapping[str, Any]) -> None:
        body = part.get("body") if isinstance(part.get("body"), Mapping) else {}
        filename = str(part.get("filename") or "").strip()
        attachment_id = str(body.get("attachmentId") or "").strip()
        if filename or attachment_id:
            attachments.append(
                {
                    "part_id": str(part.get("partId") or "").strip(),
                    "attachment_id": attachment_id,
                    "filename": filename,
                    "mime_type": str(part.get("mimeType") or "").strip(),
                    "size_bytes": int(body.get("size") or 0),
                }
            )
        for child in part.get("parts") or []:
            if isinstance(child, Mapping):
                walk(child)

    walk(payload)
    return [item for item in attachments if item.get("attachment_id") or item.get("filename")]


def _gmail_date_token(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if "/" in value:
        return value[:20]
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%Y/%m/%d")
    except Exception:
        return value[:20]


def _message_summary(message: Mapping[str, Any], *, body_limit: int = 4000, include_body: bool = False) -> Dict[str, Any]:
    payload = message.get("payload") if isinstance(message.get("payload"), Mapping) else {}
    headers = payload.get("headers") if isinstance(payload.get("headers"), list) else []
    body = _plain_body(payload)
    limit = max(0, int(body_limit or 0))
    summary = {
        "message_id": str(message.get("id") or "").strip(),
        "thread_id": str(message.get("threadId") or "").strip(),
        "from": _header_value(headers, "From"),
        "to": _header_value(headers, "To"),
        "subject": _header_value(headers, "Subject"),
        "date": _header_value(headers, "Date"),
        "internal_date": str(message.get("internalDate") or "").strip(),
        "snippet": str(message.get("snippet") or "").strip(),
        "body_excerpt": body[: min(limit or 4000, 4000)],
        "body_truncated": bool(limit and len(body) > limit),
        "label_ids": list(message.get("labelIds") or []),
        "size_estimate": int(message.get("sizeEstimate") or 0),
        "attachments": _payload_attachments(payload),
    }
    summary["has_attachments"] = bool(summary["attachments"])
    if include_body:
        summary["body"] = body[:limit] if limit else body
    return summary


async def ensure_google_access_token(
    *,
    store: EmailAccountStore,
    entrypoint: Any,
    account: Mapping[str, Any],
) -> Dict[str, Any]:
    account_id = str(account.get("account_id") or "").strip()
    account_email = str(account.get("email") or account_id or "").strip()
    token = await store.get_tokens_async(account_id)
    if not token:
        logger.warning(
            "[email.gmail] missing stored OAuth token | account=%s",
            account_email,
        )
        return {"ok": False, "error": {"code": "email_account_not_connected", "message": "Email account has no stored OAuth token."}}
    client_id = google_client_id(entrypoint)
    client_secret = await google_client_secret_async()
    expires_at = int(token.get("expires_at") or 0)
    if expires_at and expires_at < int(time.time()) + 120:
        try:
            token = await refresh_google_token(token=token, client_id=client_id, client_secret=client_secret)
            await store.set_tokens_async(account_id, token)
        except ProviderHttpError as exc:
            return _google_error_payload(exc, operation="oauth_token_refresh", account=account)
    access_token = str(token.get("access_token") or "").strip()
    if not access_token:
        return {"ok": False, "error": {"code": "email_account_not_connected", "message": "Email account has no access token."}}
    return {"ok": True, "access_token": access_token, "account": account}


async def fetch_google_messages(
    *,
    store: EmailAccountStore,
    entrypoint: Any,
    account: Mapping[str, Any],
    mailbox: str = "",
    unread_only: bool = True,
    limit: int = 20,
    gmail_query: str = "",
    from_email: str = "",
    to_email: str = "",
    subject: str = "",
    since: str = "",
    before: str = "",
    text: str = "",
) -> Dict[str, Any]:
    account_id = str(account.get("account_id") or "").strip()
    account_email = str(account.get("email") or account_id or "").strip()
    token_result = await ensure_google_access_token(store=store, entrypoint=entrypoint, account=account)
    if not token_result.get("ok"):
        return token_result
    access_token = str(token_result.get("access_token") or "").strip()

    clauses: List[str] = []
    if unread_only:
        clauses.append("is:unread")
    mailbox_norm = str(mailbox or "inbox").strip()
    if mailbox_norm:
        clauses.append(f"in:{mailbox_norm}")
    gmail_query_norm = str(gmail_query or "").strip()
    if gmail_query_norm:
        clauses.append(gmail_query_norm[:500])
    if str(from_email or "").strip():
        clauses.append(f"from:{str(from_email).strip()[:200]}")
    if str(to_email or "").strip():
        clauses.append(f"to:{str(to_email).strip()[:200]}")
    if str(subject or "").strip():
        escaped = str(subject).strip()[:200].replace('"', r"\"")
        clauses.append(f'subject:"{escaped}"')
    if str(since or "").strip():
        clauses.append(f"after:{_gmail_date_token(str(since).strip())}")
    if str(before or "").strip():
        clauses.append(f"before:{_gmail_date_token(str(before).strip())}")
    if str(text or "").strip():
        escaped = str(text).strip()[:200].replace('"', r"\"")
        clauses.append(f'"{escaped}"')
    query = " ".join(clauses)
    list_params = {"maxResults": max(1, min(int(limit or 20), 50))}
    if query:
        list_params["q"] = query
    logger.info(
        "[email.gmail] fetch start | account=%s mailbox=%s unread_only=%s limit=%s query=%r",
        account_email,
        mailbox_norm or "inbox",
        bool(unread_only),
        list_params["maxResults"],
        query,
    )
    try:
        listing = await _google_get(f"{GOOGLE_GMAIL_API}/messages?{urllib.parse.urlencode(list_params)}", access_token=access_token)
    except ProviderHttpError as exc:
        logger.warning(
            "[email.gmail] list failed | account=%s status=%s reason=%s message=%s",
            account_email,
            exc.status,
            exc.reason,
            exc.message,
        )
        return _google_error_payload(exc, operation="gmail_messages_list", account=account)
    messages = []
    for row in listing.get("messages") or []:
        message_id = str(row.get("id") or "").strip() if isinstance(row, Mapping) else ""
        if not message_id:
            continue
        try:
            detail = await _google_get(
                f"{GOOGLE_GMAIL_API}/messages/{urllib.parse.quote(message_id)}?format=full",
                access_token=access_token,
            )
        except ProviderHttpError as exc:
            logger.warning(
                "[email.gmail] message fetch failed | account=%s message_id=%s status=%s reason=%s message=%s",
                account_email,
                message_id,
                exc.status,
                exc.reason,
                exc.message,
            )
            return _google_error_payload(exc, operation="gmail_messages_get", account=account)
        messages.append(_message_summary(detail))
    logger.info(
        "[email.gmail] fetch done | account=%s returned=%s result_size_estimate=%s query=%r",
        account_email,
        len(messages),
        listing.get("resultSizeEstimate"),
        query,
    )
    return {"ok": True, "messages": messages, "result_size_estimate": listing.get("resultSizeEstimate")}


async def fetch_google_message(
    *,
    store: EmailAccountStore,
    entrypoint: Any,
    account: Mapping[str, Any],
    message_id: str,
    body_limit: int = 20000,
) -> Dict[str, Any]:
    account_id = str(account.get("account_id") or "").strip()
    account_email = str(account.get("email") or account_id or "").strip()
    wanted = str(message_id or "").strip()
    if not wanted:
        return {"ok": False, "error": {"code": "email_message_id_required", "message": "message_id is required."}}
    token_result = await ensure_google_access_token(store=store, entrypoint=entrypoint, account=account)
    if not token_result.get("ok"):
        return token_result
    access_token = str(token_result.get("access_token") or "").strip()
    try:
        detail = await _google_get(
            f"{GOOGLE_GMAIL_API}/messages/{urllib.parse.quote(wanted)}?format=full",
            access_token=access_token,
        )
    except ProviderHttpError as exc:
        logger.warning(
            "[email.gmail] message fetch failed | account=%s message_id=%s status=%s reason=%s message=%s",
            account_email,
            wanted,
            exc.status,
            exc.reason,
            exc.message,
        )
        return _google_error_payload(exc, operation="gmail_messages_get", account=account)
    return {"ok": True, "message": _message_summary(detail, body_limit=body_limit, include_body=True)}


async def fetch_google_attachment(
    *,
    store: EmailAccountStore,
    entrypoint: Any,
    account: Mapping[str, Any],
    message_id: str,
    attachment_id: str,
    max_bytes: int = 5 * 1024 * 1024,
) -> Dict[str, Any]:
    account_id = str(account.get("account_id") or "").strip()
    account_email = str(account.get("email") or account_id or "").strip()
    wanted_message = str(message_id or "").strip()
    wanted_attachment = str(attachment_id or "").strip()
    if not wanted_message or not wanted_attachment:
        return {
            "ok": False,
            "error": {
                "code": "email_attachment_id_required",
                "message": "message_id and attachment_id are required.",
            },
        }
    token_result = await ensure_google_access_token(store=store, entrypoint=entrypoint, account=account)
    if not token_result.get("ok"):
        return token_result
    access_token = str(token_result.get("access_token") or "").strip()
    try:
        detail = await _google_get(
            f"{GOOGLE_GMAIL_API}/messages/{urllib.parse.quote(wanted_message)}?format=full",
            access_token=access_token,
        )
    except ProviderHttpError as exc:
        return _google_error_payload(exc, operation="gmail_messages_get", account=account)
    attachments = _payload_attachments(detail.get("payload") if isinstance(detail.get("payload"), Mapping) else {})
    meta = next((item for item in attachments if str(item.get("attachment_id") or "") == wanted_attachment), None)
    if not meta:
        logger.warning(
            "[email.gmail] attachment metadata id not present on refetch; trying attachment endpoint | "
            "account=%s message_id=%s attachment_id=%s available=%s",
            account_email,
            wanted_message,
            wanted_attachment[:80],
            [
                {
                    "part_id": item.get("part_id"),
                    "filename": item.get("filename"),
                    "attachment_id_prefix": str(item.get("attachment_id") or "")[:40],
                }
                for item in attachments[:10]
            ],
        )
        meta = {"attachment_id": wanted_attachment}
    size_bytes = int(meta.get("size_bytes") or 0)
    max_size = max(1, min(int(max_bytes or 0), 10 * 1024 * 1024))
    if size_bytes and size_bytes > max_size:
        return {
            "ok": False,
            "error": {
                "code": "email_attachment_too_large",
                "message": f"Attachment is {size_bytes} bytes, above the {max_size} byte MCP read limit.",
                "size_bytes": size_bytes,
                "max_bytes": max_size,
                "attachment": meta,
            },
        }
    try:
        attachment = await _google_get(
            f"{GOOGLE_GMAIL_API}/messages/{urllib.parse.quote(wanted_message)}/attachments/{urllib.parse.quote(wanted_attachment)}",
            access_token=access_token,
        )
    except ProviderHttpError as exc:
        logger.warning(
            "[email.gmail] attachment fetch failed | account=%s message_id=%s attachment_id=%s status=%s reason=%s message=%s",
            account_email,
            wanted_message,
            wanted_attachment,
            exc.status,
            exc.reason,
            exc.message,
        )
        return _google_error_payload(exc, operation="gmail_attachments_get", account=account)
    raw_data = str(attachment.get("data") or "")
    try:
        raw = base64.urlsafe_b64decode(raw_data + ("=" * (-len(raw_data) % 4)))
    except Exception:
        raw = b""
    if len(raw) > max_size:
        return {
            "ok": False,
            "error": {
                "code": "email_attachment_too_large",
                "message": f"Attachment is {len(raw)} bytes, above the {max_size} byte MCP read limit.",
                "size_bytes": len(raw),
                "max_bytes": max_size,
                "attachment": meta,
            },
        }
    mime_type = str(meta.get("mime_type") or "").strip().lower()
    text = ""
    if mime_type.startswith("text/") or mime_type in {"application/json", "application/xml"}:
        text = raw.decode("utf-8", errors="replace")
    return {
        "ok": True,
        "message_id": wanted_message,
        "attachment": meta,
        "size_bytes": len(raw),
        "mime_type": str(meta.get("mime_type") or ""),
        "filename": str(meta.get("filename") or ""),
        "base64": base64.b64encode(raw).decode("ascii"),
        "text": text,
    }


async def ensure_email_account_access(
    *,
    store: EmailAccountStore,
    entrypoint: Any,
    account: Mapping[str, Any],
) -> Dict[str, Any]:
    provider = str(account.get("provider") or "google").strip().lower() or "google"
    if provider == "google":
        return await ensure_google_access_token(store=store, entrypoint=entrypoint, account=account)
    if provider == "icloud":
        from .icloud import ensure_icloud_credentials

        return await ensure_icloud_credentials(store=store, account=account)
    return {
        "ok": False,
        "error": {
            "code": "email_provider_not_supported",
            "message": f"Provider {provider!r} is not supported yet.",
            "category": "unsupported_provider",
            "provider": provider,
        },
        "account": account,
    }


async def fetch_email_messages(
    *,
    store: EmailAccountStore,
    entrypoint: Any,
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
) -> Dict[str, Any]:
    provider = str(account.get("provider") or "google").strip().lower() or "google"
    provider_query = str(query or gmail_query or "").strip()
    if provider == "google":
        return await fetch_google_messages(
            store=store,
            entrypoint=entrypoint,
            account=account,
            mailbox=mailbox,
            unread_only=unread_only,
            limit=limit,
            gmail_query=provider_query,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            since=since,
            before=before,
            text=text,
        )
    if provider == "icloud":
        from .icloud import fetch_icloud_messages

        return await fetch_icloud_messages(
            store=store,
            account=account,
            mailbox=mailbox or "INBOX",
            unread_only=unread_only,
            limit=limit,
            query=provider_query,
            gmail_query=provider_query,
            from_email=from_email,
            to_email=to_email,
            subject=subject,
            since=since,
            before=before,
            text=text,
        )
    return {
        "ok": False,
        "error": {
            "code": "email_provider_not_supported",
            "message": f"Provider {provider!r} is not supported yet.",
            "category": "unsupported_provider",
            "provider": provider,
        },
        "account": account,
    }


async def fetch_email_message(
    *,
    store: EmailAccountStore,
    entrypoint: Any,
    account: Mapping[str, Any],
    message_id: str,
    body_limit: int = 20000,
    mailbox: str = "",
) -> Dict[str, Any]:
    provider = str(account.get("provider") or "google").strip().lower() or "google"
    if provider == "google":
        return await fetch_google_message(
            store=store,
            entrypoint=entrypoint,
            account=account,
            message_id=message_id,
            body_limit=body_limit,
        )
    if provider == "icloud":
        from .icloud import fetch_icloud_message

        return await fetch_icloud_message(
            store=store,
            account=account,
            message_id=message_id,
            body_limit=body_limit,
            mailbox=mailbox or "INBOX",
        )
    return {
        "ok": False,
        "error": {
            "code": "email_provider_not_supported",
            "message": f"Provider {provider!r} is not supported yet.",
            "category": "unsupported_provider",
            "provider": provider,
        },
        "account": account,
    }


async def fetch_email_attachment(
    *,
    store: EmailAccountStore,
    entrypoint: Any,
    account: Mapping[str, Any],
    message_id: str,
    attachment_id: str,
    max_bytes: int = 5 * 1024 * 1024,
    mailbox: str = "",
) -> Dict[str, Any]:
    provider = str(account.get("provider") or "google").strip().lower() or "google"
    if provider == "google":
        return await fetch_google_attachment(
            store=store,
            entrypoint=entrypoint,
            account=account,
            message_id=message_id,
            attachment_id=attachment_id,
            max_bytes=max_bytes,
        )
    if provider == "icloud":
        from .icloud import fetch_icloud_attachment

        return await fetch_icloud_attachment(
            store=store,
            account=account,
            message_id=message_id,
            attachment_id=attachment_id,
            max_bytes=max_bytes,
            mailbox=mailbox or "INBOX",
        )
    return {
        "ok": False,
        "error": {
            "code": "email_provider_not_supported",
            "message": f"Provider {provider!r} is not supported yet.",
            "category": "unsupported_provider",
            "provider": provider,
        },
        "account": account,
    }


def _run_state_task_id(
    *,
    task_id: str,
    mailbox: str,
    unread_only: bool,
    gmail_query: str,
    instruction: str,
) -> str:
    explicit = str(task_id or "").strip()
    if explicit:
        return explicit
    seed = json.dumps(
        {
            "mailbox": str(mailbox or "inbox").strip(),
            "unread_only": bool(unread_only),
            "gmail_query": str(gmail_query or "").strip(),
            "instruction": str(instruction or "").strip(),
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return f"manual_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _message_internal_date_ms(message: Mapping[str, Any]) -> int:
    raw = str(message.get("internal_date") or message.get("internalDate") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except Exception:
            pass
    date_raw = str(message.get("date") or "").strip()
    if date_raw:
        try:
            parsed = parsedate_to_datetime(date_raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0, int(parsed.timestamp() * 1000))
        except Exception:
            return 0
    return 0


def _iso_from_internal_date_ms(value: int) -> str:
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _email_run_progress_cursor(
    *,
    previous_state: Mapping[str, Any],
    messages: Iterable[Mapping[str, Any]],
    processed_count: int,
    checked_at: str,
    search_query: str,
) -> Dict[str, Any]:
    previous_cursor = previous_state.get("cursor") if isinstance(previous_state.get("cursor"), Mapping) else {}
    try:
        total = int(previous_cursor.get("processed_message_count_total") or previous_state.get("processed_message_count_total") or 0)
    except Exception:
        total = 0
    total += max(0, int(processed_count or 0))

    high_ms = 0
    try:
        high_ms = int(previous_cursor.get("high_watermark_internal_date_ms") or 0)
    except Exception:
        high_ms = 0
    for message in messages or []:
        if not isinstance(message, Mapping):
            continue
        internal_ms = _message_internal_date_ms(message)
        if internal_ms > high_ms:
            high_ms = internal_ms
    return {
        "last_checked_at": checked_at,
        "last_search_query": str(search_query or "").strip(),
        "high_watermark_internal_date_ms": high_ms,
        "high_watermark_at": _iso_from_internal_date_ms(high_ms),
        "processed_message_count_total": total,
        "state_strategy": (
            "This SDK cursor is diagnostic only. Claude-owned MCP task state is the authoritative future-run memory."
        ),
    }


def _email_attachment_index(
    *,
    messages: Iterable[Mapping[str, Any]],
    account: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    account_email = str(account.get("email") or account.get("account_id") or "").strip()
    for message in messages or []:
        if not isinstance(message, Mapping):
            continue
        message_id = str(message.get("message_id") or "").strip()
        if not message_id:
            continue
        for attachment in message.get("attachments") or []:
            if not isinstance(attachment, Mapping):
                continue
            attachment_id = str(attachment.get("attachment_id") or "").strip()
            if not attachment_id:
                continue
            rows.append(
                {
                    "account": account_email,
                    "message_id": message_id,
                    "thread_id": str(message.get("thread_id") or "").strip(),
                    "subject": str(message.get("subject") or "").strip(),
                    "from": str(message.get("from") or "").strip(),
                    "to": str(message.get("to") or "").strip(),
                    "date": str(message.get("date") or "").strip(),
                    "internal_date": str(message.get("internal_date") or "").strip(),
                    "attachment_id": attachment_id,
                    "filename": str(attachment.get("filename") or "").strip(),
                    "mime_type": str(attachment.get("mime_type") or "").strip(),
                    "size_bytes": int(attachment.get("size_bytes") or 0),
                    "part_id": str(attachment.get("part_id") or "").strip(),
                    "source": "email.process_user_emails.ret.messages",
                }
            )
    return rows


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _compact_executive_journal(result: Mapping[str, Any] | None) -> list[Dict[str, Any]]:
    if not isinstance(result, Mapping) or not isinstance(result.get("executive_journal"), list):
        return []
    entries: list[Dict[str, Any]] = []
    for item in result.get("executive_journal") or []:
        if not isinstance(item, Mapping):
            continue
        entry: Dict[str, Any] = {}
        prefix = str(item.get("prefix") or "").strip()
        if prefix:
            entry["prefix"] = prefix
        channel = str(item.get("channel") or "").strip()
        if channel:
            entry["channel"] = channel
        captured_at = str(item.get("captured_at") or "").strip()
        if captured_at:
            entry["captured_at"] = captured_at
        payload = item.get("payload")
        if isinstance(payload, Mapping):
            entry["payload"] = dict(payload)
        elif isinstance(payload, list):
            entry["payload"] = list(payload)
        elif payload not in ("", None):
            entry["payload"] = payload
        text = str(item.get("text") or "").strip()
        if text:
            entry["text"] = text[:4000]
        code = str(item.get("code") or "")
        if code:
            entry["code"] = code[:12000]
        raw_line = str(item.get("raw_line") or "").strip()
        if raw_line:
            entry["raw_line"] = raw_line[:4000]
        if entry:
            entries.append(entry)
    return entries[-100:]


def _compact_claude_code_mcp_result(
    result: Mapping[str, Any] | None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    if not isinstance(result, Mapping):
        return (
            {
                "status": "not_enabled",
                "note": "Claude Code MCP processing is disabled for this deployment; React receives bounded message summaries.",
            },
            {},
        )

    recorded = result.get("recorded_result") if isinstance(result.get("recorded_result"), Mapping) else {}
    processor_result: Dict[str, Any] = {}
    if recorded:
        processor_result = {
            "source": "claude_code_mcp.record_processing_result",
            "summary": str(recorded.get("summary") or "").strip(),
            "user_notification": str(recorded.get("user_notification") or "").strip(),
            "processed_message_ids": _str_list(recorded.get("processed_message_ids")),
            "matched_message_ids": _str_list(recorded.get("matched_message_ids")),
            "details": recorded.get("details") if isinstance(recorded.get("details"), (Mapping, list)) else {},
            "recorded_at": str(recorded.get("recorded_at") or "").strip(),
        }
        processor_result = {
            key: value
            for key, value in processor_result.items()
            if value not in ("", [], {}, None)
        }

    diagnostics: Dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "status": str(result.get("status") or "").strip(),
        "run_id": str(result.get("run_id") or "").strip(),
        "candidate_message_count": _int_or_zero(result.get("candidate_message_count")),
        "candidate_message_ids": _str_list(result.get("candidate_message_ids")),
        "warnings": [dict(item) for item in result.get("warnings") or [] if isinstance(item, Mapping)],
    }
    if isinstance(result.get("last_search"), Mapping):
        diagnostics["last_search"] = dict(result.get("last_search") or {})
    if str(result.get("error_code") or "").strip():
        diagnostics["error_code"] = str(result.get("error_code") or "").strip()
    if str(result.get("effective_error_message") or result.get("error_message") or "").strip():
        diagnostics["effective_error_message"] = str(
            result.get("effective_error_message") or result.get("error_message") or ""
        ).strip()
    if str(result.get("model") or "").strip():
        diagnostics["model"] = str(result.get("model") or "").strip()
    if str(result.get("requested_model") or "").strip():
        diagnostics["requested_model"] = str(result.get("requested_model") or "").strip()
    if result.get("exit_code") is not None:
        diagnostics["exit_code"] = result.get("exit_code")
    if result.get("timed_out") is not None:
        diagnostics["timed_out"] = bool(result.get("timed_out"))
    if result.get("timeout_seconds") is not None:
        try:
            diagnostics["timeout_seconds"] = float(result.get("timeout_seconds") or 0.0)
        except Exception:
            pass
    if isinstance(result.get("failure_diagnostics"), Mapping):
        diagnostics["failure_diagnostics"] = dict(result.get("failure_diagnostics") or {})
    if result.get("duration_ms") is not None:
        try:
            diagnostics["duration_ms"] = int(result.get("duration_ms") or 0)
        except Exception:
            pass
    if result.get("api_duration_ms") is not None:
        try:
            diagnostics["api_duration_ms"] = int(result.get("api_duration_ms") or 0)
        except Exception:
            pass
    if result.get("cost_usd") is not None:
        try:
            diagnostics["cost_usd"] = float(result.get("cost_usd") or 0.0)
        except Exception:
            pass
    executive_journal = _compact_executive_journal(result)
    if executive_journal:
        diagnostics["executive_journal_count"] = len(executive_journal)

    return (
        {
            key: value
            for key, value in diagnostics.items()
            if value not in ("", [], {}, None)
        },
        processor_result,
    )


def _sync_email_mcp_task_state(
    *,
    storage_root: str | Path,
    user_id: str,
    task_id: str,
    account_id: str,
    sdk_state: Mapping[str, Any],
    note: str,
    run_id: str = "",
    execution_id: str = "",
) -> None:
    if not str(task_id or "").strip() or not str(account_id or "").strip():
        return
    try:
        from .mcp import EmailMCPRunStore

        mcp_store = EmailMCPRunStore(storage_root, user_id=user_id)
        existing = mcp_store.read_task_state(task_id=task_id, account_id=account_id)
        current = existing.get("state") if isinstance(existing.get("state"), dict) else {}
        merged = dict(current)
        merged["sdk_email_run_state"] = dict(sdk_state)
        mcp_store.write_task_state(
            task_id=task_id,
            account_id=account_id,
            state=merged,
            note=note,
            run_id=run_id,
            execution_id=execution_id,
        )
    except Exception as exc:
        logger.warning(
            "[email.process] failed to sync MCP task state | user_id=%s task_id=%s account_id=%s error=%s",
            user_id,
            task_id,
            account_id,
            exc,
        )


def _email_processor_failure_response(
    *,
    account: Mapping[str, Any],
    task_id: str,
    state_task_id: str,
    mailbox: str,
    unread_only: bool,
    provider_query: str,
    processing_mode: str,
    checked_count: int,
    error_code: str,
    error_message: str,
    run_id: str = "",
    warnings: list[Dict[str, Any]] | None = None,
    claude_result: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    compact_claude, processor_result = _compact_claude_code_mcp_result(claude_result)
    executive_journal = _compact_executive_journal(claude_result)
    normalized_code = str(error_code or "email_processor_failed").strip() or "email_processor_failed"
    normalized_message = str(error_message or "Email processor failed before recording a result.").strip()
    response = {
        "ok": False,
        "error": {
            "code": "email_processor_failed",
            "message": normalized_message,
            "category": "internal_runtime",
            "user_action_required": False,
            "retryable": True,
            "processor_error_code": normalized_code,
            "run_id": str(run_id or ""),
        },
        "account": account,
        "task_id": task_id,
        "state_task_id": state_task_id,
        "mailbox": mailbox or "inbox",
        "unread_only": bool(unread_only),
        "search_query": provider_query,
        "gmail_query": provider_query,
        "checked_count": max(0, int(checked_count or 0)),
        "seen_count": 0,
        "new_count": 0,
        "messages": [],
        "attachment_count": 0,
        "attachment_index": [],
        "processed_count": 0,
        "processing_mode": processing_mode,
        "warnings": list(warnings or []),
        "claude_code_mcp": compact_claude,
        "executive_journal": executive_journal,
    }
    if processor_result:
        response["processor_result"] = processor_result
    return response


async def process_user_emails(
    *,
    entrypoint: Any,
    storage_root: str | Path,
    user_id: str,
    bundle_id: str,
    tenant: str = "",
    project: str = "",
    conversation_id: str = "",
    session_id: str = "",
    execution_id: str = "",
    comm: Any = None,
    account: str = "",
    mailbox: str = "",
    unread_only: bool = True,
    limit: int = 20,
    gmail_query: str = "",
    search_query: str = "",
    task_id: str = "",
    task_definition: str = "",
    instruction: str = "",
) -> Dict[str, Any]:
    logger.info(
        "[email.process] start | user_id=%s tenant=%s project=%s conversation_id=%s account_param=%s "
        "mailbox=%s unread_only=%s limit=%s search_query=%r task_id=%s execution_id=%s",
        user_id,
        tenant,
        project,
        conversation_id,
        account or "",
        mailbox or "inbox",
        bool(unread_only),
        limit,
        search_query or gmail_query or "",
        task_id or "",
        execution_id or "",
    )
    store = EmailAccountStore(storage_root, user_id=user_id, bundle_id=bundle_id)
    accounts = [item for item in await store.list_accounts_async() if item.get("status") == "connected"]
    logger.info(
        "[email.process] connected accounts | user_id=%s count=%s accounts=%s",
        user_id,
        len(accounts),
        [str(item.get("email") or item.get("account_id") or "") for item in accounts],
    )
    if account:
        selected = await store.get_account_async(account)
        if selected is None:
            logger.warning(
                "[email.process] account not found | user_id=%s account_param=%s available=%s",
                user_id,
                account,
                [str(item.get("email") or item.get("account_id") or "") for item in accounts],
            )
            return {
                "ok": False,
                "error": {"code": "email_account_not_found", "message": f"Email account {account!r} was not found."},
                "accounts": accounts,
            }
    elif len(accounts) == 1:
        selected = accounts[0]
    elif not accounts:
        logger.warning("[email.process] no connected account | user_id=%s", user_id)
        return {
            "ok": False,
            "error": {
                "code": "email_account_not_connected",
                "message": (
                    f"No connected email account is available for user scope {user_id!r}. "
                    "Connect email in this same KDCube/Telegram user scope or map Telegram to the KDCube user that owns the connected email account."
                ),
            },
            "user_id": user_id,
            "task_id": task_id,
            "accounts": [],
        }
    else:
        logger.warning(
            "[email.process] account required | user_id=%s accounts=%s",
            user_id,
            [str(item.get("email") or item.get("account_id") or "") for item in accounts],
        )
        return {
            "ok": False,
            "error": {
                "code": "email_account_required",
                "message": (
                    "Multiple email accounts are connected; the request must name one. "
                    f"Available accounts: {', '.join(str(item.get('email') or item.get('account_id')) for item in accounts)}."
                ),
            },
            "user_id": user_id,
            "task_id": task_id,
            "accounts": accounts,
        }

    provider = str(selected.get("provider") or "google").strip().lower()
    provider_query = str(search_query or gmail_query or "").strip()
    task_aware = bool(str(task_id or "").strip())
    logger.info(
        "[email.process] selected account | user_id=%s account=%s provider=%s provider_query=%r",
        user_id,
        selected.get("email") or selected.get("account_id"),
        provider,
        provider_query,
    )
    account_id = str(selected.get("account_id") or "")
    state_task_id = _run_state_task_id(
        task_id=task_id,
        mailbox=mailbox,
        unread_only=unread_only,
        gmail_query=provider_query,
        instruction=instruction,
    )
    run_state = await store.read_run_state_async(task_id=state_task_id, account_id=account_id)

    claude_result: Dict[str, Any] | None = None
    processing_mode = "react_agent_review"
    warnings: list[Dict[str, Any]] = []
    fetched_messages: list[Mapping[str, Any]] = []
    new_messages: list[Mapping[str, Any]] = []
    processed_ids: set[str] = set()
    checked_count = 0

    async def _fetch_for_react_review() -> Dict[str, Any]:
        fetched = await fetch_email_messages(
            store=store,
            entrypoint=entrypoint,
            account=selected,
            mailbox=mailbox,
            unread_only=unread_only,
            limit=limit,
            query=provider_query,
            gmail_query=provider_query,
        )
        if not fetched.get("ok"):
            err = fetched.get("error") if isinstance(fetched.get("error"), dict) else {}
            logger.warning(
                "[email.process] provider fetch failed | user_id=%s account=%s code=%s message=%s",
                user_id,
                selected.get("email") or selected.get("account_id"),
                err.get("code"),
                err.get("message"),
            )
        return fetched

    try:
        claude_code_enabled_fn = None
        run_email_processor_with_claude_code_fn = None
        try:
            from .claude import claude_code_enabled, run_email_processor_with_claude_code
            claude_code_enabled_fn = claude_code_enabled
            run_email_processor_with_claude_code_fn = run_email_processor_with_claude_code
        except ImportError:
            logger.warning("[email.process] claude-code integration unavailable; using direct email fetch")

        if claude_code_enabled_fn is not None and run_email_processor_with_claude_code_fn is not None and claude_code_enabled_fn(entrypoint):
            token_check = await ensure_email_account_access(store=store, entrypoint=entrypoint, account=selected)
            if not token_check.get("ok"):
                return {**token_check, "account": selected}
            processing_mode = "claude_code_mcp"
            if run_state.get("last_checked_at") or run_state.get("cursor"):
                _sync_email_mcp_task_state(
                    storage_root=storage_root,
                    user_id=user_id,
                    task_id=task_id or state_task_id,
                    account_id=account_id,
                    sdk_state={
                        "source": "email-run-state",
                        "cursor": run_state.get("cursor") or {},
                        "state_policy": (
                            "SDK run metadata is diagnostic only and does not contain a processed-email id list. "
                            "Claude-owned MCP task state is authoritative for future-run decisions."
                        ),
                        "last_checked_at": run_state.get("last_checked_at") or "",
                        "last_search_query": run_state.get("search_query") or provider_query,
                        "last_instruction": run_state.get("last_instruction") or "",
                        "last_task_definition": run_state.get("last_task_definition") or "",
                        "last_new_count": run_state.get("last_new_count") or 0,
                        "last_processed_count": run_state.get("last_processed_count") or 0,
                        "last_processing_mode": run_state.get("last_processing_mode") or "",
                        "last_claude_code_run_id": run_state.get("last_claude_code_run_id") or "",
                    },
                    note="SDK seeded Claude email task state from email run state before processing.",
                    run_id=str(run_state.get("last_claude_code_run_id") or ""),
                    execution_id=execution_id,
                )
            logger.info(
                "[email.process] claude-code mcp start | user_id=%s account=%s task_id=%s execution_id=%s default_query=%r",
                user_id,
                selected.get("email") or selected.get("account_id"),
                task_id or "",
                execution_id or "",
                provider_query,
            )
            claude_result = await run_email_processor_with_claude_code_fn(
                entrypoint=entrypoint,
                storage_root=storage_root,
                user_id=user_id,
                bundle_id=bundle_id,
                tenant=tenant,
                project=project,
                account=selected,
                mailbox=mailbox,
                unread_only=unread_only,
                limit=limit,
                gmail_query=provider_query,
                task_id=task_id,
                task_definition=task_definition,
                instruction=instruction,
                messages=[],
                execution_id=execution_id,
                conversation_id=conversation_id,
                session_id=session_id,
                comm=comm,
            )
            checked_count = int(claude_result.get("candidate_message_count") or 0)
            if isinstance(claude_result.get("messages"), list):
                new_messages = [
                    item
                    for item in claude_result.get("messages") or []
                    if isinstance(item, Mapping)
                ]
                fetched_messages = list(new_messages)
            recorded = claude_result.get("recorded_result") if isinstance(claude_result, dict) else None
            if isinstance(recorded, dict) and isinstance(recorded.get("processed_message_ids"), list):
                recorded_ids = {
                    str(item).strip()
                    for item in recorded.get("processed_message_ids") or []
                    if str(item).strip()
                }
                if recorded_ids:
                    processed_ids = recorded_ids
                    checked_count = max(checked_count, len(processed_ids))
            if isinstance(claude_result.get("warnings"), list):
                warnings.extend(
                    item
                    for item in claude_result.get("warnings") or []
                    if isinstance(item, dict)
                )
            if not claude_result.get("ok"):
                error_code = str(
                    claude_result.get("error_code")
                    or "claude_code_email_processing_failed"
                )
                error_message = str(
                    claude_result.get("effective_error_message")
                    or claude_result.get("error_message")
                    or "Claude Code email processing failed."
                )
                logger.warning(
                    "[email.process] claude-code mcp unavailable | "
                    "user_id=%s account=%s run_id=%s status=%s code=%s error=%s",
                    user_id,
                    selected.get("email") or selected.get("account_id"),
                    claude_result.get("run_id"),
                    claude_result.get("status"),
                    error_code,
                    error_message,
                )
                warnings.append(
                    {
                        "code": error_code,
                        "message": error_message,
                        "run_id": claude_result.get("run_id"),
                        "category": "internal_runtime",
                    }
                )
                if task_aware:
                    await store.write_run_state_async(
                        task_id=state_task_id,
                        account_id=account_id,
                        data={
                            **dict(run_state or {}),
                            "task_id": task_id,
                            "account_id": account_id,
                            "mailbox": mailbox or "inbox",
                            "unread_only": bool(unread_only),
                            "search_query": provider_query,
                            "last_instruction": instruction,
                            "last_task_definition": task_definition,
                            "last_failed_at": _utc_now(),
                            "last_error": {
                                "code": error_code,
                                "message": error_message,
                                "run_id": claude_result.get("run_id"),
                            },
                            "last_processing_mode": processing_mode,
                        },
                    )
                    logger.warning(
                        "[email.process] task-aware processor failed closed | "
                        "user_id=%s account=%s task_id=%s run_id=%s code=%s",
                        user_id,
                        selected.get("email") or selected.get("account_id"),
                        task_id,
                        claude_result.get("run_id"),
                        error_code,
                    )
                    return _email_processor_failure_response(
                        account=selected,
                        task_id=task_id,
                        state_task_id=state_task_id,
                        mailbox=mailbox,
                        unread_only=unread_only,
                        provider_query=provider_query,
                        processing_mode=processing_mode,
                        checked_count=checked_count,
                        error_code=error_code,
                        error_message=error_message,
                        run_id=str(claude_result.get("run_id") or ""),
                        warnings=warnings,
                        claude_result=claude_result,
                    )
                await store.write_run_state_async(
                    task_id=state_task_id,
                    account_id=account_id,
                    data={
                        "task_id": task_id,
                        "account_id": account_id,
                        "mailbox": mailbox or "inbox",
                        "unread_only": bool(unread_only),
                        "search_query": provider_query,
                        "cursor": run_state.get("cursor") or {},
                        "last_instruction": instruction,
                        "last_task_definition": task_definition,
                        "last_checked_at": _utc_now(),
                        "last_new_count": len(new_messages),
                        "last_error": {
                            "code": error_code,
                            "message": error_message,
                            "run_id": claude_result.get("run_id"),
                        },
                    },
                )
                fallback = await _fetch_for_react_review()
                if fallback.get("ok"):
                    fetched_messages = list(fallback.get("messages") or [])
                    new_messages = fetched_messages
                    checked_count = len(fetched_messages)
                    if not processed_ids:
                        processed_ids = {
                            str(item.get("message_id") or "")
                            for item in new_messages
                            if isinstance(item, Mapping) and str(item.get("message_id") or "")
                        }
                else:
                    compact_claude, processor_result = _compact_claude_code_mcp_result(claude_result)
                    failed = {**fallback, "account": selected, "warnings": warnings, "claude_code_mcp": compact_claude}
                    executive_journal = _compact_executive_journal(claude_result)
                    if executive_journal:
                        failed["executive_journal"] = executive_journal
                    if processor_result:
                        failed["processor_result"] = processor_result
                    return failed
                processing_mode = "react_agent_review"
            logger.info(
                "[email.process] claude-code mcp finished | user_id=%s account=%s run_id=%s ok=%s processed=%s fallback_warnings=%s",
                user_id,
                selected.get("email") or selected.get("account_id"),
                claude_result.get("run_id"),
                bool(claude_result.get("ok")),
                len(processed_ids),
                len(warnings),
            )
        else:
            fetched = await _fetch_for_react_review()
            if not fetched.get("ok"):
                return {**fetched, "account": selected}
            fetched_messages = list(fetched.get("messages") or [])
            new_messages = fetched_messages
            checked_count = len(fetched_messages)
            processed_ids = {
                str(item.get("message_id") or "")
                for item in new_messages
                if isinstance(item, Mapping) and str(item.get("message_id") or "")
            }
    except Exception as exc:
        if processing_mode == "claude_code_mcp":
            error_message = str(exc)
            logger.exception(
                "[email.process] claude-code mcp exception | "
                "user_id=%s account=%s task_id=%s execution_id=%s",
                user_id,
                selected.get("email") or selected.get("account_id"),
                task_id or "",
                execution_id or "",
            )
            warnings.append(
                {
                    "code": "claude_code_email_processing_failed",
                    "message": error_message,
                    "category": "internal_runtime",
                }
            )
            if task_aware:
                await store.write_run_state_async(
                    task_id=state_task_id,
                    account_id=account_id,
                    data={
                        **dict(run_state or {}),
                        "task_id": task_id,
                        "account_id": account_id,
                        "mailbox": mailbox or "inbox",
                        "unread_only": bool(unread_only),
                        "search_query": provider_query,
                        "last_instruction": instruction,
                        "last_task_definition": task_definition,
                        "last_failed_at": _utc_now(),
                        "last_error": {
                            "code": "claude_code_email_processing_failed",
                            "message": error_message,
                        },
                        "last_processing_mode": processing_mode,
                    },
                )
                logger.warning(
                    "[email.process] task-aware processor exception failed closed | "
                    "user_id=%s account=%s task_id=%s error=%s",
                    user_id,
                    selected.get("email") or selected.get("account_id"),
                    task_id,
                    error_message,
                )
                return _email_processor_failure_response(
                    account=selected,
                    task_id=task_id,
                    state_task_id=state_task_id,
                    mailbox=mailbox,
                    unread_only=unread_only,
                    provider_query=provider_query,
                    processing_mode=processing_mode,
                    checked_count=checked_count,
                    error_code="claude_code_email_processing_failed",
                    error_message=error_message,
                    warnings=warnings,
                    claude_result=claude_result,
                )
            await store.write_run_state_async(
                task_id=state_task_id,
                account_id=account_id,
                data={
                    "task_id": task_id,
                    "account_id": account_id,
                    "mailbox": mailbox or "inbox",
                    "unread_only": bool(unread_only),
                    "search_query": provider_query,
                    "cursor": run_state.get("cursor") or {},
                    "last_instruction": instruction,
                    "last_task_definition": task_definition,
                    "last_checked_at": _utc_now(),
                    "last_new_count": len(new_messages),
                    "last_error": {"code": "claude_code_email_processing_failed", "message": error_message},
                },
            )
            fallback = await _fetch_for_react_review()
            if fallback.get("ok"):
                fetched_messages = list(fallback.get("messages") or [])
                new_messages = fetched_messages
                checked_count = len(fetched_messages)
                if not processed_ids:
                    processed_ids = {
                        str(item.get("message_id") or "")
                        for item in new_messages
                        if isinstance(item, Mapping) and str(item.get("message_id") or "")
                    }
            else:
                compact_claude, processor_result = _compact_claude_code_mcp_result(claude_result)
                failed = {**fallback, "account": selected, "warnings": warnings, "claude_code_mcp": compact_claude}
                executive_journal = _compact_executive_journal(claude_result)
                if executive_journal:
                    failed["executive_journal"] = executive_journal
                if processor_result:
                    failed["processor_result"] = processor_result
                return failed
            claude_result = claude_result or {
                "ok": False,
                "status": "failed",
                "error_code": "claude_code_email_processing_failed",
                "error_message": error_message,
                "effective_error_message": error_message,
            }
        processing_mode = "react_agent_review"

    checked_count = max(checked_count, len(fetched_messages), len(new_messages), len(processed_ids))
    logger.info(
        "[email.process] candidate state | user_id=%s account=%s state_task_id=%s checked=%s previously_processed=%s selected=%s",
        user_id,
        selected.get("email") or selected.get("account_id"),
        state_task_id,
        checked_count,
        0,
        len(new_messages),
    )
    checked_at = _utc_now()
    cursor = _email_run_progress_cursor(
        previous_state=run_state,
        messages=new_messages or fetched_messages,
        processed_count=len(processed_ids),
        checked_at=checked_at,
        search_query=provider_query,
    )
    last_claude_code_run_id = (claude_result or {}).get("run_id") if isinstance(claude_result, dict) else ""
    await store.write_run_state_async(
        task_id=state_task_id,
        account_id=account_id,
        data={
            "task_id": task_id,
            "account_id": account_id,
            "mailbox": mailbox or "inbox",
            "unread_only": bool(unread_only),
            "search_query": provider_query,
            "cursor": cursor,
            "state_policy": (
                "SDK run metadata is diagnostic only and does not contain a processed-email id list. "
                "Claude-owned MCP task state is authoritative for future-run decisions."
            ),
            "last_instruction": instruction,
            "last_task_definition": task_definition,
            "last_checked_at": checked_at,
            "last_new_count": checked_count,
            "last_processed_count": len(processed_ids),
            "last_processing_mode": processing_mode,
            "last_claude_code_run_id": last_claude_code_run_id,
        },
    )
    _sync_email_mcp_task_state(
        storage_root=storage_root,
        user_id=user_id,
        task_id=task_id or state_task_id,
        account_id=account_id,
        sdk_state={
            "source": "email.process_user_emails",
            "cursor": cursor,
            "state_policy": (
                "SDK run metadata is diagnostic only and does not contain a processed-email id list. "
                "Claude-owned MCP task state is authoritative for future-run decisions."
            ),
            "last_checked_at": checked_at,
            "last_search_query": provider_query,
            "last_instruction": instruction,
            "last_task_definition": task_definition,
            "last_new_count": checked_count,
            "last_processed_count": len(processed_ids),
            "last_processing_mode": processing_mode,
            "last_claude_code_run_id": last_claude_code_run_id,
        },
        note="SDK synchronized email run state after process_user_emails.",
        run_id=str(last_claude_code_run_id or ""),
        execution_id=execution_id,
    )
    logger.info(
        "[email.process] done | user_id=%s account=%s mode=%s checked=%s new=%s processed=%s",
        user_id,
        selected.get("email") or selected.get("account_id"),
        processing_mode,
        checked_count,
        checked_count,
        len(processed_ids),
    )
    attachment_index = _email_attachment_index(messages=new_messages, account=selected)
    compact_claude, processor_result = _compact_claude_code_mcp_result(claude_result)
    executive_journal = _compact_executive_journal(claude_result)
    if attachment_index:
        logger.info(
            "[email.process] attachment index | user_id=%s account=%s count=%s items=%s",
            user_id,
            selected.get("email") or selected.get("account_id"),
            len(attachment_index),
            [
                {
                    "message_id": item.get("message_id"),
                    "filename": item.get("filename"),
                    "mime_type": item.get("mime_type"),
                    "attachment_id_prefix": str(item.get("attachment_id") or "")[:40],
                }
                for item in attachment_index[:10]
            ],
        )
    response = {
        "ok": True,
        "account": selected,
        "task_id": task_id,
        "state_task_id": state_task_id,
        "task_definition": task_definition,
        "instruction": instruction,
        "mailbox": mailbox or "inbox",
        "unread_only": bool(unread_only),
        "search_query": provider_query,
        "gmail_query": provider_query,
        "checked_count": checked_count,
        "seen_count": 0,
        "new_count": checked_count,
        "messages": new_messages,
        "attachment_count": len(attachment_index),
        "attachment_index": attachment_index,
        "processed_count": len(processed_ids),
        "processing_mode": processing_mode,
        "warnings": warnings,
        "claude_code_mcp": compact_claude,
        "executive_journal": executive_journal,
    }
    if processor_result:
        response["processor_result"] = processor_result
    return response
