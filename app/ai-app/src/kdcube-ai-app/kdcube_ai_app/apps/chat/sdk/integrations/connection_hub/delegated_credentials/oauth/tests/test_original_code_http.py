"""Actual token-handler selection; server-owned original-pair capability."""
from __future__ import annotations

import importlib
import json
import time
from types import SimpleNamespace

import pytest
from starlette.datastructures import FormData

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import routes

MODULE = "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http.original_code"
VERIFIER = "x" * 48


def api():
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == MODULE:
            pytest.fail("original-code HTTP capability is not implemented")
        raise


def request(factory):
    app_state = SimpleNamespace(oauth_original_exchange_factory=factory)
    state = SimpleNamespace()
    value = SimpleNamespace(state=state, app=SimpleNamespace(state=app_state))
    async def form():
        return FormData({"grant_type": "authorization_code", "code": "unit-original-code",
                         "client_id": "unit-client", "redirect_uri": "https://unit.test/cb",
                         "code_verifier": VERIFIER})
    value.form = form
    return value


def handler(call, **changes):
    return api().OriginalCodeExchangeHandler(tenant="unit-tenant", project="unit-project", exchange=call, **changes)


def pair(proof, **changes):
    return api().OriginalTokenPair(**{
        "proof_fingerprint": proof.fingerprint, "access_token": "unit-access-original",
        "refresh_token": "unit-refresh-original", "access_expires_at": int(time.time()) + 60,
        "delivery_deadline": int(time.time()) + 30, "scopes": ("records:read",),
        "access_id": "unit-card", "card_kind": "automation", **changes,
    })


@pytest.fixture(autouse=True)
def namespace(monkeypatch):
    monkeypatch.setattr(api(), "oauth_tenant_project", lambda request: ("unit-tenant", "unit-project"))
    def legacy_store(request):
        pytest.fail("configured original exchange reached legacy consume/minter")
    monkeypatch.setattr(routes, "get_grant_store", legacy_store)


@pytest.mark.asyncio
async def test_actual_http_retries_use_same_bound_provider_without_legacy_consumption():
    calls = []
    async def exchange(*, proof, code):
        calls.append((proof.fingerprint, code))
        return pair(proof)
    req = request(lambda: handler(exchange))
    first, second = await routes.token(req), await routes.token(req)
    assert first.status_code == second.status_code == 200
    one, two = json.loads(first.body), json.loads(second.body)
    assert one["access_token"] == two["access_token"] == "unit-access-original"
    assert one["refresh_token"] == two["refresh_token"] == "unit-refresh-original"
    assert len(calls) == 2 and calls[0] == calls[1]
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["pragma"] == "no-cache"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, {}, object()])
async def test_present_but_invalid_factory_never_falls_back(bad):
    response = await routes.token(request(bad))
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_wrong_host_namespace_refuses_before_provider():
    async def exchange(**kwargs):
        pytest.fail("namespace mismatch reached provider")
    bound = api().OriginalCodeExchangeHandler(tenant="another-tenant", project="unit-project", exchange=exchange)
    response = await routes.token(request(lambda: bound))
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_request_state_binding_takes_precedence_over_app_state():
    async def exchange(**kwargs):
        pytest.fail("invalid request binding fell back to application factory")
    req = request(lambda: handler(exchange))
    req.state.oauth_original_exchange_factory = None
    assert (await routes.token(req)).status_code == 503


@pytest.mark.asyncio
async def test_provider_error_never_echoes_credentials_or_exception_details():
    async def exchange(**kwargs):
        raise RuntimeError("unit-canary-bearer-and-provider-detail")
    response = await routes.token(request(lambda: handler(exchange)))
    assert response.status_code == 503
    assert b"unit-canary" not in response.body


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"proof_fingerprint": "0" * 64}, {"access_expires_at": 1}, {"delivery_deadline": 1},
    {"access_expires_at": True}, {"refresh_token": ""}, {"access_token": "bad\nvalue"},
    {"scopes": (" records:read",)}, {"scopes": ("records:read", "records:read")},
])
async def test_invalid_original_pair_is_never_published(changes):
    async def exchange(*, proof, code):
        return pair(proof, **changes)
    response = await routes.token(request(lambda: handler(exchange)))
    assert response.status_code == 400
    assert b"unit-access-original" not in response.body


@pytest.mark.asyncio
async def test_invalid_pkce_shape_does_not_reach_provider():
    async def exchange(**kwargs):
        pytest.fail("bad proof reached provider")
    req = request(lambda: handler(exchange))
    original_form = req.form
    async def bad_form():
        form = dict(await original_form())
        form["code_verifier"] = "short"
        return FormData(form)
    req.form = bad_form
    assert (await routes.token(req)).status_code == 400


@pytest.mark.asyncio
async def test_absent_factory_leaves_existing_workflow_selected():
    req = request(None)
    del req.app.state.oauth_original_exchange_factory
    assert await api().original_authorization_code_response(req, await req.form()) is None


def test_pair_repr_excludes_both_bearers():
    proof = SimpleNamespace(fingerprint="a" * 64)
    value = pair(proof)
    assert "unit-access-original" not in repr(value)
    assert "unit-refresh-original" not in repr(value)
