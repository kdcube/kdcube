# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The OAuth surfaces compose Card chains under the Hub's own per-request External MCP family policy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials import serving
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth import surface_guard

POLICY = object()  # opaque here: the platform only passes the Hub's value on


def request_with(policy=None):
    state = SimpleNamespace(oauth_authority_backend="")
    if policy is not None:
        setattr(state, serving.PROJECT_RESOURCE_FAMILIES_STATE_ATTR, policy)
    return SimpleNamespace(state=state)


def test_the_keyword_is_present_only_when_the_hub_bound_a_policy():
    assert serving.project_resource_families_kwargs(request_with(POLICY)) == {"project_families": POLICY}
    assert serving.project_resource_families_kwargs(request_with()) == {}
    assert serving.project_resource_families_kwargs(SimpleNamespace()) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [POLICY, None])
async def test_the_surface_guard_composes_under_the_requests_policy(monkeypatch, policy):
    seen = {}

    async def compose(redis, **kwargs):
        seen.update(kwargs)
        return None

    async def store(request):
        return SimpleNamespace(redis=object())

    monkeypatch.setattr(surface_guard, "resolve_live_grant_composition", compose)
    monkeypatch.setattr(surface_guard, "_default_grant_store", store)
    monkeypatch.setattr(surface_guard, "oauth_tenant_project", lambda request: ("t", "p"))
    monkeypatch.setattr(surface_guard, "delegated_serving_resolvers", lambda request: None)
    record = {"registry_access_id": "card-1", "credential": {"subject": "agent", "attrs": {"client_id": "c"}}}
    assert await surface_guard._live_grant_record(request_with(policy), record) is None
    assert seen["access_id"] == "card-1"
    assert ("project_families" in seen) is (policy is not None)
    if policy is not None:
        assert seen["project_families"] is POLICY
