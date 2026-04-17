# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter
# kdcube_ai_app/infra/plugin/bundle_store.py

from __future__ import annotations
import json, os, time, uuid, threading
import logging
import shutil
import fcntl
from typing import Dict, Optional, Tuple, Any, Set
from pathlib import Path
from functools import lru_cache
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

def _example_bundle_lock_path(bundle_name: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".", "@") else "-" for ch in bundle_name)
    lock_dir = _SHARED_BUNDLES_ROOT / ".example-bundle-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{safe}.lock"

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

def _shared_example_bundle_dir(bundle_name: str, version: str) -> Path:
    platform_ref = _current_platform_ref()
    if platform_ref:
        return _SHARED_BUNDLES_ROOT / f"{bundle_name}__{platform_ref}__{version[:12]}"
    return _SHARED_BUNDLES_ROOT / f"{bundle_name}__{version[:12]}"

def cleanup_old_shared_example_bundles(
    *,
    bundle_id: str,
    bundles_root: Optional[Path] = None,
    keep: Optional[int] = None,
    ttl_hours: Optional[int] = None,
    active_paths: Optional[set[str] | list[str] | tuple[str, ...]] = None,
) -> int:
    root = bundles_root or _SHARED_BUNDLES_ROOT
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

    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    removed = 0

    if ttl_hours and ttl_hours > 0:
        cutoff = time.time() - (ttl_hours * 3600)
        for p in list(candidates):
            try:
                if _is_active_dir(p):
                    continue
                if p.stat().st_mtime < cutoff:
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

