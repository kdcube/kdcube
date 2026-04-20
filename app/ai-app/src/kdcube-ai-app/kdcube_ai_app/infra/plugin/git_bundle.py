# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import asyncio
import os
import pathlib
import subprocess
from dataclasses import dataclass
from typing import Optional, Dict, Iterator, Any
import time
from contextlib import contextmanager
import uuid
import fcntl
from kdcube_ai_app.apps.chat.sdk.config import get_settings

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.config import get_secret


@dataclass
class GitBundlePaths:
    repo_root: pathlib.Path
    bundle_root: pathlib.Path


class GitBundleCooldown(Exception):
    """Raised when a git bundle is in cooldown after failures."""


_FAIL_STATE: Dict[str, Dict[str, Any]] = {}
_WARNED_HTTP_SSH: bool = False


def _fail_key(*, git_url: str, bundle_id: str, git_ref: Optional[str]) -> str:
    repo = _repo_name_from_url(git_url)
    ref = _sanitize_ref(git_ref or "head")
    return f"{repo}::{bundle_id}::{ref}"


def _fail_backoff_initial() -> int:
    try:
        return get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_FAIL_BACKOFF_SECONDS
    except Exception:
        return 60


def _fail_backoff_max() -> int:
    try:
        return get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_FAIL_MAX_BACKOFF_SECONDS
    except Exception:
        return 300


def _check_fail_cooldown(key: str) -> None:
    state = _FAIL_STATE.get(key)
    if not state:
        return
    next_ts = state.get("next_ts", 0)
    if next_ts and time.time() < next_ts:
        raise GitBundleCooldown(state.get("last_error") or "git bundle in cooldown")


def _record_fail(key: str, err: Exception) -> None:
    now = time.time()
    state = _FAIL_STATE.get(key, {})
    prev_backoff = int(state.get("backoff", 0) or 0)
    backoff = prev_backoff * 2 if prev_backoff else _fail_backoff_initial()
    backoff = min(backoff, _fail_backoff_max())
    _FAIL_STATE[key] = {
        "backoff": backoff,
        "next_ts": now + backoff,
        "last_error": str(err),
        "last_ts": now,
    }


def _clear_fail(key: str) -> None:
    _FAIL_STATE.pop(key, None)


def resolve_bundles_root() -> pathlib.Path:
    """
    Resolve bundles root on the current host.
    Prefer HOST_BUNDLES_PATH (host filesystem), then AGENTIC_BUNDLES_ROOT.
    """
    settings = get_settings()
    host_root = str(getattr(settings, "HOST_BUNDLES_PATH", None) or os.environ.get("HOST_BUNDLES_PATH") or "").strip()
    agentic_root = settings.PLATFORM.APPLICATIONS.AGENTIC_BUNDLES_ROOT
    if host_root:
        host_path = pathlib.Path(host_root).expanduser()
        try:
            host_path = host_path.resolve()
        except Exception:
            pass
        if host_path.exists():
            return host_path
        log = AgentLogger("git.bundle")
        if agentic_root:
            log.log(
                f"HOST_BUNDLES_PATH points to missing path {host_path}; "
                f"using AGENTIC_BUNDLES_ROOT={agentic_root}",
                level="WARNING",
            )
        else:
            log.log(
                f"HOST_BUNDLES_PATH points to missing path {host_path}; falling back to /bundles",
                level="WARNING",
            )
    root = agentic_root or "/bundles"
    return pathlib.Path(root).expanduser().resolve()


