# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""REST contract of POST /api/cb/conversations/{tenant}/{project}/search.

Follows the TestClient pattern of the other ingress tests: real router wiring
(mount_conversations_router), auth swapped via FastAPI dependency_overrides,
bundle-registry helpers and the search backend factory monkeypatched at the
search module's globals.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kdcube_ai_app.apps.chat.ingress.conversations import mount_conversations_router
from kdcube_ai_app.apps.chat.ingress.conversations import search as search_mod

TENANT = "tenant-a"
PROJECT = "project-b"
SEARCH_URL = f"/api/cb/conversations/{TENANT}/{PROJECT}/search"


class FakeSearchBackend:
    """Records call kwargs; serves one hybrid hit and one catalog row."""

    def __init__(self):
        self.search_kwargs = None
        self.catalog_kwargs = None

    async def search(self, **kwargs):
        self.search_kwargs = kwargs
        return "turn-1", [{
            "turn_id": "turn-1",
            "conversation_id": "conv-hit",
            "score": 0.91,
            "sim": 0.82,
            "rec": 0.73,
            "matched_via_role": "assistant",
            "source_query": kwargs["targets"][0]["query"] if kwargs.get("targets") else "",
            "text": "matched row text: invoice thread",
            "ts": "2026-06-01T10:00:00+00:00",
        }]

    async def search_turn_catalog(self, **kwargs):
        self.catalog_kwargs = kwargs
        return [{
            "turn_id": "turn-2",
            "conversation_id": "conv-hit",
            "ordinal": 3,
            "total_turns": 9,
            "started_at": "2026-05-01T00:00:00+00:00",
            "first_user_text": "hello from may",
            "user_path": "conv:ar:turn-2.user.prompt",
            "first_user_ts": "2026-05-01T00:00:00+00:00",
        }]

    async def get_turn_log(self, *, turn_id, conversation_id=None, bundle_id=None):
        return {
            "blocks": [{
                "type": "user.prompt",
                "turn_id": turn_id,
                "path": f"conv:ar:{turn_id}.user.prompt",
                "text": "find the invoice from acme",
                "ts": "2026-06-01T10:00:00+00:00",
                "meta": {},
            }],
            "sources_pool": [],
        }


class FakeConversationBrowser:
    """Only what the search route touches: model_service + list_conversations."""

    def __init__(self):
        self.model_service = object()
        self.list_kwargs = None

    async def list_conversations(self, **kwargs):
        self.list_kwargs = kwargs
        return {
            "user_id": kwargs.get("user_id"),
            "items": [
                {"conversation_id": "conv-hit", "title": "Acme invoices",
                 "last_activity_at": "2026-06-02T08:00:00+00:00"},
                {"conversation_id": "conv-unrelated", "title": "Other",
                 "last_activity_at": "2026-06-03T08:00:00+00:00"},
            ],
        }


@pytest.fixture()
def harness(monkeypatch):
    app = FastAPI()
    browser = FakeConversationBrowser()
    app.state.conversation_browser = browser
    app.state.conversation_store = object()
    app.state.pg_pool = object()
    mount_conversations_router(app)

    session = SimpleNamespace(user_id="user-1", session_id="sess-1", user_type="registered")
    app.dependency_overrides[search_mod._user_session_dep] = lambda: session

    backend = FakeSearchBackend()
    factory_kwargs = {}

    def _fake_backend_factory(**kwargs):
        factory_kwargs.update(kwargs)
        return backend

    resolutions = {"default": 0, "allowed": [], "scoped": []}

    async def _fake_default(tenant, project, bundle_id):
        resolutions["default"] += 1
        assert bundle_id is None
        return "bundle-default"

    async def _fake_allowed(tenant, project, bundle_id=None):
        resolutions["allowed"].append(bundle_id)
        return [bundle_id] if bundle_id else ["bundle-default"]

    async def _fake_in_scope(tenant, project, *, user_id, conversation_id, bundle_id=None):
        resolutions["scoped"].append((user_id, conversation_id, bundle_id))
        return ["bundle-default"]

    monkeypatch.setattr(search_mod, "make_conversation_search_backend", _fake_backend_factory)
    monkeypatch.setattr(search_mod, "_resolve_bundle_id_or_default", _fake_default)
    monkeypatch.setattr(search_mod, "_resolve_allowed_bundle_ids_or_404", _fake_allowed)
    monkeypatch.setattr(search_mod, "_ensure_conversation_in_scope_or_404", _fake_in_scope)

    return SimpleNamespace(
        client=TestClient(app),
        backend=backend,
        browser=browser,
        factory_kwargs=factory_kwargs,
        resolutions=resolutions,
    )


