# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import pathlib
import shlex
import subprocess
from typing import Any, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    ARTIFACT_NAMESPACE_FILES,
    ARTIFACT_NAMESPACE_SNAPSHOTS,
    split_physical_artifact_ref,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.workspace import (
    _guess_mime_from_path,
    _is_text_mime,
    workspace_lineage_branch_ref,
    workspace_lineage_segments,
    workspace_version_ref,
)
from kdcube_ai_app.infra.git.auth import (
    build_git_env,
    ensure_git_commit_identity as _ensure_git_commit_identity,
    normalize_git_remote_url,
)
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import artifact_outdir_for, runtime_outdir_for_artifact_outdir

_SKIP_WORKSPACE_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "logs", "executed_programs"}
_GIT_WORKSPACE_NAMESPACES = (ARTIFACT_NAMESPACE_FILES, ARTIFACT_NAMESPACE_SNAPSHOTS)
_WORKSPACE_BRANCH = "workspace"
_WORKSPACE_BRANCH_REF = f"refs/heads/{_WORKSPACE_BRANCH}"


class _ConversationScopedRuntimeCtx:
    def __init__(self, base: Any, conversation_id: str) -> None:
        self._base = base
        self.conversation_id = conversation_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _runtime_ctx_for_conversation(runtime_ctx: Any, conversation_id: str) -> Any:
    raw = str(conversation_id or "").strip()
    if not raw or raw == str(getattr(runtime_ctx, "conversation_id", "") or "").strip():
        return runtime_ctx
    return _ConversationScopedRuntimeCtx(runtime_ctx, raw)


