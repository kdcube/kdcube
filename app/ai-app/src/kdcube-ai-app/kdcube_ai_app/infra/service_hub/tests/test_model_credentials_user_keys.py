# SPDX-License-Identifier: MIT
"""A signed-in user's own provider key serves their turns — when the app enables it.

The decisive case is the round trip: a key written with the SDK's own
``set_user_secret(user_model_credential_key(...))`` must be the key
``resolve_config_request_secrets`` finds. Canonical platform ids carry a
``platform.`` prefix that app- and user-scoped keys do not; a reader that asks
the user namespace for the prefixed id misses every stored key and silently
runs the turn on the platform key instead.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import kdcube_ai_app.apps.chat.sdk.config as sdk_config
import kdcube_ai_app.infra.plugin.bundle_store as bundle_store
import kdcube_ai_app.infra.redis.client as redis_client
import kdcube_ai_app.infra.service_hub.inventory as inventory
import kdcube_ai_app.infra.service_hub.model_credentials as mc
from kdcube_ai_app.apps.chat.sdk.config_cache import clear_secret_cache
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import REQUEST_CONTEXT_CV
from kdcube_ai_app.infra.secrets.manager import build_user_secret_key

APP = "assistant@1-0"
USER = "user-7"


class _MemorySecrets:
    """In-memory secrets manager keyed by the platform's real storage ids."""

    def __init__(self, values: dict[str, str] | None = None):
        self.values = dict(values or {})
        self.user_reads: list[str] = []

    async def get_secret(self, key):
        return self.values.get(key)

    async def get_user_secret(self, *, user_id, bundle_id, key):
        full = build_user_secret_key(user_id=user_id, key=key, bundle_id=bundle_id)
        self.user_reads.append(full)
        return self.values.get(full)

    async def set_user_secret(self, *, user_id, bundle_id, key, value):
        self.values[build_user_secret_key(user_id=user_id, key=key, bundle_id=bundle_id)] = value


_SECRET_ENV = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
    "OPENROUTER_API_KEY", "HUGGING_FACE_KEY", "HUGGINGFACE_API_KEY", "HUGGING_FACE_API_TOKEN",
)


@pytest.fixture
def secrets(monkeypatch):
    store = _MemorySecrets()
    for name in _SECRET_ENV:
        monkeypatch.delenv(name, raising=False)
    settings = SimpleNamespace(
        TENANT="tenant-a", PROJECT="project-a", REDIS_URL="redis://unused",
        plain=lambda _key, default=None: default,
    )
    monkeypatch.setattr(sdk_config, "get_settings", lambda: settings)
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: store)

    async def _no_publish(**_kwargs):
        return None

    monkeypatch.setattr(sdk_config, "_publish_secret_write", _no_publish)
    clear_secret_cache()
    yield store
    clear_secret_cache()


def _app_props(monkeypatch, props: dict) -> None:
    """Serve ``props`` as the app's bundles.yaml ``config`` through the real policy loader."""

    async def _get_bundle_props(_redis, *, tenant, project, bundle_id):
        assert (tenant, project, bundle_id) == ("tenant-a", "project-a", APP)
        return props

    monkeypatch.setattr(bundle_store, "get_bundle_props", _get_bundle_props)
    monkeypatch.setattr(redis_client, "get_async_redis_client", lambda _url: object())


USER_KEYS_ON = {"model_credentials": {"user_keys": {"enabled": True}}}


# ---------------------------------------------------------------------------
# Key naming: the writer and the reader agree
# ---------------------------------------------------------------------------
def test_user_key_name_is_the_unprefixed_service_key():
    assert mc.user_model_credential_key("anthropic") == "services.anthropic.api_key"
    for credential in mc.MODEL_CREDENTIALS:
        assert credential.platform_key.startswith("platform.")
        assert not credential.scoped_key.startswith("platform.")
        assert mc.user_model_credential_key(credential.provider) == credential.scoped_key


def test_credential_table_covers_every_config_request_credential_field():
    declared = {name for name in inventory.ConfigRequest.model_fields if name.endswith("_api_key")}
    assert {credential.field for credential in mc.MODEL_CREDENTIALS} == declared


@pytest.mark.asyncio
async def test_a_key_stored_with_the_sdk_writer_is_found_by_the_resolver(secrets, monkeypatch):
    _app_props(monkeypatch, USER_KEYS_ON)
    secrets.values["platform.services.anthropic.api_key"] = "sk-platform"
    await sdk_config.set_user_secret(
        mc.user_model_credential_key("anthropic"), "sk-user-own", user_id=USER, bundle_id=APP,
    )

    resolved = await inventory.resolve_config_request_secrets(
        inventory.ConfigRequest(), bundle_id=APP, user_id=USER,
    )

    assert resolved.claude_api_key == "sk-user-own"
    assert resolved._credential_sources["claude_api_key"] == mc.SOURCE_USER
    assert resolved._shared_credentials["claude_api_key"] == ("sk-platform", mc.SOURCE_PLATFORM)
    assert f"users.{USER}.bundles.{APP}.secrets.services.anthropic.api_key" in secrets.user_reads
    assert not any(".secrets.platform." in key for key in secrets.user_reads)

    config = inventory.create_workflow_config(resolved)
    assert config.claude_api_key == "sk-user-own"
    assert config.credential_sources["claude_api_key"] == mc.SOURCE_USER


