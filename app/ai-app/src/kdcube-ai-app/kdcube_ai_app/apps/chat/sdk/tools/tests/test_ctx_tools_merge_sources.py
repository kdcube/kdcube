# SPDX-License-Identifier: MIT
"""merge_sources accepts one flat collection and always publishes the next sid."""
from __future__ import annotations

import asyncio

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, SOURCE_ID_CV
from kdcube_ai_app.apps.chat.sdk.tools.ctx_tools import ContextTools

FLAT = [
    {"url": "https://a.example/one", "title": "One", "text": "first"},
    {"url": "https://b.example/two", "title": "Two", "text": "second"},
]
NESTED = [[FLAT[0]], [FLAT[1]]]


@pytest.fixture(autouse=True)
def outdir(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    token = OUTDIR_CV.set(str(out))
    try:
        yield out
    finally:
        OUTDIR_CV.reset(token)


def _merge(collections):
    return asyncio.run(ContextTools().merge_sources(source_collections=collections))


def _merge_with_counter(seed, collections):
    """Seed, merge and read SOURCE_ID_CV inside one task: asyncio.run copies the context."""
    async def _run():
        SOURCE_ID_CV.set({"next": seed})
        merged = await ContextTools().merge_sources(source_collections=collections)
        return merged, SOURCE_ID_CV.get(None)

    return asyncio.run(_run())


def test_a_flat_list_of_sources_is_merged():
    merged = _merge(FLAT)
    assert [(r["sid"], r["url"]) for r in merged] == [(1, FLAT[0]["url"]), (2, FLAT[1]["url"])]


def test_flat_and_nested_spellings_agree():
    assert _merge(FLAT) == _merge(NESTED)


def test_a_flat_json_string_is_merged():
    merged = _merge('[{"url": "https://a.example/1", "title": "A"}]')
    assert [(r["sid"], r["title"]) for r in merged] == [(1, "A")]


def test_a_mixed_list_keeps_every_source_in_order():
    third = {"url": "https://c.example/three", "title": "Three"}
    merged = _merge([FLAT[0], [FLAT[1]], third])
    assert [r["url"] for r in merged] == [FLAT[0]["url"], FLAT[1]["url"], third["url"]]
    assert [r["sid"] for r in merged] == [1, 2, 3]


def test_a_mapping_of_sources_is_still_one_collection():
    assert [r["url"] for r in _merge([{"1": FLAT[0], "2": FLAT[1]}])] == [s["url"] for s in FLAT]


@pytest.mark.parametrize("garbage", ["free text", "", "[[", "null", None, 7, [], [None], [7]])
def test_unparseable_input_still_merges_to_nothing(garbage):
    assert _merge(garbage) == []


def test_the_counter_is_overwritten_by_a_non_empty_merge():
    _merged, counter = _merge_with_counter(999, NESTED)
    assert counter == {"next": 3}, "if this reads 999 the counter is read from the wrong context"


@pytest.mark.parametrize("empty", [[], "free text", None, [None]])
def test_an_empty_merge_resets_the_counter(empty):
    merged, counter = _merge_with_counter(42, empty)
    assert merged == []
    assert counter == {"next": 1}
