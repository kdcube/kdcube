# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import pathlib
import subprocess
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.workspace import (
    _guess_mime_from_path,
    _is_text_mime,
    workspace_lineage_branch_ref,
    workspace_lineage_segments,
    workspace_version_ref,
)
from kdcube_ai_app.infra.plugin.git_bundle import _build_git_env
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger

_SKIP_WORKSPACE_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "logs", "executed_programs"}


def _workspace_cache_root(*, runtime_ctx: Any, outdir: pathlib.Path) -> pathlib.Path:
    root = pathlib.Path(outdir).parent / ".react_workspace_git"
    segs = workspace_lineage_segments(runtime_ctx)
    return root / f"{segs['tenant']}__{segs['project']}__{segs['user_id']}__{segs['conversation_id']}"


def _workspace_lineage_repo_root(*, runtime_ctx: Any, outdir: pathlib.Path) -> pathlib.Path:
    return _workspace_cache_root(runtime_ctx=runtime_ctx, outdir=outdir) / "lineage.git"


def describe_current_turn_git_repo(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
) -> Dict[str, Any]:
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    if not turn_id:
        return {}
    turn_root = pathlib.Path(outdir) / turn_id
    if not (turn_root / ".git").exists():
        return {
            "repo_mode": "sparse git repo",
            "repo_status": "uninitialized",
        }
    repo_status = "unknown"
    try:
        proc = subprocess.run(
            ["git", "-C", str(turn_root), "status", "--short", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        )
        repo_status = "clean" if not (proc.stdout or "").strip() else "dirty"
    except Exception:
        repo_status = "unavailable"
    return {
        "repo_mode": "sparse git repo",
        "repo_status": repo_status,
    }


def summarize_current_turn_git_lineage_scopes(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
) -> List[Dict[str, Any]]:
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    if not turn_id:
        return []
    turn_root = pathlib.Path(outdir) / turn_id
    if not (turn_root / ".git").exists():
        return []
    try:
        subprocess.run(
            ["git", "-C", str(turn_root), "rev-parse", "--verify", "workspace"],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return []
    try:
        proc = subprocess.run(
            ["git", "-C", str(turn_root), "ls-tree", "-r", "--name-only", "workspace", "--", "files"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return []

    counts: Dict[str, int] = {}
    for line in (proc.stdout or "").splitlines():
        raw = line.strip()
        if not raw.startswith("files/"):
            continue
        rel = raw[len("files/"):].strip("/")
        if not rel:
            continue
        top = rel.split("/", 1)[0]
        counts[top] = counts.get(top, 0) + 1

    out: List[Dict[str, Any]] = []
    for scope in sorted(counts.keys(), key=str.lower):
        out.append({"scope": f"{scope}/", "files": counts[scope], "kind": "dir"})
    return out


def _run_git_capture(repo_root: pathlib.Path, args: List[str], *, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        env=env,
    )


def _ensure_workspace_repo(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
    logger: Optional[AgentLogger] = None,
) -> pathlib.Path:
    repo_url = str(getattr(runtime_ctx, "workspace_git_repo", "") or "").strip()
    if not repo_url:
        raise ValueError("missing_workspace_git_repo")
    log = logger or AgentLogger("react.workspace.git")
    env = _build_git_env()
    cache_root = _workspace_cache_root(runtime_ctx=runtime_ctx, outdir=outdir)
    repo_root = _workspace_lineage_repo_root(runtime_ctx=runtime_ctx, outdir=outdir)
    cache_root.mkdir(parents=True, exist_ok=True)
    if not (repo_root / "HEAD").exists():
        subprocess.run(
            ["git", "init", "--bare", str(repo_root)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_root), "remote", "add", "origin", repo_url],
            check=True,
            capture_output=True,
            env=env,
        )
        try:
            subprocess.run(
                ["git", "-C", str(repo_root), "config", "--unset-all", "remote.origin.fetch"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass
        subprocess.run(
            ["git", "-C", str(repo_root), "config", "remote.origin.tagOpt", "--no-tags"],
            check=True,
            capture_output=True,
        )
    else:
        try:
            subprocess.run(
                ["git", "-C", str(repo_root), "remote", "set-url", "origin", repo_url],
                check=True,
                capture_output=True,
                env=env,
            )
        except Exception:
            pass
    return repo_root


def _git_has_ref(*, repo_root: pathlib.Path, ref_name: str) -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "show-ref", "--verify", "--quiet", ref_name],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _ensure_local_version_ref(*, repo_root: pathlib.Path, runtime_ctx: Any, version_id: str) -> str:
    env = _build_git_env()
    remote_ref = workspace_version_ref(runtime_ctx, version_id)
    if not remote_ref:
        raise ValueError("missing_workspace_version_ref")
    local_ref = f"refs/kdcube-local/versions/{version_id}"
    if _git_has_ref(repo_root=repo_root, ref_name=local_ref):
        return local_ref
    subprocess.run(
        ["git", "-C", str(repo_root), "fetch", "--no-tags", "origin", f"+{remote_ref}:{local_ref}"],
        check=True,
        capture_output=True,
        env=env,
    )
    return local_ref


def _ensure_local_lineage_branch_ref(*, repo_root: pathlib.Path, runtime_ctx: Any) -> str:
    env = _build_git_env()
    remote_ref = workspace_lineage_branch_ref(runtime_ctx)
    if not remote_ref:
        return ""
    local_ref = "refs/heads/workspace"
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "fetch", "--no-tags", "origin", f"+{remote_ref}:{local_ref}"],
            check=True,
            capture_output=True,
            env=env,
        )
    except subprocess.CalledProcessError:
        if _git_has_ref(repo_root=repo_root, ref_name=local_ref):
            return local_ref
        return ""
    return local_ref


def _fetch_ref_into_turn_repo(
    *,
    turn_root: pathlib.Path,
    source_repo_root: pathlib.Path,
    source_ref: str,
    target_ref: str,
) -> str:
    subprocess.run(
        ["git", "-C", str(turn_root), "fetch", "--no-tags", str(source_repo_root), f"+{source_ref}:{target_ref}"],
        check=True,
        capture_output=True,
    )
    return target_ref


def _workspace_commit_identity(runtime_ctx: Any) -> tuple[str, str]:
    user_id = str(getattr(runtime_ctx, "user_id", "") or "").strip() or "react"
    safe_user = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in user_id).strip("-") or "react"
    name = f"React Workspace ({user_id})"
    email = f"{safe_user}@local.invalid"
    return name, email


def ensure_current_turn_git_workspace(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
    logger: Optional[AgentLogger] = None,
) -> pathlib.Path:
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    if not turn_id:
        raise ValueError("missing_turn_id")
    log = logger or AgentLogger("react.workspace.git")
    repo_root = _ensure_workspace_repo(runtime_ctx=runtime_ctx, outdir=outdir, logger=log)
    lineage_ref = _ensure_local_lineage_branch_ref(repo_root=repo_root, runtime_ctx=runtime_ctx)

    turn_root = pathlib.Path(outdir) / turn_id
    git_dir = turn_root / ".git"
    if git_dir.exists():
        return turn_root

    turn_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", str(turn_root)],
        check=True,
        capture_output=True,
    )
    name, email = _workspace_commit_identity(runtime_ctx)
    subprocess.run(
        ["git", "-C", str(turn_root), "config", "user.name", name],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(turn_root), "config", "user.email", email],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(turn_root), "config", "advice.detachedHead", "false"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(turn_root), "config", "core.sparseCheckout", "true"],
        check=True,
        capture_output=True,
    )
    sparse_file = turn_root / ".git" / "info" / "sparse-checkout"
    sparse_file.parent.mkdir(parents=True, exist_ok=True)
    sparse_file.write_text("", encoding="utf-8")

    if lineage_ref:
        subprocess.run(
            ["git", "-C", str(turn_root), "fetch", "--no-tags", str(repo_root), f"+{lineage_ref}:refs/heads/workspace"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(turn_root), "checkout", "-f", "workspace"],
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "-C", str(turn_root), "checkout", "--orphan", "workspace"],
            check=True,
            capture_output=True,
        )

    return turn_root


