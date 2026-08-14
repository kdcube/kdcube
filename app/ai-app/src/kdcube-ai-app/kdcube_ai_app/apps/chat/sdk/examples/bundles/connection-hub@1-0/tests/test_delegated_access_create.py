"""delegated_access_create must forward every field the service accepts.

A manual token's record is keyed by a random access_id, so the
deterministic-key extend path (delegated_agent_grant_create) cannot reach it
later: whatever the caller sends at creation is its only chance.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import (
    load_dynamic_module_for_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
    AutomationAccessService,
)

ACCOUNT_SCOPE = {"linkedin": {"linkedin_acc_1": ["linkedin:post"]}}


def _entrypoint_module():
    bundle_root = Path(__file__).resolve().parents[1]
    _name, module = load_dynamic_module_for_path(bundle_root / "entrypoint.py")
    return module


class _RecordingService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_access(self, user, **kwargs):
        self.calls.append({"user": user, **kwargs})
        return {"ok": True}

    async def update_access(self, user, **kwargs):
        self.calls.append({"method": "update", "user": user, **kwargs})
        return {"ok": True}

    async def extend_client_access(self, user, **kwargs):
        self.calls.append({"method": "extend", "user": user, **kwargs})
        return {"ok": True}


@pytest.fixture()
def entrypoint(monkeypatch):
    module = _entrypoint_module()
    service = _RecordingService()
    monkeypatch.setattr(module, "_automation_access_service", lambda *a, **kw: service)
    monkeypatch.setattr(
        module, "_platform_user_payload", lambda *a, **kw: {"user_id": "google:1"}
    )
    instance = module.ConnectionHubEntrypoint.__new__(module.ConnectionHubEntrypoint)
    return SimpleNamespace(module=module, instance=instance, service=service)


def test_service_still_accepts_account_scope():
    # Guards the other side of the contract: a renamed/removed service
    # parameter must fail here, not silently drop bindings at runtime.
    assert "account_scope" in inspect.signature(AutomationAccessService.create_access).parameters


@pytest.mark.asyncio
async def test_account_scope_is_forwarded_to_the_service(entrypoint):
    await entrypoint.module.ConnectionHubEntrypoint.delegated_access_create(
        entrypoint.instance,
        data={
            "label": "Automation access",
            "resource_grants": {"res": ["named_services:use"]},
            "account_scope": ACCOUNT_SCOPE,
        },
    )
    call = entrypoint.service.calls[-1]
    assert call["account_scope"] == ACCOUNT_SCOPE


@pytest.mark.asyncio
async def test_absent_account_scope_stays_none(entrypoint):
    await entrypoint.module.ConnectionHubEntrypoint.delegated_access_create(
        entrypoint.instance,
        data={"label": "x", "resource_grants": {"res": ["named_services:use"]}},
    )
    assert entrypoint.service.calls[-1]["account_scope"] is None


@pytest.mark.asyncio
async def test_update_forwards_explicit_empty_account_scope(entrypoint):
    await entrypoint.module.ConnectionHubEntrypoint.delegated_access_update(
        entrypoint.instance,
        data={
            "access_id": "aut_1",
            "resource_grants": {"res": ["named_services:use"]},
            "account_scope": {},
        },
    )
    assert entrypoint.service.calls[-1]["method"] == "update"
    assert entrypoint.service.calls[-1]["account_scope"] == {}


@pytest.mark.asyncio
async def test_external_client_grant_forwards_explicit_empty_account_scope(entrypoint):
    await entrypoint.module.ConnectionHubEntrypoint.delegated_agent_grant_create(
        entrypoint.instance,
        data={
            "client_id": "external-client",
            "resource": "res",
            "claims": ["named_services:use"],
            "account_scope": {},
            "replace": True,
        },
    )
    assert entrypoint.service.calls[-1]["method"] == "extend"
    assert entrypoint.service.calls[-1]["account_scope"] == {}


@pytest.mark.asyncio
async def test_named_service_operations_keep_their_absent_semantics(entrypoint):
    await entrypoint.module.ConnectionHubEntrypoint.delegated_access_create(
        entrypoint.instance,
        data={"label": "x", "resource_grants": {"res": ["named_services:use"]}},
    )
    assert entrypoint.service.calls[-1]["named_service_operations"] is None


# -- save preconditions ---------------------------------------------------------


def test_service_still_accepts_the_save_preconditions():
    parameters = inspect.signature(AutomationAccessService.update_access).parameters
    assert "expected_card_revision" in parameters
    assert "expected_catalog_version" in parameters


@pytest.mark.asyncio
async def test_update_forwards_the_editor_preconditions(entrypoint):
    await entrypoint.module.ConnectionHubEntrypoint.delegated_access_update(
        entrypoint.instance,
        data={
            "access_id": "aut_1",
            "resource_grants": {"res": ["named_services:use"]},
            "expected_card_revision": 8,
            "expected_catalog_version": "delegated_catalog_2026-08-14-10-00-00-000_abcdef012345",
        },
    )
    call = entrypoint.service.calls[-1]
    assert call["expected_card_revision"] == 8
    assert call["expected_catalog_version"] == (
        "delegated_catalog_2026-08-14-10-00-00-000_abcdef012345"
    )


@pytest.mark.asyncio
async def test_a_revision_sent_as_a_string_is_accepted(entrypoint):
    await entrypoint.module.ConnectionHubEntrypoint.delegated_access_update(
        entrypoint.instance,
        data={
            "access_id": "aut_1",
            "resource_grants": {"res": ["named_services:use"]},
            "expected_card_revision": "8",
        },
    )
    assert entrypoint.service.calls[-1]["expected_card_revision"] == 8


@pytest.mark.asyncio
async def test_absent_preconditions_stay_none(entrypoint):
    await entrypoint.module.ConnectionHubEntrypoint.delegated_access_update(
        entrypoint.instance,
        data={"access_id": "aut_1", "resource_grants": {"res": ["named_services:use"]}},
    )
    call = entrypoint.service.calls[-1]
    assert call["expected_card_revision"] is None
    assert call["expected_catalog_version"] is None


@pytest.mark.asyncio
async def test_a_malformed_revision_is_a_bad_request_not_a_conflict(entrypoint):
    """A conflict means the editor is stale; a broken field is neither."""
    result = await entrypoint.module.ConnectionHubEntrypoint.delegated_access_update(
        entrypoint.instance,
        data={
            "access_id": "aut_1",
            "resource_grants": {"res": ["named_services:use"]},
            "expected_card_revision": "not-a-number",
        },
    )
    assert result["ok"] is False
    assert result["error"] == "invalid_delegated_access_request"
    assert entrypoint.service.calls == []
