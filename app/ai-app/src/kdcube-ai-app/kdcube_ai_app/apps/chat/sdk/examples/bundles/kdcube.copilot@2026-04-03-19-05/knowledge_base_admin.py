# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage


ROOT_KEY = "knowledge_base_admin"
AGENT_NAME = "knowledge-base-admin"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_user_id(user_id: str | None) -> str:
    raw = str(user_id or "anonymous").strip() or "anonymous"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw)


def _slug(value: str | None, *, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    safe = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
    return safe or fallback


def _repo_name_from_url(url: str | None) -> str:
    text = str(url or "").rstrip("/")
    if not text:
        return "repo"
    if text.startswith("git@") and ":" in text:
        text = text.split(":", 1)[1]
    name = text.split("/")[-1] or "repo"
    return name[:-4] if name.endswith(".git") else name


def _default_config() -> dict[str, Any]:
    return {
        "content_repos": [],
        "output_repo": {},
        "last_sync": None,
        "updated_at": None,
    }


def build_kb_admin_storage(
    *,
    tenant: str,
    project: str,
    bundle_id: str,
    storage_uri: str | None = None,
) -> AIBundleStorage:
    return AIBundleStorage(
        tenant=str(tenant or "unknown"),
        project=str(project or "unknown"),
        ai_bundle_id=str(bundle_id or "kdcube.copilot"),
        storage_uri=storage_uri,
    )


def _user_root(user_id: str | None) -> str:
    return f"{ROOT_KEY}/users/{_safe_user_id(user_id)}"


def _config_key(user_id: str | None) -> str:
    return f"{_user_root(user_id)}/config.json"


def _conversations_index_key(user_id: str | None) -> str:
    return f"{_user_root(user_id)}/conversations/index.json"


def _conversation_key(user_id: str | None, conversation_id: str) -> str:
    return f"{_user_root(user_id)}/conversations/{conversation_id}.json"


def _load_json(storage: AIBundleStorage, key: str, default: Any) -> Any:
    try:
        if not storage.exists(key):
            return default
        return json.loads(str(storage.read(key, as_text=True)))
    except Exception:
        return default


def _write_json(storage: AIBundleStorage, key: str, data: Any) -> None:
    storage.write(
        key,
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        mime="application/json",
    )


def _normalize_repo_item(item: Mapping[str, Any] | None, *, slot: str) -> dict[str, Any]:
    source = str((item or {}).get("source") or "").strip()
    branch = str((item or {}).get("branch") or "").strip()
    label = str((item or {}).get("label") or "").strip()
    repo_id = str((item or {}).get("id") or "").strip()
    if not repo_id:
        base = _slug(_repo_name_from_url(source), fallback=slot)
        repo_id = f"{slot}-{base}"
    return {
        "id": repo_id,
        "slot": slot,
        "label": label or _repo_name_from_url(source) or slot,
        "source": source,
        "branch": branch,
    }


def normalize_config(data: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(_default_config())
    raw.update(dict(data or {}))

    content_repos: list[dict[str, Any]] = []
    for idx, item in enumerate(list(raw.get("content_repos") or [])[:3], start=1):
        normalized = _normalize_repo_item(item, slot=f"content-{idx}")
        if normalized["source"]:
            content_repos.append(normalized)

    output_repo = _normalize_repo_item(raw.get("output_repo") or {}, slot="output")
    if not output_repo["source"]:
        output_repo = {}

    return {
        "content_repos": content_repos,
        "output_repo": output_repo,
        "last_sync": raw.get("last_sync"),
        "updated_at": raw.get("updated_at"),
    }


def validate_workspace_config(data: Mapping[str, Any] | None) -> dict[str, Any]:
    config = normalize_config(data)
    content_repos = list(config.get("content_repos") or [])
    output_repo = dict(config.get("output_repo") or {})
    if not content_repos:
        raise ValueError("Configure at least one source repository before syncing or chatting.")
    if not str(output_repo.get("source") or "").strip():
        raise ValueError("Configure the output repository before syncing or chatting.")
    return config


def load_user_config(storage: AIBundleStorage, user_id: str | None) -> dict[str, Any]:
    return normalize_config(_load_json(storage, _config_key(user_id), _default_config()))


def save_user_config(
    storage: AIBundleStorage,
    user_id: str | None,
    *,
    content_repos: Iterable[Mapping[str, Any]] | None,
    output_repo: Mapping[str, Any] | None,
    last_sync: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    config = normalize_config(
        {
            "content_repos": list(content_repos or []),
            "output_repo": dict(output_repo or {}),
            "last_sync": dict(last_sync or {}) if last_sync else None,
            "updated_at": _utc_now(),
        }
    )
    _write_json(storage, _config_key(user_id), config)
    return config


def update_last_sync(
    storage: AIBundleStorage,
    user_id: str | None,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    config = load_user_config(storage, user_id)
    config["last_sync"] = dict(payload or {})
    config["updated_at"] = _utc_now()
    _write_json(storage, _config_key(user_id), config)
    return config


def list_conversations(storage: AIBundleStorage, user_id: str | None) -> list[dict[str, Any]]:
    data = _load_json(storage, _conversations_index_key(user_id), [])
    if not isinstance(data, list):
        return []
    items = [item for item in data if isinstance(item, dict) and item.get("conversation_id")]
    items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return items


def load_conversation(
    storage: AIBundleStorage,
    user_id: str | None,
    conversation_id: str,
) -> dict[str, Any] | None:
    cid = str(conversation_id or "").strip()
    if not cid:
        return None
    data = _load_json(storage, _conversation_key(user_id, cid), None)
    return data if isinstance(data, dict) else None


def save_conversation(
    storage: AIBundleStorage,
    user_id: str | None,
    conversation: Mapping[str, Any],
) -> dict[str, Any]:
    cid = str(conversation.get("conversation_id") or "").strip()
    if not cid:
        raise ValueError("conversation_id is required")

    messages = list(conversation.get("messages") or [])
    title = str(conversation.get("title") or "").strip()
    if not title:
        for message in messages:
            if str(message.get("role") or "") == "user":
                title = str(message.get("text") or "").strip()[:80]
                break
    title = title or "New conversation"

    created_at = str(conversation.get("created_at") or _utc_now())
    updated_at = str(conversation.get("updated_at") or _utc_now())
    document = {
        "conversation_id": cid,
        "title": title,
        "agent_name": str(conversation.get("agent_name") or AGENT_NAME),
        "created_at": created_at,
        "updated_at": updated_at,
        "messages": messages,
    }
    _write_json(storage, _conversation_key(user_id, cid), document)

    summaries = [item for item in list_conversations(storage, user_id) if item.get("conversation_id") != cid]
    summaries.append(
        {
            "conversation_id": cid,
            "title": title,
            "updated_at": updated_at,
            "created_at": created_at,
            "message_count": len(messages),
            "last_role": messages[-1].get("role") if messages else None,
            "last_preview": str(messages[-1].get("text") or "")[:120] if messages else "",
        }
    )
    summaries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    _write_json(storage, _conversations_index_key(user_id), summaries)
    return document


def create_or_load_conversation(
    storage: AIBundleStorage,
    user_id: str | None,
    *,
    conversation_id: str | None = None,
    title_hint: str | None = None,
) -> dict[str, Any]:
    cid = str(conversation_id or "").strip() or f"kb_admin_{uuid.uuid4().hex[:12]}"
    existing = load_conversation(storage, user_id, cid)
    if existing:
        return existing
    now = _utc_now()
    return save_conversation(
        storage,
        user_id,
        {
            "conversation_id": cid,
            "title": (title_hint or "").strip()[:80] or "New conversation",
            "agent_name": AGENT_NAME,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        },
    )


def append_conversation_message(
    storage: AIBundleStorage,
    user_id: str | None,
    *,
    conversation_id: str,
    role: str,
    text: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    conversation = create_or_load_conversation(storage, user_id, conversation_id=conversation_id)
    messages = list(conversation.get("messages") or [])
    messages.append(
        {
            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
            "role": str(role or "assistant"),
            "text": str(text or ""),
            "created_at": _utc_now(),
            "metadata": dict(metadata or {}),
        }
    )
    conversation["messages"] = messages
    conversation["updated_at"] = _utc_now()
    return save_conversation(storage, user_id, conversation)


def build_widget_payload(
    storage: AIBundleStorage,
    user_id: str | None,
    *,
    has_git_pat: bool,
    has_anthropic_api_key: bool,
    has_claude_code_key: bool,
    selected_conversation_id: str | None = None,
) -> dict[str, Any]:
    config = load_user_config(storage, user_id)
    conversations = list_conversations(storage, user_id)
    current_id = str(selected_conversation_id or "").strip() or (
        conversations[0]["conversation_id"] if conversations else ""
    )
    current = load_conversation(storage, user_id, current_id) if current_id else None
    return {
        "user_id": user_id or "anonymous",
        "config": config,
        "secrets": {
            "has_git_pat": bool(has_git_pat),
            "has_anthropic_api_key": bool(has_anthropic_api_key),
            "has_claude_code_key": bool(has_claude_code_key),
        },
        "conversations": conversations,
        "selected_conversation_id": current_id or None,
        "current_conversation": current,
    }


@dataclass(frozen=True)
class ManagedRepo:
    repo_type: str
    slot: str
    label: str
    source: str
    branch: str
    local_path: Path
    repo_id: str


def workspace_root(local_root: Path, user_id: str | None) -> Path:
    return Path(local_root) / ROOT_KEY / "users" / _safe_user_id(user_id) / "workspace"


def _repos_root(local_root: Path, user_id: str | None) -> Path:
    return workspace_root(local_root, user_id) / "repos"


def _agent_dir(local_root: Path, user_id: str | None) -> Path:
    return workspace_root(local_root, user_id) / ".claude" / "agents"


def managed_repos_from_config(local_root: Path, user_id: str | None, config: Mapping[str, Any]) -> list[ManagedRepo]:
    repos: list[ManagedRepo] = []
    repos_root = _repos_root(local_root, user_id)
    for idx, item in enumerate(config.get("content_repos") or [], start=1):
        source = str(item.get("source") or "").strip()
        if not source:
            continue
        slot = f"content-{idx}"
        label = str(item.get("label") or _repo_name_from_url(source) or slot)
        repos.append(
            ManagedRepo(
                repo_type="content",
                slot=slot,
                label=label,
                source=source,
                branch=str(item.get("branch") or "").strip(),
                local_path=repos_root / slot,
                repo_id=str(item.get("id") or slot),
            )
        )
    output = dict(config.get("output_repo") or {})
    if str(output.get("source") or "").strip():
        source = str(output.get("source") or "").strip()
        repos.append(
            ManagedRepo(
                repo_type="output",
                slot="output",
                label=str(output.get("label") or _repo_name_from_url(source) or "output"),
                source=source,
                branch=str(output.get("branch") or "").strip(),
                local_path=repos_root / "output",
                repo_id=str(output.get("id") or "output"),
            )
        )
    return repos


def _build_git_env(*, git_http_token: str | None, git_http_user: str | None) -> dict[str, str]:
    env = dict(os.environ)
    token = str(git_http_token or "").strip()
    user = str(git_http_user or "").strip() or "x-access-token"
    if token:
        askpass_path = Path("/tmp/kdcube_kb_admin_git_askpass.sh")
        contents = (
            "#!/bin/sh\n"
            "prompt=\"$1\"\n"
            "if echo \"$prompt\" | grep -qi \"username\"; then\n"
            "  echo \"${GIT_HTTP_USER:-x-access-token}\"\n"
            "else\n"
            "  echo \"${GIT_HTTP_TOKEN}\"\n"
            "fi\n"
        )
        if not askpass_path.exists() or askpass_path.read_text(encoding="utf-8") != contents:
            askpass_path.write_text(contents, encoding="utf-8")
            askpass_path.chmod(0o700)
        env["GIT_HTTP_TOKEN"] = token
        env["GIT_HTTP_USER"] = user
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = str(askpass_path)
        env["GIT_ASKPASS_REQUIRE"] = "force"
    return env


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            env=dict(env or {}),
            capture_output=True,
            text=True,
            check=True,
        )
        return (proc.stdout or "").strip()
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}") from exc


def _git_status(local_path: Path, *, env: Mapping[str, str]) -> str:
    return _run_git(["status", "--porcelain"], cwd=local_path, env=env)


def _git_current_branch(local_path: Path, *, env: Mapping[str, str]) -> str:
    try:
        return _run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=local_path, env=env)
    except Exception:
        return ""


def _git_head(local_path: Path, *, env: Mapping[str, str]) -> str:
    try:
        return _run_git(["rev-parse", "HEAD"], cwd=local_path, env=env)
    except Exception:
        return ""


def _git_remote_branch_exists(source: str, branch: str, *, env: Mapping[str, str]) -> bool:
    try:
        raw = _run_git(["ls-remote", "--heads", source, branch], env=env)
    except Exception:
        return False
    return bool(str(raw or "").strip())


def _git_local_branch_exists(local_path: Path, branch: str, *, env: Mapping[str, str]) -> bool:
    try:
        _run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=local_path, env=env)
        return True
    except Exception:
        return False


def _prepare_repo(
    repo: ManagedRepo,
    *,
    env: Mapping[str, str],
    sync_existing: bool,
) -> dict[str, Any]:
    if env.get("GIT_HTTP_TOKEN") and repo.source.startswith("git@"):
        raise RuntimeError(
            f"Repo '{repo.label}' uses SSH URL '{repo.source}'. PAT auth only works with https:// remotes."
        )

    repo.local_path.parent.mkdir(parents=True, exist_ok=True)
    if repo.local_path.exists():
        remote_url = ""
        try:
            remote_url = _run_git(["config", "--get", "remote.origin.url"], cwd=repo.local_path, env=env)
        except Exception:
            remote_url = ""
        if remote_url and remote_url != repo.source:
            shutil.rmtree(repo.local_path, ignore_errors=True)

    if not (repo.local_path / ".git").exists():
        if repo.branch and repo.repo_type == "output" and not _git_remote_branch_exists(repo.source, repo.branch, env=env):
            _run_git(["clone", repo.source, str(repo.local_path)], env=env)
            _run_git(["checkout", "-b", repo.branch], cwd=repo.local_path, env=env)
            action = "cloned-local-branch"
        else:
            if repo.branch and not _git_remote_branch_exists(repo.source, repo.branch, env=env):
                raise RuntimeError(
                    f"Repo '{repo.label}' is configured for branch '{repo.branch}', but that branch does not exist on remote."
                )
            clone_args = ["clone"]
            if repo.branch:
                clone_args += ["--branch", repo.branch, "--single-branch"]
            clone_args += [repo.source, str(repo.local_path)]
            _run_git(clone_args, env=env)
            action = "cloned"
    else:
        action = "present"
        if sync_existing:
            dirty = bool(_git_status(repo.local_path, env=env).strip())
            _run_git(["fetch", "--prune", "origin"], cwd=repo.local_path, env=env)
            if repo.branch:
                if _git_local_branch_exists(repo.local_path, repo.branch, env=env):
                    _run_git(["checkout", repo.branch], cwd=repo.local_path, env=env)
                elif repo.repo_type == "output" and not _git_remote_branch_exists(repo.source, repo.branch, env=env):
                    _run_git(["checkout", "-b", repo.branch], cwd=repo.local_path, env=env)
                    action = "created-local-branch"
                elif _git_remote_branch_exists(repo.source, repo.branch, env=env):
                    _run_git(["checkout", "-B", repo.branch, f"origin/{repo.branch}"], cwd=repo.local_path, env=env)
                else:
                    raise RuntimeError(
                        f"Repo '{repo.label}' is configured for branch '{repo.branch}', but that branch does not exist on remote."
                    )
                if not dirty:
                    try:
                        _run_git(["pull", "--ff-only", "origin", repo.branch], cwd=repo.local_path, env=env)
                        action = "updated"
                    except RuntimeError:
                        action = "fetched"
                else:
                    action = "dirty"
            elif not dirty:
                action = "fetched"
            else:
                action = "dirty"

    return _repo_status_payload(repo, env=env, action=action)


def _repo_status_payload(
    repo: ManagedRepo,
    *,
    env: Mapping[str, str],
    action: str,
) -> dict[str, Any]:
    return {
        "repo_type": repo.repo_type,
        "slot": repo.slot,
        "repo_id": repo.repo_id,
        "label": repo.label,
        "source": repo.source,
        "branch": repo.branch,
        "local_path": str(repo.local_path),
        "current_branch": _git_current_branch(repo.local_path, env=env),
        "head": _git_head(repo.local_path, env=env),
        "dirty": bool(_git_status(repo.local_path, env=env).strip()),
        "action": action,
    }


def _resolve_output_repo(local_root: Path, user_id: str | None, config: Mapping[str, Any]) -> ManagedRepo:
    config = validate_workspace_config(config)
    repos = managed_repos_from_config(local_root, user_id, config)
    for repo in repos:
        if repo.repo_type == "output":
            return repo
    raise ValueError("Output repository is not configured.")


def push_output_repo(
    *,
    local_root: Path,
    user_id: str | None,
    config: Mapping[str, Any],
    git_http_token: str | None,
    git_http_user: str | None,
) -> dict[str, Any]:
    env = _build_git_env(git_http_token=git_http_token, git_http_user=git_http_user)
    repo = _resolve_output_repo(local_root, user_id, config)
    _prepare_repo(repo, env=env, sync_existing=False)
    if bool(_git_status(repo.local_path, env=env).strip()):
        raise RuntimeError("Output repo has uncommitted changes. Commit them or reset the branch before pushing.")
    branch = _git_current_branch(repo.local_path, env=env) or str(repo.branch or "").strip()
    if not branch:
        raise RuntimeError("Output repo does not have an active branch to push.")
    _run_git(["push", "-u", "origin", branch], cwd=repo.local_path, env=env)
    return _repo_status_payload(repo, env=env, action="pushed")


def reset_output_repo(
    *,
    local_root: Path,
    user_id: str | None,
    config: Mapping[str, Any],
    commit: str,
    git_http_token: str | None,
    git_http_user: str | None,
) -> dict[str, Any]:
    target = str(commit or "").strip()
    if not target:
        raise ValueError("commit is required")
    env = _build_git_env(git_http_token=git_http_token, git_http_user=git_http_user)
    repo = _resolve_output_repo(local_root, user_id, config)
    _prepare_repo(repo, env=env, sync_existing=False)
    _run_git(["fetch", "--prune", "origin"], cwd=repo.local_path, env=env)
    _run_git(["reset", "--hard", target], cwd=repo.local_path, env=env)
    return _repo_status_payload(repo, env=env, action="reset")


def write_workspace_context(
    *,
    local_root: Path,
    user_id: str | None,
    config: Mapping[str, Any],
    repo_statuses: list[Mapping[str, Any]],
) -> dict[str, Any]:
    root = workspace_root(local_root, user_id)
    root.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": _utc_now(),
        "agent_name": AGENT_NAME,
        "content_repos": [item for item in repo_statuses if item.get("repo_type") == "content"],
        "output_repo": next((item for item in repo_statuses if item.get("repo_type") == "output"), None),
        "config": normalize_config(config),
    }

    support_dir = root / ".kdcube"
    support_dir.mkdir(parents=True, exist_ok=True)
    (support_dir / "knowledge-base-admin-workspace.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (support_dir / "knowledge-base-admin-workspace.md").write_text(
        build_workspace_prompt_context(payload),
        encoding="utf-8",
    )

    agent_dir = _agent_dir(local_root, user_id)
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / f"{AGENT_NAME}.md").write_text(
        (
            "# Knowledge Base Admin\n\n"
            "You help build and maintain a documentation knowledge base from multiple source repositories.\n\n"
            "Rules:\n"
            "- Read the workspace map from `.kdcube/knowledge-base-admin-workspace.json`.\n"
            "- Use content repos as sources.\n"
            "- Write generated wiki or knowledge base artifacts only into the output repo unless explicitly told otherwise.\n"
            "- Prefer incremental, reviewable changes.\n"
            "- Explain important decisions in the response.\n"
        ),
        encoding="utf-8",
    )

    return payload


