"""A Card carries its agent identity once, and a reconnect re-derives only a name this service generated.

2026-09-21: every worker Card read "Connection Hub CLI · problem_board ·
claude-code:claude-main:<session> · claude-code:<session>". The registered
client name carried the alias form of the identity, the asserted
``kdcube_agent_id`` carried the plain form, a substring check could not see
that they were the same agent, and the consent page then kept that name on
every reconnect because it pre-fills the existing Card's label.
"""

from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http import (
    routes,
)

RESOURCE = "https://runtime.example/api/integrations/bundles/t/p/problem-board@1-0/public/mcp/problem_board"
SESSION = "dfd0d696-82d2-4bec-8a9c-d94493ec63a5"
STANDARD = f"Connection Hub CLI · problem_board · claude-code:claude-main:{SESSION}"


def _worker(name: str, *, provider: str = "claude-code", alias: str = "claude-main", session: str = SESSION) -> dict:
    """The full worker shape a Problem Board worker registers (local/authorization.py)."""

    return {
        "client_name": name,
        "client_metadata": {
            "kdcube_app_id": "problem-board@1-0",
            "kdcube_agent_provider": provider,
            "kdcube_worker_alias": alias,
            "kdcube_agent_session_id": session,
            "kdcube_agent_id": f"{provider}:{session}",
            "kdcube_worker_id": f"{provider}-{session}",
        },
    }


def test_the_alias_form_in_the_name_is_the_identity_and_is_not_appended_again():
    assert routes._oauth_card_label(_worker(STANDARD), resource=RESOURCE) == STANDARD


def test_a_worker_registered_under_an_older_name_gets_the_standard_name():
    # codex-ui registered "Connection Hub CLI · worker_stream · codex:<session>".
    client = _worker("Connection Hub CLI · worker_stream · codex:019e", provider="codex", alias="codex-ui", session="019e")
    assert routes._oauth_card_label(client, resource=RESOURCE) == "Connection Hub CLI · problem_board · codex:codex-ui:019e"


def test_a_client_without_an_identity_in_its_name_gets_it_exactly_once():
    client = {"client_name": "Claude Code", "client_metadata": {"kdcube_agent_id": "agent-7"}}
    assert routes._oauth_card_label(client, resource=RESOURCE) == "Claude Code · problem_board · agent-7"
    named = {"client_name": "Claude Code · agent-7", "client_metadata": {"kdcube_agent_id": "agent-7"}}
    assert routes._oauth_card_label(named, resource=RESOURCE) == "Claude Code · agent-7 · problem_board"
    assert routes._oauth_card_label({"client_name": "Some App"}, resource=RESOURCE) == "Some App · problem_board"


def test_near_collisions_are_compared_as_whole_segments():
    # Review 2026-09-21: "agent-70" is not "agent-7", and "problem_board_admin" is not "problem_board".
    client = {"client_name": "Some App · agent-70", "client_metadata": {"kdcube_agent_id": "agent-7"}}
    assert routes._oauth_card_label(client, resource=RESOURCE) == "Some App · agent-70 · problem_board · agent-7"
    door_like = {"client_name": "Some App · problem_board_admin"}
    assert routes._oauth_card_label(door_like, resource=RESOURCE) == "Some App · problem_board_admin · problem_board"


def test_a_client_asserting_part_of_the_worker_shape_keeps_the_general_rule():
    # Provider, alias and session without kdcube_worker_id is not a worker: no canonical rename.
    client = {
        "client_name": "Research Agent · nightly",
        "client_metadata": {
            "kdcube_agent_provider": "claude-code",
            "kdcube_worker_alias": "nightly",
            "kdcube_agent_session_id": "s-1",
            "kdcube_agent_id": "claude-code:s-1",
        },
    }
    assert routes._oauth_card_label(client, resource=RESOURCE) == "Research Agent · nightly · problem_board · claude-code:s-1"


def test_a_byte_identical_duplicate_segment_is_collapsed():
    client = {"client_name": "Some App · Some App", "client_metadata": {"kdcube_agent_id": "agent-7"}}
    assert routes._oauth_card_label(client, resource=RESOURCE) == "Some App · problem_board · agent-7"


def test_the_four_real_worker_cards_are_re_derived_to_the_standard_on_reconnect():
    cases = [
        (  # claude-main: the two-form duplicate
            _worker(STANDARD),
            f"{STANDARD} · claude-code:{SESSION}",
            STANDARD,
        ),
        (  # codex-main: registered at the worker_stream door
            _worker("Connection Hub CLI · worker_stream · codex:codex-main:019fb", provider="codex", alias="codex-main", session="019fb"),
            "Connection Hub CLI · worker_stream · codex:codex-main:019fb · codex:019fb",
            "Connection Hub CLI · problem_board · codex:codex-main:019fb",
        ),
        (  # codex-ui: registered at the worker_stream door without its alias
            _worker("Connection Hub CLI · worker_stream · codex:019e", provider="codex", alias="codex-ui", session="019e"),
            "Connection Hub CLI · worker_stream · codex:019e",
            "Connection Hub CLI · problem_board · codex:codex-ui:019e",
        ),
    ]
    for client, existing, expected in cases:
        derived = routes._oauth_card_label(client, resource=RESOURCE)
        assert derived == expected
        assert routes._consent_label(existing, derived, client, resource=RESOURCE) == expected


def test_a_label_a_person_chose_is_kept_on_reconnect_even_when_it_names_the_session():
    client = _worker(STANDARD)
    derived = routes._oauth_card_label(client, resource=RESOURCE)
    # Review 2026-09-21: a chosen name that happens to contain the session is still chosen.
    for chosen in ("My research worker", f"My reviewed {SESSION} worker", "Chosen name"):
        assert routes._consent_label(chosen, derived, client, resource=RESOURCE) == chosen
    assert routes._consent_label("", derived, client, resource=RESOURCE) == STANDARD


def test_an_explicit_label_submitted_from_the_consent_page_is_stored_as_given():
    assert routes._oauth_card_label(_worker(STANDARD), resource=RESOURCE, explicit="Chosen name") == "Chosen name"