# ---------------------------------------------------------------------------
# Precedence and opt-in
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_user_keys_are_off_unless_the_app_enables_them(secrets, monkeypatch):
    _app_props(monkeypatch, {})
    secrets.values[build_user_secret_key(user_id=USER, bundle_id=APP, key="services.anthropic.api_key")] = "sk-user"
    secrets.values[f"bundles.{APP}.secrets.services.anthropic.api_key"] = "sk-app"

    resolved = await inventory.resolve_config_request_secrets(
        inventory.ConfigRequest(), bundle_id=APP, user_id=USER,
    )

    assert resolved.claude_api_key == "sk-app"
    assert resolved._credential_sources["claude_api_key"] == mc.SOURCE_BUNDLE
    assert secrets.user_reads == []


@pytest.mark.asyncio
async def test_precedence_is_request_then_user_then_app_then_platform(secrets, monkeypatch):
    _app_props(monkeypatch, USER_KEYS_ON)
    for provider in ("anthropic", "openai"):
        secrets.values[build_user_secret_key(
            user_id=USER, bundle_id=APP, key=mc.user_model_credential_key(provider),
        )] = f"sk-user-{provider}"
    secrets.values[f"bundles.{APP}.secrets.services.google.api_key"] = "sk-app-google"
    secrets.values["platform.services.openrouter.api_key"] = "sk-platform-openrouter"

    resolved = await inventory.resolve_config_request_secrets(
        inventory.ConfigRequest(openai_api_key="sk-server-set"), bundle_id=APP, user_id=USER,
    )

    assert resolved.openai_api_key == "sk-server-set"
    assert resolved.claude_api_key == "sk-user-anthropic"
    assert resolved.google_api_key == "sk-app-google"
    assert resolved.openrouter_api_key == "sk-platform-openrouter"
    assert resolved._credential_sources == {
        "openai_api_key": mc.SOURCE_REQUEST,
        "claude_api_key": mc.SOURCE_USER,
        "google_api_key": mc.SOURCE_BUNDLE,
        "openrouter_api_key": mc.SOURCE_PLATFORM,
    }


@pytest.mark.asyncio
async def test_the_provider_list_limits_which_user_keys_are_read(secrets, monkeypatch):
    _app_props(monkeypatch, {"model_credentials": {"user_keys": {"enabled": True, "providers": ["anthropic"]}}})
    secrets.values[build_user_secret_key(user_id=USER, bundle_id=APP, key="services.openai.api_key")] = "sk-user-openai"
    secrets.values["platform.services.openai.api_key"] = "sk-platform-openai"

    resolved = await inventory.resolve_config_request_secrets(
        inventory.ConfigRequest(), bundle_id=APP, user_id=USER,
    )

    assert resolved.openai_api_key == "sk-platform-openai"
    assert secrets.user_reads == [f"users.{USER}.bundles.{APP}.secrets.services.anthropic.api_key"]


@pytest.mark.asyncio
async def test_an_anonymous_caller_has_no_user_rung(secrets, monkeypatch):
    _app_props(monkeypatch, USER_KEYS_ON)
    secrets.values[build_user_secret_key(user_id="fp-1", bundle_id=APP, key="services.anthropic.api_key")] = "sk-x"
    token = REQUEST_CONTEXT_CV.set(SimpleNamespace(
        user=SimpleNamespace(user_type="anonymous", user_id="fp-1"),
        routing=SimpleNamespace(bundle_id=APP),
    ))
    try:
        resolved = await inventory.resolve_config_request_secrets(inventory.ConfigRequest(), bundle_id=APP)
    finally:
        REQUEST_CONTEXT_CV.reset(token)

    assert resolved.claude_api_key is None
    assert secrets.user_reads == []


@pytest.mark.asyncio
async def test_the_request_bound_signed_in_user_is_the_default_caller(secrets, monkeypatch):
    _app_props(monkeypatch, USER_KEYS_ON)
    secrets.values[build_user_secret_key(user_id=USER, bundle_id=APP, key="services.anthropic.api_key")] = "sk-mine"
    token = REQUEST_CONTEXT_CV.set(SimpleNamespace(
        user=SimpleNamespace(user_type="registered", user_id=USER),
        routing=SimpleNamespace(bundle_id=APP),
    ))
    try:
        resolved = await inventory.resolve_config_request_secrets(inventory.ConfigRequest(), bundle_id=APP)
    finally:
        REQUEST_CONTEXT_CV.reset(token)

    assert resolved.claude_api_key == "sk-mine"


