"""Inline REST identity hand-off through the mounted SDK router.

The real FastAPI auth dependency, gateway, RequestAuthResolver,
PlatformTokenAuthenticator, session/realm guards, endpoint dispatch and CSRF
mint/consume run here. Named seams are the synthetic IdP token table, Redis
session persistence, availability circuits, in-memory atomic CSRF storage,
and bundle discovery/loading (including an unused PG handle). The probe is a
generic bundle, not PB policy.
No middleware or dependency override supplies the handler's AuthContext.
This is in-process HTTP Source proof, not an installed runtime/IdP check.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from kdcube_ai_app.apps.chat.ingress import resolvers
from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations, operation_csrf
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.infra.auth_context import (
    AuthContext, bind_auth_context, get_current_auth_context,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.request_auth import RequestAuthResolver
from kdcube_ai_app.auth.AuthManager import AuthManager, AuthenticationError, User
from kdcube_ai_app.auth.sessions import UserSession, UserType
from kdcube_ai_app.infra.plugin.bundle_loader import api


TENANT, PROJECT, BUNDLE = "rest-test-tenant", "rest-test-project", "context-probe@1-0"
COOKIE = "__Secure-LATC"
TOKENS = {"synthetic-alice-token": "alice", "synthetic-bob-token": "bob"}


class TokenTableIdP(AuthManager):
    """Synthetic provider verdict; the SDK extracts and checks cookie tokens."""

    async def authenticate(self, token):
        subject = TOKENS.get(token)
        if subject is None:
            raise AuthenticationError("unknown synthetic token")
        return User(username=subject, email=f"{subject}@example.test",
                    roles=["kdcube:role:registered"], permissions=["probe.read"])

    async def get_service_token(self):
        raise AuthenticationError("no service token in this fixture")


async def fresh_session(context, user_type, user_data):
    """Session persistence seam, retaining only the real authenticator's facts."""
    if not user_data:
        return UserSession(session_id="anonymous", user_type=UserType.ANONYMOUS,
                           fingerprint=context.get_fingerprint(), request_context=context)
    return UserSession(
        session_id=f"session-{user_data['user_id']}", user_type=user_type,
        user_id=user_data["user_id"], username=user_data["username"],
        email=user_data["email"], roles=list(user_data["roles"]),
        permissions=list(user_data["permissions"]),
        identity_authority=dict(user_data["identity_authority"]), request_context=context,
    )


class AvailableCircuit:
    async def check_request_allowed(self, session):
        return True

    async def record_success(self):
        return None

    async def record_failure(self, reason):
        return None


class AvailableCircuits:
    def get_circuit_breaker(self, name, config=None):
        return AvailableCircuit()


class AtomicCsrfMemory:
    def __init__(self):
        self.values = {}

    async def setex(self, key, _ttl, value):
        self.values[key] = value
        return True

    async def eval(self, _script, _numkeys, key):
        # No await between read and deletion: single-use in this event loop.
        return self.values.pop(key, None)


def snapshot():
    context = get_current_auth_context()
    if context is None:
        raise HTTPException(status_code=409, detail="handler_auth_context_missing")
    return context.to_dict()


class ContextProbe:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.concurrent = []

    @api(alias="context_probe", csrf=True)
    async def probe(self, mode="normal", **_untrusted_data):
        before = snapshot()
        self.calls.append(before)
        if mode == "exception":
            raise HTTPException(status_code=409, detail="synthetic_handler_failure")
        if mode == "cancel":
            self.entered.set()
            await self.release.wait()
        if mode == "concurrent":
            self.concurrent.append(before["user_id"])
            if len(self.concurrent) == 2:
                self.release.set()
            await asyncio.wait_for(self.release.wait(), timeout=3)
            await asyncio.sleep(0)
        return {"before": before, "after": snapshot()}

    @api(alias="context_public", route="public")
    async def public_probe(self, **_untrusted_data):
        return snapshot()


