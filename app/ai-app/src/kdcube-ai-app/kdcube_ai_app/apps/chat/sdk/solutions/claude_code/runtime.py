# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.agent import ClaudeCodeAgent
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.types import (
    ClaudeCodeRunResult,
    ClaudeCodeTurnKind,
)
from kdcube_ai_app.infra.git.auth import (
    build_git_env,
    ensure_git_commit_identity as _ensure_git_commit_identity,
    normalize_git_remote_url,
)


ClaudeCodeSessionStoreImplementation = Literal["local", "git"]


def _safe_segment(value: str | None, *, fallback: str) -> str:
    raw = str(value or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in raw).strip("-_.")
    return safe or fallback


@dataclass(frozen=True)
class ClaudeCodeSessionStoreConfig:
    implementation: ClaudeCodeSessionStoreImplementation = "local"
    local_root: pathlib.Path = pathlib.Path(".")
    tenant: str = "home"
    project: str = "default-project"
    user_id: str = "anonymous"
    conversation_id: str = "conversation"
    agent_name: str = "claude-code"
    git_repo: str | None = None
    bootstrap_turn_kinds: tuple[ClaudeCodeTurnKind, ...] = field(default_factory=lambda: ("regular",))
    publish_turn_kinds: tuple[ClaudeCodeTurnKind, ...] = field(default_factory=lambda: ("regular",))

    def __post_init__(self) -> None:
        object.__setattr__(self, "implementation", str(self.implementation or "local").strip().lower() or "local")
        object.__setattr__(self, "local_root", pathlib.Path(self.local_root))
        object.__setattr__(self, "tenant", _safe_segment(self.tenant, fallback="home"))
        object.__setattr__(self, "project", _safe_segment(self.project, fallback="default-project"))
        object.__setattr__(self, "user_id", _safe_segment(self.user_id, fallback="anonymous"))
        object.__setattr__(self, "conversation_id", _safe_segment(self.conversation_id, fallback="conversation"))
        object.__setattr__(self, "agent_name", _safe_segment(self.agent_name, fallback="claude-code"))
        object.__setattr__(self, "git_repo", str(self.git_repo or "").strip() or None)
        object.__setattr__(
            self,
            "bootstrap_turn_kinds",
            tuple(kind for kind in self.bootstrap_turn_kinds if str(kind).strip()),
        )
        object.__setattr__(
            self,
            "publish_turn_kinds",
            tuple(kind for kind in self.publish_turn_kinds if str(kind).strip()),
        )
        if self.implementation not in {"local", "git"}:
            raise ValueError(f"unsupported_claude_code_session_store:{self.implementation}")


def claude_code_session_branch_ref(config: ClaudeCodeSessionStoreConfig) -> str:
    return (
        "refs/heads/kdcube/claude/"
        f"{config.tenant}/{config.project}/{config.user_id}/{config.conversation_id}/{config.agent_name}"
    )


def _session_cache_root(config: ClaudeCodeSessionStoreConfig) -> pathlib.Path:
    local_root = pathlib.Path(config.local_root)
    return (
        local_root.parent
        / ".claude_session_git"
        / f"{config.tenant}__{config.project}__{config.user_id}__{config.conversation_id}__{config.agent_name}"
    )


def _session_lineage_repo_root(config: ClaudeCodeSessionStoreConfig) -> pathlib.Path:
    return _session_cache_root(config) / "lineage.git"