def _git_path_is_file(*, repo_root: pathlib.Path, ref_name: str, tree_path: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-t", f"{ref_name}:{tree_path}"],
            check=True,
            capture_output=True,
            text=True,
        )
        return (proc.stdout or "").strip() == "blob"
    except subprocess.CalledProcessError:
        return False


def _git_list_tree(*, repo_root: pathlib.Path, ref_name: str, tree_path: str) -> List[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "ls-tree", "-r", "--name-only", ref_name, "--", tree_path],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def _git_read_blob(*, repo_root: pathlib.Path, ref_name: str, tree_path: str) -> bytes:
    proc = _run_git_capture(repo_root, ["show", f"{ref_name}:{tree_path}"])
    return proc.stdout or b""


def _blob_is_text_like(*, data: bytes, path_hint: str) -> bool:
    mime = _guess_mime_from_path(path_hint)
    if _is_text_mime(mime):
        return True
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except Exception:
        return False


def _write_blob(target: pathlib.Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _git_has_head(*, repo_root: pathlib.Path) -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _git_head_sha(*, repo_root: pathlib.Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return (proc.stdout or "").strip()


def _git_has_staged_changes(*, repo_root: pathlib.Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--cached", "--quiet", "--exit-code"],
        check=False,
        capture_output=True,
    )
    return proc.returncode != 0


def _git_is_dirty(*, repo_root: pathlib.Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool((proc.stdout or "").strip())


def _file_is_text_like(path: pathlib.Path) -> bool:
    try:
        data = path.read_bytes()
    except Exception:
        return False
    return _blob_is_text_like(data=data, path_hint=str(path))


def _workspace_path_is_skipped(path: pathlib.Path, *, turn_root: pathlib.Path) -> bool:
    try:
        rel = path.relative_to(turn_root)
    except Exception:
        rel = path
    return any(part in _SKIP_WORKSPACE_DIRS for part in rel.parts)


def _git_path_is_ignored(*, repo_root: pathlib.Path, rel_path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "check-ignore", "-q", "--no-index", "--", rel_path],
        check=False,
        capture_output=True,
    )
    return proc.returncode == 0


def _stage_current_turn_text_workspace(*, turn_root: pathlib.Path) -> None:
    subprocess.run(
        ["git", "-C", str(turn_root), "add", "--sparse", "-u", "--", "."],
        check=True,
        capture_output=True,
    )
    files_root = turn_root / "files"
    if not files_root.exists():
        return
    text_paths: List[str] = []
    for path in files_root.rglob("*"):
        if not path.is_file():
            continue
        if _workspace_path_is_skipped(path, turn_root=turn_root):
            continue
        if not _file_is_text_like(path):
            continue
        rel_path = str(path.relative_to(turn_root))
        if _git_path_is_ignored(repo_root=turn_root, rel_path=rel_path):
            continue
        text_paths.append(rel_path)
    if not text_paths:
        return
    for idx in range(0, len(text_paths), 128):
        chunk = text_paths[idx: idx + 128]
        subprocess.run(
            ["git", "-C", str(turn_root), "add", "--sparse", "--", *chunk],
            check=True,
            capture_output=True,
        )


def publish_current_turn_git_workspace(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
    logger: Optional[AgentLogger] = None,
) -> Dict[str, Any]:
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    if not turn_id:
        raise ValueError("missing_turn_id")
    log = logger or AgentLogger("react.workspace.git")
    turn_root = ensure_current_turn_git_workspace(
        runtime_ctx=runtime_ctx,
        outdir=outdir,
        logger=log,
    )
    repo_root = _ensure_workspace_repo(
        runtime_ctx=runtime_ctx,
        outdir=outdir,
        logger=log,
    )
    lineage_ref = workspace_lineage_branch_ref(runtime_ctx)
    version_ref = workspace_version_ref(runtime_ctx, turn_id)
    if not lineage_ref or not version_ref:
        raise ValueError("missing_workspace_refs")

    _stage_current_turn_text_workspace(turn_root=turn_root)
    has_head = _git_has_head(repo_root=turn_root)
    committed = False
    if not has_head:
        commit_args = ["git", "-C", str(turn_root), "commit", "--allow-empty", "-m", f"React workspace snapshot {turn_id}"]
        subprocess.run(commit_args, check=True, capture_output=True)
        committed = True
    elif _git_has_staged_changes(repo_root=turn_root):
        subprocess.run(
            ["git", "-C", str(turn_root), "commit", "-m", f"React workspace snapshot {turn_id}"],
            check=True,
            capture_output=True,
        )
        committed = True

    head_sha = _git_head_sha(repo_root=turn_root)
    subprocess.run(
        ["git", "-C", str(turn_root), "push", str(repo_root), "HEAD:refs/heads/workspace"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(turn_root), "push", str(repo_root), f"{head_sha}:refs/kdcube-local/versions/{turn_id}"],
        check=True,
        capture_output=True,
    )

    env = _build_git_env()
    subprocess.run(
        ["git", "-C", str(repo_root), "push", "origin", f"refs/heads/workspace:{lineage_ref}"],
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "push", "origin", f"refs/kdcube-local/versions/{turn_id}:{version_ref}"],
        check=True,
        capture_output=True,
        env=env,
    )
    return {
        "turn_root": str(turn_root),
        "commit_sha": head_sha,
        "lineage_ref": lineage_ref,
        "version_ref": version_ref,
        "committed": committed,
    }


def checkout_current_turn_git_workspace(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
    version_id: str,
    logger: Optional[AgentLogger] = None,
) -> Dict[str, Any]:
    version = str(version_id or "").strip()
    if not version:
        raise ValueError("missing_version_id")
    log = logger or AgentLogger("react.workspace.git")
    turn_root = ensure_current_turn_git_workspace(
        runtime_ctx=runtime_ctx,
        outdir=outdir,
        logger=log,
    )
    if _git_is_dirty(repo_root=turn_root):
        raise ValueError("workspace_checkout_dirty")
    repo_root = _ensure_workspace_repo(
        runtime_ctx=runtime_ctx,
        outdir=outdir,
        logger=log,
    )
    local_ref = _ensure_local_version_ref(
        repo_root=repo_root,
        runtime_ctx=runtime_ctx,
        version_id=version,
    )
    checkout_ref = _fetch_ref_into_turn_repo(
        turn_root=turn_root,
        source_repo_root=repo_root,
        source_ref=local_ref,
        target_ref=f"refs/kdcube-local/checkout/{version}",
    )
    subprocess.run(
        ["git", "-C", str(turn_root), "sparse-checkout", "set", "--cone", "files"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(turn_root), "checkout", "-f", "workspace"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(turn_root), "reset", "--hard", checkout_ref],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(turn_root), "clean", "-fd", "--", "files"],
        check=True,
        capture_output=True,
    )
    return {
        "turn_root": str(turn_root),
        "checked_out_version": version,
        "version_ref": workspace_version_ref(runtime_ctx, version),
        "workspace_ref": "refs/heads/workspace",
        "checkout_ref": checkout_ref,
    }


