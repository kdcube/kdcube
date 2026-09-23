from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_cli.authority_cutover import (
    AuthorityCutoverRuntime,
    run_authority_command,
)


def _runtime(tmp_path: Path) -> AuthorityCutoverRuntime:
    config = tmp_path / "config"
    config.mkdir()
    env = config / ".env"
    env.write_text("KDCUBE_CONFIG_DIR=/config\n", encoding="utf-8")
    docker = tmp_path / "docker"
    docker.mkdir()
    return AuthorityCutoverRuntime(
        docker_dir=docker,
        config_dir=config,
        env_file=env,
    )


def test_preview_moves_the_container_artifact_to_the_requested_path(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    output = tmp_path / "review" / "preview.json"

    def runner(command, *, cwd):
        assert cwd == runtime.docker_dir
        container_path = command[command.index("--preview-file") + 1]
        staged = runtime.config_dir / Path(container_path).name
        staged.write_text(
            json.dumps({"preview_sha256": "a" * 64}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="previewed\n", stderr="")

    result = run_authority_command(
        argparse.Namespace(
            authority_action="preview",
            generation_id="authority-2026-09-23",
            preview_file=str(output),
            verbose=False,
        ),
        runtime=runtime,
        runner=runner,
    )

    assert result == "previewed\n"
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "preview_sha256": "a" * 64
    }
    assert list(runtime.config_dir.glob(".authority-cutover-*.json")) == []


def test_apply_refuses_while_a_source_writer_is_running(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    preview = tmp_path / "preview.json"
    preview.write_text("{}", encoding="utf-8")

    def runner(command, *, cwd):
        assert command[-4:] == [
            "ps",
            "--services",
            "--filter",
            "status=running",
        ]
        return SimpleNamespace(
            returncode=0,
            stdout="postgres-db\nchat-proc\nredis\n",
            stderr="",
        )

    with pytest.raises(SystemExit, match="chat-proc"):
        run_authority_command(
            argparse.Namespace(
                authority_action="apply",
                preview_file=str(preview),
                confirm_preview_sha256="a" * 64,
                source_quiesced=True,
                stop_writers=False,
                verbose=False,
            ),
            runtime=runtime,
            runner=runner,
        )


def test_apply_uses_a_one_off_container_after_quiescence(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    preview = tmp_path / "preview.json"
    preview.write_text("{}", encoding="utf-8")
    commands: list[list[str]] = []

    def runner(command, *, cwd):
        commands.append(command)
        if "ps" in command:
            return SimpleNamespace(
                returncode=0,
                stdout="postgres-db\nredis\nkdcube-secrets\n",
                stderr="",
            )
        staged_path = command[command.index("--preview-file") + 1]
        assert (runtime.config_dir / Path(staged_path).name).read_text(
            encoding="utf-8"
        ) == "{}"
        return SimpleNamespace(returncode=0, stdout="applied\n", stderr="")

    result = run_authority_command(
        argparse.Namespace(
            authority_action="apply",
            preview_file=str(preview),
            confirm_preview_sha256="a" * 64,
            source_quiesced=True,
            stop_writers=False,
            verbose=False,
        ),
        runtime=runtime,
        runner=runner,
    )

    assert result == "applied\n"
    assert commands[1][4:12] == [
        "run",
        "--rm",
        "--no-deps",
        "chat-proc",
        "python",
        "-m",
        "kdcube_ai_app.ops.authority_cutover.cli",
        "apply",
    ]
    assert list(runtime.config_dir.glob(".authority-cutover-*.json")) == []


def test_apply_can_stop_and_then_verify_writer_services(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    preview = tmp_path / "preview.json"
    preview.write_text("{}", encoding="utf-8")
    commands: list[list[str]] = []

    def runner(command, *, cwd):
        commands.append(command)
        if "stop" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "ps" in command:
            return SimpleNamespace(
                returncode=0,
                stdout="postgres-db\nredis\nkdcube-secrets\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="applied\n", stderr="")

    run_authority_command(
        argparse.Namespace(
            authority_action="apply",
            preview_file=str(preview),
            confirm_preview_sha256="a" * 64,
            source_quiesced=False,
            stop_writers=True,
            verbose=False,
        ),
        runtime=runtime,
        runner=runner,
    )

    assert commands[0][-3:] == ["stop", "chat-ingress", "chat-proc"]
    assert commands[1][-4:] == [
        "ps",
        "--services",
        "--filter",
        "status=running",
    ]
