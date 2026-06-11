from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleOperationCall,
    bind_bundle_operation_caller,
    call_bundle_operation,
    get_current_bundle_operation_caller,
)


@pytest.mark.asyncio
async def test_call_bundle_operation_uses_bound_request_caller():
    calls: list[BundleOperationCall] = []

    async def _caller(call: BundleOperationCall):
        calls.append(call)
        return {"ok": True, "operation": call.operation, "data": call.data}

    with bind_bundle_operation_caller(_caller):
        result = await call_bundle_operation(
            tenant="tenant-a",
            project="project-a",
            bundle_id="task-tracker@1-0",
            operation="named_service",
            data={"operation": "provider.about"},
        )

    assert result == {"ok": True, "operation": "named_service", "data": {"operation": "provider.about"}}
    assert calls == [
        BundleOperationCall(
            tenant="tenant-a",
            project="project-a",
            bundle_id="task-tracker@1-0",
            operation="named_service",
            data={"operation": "provider.about"},
            route="operations",
        )
    ]
    assert get_current_bundle_operation_caller() is None


@pytest.mark.asyncio
async def test_call_bundle_operation_requires_request_caller():
    with pytest.raises(RuntimeError, match="No request-bound bundle operation caller"):
        await call_bundle_operation(bundle_id="task-tracker@1-0", operation="named_service")
