"""W673 (W670 regression): runtime-record defaults when secrets.runtime is absent.

Operator, 2026-10-09: "it simply must work regardless of the backend". Reported: a local install without
secrets.runtime refused reconnect with runtime_secret_storage_unavailable and POST
/runtime-secrets/login-attempts/create 403. With no runtime configuration, install and refresh now enroll the
platform's own purposes and grant them only to this deployment's own proc read and writer credentials, by
SHA-256; a declared runtime configuration wins whole. Pure projection: no store, broker or credential is used.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from kdcube_cli import installer
from kdcube_cli.host_vault import (
    DEFAULT_RUNTIME_NAMESPACES, compose_environment, config_from_assembly, default_runtime_scope_policy,
)
from kdcube_ai_app.infra.secrets import runtime_contract
from kdcube_ai_app.infra.secrets.runtime_contract import POLICY_SCHEMA, RuntimeScopePolicy

READ, WRITE, INGRESS = "synthetic-proc-reader-" + "r" * 20, "synthetic-writer-" + "w" * 20, "synthetic-ingress-" + "i" * 20


def _overlay(tmp_path, values, tokens, assembly=None):
    base = tmp_path / ".env"
    base.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    (tmp_path / "assembly.yaml").write_text(json.dumps(assembly or {"secrets": {"provider": "secrets-service"}}))
    original = base.read_text()
    out = installer.write_env_overlay(base, tokens)
    try:
        assert base.read_text() == original
        return dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    finally:
        out.unlink()


def _tokens():
    return {"SECRETS_ADMIN_TOKEN": WRITE, "SECRETS_READ_TOKENS": f"{INGRESS},{READ}",
            "SECRETS_TOKEN_INGRESS": INGRESS, "SECRETS_TOKEN_PROC": READ}


def test_the_cli_defaults_equal_the_sdk_contract():
    assert DEFAULT_RUNTIME_NAMESPACES == runtime_contract.DEFAULT_RUNTIME_NAMESPACES
    assert DEFAULT_RUNTIME_NAMESPACES == ("login-attempts", "card-credentials", "oauth-refresh-tokens")
    assert "users" not in DEFAULT_RUNTIME_NAMESPACES  # reserved for per-user secret folders
    assert all(runtime_contract.valid_namespace(value) for value in DEFAULT_RUNTIME_NAMESPACES)


@pytest.mark.parametrize("provider", ["secrets-file", "secrets-service"])
def test_absent_runtime_enrolls_defaults_without_persisting_a_projection(provider):
    config = config_from_assembly({"secrets": {"provider": provider}})
    assert config.runtime_namespaces == DEFAULT_RUNTIME_NAMESPACES
    assert config.runtime_defaulted
    assert not any("RUNTIME" in key for key in compose_environment(config))


@pytest.mark.parametrize("namespaces", [[], ["custody"]])
def test_explicit_namespaces_without_policy_stay_closed_despite_stale_env(tmp_path, namespaces):
    assembly = {"secrets": {"provider": "secrets-service", "runtime": {"namespaces": namespaces}}}
    stale = {"KDCUBE_SECRETS_RUNTIME_DEFAULTED": "1",
             "KDCUBE_SECRETS_RUNTIME_NAMESPACES": '["foreign"]',
             "KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY": "stale-policy"}
    merged = _overlay(tmp_path, stale, _tokens(), assembly)
    assert merged["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"] == ""
    assert "KDCUBE_SECRETS_RUNTIME_NAMESPACES" not in merged
    assert "KDCUBE_SECRETS_RUNTIME_DEFAULTED" not in merged


@pytest.mark.parametrize("provider", ["secrets-service", "service", "sidecar", "local"])
def test_local_service_refuses_fixed_digest_policy(tmp_path, provider):
    from kdcube_cli.host_vault import HostVaultConfigurationError
    assembly = {"secrets": {"provider": provider, "runtime": {"scope_policy": {
        "schema": POLICY_SCHEMA, "read": {"a" * 64: ["login-attempts"]}, "write": {}}}}}
    with pytest.raises(HostVaultConfigurationError, match=r"secrets.runtime.scope_policy.*fixed credential digests"):
        _overlay(tmp_path, {}, _tokens(), assembly)


def test_root_only_uses_default_namespaces_and_custom_mounts(tmp_path):
    assembly = {"secrets": {"provider": "secrets-service", "runtime": {"root": "/config/custody"}}}
    merged = _overlay(tmp_path, {}, _tokens(), assembly)
    assert merged["KDCUBE_SECRETS_RUNTIME_ROOT"] == "/config/custody"
    assert merged["HOST_KDCUBE_RUNTIME_SECRETS_ROOT"] == str(tmp_path / "custody")
    assert RuntimeScopePolicy(merged["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"]).authorized(
        namespace="card-credentials", token=READ, kind="read")


def test_the_fresh_token_overlay_grants_only_this_runs_proc_credentials(tmp_path):
    values = compose_environment(config_from_assembly({"secrets": {"provider": "secrets-service"}}))
    merged = _overlay(tmp_path, values, _tokens())
    policy = RuntimeScopePolicy(merged["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"])
    for namespace in DEFAULT_RUNTIME_NAMESPACES:
        assert policy.authorized(namespace=namespace, token=READ, kind="read")
        assert policy.authorized(namespace=namespace, token=WRITE, kind="write")
        # Wrong identity: the ingress door credential and an unknown one get nothing.
        for kind in ("read", "write"):
            assert not policy.authorized(namespace=namespace, token=INGRESS, kind=kind)
            assert not policy.authorized(namespace=namespace, token="foreign-credential", kind=kind)
        # Wrong kind: the reader cannot write.
        assert not policy.authorized(namespace=namespace, token=READ, kind="write")
    # Wrong purpose / owner: only the three platform purposes; "users" and others are not granted.
    for namespace in ("users", "foreign", "chub-oauth-" + "0" * 52):
        assert not policy.authorized(namespace=namespace, token=READ, kind="read")
        assert not policy.authorized(namespace=namespace, token=WRITE, kind="write")
    raw = json.loads(merged["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"])
    assert raw == {"schema": POLICY_SCHEMA,
                   "read": {hashlib.sha256(READ.encode()).hexdigest(): list(DEFAULT_RUNTIME_NAMESPACES)},
                   "write": {hashlib.sha256(WRITE.encode()).hexdigest(): list(DEFAULT_RUNTIME_NAMESPACES)}}
    assert READ not in merged["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"]
    assert WRITE not in merged["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"]


def test_a_refresh_with_new_tokens_replaces_the_grant(tmp_path):
    values = compose_environment(config_from_assembly({"secrets": {}}))
    first = RuntimeScopePolicy(_overlay(tmp_path, values, _tokens())["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"])
    rotated = {**_tokens(), "SECRETS_TOKEN_PROC": READ + "-next", "SECRETS_ADMIN_TOKEN": WRITE + "-next"}
    second = RuntimeScopePolicy(_overlay(tmp_path, values, rotated)["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"])
    assert first.authorized(namespace="login-attempts", token=READ, kind="read")
    assert not second.authorized(namespace="login-attempts", token=READ, kind="read")
    assert second.authorized(namespace="login-attempts", token=READ + "-next", kind="read")


@pytest.mark.parametrize("kwargs", [
    {"read_token": "", "write_token": WRITE, "namespaces": DEFAULT_RUNTIME_NAMESPACES},
    {"read_token": READ, "write_token": "", "namespaces": DEFAULT_RUNTIME_NAMESPACES},
    {"read_token": READ, "write_token": WRITE, "namespaces": ()},
    {"read_token": READ, "write_token": WRITE, "namespaces": ("login-attempts.*",)},
])
def test_an_unusable_input_grants_nothing(kwargs):
    assert default_runtime_scope_policy(**kwargs) == ""


def test_an_overlay_without_fresh_proc_tokens_grants_nothing(tmp_path):
    values = compose_environment(config_from_assembly({"secrets": {}}))
    merged = _overlay(tmp_path, values, {"SOMETHING_ELSE": "x"})
    assert merged["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"] == ""


def test_compose_uses_launch_policy_and_descriptor_mounts_over_ambient_values(tmp_path, monkeypatch):
    for key in (*installer.RUNTIME_PROJECTION_KEYS, "KDCUBE_SECRETS_RUNTIME_ROOT", "HOST_KDCUBE_RUNTIME_SECRETS_ROOT"):
        monkeypatch.setenv(key, "ambient-rival")
    (tmp_path / "assembly.yaml").write_text('{"secrets":{"provider":"secrets-service"}}')
    base = tmp_path / ".env"
    base.write_text("")
    overlay = installer.write_env_overlay(base, _tokens())
    try:
        env = installer.compose_env(overlay)
        assert "KDCUBE_SECRETS_RUNTIME_ROOT" not in env
        assert "HOST_KDCUBE_RUNTIME_SECRETS_ROOT" not in env
        assert "KDCUBE_SECRETS_RUNTIME_NAMESPACES" not in env
        assert "ambient-rival" not in env["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"]
        assert RuntimeScopePolicy(env["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"]).authorized(
            namespace="card-credentials", token=READ, kind="read")
    finally:
        overlay.unlink()
