from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kdcube_ai_app.apps.chat.ingress.socketio import live_lease
from kdcube_ai_app.apps.chat.ingress.socketio.live_lease import DataBusSocketLeases
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.live_sessions import DataBusLiveSession


def server(*, health=True, connected=True):
    socket = SimpleNamespace(connected=True, closed=False, closing=False,
                             check_ping_timeout=AsyncMock(return_value=health))
    sio = SimpleNamespace(
        manager=SimpleNamespace(is_connected=Mock(return_value=connected),
                                eio_sid_from_sid=Mock(return_value="eio-1")),
        eio=SimpleNamespace(ping_interval=25, ping_timeout=20, sockets={"eio-1": socket}),
        disconnect=AsyncMock(),
    )
    return sio, socket


@pytest.mark.asyncio
async def test_idle_healthy_transport_is_checked_without_business_traffic():
    sio, socket = server()
    leases = DataBusSocketLeases(sio)
    assert leases.lease_seconds == 75
    assert await leases._healthy("sid")
    socket.check_ping_timeout.assert_awaited_once()


@pytest.mark.parametrize("kind,reason", [
    ("remote", "namespace_disconnected"), ("missing", "socket_missing"),
    ("not-connected", "socket_closed"), ("closed", "socket_closed"),
    ("closing", "socket_closed"), ("timeout", "ping_timeout"),
    ("exception", "liveness_unavailable"),
])
@pytest.mark.asyncio
async def test_unhealthy_or_other_ingress_socket_is_not_renewed(kind, reason):
    sio, socket = server()
    if kind == "remote":
        sio.manager.is_connected.return_value = False
    elif kind == "missing":
        sio.eio.sockets.clear()
    elif kind == "not-connected":
        socket.connected = False
    elif kind in ("closed", "closing"):
        setattr(socket, kind, True)
    elif kind == "exception":
        socket.check_ping_timeout.side_effect = RuntimeError("synthetic liveness failure")
    else:
        socket.check_ping_timeout.return_value = False
    leases = DataBusSocketLeases(sio)
    assert not await leases._healthy("sid")
    assert await leases._transport_refusal_reason("sid") == reason


async def run_cycles(monkeypatch, renewals):
    async def no_sleep(interval):
        assert interval == 20
    monkeypatch.setattr(live_lease.asyncio, "sleep", no_sleep)
    sio, socket = server()
    socket.check_ping_timeout.side_effect = [True] * len(renewals) + [False]
    leases = DataBusSocketLeases(sio)
    session = DataBusLiveSession("principal", "session", "sid", 2000000000, "generation")
    registry = SimpleNamespace(renew=AsyncMock(side_effect=renewals), unregister=AsyncMock())
    leases._tasks["sid"] = asyncio.current_task()
    leases._sessions["sid"] = session
    await leases._renew(registry=registry, session=session, tenant="tenant",
                        project="project", bundle_id="bundle")
    return sio, leases, registry


@pytest.mark.asyncio
async def test_idle_socket_renews_until_transport_stops(monkeypatch):
    sio, leases, registry = await run_cycles(monkeypatch, [True, True])
    assert registry.renew.await_count == 2
    registry.unregister.assert_awaited_once_with(socket_id="sid", registration_id="generation")
    sio.disconnect.assert_not_awaited()
    assert not leases._tasks and not leases._sessions


@pytest.mark.asyncio
async def test_missing_or_expired_registration_requires_new_connect(monkeypatch):
    sio, _, registry = await run_cycles(monkeypatch, [False])
    sio.disconnect.assert_awaited_once_with("sid")
    assert registry.renew.await_count == 1


@pytest.mark.asyncio
async def test_redis_outage_does_not_recreate_or_extend_missing_registration(monkeypatch):
    sio, _, registry = await run_cycles(monkeypatch, [ConnectionError("synthetic failure"), False])
    assert registry.renew.await_count == 2
    sio.disconnect.assert_awaited_once_with("sid")


@pytest.mark.asyncio
async def test_stop_from_own_disconnect_does_not_await_itself():
    sio, _ = server()
    leases = DataBusSocketLeases(sio)
    current = asyncio.current_task()
    leases._tasks["sid"] = current
    await leases.stop("sid")
    assert not current.cancelled()
    assert not leases._tasks


