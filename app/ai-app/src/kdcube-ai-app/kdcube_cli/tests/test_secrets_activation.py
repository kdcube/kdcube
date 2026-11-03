from __future__ import annotations

import json
import subprocess
from pathlib import Path

import kdcube_cli.secrets_activation as activation_module
import pytest
import yaml
from kdcube_cli.host_vault import HOST_VAULT_ACTIVATION_MARKER
from kdcube_cli.secrets_activation import (
    ComposeHostVaultActivationRuntime,
    HostVaultActivationError,
    activate_host_vault,
    recover_host_vault_activation,
)
from kdcube_cli.secrets_migration import HostVaultStageError, VerificationState


class _MemoryDestination:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)

    def verify(self, key: str, value: str) -> VerificationState:
        if key not in self.values:
            return VerificationState.MISSING
        if self.values[key] == value:
            return VerificationState.MATCH
        return VerificationState.DIFFERENT

    def create(self, key: str, value: str) -> None:
        raise HostVaultStageError("activation must not write")


class _Runtime:
    def __init__(
        self,
        config_dir: Path,
        *,
        fail_active_verify: bool = False,
        fail_shadow_recreate: bool = False,
        fail_all_recreate: bool = False,
        interrupt_active_recreate: bool = False,
        mutate_source_after_verify: bool = False,
    ) -> None:
        self.config_dir = config_dir
        self.fail_active_verify = fail_active_verify
        self.fail_shadow_recreate = fail_shadow_recreate
        self.fail_all_recreate = fail_all_recreate
        self.interrupt_active_recreate = interrupt_active_recreate
        self.mutate_source_after_verify = mutate_source_after_verify
        self.events: list[str] = []

    def _assembly(self) -> dict:
        return yaml.safe_load((self.config_dir / "assembly.yaml").read_text())

    def require_running(self) -> None:
        self.events.append("require_running")

    def quiesce_consumers(self) -> None:
        self.events.append("quiesce")

    def recreate_secret_path(self) -> None:
        assembly = self._assembly()
        provider = assembly["secrets"]["provider"]
        backend = assembly["secrets"]["service"]["backend"]
        self.events.append(f"recreate:{provider}:{backend}")
        if self.fail_all_recreate:
            raise HostVaultActivationError(
                "fixed test failure",
                code="broker_recreate_failed",
            )
        if self.interrupt_active_recreate and provider == "secrets-service":
            raise KeyboardInterrupt
        if (
            self.fail_shadow_recreate
            and provider == "secrets-file"
            and backend == "host-vault"
        ):
            raise HostVaultActivationError(
                "fixed test failure",
                code="broker_recreate_failed",
            )

    def verify_consumer(self, service: str, *, key: str, digest: str) -> None:
        provider = self._assembly()["secrets"]["provider"]
        self.events.append(f"verify:{provider}:{service}")
        assert key == "platform.services.fixture.token"
        assert len(digest) == 64
        if self.fail_active_verify and provider == "secrets-service":
            raise HostVaultActivationError(
                "fixed test failure",
                code="consumer_verification_failed",
            )
        if self.mutate_source_after_verify and service == "chat-proc":
            path = self.config_dir / "secrets.yaml"
            path.write_text(
                "platform:\n  services:\n    fixture:\n      token: changed\n",
                encoding="utf-8",
            )
            path.chmod(0o600)


def _write_runtime(config_dir: Path) -> dict[str, bytes]:
    config_dir.mkdir()
    (config_dir / "assembly.yaml").write_text(
        """
context:
  tenant: test-tenant
  project: test-project
platform:
  services:
    proc:
      exec:
        py_code_exec_network_mode: auto
secrets:
  provider: secrets-file
  service:
    backend: host-vault
""".lstrip(),
        encoding="utf-8",
    )
    (config_dir / ".env").write_text(
        "KDCUBE_SECRETS_SERVICE_BACKEND=host-vault\nBASE=kept\n",
        encoding="utf-8",
    )
    for name in (".env.ingress", ".env.proc"):
        (config_dir / name).write_text("GATEWAY_COMPONENT=test\n", encoding="utf-8")
    secrets = config_dir / "secrets.yaml"
    secrets.write_text(
        "platform:\n  services:\n    fixture:\n      token: activation-canary\n",
        encoding="utf-8",
    )
    secrets.chmod(0o600)
    bundle_secrets = config_dir / "bundles.secrets.yaml"
    bundle_secrets.write_text("bundles:\n  items: []\n", encoding="utf-8")
    bundle_secrets.chmod(0o600)
    return {
        name: (config_dir / name).read_bytes()
        for name in ("assembly.yaml", ".env", ".env.ingress", ".env.proc")
    }


