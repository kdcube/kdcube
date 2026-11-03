"""Registered and admin listings expose the same display-name contract."""
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.infra.plugin.bundle_store import BundleEntry, BundlesRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["get_available_bundles", "get_bundles"])
async def test_app_lists_expose_fallback_and_explicit_names(monkeypatch, endpoint):
    registry = BundlesRegistry(bundles={
        "unnamed@1-0": BundleEntry(id="unnamed@1-0", path="/bundles/unnamed"),
        "named@1-0": BundleEntry(id="named@1-0", name="Custom Name", path="/bundles/named"),
    })

    async def load(*args):
        return registry

    async def manifest(**kwargs):
        return None

    monkeypatch.setattr(integrations, "load_registry", load)
    monkeypatch.setattr(integrations, "_get_bundle_manifest", manifest)
    monkeypatch.setattr(integrations, "_get_app_redis", lambda request: object())
    monkeypatch.setattr(integrations, "get_settings", lambda: SimpleNamespace(TENANT="tenant", PROJECT="project"))
    response = await getattr(integrations, endpoint)(request=SimpleNamespace(), tenant="tenant", project="project", session=SimpleNamespace())
    assert response["available_bundles"]["unnamed@1-0"]["name"] == "unnamed@1-0"
    assert response["available_bundles"]["named@1-0"]["name"] == "Custom Name"
