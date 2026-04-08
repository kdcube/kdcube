from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage


def _bundle_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module():
    module_path = _bundle_root() / "knowledge_base_admin.py"
    spec = importlib.util.spec_from_file_location("copilot_knowledge_base_admin_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_storage(tmp_path: Path) -> AIBundleStorage:
    return AIBundleStorage(
        tenant="demo-tenant",
        project="demo-project",
        ai_bundle_id="kdcube.copilot@2026-04-03-19-05",
        storage_uri=tmp_path.resolve().as_uri(),
    )


def _init_git_repo(path: Path, *, branch: str = "main", filename: str = "README.md", content: str = "# Test\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--initial-branch", branch], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True, text=True)
    (path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, text=True)
    return path


def _init_bare_remote_from_repo(source_repo: Path, bare_path: Path) -> Path:
    subprocess.run(["git", "clone", "--bare", str(source_repo), str(bare_path)], check=True, capture_output=True, text=True)
    return bare_path


def test_validate_workspace_config_limits_to_three_content_repos():
    mod = _load_module()

    config = mod.validate_workspace_config(
        {
            "content_repos": [
                {"source": "https://example.com/one.git", "label": "One"},
                {"source": "https://example.com/two.git", "label": "Two"},
                {"source": "https://example.com/three.git", "label": "Three"},
                {"source": "https://example.com/four.git", "label": "Four"},
            ],
            "output_repo": {"source": "https://example.com/output.git", "label": "Output"},
        }
    )

    assert [item["label"] for item in config["content_repos"]] == ["One", "Two", "Three"]
    assert config["output_repo"]["label"] == "Output"


def test_validate_workspace_config_requires_sources_and_output_repo():
    mod = _load_module()

    with pytest.raises(ValueError, match="at least one source repository"):
        mod.validate_workspace_config({"content_repos": [], "output_repo": {}})

    with pytest.raises(ValueError, match="output repository"):
        mod.validate_workspace_config(
            {
                "content_repos": [{"source": "https://example.com/source.git"}],
                "output_repo": {},
            }
        )


def test_conversation_roundtrip_and_widget_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load_module()
    storage = _make_storage(tmp_path)
    timestamps = iter(
        [
            "2026-04-08T10:00:00+00:00",
            "2026-04-08T10:01:00+00:00",
            "2026-04-08T10:02:00+00:00",
            "2026-04-08T10:03:00+00:00",
            "2026-04-08T10:04:00+00:00",
            "2026-04-08T10:05:00+00:00",
        ]
    )
    monkeypatch.setattr(mod, "_utc_now", lambda: next(timestamps))

    mod.save_user_config(
        storage,
        "alice",
        content_repos=[{"source": "https://example.com/docs.git", "label": "Docs"}],
        output_repo={"source": "https://example.com/wiki.git", "label": "Wiki"},
    )

    conversation = mod.create_or_load_conversation(
        storage,
        "alice",
        conversation_id="kb_admin_demo",
        title_hint="Initial wiki planning pass",
    )
    mod.append_conversation_message(
        storage,
        "alice",
        conversation_id=conversation["conversation_id"],
        role="user",
        text="Build a wiki plan.",
        metadata={"turn_kind": "regular"},
    )
    mod.append_conversation_message(
        storage,
        "alice",
        conversation_id=conversation["conversation_id"],
        role="assistant",
        text="I will inspect the repos and prepare a structure.",
        metadata={"turn_kind": "regular"},
    )

    payload = mod.build_widget_payload(
        storage,
        "alice",
        has_git_pat=True,
        has_anthropic_api_key=False,
        has_claude_code_key=True,
        selected_conversation_id=conversation["conversation_id"],
    )

    assert payload["secrets"] == {
        "has_git_pat": True,
        "has_anthropic_api_key": False,
        "has_claude_code_key": True,
    }
    assert payload["selected_conversation_id"] == "kb_admin_demo"
    assert payload["current_conversation"]["messages"][0]["role"] == "user"
    assert payload["current_conversation"]["messages"][1]["role"] == "assistant"
    assert payload["conversations"][0]["message_count"] == 2
    serialized = json.dumps(payload)
    assert "secret-value" not in serialized
    assert "ghp_" not in serialized


def test_ensure_workspace_clones_local_repos_and_writes_context(tmp_path: Path):
    mod = _load_module()
    source_repo = _init_git_repo(tmp_path / "source", filename="docs.md", content="# Source docs\n")
    output_repo = _init_git_repo(tmp_path / "output", filename="README.md", content="# Output repo\n")
    local_root = tmp_path / "local-storage"

    result = mod.ensure_workspace(
        local_root=local_root,
        user_id="alice",
        config={
            "content_repos": [{"source": str(source_repo), "label": "Source Docs", "branch": "main"}],
            "output_repo": {"source": str(output_repo), "label": "Wiki Output", "branch": "main"},
        },
        git_http_token=None,
        git_http_user=None,
        sync_existing=True,
    )

    workspace_root = Path(result["workspace_root"])
    assert workspace_root.exists()
    assert len(result["repo_statuses"]) == 2
    assert {item["repo_type"] for item in result["repo_statuses"]} == {"content", "output"}

    payload_path = workspace_root / ".kdcube" / "knowledge-base-admin-workspace.json"
    prompt_path = workspace_root / ".kdcube" / "knowledge-base-admin-workspace.md"
    agent_path = workspace_root / ".claude" / "agents" / "knowledge-base-admin.md"

    assert payload_path.exists()
    assert prompt_path.exists()
    assert agent_path.exists()

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["output_repo"]["label"] == "Wiki Output"
    assert payload["content_repos"][0]["label"] == "Source Docs"
    assert "Write generated wiki and knowledge-base outputs only into the output repo" in prompt_path.read_text(encoding="utf-8")


