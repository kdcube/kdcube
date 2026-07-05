# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
# kdcube_ai_app/infra/plugin/bundle_store.py

from __future__ import annotations
import ast
import asyncio
import copy
import hashlib
import inspect
import json, os, time, uuid, threading
import logging
import shutil
import fcntl
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, Any, Set
from pathlib import Path
from pydantic import BaseModel, Field, ValidationError
import kdcube_ai_app.infra.namespaces as namespaces
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.runtime.external.service_discovery import _is_running_in_docker
from kdcube_ai_app.apps.chat.sdk.runtime.external.distributed_snapshot import compute_dir_sha256
from kdcube_ai_app.infra.secrets.manager import (
    _bundle_descriptor_items as _secrets_bundle_descriptor_items,
    _find_bundle_item as _secrets_find_bundle_item,
    _load_yaml_mapping_from_storage,
    _write_yaml_mapping_to_storage,
)
from kdcube_ai_app.storage.observed_file_locks import observed_file_lock

REDIS_KEY_FMT = namespaces.CONFIG.BUNDLES.BUNDLE_MAPPING_KEY_FMT
REDIS_CHANNEL_FMT = namespaces.CONFIG.BUNDLES.UPDATE_CHANNEL
ADMIN_BUNDLE_ID = "kdcube.admin"
_EXAMPLES_REL_PATH = Path("apps/chat/sdk/examples/bundles")
_DEFAULT_MANAGED_BUNDLES_ROOT = Path("/managed-bundles")
_MANAGED_BUNDLE_METADATA_FILE = ".kdcube-managed-bundle.json"
_BUNDLE_PROPS_LOCK_KEY_FMT = "bundle:props:write:{tenant}:{project}:{bundle_id}"
_BUNDLE_PROPS_LOCK_TTL_SECONDS = 30
_BUNDLE_PROPS_LOCK_WAIT_SECONDS = 10.0
_log = logging.getLogger(__name__)

# ── per-process descriptor read caches ────────────────────────────────────────
#
# The bundle descriptor file (bundles.yaml) is the authority; every read still
# stat()s it, and any content change (mtime_ns/size) triggers a fresh read +
# parse in every process. Only the redundant YAML re-parse of an UNCHANGED file
# is skipped. The cache stores the RAW parsed mapping (secret refs intact) —
# secret-ref resolution (`_resolve_props_node`) still runs on every read, so
# resolved secret values are never captured in the cache.
#
# Admin mutation paths (save_registry / set_bundle_props / put/patch props) all
# rewrite the descriptor file through _write_mapping (atomic tmp+rename), which
# both changes the stat key and explicitly pops this cache.
_DESCRIPTOR_MAPPING_CACHE: Dict[str, Tuple[Tuple[int, int], Dict[str, Any]]] = {}
_DESCRIPTOR_MAPPING_CACHE_LOCK = threading.Lock()

# Per-process guard for the Redis write-back done on descriptor reads.
# Maps redis key -> (descriptor stat key, monotonic ts of last write-back).
# The write-back is skipped only while the descriptor is unchanged AND the last
# write-back is younger than the refresh interval; the periodic re-assert
# self-heals Redis after a flush/restart. Keys carry no TTL in Redis, so the
# write-back is not a TTL heartbeat (verified: plain SET, no EX).
_REDIS_WRITEBACK_REFRESH_SECONDS = 60.0
_REDIS_WRITEBACK_STATE: Dict[str, Tuple[Tuple[int, int], float]] = {}
_REDIS_WRITEBACK_STATE_LOCK = threading.Lock()


def _redis_writeback_due(key: str, state: Optional[Tuple[int, int]]) -> bool:
    """True when the Redis write-back for `key` should run."""
    if state is None:
        return True
    with _REDIS_WRITEBACK_STATE_LOCK:
        entry = _REDIS_WRITEBACK_STATE.get(key)
    if entry is None:
        return True
    prev_state, ts = entry
    if prev_state != state:
        return True
    return (time.monotonic() - ts) >= _REDIS_WRITEBACK_REFRESH_SECONDS


def _record_redis_writeback(key: str, state: Optional[Tuple[int, int]]) -> None:
    if state is None:
        return
    with _REDIS_WRITEBACK_STATE_LOCK:
        _REDIS_WRITEBACK_STATE[key] = (state, time.monotonic())


def clear_descriptor_read_caches() -> None:
    """Drop per-process descriptor read caches (tests / explicit admin reset)."""
    with _DESCRIPTOR_MAPPING_CACHE_LOCK:
        _DESCRIPTOR_MAPPING_CACHE.clear()
    with _REDIS_WRITEBACK_STATE_LOCK:
        _REDIS_WRITEBACK_STATE.clear()


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

def _shared_bundles_root() -> Path:
    try:
        from kdcube_ai_app.infra.plugin.git_bundle import resolve_managed_bundles_root

        return resolve_managed_bundles_root()
    except Exception:
        return _DEFAULT_MANAGED_BUNDLES_ROOT

def _example_bundle_lock_path(bundle_name: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".", "@") else "-" for ch in bundle_name)
    lock_dir = _shared_bundles_root() / ".example-bundle-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{safe}.lock"

def _instance_id_for_lock_metadata() -> str:
    try:
        instance_id = str(get_settings().INSTANCE_ID or "").strip()
        if instance_id:
            return instance_id
    except Exception:
        pass
    return os.environ.get("INSTANCE_ID") or os.environ.get("HOSTNAME") or "unknown"

def _sanitize_example_version_part(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in (value or ""))
    safe = safe.strip("-_.")
    return safe or "unknown"

def _current_platform_ref() -> Optional[str]:
    direct = (
        os.getenv("PLATFORM_REF")
        or os.getenv("APP_IMAGE_TAG")
        or os.getenv("IMAGE_TAG")
        or ""
    ).strip()
    if direct:
        return _sanitize_example_version_part(direct)

    image_ref = (get_settings().PLATFORM.EXEC.PY.PY_CODE_EXEC_IMAGE or "").strip()
    if image_ref and ":" in image_ref:
        tail = image_ref.rsplit(":", 1)[-1].strip()
        if tail and "/" not in tail:
            return _sanitize_example_version_part(tail)
    return None

