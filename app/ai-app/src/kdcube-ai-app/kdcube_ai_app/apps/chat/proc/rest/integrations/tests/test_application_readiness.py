# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.infra.plugin.app_readiness import (
    ApplicationNotReadyError,
    ApplicationReadinessMode,
    ApplicationReadinessRegistry,
    DesiredApplicationState,
)
from kdcube_ai_app.infra.plugin.bundle_store import BundlesRegistry


def _request(body: bytes) -> Request:
    delivered = False

    async def _receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        _receive,
    )


def _not_ready_error() -> ApplicationNotReadyError:
    registry = ApplicationReadinessRegistry()
    registry.replace_desired(
        tenant="tenant-a",
        project="project-a",
        applications={
            "app@1-0": DesiredApplicationState(
                generation="private-generation",
                readiness=ApplicationReadinessMode.INDEPENDENT,
            )
        },
    )
    snapshot = registry.snapshot(
        tenant="tenant-a",
        project="project-a",
        application_id="app@1-0",
    )
    assert snapshot is not None
    return ApplicationNotReadyError(snapshot)


@pytest.mark.asyncio
async def test_mcp_unavailable_application_returns_structured_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _unavailable(**kwargs):
        del kwargs
        raise _not_ready_error()

    monkeypatch.setattr(integrations, "_call_bundle_mcp_inner", _unavailable)
    request = _request(
        b'{"jsonrpc":"2.0","id":"call-1","method":"tools/list","params":{}}'
    )

    response = await integrations._call_bundle_mcp_limited(
        tenant="tenant-a",
        project="project-a",
        bundle_id="app@1-0",
        request=request,
        endpoint_alias="tools",
        route="operations",
        mcp_path="",
    )

    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == "call-1"
    assert payload["error"]["code"] == -32001
    assert payload["error"]["data"] == {
        "type": "application_not_ready",
        "application_id": "app@1-0",
        "state": "preparing",
        "retryable": True,
    }
    assert "generation" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_authority_discovery_publication_uses_lifecycle_for_local_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[BundlesRegistry] = []
    direct: list[tuple[str, str]] = []

    class _Lifecycle:
        async def publish_authority_discovery_change(
            self,
            registry: BundlesRegistry,
        ) -> None:
            published.append(registry)

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="tenant-a", PROJECT="project-a"),
    )

    async def _direct_reconcile(**kwargs):
        direct.append((kwargs["tenant"], kwargs["project"]))

    monkeypatch.setattr(
        "kdcube_ai_app.infra.plugin.authority_discovery.reconcile_authority_discovery",
        _direct_reconcile,
    )
    request = _request(b"")
    request.scope["app"] = SimpleNamespace(
        state=SimpleNamespace(
            application_lifecycle=_Lifecycle(),
            redis_async=object(),
        ),
    )
    registry = BundlesRegistry()

    await integrations._publish_authority_discovery_change(
        request,
        tenant="tenant-a",
        project="project-a",
        registry=registry,
    )
    await integrations._publish_authority_discovery_change(
        request,
        tenant="other-tenant",
        project="project-a",
        registry=registry,
    )

    assert published == [registry]
    assert direct == [("other-tenant", "project-a")]