@pytest.fixture
def mounted(monkeypatch, tmp_path):
    probe = ContextProbe()
    settings = get_settings()

    class ServedRealm:
        TENANT, PROJECT = TENANT, PROJECT

        def __getattr__(self, name):
            return getattr(settings, name)

    async def bundle_spec(*, request, tenant, project, bundle_id):
        if bundle_id != BUNDLE:
            return None
        return SimpleNamespace(id=BUNDLE, path=str(tmp_path), module="entrypoint", singleton=True)

    async def config_secrets(config, *, bundle_id):
        return config

    async def bundle_instance(spec, config, *, comm_context, redis, pg_pool):
        return probe, None

    monkeypatch.setattr(integrations, "get_settings", lambda: ServedRealm())
    monkeypatch.setattr(integrations, "_resolve_bundle_spec_from_runtime", bundle_spec)
    monkeypatch.setattr(integrations, "resolve_config_request_secrets", config_secrets)
    monkeypatch.setattr(integrations, "get_workflow_instance_async", bundle_instance)
    monkeypatch.setattr(integrations, "store_get_bundle_props_from_authority", lambda **_: {})
    adapter = resolvers.get_fastapi_adapter()
    monkeypatch.setattr(adapter.gateway, "circuit_manager", AvailableCircuits())
    monkeypatch.setattr(adapter, "request_auth_resolver",
                        RequestAuthResolver(auth_manager=TokenTableIdP(), session_factory=fresh_session))
    app = FastAPI()
    app.include_router(integrations.router, prefix="/api/integrations")
    app.state.redis_async, app.state.pg_pool = AtomicCsrfMemory(), object()
    return SimpleNamespace(app=app, probe=probe)


def path(*, tenant=TENANT, project=PROJECT, bundle=BUNDLE, operation="context_probe", route="operations"):
    return f"/api/integrations/bundles/{tenant}/{project}/{bundle}/{route}/{operation}"


def cookie(user="alice"):
    return {"cookie": f"{COOKIE}=synthetic-{user}-token"}


async def mint(client, user="alice"):
    response = await client.get(path() + "/csrf", headers=cookie(user))
    assert response.status_code == 200, response.text
    assert response.json()["csrf_required"] is True
    return response.json()["csrf_token"]


def client_for(mounted):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=mounted.app),
                             base_url="http://source-test.example.test")


async def invoke(client, user="alice", *, data=None, token=None, headers=None, url=None):
    if token is None:
        token = await mint(client, user)
    return await client.post(url or path(), json={"data": data or {}}, headers={
        **cookie(user), operation_csrf.OPERATION_CSRF_HEADER: token, **(headers or {}),
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("user", ["alice", "bob"])
async def test_cookie_admission_hands_verified_context_to_handler(mounted, user):
    assert get_current_auth_context() is None
    async with client_for(mounted) as client:
        response = await invoke(client, user)
    assert response.status_code == 200, response.text
    value = response.json()["context_probe"]
    assert value["before"] == value["after"]
    context = value["before"]
    assert (context["tenant"], context["project"], context["bundle_id"]) == (TENANT, PROJECT, BUNDLE)
    assert (context["principal_kind"], context["principal_id"], context["user_id"]) == ("user", user, user)
    assert context["user_type"] == "registered"
    assert context["session_id"] == f"session-{user}"
    assert context["roles"] == ["kdcube:role:registered"]
    assert context["permissions"] == ["probe.read"]
    assert context["actor"]["identity_authority"]["platform_user_id"] == user
    assert context["actor"]["identity_authority"]["source"] == "platform_token_authenticator"
    assert get_current_auth_context() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"cookie": f"{COOKIE}=forged-token"}])
async def test_missing_or_forged_cookie_cannot_create_context(mounted, headers):
    async with client_for(mounted) as client:
        response = await client.post(path(), headers=headers, json={"data": {
            "user_id": "alice", "auth_context": {"principal_kind": "user", "user_id": "alice"},
        }})
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": "User is required."}
    assert mounted.probe.calls == []
    assert get_current_auth_context() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{"tenant": "foreign"}, {"project": "foreign"}, {"bundle": "foreign"}])
