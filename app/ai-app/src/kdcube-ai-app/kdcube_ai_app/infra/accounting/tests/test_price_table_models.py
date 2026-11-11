# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Baseline price-table coverage for the current Anthropic model line: every id is
priced, resolves through its aliases, and follows the table's cache convention
(5m write = 1.25x input, 1h write = 2x input, read = 0.1x input)."""

from __future__ import annotations

import pytest

from kdcube_ai_app.infra.accounting import usage


TABLE = usage.DEFAULT_PRICE_TABLE

# (model id, input per 1M, output per 1M)
CURRENT_MODELS = [
    (usage.opus_5, 5.00, 25.00),
    (usage.fable_5, 10.00, 50.00),
    (usage.sonnet_5, 3.00, 15.00),
    (usage.opus_48, 5.00, 25.00),
    (usage.opus_46, 5.00, 25.00),
    (usage.sonnet_46, 3.00, 15.00),
]

ALIASES = [
    ("opus-5", usage.opus_5),
    ("claude-opus-5", usage.opus_5),
    ("fable", usage.fable_5),
    ("claude-fable", usage.fable_5),
    ("fable-5", usage.fable_5),
    ("claude-fable-5", usage.fable_5),
    ("sonnet-5", usage.sonnet_5),
    ("claude-sonnet-5", usage.sonnet_5),
]


@pytest.mark.parametrize("model,inp,out", CURRENT_MODELS)
def test_model_is_priced(model, inp, out):
    entry = usage._find_llm_price("anthropic", model, TABLE)
    assert entry is not None, f"{model} missing from the baseline price table"
    assert entry["input_tokens_1M"] == inp
    assert entry["output_tokens_1M"] == out


@pytest.mark.parametrize("model,inp,out", CURRENT_MODELS)
def test_cache_pricing_follows_table_convention(model, inp, out):
    entry = usage._find_llm_price("anthropic", model, TABLE)
    cache = entry["cache_pricing"]
    assert cache["5m"]["write_tokens_1M"] == pytest.approx(inp * 1.25)
    assert cache["1h"]["write_tokens_1M"] == pytest.approx(inp * 2.0)
    assert cache["5m"]["read_tokens_1M"] == pytest.approx(inp * 0.1)
    assert cache["1h"]["read_tokens_1M"] == pytest.approx(inp * 0.1)
    # Flat keys mirror the 5m tier plus the shared read rate.
    assert entry["cache_write_tokens_1M"] == pytest.approx(inp * 1.25)
    assert entry["cache_read_tokens_1M"] == pytest.approx(inp * 0.1)


@pytest.mark.parametrize("alias,model", ALIASES)
def test_alias_resolves_to_its_model(alias, model):
    entry = usage._find_llm_price("anthropic", alias, TABLE)
    assert entry is not None, f"alias {alias!r} does not resolve"
    assert entry["model"] == model


@pytest.mark.parametrize("alias,model", ALIASES)
def test_alias_lookup_is_case_insensitive(alias, model):
    entry = usage._find_llm_price("anthropic", alias.upper(), TABLE)
    assert entry is not None and entry["model"] == model


def test_new_model_ids_are_unique_entries():
    ids = [e.get("model") for e in TABLE["llm"]]
    for model in (usage.opus_5, usage.fable_5, usage.sonnet_5):
        assert ids.count(model) == 1, f"{model} must appear exactly once"


def test_aliases_do_not_collide_across_entries():
    seen: dict[str, str] = {}
    for entry in TABLE["llm"]:
        for alias in entry.get("aliases") or []:
            key = f"{entry.get('provider')}::{str(alias).strip().lower()}"
            assert key not in seen, (
                f"alias {alias!r} claimed by both {seen.get(key)} and {entry.get('model')}"
            )
            seen[key] = entry.get("model")


ANTHROPIC_ROWS = [e for e in TABLE["llm"] if e.get("provider") == "anthropic"]


@pytest.mark.parametrize("entry", ANTHROPIC_ROWS, ids=lambda e: e["model"])
def test_every_anthropic_row_prices_cache_writes_above_input(entry):
    """A cache write costs more than a plain input token: 1.25x for the 5-minute
    TTL, 2x for the 1-hour TTL. Older rows priced the 5-minute write at 1x and
    the 1-hour write at 1.25x, undercharging every cache write by one step.
    Anthropic rounds the published rate for its cheapest model (claude-3-haiku:
    $0.30, not $0.3125), hence the 5% tolerance."""
    inp = float(entry["input_tokens_1M"])
    cache = entry["cache_pricing"]
    assert cache["5m"]["write_tokens_1M"] == pytest.approx(inp * 1.25, rel=0.05)
    assert cache["1h"]["write_tokens_1M"] == pytest.approx(inp * 2.0, rel=0.05)
    assert entry["cache_write_tokens_1M"] == cache["5m"]["write_tokens_1M"]


# Published rates for the rows that were one step low.
@pytest.mark.parametrize("model,w5m,w1h", [
    (usage.haiku_4, 1.25, 2.00),
    (usage.sonnet_45, 3.75, 6.00),
    ("claude-3-5-haiku-20241022", 1.00, 1.60),
    ("claude-3-haiku-20240307", 0.30, 0.50),
])
def test_corrected_cache_write_rates(model, w5m, w1h):
    entry = usage._find_llm_price("anthropic", model, TABLE)
    assert entry["cache_pricing"]["5m"]["write_tokens_1M"] == w5m
    assert entry["cache_pricing"]["1h"]["write_tokens_1M"] == w1h


@pytest.mark.parametrize("model,inp,out,cached", [
    ("gpt-5.4-nano", 0.20, 1.25, 0.02),
    ("gpt-5-4-nano", 0.20, 1.25, 0.02),
    ("gpt-4o-mini-transcribe", 1.25, 5.00, 0.00),
])
def test_openai_rows_are_priced(model, inp, out, cached):
    entry = usage._find_llm_price("openai", model, TABLE)
    assert entry is not None, f"openai/{model} has no price row and would book at $0"
    assert entry["input_tokens_1M"] == inp
    assert entry["output_tokens_1M"] == out
    assert entry["cache_read_tokens_1M"] == cached
    assert entry["cache_write_tokens_1M"] == 0.0


def test_gpt5_nano_is_not_an_alias_of_gpt54_nano():
    # A different model with different rates; an over-broad alias would
    # misprice it just as silently as a missing row.
    entry = usage._find_llm_price("openai", "gpt-5-nano", TABLE)
    assert entry is None or entry["model"] != "gpt-5.4-nano"
