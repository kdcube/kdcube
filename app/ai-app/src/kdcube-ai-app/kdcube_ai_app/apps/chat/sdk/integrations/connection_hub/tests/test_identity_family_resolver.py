# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from typing import Any

from connection_hub.hub.resolver import (
    actor_user_id_for_identity,
    parse_actor_user_id,
    resolve_delegated_identity_scope,
    resolve_identity_family,
)


class _EdgeStore:
    def __init__(self, edges: list[dict[str, Any]]) -> None:
        self.edges = edges

    def resolve_edge(
        self,
        *,
        from_provider: str,
        from_subject: str,
        target_authority_id: str = "platform",
    ) -> dict[str, Any] | None:
        for edge in self.edges:
            source = edge.get("from") or {}
            target = edge.get("to") or {}
            if (
                source.get("provider") == from_provider
                and source.get("subject") == from_subject
                and target.get("authority_id") == target_authority_id
            ):
                return dict(edge)
        return None

    def list_edges(self, *, target_user_id: str, **_: Any) -> list[dict[str, Any]]:
        return [dict(edge) for edge in self.edges if (edge.get("to") or {}).get("user_id") == target_user_id]


def _telegram_edge(
    *,
    telegram_id: str = "100200300",
    platform_user_id: str = "a1b2c3d4",
    grants: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "edge_id": f"edge_telegram_{telegram_id}",
        "relationship": "delegates_to",
        "from": {
            "authority_id": "telegram.kdcube_ref",
            "provider": "telegram",
            "subject": telegram_id,
            "user_id": f"telegram_{telegram_id}",
            "label": "elena_viter",
        },
        "to": {
            "authority_id": "platform",
            "provider": "platform",
            "subject": platform_user_id,
            "user_id": platform_user_id,
            "label": "KDCube platform user",
        },
        "grants": ["identity:family"] if grants is None else list(grants),
        "metadata": {
            "integration_id": "telegram.kdcube_ref",
            "authenticator_id": "telegram.kdcube_ref",
        },
        "status": "active",
    }


def test_resolve_identity_family_expands_linked_telegram_actor_to_platform_family():
    store = _EdgeStore([_telegram_edge()])

    result = resolve_identity_family(store, input_user_id="telegram_100200300")

    assert result["ok"] is True
    assert result["linked"] is True
    assert result["platform_user_id"] == "a1b2c3d4"
    assert result["memory_user_ids"] == ["a1b2c3d4", "telegram_100200300"]
    assert result["authority"]["authority_id"] == "platform"
    assert result["identities"][1]["integration_id"] == "telegram.kdcube_ref"


def test_resolve_identity_family_requires_family_grant_for_channel_actor():
    store = _EdgeStore([_telegram_edge(grants=[])])

    result = resolve_identity_family(store, input_user_id="telegram_100200300")

    assert result["ok"] is True
    assert result["linked"] is False
    assert result["platform_user_id"] == ""
    assert result["memory_user_ids"] == ["telegram_100200300"]
    assert result["requested_connection_edge"]["edge_id"] == "edge_telegram_100200300"


def test_resolve_identity_family_keeps_unlinked_actor_local():
    result = resolve_identity_family(_EdgeStore([]), input_user_id="telegram_100200300")

    assert result["ok"] is True
    assert result["linked"] is False
    assert result["platform_user_id"] == ""
    assert result["memory_user_ids"] == ["telegram_100200300"]
    assert result["identities"][0]["status"] == "unlinked"


def test_actor_user_id_helpers_preserve_registered_provider_conventions():
    assert parse_actor_user_id("telegram_100200300") == {
        "provider": "telegram",
        "provider_subject": "100200300",
        "identity_ref": "telegram:100200300",
    }
    assert actor_user_id_for_identity("telegram", "100200300") == "telegram_100200300"
    assert actor_user_id_for_identity(
        "custom",
        "subject-1",
        metadata={"actor_user_id": "custom_actor_subject_1"},
    ) == "custom_actor_subject_1"


def _delegated_credential(*, grantor="a1b2c3d4", identity_scope="grantor_identity_family"):
    return {
        "schema": "kdcube.credential.v1",
        "credential_kind": "delegated_client_access",
        "issuer_authority_id": "delegated_client",
        "issuer_authenticator_id": "delegated_client.bearer",
        "subject": f"integration:claude:{grantor}",
        "audience": "kdcube:delegated_client",
        "attrs": {
            "grantor_subject": grantor,
            "client_id": "claude",
            "resource": "https://runtime/api/integrations/bundles/demo/demo/user-memories@2026-06-26/public/mcp/memories",
            "scopes": ["memories:read"],
            "tools": ["memory_search", "memory_get"],
            "identity_scope": identity_scope,
        },
    }


def test_delegated_identity_scope_grantor_family_expands_linked_identities():
    store = _EdgeStore([_telegram_edge()])

    result = resolve_delegated_identity_scope(
        store,
        credential=_delegated_credential(identity_scope="grantor_identity_family"),
        grantor_authority={
            "grantor_roles": ["kdcube:role:super-admin"],
            "grantor_permissions": ["memories:read"],
            "economics_budget_bypass": True,
        },
    )

    assert result["ok"] is True
    assert result["delegate_identity"] == "integration:claude:a1b2c3d4"
    assert result["grantor_user_id"] == "a1b2c3d4"
    assert result["identity_scope"] == "grantor_identity_family"
    assert result["memory_user_ids"] == ["a1b2c3d4", "telegram_100200300"]
    assert result["delegation"]["client_id"] == "claude"
    assert result["economics"]["user_id"] == "a1b2c3d4"
    assert result["economics"]["charge_to"] == "grantor"
    # A passed grantor_authority mapping is not entitlement (App Ecosystem
    # c1648fd2): managed admission supplies roles, permissions and budget
    # bypass from the grantor's current platform record, never this resolver.
    for captured in ("roles", "permissions", "budget_bypass", "user_type"):
        assert captured not in result["economics"]


def test_delegated_identity_scope_grantor_only_does_not_expand_family():
    store = _EdgeStore([_telegram_edge()])

    result = resolve_delegated_identity_scope(
        store,
        credential=_delegated_credential(identity_scope="grantor"),
    )

    assert result["ok"] is True
    assert result["identity_scope"] == "grantor"
    assert result["memory_user_ids"] == ["a1b2c3d4"]
