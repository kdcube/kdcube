import os

from kdcube_ai_app.apps.chat.sdk import config as sdk_config


class _NoopSecretsManager:
    def get_secret(self, key: str):
        return None


def test_asyncpg_ssl_defaults_to_require_when_postgres_ssl_enabled(monkeypatch):
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setenv("POSTGRES_SSL", "true")
    monkeypatch.delenv("POSTGRES_SSL_MODE", raising=False)
    monkeypatch.delenv("POSTGRES_SSL_ROOT_CERT", raising=False)
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)

    settings = sdk_config.Settings()

    assert sdk_config.resolve_asyncpg_ssl(settings) == "require"


def test_asyncpg_ssl_can_use_explicit_verify_mode_and_root_cert(monkeypatch):
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setenv("POSTGRES_SSL", "true")
    monkeypatch.setenv("POSTGRES_SSL_MODE", "verify-full")
    monkeypatch.setenv("POSTGRES_SSL_ROOT_CERT", "/tmp/rds-ca.pem")
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)

    settings = sdk_config.Settings()

    assert sdk_config.resolve_asyncpg_ssl(settings) == "verify-full"
    assert os.getenv("PGSSLROOTCERT") == "/tmp/rds-ca.pem"


def test_asyncpg_ssl_rejects_unknown_mode(monkeypatch):
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _NoopSecretsManager())
    monkeypatch.setenv("POSTGRES_SSL", "true")
    monkeypatch.setenv("POSTGRES_SSL_MODE", "bogus-mode")

    settings = sdk_config.Settings()

    try:
        sdk_config.resolve_asyncpg_ssl(settings)
    except ValueError as exc:
        assert "Unsupported POSTGRES_SSL_MODE" in str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid POSTGRES_SSL_MODE")
