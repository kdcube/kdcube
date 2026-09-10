# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.plugin import bundle_loader
from kdcube_ai_app.infra.plugin.bundle_loader import BundleSpec


@pytest.mark.asyncio
async def test_deprovision_loader_uses_sync_loader_without_on_load_hook(monkeypatch) -> None:
    config = SimpleNamespace()
    comm_context = SimpleNamespace()
    workflow = object()
    module = object()
    calls: dict[str, object] = {}

    async def _config(spec, bundle_spec, **kwargs):
        calls["config"] = (spec, bundle_spec, kwargs)
        return config, comm_context

    def _load(spec, supplied_config, **kwargs):
        calls["load"] = (spec, supplied_config, kwargs)
        return workflow, module

    async def _load_with_lifecycle(*args, **kwargs):
        del args, kwargs
        raise AssertionError("deprovision must not run on_bundle_load")

    monkeypatch.setattr(bundle_loader, "_lifecycle_workflow_config", _config)
    monkeypatch.setattr(bundle_loader, "get_workflow_instance", _load)
    monkeypatch.setattr(bundle_loader, "get_workflow_instance_async", _load_with_lifecycle)

    spec = BundleSpec(id="app@1-0", path="/installed/app", module="entrypoint")
    bundle_spec = SimpleNamespace(id="app@1-0")
    result = await bundle_loader.load_bundle_for_deprovision_async(
        spec,
        bundle_spec,
        tenant="tenant-a",
        project="project-a",
        pg_pool="pool",
        redis="redis",
    )

    assert result == (workflow, module)
    assert calls["config"] == (
        spec,
        bundle_spec,
        {"tenant": "tenant-a", "project": "project-a"},
    )
    assert calls["load"] == (
        spec,
        config,
        {"comm_context": comm_context, "pg_pool": "pool", "redis": "redis"},
    )
