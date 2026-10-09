"""W673: the SDK enrolls the platform's own runtime purposes only when secrets.runtime is absent.

The whole section decides, exactly as the CLI projection does (kdcube_cli.host_vault): a declared section
wins whole, so a root-only or empty one enrolls nothing (Infra review, 2026-10-09).
"""
from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk import config as sdk_config
from kdcube_ai_app.infra.secrets.runtime_contract import (
    DEFAULT_RUNTIME_NAMESPACES, runtime_section_namespaces, valid_namespace,
)
from kdcube_cli.host_vault import compose_environment, config_from_assembly

import json


def test_the_defaults_are_valid_and_never_users():
    assert DEFAULT_RUNTIME_NAMESPACES == ("login-attempts", "card-credentials", "oauth-refresh-tokens")
    assert all(valid_namespace(value) for value in DEFAULT_RUNTIME_NAMESPACES)
    assert "users" not in DEFAULT_RUNTIME_NAMESPACES


@pytest.mark.parametrize("section,expected", [
    (None, DEFAULT_RUNTIME_NAMESPACES),
    ({}, ()),
    ({"root": "/config/secrets"}, ()),
    ({"namespaces": None}, ()),
    ({"namespaces": []}, ()),
    ({"namespaces": ["custody"]}, ("custody",)),
    ([], ()), ("runtime", ()), (True, ()),
])
def test_the_whole_section_decides(section, expected):
    assert runtime_section_namespaces(section) == expected


RUNTIME_SECTIONS = {
    "absent": None,
    "empty section": {},
    "root only": {"root": "/config/secrets"},
    "explicit empty": {"namespaces": []},
    "explicit list": {"namespaces": ["custody"]},
}


@pytest.mark.parametrize("name", sorted(RUNTIME_SECTIONS))
def test_settings_and_the_cli_projection_enroll_the_same(monkeypatch, tmp_path, name):
    section = RUNTIME_SECTIONS[name]
    secrets = {"provider": "secrets-file"}
    if section is not None:
        secrets["runtime"] = section
    assembly = tmp_path / "assembly.yaml"
    assembly.write_text(json.dumps({"secrets": secrets}), encoding="utf-8")
    monkeypatch.setenv("ASSEMBLY_YAML_DESCRIPTOR_PATH", str(assembly))
    settings = sdk_config.Settings()
    projected = compose_environment(config_from_assembly({"secrets": secrets}))
    assert settings.SECRETS_RUNTIME_NAMESPACES == tuple(json.loads(projected["KDCUBE_SECRETS_RUNTIME_NAMESPACES"]))
    assert (settings.SECRETS_RUNTIME_NAMESPACES == DEFAULT_RUNTIME_NAMESPACES) is (section is None)
