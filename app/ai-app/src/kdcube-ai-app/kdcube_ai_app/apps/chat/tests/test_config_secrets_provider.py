import logging
import inspect
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.sdk import config as sdk_config
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventActor, ExternalEventPayload, ExternalEventRouting, ExternalEventUser
from kdcube_ai_app.apps.chat.sdk.runtime import comm_ctx


class _FakeSecretsManager:
    def __init__(self):
        self.set_calls = []
        self.get_calls = []
        self.user_get_calls = []
        self.values = {
            "services.openai.api_key": "sk-openai-test",
            "services.anthropic.api_key": "sk-anthropic-test",
            "services.git.http_token": "gh-token-test",
            "infra.postgres.password": "pg-secret-test",
            "infra.redis.password": "redis-secret-test",
            "bundles.bundle.demo.secrets.user_management.cognito_user_pool_id": "pool-123",
        }

    async def get_secret(self, key: str):
        self.get_calls.append(key)
        return self.values.get(key)

    async def get_user_secret(self, *, user_id: str, key: str, bundle_id: str | None = None):
        self.user_get_calls.append((user_id, bundle_id, key))
        values = {
            ("user-1", "bundle.demo", "anthropic.api_key"): "sk-user-anthropic",
        }
        return values.get((user_id, bundle_id, key))

    async def set_secret(self, key: str, value: str):
        self.set_calls.append((key, value))

    async def set_user_secret(self, *, user_id: str, key: str, value: str, bundle_id: str | None = None):
        self.set_calls.append((user_id, bundle_id, key, value))


class _FakePropsManager:
    async def get_user_prop(self, *, user_id: str, key: str, bundle_id: str | None = None):
        values = {
            ("user-1", "bundle.demo", "preferences.theme"): "dark",
        }
        return values.get((user_id, bundle_id, key))

    async def list_user_props(self, *, user_id: str, bundle_id: str | None = None):
        values = {
            ("user-1", "bundle.demo"): {"preferences.theme": "dark"},
        }
        return values.get((user_id, bundle_id), {})


def test_user_prop_sdk_surface_is_native_async():
    assert inspect.iscoroutinefunction(sdk_config.get_user_prop)
    assert inspect.iscoroutinefunction(sdk_config.get_user_props)
    assert inspect.iscoroutinefunction(sdk_config.set_user_prop)
    assert inspect.iscoroutinefunction(sdk_config.delete_user_prop)


@pytest.fixture(autouse=True)
def _clear_secret_lookup_cache():
    sdk_config.clear_config_cache()
    yield
    sdk_config.clear_config_cache()


@pytest.mark.asyncio
async def test_get_secret_reads_through_provider(monkeypatch, caplog):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("GIT_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("GIT_HTTP_USER", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    manager = _FakeSecretsManager()
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: manager)
    sdk_config._SECRET_LOGGED.clear()
    caplog.set_level(logging.INFO, logger="kdcube.settings.secrets")

    settings = sdk_config.Settings()
    monkeypatch.setattr(sdk_config, "get_settings", lambda: settings)

    assert settings.OPENAI_API_KEY is None
    assert settings.ANTHROPIC_API_KEY is None
    assert manager.get_calls == []
    assert await sdk_config.get_secret("services.openai.api_key") == "sk-openai-test"
    assert await sdk_config.get_secret("services.git.http_token") == "gh-token-test"


@pytest.mark.asyncio
async def test_get_secret_uses_central_provider_cache_and_explicit_clear(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    manager = _FakeSecretsManager()
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: manager)
    monkeypatch.setattr(
        sdk_config,
        "get_settings",
        lambda: SimpleNamespace(TENANT="tenant-a", PROJECT="project-a"),
    )

    assert await sdk_config.get_secret("services.openai.api_key") == "sk-openai-test"
    assert await sdk_config.get_secret("services.openai.api_key") == "sk-openai-test"
    assert manager.get_calls == ["services.openai.api_key"]

    cleared = sdk_config.clear_secret_cache(
        tenant="tenant-a",
        project="project-a",
        key="services.openai.api_key",
    )
    assert cleared == 1

    assert await sdk_config.get_secret("services.openai.api_key") == "sk-openai-test"
    assert manager.get_calls == ["services.openai.api_key", "services.openai.api_key"]


@pytest.mark.asyncio
async def test_get_secret_canonicalizes_legacy_alias_for_provider(monkeypatch):
    manager = _FakeSecretsManager()
    manager.values["services.git.http_user"] = "git-user-secret"
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: manager)
    sdk_config._SECRET_LOGGED.clear()

    settings = sdk_config.Settings()
    monkeypatch.setattr(sdk_config, "get_settings", lambda: settings)
    manager.get_calls.clear()

    assert await sdk_config.get_secret("GIT_HTTP_USER") == "git-user-secret"
    assert manager.get_calls == ["services.git.http_user"]


