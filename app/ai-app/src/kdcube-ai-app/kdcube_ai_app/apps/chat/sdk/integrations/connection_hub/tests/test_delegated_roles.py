# SPDX-License-Identifier: MIT

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_roles import (
    delegated_role_projection,
)


def test_explicit_card_role_replaces_grantor_role() -> None:
    projection = delegated_role_projection(
        ["kdcube:role:registered", "reports:read"],
        fallback_roles=["kdcube:role:super-admin"],
    )

    assert projection.selected_on_card is True
    assert projection.roles == ("kdcube:role:registered",)
    assert projection.user_type == "registered"


def test_legacy_card_without_role_keeps_existing_projection() -> None:
    projection = delegated_role_projection(
        ["reports:read"],
        fallback_roles=["kdcube:role:super-admin"],
    )

    assert projection.selected_on_card is False
    assert projection.roles == ("kdcube:role:super-admin",)
    assert projection.user_type == "external"
