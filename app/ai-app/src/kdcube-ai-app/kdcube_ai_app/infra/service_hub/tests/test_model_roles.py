# SPDX-License-Identifier: MIT

"""Model roles: the format fixer is an ordinary role resolved through every
role scope, and provider ids are canonical."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk import config as sdk_config
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_bundle_call_context
from kdcube_ai_app.infra.service_hub import inventory, model_roles

RETIRED_FIXER_MODEL = "claude-3-haiku-20240307"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        DEFAULT_EMBEDDER="openai-text-embedding-3-small",
        PLATFORM=SimpleNamespace(LOG=SimpleNamespace(LOG_LEVEL="WARNING")),
        BUNDLE_STORAGE_URL=None,
        TENANT="t",
        PROJECT="p",
        OPENAI_API_KEY=None,
        ANTHROPIC_API_KEY=None,
        GOOGLE_API_KEY=None,
    )


def _config(monkeypatch, *, default="claude-sonnet-4-6", assembly=None) -> inventory.Config:
    # Hermetic: no descriptor files and no process-wide settings are read.
    monkeypatch.setattr(inventory, "get_settings", _settings)
    monkeypatch.setattr(sdk_config, "get_settings", _settings)
    monkeypatch.setattr(inventory, "get_plain", lambda key, default=None: default)
    monkeypatch.setattr(
        inventory,
        "assembly_role_models",
        lambda: model_roles.normalize_role_models(assembly or {}),
    )
    return inventory.Config(default_llm_model=default)


# ---------------------------------------------------------------------------
# format_fixer is an ordinary role
# ---------------------------------------------------------------------------

def test_format_fixer_defaults_to_the_default_llm(monkeypatch):
    cfg = _config(monkeypatch, default="claude-sonnet-4-6")

    assert cfg.ensure_role("format_fixer") == {"provider": "anthropic", "model": "claude-sonnet-4-6"}
    assert cfg.format_fixer_model != RETIRED_FIXER_MODEL


def test_assembly_role_models_configure_the_format_fixer(monkeypatch):
    cfg = _config(monkeypatch, assembly={
        "format_fixer": {"provider": "anthropic", "model": "claude-haiku-4-5"},
    })

    assert cfg.ensure_role("format_fixer") == {"provider": "anthropic", "model": "claude-haiku-4-5"}
    # Other roles are untouched by an assembly spec for one role.
    assert cfg.ensure_role("classifier")["model"] == "claude-sonnet-4-6"


def test_assembly_role_also_applies_to_lazily_ensured_roles(monkeypatch):
    cfg = _config(monkeypatch, assembly={"report_writer": {"provider": "openai", "model": "gpt-4o"}})
    cfg.role_models = {}

    assert cfg.ensure_role("report_writer") == {"provider": "openai", "model": "gpt-4o"}


def test_bundle_role_models_override_assembly(monkeypatch):
    cfg = _config(monkeypatch, assembly={
        "format_fixer": {"provider": "anthropic", "model": "claude-haiku-4-5"},
    })
    cfg.set_role_models({"format_fixer": {"provider": "openai", "model": "gpt-4o-mini"}})

    assert cfg.ensure_role("format_fixer") == {"provider": "openai", "model": "gpt-4o-mini"}


def test_bundle_spec_without_provider_keeps_the_assembly_provider(monkeypatch):
    cfg = _config(monkeypatch, assembly={
        "format_fixer": {"provider": "openai", "model": "gpt-4o-mini"},
    })
    cfg.set_role_models({"format_fixer": {"model": "gpt-4o"}})

    assert cfg.ensure_role("format_fixer") == {"provider": "openai", "model": "gpt-4o"}


def test_request_scoped_role_models_override_bundle_scope(monkeypatch):
    # Per-agent ReAct role models reach the router through the same
    # bundle_call_context.role_models binding (agent first, request on top).
    cfg = _config(monkeypatch)
    cfg.set_role_models({"format_fixer": {"provider": "anthropic", "model": "claude-haiku-4-5"}})
    router = inventory.ModelRouter(cfg)

    with bind_current_bundle_call_context({
        "role_models": {"format_fixer": {"provider": "openai", "model": "o3-mini"}},
    }):
        spec = router._effective_role_spec("format_fixer")
    assert spec == {"provider": "openai", "model": "o3-mini"}
    assert router._effective_role_spec("format_fixer")["model"] == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# canonical providers
# ---------------------------------------------------------------------------

def test_gemini_provider_alias_is_normalized_with_a_warning(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger=model_roles.__name__):
        cfg = _config(monkeypatch, assembly={
            "report_writer": {"provider": "gemini", "model": "gemini-2.5-pro"},
        })
        cfg.set_role_models({"summarizer": {"provider": "Gemini", "model": "gemini-2.5-flash"}})

    assert cfg.ensure_role("report_writer")["provider"] == "google"
    assert cfg.ensure_role("summarizer")["provider"] == "google"
    assert any("canonical" in r.getMessage() for r in caplog.records)


def test_request_scoped_gemini_alias_is_normalized(monkeypatch):
    router = inventory.ModelRouter(_config(monkeypatch))
    with bind_current_bundle_call_context({
        "role_models": {"summarizer": {"provider": "gemini", "model": "gemini-2.5-flash"}},
    }):
        assert router._effective_role_spec("summarizer")["provider"] == "google"


def test_string_role_spec_is_a_model_name():
    assert model_roles.normalize_role_spec("gpt-4o") == {"model": "gpt-4o"}
    assert model_roles.normalize_role_spec({"model": "m", "num_ctx": 4096}) == {"model": "m", "num_ctx": 4096}
    assert model_roles.normalize_role_spec(42) is None


# ---------------------------------------------------------------------------
# FormatFixerService routes through the model service
# ---------------------------------------------------------------------------

class _FakeService:
    def __init__(self, router, text):
        self.router = router
        self.text = text
        self.calls = []

    async def call_model_text(self, client, messages, **kwargs):
        self.calls.append({"client": client, "messages": messages, **kwargs})
        return {"text": self.text, "usage": {}, "model_name": kwargs["client_cfg"].model_name}


@pytest.mark.asyncio
async def test_format_fixer_calls_the_resolved_role_through_the_service(monkeypatch):
    cfg = _config(monkeypatch)
    cfg.set_role_models({"format_fixer": {"provider": "openai", "model": "o3-mini"}})
    router = inventory.ModelRouter(cfg)
    built = []
    monkeypatch.setattr(router, "get_client", lambda role, temperature: built.append((role, temperature)) or "client")
    service = _FakeService(router, '```json\n{"ok": true}\n```')

    result = await inventory.FormatFixerService(cfg, service).fix_format(
        raw_output="{ok: true", expected_format="Thing", input_data="in", system_prompt="sys",
    )

    assert result == {"success": True, "data": {"ok": True}, "raw": '```json\n{"ok": true}\n```'}
    assert built == [("format_fixer", 0.0)]
    call = service.calls[0]
    assert call["role"] == "format_fixer"
    assert (call["client_cfg"].provider, call["client_cfg"].model_name) == ("openai", "o3-mini")


@pytest.mark.asyncio
async def test_format_fixer_reports_a_failed_model_call(monkeypatch):
    cfg = _config(monkeypatch)
    router = inventory.ModelRouter(cfg)
    monkeypatch.setattr(router, "get_client", lambda role, temperature: "client")

    class _Failing(_FakeService):
        async def call_model_text(self, client, messages, **kwargs):
            return {"text": "Model call failed: boom", "error": "boom"}

    result = await inventory.FormatFixerService(cfg, _Failing(router, "")).fix_format(
        raw_output="{x", expected_format="T", input_data="", system_prompt="",
    )
    assert result["success"] is False and result["error"] == "boom"


def test_openai_o_series_fixer_client_is_capability_aware(monkeypatch):
    cfg = _config(monkeypatch)
    cfg.openai_api_key = "sk-test"
    cfg.set_role_models({"format_fixer": {"provider": "openai", "model": "o3-mini"}})
    constructed = []
    monkeypatch.setattr(inventory, "ChatOpenAI", lambda **params: constructed.append(params) or "client")

    assert inventory.ModelRouter(cfg).get_client("format_fixer", 0.0) == "client"

    # The shared client builder drops sampling parameters the o-series rejects.
    assert constructed[0]["model"] == "o3-mini"
    assert "temperature" not in constructed[0]


def test_format_fixer_service_has_no_provider_specific_client(monkeypatch):
    cfg = _config(monkeypatch)
    fixer = inventory.FormatFixerService(cfg, SimpleNamespace(router=inventory.ModelRouter(cfg)))
    assert not hasattr(fixer, "claude_client")


@pytest.mark.asyncio
async def test_request_scoped_switch_to_an_unlisted_provider_has_its_secret(monkeypatch):
    """Secrets are resolved for every provider, not only those the static role map names."""

    async def _get_secret(key, *, bundle_id=None, **_kwargs):
        return {"platform.services.openai.api_key": "sk-openai"}.get(key)

    monkeypatch.setattr(inventory, "get_secret", _get_secret)
    resolved = await inventory.resolve_config_request_secrets(
        inventory.ConfigRequest(role_models={"format_fixer": {"provider": "anthropic", "model": "claude-haiku-4-5"}}),
        bundle_id="app@1-0",
    )
    assert resolved.openai_api_key == "sk-openai"
