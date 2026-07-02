# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Reference resolution: a descriptor `llm_reference_service` that does not resolve
in the effective price table falls back to the in-code default instead of raising
downstream."""

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk import config_scopes
from kdcube_ai_app.infra.accounting import usage


DEFAULT_REF = (usage.DEFAULT_LLM_REFERENCE_PROVIDER, usage.DEFAULT_LLM_REFERENCE_SERVICE)


@pytest.fixture
def patch_descriptor(monkeypatch):
    def _apply(reference, price_tables=None):
        monkeypatch.setattr(config_scopes, "economics_llm_reference_service", lambda: reference)
        monkeypatch.setattr(config_scopes, "economics_price_tables", lambda: price_tables)
    return _apply


def test_absent_descriptor_reference_uses_default(patch_descriptor):
    patch_descriptor(None)
    assert usage.llm_reference_service() == DEFAULT_REF


def test_invalid_reference_falls_back_to_default(patch_descriptor):
    patch_descriptor({"provider": "bogus", "service_name": "missing-model"})
    assert usage.llm_reference_service() == DEFAULT_REF


def test_invalid_reference_does_not_raise_downstream(patch_descriptor):
    patch_descriptor({"provider": "bogus", "service_name": "missing-model"})
    # Would raise RuntimeError in _ref_out_price_1m without the fallback.
    assert usage.usd_per_reference_token() > 0


def test_valid_reference_from_baseline_is_used(patch_descriptor):
    provider, model = DEFAULT_REF
    patch_descriptor({"provider": provider, "service_name": model})
    assert usage.llm_reference_service() == (provider, model)


def test_valid_reference_from_overlay_is_used(patch_descriptor):
    overlay = {"llm": [{"provider": "acme", "model": "acme-1", "output_tokens_1M": 20.0}]}
    patch_descriptor({"provider": "acme", "service_name": "acme-1"}, price_tables=overlay)
    assert usage.llm_reference_service() == ("acme", "acme-1")


def test_reference_absent_from_overlay_but_present_in_baseline_is_used(patch_descriptor):
    # Overlay does not carry the (default) reference, so the effective table is the
    # baseline, where the reference does resolve.
    provider, model = DEFAULT_REF
    overlay = {"llm": [{"provider": "acme", "model": "acme-1", "output_tokens_1M": 20.0}]}
    patch_descriptor({"provider": provider, "service_name": model}, price_tables=overlay)
    assert usage.llm_reference_service() == (provider, model)
