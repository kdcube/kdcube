# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
# kdcube_ai_app/infra/plugin/bundle_registry.py

from __future__ import annotations
import json, os, threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

_REG_LOCK = threading.RLock()
_REGISTRY: Dict[str, Dict[str, Any]] = {}
_DEFAULT_ID: Optional[str] = None

@dataclass
class BundleSpec:
    id: str
    name: Optional[str] = None
    path: str = ""
    module: Optional[str] = None
    singleton: bool = False
    description: Optional[str] = None
    version: Optional[str] = None

ENV_JSON = "AGENTIC_BUNDLES_JSON"
ADMIN_BUNDLE_ID = "kdcube.admin"


def _admin_bundle_spec() -> Dict[str, Any]:
    root = Path(__file__).resolve().parent
    return {
        "id": ADMIN_BUNDLE_ID,
        "name": "KDCube Admin",
        "path": str(root),
        "module": "admin_bundle.entrypoint",
        "singleton": True,
        "description": "Built-in admin-only bundle",
    }


def _ensure_admin_bundle(reg: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if ADMIN_BUNDLE_ID not in reg:
        reg = dict(reg)
        reg[ADMIN_BUNDLE_ID] = _normalize(_admin_bundle_spec())
    return reg

def _normalize(d: Dict[str, Any]) -> Dict[str, Any]:
    # Ensure required keys exist
    d = dict(d)
    d["id"] = d.get("id") or d.get("key") or d.get("name")
    if not d.get("id"):
        raise ValueError("BundleSpec missing 'id'")
    if not d.get("path"):
        raise ValueError(f"BundleSpec '{d['id']}' missing 'path'")
    if not d.get("version"):
        d["version"] = d.get("bundle_version")
    d["singleton"] = bool(d.get("singleton", False))
    return d

def load_from_env() -> None:
    """
    Accept both shapes:
      1) {"default_bundle_id": "...", "bundles": { "<id>": {...}, ... }}
      2) legacy flat dict: { "<id>": {...}, ... }
    """
    global _REGISTRY, _DEFAULT_ID
    with _REG_LOCK:
        raw = os.getenv(ENV_JSON)
        if not raw:
            _REGISTRY = {}
            _DEFAULT_ID = None
            return

        data = json.loads(raw)

        if isinstance(data, dict) and "bundles" in data:
            default_bundle_id = data.get("default_bundle_id")
            raw_bundles = data.get("bundles") or {}
        else:
            # legacy: env was just a mapping
            default_bundle_id = None
            raw_bundles = data or {}

        reg: Dict[str, Dict[str, Any]] = {}
        for k, v in (raw_bundles or {}).items():
            # ensure id consistency for each entry
            v = dict(v or {})
            v.setdefault("id", k)
            item = _normalize(v)
            reg[item["id"]] = item

        reg = _ensure_admin_bundle(reg)
        _REGISTRY = reg

        # resolve default
        if default_bundle_id and default_bundle_id in _REGISTRY:
            _DEFAULT_ID = default_bundle_id
        else:
            _DEFAULT_ID = ADMIN_BUNDLE_ID if ADMIN_BUNDLE_ID in _REGISTRY else next(iter(_REGISTRY.keys()), None)

def serialize_to_env(registry: Dict[str, Dict[str, Any]], default_id: Optional[str]) -> str:
    """Reflect current in-memory mapping back into env (best-effort)."""
    with _REG_LOCK:
        registry = _ensure_admin_bundle(registry or {})
        payload = {
            "default_bundle_id": default_id if default_id in registry else ADMIN_BUNDLE_ID,
            "bundles": registry,
        }
        os.environ[ENV_JSON] = json.dumps(payload, ensure_ascii=False)
        return os.environ[ENV_JSON]


def get_all() -> Dict[str, Dict[str, Any]]:
    with _REG_LOCK:
        return {k: dict(v) for k, v in _REGISTRY.items()}

def get_default_id() -> Optional[str]:
    with _REG_LOCK:
        return _DEFAULT_ID

def set_registry(registry: Dict[str, Dict[str, Any]], default_id: Optional[str]) -> None:
    global _REGISTRY, _DEFAULT_ID
    with _REG_LOCK:
        # normalize & replace
        new_reg: Dict[str, Dict[str, Any]] = {}
        for k, v in (registry or {}).items():
            item = _normalize({"id": k, **(v or {})})
            new_reg[item["id"]] = item
        new_reg = _ensure_admin_bundle(new_reg)
        _REGISTRY = new_reg
        _DEFAULT_ID = default_id if default_id in _REGISTRY else ADMIN_BUNDLE_ID

def upsert_bundles(partial: Dict[str, Dict[str, Any]], default_id: Optional[str]) -> None:
    """Merge update."""
    global _REGISTRY, _DEFAULT_ID
    with _REG_LOCK:
        reg = dict(_REGISTRY)
        for k, v in (partial or {}).items():
            item = _normalize({"id": k, **(v or {})})
            reg[item["id"]] = {**reg.get(item["id"], {}), **item}
        reg = _ensure_admin_bundle(reg)
        _REGISTRY = reg
        if default_id:
            _DEFAULT_ID = default_id if default_id in _REGISTRY else _DEFAULT_ID

def resolve_bundle(bundle_id: Optional[str], override: Optional[Dict[str, Any]] = None) -> Optional[BundleSpec]:
    """Return the effective BundleSpec from (id OR override)."""
    with _REG_LOCK:
        if override and override.get("path"):
            d = _normalize({
                "id": override.get("id") or "override",
                "path": override["path"],
                "module": override.get("module"),
                "singleton": bool(override.get("singleton", False)),
                "name": override.get("name"),
                "description": override.get("description"),
            })
            return BundleSpec(**d)
        bid = bundle_id or _DEFAULT_ID
        print(f"[resolve_bundle]. Default bundle id = {_DEFAULT_ID}\nRegistry={_REGISTRY}\nRequested id = {bundle_id}\nUsing id = {bid}")
        if not bid or bid not in _REGISTRY:
            return None
        return BundleSpec(**_REGISTRY[bid])


async def load_registry(redis, logger):
    try:
        from kdcube_ai_app.infra.plugin.bundle_store import load_registry as _load_store

        persisted = await _load_store(redis)  # tenant/project inferred from env
        set_registry(
            {bid: be.model_dump() for bid, be in persisted.bundles.items()},
            persisted.default_bundle_id
        )
        serialize_to_env(get_all(), get_default_id())
        logger.info(f"Bundle mapping synced from Redis: {len(persisted.bundles)} bundles (default={persisted.default_bundle_id})")
    except Exception as _e:
        logger.warning(f"Could not sync bundles from Redis; using env-only: {_e}")
