from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from contextlib import nullcontext
from typing import Any, Awaitable, Callable, Dict, Optional

from kdcube_ai_app.apps.chat.sdk.config import get_secret
from kdcube_ai_app.apps.chat.sdk.infra.bundle_urls import bundle_operation_url
from kdcube_ai_app.infra.jobs.stream import RedisBackgroundJobStream
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import BundleBinaryResponse
from .async_storage import AsyncAutomationStorage
from .execution_artifacts import (
    artifact_ref_for_execution_artifact,
    downloadable_execution_artifacts,
    execution_id_from_artifact_ref,
    read_execution_artifact_for_download,
)
from kdcube_ai_app.apps.chat.sdk.integrations.telegram import (
    TelegramMessage,
    TelegramUserAdminStorage,
    render_telegram_messages_from_timeline,
    send_telegram_messages,
)
from kdcube_ai_app.apps.chat.sdk.identity_authority import (
    apply_authority_to_comm_context,
    authority_from_source,
    normalize_execution_authority,
)


logger = logging.getLogger(__name__)

BUNDLE_ID = ""
WORK_KIND_AUTOMATION_RUN_NOW = "automation.execution.manual"

_storage_root_or_error = None
_target_user_id = None


def configure_automation_operations(
    *,
    storage_root_or_error: Any,
    target_user_id: Any,
    bundle_id: str = "",
) -> None:
    """Bind bundle-specific storage and user scope resolution."""
    global BUNDLE_ID, _storage_root_or_error, _target_user_id
    BUNDLE_ID = str(bundle_id or "").strip()
    _storage_root_or_error = storage_root_or_error
    _target_user_id = target_user_id


def _storage_root(entrypoint: Any) -> str:
    for method_name in ("automation_storage_root", "storage_root_or_error"):
        resolver = getattr(entrypoint, method_name, None)
        if callable(resolver):
            return str(resolver())
    if not callable(_storage_root_or_error):
        raise RuntimeError("automation operations are not configured: storage_root_or_error is missing")
    return str(_storage_root_or_error(entrypoint))


def _target_user(entrypoint: Any, *, user_id: Optional[str] = None, fingerprint: Optional[str] = None) -> str:
    resolver = getattr(entrypoint, "target_automation_user_id", None)
    if callable(resolver):
        return str(resolver(user_id=user_id, fingerprint=fingerprint))
    if callable(_target_user_id):
        return str(_target_user_id(entrypoint, user_id=user_id, fingerprint=fingerprint))
    value = str(user_id or fingerprint or "").strip()
    if value:
        return value
    comm_context = getattr(entrypoint, "comm_context", None)
    comm_user = getattr(comm_context, "user", None)
    return str(getattr(comm_user, "user_id", "") or "anonymous")


def storage_for(
    entrypoint: Any,
    *,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> tuple[AsyncAutomationStorage, str]:
    target_user = _target_user(entrypoint, user_id=user_id, fingerprint=fingerprint)
    return AsyncAutomationStorage(_storage_root(entrypoint), user_id=target_user), target_user


def _bundle_route_parts(entrypoint: Any) -> tuple[str, str, str]:
    settings = getattr(entrypoint, "settings", None)
    tenant = str(getattr(getattr(entrypoint, "config", None), "tenant", "") or getattr(settings, "TENANT", "") or "").strip()
    project = str(getattr(getattr(entrypoint, "config", None), "project", "") or getattr(settings, "PROJECT", "") or "").strip()
    bundle_id = str(
        getattr(getattr(getattr(entrypoint, "config", None), "ai_bundle_spec", None), "id", "")
        or BUNDLE_ID
    ).strip()
    return tenant, project, bundle_id


def _execution_scope(entrypoint: Any, *, user_id: str) -> Dict[str, Any]:
    tenant, project, _bundle_id = _bundle_route_parts(entrypoint)
    return {
        "tenant": tenant,
        "project": project,
        "user_id": user_id,
        # NOTE: user_type here scopes ARTIFACT storage paths (execution_artifacts),
        # not economics. It is intentionally left constant so artifact read/write
        # paths stay consistent across executions regardless of the funding role.
        # Economics uses an independently resolved role via _automation_econ_subject.
        "user_type": "registered",
        "storage_root": _storage_root(entrypoint),
    }


# ---------------------------------------------------------------------------
# Economics enforcement (Option A: estimate + post-run settle, funding_flow parity)
# ---------------------------------------------------------------------------
def _automation_economics_enabled(entrypoint: Any) -> bool:
    return bool(
        getattr(entrypoint, "cp_manager", None)
        and getattr(entrypoint, "rl", None)
        and getattr(entrypoint, "budget_limiter", None)
    )


def _automation_reservation_usd(entrypoint: Any) -> float:
    """Feasibility estimate for a automation pipeline (bundle-configurable).

    Automations verify economic feasibility at the start (economic_preflight); this
    only sizes the estimate the preflight admits against. The automation's actual cost
    is metered by the inner ReAct turn (self.run()), not reserved/settled here.
    """
    try:
        return float(_bundle_prop(entrypoint, "economics.automation.reservation_amount_dollars", 0.50) or 0.50)
    except Exception:
        return 0.50


def _source_text(source: Dict[str, Any] | None, *keys: str) -> str:
    for key in keys:
        value = (source or {}).get(key)
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _automation_economics_user_id(*, target_user: str, source: Dict[str, Any] | None) -> str:
    return _source_text(source, "economics_user_id", "platform_user_id", "authority_user_id") or target_user


async def _automation_econ_subject(entrypoint: Any, *, target_user: str, source: Dict[str, Any]):
    """Build an EconomicsSubject for a automation execution.

    The automation actor/storage user can be a surface-local identity such as
    ``telegram_...``. Economics is allowed to use a separately carried platform
    authority identity when the surface identity was linked to a platform user.
    privileged/admin is preserved only from that platform authority role; paid
    and registered are re-resolved at run time from economics state via
    RoleResolver.
    """
    from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
        EconomicsSubject,
        RoleResolver,
    )

    tenant, project, _bundle_id = _bundle_route_parts(entrypoint)
    comm_context = getattr(entrypoint, "comm_context", None)
    comm_user = getattr(comm_context, "user", None)
    economics_user_id = _automation_economics_user_id(target_user=target_user, source=source)
    carried_role = str(
        (source or {}).get("economics_user_type")
        or (source or {}).get("platform_user_type")
        or (source or {}).get("user_type")
        or getattr(comm_user, "user_type", "")
        or "registered"
    ).strip() or "registered"
    role = carried_role
    try:
        pg_pool = getattr(entrypoint, "pg_pool", None)
        if pg_pool is not None and tenant and project and economics_user_id:
            resolver = RoleResolver(pg_pool=pg_pool, tenant=tenant, project=project)
            role = await resolver.resolve(user_id=economics_user_id, carried_role=carried_role)
    except Exception as exc:
        logger.warning(
            "[automations.economics] role resolve failed; using carried role: actor=%s economics_user=%s carried=%s err=%s",
            target_user, economics_user_id, carried_role, exc,
        )
        role = carried_role
    timezone = str((source or {}).get("timezone") or getattr(comm_user, "timezone", "") or "") or None
    return EconomicsSubject(
        tenant=tenant, project=project, user_id=economics_user_id,
        user_type=role, timezone=timezone,
    )


