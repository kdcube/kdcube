# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""Enqueue a background job that runs as a captured identity.

A ``@cron(span="system")`` worker has no request, no user, and no communicator
(the scheduler hands the method a bare stub context, and ``entrypoint.comm`` is
None). So work that needs a real per-user turn context — a communicator for
streaming/telemetry, an identity for authority and economics — cannot be built
inside the cron itself.

The platform already solves this for automations: the cron only *enqueues* a
background job, and the chat turn processor claims it and mints a full turn
context + ``ChatCommunicator`` from the job's identity before dispatching the
bundle's ``@on_job``. This helper exposes that same path for any system trigger,
without an automation record.

Attribution is by **identity**, not a bare user id. KDCube has linked
identities — a surface/app actor (e.g. ``telegram_<id>``) linked to a platform
principal that owns funding/roles. That linkage lives in an ``identity_authority``
projection (actor_user_id, economics/platform_user_id, platform_roles,
platform_permissions). Enqueuing with only a ``user_id`` would drop the linked
principal, so the caller passes the whole ``identity_authority`` and this helper
forwards it — the processor re-projects actor/economics/roles/permissions from
it on the way into the turn.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from kdcube_ai_app.infra.jobs.stream import RedisBackgroundJobStream

log = logging.getLogger("kdcube.automations.system_jobs")


def _scope(entrypoint: Any) -> tuple[str, str, str]:
    config = getattr(entrypoint, "config", None)
    settings = getattr(entrypoint, "settings", None)
    tenant = str(getattr(config, "tenant", "") or getattr(settings, "TENANT", "") or "")
    project = str(getattr(config, "project", "") or getattr(settings, "PROJECT", "") or "")
    bundle_id = str(getattr(getattr(config, "ai_bundle_spec", None), "id", "") or "")
    return tenant, project, bundle_id


def _actor_user_id(identity_authority: Dict[str, Any]) -> str:
    return str(
        identity_authority.get("actor_user_id")
        or identity_authority.get("storage_user_id")
        or identity_authority.get("platform_user_id")
        or ""
    ).strip()


async def enqueue_job_as_identity(
    entrypoint: Any,
    *,
    identity_authority: Dict[str, Any],
    work_kind: str,
    payload: Dict[str, Any],
    queue_label: str = "registered",
    conversation_id: str = "",
    turn_text: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    source: Optional[Dict[str, Any]] = None,
    dedupe_key: str = "",
    job_id: str = "",
) -> Dict[str, Any]:
    """Enqueue a background job attributed to ``identity_authority``, to be
    executed by the bundle's ``@on_job`` inside a real turn context. The full
    authority projection is forwarded so linked-identity funding/roles survive.

    Returns ``{"ok", "enqueued", "job_id", "actor_user_id", "reason"}``.
    ``enqueued`` is False without an error when ``dedupe_key`` collapses a
    duplicate — the caller's idempotency, not a failure.
    """
    authority = dict(identity_authority or {})
    actor = _actor_user_id(authority)
    if not actor:
        return {"ok": False, "enqueued": False, "reason": "missing_identity"}
    if getattr(entrypoint, "redis", None) is None:
        return {"ok": False, "enqueued": False, "actor_user_id": actor, "reason": "redis_unavailable"}

    tenant, project, bundle_id = _scope(entrypoint)
    resolved_job_id = str(job_id or "").strip() or f"job_{uuid.uuid4().hex}"
    run_conversation_id = str(conversation_id or "").strip() or f"job_{resolved_job_id}"
    turn_id = f"turn_{resolved_job_id}"
    job_source = dict(source or {})
    job_source.setdefault("identity_authority", authority)

    stream = RedisBackgroundJobStream(entrypoint.redis, tenant=tenant, project=project)
    result = await stream.enqueue(
        work_kind=work_kind,
        payload=dict(payload or {}),
        queue_label=str(queue_label or "registered"),
        bundle_id=bundle_id,
        user_id=actor,
        source=job_source,
        identity_authority=authority,
        metadata={
            "conversation_id": run_conversation_id,
            "turn_id": turn_id,
            "text": str(turn_text or f"Run {work_kind}"),
            "roles": list(authority.get("platform_roles") or authority.get("roles") or []),
            "permissions": list(authority.get("platform_permissions") or authority.get("permissions") or []),
            **(metadata or {}),
        },
        job_id=resolved_job_id,
        dedupe_key=str(dedupe_key or ""),
    )
    log.info(
        "[system-job] work_kind=%s actor=%s economics=%s bundle=%s job_id=%s enqueued=%s reason=%s",
        work_kind, actor, authority.get("economics_user_id") or authority.get("platform_user_id") or actor,
        bundle_id, result.job_id, result.enqueued, result.reason,
    )
    return {
        "ok": True,
        "enqueued": bool(result.enqueued),
        "job_id": result.job_id,
        "actor_user_id": actor,
        "reason": result.reason,
    }