def _ensure_example_bundle_shared(bundle_root: Path) -> Path:
    """
    If running in Docker, copy example bundles from the image into /bundles
    so sibling containers (py-code-exec) can mount them.
    """
    if not _is_running_in_docker():
        return bundle_root

    version = compute_dir_sha256(bundle_root, skip_files=set())
    dest_root = _shared_example_bundle_dir(bundle_root.name, version)
    lock_path = _example_bundle_lock_path(bundle_root.name)
    try:
        with lock_path.open("a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            if dest_root.exists() and (dest_root / "entrypoint.py").exists():
                return dest_root

            dest_root.parent.mkdir(parents=True, exist_ok=True)
            tmp_root = dest_root.parent / f".{dest_root.name}.tmp-{os.getpid()}-{time.time_ns()}"
            try:
                shutil.copytree(bundle_root, tmp_root, dirs_exist_ok=False)
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
        _log.warning("Failed to copy example bundle to %s: %s", dest_root, exc)
        return bundle_root

def _examples_enabled() -> bool:
    component = (get_settings().GATEWAY_COMPONENT or "ingress").strip().lower()
    if component != "proc":
        return False
    return bool(get_settings().PLATFORM.APPLICATIONS.BUNDLES_INCLUDE_EXAMPLES)

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
            from kdcube_ai_app.infra.plugin.agentic_loader import get_declared_bundle_id
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
        bundle_path = _ensure_example_bundle_shared(item)
        try:
            from kdcube_ai_app.infra.plugin.agentic_loader import get_declared_bundle_id
            bid = get_declared_bundle_id(bundle_path, "entrypoint") or item.name
        except Exception:
            bid = item.name
        ids.add(bid)
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
        from kdcube_ai_app.infra.plugin.agentic_loader import get_declared_bundle_id
        for item in root.iterdir():
            if not item.is_dir() or item.name in {"data", "__pycache__"}:
                continue
            if not (item / "entrypoint.py").exists():
                continue
            bundle_path = _ensure_example_bundle_shared(item)
            if get_declared_bundle_id(bundle_path, "entrypoint") == bid:
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
    """Return the stored props dict for a bundle, or {} if not set."""
    key = _props_key(tenant=tenant, project=project, bundle_id=bundle_id)
    raw = await redis.get(key)
    if not raw:
        store = _get_authoritative_bundle_store(tenant, project)
        if store is None:
            return {}
        props = store.load_bundle_props(bundle_id)
        if props:
            await redis.set(key, json.dumps(props, ensure_ascii=False))
        return props
    return json.loads(raw)


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

    This is used by explicit env reset paths where AGENTIC_BUNDLES_JSON is the
    source of truth for bundle-level config overrides.
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


def _deployment_bundle_ids(reg: "BundlesRegistry") -> list[str]:
    reserved = _reserved_bundle_ids()
    return [bid for bid in reg.bundles.keys() if bid not in reserved]


def _deployment_default_bundle_id(reg: "BundlesRegistry") -> Optional[str]:
    deployment_ids = _deployment_bundle_ids(reg)
    default_id = str(reg.default_bundle_id or "").strip()
    if default_id and default_id in deployment_ids:
        return default_id
    return deployment_ids[0] if deployment_ids else None


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

    def _get_sync_redis(self):
        if not self._redis_url:
            return None
        if self._redis is not None:
            return self._redis
        try:
            from kdcube_ai_app.infra.redis.client import get_sync_redis_client

            self._redis = get_sync_redis_client(self._redis_url, decode_responses=True)
        except Exception:
            _log.debug("Failed to initialize sync Redis client for aws bundle descriptors", exc_info=True)
            self._redis = None
        return self._redis

    def _acquire_distributed_lock(self, secret_id: str) -> tuple[Any, str] | tuple[None, None]:
        redis = self._get_sync_redis()
        if redis is None:
            return None, None
        token = uuid.uuid4().hex
        lock_key = self._doc_lock_key(secret_id)
        start = time.time()
        while (time.time() - start) < self._LOCK_WAIT_SECONDS:
            try:
                acquired = bool(redis.set(lock_key, token, nx=True, ex=self._LOCK_TTL_SECONDS))
            except Exception:
                acquired = False
            if acquired:
                return redis, token
            time.sleep(0.25)
        raise ValueError(f"Failed to acquire distributed aws-sm descriptor lock for {secret_id}")

    def _release_distributed_lock(self, redis, token: str | None, secret_id: str) -> None:
        if redis is None or not token:
            return
        lock_key = self._doc_lock_key(secret_id)
        try:
            redis.eval(
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
        if reg.default_bundle_id and reg.default_bundle_id not in reg.bundles:
            reg.default_bundle_id = next(iter(reg.bundles.keys()), None)
        return reg, props_map

    def save_registry(
        self,
        reg: "BundlesRegistry",
        props_map: Dict[str, Dict[str, Any]],
        *,
        replace: bool,
    ) -> None:
        deployment_ids = _deployment_bundle_ids(reg)
        desired_ids = list(deployment_ids)
        previous_ids: set[str] = set()
        if replace:
            previous = self._get_secret_mapping_by_id(self._bundles_meta_secret_id()) or {}
            previous_ids = {
                str(item).strip()
                for item in (previous.get("bundle_ids") or [])
                if str(item).strip()
            }

        for bundle_id in desired_ids:
            secret_id = self._bundle_descriptor_secret_id(bundle_id)
            payload = _descriptor_doc_from_entry(reg.bundles[bundle_id], props_map.get(bundle_id) or {})
            with self._lock:
                redis, token = self._acquire_distributed_lock(secret_id)
                try:
                    self._put_secret_mapping_by_id(secret_id, payload)
                finally:
                    self._release_distributed_lock(redis, token, secret_id)

        if replace:
            for stale_bundle_id in sorted(previous_ids.difference(desired_ids)):
                secret_id = self._bundle_descriptor_secret_id(stale_bundle_id)
                with self._lock:
                    redis, token = self._acquire_distributed_lock(secret_id)
                    try:
                        self._delete_secret_by_id(secret_id)
                    finally:
                        self._release_distributed_lock(redis, token, secret_id)

        meta_secret_id = self._bundles_meta_secret_id()
        meta_payload = {
            "default_bundle_id": _deployment_default_bundle_id(reg),
            "bundle_ids": desired_ids,
        }
        with self._lock:
            redis, token = self._acquire_distributed_lock(meta_secret_id)
            try:
                self._put_secret_mapping_by_id(meta_secret_id, meta_payload)
            finally:
                self._release_distributed_lock(redis, token, meta_secret_id)

    def load_bundle_props(self, bundle_id: str) -> Dict[str, Any]:
        if bundle_id in _reserved_bundle_ids():
            return {}
        payload = self._get_secret_mapping_by_id(self._bundle_descriptor_secret_id(bundle_id))
        if not isinstance(payload, dict):
            return {}
        props = payload.get("props")
        return dict(props) if isinstance(props, dict) else {}

    def set_bundle_props(self, bundle_id: str, entry: "BundleEntry", props: Dict[str, Any]) -> None:
        if bundle_id in _reserved_bundle_ids():
            return
        secret_id = self._bundle_descriptor_secret_id(bundle_id)
        with self._lock:
            redis, token = self._acquire_distributed_lock(secret_id)
            try:
                payload = self._get_secret_mapping_by_id(secret_id) or _descriptor_doc_from_entry(entry, {})
                fresh_payload = _descriptor_doc_from_entry(entry, props or {})
                payload.update({k: v for k, v in fresh_payload.items() if k != "props"})
                if props:
                    payload["props"] = props
                else:
                    payload.pop("props", None)
                self._put_secret_mapping_by_id(secret_id, payload)
            finally:
                self._release_distributed_lock(redis, token, secret_id)


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
        return path.with_name(f".{path.name}.lock")

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

    def _load_mapping(self) -> Dict[str, Any]:
        return _load_yaml_mapping_from_storage(self._bundles_yaml_uri, missing_ok=True)

    def _write_mapping(self, payload: Dict[str, Any]) -> None:
        _write_yaml_mapping_to_storage(self._bundles_yaml_uri, payload)

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

    def load_registry(self) -> tuple["BundlesRegistry", Dict[str, Dict[str, Any]]] | None:
        with self._lock, self._acquire_file_lock():
            payload = self._load_mapping()
            items = self._bundle_items(payload)
            bundles: Dict[str, BundleEntry] = {}
            props_map: Dict[str, Dict[str, Any]] = {}
            for item in items:
                bundle_id = str(item.get("id") or "").strip()
                if not bundle_id:
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
            if reg.default_bundle_id and reg.default_bundle_id not in reg.bundles:
                reg.default_bundle_id = next(iter(reg.bundles.keys()), None)
            return reg, props_map

    def save_registry(
        self,
        reg: "BundlesRegistry",
        props_map: Dict[str, Dict[str, Any]],
        *,
        replace: bool,
    ) -> None:
        deployment_ids = _deployment_bundle_ids(reg)
        with self._lock, self._acquire_file_lock():
            payload = self._load_mapping()
            items = self._bundle_items(payload)
            new_items: list[dict[str, Any]] = []
            for bundle_id in deployment_ids:
                entry = reg.bundles.get(bundle_id)
                if entry is None:
                    continue
                new_items.append(self._item_from_entry(entry, props_map.get(bundle_id) or {}))
            items[:] = new_items
            bundles_root = payload.get("bundles")
            if not isinstance(bundles_root, dict):
                bundles_root = {}
                payload["bundles"] = bundles_root
            bundles_root["version"] = str(bundles_root.get("version") or "1")
            default_bundle_id = _deployment_default_bundle_id(reg)
            if default_bundle_id:
                bundles_root["default_bundle_id"] = default_bundle_id
            else:
                bundles_root.pop("default_bundle_id", None)
            self._write_mapping(payload)

    def load_bundle_props(self, bundle_id: str) -> Dict[str, Any]:
        if bundle_id in _reserved_bundle_ids():
            return {}
        loaded = self.load_registry()
        if loaded is None:
            return {}
        _reg, props_map = loaded
        return dict(props_map.get(bundle_id) or {})

    def set_bundle_props(self, bundle_id: str, entry: "BundleEntry", props: Dict[str, Any]) -> None:
        if bundle_id in _reserved_bundle_ids():
            return
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


def _resolve_bundles_descriptor_authority_uri() -> str | None:
    descriptors_dir = str(os.getenv("PLATFORM_DESCRIPTORS_DIR") or "").strip()
    candidates = [
        os.getenv("BUNDLES_YAML_DESCRIPTOR_PATH"),
        os.getenv("AGENTIC_BUNDLES_JSON"),
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
    return None


def _get_authoritative_bundle_store(
    tenant: str,
    project: str,
) -> Any | None:
    try:
        from kdcube_ai_app.infra.secrets.manager import build_secrets_manager_config

        cfg = build_secrets_manager_config(get_settings())
    except Exception:
        return None
    bundles_yaml_uri = _resolve_bundles_descriptor_authority_uri()
    if bundles_yaml_uri:
        return _FileBundleDescriptorStore(bundles_yaml_uri=bundles_yaml_uri)
    if cfg.provider == "aws-sm":
        return _AwsBundleDescriptorStore(
            tenant=tenant,
            project=project,
            prefix=cfg.aws_sm_prefix,
            region=cfg.aws_region,
            profile=cfg.aws_profile,
            redis_url=cfg.redis_url,
        )
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

async def load_registry(redis, tenant: Optional[str] = None, project: Optional[str] = None) -> BundlesRegistry:
    """
    Load per-tenant/project registry from Redis.
    If the key is missing OR contains an 'effectively empty' registry
    (e.g. {"default_bundle_id": null, "bundles": {}}), seed once from env.
    """
    key = redis_key(tenant, project)
    t, p = tenant, project
    if not t or not p:
        t, p = _tp_from_env()
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
            store = _get_authoritative_bundle_store(t, p)
            if store is not None:
                loaded = store.load_registry()
                if loaded is not None:
                    reg, props_map = loaded
                    await redis.set(key, _ensure_admin_bundle(reg).model_dump_json())
                    await _sync_bundle_props_authoritative(redis, tenant=t, project=p, props_map=props_map)
                    reg, _ = _merge_example_bundles(reg)
                    return _ensure_admin_bundle(reg)
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
    store = _get_authoritative_bundle_store(t, p)
    if store is not None:
        loaded = store.load_registry()
        if loaded is not None:
            reg, props_map = loaded
            await redis.set(key, _ensure_admin_bundle(reg).model_dump_json())
            await _sync_bundle_props_authoritative(redis, tenant=t, project=p, props_map=props_map)
            reg, _ = _merge_example_bundles(reg)
            return _ensure_admin_bundle(reg)
    seeded = await seed_from_env_if_any(redis, tenant, project)
    if seeded:
        seeded, _ = _merge_example_bundles(seeded)
        return _ensure_admin_bundle(seeded)
    reg, updated = _merge_example_bundles(BundlesRegistry())
    reg = _ensure_admin_bundle(reg)
    if updated:
        await save_registry(redis, reg, tenant, project)
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
    await redis.set(key, reg.model_dump_json())
    t, p = tenant, project
    if not t or not p:
        t, p = _tp_from_env()
    store = _get_authoritative_bundle_store(t, p)
    if store is None:
        return
    if not replace and not _deployment_bundle_ids(reg):
        return
    effective_props: Dict[str, Dict[str, Any]] = {}
    incoming_props = props_map or {}
    for bid in _deployment_bundle_ids(reg):
        if replace:
            candidate = incoming_props.get(bid) or {}
        else:
            candidate = incoming_props.get(bid)
            if candidate is None:
                raw_existing = await redis.get(_props_key(tenant=t, project=p, bundle_id=bid))
                if raw_existing:
                    try:
                        candidate = json.loads(raw_existing)
                    except Exception:
                        candidate = None
                if candidate is None:
                    candidate = store.load_bundle_props(bid)
        if isinstance(candidate, dict) and candidate:
            effective_props[bid] = candidate
    store.save_registry(reg, effective_props, replace=replace)

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


async def put_bundle_props(
    redis,
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    props: Dict[str, Any],
) -> None:
    key = _props_key(tenant=tenant, project=project, bundle_id=bundle_id)
    await redis.set(key, json.dumps(props, ensure_ascii=False))
    if bundle_id in _reserved_bundle_ids():
        return
    reg = await load_registry(redis, tenant, project)
    entry = reg.bundles.get(bundle_id)
    if entry is None or bundle_id in _reserved_bundle_ids():
        return
    await save_registry(
        redis,
        reg,
        tenant,
        project,
        props_map={bundle_id: props},
        replace=False,
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
        return _ensure_admin_bundle(BundlesRegistry(default_bundle_id=new_default, bundles=new_map))

    # merge
    new_map: Dict[str, BundleEntry] = dict(current.bundles)
    for bid, v in (bundles_patch or {}).items():
        new_map[bid] = _to_entry(bid, v)

    new_default = default_bundle_id or current.default_bundle_id
    if new_default and new_default not in new_map:
        new_default = next(iter(new_map), None)

    return _ensure_admin_bundle(BundlesRegistry(default_bundle_id=new_default, bundles=new_map))
