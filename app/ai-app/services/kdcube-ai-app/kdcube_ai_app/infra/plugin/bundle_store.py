# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
# kdcube_ai_app/infra/plugin/bundle_store.py

from __future__ import annotations
import json, os, time
import logging
import shutil
from typing import Dict, Optional, Tuple, Any, Set
from pathlib import Path
from functools import lru_cache
from pydantic import BaseModel, Field, ValidationError
import kdcube_ai_app.infra.namespaces as namespaces
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.runtime.external.service_discovery import _is_running_in_docker

REDIS_KEY_FMT = namespaces.CONFIG.BUNDLES.BUNDLE_MAPPING_KEY_FMT
REDIS_CHANNEL_FMT = namespaces.CONFIG.BUNDLES.UPDATE_CHANNEL
ADMIN_BUNDLE_ID = "kdcube.admin"
_EXAMPLES_REL_PATH = Path("apps/chat/sdk/examples/bundles")
_SHARED_BUNDLES_ROOT = Path("/bundles")
_log = logging.getLogger(__name__)


def _admin_bundle_entry() -> "BundleEntry":
    root = Path(__file__).resolve().parent
    return BundleEntry(
        id=ADMIN_BUNDLE_ID,
        name="KDCube Admin",
        path=str(root),
        module="admin_bundle.entrypoint",
        singleton=True,
        description="Built-in admin-only bundle",
    )

def _examples_root() -> Path:
    return (Path(__file__).resolve().parents[2] / _EXAMPLES_REL_PATH).resolve()

def _ensure_example_bundle_shared(bundle_root: Path) -> Path:
    """
    If running in Docker, copy example bundles from the image into /bundles
    so sibling containers (py-code-exec) can mount them.
    """
    if not _is_running_in_docker():
        return bundle_root

    dest_root = _SHARED_BUNDLES_ROOT / bundle_root.name
    try:
        if dest_root.exists() and (dest_root / "entrypoint.py").exists():
            return dest_root
        dest_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundle_root, dest_root, dirs_exist_ok=True)
        _log.info("Copied example bundle to shared root: %s -> %s", bundle_root, dest_root)
        return dest_root
    except Exception as exc:
        _log.warning("Failed to copy example bundle to %s: %s", dest_root, exc)
        return bundle_root

def _examples_enabled() -> bool:
    component = (os.getenv("GATEWAY_COMPONENT") or "ingress").strip().lower()
    if component != "proc":
        return False
    try:
        settings = get_settings()
        return bool(settings.BUNDLES_INCLUDE_EXAMPLES)
    except Exception:
        raw = os.getenv("BUNDLES_INCLUDE_EXAMPLES", "1").lower()
        return raw in {"1", "true", "yes", "on"}

def _load_example_bundles() -> Dict[str, "BundleEntry"]:
    if not _examples_enabled():
        return {}
    root = _examples_root()
    if not root.exists():
        return {}
    bundles: Dict[str, BundleEntry] = {}
    for item in root.iterdir():
        if not item.is_dir():
            continue
        if item.name in {"data", "__pycache__"}:
            continue
        if not (item / "entrypoint.py").exists():
            continue
        bid = item.name
        bundle_path = _ensure_example_bundle_shared(item)
        bundles[bid] = BundleEntry(
            id=bid,
            name=bid,
            path=str(bundle_path),
            module="entrypoint",
            singleton=False,
            description="Built-in example bundle",
        )
    return bundles

def _discover_example_bundle_ids() -> Set[str]:
    root = _examples_root()
    if not root.exists():
        return set()
    ids: Set[str] = set()
    for item in root.iterdir():
        if not item.is_dir():
            continue
        if item.name in {"data", "__pycache__"}:
            continue
        if not (item / "entrypoint.py").exists():
            continue
        ids.add(item.name)
    return ids

@lru_cache(maxsize=1)
def _reserved_bundle_ids() -> Set[str]:
    # Always reserve built-in admin bundle and example bundle ids.
    ids = {ADMIN_BUNDLE_ID}
    try:
        ids.update(_discover_example_bundle_ids())
    except Exception:
        pass
    return ids

