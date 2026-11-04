import os
from pathlib import Path

import pytest
from kdcube_cli import installer as installer_module
from kdcube_cli.host_vault import (
    DEPLOYMENT_SECRET_APPLICATION,
    HOST_VAULT_ACTIVATION_MARKER,
    HostVaultConfigurationError,
    compose_environment,
    config_from_assembly,
    validate_assembly_for_start,
)
from kdcube_cli.installer import PathsContext


def _assembly(identity_dir: Path, *, provider: str = "secrets-service") -> dict:
    return {
        "context": {"tenant": "demo-tenant", "project": "demo-project"},
        "platform": {
            "services": {"proc": {"exec": {"py_code_exec_network_mode": "auto"}}}
        },
        "secrets": {
            "provider": provider,
            "service": {
                "backend": "host-vault",
                "host_vault": {
                    "address": "host.docker.internal:7781",
                    "server_name": "host.docker.internal",
                    "identity_dir": str(identity_dir),
                },
            },
        },
    }


def _write_identity(identity_dir: Path) -> None:
    identity_dir.mkdir(parents=True)
    for name in (
        "host-vault-client.crt",
        "host-vault-client.key",
        "host-vault-ca.crt",
    ):
        path = identity_dir / name
        path.write_text(f"fixture-{name}", encoding="utf-8")
        path.chmod(0o400 if name.endswith(".key") else 0o644)


def test_default_backend_keeps_host_vault_disabled():
    config = config_from_assembly(
        {
            "context": {"tenant": "demo-tenant", "project": "demo-project"},
            "secrets": {"provider": "secrets-file"},
        }
    )

    assert config.backend == "ephemeral"
    assert config.enabled is False
    assert compose_environment(config) == {
        "KDCUBE_SECRETS_SERVICE_BACKEND": "ephemeral",
        "KDCUBE_HOST_VAULT_ADDR": "",
        "KDCUBE_HOST_VAULT_SERVER_NAME": "",
        "KDCUBE_SECRETS_TENANT": "",
        "KDCUBE_SECRETS_PROJECT": "",
        "HOST_KDCUBE_HOST_VAULT_CLIENT_CERT_PATH": "",
        "HOST_KDCUBE_HOST_VAULT_CLIENT_KEY_PATH": "",
        "HOST_KDCUBE_HOST_VAULT_CA_PATH": "",
    }


def test_host_vault_descriptor_maps_only_non_secret_compose_inputs(tmp_path):
    identity_dir = tmp_path / "deployment-identity"
    config = config_from_assembly(_assembly(identity_dir))

    assert DEPLOYMENT_SECRET_APPLICATION == "kdcube-runtime"
    assert compose_environment(config) == {
        "KDCUBE_SECRETS_SERVICE_BACKEND": "host-vault",
        "KDCUBE_HOST_VAULT_ADDR": "host.docker.internal:7781",
        "KDCUBE_HOST_VAULT_SERVER_NAME": "host.docker.internal",
        "KDCUBE_SECRETS_TENANT": "demo-tenant",
        "KDCUBE_SECRETS_PROJECT": "demo-project",
        "HOST_KDCUBE_HOST_VAULT_CLIENT_CERT_PATH": str(
            identity_dir / "host-vault-client.crt"
        ),
        "HOST_KDCUBE_HOST_VAULT_CLIENT_KEY_PATH": str(
            identity_dir / "host-vault-client.key"
        ),
        "HOST_KDCUBE_HOST_VAULT_CA_PATH": str(identity_dir / "host-vault-ca.crt"),
    }


def test_host_vault_accepts_secrets_file_for_shadow_staging(tmp_path):
    config = config_from_assembly(
        _assembly(tmp_path / "identity", provider="secrets-file")
    )

    assert config.enabled is True
    assert config.provider == "secrets-file"


def test_host_vault_rejects_unrelated_provider(tmp_path):
    with pytest.raises(HostVaultConfigurationError, match="shadow staging"):
        config_from_assembly(_assembly(tmp_path / "identity", provider="aws-sm"))


def test_host_vault_accepts_bracketed_ipv6_address(tmp_path):
    assembly = _assembly(tmp_path / "identity")
    assembly["secrets"]["service"]["host_vault"]["address"] = "[::1]:7781"

    assert config_from_assembly(assembly).address == "[::1]:7781"


def test_host_vault_requires_parent_network_auto_mode(tmp_path):
    assembly = _assembly(tmp_path / "identity")
    assembly["platform"]["services"]["proc"]["exec"]["py_code_exec_network_mode"] = (
        "host"
    )

    with pytest.raises(HostVaultConfigurationError, match="network_mode 'auto'"):
        config_from_assembly(assembly)


def test_host_vault_start_requires_complete_identity_outside_workdir(tmp_path):
    workdir = tmp_path / "runtime"
    identity_dir = tmp_path / "identity"

    with pytest.raises(HostVaultConfigurationError, match="is incomplete"):
        validate_assembly_for_start(_assembly(identity_dir), workdir=workdir)

    in_runtime = workdir / "identity"
    _write_identity(in_runtime)
    with pytest.raises(HostVaultConfigurationError, match="outside the KDCube workdir"):
        validate_assembly_for_start(_assembly(in_runtime), workdir=workdir)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes are required")
def test_host_vault_start_rejects_public_private_key(tmp_path):
    identity_dir = tmp_path / "identity"
    _write_identity(identity_dir)
    (identity_dir / "host-vault-client.key").chmod(0o644)

    with pytest.raises(HostVaultConfigurationError, match="group or other"):
        validate_assembly_for_start(
            _assembly(identity_dir),
            workdir=tmp_path / "runtime",
        )


def test_host_vault_start_accepts_complete_private_identity(tmp_path):
    identity_dir = tmp_path / "identity"
    _write_identity(identity_dir)

    config = validate_assembly_for_start(
        _assembly(identity_dir),
        workdir=tmp_path / "runtime",
    )

    assert config.enabled is True


def test_start_refuses_pending_host_vault_activation(tmp_path):
    workdir = tmp_path / "runtime"
    identity_dir = tmp_path / "identity"
    _write_identity(identity_dir)
    config_dir = workdir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / HOST_VAULT_ACTIVATION_MARKER).write_text("{}\n", encoding="utf-8")

    with pytest.raises(HostVaultConfigurationError, match="host-vault recover"):
        validate_assembly_for_start(_assembly(identity_dir), workdir=workdir)


def test_runtime_secret_injection_uses_stdin_not_process_arguments(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(
        installer_module, "wait_for_secrets_ready", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        installer_module.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    context = PathsContext(
        lib_root=tmp_path / "lib",
        ai_app_root=tmp_path / "ai-app",
        docker_dir=tmp_path / "docker",
        sample_env_dir=tmp_path / "sample-env",
        workdir=tmp_path / "runtime",
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
    )

    class _Console:
        def print(self, *_args, **_kwargs):
            return None

    canary = "must-not-enter-process-arguments"
    installer_module.apply_runtime_secrets(
        _Console(),
        context,
        {"platform.services.fixture.token": canary},
        tmp_path / ".env",
    )

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[-3:] == ["set", "platform.services.fixture.token", "--stdin"]
    assert canary not in command
    assert kwargs["input"] == canary
    assert kwargs["text"] is True
