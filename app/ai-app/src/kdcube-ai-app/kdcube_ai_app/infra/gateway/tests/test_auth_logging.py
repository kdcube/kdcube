import logging

import pytest

from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.sessions import RequestContext, UserType
from kdcube_ai_app.infra.gateway.gateway import RequestGateway


class _RejectingAuthManager:
    async def authenticate_with_both(self, access_token, id_token):
        del access_token, id_token
        raise AuthenticationError("bundle session token subject/session is missing")


@pytest.mark.asyncio
async def test_expected_auth_failure_degrades_without_warning_traceback(caplog, monkeypatch):
    monkeypatch.delenv("AUTH_DEBUG", raising=False)
    gateway = object.__new__(RequestGateway)
    gateway.auth_manager = _RejectingAuthManager()
    context = RequestContext(
        client_ip="127.0.0.1",
        user_agent="pytest",
        authorization_header="Bearer invalid-bundle-session",
    )

    with caplog.at_level(logging.WARNING, logger="kdcube_ai_app.infra.gateway.gateway"):
        user_type, user_data = await gateway._authenticate(context)

    assert user_type is UserType.ANONYMOUS
    assert user_data is None
    assert caplog.records == []
