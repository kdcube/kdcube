# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

import copy
import json

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.hashing import (
    connections_content_hash,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.models import (
    CatalogDocument,
    CatalogDocumentError,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.source import (
    connections_from_props,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.catalog.store import (
    BundleStorageDelegatedCatalogStore,
    CatalogStorageError,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.durable_io import (
    DurableDecodeError,
)

CONNECTIONS = {
    "delegated_credentials": {
        "oauth": {
            "enabled": True,
            "resources": [
                {
                    "resource": "https://example.test/mcp/named-services",
                    "grants": ["named_services:use", "slack:read"],
                    "named_services": {
                        "namespaces": {
                            "slack": {"tools": {"call": {"operations": {"object.search": {}}}}},
                        },
                    },
                },
            ],
        },
    },
}


def _reordered(mapping):
    """Same content, reversed key insertion order at every mapping level."""
    if isinstance(mapping, dict):
        return {key: _reordered(mapping[key]) for key in reversed(list(mapping))}
    if isinstance(mapping, list):
        return [_reordered(item) for item in mapping]
    return mapping


def test_hash_is_full_lowercase_sha256():
    digest = connections_content_hash(CONNECTIONS)
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)


def test_map_key_order_does_not_change_the_hash():
    assert connections_content_hash(_reordered(CONNECTIONS)) == connections_content_hash(CONNECTIONS)


def test_any_value_change_changes_the_hash():
    changed = copy.deepcopy(CONNECTIONS)
    changed["delegated_credentials"]["oauth"]["resources"][0]["grants"] = [
        "named_services:use",
        "slack:write",
    ]
    assert connections_content_hash(changed) != connections_content_hash(CONNECTIONS)


def test_list_order_changes_the_hash():
    changed = copy.deepcopy(CONNECTIONS)
    changed["delegated_credentials"]["oauth"]["resources"][0]["grants"] = [
        "slack:read",
        "named_services:use",
    ]
    assert connections_content_hash(changed) != connections_content_hash(CONNECTIONS)


def test_unsupported_value_is_a_publication_error_not_a_coerced_string():
    with pytest.raises(TypeError):
        connections_content_hash({"resources": {object()}})


def test_document_preserves_the_exact_mapping_without_enrichment():
    document = CatalogDocument.build(CONNECTIONS)
    assert document.connections == CONNECTIONS
    assert json.loads(json.dumps(document.to_dict()))["connections"] == CONNECTIONS


def test_document_round_trips_and_verifies():
    document = CatalogDocument.build(CONNECTIONS)
    parsed = CatalogDocument.from_mapping(document.to_dict())
    assert parsed == document
    assert parsed.version.startswith("delegated_catalog_")
    assert parsed.version.endswith(document.content_hash[:12])


def test_tampered_body_fails_hash_validation():
    payload = CatalogDocument.build(CONNECTIONS).to_dict()
    payload["connections"]["delegated_credentials"]["oauth"]["enabled"] = False
    with pytest.raises(CatalogDocumentError) as exc:
        CatalogDocument.from_mapping(payload)
    assert exc.value.reason == "content_hash_mismatch"


def test_document_build_snapshots_the_mapping():
    source = copy.deepcopy(CONNECTIONS)
    document = CatalogDocument.build(source)
    source["delegated_credentials"]["oauth"]["enabled"] = False
    document.verify()


def test_connections_from_props_reads_the_exact_mapping():
    assert connections_from_props({"connections": CONNECTIONS}) == CONNECTIONS
    assert connections_from_props({"identity": {}}) == {}
    assert connections_from_props(None) == {}


@pytest.mark.asyncio
async def test_catalog_store_round_trip(tmp_path):
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    document = CatalogDocument.build(CONNECTIONS)

    assert await store.read_active() is None
    assert await store.read_version(document.version) is None
    assert await store.version_exists(document.version) is False

    await store.write_version(document)
    await store.publish_active(document)

    assert await store.version_exists(document.version) is True
    assert await store.read_version(document.version) == document
    active = await store.read_active()
    assert active == document
    assert active.connections == CONNECTIONS


@pytest.mark.asyncio
async def test_catalog_store_rejects_a_malformed_version_name(tmp_path):
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    for bad in ("../../etc/passwd", "delegated_catalog_x", "", "delegated_catalog_2026-08-11_zz"):
        with pytest.raises(CatalogStorageError) as exc:
            await store.read_version(bad)
        assert exc.value.reason == "version_name_invalid"


@pytest.mark.asyncio
async def test_catalog_store_reports_corrupt_content_distinctly_from_absence(tmp_path):
    store = BundleStorageDelegatedCatalogStore(tmp_path)
    path = store.active_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DurableDecodeError):
        await store.read_active()