# -------------------- Validation (mirrors the widget's gating) --------------------

def test_blank_query_without_range_is_400(harness):
    resp = harness.client.post(SEARCH_URL, json={"query": "  "})
    assert resp.status_code == 400
    assert "temporal browse" in resp.json()["detail"]
    assert harness.backend.search_kwargs is None
    assert harness.backend.catalog_kwargs is None


def test_unknown_target_is_400(harness):
    resp = harness.client.post(SEARCH_URL, json={"query": "invoice", "targets": ["assistant", "bogus"]})
    assert resp.status_code == 400
    assert "bogus" in resp.json()["detail"]


def test_empty_targets_is_400(harness):
    resp = harness.client.post(SEARCH_URL, json={"query": "invoice", "targets": []})
    assert resp.status_code == 400


def test_conversation_scope_requires_conversation_id(harness):
    resp = harness.client.post(SEARCH_URL, json={"query": "invoice", "scope": "conversation"})
    assert resp.status_code == 400
    assert "conversation_id" in resp.json()["detail"]


def test_unknown_scope_is_400(harness):
    resp = harness.client.post(SEARCH_URL, json={"query": "invoice", "scope": "agent"})
    assert resp.status_code == 400


def test_invalid_from_ts_is_400(harness):
    resp = harness.client.post(SEARCH_URL, json={"query": "", "from_ts": "yesterday-ish"})
    assert resp.status_code == 400
    assert "from_ts" in resp.json()["detail"]


def test_limit_bounds_are_enforced(harness):
    assert harness.client.post(SEARCH_URL, json={"query": "x", "limit": 0}).status_code == 422
    assert harness.client.post(SEARCH_URL, json={"query": "x", "limit": 51}).status_code == 422


# -------------------- Hybrid path --------------------

def test_hybrid_search_contract(harness):
    resp = harness.client.post(SEARCH_URL, json={
        "query": "acme invoice",
        "targets": ["user", "assistant"],
        "limit": 7,
        "weights": {"semantic": 9.0, "recency": 0.0},
    })
    assert resp.status_code == 200
    body = resp.json()

    # Identity is the session user — the hard boundary.
    assert body["user_id"] == "user-1"
    assert harness.backend.search_kwargs["user"] == "user-1"
    assert harness.factory_kwargs["user_id"] == "user-1"
    assert harness.factory_kwargs["tenant"] == TENANT
    assert harness.factory_kwargs["project"] == PROJECT

    # scope defaults to user; limit maps to top_k over-fetch downstream.
    assert harness.backend.search_kwargs["scope"] == "user"
    assert harness.backend.search_kwargs["scoring_mode"] == "rrf_hybrid"

    # Weights are clamped to [0, 2] and forwarded.
    assert harness.backend.search_kwargs["rank_weights"] == {"semantic": 2.0, "recency": 0.0}

    assert body["effective_mode"] == "hybrid"
    assert body["warnings"] == []
    assert len(body["hits"]) == 1
    hit = body["hits"][0]
    assert hit["conversation_id"] == "conv-hit"
    assert hit["turn_id"] == "turn-1"
    assert hit["score"] == 0.91
    assert hit["sim_score"] == 0.82
    assert hit["recency_score"] == 0.73
    assert hit["matched_via_role"] == "assistant"
    assert hit["ordinal"] is None and hit["total_turns"] is None
    assert hit["ts"] == "2026-06-01T10:00:00+00:00"
    assert hit["snippets"] == [{
        "role": "user",
        "text": "find the invoice from acme",
        "ts": "2026-06-01T10:00:00+00:00",
        "path": "conv:ar:turn-1.user.prompt",
    }]

    # Titles are enriched server-side from the list_conversations source.
    assert body["conversations"] == {
        "conv-hit": {"title": "Acme invoices", "last_activity_at": "2026-06-02T08:00:00+00:00"},
    }
    assert harness.browser.list_kwargs["user_id"] == "user-1"
    assert harness.browser.list_kwargs["bundle_id"] == "bundle-default"
    assert harness.resolutions["default"] == 1


def test_no_weights_keeps_search_call_shape_unchanged(harness):
    resp = harness.client.post(SEARCH_URL, json={"query": "acme invoice"})
    assert resp.status_code == 200
    assert "rank_weights" not in harness.backend.search_kwargs


