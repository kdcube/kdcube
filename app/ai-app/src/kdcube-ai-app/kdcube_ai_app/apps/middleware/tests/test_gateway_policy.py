from types import SimpleNamespace

from kdcube_ai_app.apps.middleware.gateway_policy import (
    EndpointClass,
    GatewayPolicyResolver,
)


def _request(path: str):
    return SimpleNamespace(url=SimpleNamespace(path=path))


def test_socket_connect_bypasses_request_throttling():
    policy = GatewayPolicyResolver().resolve(_request("/socket.io/"))

    assert policy.cls == EndpointClass.CONNECT
    assert policy.bypass_throttling is True
    assert policy.bypass_gate is True
    assert policy.bypass_backpressure is True


def test_sse_stream_connect_bypasses_request_throttling():
    policy = GatewayPolicyResolver().resolve(_request("/sse/stream"))

    assert policy.cls == EndpointClass.CONNECT
    assert policy.bypass_throttling is True


def test_regular_read_keeps_normal_session_policy():
    policy = GatewayPolicyResolver().resolve(_request("/profile"))

    assert policy.cls == EndpointClass.READ
    assert policy.bypass_throttling is False
    assert policy.bypass_gate is False