def test_settings_does_not_read_provider_during_sync_construction(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.delenv("GLOBAL_SECRETS_YAML", raising=False)
    monkeypatch.delenv("PLATFORM_DESCRIPTORS_DIR", raising=False)
    manager = _FakeSecretsManager()
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: manager)

    settings = sdk_config.Settings()

    assert settings.PGPASSWORD == "postgres"
    assert settings.REDIS_PASSWORD is None
    assert manager.get_calls == []


def test_plain_config_reads_use_value_cache_until_descriptor_changes(monkeypatch, tmp_path):
    assembly_path = tmp_path / "assembly.yaml"
    assembly_path.write_text("ai:\n  react:\n    output_budget: 10\n", encoding="utf-8")
    monkeypatch.setenv("ASSEMBLY_YAML_DESCRIPTOR_PATH", str(assembly_path))

    calls = {"count": 0}
    original_resolve = sdk_config._resolve_dotted_value

    def _counting_resolve(data, dotted_path):
        calls["count"] += 1
        return original_resolve(data, dotted_path)

    monkeypatch.setattr(sdk_config, "_resolve_dotted_value", _counting_resolve)
    settings = sdk_config.Settings()

    assert settings.plain("ai.react.output_budget") == 10
    assert settings.plain("ai.react.output_budget") == 10
    assert calls["count"] == 1

    assembly_path.write_text("ai:\n  react:\n    output_budget: 1000\n", encoding="utf-8")

    assert settings.plain("ai.react.output_budget") == 1000
    assert calls["count"] == 2


def test_settings_reads_platform_secrets_from_descriptor_without_provider_lookup(monkeypatch, tmp_path):
    secrets_path = tmp_path / "secrets.yaml"
    secrets_path.write_text(
        "infra:\n"
        "  postgres:\n"
        "    password: pg-secret-from-file\n"
        "  redis:\n"
        "    password: redis-secret-from-file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.delenv("PLATFORM_DESCRIPTORS_DIR", raising=False)
    monkeypatch.setenv("GLOBAL_SECRETS_YAML", secrets_path.resolve().as_uri())
    manager = _FakeSecretsManager()
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: manager)

    settings = sdk_config.Settings()

    assert settings.PGPASSWORD == "pg-secret-from-file"
    assert settings.REDIS_PASSWORD == "redis-secret-from-file"
    assert manager.get_calls == []


def test_settings_prefers_sensitive_env_without_provider_lookup(monkeypatch):
    manager = _FakeSecretsManager()
    manager.values.update(
        {
            "auth.oidc.admin_email": "secret@example.com",
            "auth.oidc.admin_username": "secret-user",
            "auth.oidc.admin_password": "secret-pass",
        }
    )
    monkeypatch.setenv("POSTGRES_PASSWORD", "env-postgres-pass")
    monkeypatch.setenv("REDIS_PASSWORD", "env-redis-pass")
    monkeypatch.setenv("OIDC_SERVICE_USER_EMAIL", "env@example.com")
    monkeypatch.setenv("OIDC_SERVICE_ADMIN_USERNAME", "env-user")
    monkeypatch.setenv("OIDC_SERVICE_ADMIN_PASSWORD", "env-pass")
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: manager)

    settings = sdk_config.Settings()

    assert settings.PGPASSWORD == "env-postgres-pass"
    assert settings.REDIS_PASSWORD == "env-redis-pass"
    assert settings.AUTH.OIDC_SERVICE_USER_EMAIL == "env@example.com"
    assert settings.AUTH.OIDC_SERVICE_ADMIN_USERNAME == "env-user"
    assert settings.AUTH.OIDC_SERVICE_ADMIN_PASSWORD == "env-pass"
    assert "infra.postgres.password" not in manager.get_calls
    assert "infra.redis.password" not in manager.get_calls
    assert "auth.oidc.admin_email" not in manager.get_calls
    assert "auth.oidc.admin_username" not in manager.get_calls
    assert "auth.oidc.admin_password" not in manager.get_calls


@pytest.mark.asyncio
async def test_get_secret_user_namespace_uses_request_context_scope(monkeypatch):
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _FakeSecretsManager())
    monkeypatch.setattr(
        comm_ctx,
        "get_current_request_context",
        lambda: ExternalEventPayload(
            routing=ExternalEventRouting(bundle_id="bundle.demo", session_id="s-1"),
            user=ExternalEventUser(user_type="registered", user_id="user-1"),
        ),
    )

    assert await sdk_config.get_secret("u:anthropic.api_key") == "sk-user-anthropic"


