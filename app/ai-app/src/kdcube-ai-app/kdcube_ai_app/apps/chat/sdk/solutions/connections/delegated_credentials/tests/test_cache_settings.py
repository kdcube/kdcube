# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

import pathlib

import pytest
import yaml

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.cache_settings import (
    DEFAULT_ACTIVE_CACHE_SECONDS,
    DEFAULT_REVOKED_TOMBSTONE_SECONDS,
    DEFAULT_UPDATING_MARKER_SECONDS,
    DEFAULT_VERSION_CACHE_SECONDS,
    DelegatedCacheSettings,
)

def _ai_app_root() -> pathlib.Path:
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / "deployment/bundles.yaml").is_file():
            return parent
    raise AssertionError("ai-app root with deployment/bundles.yaml not found")


_AI_APP_ROOT = _ai_app_root()
_REFERENCE_DESCRIPTOR = (
    _AI_APP_ROOT
    / "src/kdcube-ai-app/kdcube_ai_app/apps/chat/sdk/examples/bundles"
    / "connection-hub@1-0/config/bundles.template.yaml"
)
_DEPLOYED_DESCRIPTOR = _AI_APP_ROOT / "deployment/bundles.yaml"


def test_defaults_apply_when_the_block_is_absent():
    settings = DelegatedCacheSettings.from_connections({})
    assert settings.catalog.active_cache_seconds == DEFAULT_ACTIVE_CACHE_SECONDS
    assert settings.catalog.version_cache_seconds == DEFAULT_VERSION_CACHE_SECONDS
    assert settings.cards.updating_marker_seconds == DEFAULT_UPDATING_MARKER_SECONDS
    assert settings.cards.revoked_tombstone_seconds == DEFAULT_REVOKED_TOMBSTONE_SECONDS


def test_declared_values_are_read():
    settings = DelegatedCacheSettings.from_connections(
        {
            "delegated_credentials": {
                "catalog": {"active_cache_seconds": 60, "version_cache_seconds": 120},
                "cards": {"updating_marker_seconds": 5, "revoked_tombstone_seconds": 30},
            }
        }
    )
    assert settings.catalog.active_cache_seconds == 60
    assert settings.catalog.version_cache_seconds == 120
    assert settings.cards.updating_marker_seconds == 5
    assert settings.cards.revoked_tombstone_seconds == 30


@pytest.mark.parametrize("bad", [0, -1, "", None, "abc", {}])
def test_unusable_values_fall_back_to_the_default(bad):
    settings = DelegatedCacheSettings.from_connections(
        {"delegated_credentials": {"catalog": {"active_cache_seconds": bad}}}
    )
    assert settings.catalog.active_cache_seconds == DEFAULT_ACTIVE_CACHE_SECONDS


def test_residency_is_bounded():
    settings = DelegatedCacheSettings.from_connections(
        {"delegated_credentials": {"catalog": {"version_cache_seconds": 10 ** 9}}}
    )
    assert settings.catalog.version_cache_seconds == 24 * 3600


def _descriptor_connections(path: pathlib.Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    found: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            candidate = node.get("connections")
            if isinstance(candidate, dict) and "delegated_credentials" in candidate:
                found.append(candidate)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    assert found, f"no connections block with delegated_credentials in {path}"
    return found[0]


def test_reference_and_deployed_descriptors_declare_the_same_residency():
    reference = DelegatedCacheSettings.from_connections(
        _descriptor_connections(_REFERENCE_DESCRIPTOR)
    )
    deployed = DelegatedCacheSettings.from_connections(
        _descriptor_connections(_DEPLOYED_DESCRIPTOR)
    )
    assert reference == deployed
    assert reference == DelegatedCacheSettings()
