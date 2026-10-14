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


@pytest.mark.parametrize("kind", ["remote", "missing", "not-connected", "closed", "closing", "timeout"])
@pytest.mark.asyncio
async def test_unhealthy_or_other_ingress_socket_is_not_renewed(kind):
    sio, socket = server()
    if kind == "remote":
        sio.manager.is_connected.return_value = False
    elif kind == "missing":
        sio.eio.sockets.clear()
    elif kind == "not-connected":
        socket.connected = False
    elif kind in ("closed", "closing"):
        setattr(socket, kind, True)
    else:
        socket.check_ping_timeout.return_value = False
    assert not await DataBusSocketLeases(sio)._healthy("sid")


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