@pytest.mark.asyncio
async def test_get_secret_uses_central_user_secret_cache_and_bundle_clear(monkeypatch):
    manager = _FakeSecretsManager()
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: manager)
    monkeypatch.setattr(
        sdk_config,
        "get_settings",
        lambda: SimpleNamespace(TENANT="tenant-a", PROJECT="project-a"),
    )
    monkeypatch.setattr(
        comm_ctx,
        "get_current_request_context",
        lambda: ExternalEventPayload(
            routing=ExternalEventRouting(bundle_id="bundle.demo", session_id="s-1"),
            user=ExternalEventUser(user_type="registered", user_id="user-1"),
        ),
    )

    assert await sdk_config.get_secret("u:anthropic.api_key") == "sk-user-anthropic"
    assert await sdk_config.get_secret("u:anthropic.api_key") == "sk-user-anthropic"
    assert manager.user_get_calls == [("user-1", "bundle.demo", "anthropic.api_key")]

    cleared = sdk_config.clear_secret_cache(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        user_id="user-1",
    )
    assert cleared == 1

    assert await sdk_config.get_secret("u:anthropic.api_key") == "sk-user-anthropic"
    assert manager.user_get_calls == [
        ("user-1", "bundle.demo", "anthropic.api_key"),
        ("user-1", "bundle.demo", "anthropic.api_key"),
    ]


@pytest.mark.asyncio
async def test_get_user_prop_uses_request_context_scope(monkeypatch):
    monkeypatch.setattr(sdk_config, "get_props_manager", lambda: _FakePropsManager())
    monkeypatch.setattr(
        comm_ctx,
        "get_current_request_context",
        lambda: ExternalEventPayload(
            routing=ExternalEventRouting(bundle_id="bundle.demo", session_id="s-1"),
            user=ExternalEventUser(user_type="registered", user_id="user-1"),
        ),
    )

    assert await sdk_config.get_user_prop("preferences.theme") == "dark"
    assert await sdk_config.get_user_props() == {"preferences.theme": "dark"}


@pytest.mark.asyncio
async def test_get_secret_bundle_namespace_uses_request_context_scope(monkeypatch):
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _FakeSecretsManager())
    monkeypatch.setattr(
        comm_ctx,
        "get_current_request_context",
        lambda: ExternalEventPayload(
            routing=ExternalEventRouting(bundle_id="bundle.demo", session_id="s-1"),
            user=ExternalEventUser(user_type="registered", user_id="user-1"),
        ),
    )

    assert await sdk_config.get_secret("b:user_management.cognito_user_pool_id") == "pool-123"


@pytest.mark.asyncio
async def test_get_secret_bundle_namespace_uses_explicit_bundle_contextvar(monkeypatch):
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _FakeSecretsManager())
    monkeypatch.setattr(comm_ctx, "get_current_request_context", lambda: None)

    with comm_ctx.bind_current_bundle_id("bundle.demo"):
        assert await sdk_config.get_secret("b:user_management.cognito_user_pool_id") == "pool-123"


@pytest.mark.asyncio
async def test_get_secret_bundle_namespace_uses_bundle_env_fallback(monkeypatch):
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _FakeSecretsManager())
    monkeypatch.setattr(comm_ctx, "get_current_request_context", lambda: None)
    monkeypatch.setenv("KDCUBE_BUNDLE_ID", "bundle.demo")

    assert await sdk_config.get_secret("b:user_management.cognito_user_pool_id") == "pool-123"


@pytest.mark.asyncio
async def test_get_secret_bundle_namespace_without_context_does_not_query_provider(monkeypatch, caplog):
    class _SettingsThatMustNotBeUsed:
        pass

    monkeypatch.setattr(sdk_config, "get_settings", lambda: _SettingsThatMustNotBeUsed())
    monkeypatch.setattr(comm_ctx, "get_current_request_context", lambda: None)
    monkeypatch.setattr(comm_ctx, "get_current_bundle_id", lambda: None)
    monkeypatch.delenv("KDCUBE_BUNDLE_ID", raising=False)
    monkeypatch.delenv("AGENTIC_BUNDLE_ID", raising=False)
    monkeypatch.delenv("BUNDLE_ID", raising=False)

    caplog.set_level(logging.WARNING, logger="kdcube.settings.secrets")

    assert await sdk_config.get_secret("b:services.git.http_token", default="fallback") == "fallback"
    assert "Bundle-scoped secret b:services.git.http_token requested without bundle context" in caplog.text


