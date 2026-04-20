# SPDX-License-Identifier: MIT

from __future__ import annotations

from contextlib import contextmanager
import subprocess
from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.git import auth as git_auth
from kdcube_ai_app.infra.plugin import git_bundle


@pytest.fixture(autouse=True)
def _clear_git_bundle_fail_state():
    git_bundle._FAIL_STATE.clear()
    yield
    git_bundle._FAIL_STATE.clear()


def _settings_for_roots(*, host_bundles=None, host_managed=None, managed_root="/managed-bundles", always_pull=False):
    return SimpleNamespace(
        HOST_BUNDLES_PATH=host_bundles,
        HOST_MANAGED_BUNDLES_PATH=host_managed,
        PLATFORM=SimpleNamespace(
            APPLICATIONS=SimpleNamespace(
                BUNDLES_ROOT="/bundles",
                MANAGED_BUNDLES_ROOT=managed_root,
                GIT=SimpleNamespace(
                    BUNDLE_GIT_FAIL_BACKOFF_SECONDS=60,
                    BUNDLE_GIT_FAIL_MAX_BACKOFF_SECONDS=300,
                    BUNDLE_GIT_ALWAYS_PULL=always_pull,
                ),
            )
        ),
    )


def test_ensure_git_bundle_holds_local_lock_during_git_operations(monkeypatch, tmp_path):
    state = {"redis_lock_held": False, "bundle_lock_held": False, "run_git_calls": 0}

    @contextmanager
    def _fake_redis_lock(*, bundle_id, git_ref):
        del bundle_id, git_ref
        state["redis_lock_held"] = True
        try:
            yield
        finally:
            state["redis_lock_held"] = False

    @contextmanager
    def _fake_bundle_lock(*, bundle_id, git_ref, bundles_root):
        del bundle_id, git_ref, bundles_root
        state["bundle_lock_held"] = True
        try:
            yield
        finally:
            state["bundle_lock_held"] = False

    def _fake_run_git(args, *, logger=None, env=None):
        del logger, env
        state["run_git_calls"] += 1
        assert state["redis_lock_held"] is True
        assert state["bundle_lock_held"] is True
        repo_root = git_bundle.pathlib.Path(args[-1])
        repo_root.mkdir(parents=True, exist_ok=True)
        (repo_root / ".git").mkdir(exist_ok=True)

    monkeypatch.setattr(git_bundle, "_redis_bundle_lock", _fake_redis_lock)
    monkeypatch.setattr(git_bundle, "_bundle_lock", _fake_bundle_lock)
    monkeypatch.setattr(git_bundle, "_build_git_env", lambda: {})
    monkeypatch.setattr(git_bundle, "_git_depth", lambda: None)
    monkeypatch.setattr(git_bundle, "_run_git", _fake_run_git)

    paths = git_bundle.ensure_git_bundle(
        bundle_id="demo",
        git_url="https://example.com/repo.git",
        bundles_root=tmp_path,
    )

    assert state["run_git_calls"] == 1
    assert paths.repo_root.exists()


def test_resolve_managed_bundles_root_prefers_dedicated_managed_root(monkeypatch, tmp_path):
    host_git_root = tmp_path / "host-git"
    host_git_root.mkdir()
    fallback_root = tmp_path / "fallback"
    fallback_root.mkdir()

    monkeypatch.setattr(
        git_bundle,
        "get_settings",
        lambda: _settings_for_roots(host_managed=str(host_git_root), managed_root=str(fallback_root)),
    )

    assert git_bundle.resolve_managed_bundles_root() == host_git_root.resolve()


def test_resolve_managed_bundles_root_defaults_to_container_root(monkeypatch):
    container_root = "/managed-bundles"

    monkeypatch.setattr(
        git_bundle,
        "get_settings",
        lambda: _settings_for_roots(host_managed=None, managed_root=container_root),
    )

    assert git_bundle.resolve_managed_bundles_root() == git_bundle.pathlib.Path(container_root).resolve()


