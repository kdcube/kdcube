"""W670 K2: the runtime-secrets folder is config/secrets; host and container paths are separate.

Operator, 2026-10-09: the folder lives "in config folder. make the folder secrets". In the containers it is
/config/secrets (or a declared secrets.runtime.root inside /config). On the host it is the matching folder
under the host config folder, derived from the descriptor's location and never the container path. The
processors already mount /config; only the secrets service (no /config mount) binds the host folder, and it
receives the runtime root, namespaces and scope policy.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ASSEMBLIES = ("all_in_one_kdcube", "custom-ui-managed-infra")
RUNTIME_BIND = {"type": "bind", "source": "${HOST_KDCUBE_RUNTIME_SECRETS_ROOT:-/dev/null}",
                "target": "${KDCUBE_SECRETS_RUNTIME_ROOT:-/run/kdcube-runtime-secrets}",
                "bind": {"create_host_path": False}}
RUNTIME_ENV = {"KDCUBE_SECRETS_RUNTIME_ROOT=${KDCUBE_SECRETS_RUNTIME_ROOT:-}",
               "KDCUBE_SECRETS_RUNTIME_NAMESPACES=${KDCUBE_SECRETS_RUNTIME_NAMESPACES:-}",
               "KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY=${KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY:-}"}
HOST_CONFIG = "/srv/workdir/config"


def _services(assembly):
    return yaml.safe_load((ROOT / assembly / "docker-compose.yaml").read_text())["services"]


@pytest.mark.parametrize("assembly", ASSEMBLIES)
def test_the_secrets_service_binds_the_host_folder_and_receives_the_runtime_policy(assembly):
    service = _services(assembly)["kdcube-secrets"]
    assert RUNTIME_BIND in service["volumes"]
    assert not any(isinstance(volume, str) and volume.endswith(":/config") for volume in service["volumes"])
    assert RUNTIME_ENV <= set(service["environment"])


@pytest.mark.parametrize("assembly", ASSEMBLIES)
@pytest.mark.parametrize("service", ["chat-ingress", "chat-proc"])
def test_processors_reach_the_folder_through_their_config_mount_only(assembly, service):
    volumes = _services(assembly)[service]["volumes"]
    assert any(isinstance(volume, str) and volume.endswith(":/config") for volume in volumes)
    assert RUNTIME_BIND not in volumes


def _env(runtime=None, provider="secrets-file", host_config=HOST_CONFIG):
    from kdcube_cli.host_vault import runtime_compose_environment

    secrets = {"provider": provider}
    if runtime is not None:
        secrets["runtime"] = runtime
    return runtime_compose_environment({"secrets": secrets}, host_config_dir=host_config)


@pytest.mark.parametrize("provider", ["secrets-file", "secrets-service"])
def test_no_runtime_configuration_defaults_to_config_secrets(provider):
    env = _env(provider=provider)
    assert env["KDCUBE_SECRETS_RUNTIME_ROOT"] == "/config/secrets"
    assert env["HOST_KDCUBE_RUNTIME_SECRETS_ROOT"] == f"{HOST_CONFIG}/secrets"
    assert env["HOST_KDCUBE_RUNTIME_SECRETS_ROOT"] != env["KDCUBE_SECRETS_RUNTIME_ROOT"]
    assert json.loads(env["KDCUBE_SECRETS_RUNTIME_NAMESPACES"]) == [
        "login-attempts", "card-credentials", "oauth-refresh-tokens"]


def test_a_declared_root_inside_config_maps_to_the_host_config_folder():
    env = _env({"root": "/config/runtime/records", "namespaces": ["card-credentials"]})
    assert env["KDCUBE_SECRETS_RUNTIME_ROOT"] == "/config/runtime/records"
    assert env["HOST_KDCUBE_RUNTIME_SECRETS_ROOT"] == f"{HOST_CONFIG}/runtime/records"
    assert json.loads(env["KDCUBE_SECRETS_RUNTIME_NAMESPACES"]) == ["card-credentials"]


@pytest.mark.parametrize("root", ["/config", "/srv/kdcube/runtime-secrets", "/configuration/secrets"])
def test_a_root_outside_the_config_folder_is_refused(root):
    from kdcube_cli.host_vault import HostVaultConfigurationError

    with pytest.raises(HostVaultConfigurationError, match="inside the config folder"):
        _env({"root": root, "namespaces": []})


def test_a_relative_host_config_folder_or_root_is_refused():
    from kdcube_cli.host_vault import HostVaultConfigurationError

    with pytest.raises(HostVaultConfigurationError):
        _env(host_config="relative/config")
    with pytest.raises(HostVaultConfigurationError):
        _env({"root": "relative/runtime"})
