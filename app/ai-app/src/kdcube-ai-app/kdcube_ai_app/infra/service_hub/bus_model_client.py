# SPDX-License-Identifier: MIT
"""Model calls for the ``custom`` provider over the KDCube Data Bus.

Selected by setting the ``custom`` provider endpoint to a ``bus://`` URL::

    services.llm.custom.endpoint: bus://llm-gateway                 # tenant/project of this runtime
    services.llm.custom.endpoint: bus://<tenant>/<project>/llm-gateway

Every model call becomes a durable Data Bus message with subject
``llm.generate`` on ``kdcube:data-bus:{tenant}:{project}:{bus_id}:messages``.
A model provider on any host that reaches the same Redis claims it through
the Data Bus consumer group, streams the reply into
``kdcube:data-bus:{tenant}:{project}:{bus_id}:deltas:{message_id}`` and
writes the standard Data Bus result. The message payload is exactly the body
``CustomModelClient`` POSTs to an HTTP model gateway, plus ``delta_stream``,
so a provider is a transport swap over the same contract.
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Dict, List

from langchain_core.messages import AIMessage, BaseMessage

from kdcube_ai_app.infra.accounting.usage import _norm_usage_dict
from kdcube_ai_app.infra.service_hub.inventory import CustomModelClient

LLM_BUS_SUBJECT = "llm.generate"
LLM_BUS_SCHEME = "bus://"
DEFAULT_FIRST_EVENT_TIMEOUT_S = 120.0
DEFAULT_TIMEOUT_S = 600.0


def is_bus_endpoint(endpoint: str | None) -> bool:
    return str(endpoint or "").strip().lower().startswith(LLM_BUS_SCHEME)


def parse_bus_endpoint(endpoint: str, *, tenant: str, project: str) -> tuple[str, str, str]:
    """``bus://<id>`` or ``bus://<tenant>/<project>/<id>`` -> (tenant, project, id)."""
    rest = str(endpoint).strip()[len(LLM_BUS_SCHEME):].strip("/")
    parts = [p for p in rest.split("/") if p]
    if len(parts) == 1:
        if not tenant or not project:
            raise ValueError(f"bus endpoint {endpoint!r} needs a tenant and project in this runtime")
        return tenant, project, parts[0]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    raise ValueError(f"bus endpoint must be bus://<id> or bus://<tenant>/<project>/<id>: {endpoint!r}")


def llm_bus_delta_key(*, tenant: str, project: str, bus_id: str, message_id: str) -> str:
    from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import data_bus_stream_key

    return data_bus_stream_key(tenant=tenant, project=project, bundle_id=bus_id, kind=f"deltas:{message_id}")


def _decode(v: Any) -> str:
    return v.decode("utf-8", errors="replace") if isinstance(v, (bytes, bytearray)) else str(v)


class BusModelClient(CustomModelClient):
    """``CustomModelClient`` whose transport is the Data Bus instead of HTTP.

    It keeps the ``CustomModelClient`` type, so streaming, accounting and
    client-config handling for ``provider: custom`` apply unchanged.
    """

    def __init__(
        self,
        *args,
        tenant: str | None = None,
        project: str | None = None,
        redis: Any = None,
        publisher: Any = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        first_event_timeout_s: float = DEFAULT_FIRST_EVENT_TIMEOUT_S,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if not tenant or not project:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings

            settings = get_settings()
            tenant = tenant or str(getattr(settings, "TENANT", "") or "")
            project = project or str(getattr(settings, "PROJECT", "") or "")
        self.tenant, self.project, self.bus_id = parse_bus_endpoint(
            self.endpoint, tenant=tenant, project=project,
        )
        self._redis = redis
        self._publisher = publisher
        self.timeout_s = float(timeout_s)
        self.first_event_timeout_s = float(first_event_timeout_s)

    def _redis_client(self) -> Any:
        if self._redis is None:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings
            from kdcube_ai_app.infra.redis.client import get_async_redis_client

            self._redis = get_async_redis_client(get_settings().REDIS_URL)
        return self._redis

    def _publisher_client(self) -> Any:
        if self._publisher is None:
            from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.publisher import DataBusPublisher

            self._publisher = DataBusPublisher(
                redis=self._redis_client(), tenant=self.tenant, project=self.project, bundle_id=self.bus_id,
            )
        return self._publisher

    async def _events(self, messages: List[BaseMessage], **kwargs) -> AsyncIterator[Dict[str, Any]]:
        from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import timestamp_message_id

        message_id = timestamp_message_id("llm")
        delta_key = llm_bus_delta_key(
            tenant=self.tenant, project=self.project, bus_id=self.bus_id, message_id=message_id,
        )
        payload = self._prepare_payload(messages, **kwargs)
        payload["delta_stream"] = delta_key
        await self._publisher_client().publish(
            subject=LLM_BUS_SUBJECT,
            payload=payload,
            message_id=message_id,
            idempotency_key=message_id,
            trace={"model": self.model_name},
        )
        redis = self._redis_client()
        last_id, started = "0-0", time.monotonic()
        got_any = False
        while True:
            elapsed = time.monotonic() - started
            limit = self.timeout_s if got_any else self.first_event_timeout_s
            if elapsed >= limit:
                what = "completion" if got_any else "provider"
                raise TimeoutError(
                    f"llm bus: no {what} for {self.model_name} on {self.bus_id} after {elapsed:.0f}s"
                )
            block_ms = int(max(50, min(1000, (limit - elapsed) * 1000)))
            items = await redis.xread({delta_key: last_id}, count=64, block=block_ms)
            for _key, entries in items or []:
                for entry_id, fields in entries:
                    last_id = _decode(entry_id)
                    data = {_decode(k): v for k, v in dict(fields or {}).items()}
                    try:
                        evt = json.loads(_decode(data.get("json") or "{}"))
                    except Exception:
                        continue
                    if not isinstance(evt, dict):
                        continue
                    got_any = True
                    if evt.get("final") or evt.get("error"):
                        # Delete before the last yield: callers return on the
                        # final event and never resume this generator.
                        try:
                            await redis.delete(delta_key)
                        except Exception:
                            pass
                        yield evt
                        return
                    yield evt

    async def ainvoke(self, messages: List[BaseMessage], **kwargs) -> AIMessage:
        self.logger.start_operation(
            "bus_model_invocation", model_name=self.model_name, endpoint=self.endpoint, message_count=len(messages),
        )
        parts: list[str] = []
        usage: dict = {}
        try:
            async for evt in self._events(messages, **kwargs):
                if evt.get("error"):
                    raise RuntimeError(f"Model error: {evt['error']}")
                if evt.get("delta"):
                    parts.append(str(evt["delta"]))
                if evt.get("final"):
                    usage = evt.get("usage") or {}
        except Exception as e:
            self.logger.log_error(e, "bus_invoke_failed")
            self.logger.finish_operation(False, "bus_invoke_failed")
            raise
        text = "".join(parts) or "No response generated"
        self.logger.finish_operation(True, f"Generated {len(text)} characters")
        return AIMessage(content=text, additional_kwargs={"usage": usage, "model_name": self.model_name})

    async def astream(self, messages: List[BaseMessage], **kwargs) -> AsyncIterator[Dict[str, Any]]:
        async for evt in self._events(messages, **{**kwargs, "stream": True}):
            if evt.get("error"):
                raise RuntimeError(f"Model error: {evt['error']}")
            if evt.get("delta"):
                yield {"delta": evt["delta"]}
            if evt.get("final"):
                yield {"event": "final", "usage": _norm_usage_dict(evt.get("usage") or {}), "model_name": self.model_name}
                return
