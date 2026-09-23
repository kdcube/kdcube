from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_cli import authority_cutover
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


def _secret_environment(_runtime, *, runner):
    del runner
    return {
        "SECRETS_TOKEN": "read-token",
        "SECRETS_ADMIN_TOKEN": "admin-token",
    }


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

    def runner(command, *, cwd, env=None):
        del env
        if "preflight-target" in command:
            return SimpleNamespace(returncode=0, stdout='{"ok": true}\n', stderr="")
        assert command[-4:] == ["ps", "--services", "--filter", "status=running"]
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
            environment_loader=_secret_environment,
        )


def test_apply_uses_a_one_off_container_after_quiescence(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    preview = tmp_path / "preview.json"
    preview.write_text("{}", encoding="utf-8")
    commands: list[list[str]] = []

    def runner(command, *, cwd, env=None):
        del env
        commands.append(command)
        if "preflight-target" in command:
            return SimpleNamespace(returncode=0, stdout='{"ok": true}\n', stderr="")
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
        environment_loader=_secret_environment,
    )

    assert result == "applied\n"
    assert commands[2][4:16] == [
        "run",
        "--rm",
        "--no-deps",
        "--env",
        "SECRETS_TOKEN",
        "--env",
        "SECRETS_ADMIN_TOKEN",
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

    def runner(command, *, cwd, env=None):
        del env
        commands.append(command)
        if "preflight-target" in command:
            return SimpleNamespace(returncode=0, stdout='{"ok": true}\n', stderr="")
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
        environment_loader=_secret_environment,
    )

    assert "preflight-target" in commands[0]
    assert commands[1][-3:] == ["stop", "chat-ingress", "chat-proc"]
    assert commands[2][-4:] == [
        "ps",
        "--services",
        "--filter",
        "status=running",
    ]


def test_apply_target_preflight_failure_leaves_writers_running(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    preview = tmp_path / "preview.json"
    preview.write_text("{}", encoding="utf-8")
    commands: list[list[str]] = []

    def runner(command, *, cwd, env=None):
        del env
        commands.append(command)
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="resident secret destination is not writable",
        )

    with pytest.raises(SystemExit, match="target preflight failed"):
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
            environment_loader=_secret_environment,
        )

    assert len(commands) == 1
    assert "preflight-target" in commands[0]
    assert "stop" not in commands[0]
    assert list(runtime.config_dir.glob(".authority-cutover-*.json")) == []


def test_preflight_opens_target_without_writer_commands(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    preview = tmp_path / "preview.json"
    preview.write_text("{}", encoding="utf-8")
    commands: list[list[str]] = []

    def runner(command, *, cwd, env=None):
        assert cwd == runtime.docker_dir
        commands.append(command)
        assert env is not None
        assert env["SECRETS_TOKEN"] == "read-token"
        assert env["SECRETS_ADMIN_TOKEN"] == "admin-token"
        return SimpleNamespace(returncode=0, stdout='{"ok": true}\n', stderr="")

    result = run_authority_command(
        argparse.Namespace(
            authority_action="preflight",
            preview_file=str(preview),
            confirm_preview_sha256="a" * 64,
            verbose=False,
        ),
        runtime=runtime,
        runner=runner,
        environment_loader=_secret_environment,
    )

    assert result == '{"ok": true}\n'
    assert len(commands) == 1
    assert "preflight-target" in commands[0]
    assert "stop" not in commands[0]
    assert list(runtime.config_dir.glob(".authority-cutover-*.json")) == []


def test_runtime_secret_environment_forwards_only_existing_service_gates(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    commands: list[list[str]] = []

    def runner(command, *, cwd):
        assert cwd == runtime.docker_dir
        commands.append(command)
        if command[-4:] == ["ps", "--all", "--quiet", "kdcube-secrets"]:
            return SimpleNamespace(returncode=0, stdout="broker-id\n", stderr="")
        if command[-4:] == ["ps", "--all", "--quiet", "chat-proc"]:
            return SimpleNamespace(returncode=0, stdout="proc-id\n", stderr="")
        if command[-1] == "broker-id":
            return SimpleNamespace(
                returncode=0,
                stdout="UNRELATED=value\nSECRETS_ADMIN_TOKEN=admin-token\n",
                stderr="",
            )
        if command[-1] == "proc-id":
            return SimpleNamespace(
                returncode=0,
                stdout="SECRETS_TOKEN=read-token\nOTHER_SECRET=do-not-forward\n",
                stderr="",
            )
        raise AssertionError(command)

    environment = authority_cutover._cutover_process_environment(
        runtime,
        runner=runner,
    )

    assert environment["SECRETS_ADMIN_TOKEN"] == "admin-token"
    assert environment["SECRETS_TOKEN"] == "read-token"
    assert "OTHER_SECRET" not in environment