class GitWorkspaceCommandError(RuntimeError):
    def __init__(
        self,
        *,
        op: str,
        cmd: List[str],
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.op = op
        self.cmd = list(cmd)
        self.returncode = int(returncode)
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        detail = (self.stderr or self.stdout or "no output").strip()
        super().__init__(
            f"{op} failed (exit={self.returncode}): {shlex.join(self.cmd)}"
            + (f" :: {detail}" if detail else "")
        )


def _run_checked(
    cmd: List[str],
    *,
    op: str,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise GitWorkspaceCommandError(
            op=op,
            cmd=cmd,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    return proc


def _git_cmd(repo_root: pathlib.Path, args: List[str]) -> List[str]:
    repo = str(pathlib.Path(repo_root).resolve())
    return ["git", "-c", f"safe.directory={repo}", "-C", repo, *args]


def _run_git_checked(
    repo_root: pathlib.Path,
    args: List[str],
    *,
    op: str,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    return _run_checked(_git_cmd(repo_root, args), op=op, env=env)


def _is_empty_workspace_pathspec_error(exc: GitWorkspaceCommandError) -> bool:
    detail = f"{exc.stderr}\n{exc.stdout}".lower()
    return (
        exc.op == "stage tracked workspace updates"
        and "pathspec '.' did not match any file(s) known to git" in detail
    )


def _workspace_cache_root(*, runtime_ctx: Any, outdir: pathlib.Path) -> pathlib.Path:
    root = runtime_outdir_for_artifact_outdir(pathlib.Path(outdir)).parent / ".react_workspace_git"
    segs = workspace_lineage_segments(runtime_ctx)
    return root / f"{segs['tenant']}__{segs['project']}__{segs['user_id']}__{segs['conversation_id']}"


def _artifact_outdir(outdir: pathlib.Path) -> pathlib.Path:
    return artifact_outdir_for(pathlib.Path(outdir))


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
    turn_root = _artifact_outdir(outdir) / turn_id
    if not (turn_root / ".git").exists():
        return {
            "repo_mode": "sparse git repo",
            "repo_status": "uninitialized",
        }
    repo_status = "unknown"
    try:
        proc = subprocess.run(
            _git_cmd(turn_root, ["status", "--short", "--untracked-files=all"]),
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
    turn_root = _artifact_outdir(outdir) / turn_id
    if not (turn_root / ".git").exists():
        return []
    try:
        subprocess.run(
            _git_cmd(turn_root, ["rev-parse", "--verify", "workspace"]),
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return []
    try:
        proc = subprocess.run(
            _git_cmd(turn_root, ["ls-tree", "-r", "--name-only", "workspace", "--", *_GIT_WORKSPACE_NAMESPACES]),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return []

    counts: Dict[tuple[str, str], int] = {}
    for line in (proc.stdout or "").splitlines():
        raw = line.strip()
        namespace = ""
        rel = ""
        for candidate in _GIT_WORKSPACE_NAMESPACES:
            prefix = f"{candidate}/"
            if raw.startswith(prefix):
                namespace = candidate
                rel = raw[len(prefix):].strip("/")
                break
        if namespace and rel:
            top = rel.split("/", 1)[0]
            key = (namespace, top)
            counts[key] = counts.get(key, 0) + 1

    out: List[Dict[str, Any]] = []
    for namespace, scope in sorted(counts.keys(), key=lambda item: (item[0], item[1].lower())):
        out.append({
            "namespace": namespace,
            "scope": f"{scope}/",
            "files": counts[(namespace, scope)],
            "kind": "dir",
        })
    return out


def current_turn_modified_files_scopes(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
) -> set:
    """Top-level files/<scope> dirs with uncommitted changes in the current-turn repo.

    Used by the ANNOUNCE [WORKSPACE] map to mark a checked-out project as
    MODIFIED this turn. Best-effort: returns an empty set on any error.
    """
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    if not turn_id:
        return set()
    turn_root = _artifact_outdir(outdir) / turn_id
    if not (turn_root / ".git").exists():
        return set()
    try:
        proc = subprocess.run(
            _git_cmd(turn_root, ["status", "--porcelain", "--untracked-files=all"]),
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return set()
    scopes: set = set()
    prefix = f"{ARTIFACT_NAMESPACE_FILES}/"
    for line in (proc.stdout or "").splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        if " -> " in path:  # rename: "old -> new"
            path = path.split(" -> ", 1)[1]
        if path.startswith(prefix):
            rest = path[len(prefix):].strip("/")
            top = rest.split("/", 1)[0] if rest else ""
            if top:
                scopes.add(top)
    return scopes


def _run_git_capture(repo_root: pathlib.Path, args: List[str], *, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        _git_cmd(repo_root, args),
        check=True,
        capture_output=True,
        env=env,
    )


async def _resolve_workspace_git(
    *,
    runtime_ctx: Any,
    logger: Optional[AgentLogger] = None,
) -> tuple[str, Dict[str, str]]:
    repo_url = str(getattr(runtime_ctx, "workspace_git_repo", "") or "").strip()
    if not repo_url:
        raise ValueError("missing_workspace_git_repo")
    log = logger or AgentLogger("react.workspace.git")
    normalized_repo_url = await normalize_git_remote_url(repo_url)
    if normalized_repo_url != repo_url:
        log.log(f"[react.workspace.git] using HTTPS for {repo_url}", level="INFO")
    env = await build_git_env(logger=log)
    return normalized_repo_url, env


def _ensure_workspace_repo(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
    repo_url: str,
    env: Dict[str, str],
    logger: Optional[AgentLogger] = None,
) -> pathlib.Path:
    if not repo_url:
        raise ValueError("missing_workspace_git_repo")
    log = logger or AgentLogger("react.workspace.git")
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
            _git_cmd(repo_root, ["remote", "add", "origin", repo_url]),
            check=True,
            capture_output=True,
            env=env,
        )
        try:
            subprocess.run(
                _git_cmd(repo_root, ["config", "--unset-all", "remote.origin.fetch"]),
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass
        subprocess.run(
            _git_cmd(repo_root, ["config", "remote.origin.tagOpt", "--no-tags"]),
            check=True,
            capture_output=True,
        )
    else:
        try:
            subprocess.run(
                _git_cmd(repo_root, ["remote", "set-url", "origin", repo_url]),
                check=True,
                capture_output=True,
                env=env,
            )
        except Exception:
            pass
    return repo_root


def _git_ref_points_to_commit(*, repo_root: pathlib.Path, ref_name: str) -> bool:
    try:
        subprocess.run(
            _git_cmd(repo_root, ["cat-file", "-e", f"{ref_name}^{{commit}}"]),
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _git_rev_parse(*, repo_root: pathlib.Path, ref_name: str) -> str:
    proc = _run_git_checked(
        repo_root,
        ["rev-parse", "--verify", ref_name],
        op=f"resolve git ref {ref_name}",
    )
    return (proc.stdout or "").strip()


def _ensure_local_version_ref(
    *,
    repo_root: pathlib.Path,
    runtime_ctx: Any,
    version_id: str,
    env: Dict[str, str],
) -> str:
    remote_ref = workspace_version_ref(runtime_ctx, version_id)
    if not remote_ref:
        raise ValueError("missing_workspace_version_ref")
    segs = workspace_lineage_segments(runtime_ctx)
    local_ref = f"refs/kdcube-local/versions/{segs['conversation_id']}/{version_id}"
    if _git_ref_points_to_commit(repo_root=repo_root, ref_name=local_ref):
        return local_ref
    subprocess.run(
        _git_cmd(repo_root, ["fetch", "--no-tags", "origin", f"+{remote_ref}:{local_ref}"]),
        check=True,
        capture_output=True,
        env=env,
    )
    if not _git_ref_points_to_commit(repo_root=repo_root, ref_name=local_ref):
        raise GitWorkspaceCommandError(
            op="resolve fetched workspace version ref",
            cmd=_git_cmd(repo_root, ["cat-file", "-e", f"{local_ref}^{{commit}}"]),
            returncode=128,
            stderr=f"fetched ref does not point to a local commit object: {local_ref}",
        )
    return local_ref


def _ensure_local_lineage_branch_ref(
    *,
    repo_root: pathlib.Path,
    runtime_ctx: Any,
    env: Dict[str, str],
) -> str:
    remote_ref = workspace_lineage_branch_ref(runtime_ctx)
    if not remote_ref:
        return ""
    local_ref = _WORKSPACE_BRANCH_REF
    try:
        subprocess.run(
            _git_cmd(repo_root, ["fetch", "--no-tags", "origin", f"+{remote_ref}:{local_ref}"]),
            check=True,
            capture_output=True,
            env=env,
        )
    except subprocess.CalledProcessError:
        if _git_ref_points_to_commit(repo_root=repo_root, ref_name=local_ref):
            return local_ref
        return ""
    if not _git_ref_points_to_commit(repo_root=repo_root, ref_name=local_ref):
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
        _git_cmd(turn_root, ["fetch", "--no-tags", str(source_repo_root), f"+{source_ref}:{target_ref}"]),
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


def _configure_turn_git_workspace_repo(*, turn_root: pathlib.Path, runtime_ctx: Any) -> None:
    name, email = _workspace_commit_identity(runtime_ctx)
    _ensure_git_commit_identity(repo_root=turn_root, name=name, email=email)
    _run_git_checked(
        turn_root,
        ["config", "advice.detachedHead", "false"],
        op="configure detached-head advice",
    )
    _run_git_checked(
        turn_root,
        ["config", "core.sparseCheckout", "true"],
        op="configure sparse checkout",
    )
    sparse_file = turn_root / ".git" / "info" / "sparse-checkout"
    sparse_file.parent.mkdir(parents=True, exist_ok=True)
    if not sparse_file.exists():
        sparse_file.write_text("", encoding="utf-8")


def _repair_existing_turn_git_workspace(
    *,
    turn_root: pathlib.Path,
    source_repo_root: pathlib.Path,
    lineage_ref: str,
) -> None:
    if _git_ref_points_to_commit(repo_root=turn_root, ref_name=_WORKSPACE_BRANCH_REF):
        return

    _run_git_checked(
        turn_root,
        ["update-ref", "-d", _WORKSPACE_BRANCH_REF],
        op="remove invalid workspace branch ref before repair",
    )

    if lineage_ref and _git_ref_points_to_commit(repo_root=source_repo_root, ref_name=lineage_ref):
        repair_ref = "refs/kdcube-local/repair/workspace"
        _run_git_checked(
            turn_root,
            ["fetch", "--no-tags", str(source_repo_root), f"+{lineage_ref}:{repair_ref}"],
            op="fetch workspace lineage for turn repo repair",
        )
        repair_sha = _git_rev_parse(repo_root=turn_root, ref_name=repair_ref)
        _run_git_checked(
            turn_root,
            ["update-ref", _WORKSPACE_BRANCH_REF, repair_sha],
            op="repair turn workspace branch ref",
        )
        _run_git_checked(
            turn_root,
            ["reset", "--mixed", "-q", _WORKSPACE_BRANCH],
            op="reset repaired workspace index",
        )
        try:
            _run_git_checked(
                turn_root,
                ["update-ref", "-d", repair_ref],
                op="cleanup turn workspace repair ref",
            )
        except GitWorkspaceCommandError:
            pass
        return

    _run_git_checked(
        turn_root,
        ["symbolic-ref", "HEAD", _WORKSPACE_BRANCH_REF],
        op="point HEAD at workspace branch",
    )


def _ensure_current_turn_git_workspace_sync(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
    repo_url: str,
    env: Dict[str, str],
    logger: Optional[AgentLogger] = None,
) -> pathlib.Path:
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    if not turn_id:
        raise ValueError("missing_turn_id")
    log = logger or AgentLogger("react.workspace.git")
    repo_root = _ensure_workspace_repo(runtime_ctx=runtime_ctx, outdir=outdir, repo_url=repo_url, env=env, logger=log)
    lineage_ref = _ensure_local_lineage_branch_ref(repo_root=repo_root, runtime_ctx=runtime_ctx, env=env)

    turn_root = _artifact_outdir(outdir) / turn_id
    git_dir = turn_root / ".git"
    if git_dir.exists():
        _configure_turn_git_workspace_repo(turn_root=turn_root, runtime_ctx=runtime_ctx)
        _repair_existing_turn_git_workspace(
            turn_root=turn_root,
            source_repo_root=repo_root,
            lineage_ref=lineage_ref,
        )
        return turn_root

    turn_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", str(turn_root)],
        check=True,
        capture_output=True,
    )
    _configure_turn_git_workspace_repo(turn_root=turn_root, runtime_ctx=runtime_ctx)

    if lineage_ref:
        subprocess.run(
            _git_cmd(turn_root, ["fetch", "--no-tags", str(repo_root), f"+{lineage_ref}:{_WORKSPACE_BRANCH_REF}"]),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            _git_cmd(turn_root, ["checkout", "-f", _WORKSPACE_BRANCH]),
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(
            _git_cmd(turn_root, ["checkout", "--orphan", _WORKSPACE_BRANCH]),
            check=True,
            capture_output=True,
        )

    return turn_root


async def ensure_current_turn_git_workspace(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
    logger: Optional[AgentLogger] = None,
) -> pathlib.Path:
    log = logger or AgentLogger("react.workspace.git")
    repo_url, env = await _resolve_workspace_git(runtime_ctx=runtime_ctx, logger=log)
    return await asyncio.to_thread(
        _ensure_current_turn_git_workspace_sync,
        runtime_ctx=runtime_ctx,
        outdir=outdir,
        repo_url=repo_url,
        env=env,
        logger=log,
    )


def _git_path_is_file(*, repo_root: pathlib.Path, ref_name: str, tree_path: str) -> bool:
    try:
        proc = subprocess.run(
            _git_cmd(repo_root, ["cat-file", "-t", f"{ref_name}:{tree_path}"]),
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
            _git_cmd(repo_root, ["ls-tree", "-r", "--name-only", ref_name, "--", tree_path]),
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
    return _git_ref_points_to_commit(repo_root=repo_root, ref_name="HEAD")


def _git_head_sha(*, repo_root: pathlib.Path) -> str:
    proc = subprocess.run(
        _git_cmd(repo_root, ["rev-parse", "HEAD"]),
        check=True,
        capture_output=True,
        text=True,
    )
    return (proc.stdout or "").strip()


def _git_has_staged_changes(*, repo_root: pathlib.Path) -> bool:
    proc = subprocess.run(
        _git_cmd(repo_root, ["diff", "--cached", "--quiet", "--exit-code"]),
        check=False,
        capture_output=True,
    )
    return proc.returncode != 0


def _workspace_publish_skipped(*, turn_root: pathlib.Path, reason: str) -> Dict[str, Any]:
    return {
        "turn_root": str(turn_root),
        "skipped": True,
        "reason": reason,
        "committed": False,
    }


def _git_is_dirty(*, repo_root: pathlib.Path) -> bool:
    proc = subprocess.run(
        _git_cmd(repo_root, ["status", "--porcelain", "--untracked-files=all"]),
        check=True,
        capture_output=True,
        text=True,
    )
    return bool((proc.stdout or "").strip())


def _publish_workspace_version_alias(
    *,
    turn_root: pathlib.Path,
    repo_root: pathlib.Path,
    head_sha: str,
    lineage_ref: str,
    version_ref: str,
    turn_id: str,
    env: Dict[str, str],
) -> None:
    _run_git_checked(
        turn_root,
        ["push", str(repo_root), "HEAD:refs/heads/workspace"],
        op="push unchanged workspace branch into local lineage repo",
    )
    _run_git_checked(
        turn_root,
        ["push", str(repo_root), f"{head_sha}:refs/kdcube-local/versions/{turn_id}"],
        op="push unchanged workspace version ref into local lineage repo",
    )
    _run_git_checked(
        repo_root,
        ["push", "origin", f"refs/heads/workspace:{lineage_ref}"],
        op="push unchanged workspace lineage branch to origin",
        env=env,
    )
    _run_git_checked(
        repo_root,
        ["push", "origin", f"refs/kdcube-local/versions/{turn_id}:{version_ref}"],
        op="push unchanged workspace version ref to origin",
        env=env,
    )


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
        _git_cmd(repo_root, ["check-ignore", "-q", "--no-index", "--", rel_path]),
        check=False,
        capture_output=True,
    )
    return proc.returncode == 0


def _stage_current_turn_text_workspace(*, turn_root: pathlib.Path) -> None:
    try:
        _run_git_checked(
            turn_root,
            ["add", "--sparse", "-u", "--", "."],
            op="stage tracked workspace updates",
        )
    except GitWorkspaceCommandError as exc:
        if not _is_empty_workspace_pathspec_error(exc):
            raise
    new_paths: List[str] = []
    for namespace in _GIT_WORKSPACE_NAMESPACES:
        workspace_root = turn_root / namespace
        if not workspace_root.exists():
            continue
        for path in workspace_root.rglob("*"):
            if not path.is_file():
                continue
            if _workspace_path_is_skipped(path, turn_root=turn_root):
                continue
            # Binaries are intentionally included in the project snapshot: files/ is the
            # git-backed project, so its whole tree (text AND binary) is versioned per turn.
            # (Retrieval/sharing of an individual file still goes through hosting via the
            # exec contract — the rn resolver points at hosting, not git.)
            # if not _file_is_text_like(path):
            #     continue
            rel_path = str(path.relative_to(turn_root))
            if _git_path_is_ignored(repo_root=turn_root, rel_path=rel_path):
                continue
            new_paths.append(rel_path)
    if not new_paths:
        return
    for idx in range(0, len(new_paths), 128):
        chunk = new_paths[idx: idx + 128]
        _run_git_checked(
            turn_root,
            ["add", "--sparse", "--", *chunk],
            op="stage new workspace paths",
        )


def _publish_current_turn_git_workspace_sync(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
    repo_url: str,
    env: Dict[str, str],
    logger: Optional[AgentLogger] = None,
) -> Dict[str, Any]:
    turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
    if not turn_id:
        raise ValueError("missing_turn_id")
    log = logger or AgentLogger("react.workspace.git")
    turn_root = _artifact_outdir(outdir) / turn_id
    if not turn_root.exists():
        return _workspace_publish_skipped(
            turn_root=turn_root,
            reason="workspace_not_materialized",
        )
    has_workspace_namespace = any((turn_root / namespace).exists() for namespace in _GIT_WORKSPACE_NAMESPACES)
    if not (turn_root / ".git").exists() and not has_workspace_namespace:
        return _workspace_publish_skipped(
            turn_root=turn_root,
            reason="empty_workspace",
        )
    turn_root = _ensure_current_turn_git_workspace_sync(
        runtime_ctx=runtime_ctx,
        outdir=outdir,
        repo_url=repo_url,
        env=env,
        logger=log,
    )
    repo_root = _ensure_workspace_repo(
        runtime_ctx=runtime_ctx,
        outdir=outdir,
        repo_url=repo_url,
        env=env,
        logger=log,
    )
    lineage_ref = workspace_lineage_branch_ref(runtime_ctx)
    version_ref = workspace_version_ref(runtime_ctx, turn_id)
    if not lineage_ref or not version_ref:
        raise ValueError("missing_workspace_refs")

    _stage_current_turn_text_workspace(turn_root=turn_root)
    has_head = _git_has_head(repo_root=turn_root)
    if not _git_has_staged_changes(repo_root=turn_root):
        if has_head:
            head_sha = _git_head_sha(repo_root=turn_root)
            _publish_workspace_version_alias(
                turn_root=turn_root,
                repo_root=repo_root,
                head_sha=head_sha,
                lineage_ref=lineage_ref,
                version_ref=version_ref,
                turn_id=turn_id,
                env=env,
            )
            result = _workspace_publish_skipped(
                turn_root=turn_root,
                reason="workspace_unchanged",
            )
            result.update({
                "commit_sha": head_sha,
                "lineage_ref": lineage_ref,
                "version_ref": version_ref,
                "version_aliased": True,
            })
            return result
        return _workspace_publish_skipped(
            turn_root=turn_root,
            reason="empty_workspace",
        )
    if not has_head:
        _run_git_checked(
            turn_root,
            ["commit", "-m", f"React workspace snapshot {turn_id}"],
            op="create initial workspace snapshot commit",
        )
    else:
        _run_git_checked(
            turn_root,
            ["commit", "-m", f"React workspace snapshot {turn_id}"],
            op="commit workspace snapshot",
        )

    head_sha = _git_head_sha(repo_root=turn_root)
    _run_git_checked(
        turn_root,
        ["push", str(repo_root), "HEAD:refs/heads/workspace"],
        op="push workspace branch into local lineage repo",
    )
    _run_git_checked(
        turn_root,
        ["push", str(repo_root), f"{head_sha}:refs/kdcube-local/versions/{turn_id}"],
        op="push workspace version ref into local lineage repo",
    )

    _run_git_checked(
        repo_root,
        ["push", "origin", f"refs/heads/workspace:{lineage_ref}"],
        op="push workspace lineage branch to origin",
        env=env,
    )
    _run_git_checked(
        repo_root,
        ["push", "origin", f"refs/kdcube-local/versions/{turn_id}:{version_ref}"],
        op="push workspace version ref to origin",
        env=env,
    )
    return {
        "turn_root": str(turn_root),
        "commit_sha": head_sha,
        "lineage_ref": lineage_ref,
        "version_ref": version_ref,
        "committed": True,
    }


async def publish_current_turn_git_workspace(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
    logger: Optional[AgentLogger] = None,
) -> Dict[str, Any]:
    log = logger or AgentLogger("react.workspace.git")
    repo_url, env = await _resolve_workspace_git(runtime_ctx=runtime_ctx, logger=log)
    return await asyncio.to_thread(
        _publish_current_turn_git_workspace_sync,
        runtime_ctx=runtime_ctx,
        outdir=outdir,
        repo_url=repo_url,
        env=env,
        logger=log,
    )


def _checkout_current_turn_git_workspace_sync(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
    version_id: str,
    repo_url: str,
    env: Dict[str, str],
    logger: Optional[AgentLogger] = None,
) -> Dict[str, Any]:
    version = str(version_id or "").strip()
    if not version:
        raise ValueError("missing_version_id")
    log = logger or AgentLogger("react.workspace.git")
    turn_root = _ensure_current_turn_git_workspace_sync(
        runtime_ctx=runtime_ctx,
        outdir=outdir,
        repo_url=repo_url,
        env=env,
        logger=log,
    )
    if _git_is_dirty(repo_root=turn_root):
        raise ValueError("workspace_checkout_dirty")
    repo_root = _ensure_workspace_repo(
        runtime_ctx=runtime_ctx,
        outdir=outdir,
        repo_url=repo_url,
        env=env,
        logger=log,
    )
    local_ref = _ensure_local_version_ref(
        repo_root=repo_root,
        runtime_ctx=runtime_ctx,
        version_id=version,
        env=env,
    )
    checkout_ref = _fetch_ref_into_turn_repo(
        turn_root=turn_root,
        source_repo_root=repo_root,
        source_ref=local_ref,
        target_ref=f"refs/kdcube-local/checkout/{version}",
    )
    _run_git_checked(
        turn_root,
        ["sparse-checkout", "set", "--cone", *_GIT_WORKSPACE_NAMESPACES],
        op="configure sparse checkout",
    )
    _run_git_checked(
        turn_root,
        ["checkout", "-f", "workspace"],
        op="checkout workspace branch",
    )
    _run_git_checked(
        turn_root,
        ["reset", "--hard", checkout_ref],
        op="reset workspace to requested version",
    )
    _run_git_checked(
        turn_root,
        ["clean", "-fd", "--", *_GIT_WORKSPACE_NAMESPACES],
        op="clean workspace paths",
    )
    return {
        "turn_root": str(turn_root),
        "checked_out_version": version,
        "version_ref": workspace_version_ref(runtime_ctx, version),
        "workspace_ref": "refs/heads/workspace",
        "checkout_ref": checkout_ref,
    }


async def checkout_current_turn_git_workspace(
    *,
    runtime_ctx: Any,
    outdir: pathlib.Path,
    version_id: str,
    logger: Optional[AgentLogger] = None,
) -> Dict[str, Any]:
    log = logger or AgentLogger("react.workspace.git")
    repo_url, env = await _resolve_workspace_git(runtime_ctx=runtime_ctx, logger=log)
    return await asyncio.to_thread(
        _checkout_current_turn_git_workspace_sync,
        runtime_ctx=runtime_ctx,
        outdir=outdir,
        version_id=version_id,
        repo_url=repo_url,
        env=env,
        logger=log,
    )


def _parse_git_workspace_physical_ref(physical: str) -> tuple[str, str, str, str]:
    raw = str(physical or "").strip().strip("/")
    for namespace in _GIT_WORKSPACE_NAMESPACES:
        suffix = f"/{namespace}"
        if raw.endswith(suffix):
            unscoped = raw[: -len(suffix)].strip("/")
            conversation_id = ""
            if unscoped.startswith("conv_") and "/" in unscoped:
                conversation_segment, _, turn_id = unscoped.partition("/")
                conversation_id = conversation_segment[len("conv_"):]
            else:
                turn_id = unscoped
            return conversation_id, turn_id, namespace, ""
    conversation_id, turn_id, namespace, rel = split_physical_artifact_ref(raw)
    if not turn_id or namespace not in _GIT_WORKSPACE_NAMESPACES:
        return "", "", "", ""
    return conversation_id, turn_id, namespace, rel.strip("/")


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
        repo_url, env = await _resolve_workspace_git(runtime_ctx=runtime_ctx)
        repo_root = await asyncio.to_thread(
            _ensure_workspace_repo,
            runtime_ctx=runtime_ctx,
            outdir=outdir,
            repo_url=repo_url,
            env=env,
        )
    except Exception as exc:
        return {"rehosted": [], "missing": [], "errors": [f"workspace_git_repo_unavailable:{exc}"]}

    rehosted: List[str] = []
    missing: List[str] = []
    errors: List[str] = []
    seen_targets: set[str] = set()
    version_refs: Dict[tuple[str, str], str] = {}
    version_errors: Dict[tuple[str, str], str] = {}

    for physical in paths:
        if not isinstance(physical, str):
            continue
        conversation_id, turn_id, namespace, rel = _parse_git_workspace_physical_ref(physical)
        if not turn_id or not namespace:
            continue
        scoped_runtime_ctx = _runtime_ctx_for_conversation(runtime_ctx, conversation_id)
        tree_path = f"{namespace}/{rel}".strip("/")
        ref_key = (conversation_id, turn_id)
        if ref_key in version_errors:
            missing.append(physical)
            errors.append(version_errors[ref_key])
            continue
        local_ref = version_refs.get(ref_key, "")
        if not local_ref:
            try:
                local_ref = await asyncio.to_thread(
                    _ensure_local_version_ref,
                    repo_root=repo_root,
                    runtime_ctx=scoped_runtime_ctx,
                    version_id=turn_id,
                    env=env,
                )
                version_refs[ref_key] = local_ref
            except Exception as exc:
                err = f"version_ref_unavailable:{conversation_id + '/' if conversation_id else ''}{turn_id}:{exc}"
                version_errors[ref_key] = err
                missing.append(physical)
                errors.append(err)
                continue

        artifact_root = _artifact_outdir(outdir)
        target_root = (
            artifact_root / f"conv_{conversation_id}" / turn_id / namespace
            if conversation_id
            else artifact_root / turn_id / namespace
        )
        if await asyncio.to_thread(_git_path_is_file, repo_root=repo_root, ref_name=local_ref, tree_path=tree_path):
            try:
                data = await asyncio.to_thread(_git_read_blob, repo_root=repo_root, ref_name=local_ref, tree_path=tree_path)
                target = target_root / rel
                await asyncio.to_thread(_write_blob, target, data)
                target_key = f"{turn_id}/{namespace}/{rel}"
                if conversation_id:
                    target_key = f"conv_{conversation_id}/{target_key}"
                rehosted.append(target_key)
            except Exception as exc:
                display_key = f"{conversation_id + '/' if conversation_id else ''}{turn_id}/{namespace}/{rel}"
                errors.append(f"git_materialize_failed:{display_key}:{exc}")
            continue

        candidates = await asyncio.to_thread(_git_list_tree, repo_root=repo_root, ref_name=local_ref, tree_path=tree_path)
        if not candidates:
            missing_key = f"{turn_id}/{namespace}/{rel}"
            if conversation_id:
                missing_key = f"conv_{conversation_id}/{missing_key}"
            missing.append(missing_key)
            continue

        pulled_any = False
        for candidate in candidates:
            prefix = f"{namespace}/"
            if not candidate.startswith(prefix):
                continue
            rel_candidate = candidate[len(prefix):].strip("/")
            target_key = f"{turn_id}/{namespace}/{rel_candidate}"
            if conversation_id:
                target_key = f"conv_{conversation_id}/{target_key}"
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
            missing_key = f"{turn_id}/{namespace}/{rel}"
            if conversation_id:
                missing_key = f"conv_{conversation_id}/{missing_key}"
            missing.append(missing_key)

    return {"rehosted": rehosted, "missing": missing, "errors": errors}