def resolve_git_bundles_root() -> pathlib.Path:
    """
    Resolve the root used for materialized git-backed bundles.

    Preferred order:
    1. HOST_GIT_BUNDLES_PATH (host filesystem)
    2. AGENTIC_GIT_BUNDLES_ROOT (container path)
    3. legacy bundles root fallback

    This keeps existing cloud/ECS behavior unchanged until a dedicated git root
    is explicitly configured.
    """
    settings = get_settings()
    host_root = str(getattr(settings, "HOST_GIT_BUNDLES_PATH", None) or os.environ.get("HOST_GIT_BUNDLES_PATH") or "").strip()
    agentic_root = os.environ.get("AGENTIC_GIT_BUNDLES_ROOT")
    if host_root:
        host_path = pathlib.Path(host_root).expanduser()
        try:
            host_path = host_path.resolve()
        except Exception:
            pass
        if host_path.exists():
            return host_path
        log = AgentLogger("git.bundle")
        if agentic_root:
            log.log(
                f"HOST_GIT_BUNDLES_PATH points to missing path {host_path}; "
                f"using AGENTIC_GIT_BUNDLES_ROOT={agentic_root}",
                level="WARNING",
            )
        else:
            log.log(
                f"HOST_GIT_BUNDLES_PATH points to missing path {host_path}; "
                "falling back to the legacy bundles root",
                level="WARNING",
            )
    if agentic_root:
        return pathlib.Path(agentic_root).expanduser().resolve()
    return resolve_bundles_root()


def _repo_name_from_url(url: str) -> str:
    name = (url or "").rstrip("/").split("/")[-1] or "bundle"
    if name.endswith(".git"):
        name = name[:-4]
    return name or "bundle"

def _sanitize_ref(ref: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in (ref or ""))
    safe = safe.strip("-_")
    return safe or "head"

def _atomic_dir_name(base_dir: str) -> str:
    return f".{base_dir}.tmp-{os.getpid()}-{time.time_ns()}"

def _lock_dir(root: pathlib.Path) -> pathlib.Path:
    lock_dir = root / ".bundle-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir

def _lock_file_name(bundle_id: str, git_ref: Optional[str]) -> str:
    base = _bundle_dir_name(bundle_id, git_ref)
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in base)
    return f"{safe}.lock"

@contextmanager
def _bundle_lock(*, bundle_id: str, git_ref: Optional[str], bundles_root: pathlib.Path) -> Iterator[None]:
    """
    Cross-process lock for git operations on the same bundle_id/git_ref.
    Scope: local host/container (advisory file lock).
    """
    lock_path = _lock_dir(bundles_root) / _lock_file_name(bundle_id, git_ref)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

def _redis_lock_enabled() -> bool:
    return get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_REDIS_LOCK

def _redis_lock_ttl() -> int:
    try:
        return get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_REDIS_LOCK_TTL_SECONDS
    except Exception:
        return 300

def _redis_lock_wait() -> int:
    try:
        return get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_REDIS_LOCK_WAIT_SECONDS
    except Exception:
        return 60

def _redis_client():
    if not _redis_lock_enabled():
        return None
    try:
        import redis  # type: ignore
    except Exception:
        return None
    redis_url = str(getattr(get_settings(), "REDIS_URL", None) or os.environ.get("REDIS_URL") or "").strip()
    if not redis_url:
        return None
    try:
        return redis.Redis.from_url(redis_url, decode_responses=True)
    except Exception:
        return None

def _redis_lock_key(bundle_id: str, git_ref: Optional[str]) -> str:
    tenant = "default"
    project = "default"
    instance_id = os.environ.get("INSTANCE_ID") or os.environ.get("HOSTNAME") or "unknown"
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        settings = get_settings()
        tenant = settings.TENANT
        project = settings.PROJECT
        if settings.INSTANCE_ID:
            instance_id = settings.INSTANCE_ID
    except Exception:
        pass
    ref = _sanitize_ref(git_ref or "head")
    return f"kdcube:bundles:git-lock:{tenant}:{project}:{instance_id}:{bundle_id}:{ref}"

@contextmanager
def _redis_bundle_lock(*, bundle_id: str, git_ref: Optional[str]) -> Iterator[None]:
    client = _redis_client()
    if not client:
        yield
        return
    key = _redis_lock_key(bundle_id, git_ref)
    token = uuid.uuid4().hex
    ttl = _redis_lock_ttl()
    wait_seconds = _redis_lock_wait()
    acquired = False
    start = time.time()
    while time.time() - start < wait_seconds:
        try:
            acquired = bool(client.set(key, token, nx=True, ex=ttl))
        except Exception:
            acquired = False
        if acquired:
            break
        time.sleep(0.5)
    try:
        yield
    finally:
        if acquired:
            # best-effort compare-and-del
            try:
                client.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    key,
                    token,
                )
            except Exception:
                pass


