import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.secrets import (
    AwsSecretsManagerSecretsManager,
    SecretsManagerConfig,
    SecretsFileSecretsManager,
    build_secrets_manager_config,
    get_secrets_manager,
    reset_secrets_manager_cache,
)


def test_build_secrets_manager_config_uses_env_and_ignores_gateway_json(monkeypatch):
    monkeypatch.setenv("GATEWAY_CONFIG_JSON", '{"secrets":{"provider":"in-memory"}}')
    monkeypatch.setenv("GATEWAY_COMPONENT", "proc")
    monkeypatch.setenv("SECRETS_PROVIDER", "local")
    monkeypatch.setenv("SECRETS_URL", "http://kdcube-secrets:7777")
    monkeypatch.setenv("SECRETS_TOKEN", "proc-read")
    monkeypatch.setenv("SECRETS_ADMIN_TOKEN", "proc-admin")
    reset_secrets_manager_cache()

    config = build_secrets_manager_config()

    assert config.provider == "secrets-service"
    assert config.component == "proc"
    assert config.url == "http://kdcube-secrets:7777"
    assert config.token == "proc-read"
    assert config.admin_token == "proc-admin"


def test_aws_sm_secret_path_matches_existing_runtime_contract():
    manager = AwsSecretsManagerSecretsManager(
        SecretsManagerConfig(
            provider="aws-sm",
            component="proc",
            aws_sm_prefix="kdcube/demo/demo-march",
        )
    )

    assert manager._secret_id("services.openai.api_key") == "kdcube/demo/demo-march/services/openai/api_key"
    assert (
        manager._secret_id("bundles.react@2026-03-15.secrets.openai.api_key")
        == "kdcube/demo/demo-march/bundles/react@2026-03-15/secrets/openai/api_key"
    )
    assert (
        manager._secret_id("bundles.react@2026-03-15.secrets.__keys")
        == "kdcube/demo/demo-march/bundles/react@2026-03-15/secrets/__keys"
    )
    assert (
        manager._secret_id("bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret")
        == "kdcube/demo/demo-march/bundles/kdcube.copilot@2026-04-03-19-05/secrets/telegram/webhook_secret"
    )


def test_build_secrets_manager_config_defaults_prefix_from_tenant_and_project(monkeypatch):
    reset_secrets_manager_cache()

    config = build_secrets_manager_config(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
        )
    )

    assert config.tenant == "demo"
    assert config.project == "demo-march"
    assert config.aws_sm_prefix == "kdcube/demo/demo-march"


def test_build_secrets_manager_config_prefers_explicit_prefix_from_settings():
    reset_secrets_manager_cache()

    config = build_secrets_manager_config(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
            SECRETS_SM_PREFIX="kdcube/custom/prefix",
        )
    )

    assert config.tenant == "demo"
    assert config.project == "demo-march"
    assert config.aws_sm_prefix == "kdcube/custom/prefix"


def test_build_secrets_manager_config_uses_secrets_file_when_yaml_is_configured(monkeypatch):
    reset_secrets_manager_cache()
    monkeypatch.delenv("SECRETS_PROVIDER", raising=False)
    monkeypatch.setenv("GLOBAL_SECRETS_YAML", "file:///tmp/global-secrets.yaml")

    config = build_secrets_manager_config(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
        )
    )

    assert config.provider == "secrets-file"
    assert config.global_secrets_yaml == "file:///tmp/global-secrets.yaml"


