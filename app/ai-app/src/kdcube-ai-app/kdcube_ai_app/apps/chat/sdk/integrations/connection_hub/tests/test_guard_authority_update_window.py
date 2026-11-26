"""The delegated guard reads the turn token and answers the authority-update window plainly."""

from __future__ import annotations

import json
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth import surface_guard


def test_the_turn_token_is_read_from_its_header_only():
    request = SimpleNamespace(headers={"x-connection-hub-turn": " ts1.a.b "})
    assert surface_guard._request_turn_token(request) == "ts1.a.b"
    assert surface_guard._request_turn_token(SimpleNamespace(headers={})) == ""
    assert surface_guard._request_turn_token(SimpleNamespace()) == ""


def test_a_fenced_card_answers_agent_authority_updating_with_a_retry_hint():
    response = surface_guard._live_grant_refusal("card_transaction_undecided")
    body = json.loads(response.body)
    assert response.status_code == 503 and body["reason"] == "agent_authority_updating"
    assert "being updated" in body["error_description"] and response.headers["retry-after"] == "120"


def test_other_live_grant_refusals_keep_their_own_reason():
    body = json.loads(surface_guard._live_grant_refusal("source_invalid").body)
    assert body["reason"] == "live_grant_source_invalid"