def _bundle_dir_name(bundle_id: str, git_ref: Optional[str]) -> str:
    ref = (git_ref or "").strip()
    if not ref:
        return bundle_id
    return f"{bundle_id}__{_sanitize_ref(ref)}"

def _bundle_dir_name_for_git(bundle_id: Optional[str], git_url: str, git_ref: Optional[str]) -> str:
    repo = _repo_name_from_url(git_url)
    bid = (bundle_id or "").strip() or repo
    base = f"{repo}__{bid}" if bid and bid != repo else repo
    ref = (git_ref or "").strip()
    if not ref:
        return base
    return f"{base}__{_sanitize_ref(ref)}"


def bundle_dir_for_git(bundle_id: str, git_ref: Optional[str] = None, git_url: Optional[str] = None) -> str:
    """
    Public helper to compute base directory name for a git bundle.
    If git_url is provided, the repo name is included in the directory.
    """
    bid = (bundle_id or "").strip() or "bundle"
    if git_url:
        return _bundle_dir_name_for_git(bid, git_url, git_ref)
    return _bundle_dir_name(bid, git_ref)


def compute_git_bundle_paths(
    *,
    bundle_id: Optional[str],
    git_url: str,
    git_ref: Optional[str] = None,
    git_subdir: Optional[str] = None,
    bundles_root: Optional[pathlib.Path] = None,
) -> GitBundlePaths:
    root = bundles_root or resolve_git_bundles_root()
    bid = (bundle_id or "").strip()
    folder = _bundle_dir_name_for_git(bid or None, git_url, git_ref)
    repo_root = (root / folder).resolve()
    if git_subdir:
        bundle_root = (repo_root / git_subdir).resolve()
    else:
        bundle_root = repo_root
    return GitBundlePaths(repo_root=repo_root, bundle_root=bundle_root)


def _git_depth() -> Optional[int]:
    raw = os.environ.get("BUNDLE_GIT_CLONE_DEPTH") or ""
    if not raw:
        shallow = os.environ.get("BUNDLE_GIT_SHALLOW", "").lower() in {"1", "true", "yes"}
        return 50 if shallow else None
    try:
        depth = int(raw)
        return depth if depth > 0 else None
    except Exception:
        return None


def _build_git_env() -> Dict[str, str]:
    """
    Build env for git commands. Supports SSH key/known_hosts via env vars.

    Supported env:
      - GIT_SSH_COMMAND (verbatim)
      - GIT_SSH_KEY_PATH (path to private key)
      - GIT_SSH_KNOWN_HOSTS (path to known_hosts file)
      - GIT_SSH_STRICT_HOST_KEY_CHECKING (yes|no)
      - GIT_HTTP_TOKEN (HTTPS token)
      - GIT_HTTP_USER (HTTPS username, defaults to x-access-token)
    """
    env = os.environ.copy()
    # HTTPS token auth (via GIT_ASKPASS)
    if not env.get("GIT_HTTP_TOKEN"):
        secret = get_secret("services.git.http_token")
        if secret:
            env["GIT_HTTP_TOKEN"] = secret
            if not env.get("GIT_HTTP_USER"):
                env["GIT_HTTP_USER"] = get_secret("services.git.http_user") or "x-access-token"
    if env.get("GIT_HTTP_TOKEN"):
        global _WARNED_HTTP_SSH
        if not _WARNED_HTTP_SSH and (env.get("GIT_SSH_KEY_PATH") or env.get("GIT_SSH_COMMAND")):
            AgentLogger("git.bundle").log(
                "Both GIT_HTTP_TOKEN and SSH settings are set. "
                "HTTPS token auth will be used for git bundles.",
                level="WARNING",
            )
            _WARNED_HTTP_SSH = True
        user = env.get("GIT_HTTP_USER") or "x-access-token"
        env["GIT_HTTP_USER"] = user
        env["GIT_TERMINAL_PROMPT"] = "0"
        askpass_path = pathlib.Path("/tmp/kdcube_git_askpass.sh")
        askpass_contents = (
            "#!/bin/sh\n"
            "prompt=\"$1\"\n"
            "if echo \"$prompt\" | grep -qi \"username\"; then\n"
            "  echo \"${GIT_HTTP_USER:-x-access-token}\"\n"
            "else\n"
            "  echo \"${GIT_HTTP_TOKEN}\"\n"
            "fi\n"
        )
        try:
            if not askpass_path.exists() or askpass_path.read_text() != askpass_contents:
                askpass_path.write_text(askpass_contents)
                askpass_path.chmod(0o700)
        except Exception:
            pass
        if askpass_path.exists():
            env["GIT_ASKPASS"] = str(askpass_path)
        return env

    if env.get("GIT_SSH_COMMAND"):
        return env
    key_path = env.get("GIT_SSH_KEY_PATH")
    strict = env.get("GIT_SSH_STRICT_HOST_KEY_CHECKING")
    if strict:
        strict = str(strict).strip()
    known_hosts = env.get("GIT_SSH_KNOWN_HOSTS")
    if known_hosts:
        known_hosts = str(known_hosts).strip()
    if not key_path and not strict and not known_hosts:
        return env
    cmd = ["ssh"]
    if key_path:
        cmd += ["-i", key_path, "-o", "IdentitiesOnly=yes"]
    if strict:
        cmd += ["-o", f"StrictHostKeyChecking={strict}"]
    if known_hosts:
        cmd += ["-o", f"UserKnownHostsFile={known_hosts}"]
    env["GIT_SSH_COMMAND"] = " ".join(cmd)
    return env


