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


def _overlay(tmp_path, values, tokens):
    base = tmp_path / ".env"
    base.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    out = installer.write_env_overlay(base, tokens)
    try:
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
def test_absent_runtime_projects_the_default_namespaces_and_marks_them_defaulted(provider):
    values = compose_environment(config_from_assembly({"secrets": {"provider": provider}}))
    assert json.loads(values["KDCUBE_SECRETS_RUNTIME_NAMESPACES"]) == list(DEFAULT_RUNTIME_NAMESPACES)
    assert values["KDCUBE_SECRETS_RUNTIME_DEFAULTED"] == "1"
    # The static projection carries no grant: the policy needs this run's credentials (the overlay).
    assert values["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"] == ""


def test_declared_namespaces_and_policies_win(tmp_path):
    declared = {"secrets": {"provider": "secrets-service", "runtime": {
        "root": str(tmp_path / "custody"), "namespaces": ["custody"],
        "scope_policy": {"schema": POLICY_SCHEMA, "read": {"a" * 64: ["custody"]}, "write": {"b" * 64: ["custody"]}}}}}
    values = compose_environment(config_from_assembly(declared))
    assert json.loads(values["KDCUBE_SECRETS_RUNTIME_NAMESPACES"]) == ["custody"]
    assert values["KDCUBE_SECRETS_RUNTIME_DEFAULTED"] == ""
    # Declared namespaces without a policy stay closed: no default grant.
    no_policy = compose_environment(config_from_assembly({"secrets": {"runtime": {"namespaces": ["custody"]}}}))
    assert no_policy["KDCUBE_SECRETS_RUNTIME_DEFAULTED"] == ""
    # Defaulted namespaces with a declared policy: the declared policy is projected, no default grant.
    with_policy = compose_environment(config_from_assembly({"secrets": {"runtime": {"scope_policy": {
        "schema": POLICY_SCHEMA, "read": {"c" * 64: ["login-attempts"]}, "write": {}}}}}))
    assert json.loads(with_policy["KDCUBE_SECRETS_RUNTIME_NAMESPACES"]) == list(DEFAULT_RUNTIME_NAMESPACES)
    assert with_policy["KDCUBE_SECRETS_RUNTIME_DEFAULTED"] == ""
    assert json.loads(with_policy["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"])["read"] == {"c" * 64: ["login-attempts"]}
    # Root-only: the defaults and the per-run grant, like an absent section.
    root_only = compose_environment(config_from_assembly({"secrets": {"runtime": {"root": "/config/secrets"}}}))
    assert json.loads(root_only["KDCUBE_SECRETS_RUNTIME_NAMESPACES"]) == list(DEFAULT_RUNTIME_NAMESPACES)
    assert root_only["KDCUBE_SECRETS_RUNTIME_DEFAULTED"] == "1"
    explicit_empty = compose_environment(config_from_assembly({"secrets": {"runtime": {"namespaces": []}}}))
    assert explicit_empty["KDCUBE_SECRETS_RUNTIME_NAMESPACES"] == "[]"
    assert explicit_empty["KDCUBE_SECRETS_RUNTIME_DEFAULTED"] == ""
    # The overlay never replaces a declared policy.
    merged = _overlay(tmp_path, values, _tokens())
    assert merged["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"] == values["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"]


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
