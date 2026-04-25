# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Unit tests for the advanced-RAG pipeline pure helpers and rerank extension.

These tests avoid model / DB / cross-encoder dependencies — they exercise the
deterministic Python logic on top of which the LLM-driven steps are layered.
"""

from __future__ import annotations

import asyncio
import types

import pytest


# ============================================================================
# 1) Compound rerank mode (cross_encoder_rerank)
# ============================================================================

def _stub_cross_encoder(scores):
    """Build a stub cross-encoder returning fixed scores (in order)."""
    iter_scores = iter(scores)

    class _CE:
        def predict(self, pairs, convert_to_numpy=True):
            import numpy as np
            picked = []
            for _ in range(len(pairs)):
                try:
                    picked.append(next(iter_scores))
                except StopIteration:
                    picked.append(0.0)
            return np.array(picked, dtype=float)

    return _CE()


def test_compound_rerank_plain_mode_unchanged_behaviour():
    from kdcube_ai_app.infra.rerank.rerank import cross_encoder_rerank

    ce = _stub_cross_encoder([3.0, 1.0, 2.0])

    candidates = [
        {"id": "a", "text": "alpha"},
        {"id": "b", "text": "bravo"},
        {"id": "c", "text": "charlie"},
    ]
    out = cross_encoder_rerank("q", candidates, column_name="text", cross_encoder=ce)
    # plain mode: ordered purely by ce score (sigmoid is monotonic)
    assert [c["id"] for c in out] == ["a", "c", "b"]
    assert all("rerank_score" in c for c in out)


def test_compound_rerank_compound_mode_blends_components():
    from kdcube_ai_app.infra.rerank.rerank import cross_encoder_rerank

    # ce scores roughly equal so the vec / kw / priority components dominate
    ce = _stub_cross_encoder([0.0, 0.0, 0.0])

    candidates = [
        {"id": "a", "text": "the alpha doc",   "semantic_score": 0.10, "tags": []},
        {"id": "b", "text": "bravo and stuff", "semantic_score": 0.90, "tags": []},
        {"id": "c", "text": "charlie matches", "semantic_score": 0.10, "tags": ["important"]},
    ]
    out = cross_encoder_rerank(
        "alpha bravo",
        candidates,
        column_name="text",
        cross_encoder=ce,
        top_k=None,
        mode="compound",
        weights={"rerank": 0.0, "vec": 0.5, "kw": 0.3, "priority": 0.2},
        priority_keys=["important"],
    )
    # b has the highest semantic_score; c has priority + some kw; a has only kw on "alpha"
    # With ce neutral, ordering should favour high vec (b) and priority (c) over a.
    ids = [c["id"] for c in out]
    assert ids[0] in ("b", "c")
    assert "rerank_components" in out[0]
    comps = out[0]["rerank_components"]
    assert set(comps.keys()) == {"ce", "vec", "kw", "priority"}


def test_compound_rerank_min_priority_slots_promotes():
    from kdcube_ai_app.infra.rerank.rerank import cross_encoder_rerank

    # Stack the deck so non-priority rows would otherwise win
    ce = _stub_cross_encoder([5.0, 4.0, 1.0])

    candidates = [
        {"id": "x", "text": "irrelevant",  "semantic_score": 0.9, "tags": []},
        {"id": "y", "text": "irrelevant2", "semantic_score": 0.85, "tags": []},
        {"id": "z", "text": "important",   "semantic_score": 0.20, "tags": ["important"]},
    ]
    out = cross_encoder_rerank(
        "q",
        candidates,
        column_name="text",
        cross_encoder=ce,
        top_k=2,
        mode="compound",
        weights={"rerank": 0.6, "vec": 0.4, "kw": 0.0, "priority": 0.0},
        priority_keys=["important"],
        min_priority_slots=1,
    )
    ids = [c["id"] for c in out]
    assert "z" in ids, "min_priority_slots=1 must keep z in the top window"
    assert len(ids) == 2


def test_compound_rerank_unknown_mode_raises():
    from kdcube_ai_app.infra.rerank.rerank import cross_encoder_rerank
    ce = _stub_cross_encoder([0.5])

    with pytest.raises(ValueError):
        cross_encoder_rerank("q", [{"id": 1, "text": "x"}], cross_encoder=ce, mode="bogus")


# ============================================================================
# 2) Pipeline knob extraction (settings → pipeline knobs)
# ============================================================================

def test_adv_settings_reuses_hybrid_fields():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.pipeline import _adv_settings

    rt_ctx = types.SimpleNamespace(
        search_settings={
            "hybrid": {
                "enabled": True,
                "top_k_vector": 12,
                "use_reranking": False,
                "min_score_threshold": 0.4,
                "context_window": 2,
                "distance_type": "l2",
                "w_sem": 0.7,
                "w_bm25": 0.3,
            },
            "advancedRag": {
                "enable_query_rewrite": False,
                "enable_entity_pass": True,
                "entity_top_k": 9,
                "min_priority_slots": 2,
            },
        },
    )
    knobs = _adv_settings(rt_ctx)
    assert knobs["enabled"] is True
    assert knobs["ui_top_k"] == 12
    assert knobs["compound_rerank"] is False        # follows hybrid.use_reranking
    assert knobs["min_score_threshold"] == 0.4
    assert knobs["neighbor_window"] == 2            # follows hybrid.context_window
    assert knobs["distance_type"] == "l2"
    assert knobs["w_sem"] == 0.7
    assert knobs["w_bm25"] == 0.3
    assert knobs["rewrite"] is False
    assert knobs["entity_pass"] is True
    assert knobs["entity_top_k"] == 9
    assert knobs["min_priority_slots"] == 2


def test_adv_settings_defaults_when_empty():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.pipeline import _adv_settings

    rt_ctx = types.SimpleNamespace(search_settings={})
    knobs = _adv_settings(rt_ctx)
    assert knobs["enabled"] is True
    assert knobs["rewrite"] is True
    assert knobs["entity_pass"] is True
    assert knobs["compound_rerank"] is True
    assert knobs["neighbor_window"] == 0
    assert knobs["ui_top_k"] == 0


# ============================================================================
# 3) Merge & dedup
# ============================================================================

def test_merge_dedup_keeps_highest_semantic_score():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.pipeline import _merge_dedup

    a = [
        {"resource_id": "r1", "version": 1, "id": "s1", "semantic_score": 0.4},
        {"resource_id": "r1", "version": 1, "id": "s2", "semantic_score": 0.6},
    ]
    b = [
        {"resource_id": "r1", "version": 1, "id": "s1", "semantic_score": 0.9},  # higher → wins
        {"resource_id": "r2", "version": 1, "id": "s3", "semantic_score": 0.5},
    ]
    out = _merge_dedup(a, b)
    by_id = {(r["resource_id"], r["version"], r["id"]): r for r in out}
    assert by_id[("r1", 1, "s1")]["semantic_score"] == 0.9
    assert by_id[("r1", 1, "s2")]["semantic_score"] == 0.6
    assert by_id[("r2", 1, "s3")]["semantic_score"] == 0.5
    assert len(out) == 3


def test_merge_dedup_drops_rows_without_id():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.pipeline import _merge_dedup

    a = [{"resource_id": "r", "version": 1, "id": ""}]  # invalid
    b = [{"resource_id": "r", "version": 1, "id": "good"}]
    out = _merge_dedup(a, b)
    assert len(out) == 1
    assert out[0]["id"] == "good"


# ============================================================================
# 4) Source shaping
# ============================================================================

def test_shape_source_prefers_datasource_metadata():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.pipeline import _shape_source

    row = {
        "id": "seg1",
        "version": 3,
        "resource_id": "doc-1",
        "rn": "rn://doc-1",
        "title": "",
        "content": "body text",
        "summary": "the gist",
        "provider": "kb",
        "rerank_score": 0.7,
        "semantic_score": 0.55,
        "extensions": {
            "datasource": {"title": "Real Title", "uri": "https://example.com/x", "provider": "kb"},
        },
    }
    out = _shape_source(row, sid=4)
    assert out["sid"] == 4
    assert out["title"] == "Real Title"
    assert out["url"] == "https://example.com/x"
    assert out["text"] == "body text"
    assert out["scores"]["rerank"] == 0.7
    assert out["scores"]["semantic"] == 0.55


# ============================================================================
# 5) Entity hallucination filter
# ============================================================================

def test_entity_filter_drops_hallucinations():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.entity_extract import _filter_in_question

    q = "Does the FooBar 3000 support TLS 1.2 with model bge-large?"
    raw = ["FooBar 3000", "TLS 1.2", "bge-large", "GPT-9000-Hyperdrive", "support"]
    out = _filter_in_question(q, raw)
    assert "FooBar 3000" in out
    assert "TLS 1.2" in out
    assert "bge-large" in out
    assert "GPT-9000-Hyperdrive" not in out  # never appeared in question
    # "support" is too short / generic but technically appears; that's fine,
    # the LLM-side prompt already excludes generic verbs. We just verify dedup
    # by case + length filtering didn't accidentally drop the legit ones.


def test_entity_filter_dedup_case_insensitive():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.entity_extract import _filter_in_question

    q = "Compare Postgres and postgres performance"
    out = _filter_in_question(q, ["Postgres", "postgres"])
    assert len(out) == 1


# ============================================================================
# 6) Query-rewrite history formatter
# ============================================================================

def test_format_history_truncates_and_orders():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.query_rewrite import _format_history

    history = [
        {"role": "user",      "content": "first"},
        {"role": "assistant", "content": "answer 1"},
        {"role": "user",      "content": "second"},
        {"role": "ai",        "content": "answer 2"},
    ]
    out = _format_history(history, limit=3)
    lines = out.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("Assistant: answer 1")
    assert lines[-1].startswith("Assistant: answer 2")


def test_format_history_handles_block_content():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.query_rewrite import _format_history

    history = [
        {"role": "user",
         "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]},
    ]
    out = _format_history(history, limit=5)
    assert "hello" in out and "world" in out


# ============================================================================
# 7) Query rewrite — graceful fallback when no history / no model
# ============================================================================

def test_rewrite_returns_query_unchanged_when_no_history():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.query_rewrite import rewrite_for_retrieval

    fake_service = object()  # would normally be the model service; never reached
    out = asyncio.run(rewrite_for_retrieval(query="raw question", history=[], model_service=fake_service))
    assert out == "raw question"


def test_rewrite_returns_query_unchanged_when_no_service():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.query_rewrite import rewrite_for_retrieval

    out = asyncio.run(rewrite_for_retrieval(
        query="raw question",
        history=[{"role": "user", "content": "earlier"}],
        model_service=None,
    ))
    assert out == "raw question"


# ============================================================================
# 8) Entity extract — graceful fallback when no service / empty input
# ============================================================================

def test_extract_entities_no_service_returns_empty():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.entity_extract import extract_entities

    out = asyncio.run(extract_entities(query="something", model_service=None))
    assert out == []


def test_extract_entities_empty_query_returns_empty():
    from kdcube_ai_app.apps.chat.sdk.tools._advanced_rag_internal.entity_extract import extract_entities

    fake_service = object()
    out = asyncio.run(extract_entities(query="   ", model_service=fake_service))
    assert out == []
