# SPDX-License-Identifier: MIT

"""``provider: custom`` over the Data Bus (``bus://`` endpoints).

The provider side is a one-shot stand-in: it reads the published
``llm.generate`` message and writes reply events into the message's delta
stream, as a model provider on another host does through Redis.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting import model_service
from kdcube_ai_app.infra.service_hub.bus_model_client import (
    LLM_BUS_SUBJECT,
    BusModelClient,
    is_bus_endpoint,
    parse_bus_endpoint,
)
from kdcube_ai_app.infra.service_hub.inventory import CustomModelClient, ModelRouter


class _Streams:
    """The slice of an async Redis client the bus client reads: xread + delete."""

    def __init__(self):
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self._changed = asyncio.Event()

    def xadd(self, key: str, fields: dict) -> None:
        entries = self.streams.setdefault(key, [])
        entries.append((f"{len(entries) + 1}-0", {k.encode(): v.encode() for k, v in fields.items()}))
        self._changed.set()

    async def xread(self, streams: dict, count: int = 64, block: int = 0):
        (key, last), = streams.items()
        deadline = asyncio.get_running_loop().time() + block / 1000
        while True:
            newer = [(eid, f) for eid, f in self.streams.get(key, []) if int(eid.split("-")[0]) > int(last.split("-")[0])]
            if newer:
                return [(key.encode(), [(eid.encode(), f) for eid, f in newer[:count]])]
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return []
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                return []

    async def delete(self, key: str) -> None:
        self.streams.pop(key, None)


class _Publisher:
    def __init__(self):
        self.published: list[dict] = []
        self.arrived = asyncio.Event()

    async def publish(self, **kwargs):
        self.published.append(kwargs)
        self.arrived.set()
        return SimpleNamespace(message_id=kwargs["message_id"])


async def _provider(redis: _Streams, publisher: _Publisher, *, reply="Hello there.", error=None) -> dict:
    await asyncio.wait_for(publisher.arrived.wait(), 5)
    message = publisher.published[-1]
    assert message["subject"] == LLM_BUS_SUBJECT
    payload = message["payload"]
    delta = payload["delta_stream"]
    if error:
        redis.xadd(delta, {"json": json.dumps({"error": error})})
        return message
    for piece in (reply[:5], reply[5:]):
        redis.xadd(delta, {"json": json.dumps({"delta": piece})})
    redis.xadd(delta, {"json": json.dumps(
        {"final": True, "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})})
    return message


def _client(redis, publisher, **kw) -> BusModelClient:
    return BusModelClient(
        endpoint="bus://acme/demo/llm-gateway", api_key="", model_name="local:model",
        temperature=0.0, num_ctx=32768, redis=redis, publisher=publisher,
        first_event_timeout_s=5, **kw,
    )


def _msgs():
    return [SystemMessage(content="be brief"), HumanMessage(content="hi")]


def test_endpoint_parsing():
    assert is_bus_endpoint("bus://llm-gateway")
    assert not is_bus_endpoint("http://gw:11500/generate")
    assert parse_bus_endpoint("bus://llm-gateway", tenant="t", project="p") == ("t", "p", "llm-gateway")
    assert parse_bus_endpoint("bus://a/b/c", tenant="t", project="p") == ("a", "b", "c")
    with pytest.raises(ValueError):
        parse_bus_endpoint("bus://a/b", tenant="t", project="p")
    with pytest.raises(ValueError):
        parse_bus_endpoint("bus://llm-gateway", tenant="", project="")


def test_router_builds_a_bus_client_only_for_bus_endpoints():
    def _router(endpoint):
        router = ModelRouter.__new__(ModelRouter)
        router.config = SimpleNamespace(
            custom_model_endpoint=endpoint, custom_model_api_key="", custom_model_num_ctx=None,
            custom_model_overrides=None, tenant="t", project="p",
        )
        router._cache = {}
        return router

    bus = _router("bus://llm-gateway")._mk_custom("local:model", 0.2)
    http = _router("http://gw/generate")._mk_custom("local:model", 0.2)

    assert isinstance(bus, BusModelClient) and isinstance(bus, CustomModelClient)
    assert (bus.tenant, bus.project, bus.bus_id) == ("t", "p", "llm-gateway")
    assert type(http) is CustomModelClient


def test_direct_hosting_accepts_a_bus_endpoint():
    assert model_service._custom_model_endpoint("bus://llm-gateway") == "bus://llm-gateway"
    with pytest.raises(ValueError):
        model_service._custom_model_endpoint("bus://")
    with pytest.raises(ValueError):
        model_service._custom_model_endpoint("ftp://gw/generate")


@pytest.mark.asyncio
async def test_ainvoke_round_trip_carries_the_gateway_payload():
    redis, publisher = _Streams(), _Publisher()
    provider = asyncio.create_task(_provider(redis, publisher))

    out = await _client(redis, publisher).ainvoke(_msgs(), max_new_tokens=40)
    message = await provider

    assert out.content == "Hello there."
    assert out.additional_kwargs["usage"]["completion_tokens"] == 3
    payload = message["payload"]
    assert payload["model"] == "local:model"
    assert payload["inputs"][0] == {"role": "system", "content": "be brief"}
    assert payload["parameters"]["max_new_tokens"] == 40
    assert payload["parameters"]["num_ctx"] == 32768
    assert payload["delta_stream"] == f"kdcube:data-bus:acme:demo:llm-gateway:deltas:{message['message_id']}"
    assert redis.streams == {}


@pytest.mark.asyncio
async def test_astream_yields_deltas_then_final_and_cleans_up():
    redis, publisher = _Streams(), _Publisher()
    provider = asyncio.create_task(_provider(redis, publisher))

    events = [e async for e in _client(redis, publisher).astream(_msgs())]
    message = await provider

    assert message["payload"]["parameters"]["stream"] is True
    assert "".join(e.get("delta", "") for e in events) == "Hello there."
    assert events[-1]["event"] == "final"
    assert events[-1]["usage"]["output_tokens"] == 3
    assert redis.streams == {}


@pytest.mark.asyncio
async def test_a_provider_error_raises():
    redis, publisher = _Streams(), _Publisher()
    provider = asyncio.create_task(_provider(redis, publisher, error="model not found"))

    with pytest.raises(RuntimeError, match="model not found"):
        await _client(redis, publisher).ainvoke(_msgs())
    await provider


@pytest.mark.asyncio
async def test_no_provider_times_out():
    client = _client(_Streams(), _Publisher())
    client.first_event_timeout_s = 0.3

    with pytest.raises(TimeoutError, match="no provider"):
        await client.ainvoke(_msgs())
