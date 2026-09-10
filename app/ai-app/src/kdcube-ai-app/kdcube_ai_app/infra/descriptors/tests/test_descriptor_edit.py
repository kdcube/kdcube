# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The descriptor editor changes one node, keeps every comment and every
other key, keeps a backup beside the file, and refuses what would drop a
secret-bearing key or leave a provider that cannot resolve."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kdcube_ai_app.infra.descriptors.edit import (
    DescriptorEditRefused,
    edit_assembly_platform_sign_in,
    edit_bundle_authority_provider,
    merge_unchanged,
    validate_authority_provider,
)

ASSEMBLY = """auth:
  # Server-side login ON:  type: bundle   + connection_hub.provider_id: browser_session
  # Server-side login OFF: type: cognito  + connection_hub.provider_id: cognito
  type: cognito
  turnstile_development_token: 1x00000000000000000000AA   # dev only
  connection_hub:
    bundle_id: connection-hub@1-0
    authority_id: kdcube.platform
    provider_id: cognito
storage:
  kind: local   # keep
"""

BUNDLES = """bundles:
  version: '1'
  default_bundle_id: workspace@1
  items:
  - id: workspace@1
    name: Workspace   # the default app
  - id: connection-hub@1-0
    name: Connection Hub
    authority_registry:
      authorities:
        kdcube.platform:
          label: KDCube platform authority
          platform: true
          providers:
            cognito:
              type: multi_cognito
              enabled: true
              label: KDCube Cognito platform session   # shown in the widget
              authenticator:
                type: cognito_id_token
                id_token: ref://cookie-name   # a reference, never a value
                region: eu-west-1
                user_pool_id: eu-west-1_AAA
                app_client_id: client-a
                trusted_providers:
                - alias: demo
                  kind: cognito
                  region: eu-west-1
                  user_pool_id: eu-west-1_AAA
                  app_client_id: client-a
            browser_session:
              type: bundle_session_login
              enabled: true
              input:
                authenticator_ref:
                  authority_id: kdcube.platform
                  provider_id: cognito
              issuer:
                type: kdcube_session_token
"""


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    assembly = tmp_path / "assembly.yaml"
    bundles = tmp_path / "bundles.yaml"
    assembly.write_text(ASSEMBLY, encoding="utf-8")
    bundles.write_text(BUNDLES, encoding="utf-8")
    return {"assembly": assembly, "bundles": bundles}


def test_platform_sign_in_switch_changes_two_lines_and_keeps_the_rest(files):
    edit = edit_assembly_platform_sign_in(provider_id="browser_session", path=files["assembly"], bundles=files["bundles"])
    text = files["assembly"].read_text(encoding="utf-8")
    assert edit.changed == ("auth.type", "auth.connection_hub.provider_id")
    assert edit.activation == "refresh" and edit.scope == "lane"
    assert "  type: bundle\n" in text and "    provider_id: browser_session\n" in text
    # comments and unrelated keys survive
    assert "# Server-side login ON" in text and "# dev only" in text and "kind: local   # keep" in text
    assert "authority_id: kdcube.platform" in text
    backup = Path(edit.backup)
    assert backup.exists() and backup.read_text(encoding="utf-8") == ASSEMBLY
    assert not [p for p in files["assembly"].parent.iterdir() if p.name.endswith(".tmp")]


def test_platform_sign_in_switch_refuses_unknown_and_unsupported(files):
    with pytest.raises(DescriptorEditRefused) as unknown:
        edit_assembly_platform_sign_in(provider_id="nope", path=files["assembly"], bundles=files["bundles"])
    assert unknown.value.reason == "provider_unknown"
    # no change: nothing written, no backup
    assert files["assembly"].read_text(encoding="utf-8") == ASSEMBLY
    same = edit_assembly_platform_sign_in(provider_id="cognito", path=files["assembly"], bundles=files["bundles"])
    assert same.changed == () and same.activation == "none"


def test_provider_edit_adds_a_pool_keeps_secrets_and_comments(files):
    submitted = {
        "type": "multi_cognito",
        "enabled": True,
        "label": "KDCube Cognito platform session",
        "authenticator": {
            "type": "cognito_id_token",
            "id_token": "<unchanged>",
            "region": "eu-west-1",
            "user_pool_id": "eu-west-1_AAA",
            "app_client_id": "client-a",
            "trusted_providers": [
                {"alias": "demo", "kind": "cognito", "region": "eu-west-1", "user_pool_id": "eu-west-1_AAA", "app_client_id": "client-a"},
                {"alias": "staging", "kind": "cognito", "region": "eu-west-1", "user_pool_id": "eu-west-1_BBB", "app_client_id": "client-b"},
            ],
        },
    }
    edit = edit_bundle_authority_provider(
        bundle_id="connection-hub@1-0", authority_id="kdcube.platform", provider_id="cognito",
        provider=submitted, path=files["bundles"],
    )
    text = files["bundles"].read_text(encoding="utf-8")
    assert edit.scope == "providers" and edit.activation == "refresh"
    assert "id_token: ref://cookie-name" in text, "the secret reference is merged back, never retyped"
    assert "alias: staging" in text and "user_pool_id: eu-west-1_BBB" in text
    assert "name: Workspace   # the default app" in text and "authority_id: kdcube.platform" in text
    assert "browser_session:" in text
    assert Path(edit.backup).read_text(encoding="utf-8") == BUNDLES


