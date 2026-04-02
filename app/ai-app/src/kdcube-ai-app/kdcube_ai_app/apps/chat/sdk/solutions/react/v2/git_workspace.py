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


def _workspace_cache_root(*, runtime_ctx: Any, outdir: pathlib.Path) -> pathlib.Path:
    root = pathlib.Path(outdir).parent / ".react_workspace_git"
    segs = workspace_lineage_segments(runtime_ctx)
    return root / f"{segs['tenant']}__{segs['project']}__{segs['user_id']}__{segs['conversation_id']}"


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
    repo_root = cache_root / "repo"
    cache_root.mkdir(parents=True, exist_ok=True)
    if not (repo_root / ".git").exists():
        subprocess.run(
            ["git", "clone", "--no-checkout", repo_url, str(repo_root)],
            check=True,
            capture_output=True,
            env=env,
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
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "fetch", "--all", "--tags", "--prune", "--force"],
            check=True,
            capture_output=True,
            env=env,
        )
    except Exception as exc:
        log.log(f"[react.workspace.git] fetch failed: {exc}", level="WARNING")
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
    subprocess.run(
        ["git", "-C", str(repo_root), "fetch", "origin", f"+{remote_ref}:{local_ref}"],
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
    segs = workspace_lineage_segments(runtime_ctx)
    local_ref = (
        "refs/kdcube-local/lineages/"
        f"{segs['tenant']}/{segs['project']}/{segs['user_id']}/{segs['conversation_id']}"
    )
    try:
        subprocess.run(
            ["git", "-C", str(repo_root), "fetch", "origin", f"+{remote_ref}:{local_ref}"],
            check=True,
            capture_output=True,
            env=env,
        )
    except subprocess.CalledProcessError:
        if _git_has_ref(repo_root=repo_root, ref_name=local_ref):
            return local_ref
        return ""
    return local_ref


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
    try:
        subprocess.run(
            ["git", "-C", str(turn_root), "remote", "add", "origin", str(repo_root)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        subprocess.run(
            ["git", "-C", str(turn_root), "remote", "set-url", "origin", str(repo_root)],
            check=True,
            capture_output=True,
        )
    sparse_file = turn_root / ".git" / "info" / "sparse-checkout"
    sparse_file.parent.mkdir(parents=True, exist_ok=True)
    sparse_file.write_text("", encoding="utf-8")

    if lineage_ref:
        subprocess.run(
            ["git", "-C", str(turn_root), "fetch", "origin", f"+{lineage_ref}:refs/heads/workspace"],
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


def _file_is_text_like(path: pathlib.Path) -> bool:
    try:
        data = path.read_bytes()
    except Exception:
        return False
    return _blob_is_text_like(data=data, path_hint=str(path))


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
        if not _file_is_text_like(path):
            continue
        text_paths.append(str(path.relative_to(turn_root)))
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
        ["git", "-C", str(turn_root), "push", "origin", f"HEAD:{lineage_ref}"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(turn_root), "push", "origin", f"{head_sha}:{version_ref}"],
        check=True,
        capture_output=True,
    )

    env = _build_git_env()
    subprocess.run(
        ["git", "-C", str(repo_root), "push", "origin", f"{lineage_ref}:{lineage_ref}"],
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "push", "origin", f"{version_ref}:{version_ref}"],
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

    for physical in paths:
        if not isinstance(physical, str) or "/files/" not in physical:
            continue
        turn_id, rel = physical.split("/files/", 1)
        tree_path = f"files/{rel}".strip("/")
        try:
            local_ref = await asyncio.to_thread(
                _ensure_local_version_ref,
                repo_root=repo_root,
                runtime_ctx=runtime_ctx,
                version_id=turn_id,
            )
        except Exception as exc:
            missing.append(physical)
            errors.append(f"version_ref_unavailable:{turn_id}:{exc}")
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
