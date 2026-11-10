# SPDX-License-Identifier: MIT
"""Cached prompt tokens are billed once, at the cache-read rate.

OpenAI (Chat Completions `prompt_tokens`, Responses `input_tokens`) and
LangChain's UsageMetadata count cached tokens INSIDE the prompt total, next to
a separate cached count. Every price consumer charges
`input * input_rate + cache_read * cache_read_rate`, so storing the total as
`input_tokens` billed each cached token twice: once at the full input rate and
again at the cache rate. With a 97%-cached prompt that is several times the
provider bill.

The accounting convention is `input_tokens` = uncached prompt (Anthropic's
native meaning); inclusive reports are split where they are parsed."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.accounting import usage
from kdcube_ai_app.infra.accounting.calculator import _accumulate_compact, _spent_seed
from kdcube_ai_app.infra.accounting.pricing import compute_cost_estimate
from kdcube_ai_app.infra.accounting.usage import (
    _norm_usage_dict,
    _structured_usage_extractor,
    split_inclusive_prompt_usage,
)
from kdcube_ai_app.infra.llm.llm_data_model import BatchResultWrapper
from kdcube_ai_app.infra.service_hub.inventory import ModelServiceBase, _stream_usage_from_report

PROMPT, CACHED, OUT = 100_000, 97_000, 1_000
# openai/gpt-5.4-nano: input 0.20, cached 0.02, output 1.25 per 1M
EXPECTED_USD = (3_000 * 0.20 + 97_000 * 0.02 + 1_000 * 1.25) / 1_000_000
DOUBLE_BILLED_USD = (100_000 * 0.20 + 97_000 * 0.02 + 1_000 * 1.25) / 1_000_000


@pytest.fixture(autouse=True)
def _baseline_table(monkeypatch):
    import kdcube_ai_app.infra.accounting.pricing as pricing
    monkeypatch.setattr(pricing, "price_table", lambda: usage.DEFAULT_PRICE_TABLE)
    monkeypatch.setattr(usage, "price_table", lambda: usage.DEFAULT_PRICE_TABLE)


@pytest.mark.parametrize("report", [
    # LangChain UsageMetadata (ChatOpenAI)
    {"input_tokens": PROMPT, "output_tokens": OUT, "total_tokens": PROMPT + OUT,
     "input_token_details": {"cache_read": CACHED}},
    # OpenAI Chat Completions
    {"prompt_tokens": PROMPT, "completion_tokens": OUT, "total_tokens": PROMPT + OUT,
     "prompt_tokens_details": {"cached_tokens": CACHED}},
    # OpenAI Responses
    {"input_tokens": PROMPT, "output_tokens": OUT, "total_tokens": PROMPT + OUT,
     "input_tokens_details": {"cached_tokens": CACHED}},
], ids=["langchain", "chat-completions", "responses"])
def test_inclusive_reports_are_split(report):
    split = split_inclusive_prompt_usage(report)
    assert split.get("input_tokens", split.get("prompt_tokens")) == PROMPT - CACHED
    assert split["cache_read_input_tokens"] == CACHED
    assert split["total_tokens"] == PROMPT + OUT  # volume figure, never priced
    assert report.get("input_tokens", report.get("prompt_tokens")) == PROMPT  # input not mutated


def test_langchain_cache_creation_is_split_too():
    split = split_inclusive_prompt_usage(
        {"input_tokens": 1_000, "input_token_details": {"cache_read": 600, "cache_creation": 300}})
    assert (split["input_tokens"], split["cache_read_input_tokens"], split["cache_creation_input_tokens"]) == (100, 600, 300)


def test_fully_cached_prompt_books_zero_uncached_input():
    split = split_inclusive_prompt_usage({"input_tokens": 500, "input_token_details": {"cache_read": 500}})
    assert split["input_tokens"] == 0
    assert _norm_usage_dict(split)["prompt_tokens"] == 0


def test_native_anthropic_usage_is_untouched():
    native = {"input_tokens": 3_000, "output_tokens": 10, "cache_read_input_tokens": 97_000}
    assert split_inclusive_prompt_usage(native) == native


def test_inconsistent_report_is_not_clamped():
    split = split_inclusive_prompt_usage({"input_tokens": 10, "input_token_details": {"cache_read": 50}})
    assert split["input_tokens"] == 10
    assert split["cache_read_input_tokens"] == 50


def _price_event_usage(service_usage) -> float:
    spent = _spent_seed("llm")
    _accumulate_compact(spent, service_usage.to_compact_dict(), "llm")
    est = compute_cost_estimate(
        [{"service": "llm", "provider": "openai", "model": "gpt-5.4-nano", "spent": spent}],
        services_config={},
    )
    return est["total_cost_usd"]


def test_streamed_openai_usage_is_priced_once():
    """Streaming path: chunk.usage_metadata -> final usage -> tracked event -> price."""
    usage_metadata = {"input_tokens": PROMPT, "output_tokens": OUT, "total_tokens": PROMPT + OUT,
                      "input_token_details": {"cache_read": CACHED}}
    final = {"usage": _norm_usage_dict(_stream_usage_from_report(usage_metadata))}
    service_usage = ModelServiceBase._freeform_usage_extractor(final)
    assert service_usage.input_tokens == PROMPT - CACHED
    assert service_usage.cache_read_tokens == CACHED
    priced = _price_event_usage(service_usage)
    assert priced == pytest.approx(EXPECTED_USD)
    assert priced != pytest.approx(DOUBLE_BILLED_USD)


def test_non_streamed_usage_carries_cache_reads():
    """call_model_text / call_model_with_structure: usage dict -> _structured_usage_extractor."""
    result = {"usage": _norm_usage_dict(split_inclusive_prompt_usage(
        {"input_tokens": PROMPT, "output_tokens": OUT, "input_token_details": {"cache_read": CACHED}}))}
    service_usage = _structured_usage_extractor(result)
    assert (service_usage.input_tokens, service_usage.cache_read_tokens) == (PROMPT - CACHED, CACHED)
    assert _price_event_usage(service_usage) == pytest.approx(EXPECTED_USD)


def test_openai_batch_result_is_split():
    msg = SimpleNamespace(id="m1", error=None, result=None, native_result={
        "id": "r1",
        "response": {"status_code": 200, "body": {
            "model": "gpt-5.4-nano",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": PROMPT, "completion_tokens": OUT, "total_tokens": PROMPT + OUT,
                      "prompt_tokens_details": {"cached_tokens": CACHED}},
        }},
    })
    wrapper = BatchResultWrapper.from_openai_batch_message(msg)
    assert (wrapper.usage.input_tokens, wrapper.usage.cache_read_tokens) == (PROMPT - CACHED, CACHED)


def test_flat_cache_writes_survive_an_empty_ttl_split():
    """A usage with cache_creation_tokens and an empty `cache_creation` dict
    used to drop the writes from the rollup, so they were never billed."""
    spent = _spent_seed("llm")
    _accumulate_compact(spent, {"input_tokens": 10, "cache_creation_tokens": 500, "cache_creation": {}}, "llm")
    assert spent["cache_creation"] == 500
    assert spent["cache_5m_write"] == 500
