# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from types import SimpleNamespace

import pytest

from connection_hub.delegated_credentials.catalog.models import CatalogDocument
from kdcube_ai_app.apps.chat.proc.app_deployment import delegated_catalog
from kdcube_ai_app.infra.plugin.bundle_store import BundleEntry, BundlesRegistry


NAMED_SERVICES_RESOURCE = "*/public/mcp/named_services*"


def _registry(tmp_path) -> BundlesRegistry:
    return BundlesRegistry(
        bundles={
            "connection-hub@1-0": BundleEntry(
                id="connection-hub@1-0",
                path=str(tmp_path / "connection-hub"),
                module="entrypoint",
            ),
            "example@1-0": BundleEntry(
                id="example@1-0",
                path=str(tmp_path / "example"),
                module="entrypoint",
            ),
        }
    )


def _base_props() -> dict:
    return {
        "connections": {
            "delegated_credentials": {
                "oauth": {
                    "capabilities": [],
                    "resources": [
                        {
                            "resource": NAMED_SERVICES_RESOURCE,
                            "named_services": {"namespaces": {}},
                        }
                    ],
                }
            }
        }
    }


def _app_props() -> dict:
    return {
        "delegated_catalog": {
            "version": "1",
            "capabilities": [{"grant": "example:read"}],
            "resources": [
                {
                    "resource": "*/public/mcp/example*",
                    "tools": {"object.get": {"grants": ["example:read"]}},
                }
            ],
            "named_service_namespaces": [
                {
                    "resource": NAMED_SERVICES_RESOURCE,
                    "namespaces": {"example": {"label": "Example"}},
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_authoritative_assembly_uses_effective_hub_props_and_all_apps(
    monkeypatch,
    tmp_path,
):
    registry = _registry(tmp_path)
    hub_entry = registry.bundles["connection-hub@1-0"]

    async def bundle_props(**kwargs):
        return (
            _base_props()
            if kwargs["bundle_id"] == "connection-hub@1-0"
            else _app_props()
        )

    async def resolved(_registry):
        return hub_entry

    async def effective(**kwargs):
        return kwargs["descriptor_props"]

    async def storage(**kwargs):
        del kwargs
        return tmp_path / "storage"

    monkeypatch.setattr(delegated_catalog, "get_bundle_props_from_authority", bundle_props)
    monkeypatch.setattr(delegated_catalog, "_resolved_connection_hub_entry", resolved)
    monkeypatch.setattr(delegated_catalog, "_connection_hub_effective_props", effective)
    monkeypatch.setattr(delegated_catalog, "resolve_app_storage_root", storage)

    context = await delegated_catalog.assemble_authoritative_delegated_catalog(
        registry=registry,
        tenant="tenant-a",
        project="project-a",
        pg_pool=object(),
        redis=object(),
    )

    assert context is not None
    assert context.assembly.contributors == ("example@1-0",)
    oauth = context.assembly.connections["delegated_credentials"]["oauth"]
    assert oauth["capabilities"] == [{"grant": "example:read"}]
    assert oauth["resources"][0]["named_services"]["namespaces"] == {
        "example": {"label": "Example"}
    }


@pytest.mark.asyncio
async def test_authoritative_check_compares_against_request_serving_catalog(
    monkeypatch,
    tmp_path,
):
    registry = _registry(tmp_path)
    expected_connections = {
        "delegated_credentials": {
            "oauth": {"resources": [{"resource": "*/expected*"}]}
        }
    }
    context = delegated_catalog.AuthoritativeCatalogAssembly(
        assembly=delegated_catalog.DelegatedCatalogAssembly(
            connections=expected_connections,
            contributors=("example@1-0",),
        ),
        connection_hub_entry=registry.bundles["connection-hub@1-0"],
        storage_root=tmp_path,
    )

    async def assemble(**kwargs):
        del kwargs
        return context

    class Resolver:
        def __init__(self, **kwargs):
            del kwargs

        async def resolve_active(self):
            return CatalogDocument.build(
                {
                    "delegated_credentials": {
                        "oauth": {"resources": [{"resource": "*/served*"}]}
                    }
                }
            )

    monkeypatch.setattr(
        delegated_catalog,
        "assemble_authoritative_delegated_catalog",
        assemble,
    )
    monkeypatch.setattr(delegated_catalog, "DelegatedCatalogResolver", Resolver)

    result = await delegated_catalog.check_authoritative_delegated_catalog(
        registry=registry,
        tenant="tenant-a",
        project="project-a",
        pg_pool=object(),
        redis=object(),
    )

    assert result["status"] == "drift"
    assert result["in_sync"] is False
    assert {(row["kind"], row["path"]) for row in result["differences"]} == {
        (
            "missing",
            "connections.delegated_credentials.oauth.resources.*/expected*",
        ),
        (
            "extra",
            "connections.delegated_credentials.oauth.resources.*/served*",
        ),
    }


@pytest.mark.asyncio
async def test_publish_rereads_complete_assembly_inside_catalog_lock(
    monkeypatch,
    tmp_path,
):
    registry = _registry(tmp_path)
    calls = 0

    async def assemble(**kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        connections = {
            "delegated_credentials": {
                "oauth": {"resources": [{"resource": f"*/generation-{calls}*"}]}
            }
        }
        return delegated_catalog.AuthoritativeCatalogAssembly(
            assembly=delegated_catalog.DelegatedCatalogAssembly(
                connections=connections,
                contributors=("example@1-0",),
            ),
            connection_hub_entry=registry.bundles["connection-hub@1-0"],
            storage_root=tmp_path,
        )

    async def ensure(**kwargs):
        assert kwargs["connections"]["delegated_credentials"]["oauth"]["resources"][0][
            "resource"
        ] == "*/generation-1*"
        reread = await kwargs["reread"]()
        assert reread["delegated_credentials"]["oauth"]["resources"][0][
            "resource"
        ] == "*/generation-2*"
        return SimpleNamespace(version="v2", created=True)

    monkeypatch.setattr(
        delegated_catalog,
        "assemble_authoritative_delegated_catalog",
        assemble,
    )
    monkeypatch.setattr(delegated_catalog, "ensure_delegated_catalog", ensure)

    result = await delegated_catalog.publish_authoritative_delegated_catalog(
        registry=registry,
        tenant="tenant-a",
        project="project-a",
        pg_pool=object(),
        redis=object(),
        reason="test",
    )

    assert result is not None
    assert result.version == "v2"
    assert calls == 2
