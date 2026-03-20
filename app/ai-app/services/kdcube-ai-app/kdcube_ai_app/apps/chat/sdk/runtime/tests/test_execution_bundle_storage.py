# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.execution import _resolve_bundle_storage_dir
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx


class _Logger:
    def __init__(self) -> None:
        self.messages = []

    def log(self, msg, level="INFO"):
        self.messages.append((level, str(msg)))


def test_resolve_bundle_storage_dir_uses_runtime_ctx_value(tmp_path):
    bundle_storage = tmp_path / "bundle-storage" / "tenant" / "project" / "react.doc__main"
    bundle_storage.mkdir(parents=True)

    runtime_ctx = RuntimeCtx(bundle_storage=str(bundle_storage))
    tool_manager = SimpleNamespace(comm=SimpleNamespace(tenant="tenant", project="project"), bundle_spec=None)
    logger = _Logger()

    resolved = _resolve_bundle_storage_dir(
        runtime_ctx=runtime_ctx,
        tool_manager=tool_manager,
        logger=logger,
    )

    assert resolved == bundle_storage.resolve()


def test_resolve_bundle_storage_dir_derives_from_bundle_spec(tmp_path, monkeypatch):
    expected = tmp_path / "derived" / "tenant" / "project" / "react.doc__main"

    def _fake_storage_for_spec(*, spec, tenant, project, ensure):
        assert spec.id == "react.doc@2026-03-02-22-10"
        assert tenant == "tenant"
        assert project == "project"
        assert ensure is True
        expected.mkdir(parents=True, exist_ok=True)
        return expected

    monkeypatch.setattr(
        "kdcube_ai_app.infra.plugin.bundle_storage.storage_for_spec",
        _fake_storage_for_spec,
    )

    runtime_ctx = RuntimeCtx(tenant="tenant", project="project", bundle_storage=None)
    tool_manager = SimpleNamespace(
        comm=SimpleNamespace(tenant="tenant", project="project"),
        bundle_spec=SimpleNamespace(id="react.doc@2026-03-02-22-10"),
    )
    logger = _Logger()

    resolved = _resolve_bundle_storage_dir(
        runtime_ctx=runtime_ctx,
        tool_manager=tool_manager,
        logger=logger,
    )

    assert resolved == expected.resolve()
    assert any("Derived missing bundle storage dir" in msg for _, msg in logger.messages)
