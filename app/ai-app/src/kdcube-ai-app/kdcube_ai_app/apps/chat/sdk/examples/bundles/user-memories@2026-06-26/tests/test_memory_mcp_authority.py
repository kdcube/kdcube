# SPDX-License-Identifier: MIT
"""Memory data scope and paid embeddings consume distinct admitted facts."""
from __future__ import annotations

from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kdcube_ai_app.apps.chat.sdk.context.memory import MemoryScope
from kdcube_ai_app.apps.chat.sdk.infra import bundle_operations
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.current_grantor import (
    CurrentGrantorAuthority,
)
from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import (
    load_dynamic_module_for_path,
)
from kdcube_ai_app.apps.chat.sdk.solutions import search_service

REGISTERED = "kdcube:role:registered"
ADMIN = "kdcube:role:super-admin"
ACTOR = "integration:client:owner"


@pytest.fixture
def memory():
    _, module = load_dynamic_module_for_path(Path(__file__).resolve().parents[1] / "entrypoint.py")
    entrypoint = SimpleNamespace(
        logger=SimpleNamespace(log=lambda *_args: None),
        _memory_economics_enabled=lambda: True,
        _memory_identity_family_bundle_id=lambda: "connection-hub@1-0",
        _memory_read_user_ids=AsyncMock(return_value=["owner", "telegram_123"]),
    )
    for name in (
        "_memory_mcp_credential", "_memory_mcp_projection",
        "_memory_mcp_read_user_ids", "_memory_mcp_economics_subject",
        "_memory_mcp_query_embedding",
    ):
        setattr(entrypoint, name, MethodType(getattr(module.UserMemoriesEntrypoint, name), entrypoint))
    return entrypoint, MemoryScope(tenant="tenant", project="project", user_id="owner", bundle_id="memories")


def _request(*, admitted=True):
    request = SimpleNamespace(state=SimpleNamespace(delegated_credential={
        "credential": {
            "credential_kind": "delegated_client_access",
            "issuer_authority_id": "delegated_client", "subject": ACTOR,
            "attrs": {"grantor_subject": "owner", "client_id": "client"},
        },
        "grant_record": {"grantor_authority": {
            "grantor_roles": [ADMIN], "grantor_permissions": ["kdcube:*"],
            "economics_budget_bypass": True,
        }},
    }))
    # A forged data-scope response is descriptive and cannot set paid authority.
    request.state._kdcube_memory_mcp_projection = {
        "ok": True, "memory_user_ids": ["owner"],
        "economics": {"roles": [ADMIN], "budget_bypass": True, "user_id": "other-owner"},
    }
    request.state._delegated_admitted_runtime = {"identity_authority": {
        "user_id": "owner", "economics_user_id": "owner", "platform_user_id": "owner",
        "actor_identity": ACTOR, "roles": [REGISTERED],
        "permissions": ["memories:read"], "budget_bypass": False,
    }}
    if admitted:
        request.state._delegated_current_grantor_authority = CurrentGrantorAuthority(
            tenant="tenant", project="project", subject="owner",
            source_authority="delegated_client", actor=ACTOR,
            target_authority="kdcube.platform", edge_id="card", edge_revision=3,
            roles=(REGISTERED, ADMIN), permissions=("kdcube:*",),
            source="bundle_session_native_user", updated_at=1,
            freshness="last_native_sign_in_or_admin_write",
        )
    return request


@pytest.mark.asyncio
async def test_paid_embedding_uses_admitted_effective_role_not_scope_or_consent(memory, monkeypatch):
    entrypoint, scope = memory
    embed = AsyncMock(return_value=[0.25])
    factory = Mock(return_value=SimpleNamespace(embed_search_query=embed))
    monkeypatch.setattr(search_service, "make_search_model_service", factory)

    assert await entrypoint._memory_mcp_query_embedding(scope, " query ", request=_request()) == [0.25]

    subject = factory.call_args.kwargs["subject"]
    assert subject.user_id == "owner"
    assert subject.roles == (REGISTERED,)
    assert subject.permissions == ("memories:read",)
    assert subject.budget_bypass is False
    assert subject.provenance["actor_user_id"] == ACTOR
    embed.assert_awaited_once_with("query", flow="memory.search")


@pytest.mark.asyncio
@pytest.mark.parametrize("forged_cast", [None, {"roles": [ADMIN]}])
async def test_missing_host_cast_stays_lexical_without_paid_call(memory, monkeypatch, forged_cast):
    entrypoint, scope = memory
    request = _request(admitted=False)
    request.state._delegated_current_grantor_authority = forged_cast
    factory = Mock()
    monkeypatch.setattr(search_service, "make_search_model_service", factory)

    assert await entrypoint._memory_mcp_query_embedding(scope, "query", request=request) is None
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {"ok": False}, {"ok": True, "memory_user_ids": []}])
async def test_failed_delegated_scope_does_not_widen_into_native_family(memory, monkeypatch, result):
    entrypoint, scope = memory
    request = _request()
    del request.state._kdcube_memory_mcp_projection
    resolve = AsyncMock(return_value=result)
    if result is None:
        resolve.side_effect = RuntimeError("scope unavailable")
    monkeypatch.setattr(bundle_operations, "call_bundle_operation", resolve)

    assert await entrypoint._memory_mcp_read_user_ids(scope, request=request) == ["owner"]
    entrypoint._memory_read_user_ids.assert_not_awaited()
    assert set(resolve.call_args.kwargs["data"]) == {"credential"}


@pytest.mark.asyncio
async def test_native_read_keeps_native_family_resolution(memory):
    entrypoint, scope = memory

    assert await entrypoint._memory_mcp_read_user_ids(scope) == ["owner", "telegram_123"]
    entrypoint._memory_read_user_ids.assert_awaited_once_with(scope=scope)
