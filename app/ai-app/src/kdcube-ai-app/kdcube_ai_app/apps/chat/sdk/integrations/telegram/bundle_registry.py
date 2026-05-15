from __future__ import annotations

import sys
from typing import Any, Dict, Iterable


LAST_CONFIG_KEY = "__last__"


def normalize_bundle_id(bundle_id: str = "") -> str:
    return str(bundle_id or "").strip() or "__default__"


def register_config(registry: Dict[str, Dict[str, Any]], *, bundle_id: str = "", config: Dict[str, Any]) -> str:
    key = normalize_bundle_id(bundle_id)
    entry = dict(config)
    entry["bundle_id"] = str(bundle_id or "").strip()
    registry[key] = entry
    registry[LAST_CONFIG_KEY] = {"key": key}
    return key


def entrypoint_bundle_candidates(entrypoint: Any) -> list[str]:
    candidates: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in candidates:
            candidates.append(text)

    config = getattr(entrypoint, "config", None)
    spec = getattr(config, "ai_bundle_spec", None)
    add(getattr(spec, "id", None))
    add(getattr(spec, "name", None))
    add(getattr(spec, "bundle_id", None))
    add(getattr(config, "bundle_id", None))
    add(getattr(entrypoint, "bundle_id", None))
    add(getattr(entrypoint, "BUNDLE_ID", None))

    module = sys.modules.get(getattr(getattr(entrypoint, "__class__", None), "__module__", ""))
    add(getattr(module, "BUNDLE_ID", None))
    add(getattr(module, "WORKFLOW_NAME", None))
    return candidates


def _candidate_matches(candidate: str, configured: str) -> bool:
    if candidate == configured:
        return True
    if "@" in candidate or "@" in configured:
        return candidate.startswith(f"{configured}@") or configured.startswith(f"{candidate}@")
    return False


def resolve_config(
    registry: Dict[str, Dict[str, Any]],
    *,
    entrypoint: Any = None,
    label: str = "integration",
) -> Dict[str, Any]:
    configs = {key: value for key, value in registry.items() if key != LAST_CONFIG_KEY}
    if not configs:
        raise RuntimeError(f"{label} is not configured")

    if entrypoint is not None:
        candidates = entrypoint_bundle_candidates(entrypoint)
        for candidate in candidates:
            key = normalize_bundle_id(candidate)
            if key in configs:
                return configs[key]
        for candidate in candidates:
            for key, config in configs.items():
                configured = str(config.get("bundle_id") or key or "").strip()
                if configured and _candidate_matches(candidate, configured):
                    return config

    if len(configs) == 1:
        return next(iter(configs.values()))

    last_key = str((registry.get(LAST_CONFIG_KEY) or {}).get("key") or "").strip()
    if last_key and last_key in configs:
        return configs[last_key]

    raise RuntimeError(f"{label} has multiple bundle configurations; could not resolve current bundle")


def configured_bundle_id(config: Dict[str, Any]) -> str:
    return str(config.get("bundle_id") or "").strip()
