# SPDX-License-Identifier: MIT
"""`kdcube refresh --build` must not leave the runtime down on a receipt check.

A refresh stopped the stack, built every image, then failed its deployment
source receipt ("no image reference for built service(s): proxylogin") and
ended without starting the stack. Two faults: the receipt read the compose
config without the proxylogin profile, and a receipt failure skipped the start.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from kdcube_cli import cli
from kdcube_cli import deployment_provenance as provenance


def _profiled_compose_config(**kwargs):
    # Like `docker compose config`: a service behind a profile is left out
    # unless that profile is enabled.
    services = {
        name: {"build": ".", "image": f"kdcube-{name}:latest"}
        for name in (
            "chat-ingress", "chat-proc", "metrics", "web-proxy",
            "postgres-setup", "kdcube-secrets", "web-ui",
        )
    }
    if "proxylogin" in tuple(kwargs.get("profile_args") or ()):
        services["proxylogin"] = {"build": ".", "image": "proxylogin:latest"}
    return {"services": services}


def test_the_build_receipt_reads_the_proxylogin_profile(monkeypatch, tmp_path: Path) -> None:
    docker_dir = tmp_path / "custom-ui-managed-infra"
    docker_dir.mkdir()
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        cli, "_build_paths_for_repo",
        lambda repo_root, workdir: SimpleNamespace(docker_dir=docker_dir, config_dir=tmp_path),
    )
    monkeypatch.setattr(cli, "_ensure_docker_responsive", lambda: None)
    monkeypatch.setattr(cli.installer_mod, "load_env_file", lambda path: SimpleNamespace(entries={}))
    monkeypatch.setattr(cli.installer_mod, "missing_build_keys", lambda env: [])
    monkeypatch.setattr(cli, "_maintain_docker_build_storage", lambda console, phase: None)
    built: list[list[str]] = []
    monkeypatch.setattr(cli, "_run_compose", lambda console, command, cwd: built.append(list(command)))
    monkeypatch.setattr(provenance, "_compose_configuration", _profiled_compose_config)
    monkeypatch.setattr(provenance, "platform_source_identity", lambda _root: {"mode": "git", "version": "git:abc"})
    monkeypatch.setattr(
        provenance, "_inspect_image_id",
        lambda reference, strict=True: "sha256:" + reference.split(":")[0],
    )

    cli.build_compose_images(Console(quiet=True), repo_root=tmp_path, workdir=tmp_path)

    assert "proxylogin" in built[0]
    receipt = provenance.load_image_receipts(tmp_path)
    assert "proxylogin" in receipt["latest_services"]


def test_a_receipt_failure_still_starts_the_stack_and_exits_non_zero(tmp_path: Path) -> None:
    started: list[bool] = []

    def build() -> None:
        raise cli.ImageReceiptError(
            "Docker images were built, but their deployment source receipt failed: boom"
        )

    with pytest.raises(SystemExit) as raised:
        cli._build_and_restart(
            Console(quiet=True), build=build, start=lambda: started.append(True), workdir=tmp_path,
        )

    assert started == [True]
    assert "receipt failed: boom" in str(raised.value)
    assert "The stack was started on the new images." in str(raised.value)


def test_a_receipt_failure_with_no_restart_says_the_stack_was_not_started(tmp_path: Path) -> None:
    def build() -> None:
        raise cli.ImageReceiptError("receipt failed")

    with pytest.raises(SystemExit) as raised:
        cli._build_and_restart(Console(quiet=True), build=build, start=None, workdir=tmp_path)

    assert "not started (--no-restart)" in str(raised.value)


def test_a_failed_build_is_not_started_over(tmp_path: Path) -> None:
    # Only a receipt failure after a good build restarts; a build that failed
    # raises as before.
    started: list[bool] = []

    def build() -> None:
        raise SystemExit("Command failed: docker compose build")

    with pytest.raises(SystemExit, match="docker compose build"):
        cli._build_and_restart(
            Console(quiet=True), build=build, start=lambda: started.append(True), workdir=tmp_path,
        )
    assert started == []


def test_a_clean_refresh_builds_then_starts(tmp_path: Path) -> None:
    steps: list[str] = []
    cli._build_and_restart(
        Console(quiet=True),
        build=lambda: steps.append("build"),
        start=lambda: steps.append("start"),
        workdir=tmp_path,
    )
    assert steps == ["build", "start"]


def test_a_failed_start_after_a_receipt_failure_names_both(tmp_path: Path) -> None:
    def build() -> None:
        raise cli.ImageReceiptError("receipt failed: boom")

    def start() -> None:
        raise SystemExit("compose up failed")

    with pytest.raises(SystemExit) as raised:
        cli._build_and_restart(Console(quiet=True), build=build, start=start, workdir=tmp_path)

    assert "receipt failed: boom" in str(raised.value)
    assert "compose up failed" in str(raised.value)
