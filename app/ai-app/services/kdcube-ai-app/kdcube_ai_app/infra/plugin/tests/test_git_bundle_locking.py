# SPDX-License-Identifier: MIT

from __future__ import annotations

from contextlib import contextmanager

from kdcube_ai_app.infra.plugin import git_bundle


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
    monkeypatch.setattr(git_bundle, "get_secret", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(git_bundle, "_run_git", _fake_run_git)

    paths = git_bundle.ensure_git_bundle(
        bundle_id="demo",
        git_url="https://example.com/repo.git",
        bundles_root=tmp_path,
    )

    assert state["run_git_calls"] == 1
    assert paths.repo_root.exists()
