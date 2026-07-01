# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
# kdcube_ai_app/infra/plugin/bundle_registry.py

from __future__ import annotations
import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any
from kdcube_ai_app.apps.chat.sdk.config import get_settings

_REG_LOCK = threading.RLock()
_REGISTRY: Dict[str, Dict[str, Any]] = {}
_DEFAULT_ID: Optional[str] = None
_MISSING_PATH_WARNED: set[tuple[str, str]] = set()
logger = logging.getLogger(__name__)


async def _load_store_registry(runtime_redis, tenant: str, project: str):
    from kdcube_ai_app.infra.plugin.bundle_store import load_registry as _load_store_registry_impl

    return await _load_store_registry_impl(runtime_redis, tenant, project)

async def _load_store_registry_readonly(tenant: str, project: str):
    from kdcube_ai_app.infra.plugin.bundle_store import (
        load_registry_from_authority_readonly as _load_store_registry_readonly_impl,
    )

    return await _load_store_registry_readonly_impl(tenant, project)

@dataclass
class BundleSpec:
    id: str
    name: Optional[str] = None
    path: str = ""
    module: Optional[str] = None
    singleton: bool = False
    description: Optional[str] = None
    version: Optional[str] = None
    repo: Optional[str] = None
    ref: Optional[str] = None
    subdir: Optional[str] = None
    git_commit: Optional[str] = None

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
    unsupported_keys = {"git_url", "git_ref", "git_subdir", "git_repo"}
    if any(k in d for k in unsupported_keys):
        raise ValueError("Use repo/ref/subdir only; git_* keys are not supported.")
    repo = d.get("repo")
    if repo:
        try:
            from kdcube_ai_app.infra.plugin.git_bundle import compute_git_bundle_paths
            paths = compute_git_bundle_paths(
                bundle_id=d["id"],
                git_url=repo,
                git_ref=d.get("ref"),
                git_subdir=d.get("subdir"),
            )
            d["path"] = str(paths.bundle_root)
        except Exception:
            d["path"] = ""
    elif not d.get("path"):
        try:
            from kdcube_ai_app.infra.plugin.bundle_store import _reserved_bundle_entry

            reserved = _reserved_bundle_entry(str(d["id"]))
        except Exception:
            reserved = None
        if reserved is not None:
            reserved_dict = reserved.model_dump(exclude_none=True)
            reserved_dict["id"] = d["id"]
            d = reserved_dict
        else:
            raise ValueError(f"BundleSpec '{d['id']}' missing 'path'")
    if not d.get("version"):
        d["version"] = d.get("bundle_version")
    if not d.get("git_commit"):
        d["git_commit"] = d.get("bundle_commit")
    d["singleton"] = bool(d.get("singleton", False))
    return d