async def test_wrong_realm_or_unserved_bundle_is_refused(mounted, changed):
    async with client_for(mounted) as client:
        response = await invoke(client, url=path(**changed))
    assert response.status_code == (404 if "bundle" in changed else 403), response.text
    assert mounted.probe.calls == []
    assert get_current_auth_context() is None


@pytest.mark.asyncio
async def test_payload_and_header_identity_do_not_replace_verified_user(mounted):
    async with client_for(mounted) as client:
        response = await invoke(client, data={"user_id": "bob", "tenant": "foreign",
            "actor": {"user_id": "bob"}, "auth_context": {"user_id": "bob"}},
            headers={"x-user-id": "bob", "x-test-auth-context": "forged", "x-tenant": "foreign"})
    assert response.status_code == 200, response.text
    context = response.json()["context_probe"]["before"]
    assert context["user_id"] == "alice" and context["tenant"] == TENANT
    assert context["actor"]["identity_authority"]["platform_user_id"] == "alice"
    assert get_current_auth_context() is None


@pytest.mark.asyncio
async def test_csrf_missing_replayed_and_wrong_subject_still_refuse(mounted):
    async with client_for(mounted) as client:
        missing = await client.post(path(), json={"data": {}}, headers=cookie())
        assert missing.status_code == 403
        token = await mint(client)
        wrong_subject = await invoke(client, "bob", token=token)
        assert wrong_subject.status_code == 403
        # A failed subject match still consumes this single-use token.
        consumed = await invoke(client, token=token)
        assert consumed.status_code == 403
        fresh = await mint(client)
        assert (await invoke(client, token=fresh)).status_code == 200
        assert (await invoke(client, token=fresh)).status_code == 403
    assert len(mounted.probe.calls) == 1
    assert get_current_auth_context() is None


@pytest.mark.asyncio
async def test_concurrent_authenticated_users_keep_task_local_identity(mounted):
    async with client_for(mounted) as client:
        responses = await asyncio.gather(*(
            invoke(client, user, data={"mode": "concurrent"}) for user in ("alice", "bob")
        ))
    for user, response in zip(("alice", "bob"), responses):
        assert response.status_code == 200, response.text
        value = response.json()["context_probe"]
        assert value["before"]["user_id"] == value["after"]["user_id"] == user
    assert sorted(mounted.probe.concurrent) == ["alice", "bob"]
    assert get_current_auth_context() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "exception", "cancel"])
async def test_invocation_restores_parent_context_on_all_exit_paths(mounted, mode):
    parent = AuthContext.for_service(tenant="outer", project="outer", service_id="outer-service")

    async def call_and_observe(client):
        try:
            return await invoke(client, data={"mode": mode})
        finally:
            assert get_current_auth_context() is parent

    async with client_for(mounted) as client:
        # This sentinel tests reset, not admission: the handler must see Alice,
        # never the pre-existing parent service identity.
        with bind_auth_context(parent):
            task = asyncio.create_task(call_and_observe(client))
            if mode == "cancel":
                try:
                    await asyncio.wait_for(mounted.probe.entered.wait(), timeout=3)
                finally:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                response = await task
                assert response.status_code == (409 if mode == "exception" else 200), response.text
            assert mounted.probe.calls[0]["user_id"] == "alice"
            assert get_current_auth_context() is parent
    assert get_current_auth_context() is None


@pytest.mark.asyncio
async def test_public_anonymous_invocation_masks_parent_authenticated_context(mounted):
    parent = AuthContext(tenant=TENANT, project=PROJECT, bundle_id=BUNDLE,
                         principal_kind="user", principal_id="alice", user_id="alice", user_type="registered")
    async with client_for(mounted) as client:
        with bind_auth_context(parent):
            response = await client.post(path(operation="context_public", route="public"), json={"data": {}})
            assert response.status_code == 200, response.text
            context = response.json()["context_public"]
            assert context["principal_kind"] == "anonymous"
            assert context["user_id"] is None
            assert (context["tenant"], context["project"], context["bundle_id"]) == (TENANT, PROJECT, BUNDLE)
            assert get_current_auth_context() is parent
    assert get_current_auth_context() is None
