"""The harness also runs outside a KDCube image; these guard that path.

Both behaviours were defects: a workspace without the setgid bit made every
file the root supervisor wrote unreachable to the host process that started the
turn, and a browsers path pinned only to the image's baked tree sent the
renderer looking inside an empty per-turn cache.
"""
from __future__ import annotations

import os
import pathlib
import stat

import kdcube_ai_app.apps.chat.sdk.runtime.iso_runtime as iso_runtime
from kdcube_ai_app.apps.chat.sdk.runtime.external.docker import (
    _prepare_split_writable_tree,
)


def test_split_preflight_marks_directories_setgid(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "out"
    (root / "logs").mkdir(parents=True)
    (root / "result.json").write_text("{}", encoding="utf-8")

    _prepare_split_writable_tree(root)

    for directory in (root, root / "logs"):
        mode = stat.S_IMODE(directory.stat().st_mode)
        assert mode & stat.S_ISGID, f"{directory} carries no setgid bit"
        assert mode & 0o777 == 0o777
    assert stat.S_IMODE((root / "result.json").stat().st_mode) & 0o666 == 0o666


def test_browsers_path_falls_back_to_the_one_this_process_resolves(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    installed = home / ".cache" / "ms-playwright"
    installed.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(iso_runtime, "BAKED_BROWSERS_PATH", tmp_path / "absent")

    env: dict[str, str] = {}
    iso_runtime._ensure_subprocess_temp_env(env, outdir=tmp_path / "turn")

    assert env["XDG_CACHE_HOME"].startswith(str(tmp_path / "turn"))
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(installed)


def test_browsers_path_prefers_the_image_tree_when_it_exists(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    baked = tmp_path / "opt" / "ms-playwright"
    baked.mkdir(parents=True)
    monkeypatch.setattr(iso_runtime, "BAKED_BROWSERS_PATH", baked)

    env: dict[str, str] = {}
    iso_runtime._ensure_subprocess_temp_env(env, outdir=tmp_path / "turn")

    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(baked)


def test_an_explicit_browsers_path_is_never_overridden(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    monkeypatch.setattr(iso_runtime, "BAKED_BROWSERS_PATH", tmp_path / "absent")
    env = {"PLAYWRIGHT_BROWSERS_PATH": "/somewhere/else"}

    iso_runtime._ensure_subprocess_temp_env(env, outdir=tmp_path / "turn")

    assert env["PLAYWRIGHT_BROWSERS_PATH"] == "/somewhere/else"


def test_no_browsers_path_is_invented_when_nothing_is_installed(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(iso_runtime, "BAKED_BROWSERS_PATH", tmp_path / "absent")

    env: dict[str, str] = {}
    iso_runtime._ensure_subprocess_temp_env(env, outdir=tmp_path / "turn")

    assert "PLAYWRIGHT_BROWSERS_PATH" not in env