def _shared_example_timestamp_suffix(now: Optional[datetime] = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y%m%d%H%M%S") + f"{value.microsecond // 1000:03d}"

def _shared_example_bundle_prefix(bundle_name: str, version: str) -> str:
    platform_ref = _current_platform_ref()
    if platform_ref:
        return f"{bundle_name}__{platform_ref}__{version[:12]}"
    return f"{bundle_name}__{version[:12]}"

def _managed_bundle_created_sort_key(path: Path) -> tuple[float, float]:
    metadata_path = path / _MANAGED_BUNDLE_METADATA_FILE
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        created_epoch = data.get("created_at_epoch")
        if isinstance(created_epoch, (int, float)):
            return float(created_epoch), path.stat().st_mtime if path.exists() else 0.0
    except Exception:
        pass

    suffix = path.name.rsplit("__", 1)[-1]
    if suffix.isdigit() and len(suffix) >= 14:
        try:
            created_dt = datetime.strptime(suffix[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            created_epoch = created_dt.timestamp()
            if len(suffix) > 14:
                created_epoch += int(suffix[14:17].ljust(3, "0")) / 1000.0
            return created_epoch, path.stat().st_mtime if path.exists() else 0.0
        except Exception:
            pass

    try:
        mtime = path.stat().st_mtime
    except Exception:
        mtime = 0.0
    return mtime, mtime

def _find_existing_shared_example_bundle(root: Path, prefix: str) -> Optional[Path]:
    if not root.exists():
        return None
    candidates: list[Path] = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        if not path.name.startswith(f"{prefix}__"):
            continue
        if not (path / "entrypoint.py").exists():
            continue
        candidates.append(path)
    if not candidates:
        return None
    candidates.sort(key=_managed_bundle_created_sort_key, reverse=True)
    return candidates[0]

def _shared_example_bundle_dir(bundle_name: str, version: str) -> Path:
    root = _shared_bundles_root()
    prefix = _shared_example_bundle_prefix(bundle_name, version)
    existing = _find_existing_shared_example_bundle(root, prefix)
    if existing is not None:
        return existing
    return root / f"{prefix}__{_shared_example_timestamp_suffix()}"

def _write_shared_example_bundle_metadata(
    *,
    bundle_root: Path,
    dest_root: Path,
    version: str,
    recorded_dest_root: Optional[Path] = None,
) -> None:
    now = datetime.now(timezone.utc)
    payload = {
        "kind": "shared_example_bundle",
        "bundle_name": bundle_root.name,
        "source_path": str(bundle_root),
        "dest_path": str(recorded_dest_root or dest_root),
        "platform_ref": _current_platform_ref(),
        "content_hash": version,
        "content_hash_prefix": version[:12],
        "created_at": now.isoformat(),
        "created_at_epoch": now.timestamp(),
    }
    metadata_path = dest_root / _MANAGED_BUNDLE_METADATA_FILE
    tmp_path = dest_root / f".{_MANAGED_BUNDLE_METADATA_FILE}.tmp-{os.getpid()}-{time.time_ns()}"
    try:
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(metadata_path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

def cleanup_old_shared_example_bundles(
    *,
    bundle_id: str,
    bundles_root: Optional[Path] = None,
    keep: Optional[int] = None,
    ttl_hours: Optional[int] = None,
    active_paths: Optional[set[str] | list[str] | tuple[str, ...]] = None,
) -> int:
    root = bundles_root or _shared_bundles_root()
    if not root.exists():
        return 0

    keep = keep if keep is not None else get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_KEEP
    ttl_hours = ttl_hours if ttl_hours is not None else get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_TTL_HOURS
    active_set: set[Path] = set()
    for ap in active_paths or ():
        try:
            active_set.add(Path(ap).resolve())
        except Exception:
            continue

    def _is_candidate_dir(path: Path) -> bool:
        return path.name == bundle_id or path.name.startswith(f"{bundle_id}__")

    def _is_active_dir(path: Path) -> bool:
        if not active_set:
            return False
        for ap in active_set:
            try:
                ap.relative_to(path)
                return True
            except Exception:
                continue
        return False

    candidates: list[Path] = []
    for p in root.iterdir():
        if not p.is_dir():
            continue
        if not _is_candidate_dir(p):
            continue
        candidates.append(p)

    candidates.sort(key=_managed_bundle_created_sort_key, reverse=True)
    removed = 0

    if ttl_hours and ttl_hours > 0:
        cutoff = time.time() - (ttl_hours * 3600)
        for p in list(candidates):
            try:
                if _is_active_dir(p):
                    continue
                created_at, _mtime = _managed_bundle_created_sort_key(p)
                if created_at < cutoff:
                    shutil.rmtree(p, ignore_errors=True)
                    removed += 1
                    candidates = [c for c in candidates if c != p]
            except Exception:
                continue

    for p in candidates[keep:]:
        try:
            if _is_active_dir(p):
                continue
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
        except Exception:
            continue

    if removed:
        _log.info("Cleaned %s old shared example bundle dirs for %s", removed, bundle_id)
    return removed

# Example bundles are baked into the image, so the expensive share pass
# (content hash + EFS lock/fsync + shared-root readdir) is memoized per
# process, keyed by a cheap stat signature of the SOURCE directory. The
# signature (relpath, size, mtime_ns of every file; no content reads, no EFS
# access) detects any source change, so anything that mutates the source
# mid-process still triggers a full re-share — the memo only ever skips work
# for a provably unchanged source. Only SUCCESSFUL shares are memoized (the
# exception fallback is retried on the next call), and a hit is re-validated
# with one entrypoint stat so an out-of-band cleanup of the shared copy
# self-heals instead of pinning a dangling path. Admin/runtime mutations never
# rewrite the shared code copy in place (bundle props/refs live in the
# descriptor file), so no other invalidation channel is needed.
_EXAMPLE_SHARE_CACHE: Dict[str, Tuple[Tuple, Path]] = {}
_EXAMPLE_SHARE_CACHE_LOCK = threading.Lock()


def clear_example_share_cache() -> None:
    with _EXAMPLE_SHARE_CACHE_LOCK:
        _EXAMPLE_SHARE_CACHE.clear()


def _dir_stat_signature(root: Path) -> Tuple:
    """Stat-only change signature of a directory tree (no content reads)."""
    entries = []
    for p in sorted(root.rglob("*")):
        try:
            if not p.is_file():
                continue
            st = p.stat()
        except OSError:
            continue
        entries.append((str(p.relative_to(root)), st.st_size, st.st_mtime_ns))
    return tuple(entries)


def _ensure_example_bundle_shared(bundle_root: Path) -> Path:
    """
    If running in Docker, copy example bundles from the image into the shared
    managed bundles root so sibling containers can mount them.

    The resolved shared path is memoized per process keyed by a stat signature
    of the source dir; the first call (and any source change) does the real
    hashing/locking work.
    """
    if not _is_running_in_docker():
        return bundle_root

    cache_key = str(bundle_root)
    signature = _dir_stat_signature(bundle_root)
    with _EXAMPLE_SHARE_CACHE_LOCK:
        cached = _EXAMPLE_SHARE_CACHE.get(cache_key)
    if cached is not None and cached[0] == signature:
        cached_path = cached[1]
        try:
            if (cached_path / "entrypoint.py").exists():
                return cached_path
        except OSError:
            pass
        with _EXAMPLE_SHARE_CACHE_LOCK:
            _EXAMPLE_SHARE_CACHE.pop(cache_key, None)

    shared = _share_example_bundle(bundle_root)
    if shared is not None:
        with _EXAMPLE_SHARE_CACHE_LOCK:
            _EXAMPLE_SHARE_CACHE[cache_key] = (signature, shared)
        return shared
    return bundle_root


def _share_example_bundle(bundle_root: Path) -> Path | None:
    """One real share pass; returns the shared path or None on failure."""
    try:
        version = compute_dir_sha256(bundle_root, skip_files=set())
        lock_path = _example_bundle_lock_path(bundle_root.name)
        with observed_file_lock(
            lock_path=lock_path,
            resource_id=f"example-bundle:{bundle_root.name}:{version[:12]}",
            operation="example.bundle.materialize",
            instance_id=_instance_id_for_lock_metadata(),
            wait_seconds=300,
        ):
            dest_root = _shared_example_bundle_dir(bundle_root.name, version)
            if dest_root.exists() and (dest_root / "entrypoint.py").exists():
                if not (dest_root / _MANAGED_BUNDLE_METADATA_FILE).exists():
                    _write_shared_example_bundle_metadata(bundle_root=bundle_root, dest_root=dest_root, version=version)
                return dest_root

            dest_root.parent.mkdir(parents=True, exist_ok=True)
            tmp_root = dest_root.parent / f".{dest_root.name}.tmp-{os.getpid()}-{time.time_ns()}"
            try:
                shutil.copytree(bundle_root, tmp_root, dirs_exist_ok=False)
                _write_shared_example_bundle_metadata(
                    bundle_root=bundle_root,
                    dest_root=tmp_root,
                    version=version,
                    recorded_dest_root=dest_root,
                )
                tmp_root.replace(dest_root)
            finally:
                if tmp_root.exists():
                    shutil.rmtree(tmp_root, ignore_errors=True)

            _log.info(
                "Copied example bundle to shared versioned root: %s -> %s (sha=%s)",
                bundle_root,
                dest_root,
                version[:12],
            )
            return dest_root
    except Exception as exc:
        fallback_dest = locals().get("dest_root", bundle_root)
        _log.warning("Failed to copy example bundle to %s: %s", fallback_dest, exc)
        return None

def _examples_enabled() -> bool:
    return bool(get_settings().PLATFORM.APPLICATIONS.BUNDLES_INCLUDE_EXAMPLES)

# Example entrypoints live in the immutable image; memoize the ast.parse-based
# BUNDLE_ID discovery per process (it previously ran per registry read).
_DECLARED_EXAMPLE_ID_CACHE: Dict[str, Optional[str]] = {}


def _declared_example_bundle_id(bundle_root: Path) -> Optional[str]:
    entrypoint = bundle_root / "entrypoint.py"
    cache_key = str(entrypoint)
    if cache_key in _DECLARED_EXAMPLE_ID_CACHE:
        return _DECLARED_EXAMPLE_ID_CACHE[cache_key]
    result = _parse_declared_example_bundle_id(entrypoint)
    _DECLARED_EXAMPLE_ID_CACHE[cache_key] = result
    return result


def _parse_declared_example_bundle_id(entrypoint: Path) -> Optional[str]:
    try:
        tree = ast.parse(entrypoint.read_text(encoding="utf-8"), filename=str(entrypoint))
    except Exception:
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "BUNDLE_ID" for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            value = node.value.value.strip()
            return value or None
    return None

def _example_bundle_id_candidates(bundle_root: Path) -> Set[str]:
    ids = {bundle_root.name}
    declared = _declared_example_bundle_id(bundle_root)
    if declared:
        ids.add(declared)
    return ids

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
        bundle_path = _ensure_example_bundle_shared(item)
        try:
            from kdcube_ai_app.infra.plugin.bundle_loader import get_declared_bundle_id
            bid = get_declared_bundle_id(bundle_path, "entrypoint") or item.name
        except Exception:
            bid = item.name
        bundles[bid] = BundleEntry(
            id=bid,
            name=bid,
            path=str(bundle_path),
            module="entrypoint",
            singleton=False,
            description="Built-in example bundle",
        )
    return bundles

def _discover_example_bundle_ids(*, respect_enabled: bool = True) -> Set[str]:
    if respect_enabled and not _examples_enabled():
        return set()
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
        ids.update(_example_bundle_id_candidates(item))
    return ids

def _reserved_bundle_ids() -> Set[str]:
    # Always reserve built-in admin bundle and example bundle ids.
    ids = {ADMIN_BUNDLE_ID}
    try:
        ids.update(_discover_example_bundle_ids(respect_enabled=True))
    except Exception:
        pass
    return ids

def _is_example_bundle_id(bundle_id: str) -> bool:
    bid = str(bundle_id or "").strip()
    if not bid:
        return False
    try:
        return bid in _discover_example_bundle_ids(respect_enabled=False)
    except Exception:
        return False

def _drop_disabled_example_bundle_entries(
    bundles_dict: Dict[str, Dict[str, Any]],
    props_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    props_map = dict(props_map or {})
    if _examples_enabled():
        return bundles_dict, props_map
    example_ids = _discover_example_bundle_ids(respect_enabled=False)
    if not example_ids:
        return bundles_dict, props_map
    filtered = {
        bid: value
        for bid, value in (bundles_dict or {}).items()
        if str(bid or "").strip() not in example_ids
    }
    filtered_props = {
        bid: value
        for bid, value in props_map.items()
        if str(bid or "").strip() not in example_ids
    }
    return filtered, filtered_props

def _reserved_bundle_entry(bid: str) -> Optional["BundleEntry"]:
    if bid == ADMIN_BUNDLE_ID:
        return _admin_bundle_entry()
    if not _examples_enabled() or not _is_example_bundle_id(bid):
        return None
    root = _examples_root()
    if not root.exists():
        return None
    # First try direct directory match (bid == dir name, no @bundle_id decorator).
    candidate = root / bid
    if candidate.is_dir() and (candidate / "entrypoint.py").exists():
        candidate = _ensure_example_bundle_shared(candidate)
        return BundleEntry(
            id=bid,
            name=bid,
            path=str(candidate),
            module="entrypoint",
            singleton=False,
            description="Built-in example bundle",
        )
    # Fallback: scan all example dirs and check declared @bundle_id.
    try:
        from kdcube_ai_app.infra.plugin.bundle_loader import get_declared_bundle_id
        for item in root.iterdir():
            if not item.is_dir() or item.name in {"data", "__pycache__"}:
                continue
            if not (item / "entrypoint.py").exists():
                continue
            bundle_path = _ensure_example_bundle_shared(item)
            if get_declared_bundle_id(bundle_path, "entrypoint") == bid or bid in _example_bundle_id_candidates(item):
                return BundleEntry(
                    id=bid,
                    name=bid,
                    path=str(bundle_path),
                    module="entrypoint",
                    singleton=False,
                    description="Built-in example bundle",
                )
    except Exception:
        pass
    return None

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


async def get_bundle_props(
    redis,
    *,
    tenant: str,
    project: str,
    bundle_id: str,
) -> Dict[str, Any]:
    """Return bundle props from descriptor authority, falling back to Redis."""
    key = _props_key(tenant=tenant, project=project, bundle_id=bundle_id)
    store = _get_authoritative_bundle_store(tenant, project)
    if store is not None:
        props = store.load_bundle_props(bundle_id)
        props = dict(props or {})
        state = None
        state_fn = getattr(store, "descriptor_state_key", None)
        if callable(state_fn):
            state = state_fn()
        if _redis_writeback_due(key, state):
            await redis.set(key, json.dumps(props, ensure_ascii=False))
            _record_redis_writeback(key, state)
        return props
    raw = await redis.get(key)
    if not raw:
        return {}
    return json.loads(raw)


def _get_bundle_props_from_authority(
    *,
    tenant: str,
    project: str,
    bundle_id: str,
) -> Dict[str, Any] | None:
    store = _get_authoritative_bundle_store(tenant, project)
    if store is None:
        return None
    props = store.load_bundle_props(bundle_id)
    return dict(props or {})


def resolve_dot_path(obj: Any, path: str) -> Any:
    """
    Resolve a dot-separated path into a nested dict, e.g.
    ``resolve_dot_path(props, "apps.some_app.routines.cron")`` returns the value
    at ``props["apps"]["some_app"]["routines"]["cron"]``, or ``None`` if any
    segment is missing or the traversal hits a non-dict node.
    """
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _decode_redis_key(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore")
    return str(raw)


async def _iter_matching_keys(redis, pattern: str):
    scan_iter = getattr(redis, "scan_iter", None)
    if callable(scan_iter):
        async for key in scan_iter(match=pattern):
            yield _decode_redis_key(key)
        return

    keys_fn = getattr(redis, "keys", None)
    if callable(keys_fn):
        keys = await keys_fn(pattern)
        for key in keys or []:
            yield _decode_redis_key(key)


async def _sync_bundle_props_authoritative(
    redis,
    *,
    tenant: str,
    project: str,
    props_map: Dict[str, Dict[str, Any]],
) -> None:
    """
    Make Redis bundle props exactly match the descriptor-provided props for this
    tenant/project scope.

    This is used by explicit descriptor-authority reset paths where bundles.yaml
    is the source of truth for bundle-level config overrides.
    """
    prefix = _props_key(tenant=tenant, project=project, bundle_id="")
    pattern = f"{prefix}*"
    desired: Dict[str, str] = {}

    for bid, props in (props_map or {}).items():
        if props is None:
            continue
        key = _props_key(tenant=tenant, project=project, bundle_id=bid)
        desired[key] = json.dumps(props, ensure_ascii=False)

    existing = set()
    async for key in _iter_matching_keys(redis, pattern):
        existing.add(key)

    for key in existing:
        if key not in desired:
            await redis.delete(key)

    for key, payload in desired.items():
        await redis.set(key, payload)

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
    # Evict stale registry entries whose path matches a current example bundle
    # but whose id differs (e.g. bundle was renamed via @bundle_id decorator).
    example_paths = {entry.path for entry in examples.values()}
    example_ids = set(examples.keys())
    for bid in list(merged.bundles.keys()):
        if bid in example_ids:
            continue
        if merged.bundles[bid].path in example_paths:
            del merged.bundles[bid]
            updated = True
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


def bundle_entry_to_spec(entry: BundleEntry):
    from kdcube_ai_app.infra.plugin.bundle_registry import BundleSpec

    return BundleSpec(
        id=entry.id,
        name=entry.name,
        path=entry.path,
        module=entry.module,
        singleton=bool(entry.singleton),
        description=entry.description,
        repo=entry.repo,
        ref=entry.ref,
        subdir=entry.subdir,
        git_commit=entry.git_commit,
    )


async def resolve_bundle_spec_from_store(
    redis,
    *,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
    bundle_id: Optional[str] = None,
    override: Optional[Dict[str, Any]] = None,
):
    """
    Resolve a BundleSpec from the active bundle registry.

    In file-backed deployments this rereads bundles.yaml through load_registry
    and refreshes Redis only as a runtime cache. Request paths should resolve
    here rather than from process-local _REGISTRY.
    """
    if override and (override.get("path") or override.get("repo")):
        return bundle_entry_to_spec(_to_entry(str(override.get("id") or "override"), override))

    reg = await load_registry(redis, tenant, project)
    bid = str(bundle_id or reg.default_bundle_id or "").strip()
    if not bid:
        return None
    entry = (reg.bundles or {}).get(bid)
    if entry is None:
        return None
    return bundle_entry_to_spec(entry)


def _deployment_bundle_ids(reg: "BundlesRegistry") -> list[str]:
    reserved = _reserved_bundle_ids()
    return [bid for bid in reg.bundles.keys() if bid not in reserved]


def _deployment_default_bundle_id(reg: "BundlesRegistry") -> Optional[str]:
    deployment_ids = _deployment_bundle_ids(reg)
    default_id = str(reg.default_bundle_id or "").strip()
    if default_id and default_id in deployment_ids:
        return default_id
    return deployment_ids[0] if deployment_ids else None


def _persisted_bundle_ids(
    reg: "BundlesRegistry",
    *,
    props_map: Optional[Dict[str, Dict[str, Any]]] = None,
    existing_ids: Optional[Set[str] | list[str] | tuple[str, ...]] = None,
) -> list[str]:
    reserved = _reserved_bundle_ids()
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(bid: Optional[str]) -> None:
        bid_norm = str(bid or "").strip()
        if not bid_norm or bid_norm in seen:
            return
        entry = reg.bundles.get(bid_norm)
        if entry is None and bid_norm in reserved:
            entry = _reserved_bundle_entry(bid_norm)
        if entry is None:
            return
        seen.add(bid_norm)
        ordered.append(bid_norm)

    for bid in reg.bundles.keys():
        if bid not in reserved:
            _add(bid)

    for bid in existing_ids or ():
        if bid in reserved:
            _add(bid)

    for bid in (props_map or {}).keys():
        if bid in reserved:
            _add(bid)

    default_id = str(reg.default_bundle_id or "").strip()
    if default_id and default_id in reserved:
        _add(default_id)

    return ordered


def _persisted_default_bundle_id(
    reg: "BundlesRegistry",
    *,
    persisted_ids: Set[str] | list[str] | tuple[str, ...],
) -> Optional[str]:
    default_id = str(reg.default_bundle_id or "").strip()
    if default_id and default_id in set(persisted_ids):
        return default_id
    return _deployment_default_bundle_id(reg)


def _descriptor_doc_from_entry(entry: "BundleEntry", props: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = entry.model_dump(exclude_none=True)
    payload.pop("id", None)
    if props:
        payload["props"] = props
    return payload


def _entry_and_props_from_descriptor_doc(bundle_id: str, payload: Dict[str, Any]) -> tuple["BundleEntry", Dict[str, Any]]:
    data = dict(payload or {})
    props = data.pop("props", None)
    entry = _to_entry(bundle_id, {"id": bundle_id, **data})
    return entry, props if isinstance(props, dict) else {}


class _AwsBundleDescriptorStore:
    """
    Low-level AWS Secrets Manager mapping helper.

    The active bundle registry authority is still bundles.yaml. This helper is
    retained for explicit admin/migration flows that need to read, store, or
    delete concrete AWS SM documents by secret id.
    """
    _LOCK_TTL_SECONDS = 30
    _LOCK_WAIT_SECONDS = 10.0

    def __init__(
        self,
        *,
        tenant: str,
        project: str,
        prefix: str,
        region: Optional[str],
        profile: Optional[str],
        redis_url: Optional[str],
    ) -> None:
        self._tenant = tenant
        self._project = project
        self._prefix = (prefix or "kdcube").strip("/") or "kdcube"
        self._region = region
        self._profile = profile
        self._redis_url = redis_url
        self._client: Any | None = None
        self._redis = None
        self._lock = threading.RLock()
        self._lock_key_fmt = namespaces.CONFIG.BUNDLES.DESCRIPTORS_AWS_SM_LOCK_FMT

    def _get_client(self):
        with self._lock:
            if self._client is not None:
                return self._client
            import boto3

            session_kwargs: dict[str, Any] = {}
            if self._profile:
                session_kwargs["profile_name"] = self._profile
            session = boto3.Session(**session_kwargs)
            self._client = session.client("secretsmanager", region_name=self._region)
            return self._client

    def _bundles_meta_secret_id(self) -> str:
        return f"{self._prefix}/bundles-meta"

    def _bundle_descriptor_secret_id(self, bundle_id: str) -> str:
        return f"{self._prefix}/bundles/{bundle_id}/descriptor"

    def _error_code(self, exc: Exception) -> str:
        response = getattr(exc, "response", None) or {}
        error = response.get("Error") if isinstance(response, dict) else {}
        return str((error or {}).get("Code") or "")

    def _doc_lock_key(self, secret_id: str) -> str:
        safe = str(secret_id or "").replace("/", ":")
        return self._lock_key_fmt.format(
            tenant=self._tenant,
            project=self._project,
            doc=safe,
        )

    def _get_redis(self):
        if not self._redis_url:
            return None
        if self._redis is not None:
            return self._redis
        try:
            from kdcube_ai_app.infra.redis.client import get_async_redis_client

            self._redis = get_async_redis_client(self._redis_url, decode_responses=True)
        except Exception:
            _log.debug("Failed to initialize Redis client for aws bundle descriptors", exc_info=True)
            self._redis = None
        return self._redis

    async def _acquire_distributed_lock(self, secret_id: str) -> tuple[Any, str] | tuple[None, None]:
        redis = self._get_redis()
        if redis is None:
            return None, None
        token = uuid.uuid4().hex
        lock_key = self._doc_lock_key(secret_id)
        start = time.time()
        while (time.time() - start) < self._LOCK_WAIT_SECONDS:
            try:
                acquired = bool(await redis.set(lock_key, token, nx=True, ex=self._LOCK_TTL_SECONDS))
            except Exception:
                acquired = False
            if acquired:
                return redis, token
            await asyncio.sleep(0.25)
        raise ValueError(f"Failed to acquire distributed aws-sm descriptor lock for {secret_id}")

    async def _release_distributed_lock(self, redis, token: str | None, secret_id: str) -> None:
        if redis is None or not token:
            return
        lock_key = self._doc_lock_key(secret_id)
        try:
            await redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                lock_key,
                token,
            )
        except Exception:
            _log.debug("Failed to release distributed aws-sm descriptor lock", exc_info=True)

    def _get_secret_string_by_id(self, secret_id: str) -> str | None:
        try:
            response = self._get_client().get_secret_value(SecretId=secret_id)
        except Exception as exc:
            if self._error_code(exc) == "ResourceNotFoundException":
                return None
            _log.warning("AWS descriptor GET %s failed", secret_id, exc_info=True)
            return None
        if "SecretString" in response:
            value = response.get("SecretString")
            return str(value) if value is not None else None
        binary = response.get("SecretBinary")
        if binary is None:
            return None
        if isinstance(binary, (bytes, bytearray)):
            return bytes(binary).decode("utf-8")
        return str(binary)

    def _get_secret_mapping_by_id(self, secret_id: str) -> Dict[str, Any] | None:
        raw = self._get_secret_string_by_id(secret_id)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _put_secret_mapping_by_id(self, secret_id: str, payload: Dict[str, Any]) -> None:
        client = self._get_client()
        rendered = json.dumps(payload, ensure_ascii=False)
        try:
            client.put_secret_value(SecretId=secret_id, SecretString=rendered)
            return
        except Exception as exc:
            if self._error_code(exc) != "ResourceNotFoundException":
                raise
        client.create_secret(Name=secret_id, SecretString=rendered)

    def _delete_secret_by_id(self, secret_id: str) -> None:
        client = self._get_client()
        try:
            client.delete_secret(SecretId=secret_id, ForceDeleteWithoutRecovery=True)
        except Exception as exc:
            code = self._error_code(exc)
            if code in {"ResourceNotFoundException", "InvalidRequestException"}:
                return
            raise

    def load_registry(self) -> tuple["BundlesRegistry", Dict[str, Dict[str, Any]]] | None:
        meta = self._get_secret_mapping_by_id(self._bundles_meta_secret_id()) or {}
        bundle_ids = [
            str(item).strip()
            for item in (meta.get("bundle_ids") or [])
            if str(item).strip()
        ]
        if not bundle_ids and not str(meta.get("default_bundle_id") or "").strip():
            return None
        bundles: Dict[str, BundleEntry] = {}
        props_map: Dict[str, Dict[str, Any]] = {}
        for bundle_id in bundle_ids:
            payload = self._get_secret_mapping_by_id(self._bundle_descriptor_secret_id(bundle_id))
            if not isinstance(payload, dict):
                continue
            try:
                entry, props = _entry_and_props_from_descriptor_doc(bundle_id, payload)
            except Exception:
                _log.warning("Failed to parse stored bundle descriptor for %s", bundle_id, exc_info=True)
                continue
            bundles[bundle_id] = entry
            if props:
                props_map[bundle_id] = props
        reg = BundlesRegistry(
            default_bundle_id=str(meta.get("default_bundle_id") or "").strip() or None,
            bundles=bundles,
        )
        return reg, props_map

    async def save_registry(
        self,
        reg: "BundlesRegistry",
        props_map: Dict[str, Dict[str, Any]],
        *,
        replace: bool,
    ) -> None:
        previous = self._get_secret_mapping_by_id(self._bundles_meta_secret_id()) or {}
        previous_ids = {
            str(item).strip()
            for item in (previous.get("bundle_ids") or [])
            if str(item).strip()
        }
        desired_ids = _persisted_bundle_ids(
            reg,
            props_map=props_map,
            existing_ids=previous_ids,
        )

        for bundle_id in desired_ids:
            secret_id = self._bundle_descriptor_secret_id(bundle_id)
            entry = reg.bundles.get(bundle_id) or _reserved_bundle_entry(bundle_id)
            if entry is None:
                continue
            payload = _descriptor_doc_from_entry(entry, props_map.get(bundle_id) or {})
            def write_descriptor() -> None:
                with self._lock:
                    self._put_secret_mapping_by_id(secret_id, payload)

            redis, token = await self._acquire_distributed_lock(secret_id)
            try:
                await _run_blocking_critical_section(write_descriptor)
            finally:
                await self._release_distributed_lock(redis, token, secret_id)

        if replace:
            for stale_bundle_id in sorted(previous_ids.difference(desired_ids)):
                secret_id = self._bundle_descriptor_secret_id(stale_bundle_id)
                def delete_descriptor() -> None:
                    with self._lock:
                        self._delete_secret_by_id(secret_id)

                redis, token = await self._acquire_distributed_lock(secret_id)
                try:
                    await _run_blocking_critical_section(delete_descriptor)
                finally:
                    await self._release_distributed_lock(redis, token, secret_id)

        meta_secret_id = self._bundles_meta_secret_id()
        meta_payload = {
            "default_bundle_id": _persisted_default_bundle_id(reg, persisted_ids=desired_ids),
            "bundle_ids": desired_ids,
        }
        def write_meta() -> None:
            with self._lock:
                self._put_secret_mapping_by_id(meta_secret_id, meta_payload)

        redis, token = await self._acquire_distributed_lock(meta_secret_id)
        try:
            await _run_blocking_critical_section(write_meta)
        finally:
            await self._release_distributed_lock(redis, token, meta_secret_id)

    def load_bundle_props(self, bundle_id: str) -> Dict[str, Any]:
        payload = self._get_secret_mapping_by_id(self._bundle_descriptor_secret_id(bundle_id))
        if not isinstance(payload, dict):
            return {}
        props = payload.get("props")
        return dict(props) if isinstance(props, dict) else {}

    async def set_bundle_props(self, bundle_id: str, entry: "BundleEntry", props: Dict[str, Any]) -> None:
        secret_id = self._bundle_descriptor_secret_id(bundle_id)
        def write_props() -> None:
            with self._lock:
                payload = self._get_secret_mapping_by_id(secret_id) or _descriptor_doc_from_entry(entry, {})
                fresh_payload = _descriptor_doc_from_entry(entry, props or {})
                payload.update({k: v for k, v in fresh_payload.items() if k != "props"})
                if props:
                    payload["props"] = props
                else:
                    payload.pop("props", None)
                self._put_secret_mapping_by_id(secret_id, payload)

        redis, token = await self._acquire_distributed_lock(secret_id)
        try:
            await _run_blocking_critical_section(write_props)
        finally:
            await self._release_distributed_lock(redis, token, secret_id)


class _FileBundleDescriptorStore:
    def __init__(self, *, bundles_yaml_uri: str) -> None:
        self._bundles_yaml_uri = bundles_yaml_uri
        self._lock = threading.RLock()
        self._lock_file_path = self._resolve_local_lock_path(bundles_yaml_uri)

    @staticmethod
    def _resolve_local_lock_path(storage_uri: str) -> Path | None:
        raw = str(storage_uri or "").strip()
        if not raw:
            return None
        if raw.startswith("file://"):
            raw = raw[len("file://"):]
        try:
            path = Path(raw)
        except Exception:
            return None
        if not path.is_absolute():
            return None
        parent = path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            if os.access(parent, os.W_OK):
                return path.with_name(f".{path.name}.lock")
        except OSError:
            pass

        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
        lock_root = Path(os.getenv("BUNDLE_DESCRIPTOR_LOCK_DIR") or "/tmp/kdcube-bundle-descriptor-locks")
        safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in path.name)
        return lock_root / f"{safe_name}.{digest}.lock"

    def _acquire_file_lock(self):
        class _LockCtx:
            def __init__(self, outer: "_FileBundleDescriptorStore") -> None:
                self._outer = outer
                self._fh = None

            def __enter__(self):
                if self._outer._lock_file_path is None:
                    return None
                self._outer._lock_file_path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = self._outer._lock_file_path.open("a+")
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
                return self._fh

            def __exit__(self, exc_type, exc, tb):
                if self._fh is not None:
                    try:
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                    finally:
                        self._fh.close()
                return False

        return _LockCtx(self)

    def _descriptor_local_path(self) -> Path | None:
        raw = str(self._bundles_yaml_uri or "").strip()
        if raw.startswith("file://"):
            raw = raw[len("file://"):]
        try:
            path = Path(raw)
        except Exception:
            return None
        return path if path.is_absolute() else None

    def descriptor_state_key(self) -> Tuple[int, int] | None:
        """(mtime_ns, size) of the descriptor file, or None when unavailable."""
        path = self._descriptor_local_path()
        if path is None:
            return None
        try:
            st = path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _load_mapping(self) -> Dict[str, Any]:
        """
        Load the raw descriptor mapping, reusing the per-process parse cache
        when the file is unchanged.

        Freshness contract: the file is stat()ed on EVERY call; any change of
        (mtime_ns, size) forces a fresh read + parse. To handle the race where
        the file is replaced between stat and read, the file is stat()ed again
        AFTER the read and the result is cached only when both stats agree —
        otherwise the (consistent, atomic-rename) content is returned uncached
        and the next call re-reads.
        """
        state_before = self.descriptor_state_key()
        if state_before is not None:
            with _DESCRIPTOR_MAPPING_CACHE_LOCK:
                entry = _DESCRIPTOR_MAPPING_CACHE.get(self._bundles_yaml_uri)
            if entry is not None and entry[0] == state_before:
                return copy.deepcopy(entry[1])
        payload = _load_yaml_mapping_from_storage(self._bundles_yaml_uri, missing_ok=True)
        if state_before is not None and self.descriptor_state_key() == state_before:
            with _DESCRIPTOR_MAPPING_CACHE_LOCK:
                _DESCRIPTOR_MAPPING_CACHE[self._bundles_yaml_uri] = (
                    state_before,
                    copy.deepcopy(payload),
                )
        return payload

    def _write_mapping(self, payload: Dict[str, Any]) -> None:
        _write_yaml_mapping_to_storage(self._bundles_yaml_uri, payload)
        # The rename changes (mtime_ns, size), but pop explicitly for immediate
        # same-process coherence even on filesystems with coarse timestamps.
        with _DESCRIPTOR_MAPPING_CACHE_LOCK:
            _DESCRIPTOR_MAPPING_CACHE.pop(self._bundles_yaml_uri, None)

    def _bundle_items(self, payload: Dict[str, Any]) -> list[dict[str, Any]]:
        return _secrets_bundle_descriptor_items(payload)

    def _bundle_item_entry_and_props(
        self,
        bundle_id: str,
        item: dict[str, Any],
    ) -> tuple["BundleEntry", Dict[str, Any]]:
        bundles_dict, props_map = _split_bundles_and_props({bundle_id: dict(item)})
        entry = _to_entry(bundle_id, bundles_dict.get(bundle_id) or {"id": bundle_id})
        return entry, props_map.get(bundle_id) or {}

    def _item_from_entry(self, entry: "BundleEntry", props: Dict[str, Any] | None) -> dict[str, Any]:
        payload = entry.model_dump(exclude_none=True)
        payload["id"] = entry.id
        if props:
            payload["config"] = props
        else:
            payload.pop("config", None)
        payload.pop("props", None)
        return payload

    def _registry_from_payload(self, payload: Dict[str, Any]) -> tuple["BundlesRegistry", Dict[str, Dict[str, Any]]] | None:
        items = self._bundle_items(payload)
        bundles: Dict[str, BundleEntry] = {}
        props_map: Dict[str, Dict[str, Any]] = {}
        for item in items:
            bundle_id = str(item.get("id") or "").strip()
            if not bundle_id:
                continue
            if not _examples_enabled() and _is_example_bundle_id(bundle_id):
                continue
            try:
                entry, props = self._bundle_item_entry_and_props(bundle_id, item)
            except Exception:
                _log.warning("Failed to parse file-backed bundle descriptor for %s", bundle_id, exc_info=True)
                continue
            bundles[bundle_id] = entry
            if props:
                props_map[bundle_id] = props
        bundles_root = payload.get("bundles") or {}
        default_bundle_id = None
        if isinstance(bundles_root, dict):
            default_bundle_id = str(bundles_root.get("default_bundle_id") or "").strip() or None
        if not bundles and not default_bundle_id:
            return None
        reg = BundlesRegistry(default_bundle_id=default_bundle_id, bundles=bundles)
        return reg, props_map

    def load_registry(self) -> tuple["BundlesRegistry", Dict[str, Dict[str, Any]]] | None:
        with self._lock, self._acquire_file_lock():
            return self._registry_from_payload(self._load_mapping())

    def load_registry_readonly(self) -> tuple["BundlesRegistry", Dict[str, Dict[str, Any]]] | None:
        # Read-only components such as ingress can read the descriptor file
        # without creating a sibling lock file under /config. Writers replace
        # local descriptor files atomically (tmp + rename), so readers see
        # either the old or the new complete YAML document; a parse error here
        # is therefore almost certainly transient — retry once before failing.
        with self._lock:
            try:
                return self._registry_from_payload(self._load_mapping())
            except Exception:
                time.sleep(0.05)
                return self._registry_from_payload(self._load_mapping())

    def save_registry(
        self,
        reg: "BundlesRegistry",
        props_map: Dict[str, Dict[str, Any]],
        *,
        replace: bool,
    ) -> None:
        with self._lock, self._acquire_file_lock():
            payload = self._load_mapping()
            items = self._bundle_items(payload)
            existing_items_by_id = {
                str(item.get("id") or "").strip(): item
                for item in items
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
            persisted_ids = _persisted_bundle_ids(
                reg,
                props_map=props_map,
                existing_ids=set(existing_items_by_id.keys()),
            )
            new_items: list[dict[str, Any]] = []
            for bundle_id in persisted_ids:
                entry = reg.bundles.get(bundle_id) or _reserved_bundle_entry(bundle_id)
                if entry is None:
                    continue
                props = props_map.get(bundle_id)
                if props is None:
                    existing_item = existing_items_by_id.get(bundle_id)
                    if existing_item is not None:
                        try:
                            _existing_entry, props = self._bundle_item_entry_and_props(bundle_id, existing_item)
                        except Exception:
                            props = {}
                new_items.append(self._item_from_entry(entry, props or {}))
            items[:] = new_items
            bundles_root = payload.get("bundles")
            if not isinstance(bundles_root, dict):
                bundles_root = {}
                payload["bundles"] = bundles_root
            bundles_root["version"] = str(bundles_root.get("version") or "1")
            default_bundle_id = _persisted_default_bundle_id(reg, persisted_ids=persisted_ids)
            if default_bundle_id:
                bundles_root["default_bundle_id"] = default_bundle_id
            else:
                bundles_root.pop("default_bundle_id", None)
            self._write_mapping(payload)

    def load_bundle_props(self, bundle_id: str) -> Dict[str, Any]:
        # Read-only path: no descriptor flock. Writers replace the file
        # atomically, so lock-free reads are consistent (see
        # load_registry_readonly); this keeps request-path reads off the
        # (potentially networked) lock file entirely.
        loaded = self.load_registry_readonly()
        if loaded is None:
            return {}
        _reg, props_map = loaded
        return dict(props_map.get(bundle_id) or {})

    def set_bundle_props(self, bundle_id: str, entry: "BundleEntry", props: Dict[str, Any]) -> None:
        with self._lock, self._acquire_file_lock():
            payload = self._load_mapping()
            items = self._bundle_items(payload)
            item = _secrets_find_bundle_item(items, bundle_id)
            if item is None:
                item = self._item_from_entry(entry, props)
                items.append(item)
            else:
                fresh = self._item_from_entry(entry, props)
                item.clear()
                item.update(fresh)
            self._write_mapping(payload)


def _resolve_bundles_descriptor_authority_uri(*, allow_missing: bool = False) -> str | None:
    settings = get_settings()
    descriptors_dir = str(
        getattr(settings, "PLATFORM_DESCRIPTORS_DIR", None)
        or os.getenv("PLATFORM_DESCRIPTORS_DIR")
        or ""
    ).strip()
    candidates = [
        os.getenv("BUNDLES_YAML_DESCRIPTOR_PATH"),
        str((Path(descriptors_dir).expanduser() / "bundles.yaml").resolve()) if descriptors_dir else None,
        "/config/bundles.yaml",
    ]
    for raw in candidates:
        value = str(raw or "").strip()
        if not value:
            continue
        if value.startswith("{") or value.startswith("["):
            continue
        if value == "/dev/null":
            continue
        if value.endswith("assembly.yaml"):
            continue
        path_text = value
        if value.startswith("file://"):
            path_text = value[len("file://"):]
        path = Path(path_text).expanduser()
        if path.exists() and path.is_file():
            return path.resolve().as_uri()
        if allow_missing:
            return path.resolve().as_uri()
    return None


def _normalize_bundles_descriptor_provider(value: Any) -> str | None:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw in {"", "auto", "default"}:
        return None
    if raw in {"file", "yaml", "bundles-yaml", "descriptor-file", "efs"}:
        return "file"
    if raw in {"aws", "aws-sm", "awssm", "secrets-manager"}:
        return "aws-sm"
    return raw or None


def _resolve_bundles_descriptor_provider() -> str | None:
    settings = get_settings()
    explicit = _normalize_bundles_descriptor_provider(
        getattr(settings, "BUNDLES_DESCRIPTOR_PROVIDER", None)
        or getattr(getattr(getattr(settings, "PLATFORM", None), "APPLICATIONS", None), "BUNDLES_DESCRIPTOR_PROVIDER", None)
        or os.getenv("BUNDLES_DESCRIPTOR_PROVIDER")
    )
    if explicit is not None:
        return explicit
    if _resolve_bundles_descriptor_authority_uri():
        return "file"
    return None


def _is_live_file_authority(store: Any | None) -> bool:
    return isinstance(store, _FileBundleDescriptorStore)


def describe_authoritative_bundle_store(
    tenant: Optional[str] = None,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    del tenant, project
    provider = _resolve_bundles_descriptor_provider()
    if provider == "aws-sm":
        try:
            from kdcube_ai_app.infra.secrets.manager import build_secrets_manager_config

            cfg = build_secrets_manager_config(get_settings())
        except Exception:
            cfg = None
        prefix = str(getattr(cfg, "aws_sm_prefix", "") or "").strip() if cfg is not None else ""
        return {
            "kind": "aws-sm",
            "label": "AWS Secrets Manager",
            "description": "Reload from the live AWS bundle descriptor store.",
            "detail": prefix or None,
        }

    bundles_yaml_uri = _resolve_bundles_descriptor_authority_uri(allow_missing=(provider == "file"))
    if provider == "file" and bundles_yaml_uri:
        raw = bundles_yaml_uri
        if raw.startswith("file://"):
            raw = raw[len("file://"):]
        path = Path(raw).expanduser()
        label = path.name or "bundle descriptor file"
        return {
            "kind": "bundles-yaml",
            "label": label,
            "description": "Reload from the mounted bundle descriptor file.",
            "detail": str(path),
        }

    return {
        "kind": "unknown",
        "label": "configured bundle authority",
        "description": "Reload from the currently configured authoritative bundle store.",
        "detail": None,
    }


def _get_authoritative_bundle_store(
    tenant: str,
    project: str,
) -> Any | None:
    provider = _resolve_bundles_descriptor_provider()
    cfg = None
    if provider == "aws-sm":
        try:
            from kdcube_ai_app.infra.secrets.manager import build_secrets_manager_config

            cfg = build_secrets_manager_config(get_settings())
        except Exception:
            return None
        return _AwsBundleDescriptorStore(
            tenant=tenant,
            project=project,
            prefix=cfg.aws_sm_prefix,
            region=cfg.aws_region,
            profile=cfg.aws_profile,
            redis_url=cfg.redis_url,
        )
    bundles_yaml_uri = _resolve_bundles_descriptor_authority_uri(allow_missing=(provider == "file"))
    if provider == "file" and bundles_yaml_uri:
        return _FileBundleDescriptorStore(bundles_yaml_uri=bundles_yaml_uri)
    return None

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


def props_update_channel(tenant: Optional[str]=None, project: Optional[str]=None) -> str:
    t, p = tenant, project
    if not t or not p:
        t2, p2 = _tp_from_env()
        t = t or t2
        p = p or p2
    return namespaces.CONFIG.BUNDLES.PROPS_UPDATE_CHANNEL.format(tenant=t, project=p)


def _bundle_props_lock_key(*, tenant: str, project: str, bundle_id: str) -> str:
    return _BUNDLE_PROPS_LOCK_KEY_FMT.format(
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    )


def _redis_token_matches(current: Any, token: str) -> bool:
    if current is None:
        return False
    if isinstance(current, (bytes, bytearray)):
        try:
            current = current.decode("utf-8")
        except Exception:
            current = bytes(current).decode("utf-8", errors="ignore")
    return str(current) == str(token)


def _deep_merge_mapping(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base or {})
    for key, value in (patch or {}).items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_mapping(existing, value)
        else:
            merged[key] = value
    return merged


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _run_blocking_critical_section(fn) -> Any:
    task = asyncio.create_task(asyncio.to_thread(fn))
    cancelled = False
    while not task.done():
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        task.result()
        raise asyncio.CancelledError
    return task.result()


async def _release_bundle_props_lock(redis: Any, *, lock_key: str, token: str) -> None:
    if redis is None or not token:
        return
    eval_fn = getattr(redis, "eval", None)
    if callable(eval_fn):
        try:
            await eval_fn(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                lock_key,
                token,
            )
            return
        except Exception:
            _log.debug("Failed to release bundle props lock via eval", exc_info=True)
    try:
        current = await redis.get(lock_key)
        if _redis_token_matches(current, token):
            await redis.delete(lock_key)
    except Exception:
        _log.debug("Failed to release bundle props lock", exc_info=True)


@asynccontextmanager
async def _bundle_props_write_lock(
    redis: Any,
    *,
    tenant: str,
    project: str,
    bundle_id: str,
):
    if redis is None:
        yield
        return
    lock_key = _bundle_props_lock_key(tenant=tenant, project=project, bundle_id=bundle_id)
    token = uuid.uuid4().hex
    start = time.time()
    while True:
        acquired = False
        try:
            acquired = bool(await redis.set(
                lock_key,
                token,
                nx=True,
                ex=_BUNDLE_PROPS_LOCK_TTL_SECONDS,
            ))
        except Exception:
            acquired = False
        if acquired:
            break
        if (time.time() - start) >= _BUNDLE_PROPS_LOCK_WAIT_SECONDS:
            raise TimeoutError(
                f"Timed out waiting for bundle props write lock: tenant={tenant} project={project} bundle={bundle_id}"
            )
        await asyncio.sleep(0.1)
    try:
        yield
    finally:
        await _release_bundle_props_lock(redis, lock_key=lock_key, token=token)


async def publish_props_update(
    redis,
    *,
    bundle_id: str,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
    actor: Optional[str] = None,
    source: Optional[str] = None,
) -> None:
    t, p = tenant, project
    if not t or not p:
        t2, p2 = _tp_from_env()
        t = t or t2
        p = p or p2
    payload = {
        "type": "bundles.props.update",
        "bundle_id": bundle_id,
        "tenant": t,
        "project": p,
        "ts": time.time(),
    }
    if actor:
        payload["updated_by"] = actor
    if source:
        payload["source"] = source
    await redis.publish(
        props_update_channel(t, p),
        json.dumps(payload, ensure_ascii=False),
    )


async def _put_bundle_props_locked(
    redis,
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    props: Dict[str, Any],
    actor: Optional[str] = None,
    source: Optional[str] = None,
) -> None:
    key = _props_key(tenant=tenant, project=project, bundle_id=bundle_id)
    store = _get_authoritative_bundle_store(tenant, project)

    if bundle_id in _reserved_bundle_ids():
        if store is not None:
            entry = _reserved_bundle_entry(bundle_id)
            if entry is None:
                entry = BundleEntry(id=bundle_id, path="")
            await _await_if_needed(store.set_bundle_props(bundle_id, entry, props))
        await redis.set(key, json.dumps(props, ensure_ascii=False))
        try:
            await publish_props_update(
                redis,
                bundle_id=bundle_id,
                tenant=tenant,
                project=project,
                actor=actor,
                source=source,
            )
        except Exception:
            _log.warning("Failed to publish bundle props update", exc_info=True)
        return

    reg = await load_registry(redis, tenant, project)
    entry = reg.bundles.get(bundle_id)
    if entry is None:
        raise ValueError(f"Bundle '{bundle_id}' is not present in the active descriptor")
    if store is not None:
        await save_registry(
            redis,
            reg,
            tenant,
            project,
            props_map={bundle_id: props},
            replace=False,
        )
    await redis.set(key, json.dumps(props, ensure_ascii=False))
    try:
        await publish_props_update(
            redis,
            bundle_id=bundle_id,
            tenant=tenant,
            project=project,
            actor=actor,
            source=source,
        )
    except Exception:
        _log.warning("Failed to publish bundle props update", exc_info=True)

async def load_registry(redis, tenant: Optional[str] = None, project: Optional[str] = None) -> BundlesRegistry:
    """
    Load the active per-tenant/project bundle registry.

    In file-backed descriptor mode the descriptor authority is reread on every
    call, and Redis is refreshed as a runtime cache. If no descriptor file is
    configured, legacy env-seeded runtime state is still supported. Explicit
    provider-backed descriptor mode is also supported.
    """
    key = redis_key(tenant, project)
    t, p = tenant, project
    if not t or not p:
        t, p = _tp_from_env()
    store = _get_authoritative_bundle_store(t, p)

    # In descriptor-backed mode, bundles.yaml is the source of truth.
    # Redis is only a runtime cache and must be refreshed from the file-backed
    # authority on every load so proc restarts and descriptor edits cannot
    # revive stale bundle state.
    if _is_live_file_authority(store):
        # Read-only w.r.t. the descriptor file: no flock needed (writers replace
        # the file atomically; see load_registry_readonly).
        loaded = store.load_registry_readonly()
        if loaded is None:
            reg = BundlesRegistry()
            props_map: Dict[str, Dict[str, Any]] = {}
        else:
            reg, props_map = loaded
        reg, _ = _merge_example_bundles(reg)
        reg = _ensure_admin_bundle(reg)
        # Refresh the Redis runtime cache only when the descriptor changed (or
        # periodically, to self-heal after a Redis flush). Admin mutation paths
        # rewrite the descriptor file AND set these keys directly, so a skipped
        # write-back here never hides an admin update.
        state = store.descriptor_state_key()
        if _redis_writeback_due(key, state):
            await redis.set(key, reg.model_dump_json())
            await _sync_bundle_props_authoritative(redis, tenant=t, project=p, props_map=props_map)
            _record_redis_writeback(key, state)
        return reg

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
            if store is not None:
                loaded = store.load_registry()
                if loaded is not None:
                    reg, props_map = loaded
                    reg, _ = _merge_example_bundles(reg)
                    reg = _ensure_admin_bundle(reg)
                    await redis.set(key, reg.model_dump_json())
                    await _sync_bundle_props_authoritative(redis, tenant=t, project=p, props_map=props_map)
                    return reg
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
    if store is not None:
        loaded = store.load_registry()
        if loaded is not None:
            reg, props_map = loaded
            reg, _ = _merge_example_bundles(reg)
            reg = _ensure_admin_bundle(reg)
            await redis.set(key, reg.model_dump_json())
            await _sync_bundle_props_authoritative(redis, tenant=t, project=p, props_map=props_map)
            return reg
    seeded = await seed_from_env_if_any(redis, tenant, project)
    if seeded:
        seeded, _ = _merge_example_bundles(seeded)
        return _ensure_admin_bundle(seeded)
    reg, updated = _merge_example_bundles(BundlesRegistry())
    reg = _ensure_admin_bundle(reg)
    if updated:
        await save_registry(redis, reg, tenant, project)
    return reg

async def load_registry_from_authority_readonly(
    tenant: Optional[str] = None,
    project: Optional[str] = None,
) -> Optional[BundlesRegistry]:
    """
    Read the active registry directly from descriptor authority without
    mutating that authority and without refreshing Redis.

    This is for non-proc components that need routing metadata but must not
    acquire descriptor write locks or publish descriptor-derived state.
    """
    t, p = tenant, project
    if not t or not p:
        t, p = _tp_from_env()
    store = _get_authoritative_bundle_store(t, p)
    if store is None:
        return None

    loader = getattr(store, "load_registry_readonly", None)
    if callable(loader):
        loaded = loader()
    else:
        loaded = store.load_registry()
    if loaded is None:
        return None

    reg, _props_map = loaded
    reg, _ = _merge_example_bundles(reg)
    reg = _ensure_admin_bundle(reg)
    return reg

async def save_registry(
    redis,
    reg: BundlesRegistry,
    tenant: Optional[str]=None,
    project: Optional[str]=None,
    *,
    props_map: Optional[Dict[str, Dict[str, Any]]] = None,
    replace: bool = False,
) -> None:
    key = redis_key(tenant, project)
    reg = _ensure_admin_bundle(reg)
    t, p = tenant, project
    if not t or not p:
        t, p = _tp_from_env()
    store = _get_authoritative_bundle_store(t, p)
    if store is None:
        await redis.set(key, reg.model_dump_json())
        return
    if not replace and not _deployment_bundle_ids(reg):
        await redis.set(key, reg.model_dump_json())
        return
    effective_props: Dict[str, Dict[str, Any]] = {}
    incoming_props = props_map or {}
    for bid in _deployment_bundle_ids(reg):
        if replace:
            candidate = incoming_props.get(bid) or {}
        else:
            candidate = incoming_props.get(bid)
            if candidate is None:
                candidate = store.load_bundle_props(bid)
            if candidate is None:
                raw_existing = await redis.get(_props_key(tenant=t, project=p, bundle_id=bid))
                if raw_existing:
                    try:
                        candidate = json.loads(raw_existing)
                    except Exception:
                        candidate = None
        if isinstance(candidate, dict) and candidate:
            effective_props[bid] = candidate
    await _await_if_needed(store.save_registry(reg, effective_props, replace=replace))
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
    Seed Redis with bundles mapping from the current local bundle descriptor
    authority, if present.
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
        bundles_dict, props_map = _drop_disabled_example_bundle_entries(bundles_dict, props_map)

        reg = BundlesRegistry(
            default_bundle_id=default_id,
            bundles={bid: _to_entry(bid, b) for bid, b in bundles_dict.items()}
        )
        if not reg.bundles:
            return None
        reg, _ = _merge_example_bundles(reg)
        reg = _ensure_admin_bundle(reg)
        await save_registry(redis, reg, tenant, project, props_map=props_map, replace=False)
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
    Legacy public API name kept for compatibility.
    Force-reload from the current local descriptor authority and overwrite Redis
    for (tenant, project).
    """
    data = _load_env_json(strict=True)
    if "bundles" not in data:
        bundles_dict = data
        default_id = next(iter(bundles_dict.keys()), None)
    else:
        bundles_dict = data.get("bundles") or {}
        default_id = data.get("default_bundle_id") or (next(iter(bundles_dict.keys()), None))

    bundles_dict, props_map = _split_bundles_and_props(bundles_dict)
    bundles_dict, props_map = _drop_disabled_example_bundle_entries(bundles_dict, props_map)

    reg = BundlesRegistry(
        default_bundle_id=default_id,
        bundles={bid: _to_entry(bid, b) for bid, b in bundles_dict.items()}
    )
    reg, _ = _merge_example_bundles(reg)
    reg = _ensure_admin_bundle(reg)
    await save_registry(redis, reg, tenant, project, props_map=props_map, replace=True)
    t, p = tenant, project
    if not t or not p:
        t, p = _tp_from_env()
    await _sync_bundle_props_authoritative(
        redis,
        tenant=t,
        project=p,
        props_map=props_map,
    )
    return reg


async def reload_registry_from_authority(
    redis,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
) -> BundlesRegistry:
    """
    Reload the runtime registry from the current authoritative bundle store and
    overwrite Redis for (tenant, project).

    The authoritative store is resolved through _get_authoritative_bundle_store():
      - mounted bundles.yaml in descriptor-backed deployments
      - AWS Secrets Manager bundle descriptor store in explicit aws-sm deployments
    """
    t, p = tenant, project
    if not t or not p:
        t, p = _tp_from_env()

    store = _get_authoritative_bundle_store(t, p)
    if store is None:
        raise ValueError(
            "No authoritative bundle store is configured. "
            "Expected a mounted bundles.yaml descriptor or an explicit AWS Secrets Manager bundle descriptor store."
        )

    loaded = store.load_registry()
    if loaded is None:
        raise ValueError(
            "The authoritative bundle store is empty or not initialized."
        )

    reg, props_map = loaded
    reg, _ = _merge_example_bundles(reg)
    reg = _ensure_admin_bundle(reg)
    key = redis_key(t, p)
    await redis.set(key, reg.model_dump_json())
    await _sync_bundle_props_authoritative(
        redis,
        tenant=t,
        project=p,
        props_map=props_map,
    )
    return reg


async def force_env_reset_if_requested(
    redis,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
    actor: Optional[str] = None,
) -> Optional[BundlesRegistry]:
    """
    If BUNDLES_FORCE_ENV_ON_STARTUP is set, overwrite Redis registry once from
    the mounted local bundle descriptor authority.
    Uses a Redis lock to avoid multiple workers doing it concurrently.
    """
    settings = get_settings()
    if not settings.BUNDLES_FORCE_ENV_ON_STARTUP:
        return None
    t, p = tenant, project
    if not t or not p:
        t, p = _tp_from_env()
    store = _get_authoritative_bundle_store(t, p)
    if store is not None and not _is_live_file_authority(store):
        _log.info(
            "Skipping startup bundle descriptor reset because a non-file authoritative bundle store is configured."
        )
        return None

    lock_key = namespaces.CONFIG.BUNDLES.ENV_SYNC_LOCK_FMT.format(tenant=t, project=p)
    lock_token = uuid.uuid4().hex
    try:
        acquired = await redis.set(
            lock_key,
            lock_token,
            nx=True,
            ex=settings.BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS,
        )
    except Exception:
        acquired = False

    if not acquired:
        wait_seconds = min(max(float(settings.BUNDLES_FORCE_ENV_LOCK_TTL_SECONDS or 1), 1.0), 30.0)
        deadline = time.monotonic() + wait_seconds
        _log.info(
            "Startup bundle descriptor reset lock is busy; waiting for descriptor cache refresh "
            "(tenant=%s project=%s wait_seconds=%.1f)",
            t,
            p,
            wait_seconds,
        )
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            try:
                current = await redis.get(lock_key)
            except Exception:
                current = None
            if current is not None:
                continue
            try:
                reg = await reload_registry_from_authority(redis, t, p)
                await publish_update(
                    redis,
                    reg,
                    tenant=t,
                    project=p,
                    op="replace",
                    actor=actor or "startup-env-waiter",
                )
                return reg
            except Exception:
                _log.warning(
                    "Failed to reload bundle registry after startup reset lock released; "
                    "this worker will refresh the descriptor directly.",
                    exc_info=True,
                )
                break

        _log.warning(
            "Startup bundle descriptor reset lock wait expired; refreshing descriptor directly "
            "(tenant=%s project=%s)",
            t,
            p,
        )
        reg = await reset_registry_from_env(redis, t, p)
        await publish_update(redis, reg, tenant=t, project=p, op="replace", actor=actor or "startup-env-timeout")
        return reg

    try:
        reg = await reset_registry_from_env(redis, t, p)
        await publish_update(redis, reg, tenant=t, project=p, op="replace", actor=actor or "startup-env")
        return reg
    finally:
        await _release_bundle_props_lock(redis, lock_key=lock_key, token=lock_token)


def _to_entry(bid: str, v: Dict[str, Any]) -> BundleEntry:
    """Normalize incoming dict -> BundleEntry."""
    unsupported_keys = {"git_url", "git_ref", "git_subdir", "git_repo"}
    if any(k in v for k in unsupported_keys):
        raise ValueError("Use repo/ref/subdir only; git_* keys are not supported.")
    repo = _norm_str(v.get("repo"))
    path_val = _norm_str(v.get("path")) or ""
    ref = _norm_str(v.get("ref"))
    subdir = _norm_str(v.get("subdir"))
    if repo:
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
    # Explicit descriptor references to built-in bundles should resolve even
    # when automatic example inclusion is disabled.
    reserved = _reserved_bundle_entry(bid)
    if reserved:
        # Allow reserved ids in bundles.yaml only to override props.
        # Ignore any repo/ref/path/module fields and keep the built-in entry.
        if _entries_equivalent(candidate, reserved):
            return reserved
        ignored_loader_fields = [
            key
            for key in ("repo", "ref", "subdir", "path", "module", "git_commit")
            if _norm_str(v.get(key))
        ]
        if ignored_loader_fields:
            _log.warning(
                "Bundle id '%s' is reserved; ignoring loader fields %s and using built-in bundle entry.",
                bid,
                ignored_loader_fields,
            )
        return reserved
    return candidate

def _load_bundle_descriptor_payload(raw: str, *, strict: bool, label: str) -> Optional[Dict[str, Any]]:
    raw = str(raw or "").strip()
    if not raw:
        return None
    if raw.startswith("{") or raw.startswith("["):
        return json.loads(raw)
    path = Path(raw).expanduser()
    if not path.exists():
        if strict:
            raise ValueError(f"{label} file not found: {path}")
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


def _load_env_json(strict: bool) -> Optional[Dict[str, Any]]:
    authority_uri = _resolve_bundles_descriptor_authority_uri()
    if authority_uri:
        authority_raw = authority_uri
        if authority_raw.startswith("file://"):
            authority_raw = authority_raw[len("file://"):]
        loaded = _load_bundle_descriptor_payload(
            authority_raw,
            strict=strict,
            label="bundle descriptor authority",
        )
        if loaded is not None:
            return loaded

    if strict:
        raise ValueError(
            "No bundle descriptor authority is configured. "
            "Expected a mounted bundles.yaml descriptor."
        )
    return None


async def put_bundle_props(
    redis,
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    props: Dict[str, Any],
    actor: Optional[str] = None,
    source: Optional[str] = None,
) -> None:
    async with _bundle_props_write_lock(
        redis,
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    ):
        await _put_bundle_props_locked(
            redis,
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            props=props,
            actor=actor,
            source=source,
        )


async def patch_bundle_props(
    redis,
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    props_patch: Dict[str, Any],
    actor: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    async with _bundle_props_write_lock(
        redis,
        tenant=tenant,
        project=project,
        bundle_id=bundle_id,
    ):
        current = _get_bundle_props_from_authority(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )
        if current is None:
            current = await get_bundle_props(
                redis,
                tenant=tenant,
                project=project,
                bundle_id=bundle_id,
            )
        merged = _deep_merge_mapping(dict(current or {}), dict(props_patch or {}))
        await _put_bundle_props_locked(
            redis,
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            props=merged,
            actor=actor,
            source=source,
        )
        return merged

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
