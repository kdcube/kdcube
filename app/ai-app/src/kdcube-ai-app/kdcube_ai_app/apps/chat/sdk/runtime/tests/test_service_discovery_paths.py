# SPDX-License-Identifier: MIT

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.external import service_discovery


def _empty_host_mount_settings() -> SimpleNamespace:
    return SimpleNamespace(
        HOST_KDCUBE_STORAGE_PATH=None,
        HOST_BUNDLES_PATH=None,
        HOST_MANAGED_BUNDLES_PATH=None,
        HOST_BUNDLE_STORAGE_PATH=None,
        HOST_EXEC_WORKSPACE_PATH=None,
    )


def test_translate_managed_bundles_container_path_to_host(monkeypatch):
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.config.get_settings",
        _empty_host_mount_settings,
    )
    monkeypatch.setenv("HOST_MANAGED_BUNDLES_PATH", "/host/managed-bundles")

    translated = service_discovery._translate_container_path_to_host(
        Path("/managed-bundles/demo.bundle/entrypoint.py")
    )

    assert translated == Path("/host/managed-bundles/demo.bundle/entrypoint.py")


def test_get_host_mount_paths_collects_proc_runtime_mount_hints(monkeypatch):
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.config.get_settings",
        _empty_host_mount_settings,
    )
    monkeypatch.setenv("HOST_KDCUBE_STORAGE_PATH", "/host/kdcube-storage")
    monkeypatch.setenv("HOST_BUNDLES_PATH", "/host/bundles")
    monkeypatch.setenv("HOST_MANAGED_BUNDLES_PATH", "/host/managed-bundles")
    monkeypatch.setenv("HOST_BUNDLE_STORAGE_PATH", "/host/bundle-storage")
    monkeypatch.setenv("HOST_EXEC_WORKSPACE_PATH", "/host/exec-workspace")

    mounts = service_discovery.get_host_mount_paths()

    assert mounts.kdcube_storage == "/host/kdcube-storage"
    assert mounts.bundles == "/host/bundles"
    assert mounts.managed_bundles == "/host/managed-bundles"
    assert mounts.bundle_storage == "/host/bundle-storage"
    assert mounts.exec_workspace == "/host/exec-workspace"
    assert mounts.effective_managed_bundles == "/host/managed-bundles"


def test_get_host_mount_paths_falls_back_to_descriptor_backed_settings(monkeypatch):
    monkeypatch.delenv("HOST_KDCUBE_STORAGE_PATH", raising=False)
    monkeypatch.delenv("HOST_BUNDLES_PATH", raising=False)
    monkeypatch.delenv("HOST_MANAGED_BUNDLES_PATH", raising=False)
    monkeypatch.delenv("HOST_BUNDLE_STORAGE_PATH", raising=False)
    monkeypatch.delenv("HOST_EXEC_WORKSPACE_PATH", raising=False)

    settings = SimpleNamespace(
        HOST_KDCUBE_STORAGE_PATH="/settings/kdcube-storage",
        HOST_BUNDLES_PATH="/settings/bundles",
        HOST_MANAGED_BUNDLES_PATH="/settings/managed-bundles",
        HOST_BUNDLE_STORAGE_PATH="/settings/bundle-storage",
        HOST_EXEC_WORKSPACE_PATH="/settings/exec-workspace",
    )

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.config.get_settings",
        lambda: settings,
    )

    mounts = service_discovery.get_host_mount_paths()

    assert mounts.kdcube_storage == "/settings/kdcube-storage"
    assert mounts.bundles == "/settings/bundles"
    assert mounts.managed_bundles == "/settings/managed-bundles"
    assert mounts.bundle_storage == "/settings/bundle-storage"
    assert mounts.exec_workspace == "/settings/exec-workspace"
