from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import set_comm
from kdcube_ai_app.apps.chat.sdk.runtime.tool_module_bindings import (
    bind_module_target,
    get_bound_context,
)


def test_bindings_expose_service_and_tool_subsystem_comm():
    module = type("_Module", (), {})()
    svc = object()
    comm = object()
    tool_subsystem = type("_ToolSubsystem", (), {"comm": comm})()

    bind_module_target(
        module,
        svc=svc,
        registry={"kb_client": "stub"},
        integrations={
            "tool_subsystem": tool_subsystem,
            "kv_cache": {"namespace": "demo"},
            "ctx_client": {"mode": "stub"},
        },
    )

    ctx = get_bound_context(module)
    assert ctx.service is svc
    assert ctx.tool_subsystem is tool_subsystem
    assert ctx.communicator is comm
    assert ctx.kv_cache == {"namespace": "demo"}
    assert ctx.ctx_client == {"mode": "stub"}
    assert getattr(module, "REGISTRY") == {"kb_client": "stub"}
    assert getattr(module, "__KDCUBE_BIND_DONE__") is True


def test_bindings_fall_back_to_comm_context_when_tool_subsystem_is_absent():
    module = type("_Module", (), {})()
    svc = object()
    comm = object()
    set_comm(comm)

    bind_module_target(
        module,
        svc=svc,
        integrations={"kv_cache": {"namespace": "demo"}},
    )

    ctx = get_bound_context(module)
    assert ctx.service is svc
    assert ctx.tool_subsystem is None
    assert ctx.communicator is comm
    assert ctx.kv_cache == {"namespace": "demo"}

    set_comm(None)
