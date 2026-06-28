# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.connections.hub import (
    actor_user_id_for_identity,
    parse_actor_user_id,
    resolve_identity_family,
)


class _LinkStore:
    def __init__(self, links: list[dict[str, Any]]) -> None:
        self.links = links

    def resolve_link(self, *, provider: str, provider_subject: str) -> dict[str, Any] | None:
        for link in self.links:
            if link.get("provider") == provider and link.get("provider_subject") == provider_subject:
                return dict(link)
        return None

    def list_links(self, *, platform_user_id: str) -> list[dict[str, Any]]:
        return [dict(link) for link in self.links if link.get("platform_user_id") == platform_user_id]


def test_resolve_identity_family_expands_linked_telegram_actor_to_platform_family():
    store = _LinkStore([
        {
            "provider": "telegram",
            "provider_subject": "434804821",
            "platform_user_id": "02e53484",
            "label": "elena_viter",
            "metadata": {
                "integration_id": "telegram.kdcube_ref",
                "authenticator_id": "telegram.kdcube_ref",
            },
        }
    ])

    result = resolve_identity_family(store, input_user_id="telegram_434804821")

    assert result["ok"] is True
    assert result["linked"] is True
    assert result["platform_user_id"] == "02e53484"
    assert result["memory_user_ids"] == ["02e53484", "telegram_434804821"]
    assert result["authority"]["authority_id"] == "platform"
    assert result["identities"][1]["integration_id"] == "telegram.kdcube_ref"


def test_resolve_identity_family_keeps_unlinked_actor_local():
    result = resolve_identity_family(_LinkStore([]), input_user_id="telegram_434804821")

    assert result["ok"] is True
    assert result["linked"] is False
    assert result["platform_user_id"] == ""
    assert result["memory_user_ids"] == ["telegram_434804821"]
    assert result["identities"][0]["status"] == "unlinked"


def test_actor_user_id_helpers_preserve_registered_provider_conventions():
    assert parse_actor_user_id("telegram_434804821") == {
        "provider": "telegram",
        "provider_subject": "434804821",
        "identity_ref": "telegram:434804821",
    }
    assert actor_user_id_for_identity("telegram", "434804821") == "telegram_434804821"
    assert actor_user_id_for_identity(
        "custom",
        "subject-1",
        metadata={"actor_user_id": "custom_actor_subject_1"},
    ) == "custom_actor_subject_1"
