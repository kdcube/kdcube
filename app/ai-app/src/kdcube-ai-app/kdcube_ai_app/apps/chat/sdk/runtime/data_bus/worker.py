# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator, ChatRelayCommunicator
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ConversationCtx,
    ExternalEventActor,
    ExternalEventMeta,
    ExternalEventPayload,
    ExternalEventRequest,
    ExternalEventRouting,
    ExternalEventUser,
    ServiceCtx,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.locks import (
    RedisDataBusPartitionLocker,
    renew_lock_until_cancelled,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import DataBusClaim, RedisDataBusStream
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import (
    DATA_BUS_IDEMPOTENCY_REQUIRED,
    DATA_BUS_ORDERING_SERIAL_PER_PARTITION,
    DATA_BUS_PARTITION_OBJECT_REF,
    DataBusContext,
    DataBusHandlerSpec,
    DataBusMessage,
    DataBusReply,
    DataBusResult,
    coerce_data_bus_result,
    now_ms,
)
from kdcube_ai_app.infra.plugin.bundle_loader import BundleSpec, get_workflow_instance_async, load_bundle_manifest

_log = logging.getLogger("kdcube.data_bus.worker")

DATA_BUS_CLAIM_BLOCK_MS = max(1, int(os.getenv("DATA_BUS_CLAIM_BLOCK_MS", "1000") or "1000"))
DATA_BUS_HANDLER_TIMEOUT_SECONDS = max(1, int(os.getenv("DATA_BUS_HANDLER_TIMEOUT_SECONDS", "120") or "120"))
DATA_BUS_MAX_RETRIES = max(0, int(os.getenv("DATA_BUS_MAX_RETRIES", "5") or "5"))
DATA_BUS_LOCK_MAX_RETRIES = max(1, int(os.getenv("DATA_BUS_LOCK_MAX_RETRIES", "100") or "100"))
DATA_BUS_LOCK_TTL_SECONDS = max(1, int(os.getenv("DATA_BUS_LOCK_TTL_SECONDS", "60") or "60"))
DATA_BUS_LOCK_RENEW_INTERVAL_SECONDS = max(
    0.1,
    float(os.getenv("DATA_BUS_LOCK_RENEW_INTERVAL_SECONDS", "10") or "10"),
)
DATA_BUS_LOCK_RETRY_SLEEP_SECONDS = max(
    0.0,
    float(os.getenv("DATA_BUS_LOCK_RETRY_SLEEP_SECONDS", "0.05") or "0.05"),
)


@dataclass(frozen=True)
class _DataBusWorkerKey:
    bundle_id: str


def _actor_user_id(actor: Mapping[str, Any] | None) -> str | None:
    actor = actor or {}
    return actor.get("user_id") or actor.get("fingerprint")


def _make_comm_context(message: DataBusMessage) -> ExternalEventPayload:
    actor = message.actor or {}
    reply = message.reply or {}
    session_id = str(reply.get("session_id") or actor.get("session_id") or "data-bus")
    socket_id = str(reply.get("socket_id") or "").strip() or None
    request_id = str((message.trace or {}).get("request_id") or message.message_id)
    return ExternalEventPayload(
        meta=ExternalEventMeta(
            task_id=message.message_id,
            created_at=now_ms() / 1000.0,
            instance_id=os.getenv("INSTANCE_ID"),
        ),
        routing=ExternalEventRouting(
            bundle_id=message.bundle_id,
            session_id=session_id,
            conversation_id=session_id,
            turn_id=None,
            socket_id=socket_id,
        ),
        actor=ExternalEventActor(
            tenant_id=message.tenant,
            project_id=message.project,
        ),
        user=ExternalEventUser(
            user_type=str(actor.get("user_type") or "anonymous"),
            user_id=actor.get("user_id"),
            username=actor.get("username"),
            email=actor.get("email"),
            fingerprint=actor.get("fingerprint"),
            roles=list(actor.get("roles") or []),
            permissions=list(actor.get("permissions") or []),
            timezone=actor.get("timezone"),
        ),
        request=ExternalEventRequest(
            request_id=request_id,
            operation="data_bus",
            payload={
                "subject": message.subject,
                "message_id": message.message_id,
            },
        ),
    )


def _make_reply_comm(
    *,
    message: DataBusMessage,
    relay: ChatRelayCommunicator,
) -> ChatCommunicator | None:
    reply = message.reply or {}
    session_id = str(reply.get("session_id") or (message.actor or {}).get("session_id") or "").strip()
    if not session_id:
        return None
    socket_id = str(reply.get("socket_id") or "").strip() or None
    request_id = str((message.trace or {}).get("request_id") or message.message_id)
    user_id = _actor_user_id(message.actor)
    service = ServiceCtx(
        request_id=request_id,
        tenant=message.tenant,
        project=message.project,
        user=user_id,
    )
    conversation = ConversationCtx(
        session_id=session_id,
        conversation_id=session_id,
        turn_id=None,
    )
    return ChatCommunicator(
        emitter=relay,
        service=service.model_dump(),
        conversation=conversation.model_dump(),
        room=session_id,
        target_sid=socket_id,
        tenant=message.tenant,
        project=message.project,
        user_id=(message.actor or {}).get("user_id"),
        user_type=(message.actor or {}).get("user_type"),
    )


async def _refresh_bundle_props(
    *,
    instance: Any,
    tenant: str,
    project: str,
    bundle_id: str,
) -> None:
    refresh_fn = getattr(instance, "refresh_bundle_props", None)
    if not callable(refresh_fn):
        return
    try:
        refresh_kwargs = {"state": {"tenant": tenant, "project": project}}
        try:
            refresh_params = inspect.signature(refresh_fn).parameters
        except (TypeError, ValueError):
            refresh_params = {}
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in refresh_params.values())
        if accepts_kwargs or "notify" in refresh_params:
            refresh_kwargs["notify"] = False
        if accepts_kwargs or "reason" in refresh_params:
            refresh_kwargs["reason"] = "data_bus"
        result = refresh_fn(**refresh_kwargs)
        if inspect.isawaitable(result):
            await result
    except Exception:
        _log.warning(
            "[data_bus] refresh_bundle_props failed for bundle=%s; proceeding with current props",
            bundle_id,
            exc_info=True,
        )