def test_secrets_file_manager_reads_global_and_bundle_yaml(tmp_path, monkeypatch):
    global_file = tmp_path / "secrets.yaml"
    bundle_file = tmp_path / "bundles.secrets.yaml"
    global_file.write_text(
        "\n".join(
            [
                "services:",
                "  openai:",
                "    api_key: sk-global",
                "  anthropic:",
                "    claude_code_key: sk-claude-code",
            ]
        ),
        encoding="utf-8",
    )
    bundle_file.write_text(
        "\n".join(
            [
                "bundles:",
                "  version: '1'",
                "  items:",
                "    - id: 'kdcube.copilot@2026-04-03-19-05'",
                "      secrets:",
                "        telegram:",
                "          webhook_secret: tg-secret",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("SECRETS_PROVIDER", "secrets-file")
    monkeypatch.setenv("GLOBAL_SECRETS_YAML", global_file.resolve().as_uri())
    monkeypatch.setenv("BUNDLE_SECRETS_YAML", bundle_file.resolve().as_uri())
    reset_secrets_manager_cache()

    manager = get_secrets_manager(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
            SECRETS_PROVIDER="secrets-file",
            GLOBAL_SECRETS_YAML=global_file.resolve().as_uri(),
            BUNDLE_SECRETS_YAML=bundle_file.resolve().as_uri(),
        )
    )

    assert manager.get_secret("services.openai.api_key") == "sk-global"
    assert manager.get_secret("services.anthropic.claude_code_key") == "sk-claude-code"
    assert (
        manager.get_secret(
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret"
        )
        == "tg-secret"
    )
    assert json.loads(
        manager.get_secret("bundles.kdcube.copilot@2026-04-03-19-05.secrets.__keys") or "[]"
    ) == [
        "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret"
    ]


def test_secrets_file_manager_writes_global_and_bundle_yaml(tmp_path, monkeypatch):
    global_file = tmp_path / "secrets.yaml"
    bundle_file = tmp_path / "bundles.secrets.yaml"

    monkeypatch.setenv("SECRETS_PROVIDER", "secrets-file")
    monkeypatch.setenv("GLOBAL_SECRETS_YAML", global_file.resolve().as_uri())
    monkeypatch.setenv("BUNDLE_SECRETS_YAML", bundle_file.resolve().as_uri())
    reset_secrets_manager_cache()

    manager = get_secrets_manager(
        SimpleNamespace(
            TENANT="demo",
            PROJECT="demo-march",
            SECRETS_PROVIDER="secrets-file",
            GLOBAL_SECRETS_YAML=global_file.resolve().as_uri(),
            BUNDLE_SECRETS_YAML=bundle_file.resolve().as_uri(),
        )
    )

    assert manager.can_write() is True

    manager.set_secret("services.openai.api_key", "sk-new")
    manager.set_many(
        {
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret": "tg-secret",
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.docs.token": "docs-secret",
        }
    )

    assert manager.get_secret("services.openai.api_key") == "sk-new"
    assert (
        manager.get_secret(
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret"
        )
        == "tg-secret"
    )
    assert json.loads(
        manager.get_secret("bundles.kdcube.copilot@2026-04-03-19-05.secrets.__keys") or "[]"
    ) == [
        "bundles.kdcube.copilot@2026-04-03-19-05.secrets.docs.token",
        "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret",
    ]

    assert "sk-new" in global_file.read_text(encoding="utf-8")
    bundle_text = bundle_file.read_text(encoding="utf-8")
    assert "kdcube.copilot@2026-04-03-19-05" in bundle_text
    assert "tg-secret" in bundle_text
    assert "docs-secret" in bundle_text

    manager.delete_secret("services.openai.api_key")
    manager.delete_many(
        [
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret",
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.docs.token",
        ]
    )

    assert manager.get_secret("services.openai.api_key") is None
    assert (
        manager.get_secret(
            "bundles.kdcube.copilot@2026-04-03-19-05.secrets.telegram.webhook_secret"
        )
        is None
    )
    assert manager.get_secret("bundles.kdcube.copilot@2026-04-03-19-05.secrets.__keys") is None


class _FakeSyncRedis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    def eval(self, _script, _keys_count, key, token):
        if self.data.get(key) == token:
            self.data.pop(key, None)
            return 1
        return 0


def test_secrets_file_manager_cross_replica_reads_current_yaml(tmp_path, monkeypatch):
    from kdcube_ai_app.infra import namespaces
    from kdcube_ai_app.infra.redis import client as redis_client

    global_file = tmp_path / "secrets.yaml"
    global_file.write_text("services:\n  openai:\n    api_key: sk-old\n", encoding="utf-8")

    fake_redis = _FakeSyncRedis()
    monkeypatch.setattr(redis_client, "get_sync_redis_client", lambda *args, **kwargs: fake_redis)

    cfg = SecretsManagerConfig(
        provider="secrets-file",
        component="proc",
        tenant="demo",
        project="demo-project",
        redis_url="redis://fake",
        global_secrets_yaml=global_file.resolve().as_uri(),
    )

    manager_a = SecretsFileSecretsManager(cfg)
    manager_b = SecretsFileSecretsManager(cfg)

    lock_key = namespaces.CONFIG.BUNDLES.SECRETS_FILE_LOCK_FMT.format(
        tenant="demo",
        project="demo-project",
    )

    assert manager_a.get_secret("services.openai.api_key") == "sk-old"
    assert manager_b.get_secret("services.openai.api_key") == "sk-old"

    manager_a.set_secret("services.openai.api_key", "sk-new")

    assert lock_key not in fake_redis.data
    assert manager_b.get_secret("services.openai.api_key") == "sk-new"
