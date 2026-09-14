# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Snippet-text assembly through the SAME construction path the ingress search
route uses: make_conversation_search_backend -> real ContextBrowser -> real
ContextRAGClient -> real ConversationStore (file:// tmp). Postgres is replaced
by patching ConvIndex query methods; everything else is the production wiring.

Covers the live regression: hits rendered with kind + score but BLANK snippet
text. Asserts
  * happy path: turn-log blocks materialize from the store and snippets carry
    the clipped text;
  * degraded path: when the turn log cannot be materialized the hit still
    ships a snippet with the matched row text (retrieval-row fallback) plus a
    response warning — never silent blank snippets;
  * summary honesty: targets=["summary"] searches the working-summary arm
    (assistant role + kind:working.summary tags) and labels hits/snippets
    "summary", never "assistant".
"""

from __future__ import annotations

import json
import pathlib

import pytest

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import (
    ConversationSearchContext,
    ConversationSearchParams,
    run_conversation_search,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.search_backend import (
    make_conversation_search_backend,
)
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

TS = "2026-06-01T10:00:00+00:00"
TURN_ID = "turn-1"
CONV_ID = "conv-1"
ROW_TEXT = "Row text from the search index: acme invoice discussion"


class FakeModelService:
    async def embed_texts(self, texts):
        return [[0.1] * 8 for _ in texts]


def _index_row(*, matched_role: str = "assistant") -> dict:
    return {
        "turn_id": TURN_ID,
        "conversation_id": CONV_ID,
        "role": "assistant",
        "matched_role": matched_role,
        "ts": TS,
        "rec": 0.5,
        "sim": 0.7,
        "score": 0.7,
        "text": ROW_TEXT,
        "tags": [f"turn:{TURN_ID}"],
        "hosted_uri": None,
    }


def _turn_log_record() -> dict:
    return {
        "role": "artifact",
        "ts": TS,
        "message_id": "m-log",
        "payload": {
            "turn_id": TURN_ID,
            "ts": TS,
            "blocks": [
                {
                    "type": "user.prompt",
                    "turn_id": TURN_ID,
                    "path": f"conv:ar:{TURN_ID}.user.prompt",
                    "text": "find the acme invoice",
                    "ts": TS,
                    "meta": {},
                },
                {
                    "type": "conv.working.summary",
                    "turn_id": TURN_ID,
                    "path": f"conv:ws:{TURN_ID}.conv.working.summary",
                    "text": "Goal: find the acme invoice. Outcome: found in conv-1.",
                    "ts": TS,
                    "meta": {},
                },
            ],
        },
    }


@pytest.fixture()
def store_with_turn_log(tmp_path: pathlib.Path):
    """Real ConversationStore over file:// tmp with a persisted turn-log blob."""
    rel = f"cb/msgs/{TURN_ID}.json"
    blob = tmp_path / rel
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text(json.dumps(_turn_log_record()), encoding="utf-8")
    store = ConversationStore(f"file://{tmp_path}")
    return store, f"file://{tmp_path}/{rel}"


class _ArmRecorder:
    """Patches ConvIndex query methods; records each arm's roles/tags."""

    def __init__(self, monkeypatch, *, rows, turn_log_rows):
        self.arm_calls: list[dict] = []
        self.fetch_recent_calls: list[dict] = []
        recorder = self

        async def _content(self_idx, **kwargs):
            recorder.arm_calls.append({"arm": "semantic", **_arm_scope(kwargs)})
            return [dict(r) for r in rows]

        async def _lexical(self_idx, **kwargs):
            recorder.arm_calls.append({"arm": "lexical", **_arm_scope(kwargs)})
            return [dict(r) for r in rows]

        async def _trigram(self_idx, **kwargs):
            recorder.arm_calls.append({"arm": "trigram", **_arm_scope(kwargs)})
            return []

        async def _fetch_recent(self_idx, **kwargs):
            recorder.fetch_recent_calls.append(dict(kwargs))
            return [dict(r) for r in turn_log_rows]

        def _arm_scope(kwargs):
            return {
                "search_roles": tuple(kwargs.get("search_roles") or ()),
                "search_tags": list(kwargs.get("search_tags") or []) or None,
                "bundle_id": kwargs.get("bundle_id"),
            }

        monkeypatch.setattr(ConvIndex, "search_turn_logs_via_content", _content)
        monkeypatch.setattr(ConvIndex, "search_turn_logs_via_content_lexical", _lexical)
        monkeypatch.setattr(ConvIndex, "search_turn_logs_via_content_trigram", _trigram)
        monkeypatch.setattr(ConvIndex, "fetch_recent", _fetch_recent)


def _make_backend(store):
    # EXACTLY what the ingress route builds (fake pg pool: patched ConvIndex
    # never touches it).
    return make_conversation_search_backend(
        pg_pool=object(),
        tenant="tenant-a",
        project="project-b",
        model_service=FakeModelService(),
        store=store,
        user_id="user-1",
        conversation_id=CONV_ID,
    )


def _context() -> ConversationSearchContext:
    return ConversationSearchContext(
        user_id="user-1",
        conversation_id=CONV_ID,
        bundle_id="bundle-default",
        tenant="tenant-a",
        project="project-b",
    )


async def _search(backend, *, targets):
    return await run_conversation_search(
        context=_context(),
        params=ConversationSearchParams(query="acme invoice", targets=targets, scope="user", top_k=5),
        search_backend=backend,
    )


@pytest.mark.asyncio
async def test_snippet_text_assembles_from_store_through_ingress_construction(
    monkeypatch, store_with_turn_log,
):
    store, hosted_uri = store_with_turn_log
    turn_log_row = {
        "id": 1,
        "message_id": "m-log",
        "role": "artifact",
        "text": "{}",
        "ts": TS,
        "tags": ["kind:turn.log", "artifact:turn.log"],
        "turn_id": TURN_ID,
        "bundle_id": None,
        "hosted_uri": hosted_uri,
    }
    _ArmRecorder(monkeypatch, rows=[_index_row()], turn_log_rows=[turn_log_row])

    result = await _search(_make_backend(store), targets=["user", "summary"])

    assert len(result.hits) == 1
    snippets = result.hits[0]["snippets"]
    by_role = {sn["role"]: sn for sn in snippets}
    # The turn-log blocks materialized from the REAL store and carry text.
    assert by_role["user"]["text"] == "find the acme invoice"
    assert "acme invoice" in by_role["summary"]["text"]
    assert all((sn.get("text") or "").strip() for sn in snippets)
    # No fallback needed, so no degradation warning.
    assert result.warnings == []


@pytest.mark.asyncio
async def test_missing_turn_log_falls_back_to_retrieval_row_text(monkeypatch, tmp_path):
    # Turn log row exists in no store/index: materialization yields nothing.
    store = ConversationStore(f"file://{tmp_path}")
    _ArmRecorder(monkeypatch, rows=[_index_row(matched_role="assistant")], turn_log_rows=[])

    result = await _search(_make_backend(store), targets=["user", "assistant"])

    assert len(result.hits) == 1
    snippets = result.hits[0]["snippets"]
    # The hit is not dropped and not blank: the matched row text ships.
    assert len(snippets) == 1
    assert snippets[0]["text"] == ROW_TEXT
    assert snippets[0]["meta"] == {"source": "retrieval_row"}
    assert any("turn log snippets unavailable" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_unreadable_turn_log_prefers_the_direct_matched_text(monkeypatch, tmp_path):
    store = ConversationStore(f"file://{tmp_path}")
    row = _index_row(matched_role="assistant")
    row.update(text="opaque turn-log row", matched_text=ROW_TEXT)
    _ArmRecorder(monkeypatch, rows=[row], turn_log_rows=[])

    result = await _search(_make_backend(store), targets=["assistant"])

    assert result.hits[0]["snippets"][0]["text"] != "opaque turn-log row"
    assert result.hits[0]["snippets"][0]["text"] == ROW_TEXT
    assert result.hits[0]["snippets"][0]["meta"] == {"source": "retrieval_row"}


@pytest.mark.asyncio
async def test_unreadable_hosted_uri_falls_back_to_retrieval_row_text(monkeypatch, tmp_path):
    # Index has the turn-log row, but its blob is missing from the store (the
    # live mis-wired-store shape): assembly degrades to the row text.
    store = ConversationStore(f"file://{tmp_path}")
    turn_log_row = {
        "id": 1,
        "message_id": "m-log",
        "role": "artifact",
        "text": "{}",
        "ts": TS,
        "tags": ["kind:turn.log", "artifact:turn.log"],
        "turn_id": TURN_ID,
        "bundle_id": None,
        "hosted_uri": f"file://{tmp_path}/cb/msgs/does-not-exist.json",
    }
    _ArmRecorder(monkeypatch, rows=[_index_row()], turn_log_rows=[turn_log_row])

    result = await _search(_make_backend(store), targets=["assistant"])

    assert len(result.hits) == 1
    snippets = result.hits[0]["snippets"]
    assert len(snippets) == 1
    assert snippets[0]["text"] == ROW_TEXT
    assert any("turn log snippets unavailable" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_summary_target_scopes_to_working_summary_rows_and_labels_summary(
    monkeypatch, store_with_turn_log,
):
    store, hosted_uri = store_with_turn_log
    turn_log_row = {
        "id": 1,
        "message_id": "m-log",
        "role": "artifact",
        "text": "{}",
        "ts": TS,
        "tags": ["kind:turn.log", "artifact:turn.log"],
        "turn_id": TURN_ID,
        "bundle_id": None,
        "hosted_uri": hosted_uri,
    }
    recorder = _ArmRecorder(monkeypatch, rows=[_index_row()], turn_log_rows=[turn_log_row])

    result = await _search(_make_backend(store), targets=["summary"])

    # The retriever arm was scoped to working-summary rows, not all assistant rows.
    assert recorder.arm_calls, "retriever arms were not called"
    for call in recorder.arm_calls:
        assert call["search_roles"] == ("assistant",)
        assert "kind:working.summary" in (call["search_tags"] or [])
        assert call["bundle_id"] == "bundle-default"

    assert len(result.hits) == 1
    hit = result.hits[0]
    # Honest labeling: matched via the summary target, summary-roled snippets only.
    assert hit["matched_via_role"] == "summary"
    roles = {sn["role"] for sn in hit["snippets"]}
    assert roles == {"summary"}
    assert all((sn.get("text") or "").strip() for sn in hit["snippets"])


@pytest.mark.asyncio
async def test_summary_fallback_snippet_is_labeled_summary(monkeypatch, tmp_path):
    # Even on the degraded path, a summary-targeted hit never shows up as
    # "assistant": the fallback snippet carries the summary label.
    store = ConversationStore(f"file://{tmp_path}")
    _ArmRecorder(monkeypatch, rows=[_index_row()], turn_log_rows=[])

    result = await _search(_make_backend(store), targets=["summary"])

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit["matched_via_role"] == "summary"
    assert [sn["role"] for sn in hit["snippets"]] == ["summary"]
    assert hit["snippets"][0]["text"] == ROW_TEXT
