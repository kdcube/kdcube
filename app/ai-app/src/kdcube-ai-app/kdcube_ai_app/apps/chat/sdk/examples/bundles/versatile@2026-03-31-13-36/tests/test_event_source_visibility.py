from __future__ import annotations

from pathlib import Path

import yaml

from kdcube_ai_app.apps.chat.sdk.events import EventSourceSubsystem
from kdcube_ai_app.apps.chat.sdk.runtime.skill_config import agent_skill_config_from_bundle_props
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import agent_tool_config_from_bundle_props
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.defaults import default_canvas_event_source_specs


def _bundle_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _template_bundle_props() -> dict:
    template = yaml.safe_load((_bundle_root() / "config" / "bundles.template.yaml").read_text()) or {}
    bundle = next(
        item
        for item in template["bundles"]["items"]
        if item.get("id") == "versatile@2026-03-31-13-36"
    )
    return bundle["config"]


def test_canvas_event_source_visibility_is_separate_from_named_service_actions():
    props = _template_bundle_props()
    tool_aliases = {
        str(spec.get("alias") or "")
        for spec in agent_tool_config_from_bundle_props(
            props,
            "main",
            bundle_root=_bundle_root(),
        ).tool_specs
        if isinstance(spec, dict)
    }
    assert "canvas" in tool_aliases

    tool_config = agent_tool_config_from_bundle_props(
        props,
        "default_agent",
        bundle_root=_bundle_root(),
    )
    assert "object_action" not in (tool_config.allowed_tool_names_by_alias.get("named_services") or [])

    event_sources = EventSourceSubsystem(
        event_specs=default_canvas_event_source_specs(),
        bundle_root=_bundle_root(),
    )

    assert event_sources.namespace_rehoster("cnv") is not None
    assert event_sources.event_source_reader("cnv") is not None
    assert event_sources.by_event_source_id("canvas.read") is not None


def test_reference_template_tool_config_uses_as_consumer_surface():
    props = _template_bundle_props()
    assert "tools" not in props

    as_consumer = props["surfaces"]["as_consumer"]
    main = as_consumer["agents"]["main"]
    assert as_consumer["default_agent"] == "main"
    assert isinstance(main["tools"], list)
    assert main["skills"]["custom_root"] == "skills"
    assert main["skills"]["consumers"] == {}
    assert main["event_sources"] == []
    assert as_consumer["ui"]["canvas"]["resolvers"] == []

    tool_config = agent_tool_config_from_bundle_props(props, "main", bundle_root=_bundle_root())
    assert "canvas" in tool_config.allowed_plugins
    assert tool_config.allowed_tool_names_by_alias["canvas"] == ["patch"]
    assert tool_config.allowed_tool_names_by_alias["knowledge"] is None

    skill_config = agent_skill_config_from_bundle_props(props, "main", bundle_root=_bundle_root())
    assert skill_config.custom_skills_root == _bundle_root() / "skills"
    assert skill_config.agents_config == {}
