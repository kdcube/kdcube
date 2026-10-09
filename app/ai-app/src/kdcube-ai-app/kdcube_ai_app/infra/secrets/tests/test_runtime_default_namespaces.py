"""W673: the SDK enrolls the platform's own runtime purposes when no namespaces are declared."""
from __future__ import annotations

from pathlib import Path

from kdcube_ai_app.infra.secrets.runtime_contract import (
    DEFAULT_RUNTIME_NAMESPACES, default_runtime_namespaces, valid_namespace,
)


def test_absent_namespaces_enroll_the_defaults_and_a_declaration_wins():
    assert default_runtime_namespaces(None) == DEFAULT_RUNTIME_NAMESPACES
    assert default_runtime_namespaces(["custody"]) == ("custody",)
    assert default_runtime_namespaces([]) == ()  # an explicit empty list is the operator's choice
    assert default_runtime_namespaces("login-attempts") == ()
    assert all(valid_namespace(value) for value in DEFAULT_RUNTIME_NAMESPACES)
    assert "users" not in DEFAULT_RUNTIME_NAMESPACES


def test_settings_read_the_namespaces_through_the_default():
    config = (Path(__file__).resolve().parents[3] / "apps" / "chat" / "sdk" / "config.py").read_text()
    assert ('self.SECRETS_RUNTIME_NAMESPACES = default_runtime_namespaces(\n'
            '            _load_assembly_plain("secrets.runtime.namespaces"))') in config
