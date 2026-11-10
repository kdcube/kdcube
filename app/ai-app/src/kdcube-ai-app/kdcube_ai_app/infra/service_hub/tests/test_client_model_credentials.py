# SPDX-License-Identifier: MIT
"""Client-supplied config never carries a model credential into a turn.

``ConfigRequest`` declares the provider credential fields, and
``resolve_config_request_secrets`` keeps any field that is already set above
every server-side key lookup. Client config reaches ``ConfigRequest`` through
the chat ingress, the processor, and the REST operation door; each drops the
credential fields. The forbidden set is derived from the credential table and
cross-checked against ``ConfigRequest`` so a newly declared credential field
cannot quietly reopen the door.
"""
from __future__ import annotations

import logging
import re
from types import SimpleNamespace

import kdcube_ai_app.infra.service_hub.inventory as inventory
import kdcube_ai_app.infra.service_hub.model_credentials as mc

_CREDENTIAL_NAME = re.compile(r"(api_key|_key|_token|_secret|_password)$")


def test_every_credential_like_config_request_field_is_forbidden_to_clients():
    declared = set(inventory.ConfigRequest.model_fields)
    credential_like = {name for name in declared if _CREDENTIAL_NAME.search(name)}

    assert credential_like, "the cross-check must see the declared credential fields"
    assert credential_like <= mc.CLIENT_FORBIDDEN_CONFIG_KEYS
    assert mc.CLIENT_FORBIDDEN_CONFIG_KEYS <= declared


def test_strip_drops_credentials_in_any_case_and_keeps_everything_else(caplog):
    values = {
        "claude_api_key": "sk-secret-1",
        " Google_API_KEY ": "sk-secret-2",
        "role_models": {"answer": {"provider": "anthropic", "model": "m"}},
        "tenant": "t",
    }

    with caplog.at_level(logging.WARNING, logger=mc.logger.name):
        kept = mc.strip_client_model_credentials(values, door="test", subject="sess-1")

    assert kept == {"role_models": values["role_models"], "tenant": "t"}
    assert "claude_api_key" in values  # the caller's mapping is not mutated
    assert "sk-secret" not in caplog.text
    assert "claude_api_key" in caplog.text and "sess-1" in caplog.text
    assert mc.strip_client_model_credentials(None, door="test") == {}


def test_the_processor_builds_the_turn_config_without_client_credentials():
    from kdcube_ai_app.apps.chat.proc.web_app import _turn_config_request

    payload = SimpleNamespace(
        config=SimpleNamespace(values={
            "claude_api_key": "sk-client",
            "custom_model_api_key": "sk-client-2",
            "role_models": {"answer": {"provider": "anthropic", "model": "m"}},
        }),
        routing=SimpleNamespace(session_id="sess-1"),
    )

    request = _turn_config_request(payload)

    assert request.claude_api_key is None
    assert request.custom_model_api_key is None
    assert request.role_models == {"answer": {"provider": "anthropic", "model": "m"}}