def resolve_bundle_root(path: str, module: Optional[str]) -> Path:
    """
    Resolve the bundle root from a bundle spec path + module.

    Rules:
    - If module is empty or has no dot (e.g. "entrypoint"), assume `path` already points to the bundle root.
    - If module has a dotted base (e.g. "react@2026-...entrypoint" or "my.pkg.entrypoint"),
      try `<path>/<module_base>` and `<path>/<module_base as pkg path>`.
    - Fall back to `path` if no candidate exists.
    """
    base = Path(path).resolve() if path else Path(".").resolve()
    if not module:
        return base
    if "." not in module:
        return base

    module_base = module.rsplit(".", 1)[0]
    candidates = [
        (base / Path(module_base)).resolve(),
        (base / Path(*module_base.split("."))).resolve(),
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    if base.name == module_base:
        return base
    return base

async def _apply_git_resolution(reg: Dict[str, Dict[str, Any]], source: str = "unknown") -> Dict[str, Dict[str, Any]]:
    """
    Resolve git bundles to local paths (and optionally refresh).
    Keeps logic scoped to bundle registry, not processor.
    """
    component = (os.getenv("GATEWAY_COMPONENT") or "ingress").strip().lower()
    if component != "proc":
        if any(entry.get("repo") for entry in reg.values()):
            logger.info(
                "Git bundle resolution skipped (component=%s). Only proc resolves git bundles.",
                component,
            )
        _warn_missing_local_path_bundles(reg, source=source)
        return reg
    enabled = get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_RESOLUTION_ENABLED
    repo_count = sum(1 for entry in reg.values() if entry.get("repo"))
    if not enabled:
        if any(entry.get("repo") for entry in reg.values()):
            logger.warning("Git bundle resolution disabled (BUNDLE_GIT_RESOLUTION_ENABLED=0); repo/ref/subdir kept as metadata only.")
        return reg
    if repo_count:
        logger.info("Resolving git bundles (source=%s, bundles=%s)", source, repo_count)
    try:
        from kdcube_ai_app.infra.plugin.git_bundle import (
            ensure_git_bundle,
            resolve_managed_bundles_root,
            cleanup_old_git_bundles_async,
            bundle_dir_for_git,
            git_bundle_cache_status,
        )
        from kdcube_ai_app.infra.plugin.bundle_refs import get_local_active_paths
    except Exception:
        return reg

    _git = get_settings().PLATFORM.APPLICATIONS.GIT
    atomic = _git.BUNDLE_GIT_ATOMIC
    force_pull = _git.BUNDLE_GIT_ALWAYS_PULL
    out = dict(reg)
    total = 0
    resolved = 0
    skipped = 0
    failed = 0
    for bid, entry in reg.items():
        repo = entry.get("repo")
        if not repo:
            continue
        total += 1
        logger.info(
            "Git bundle resolve: id=%s repo=%s ref=%s subdir=%s source=%s",
            bid,
            repo,
            entry.get("ref"),
            entry.get("subdir"),
            source,
        )
        # Skip git resolution only when the managed bundle cache marker proves
        # this path was materialized for the requested repo/ref/subdir. A plain
        # filesystem existence check is not enough: after descriptor ref bumps a
        # stale path can still exist and would otherwise keep executing old
        # bundle source against the newly deployed platform.
        if not force_pull:
            try:
                cache_status = await git_bundle_cache_status(
                    bundle_id=bid,
                    git_url=repo,
                    git_ref=entry.get("ref"),
                    git_subdir=entry.get("subdir"),
                    bundles_root=resolve_managed_bundles_root(),
                )
                if cache_status.current:
                    entry = dict(entry)
                    entry["path"] = str(cache_status.paths.bundle_root)
                    if cache_status.marker and cache_status.marker.get("commit"):
                        entry["git_commit"] = str(cache_status.marker.get("commit") or "")
                    out[bid] = entry
                    logger.info(
                        "Git bundle skip current: id=%s ref=%s subdir=%s path=%s source=%s",
                        bid,
                        entry.get("ref") or "head",
                        entry.get("subdir") or "",
                        entry.get("path"),
                        source,
                    )
                    skipped += 1
                    continue
                logger.warning(
                    "Git bundle cache not current: id=%s reason=%s ref=%s subdir=%s path=%s source=%s",
                    bid,
                    cache_status.reason,
                    entry.get("ref") or "head",
                    entry.get("subdir") or "",
                    str(cache_status.paths.bundle_root),
                    source,
                )
            except Exception:
                logger.exception(
                    "Git bundle cache validation failed: id=%s repo=%s ref=%s subdir=%s source=%s",
                    bid,
                    repo,
                    entry.get("ref"),
                    entry.get("subdir"),
                    source,
                )
        try:
            paths = await ensure_git_bundle(
                bundle_id=bid,
                git_url=repo,
                git_ref=entry.get("ref"),
                git_subdir=entry.get("subdir"),
                bundles_root=resolve_managed_bundles_root(),
                atomic=atomic,
            )
            entry = dict(entry)
            entry["path"] = str(paths.bundle_root)
            # best-effort commit capture
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "-C",
                    str(paths.repo_root),
                    "rev-parse",
                    "HEAD",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_b, _ = await proc.communicate()
                commit = stdout_b.decode("utf-8", errors="replace").strip() if proc.returncode == 0 else ""
                if commit:
                    entry["git_commit"] = commit
            except Exception:
                pass
            out[bid] = entry
            logger.info(
                "Git bundle resolved: id=%s path=%s repo_root=%s",
                bid,
                entry.get("path"),
                str(paths.repo_root),
            )
            resolved += 1
            if atomic:
                base_dir = bundle_dir_for_git(bid, entry.get("ref"), repo)
                await cleanup_old_git_bundles_async(
                    bundle_id=base_dir,
                    bundles_root=resolve_managed_bundles_root(),
                    active_paths=get_local_active_paths(),
                )
        except Exception:
            failed += 1
            logger.exception(
                "Git bundle resolve failed: id=%s repo=%s ref=%s subdir=%s source=%s",
                bid,
                repo,
                entry.get("ref"),
                entry.get("subdir"),
                source,
            )
            continue
    if total:
        logger.info(
            "Git bundle resolve summary: source=%s total=%s resolved=%s skipped=%s failed=%s",
            source,
            total,
            resolved,
            skipped,
            failed,
        )
    _warn_missing_local_path_bundles(out, source=source)
    return out


def _warn_missing_bundle_path_once(*, bundle_id: str, path_val: str, source: str, repo: Optional[str] = None) -> None:
    key = (bundle_id, path_val)
    with _REG_LOCK:
        if key in _MISSING_PATH_WARNED:
            return
        _MISSING_PATH_WARNED.add(key)
    kind = "git bundle" if repo else "local-path bundle"
    guidance = (
        "Path-only bundles require a mounted bundles root and are not portable to cloud/"
        "git-only deployments unless that path is explicitly provided."
        if not repo
        else "Ensure git bundles are prefetched during startup or registry updates."
    )
    logger.warning(
        "Bundle path missing for bundle_id=%s path=%s source=%s kind=%s repo=%s. %s",
        bundle_id,
        path_val,
        source,
        kind,
        repo or "<none>",
        guidance,
    )


def _warn_missing_local_path_bundles(reg: Dict[str, Dict[str, Any]], *, source: str) -> None:
    for entry in reg.values():
        repo = (entry.get("repo") or "").strip()
        path_val = (entry.get("path") or "").strip()
        if repo or not path_val:
            continue
        try:
            if not Path(path_val).exists():
                _warn_missing_bundle_path_once(
                    bundle_id=str(entry.get("id") or "<unknown>"),
                    path_val=path_val,
                    source=source,
                )
        except Exception:
            continue

def load_from_env() -> None:
    """
    Accept both shapes:
      1) {"default_bundle_id": "...", "bundles": { "<id>": {...}, ... }}
      2) flat dict: { "<id>": {...}, ... }
    """
    global _REGISTRY, _DEFAULT_ID
    with _REG_LOCK:
        data = _load_env_json(strict=False)
        if not data:
            _REGISTRY = {}
            _DEFAULT_ID = None
            return

        if isinstance(data, dict) and "bundles" in data:
            default_bundle_id = data.get("default_bundle_id")
            raw_bundles = data.get("bundles") or {}
        else:
            # env was just a mapping
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
        if any(entry.get("repo") for entry in reg.values()):
            logger.warning(
                "Sync load_from_env does not resolve git bundles; use the async registry load path "
                "so provider-backed git credentials can be resolved without blocking the event loop."
            )
            _warn_missing_local_path_bundles(reg, source="env")
        _REGISTRY = reg

        # resolve default
        if default_bundle_id and default_bundle_id in _REGISTRY:
            _DEFAULT_ID = default_bundle_id
        else:
            _DEFAULT_ID = ADMIN_BUNDLE_ID if ADMIN_BUNDLE_ID in _REGISTRY else next(iter(_REGISTRY.keys()), None)

def serialize_to_env(registry: Dict[str, Dict[str, Any]], default_id: Optional[str]) -> str:
    """Serialize the current in-memory mapping to a JSON payload."""
    with _REG_LOCK:
        registry = _ensure_admin_bundle(registry or {})
        payload = {
            "default_bundle_id": default_id if default_id in registry else ADMIN_BUNDLE_ID,
            "bundles": registry,
        }
        return json.dumps(payload, ensure_ascii=False)


def get_all() -> Dict[str, Dict[str, Any]]:
    with _REG_LOCK:
        return {k: dict(v) for k, v in _REGISTRY.items()}

def get_default_id() -> Optional[str]:
    with _REG_LOCK:
        return _DEFAULT_ID

def set_registry(
    registry: Dict[str, Dict[str, Any]],
    default_id: Optional[str],
    *,
    resolve_git: bool = True,
    source: str = "registry.set",
) -> None:
    global _REGISTRY, _DEFAULT_ID
    with _REG_LOCK:
        # normalize & replace
        new_reg: Dict[str, Dict[str, Any]] = {}
        for k, v in (registry or {}).items():
            item = _normalize({"id": k, **(v or {})})
            new_reg[item["id"]] = item
        new_reg = _ensure_admin_bundle(new_reg)
        if resolve_git:
            logger.warning(
                "Sync set_registry does not resolve git bundles; use await set_registry_async(...). "
                "The registry was updated without pulling git-backed bundle content."
            )
            _warn_missing_local_path_bundles(new_reg, source=source)
        _REGISTRY = new_reg
        _DEFAULT_ID = default_id if default_id in _REGISTRY else ADMIN_BUNDLE_ID

def upsert_bundles(
    partial: Dict[str, Dict[str, Any]],
    default_id: Optional[str],
    *,
    resolve_git: bool = True,
    source: str = "registry.upsert",
) -> None:
    """Merge update."""
    global _REGISTRY, _DEFAULT_ID
    with _REG_LOCK:
        reg = dict(_REGISTRY)
        for k, v in (partial or {}).items():
            item = _normalize({"id": k, **(v or {})})
            reg[item["id"]] = {**reg.get(item["id"], {}), **item}
        reg = _ensure_admin_bundle(reg)
        if resolve_git:
            logger.warning(
                "Sync upsert_bundles does not resolve git bundles; use await upsert_bundles_async(...). "
                "The registry was updated without pulling git-backed bundle content."
            )
            _warn_missing_local_path_bundles(reg, source=source)
        _REGISTRY = reg
        if default_id:
            _DEFAULT_ID = default_id if default_id in _REGISTRY else _DEFAULT_ID

def resolve_bundle(bundle_id: Optional[str], override: Optional[Dict[str, Any]] = None) -> Optional[BundleSpec]:
    """Return the effective BundleSpec from (id OR override)."""
    if override and (override.get("path") or override.get("repo")):
        d = _normalize({
            "id": override.get("id") or "override",
            "path": override.get("path") or "",
            "module": override.get("module"),
            "singleton": bool(override.get("singleton", False)),
            "name": override.get("name"),
            "description": override.get("description"),
            "repo": override.get("repo"),
            "ref": override.get("ref"),
            "subdir": override.get("subdir"),
        })
        return BundleSpec(**d)

    with _REG_LOCK:
        bid = bundle_id or _DEFAULT_ID
        if not bid or bid not in _REGISTRY:
            return None
        spec_dict = dict(_REGISTRY[bid])

    # Git bundle resolution is a controlled operation performed during registry
    # sync/update (load_from_env/set_registry/upsert). Avoid pulling on every
    # request-level resolve.
    repo = spec_dict.get("repo")
    try:
        from pathlib import Path as _Path
        path_val = (spec_dict.get("path") or "").strip()
        if path_val and not _Path(path_val).exists():
            _warn_missing_bundle_path_once(
                bundle_id=str(spec_dict.get("id") or "<unknown>"),
                path_val=path_val,
                source="resolve_bundle",
                repo=repo,
            )
    except Exception:
        pass

    return BundleSpec(**spec_dict)

def _load_env_json(strict: bool) -> Optional[Dict[str, Any]]:
    authority_path = str(os.getenv("BUNDLES_YAML_DESCRIPTOR_PATH") or "/config/bundles.yaml").strip()
    if not authority_path:
        if strict:
            raise ValueError("BUNDLES_YAML_DESCRIPTOR_PATH is not set")
        return None
    path = Path(authority_path).expanduser()
    if not path.exists():
        if strict:
            raise ValueError(f"Bundle descriptor file not found: {path}")
        return None
    raw = path.read_text().strip()
    if not raw:
        if strict:
            raise ValueError(f"Bundle descriptor file is empty: {path}")
        return None
    if path.suffix.lower() in {".yml", ".yaml"}:
        import yaml  # type: ignore

        return yaml.safe_load(raw)
    return json.loads(raw)


async def resolve_bundle_async(bundle_id: Optional[str], override: Optional[Dict[str, Any]] = None) -> Optional[BundleSpec]:
    """Async wrapper around resolve_bundle (runs in thread pool)."""
    return await asyncio.to_thread(resolve_bundle, bundle_id, override)


async def set_registry_async(
    registry: Dict[str, Dict[str, Any]],
    default_id: Optional[str],
    *,
    resolve_git: bool = True,
    source: str = "registry.set",
) -> None:
    """Replace the in-memory registry and resolve git-backed bundles through async git/auth paths."""
    global _REGISTRY, _DEFAULT_ID
    new_reg: Dict[str, Dict[str, Any]] = {}
    for k, v in (registry or {}).items():
        item = _normalize({"id": k, **(v or {})})
        new_reg[item["id"]] = item
    new_reg = _ensure_admin_bundle(new_reg)
    if resolve_git:
        new_reg = await _apply_git_resolution(new_reg, source=source)
    with _REG_LOCK:
        _REGISTRY = new_reg
        _DEFAULT_ID = default_id if default_id in _REGISTRY else ADMIN_BUNDLE_ID



async def upsert_bundles_async(
    partial: Dict[str, Dict[str, Any]],
    default_id: Optional[str],
    *,
    resolve_git: bool = True,
    source: str = "registry.upsert",
) -> None:
    """Merge bundle registry entries and resolve git-backed bundles through async git/auth paths."""
    global _REGISTRY, _DEFAULT_ID
    with _REG_LOCK:
        reg = dict(_REGISTRY)
    for k, v in (partial or {}).items():
        item = _normalize({"id": k, **(v or {})})
        reg[item["id"]] = {**reg.get(item["id"], {}), **item}
    reg = _ensure_admin_bundle(reg)
    if resolve_git:
        reg = await _apply_git_resolution(reg, source=source)
    with _REG_LOCK:
        _REGISTRY = reg
        if default_id:
            _DEFAULT_ID = default_id if default_id in _REGISTRY else _DEFAULT_ID


async def load_registry(redis, logger):
    try:
        from kdcube_ai_app.infra.plugin.bundle_store import load_registry as _load_store

        persisted = await _load_store(redis)  # tenant/project inferred from env
        await set_registry_async(
            {bid: be.model_dump() for bid, be in persisted.bundles.items()},
            persisted.default_bundle_id
        )
        logger.info(f"Bundle mapping synced from active registry: {len(persisted.bundles)} bundles (default={persisted.default_bundle_id})")
    except Exception as _e:
        logger.warning(f"Could not sync bundles from active registry; using env-only: {_e}")


def get_registry_redis_client(runtime_ctx) -> Optional[object]:
    """
    Best-effort access to the Redis client used by chat ingress/API surfaces.
    """
    redis_client = getattr(runtime_ctx, "redis_async", None)
    if redis_client is not None:
        return redis_client
    middleware = getattr(runtime_ctx, "middleware", None)
    return getattr(middleware, "redis", None)


async def load_persisted_registry_from_runtime_ctx(
    runtime_ctx,
    tenant: str,
    project: str,
) -> Optional[object]:
    """
    Read the active bundle registry for the given tenant/project from the
    configured runtime registry store without touching process-local _REGISTRY.

    Proc owns descriptor-authority refresh and cache publication. Non-proc
    components read descriptor authority in read-only mode first and fall back
    to the published Redis snapshot; they must not acquire descriptor write
    locks or mutate descriptor/cache state.
    """
    component = (os.getenv("GATEWAY_COMPONENT") or "proc").strip().lower()
    if component != "proc":
        try:
            reg = await _load_store_registry_readonly(tenant, project)
            if reg and getattr(reg, "bundles", None):
                return reg
        except Exception as e:
            logger.warning(
                "Failed to read active bundle registry from read-only authority "
                "(tenant=%s project=%s component=%s): %s",
                tenant,
                project,
                component,
                e,
            )

        redis_client = get_registry_redis_client(runtime_ctx)
        if not redis_client:
            logger.error(
                "Bundle registry unavailable: no Redis client on runtime context "
                "and read-only authority did not return a registry "
                "(tenant=%s project=%s component=%s)",
                tenant,
                project,
                component,
            )
            return None

        try:
            from kdcube_ai_app.infra.plugin.bundle_store import BundlesRegistry, redis_key

            raw = await redis_client.get(redis_key(tenant, project))
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                reg = BundlesRegistry.model_validate_json(raw)
                if reg and reg.bundles:
                    return reg
        except Exception as e:
            logger.warning(
                "Failed to read cached bundle registry snapshot "
                "(tenant=%s project=%s component=%s): %s",
                tenant,
                project,
                component,
                e,
            )
            return None

        logger.error(
            "Active bundle registry cache missing/empty "
            "(tenant=%s project=%s component=%s). Non-proc components do not load bundle descriptors.",
            tenant,
            project,
            component,
        )
        return None

    redis_client = get_registry_redis_client(runtime_ctx)
    if not redis_client:
        logger.error(
            "Bundle registry unavailable: no Redis client on runtime context "
            "(tenant=%s project=%s component=%s)",
            tenant,
            project,
            component,
        )
        return None

    try:
        reg = await _load_store_registry(redis_client, tenant, project)
    except Exception as e:
        logger.warning(
            "Failed to load active bundle registry (tenant=%s project=%s): %s",
            tenant,
            project,
            e,
        )
        return None

    if reg and reg.bundles:
        return reg

    logger.error(
        "Active bundle registry missing/empty (tenant=%s project=%s)",
        tenant,
        project,
    )
    return None


async def resolve_default_bundle_id_from_runtime_ctx(
    runtime_ctx,
    tenant: str,
    project: str,
) -> Optional[str]:
    reg = await load_persisted_registry_from_runtime_ctx(runtime_ctx, tenant, project)
    if not reg:
        return None

    default_bundle_id = getattr(reg, "default_bundle_id", None)
    bundles = getattr(reg, "bundles", None) or {}
    if default_bundle_id and default_bundle_id in bundles:
        return default_bundle_id

    logger.error(
        "Default bundle id missing or invalid in active registry "
        "(tenant=%s project=%s default=%s)",
        tenant,
        project,
        default_bundle_id,
    )
    return None
