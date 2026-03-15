from kdcube_ai_app.apps.chat.sdk import config as sdk_config


class _FakeSecretsManager:
    def get_secret(self, key: str):
        values = {
            "services.openai.api_key": "sk-openai-test",
            "services.anthropic.api_key": "sk-anthropic-test",
        }
        return values.get(key)


def test_settings_reads_secrets_through_provider(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("GIT_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("GIT_HTTP_USER", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(sdk_config, "get_secrets_manager", lambda _settings: _FakeSecretsManager())

    settings = sdk_config.Settings()

    assert settings.OPENAI_API_KEY == "sk-openai-test"
    assert settings.ANTHROPIC_API_KEY == "sk-anthropic-test"
    assert settings.secret("services.openai.api_key") == "sk-openai-test"