@pytest.mark.asyncio
async def test_set_bundle_secret_uses_request_context_bundle_scope(monkeypatch):
    manager = _FakeSecretsManager()
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: manager)
    monkeypatch.setattr(
        sdk_config,
        "get_settings",
        lambda: SimpleNamespace(TENANT="demo-tenant", PROJECT="demo-project"),
    )
    monkeypatch.setattr(
        comm_ctx,
        "get_current_request_context",
        lambda: ExternalEventPayload(
            routing=ExternalEventRouting(bundle_id="bundle.demo", session_id="s-1"),
            actor=ExternalEventActor(tenant_id="ctx-tenant", project_id="ctx-project"),
            user=ExternalEventUser(user_type="registered", user_id="user-1"),
        ),
    )

    await sdk_config.set_bundle_secret("api.token", "secret-value")

    assert manager.set_calls == [
        ("bundles.bundle.demo.secrets.api.token", "secret-value"),
    ]


@pytest.mark.asyncio
async def test_set_bundle_prop_uses_request_context_scope_and_merges_nested_key(monkeypatch):
    calls = {}

    async def _fake_patch_bundle_props(redis, *, tenant, project, bundle_id, props_patch, actor=None, source=None):
        calls["patch"] = {
            "redis": redis,
            "tenant": tenant,
            "project": project,
            "bundle_id": bundle_id,
            "props_patch": props_patch,
            "actor": actor,
            "source": source,
        }

    redis_client = object()
    monkeypatch.setattr(
        sdk_config,
        "get_settings",
        lambda: SimpleNamespace(
            REDIS_URL="redis://test",
            TENANT="settings-tenant",
            PROJECT="settings-project",
        ),
    )
    monkeypatch.setattr(
        comm_ctx,
        "get_current_request_context",
        lambda: ExternalEventPayload(
            routing=ExternalEventRouting(bundle_id="bundle.demo", session_id="s-1"),
            actor=ExternalEventActor(tenant_id="ctx-tenant", project_id="ctx-project"),
            user=ExternalEventUser(user_type="registered", user_id="user-1"),
        ),
    )

    import kdcube_ai_app.infra.redis.client as redis_client_mod
    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store_mod

    monkeypatch.setattr(redis_client_mod, "get_async_redis_client", lambda url: redis_client)
    monkeypatch.setattr(bundle_store_mod, "patch_bundle_props", _fake_patch_bundle_props)

    await sdk_config.set_bundle_prop("features.sync.enabled", False)

    assert calls["patch"] == {
        "redis": redis_client,
        "tenant": "ctx-tenant",
        "project": "ctx-project",
        "bundle_id": "bundle.demo",
        "props_patch": {
            "features": {
                "sync": {"enabled": False},
            }
        },
        "actor": None,
        "source": None,
    }


@pytest.mark.asyncio
async def test_set_bundle_props_merges_patch_and_publishes_once(monkeypatch):
    calls = {}

    async def _fake_patch_bundle_props(redis, *, tenant, project, bundle_id, props_patch, actor=None, source=None):
        calls["patch"] = {
            "redis": redis,
            "tenant": tenant,
            "project": project,
            "bundle_id": bundle_id,
            "props_patch": props_patch,
            "actor": actor,
            "source": source,
        }

    redis_client = object()
    monkeypatch.setattr(
        sdk_config,
        "get_settings",
        lambda: SimpleNamespace(
            REDIS_URL="redis://test",
            TENANT="settings-tenant",
            PROJECT="settings-project",
        ),
    )
    monkeypatch.setattr(
        comm_ctx,
        "get_current_request_context",
        lambda: ExternalEventPayload(
            routing=ExternalEventRouting(bundle_id="bundle.demo", session_id="s-1"),
            actor=ExternalEventActor(tenant_id="ctx-tenant", project_id="ctx-project"),
            user=ExternalEventUser(user_type="registered", user_id="user-1"),
        ),
    )

    import kdcube_ai_app.infra.redis.client as redis_client_mod
    import kdcube_ai_app.infra.plugin.bundle_store as bundle_store_mod

    monkeypatch.setattr(redis_client_mod, "get_async_redis_client", lambda url: redis_client)
    monkeypatch.setattr(bundle_store_mod, "patch_bundle_props", _fake_patch_bundle_props)

    await sdk_config.set_bundle_props(
        {
            "subsystems": {
                "news": {
                    "pipeline": {
                        "cron": "57 19 * * *",
                        "first_run_days": 3,
                    }
                }
            }
        }
    )

    assert calls["patch"] == {
        "redis": redis_client,
        "tenant": "ctx-tenant",
        "project": "ctx-project",
        "bundle_id": "bundle.demo",
        "props_patch": {
            "subsystems": {
                "news": {
                    "pipeline": {
                        "cron": "57 19 * * *",
                        "first_run_days": 3,
                    }
                }
            }
        },
        "actor": None,
        "source": None,
    }
