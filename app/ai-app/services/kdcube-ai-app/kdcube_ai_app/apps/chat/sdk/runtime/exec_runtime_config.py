# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import copy
from typing import Any, Dict, Optional, Tuple

_MODE_VALUES = {"none", "local", "docker", "fargate", "external"}
_PROFILE_SELECTOR_KEYS = {"profile", "selected_profile", "default_profile", "use"}
_PROFILE_CONTAINER_KEYS = {"profiles"}


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base or {})
    for key, value in (patch or {}).items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _copy_profiles(raw_profiles: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw_profiles, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for name, cfg in raw_profiles.items():
        if isinstance(name, str) and isinstance(cfg, dict):
            out[name] = copy.deepcopy(cfg)
    return out


def normalize_exec_runtime_config(raw: Any) -> Dict[str, Any]:
    """
    Normalize bundle exec runtime config into a single canonical dict.

    Supported forms:
    - direct config:
        {"mode": "docker"}
    - bundle-scoped profiles:
        {
          "default_profile": "fargate",
          "profiles": {
            "docker": {"mode": "docker"},
            "fargate": {"mode": "fargate", ...}
          }
        }
    - shorthand string:
        "docker" -> {"mode": "docker"}
        "fargate" -> {"mode": "fargate"}
    """
    if isinstance(raw, str):
        token = raw.strip()
        if not token:
            return {}
        if token.lower() in _MODE_VALUES:
            return {"mode": token.lower()}
        return {"default_profile": token}

    if not isinstance(raw, dict):
        return {}

    data = copy.deepcopy(raw)
    profiles = _copy_profiles(data.get("profiles"))
    selected = None
    for key in ("profile", "selected_profile", "default_profile", "use"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            selected = value.strip()
            break

    normalized = {
        key: value
        for key, value in data.items()
        if key not in _PROFILE_SELECTOR_KEYS and key not in _PROFILE_CONTAINER_KEYS
    }
    if profiles:
        normalized["profiles"] = profiles
    if selected:
        normalized["default_profile"] = selected
    return normalized


def resolve_exec_runtime_bundle_config(raw: Any) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]], Optional[str]]:
    """
    Backward-compatible helper.

    Returns:
    - resolved active/default runtime config
    - profiles map
    - selected/default profile name, if any
    """
    runtime = normalize_exec_runtime_config(raw)
    profiles = _copy_profiles(runtime.get("profiles"))
    selected = runtime.get("default_profile")
    return (
        resolve_exec_runtime_profile(runtime=runtime),
        profiles,
        selected if isinstance(selected, str) and selected.strip() else None,
    )


def resolve_exec_runtime_profile(
    *,
    runtime: Optional[Dict[str, Any]] = None,
    profile: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resolve a specific exec runtime profile from a canonical runtime config.

    If `profile` is omitted, the current default runtime is returned.
    """
    cfg = normalize_exec_runtime_config(runtime)
    profiles = _copy_profiles(cfg.get("profiles"))
    default_profile = cfg.get("default_profile")
    base: Dict[str, Any] = {
        key: copy.deepcopy(value)
        for key, value in cfg.items()
        if key not in _PROFILE_SELECTOR_KEYS and key not in _PROFILE_CONTAINER_KEYS
    }
    profile_name = (profile or "").strip() or None
    available = profiles or {}

    if profile_name:
        chosen = available.get(profile_name)
        if isinstance(chosen, dict):
            base = _deep_merge(chosen, base)
    elif isinstance(default_profile, str) and default_profile.strip():
        chosen = available.get(default_profile.strip())
        if isinstance(chosen, dict):
            base = _deep_merge(chosen, base)

    if isinstance(overrides, dict) and overrides:
        base = _deep_merge(base, overrides)
    return base
