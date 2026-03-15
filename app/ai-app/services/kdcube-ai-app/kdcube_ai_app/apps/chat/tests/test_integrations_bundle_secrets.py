import json
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations


class _FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value

    async def publish(self, *_args, **_kwargs):
        return 1


class _FakeSecretsManager:
    def __init__(self):
        self.set_many_calls = []
        self.delete_many_calls = []
        self.metadata = {}

    def can_write(self):
        return True

    def set_many(self, values):
        self.set_many_calls.append(dict(values))

    def delete_many(self, keys):
        self.delete_many_calls.append(sorted(keys))

    def set_secret(self, key, value):
        self.metadata[key] = value

    def delete_secret(self, key):
        self.metadata.pop(key, None)


def _request_with_redis(redis):
    state = SimpleNamespace(redis_async=redis)
    app = SimpleNamespace(state=state)
    return SimpleNamespace(app=app)


@pytest.mark.asyncio
async def test_set_bundle_secrets_uses_provider_and_tracks_known_keys(monkeypatch):
    redis = _FakeRedis()
    request = _request_with_redis(redis)
    session = SimpleNamespace(username="tester", user_id="user-1")
    manager = _FakeSecretsManager()

    monkeypatch.setattr(integrations, "get_settings", lambda: SimpleNamespace(TENANT="tenant-a", PROJECT="project-a"))
    monkeypatch.setattr(integrations, "get_secrets_manager", lambda _settings: manager)

    result = await integrations.set_bundle_secrets(
        "bundle@1",
        integrations.BundleSecretsUpdateRequest(
            mode="set",
            secrets={"openai": {"api_key": "sk-test"}},
        ),
        request,
        session,
    )

    assert result["mode"] == "set"
    assert result["keys"] == ["bundles.bundle@1.secrets.openai.api_key"]
    assert manager.set_many_calls == [{"bundles.bundle@1.secrets.openai.api_key": "sk-test"}]
    assert json.loads(manager.metadata["bundles.bundle@1.secrets.__keys"]) == [
        "bundles.bundle@1.secrets.openai.api_key"
    ]
    assert json.loads(
        redis.data["kdcube:config:bundles:secrets:tenant-a:project-a:bundle@1"]
    ) == ["bundles.bundle@1.secrets.openai.api_key"]

    result = await integrations.set_bundle_secrets(
        "bundle@1",
        integrations.BundleSecretsUpdateRequest(
            mode="clear",
            secrets={"openai": {"api_key": None}},
        ),
        request,
        session,
    )

    assert result["mode"] == "clear"
    assert manager.delete_many_calls == [["bundles.bundle@1.secrets.openai.api_key"]]
    assert "bundles.bundle@1.secrets.__keys" not in manager.metadata
    assert json.loads(
        redis.data["kdcube:config:bundles:secrets:tenant-a:project-a:bundle@1"]
    ) == []
