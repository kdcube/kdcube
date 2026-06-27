from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk.integrations import integration_config


class Entrypoint:
    config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="app@1-0"))

    def __init__(self, props):
        self.props = dict(props)

    def bundle_prop(self, path, default=None):
        return self.props.get(path, default)


def test_configured_integrations_uses_map_key_as_integration_id():
    entrypoint = Entrypoint(
        {
            "integrations": {
                "telegram.kdcube_ref": {
                    "provider": "telegram",
                    "enabled": True,
                    "definition": {
                        "webhook": {
                            "send_responses": False,
                        },
                    },
                },
                "telegram.disabled": {
                    "provider": "telegram",
                    "enabled": False,
                },
            }
        }
    )

    rows = integration_config.configured_integrations(entrypoint, provider="telegram")

    assert [row["id"] for row in rows] == ["telegram.kdcube_ref", "telegram.disabled"]
    assert rows[0]["integration_id"] == "telegram.kdcube_ref"
    assert integration_config.select_integration(
        entrypoint,
        provider="telegram",
        integration_id="telegram.kdcube_ref",
    )["id"] == "telegram.kdcube_ref"
    assert (
        integration_config.integration_definition_value(
            entrypoint,
            provider="telegram",
            integration_id="telegram.kdcube_ref",
            key="send_responses",
            default=True,
        )
        is False
    )


def test_select_integration_without_id_only_when_unambiguous():
    entrypoint = Entrypoint(
        {
            "integrations": {
                "telegram.one": {"provider": "telegram", "enabled": True},
                "telegram.two": {"provider": "telegram", "enabled": True},
            }
        }
    )

    assert integration_config.select_integration(entrypoint, provider="telegram") == {}


def test_integrations_items_is_not_a_supported_shape():
    entrypoint = Entrypoint(
        {
            "integrations": {
                "items": [
                    {
                        "id": "telegram.legacy",
                        "provider": "telegram",
                        "enabled": True,
                    }
                ]
            }
        }
    )

    assert integration_config.configured_integrations(entrypoint, provider="telegram") == []


@pytest.mark.asyncio
async def test_integration_secret_value_resolves_secret_refs(monkeypatch):
    seen = []

    async def fake_get_secret(key, **kwargs):
        seen.append((key, kwargs))
        if key == "b:identity.authenticators.telegram_kdcube_ref.bot_token":
            return "token-123"
        return ""

    monkeypatch.setattr(integration_config, "get_secret", fake_get_secret)
    entrypoint = Entrypoint(
        {
            "integrations": {
                "telegram.kdcube_ref": {
                    "provider": "telegram",
                    "enabled": True,
                    "secret_refs": {
                        "bot_token": "identity.authenticators.telegram_kdcube_ref.bot_token",
                    },
                }
            }
        }
    )

    value = await integration_config.integration_secret_value(
        entrypoint,
        provider="telegram",
        integration_id="telegram.kdcube_ref",
        field="bot_token",
    )

    assert value == "token-123"
    assert seen == [
        (
            "b:identity.authenticators.telegram_kdcube_ref.bot_token",
            {"bundle_id": "app@1-0"},
        )
    ]