@pytest.mark.asyncio
async def test_actual_socketio_engineio_api_rejects_missing_pong():
    import time
    import socketio
    from engineio.async_socket import AsyncSocket

    sio = socketio.AsyncServer(async_mode="asgi")
    sid = await sio.manager.connect("engine-sid", "/")
    socket = AsyncSocket(sio.eio, "engine-sid")
    socket.connected = True
    socket.last_ping = None
    sio.eio.sockets["engine-sid"] = socket
    leases = DataBusSocketLeases(sio)
    assert await leases._healthy(sid)
    socket.last_ping = time.time() - sio.eio.ping_timeout - 1
    assert not await leases._healthy(sid)
    assert socket.closed


@pytest.mark.asyncio
async def test_refused_renewal_reports_scheduling_lag_without_session_data(monkeypatch, caplog):
    clock = [0.0]
    monkeypatch.setattr(live_lease, "monotonic", lambda: clock[0])

    async def delayed_sleep(interval):
        clock[0] += interval + 80

    monkeypatch.setattr(live_lease.asyncio, "sleep", delayed_sleep)
    sio, _ = server()
    leases = DataBusSocketLeases(sio)
    session = DataBusLiveSession("private-principal", "private-session", "private-socket", 2000000000, "private-generation")
    registry = SimpleNamespace(renew=AsyncMock(return_value=False), unregister=AsyncMock())
    leases._tasks[session.socket_id] = asyncio.current_task()
    await leases._renew(registry=registry, session=session, tenant="private-tenant",
                        project="private-project", bundle_id="private-bundle")
    assert "reason=scheduling_lag lag_ms=80000 lease_seconds=75" in caplog.text
    assert "reason=registration_refused lag_ms=80000 lease_seconds=75" in caplog.text
    assert "private-" not in caplog.text
    sio.disconnect.assert_awaited_once_with(session.socket_id)
    registry.unregister.assert_awaited_once_with(socket_id=session.socket_id, registration_id=session.registration_id)


@pytest.mark.asyncio
async def test_repeated_renewal_errors_have_bounded_diagnostics_and_keep_retrying(monkeypatch, caplog):
    monkeypatch.setattr(live_lease, "monotonic", lambda: 0.0)
    failures = [ConnectionError("private-error-detail")] * 5
    sio, _, registry = await run_cycles(monkeypatch, failures + [False])
    assert registry.renew.await_count == 6
    assert caplog.text.count("reason=renewal_unavailable") == 1
    assert "error_type=ConnectionError" in caplog.text
    assert "private-error-detail" not in caplog.text
    assert caplog.text.count("reason=registration_refused") == 1
    sio.disconnect.assert_awaited_once_with("sid")


@pytest.mark.asyncio
async def test_transport_stop_names_its_reason(monkeypatch, caplog):
    with caplog.at_level("INFO", logger=live_lease.__name__):
        sio, _, registry = await run_cycles(monkeypatch, [True])
    assert "reason=ping_timeout" in caplog.text
    sio.disconnect.assert_not_awaited()
    assert registry.renew.await_count == 1


@pytest.mark.asyncio
async def test_scheduling_lag_warning_is_throttled_per_task(monkeypatch, caplog):
    clock = [0.0]
    monkeypatch.setattr(live_lease, "monotonic", lambda: clock[0])

    async def delayed_sleep(interval):
        clock[0] += interval + 20

    monkeypatch.setattr(live_lease.asyncio, "sleep", delayed_sleep)
    sio, socket = server()
    socket.check_ping_timeout.side_effect = [True, True, True, False]
    leases = DataBusSocketLeases(sio)
    session = DataBusLiveSession("principal", "session", "sid", 2000000000, "generation")
    registry = SimpleNamespace(renew=AsyncMock(return_value=True), unregister=AsyncMock())
    leases._tasks["sid"] = asyncio.current_task()
    await leases._renew(registry=registry, session=session, tenant="tenant",
                        project="project", bundle_id="bundle")
    assert caplog.text.count("reason=scheduling_lag") == 2
    assert registry.renew.await_count == 3
    sio.disconnect.assert_not_awaited()
