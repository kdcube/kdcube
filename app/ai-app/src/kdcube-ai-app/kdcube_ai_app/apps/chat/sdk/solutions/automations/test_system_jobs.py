# SPDX-License-Identifier: MIT
"""enqueue_job_as_identity forwards the full identity authority (not a bare
user id), derives the actor from it, and reports dedupe as non-error."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from kdcube_ai_app.infra.jobs.stream import EnqueueResult
from kdcube_ai_app.apps.chat.sdk.solutions.automations import system_jobs


class _FakeStream:
    last_kwargs: dict = {}

    def __init__(self, redis, *, tenant, project):
        self.tenant, self.project = tenant, project

    async def enqueue(self, **kwargs):
        _FakeStream.last_kwargs = kwargs
        return EnqueueResult(enqueued=True, job_id=kwargs.get("job_id") or "job_x", stream_key="k", reason="enqueued")


def _entrypoint():
    return SimpleNamespace(
        redis=object(),
        config=SimpleNamespace(tenant="demo", project="demo-march",
                               ai_bundle_spec=SimpleNamespace(id="news@2026-05-20-12-05")),
        settings=SimpleNamespace(TENANT="demo", PROJECT="demo-march"),
    )


_AUTHORITY = {
    "actor_user_id": "telegram_42",              # surface identity
    "economics_user_id": "platform_principal_7", # linked platform principal
    "platform_user_id": "platform_principal_7",
    "platform_roles": ["kdcube:role:super-admin"],
    "platform_permissions": ["news:generate"],
}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_enqueue_forwards_identity_authority_and_derives_actor(monkeypatch):
    monkeypatch.setattr(system_jobs, "RedisBackgroundJobStream", _FakeStream)
    result = _run(system_jobs.enqueue_job_as_identity(
        _entrypoint(),
        identity_authority=_AUTHORITY,
        work_kind="news.generation.scheduled",
        payload={"channel": "news"},
        dedupe_key="news-gen:news:2026-07-21",
    ))
    kw = _FakeStream.last_kwargs
    assert result["ok"] and result["enqueued"] is True
    assert result["actor_user_id"] == "telegram_42"
    # the actor drives stream routing...
    assert kw["user_id"] == "telegram_42"
    # ...but the WHOLE authority (incl. the linked platform principal) is forwarded
    assert kw["identity_authority"] == _AUTHORITY
    assert kw["identity_authority"]["economics_user_id"] == "platform_principal_7"
    assert kw["source"]["identity_authority"] == _AUTHORITY
    assert kw["metadata"]["roles"] == ["kdcube:role:super-admin"]
    assert kw["work_kind"] == "news.generation.scheduled"
    assert kw["payload"] == {"channel": "news"}


def test_missing_identity_is_rejected(monkeypatch):
    monkeypatch.setattr(system_jobs, "RedisBackgroundJobStream", _FakeStream)
    result = _run(system_jobs.enqueue_job_as_identity(
        _entrypoint(), identity_authority={}, work_kind="x", payload={}))
    assert result["ok"] is False and result["reason"] == "missing_identity"


def test_no_redis_is_graceful(monkeypatch):
    ep = _entrypoint(); ep.redis = None
    result = _run(system_jobs.enqueue_job_as_identity(
        ep, identity_authority=_AUTHORITY, work_kind="x", payload={}))
    assert result["ok"] is False and result["reason"] == "redis_unavailable"