def _https_url_for_ssh(git_url: str) -> str:
    if git_url.startswith("git@") and ":" in git_url:
        host_and_path = git_url.split("git@", 1)[1]
        host, path = host_and_path.split(":", 1)
        return f"https://{host}/{path}"
    if git_url.startswith("ssh://git@"):
        rest = git_url.split("ssh://git@", 1)[1]
        host, path = rest.split("/", 1)
        return f"https://{host}/{path}"
    return git_url


def _run_git(args: list[str], *, logger: Optional[AgentLogger] = None, env: Optional[Dict[str, str]] = None) -> None:
    log = logger or AgentLogger("git.bundle")
    try:
        proc = subprocess.run(args, check=True, capture_output=True, text=True, env=env)
        if proc.stdout:
            log.log(proc.stdout.strip(), level="INFO")
        if proc.stderr:
            log.log(proc.stderr.strip(), level="WARNING")
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or str(e)).strip()
        log.log(f"[git.bundle] command failed: {' '.join(args)} :: {msg}", level="ERROR")
        raise


def _current_branch_name(repo_root: pathlib.Path, *, env: Optional[Dict[str, str]] = None) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "symbolic-ref", "--quiet", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except subprocess.CalledProcessError:
        return None
    branch = (proc.stdout or "").strip()
    return branch or None


