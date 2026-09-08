# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Weighted rrf_hybrid fusion in search_context, on a fully seeded scoring path.

The fake conv index returns fixed row orders for the semantic / lexical /
trigram arms so the RRF math is deterministic. Assertions:

* no weights == weights {1,1,1} — EXACT float equality (byte-identical math);
* recency=0 removes the recency lift and flips the seeded ordering;
* lexical weight scales BOTH the lexical and trigram arms;
* semantic weight scales the semantic arm.
"""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.ctx_rag import search_context


def _row(turn_id: str, *, rec: float) -> dict:
    return {
        "turn_id": turn_id,
        "conversation_id": "conv-1",
        "role": "assistant",
        "ts": "2026-06-01T00:00:00Z",
        "rec": rec,
        "sim": 0.0,
        "score": 0.0,
        "text": f"text of {turn_id}",
    }


class FakeConvIndex:
    """Seeded retriever arms: each returns rows already ordered by relevance."""

    def __init__(self, *, sem: list[dict], lex: list[dict], trgm: list[dict]):
        self._sem = sem
        self._lex = lex
        self._trgm = trgm
        self.semantic_calls = 0

    async def search_turn_logs_via_content(self, **kwargs):
        self.semantic_calls += 1
        return list(self._sem)

    async def search_turn_logs_via_content_lexical(self, **kwargs):
        return list(self._lex)

    async def search_turn_logs_via_content_trigram(self, **kwargs):
        return list(self._trgm)


class FakeModelService:
    async def embed_texts(self, texts):
        return [[0.0] * 8 for _ in texts]


class FailingModelService:
    """Embedding denied/unavailable: the semantic arm must degrade to empty."""

    async def embed_texts(self, texts):
        raise RuntimeError("embedding unavailable")


_DEFAULT_MODEL_SERVICE = object()


async def _run(
    conv_idx,
    *,
    rank_weights=None,
    model_service=_DEFAULT_MODEL_SERVICE,
    **overrides,
):
    kwargs = dict(
        conv_idx=conv_idx,
        ctx_client=object(),  # unused: with_payload=False
        model_service=(
            FakeModelService()
            if model_service is _DEFAULT_MODEL_SERVICE
            else model_service
        ),
        targets=[{"where": "assistant", "query": "seeded query"}],
        user="u1",
        conv="conv-1",
        scope="user",
        top_k=10,
        scoring_mode="rrf_hybrid",
        with_payload=False,
    )
    kwargs.update(overrides)
    if rank_weights is not None:
        kwargs["rank_weights"] = rank_weights
    best_tid, hits = await search_context(**kwargs)
    return best_tid, hits


def _scores(hits) -> dict:
    return {h["turn_id"]: h["score"] for h in hits}


def _order(hits) -> list:
    return [h["turn_id"] for h in hits]


def _recency_seeded_index() -> FakeConvIndex:
    # A ranks first on both text arms but is old; B ranks second but is fresh.
    # Default fusion: A rrf = 2/61, B rrf = 2/62 * (1 + 1.0) -> B wins on recency.
    # With recency weight 0 the lift disappears and A wins on rank.
    a_sem, b_sem = _row("A", rec=0.0), _row("B", rec=1.0)
    return FakeConvIndex(sem=[a_sem, b_sem], lex=[_row("A", rec=0.0), _row("B", rec=1.0)], trgm=[])


@pytest.mark.asyncio
async def test_unit_weights_are_byte_identical_to_no_weights():
    baseline_best, baseline_hits = await _run(_recency_seeded_index())
    unit_best, unit_hits = await _run(
        _recency_seeded_index(),
        rank_weights={"semantic": 1.0, "lexical": 1.0, "recency": 1.0},
    )

    assert _order(unit_hits) == _order(baseline_hits)
    # Exact float equality: weights of 1.0 must reproduce today's math bit-for-bit.
    assert _scores(unit_hits) == _scores(baseline_hits)
    assert unit_best == baseline_best


@pytest.mark.asyncio
async def test_default_ordering_prefers_recent_hit():
    best, hits = await _run(_recency_seeded_index())
    assert _order(hits) == ["B", "A"]
    assert best == "B"


@pytest.mark.asyncio
async def test_recency_zero_flips_to_rank_order():
    best, hits = await _run(_recency_seeded_index(), rank_weights={"recency": 0.0})
    assert _order(hits) == ["A", "B"]
    assert best == "A"
    # Without the lift, scores are the pure (weighted) RRF sums.
    scores = _scores(hits)
    assert scores["A"] == pytest.approx(2.0 / 61.0)
    assert scores["B"] == pytest.approx(2.0 / 62.0)


@pytest.mark.asyncio
async def test_lexical_weight_scales_both_lexical_and_trigram_arms():
    # Semantic prefers A; lexical AND trigram prefer B. With defaults the two
    # text arms outvote semantic (B first); zeroing `lexical` silences both
    # text arms at once and A wins on the semantic arm alone.
    def make_index():
        return FakeConvIndex(
            sem=[_row("A", rec=0.0), _row("B", rec=0.0)],
            lex=[_row("B", rec=0.0), _row("A", rec=0.0)],
            trgm=[_row("B", rec=0.0), _row("A", rec=0.0)],
        )

    _, default_hits = await _run(make_index())
    assert _order(default_hits) == ["B", "A"]

    _, weighted_hits = await _run(make_index(), rank_weights={"lexical": 0.0})
    assert _order(weighted_hits) == ["A", "B"]
    scores = _scores(weighted_hits)
    assert scores["A"] == pytest.approx(1.0 / 61.0)  # semantic rank 1 only
    assert scores["B"] == pytest.approx(1.0 / 62.0)  # semantic rank 2 only


@pytest.mark.asyncio
async def test_semantic_weight_scales_semantic_arm():
    # Semantic prefers A, lexical prefers B, no trigram rows: the unweighted
    # RRF sums tie exactly (1/61 + 1/62 each). Boosting `semantic` breaks the
    # tie toward the semantic arm's favorite:
    # A = 2/61 + 1/62 > B = 2/62 + 1/61.
    index = FakeConvIndex(
        sem=[_row("A", rec=0.0), _row("B", rec=0.0)],
        lex=[_row("B", rec=0.0), _row("A", rec=0.0)],
        trgm=[],
    )
    _, hits = await _run(index, rank_weights={"semantic": 2.0})
    assert _order(hits) == ["A", "B"]
    scores = _scores(hits)
    assert scores["A"] == pytest.approx(2.0 / 61.0 + 1.0 / 62.0)
    assert scores["B"] == pytest.approx(2.0 / 62.0 + 1.0 / 61.0)
    assert scores["A"] > scores["B"]


@pytest.mark.asyncio
async def test_semantic_arm_degrades_when_embedding_fails():
    # Embedding failure empties ONLY the semantic arm; lexical/trigram answer.
    index = FakeConvIndex(
        sem=[_row("A", rec=0.0)],
        lex=[_row("B", rec=0.0)],
        trgm=[],
    )
    best, hits = await _run(index, model_service=FailingModelService())
    assert _order(hits) == ["B"]
    assert best == "B"


@pytest.mark.asyncio
async def test_unconfigured_embedding_skips_semantic_arm_without_network_probe():
    index = FakeConvIndex(
        sem=[_row("A", rec=0.0)],
        lex=[_row("B", rec=0.0)],
        trgm=[],
    )

    best, hits = await _run(index, model_service=None)

    assert _order(hits) == ["B"]
    assert best == "B"
    assert index.semantic_calls == 0
