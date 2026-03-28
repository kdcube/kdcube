from kdcube_ai_app.infra.secrets import (
    AwsSecretsManagerSecretsManager,
    SecretsManagerConfig,
    build_secrets_manager_config,
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


def test_build_secrets_manager_config_defaults_prefix_from_tenant_and_project(monkeypatch):
    monkeypatch.delenv("SECRETS_AWS_SM_PREFIX", raising=False)
    monkeypatch.delenv("SECRETS_SM_PREFIX", raising=False)
    monkeypatch.setenv("TENANT_ID", "demo")
    monkeypatch.setenv("PROJECT_ID", "demo-march")
    reset_secrets_manager_cache()

    config = build_secrets_manager_config()

    assert config.aws_sm_prefix == "kdcube/demo/demo-march"
