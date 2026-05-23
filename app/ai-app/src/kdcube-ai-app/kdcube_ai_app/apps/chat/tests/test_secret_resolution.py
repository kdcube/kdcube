# SPDX-License-Identifier: MIT

import pytest

from kdcube_ai_app.apps.chat.sdk import config as sdk_config


async def _service_secret(key: str, default: str | None = None) -> str | None:
    canonical = f"services.{key.lstrip('.')}"
    return await sdk_config.get_secret(f"b:{canonical}") or await sdk_config.get_secret(canonical, default=default)


def _fake_get_secret(bundle_secrets: dict, global_secrets: dict):
    async def _inner(key: str, default=None, **_kwargs):
        if key.startswith("b:"):
            tail = key[2:]
            return bundle_secrets.get(tail) or default
        return global_secrets.get(key) or default
    return _inner


@pytest.mark.asyncio
async def test_service_secret_pattern_prefers_bundle_override(monkeypatch):
    monkeypatch.setattr(
        sdk_config,
        "get_secret",
        _fake_get_secret(
            {"services.openai.api_key": "sk-bundle"},
            {"services.openai.api_key": "sk-global"},
        ),
    )

    assert await _service_secret("openai.api_key") == "sk-bundle"


@pytest.mark.asyncio
async def test_service_secret_pattern_falls_back_to_global(monkeypatch):
    monkeypatch.setattr(
        sdk_config,
        "get_secret",
        _fake_get_secret({}, {"services.openai.api_key": "sk-global"}),
    )

    assert await _service_secret("openai.api_key") == "sk-global"


@pytest.mark.asyncio
async def test_service_secret_pattern_supports_default(monkeypatch):
    monkeypatch.setattr(sdk_config, "get_secret", _fake_get_secret({}, {}))

    assert await _service_secret("openai.api_key", default="fallback") == "fallback"


@pytest.mark.asyncio
async def test_service_secret_pattern_probes_bundle_then_global(monkeypatch):
    probed = []

    async def _recorder(key: str, default=None, **_kwargs):
        probed.append(key)
        return default

    monkeypatch.setattr(sdk_config, "get_secret", _recorder)

    await _service_secret("stripe.secret_key")

    assert probed == ["b:services.stripe.secret_key", "services.stripe.secret_key"]
