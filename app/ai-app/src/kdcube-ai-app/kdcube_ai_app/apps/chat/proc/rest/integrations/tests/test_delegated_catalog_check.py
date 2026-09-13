from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.catalog.assembly import (
    CatalogAssemblyError,
)


def _request(*, host: str = "127.0.0.1") -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        app=SimpleNamespace(
            state=SimpleNamespace(
                redis_async=object(),
                pg_pool=object(),
            )
        ),
    )


@pytest.mark.asyncio
async def test_internal_catalog_check_compares_authority_with_active_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    registry = object()
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="demo", PROJECT="project"),
    )

    async def load_registry(_redis, tenant, project):
        assert tenant == "demo"
        assert project == "project"
        return registry

    async def check(**kwargs):
        calls.append(kwargs)
        return {
            "schema": "kdcube.delegated-catalog-check.v1",
            "status": "in_sync",
            "in_sync": True,
            "contributors": ["problem-board@1-0"],
            "differences": [],
        }

    monkeypatch.setattr(integrations, "load_registry", load_registry)
    monkeypatch.setattr(integrations, "check_authoritative_delegated_catalog", check)

    result = await integrations.internal_delegated_catalog_check(
        integrations.BundleCatalogCheckRequest(),
        request,
    )

    assert result["status"] == "in_sync"
    assert result["tenant"] == "demo"
    assert result["project"] == "project"
    assert result["contributors"] == ["problem-board@1-0"]
    assert calls == [
        {
            "registry": registry,
            "tenant": "demo",
            "project": "project",
            "pg_pool": request.app.state.pg_pool,
            "redis": request.app.state.redis_async,
        }
    ]


@pytest.mark.asyncio
async def test_internal_catalog_check_reports_duplicate_resource_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(TENANT="demo", PROJECT="project"),
    )

    async def load_registry(*_args):
        return object()

    async def check(**_kwargs):
        raise CatalogAssemblyError(
            "duplicate_resource",
            "resource 'shared' is declared by both 'app-a' and 'app-b'",
            path="connections.delegated_credentials.oauth.resources[shared]",
            owners=("app-a", "app-b"),
        )

    monkeypatch.setattr(integrations, "load_registry", load_registry)
    monkeypatch.setattr(integrations, "check_authoritative_delegated_catalog", check)

    result = await integrations.internal_delegated_catalog_check(
        integrations.BundleCatalogCheckRequest(),
        _request(),
    )

    assert result["status"] == "invalid"
    assert result["in_sync"] is False
    assert result["differences"] == [
        {
            "kind": "declaration_error",
            "code": "duplicate_resource",
            "message": "resource 'shared' is declared by both 'app-a' and 'app-b'",
            "path": "connections.delegated_credentials.oauth.resources[shared]",
            "owners": ["app-a", "app-b"],
        }
    ]


@pytest.mark.asyncio
async def test_internal_catalog_check_is_localhost_only() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await integrations.internal_delegated_catalog_check(
            integrations.BundleCatalogCheckRequest(),
            _request(host="203.0.113.8"),
        )

    assert exc_info.value.status_code == 403
