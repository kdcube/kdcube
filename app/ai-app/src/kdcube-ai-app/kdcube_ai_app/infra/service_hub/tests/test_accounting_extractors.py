# SPDX-License-Identifier: MIT
"""ModelService accounting extractors attribute a call to its real provider/model.

AccountingTracker calls extractors as `extractor(result, *call_args, **kw)`, and
the tracked calls are ModelService methods, so the arguments arrive as
(result, model_service, client, ...). The extractors used to be declared
without `result`, which shifted every parameter one place: a call made without
an explicit `client_cfg=` (only `role=`) was booked as provider "unknown",
model "unknown", which matches no price row and costs $0.00.

These tests decorate a real method with the real extractors and read back the
stored event."""
from __future__ import annotations

import pytest

from kdcube_ai_app.infra import accounting
from kdcube_ai_app.infra.accounting import track_llm
from kdcube_ai_app.infra.accounting.usage import ClientConfigHint, _structured_usage_extractor
from kdcube_ai_app.infra.service_hub.inventory import (
    ms_freeform_meta_extractor,
    ms_model_extractor,
    ms_provider_extractor,
    ms_structured_meta_extractor,
)


class _Store:
    def __init__(self):
        self.events = []

    async def store_event(self, event):
        self.events.append(event)
        return True


class _OpenAIishClient:
    model = "client-model"


class _Service:
    """Stands in for ModelService: `describe_client` with the same contract."""

    def describe_client(self, client, role=None):
        if role:
            return ClientConfigHint(provider="anthropic", model_name=f"model-for-{role}")
        if isinstance(client, _OpenAIishClient):
            return ClientConfigHint(provider="openai", model_name=client.model)
        return ClientConfigHint(provider="unknown", model_name="unknown")

    @track_llm(
        provider_extractor=ms_provider_extractor,
        model_extractor=ms_model_extractor,
        usage_extractor=_structured_usage_extractor,
        metadata_extractor=ms_freeform_meta_extractor,
    )
    async def call_text(self, client, messages, *, client_cfg=None, role=None):
        return {"text": "ok", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    @track_llm(
        provider_extractor=ms_provider_extractor,
        model_extractor=ms_model_extractor,
        usage_extractor=_structured_usage_extractor,
        metadata_extractor=ms_structured_meta_extractor,
    )
    async def call_structured(self, client, system_prompt, user_message, response_format,
                              *, client_cfg=None, role=None):
        return {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}


@pytest.fixture
def store():
    s = _Store()
    token = accounting._storage_var.set(s)
    yield s
    accounting._storage_var.reset(token)


class _Msg:
    def __init__(self, content):
        self.content = content


async def test_role_only_call_is_attributed(store):
    await _Service().call_text(object(), [_Msg("hello")], role="answer")
    ev = store.events[-1]
    assert (ev.provider, ev.model_or_service) == ("anthropic", "model-for-answer")
    assert ev.metadata["selected_model"] == "model-for-answer"
    assert ev.metadata["prompt_chars"] == 5


async def test_client_only_call_is_attributed(store):
    await _Service().call_text(_OpenAIishClient(), [_Msg("hi")])
    ev = store.events[-1]
    assert (ev.provider, ev.model_or_service) == ("openai", "client-model")


async def test_explicit_client_cfg_wins(store):
    cfg = ClientConfigHint(provider="google", model_name="explicit")
    await _Service().call_text(_OpenAIishClient(), [_Msg("hi")], client_cfg=cfg, role="answer")
    ev = store.events[-1]
    assert (ev.provider, ev.model_or_service) == ("google", "explicit")


async def test_structured_call_metadata_is_attributed(store):
    class Fmt:
        pass

    await _Service().call_structured(_OpenAIishClient(), "sys", "user", Fmt, role="classifier")
    ev = store.events[-1]
    assert (ev.provider, ev.model_or_service) == ("anthropic", "model-for-classifier")
    assert ev.metadata["model"] == "model-for-classifier"
    assert ev.metadata["expected_format"] == "Fmt"
    assert ev.metadata["prompt_chars"] == len("sys") + len("user")


def test_without_a_service_degrades_to_unknown():
    assert ms_provider_extractor({"text": "x"}) == "unknown"
    assert ms_model_extractor({"text": "x"}, None, object()) == "unknown"


def test_describe_failure_never_raises():
    class Broken:
        def describe_client(self, *_a, **_kw):
            raise RuntimeError("boom")

    assert ms_provider_extractor(None, Broken(), object(), role="r") == "unknown"
