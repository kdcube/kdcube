"""A namespace is an address, not an authenticated grant or durability claim."""
import hashlib
import json
import plistlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.secrets import runtime_contract as contract


def policy_json():
    return json.dumps({
        "schema": contract.POLICY_SCHEMA,
        "read": {hashlib.sha256(b"reader-fixture").hexdigest(): ["custody"]},
        "write": {hashlib.sha256(b"writer-fixture").hexdigest(): ["custody"]},
    })


def test_scope_requires_exact_authenticated_credential_and_namespace():
    policy = contract.RuntimeScopePolicy(policy_json())
    assert policy.authorized(namespace="custody", token="reader-fixture", kind="read")
    assert policy.authorized(namespace="custody", token="writer-fixture", kind="write")
    for namespace in ("other", "custody-extra", "custody.*", "", "Custody"):
        assert not policy.authorized(namespace=namespace, token="reader-fixture", kind="read")
    assert not policy.authorized(namespace="custody", token="reader-fixture", kind="write")
    assert not policy.authorized(namespace="custody", token="writer-fixture", kind="read")
    for token in (None, "", "unknown-fixture", True, "\ud800", "x" * 4097, "\u00e9" * 4096):
        assert not policy.authorized(namespace="custody", token=token, kind="read")
    for kind in (None, [], {}, True, "unknown"):
        assert not policy.authorized(namespace="custody", token="reader-fixture", kind=kind)


@pytest.mark.parametrize("raw", [None, "", "{}", "[]", "null", "not-json", "x" * 65537,
    '{"schema":"kdcube.runtime_secret_scopes.v1","read":{},"read":{},"write":{}}'])
def test_unconfigured_or_malformed_policy_grants_nothing(raw):
    policy = contract.RuntimeScopePolicy(raw)
    assert not policy.authorized(namespace="custody", token="reader-fixture", kind="read")


@pytest.mark.parametrize("bad", [True, "custody", ["*"], ["Custody"], [1], ["a"] * 65])
def test_policy_rejects_bad_namespace_lists_without_partial_grants(bad):
    payload = json.loads(policy_json())
    payload["write"] = {"a" * 64: bad}
    policy = contract.RuntimeScopePolicy(json.dumps(payload))
    assert not policy.authorized(namespace="custody", token="reader-fixture", kind="read")


@pytest.mark.parametrize("namespace", ["", "*", "a.b", "a/b", "Upper", "a" * 65, None, True])
def test_invalid_qualification_namespaces_never_qualify(namespace):
    assert not contract.qualified(contract.qualification("custody"), namespace=namespace)


def test_qualification_is_exact_and_cannot_coerce_booleans():
    payload = contract.qualification("custody")
    assert contract.qualified(payload, namespace="custody")
    assert not contract.qualified(payload, namespace="other")
    assert not contract.qualified({**payload, "extra": True}, namespace="custody")
    for field in contract.GUARANTEES:
        without = dict(payload)
        without.pop(field)
        assert not contract.qualified(without, namespace="custody")
        for bad in (False, 1, "true", None):
            assert not contract.qualified({**payload, field: bad}, namespace="custody")


@pytest.mark.parametrize("key, expected", [
    ("ordinary.secret", None),
    ("platform.runtime.custody." + "a" * 32, "custody"),
    ("platform.runtime.custody.__keys", "custody"),
    ("platform.runtime.Custody.ref", ""),
    ("platform.runtime.custody.ref.extra", ""),
    ("platform.runtime..ref", ""),
    ("platform.runtime", ""),
    (None, ""),
])
def test_runtime_key_namespace_never_turns_malformed_runtime_keys_into_ordinary_keys(key, expected):
    assert contract.runtime_key_namespace(key) == expected


@pytest.mark.parametrize("inner_type, expected", [("ext4", True), ("xfs", True),
    ("tmpfs", False), ("ramfs", False), ("overlay", False), ("unknown", False)])
def test_linux_closest_mount_controls_durability(monkeypatch, tmp_path, inner_type, expected):
    monkeypatch.setattr(contract.os, "uname", lambda: SimpleNamespace(sysname="Linux"))
    root = str(tmp_path.resolve())
    mounts = ("1 0 8:1 / / rw - ext4 /dev/root rw\n"
              f"2 1 0:2 / {root} rw - {inner_type} test rw\n")
    monkeypatch.setattr(Path, "read_text", lambda path: mounts)
    assert contract.persistent_filesystem(tmp_path) is expected


@pytest.mark.parametrize("bus, expected", [("Apple Fabric", True),
    ("Disk Image", False), ("Virtual Interface", False)])
