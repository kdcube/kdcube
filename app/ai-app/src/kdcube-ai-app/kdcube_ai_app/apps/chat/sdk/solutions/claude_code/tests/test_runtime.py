from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import kdcube_ai_app.apps.chat.sdk.solutions.claude_code.runtime as runtime_module
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.runtime import (
    ClaudeCodeSessionStoreConfig,
    bootstrap_claude_code_session_store,
    claude_code_session_branch_ref,
    publish_claude_code_session_store,
    run_claude_code_turn,
)
from kdcube_ai_app.apps.chat.sdk.solutions.claude_code.types import ClaudeCodeRunResult


def _init_git_repo(path: Path, *, branch: str = "main", files: dict[str, str] | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--initial-branch", branch], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True, text=True)
    for rel_path, content in dict(files or {"README.md": "# Test\n"}).items():
        target = path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)
    return path


def _init_bare_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", str(path)], check=True, capture_output=True, text=True)
    return path


def _push_branch(source_repo: Path, remote_repo: Path, branch_ref: str) -> None:
    subprocess.run(
        ["git", "-C", str(source_repo), "push", str(remote_repo), f"HEAD:{branch_ref}"],
        check=True,
        capture_output=True,
        text=True,
    )


def _read_remote_file(remote_repo: Path, branch_ref: str, rel_path: str) -> str:
    proc = subprocess.run(
        ["git", "--git-dir", str(remote_repo), "show", f"{branch_ref}:{rel_path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def _config(tmp_path: Path, *, git_repo: Path, local_root: Path | None = None) -> ClaudeCodeSessionStoreConfig:
    return ClaudeCodeSessionStoreConfig(
        implementation="git",
        local_root=local_root or (tmp_path / "workspace" / ".claude"),
        tenant="home",
        project="demo",
        user_id="alice",
        conversation_id="conv-1",
        agent_name="knowledge-base-admin",
        git_repo=str(git_repo),
    )


@pytest.mark.asyncio
async def test_bootstrap_materializes_remote_session_branch(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    seed_repo = _init_git_repo(
        tmp_path / "seed",
        files={
            "agents/knowledge-base-admin.md": "# Agent\n",
            "sessions/history.json": "{\"turns\": 1}\n",
        },
    )
    config = _config(tmp_path, git_repo=remote_repo)
    branch_ref = claude_code_session_branch_ref(config)
    _push_branch(seed_repo, remote_repo, branch_ref)

    result = await bootstrap_claude_code_session_store(config=config)

    assert result["bootstrapped"] is True
    assert (config.local_root / "sessions" / "history.json").read_text(encoding="utf-8") == "{\"turns\": 1}\n"
    remote_url = subprocess.run(
        ["git", "-C", str(config.local_root), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
    )
    assert remote_url.returncode != 0


@pytest.mark.asyncio
async def test_bootstrap_rerun_reuses_checked_out_workspace_branch(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    seed_repo = _init_git_repo(
        tmp_path / "seed",
        files={
            "agents/knowledge-base-admin.md": "# Agent\n",
            "sessions/history.json": "{\"turns\": 1}\n",
        },
    )
    config = _config(tmp_path, git_repo=remote_repo)
    branch_ref = claude_code_session_branch_ref(config)
    _push_branch(seed_repo, remote_repo, branch_ref)

    first = await bootstrap_claude_code_session_store(config=config)
    assert first["bootstrapped"] is True
    assert (config.local_root / "sessions" / "history.json").read_text(encoding="utf-8") == "{\"turns\": 1}\n"

    (config.local_root / "sessions" / "history.json").write_text("{\"turns\": 2}\n", encoding="utf-8")
    publish = await publish_claude_code_session_store(config=config)
    assert publish["published"] is True

    second = await bootstrap_claude_code_session_store(config=config)
    assert second["bootstrapped"] is True
    assert (config.local_root / "sessions" / "history.json").read_text(encoding="utf-8") == "{\"turns\": 2}\n"


@pytest.mark.asyncio
async def test_publish_pushes_session_root_to_conversation_branch(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)

    await bootstrap_claude_code_session_store(config=config)
    config.local_root.mkdir(parents=True, exist_ok=True)
    (config.local_root / "sessions").mkdir(parents=True, exist_ok=True)
    (config.local_root / "sessions" / "history.json").write_text("{\"turns\": 2}\n", encoding="utf-8")

    result = await publish_claude_code_session_store(config=config)

    assert result["published"] is True
    assert _read_remote_file(remote_repo, claude_code_session_branch_ref(config), "sessions/history.json") == "{\"turns\": 2}\n"


def test_ensure_session_repo_uses_resolved_https_origin(tmp_path: Path):
    config = ClaudeCodeSessionStoreConfig(
        implementation="git",
        local_root=tmp_path / "workspace" / ".claude",
        tenant="home",
        project="demo",
        user_id="alice",
        conversation_id="conv-1",
        agent_name="knowledge-base-admin",
        git_repo="git@github.com:org/session-store.git",
    )
    repo_root = runtime_module._ensure_session_repo(
        config=config,
        repo_url="https://github.com/org/session-store.git",
        env={},
    )

    remote_url = subprocess.run(
        ["git", "-C", str(repo_root), "config", "--get", "remote.origin.url"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_url == "https://github.com/org/session-store.git"


class _FakeAgent:
    def __init__(self, root: Path):
        self.root = root
        self.calls: list[dict[str, object]] = []

    async def run_turn(self, prompt: str, *, kind: str = "regular", resume_existing: bool = False) -> ClaudeCodeRunResult:
        self.calls.append({"prompt": prompt, "kind": kind, "resume_existing": resume_existing})
        target = self.root / "sessions" / "history.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{\"turns\": 3}\n", encoding="utf-8")
        return ClaudeCodeRunResult(
            status="completed",
            session_id="claude-session-1",
            final_text="Done",
            delta_count=1,
            exit_code=0,
            stderr_lines=[],
            raw_output_lines=[],
            turn_kind=kind,
            agent_name="knowledge-base-admin",
            provider="anthropic",
            requested_model="default",
            model="claude-sonnet-4-6",
            usage={"input_tokens": 10, "output_tokens": 20, "requests": 1},
            cost_usd=0.01,
            duration_ms=10,
            api_duration_ms=5,
        )


@pytest.mark.asyncio
async def test_run_claude_code_turn_bootstraps_and_publishes_regular_turns(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)
    agent = _FakeAgent(config.local_root)
    refresh_calls: list[str] = []

    result = await run_claude_code_turn(
        agent=agent,  # type: ignore[arg-type]
        prompt="hello",
        kind="regular",
        resume_existing=False,
        session_store=config,
        refresh_support_files=lambda: refresh_calls.append("refresh"),
    )

    assert result.status == "completed"
    assert refresh_calls == ["refresh"]
    assert agent.calls == [{"prompt": "hello", "kind": "regular", "resume_existing": False}]
    assert _read_remote_file(remote_repo, claude_code_session_branch_ref(config), "sessions/history.json") == "{\"turns\": 3}\n"


@pytest.mark.asyncio
async def test_run_claude_code_turn_resumes_regular_turn_when_bootstrap_found_existing_lineage(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)
    branch_ref = claude_code_session_branch_ref(config)

    seed_repo = _init_git_repo(
        tmp_path / "seed-existing-lineage",
        files={
            "sessions/history.json": "{\"turns\": 1}\n",
        },
    )
    _push_branch(seed_repo, remote_repo, branch_ref)

    agent = _FakeAgent(config.local_root)

    result = await run_claude_code_turn(
        agent=agent,  # type: ignore[arg-type]
        prompt="hello again",
        kind="regular",
        resume_existing=False,
        session_store=config,
    )

    assert result.status == "completed"
    assert agent.calls == [{"prompt": "hello again", "kind": "regular", "resume_existing": True}]


@pytest.mark.asyncio
async def test_run_claude_code_turn_does_not_resume_when_git_bootstrap_found_no_lineage(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)
    agent = _FakeAgent(config.local_root)

    result = await run_claude_code_turn(
        agent=agent,  # type: ignore[arg-type]
        prompt="fresh start",
        kind="regular",
        resume_existing=True,
        session_store=config,
    )

    assert result.status == "completed"
    assert agent.calls == [{"prompt": "fresh start", "kind": "regular", "resume_existing": False}]


class _RetryingFakeAgent:
    def __init__(self, root: Path):
        self.root = root
        self.calls: list[dict[str, object]] = []

    async def run_turn(self, prompt: str, *, kind: str = "regular", resume_existing: bool = False) -> ClaudeCodeRunResult:
        self.calls.append({"prompt": prompt, "kind": kind, "resume_existing": resume_existing})
        if len(self.calls) == 1:
            return ClaudeCodeRunResult(
                status="failed",
                session_id="claude-session-1",
                final_text="",
                delta_count=0,
                exit_code=1,
                stderr_lines=["Error: Session ID 123 is already in use."],
                raw_output_lines=[],
                turn_kind=kind,
                agent_name="knowledge-base-admin",
                provider="anthropic",
                requested_model="default",
                model="claude-sonnet-4-6",
                usage={},
                cost_usd=None,
                duration_ms=10,
                api_duration_ms=5,
                error_message="Error: Session ID 123 is already in use.",
            )
        target = self.root / "sessions" / "history.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{\"turns\": 4}\n", encoding="utf-8")
        return ClaudeCodeRunResult(
            status="completed",
            session_id="claude-session-1",
            final_text="Done",
            delta_count=1,
            exit_code=0,
            stderr_lines=[],
            raw_output_lines=[],
            turn_kind=kind,
            agent_name="knowledge-base-admin",
            provider="anthropic",
            requested_model="default",
            model="claude-sonnet-4-6",
            usage={"input_tokens": 10, "output_tokens": 20, "requests": 1},
            cost_usd=0.01,
            duration_ms=10,
            api_duration_ms=5,
        )


@pytest.mark.asyncio
async def test_run_claude_code_turn_self_heals_stale_session_checkout_and_retries(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)
    branch_ref = claude_code_session_branch_ref(config)

    seed_repo = _init_git_repo(
        tmp_path / "seed-stale-lineage",
        files={
            "sessions/history.json": "{\"turns\": 1}\n",
        },
    )
    _push_branch(seed_repo, remote_repo, branch_ref)

    stale_file = config.local_root / "stale.txt"
    stale_file.parent.mkdir(parents=True, exist_ok=True)
    stale_file.write_text("stale", encoding="utf-8")

    agent = _RetryingFakeAgent(config.local_root)

    result = await run_claude_code_turn(
        agent=agent,  # type: ignore[arg-type]
        prompt="recover",
        kind="regular",
        resume_existing=False,
        session_store=config,
    )

    assert result.status == "completed"
    assert len(agent.calls) == 2
    assert agent.calls[0]["resume_existing"] is True
    assert agent.calls[1]["resume_existing"] is True
    assert stale_file.exists() is False
    assert (config.local_root / "sessions" / "history.json").read_text(encoding="utf-8") == "{\"turns\": 4}\n"


@pytest.mark.asyncio
async def test_run_claude_code_turn_skips_git_sync_for_followup(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)
    agent = _FakeAgent(config.local_root)
    refresh_calls: list[str] = []

    result = await run_claude_code_turn(
        agent=agent,  # type: ignore[arg-type]
        prompt="continue",
        kind="followup",
        resume_existing=True,
        session_store=config,
        refresh_support_files=lambda: refresh_calls.append("refresh"),
    )

    assert result.status == "completed"
    assert refresh_calls == []
    show_ref = subprocess.run(
        ["git", "--git-dir", str(remote_repo), "show-ref", "--verify", "--quiet", claude_code_session_branch_ref(config)],
        capture_output=True,
    )
    assert show_ref.returncode != 0
