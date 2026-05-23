# SPDX-License-Identifier: MIT

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kdcube_ai_app.infra.git import auth as git_auth


@pytest.mark.asyncio
async def test_build_git_env_uses_explicit_http_credentials(tmp_path: Path):
    askpass_path = tmp_path / "git_askpass.sh"

    env = await git_auth.build_git_env(
        git_http_token="pat-token",
        git_http_user="git-user",
        askpass_script_path=askpass_path,
        base_env={},
    )

    assert env["GIT_HTTP_TOKEN"] == "pat-token"
    assert env["GIT_HTTP_USER"] == "git-user"
    assert env["GIT_ASKPASS"] == str(askpass_path)
    assert env["GIT_ASKPASS_REQUIRE"] == "force"
    assert askpass_path.exists()


@pytest.mark.asyncio
async def test_build_git_env_uses_explicit_ssh_settings_without_key(monkeypatch):
    async def fake_get_secret(key, default=None, **kwargs):
        return default

    monkeypatch.setattr(git_auth, "get_secret", fake_get_secret)
    env = await git_auth.build_git_env(
        git_ssh_known_hosts="/run/secrets/git_known_hosts",
        git_ssh_strict_host_key_checking="yes",
        base_env={},
    )

    assert env["GIT_SSH_COMMAND"] == (
        "ssh -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/secrets/git_known_hosts"
    )


@pytest.mark.asyncio
async def test_normalize_git_remote_url_rewrites_ssh_when_pat_is_present():
    assert await git_auth.normalize_git_remote_url(
        "git@github.com:org/workspace.git",
        git_http_token="pat-token",
        base_env={},
    ) == "https://github.com/org/workspace.git"


@pytest.mark.asyncio
async def test_normalize_git_remote_url_leaves_ssh_when_pat_is_absent(monkeypatch):
    async def fake_get_secret(key, default=None, **kwargs):
        return default

    monkeypatch.setattr(git_auth, "get_secret", fake_get_secret)
    assert await git_auth.normalize_git_remote_url(
        "git@github.com:org/workspace.git",
        base_env={},
    ) == "git@github.com:org/workspace.git"


def test_ensure_git_commit_identity_sets_repo_local_identity(tmp_path: Path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)

    git_auth.ensure_git_commit_identity(
        repo_root=repo,
        name="Local Bundle Bot",
        email="bundle.bot@local.invalid",
    )

    name = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "user.name"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    email = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "user.email"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert name == "Local Bundle Bot"
    assert email == "bundle.bot@local.invalid"


def test_ensure_git_commit_identity_updates_existing_repo_local_identity(tmp_path: Path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Old Name"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "old@local.invalid"], check=True, capture_output=True, text=True)

    git_auth.ensure_git_commit_identity(
        repo_root=repo,
        name="New Name",
        email="new@local.invalid",
    )

    name = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "user.name"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    email = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "user.email"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert name == "New Name"
    assert email == "new@local.invalid"


# ---------------------------------------------------------------------------
# Descriptor/settings-backed credential resolution path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_git_env_token_from_secret_when_no_explicit_token(monkeypatch, tmp_path):
    async def fake_get_secret(key, default=None, **kwargs):
        return "gh-bundle-token" if key == "services.git.http_token" else default

    monkeypatch.setattr(git_auth, "get_secret", fake_get_secret)
    askpass = tmp_path / "askpass.sh"

    env = await git_auth.build_git_env(askpass_script_path=askpass, base_env={})

    assert env["GIT_HTTP_TOKEN"] == "gh-bundle-token"
    assert env.get("GIT_ASKPASS") == str(askpass)


@pytest.mark.asyncio
async def test_build_git_env_user_from_secret_when_no_explicit_user(monkeypatch, tmp_path):
    async def fake_get_secret(key, default=None, **kwargs):
        if key == "services.git.http_token":
            return "gh-token"
        if key == "services.git.http_user":
            return "custom-user"
        return default

    monkeypatch.setattr(git_auth, "get_secret", fake_get_secret)
    askpass = tmp_path / "askpass.sh"

    env = await git_auth.build_git_env(askpass_script_path=askpass, base_env={})

    assert env["GIT_HTTP_USER"] == "custom-user"


@pytest.mark.asyncio
async def test_build_git_env_explicit_token_beats_service_secret(monkeypatch, tmp_path):
    """Explicit git_http_token= parameter has priority over settings value."""
    async def fake_get_secret(key, default=None, **kwargs):
        return "gh-should-not-use" if key == "services.git.http_token" else default

    monkeypatch.setattr(git_auth, "get_secret", fake_get_secret)
    askpass = tmp_path / "askpass.sh"

    env = await git_auth.build_git_env(
        git_http_token="gh-explicit",
        askpass_script_path=askpass,
        base_env={},
    )

    assert env["GIT_HTTP_TOKEN"] == "gh-explicit"


@pytest.mark.asyncio
async def test_build_git_env_no_token_falls_back_to_base_env(monkeypatch, tmp_path):
    """When settings return nothing, GIT_HTTP_TOKEN from base_env is used."""
    async def fake_get_secret(key, default=None, **kwargs):
        return default

    monkeypatch.setattr(git_auth, "get_secret", fake_get_secret)
    askpass = tmp_path / "askpass.sh"

    env = await git_auth.build_git_env(
        askpass_script_path=askpass,
        base_env={"GIT_HTTP_TOKEN": "gh-env-token"},
    )

    assert env["GIT_HTTP_TOKEN"] == "gh-env-token"


@pytest.mark.asyncio
async def test_build_git_env_default_user_when_no_user_secret(monkeypatch, tmp_path):
    """Falls back to DEFAULT_GIT_HTTP_USER when no http_user in settings or base_env."""
    async def fake_get_secret(key, default=None, **kwargs):
        return "gh-token" if key == "services.git.http_token" else default

    monkeypatch.setattr(git_auth, "get_secret", fake_get_secret)
    askpass = tmp_path / "askpass.sh"

    env = await git_auth.build_git_env(askpass_script_path=askpass, base_env={})

    assert env["GIT_HTTP_USER"] == git_auth.DEFAULT_GIT_HTTP_USER
