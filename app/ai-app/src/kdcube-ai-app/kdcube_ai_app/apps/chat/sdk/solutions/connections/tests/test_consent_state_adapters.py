# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Store adapters for the consent resolver (`consent_state_adapters.py`).

Fakes the two stores; pins the store-shape → consent-vocabulary mapping:
delegated granted claims come from list_access resource-grants and vocabulary
from the capabilities config scoped by role; connected-account state maps the
broker reasons to given/pending/unavailable.
"""

from __future__ import annotations

import asyncio

from kdcube_ai_app.apps.chat.sdk.solutions.connections.consent_state import (
    GIVEN, PENDING, UNAVAILABLE,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.consent_state_adapters import (
    DelegatedGrantStoreReader,
    ConnectedAccountStoreReader,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import (
    REASON_AGENT_ACCOUNT_BINDING_REQUIRED,
    USER_ACTIONABLE_REASONS,
)
from kdcube_ai_app.apps.chat.sdk.integrations.named_service_consent import (
    CONSENT_ERROR_CONTRACT,
)

_CAPS = [
    {"grant": "memories:read", "label": "Read KDCube memories", "description": "Read notes.",
     "delegable_roles": ["registered", "paid"]},
    {"grant": "admin:all", "label": "Everything", "delegable_roles": ["super-admin"]},
]


class _FakeAccessService:
    def __init__(self, records):
        self._records = records

    async def list_access(self, user):
        return {"access": self._records}


def test_delegated_granted_claims_from_resource_grants():
    svc = _FakeAccessService([
        {"resource_grants": {"res-a": ("memories:read",), "res-b": ("tasks:read", "tasks:write")}},
    ])
    reader = DelegatedGrantStoreReader(svc, _CAPS, user_roles=["registered"])
    granted = asyncio.run(reader.granted_claims({"sub": "u1"}))
    assert granted == {"memories:read", "tasks:read", "tasks:write"}


def test_delegated_vocabulary_is_role_scoped():
    reader = DelegatedGrantStoreReader(_FakeAccessService([]), _CAPS, user_roles=["registered"])
    v = reader.vocabulary("memories:read")
    assert v["label"] == "Read KDCube memories" and v["delegable"] is True
    # admin:all is delegable only to super-admin -> not delegable for a registered user
    assert reader.vocabulary("admin:all")["delegable"] is False
    # an undeclared grant is not delegable, shown as-is
    assert reader.vocabulary("mystery:claim") == {"delegable": False}


def test_list_access_failure_is_no_grants_not_crash():
    class _Boom:
        async def list_access(self, user):
            raise RuntimeError("redis down")
    reader = DelegatedGrantStoreReader(_Boom(), _CAPS, user_roles=["registered"])
    assert asyncio.run(reader.granted_claims({"sub": "u1"})) == set()


def test_connected_account_reason_maps_to_state():
    async def resolve_given(**kw):
        return None  # clean resolution -> given

    async def resolve_pending(**kw):
        return "claim_upgrade_required"

    async def resolve_unavail(**kw):
        return "provider_not_configured"

    labels = {"slack:read": {"label": "Read Slack"}}
    given = asyncio.run(ConnectedAccountStoreReader(resolve_given, labels).claim_state(
        claim="slack:read", provider_id="slack", connector_app_id="", user={}))
    assert given["state"] == GIVEN and given["label"] == "Read Slack"

    pending = asyncio.run(ConnectedAccountStoreReader(resolve_pending).claim_state(
        claim="slack:write", provider_id="slack", connector_app_id="", user={}))
    assert pending["state"] == PENDING and pending["grant_action"]["reason"] == "claim_upgrade_required"

    unavail = asyncio.run(ConnectedAccountStoreReader(resolve_unavail).claim_state(
        claim="x:y", provider_id="x", connector_app_id="", user={}))
    assert unavail["state"] == UNAVAILABLE


def test_agent_account_binding_reason_is_actionable_on_every_consent_surface():
    async def resolve(**kw):
        return REASON_AGENT_ACCOUNT_BINDING_REQUIRED

    state = asyncio.run(
        ConnectedAccountStoreReader(resolve).claim_state(
            claim="linkedin:post",
            provider_id="linkedin",
            connector_app_id="",
            user={},
        )
    )

    assert state["state"] == PENDING
    assert state["grant_action"]["reason"] == REASON_AGENT_ACCOUNT_BINDING_REQUIRED
    assert REASON_AGENT_ACCOUNT_BINDING_REQUIRED in USER_ACTIONABLE_REASONS
    assert REASON_AGENT_ACCOUNT_BINDING_REQUIRED in CONSENT_ERROR_CONTRACT["reasons"]