def test_ensure_workspace_rejects_ssh_repo_when_pat_auth_is_used(tmp_path: Path):
    mod = _load_module()

    with pytest.raises(RuntimeError, match="PAT auth only works with https:// remotes"):
        mod.ensure_workspace(
            local_root=tmp_path / "local-storage",
            user_id="alice",
            config={
                "content_repos": [{"source": "git@github.com:acme/docs.git", "label": "Source Docs"}],
                "output_repo": {"source": "https://github.com/acme/wiki.git", "label": "Wiki Output"},
            },
            git_http_token="ghp_example",
            git_http_user="x-access-token",
            sync_existing=False,
        )


def test_ensure_workspace_creates_missing_output_branch_locally(tmp_path: Path):
    mod = _load_module()
    source_repo = _init_git_repo(tmp_path / "source", filename="docs.md", content="# Source docs\n")
    output_repo = _init_git_repo(tmp_path / "output", filename="README.md", content="# Output repo\n")

    result = mod.ensure_workspace(
        local_root=tmp_path / "local-storage",
        user_id="alice",
        config={
            "content_repos": [{"source": str(source_repo), "label": "Source Docs", "branch": "main"}],
            "output_repo": {
                "source": str(output_repo),
                "label": "Wiki Output",
                "branch": "kdcube-copilot-admin",
            },
        },
        git_http_token=None,
        git_http_user=None,
        sync_existing=True,
    )

    output_status = next(item for item in result["repo_statuses"] if item["repo_type"] == "output")
    assert output_status["action"] == "cloned-local-branch"
    assert output_status["current_branch"] == "kdcube-copilot-admin"


def test_push_output_repo_creates_remote_branch(tmp_path: Path):
    mod = _load_module()
    source_repo = _init_git_repo(tmp_path / "source", filename="docs.md", content="# Source docs\n")
    seed_output_repo = _init_git_repo(tmp_path / "output-seed", filename="README.md", content="# Output repo\n")
    output_remote = _init_bare_remote_from_repo(seed_output_repo, tmp_path / "output-remote.git")
    local_root = tmp_path / "local-storage"
    config = {
        "content_repos": [{"source": str(source_repo), "label": "Source Docs", "branch": "main"}],
        "output_repo": {"source": str(output_remote), "label": "Wiki Output", "branch": "kdcube-copilot-admin"},
    }

    mod.ensure_workspace(
        local_root=local_root,
        user_id="alice",
        config=config,
        git_http_token=None,
        git_http_user=None,
        sync_existing=True,
    )

    status = mod.push_output_repo(
        local_root=local_root,
        user_id="alice",
        config=config,
        git_http_token=None,
        git_http_user=None,
    )

    remote_heads = subprocess.run(
        ["git", "ls-remote", "--heads", str(output_remote), "kdcube-copilot-admin"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert status["action"] == "pushed"
    assert status["current_branch"] == "kdcube-copilot-admin"
    assert remote_heads


def test_reset_output_repo_resets_to_requested_commit(tmp_path: Path):
    mod = _load_module()
    source_repo = _init_git_repo(tmp_path / "source", filename="docs.md", content="# Source docs\n")
    seed_output_repo = _init_git_repo(tmp_path / "output-seed", filename="README.md", content="# Output repo\n")
    output_remote = _init_bare_remote_from_repo(seed_output_repo, tmp_path / "output-remote.git")
    local_root = tmp_path / "local-storage"
    config = {
        "content_repos": [{"source": str(source_repo), "label": "Source Docs", "branch": "main"}],
        "output_repo": {"source": str(output_remote), "label": "Wiki Output", "branch": "kdcube-copilot-admin"},
    }

    result = mod.ensure_workspace(
        local_root=local_root,
        user_id="alice",
        config=config,
        git_http_token=None,
        git_http_user=None,
        sync_existing=True,
    )
    output_status = next(item for item in result["repo_statuses"] if item["repo_type"] == "output")
    output_local = Path(output_status["local_path"])
    initial_head = output_status["head"]

    (output_local / "wiki.md").write_text("# wiki\n", encoding="utf-8")
    subprocess.run(["git", "add", "wiki.md"], cwd=output_local, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "add wiki"], cwd=output_local, check=True, capture_output=True, text=True)

    reset_status = mod.reset_output_repo(
        local_root=local_root,
        user_id="alice",
        config=config,
        commit=initial_head,
        git_http_token=None,
        git_http_user=None,
    )

    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=output_local,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert reset_status["action"] == "reset"
    assert current_head == initial_head
