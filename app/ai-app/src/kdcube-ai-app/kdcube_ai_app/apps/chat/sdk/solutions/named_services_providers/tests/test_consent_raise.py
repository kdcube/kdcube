# SPDX-License-Identifier: MIT

"""The consent banner rises for the connect hint, not only for errors.

Surfaced case: a user with ZERO connected accounts got no Connection Hub
banner — the agent's account listing succeeded (an empty list is not an
error) and shipped only a connect hint in ``ret.extra.consent``, which the
demand raiser ignored. The model then hand-wrote a markdown link instead of
the actionable card.
"""

from __future__ import annotations

from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.consent import (
    raise_named_service_consent_demand,
)


@pytest.fixture()
def announced(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def _announce(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.consent_demand.announce_consent_demand",
        _announce,
    )
    return calls


@pytest.mark.asyncio
async def test_connect_hint_on_successful_listing_raises_the_banner(announced):
    payload = {
        "ok": True,
        "ret": {
            "items": [],
            "extra": {
                "kind": "accounts",
                "count": 0,
                "consent": {
                    "kind": "delegated_to_kdcube.connected_account",
                    "reason": "connect_required",
                    "provider_id": "slack",
                    "connector_app_id": "demo",
                    "claims": [],
                    "url": "/api/integrations/bundles/t/p/connection-hub%401-0/widgets/connections_settings?tab=delegated_to_kdcube&provider_id=slack",
                    "action_label": "Connect account",
                },
            },
        },
    }
    await raise_named_service_consent_demand(
        payload, namespace="slack", tool_name="named_services.list_objects"
    )
    assert len(announced) == 1
    assert announced[0]["provider_id"] == "slack"
    banner = announced[0]["payload"]
    assert banner["error"]["code"] == "needs_connected_account_consent"
    assert banner["error"]["message"]
    assert banner["consent"]["reason"] == "connect_required"


@pytest.mark.asyncio
async def test_plain_success_raises_nothing(announced):
    await raise_named_service_consent_demand(
        {"ok": True, "ret": {"items": [{"id": "acc-1"}], "extra": {"count": 1}}},
        namespace="slack",
        tool_name="named_services.list_objects",
    )
    assert announced == []


@pytest.mark.asyncio
async def test_consent_error_still_raises_the_banner(announced):
    payload = {
        "ok": False,
        "error": {
            "code": "needs_connected_account_consent",
            "message": "Slack account needs the files write claim.",
            "details": {
                "consent": {
                    "reason": "claim_upgrade_required",
                    "provider_id": "slack",
                    "connector_app_id": "demo",
                    "claims": ["slack:files:write"],
                }
            },
        },
    }
    await raise_named_service_consent_demand(
        payload, namespace="slack", tool_name="named_services.object_action"
    )
    assert len(announced) == 1
    assert announced[0]["claims"] == ["slack:files:write"]


@pytest.mark.asyncio
async def test_other_errors_raise_nothing(announced):
    await raise_named_service_consent_demand(
        {"ok": False, "error": {"code": "slack_api_error", "message": "boom"}},
        namespace="slack",
        tool_name="named_services.object_action",
    )
    assert announced == []