class DataBusBundleWorker:
    def __init__(
        self,
        *,
        redis: Any,
        redis_url: str | None,
        tenant: str,
        project: str,
        bundle_id: str,
        bundle_spec: BundleSpec,
        bundle_config: Any,
        handler_specs: Mapping[str, DataBusHandlerSpec],
        instance_id: str,
    ) -> None:
        self.redis = redis
        self.redis_url = redis_url
        self.tenant = tenant
        self.project = project
        self.bundle_id = bundle_id
        self.bundle_spec = bundle_spec
        self.bundle_config = bundle_config
        self.handler_specs = dict(handler_specs or {})
        self.instance_id = instance_id
        self.consumer_name = f"{instance_id or os.getpid()}:{os.getpid()}:{bundle_id}"
        self.stream = RedisDataBusStream(
            redis,
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )
        self.locker = RedisDataBusPartitionLocker(redis, ttl_seconds=DATA_BUS_LOCK_TTL_SECONDS)
        self.relay = ChatRelayCommunicator(redis_url=redis_url)
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        await self.stream.ensure_group()
        _log.info(
            "[data_bus] Worker started: tenant=%s project=%s bundle=%s handlers=%s consumer=%s",
            self.tenant,
            self.project,
            self.bundle_id,
            sorted(self.handler_specs.keys()),
            self.consumer_name,
        )
        while not self._stop_event.is_set():
            try:
                claim = await self.stream.claim_next(
                    consumer_name=self.consumer_name,
                    count=1,
                    block_ms=DATA_BUS_CLAIM_BLOCK_MS,
                )
                if claim is None:
                    await asyncio.sleep(0)
                    continue
                await self._process_claim(claim)
            except asyncio.CancelledError:
                break
            except Exception:
                _log.warning("[data_bus] Worker loop failed: bundle=%s", self.bundle_id, exc_info=True)
                await asyncio.sleep(0.5)
        _log.info("[data_bus] Worker stopped: bundle=%s", self.bundle_id)

    def stop(self) -> None:
        self._stop_event.set()

    async def _process_claim(self, claim: DataBusClaim) -> None:
        message = claim.message
        handler_spec = self.handler_specs.get(message.subject)
        if handler_spec is None:
            result = DataBusResult.error_result(
                message,
                code="handler_not_found",
                message_text=f"No Data Bus handler registered for subject {message.subject}",
                status="rejected",
            )
            await self.stream.write_result(result, stream_id=claim.stream_id)
            await self.stream.write_dlq(message, reason="handler_not_found", details={"stream_id": claim.stream_id})
            await self.stream.ack(claim)
            return
        if handler_spec.idempotency == DATA_BUS_IDEMPOTENCY_REQUIRED and not message.idempotency_key:
            result = DataBusResult.error_result(
                message,
                code="idempotency_key_required",
                message_text="Data Bus handler requires idempotency_key",
                status="rejected",
            )
            await self.stream.write_result(result, stream_id=claim.stream_id)
            await self.stream.write_dlq(message, reason="idempotency_key_required", details={"stream_id": claim.stream_id})
            await self.stream.ack(claim)
            return

        lock = None
        renew_task: asyncio.Task | None = None
        try:
            if handler_spec.ordering == DATA_BUS_ORDERING_SERIAL_PER_PARTITION:
                partition_key = self._partition_key(handler_spec, message)
                if not partition_key:
                    result = DataBusResult.error_result(
                        message,
                        code="partition_required",
                        message_text="Data Bus handler requires an object partition",
                        status="rejected",
                    )
                    await self.stream.write_result(result, stream_id=claim.stream_id)
                    await self.stream.write_dlq(message, reason="partition_required", details={"stream_id": claim.stream_id})
                    await self.stream.ack(claim)
                    return
                lock = await self.locker.acquire(partition_key)
                if lock is None:
                    if DATA_BUS_LOCK_RETRY_SLEEP_SECONDS > 0:
                        await asyncio.sleep(DATA_BUS_LOCK_RETRY_SLEEP_SECONDS)
                        lock = await self.locker.acquire(partition_key)
                if lock is None:
                    await self.stream.requeue(
                        claim,
                        reason="partition_lock_busy",
                        max_retries=DATA_BUS_LOCK_MAX_RETRIES,
                    )
                    return
                renew_task = asyncio.create_task(
                    renew_lock_until_cancelled(
                        self.locker,
                        lock,
                        interval_seconds=DATA_BUS_LOCK_RENEW_INTERVAL_SECONDS,
                    ),
                    name=f"data-bus-lock-renew:{self.bundle_id}:{message.subject}",
                )

            result, reply_sent = await self._invoke_handler(claim, handler_spec)
            await self.stream.write_result(result, stream_id=claim.stream_id)
            if not reply_sent:
                await self._send_default_reply(message, result)
            await self.stream.ack(claim)
        except Exception as exc:
            retry_count = int((message.trace or {}).get("retry_count") or 0)
            if retry_count < DATA_BUS_MAX_RETRIES:
                _log.warning(
                    "[data_bus] Handler failed; requeueing: bundle=%s subject=%s message=%s retry=%s",
                    self.bundle_id,
                    message.subject,
                    message.message_id,
                    retry_count + 1,
                    exc_info=True,
                )
                await self.stream.requeue(
                    claim,
                    reason="handler_error",
                    max_retries=DATA_BUS_MAX_RETRIES,
                )
                return
            result = DataBusResult.error_result(
                message,
                code="handler_error",
                message_text=str(exc),
                details={"stream_id": claim.stream_id, "retry_count": retry_count},
            )
            await self.stream.write_result(result, stream_id=claim.stream_id)
            await self.stream.write_dlq(
                message,
                reason="handler_error",
                details={"stream_id": claim.stream_id, "retry_count": retry_count, "error": str(exc)},
            )
            await self._send_default_reply(message, result)
            await self.stream.ack(claim)
        finally:
            if renew_task is not None:
                renew_task.cancel()
                try:
                    await renew_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    _log.debug("[data_bus] Lock renewal task ended with error", exc_info=True)
            if lock is not None:
                try:
                    await self.locker.release(lock)
                except Exception:
                    _log.debug("[data_bus] Failed to release partition lock: key=%s", lock.key, exc_info=True)

    def _partition_key(self, handler_spec: DataBusHandlerSpec, message: DataBusMessage) -> str | None:
        if handler_spec.partition_by == DATA_BUS_PARTITION_OBJECT_REF:
            object_ref = str(message.object_ref or "").strip()
            if not object_ref:
                return None
            return f"{message.tenant}:{message.project}:{message.bundle_id}:{message.subject}:{object_ref}"
        return f"{message.tenant}:{message.project}:{message.bundle_id}:{message.subject}"

    async def _invoke_handler(self, claim: DataBusClaim, handler_spec: DataBusHandlerSpec) -> tuple[DataBusResult, bool]:
        message = claim.message
        comm_context = _make_comm_context(message)
        pg_pool = None
        try:
            from kdcube_ai_app.apps.chat.ingress.resolvers import get_pg_pool
            pg_pool = await get_pg_pool()
        except Exception:
            _log.debug("[data_bus] pg_pool not available for bundle=%s", self.bundle_id, exc_info=True)

        instance, _ = await get_workflow_instance_async(
            self.bundle_spec,
            self.bundle_config,
            comm_context=comm_context,
            redis=getattr(self.bundle_config, "redis", None),
            pg_pool=pg_pool,
        )
        await _refresh_bundle_props(
            instance=instance,
            tenant=message.tenant,
            project=message.project,
            bundle_id=message.bundle_id,
        )

        fn = getattr(instance, handler_spec.method_name, None)
        if fn is None:
            raise RuntimeError(f"Data Bus handler method not found: {handler_spec.method_name}")

        reply_comm = _make_reply_comm(message=message, relay=self.relay)
        reply = DataBusReply(message=message, comm=reply_comm)
        ctx = DataBusContext(
            tenant=message.tenant,
            project=message.project,
            bundle_id=message.bundle_id,
            actor=dict(message.actor or {}),
            bundle=instance,
            comm=reply_comm,
            reply=reply,
            stream_id=claim.stream_id,
            consumer_name=claim.consumer_name,
            handler=handler_spec,
        )
        with bind_current_request_context(
            comm_context,
            comm=reply_comm,
            bundle_id=message.bundle_id,
        ):
            if inspect.iscoroutinefunction(fn):
                result = await asyncio.wait_for(fn(ctx, message), timeout=DATA_BUS_HANDLER_TIMEOUT_SECONDS)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(fn, ctx, message),
                    timeout=DATA_BUS_HANDLER_TIMEOUT_SECONDS,
                )
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=DATA_BUS_HANDLER_TIMEOUT_SECONDS)
            return coerce_data_bus_result(result, message), reply.sent_count > 0

    async def _send_default_reply(self, message: DataBusMessage, result: DataBusResult) -> None:
        if not message.reply:
            return
        reply_comm = _make_reply_comm(message=message, relay=self.relay)
        reply = DataBusReply(message=message, comm=reply_comm)
        if result.status == "ok":
            await reply.ok(result.data)
        elif result.status == "conflict":
            await reply.conflict(result.data)
        elif result.error:
            await reply.error(
                str(result.error.get("code") or result.status or "error"),
                str(result.error.get("message") or "Data Bus handler failed"),
                result.error.get("details") if isinstance(result.error.get("details"), Mapping) else None,
            )


