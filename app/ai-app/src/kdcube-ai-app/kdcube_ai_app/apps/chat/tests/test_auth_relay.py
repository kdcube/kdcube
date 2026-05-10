import json

import pytest

from kdcube_ai_app.apps.chat.auth_relay import (
    AUTH_RELAY_KIND,
    consume_user_auth_relay,
    create_user_auth_relay,
    delegated_auth_cookie_header_from_cookie_header,
    delegated_auth_cookie_header_from_mapping,
    socket_connect_kwargs_from_relay,
    socket_auth_from_relay,
)
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType


class _Redis:
    def __init__(self):
        self.values = {}
        self.expires = {}

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.expires[key] = ex
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)
        return 1


@pytest.mark.asyncio
async def test_auth_relay_keeps_tokens_out_of_reference(monkeypatch):
    monkeypatch.setenv("KDCUBE_AUTH_RELAY_TTL_SEC", "120")
    redis = _Redis()
    session = UserSession(
        session_id="sess-1",
        user_type=UserType.REGISTERED,
        fingerprint="fp",
        user_id="user-1",
        username="Lena",
    )
    ctx = RequestContext(
        client_ip="127.0.0.1",
        user_agent="test",
        authorization_header="Bearer access-token",
        id_token="id-token",
    )

    ref = await create_user_auth_relay(
        redis=redis,
        request_context=ctx,
        session=session,
        tenant="tenant",
        project="project",
        bundle_id="bundle",
        conversation_id="conv",
        turn_id="turn",
        ingress_transport="socket",
        delegated_auth_cookie_header="__Secure-LMTC=masked-token",
    )

    assert ref is not None
    assert ref["kind"] == AUTH_RELAY_KIND
    assert ref["session_id"] == "sess-1"
    assert ref["token_types"] == ["bearer_token", "id_token", "delegated_auth_cookie"]
    assert "access-token" not in json.dumps(ref)
    assert "id-token" not in json.dumps(ref)
    assert "masked-token" not in json.dumps(ref)
    assert list(redis.expires.values()) == [120]

    consumed = await consume_user_auth_relay(
        redis=redis,
        ref=ref["ref"],
        expected_session_id="sess-1",
        expected_conversation_id="conv",
    )
    assert consumed["bearer_token"] == "access-token"
    assert consumed["id_token"] == "id-token"
    assert consumed["delegated_auth_cookie_header"] == "__Secure-LMTC=masked-token"
    assert redis.values == {}

    socket_auth = socket_auth_from_relay(consumed)
    assert socket_auth["user_session_id"] == "sess-1"
    assert socket_auth["bearer_token"] == "access-token"
    assert socket_auth["id_token"] == "id-token"
    assert socket_auth["client_role"] == "proc_reverse"

    socket_kwargs = socket_connect_kwargs_from_relay(consumed)
    assert socket_kwargs["uses_delegated_cookie"] is True
    assert socket_kwargs["headers"] == {"Cookie": "__Secure-LMTC=masked-token"}
    assert "bearer_token" not in socket_kwargs["auth"]
    assert "id_token" not in socket_kwargs["auth"]


def test_delegated_auth_cookie_extracts_secure_and_plain_names(monkeypatch):
    assert delegated_auth_cookie_header_from_mapping({"__Secure-LMTC": "secure-mask"}) == "__Secure-LMTC=secure-mask"
    assert delegated_auth_cookie_header_from_mapping({"LMTC": "plain-mask"}) == "LMTC=plain-mask"
    assert delegated_auth_cookie_header_from_cookie_header("foo=1; __Secure-LMTC=secure-mask; LATC=real") == "__Secure-LMTC=secure-mask"
    assert delegated_auth_cookie_header_from_cookie_header("foo=1; LMTC=plain-mask; LATC=real") == "LMTC=plain-mask"
