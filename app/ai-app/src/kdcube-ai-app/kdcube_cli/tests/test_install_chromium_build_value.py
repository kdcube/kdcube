# SPDX-License-Identifier: MIT
"""assembly.yaml platform.rendering.browser.local_chromium selects the image build.

The CLI renders it into the compose env as KDCUBE_INSTALL_CHROMIUM (compose
passes it to the processor build) and passes it to the executor image build.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from kdcube_cli import cli
from kdcube_cli.installer import EnvFile, exec_image_build_args, install_chromium_build_value, parse_env


def _assembly(value):
    return {"platform": {"rendering": {"browser": {"local_chromium": value}}}}


@pytest.mark.parametrize(
    ("assembly", "expected"),
    [
        (None, "1"),
        ({}, "1"),
        (_assembly(True), "1"),
        (_assembly("yes"), "1"),
        (_assembly("not-a-bool"), "1"),
        (_assembly(False), "0"),
        (_assembly("false"), "0"),
        (_assembly(0), "0"),
    ],
)
def test_build_value_follows_the_descriptor(assembly, expected):
    assert install_chromium_build_value(assembly) == expected


def _env(lines: list[str]) -> EnvFile:
    return EnvFile(path=Path("/dev/null"), lines=lines, entries=parse_env(lines))


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ([], "INSTALL_CHROMIUM=1"),
        (["KDCUBE_INSTALL_CHROMIUM=1"], "INSTALL_CHROMIUM=1"),
        (["KDCUBE_INSTALL_CHROMIUM=0"], "INSTALL_CHROMIUM=0"),
        (["KDCUBE_INSTALL_CHROMIUM=garbage"], "INSTALL_CHROMIUM=1"),
    ],
)
def test_exec_build_args_read_the_rendered_env(lines, expected):
    assert exec_image_build_args(_env(lines)) == ["--build-arg", expected]


def test_build_compose_images_passes_the_value_to_the_exec_image(monkeypatch, tmp_path: Path):
    docker_dir = tmp_path / "all_in_one_kdcube"
    docker_dir.mkdir()
    (tmp_path / ".env").write_text("KDCUBE_INSTALL_CHROMIUM=0\n", encoding="utf-8")
    monkeypatch.setattr(
        cli, "_build_paths_for_repo",
        lambda repo_root, workdir: SimpleNamespace(docker_dir=docker_dir, config_dir=tmp_path),
    )
    monkeypatch.setattr(cli, "_ensure_docker_responsive", lambda: None)
    monkeypatch.setattr(cli.installer_mod, "missing_build_keys", lambda env: [])
    monkeypatch.setattr(cli, "_maintain_docker_build_storage", lambda console, phase: None)
    monkeypatch.setattr(cli, "record_compose_image_receipts", lambda **kwargs: None)
    built: list[list[str]] = []
    monkeypatch.setattr(cli, "_run_compose", lambda console, command, cwd: built.append(list(command)))

    cli.build_compose_images(Console(quiet=True), repo_root=tmp_path, workdir=tmp_path)

    exec_build = next(command for command in built if "Dockerfile_Exec" in command)
    assert exec_build[exec_build.index("--build-arg") + 1] == "INSTALL_CHROMIUM=0"