class DataBusRuntimeManager:
    def __init__(
        self,
        *,
        redis: Any,
        redis_url: Optional[str],
        tenant: str,
        project: str,
        instance_id: str,
    ) -> None:
        self._redis = redis
        self._redis_url = redis_url
        self._worker_redis = redis
        self._owns_worker_redis = False
        if redis_url:
            try:
                from kdcube_ai_app.infra.redis.client import create_async_redis_client
                self._worker_redis = create_async_redis_client(
                    redis_url,
                    client_name_kind="data_bus",
                )
                self._owns_worker_redis = True
            except Exception:
                _log.warning(
                    "[data_bus] Could not create dedicated worker Redis client; falling back to shared pool",
                    exc_info=True,
                )
                self._worker_redis = redis
        self._tenant = tenant
        self._project = project
        self._instance_id = instance_id
        self._workers: Dict[_DataBusWorkerKey, Tuple[asyncio.Task, str, DataBusBundleWorker]] = {}
        _log.info(
            "[data_bus] Manager initialised: tenant=%s project=%s instance=%s",
            tenant,
            project,
            instance_id,
        )

    async def reconcile(self, registry: Any) -> None:
        from kdcube_ai_app.apps.chat.sdk.runtime.bundle_scheduler import _make_headless_config, is_bundle_enabled
        from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props

        desired: Dict[_DataBusWorkerKey, Tuple[str, BundleSpec, Any, Dict[str, DataBusHandlerSpec]]] = {}
        for bundle_id, entry in (getattr(registry, "bundles", None) or {}).items():
            path = entry.path if hasattr(entry, "path") else entry.get("path", "")
            module = entry.module if hasattr(entry, "module") else entry.get("module")
            singleton = entry.singleton if hasattr(entry, "singleton") else entry.get("singleton", False)
            if not path:
                continue
            spec = BundleSpec(path=path, module=module, singleton=bool(singleton))
            try:
                manifest = load_bundle_manifest(spec, bundle_id=bundle_id)
            except Exception:
                _log.warning("[data_bus] Failed to load manifest for bundle=%s; skipping", bundle_id, exc_info=True)
                continue
            if not manifest.data_bus_handlers:
                continue
            try:
                props = await get_bundle_props(
                    self._redis,
                    tenant=self._tenant,
                    project=self._project,
                    bundle_id=bundle_id,
                )
            except Exception:
                _log.warning("[data_bus] Failed to load props for bundle=%s; using empty props", bundle_id, exc_info=True)
                props = {}
            if not is_bundle_enabled(props):
                _log.info("[data_bus] Bundle disabled via enabled.bundle: bundle=%s", bundle_id)
                continue
            handler_specs = {handler.subject: handler for handler in manifest.data_bus_handlers}
            signature = "|".join(
                f"{item.subject}:{item.method_name}:{item.partition_by}:{item.ordering}:{item.idempotency}"
                for item in sorted(handler_specs.values(), key=lambda spec_item: spec_item.subject)
            )
            bundle_config = _make_headless_config(
                tenant=self._tenant,
                project=self._project,
                bundle_id=bundle_id,
                bundle_spec=spec,
                redis=self._redis,
                props=props,
            )
            desired[_DataBusWorkerKey(bundle_id=bundle_id)] = (
                signature,
                spec,
                bundle_config,
                handler_specs,
            )

        for key in list(self._workers.keys()):
            task, old_signature, worker = self._workers[key]
            if key not in desired:
                _log.info("[data_bus] Cancelling removed worker: bundle=%s", key.bundle_id)
                worker.stop()
                task.cancel()
                del self._workers[key]
            elif desired[key][0] != old_signature:
                _log.info("[data_bus] Handler signature changed; restarting worker: bundle=%s", key.bundle_id)
                worker.stop()
                task.cancel()
                del self._workers[key]

        for key, (signature, spec, bundle_config, handler_specs) in desired.items():
            if key in self._workers:
                continue
            worker = DataBusBundleWorker(
                redis=self._worker_redis,
                redis_url=self._redis_url,
                tenant=self._tenant,
                project=self._project,
                bundle_id=key.bundle_id,
                bundle_spec=spec,
                bundle_config=bundle_config,
                handler_specs=handler_specs,
                instance_id=self._instance_id,
            )
            task = asyncio.create_task(worker.run(), name=f"data-bus:{key.bundle_id}")
            self._workers[key] = (task, signature, worker)
            _log.info("[data_bus] Worker scheduled: bundle=%s subjects=%s", key.bundle_id, sorted(handler_specs.keys()))

    async def shutdown(self) -> None:
        _log.info("[data_bus] Shutting down %d worker(s)", len(self._workers))
        for _key, (task, _signature, worker) in list(self._workers.items()):
            worker.stop()
            task.cancel()
        if self._workers:
            await asyncio.gather(*[task for task, _signature, _worker in self._workers.values()], return_exceptions=True)
        self._workers.clear()
        if self._owns_worker_redis:
            try:
                await self._worker_redis.close()
            except Exception:
                _log.debug("[data_bus] Failed to close dedicated Redis client", exc_info=True)
        _log.info("[data_bus] Shutdown complete")