@pytest.mark.asyncio
async def test_an_unreadable_policy_keeps_user_keys_off(secrets, monkeypatch):
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("props store down")

    monkeypatch.setattr(bundle_store, "get_bundle_props", _boom)
    monkeypatch.setattr(redis_client, "get_async_redis_client", lambda _url: object())

    assert await mc.load_user_key_policy(APP) == mc.USER_KEYS_DISABLED


@pytest.mark.parametrize(
    "props, expected",
    [
        ({}, mc.USER_KEYS_DISABLED),
        ({"model_credentials": {"user_keys": {"enabled": "true"}}}, mc.USER_KEYS_DISABLED),
        ({"model_credentials": {"user_keys": {"enabled": False, "providers": ["anthropic"]}}}, mc.USER_KEYS_DISABLED),
        ({"model_credentials": {"user_keys": {"enabled": True}}}, mc.UserKeyPolicy(enabled=True)),
        (
            {"model_credentials": {"user_keys": {"enabled": True, "providers": ["Anthropic", "gemini"]}}},
            mc.UserKeyPolicy(enabled=True, providers=frozenset({"anthropic", "google"})),
        ),
    ],
)
def test_policy_parsing(props, expected):
    assert mc.user_key_policy_from_props(props) == expected


# ---------------------------------------------------------------------------
# Process-wide instances never hold one user's key
# ---------------------------------------------------------------------------
def _config(sources: dict, **values):
    return SimpleNamespace(
        claude_api_key=values.get("claude_api_key", ""),
        openai_api_key=values.get("openai_api_key", ""),
        credential_sources=sources,
        shared_credentials=values.get("shared", {}),
        role_models=values.get("role_models", {}),
        default_llm_model=values.get("default_llm_model", {"provider": "anthropic", "model_name": "m"}),
    )


def test_without_user_credentials_restores_the_shared_key():
    config = _config(
        {"claude_api_key": mc.SOURCE_USER, "openai_api_key": mc.SOURCE_PLATFORM},
        claude_api_key="sk-user", openai_api_key="sk-platform-openai",
        shared={"claude_api_key": ("sk-app", mc.SOURCE_BUNDLE)},
    )

    shared = mc.without_user_credentials(config)

    assert shared is not config
    assert shared.claude_api_key == "sk-app"
    assert shared.credential_sources == {"claude_api_key": mc.SOURCE_BUNDLE, "openai_api_key": mc.SOURCE_PLATFORM}
    assert config.claude_api_key == "sk-user"  # the per-turn config is untouched
    plain = _config({"claude_api_key": mc.SOURCE_PLATFORM}, claude_api_key="sk-platform")
    assert mc.without_user_credentials(plain) is plain


def test_a_singleton_app_is_built_without_the_callers_key(monkeypatch):
    import kdcube_ai_app.infra.plugin.bundle_loader as loader
    from kdcube_ai_app.infra.plugin.bundle_registry import BundleSpec

    built_with: list = []
    monkeypatch.setattr(loader, "_resolve_module", lambda _spec: SimpleNamespace(__name__="m"))
    monkeypatch.setattr(loader, "_discover_decorated", lambda _mod: ("class", {}, object))
    monkeypatch.setattr(
        loader, "_instantiate_symbol",
        lambda _kind, _symbol, config, _extra: built_with.append(config) or SimpleNamespace(),
    )
    monkeypatch.setattr(loader, "_raise_if_singleton_base_workflow", lambda *_a, **_k: None)
    monkeypatch.setattr(loader, "discover_bundle_interface_manifest", lambda *_a, **_k: None)
    config = _config(
        {"claude_api_key": mc.SOURCE_USER}, claude_api_key="sk-user",
        shared={"claude_api_key": ("sk-platform", mc.SOURCE_PLATFORM)},
    )
    spec = BundleSpec(id="single@1", path="/nonexistent/single-app", module="entry", singleton=True)
    try:
        loader.get_workflow_instance(spec, config, comm_context=None)
    finally:
        loader._singleton_cache.pop(loader._cache_key(spec), None)
        loader._manifest_cache.pop(loader._cache_key(spec), None)

    assert built_with[0].claude_api_key == "sk-platform"
    assert built_with[0].credential_sources == {"claude_api_key": mc.SOURCE_PLATFORM}

    per_turn = BundleSpec(id="multi@1", path="/nonexistent/multi-app", module="entry", singleton=False)
    try:
        loader.get_workflow_instance(per_turn, config, comm_context=None)
    finally:
        loader._manifest_cache.pop(loader._cache_key(per_turn), None)
    assert built_with[1] is config