def test_conversation_scope_checks_ownership_and_forwards_anchor(harness):
    resp = harness.client.post(SEARCH_URL, json={
        "query": "acme invoice",
        "scope": "conversation",
        "conversation_id": "conv-hit",
    })
    assert resp.status_code == 200
    assert harness.resolutions["scoped"] == [("user-1", "conv-hit", None)]
    assert harness.backend.search_kwargs["scope"] == "conversation"
    assert harness.backend.search_kwargs["conv"] == "conv-hit"
    assert harness.factory_kwargs["conversation_id"] == "conv-hit"


def test_explicit_bundle_id_is_validated_and_used(harness):
    resp = harness.client.post(SEARCH_URL, json={"query": "acme", "bundle_id": "bundle-x"})
    assert resp.status_code == 200
    assert harness.resolutions["allowed"] == ["bundle-x"]
    assert harness.resolutions["default"] == 0
    assert harness.browser.list_kwargs["bundle_id"] == "bundle-x"


def test_agent_bound_search_narrows_to_that_agent(harness):
    # An agent-bound widget (one chat per agent) passes agent_id. The whole-history
    # ("user") search is promoted to the backend's agent scope — user-wide, filtered
    # by agent_id — and the title enrichment is scoped the same way, so the tile
    # never surfaces a sibling agent's conversations.
    resp = harness.client.post(SEARCH_URL, json={"query": "invoice", "agent_id": "lg-solution"})
    assert resp.status_code == 200
    assert harness.backend.search_kwargs["agent_id"] == "lg-solution"
    assert harness.backend.search_kwargs["scope"] == "user"  # agent scope is user-wide
    assert harness.browser.list_kwargs["agent_id"] == "lg-solution"


def test_no_agent_id_searches_across_all_agents(harness):
    # A plain (unbound) widget sends no agent_id — search spans every agent in the
    # bundle, exactly as before (agent_id filter absent).
    resp = harness.client.post(SEARCH_URL, json={"query": "invoice"})
    assert resp.status_code == 200
    assert harness.backend.search_kwargs["agent_id"] is None
    assert harness.browser.list_kwargs["agent_id"] is None


def test_summary_target_maps_to_summary_arm(harness):
    resp = harness.client.post(SEARCH_URL, json={"query": "acme", "targets": ["summary"]})
    assert resp.status_code == 200
    # The orchestrator forwards the summary target as its OWN arm; the retriever
    # scopes it to working-summary rows — never the plain assistant arm.
    assert harness.backend.search_kwargs["targets"] == [{"where": "summary", "query": "acme"}]


def test_blank_turn_log_falls_back_to_row_text_with_warning(harness):
    async def _empty_turn_log(*, turn_id, conversation_id=None):
        return {}

    harness.backend.get_turn_log = _empty_turn_log
    resp = harness.client.post(SEARCH_URL, json={"query": "acme invoice"})
    assert resp.status_code == 200
    body = resp.json()
    # Never all-blank snippets: the matched row text ships with a warning.
    hit = body["hits"][0]
    assert len(hit["snippets"]) == 1
    assert hit["snippets"][0]["text"] == "matched row text: invoice thread"
    assert any("turn log snippets unavailable" in w for w in body["warnings"])


# -------------------- Temporal browse (blank query + range) --------------------

def test_blank_query_with_range_runs_temporal_browse(harness):
    resp = harness.client.post(SEARCH_URL, json={
        "query": "",
        "from_ts": "2026-05-01T00:00:00Z",
        "to_ts": "2026-06-01T00:00:00Z",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["effective_mode"] == "temporal"
    assert harness.backend.search_kwargs is None  # no hybrid call
    assert harness.backend.catalog_kwargs["from_ts"] == "2026-05-01T00:00:00Z"
    assert harness.backend.catalog_kwargs["to_ts"] == "2026-06-01T00:00:00Z"
    hit = body["hits"][0]
    assert hit["turn_id"] == "turn-2"
    assert hit["ordinal"] == 3
    assert hit["total_turns"] == 9
    assert hit["snippets"][0]["role"] == "user"
    assert body["conversations"]["conv-hit"]["title"] == "Acme invoices"


# -------------------- Degradation --------------------

def test_title_enrichment_failure_does_not_fail_search(harness):
    async def _boom(**kwargs):
        raise RuntimeError("titles down")

    harness.browser.list_conversations = _boom
    resp = harness.client.post(SEARCH_URL, json={"query": "acme invoice"})
    assert resp.status_code == 200
    # The hit conversation still gets a (blank) entry — one-response contract.
    assert resp.json()["conversations"] == {
        "conv-hit": {"title": None, "last_activity_at": None},
    }
