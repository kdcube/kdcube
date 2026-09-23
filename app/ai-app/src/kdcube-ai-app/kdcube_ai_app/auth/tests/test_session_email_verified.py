# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The provider's verdict on the session email rides from the sign-in into
the ``UserSession`` an application is handed.

The flag already travelled most of the way: the login lane records the
provider's ``email_verified`` in the user's metadata. This pins the last leg
(the authenticated user, the session user data, the session record and its
merges) and the three readings an application must tell apart: verified,
unverified, and unknown (a session stored before the field existed, or issued
by a path that carries no such claim)."""

from __future__ import annotations

import json
import os
import uuid

import pytest

from kdcube_ai_app.auth.AuthManager import User, email_verified_claim
from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, BundleSessionAuthority
from kdcube_ai_app.auth.bundle.sessions import BundleSessionAuthUser
from kdcube_ai_app.auth.platform_session_store import _merge_record
from kdcube_ai_app.auth.sessions import (
    RequestContext,
    SessionManager,
    UserSession,
    UserType,
    session_user_data,
)
from kdcube_ai_app.auth.tests.test_bundle_sessions import FakeRedis


def test_the_claim_reads_provider_shapes_and_keeps_absence_distinct():
    assert email_verified_claim(True) is True
    assert email_verified_claim(False) is False
    assert email_verified_claim("true") is True
    assert email_verified_claim("False") is False
    assert email_verified_claim(None) is None
    assert email_verified_claim("maybe") is None


def test_a_stored_session_without_the_field_loads_as_unknown_and_a_new_one_round_trips():
    legacy = UserSession(**{"session_id": "s-legacy", "user_type": "registered", "user_id": "u1", "username": "u1", "email": "u1@example.test"})
    assert legacy.email_verified is None
    assert "email_verified" in legacy.serialize_to_dict()
    assert legacy.to_user().email_verified is None

    verified = UserSession(session_id="s-new", user_type=UserType.REGISTERED, user_id="u2", username="u2", email="u2@example.test", email_verified=True)
    stored = json.loads(json.dumps(verified.serialize_to_dict()))
    assert stored["email_verified"] is True
    reloaded = UserSession(**stored)
    assert reloaded.email_verified is True
    assert reloaded.to_user().email_verified is True

    unverified = UserSession(session_id="s-no", user_type=UserType.REGISTERED, user_id="u3", username="u3", email_verified=False)
    assert UserSession(**unverified.serialize_to_dict()).email_verified is False


def test_session_user_data_carries_the_flag_only_when_the_login_knows_it():
    verified = BundleSessionAuthUser(username="alice", email="alice@example.test", email_verified=True, sub="sub-alice")
    data = session_user_data(verified)
    assert data["user_id"] == "sub-alice"
    assert data["email"] == "alice@example.test"
    assert data["email_verified"] is True

    unverified = BundleSessionAuthUser(username="bob", email="bob@example.test", email_verified=False, sub="sub-bob")
    assert session_user_data(unverified)["email_verified"] is False

    # A login that carries no claim says nothing, so a merge into an existing
    # session cannot turn a verified email back into an unknown one.
    unknown = User(username="carol", email="carol@example.test", roles=["r"])
    data = session_user_data(unknown, roles=["override"])
    assert "email_verified" not in data
    assert data["user_id"] == "carol"
    assert data["roles"] == ["override"]


def test_the_postgres_session_merge_follows_the_same_presence_rule():
    existing = {"session_id": "s", "user_id": "u", "email": "u@example.test", "email_verified": True, "roles": [], "permissions": []}
    context = {"client_ip": "", "user_agent": "", "user_timezone": None, "user_utc_offset_min": None}

    kept = _merge_record(existing, user_data={"user_id": "u"}, request_context=context, user_type="registered")
    assert kept["email_verified"] is True

    revoked = _merge_record(existing, user_data={"user_id": "u", "email_verified": False}, request_context=context, user_type="registered")
    assert revoked["email_verified"] is False


@pytest.mark.asyncio
async def test_a_server_login_session_carries_the_provider_verdict_and_a_legacy_user_reads_unknown():
    authority = BundleSessionAuthority(tenant="t", project="p", redis=FakeRedis(), secret="s3cret")
    manager = BundleSessionAuthManager(authority=authority)

    # What the login lane records for a Google or Cognito sign-in.
    verified = await authority.login_or_register(
        sub="sub-verified", username="person@example.test", email="person@example.test",
        metadata={"email_verified": True}, ttl_seconds=3600, idle_ttl_seconds=600,
    )
    assert (await manager.authenticate(verified.token)).email_verified is True

    unverified = await authority.login_or_register(
        sub="sub-unverified", username="other@example.test", email="other@example.test",
        metadata={"email_verified": False}, ttl_seconds=3600, idle_ttl_seconds=600,
    )
    assert (await manager.authenticate(unverified.token)).email_verified is False

    # A user registered before the lane recorded the verdict has no entry.
    await authority.register_user(sub="sub-legacy", username="legacy")
    legacy = await authority.login(sub="sub-legacy", ttl_seconds=3600, idle_ttl_seconds=600)
    assert (await manager.authenticate(legacy.token)).email_verified is None


@pytest.mark.skipif(not os.getenv("KDCUBE_TEST_REDIS_URL"), reason="requires a disposable Redis")
@pytest.mark.asyncio
async def test_the_redis_session_merge_keeps_a_verified_email_across_logins_without_the_claim():
    manager = SessionManager(
        redis_url=os.environ["KDCUBE_TEST_REDIS_URL"],
        tenant=f"t-{uuid.uuid4().hex[:8]}",
        project=f"p-{uuid.uuid4().hex[:8]}",
    )
    context = RequestContext(client_ip="192.0.2.10", user_agent="browser-agent")
    user_id = f"user-{uuid.uuid4().hex[:8]}"
    try:
        first = await manager.get_or_create_session(
            context, UserType.REGISTERED,
            {"user_id": user_id, "username": "alice", "email": "alice@example.test", "email_verified": True},
        )
        assert first.email_verified is True

        # A later request whose login carries no claim leaves the verdict alone.
        again = await manager.get_or_create_session(context, UserType.REGISTERED, {"user_id": user_id, "username": "alice"})
        assert again.session_id == first.session_id
        assert again.email_verified is True

        # A login that says unverified is recorded as such.
        revoked = await manager.get_or_create_session(
            context, UserType.REGISTERED, {"user_id": user_id, "username": "alice", "email_verified": False},
        )
        assert revoked.email_verified is False
        assert (await manager.get_session_by_id(first.session_id)).email_verified is False
    finally:
        await manager.init_redis()
        await manager.redis.delete(
            f"{manager.SESSION_PREFIX}:registered:{user_id}",
            f"{manager.SESSION_INDEX_PREFIX}:{first.session_id}",
        )