def test_macos_checks_actual_device_and_refuses_virtual_storage(monkeypatch, tmp_path, bus, expected):
    monkeypatch.setattr(contract.os, "uname", lambda: SimpleNamespace(sysname="Darwin"))
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        assert kwargs["timeout"] == 2 and kwargs["check"] is True
        if arguments[0] == "/bin/df":
            return SimpleNamespace(stdout=b"Filesystem 512-blocks Used Available Capacity Mounted\n/dev/disk3s5 1 0 1 0% /\n")
        assert arguments == ["/usr/sbin/diskutil", "info", "-plist", "/dev/disk3s5"]
        return SimpleNamespace(stdout=plistlib.dumps({"FilesystemType": "apfs", "BusProtocol": bus}))

    monkeypatch.setattr(contract.subprocess, "run", run)
    assert contract.persistent_filesystem(tmp_path) is expected
    assert calls[0] == ["/bin/df", "-P", str(tmp_path)]


def test_unknown_filesystem_probe_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(contract.os, "uname", lambda: SimpleNamespace(sysname="unknown"))
    assert not contract.persistent_filesystem(tmp_path)


@pytest.mark.parametrize("settings", [None, SimpleNamespace(), SimpleNamespace(SECRETS_RUNTIME_NAMESPACES=None)])
def test_environment_cannot_supply_runtime_custody_root_or_namespace_grants(monkeypatch, settings):
    from kdcube_ai_app.infra.secrets.manager import build_secrets_manager_config

    monkeypatch.setenv("SECRETS_RUNTIME_ROOT", "/synthetic/ambient/root")
    monkeypatch.setenv("SECRETS_RUNTIME_NAMESPACES", '["custody"]')
    config = build_secrets_manager_config(settings)
    assert config.runtime_secrets_root is None
    assert config.runtime_secret_namespaces == ()


def test_trusted_settings_supply_runtime_policy_independently_of_environment(monkeypatch):
    from kdcube_ai_app.infra.secrets.manager import build_secrets_manager_config

    monkeypatch.setenv("SECRETS_RUNTIME_ROOT", "/synthetic/ambient/root")
    monkeypatch.setenv("SECRETS_RUNTIME_NAMESPACES", '["other"]')
    settings = SimpleNamespace(
        SECRETS_RUNTIME_ROOT="/synthetic/descriptor/root",
        SECRETS_RUNTIME_NAMESPACES=("custody",),
    )
    config = build_secrets_manager_config(settings)
    assert config.runtime_secrets_root == settings.SECRETS_RUNTIME_ROOT
    assert config.runtime_secret_namespaces == ("custody",)


@pytest.mark.parametrize("changed", ["root", "namespaces"])
def test_runtime_policy_change_invalidates_the_manager_cache(monkeypatch, changed):
    from kdcube_ai_app.infra.secrets import manager

    settings = SimpleNamespace(
        SECRETS_PROVIDER="secrets-file",
        SECRETS_RUNTIME_ROOT="/synthetic/descriptor/root",
        SECRETS_RUNTIME_NAMESPACES=("custody",),
    )
    builds = []

    def build(config):
        builds.append(config)
        return object()

    monkeypatch.setattr(manager, "create_secrets_manager", build)
    manager.reset_secrets_manager_cache()
    try:
        first = manager.get_secrets_manager(settings)
        assert manager.get_secrets_manager(settings) is first
        if changed == "root":
            settings.SECRETS_RUNTIME_ROOT = "/synthetic/replacement/root"
        else:
            settings.SECRETS_RUNTIME_NAMESPACES = ("other",)
        second = manager.get_secrets_manager(settings)
        assert second is not first
        assert manager.get_secrets_manager(settings) is second
        assert len(builds) == 2
    finally:
        manager.reset_secrets_manager_cache()


@pytest.mark.asyncio
async def test_aws_qualifies_only_an_enrolled_namespace_after_one_signed_probe(monkeypatch):
    from kdcube_ai_app.infra.secrets import manager

    adapter = manager.AwsSecretsManagerSecretsManager(manager.SecretsManagerConfig(
        provider="aws-sm", component="proc", runtime_secret_namespaces=("custody",),
    ))
    calls = []

    class AvailableClient:
        async def list_secrets(self, **kwargs):
            calls.append(kwargs)
            return {"SecretList": []}

    @asynccontextmanager
    async def client():
        yield AvailableClient()

    monkeypatch.setattr(adapter, "_client_cm", client)
    # Enrollment plus authenticated availability, not the full runtime guarantee set.
    assert await adapter.qualify_runtime_custody(namespace="custody") is True
    assert len(calls) == 1
    assert await adapter.qualify_runtime_custody(namespace="other") is False
    assert len(calls) == 1
