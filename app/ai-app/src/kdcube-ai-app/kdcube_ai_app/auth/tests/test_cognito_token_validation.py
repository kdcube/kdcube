import pytest
from types import SimpleNamespace

from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.OAuthManager import OAuthManager
from kdcube_ai_app.auth.implementations.cognito import CognitoAuthManager


def _manager() -> CognitoAuthManager:
    manager = object.__new__(CognitoAuthManager)
    manager.oauth_config = SimpleNamespace(OAUTH2_AUDIENCE="app-client")
    return manager


@pytest.mark.asyncio
async def test_cognito_access_token_requires_access_token_use(monkeypatch):
    async def fake_verify_access_token(self, token):
        return {"token_use": "id", "client_id": "app-client", "sub": "user-1"}

    monkeypatch.setattr(OAuthManager, "_verify_access_token", fake_verify_access_token)

    with pytest.raises(AuthenticationError, match="Expected Cognito access token"):
        await _manager()._verify_access_token("token")


@pytest.mark.asyncio
async def test_cognito_access_token_requires_matching_client_id(monkeypatch):
    async def fake_verify_access_token(self, token):
        return {"token_use": "access", "client_id": "other-client", "sub": "user-1"}

    monkeypatch.setattr(OAuthManager, "_verify_access_token", fake_verify_access_token)

    with pytest.raises(AuthenticationError, match="client_id mismatch"):
        await _manager()._verify_access_token("token")


@pytest.mark.asyncio
async def test_cognito_access_token_accepts_matching_client_id(monkeypatch):
    async def fake_verify_access_token(self, token):
        return {"token_use": "access", "client_id": "app-client", "sub": "user-1"}

    monkeypatch.setattr(OAuthManager, "_verify_access_token", fake_verify_access_token)

    payload = await _manager()._verify_access_token("token")

    assert payload["sub"] == "user-1"


@pytest.mark.asyncio
async def test_cognito_id_token_requires_id_token_use(monkeypatch):
    async def fake_jwt_verify(self, token, *, audience=None, verify_audience=False):
        assert audience == "app-client"
        assert verify_audience is True
        return {"token_use": "access", "aud": "app-client", "sub": "user-1"}

    monkeypatch.setattr(CognitoAuthManager, "_jwt_verify", fake_jwt_verify)

    with pytest.raises(AuthenticationError, match="Expected Cognito ID token"):
        await _manager()._verify_id_token("token")