def _reserved_bundle_entry(bid: str) -> Optional["BundleEntry"]:
    if bid == ADMIN_BUNDLE_ID:
        return _admin_bundle_entry()
    root = _examples_root()
    if not root.exists():
        return None
    candidate = root / bid
    if not candidate.is_dir():
        return None
    if not (candidate / "entrypoint.py").exists():
        return None
    candidate = _ensure_example_bundle_shared(candidate)
    return BundleEntry(
        id=bid,
        name=bid,
        path=str(candidate),
        module="entrypoint",
        singleton=False,
        description="Built-in example bundle",
    )

def _norm_str(val: Optional[str]) -> Optional[str]:
    if val is None:
        return None
    val = str(val).strip()
    return val or None


def _resolve_secret_ref(ref: Any) -> Any:
    """
    Resolve a secret reference to a concrete value.
    Supported forms:
      - "env:NAME" -> os.environ["NAME"]
      - "file:/path/to/secret" -> file contents (stripped)
      - "NAME" -> os.environ["NAME"]
    """
    if ref is None:
        return None
    ref_str = str(ref).strip()
    if not ref_str:
        return None
    if ref_str.startswith("env:"):
        key = ref_str[len("env:"):].strip()
        val = os.getenv(key)
        if val is None:
            raise ValueError(f"Secret ref could not be resolved: {ref_str}")
        return val
    if ref_str.startswith("file:"):
        path = Path(ref_str[len("file:"):].strip()).expanduser()
        if not path.exists():
            raise ValueError(f"Secret ref could not be resolved: {ref_str}")
        return path.read_text().strip()
    raise ValueError(f"Secret ref must start with 'env:' or 'file:': {ref_str}")


