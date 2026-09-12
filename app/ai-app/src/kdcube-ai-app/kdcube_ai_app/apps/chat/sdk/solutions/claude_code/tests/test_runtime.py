from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

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
            "projects/-fixture/history.json": "{\"turns\": 1}\n",
        },
    )
    config = _config(tmp_path, git_repo=remote_repo)
    branch_ref = claude_code_session_branch_ref(config)
    _push_branch(seed_repo, remote_repo, branch_ref)

    result = await bootstrap_claude_code_session_store(config=config)

    assert result["bootstrapped"] is True
    assert (config.local_root / "projects" / "-fixture" / "history.json").read_text(encoding="utf-8") == "{\"turns\": 1}\n"
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
            "projects/-fixture/history.json": "{\"turns\": 1}\n",
        },
    )
    config = _config(tmp_path, git_repo=remote_repo)
    branch_ref = claude_code_session_branch_ref(config)
    _push_branch(seed_repo, remote_repo, branch_ref)

    first = await bootstrap_claude_code_session_store(config=config)
    assert first["bootstrapped"] is True
    assert (config.local_root / "projects" / "-fixture" / "history.json").read_text(encoding="utf-8") == "{\"turns\": 1}\n"

    (config.local_root / "projects" / "-fixture" / "history.json").write_text("{\"turns\": 2}\n", encoding="utf-8")
    publish = await publish_claude_code_session_store(config=config)
    assert publish["published"] is True

    second = await bootstrap_claude_code_session_store(config=config)
    assert second["bootstrapped"] is True
    assert (config.local_root / "projects" / "-fixture" / "history.json").read_text(encoding="utf-8") == "{\"turns\": 2}\n"


@pytest.mark.asyncio
async def test_publish_pushes_session_root_to_conversation_branch(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)

    await bootstrap_claude_code_session_store(config=config)
    config.local_root.mkdir(parents=True, exist_ok=True)
    (config.local_root / "projects" / "-fixture").mkdir(parents=True, exist_ok=True)
    (config.local_root / "projects" / "-fixture" / "history.json").write_text("{\"turns\": 2}\n", encoding="utf-8")

    result = await publish_claude_code_session_store(config=config)

    assert result["published"] is True
    assert _read_remote_file(remote_repo, claude_code_session_branch_ref(config), "projects/-fixture/history.json") == "{\"turns\": 2}\n"


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


