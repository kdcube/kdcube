from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.data_bus import data_bus_handler as sdk_data_bus_handler
from kdcube_ai_app.infra.plugin.bundle_loader import (
    bundle_entrypoint,
    data_bus_handler,
    discover_bundle_interface_manifest,
)


def test_data_bus_handler_is_discovered_in_bundle_manifest():
    @bundle_entrypoint(name="Data Bundle")
    class DataBundle:
        @data_bus_handler(
            subject="task_tracker.canvas.patch",
            partition_by="object_ref",
            ordering="serial_per_partition",
            idempotency="required",
            user_types=("registered",),
        )
        async def handle_canvas_patch(self, ctx, message):
            return {"status": "ok"}

    manifest = discover_bundle_interface_manifest(DataBundle, bundle_id="task-tracker@1-0")

    assert manifest.bundle_id == "task-tracker@1-0"
    assert len(manifest.data_bus_handlers) == 1
    spec = manifest.data_bus_handlers[0]
    assert spec.method_name == "handle_canvas_patch"
    assert spec.subject == "task_tracker.canvas.patch"
    assert spec.partition_by == "object_ref"
    assert spec.ordering == "serial_per_partition"
    assert spec.idempotency == "required"
    assert spec.user_types == ("registered",)


def test_data_bus_handler_is_exported_from_sdk():
    assert sdk_data_bus_handler is not None
    assert callable(sdk_data_bus_handler)


def test_data_bus_handler_rejects_invalid_serial_partition_contract():
    with pytest.raises(ValueError, match="serial_per_partition requires"):

        @data_bus_handler(
            subject="task_tracker.canvas.patch",
            ordering="serial_per_partition",
        )
        async def invalid_handler(ctx, message):
            return None


def test_data_bus_duplicate_subjects_are_rejected():
    @bundle_entrypoint(name="Bad Data Bundle")
    class BadDataBundle:
        @data_bus_handler(subject="same.subject")
        async def first(self, ctx, message):
            return None

        @data_bus_handler(subject="same.subject")
        async def second(self, ctx, message):
            return None

    with pytest.raises(ValueError, match="Duplicate Data Bus handler subject"):
        discover_bundle_interface_manifest(BadDataBundle, bundle_id="bad@1-0")