async def hydrate_files_from_git_workspace(
    *,
    ctx_browser: Any,
    paths: List[str],
    outdir: pathlib.Path,
) -> Dict[str, Any]:
    runtime_ctx = getattr(ctx_browser, "runtime_ctx", None)
    if runtime_ctx is None:
        return {"rehosted": [], "missing": [], "errors": ["missing_runtime_ctx"]}
    try:
        repo_root = await asyncio.to_thread(
            _ensure_workspace_repo,
            runtime_ctx=runtime_ctx,
            outdir=outdir,
        )
    except Exception as exc:
        return {"rehosted": [], "missing": [], "errors": [f"workspace_git_repo_unavailable:{exc}"]}

    rehosted: List[str] = []
    missing: List[str] = []
    errors: List[str] = []
    seen_targets: set[str] = set()
    version_refs: Dict[str, str] = {}
    version_errors: Dict[str, str] = {}

    for physical in paths:
        if not isinstance(physical, str):
            continue
        if physical.endswith("/files"):
            turn_id = physical[: -len("/files")].rstrip("/")
            rel = ""
        elif "/files/" in physical:
            turn_id, rel = physical.split("/files/", 1)
        else:
            continue
        tree_path = f"files/{rel}".strip("/")
        if turn_id in version_errors:
            missing.append(physical)
            errors.append(version_errors[turn_id])
            continue
        local_ref = version_refs.get(turn_id, "")
        if not local_ref:
            try:
                local_ref = await asyncio.to_thread(
                    _ensure_local_version_ref,
                    repo_root=repo_root,
                    runtime_ctx=runtime_ctx,
                    version_id=turn_id,
                )
                version_refs[turn_id] = local_ref
            except Exception as exc:
                err = f"version_ref_unavailable:{turn_id}:{exc}"
                version_errors[turn_id] = err
                missing.append(physical)
                errors.append(err)
                continue

        target_root = pathlib.Path(outdir) / turn_id / "files"
        if await asyncio.to_thread(_git_path_is_file, repo_root=repo_root, ref_name=local_ref, tree_path=tree_path):
            try:
                data = await asyncio.to_thread(_git_read_blob, repo_root=repo_root, ref_name=local_ref, tree_path=tree_path)
                target = target_root / rel
                await asyncio.to_thread(_write_blob, target, data)
                rehosted.append(f"{turn_id}/files/{rel}")
            except Exception as exc:
                errors.append(f"git_materialize_failed:{turn_id}/files/{rel}:{exc}")
            continue

        candidates = await asyncio.to_thread(_git_list_tree, repo_root=repo_root, ref_name=local_ref, tree_path=tree_path)
        if not candidates:
            missing.append(f"{turn_id}/files/{rel}")
            continue

        pulled_any = False
        for candidate in candidates:
            if not candidate.startswith("files/"):
                continue
            rel_candidate = candidate[len("files/"):].strip("/")
            target_key = f"{turn_id}/files/{rel_candidate}"
            if target_key in seen_targets:
                continue
            try:
                data = await asyncio.to_thread(_git_read_blob, repo_root=repo_root, ref_name=local_ref, tree_path=candidate)
            except Exception as exc:
                errors.append(f"git_materialize_failed:{target_key}:{exc}")
                continue
            if not _blob_is_text_like(data=data, path_hint=rel_candidate):
                continue
            target = target_root / rel_candidate
            await asyncio.to_thread(_write_blob, target, data)
            seen_targets.add(target_key)
            rehosted.append(target_key)
            pulled_any = True

        if not pulled_any:
            missing.append(f"{turn_id}/files/{rel}")

    return {"rehosted": rehosted, "missing": missing, "errors": errors}
