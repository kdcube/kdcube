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

def _apply_git_resolution(reg: Dict[str, Dict[str, Any]], source: str = "unknown") -> Dict[str, Dict[str, Any]]:
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
                cleanup_old_git_bundles,
                bundle_dir_for_git,
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
        # Skip git resolution if path already exists and we are not forcing pull.
        if not force_pull:
            path_val = (entry.get("path") or "").strip()
            if path_val:
                try:
                    from pathlib import Path as _Path
                    if _Path(path_val).exists():
                        logger.info(
                            "Git bundle skip (path exists): id=%s path=%s source=%s",
                            bid,
                            path_val,
                            source,
                        )
                        skipped += 1
                        continue
                except Exception:
                    pass
        try:
            paths = ensure_git_bundle(
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
                import subprocess
                proc = subprocess.run(
                    ["git", "-C", str(paths.repo_root), "rev-parse", "HEAD"],
                    check=True, capture_output=True, text=True,
                )
                commit = (proc.stdout or "").strip()
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
                cleanup_old_git_bundles(
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
        reg = _apply_git_resolution(reg, source="env")
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
            new_reg = _apply_git_resolution(new_reg, source=source)
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
            reg = _apply_git_resolution(reg, source=source)
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
    """Async wrapper around set_registry (runs in thread pool)."""
    await asyncio.to_thread(set_registry, registry, default_id, resolve_git=resolve_git, source=source)



async def upsert_bundles_async(partial: Dict[str, Dict[str, Any]], default_id: Optional[str]) -> None:
    """Async wrapper around upsert_bundles (runs in thread pool)."""
    await asyncio.to_thread(upsert_bundles, partial, default_id)


async def load_registry(redis, logger):
    try:
        from kdcube_ai_app.infra.plugin.bundle_store import load_registry as _load_store

        persisted = await _load_store(redis)  # tenant/project inferred from env
        await set_registry_async(
            {bid: be.model_dump() for bid, be in persisted.bundles.items()},
            persisted.default_bundle_id
        )
        logger.info(f"Bundle mapping synced from Redis: {len(persisted.bundles)} bundles (default={persisted.default_bundle_id})")
    except Exception as _e:
        logger.warning(f"Could not sync bundles from Redis; using env-only: {_e}")


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
    Read the persisted bundle registry for the given tenant/project directly
    from Redis-backed storage without touching the process-local registry.
    """
    redis_client = get_registry_redis_client(runtime_ctx)
    if not redis_client:
        logger.error(
            "Bundle registry unavailable: no Redis client on runtime context "
            "(tenant=%s project=%s)",
            tenant,
            project,
        )
        return None

    try:
        reg = await _load_store_registry(redis_client, tenant, project)
    except Exception as e:
        logger.warning(
            "Failed to load bundle registry from Redis (tenant=%s project=%s): %s",
            tenant,
            project,
            e,
        )
        return None

    if reg and reg.bundles:
        return reg

    logger.error(
        "Bundle registry missing/empty in Redis (tenant=%s project=%s)",
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
        "Default bundle id missing or invalid in Redis registry "
        "(tenant=%s project=%s default=%s)",
        tenant,
        project,
        default_bundle_id,
    )
    return None
