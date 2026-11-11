# SPDX-License-Identifier: MIT

"""Model registry: current models are keys, and a default model id the
registry does not know is reported."""

from __future__ import annotations

import logging

import pytest

from kdcube_ai_app.apps.chat.reg import MODEL_CONFIGS, model_caps
from kdcube_ai_app.infra.service_hub import inventory


# ---------------------------------------------------------------------------
# registry + default model
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model_id",
    ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-6", "claude-sonnet-5-5",
     "claude-opus-5-5", "gpt-5.4-nano"],
)
def test_current_models_are_registry_keys(model_id):
    entry = MODEL_CONFIGS[model_id]
    assert entry["provider"] in {"anthropic", "openai"}
    assert entry["model_name"]


def test_gpt5_nano_is_sent_no_sampling_parameters():
    caps = model_caps(MODEL_CONFIGS["gpt-5.4-nano"]["model_name"])
    assert caps["temperature"] is False and caps["top_p"] is False


def test_registered_default_resolves_without_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=inventory.__name__):
        entry = inventory.resolve_default_llm_model("claude-haiku-4-5")
    assert entry["model_name"] == "claude-haiku-4-5"
    assert not caplog.records


def test_unknown_default_falls_back_and_is_reported(caplog):
    with caplog.at_level(logging.WARNING, logger=inventory.__name__):
        entry = inventory.resolve_default_llm_model("not-a-registered-model")
    assert entry is MODEL_CONFIGS["o3-mini"]
    assert any("not-a-registered-model" in r.getMessage() for r in caplog.records)


def test_unregistered_default_with_an_explicit_provider_is_used_as_configured(caplog):
    with caplog.at_level(logging.WARNING, logger=inventory.__name__):
        entry = inventory.resolve_default_llm_model("qwen3:8b", "custom")
    assert entry == {"model_name": "qwen3:8b", "provider": "custom"}
    assert not caplog.records
