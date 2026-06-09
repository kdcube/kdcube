from __future__ import annotations

from typing import Any, Dict, Mapping


DEFAULT_CANVAS_CONFIG: Dict[str, Any] = {
    "artifact_prefix": "canvas",
    "origin_prefix": "canvas",
    "state_event_source_id": "canvas.state",
    "ui_event_type": "canvas.patch.applied",
    "artifact_resolver_name": "sdk.canvas.artifact_storage",
    "handoff_resolver_names": {},
    "revision_retention": 80,
    "data_bus_subject": "canvas.patch",
    "event_agent_id": "canvas",
    "event_surface": "canvas",
}


def mapping_or_empty(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def canvas_config_from_props(props: Mapping[str, Any] | None) -> Dict[str, Any]:
    bundle_props = mapping_or_empty(props)
    configured = mapping_or_empty(bundle_props.get("canvas"))
    if not configured:
        sdk_props = mapping_or_empty(bundle_props.get("sdk"))
        configured = mapping_or_empty(sdk_props.get("canvas"))
    cfg = dict(DEFAULT_CANVAS_CONFIG)
    cfg.update(configured)
    handoff = cfg.get("handoff_resolver_names")
    cfg["handoff_resolver_names"] = dict(handoff) if isinstance(handoff, Mapping) else {}
    try:
        cfg["revision_retention"] = int(cfg.get("revision_retention") or 80)
    except Exception:
        cfg["revision_retention"] = 80
    return cfg

