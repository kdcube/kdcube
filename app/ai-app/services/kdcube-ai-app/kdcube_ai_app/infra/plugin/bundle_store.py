# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
# kdcube_ai_app/infra/plugin/bundle_store.py

from __future__ import annotations
import json, os, time
from typing import Dict, Optional, Tuple, Any
from pydantic import BaseModel, Field, ValidationError
import kdcube_ai_app.infra.namespaces as namespaces

REDIS_KEY_FMT = namespaces.CONFIG.BUNDLES.BUNDLE_MAPPING_KEY_FMT
REDIS_CHANNEL = namespaces.CONFIG.BUNDLES.UPDATE_CHANNEL

class BundleEntry(BaseModel):
    id: str
    name: Optional[str] = None
    path: str                     # container-visible absolute path, e.g. /bundles/...
    module: Optional[str] = None
    singleton: Optional[bool] = False
    description: Optional[str] = None

class BundlesRegistry(BaseModel):
    default_bundle_id: Optional[str] = None
    bundles: Dict[str, BundleEntry] = Field(default_factory=dict)

def _tp_from_env() -> Tuple[str,str]:
    tenant = os.getenv("DEFAULT_TENANT") or os.getenv("TENANT_ID") or "default-tenant"
    project = os.getenv("DEFAULT_PROJECT_NAME") or os.getenv("CHAT_WEB_APP_PROJECT") or "default-project"
    return tenant, project

def redis_key(tenant: Optional[str]=None, project: Optional[str]=None) -> str:
    t,p = tenant, project
    if not t or not p:
        t2,p2 = _tp_from_env()
        t = t or t2
        p = p or p2
    return REDIS_KEY_FMT.format(tenant=t, project=p)

async def load_registry(redis, tenant: Optional[str] = None, project: Optional[str] = None) -> BundlesRegistry:
    """
    Load per-tenant/project registry from Redis.
    If the key is missing OR contains an 'effectively empty' registry
    (e.g. {"default_bundle_id": null, "bundles": {}}), seed once from env.
    """
    key = redis_key(tenant, project)
    raw = await redis.get(key)

    # Helper: parse raw -> BundlesRegistry (tolerant to shape)
    def _parse(raw_bytes) -> BundlesRegistry:
        try:
            return BundlesRegistry.model_validate_json(raw_bytes)
        except ValidationError:
            data = json.loads(raw_bytes)
            return BundlesRegistry(**data)

    if raw:
        reg = _parse(raw)
        # ---empty registry stored -> seed from env once ---
        if not reg.bundles or len(reg.bundles) == 0:
            seeded = await seed_from_env_if_any(redis, tenant, project)
            if seeded and seeded.bundles:
                return seeded
            # keep returning the empty registry if seeding didn't produce anything
            return reg
        return reg

    # Key absent -> try seeding once
    seeded = await seed_from_env_if_any(redis, tenant, project)
    return seeded or BundlesRegistry()

async def save_registry(redis, reg: BundlesRegistry, tenant: Optional[str]=None, project: Optional[str]=None) -> None:
    key = redis_key(tenant, project)
    await redis.set(key, reg.model_dump_json())

async def publish_update(redis, reg: BundlesRegistry, *, tenant: Optional[str]=None, project: Optional[str]=None, op: str="merge", actor: Optional[str]=None):
    t,p = tenant, project
    if not t or not p: t,p = _tp_from_env()
    payload = {
        "tenant": t, "project": p,
        "op": op, "ts": time.time(),
        # attach full registry snapshot
        "registry": reg.model_dump()
    }
    if actor: payload["actor"] = actor
    await redis.publish(REDIS_CHANNEL, json.dumps(payload, ensure_ascii=False))

async def seed_from_env_if_any(redis, tenant: Optional[str] = None, project: Optional[str] = None) -> Optional[BundlesRegistry]:
    """
    Seed Redis with bundles mapping from AGENTIC_BUNDLES_JSON env var, if present.
    Accepts either:
      - legacy: {"mybundle": {...}, ...}
      - new: {"default_bundle_id": "...", "bundles": { ... }}
    """
    json_env = os.getenv("AGENTIC_BUNDLES_JSON")
    if not json_env:
        return None

    try:
        data = json.loads(json_env)
        if "bundles" not in data:
            bundles_dict = data
            default_id = next(iter(bundles_dict.keys()), None)
        else:
            bundles_dict = data.get("bundles") or {}
            default_id = data.get("default_bundle_id") or (next(iter(bundles_dict.keys()), None))

        reg = BundlesRegistry(
            default_bundle_id=default_id,
            bundles={bid: BundleEntry(**b) for bid, b in bundles_dict.items()}
        )
        if not reg.bundles:
            return None

        await save_registry(redis, reg, tenant, project)
        return reg

    except Exception:
        return None

async def reset_registry_from_env(redis, tenant: Optional[str] = None, project: Optional[str] = None) -> BundlesRegistry:
    """
    Force-reload from AGENTIC_BUNDLES_JSON and overwrite Redis for (tenant, project).
    Raise ValueError if env is missing or invalid.
    """
    json_env = os.getenv("AGENTIC_BUNDLES_JSON")
    if not json_env:
        raise ValueError("AGENTIC_BUNDLES_JSON is not set")

    data = json.loads(json_env)
    if "bundles" not in data:
        bundles_dict = data
        default_id = next(iter(bundles_dict.keys()), None)
    else:
        bundles_dict = data.get("bundles") or {}
        default_id = data.get("default_bundle_id") or (next(iter(bundles_dict.keys()), None))

    if not bundles_dict:
        raise ValueError("AGENTIC_BUNDLES_JSON has no bundles")

    reg = BundlesRegistry(
        default_bundle_id=default_id,
        bundles={bid: BundleEntry(**b) for bid, b in bundles_dict.items()}
    )
    await save_registry(redis, reg, tenant, project)
    return reg


def _to_entry(bid: str, v: Dict[str, Any]) -> BundleEntry:
    """Normalize incoming dict -> BundleEntry."""
    return BundleEntry(
        id=bid,
        name=v.get("name"),
        path=v["path"],
        module=v.get("module"),
        singleton=bool(v.get("singleton", False)),
        description=v.get("description"),
    )

def apply_update(
        current: BundlesRegistry,
        op: str,
        bundles_patch: Dict[str, Dict[str, Any]],
        default_bundle_id: Optional[str] = None,
) -> BundlesRegistry:
    """
    Apply a 'replace' or 'merge' patch to the current registry and
    return a NEW BundlesRegistry (does not persist).
    """
    if op not in ("replace", "merge"):
        raise ValueError("Invalid op; use 'replace' or 'merge'")

    if op == "replace":
        new_map = {bid: _to_entry(bid, v) for bid, v in (bundles_patch or {}).items()}
        new_default = (
            default_bundle_id if (default_bundle_id in new_map) else (next(iter(new_map), None))
        )
        return BundlesRegistry(default_bundle_id=new_default, bundles=new_map)

    # merge
    new_map: Dict[str, BundleEntry] = dict(current.bundles)
    for bid, v in (bundles_patch or {}).items():
        new_map[bid] = _to_entry(bid, v)

    new_default = default_bundle_id or current.default_bundle_id
    if new_default and new_default not in new_map:
        new_default = next(iter(new_map), None)

    return BundlesRegistry(default_bundle_id=new_default, bundles=new_map)