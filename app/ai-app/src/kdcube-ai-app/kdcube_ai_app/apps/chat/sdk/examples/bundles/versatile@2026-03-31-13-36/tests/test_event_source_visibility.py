from __future__ import annotations

from pathlib import Path

from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem
from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path


def _bundle_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_bundle_module(name: str):
    _mod_name, module = load_dynamic_module_for_path(_bundle_root() / name)
    return module


def test_canvas_event_source_visibility_is_separate_from_named_service_actions():
    tools_descriptor = _load_bundle_module("tools_descriptor.py")
    events_descriptor = _load_bundle_module("events_descriptor.py")

    tool_aliases = {
        str(spec.get("alias") or "")
        for spec in (tools_descriptor.TOOLS_SPECS or [])
        if isinstance(spec, dict)
    }
    assert "canvas" in tool_aliases

    tool_config = tools_descriptor.config_for_agent(
        "default_agent",
        bundle_props=tools_descriptor.default_tools_props(),
    )
    assert "object_action" not in (tool_config.allowed_tool_names_by_alias.get("named_services") or [])

    event_sources = EventSourceSubsystem(
        event_specs=events_descriptor.EVENT_SOURCE_SPECS,
        bundle_root=_bundle_root(),
    )

    assert event_sources.namespace_rehoster("cnv") is not None
    assert event_sources.event_source_reader("cnv") is not None
    assert event_sources.by_event_source_id("canvas.read") is not None
