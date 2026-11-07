# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials import (
    automation_access,
)
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint


@pytest.mark.asyncio
@pytest.mark.parametrize("covered", [False, True])
async def test_delegated_mcp_catalog_opens_hosted_card_without_direct_grant(
    monkeypatch, covered,
):
    reads = []

    async def read_grant(redis, **kwargs):
        reads.append((redis, kwargs))
        return {"access_id": "agent-card"} if covered else None

    monkeypatch.setattr(automation_access, "read_agent_grant_record", read_grant)
    redis = object()
    owner = SimpleNamespace(
        redis=redis,
        comm_context=SimpleNamespace(actor=SimpleNamespace(
            tenant_id="tenant-a", project_id="project-a",
        )),
        _agent_selection_identity=lambda: {"user_id": "user-a"},
        _named_services_bundle_id=lambda: "workspace@1-0",
        DELEGATED_MCP_CONSENT_BUDGET_SECONDS=2.5,
        logger=SimpleNamespace(log=lambda *_args: None),
    )
    resource = "https://example.test/mcp/mem"
    catalog = {"mcp": [{
        "alias": "memory", "delegated": True,
        "resource": resource, "claims": ["memories:read"],
    }]}

    result = await BaseEntrypoint._attach_delegated_mcp_consent(owner, catalog, "main")

    consent = result["mcp"][0]["consent"]
    assert consent["agent_card_consent"] is True
    assert "grant" not in consent
    assert consent["agent_client_id"] == "kdcube-agent:workspace@1-0:main"
    assert consent["resource"] == resource
    assert consent["claims"] == ["memories:read"]
    assert consent["covered"] is covered
    assert consent["unmet"] == ([] if covered else ["memories:read"])
    assert reads == [(redis, {
        "tenant": "tenant-a", "project": "project-a", "grantor_subject": "user-a",
        "client_id": "kdcube-agent:workspace@1-0:main", "resources": [resource],
    })]