def test_provider_edit_refuses_dropping_a_secret_key_unless_allowed(files):
    submitted = {
        "type": "multi_cognito",
        "authenticator": {"type": "cognito_id_token", "region": "eu-west-1", "user_pool_id": "eu-west-1_AAA", "app_client_id": "client-a"},
    }
    with pytest.raises(DescriptorEditRefused) as refused:
        edit_bundle_authority_provider(
            bundle_id="connection-hub@1-0", authority_id="kdcube.platform", provider_id="cognito",
            provider=submitted, path=files["bundles"],
        )
    assert refused.value.reason == "secret_key_dropped"
    assert refused.value.problems == ["missing: authenticator.id_token"]
    assert files["bundles"].read_text(encoding="utf-8") == BUNDLES
    allowed = edit_bundle_authority_provider(
        bundle_id="connection-hub@1-0", authority_id="kdcube.platform", provider_id="cognito",
        provider=submitted, allow_secret_removal=True, path=files["bundles"],
    )
    after = files["bundles"].read_text(encoding="utf-8")
    assert "id_token: ref://cookie-name" not in after and "\n                id_token:" not in after
    assert "type: cognito_id_token" in after, "the authenticator type is not a secret key"
    assert allowed.changed


def test_provider_edit_refuses_what_cannot_resolve(files):
    with pytest.raises(DescriptorEditRefused) as refused:
        edit_bundle_authority_provider(
            bundle_id="connection-hub@1-0", authority_id="kdcube.platform", provider_id="cognito",
            provider={"type": "multi_cognito", "authenticator": {"id_token": "<unchanged>"}}, path=files["bundles"],
        )
    assert refused.value.reason == "invalid_provider"
    assert any("region" in p for p in refused.value.problems)
    with pytest.raises(DescriptorEditRefused) as missing:
        edit_bundle_authority_provider(
            bundle_id="connection-hub@1-0", authority_id="kdcube.platform", provider_id="oidc",
            provider={"type": "bundle_session_login"}, path=files["bundles"],
        )
    assert missing.value.reason == "provider_missing"


def test_validation_speaks_in_sentences():
    assert validate_authority_provider([]) == ["The provider must be a mapping (a YAML block with keys), not a list or a scalar."]
    assert validate_authority_provider({}) == ["The provider needs a `type`."]
    assert validate_authority_provider({"type": "telegram_init_data"})[0].startswith("Unknown provider type")
    assert validate_authority_provider({"type": "bundle_session_login"}) == [
        "A session-login provider needs `input.authenticator_ref.provider_id`: the upstream provider it signs in through.",
        "A session-login provider needs `issuer.type` (kdcube_session_token).",
    ]
    assert validate_authority_provider({"type": "simple_idp"}) == []


def test_merge_unchanged_is_positional_for_lists_and_deep_for_mappings():
    existing = {"a": {"secret": "s", "n": 1}, "rows": [{"k": "x"}, {"k": "y"}]}
    merged = merge_unchanged({"a": {"secret": "<unchanged>", "n": 2}, "rows": [{"k": "<unchanged>"}]}, existing)
    assert merged == {"a": {"secret": "s", "n": 2}, "rows": [{"k": "x"}]}


def test_edits_can_be_switched_off_and_refuse_unwritable(files, monkeypatch):
    monkeypatch.setenv("KDCUBE_DESCRIPTOR_EDITS", "off")
    with pytest.raises(DescriptorEditRefused) as off:
        edit_assembly_platform_sign_in(provider_id="browser_session", path=files["assembly"], bundles=files["bundles"])
    assert off.value.reason == "disabled_by_env"
    monkeypatch.delenv("KDCUBE_DESCRIPTOR_EDITS")
    os.chmod(files["assembly"], 0o444)
    try:
        with pytest.raises(DescriptorEditRefused) as ro:
            edit_assembly_platform_sign_in(provider_id="browser_session", path=files["assembly"], bundles=files["bundles"])
        assert ro.value.reason == "not_writable"
    finally:
        os.chmod(files["assembly"], 0o644)