def _resolve_props_node(node: Any) -> Any:
    """
    Resolve typed props leaves:
      {"type": "value", "value": ...} -> literal
      {"type": "ref", "value": "..."} -> resolved via _resolve_secret_ref
    Recurses through dicts/lists.
    """
    if isinstance(node, dict):
        node_type = str(node.get("type", "")).strip().lower()
        if node_type in {"value", "ref"} and "value" in node:
            if node_type == "value":
                return node.get("value")
            return _resolve_secret_ref(node.get("value"))
        return {k: _resolve_props_node(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_props_node(v) for v in node]
    if isinstance(node, str):
        s = node.strip()
        if s.startswith("env:") or s.startswith("file:"):
            return _resolve_secret_ref(s)
        return node
    return node


def _split_bundles_and_props(
    bundles_dict: Dict[str, Any],
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Extract bundle-level config/props from descriptor entries.
    Returns (bundles_without_props, props_by_bundle).

    Supported keys:
      - config (preferred)
      - props (legacy alias)
    """
    cleaned: Dict[str, Dict[str, Any]] = {}
    props_map: Dict[str, Dict[str, Any]] = {}
    for bid, raw in (bundles_dict or {}).items():
        if not isinstance(raw, dict):
            continue
        entry = dict(raw)
        config_raw = entry.pop("config", None)
        props_raw = entry.pop("props", None)
        if config_raw is not None and props_raw is not None:
            # Merge legacy props on top of config for backward compatibility.
            merged = {}
            if isinstance(config_raw, dict):
                merged.update(config_raw)
            if isinstance(props_raw, dict):
                merged.update(props_raw)
            props_raw = merged
            config_raw = None
        props_candidate = config_raw if config_raw is not None else props_raw
        if props_candidate is not None:
            props_map[str(bid)] = _resolve_props_node(props_candidate)
        cleaned[str(bid)] = entry
    return cleaned, props_map


def _props_key(*, tenant: str, project: str, bundle_id: str) -> str:
    return namespaces.CONFIG.BUNDLES.PROPS_KEY_FMT.format(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )


async def _apply_bundle_props(
    redis,
    *,
    tenant: str,
    project: str,
    props_map: Dict[str, Dict[str, Any]],
) -> None:
    if not props_map:
        return
    for bid, props in props_map.items():
        if props is None:
            continue
        key = _props_key(tenant=tenant, project=project, bundle_id=bid)
        await redis.set(key, json.dumps(props, ensure_ascii=False))

def _entries_equivalent(a: "BundleEntry", b: "BundleEntry") -> bool:
    return (
        _norm_str(a.id) == _norm_str(b.id)
        and _norm_str(a.name) == _norm_str(b.name)
        and _norm_str(a.path) == _norm_str(b.path)
        and _norm_str(a.module) == _norm_str(b.module)
        and bool(a.singleton) == bool(b.singleton)
        and _norm_str(a.description) == _norm_str(b.description)
        and _norm_str(a.repo) == _norm_str(b.repo)
        and _norm_str(a.ref) == _norm_str(b.ref)
        and _norm_str(a.subdir) == _norm_str(b.subdir)
        and _norm_str(a.git_commit) == _norm_str(b.git_commit)
    )

def _merge_example_bundles(reg: "BundlesRegistry") -> tuple["BundlesRegistry", bool]:
    examples = _load_example_bundles()
    if not examples:
        return reg, False
    updated = False
    merged = BundlesRegistry(
        default_bundle_id=reg.default_bundle_id,
        bundles=dict(reg.bundles),
    )
    for bid, entry in examples.items():
        existing = merged.bundles.get(bid)
        if existing is None:
            merged.bundles[bid] = entry
            updated = True
            continue
        if not _entries_equivalent(existing, entry):
            merged.bundles[bid] = entry
            updated = True
    return merged, updated


def _ensure_admin_bundle(reg: "BundlesRegistry") -> "BundlesRegistry":
    if ADMIN_BUNDLE_ID not in reg.bundles:
        reg = BundlesRegistry(
            default_bundle_id=reg.default_bundle_id,
            bundles=dict(reg.bundles),
        )
        reg.bundles[ADMIN_BUNDLE_ID] = _admin_bundle_entry()
    if not reg.default_bundle_id or reg.default_bundle_id not in reg.bundles:
        reg.default_bundle_id = ADMIN_BUNDLE_ID
    return reg

class BundleEntry(BaseModel):
    id: str
    name: Optional[str] = None
    path: str                     # container-visible absolute path, e.g. /bundles/...
    module: Optional[str] = None
    singleton: Optional[bool] = False
    description: Optional[str] = None
    repo: Optional[str] = None
    ref: Optional[str] = None
    subdir: Optional[str] = None
    git_commit: Optional[str] = None

class BundlesRegistry(BaseModel):
    default_bundle_id: Optional[str] = None
    bundles: Dict[str, BundleEntry] = Field(default_factory=dict)

def _tp_from_env() -> Tuple[str,str]:
    settings = get_settings()
    return settings.TENANT, settings.PROJECT

def redis_key(tenant: Optional[str]=None, project: Optional[str]=None) -> str:
    t,p = tenant, project
    if not t or not p:
        t2,p2 = _tp_from_env()
        t = t or t2
        p = p or p2
    return REDIS_KEY_FMT.format(tenant=t, project=p)

def update_channel(tenant: Optional[str]=None, project: Optional[str]=None) -> str:
    t, p = tenant, project
    if not t or not p:
        t2, p2 = _tp_from_env()
        t = t or t2
        p = p or p2
    return REDIS_CHANNEL_FMT.format(tenant=t, project=p)

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
                seeded, _ = _merge_example_bundles(seeded)
                return _ensure_admin_bundle(seeded)
            # keep returning the empty registry if seeding didn't produce anything
            reg, updated = _merge_example_bundles(reg)
            reg = _ensure_admin_bundle(reg)
            if updated:
                await save_registry(redis, reg, tenant, project)
            return reg
        reg, updated = _merge_example_bundles(reg)
        reg = _ensure_admin_bundle(reg)
        if updated:
            await save_registry(redis, reg, tenant, project)
        return reg

    # Key absent -> try seeding once
    seeded = await seed_from_env_if_any(redis, tenant, project)
    if seeded:
        seeded, _ = _merge_example_bundles(seeded)
        return _ensure_admin_bundle(seeded)
    reg, updated = _merge_example_bundles(BundlesRegistry())
    reg = _ensure_admin_bundle(reg)
    if updated:
        await save_registry(redis, reg, tenant, project)
    return reg

async def save_registry(redis, reg: BundlesRegistry, tenant: Optional[str]=None, project: Optional[str]=None) -> None:
    key = redis_key(tenant, project)
    reg = _ensure_admin_bundle(reg)
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
    await redis.publish(update_channel(t, p), json.dumps(payload, ensure_ascii=False))

async def seed_from_env_if_any(redis, tenant: Optional[str] = None, project: Optional[str] = None) -> Optional[BundlesRegistry]:
    """
    Seed Redis with bundles mapping from AGENTIC_BUNDLES_JSON env var, if present.
    Accepts either:
      - flat mapping: {"mybundle": {...}, ...}
      - new: {"default_bundle_id": "...", "bundles": { ... }}
    """
    data = _load_env_json(strict=False)
    if not data:
        return None

    try:
        if "bundles" not in data:
            bundles_dict = data
            default_id = next(iter(bundles_dict.keys()), None)
        else:
            bundles_dict = data.get("bundles") or {}
            default_id = data.get("default_bundle_id") or (next(iter(bundles_dict.keys()), None))

        bundles_dict, props_map = _split_bundles_and_props(bundles_dict)

        reg = BundlesRegistry(
            default_bundle_id=default_id,
            bundles={bid: _to_entry(bid, b) for bid, b in bundles_dict.items()}
        )
        if not reg.bundles:
            return None
        reg, _ = _merge_example_bundles(reg)
        reg = _ensure_admin_bundle(reg)
        await save_registry(redis, reg, tenant, project)
        if props_map:
            t, p = tenant, project
            if not t or not p:
                t, p = _tp_from_env()
            await _apply_bundle_props(redis, tenant=t, project=p, props_map=props_map)
        return reg

    except Exception:
        return None

async def reset_registry_from_env(redis, tenant: Optional[str] = None, project: Optional[str] = None) -> BundlesRegistry:
    """
    Force-reload from AGENTIC_BUNDLES_JSON and overwrite Redis for (tenant, project).
    Raise ValueError if env is missing or invalid.
    """
    data = _load_env_json(strict=True)
    if "bundles" not in data:
        bundles_dict = data
        default_id = next(iter(bundles_dict.keys()), None)
    else:
        bundles_dict = data.get("bundles") or {}
        default_id = data.get("default_bundle_id") or (next(iter(bundles_dict.keys()), None))

    bundles_dict, props_map = _split_bundles_and_props(bundles_dict)

    if not bundles_dict:
        raise ValueError("AGENTIC_BUNDLES_JSON has no bundles")

    reg = BundlesRegistry(
        default_bundle_id=default_id,
        bundles={bid: _to_entry(bid, b) for bid, b in bundles_dict.items()}
    )
    reg, _ = _merge_example_bundles(reg)
    reg = _ensure_admin_bundle(reg)
    await save_registry(redis, reg, tenant, project)
    if props_map:
        t, p = tenant, project
        if not t or not p:
            t, p = _tp_from_env()
        await _apply_bundle_props(redis, tenant=t, project=p, props_map=props_map)
    return reg


async def force_env_reset_if_requested(
    redis,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
    actor: Optional[str] = None,
) -> Optional[BundlesRegistry]:
    """
    If BUNDLES_FORCE_ENV_ON_STARTUP is set, overwrite Redis registry from env once.
    Uses a Redis lock to avoid multiple workers doing it concurrently.
    """
    settings = get_settings()
    if not settings.BUNDLES_FORCE_ENV_ON_STARTUP:
        return None

    t, p = tenant, project
    if not t or not p:
        t, p = _tp_from_env()

    lock_key = namespaces.CONFIG.BUNDLES.ENV_SYNC_LOCK_FMT.format(tenant=t, project=p)
    try:
        acquired = await redis.set(lock_key, "1", nx=True, ex=settings.BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS)
    except Exception:
        acquired = False

    if not acquired:
        return None

    reg = await reset_registry_from_env(redis, t, p)
    await publish_update(redis, reg, tenant=t, project=p, op="replace", actor=actor or "startup-env")
    return reg


def _to_entry(bid: str, v: Dict[str, Any]) -> BundleEntry:
    """Normalize incoming dict -> BundleEntry."""
    unsupported_keys = {"git_url", "git_ref", "git_subdir", "git_repo"}
    if any(k in v for k in unsupported_keys):
        raise ValueError("Use repo/ref/subdir only; git_* keys are not supported.")
    repo = _norm_str(v.get("repo"))
    path_val = _norm_str(v.get("path")) or ""
    ref = _norm_str(v.get("ref"))
    subdir = _norm_str(v.get("subdir"))
    if not path_val and repo:
        try:
            from kdcube_ai_app.infra.plugin.git_bundle import compute_git_bundle_paths
            paths = compute_git_bundle_paths(
                bundle_id=bid,
                git_url=repo,
                git_ref=ref,
                git_subdir=subdir,
            )
            path_val = str(paths.bundle_root)
        except Exception:
            path_val = ""
    candidate = BundleEntry(
        id=bid,
        name=_norm_str(v.get("name")),
        path=path_val or v.get("path") or "",
        module=_norm_str(v.get("module")),
        singleton=bool(v.get("singleton", False)),
        description=_norm_str(v.get("description")),
        repo=repo,
        ref=ref,
        subdir=subdir,
        git_commit=_norm_str(v.get("git_commit")),
    )
    reserved = _reserved_bundle_entry(bid) if bid in _reserved_bundle_ids() else None
    if reserved:
        # Allow reserved ids in bundles.yaml only to override props.
        # Ignore any repo/ref/path/module fields and keep the built-in entry.
        if _entries_equivalent(candidate, reserved):
            return reserved
        if repo or ref or subdir or v.get("path") or v.get("module") or v.get("name") or v.get("description"):
            _log.warning(
                "Bundle id '%s' is reserved; ignoring repo/ref/path/module and using built-in bundle entry.",
                bid,
            )
        return reserved
    return candidate

def _load_env_json(strict: bool) -> Optional[Dict[str, Any]]:
    raw = os.getenv("AGENTIC_BUNDLES_JSON")
    if not raw:
        if strict:
            raise ValueError("AGENTIC_BUNDLES_JSON is not set")
        return None
    raw = raw.strip()
    if raw.startswith("{") or raw.startswith("["):
        return json.loads(raw)
    path = Path(raw).expanduser()
    if not path.exists():
        if strict:
            raise ValueError(f"AGENTIC_BUNDLES_JSON file not found: {path}")
        return None
    text = path.read_text()
    data: Optional[Dict[str, Any]]
    if path.suffix.lower() in {".yml", ".yaml"}:
        try:
            import yaml  # type: ignore
        except Exception as e:
            raise ValueError("PyYAML is required to load YAML bundle descriptors.") from e
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not data:
        return None
    # If an assembly descriptor is provided, extract the bundles section.
    if isinstance(data, dict) and "bundles" in data:
        bundles_block = data.get("bundles") or {}
        if isinstance(bundles_block, dict) and "items" in bundles_block:
            items = bundles_block.get("items") or []
            bundles: Dict[str, Any] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                bid = item.get("id")
                if not bid:
                    raise ValueError("Bundle item missing 'id' in assembly descriptor.")
                bundles[bid] = dict(item)
            return {
                "default_bundle_id": bundles_block.get("default_bundle_id"),
                "bundles": bundles,
            }
    if isinstance(data, dict) and "items" in data:
        items = data.get("items") or []
        bundles = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            bid = item.get("id")
            if not bid:
                raise ValueError("Bundle item missing 'id' in bundles descriptor.")
            bundles[bid] = dict(item)
        return {
            "default_bundle_id": data.get("default_bundle_id"),
            "bundles": bundles,
        }
    return data

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
        return _ensure_admin_bundle(BundlesRegistry(default_bundle_id=new_default, bundles=new_map))

    # merge
    new_map: Dict[str, BundleEntry] = dict(current.bundles)
    for bid, v in (bundles_patch or {}).items():
        new_map[bid] = _to_entry(bid, v)

    new_default = default_bundle_id or current.default_bundle_id
    if new_default and new_default not in new_map:
        new_default = next(iter(new_map), None)

    return _ensure_admin_bundle(BundlesRegistry(default_bundle_id=new_default, bundles=new_map))