async def _automation_verify_economics(
    entrypoint: Any,
    *,
    target_user: str,
    source: Dict[str, Any],
):
    """Verify the automation pipeline is economically feasible for the carried user.

    Returns (subject, decision) on success; raises EconomicsLimitException when
    the user's quota/funding cannot support the pipeline. Returns (None, None)
    when economics is not configured (automation runs unmetered, as before).

    Verify-only (economic_preflight): NO reservation, NO settlement. The automation's
    ReAct work routes through self.run(), which already reserves+settles the real
    cost under the automation's user identity; an outer guard would only duplicate it.
    This gates the START of the flow and surfaces the economic limits for logging.
    """
    if not _automation_economics_enabled(entrypoint):
        return None, None
    from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
        economic_preflight,
        EconomicsEstimate,
        FlowPolicy,
    )

    subject = await _automation_econ_subject(entrypoint, target_user=target_user, source=source)
    if not (subject.tenant and subject.project and subject.user_id):
        return None, None
    decision = await economic_preflight(
        entrypoint,
        subject=subject,
        estimate=EconomicsEstimate(reservation_usd=_automation_reservation_usd(entrypoint)),
        flow="automations",
        policy=FlowPolicy(enforce_concurrency=False, emit_user_events=False),
    )
    return subject, decision


def _automation_economics_metadata(decision) -> Dict[str, Any]:
    """Compact economic decision + limits for the execution journal/metadata."""
    if decision is None:
        return {}
    admit = getattr(decision, "admit", None)
    limits = (getattr(admit, "snapshot", None) or {}) if admit is not None else {}
    return {
        "economics": {
            "verified": True,
            "lane": getattr(decision, "lane", None),
            "plan_id": getattr(decision, "plan_id", None),
            "funding_source": getattr(decision, "funding_source", None),
            "est_turn_usd": getattr(decision, "est_turn_usd", None),
            "limits": limits,
        }
    }


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


async def _download_token_secret(entrypoint: Any) -> bytes:
    _tenant, _project, bundle_id = _bundle_route_parts(entrypoint)
    secret = str(
        _bundle_prop(entrypoint, "integrations.telegram.artifact_download_secret", "")
        or await get_secret("b:integrations.telegram.artifact_download_secret")
        or await get_secret(f"bundles.{bundle_id}.secrets.integrations.telegram.artifact_download_secret")
        or await _telegram_bot_token()
        or ""
    ).strip()
    return secret.encode("utf-8")


def _download_token_ttl(entrypoint: Any) -> int:
    try:
        ttl = int(_bundle_prop(entrypoint, "integrations.telegram.artifact_download_token_ttl_seconds", 900) or 900)
    except Exception:
        ttl = 900
    return max(60, min(ttl, 86400))


