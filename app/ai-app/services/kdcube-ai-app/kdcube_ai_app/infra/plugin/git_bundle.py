# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import os
import pathlib
import subprocess
from dataclasses import dataclass
from typing import Optional, Dict
import time

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger


@dataclass
class GitBundlePaths:
    repo_root: pathlib.Path
    bundle_root: pathlib.Path


def resolve_bundles_root() -> pathlib.Path:
    """
    Resolve bundles root on the current host.
    Prefer HOST_BUNDLES_PATH (host filesystem), then AGENTIC_BUNDLES_ROOT.
    """
    root = os.environ.get("HOST_BUNDLES_PATH") or os.environ.get("AGENTIC_BUNDLES_ROOT") or "/bundles"
    return pathlib.Path(root).expanduser().resolve()


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
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return f"{base_dir}__{ts}"


def _bundle_dir_name(bundle_id: str, git_ref: Optional[str]) -> str:
    ref = (git_ref or "").strip()
    if not ref:
        return bundle_id
    return f"{bundle_id}__{_sanitize_ref(ref)}"


def bundle_dir_for_git(bundle_id: str, git_ref: Optional[str] = None) -> str:
    """
    Public helper to compute base directory name for a git bundle.
    """
    return _bundle_dir_name((bundle_id or "").strip() or "bundle", git_ref)


def compute_git_bundle_paths(
    *,
    bundle_id: Optional[str],
    git_url: str,
    git_ref: Optional[str] = None,
    git_subdir: Optional[str] = None,
    bundles_root: Optional[pathlib.Path] = None,
) -> GitBundlePaths:
    root = bundles_root or resolve_bundles_root()
    bid = (bundle_id or "").strip() or _repo_name_from_url(git_url)
    folder = _bundle_dir_name(bid, git_ref)
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
    """
    env = os.environ.copy()
    if env.get("GIT_SSH_COMMAND"):
        return env
    key_path = env.get("GIT_SSH_KEY_PATH")
    if not key_path:
        return env
    cmd = ["ssh", "-i", key_path, "-o", "IdentitiesOnly=yes"]
    strict = env.get("GIT_SSH_STRICT_HOST_KEY_CHECKING")
    if strict:
        cmd += ["-o", f"StrictHostKeyChecking={strict}"]
    known_hosts = env.get("GIT_SSH_KNOWN_HOSTS")
    if known_hosts:
        cmd += ["-o", f"UserKnownHostsFile={known_hosts}"]
    env["GIT_SSH_COMMAND"] = " ".join(cmd)
    return env


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
    base_dir = _bundle_dir_name(bundle_id, git_ref)
    bundle_dir = _atomic_dir_name(base_dir) if atomic else base_dir
    paths = compute_git_bundle_paths(
        bundle_id=bundle_dir,
        git_url=git_url,
        git_ref=git_ref,
        git_subdir=git_subdir,
        bundles_root=bundles_root,
    )
    repo_root = paths.repo_root
    repo_root.parent.mkdir(parents=True, exist_ok=True)
    env = _build_git_env()
    depth = _git_depth()

    git_dir = repo_root / ".git"
    if not git_dir.exists():
        log.log(f"[git.bundle] cloning {git_url} -> {repo_root}", level="INFO")
        clone_args = ["git", "clone"]
        if depth:
            clone_args += ["--depth", str(depth)]
        clone_args += [git_url, str(repo_root)]
        _run_git(clone_args, logger=log, env=env)
    else:
        try:
            # Verify remote URL matches
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
        except Exception:
            pass
        log.log(f"[git.bundle] fetching updates in {repo_root}", level="INFO")
        fetch_args = ["git", "-C", str(repo_root), "fetch", "--all", "--tags", "--prune"]
        if depth:
            fetch_args += ["--depth", str(depth)]
        _run_git(fetch_args, logger=log, env=env)

    if git_ref:
        log.log(f"[git.bundle] checkout {git_ref}", level="INFO")
        try:
            _run_git(["git", "-C", str(repo_root), "checkout", git_ref], logger=log, env=env)
        except Exception:
            if depth:
                try:
                    _run_git(["git", "-C", str(repo_root), "fetch", "--unshallow"], logger=log, env=env)
                    _run_git(["git", "-C", str(repo_root), "checkout", git_ref], logger=log, env=env)
                except Exception:
                    raise
            else:
                raise
        # If ref is a branch, attempt fast-forward pull
        try:
            _run_git(["git", "-C", str(repo_root), "pull", "--ff-only"], logger=log, env=env)
        except Exception:
            pass

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

    return paths


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
    root = bundles_root or resolve_bundles_root()
    keep = keep if keep is not None else int(os.environ.get("BUNDLE_GIT_KEEP", "3") or "3")
    ttl_hours = ttl_hours if ttl_hours is not None else int(os.environ.get("BUNDLE_GIT_TTL_HOURS", "0") or "0")
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


def _delete_dir(path: pathlib.Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)