def ensure_git_bundle(
    *,
    bundle_id: Optional[str],
    git_url: str,
    git_ref: Optional[str] = None,
    git_subdir: Optional[str] = None,
    bundles_root: Optional[pathlib.Path] = None,
    logger: Optional[AgentLogger] = None,
    atomic: bool = False,
) -> GitBundlePaths:
    """
    Ensure a git bundle is present locally.
    Clones the repo if missing, otherwise fetches + checks out git_ref.
    """
    log = logger or AgentLogger("git.bundle")
    bundle_id = (bundle_id or "").strip() or _repo_name_from_url(git_url)
    http_token = get_secret("services.git.http_token")
    if http_token:
        https_url = _https_url_for_ssh(git_url)
        if https_url != git_url:
            log.log(f"[git.bundle] using HTTPS for {git_url}", level="INFO")
            git_url = https_url
    root = bundles_root or resolve_git_bundles_root()
    fail_key = _fail_key(git_url=git_url, bundle_id=bundle_id, git_ref=git_ref)
    _check_fail_cooldown(fail_key)
    force_pull = get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_ALWAYS_PULL
    with _redis_bundle_lock(bundle_id=bundle_id, git_ref=git_ref):
        with _bundle_lock(bundle_id=bundle_id, git_ref=git_ref, bundles_root=root):
            try:
                paths = compute_git_bundle_paths(
                    bundle_id=bundle_id,
                    git_url=git_url,
                    git_ref=git_ref,
                    git_subdir=git_subdir,
                    bundles_root=root,
                )
                repo_root = paths.repo_root
                repo_root.parent.mkdir(parents=True, exist_ok=True)
                env = _build_git_env()
                depth = _git_depth()

                git_dir = repo_root / ".git"
                if git_dir.exists() and not force_pull:
                    # Repo already present; skip fetch/checkout unless forced.
                    if not paths.bundle_root.exists():
                        raise FileNotFoundError(f"Bundle subdir not found: {paths.bundle_root}")
                    _clear_fail(fail_key)
                    return paths
                if not git_dir.exists():
                    if atomic:
                        tmp_root = repo_root.parent / _atomic_dir_name(repo_root.name)
                        log.log(f"[git.bundle] cloning {git_url} -> {tmp_root}", level="INFO")
                        clone_args = ["git", "clone"]
                        if depth:
                            clone_args += ["--depth", str(depth)]
                        clone_args += [git_url, str(tmp_root)]
                        _run_git(clone_args, logger=log, env=env)
                        try:
                            tmp_root.rename(repo_root)
                        except Exception:
                            # Fallback: if rename fails, keep tmp and point paths to it
                            repo_root = tmp_root
                            paths = compute_git_bundle_paths(
                                bundle_id=repo_root.name,
                                git_url=git_url,
                                git_ref=git_ref,
                                git_subdir=git_subdir,
                                bundles_root=repo_root.parent,
                            )
                    else:
                        log.log(f"[git.bundle] cloning {git_url} -> {repo_root}", level="INFO")
                        clone_args = ["git", "clone"]
                        if depth:
                            clone_args += ["--depth", str(depth)]
                        clone_args += [git_url, str(repo_root)]
                        _run_git(clone_args, logger=log, env=env)
                else:
                    try:
                        # Verify remote URL matches (and fix if needed)
                        proc = subprocess.run(
                            ["git", "-C", str(repo_root), "config", "--get", "remote.origin.url"],
                            check=True, capture_output=True, text=True,
                        )
                        remote_url = (proc.stdout or "").strip()
                        if remote_url and remote_url != git_url:
                            log.log(
                                f"[git.bundle] remote mismatch for {repo_root}: {remote_url} != {git_url}",
                                level="WARNING",
                            )
                            if http_token:
                                _run_git(
                                    ["git", "-C", str(repo_root), "remote", "set-url", "origin", git_url],
                                    logger=log,
                                    env=env,
                                )
                    except Exception:
                        pass
                    log.log(f"[git.bundle] fetching updates in {repo_root}", level="INFO")
                    fetch_args = ["git", "-C", str(repo_root), "fetch", "--all", "--tags", "--prune", "--force"]
                    if depth:
                        fetch_args += ["--depth", str(depth)]
                    _run_git(fetch_args, logger=log, env=env)

                if git_ref:
                    log.log(f"[git.bundle] checkout {git_ref}", level="INFO")
                    try:
                        _run_git(["git", "-C", str(repo_root), "checkout", "--force", git_ref], logger=log, env=env)
                    except Exception:
                        if depth:
                            try:
                                _run_git(["git", "-C", str(repo_root), "fetch", "--unshallow"], logger=log, env=env)
                                _run_git(["git", "-C", str(repo_root), "checkout", "--force", git_ref], logger=log, env=env)
                            except Exception:
                                raise
                        else:
                            raise
                    branch_name = _current_branch_name(repo_root, env=env)
                    if branch_name:
                        _run_git(
                            ["git", "-C", str(repo_root), "reset", "--hard", f"origin/{branch_name}"],
                            logger=log,
                            env=env,
                        )

                # Log current commit (best-effort)
                try:
                    proc = subprocess.run(
                        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                        check=True, capture_output=True, text=True, env=env,
                    )
                    commit = (proc.stdout or "").strip()
                    if commit:
                        log.log(f"[git.bundle] HEAD={commit}", level="INFO")
                except Exception:
                    pass

                if not paths.bundle_root.exists():
                    raise FileNotFoundError(f"Bundle subdir not found: {paths.bundle_root}")

                _clear_fail(fail_key)
                return paths
            except Exception as e:
                _record_fail(fail_key, e)
                raise


