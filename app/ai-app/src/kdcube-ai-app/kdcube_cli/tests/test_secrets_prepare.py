from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from kdcube_cli.host_vault import HostVaultRuntimeConfig
from kdcube_cli.secrets_prepare import (
    ComposeHostVaultPrepareRuntime,
    HostVaultPrepareError,
    prepare_host_vault_shadow,
)


class _Runtime:
    def __init__(self, *, fail_recreates: int = 0) -> None:
        self.fail_recreates = fail_recreates
        self.events: list[str] = []

    def require_running(self) -> None:
        self.events.append("require_running")

    def recreate_broker(self) -> None:
        self.events.append("recreate_broker")
        if self.fail_recreates:
            self.fail_recreates -= 1
            raise HostVaultPrepareError(
                "fixed test failure",
                code="broker_recreate_failed",
            )


def _config(tmp_path: Path) -> HostVaultRuntimeConfig:
    return HostVaultRuntimeConfig(
        provider="secrets-file",
        backend="host-vault",
        tenant="test-tenant",
        project="test-project",
        address="host.docker.internal:7781",
        server_name="host.docker.internal",
        identity_dir=tmp_path / "identity",
        exec_network_mode="auto",
    )


def _write_env(config_dir: Path) -> bytes:
    config_dir.mkdir()
    payload = b"BASE=kept\nKDCUBE_SECRETS_SERVICE_BACKEND=ephemeral\n"
    path = config_dir / ".env"
    path.write_bytes(payload)
    path.chmod(0o640)
    return payload


def test_prepare_dry_run_changes_nothing(tmp_path: Path):
    config_dir = tmp_path / "config"
    before = _write_env(config_dir)
    runtime = _Runtime()

    result = prepare_host_vault_shadow(
        config_dir=config_dir,
        config=_config(tmp_path),
        runtime=runtime,
        dry_run=True,
    )

    assert result.config_changed is True
    assert result.broker_recreated is False
    assert (config_dir / ".env").read_bytes() == before
    assert runtime.events == ["require_running"]
    assert result.to_dict() == {
        "schema": "kdcube_cli.host_vault_prepare.v1",
        "status": "ready",
        "dry_run": True,
        "config_changed": True,
        "broker_recreated": False,
        "source_provider": "secrets-file",
        "destination_backend": "host-vault",
        "provider_changed": False,
        "source_deleted": False,
    }


def test_prepare_projects_only_broker_configuration(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_env(config_dir)
    runtime = _Runtime()
    config = _config(tmp_path)

    result = prepare_host_vault_shadow(
        config_dir=config_dir,
        config=config,
        runtime=runtime,
        dry_run=False,
    )

    text = (config_dir / ".env").read_text(encoding="utf-8")
    assert "BASE=kept" in text
    assert "KDCUBE_SECRETS_SERVICE_BACKEND=host-vault" in text
    assert "KDCUBE_HOST_VAULT_ADDR=host.docker.internal:7781" in text
    assert "KDCUBE_HOST_VAULT_SERVER_NAME=host.docker.internal" in text
    assert "KDCUBE_SECRETS_TENANT=test-tenant" in text
    assert "KDCUBE_SECRETS_PROJECT=test-project" in text
    assert f"HOST_KDCUBE_HOST_VAULT_CLIENT_KEY_PATH={config.identity_dir}/host-vault-client.key" in text
    assert (config_dir / ".env").stat().st_mode & 0o777 == 0o640
    assert runtime.events == ["require_running", "recreate_broker"]
    assert result.config_changed is True
    assert result.broker_recreated is True


def test_prepare_failure_restores_exact_environment_and_previous_broker(tmp_path: Path):
    config_dir = tmp_path / "config"
    before = _write_env(config_dir)
    runtime = _Runtime(fail_recreates=1)

    with pytest.raises(HostVaultPrepareError) as captured:
        prepare_host_vault_shadow(
            config_dir=config_dir,
            config=_config(tmp_path),
            runtime=runtime,
            dry_run=False,
        )

    assert captured.value.code == "broker_recreate_failed"
    assert captured.value.rollback == "restored"
    assert (config_dir / ".env").read_bytes() == before
    assert runtime.events == [
        "require_running",
        "recreate_broker",
        "recreate_broker",
    ]


def test_prepare_rejects_non_shadow_provider_before_runtime_action(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_env(config_dir)
    runtime = _Runtime()
    config = replace(_config(tmp_path), provider="secrets-service")

    with pytest.raises(HostVaultPrepareError) as captured:
        prepare_host_vault_shadow(
            config_dir=config_dir,
            config=config,
            runtime=runtime,
            dry_run=False,
        )

    assert captured.value.code == "invalid_shadow_configuration"
    assert runtime.events == []


def test_prepare_rejects_symlink_environment(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    target = tmp_path / "target.env"
    target.write_text("BASE=untouched\n", encoding="utf-8")
    try:
        (config_dir / ".env").symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(HostVaultPrepareError) as captured:
        prepare_host_vault_shadow(
            config_dir=config_dir,
            config=_config(tmp_path),
            runtime=_Runtime(),
            dry_run=False,
        )

    assert captured.value.code == "invalid_runtime_configuration"
    assert target.read_text(encoding="utf-8") == "BASE=untouched\n"


def test_compose_prepare_recreates_only_broker_with_transient_token_overlay(
    monkeypatch,
    tmp_path: Path,
):
    env_file = tmp_path / ".env"
    env_file.write_text("BASE=1\n", encoding="utf-8")
    (tmp_path / "assembly.yaml").write_text("secrets: {provider: secrets-file}\n")
    docker_dir = tmp_path / "docker"
    docker_dir.mkdir()
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        if "ps" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="kdcube-secrets\nchat-ingress\nchat-proc\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runtime = ComposeHostVaultPrepareRuntime(
        docker_dir=docker_dir,
        env_file=env_file,
        timeout_seconds=1,
        poll_seconds=0,
    )

    runtime.require_running()
    runtime.recreate_broker()

    broker_up = next(command for command in calls if "up" in command)
    assert broker_up[-1] == "kdcube-secrets"
    assert "chat-ingress" not in broker_up
    assert "chat-proc" not in broker_up
    overlay = Path(broker_up[3])
    assert overlay != env_file
    assert not overlay.exists()
