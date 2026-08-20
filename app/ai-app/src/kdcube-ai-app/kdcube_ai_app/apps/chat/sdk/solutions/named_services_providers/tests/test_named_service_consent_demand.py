# SPDX-License-Identifier: MIT

"""In-chat named-service consumer paths speak the demand-driven consent contract.

A provider consent error (gate 2: connected-account claims inside the realm)
raises the same ask as a direct tool attempt: the pending demand is recorded
and ONE scoped chat consent event goes out. The mapping runs both directions:
the banner lists the UNDERLYING provider claims (mail get → the gmail read
claim) while the turn-off spotlight targets the NAMESPACE entry the user sees
in the composer menu. One contract, every path: the react.pull rehoster path
(server-side object.get) raises the identical demand. The external MCP
surface renders consent from the response itself and stays untouched.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleOperationStreamResult,
    bind_bundle_operation_stream_caller,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import consent_demand
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceResponse,
    register_configured_named_service_artifact_rehosters,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.tools import (
    _raise_named_service_consent_demand,
)


def _consent_error_payload() -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "needs_connected_account_consent",
            "message": "Connect Gmail and approve gmail:read.",
            "details": {
                "reason": "connect_required",
                "consent": {
                    "kind": "delegated_to_kdcube.connected_account",
                    "reason": "connect_required",
                    "provider_id": "google",
                    "connector_app_id": "gmail",
                    "claims": ["gmail:read"],
                    "url": "/widgets/connections_settings?tab=delegated_to_kdcube&provider_id=google&claims=gmail%3Aread",
                    "action_label": "Connect account",
                },
            },
        },
    }


def _record_announces(monkeypatch):
    announced: list[dict[str, Any]] = []

    async def fake_announce(**kwargs):
        announced.append(kwargs)
        return True

    monkeypatch.setattr(consent_demand, "announce_consent_demand", fake_announce)
    return announced


@pytest.mark.asyncio
async def test_named_service_consent_error_raises_the_scoped_demand(monkeypatch):
    announced = _record_announces(monkeypatch)

    await _raise_named_service_consent_demand(
        _consent_error_payload(), namespace="mail", tool_name="get_object",
    )

    assert len(announced) == 1
    demand = announced[0]
    # Underlying provider claims travel to the broker/plan…
    assert demand["provider_id"] == "google"
    assert demand["claims"] == ["gmail:read"]
    # …while the demand (and the banner's turn-off spotlight) names the
    # NAMESPACE entry the user sees in the composer menu.
    assert demand["tool_name"] == "mail"
    banner = demand["payload"]
    assert banner["error"]["code"] == "needs_connected_account_consent"
    assert banner["consent"]["claims"] == ["gmail:read"]
    assert banner["consent"]["tools"] == ["mail"]
    assert "tab=delegated_to_kdcube" in banner["consent"]["url"]


@pytest.mark.asyncio
async def test_agent_grant_demand_raises_the_agent_banner_on_the_workspace_side(monkeypatch):
    # A per-account agent-grant demand: the tool ran in a provider bundle with no
    # chat lane (comm_bound=False there), so the banner MUST be raised here on the
    # workspace side. Detected by agent_client_id, emitted directly via get_comm.
    import kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx as comm_ctx

    events: list[dict[str, Any]] = []

    class _Comm:
        def event(self, **kwargs):
            events.append(kwargs)

    monkeypatch.setattr(comm_ctx, "get_comm", lambda: _Comm())

    consent = {
        "kind": "delegated_agent_grant",
        "agent_client_id": "kdcube-agent:workspace@1-0:main",
        "resource": "*/kdcube-services@1-0/public/mcp/named_services*",
        "claims": ["gmail:send"],
        "account_id": "google_50e4a2de220d1e26",
        "url": "https://hub/w?tab=delegated_by_kdcube&pending_agent_grant=1&account_claim=gmail%3Asend",
    }
    # The REAL shape a mail forward/send produces: tool_error_response nests the
    # consent under error.details.consent with the agent_account_binding_required code.
    payload = {
        "ok": False,
        "error": {
            "code": "agent_account_binding_required",
            "message": "This needs your permission to use gmail:send on account Elena Viter.",
            "details": {"consent": dict(consent)},
        },
    }
    await _raise_named_service_consent_demand(payload, namespace="mail", tool_name="object_action")

    assert len(events) == 1
    data = events[0]["data"]
    # The reducer's consent path keys on this code; agent_client_id routes it to
    # the agent-grant banner (Delegated by KDCube), not a connect banner.
    assert data["error"]["code"] == "needs_connected_account_consent"
    assert data["consent"]["agent_client_id"] == "kdcube-agent:workspace@1-0:main"
    assert "tab=delegated_by_kdcube" in data["consent"]["url"]
    assert data["consent"]["tools"] == ["mail"]


@pytest.mark.asyncio
async def test_scoped_namespace_maps_to_its_menu_entry(monkeypatch):
    announced = _record_announces(monkeypatch)
    await _raise_named_service_consent_demand(
        _consent_error_payload(), namespace="mail:inbox", tool_name="search_objects",
    )
    assert announced[0]["tool_name"] == "mail"
    assert announced[0]["payload"]["consent"]["tools"] == ["mail"]


@pytest.mark.asyncio
async def test_plain_errors_and_successes_raise_nothing(monkeypatch):
    announced = _record_announces(monkeypatch)

    await _raise_named_service_consent_demand({"ok": True}, namespace="mail", tool_name="get_object")
    await _raise_named_service_consent_demand(
        {"ok": False, "error": {"code": "named_service_not_found", "message": "…"}},
        namespace="mail",
        tool_name="get_object",
    )
    assert announced == []


@pytest.mark.asyncio
async def test_pull_of_a_slack_ref_without_claims_raises_the_demand_then_succeeds(tmp_path, monkeypatch):
    """react.pull is THE reading path for provider-backed namespaces, so its
    server-side object.get speaks the full consent contract: an unmet claim
    propagates the structured consent error into the pull result AND records
    exactly one scoped demand (banner spotlights the `slack` menu entry);
    after consent the same pull materializes the artifact."""
    announced = _record_announces(monkeypatch)
    slack_ref = "slack:acct_1:channel:C0123"
    granted = {"value": False}

    async def _stream_caller(call):
        assert call.data["operation"] == "object.get"
        assert call.data["context"]["source"] == "react.pull"
        assert call.data["object_ref"] == slack_ref
        if not granted["value"]:
            return NamedServiceResponse.error_response(
                code="needs_connected_account_consent",
                message="Connect Slack and approve slack:history.",
                status=403,
                details={
                    "reason": "connect_required",
                    "consent": {
                        "kind": "delegated_to_kdcube.connected_account",
                        "reason": "connect_required",
                        "provider_id": "slack",
                        "connector_app_id": "demo",
                        "claims": ["slack:history"],
                        "url": "/widgets/connections_settings?tab=delegated_to_kdcube&provider_id=slack&claims=slack%3Ahistory",
                        "action_label": "Connect account",
                    },
                },
                namespace="slack",
                object_ref=slack_ref,
            )

        async def _chunks():
            yield b"channel history"

        return BundleOperationStreamResult(
            chunks=_chunks(),
            filename="history.md",
            media_type="text/markdown",
            response=NamedServiceResponse.ok_response(
                namespace="slack", object_ref=slack_ref,
            ).to_dict(),
        )

    event_sources = EventSourceSubsystem()
    count = register_configured_named_service_artifact_rehosters(
        event_sources,
        tenant="tenant-a",
        project="project-a",
        namespaces={
            "slack": {
                "pull": {"operation": "object.get"},
                "providers": [
                    {
                        "transport": "bundle_operation",
                        "bundle_id": "kdcube-services@1-0",
                        "provider": "slack",
                        "operations": ["object.get"],
                    }
                ],
            }
        },
    )
    assert count == 1

    ctx_browser = SimpleNamespace(runtime_ctx=SimpleNamespace(turn_id="turn_pull"))
    with bind_bundle_operation_stream_caller(_stream_caller):
        blocked = await event_sources.rehost_namespace_ref(
            slack_ref, ctx_browser=ctx_browser, outdir=tmp_path,
        )

    # The structured consent story rides the pull's error result…
    assert blocked["materialized"] == []
    error = blocked["errors"][0]["error"]
    assert error["code"] == "needs_connected_account_consent"
    assert error["details"]["consent"]["claims"] == ["slack:history"]
    assert "tab=delegated_to_kdcube" in error["details"]["consent"]["url"]
    # …and exactly one scoped demand went out, identical to a direct attempt.
    assert len(announced) == 1
    demand = announced[0]
    assert demand["provider_id"] == "slack"
    assert demand["claims"] == ["slack:history"]
    assert demand["tool_name"] == "slack"
    assert demand["payload"]["consent"]["tools"] == ["slack"]
    assert demand["payload"]["tools"] == ["react.pull"]

    granted["value"] = True
    with bind_bundle_operation_stream_caller(_stream_caller):
        result = await event_sources.rehost_namespace_ref(
            slack_ref, ctx_browser=ctx_browser, outdir=tmp_path,
        )

    assert result["errors"] == []
    materialized = result["materialized"][0]
    assert materialized["logical_path"].startswith("conv:fi:")
    target = tmp_path / "workdir" / materialized["physical_path"]
    assert target.read_bytes() == b"channel history"
    # The successful pull raised no further demand.
    assert len(announced) == 1


@pytest.mark.asyncio
async def test_menu_inventory_renders_even_when_coverage_hangs(monkeypatch):
    """The '+' menu must render even when coverage computation stalls: the
    decoration runs under a budget; rows simply omit consent state and a
    warning names the miss."""
    import asyncio
    import time as clock
    from types import SimpleNamespace

    from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
    from kdcube_ai_app.apps.chat.sdk.runtime import tool_config as tool_config_mod
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube import consent_demand as cd
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.models import ToolClaimPolicy

    policy = ToolClaimPolicy.from_config("slack.search_slack", {
        "connected_accounts": [
            {"provider_id": "slack", "connector_app_id": "demo", "claims": ["slack:search"]},
        ],
    })
    monkeypatch.setattr(
        tool_config_mod,
        "agent_tool_config_from_bundle_props",
        lambda *a, **k: SimpleNamespace(tool_claim_policies=[policy]),
    )

    async def hanging_coverage(**kwargs):
        await asyncio.sleep(30)
        return {}

    monkeypatch.setattr(cd, "claim_coverage_for_policies", hanging_coverage)

    logged: list[tuple[str, str]] = []
    stub = SimpleNamespace(
        bundle_props={},
        logger=SimpleNamespace(log=lambda msg, level=None, **kw: logged.append((str(level), str(msg)))),
        CLAIM_COVERAGE_BUDGET_SECONDS=0.2,
        _agent_selection_identity=lambda: {"user_id": "u1", "bundle_id": "b1"},
        _bundle_root=lambda: None,
    )

    catalog = {"tools": [{"alias": "slack", "tools": [{"name": "search_slack"}]}], "named_services": []}
    started = clock.monotonic()
    out = await BaseEntrypoint._attach_claim_coverage(stub, dict(catalog), "main")
    elapsed = clock.monotonic() - started

    assert elapsed < 2.0, "the budget bounded the coverage decoration"
    assert out["tools"][0].get("consent") is None
    assert out["tools"][0]["tools"][0].get("consent") is None
    assert any("without consent state" in msg for _lvl, msg in logged)


@pytest.mark.asyncio
async def test_a_boundary_refusal_asks_for_the_operation_it_was_refused(monkeypatch):
    """The card holds the claims; only its own boundary excludes the operation.

    A claims-shaped demand asks for consent that is already given, deduplicates
    away, and leaves the agent telling the user to wait for a permission they
    granted. The demand names the operation, and approval grants that one.
    """
    import kdcube_ai_app.apps.chat.sdk.infra.bundle_operations as bundle_operations
    import kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx as comm_ctx
    import kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_consent as mcp_consent
    from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.tools import (
        _agent_grant_gate,
    )

    resource = "*/kdcube-services@1-0/public/mcp/named_services*"
    monkeypatch.setattr(
        comm_ctx, "get_current_user_identity",
        lambda: {"user_id": "u1", "bundle_id": "workspace@1-0", "tenant_id": "t", "project_id": "p"},
    )
    monkeypatch.setattr(
        comm_ctx, "get_current_request_context",
        lambda: SimpleNamespace(event=SimpleNamespace(agent_id="main")),
    )

    state = {
        "governed": True,
        "granted": False,
        "missing_claims": [],
        "claims": ["memories:read", "named_services:use"],
        "resource": resource,
        "client_id": "kdcube-agent:workspace@1-0:main",
        "account_scope": {},
        "not_granted": {
            "ok": False,
            "error": {"code": "delegated_capability_not_granted", "retryable": False},
            "ret": {"requested_capability": {"namespace": "mem", "operation": "object.search"}},
        },
    }

    async def fake_call(**kwargs):
        return SimpleNamespace(value={"ok": True, "ret": {"object": state}})

    monkeypatch.setattr(bundle_operations, "call_bundle_named_service", fake_call)

    announced: list[Any] = []

    async def fake_announce(consent):
        announced.append(consent)

    monkeypatch.setattr(mcp_consent, "announce_agent_consent", fake_announce)

    result = await _agent_grant_gate("mem", "object.search", "named_services_search")

    assert result["error"]["code"] == "delegated_capability_not_granted"
    grant = result["consent"]["grant"]["payload"]
    assert grant["named_service_operations"] == {"mem": ["object.search"]}
    # No claim is missing, so the demand asks for the operation alone.
    assert grant["claims"] == []
    assert len(announced) == 1
    assert announced[0].consent["operation"] == "object.search"
    assert announced[0].consent["namespace"] == "mem"
    assert "object.search" in announced[0].agent_message