async def _make_download_token(
    entrypoint: Any,
    *,
    artifact_ref: str,
    user_id: str,
) -> tuple[str, int] | None:
    secret = await _download_token_secret(entrypoint)
    if not secret:
        return None
    expires_at = int(time.time()) + _download_token_ttl(entrypoint)
    payload = {
        "v": 1,
        "artifact_ref": str(artifact_ref or "").strip(),
        "user_id": str(user_id or "").strip(),
        "exp": expires_at,
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url_encode(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}", expires_at


async def _verify_download_token(entrypoint: Any, *, artifact_ref: str, download_token: str) -> Dict[str, Any]:
    secret = await _download_token_secret(entrypoint)
    if not secret:
        raise ValueError("artifact download token signing secret is not configured")
    try:
        body, sig = str(download_token or "").strip().split(".", 1)
    except ValueError:
        raise ValueError("artifact download token is malformed")
    expected = _b64url_encode(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise ValueError("artifact download token signature is invalid")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:
        raise ValueError("artifact download token payload is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("artifact download token payload is invalid")
    if str(payload.get("artifact_ref") or "") != str(artifact_ref or ""):
        raise ValueError("artifact download token does not match artifact_ref")
    try:
        expires_at = int(payload.get("exp") or 0)
    except Exception:
        expires_at = 0
    if expires_at < int(time.time()):
        raise ValueError("artifact download token is expired")
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("artifact download token does not include a user scope")
    return payload


def _artifact_download_url(entrypoint: Any, *, artifact_ref: str, public: bool, download_token: str = "") -> str:
    tenant, project, bundle_id = _bundle_route_parts(entrypoint)
    route = "public" if public else "operations"
    alias = "telegram_automation_execution_artifact_download" if public else "automation_execution_artifact_download"
    query_payload = {"artifact_ref": artifact_ref}
    if download_token:
        query_payload["download_token"] = download_token
    return bundle_operation_url(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
        route=route,
        operation=alias,
        query=query_payload,
        strict=True,
    )


async def _decorate_execution_artifacts(
    entrypoint: Any,
    execution: Dict[str, Any] | None,
    *,
    user_id: str,
    public: bool = False,
) -> Dict[str, Any] | None:
    if not execution:
        return execution
    enriched = dict(execution)
    sc = _execution_scope(entrypoint, user_id=user_id)
    try:
        artifacts = await downloadable_execution_artifacts(enriched, sc=sc)
    except Exception:
        artifacts = []
    out: list[Dict[str, Any]] = []
    for index, artifact in enumerate(artifacts):
        item = dict(artifact)
        artifact_ref = str(item.get("artifact_ref") or "").strip() or artifact_ref_for_execution_artifact(enriched, item, index=index)
        signed = await _make_download_token(entrypoint, artifact_ref=artifact_ref, user_id=user_id) if public else None
        download_token = signed[0] if signed else ""
        download_url = _artifact_download_url(entrypoint, artifact_ref=artifact_ref, public=public, download_token=download_token)
        item["artifact_ref"] = artifact_ref
        item["download_url"] = download_url
        auth_kind = "telegram_download_token" if public and download_token else ("telegram_init_data_header" if public else "kdcube_platform_auth")
        item["download"] = {
            "method": "GET",
            "url": download_url,
            "filename": item.get("filename") or "",
            "mime_type": item.get("mime_type") or item.get("mime") or "",
            "auth": auth_kind,
            "headers": [] if auth_kind == "telegram_download_token" else (["X-Telegram-Init-Data"] if public else []),
        }
        if signed:
            item["download"]["expires_at"] = signed[1]
        out.append(item)
    enriched["artifacts"] = out
    enriched["artifact_count"] = len(out)
    return enriched


async def _decorate_executions(
    entrypoint: Any,
    executions: list[Dict[str, Any]],
    *,
    user_id: str,
    public: bool = False,
) -> list[Dict[str, Any]]:
    out = []
    for execution in executions:
        decorated = await _decorate_execution_artifacts(entrypoint, execution, user_id=user_id, public=public)
        if decorated:
            out.append(decorated)
    return out


async def list_automations(
    entrypoint: Any,
    *,
    query: str = "",
    status: str = "",
    limit: int = 50,
    execution_limit: int = 3,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    automations = await storage.list_automations(query=query, status=status, limit=limit)
    automations = await storage.attach_execution_history(automations, execution_limit=execution_limit)
    for automation in automations:
        if isinstance(automation.get("executions"), list):
            automation["executions"] = await _decorate_executions(entrypoint, automation["executions"], user_id=target_user, public=public)
        if isinstance(automation.get("last_execution"), dict):
            automation["last_execution"] = await _decorate_execution_artifacts(entrypoint, automation["last_execution"], user_id=target_user, public=public)
    return {"ok": True, "user_id": target_user, "count": len(automations), "automations": automations}


async def search_automations(
    entrypoint: Any,
    *,
    query: str = "",
    status: str = "",
    limit: int = 20,
    execution_limit: int = 3,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    automations = await storage.search_automations(query=query, status=status, limit=limit)
    automations = await storage.attach_execution_history(automations, execution_limit=execution_limit)
    for automation in automations:
        if isinstance(automation.get("executions"), list):
            automation["executions"] = await _decorate_executions(entrypoint, automation["executions"], user_id=target_user, public=public)
        if isinstance(automation.get("last_execution"), dict):
            automation["last_execution"] = await _decorate_execution_artifacts(entrypoint, automation["last_execution"], user_id=target_user, public=public)
    index_path = await storage.ensure_search_index()
    return {
        "ok": True,
        "user_id": target_user,
        "count": len(automations),
        "index_path": str(index_path),
        "automations": automations,
    }


async def get_automation(
    entrypoint: Any,
    *,
    automation_id: str,
    execution_limit: int = 10,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    automation = await storage.get_automation(automation_id)
    if automation:
        automation = (await storage.attach_execution_history([automation], execution_limit=execution_limit))[0]
        if isinstance(automation.get("executions"), list):
            automation["executions"] = await _decorate_executions(entrypoint, automation["executions"], user_id=target_user, public=public)
        if isinstance(automation.get("last_execution"), dict):
            automation["last_execution"] = await _decorate_execution_artifacts(entrypoint, automation["last_execution"], user_id=target_user, public=public)
    return {
        "ok": automation is not None,
        "user_id": target_user,
        "automation": automation,
        "error": None if automation else {"code": "automation_not_found", "message": f"Automation {automation_id!r} was not found."},
    }


async def create_automation(
    entrypoint: Any,
    *,
    title: str,
    description: str = "",
    schedule_cron: str = "",
    timezone: str = "UTC",
    recurring: bool = True,
    labels: str = "",
    source: str = "widget",
    conversation_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    automation = await storage.create_automation(
        title=title,
        description=description,
        schedule_cron=schedule_cron,
        timezone_name=timezone,
        recurring=recurring,
        labels=labels,
        source=source,
        conversation_id=conversation_id or None,
    )
    return {"ok": True, "user_id": target_user, "automation": automation}


async def update_automation(
    entrypoint: Any,
    *,
    automation_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    schedule_cron: str | None = None,
    timezone: str | None = None,
    recurring: bool | None = None,
    labels: str | None = None,
    conversation_id: str | None = None,
    execution_conversation_id: str | None = None,
    metadata_patch: Dict[str, Any] | None = None,
    context_patch: Dict[str, Any] | None = None,
    relations_patch: Dict[str, Any] | None = None,
    revision_mode: str = "auto",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    automation = await storage.update_automation(
        automation_id=automation_id,
        title=title,
        description=description,
        status=status,
        schedule_cron=schedule_cron,
        timezone_name=timezone,
        recurring=recurring,
        labels=labels,
        conversation_id=conversation_id,
        execution_conversation_id=execution_conversation_id,
        metadata_patch=metadata_patch,
        context_patch=context_patch,
        relations_patch=relations_patch,
        revision_mode=revision_mode,
    )
    return {"ok": True, "user_id": target_user, "automation": automation}


async def delete_automation(
    entrypoint: Any,
    *,
    automation_id: str,
    hard: bool = False,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    automation = await storage.delete_automation(automation_id=automation_id, hard=hard)
    return {"ok": automation is not None, "user_id": target_user, "deleted": automation is not None, "automation": automation}


async def list_executions(
    entrypoint: Any,
    *,
    automation_id: str = "",
    status: str = "",
    limit: int = 50,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    executions = await storage.list_executions(automation_id=automation_id, status=status, limit=limit)
    executions = await _decorate_executions(entrypoint, executions, user_id=target_user, public=public)
    return {"ok": True, "user_id": target_user, "count": len(executions), "executions": executions}


async def search_executions(
    entrypoint: Any,
    *,
    query: str = "",
    automation_id: str = "",
    status: str = "",
    limit: int = 50,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    executions = await storage.search_executions(query=query, automation_id=automation_id, status=status, limit=limit)
    executions = await _decorate_executions(entrypoint, executions, user_id=target_user, public=public)
    index_path = await storage.ensure_execution_search_index()
    return {
        "ok": True,
        "user_id": target_user,
        "count": len(executions),
        "index_path": str(index_path),
        "executions": executions,
    }


async def get_execution(
    entrypoint: Any,
    *,
    execution_id: str,
    automation_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    public: bool = False,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    execution = await storage.get_execution(automation_id=automation_id, execution_id=execution_id)
    execution = await _decorate_execution_artifacts(entrypoint, execution, user_id=target_user, public=public)
    return {
        "ok": execution is not None,
        "user_id": target_user,
        "execution": execution,
        "error": None if execution else {"code": "execution_not_found", "message": f"Execution {execution_id!r} was not found."},
    }


async def download_execution_artifact(
    entrypoint: Any,
    *,
    artifact_ref: str,
    execution_id: str = "",
    automation_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
    download_token: str = "",
) -> BundleBinaryResponse | Dict[str, Any]:
    if download_token:
        try:
            token_payload = await _verify_download_token(entrypoint, artifact_ref=artifact_ref, download_token=download_token)
        except Exception as exc:
            return {
                "ok": False,
                "error": {"code": "execution_artifact_download_token_invalid", "message": str(exc)},
            }
        storage, target_user = storage_for(entrypoint, user_id=str(token_payload.get("user_id") or ""), fingerprint=None)
    else:
        storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    selected_execution_id = str(execution_id or "").strip()
    if not selected_execution_id:
        selected_execution_id = execution_id_from_artifact_ref(artifact_ref)
    execution = await storage.get_execution(automation_id=automation_id, execution_id=selected_execution_id)
    if not execution:
        return {
            "ok": False,
            "error": {"code": "execution_not_found", "message": f"Execution {selected_execution_id!r} was not found."},
        }
    try:
        payload = await read_execution_artifact_for_download(
            artifact_ref=artifact_ref,
            execution=execution,
            sc=_execution_scope(entrypoint, user_id=target_user),
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": {"code": "execution_artifact_not_found", "message": str(exc)},
        }
    return BundleBinaryResponse(
        content=payload["content"],
        filename=payload.get("filename") or "artifact.bin",
        media_type=payload.get("mime_type") or "application/octet-stream",
    )


async def upsert_execution(
    entrypoint: Any,
    *,
    automation_id: str,
    execution_id: str = "",
    status: str = "",
    trigger: str = "agent",
    conversation_id: str = "",
    turn_id: str = "",
    summary: str = "",
    result: Dict[str, Any] | None = None,
    error: str = "",
    log_excerpt: str = "",
    artifacts: list[Dict[str, Any]] | None = None,
    metadata: Dict[str, Any] | None = None,
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    if execution_id:
        execution = await storage.update_execution(
            execution_id=execution_id,
            automation_id=automation_id,
            status=status or None,
            conversation_id=conversation_id,
            turn_id=turn_id,
            summary=summary,
            result=result,
            error=error,
            log_excerpt=log_excerpt,
            artifacts=artifacts,
            metadata_patch=metadata,
        )
    else:
        execution = await storage.create_execution(
            automation_id=automation_id,
            status=status or "queued",
            trigger=trigger,
            conversation_id=conversation_id,
            turn_id=turn_id,
            summary=summary,
            result=result,
            error=error,
            log_excerpt=log_excerpt,
            artifacts=artifacts,
            metadata=metadata,
        )
    return {"ok": True, "user_id": target_user, "execution": execution}


async def delete_execution(
    entrypoint: Any,
    *,
    execution_id: str,
    automation_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    execution = await storage.delete_execution(execution_id=execution_id, automation_id=automation_id)
    return {"ok": execution is not None, "user_id": target_user, "deleted": execution is not None, "execution": execution}


def _run_prompt(*, automation: Dict[str, Any], execution: Dict[str, Any], trigger: str) -> str:
    return "\n".join(
        [
            "Run this saved automation as a fresh job execution.",
            "",
            f"Automation id: {automation.get('id')}",
            f"Execution id: {execution.get('id')}",
            f"Execution trigger: {trigger}",
            f"Title: {automation.get('title')}",
            "",
            "Instructions:",
            str(automation.get("body") or automation.get("description") or "").strip(),
            "",
            "Use automation_job.get_current_automation if you need to inspect the automation or linked automation definitions.",
            "Use job_memory.search_memo only when durable user context materially changes this execution.",
            "Use email.process_user_emails for email-processing automations; pass the concrete connected email address as account, the specific mailbox rule as instruction, and a bounded search_query when the automation has a date/topic/sender/label constraint.",
            "If email.process_user_emails returns email_processor_failed in a saved automation, retry the same email tool call if rounds remain; otherwise record a automation failure. Do not treat it as zero new emails or process web/search fallback.",
            "Use delivery.send_report when the automation explicitly asks to deliver the generated report by email, Telegram, or both; pass generated artifact physical paths as attachments.",
            "Do not invent or pass automation id, execution id, or automation definition to email tools; those are injected from the bundle call context.",
            "Never ask for email passwords or raw credentials.",
            "Use automation_job.update_execution_journal for substantial progress, errors, result data, and produced artifacts.",
            "Call automation_job tools directly as ReAct tool calls, not from inside exec_tools.execute_code_python code.",
            "At the end, call automation_job.update_execution_journal directly with the final status and then summarize the outcome briefly.",
        ]
    ).strip()


def _result_answer(result: Any) -> str:
    if isinstance(result, dict):
        return str(
            result.get("final_answer")
            or result.get("answer")
            or result.get("summary")
            or ""
        ).strip()
    return str(result or "").strip()


def _result_status(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    status = str(result.get("execution_status") or result.get("status") or "").strip().lower()
    return status if status in {"success", "failed", "cancelled"} else ""


def _build_automation_scoped_context(
    entrypoint: Any,
    *,
    target_user: str,
    run_conversation_id: str,
    turn_id: str,
    bundle_call_context: Dict[str, Any],
    resolved_user_type: Optional[str] = None,
):
    comm_context = getattr(entrypoint, "comm_context", None)
    if comm_context is None or not hasattr(comm_context, "model_copy"):
        raise RuntimeError("automation execution requires a request context with comm_context")

    scoped_ctx = comm_context.model_copy(deep=True)
    scoped_ctx.routing.session_id = run_conversation_id
    scoped_ctx.routing.conversation_id = run_conversation_id
    scoped_ctx.routing.turn_id = turn_id
    scoped_ctx.user.user_id = target_user
    authority_source = (
        bundle_call_context.get("identity_authority")
        if isinstance(bundle_call_context.get("identity_authority"), dict)
        else bundle_call_context.get("source")
    )
    apply_authority_to_comm_context(scoped_ctx, source=authority_source)
    # Carry the economics-resolved role so the inner run() applies the correct
    # plan/limits + funding for this user (the worker comm_context may carry a
    # stale/default role for scheduled automations).
    if resolved_user_type:
        scoped_ctx.user.user_type = resolved_user_type
    scoped_ctx.bundle_call_context = bundle_call_context
    return scoped_ctx


async def _run_with_entrypoint_request_context(
    entrypoint: Any,
    scoped_ctx: Any,
    runner: Callable[[], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    binder = getattr(entrypoint, "bind_request_context", None)
    if callable(binder):
        with binder(comm_context=scoped_ctx):
            with bind_current_request_context(scoped_ctx, comm=getattr(entrypoint, "comm", None)):
                return await runner()

    entrypoint.rebind_request_context(comm_context=scoped_ctx)
    with bind_current_request_context(scoped_ctx, comm=getattr(entrypoint, "comm", None)):
        return await runner()


async def _run_default_react_automation_job(
    entrypoint: Any,
    *,
    automation: Dict[str, Any],
    execution: Dict[str, Any],
    trigger: str,
    target_user: str,
    run_conversation_id: str,
    turn_id: str,
    bundle_call_context: Dict[str, Any],
    resolved_user_type: Optional[str] = None,
    economics_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    scoped_ctx = _build_automation_scoped_context(
        entrypoint,
        target_user=target_user,
        run_conversation_id=run_conversation_id,
        turn_id=turn_id,
        bundle_call_context=bundle_call_context,
        resolved_user_type=resolved_user_type,
    )

    async def _run_scoped() -> Dict[str, Any]:
        state = entrypoint.create_initial_state(
            {
                "request_id": getattr(scoped_ctx.request, "request_id", "") or str(uuid.uuid4()),
                "tenant": scoped_ctx.actor.tenant_id,
                "project": scoped_ctx.actor.project_id,
                "user": target_user,
                "economics_user": economics_user_id or target_user,
                "user_type": scoped_ctx.user.user_type,
                "session_id": run_conversation_id,
                "conversation_id": run_conversation_id,
                "turn_id": turn_id,
                "text": _run_prompt(automation=automation, execution=execution, trigger=trigger),
                "attachments": [],
            }
        )
        state["turn_id"] = turn_id
        state["agent_surface"] = "automation_job"
        authority = authority_from_source(bundle_call_context.get("identity_authority"))
        if authority.get("roles"):
            state["roles"] = authority["roles"]
        if authority.get("permissions"):
            state["permissions"] = authority["permissions"]
        if authority.get("actor_user_id"):
            state["actor_user"] = authority["actor_user_id"]
        if authority.get("economics_user_id"):
            state["economics_user"] = authority["economics_user_id"]
        state["automation_execution"] = {
            "automation_id": automation.get("id"),
            "execution_id": execution["id"],
            "trigger": trigger,
            "conversation_id": run_conversation_id,
            "turn_id": turn_id,
            "actor_user_id": target_user,
            "economics_user_id": economics_user_id or target_user,
            "automation_definition": bundle_call_context["automation_definition"],
        }
        run_automation_job_turn = getattr(entrypoint, "run_automation_job_turn", None)
        if not callable(run_automation_job_turn):
            raise RuntimeError(
                "Automation execution requires entrypoint.execute_automation_job(...) or entrypoint.run_automation_job_turn(...)."
            )
        result = await run_automation_job_turn(state=state)
        return result if isinstance(result, dict) else {"final_answer": str(result or "")}

    return await _run_with_entrypoint_request_context(entrypoint, scoped_ctx, _run_scoped)


async def _execute_automation_job(
    entrypoint: Any,
    *,
    automation: Dict[str, Any],
    execution: Dict[str, Any],
    storage: AsyncAutomationStorage,
    target_user: str,
    trigger: str,
    source: Dict[str, Any],
    run_conversation_id: str,
    turn_id: str,
    bundle_call_context: Dict[str, Any],
    resolved_user_type: Optional[str] = None,
    economics_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    executor = getattr(entrypoint, "execute_automation_job", None)
    if callable(executor):
        async def _run_custom() -> Dict[str, Any]:
            result = await executor(
                automation=automation,
                execution=execution,
                storage=storage,
                user_id=target_user,
                trigger=trigger,
                source=source,
                conversation_id=run_conversation_id,
                turn_id=turn_id,
                bundle_call_context=bundle_call_context,
            )
            return result if isinstance(result, dict) else {"answer": str(result or "")}

        try:
            scoped_ctx = _build_automation_scoped_context(
                entrypoint,
                target_user=target_user,
                run_conversation_id=run_conversation_id,
                turn_id=turn_id,
                bundle_call_context=bundle_call_context,
                resolved_user_type=resolved_user_type,
            )
        except RuntimeError:
            return await _run_custom()

        return await _run_with_entrypoint_request_context(entrypoint, scoped_ctx, _run_custom)

    return await _run_default_react_automation_job(
        entrypoint,
        automation=automation,
        execution=execution,
        trigger=trigger,
        target_user=target_user,
        run_conversation_id=run_conversation_id,
        turn_id=turn_id,
        bundle_call_context=bundle_call_context,
        resolved_user_type=resolved_user_type,
        economics_user_id=economics_user_id,
    )


async def _telegram_bot_token() -> str:
    bundle_id = BUNDLE_ID or "task-and-memo-app@1-0"
    return (
        await get_secret("b:integrations.telegram.bot_token")
        or await get_secret(f"bundles.{bundle_id}.secrets.integrations.telegram.bot_token")
        or ""
    )


def _bundle_prop(entrypoint: Any, path: str, default: Any = None) -> Any:
    getter = getattr(entrypoint, "bundle_prop", None)
    if callable(getter):
        return getter(path, default)
    return default


def _telegram_recipient_for_user(entrypoint: Any, *, user_id: str) -> Dict[str, Any] | None:
    target = str(user_id or "").strip()
    if not target:
        return None
    registry = TelegramUserAdminStorage(_storage_root(entrypoint))
    for row in registry.list_users():
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


def _telegram_delivery_kind_for_artifact(artifact: Dict[str, Any]) -> str:
    mime_type = str(artifact.get("mime_type") or artifact.get("mime") or "").strip().lower()
    return "photo" if mime_type.startswith("image/") else "document"


def _execution_artifact_messages(execution: Dict[str, Any]) -> list[TelegramMessage]:
    messages: list[TelegramMessage] = []
    artifacts = execution.get("artifacts") if isinstance(execution.get("artifacts"), list) else []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        text = str(artifact.get("description") or artifact.get("filename") or "").strip()
        messages.append(
            TelegramMessage(
                kind=_telegram_delivery_kind_for_artifact(artifact),
                text=text,
                files=(artifact,),
            )
        )
    return messages


def _dedupe_telegram_messages(messages: list[TelegramMessage]) -> list[TelegramMessage]:
    seen: set[str] = set()
    out: list[TelegramMessage] = []
    for message in messages:
        file_key = ""
        if message.files:
            file_item = message.files[0]
            file_key = str(
                file_item.get("id")
                or file_item.get("hosted_uri")
                or file_item.get("url")
                or file_item.get("physical_path")
                or file_item.get("local_path")
                or file_item.get("logical_path")
                or file_item.get("filename")
                or ""
            )
        key = f"{message.kind}:{file_key}:{message.text}"
        if key in seen:
            continue
        seen.add(key)
        out.append(message)
    return out


async def _deliver_execution_to_telegram(
    entrypoint: Any,
    *,
    user_id: str,
    execution: Dict[str, Any],
    react_result: Dict[str, Any],
    answer: str,
) -> Dict[str, Any] | None:
    if not _bundle_prop(entrypoint, "integrations.telegram.enabled", False):
        return None
    if not _bundle_prop(entrypoint, "integrations.telegram.send_responses", True):
        return None
    recipient = _telegram_recipient_for_user(entrypoint, user_id=user_id)
    if not recipient:
        return None

    messages = render_telegram_messages_from_timeline(
        timeline=(
            react_result.get("turn_log")
            if isinstance(react_result.get("turn_log"), dict) and react_result.get("turn_log")
            else react_result.get("timeline") if isinstance(react_result.get("timeline"), dict) else None
        ),
        react_turn={"answer": answer or execution.get("summary") or "Automation execution completed."},
    )
    messages.extend(_execution_artifact_messages(execution))
    messages = _dedupe_telegram_messages(messages)
    if not messages:
        messages = [TelegramMessage(kind="text", text=str(execution.get("summary") or "Automation execution completed."))]

    delivery = await send_telegram_messages(
        bot_token=await _telegram_bot_token(),
        chat_id=recipient["chat_id"],
        messages=messages,
    )
    return {
        "surface": "telegram",
        "recipient": recipient,
        "message_count": len(messages),
        "delivery": delivery,
    }


async def run_automation_execution(
    entrypoint: Any,
    *,
    automation_id: str,
    trigger: str,
    source: Dict[str, Any] | None = None,
    execution_id: str = "",
    run_conversation_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    automation = await storage.get_automation(automation_id)
    if not automation:
        return {
            "ok": False,
            "user_id": target_user,
            "error": {"code": "automation_not_found", "message": f"Automation {automation_id!r} was not found."},
            "execution": None,
        }
    automation_status = str(automation.get("status") or "").strip().lower()
    trigger_norm = str(trigger or "").strip().lower()
    execution = await storage.get_execution(execution_id=execution_id, automation_id=automation_id) if execution_id else None

    def _is_scheduler_disabled_one_shot_pickup() -> bool:
        if trigger_norm != "scheduled" or automation_status != "disabled" or not isinstance(execution, dict):
            return False
        if str(execution.get("trigger") or "").strip().lower() != "scheduled":
            return False
        if str(execution.get("status") or "").strip().lower() not in {"queued", "running"}:
            return False
        execution_source = execution.get("source") if isinstance(execution.get("source"), dict) else {}
        automation_meta = automation.get("metadata") if isinstance(automation.get("metadata"), dict) else {}
        execution_due_slot = str(execution_source.get("due_slot") or "").strip()
        completed_due_slot = str(automation_meta.get("one_shot_completed_due_slot") or "").strip()
        return bool(execution_due_slot and completed_due_slot and execution_due_slot == completed_due_slot)

    if automation_status in {"archived", "deleted"} or (
        trigger_norm == "scheduled"
        and automation_status != "enabled"
        and not _is_scheduler_disabled_one_shot_pickup()
    ):
        summary = (
            f"Scheduled execution skipped because automation status is {automation_status or 'unknown'}."
            if trigger_norm == "scheduled"
            else f"Automation execution skipped because automation status is {automation_status or 'unknown'}."
        )
        if execution:
            execution = await storage.update_execution(
                execution_id=str(execution.get("id") or execution_id),
                automation_id=automation_id,
                status="cancelled",
                summary=summary,
                log_excerpt=summary,
            )
        return {
            "ok": True,
            "skipped": True,
            "reason": "automation_not_runnable",
            "user_id": target_user,
            "automation": automation,
            "execution": execution,
            "answer": summary,
        }

    run_conversation_id = str(run_conversation_id or "").strip() or f"automation_job_{uuid.uuid4().hex}"
    if execution:
        execution = await storage.update_execution(
            execution_id=str(execution.get("id") or execution_id),
            automation_id=automation_id,
            status="running",
            conversation_id=run_conversation_id,
            summary="Execution started.",
            metadata_patch={"agent_surface": "automation_job"},
        )
    else:
        execution = await storage.create_execution(
            automation_id=automation_id,
            status="running",
            trigger=trigger,
            source=source or {},
            conversation_id=run_conversation_id,
            summary="Execution started.",
            metadata={"agent_surface": "automation_job"},
        )
    turn_id = f"turn_{execution['id']}"
    automation_definition = {
        "id": automation.get("id"),
        "title": automation.get("title"),
        "body": automation.get("body"),
        "description": automation.get("description"),
        "schedule": automation.get("schedule"),
        "relations": automation.get("relations"),
        "metadata": automation.get("metadata"),
    }
    bundle_call_context = {
        "kind": "automation_execution",
        "automation_id": automation_id,
        "execution_id": execution["id"],
        "trigger": trigger,
        "conversation_id": run_conversation_id,
        "turn_id": turn_id,
        "source": source or {},
        "automation": automation_definition,
        "automation_definition": json.dumps(automation_definition, sort_keys=True, ensure_ascii=True),
    }

    from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
    econ_subject = None
    econ_decision = None
    economics_user_id = _automation_economics_user_id(target_user=target_user, source=source or {})
    try:
        # Carry the user identity and verify the automation pipeline is economically
        # feasible BEFORE running it (verify-only). The ReAct work routes through
        # self.run(), which reserves+settles the real cost under the same user, so
        # we do NOT reserve/settle here. Denial -> execution cancelled, no run.
        econ_subject, econ_decision = await _automation_verify_economics(
            entrypoint, target_user=target_user, source=source or {},
        )
    except EconomicsLimitException as exc:
        code = getattr(exc, "code", "rate_limited")
        execution = await storage.update_execution(
            execution_id=execution["id"],
            automation_id=automation_id,
            status="cancelled",
            conversation_id=run_conversation_id,
            turn_id=turn_id,
            summary=f"Automation execution denied by economics ({code}).",
            error=str(exc),
            log_excerpt=str(exc)[:1000],
            metadata_patch={"economics": {
                "denied": True, "code": code, "message": str(exc),
                "data": getattr(exc, "data", {}) or {},
            }},
        )
        logger.warning(
            "[automations.economics] execution denied: execution_id=%s user=%s code=%s",
            execution.get("id"), target_user, code,
        )
        return {
            "ok": False,
            "denied": True,
            "user_id": target_user,
            "automation": await storage.get_automation(automation_id),
            "execution": execution,
            "error": {"code": "economics_denied", "message": str(exc)},
        }
    if econ_decision is not None:
        logger.info(
            "[automations.economics] preflight ok: execution_id=%s actor=%s economics_user=%s role=%s lane=%s funding=%s plan=%s",
            execution["id"], target_user, getattr(econ_subject, "user_id", None),
            getattr(econ_subject, "user_type", None),
            econ_decision.lane, econ_decision.funding_source, econ_decision.plan_id,
        )
    # Point (2): propagate the resolved role so the inner ReAct run() applies the
    # correct plan/limits for this user (esp. scheduled automations enqueued in a system
    # context where the carried role may default to "registered").
    resolved_user_type = getattr(econ_subject, "user_type", None)
    if econ_subject is not None and getattr(econ_subject, "user_id", None):
        economics_user_id = str(econ_subject.user_id)
    authority_context = normalize_execution_authority(
        source or {},
        actor_user_id=target_user,
        economics_user_id=economics_user_id,
        user_type=resolved_user_type or (source or {}).get("user_type") or "registered",
    )
    execution_source = {**(source or {}), **authority_context}
    if resolved_user_type:
        execution_source["user_type"] = resolved_user_type
    bundle_call_context["actor_user_id"] = target_user
    bundle_call_context["economics_user_id"] = economics_user_id
    bundle_call_context["economics_user_type"] = resolved_user_type
    bundle_call_context["identity_authority"] = authority_context
    bundle_call_context["source"] = execution_source

    try:
        try:
            from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_bundle_call_context
        except Exception:
            bind_current_bundle_call_context = None  # type: ignore[assignment]
        context_binding = (
            bind_current_bundle_call_context(bundle_call_context)
            if callable(bind_current_bundle_call_context)
            else nullcontext()
        )
        with context_binding:
            result = await _execute_automation_job(
                entrypoint,
                automation=automation,
                execution=execution,
                storage=storage,
                target_user=target_user,
                trigger=trigger,
                source=execution_source,
                run_conversation_id=run_conversation_id,
                turn_id=turn_id,
                bundle_call_context=bundle_call_context,
                resolved_user_type=resolved_user_type,
                economics_user_id=economics_user_id,
            )
        answer = _result_answer(result)
        current_execution = await storage.get_execution(execution_id=execution["id"], automation_id=automation_id) or execution
        current_status = str(current_execution.get("status") or "").strip().lower()
        requested_status = _result_status(result)
        final_status = current_status if current_status in {"success", "failed", "cancelled"} else (requested_status or "success")
        artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else None
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else None
        if econ_decision is not None:
            metadata = {**(metadata or {}), **_automation_economics_metadata(econ_decision)}
        execution = await storage.update_execution(
            execution_id=execution["id"],
            automation_id=automation_id,
            status=final_status,
            conversation_id=run_conversation_id,
            turn_id=turn_id,
            summary=answer or str(current_execution.get("summary") or "").strip() or "Automation execution completed.",
            result=result or {},
            log_excerpt=answer[:1000],
            artifacts=artifacts,
            metadata_patch=metadata,
        )
        delivery = await _deliver_execution_to_telegram(
            entrypoint,
            user_id=target_user,
            execution=execution,
            react_result=result or {},
            answer=answer,
        )
        if delivery:
            execution = await storage.update_execution(
                execution_id=execution["id"],
                automation_id=automation_id,
                metadata_patch={"last_delivery": delivery},
            )
        return {
            "ok": True,
            "user_id": target_user,
            "automation": await storage.get_automation(automation_id),
            "execution": execution,
            "answer": answer,
        }
    except Exception as exc:
        execution = await storage.update_execution(
            execution_id=execution["id"],
            automation_id=automation_id,
            status="failed",
            conversation_id=run_conversation_id,
            turn_id=turn_id,
            summary="Automation execution failed.",
            error=str(exc),
            log_excerpt=str(exc)[:1000],
        )
        return {
            "ok": False,
            "user_id": target_user,
            "automation": await storage.get_automation(automation_id),
            "execution": execution,
            "error": {"code": "run_automation_failed", "message": str(exc)},
        }


async def enqueue_automation_job(
    entrypoint: Any,
    *,
    work_kind: str,
    automation_id: str,
    trigger: str,
    source: Dict[str, Any],
    conversation_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    storage, target_user = storage_for(entrypoint, user_id=user_id, fingerprint=fingerprint)
    automation = await storage.get_automation(automation_id)
    if not automation:
        return {
            "ok": False,
            "user_id": target_user,
            "error": {"code": "automation_not_found", "message": f"Automation {automation_id!r} was not found."},
            "execution": None,
        }
    run_conversation_id = str(conversation_id or "").strip() or f"automation_job_{uuid.uuid4().hex}"
    execution = await storage.create_execution(
        automation_id=automation_id,
        status="queued",
        trigger=trigger,
        source=source,
        conversation_id=run_conversation_id,
        summary="Queued for automation execution.",
        metadata={"agent_surface": "automation_job"},
    )
    turn_id = f"turn_{execution['id']}"
    tenant = str(getattr(entrypoint.config, "tenant", "") or getattr(entrypoint.settings, "TENANT", "") or "")
    project = str(getattr(entrypoint.config, "project", "") or getattr(entrypoint.settings, "PROJECT", "") or "")
    bundle_id = str(getattr(getattr(entrypoint.config, "ai_bundle_spec", None), "id", "") or "task-and-memo-app@1-0")
    if getattr(entrypoint, "redis", None) is None:
        execution = await storage.update_execution(
            execution_id=execution["id"],
            automation_id=automation_id,
            status="cancelled",
            summary="Automation job could not be queued because Redis is unavailable.",
        )
        return {
            "ok": False,
            "user_id": target_user,
            "automation": automation,
            "execution": execution,
            "error": {"code": "redis_unavailable", "message": "Redis is required to enqueue automation jobs."},
        }
    comm_context = getattr(entrypoint, "comm_context", None)
    comm_user = getattr(comm_context, "user", None)
    user_type = str(source.get("user_type") or getattr(comm_user, "user_type", "registered") or "registered")
    stream = RedisBackgroundJobStream(entrypoint.redis, tenant=tenant, project=project)
    enqueue = await stream.enqueue(
        work_kind=work_kind,
        bundle_id=bundle_id,
        user_id=target_user,
        user_type=user_type,
        queue=user_type,
        job_id=f"job_{execution['id']}",
        dedupe_key=str(source.get("dedupe_key") or ""),
        source=source,
        metadata={
            "conversation_id": run_conversation_id,
            "turn_id": turn_id,
            "text": f"Run automation: {automation.get('title') or automation_id}",
        },
        payload={
            "automation_id": automation_id,
            "execution_id": execution["id"],
        },
    )
    if not enqueue.enqueued:
        execution = await storage.update_execution(
            execution_id=execution["id"],
            automation_id=automation_id,
            status="cancelled",
            summary=f"Automation job was not enqueued ({enqueue.reason}).",
        )
    return {
        "ok": bool(enqueue.enqueued),
        "user_id": target_user,
        "automation": automation,
        "execution": execution,
        "job": {
            "job_id": enqueue.job_id,
            "stream_key": enqueue.stream_key,
            "stream_id": enqueue.stream_id,
            "reason": enqueue.reason,
        },
    }


async def run_automation_now(
    entrypoint: Any,
    *,
    automation_id: str,
    conversation_id: str = "",
    user_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    source: Dict[str, Any] = {"surface": "operation", "operation": "run_automation_now"}
    if str(conversation_id or "").strip():
        source["requested_conversation_id"] = str(conversation_id or "").strip()
        source["conversation_policy"] = "ignored_fresh_job_conversation"
    return await enqueue_automation_job(
        entrypoint,
        work_kind=WORK_KIND_AUTOMATION_RUN_NOW,
        automation_id=automation_id,
        trigger="manual",
        source=source,
        user_id=user_id,
        fingerprint=fingerprint,
    )