def _run_git_capture(
    repo_root: pathlib.Path,
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


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


def _git_has_staged_changes(*, repo_root: pathlib.Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    return proc.returncode != 0


def _git_head_sha(*, repo_root: pathlib.Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return (proc.stdout or "").strip()


def _ensure_session_repo(
    *,
    config: ClaudeCodeSessionStoreConfig,
    repo_url: str,
    env: dict[str, str],
) -> pathlib.Path:
    if config.implementation != "git":
        raise ValueError("claude_code_session_store_not_git")
    if not repo_url:
        raise ValueError("missing_claude_code_session_git_repo")
    cache_root = _session_cache_root(config)
    repo_root = _session_lineage_repo_root(config)
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


def _ensure_local_lineage_branch_ref(
    *,
    repo_root: pathlib.Path,
    config: ClaudeCodeSessionStoreConfig,
    env: dict[str, str],
) -> str:
    remote_ref = claude_code_session_branch_ref(config)
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


def _session_commit_identity(config: ClaudeCodeSessionStoreConfig) -> tuple[str, str]:
    safe_user = _safe_segment(config.user_id, fallback="claude")
    return f"Claude Session ({config.user_id})", f"{safe_user}@local.invalid"


def _ensure_local_git_repo(*, local_root: pathlib.Path, config: ClaudeCodeSessionStoreConfig) -> None:
    if not (local_root / ".git").exists():
        local_root.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", str(local_root)],
            check=True,
            capture_output=True,
        )
    name, email = _session_commit_identity(config)
    _ensure_git_commit_identity(repo_root=local_root, name=name, email=email)
    subprocess.run(
        ["git", "-C", str(local_root), "config", "advice.detachedHead", "false"],
        check=True,
        capture_output=True,
    )


_TRANSIENT_GIT_LOCK_MARKERS = (
    "another git process",
    "cannot lock ref",
    "index.lock",
)


def _run_local_git_mutation(
    *,
    repo_root: pathlib.Path,
    args: list[str],
    attempts: int = 5,
) -> subprocess.CompletedProcess[str]:
    """Run a local Git write, retrying only short-lived lock conflicts."""
    command = ["git", "-C", str(repo_root), *args]
    last: subprocess.CompletedProcess[str] | None = None
    for attempt in range(max(1, attempts)):
        last = subprocess.run(command, capture_output=True, text=True)
        if last.returncode == 0:
            return last
        detail = "\n".join(
            part.strip() for part in (last.stderr, last.stdout) if part and part.strip()
        )
        transient = any(
            marker in detail.lower() for marker in _TRANSIENT_GIT_LOCK_MARKERS
        )
        if not transient or attempt + 1 >= max(1, attempts):
            operation = args[0] if args else "operation"
            raise RuntimeError(
                f"Claude session-store git {operation} failed: "
                f"{detail or f'exit code {last.returncode}'}"
            )
        time.sleep(0.1 * (2**attempt))
    raise AssertionError("unreachable")


def _clear_session_root(*, local_root: pathlib.Path) -> None:
    if not local_root.exists():
        local_root.mkdir(parents=True, exist_ok=True)
        return
    for child in local_root.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _reset_local_session_checkout(*, local_root: pathlib.Path) -> None:
    if local_root.exists():
        shutil.rmtree(local_root, ignore_errors=True)


CLAUDE_CODE_SESSION_GITIGNORE = """\
# Managed by kdcube ClaudeCodeRuntime — keep session JSONLs, drop everything else.
# Hand-edit on the remote lineage branch if you need to refine; subsequent
# bootstraps will respect the version checked out from the remote.
.credentials.json
.claude.json
.claude.json.lock
backups/
statsig/
shell-snapshots/
ide/
sessions/
kdcube-live-events.json
kdcube-live-hook.py
kdcube-live-settings.json
"""

#: Live control used to seed itself into this checkout, so the store committed
#: its files and restored them at the start of every later turn — handing that
#: turn the PREVIOUS turn's stop, with the previous turn's id on the hook
#: command line to match. Live control now keeps its own directory beside this
#: one; these names are shed so a lineage that already carries them stops
#: replaying a stop nobody pressed.
_LIVE_CONTROL_LEFTOVERS = (
    "kdcube-live-events.json",
    "kdcube-live-hook.py",
    "kdcube-live-settings.json",
)


CLI_CREDENTIALS_FILENAME = ".credentials.json"


def _materialize_cli_credentials(
    *,
    local_root: pathlib.Path,
    env: dict[str, str],
    logger=None,
) -> str | None:
    """Link this machine's Claude CLI credential into the store checkout.

    The checkout is CLAUDE_CONFIG_DIR, so an existing CLI login is invisible to
    the subprocess without it. Must run after the bootstrap clear/restore, which
    removes every child except .git. Skipped when the descriptor supplies a key:
    that deployment has already stated how it authenticates. The link is removed
    again unless git confirms the path is ignored, so a lineage that once tracked
    the name can never stage a credential.
    """
    log = logger or logging.getLogger("ClaudeCodeRuntime")
    if env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_CODE_KEY"):
        return None
    source = pathlib.Path.home() / ".claude" / CLI_CREDENTIALS_FILENAME
    if not source.is_file():
        return None
    target = local_root / CLI_CREDENTIALS_FILENAME
    try:
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(source)
    except OSError as exc:
        log.warning("[claude.session] cannot link the CLI credential: %s", exc)
        return None
    def _is_ignored() -> bool:
        return subprocess.run(
            ["git", "-C", str(local_root), "check-ignore", "-q", CLI_CREDENTIALS_FILENAME],
            capture_output=True,
        ).returncode == 0

    ignored = _is_ignored()
    if not ignored:
        # A lineage created before the managed .gitignore can carry this name as
        # a tracked path, and ignore rules never apply to tracked paths. Drop it
        # from the index; the end-of-turn snapshot commits the removal, so the
        # lineage stops carrying it after one turn.
        subprocess.run(
            [
                "git", "-C", str(local_root), "rm", "--cached", "--ignore-unmatch",
                "-q", CLI_CREDENTIALS_FILENAME,
            ],
            capture_output=True,
        )
        ignored = _is_ignored()
    if not ignored:
        target.unlink(missing_ok=True)
        log.warning(
            "[claude.session] %s is not ignored by this lineage; the CLI login stays "
            "unused rather than risking a staged credential",
            CLI_CREDENTIALS_FILENAME,
        )
        return None
    log.info("[claude.session] linked the host CLI credential into %s", local_root)
    return str(target)


def _ensure_session_gitignore(*, local_root: pathlib.Path) -> bool:
    gitignore_path = local_root / ".gitignore"
    if gitignore_path.exists():
        return False
    gitignore_path.write_text(CLAUDE_CODE_SESSION_GITIGNORE, encoding="utf-8")
    return True


def _drop_live_control_leftovers(*, local_root: pathlib.Path) -> list[str]:
    """Remove files an earlier live control left in this checkout.

    Deleting them on disk is enough to retire them: the end-of-turn snapshot
    stages everything (``git add -A``), so the deletion is committed and the
    lineage stops carrying them after one turn.
    """
    dropped: list[str] = []
    for name in _LIVE_CONTROL_LEFTOVERS:
        path = local_root / name
        try:
            if path.is_file():
                path.unlink()
                dropped.append(name)
        except OSError:
            continue
    return dropped


def _sanitize_cwd_for_claude_projects(cwd: pathlib.Path) -> str:
    resolved = str(pathlib.Path(cwd).resolve())
    return re.sub(r"[^A-Za-z0-9-]", "-", resolved)


def _read_recorded_cwd(project_dir: pathlib.Path) -> str | None:
    for jsonl in sorted(project_dir.glob("*.jsonl")):
        try:
            with jsonl.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cwd_value = record.get("cwd") if isinstance(record, dict) else None
                    if isinstance(cwd_value, str) and cwd_value.strip():
                        return cwd_value.strip()
        except OSError:
            continue
    return None


def _retarget_session_project_dir(
    *,
    local_root: pathlib.Path,
    cwd: pathlib.Path,
    logger: logging.Logger | None = None,
) -> bool:
    projects_dir = local_root / "projects"
    if not projects_dir.is_dir():
        return False
    target_name = _sanitize_cwd_for_claude_projects(cwd)
    target_dir = projects_dir / target_name
    if target_dir.exists():
        return False
    candidates = [child for child in projects_dir.iterdir() if child.is_dir()]
    if len(candidates) != 1:
        return False
    source_dir = candidates[0]
    recorded_cwd = _read_recorded_cwd(source_dir)
    current_resolved = str(pathlib.Path(cwd).resolve())
    if recorded_cwd is not None and recorded_cwd == current_resolved:
        return False
    source_dir.rename(target_dir)
    (logger or logging.getLogger("ClaudeCodeRuntime")).info(
        "[ClaudeCodeRuntime] retargeted session project dir %s -> %s for cwd=%s (recorded=%s)",
        source_dir.name,
        target_dir.name,
        current_resolved,
        recorded_cwd,
    )
    return True


def _is_session_in_use_error(result: ClaudeCodeRunResult | None) -> bool:
    if result is None:
        return False
    text_candidates = [
        str(getattr(result, "error_message", None) or ""),
        str((result.stderr_lines[-1] if getattr(result, "stderr_lines", None) else "") or ""),
        str(getattr(result, "final_text", None) or ""),
    ]
    joined = "\n".join(part for part in text_candidates if part).lower()
    return "session id" in joined and "already in use" in joined


def _is_session_missing_error(result: ClaudeCodeRunResult | None) -> bool:
    if result is None:
        return False
    text_candidates = [
        str(getattr(result, "error_message", None) or ""),
        str((result.stderr_lines[-1] if getattr(result, "stderr_lines", None) else "") or ""),
        str(getattr(result, "final_text", None) or ""),
    ]
    joined = "\n".join(part for part in text_candidates if part).lower()
    return (
        "no conversation found" in joined
        and "session id" in joined
    )


def _bootstrap_claude_code_session_store_sync(
    *,
    config: ClaudeCodeSessionStoreConfig,
    repo_url: str,
    git_env: dict[str, str],
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    log = logger or logging.getLogger("ClaudeCodeRuntime")
    local_root = pathlib.Path(config.local_root)
    local_root.mkdir(parents=True, exist_ok=True)
    repo_root = _ensure_session_repo(config=config, repo_url=repo_url, env=git_env)
    lineage_ref = _ensure_local_lineage_branch_ref(repo_root=repo_root, config=config, env=git_env)
    _ensure_local_git_repo(local_root=local_root, config=config)
    if lineage_ref:
        _clear_session_root(local_root=local_root)
        subprocess.run(
            ["git", "-C", str(local_root), "fetch", "--no-tags", str(repo_root), lineage_ref],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(local_root), "checkout", "-B", "workspace", "FETCH_HEAD"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(local_root), "reset", "--hard", "FETCH_HEAD"],
            check=True,
            capture_output=True,
        )
        action = "checked_out_remote_branch"
    else:
        if not _git_has_head(repo_root=local_root):
            subprocess.run(
                ["git", "-C", str(local_root), "checkout", "--orphan", "workspace"],
                check=True,
                capture_output=True,
            )
            action = "initialized_empty_workspace"
        else:
            action = "reused_local_workspace"
    gitignore_seeded = _ensure_session_gitignore(local_root=local_root)
    dropped = _drop_live_control_leftovers(local_root=local_root)
    log.info(
        "[ClaudeCodeRuntime] bootstrapped session store agent=%s conversation=%s local_root=%s action=%s branch=%s gitignore_seeded=%s",
        config.agent_name,
        config.conversation_id,
        local_root,
        action,
        claude_code_session_branch_ref(config),
        gitignore_seeded,
    )
    if dropped:
        log.info(
            "[ClaudeCodeRuntime] dropped live-control leftovers from the session "
            "checkout agent=%s conversation=%s files=%s",
            config.agent_name, config.conversation_id, dropped,
        )
    return {
        "implementation": config.implementation,
        "local_root": str(local_root),
        "repo_root": str(repo_root),
        "lineage_ref": claude_code_session_branch_ref(config),
        "bootstrapped": bool(lineage_ref),
        "action": action,
        "gitignore_seeded": gitignore_seeded,
    }


async def bootstrap_claude_code_session_store(
    *,
    config: ClaudeCodeSessionStoreConfig,
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    local_root = pathlib.Path(config.local_root)
    if config.implementation != "git":
        await asyncio.to_thread(local_root.mkdir, parents=True, exist_ok=True)
        return {
            "implementation": config.implementation,
            "local_root": str(local_root),
            "bootstrapped": False,
            "reason": "local_session_store",
        }

    raw_repo_url = str(config.git_repo or "").strip()
    if not raw_repo_url:
        raise ValueError("missing_claude_code_session_git_repo")
    normalized_repo_url = await normalize_git_remote_url(raw_repo_url)
    if normalized_repo_url != raw_repo_url:
        logging.getLogger(__name__).info("[claude_code] using HTTPS for %s", raw_repo_url)
    git_env = await build_git_env()
    return await asyncio.to_thread(
        _bootstrap_claude_code_session_store_sync,
        config=config,
        repo_url=normalized_repo_url,
        git_env=git_env,
        logger=logger,
    )


def _publish_claude_code_session_store_sync(
    *,
    config: ClaudeCodeSessionStoreConfig,
    repo_url: str,
    git_env: dict[str, str],
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    log = logger or logging.getLogger("ClaudeCodeRuntime")
    local_root = pathlib.Path(config.local_root)
    local_root.mkdir(parents=True, exist_ok=True)
    repo_root = _ensure_session_repo(config=config, repo_url=repo_url, env=git_env)
    _ensure_local_git_repo(local_root=local_root, config=config)
    _run_local_git_mutation(
        repo_root=local_root,
        args=["add", "-A", "--", "."],
    )
    committed = False
    message = f"Claude session snapshot {config.conversation_id}"
    has_head = _git_has_head(repo_root=local_root)
    if not has_head:
        _run_local_git_mutation(
            repo_root=local_root,
            args=["commit", "--allow-empty", "-m", message],
        )
        committed = True
    elif _git_has_staged_changes(repo_root=local_root):
        _run_local_git_mutation(
            repo_root=local_root,
            args=["commit", "-m", message],
        )
        committed = True

    head_sha = _git_head_sha(repo_root=local_root)
    subprocess.run(
        ["git", "-C", str(local_root), "push", str(repo_root), "HEAD:refs/heads/workspace"],
        check=True,
        capture_output=True,
    )
    lineage_ref = claude_code_session_branch_ref(config)
    subprocess.run(
        ["git", "-C", str(repo_root), "push", "origin", f"refs/heads/workspace:{lineage_ref}"],
        check=True,
        capture_output=True,
        env=git_env,
    )
    log.info(
        "[ClaudeCodeRuntime] published session store agent=%s conversation=%s local_root=%s branch=%s commit=%s committed=%s",
        config.agent_name,
        config.conversation_id,
        local_root,
        lineage_ref,
        head_sha,
        committed,
    )
    return {
        "implementation": config.implementation,
        "local_root": str(local_root),
        "repo_root": str(repo_root),
        "lineage_ref": lineage_ref,
        "commit_sha": head_sha,
        "committed": committed,
        "published": True,
    }


async def publish_claude_code_session_store(
    *,
    config: ClaudeCodeSessionStoreConfig,
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    local_root = pathlib.Path(config.local_root)
    if config.implementation != "git":
        await asyncio.to_thread(local_root.mkdir, parents=True, exist_ok=True)
        return {
            "implementation": config.implementation,
            "local_root": str(local_root),
            "published": False,
            "reason": "local_session_store",
        }

    raw_repo_url = str(config.git_repo or "").strip()
    if not raw_repo_url:
        raise ValueError("missing_claude_code_session_git_repo")
    normalized_repo_url = await normalize_git_remote_url(raw_repo_url)
    if normalized_repo_url != raw_repo_url:
        logging.getLogger(__name__).info("[claude_code] using HTTPS for %s", raw_repo_url)
    git_env = await build_git_env()
    return await asyncio.to_thread(
        _publish_claude_code_session_store_sync,
        config=config,
        repo_url=normalized_repo_url,
        git_env=git_env,
        logger=logger,
    )


async def run_claude_code_turn(
    *,
    agent: ClaudeCodeAgent,
    prompt: str,
    kind: ClaudeCodeTurnKind = "regular",
    resume_existing: bool = False,
    session_store: ClaudeCodeSessionStoreConfig | None = None,
    refresh_support_files: Callable[[], None] | None = None,
    logger: logging.Logger | None = None,
) -> ClaudeCodeRunResult:
    should_bootstrap = bool(
        session_store
        and session_store.implementation == "git"
        and kind in set(session_store.bootstrap_turn_kinds)
    )
    should_publish = bool(
        session_store
        and session_store.implementation == "git"
        and kind in set(session_store.publish_turn_kinds)
    )
    effective_resume_existing = bool(resume_existing)

    # Point the Claude Code CLI at the session-store's local_root so the
    # session JSONL it writes (under <CLAUDE_CONFIG_DIR>/projects/...) lands
    # in the same directory that publish_claude_code_session_store snapshots
    # to git. Without this the CLI writes JSONLs to $HOME/.claude/projects/...,
    # local_root stays empty, and publish creates empty lineage branches.
    if session_store is not None and session_store.implementation == "git":
        agent_config = getattr(agent, "config", None)
        agent_env = getattr(agent_config, "env", None) if agent_config is not None else None
        if isinstance(agent_env, dict) and "CLAUDE_CONFIG_DIR" not in agent_env:
            agent_env["CLAUDE_CONFIG_DIR"] = str(session_store.local_root)

    workspace_cwd: pathlib.Path | None = None
    if session_store is not None and session_store.implementation == "git":
        agent_workspace = getattr(getattr(agent, "config", None), "workspace_path", None)
        if agent_workspace is not None:
            workspace_cwd = pathlib.Path(agent_workspace)

    if should_bootstrap and session_store is not None:
        bootstrap_result = await bootstrap_claude_code_session_store(
            config=session_store,
            logger=logger,
        )
        # In git-backed mode, the restored lineage is the only durable signal
        # that a previous Claude session can be meaningfully resumed. A stale
        # in-memory/state flag alone is not enough, especially after storage or
        # session-store repo changes.
        effective_resume_existing = bool(bootstrap_result.get("bootstrapped"))
        # Lineage from another node will have projects/<sanitized-old-cwd>/ —
        # rename it to projects/<sanitized-current-cwd>/ so `--resume` finds the
        # JSONL under the current cwd. Without this, cross-node resume silently
        # falls back to a fresh session.
        if workspace_cwd is not None:
            await asyncio.to_thread(
                _retarget_session_project_dir,
                local_root=pathlib.Path(session_store.local_root),
                cwd=workspace_cwd,
                logger=logger,
            )
        if session_store.implementation == "git":
            agent_config = getattr(agent, "config", None)
            turn_env = getattr(agent_config, "env", None)
            await asyncio.to_thread(
                _materialize_cli_credentials,
                local_root=pathlib.Path(session_store.local_root),
                env=turn_env if isinstance(turn_env, dict) else {},
                logger=logger,
            )
        if refresh_support_files is not None:
            refresh_support_files()

    try:
        result = await agent.run_turn(
            prompt,
            kind=kind,
            resume_existing=effective_resume_existing,
        )
        if (
            result.status == "failed"
            and should_bootstrap
            and session_store is not None
            and session_store.implementation == "git"
            and _is_session_in_use_error(result)
        ):
            log = logger or logging.getLogger("ClaudeCodeRuntime")
            log.warning(
                "[ClaudeCodeRuntime] detected stale session checkout for agent=%s conversation=%s local_root=%s; "
                "resetting local checkout and retrying in resume mode",
                session_store.agent_name,
                session_store.conversation_id,
                session_store.local_root,
            )
            await asyncio.to_thread(
                _reset_local_session_checkout,
                local_root=pathlib.Path(session_store.local_root),
            )
            retry_bootstrap = await bootstrap_claude_code_session_store(
                config=session_store,
                logger=logger,
            )
            if workspace_cwd is not None:
                await asyncio.to_thread(
                    _retarget_session_project_dir,
                    local_root=pathlib.Path(session_store.local_root),
                    cwd=workspace_cwd,
                    logger=logger,
                )
            if refresh_support_files is not None:
                refresh_support_files()
            result = await agent.run_turn(
                prompt,
                kind=kind,
                resume_existing=bool(retry_bootstrap.get("bootstrapped")) or True,
            )
        elif (
            result.status == "failed"
            and should_bootstrap
            and session_store is not None
            and session_store.implementation == "git"
            and effective_resume_existing
            and _is_session_missing_error(result)
        ):
            log = logger or logging.getLogger("ClaudeCodeRuntime")
            log.warning(
                "[ClaudeCodeRuntime] detected missing remote Claude session for agent=%s conversation=%s local_root=%s; "
                "retrying without resume on the bootstrapped workspace",
                session_store.agent_name,
                session_store.conversation_id,
                session_store.local_root,
            )
            result = await agent.run_turn(
                prompt,
                kind=kind,
                resume_existing=False,
            )
        return result
    finally:
        if should_publish and session_store is not None:
            await publish_claude_code_session_store(
                config=session_store,
                logger=logger,
            )