def test_existing_session_checkout_gets_repo_local_commit_identity(tmp_path: Path):
    local_root = tmp_path / "workspace" / ".claude"
    subprocess.run(
        ["git", "init", str(local_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    config = _config(
        tmp_path,
        git_repo=tmp_path / "remote.git",
        local_root=local_root,
    )

    runtime_module._ensure_local_git_repo(local_root=local_root, config=config)

    name = subprocess.run(
        ["git", "-C", str(local_root), "config", "--local", "--get", "user.name"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    email = subprocess.run(
        ["git", "-C", str(local_root), "config", "--local", "--get", "user.email"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert name == "Claude Session (alice)"
    assert email == "alice@local.invalid"


def test_local_git_mutation_retries_a_transient_index_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    attempts = iter(
        (
            subprocess.CompletedProcess(
                args=[],
                returncode=128,
                stdout="",
                stderr="fatal: Unable to create '.git/index.lock': File exists",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        )
    )
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def run(command, **_kwargs):
        calls.append(command)
        return next(attempts)

    monkeypatch.setattr(runtime_module.subprocess, "run", run)
    monkeypatch.setattr(runtime_module.time, "sleep", sleeps.append)

    runtime_module._run_local_git_mutation(
        repo_root=tmp_path,
        args=["commit", "-m", "snapshot"],
    )

    assert len(calls) == 2
    assert sleeps == [0.1]


def test_local_git_mutation_does_not_retry_a_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=128,
            stdout="",
            stderr="fatal: unable to create directory: Permission denied",
        )

    monkeypatch.setattr(runtime_module.subprocess, "run", run)
    monkeypatch.setattr(runtime_module.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="Permission denied"):
        runtime_module._run_local_git_mutation(
            repo_root=tmp_path,
            args=["commit", "-m", "snapshot"],
        )

    assert len(calls) == 1
    assert sleeps == []


class _FakeAgent:
    def __init__(
        self,
        root: Path,
        *,
        env: dict[str, str] | None = None,
        workspace_path: Path | None = None,
    ):
        self.root = root
        self.calls: list[dict[str, object]] = []
        self.config = SimpleNamespace(
            env=dict(env) if env is not None else {},
            workspace_path=Path(workspace_path) if workspace_path is not None else None,
        )

    async def run_turn(self, prompt: str, *, kind: str = "regular", resume_existing: bool = False) -> ClaudeCodeRunResult:
        self.calls.append({"prompt": prompt, "kind": kind, "resume_existing": resume_existing})
        target = self.root / "projects" / "-fixture" / "history.json"
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
    assert _read_remote_file(remote_repo, claude_code_session_branch_ref(config), "projects/-fixture/history.json") == "{\"turns\": 3}\n"


@pytest.mark.asyncio
async def test_run_claude_code_turn_resumes_regular_turn_when_bootstrap_found_existing_lineage(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)
    branch_ref = claude_code_session_branch_ref(config)

    seed_repo = _init_git_repo(
        tmp_path / "seed-existing-lineage",
        files={
            "projects/-fixture/history.json": "{\"turns\": 1}\n",
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
        self.credential_visible: list[bool] = []
        self.config = SimpleNamespace(env={})

    async def run_turn(self, prompt: str, *, kind: str = "regular", resume_existing: bool = False) -> ClaudeCodeRunResult:
        self.calls.append({"prompt": prompt, "kind": kind, "resume_existing": resume_existing})
        self.credential_visible.append(
            (self.root / runtime_module.CLI_CREDENTIALS_FILENAME).is_symlink()
        )
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
        target = self.root / "projects" / "-fixture" / "history.json"
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
async def test_run_claude_code_turn_self_heals_stale_session_checkout_and_retries(
    tmp_path: Path, monkeypatch
):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)
    branch_ref = claude_code_session_branch_ref(config)
    _host_login(tmp_path, monkeypatch)

    seed_repo = _init_git_repo(
        tmp_path / "seed-stale-lineage",
        files={
            "projects/-fixture/history.json": "{\"turns\": 1}\n",
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
    assert agent.credential_visible == [True, True]
    assert stale_file.exists() is False
    assert (config.local_root / "projects" / "-fixture" / "history.json").read_text(encoding="utf-8") == "{\"turns\": 4}\n"


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


@pytest.mark.asyncio
async def test_run_claude_code_turn_points_cli_at_session_local_root(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)
    agent = _FakeAgent(config.local_root)

    assert "CLAUDE_CONFIG_DIR" not in agent.config.env

    result = await run_claude_code_turn(
        agent=agent,  # type: ignore[arg-type]
        prompt="hello",
        kind="regular",
        resume_existing=False,
        session_store=config,
    )

    assert result.status == "completed"
    assert agent.config.env.get("CLAUDE_CONFIG_DIR") == str(config.local_root)


@pytest.mark.asyncio
async def test_run_claude_code_turn_preserves_explicit_claude_config_dir(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)
    override = str(tmp_path / "custom-claude-home")
    agent = _FakeAgent(config.local_root, env={"CLAUDE_CONFIG_DIR": override})

    result = await run_claude_code_turn(
        agent=agent,  # type: ignore[arg-type]
        prompt="hello",
        kind="regular",
        resume_existing=False,
        session_store=config,
    )

    assert result.status == "completed"
    assert agent.config.env["CLAUDE_CONFIG_DIR"] == override


@pytest.mark.asyncio
async def test_run_claude_code_turn_does_not_set_claude_config_dir_for_local_store(tmp_path: Path):
    config = ClaudeCodeSessionStoreConfig(
        implementation="local",
        local_root=tmp_path / "workspace" / ".claude",
        tenant="home",
        project="demo",
        user_id="alice",
        conversation_id="conv-1",
        agent_name="knowledge-base-admin",
    )
    agent = _FakeAgent(config.local_root)

    result = await run_claude_code_turn(
        agent=agent,  # type: ignore[arg-type]
        prompt="hello",
        kind="regular",
        resume_existing=False,
        session_store=config,
    )

    assert result.status == "completed"
    assert "CLAUDE_CONFIG_DIR" not in agent.config.env


@pytest.mark.asyncio
async def test_bootstrap_seeds_local_root_gitignore(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)

    result = await bootstrap_claude_code_session_store(config=config)

    gitignore_path = config.local_root / ".gitignore"
    assert gitignore_path.is_file()
    contents = gitignore_path.read_text(encoding="utf-8")
    assert ".credentials.json" in contents
    assert "backups/" in contents
    assert result.get("gitignore_seeded") is True

    rerun = await bootstrap_claude_code_session_store(config=config)
    assert rerun.get("gitignore_seeded") is False


@pytest.mark.asyncio
async def test_publish_respects_seeded_gitignore(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)

    await bootstrap_claude_code_session_store(config=config)
    (config.local_root / ".credentials.json").write_text("{\"token\": \"secret\"}", encoding="utf-8")
    (config.local_root / "backups").mkdir(parents=True, exist_ok=True)
    (config.local_root / "backups" / "snapshot.json").write_text("{}", encoding="utf-8")
    projects_dir = config.local_root / "projects" / "-tmp-cwd"
    projects_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / "session.jsonl").write_text("{}\n", encoding="utf-8")

    publish = await publish_claude_code_session_store(config=config)
    assert publish["published"] is True

    branch_ref = claude_code_session_branch_ref(config)
    listing = subprocess.run(
        ["git", "--git-dir", str(remote_repo), "ls-tree", "-r", "--name-only", branch_ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert "projects/-tmp-cwd/session.jsonl" in listing
    assert ".gitignore" in listing
    assert ".credentials.json" not in listing
    assert all(not name.startswith("backups/") for name in listing)


@pytest.mark.asyncio
async def test_bootstrap_sheds_live_control_files_an_older_lineage_carries(tmp_path: Path):
    """The seam that made a stop outlive its turn, seen from the store's side.

    Live control used to seed its buffer, hook and settings into this checkout.
    The store committed them and restored them at the start of every later turn,
    which handed that turn the PREVIOUS turn's stop — with the previous turn's
    id on the hook command line, so the two agreed and the hook denied every
    tool call. Live control keeps its own directory now; a lineage that already
    carries these files has to shed them, or it replays that stop forever.
    """
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    seed_repo = _init_git_repo(
        tmp_path / "seed",
        files={
            "projects/-fixture/history.json": "{\"turns\": 1}\n",
            "kdcube-live-events.json": "{\"turn_id\": \"turn-1\", \"stop\": true, \"messages\": []}",
            "kdcube-live-hook.py": "# stale hook\n",
            "kdcube-live-settings.json": "{\"hooks\": {}}",
        },
    )
    config = _config(tmp_path, git_repo=remote_repo)
    _push_branch(seed_repo, remote_repo, claude_code_session_branch_ref(config))

    await bootstrap_claude_code_session_store(config=config)

    for name in ("kdcube-live-events.json", "kdcube-live-hook.py", "kdcube-live-settings.json"):
        assert not (config.local_root / name).exists(), name
    # The session's own content is untouched.
    assert (config.local_root / "projects" / "-fixture" / "history.json").is_file()

    # And the lineage stops carrying them after this turn's snapshot.
    publish = await publish_claude_code_session_store(config=config)
    assert publish["published"] is True
    listing = subprocess.run(
        ["git", "--git-dir", str(remote_repo), "ls-tree", "-r", "--name-only",
         claude_code_session_branch_ref(config)],
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    assert not [name for name in listing if name.startswith("kdcube-live-")]


def test_sanitize_cwd_matches_observed_claude_cli_scheme():
    sanitized = runtime_module._sanitize_cwd_for_claude_projects(
        Path("/private/tmp/claude-probe2/has.dot and_space")
    )
    assert sanitized == "-private-tmp-claude-probe2-has-dot-and-space"


def test_retarget_session_project_dir_renames_to_current_cwd(tmp_path: Path):
    local_root = tmp_path / "local"
    old_project = local_root / "projects" / "-some-other-host-workspace-issue"
    old_project.mkdir(parents=True, exist_ok=True)
    (old_project / "abc.jsonl").write_text(
        json.dumps({"type": "user", "cwd": "/some/other/host/workspace/issue", "sessionId": "abc"}) + "\n",
        encoding="utf-8",
    )
    current_cwd = tmp_path / "workspace" / "issue"
    current_cwd.mkdir(parents=True, exist_ok=True)

    renamed = runtime_module._retarget_session_project_dir(
        local_root=local_root,
        cwd=current_cwd,
    )

    expected_name = runtime_module._sanitize_cwd_for_claude_projects(current_cwd)
    assert renamed is True
    assert (local_root / "projects" / expected_name / "abc.jsonl").is_file()
    assert not old_project.exists()


def test_retarget_session_project_dir_noop_when_already_matching(tmp_path: Path):
    local_root = tmp_path / "local"
    current_cwd = tmp_path / "workspace" / "issue"
    current_cwd.mkdir(parents=True, exist_ok=True)
    expected_name = runtime_module._sanitize_cwd_for_claude_projects(current_cwd)
    project_dir = local_root / "projects" / expected_name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "abc.jsonl").write_text(
        json.dumps({"type": "user", "cwd": str(current_cwd.resolve()), "sessionId": "abc"}) + "\n",
        encoding="utf-8",
    )

    renamed = runtime_module._retarget_session_project_dir(
        local_root=local_root,
        cwd=current_cwd,
    )

    assert renamed is False
    assert (project_dir / "abc.jsonl").is_file()


def test_retarget_session_project_dir_noop_when_ambiguous(tmp_path: Path):
    local_root = tmp_path / "local"
    (local_root / "projects" / "-host-a-workspace").mkdir(parents=True, exist_ok=True)
    (local_root / "projects" / "-host-b-workspace").mkdir(parents=True, exist_ok=True)
    current_cwd = tmp_path / "workspace" / "issue"
    current_cwd.mkdir(parents=True, exist_ok=True)

    renamed = runtime_module._retarget_session_project_dir(
        local_root=local_root,
        cwd=current_cwd,
    )

    assert renamed is False
    assert (local_root / "projects" / "-host-a-workspace").is_dir()
    assert (local_root / "projects" / "-host-b-workspace").is_dir()


@pytest.mark.asyncio
async def test_run_claude_code_turn_retargets_project_dir_for_cross_node_resume(tmp_path: Path):
    remote_repo = _init_bare_repo(tmp_path / "remote.git")
    config = _config(tmp_path, git_repo=remote_repo)
    branch_ref = claude_code_session_branch_ref(config)

    seed_repo = _init_git_repo(
        tmp_path / "seed-cross-node",
        files={
            "projects/-old-host-bundle-issue/abc.jsonl": json.dumps(
                {"type": "user", "cwd": "/old/host/bundle/issue", "sessionId": "abc"}
            )
            + "\n",
        },
    )
    _push_branch(seed_repo, remote_repo, branch_ref)

    current_cwd = tmp_path / "current" / "host" / "issue"
    current_cwd.mkdir(parents=True, exist_ok=True)
    agent = _FakeAgent(config.local_root, workspace_path=current_cwd)

    result = await run_claude_code_turn(
        agent=agent,  # type: ignore[arg-type]
        prompt="resume",
        kind="regular",
        resume_existing=False,
        session_store=config,
    )

    assert result.status == "completed"
    expected_name = runtime_module._sanitize_cwd_for_claude_projects(current_cwd)
    assert (config.local_root / "projects" / expected_name / "abc.jsonl").is_file()
    assert not (config.local_root / "projects" / "-old-host-bundle-issue").exists()


def _checkout(tmp_path: Path, *, ignored: bool = True, tracked_credential: bool = False) -> Path:
    files = {"README.md": "# store\n"}
    if ignored:
        files[".gitignore"] = runtime_module.CLAUDE_CODE_SESSION_GITIGNORE
    root = _init_git_repo(tmp_path / "checkout", files=files)
    if tracked_credential:
        (root / runtime_module.CLI_CREDENTIALS_FILENAME).write_text("stale", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(root), "add", "-f", runtime_module.CLI_CREDENTIALS_FILENAME],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(root), "commit", "-m", "tracked credential"],
            check=True, capture_output=True, text=True,
        )
    return root


def _host_login(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    source = home / ".claude" / runtime_module.CLI_CREDENTIALS_FILENAME
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    return source


def _is_tracked(root: Path, name: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", name],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


def test_a_descriptor_key_leaves_the_host_login_alone(tmp_path: Path, monkeypatch) -> None:
    root = _checkout(tmp_path)
    _host_login(tmp_path, monkeypatch)

    linked = runtime_module._materialize_cli_credentials(
        local_root=root, env={"ANTHROPIC_API_KEY": "sk-configured"}
    )

    assert linked is None
    assert not (root / runtime_module.CLI_CREDENTIALS_FILENAME).exists()


def test_nothing_is_linked_without_a_host_login(tmp_path: Path, monkeypatch) -> None:
    root = _checkout(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))

    linked = runtime_module._materialize_cli_credentials(local_root=root, env={})

    assert linked is None
    assert not (root / runtime_module.CLI_CREDENTIALS_FILENAME).exists()


def test_the_host_login_is_linked_and_stays_out_of_git(tmp_path: Path, monkeypatch) -> None:
    root = _checkout(tmp_path)
    source = _host_login(tmp_path, monkeypatch)

    linked = runtime_module._materialize_cli_credentials(local_root=root, env={})

    target = root / runtime_module.CLI_CREDENTIALS_FILENAME
    assert linked == str(target)
    assert target.is_symlink() and target.resolve() == source.resolve()
    assert not _is_tracked(root, runtime_module.CLI_CREDENTIALS_FILENAME)


def test_a_lineage_that_tracks_the_credential_is_untracked(tmp_path: Path, monkeypatch) -> None:
    root = _checkout(tmp_path, tracked_credential=True)
    _host_login(tmp_path, monkeypatch)
    assert _is_tracked(root, runtime_module.CLI_CREDENTIALS_FILENAME)

    linked = runtime_module._materialize_cli_credentials(local_root=root, env={})

    assert linked is not None
    assert (root / runtime_module.CLI_CREDENTIALS_FILENAME).is_symlink()
    assert not _is_tracked(root, runtime_module.CLI_CREDENTIALS_FILENAME)


def test_the_link_is_withdrawn_when_the_name_cannot_become_ignored(
    tmp_path: Path, monkeypatch
) -> None:
    root = _checkout(tmp_path, ignored=False)
    _host_login(tmp_path, monkeypatch)

    linked = runtime_module._materialize_cli_credentials(local_root=root, env={})

    assert linked is None
    assert not (root / runtime_module.CLI_CREDENTIALS_FILENAME).exists()
