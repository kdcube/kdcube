from __future__ import annotations

import pytest

from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.bundle import (
    BundleSessionAuthManager,
    BundleSessionAuthority,
    BundleSessionInvalid,
)


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, nx=False, ex=None):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = str(value)
        return True

    async def setex(self, key, ttl, value):
        del ttl
        self.values[key] = str(value)
        return True

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.values:
                removed += 1
                self.values.pop(key, None)
            if key in self.sets:
                removed += 1
                self.sets.pop(key, None)
        return removed

    async def incr(self, key):
        value = int(self.values.get(key) or 0) + 1
        self.values[key] = str(value)
        return value

    async def sadd(self, key, *values):
        target = self.sets.setdefault(key, set())
        before = len(target)
        target.update(str(value) for value in values)
        return len(target) - before

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def expire(self, key, ttl):
        del key, ttl
        return True


@pytest.mark.asyncio
async def test_bundle_session_register_login_validate_logout():
    authority = BundleSessionAuthority(
        tenant="tenant-a",
        project="project-a",
        redis=FakeRedis(),
        secret="session-secret",
    )

    await authority.register_user(
        sub="google:123",
        username="alice",
        email="alice@example.test",
        roles=["kdcube:role:chat-user"],
        permissions=["kdcube:*:chat:*;read;write"],
        provider="google",
        provider_subject="123",
    )
    grant = await authority.login(sub="google:123", provider="google", provider_subject="123")

    verified = await authority.validate_token(grant.token)
    assert verified.session_id == grant.session_id
    assert verified.user.sub == "google:123"
    assert verified.user.roles == ["kdcube:role:chat-user"]

    assert await authority.logout(token=grant.token) is True
    with pytest.raises(BundleSessionInvalid):
        await authority.validate_token(grant.token)


@pytest.mark.asyncio
async def test_bundle_session_delete_user_invalidates_existing_session():
    authority = BundleSessionAuthority(
        tenant="tenant-a",
        project="project-a",
        redis=FakeRedis(),
        secret="session-secret",
    )

    await authority.register_user(sub="telegram:42", username="bob", roles=["kdcube:role:chat-user"])
    grant = await authority.login(sub="telegram:42")

    assert await authority.delete_user("telegram:42") is True
    with pytest.raises(BundleSessionInvalid):
        await authority.validate_token(grant.token)


@pytest.mark.asyncio
async def test_bundle_session_invalidate_user_bumps_version():
    authority = BundleSessionAuthority(
        tenant="tenant-a",
        project="project-a",
        redis=FakeRedis(),
        secret="session-secret",
    )

    await authority.register_user(sub="oidc:abc", username="carol", roles=["kdcube:role:chat-user"])
    grant = await authority.login(sub="oidc:abc")

    await authority.invalidate_user("oidc:abc")
    with pytest.raises(BundleSessionInvalid, match="not active|invalidated"):
        await authority.validate_token(grant.token)


@pytest.mark.asyncio
async def test_bundle_session_auth_manager_returns_gateway_user():
    authority = BundleSessionAuthority(
        tenant="tenant-a",
        project="project-a",
        redis=FakeRedis(),
        secret="session-secret",
    )
    await authority.register_user(
        sub="google:admin",
        username="admin",
        email="admin@example.test",
        roles=["kdcube:role:super-admin"],
    )
    grant = await authority.login(sub="google:admin")
    manager = BundleSessionAuthManager(authority=authority)

    user = await manager.authenticate(grant.token)
    assert user.sub == "google:admin"
    assert user.username == "admin"
    assert user.email == "admin@example.test"
    assert user.roles == ["kdcube:role:super-admin"]

    with pytest.raises(AuthenticationError):
        await manager.authenticate("bad-token")


@pytest.mark.asyncio
async def test_bundle_session_login_or_register_embeds_credential_claim():
    authority = BundleSessionAuthority(
        tenant="tenant-a",
        project="project-a",
        redis=FakeRedis(),
        secret="session-secret",
    )
    credential = {
        "schema": "kdcube.credential.v1",
        "credential_id": "cred_test",
        "credential_kind": "delegated_client_access",
        "issuer_authority_id": "oauth_mcp",
        "issuer_authenticator_id": "oauth_mcp.bearer",
        "subject": "integration:claude:google:admin@example.test",
        "audience": "kdcube:mcp",
        "attrs": {"client_id": "claude"},
    }

    grant = await authority.login_or_register(
        sub="integration:claude:google:admin@example.test",
        username="claude-feedback-reader",
        roles=["kdcube:role:feedback-reader"],
        provider="integration",
        provider_subject="google:admin@example.test",
        metadata={"credential": credential},
    )

    assert grant.claims["credential"]["schema"] == "kdcube.credential.v1"
    assert grant.claims["credential"]["issuer_authority_id"] == "oauth_mcp"
    assert grant.claims["credential"]["attrs"] == {"client_id": "claude"}
    verified = await authority.validate_token(grant.token)
    assert verified.claims["credential"]["subject"] == credential["subject"]
