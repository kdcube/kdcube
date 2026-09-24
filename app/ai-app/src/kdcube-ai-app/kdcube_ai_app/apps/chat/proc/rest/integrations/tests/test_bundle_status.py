# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.proc.app_deployment.models import AppStaticSurfaceManifest
from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.infra.plugin import bundle_registry
from kdcube_ai_app.infra.plugin.bundle_store import BundleEntry, BundlesRegistry


@pytest.mark.asyncio
async def test_live_bundle_status_reads_loaded_and_widget_receipts_without_importing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    source = {
        "mode": "snapshot",
        "commit": "abc123",
        "path": str(tmp_path),
        "mounted_path": "/bundles/app@1-0",
    }
    lifecycle = SimpleNamespace(
        loaded_source_diagnostic=lambda bundle_id: {
            "source": source,
            "application_generation": "app-generation-1",
            "path": str(tmp_path),
        }
        if bundle_id == "app@1-0"
        else None
    )
    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                redis_async=object(),
                application_lifecycle=lifecycle,
            )
        ),
    )
    registry = BundlesRegistry(
        default_bundle_id="app@1-0",
        bundles={
            "app@1-0": BundleEntry(
                id="app@1-0",
                path="/bundles/app@1-0",
                module="entrypoint",
                singleton=True,
            )
        },
    )

    async def _load_registry(*args):
        del args
        return registry

    async def _load_widget_manifest(_storage_root):
        return AppStaticSurfaceManifest(
            tenant="tenant-a",
            project="project-a",
            bundle_id="app@1-0",
            source_generation="source-generation-1",
            application_generation="app-generation-1",
            props_fingerprint="props-1",
            deployment_signature="deployment-1",
            generated_at="2026-09-24T00:00:00Z",
            source=source,
        )

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(
            TENANT="tenant-a",
            PROJECT="project-a",
            INSTANCE_ID="proc-1",
        ),
    )
    monkeypatch.setattr(integrations, "load_registry", _load_registry)
    monkeypatch.setattr(integrations, "load_deployment_manifest", _load_widget_manifest)
    monkeypatch.setattr(
        integrations,
        "load_bundle_manifest",
        lambda *args, **kwargs: pytest.fail("status must not import bundle code"),
    )
    monkeypatch.setattr(
        bundle_registry,
        "get_all",
        lambda: {"app@1-0": {"id": "app@1-0", "path": str(tmp_path), "source": source}},
    )

    result = await integrations.internal_bundle_status(
        integrations.BundleStatusRequest(bundle_id="app@1-0"),
        request,
    )

    assert result["loaded"] is True
    assert result["source"] == source
    assert result["process"] == {
        "component": "chat-proc",
        "instance_id": "proc-1",
        "pid": result["process"]["pid"],
        "loaded": {
            "source": source,
            "application_generation": "app-generation-1",
            "path": str(tmp_path),
        },
    }
    assert result["widget"]["source"] == source
    assert result["widget"]["deployment_signature"] == "deployment-1"
    assert result["path_exists"] is True