def build_workspace_prompt_context(workspace_payload: Mapping[str, Any]) -> str:
    lines = [
        "You are operating in the Knowledge Base Admin workspace.",
        "Use the workspace map in `.kdcube/knowledge-base-admin-workspace.json`.",
        "Write generated wiki and knowledge-base outputs only into the output repo unless explicitly instructed otherwise.",
        "",
        "Workspace repositories:",
    ]
    for item in workspace_payload.get("content_repos") or []:
        lines.append(
            f"- content repo {item.get('slot')}: {item.get('label')} at {item.get('local_path')} "
            f"(branch={item.get('current_branch') or item.get('branch') or 'default'})"
        )
    output_repo = workspace_payload.get("output_repo") or {}
    if output_repo:
        lines.append(
            f"- output repo: {output_repo.get('label')} at {output_repo.get('local_path')} "
            f"(branch={output_repo.get('current_branch') or output_repo.get('branch') or 'default'})"
        )
    lines.extend(
        [
            "",
            "When you reference files, use the actual workspace paths.",
            "If you need web information, you may use WebSearch and WebFetch.",
            "",
            "User request:",
        ]
    )
    return "\n".join(lines)


def ensure_workspace(
    *,
    local_root: Path,
    user_id: str | None,
    config: Mapping[str, Any],
    git_http_token: str | None,
    git_http_user: str | None,
    sync_existing: bool,
) -> dict[str, Any]:
    config = validate_workspace_config(config)
    root = workspace_root(local_root, user_id)
    root.mkdir(parents=True, exist_ok=True)
    env = _build_git_env(git_http_token=git_http_token, git_http_user=git_http_user)
    repos = managed_repos_from_config(local_root, user_id, config)
    statuses = [_prepare_repo(repo, env=env, sync_existing=sync_existing) for repo in repos]
    workspace_payload = write_workspace_context(
        local_root=local_root,
        user_id=user_id,
        config=config,
        repo_statuses=statuses,
    )
    return {
        "workspace_root": str(root),
        "workspace_payload": workspace_payload,
        "repo_statuses": statuses,
    }
