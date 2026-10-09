"""W670 F1: the secrets-file runtime-record folder reaches every container that keeps or reads the records.

Operator, 2026-10-09: "file backend for such secrets must be a folder in fact". The assembly's
secrets.runtime.root is a dedicated folder (one private file per namespace, RuntimeFileStore). Both
assemblies mount the host folder at that same path in the secrets service and in the processors that run
the bundles, so the records persist across container replacement, and the secrets service receives the
runtime root, namespaces and scope policy. kdcube-cli writes those values for every provider.
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


def _services(assembly):
    return yaml.safe_load((ROOT / assembly / "docker-compose.yaml").read_text())["services"]


@pytest.mark.parametrize("assembly", ASSEMBLIES)
@pytest.mark.parametrize("service", ["kdcube-secrets", "chat-ingress", "chat-proc"])
def test_every_record_container_mounts_the_runtime_folder_at_the_configured_path(assembly, service):
    volumes = _services(assembly)[service]["volumes"]
    assert RUNTIME_BIND in volumes, (assembly, service)


@pytest.mark.parametrize("assembly", ASSEMBLIES)
def test_the_secrets_service_receives_the_runtime_root_namespaces_and_policy(assembly):
    environment = set(_services(assembly)["kdcube-secrets"]["environment"])
    assert RUNTIME_ENV <= environment, assembly


def test_both_assemblies_wire_the_runtime_folder_identically():
    def wiring(assembly):
        services = _services(assembly)
        return {name: [volume for volume in services[name]["volumes"] if volume == RUNTIME_BIND]
                for name in ("kdcube-secrets", "chat-ingress", "chat-proc")}
    assert wiring("all_in_one_kdcube") == wiring("custom-ui-managed-infra")


def test_the_cli_writes_the_runtime_folder_for_every_provider():
    from kdcube_cli.host_vault import HostVaultConfigurationError, runtime_compose_environment

    assembly = {"secrets": {"provider": "secrets-file", "runtime": {
        "root": "/srv/kdcube/runtime-secrets", "namespaces": ["resident-card-credentials"]}}}
    env = runtime_compose_environment(assembly)
    assert env["KDCUBE_SECRETS_RUNTIME_ROOT"] == env["HOST_KDCUBE_RUNTIME_SECRETS_ROOT"] == "/srv/kdcube/runtime-secrets"
    assert json.loads(env["KDCUBE_SECRETS_RUNTIME_NAMESPACES"]) == ["resident-card-credentials"]
    assert runtime_compose_environment({"secrets": {"provider": "secrets-file"}})["KDCUBE_SECRETS_RUNTIME_ROOT"] == ""
    with pytest.raises(HostVaultConfigurationError):  # a relative or shared root fails closed
        runtime_compose_environment({"secrets": {"runtime": {"root": "relative/runtime"}}})