def test_build_git_env_uses_known_hosts_without_explicit_key(monkeypatch):
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH_KEY_PATH", raising=False)
    monkeypatch.setenv("GIT_SSH_KNOWN_HOSTS", "/run/secrets/git_known_hosts")
    monkeypatch.setenv("GIT_SSH_STRICT_HOST_KEY_CHECKING", "yes")
    monkeypatch.delenv("GIT_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("GIT_HTTP_USER", raising=False)
    monkeypatch.setattr(
        git_auth,
        "get_settings",
        lambda: SimpleNamespace(
            GIT_HTTP_TOKEN=None,
            GIT_HTTP_USER=None,
            PLATFORM=SimpleNamespace(
                APPLICATIONS=SimpleNamespace(
                    GIT=SimpleNamespace(
                        GIT_SSH_KEY_PATH=None,
                        GIT_SSH_KNOWN_HOSTS="/run/secrets/git_known_hosts",
                        GIT_SSH_STRICT_HOST_KEY_CHECKING="yes",
                    )
                )
            ),
        ),
    )

    env = git_bundle._build_git_env()

    assert env["GIT_SSH_COMMAND"] == (
        "ssh -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/secrets/git_known_hosts"
    )


def test_build_git_env_still_includes_key_when_present(monkeypatch):
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.setenv("GIT_SSH_KEY_PATH", "/run/secrets/git_ssh_key")
    monkeypatch.setenv("GIT_SSH_KNOWN_HOSTS", "/run/secrets/git_known_hosts")
    monkeypatch.setenv("GIT_SSH_STRICT_HOST_KEY_CHECKING", "yes")
    monkeypatch.delenv("GIT_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("GIT_HTTP_USER", raising=False)
    monkeypatch.setattr(
        git_auth,
        "get_settings",
        lambda: SimpleNamespace(
            GIT_HTTP_TOKEN=None,
            GIT_HTTP_USER=None,
            PLATFORM=SimpleNamespace(
                APPLICATIONS=SimpleNamespace(
                    GIT=SimpleNamespace(
                        GIT_SSH_KEY_PATH="/run/secrets/git_ssh_key",
                        GIT_SSH_KNOWN_HOSTS="/run/secrets/git_known_hosts",
                        GIT_SSH_STRICT_HOST_KEY_CHECKING="yes",
                    )
                )
            ),
        ),
    )

    env = git_bundle._build_git_env()

    assert env["GIT_SSH_COMMAND"] == (
        "ssh -i /run/secrets/git_ssh_key -o IdentitiesOnly=yes "
        "-o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/secrets/git_known_hosts"
    )


def test_atomic_dir_name_is_unique_tmp_workspace(monkeypatch):
    monkeypatch.setattr(git_bundle.os, "getpid", lambda: 42)
    sequence = iter([1001, 1002])
    monkeypatch.setattr(git_bundle.time, "time_ns", lambda: next(sequence))

    first = git_bundle._atomic_dir_name("bundle-root")
    second = git_bundle._atomic_dir_name("bundle-root")

    assert first == ".bundle-root.tmp-42-1001"
    assert second == ".bundle-root.tmp-42-1002"
    assert first != second


def test_ensure_git_bundle_skips_pull_for_detached_ref(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    @contextmanager
    def _fake_redis_lock(*, bundle_id, git_ref):
        del bundle_id, git_ref
        yield

    @contextmanager
    def _fake_bundle_lock(*, bundle_id, git_ref, bundles_root):
        del bundle_id, git_ref, bundles_root
        yield

    def _fake_run_git(args, *, logger=None, env=None):
        del logger, env
        calls.append(list(args))

    def _fake_subprocess_run(args, check=False, capture_output=False, text=False, env=None):
        del check, capture_output, text, env
        cmd = list(args)
        if cmd[-4:] == ["config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="https://example.com/repo.git\n", stderr="")
        if cmd[-4:] == ["symbolic-ref", "--quiet", "--short", "HEAD"]:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="")
        if cmd[-2:] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="deadbeef\n", stderr="")
        raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

    monkeypatch.setattr(git_bundle, "_redis_bundle_lock", _fake_redis_lock)
    monkeypatch.setattr(git_bundle, "_bundle_lock", _fake_bundle_lock)
    monkeypatch.setattr(git_bundle, "_build_git_env", lambda: {})
    monkeypatch.setattr(git_bundle, "_git_depth", lambda: None)
    monkeypatch.setattr(git_bundle, "_run_git", _fake_run_git)
    monkeypatch.setattr(git_bundle.subprocess, "run", _fake_subprocess_run)
    monkeypatch.setattr(git_bundle, "get_settings", lambda: _settings_for_roots(always_pull=True))

    paths = git_bundle.compute_git_bundle_paths(
        bundle_id="demo",
        git_url="https://example.com/repo.git",
        git_ref="2026.4.02.115",
        bundles_root=tmp_path,
    )
    paths.repo_root.mkdir(parents=True, exist_ok=True)
    (paths.repo_root / ".git").mkdir(exist_ok=True)

    git_bundle.ensure_git_bundle(
        bundle_id="demo",
        git_url="https://example.com/repo.git",
        git_ref="2026.4.02.115",
        bundles_root=tmp_path,
    )

    assert any(cmd[-3:] == ["checkout", "--force", "2026.4.02.115"] for cmd in calls)
    assert not any("pull" in cmd for cmd in calls)
    assert not any("reset" in cmd for cmd in calls)


def test_ensure_git_bundle_pulls_for_attached_branch(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    @contextmanager
    def _fake_redis_lock(*, bundle_id, git_ref):
        del bundle_id, git_ref
        yield

    @contextmanager
    def _fake_bundle_lock(*, bundle_id, git_ref, bundles_root):
        del bundle_id, git_ref, bundles_root
        yield

    def _fake_run_git(args, *, logger=None, env=None):
        del logger, env
        calls.append(list(args))

    def _fake_subprocess_run(args, check=False, capture_output=False, text=False, env=None):
        del check, capture_output, text, env
        cmd = list(args)
        if cmd[-4:] == ["config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="https://example.com/repo.git\n", stderr="")
        if cmd[-4:] == ["symbolic-ref", "--quiet", "--short", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="release-2026.4.02.115\n", stderr="")
        if cmd[-2:] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="cafebabe\n", stderr="")
        raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

    monkeypatch.setattr(git_bundle, "_redis_bundle_lock", _fake_redis_lock)
    monkeypatch.setattr(git_bundle, "_bundle_lock", _fake_bundle_lock)
    monkeypatch.setattr(git_bundle, "_build_git_env", lambda: {})
    monkeypatch.setattr(git_bundle, "_git_depth", lambda: None)
    monkeypatch.setattr(git_bundle, "_run_git", _fake_run_git)
    monkeypatch.setattr(git_bundle.subprocess, "run", _fake_subprocess_run)
    monkeypatch.setattr(git_bundle, "get_settings", lambda: _settings_for_roots(always_pull=True))

    paths = git_bundle.compute_git_bundle_paths(
        bundle_id="demo",
        git_url="https://example.com/repo.git",
        git_ref="release-2026.4.02.115",
        bundles_root=tmp_path,
    )
    paths.repo_root.mkdir(parents=True, exist_ok=True)
    (paths.repo_root / ".git").mkdir(exist_ok=True)

    git_bundle.ensure_git_bundle(
        bundle_id="demo",
        git_url="https://example.com/repo.git",
        git_ref="release-2026.4.02.115",
        bundles_root=tmp_path,
    )

    assert any(cmd[-3:] == ["checkout", "--force", "release-2026.4.02.115"] for cmd in calls)
    assert any(cmd[-3:] == ["reset", "--hard", "origin/release-2026.4.02.115"] for cmd in calls)


def test_ensure_git_bundle_raises_when_branch_reset_fails(monkeypatch, tmp_path):
    @contextmanager
    def _fake_redis_lock(*, bundle_id, git_ref):
        del bundle_id, git_ref
        yield

    @contextmanager
    def _fake_bundle_lock(*, bundle_id, git_ref, bundles_root):
        del bundle_id, git_ref, bundles_root
        yield

    def _fake_run_git(args, *, logger=None, env=None):
        del logger, env
        cmd = list(args)
        if cmd[-3:] == ["reset", "--hard", "origin/release-2026.4.02.115"]:
            raise RuntimeError("reset failed")

    def _fake_subprocess_run(args, check=False, capture_output=False, text=False, env=None):
        del check, capture_output, text, env
        cmd = list(args)
        if cmd[-4:] == ["config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="https://example.com/repo.git\n", stderr="")
        if cmd[-4:] == ["symbolic-ref", "--quiet", "--short", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="release-2026.4.02.115\n", stderr="")
        if cmd[-2:] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="cafebabe\n", stderr="")
        raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

    monkeypatch.setattr(git_bundle, "_redis_bundle_lock", _fake_redis_lock)
    monkeypatch.setattr(git_bundle, "_bundle_lock", _fake_bundle_lock)
    monkeypatch.setattr(git_bundle, "_build_git_env", lambda: {})
    monkeypatch.setattr(git_bundle, "_git_depth", lambda: None)
    monkeypatch.setattr(git_bundle, "_run_git", _fake_run_git)
    monkeypatch.setattr(git_bundle.subprocess, "run", _fake_subprocess_run)
    monkeypatch.setattr(git_bundle, "get_settings", lambda: _settings_for_roots(always_pull=True))

    paths = git_bundle.compute_git_bundle_paths(
        bundle_id="demo",
        git_url="https://example.com/repo.git",
        git_ref="release-2026.4.02.115",
        bundles_root=tmp_path,
    )
    paths.repo_root.mkdir(parents=True, exist_ok=True)
    (paths.repo_root / ".git").mkdir(exist_ok=True)

    with pytest.raises(RuntimeError, match="reset failed"):
        git_bundle.ensure_git_bundle(
            bundle_id="demo",
            git_url="https://example.com/repo.git",
            git_ref="release-2026.4.02.115",
            bundles_root=tmp_path,
        )


def test_ensure_git_bundle_raises_when_fetch_fails(monkeypatch, tmp_path):
    @contextmanager
    def _fake_redis_lock(*, bundle_id, git_ref):
        del bundle_id, git_ref
        yield

    @contextmanager
    def _fake_bundle_lock(*, bundle_id, git_ref, bundles_root):
        del bundle_id, git_ref, bundles_root
        yield

    def _fake_run_git(args, *, logger=None, env=None):
        del logger, env
        cmd = list(args)
        if cmd[-4:] == ["fetch", "--all", "--tags", "--prune"] or cmd[-5:] == ["fetch", "--all", "--tags", "--prune", "--force"]:
            raise RuntimeError("fetch failed")

    def _fake_subprocess_run(args, check=False, capture_output=False, text=False, env=None):
        del check, capture_output, text, env
        cmd = list(args)
        if cmd[-4:] == ["config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="https://example.com/repo.git\n", stderr="")
        raise AssertionError(f"Unexpected subprocess.run call: {cmd}")

    monkeypatch.setattr(git_bundle, "_redis_bundle_lock", _fake_redis_lock)
    monkeypatch.setattr(git_bundle, "_bundle_lock", _fake_bundle_lock)
    monkeypatch.setattr(git_bundle, "_build_git_env", lambda: {})
    monkeypatch.setattr(git_bundle, "_git_depth", lambda: None)
    monkeypatch.setattr(git_bundle, "_run_git", _fake_run_git)
    monkeypatch.setattr(git_bundle.subprocess, "run", _fake_subprocess_run)
    monkeypatch.setattr(git_bundle, "get_settings", lambda: _settings_for_roots(always_pull=True))

    paths = git_bundle.compute_git_bundle_paths(
        bundle_id="demo",
        git_url="https://example.com/repo.git",
        git_ref="release-2026.4.02.115",
        bundles_root=tmp_path,
    )
    paths.repo_root.mkdir(parents=True, exist_ok=True)
    (paths.repo_root / ".git").mkdir(exist_ok=True)

    with pytest.raises(RuntimeError, match="fetch failed"):
        git_bundle.ensure_git_bundle(
            bundle_id="demo",
            git_url="https://example.com/repo.git",
            git_ref="release-2026.4.02.115",
            bundles_root=tmp_path,
        )