def _destination() -> _MemoryDestination:
    return _MemoryDestination(
        {"platform.services.fixture.token": "activation-canary"}
    )


def test_activation_dry_run_checks_parity_without_quiescing(tmp_path: Path):
    config_dir = tmp_path / "config"
    before = _write_runtime(config_dir)
    runtime = _Runtime(config_dir)

    result = activate_host_vault(
        config_dir=config_dir,
        destination=_destination(),
        runtime=runtime,
        dry_run=True,
    )

    assert result.to_dict()["status"] == "ready"
    assert result.activated is False
    assert runtime.events == ["require_running"]
    assert {name: (config_dir / name).read_bytes() for name in before} == before


def test_activation_refuses_incomplete_stage_before_quiescing(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_runtime(config_dir)
    runtime = _Runtime(config_dir)

    with pytest.raises(HostVaultActivationError) as captured:
        activate_host_vault(
            config_dir=config_dir,
            destination=_MemoryDestination({}),
            runtime=runtime,
            dry_run=False,
        )

    assert captured.value.code == "destination_incomplete"
    assert runtime.events == ["require_running"]


def test_activation_switches_consumers_and_retains_plaintext_source(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_runtime(config_dir)
    runtime = _Runtime(config_dir)

    result = activate_host_vault(
        config_dir=config_dir,
        destination=_destination(),
        runtime=runtime,
        dry_run=False,
    )

    assembly = yaml.safe_load((config_dir / "assembly.yaml").read_text())
    assert assembly["secrets"]["provider"] == "secrets-service"
    assert assembly["secrets"]["url"] == "http://kdcube-secrets:7777"
    for name in (".env.ingress", ".env.proc"):
        text = (config_dir / name).read_text()
        assert "SECRETS_PROVIDER=" not in text
        assert "SECRETS_URL=" not in text
    assert (config_dir / "secrets.yaml").exists()
    assert not (config_dir / HOST_VAULT_ACTIVATION_MARKER).exists()
    assert result.to_dict()["plaintext_source_retained"] is True
    assert runtime.events == [
        "require_running",
        "quiesce",
        "recreate:secrets-service:host-vault",
        "verify:secrets-service:chat-ingress",
        "verify:secrets-service:chat-proc",
    ]


def test_activation_failure_restores_exact_shadow_configuration(tmp_path: Path):
    config_dir = tmp_path / "config"
    before = _write_runtime(config_dir)
    runtime = _Runtime(config_dir, fail_active_verify=True)

    with pytest.raises(HostVaultActivationError) as captured:
        activate_host_vault(
            config_dir=config_dir,
            destination=_destination(),
            runtime=runtime,
            dry_run=False,
        )

    assert captured.value.rollback == "restored"
    assert {name: (config_dir / name).read_bytes() for name in before} == before
    assert "recreate:secrets-file:host-vault" in runtime.events
    assert "verify:secrets-file:chat-proc" in runtime.events
    assert not (config_dir / HOST_VAULT_ACTIVATION_MARKER).exists()


def test_activation_uses_ephemeral_file_recovery_if_shadow_restart_fails(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_runtime(config_dir)
    runtime = _Runtime(
        config_dir,
        fail_active_verify=True,
        fail_shadow_recreate=True,
    )

    with pytest.raises(HostVaultActivationError) as captured:
        activate_host_vault(
            config_dir=config_dir,
            destination=_destination(),
            runtime=runtime,
            dry_run=False,
        )

    assert captured.value.rollback == "secrets-file-ephemeral"
    assembly = yaml.safe_load((config_dir / "assembly.yaml").read_text())
    assert assembly["secrets"] == {
        "provider": "secrets-file",
        "service": {"backend": "ephemeral"},
    }
    assert "KDCUBE_SECRETS_SERVICE_BACKEND=ephemeral" in (
        config_dir / ".env"
    ).read_text()
    assert "recreate:secrets-file:ephemeral" in runtime.events
    assert not (config_dir / HOST_VAULT_ACTIVATION_MARKER).exists()


def test_source_change_during_activation_rolls_back(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_runtime(config_dir)
    runtime = _Runtime(config_dir, mutate_source_after_verify=True)

    with pytest.raises(HostVaultActivationError) as captured:
        activate_host_vault(
            config_dir=config_dir,
            destination=_destination(),
            runtime=runtime,
            dry_run=False,
        )

    assert captured.value.rollback == "restored"
    assert yaml.safe_load((config_dir / "assembly.yaml").read_text())["secrets"][
        "provider"
    ] == "secrets-file"


def test_interrupted_activation_leaves_secret_free_marker_and_recovery_is_repeatable(
    tmp_path: Path,
):
    config_dir = tmp_path / "config"
    _write_runtime(config_dir)
    interrupted_runtime = _Runtime(config_dir, interrupt_active_recreate=True)

    with pytest.raises(KeyboardInterrupt):
        activate_host_vault(
            config_dir=config_dir,
            destination=_destination(),
            runtime=interrupted_runtime,
            dry_run=False,
        )

    marker = config_dir / HOST_VAULT_ACTIVATION_MARKER
    marker_text = marker.read_text(encoding="utf-8")
    assert marker.stat().st_mode & 0o777 == 0o600
    assert "runtime_recreated" not in marker_text
    assert "configured" in marker_text
    assert "activation-canary" not in marker_text
    assert "platform.services.fixture.token" not in marker_text

    recovery_runtime = _Runtime(config_dir)
    result = recover_host_vault_activation(
        config_dir=config_dir,
        runtime=recovery_runtime,
    )

    assert result.recovered is True
    assert result.provider_after == "secrets-file"
    assert result.verified_consumers == ("chat-ingress", "chat-proc")
    assert not marker.exists()
    assembly = yaml.safe_load((config_dir / "assembly.yaml").read_text())
    assert assembly["secrets"] == {
        "provider": "secrets-file",
        "url": "http://kdcube-secrets:7777",
        "service": {"backend": "ephemeral"},
    }
    assert recovery_runtime.events == [
        "recreate:secrets-file:ephemeral",
        "verify:secrets-file:chat-ingress",
        "verify:secrets-file:chat-proc",
    ]

    repeated = recover_host_vault_activation(
        config_dir=config_dir,
        runtime=recovery_runtime,
    )
    assert repeated.recovered is False


def test_failed_interrupted_activation_recovery_keeps_marker(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_runtime(config_dir)
    interrupted_runtime = _Runtime(config_dir, interrupt_active_recreate=True)
    with pytest.raises(KeyboardInterrupt):
        activate_host_vault(
            config_dir=config_dir,
            destination=_destination(),
            runtime=interrupted_runtime,
            dry_run=False,
        )

    with pytest.raises(HostVaultActivationError) as captured:
        recover_host_vault_activation(
            config_dir=config_dir,
            runtime=_Runtime(config_dir, fail_all_recreate=True),
        )

    assert captured.value.code == "activation_recovery_failed"
    assert captured.value.rollback == "failed"
    assert (config_dir / HOST_VAULT_ACTIVATION_MARKER).exists()


@pytest.mark.parametrize("phase", ["quiesced", "configured", "runtime_recreated"])
def test_each_durable_activation_phase_recovers_to_verified_file_provider(
    monkeypatch,
    tmp_path: Path,
    phase: str,
):
    config_dir = tmp_path / "config"
    _write_runtime(config_dir)
    original = activation_module._set_activation_phase

    def interrupt_after_phase(candidate: Path, current_phase: str) -> None:
        original(candidate, current_phase)
        if current_phase == phase:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        activation_module,
        "_set_activation_phase",
        interrupt_after_phase,
    )
    with pytest.raises(KeyboardInterrupt):
        activate_host_vault(
            config_dir=config_dir,
            destination=_destination(),
            runtime=_Runtime(config_dir),
            dry_run=False,
        )

    marker = config_dir / HOST_VAULT_ACTIVATION_MARKER
    assert json.loads(marker.read_text(encoding="utf-8"))["phase"] == phase
    monkeypatch.setattr(activation_module, "_set_activation_phase", original)

    recovered = recover_host_vault_activation(
        config_dir=config_dir,
        runtime=_Runtime(config_dir),
    )
    assert recovered.recovered is True
    assert not marker.exists()
    assembly = yaml.safe_load((config_dir / "assembly.yaml").read_text())
    assert assembly["secrets"]["provider"] == "secrets-file"
    assert assembly["secrets"]["service"]["backend"] == "ephemeral"


def test_recovery_rejects_symlink_marker_without_touching_target(tmp_path: Path):
    config_dir = tmp_path / "config"
    _write_runtime(config_dir)
    target = tmp_path / "marker-target"
    target.write_text("do-not-touch\n", encoding="utf-8")
    marker = config_dir / HOST_VAULT_ACTIVATION_MARKER
    try:
        marker.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    runtime = _Runtime(config_dir)
    with pytest.raises(HostVaultActivationError) as captured:
        recover_host_vault_activation(config_dir=config_dir, runtime=runtime)

    assert captured.value.code == "activation_marker_invalid"
    assert runtime.events == []
    assert target.read_text(encoding="utf-8") == "do-not-touch\n"


def test_compose_runtime_uses_one_token_overlay_and_secret_safe_probe(
    monkeypatch,
    tmp_path: Path,
):
    env_file = tmp_path / ".env"
    env_file.write_text("BASE=1\n", encoding="utf-8")
    (tmp_path / "assembly.yaml").write_text("secrets: {provider: secrets-service, url: 'http://kdcube-secrets:7777'}\n")
    docker_dir = tmp_path / "docker"
    docker_dir.mkdir()
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs))
        if "ps" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="kdcube-secrets\nchat-ingress\nchat-proc\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runtime = ComposeHostVaultActivationRuntime(
        docker_dir=docker_dir,
        env_file=env_file,
        timeout_seconds=1,
        poll_seconds=0,
    )

    runtime.require_running()
    runtime.quiesce_consumers()
    runtime.recreate_secret_path()
    runtime.verify_consumer(
        "chat-proc",
        key="platform.services.fixture.token",
        digest="a" * 64,
    )

    commands = [command for command, _kwargs in calls]
    broker_up = next(command for command in commands if command[-1:] == ["kdcube-secrets"] and "up" in command)
    consumer_up = next(
        command
        for command in commands
        if command[-2:] == ["chat-ingress", "chat-proc"] and "up" in command
    )
    assert broker_up[3] == consumer_up[3]
    assert not Path(broker_up[3]).exists()
    probe_command, probe_kwargs = next(
        (command, kwargs)
        for command, kwargs in calls
        if command[-3:-1] == ["python", "-c"] and command[-4] == "chat-proc"
    )
    assert "platform.services.fixture.token" not in probe_command
    payload = json.loads(str(probe_kwargs["input"]))
    assert payload == {
        "key": "platform.services.fixture.token",
        "digest": "a" * 64,
    }


def test_compose_runtime_rejects_unknown_consumer_without_dispatch(tmp_path: Path):
    runtime = ComposeHostVaultActivationRuntime(
        docker_dir=tmp_path,
        env_file=tmp_path / ".env",
    )

    with pytest.raises(HostVaultActivationError) as captured:
        runtime.verify_consumer("unknown", key="key", digest="a" * 64)

    assert captured.value.code == "invalid_consumer_probe"
