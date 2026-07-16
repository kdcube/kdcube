# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""The answer models' output-token budget.

Surfaced live: lg-react passed a full HTML page as ``run_python``'s ``code``
argument, the adapter's small default ceiling (1200) cut the response
MID-TOOL-CALL, the truncated args failed tool validation, and the model
retried into the same wall until the graph recursion limit — 12 identical
1200-token calls. The budget must (a) default high enough for payload-bearing
tool calls, (b) be env-overridable, (c) actually reach the KDCubeChatModel the
entrypoint builds. Same class for lg-solution (silently amputated answers).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path

BUNDLE_ROOT = Path(__file__).resolve().parents[1]


def _module(rel: str):
    _n, m = load_dynamic_module_for_path(BUNDLE_ROOT / rel)
    return m


def test_prebuilt_budget_defaults_high_and_env_overrides(monkeypatch) -> None:
    cfg_mod = _module("solution/lg_prebuilt/config.py")
    monkeypatch.delenv("LG_PREBUILT_MAX_TOKENS", raising=False)
    assert cfg_mod.get_config().max_tokens == cfg_mod.DEFAULT_MAX_TOKENS >= 16000
    monkeypatch.setenv("LG_PREBUILT_MAX_TOKENS", "4096")
    assert cfg_mod.get_config().max_tokens == 4096


def test_solution_budget_defaults_high_and_env_overrides(monkeypatch) -> None:
    cfg_mod = _module("solution/lg_solution/config.py")
    monkeypatch.delenv("LG_MAX_TOKENS", raising=False)
    assert cfg_mod.get_config().max_tokens == cfg_mod.DEFAULT_MAX_TOKENS >= 8000
    monkeypatch.setenv("LG_MAX_TOKENS", "2048")
    assert cfg_mod.get_config().max_tokens == 2048


def _entrypoint_stub(ep_mod, props: dict):
    """A minimal entrypoint stand-in: descriptor properties come from
    `bundle_prop` (the KDCube configuration surface — apps are configured via
    the descriptor, not process env vars)."""
    stub = SimpleNamespace(models_service=object())
    stub.bundle_prop = lambda path, default=None: props.get(path, default)
    stub._agent_max_tokens = ep_mod.LGPortedAgentsBundle._agent_max_tokens.__get__(stub)
    return stub


def test_entrypoint_passes_the_budget_to_the_answer_model(monkeypatch) -> None:
    monkeypatch.delenv("LG_PREBUILT_MAX_TOKENS", raising=False)
    ep_mod = _module("entrypoint.py")
    cfg = _module("solution/lg_prebuilt/config.py").get_config()

    # No descriptor property declared -> the vendored standalone default applies.
    model = ep_mod.LGPortedAgentsBundle._build_prebuilt_model(_entrypoint_stub(ep_mod, {}), cfg)
    assert model.max_tokens == cfg.max_tokens
    assert model.max_tokens >= 16000  # fits narration + one complete payload tool call


def test_descriptor_property_is_the_hosted_budget_knob() -> None:
    ep_mod = _module("entrypoint.py")
    cfg = _module("solution/lg_prebuilt/config.py").get_config()
    stub = _entrypoint_stub(
        ep_mod, {"surfaces.as_consumer.agents.lg-react.model.max_tokens": 2222}
    )

    model = ep_mod.LGPortedAgentsBundle._build_prebuilt_model(stub, cfg)

    # The DESCRIPTOR declares hosted config; the vendored value is only a fallback.
    assert model.max_tokens == 2222