def cleanup_old_git_bundles(
    *,
    bundle_id: str,
    bundles_root: Optional[pathlib.Path] = None,
    keep: Optional[int] = None,
    ttl_hours: Optional[int] = None,
    active_paths: Optional[Iterable[str]] = None,
    logger: Optional[AgentLogger] = None,
) -> int:
    """
    Remove old atomic bundle dirs. Returns number removed.
    """
    log = logger or AgentLogger("git.bundle")
    root = bundles_root or resolve_git_bundles_root()
    keep = keep if keep is not None else get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_KEEP
    ttl_hours = ttl_hours if ttl_hours is not None else get_settings().PLATFORM.APPLICATIONS.GIT.BUNDLE_GIT_TTL_HOURS
    prefix = f"{bundle_id}__"
    if not root.exists():
        return 0
    candidates = []
    active_set: set[pathlib.Path] = set()
    if active_paths:
        for ap in active_paths:
            try:
                active_set.add(pathlib.Path(ap).resolve())
            except Exception:
                continue
    for p in root.iterdir():
        if not p.is_dir():
            continue
        if not p.name.startswith(prefix):
            continue
        # Try to parse timestamp suffix: ...__YYYYmmdd-HHMMSS
        parts = p.name.split("__")
        ts = parts[-1] if len(parts) >= 3 else ""
        candidates.append((p, ts))
    # Sort by timestamp if present, else mtime
    def _sort_key(item):
        p, ts = item
        if ts and len(ts) >= 15:
            return ts
        try:
            return str(int(p.stat().st_mtime))
        except Exception:
            return "0"
    candidates.sort(key=_sort_key, reverse=True)

    removed = 0
    def _is_active_dir(p: pathlib.Path) -> bool:
        if not active_set:
            return False
        for ap in active_set:
            try:
                ap.relative_to(p)
                return True
            except Exception:
                continue
        return False

    # TTL cleanup
    if ttl_hours and ttl_hours > 0:
        import time as _time
        cutoff = _time.time() - (ttl_hours * 3600)
        for p, _ in list(candidates):
            try:
                if _is_active_dir(p):
                    continue
                if p.stat().st_mtime < cutoff:
                    _delete_dir(p)
                    removed += 1
                    candidates = [(x, ts) for x, ts in candidates if x != p]
            except Exception:
                continue
    # Keep-N cleanup
    for p, _ in candidates[keep:]:
        try:
            if _is_active_dir(p):
                continue
            _delete_dir(p)
            removed += 1
        except Exception:
            continue
    if removed:
        log.log(f"[git.bundle] cleaned {removed} old bundles for {bundle_id}", level="INFO")
    return removed


async def ensure_git_bundle_async(
    *,
    bundle_id: Optional[str],
    git_url: str,
    git_ref: Optional[str] = None,
    git_subdir: Optional[str] = None,
    bundles_root: Optional[pathlib.Path] = None,
    logger: Optional[AgentLogger] = None,
    atomic: bool = False,
) -> GitBundlePaths:
    """
    Async wrapper around ensure_git_bundle (runs in thread pool).
    """
    return await asyncio.to_thread(
        ensure_git_bundle,
        bundle_id=bundle_id,
        git_url=git_url,
        git_ref=git_ref,
        git_subdir=git_subdir,
        bundles_root=bundles_root,
        logger=logger,
        atomic=atomic,
    )


async def cleanup_old_git_bundles_async(
    *,
    bundle_id: str,
    bundles_root: Optional[pathlib.Path] = None,
    keep: Optional[int] = None,
    ttl_hours: Optional[int] = None,
    active_paths: Optional[Iterable[str]] = None,
    logger: Optional[AgentLogger] = None,
) -> int:
    """
    Async wrapper around cleanup_old_git_bundles (runs in thread pool).
    """
    return await asyncio.to_thread(
        cleanup_old_git_bundles,
        bundle_id=bundle_id,
        bundles_root=bundles_root,
        keep=keep,
        ttl_hours=ttl_hours,
        active_paths=active_paths,
        logger=logger,
    )


def _delete_dir(path: pathlib.Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)
