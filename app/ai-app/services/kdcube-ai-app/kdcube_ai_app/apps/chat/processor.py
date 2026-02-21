# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter


# chat/processor.py
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import traceback
from typing import Optional, Dict, Any, Iterable

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
from kdcube_ai_app.infra.availability.health_and_heartbeat import MultiprocessDistributedMiddleware, logger
from kdcube_ai_app.storage.storage import create_storage_backend
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload, ServiceCtx, ConversationCtx
from kdcube_ai_app.apps.chat.emitters import ChatRelayCommunicator, ChatCommunicator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class EnhancedChatRequestProcessor:
    """
    Queue worker that:
      - Pops tasks fairly from multiple queues
      - Acquires + renews a per-task Redis lock
      - Emits chat_* events via ChatCommunicator (async)
      - Enforces per-task timeout
      - Handles graceful shutdown
    """

    QUEUE_ORDER: Iterable[str] = ("privileged", "registered", "anonymous", "paid")

    def __init__(
            self,
            middleware: MultiprocessDistributedMiddleware,
            chat_handler,
            *,
            conversation_ctx: ContextRAGClient,
            process_id: Optional[int] = None,
            relay: Optional[ChatRelayCommunicator] = None,   # unified relay (pub/sub)
            max_concurrent: Optional[int] = None,
            task_timeout_sec: Optional[int] = None,
            lock_ttl_sec: int = 300,
            lock_renew_sec: int = 60,
    ):
        self.middleware = middleware
        self.chat_handler = chat_handler
        self.process_id = process_id or os.getpid()
        self.max_concurrent = int(max_concurrent or 5)
        self.task_timeout_sec = int(os.getenv("CHAT_TASK_TIMEOUT_SEC", str(task_timeout_sec or 600)))
        self.lock_ttl_sec = lock_ttl_sec
        self.lock_renew_sec = lock_renew_sec
        self.conversation_ctx = conversation_ctx

        self._relay = relay or ChatRelayCommunicator()  # transport
        self._processor_task: Optional[asyncio.Task] = None
        self._config_task: Optional[asyncio.Task] = None
        self._active_tasks: set[asyncio.Task] = set()
        self._current_load = 0
        self._stop_event = asyncio.Event()
        self._queue_idx = 0

    # ---------------- Public API ----------------

    async def start_processing(self):
        if self._processor_task and not self._processor_task.done():
            return
        self._processor_task = asyncio.create_task(self._processing_loop(), name="chat-processing-loop")
        if not self._config_task:
            self._config_task = asyncio.create_task(self._config_listener_loop(), name="config-bundles-listener")

    async def stop_processing(self):
        self._stop_event.set()
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        if self._config_task:
            self._config_task.cancel()
            try:
                await self._config_task
            except asyncio.CancelledError:
                pass
        if self._active_tasks:
            await asyncio.gather(*list(self._active_tasks), return_exceptions=True)

    def get_current_load(self) -> int:
        return self._current_load

    # ---------------- Core loop ----------------

    async def _processing_loop(self):
        while not self._stop_event.is_set():
            try:
                if self._current_load >= self.max_concurrent:
                    await asyncio.sleep(0.05)
                    continue

                task_data = await self._pop_any_queue_fair()
                if not task_data:
                    await asyncio.sleep(0.05)
                    continue

                task = asyncio.create_task(
                    self._process_task(task_data),
                    name=f"chat-task:{task_data.get('task_id') or task_data.get('meta',{}).get('task_id')}",
                )
                self._active_tasks.add(task)
                task.add_done_callback(lambda t: self._active_tasks.discard(t))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Processing loop error: {e}")
                await asyncio.sleep(0.5)

    async def _pop_any_queue_fair(self) -> Optional[Dict[str, Any]]:
        for _ in range(len(self.QUEUE_ORDER)):
            user_type = self.QUEUE_ORDER[self._queue_idx]
            self._queue_idx = (self._queue_idx + 1) % len(self.QUEUE_ORDER)

            if self._current_load >= self.max_concurrent:
                return None

            queue_key = f"{self.middleware.QUEUE_PREFIX}:{user_type}"
            raw = await self.middleware.redis.brpop(queue_key, timeout=0.1)
            if not raw:
                continue

            try:
                task_dict = json.loads(raw[1])
            except Exception:
                logger.error("Invalid task payload (not JSON); dropping")
                continue

            logical_id = task_dict.get("meta", {}).get("task_id") or task_dict.get("task_id")
            if not logical_id:
                logger.error("Task missing task_id; dropping")
                continue

            lock_key = f"{self.middleware.LOCK_PREFIX}:{logical_id}"
            acquired = await self.middleware.redis.set(
                lock_key,
                f"{self.middleware.instance_id}:{self.process_id}",
                nx=True,
                ex=self.lock_ttl_sec,
            )
            if acquired:
                self._current_load += 1
                created_at = (task_dict.get("meta") or {}).get("created_at")
                queue_wait_ms = None
                if created_at:
                    try:
                        queue_wait_ms = int((time.time() - float(created_at)) * 1000)
                    except Exception:
                        queue_wait_ms = None
                logger.info(
                    f"Process {self.process_id} acquired task {logical_id} ({user_type})"
                    + (f" queue_wait_ms={queue_wait_ms}" if queue_wait_ms is not None else "")
                )
                task_dict["_lock_key"] = lock_key
                task_dict["_queue_key"] = queue_key
                return task_dict

            await self.middleware.redis.lpush(queue_key, json.dumps(task_dict, ensure_ascii=False))
        return None

    # ---------------- Config loop ----------------
    async def _config_listener_loop(self):
        import kdcube_ai_app.infra.namespaces as namespaces
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        from kdcube_ai_app.infra.plugin.bundle_registry import (
            set_registry_async, serialize_to_env, get_all, get_default_id
        )
        from kdcube_ai_app.infra.plugin.agentic_loader import clear_agentic_caches
        from kdcube_ai_app.infra.plugin.bundle_store import (
            load_registry as store_load,
            save_registry as store_save,
            publish_update as store_publish,
            apply_update,
            BundlesRegistry
        )

        try:
            settings = get_settings()
            tenant = settings.TENANT
            project = settings.PROJECT
            update_channel = namespaces.CONFIG.BUNDLES.UPDATE_CHANNEL.format(tenant=tenant, project=project)
            cleanup_channel = namespaces.CONFIG.BUNDLES.CLEANUP_CHANNEL.format(tenant=tenant, project=project)
            pubsub = self.middleware.redis.pubsub()
            await pubsub.subscribe(
                update_channel,
                cleanup_channel,
            )
            logger.info(
                "Subscribed to bundles channels: "
                f"{update_channel}, {cleanup_channel}"
            )

            async for message in pubsub.listen():
                if not message or message.get("type") != "message":
                    continue

                raw = message.get("data")
                try:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    evt = json.loads(raw)
                except Exception:
                    logger.warning("Invalid bundles broadcast; ignoring")
                    continue

                if "registry" in evt:
                    try:
                        reg = BundlesRegistry(**(evt.get("registry") or {}))
                    except Exception:
                        logger.warning("Invalid registry payload; ignoring")
                        continue
                    await set_registry_async(
                        {bid: be.model_dump() for bid, be in reg.bundles.items()},
                        reg.default_bundle_id
                    )
                    serialize_to_env(get_all(), get_default_id())
                    try:
                        clear_agentic_caches()
                    except Exception:
                        pass

                    try:
                        await store_save(self.middleware.redis, reg)
                    except Exception:
                        logger.debug("Could not save snapshot to Redis; continuing")

                    logger.info(f"Applied bundles SNAPSHOT; now have {len(get_all())} bundles")
                    continue

                if evt.get("type") == "bundles.update":
                    op = evt.get("op", "merge")
                    bundles_patch = evt.get("bundles") or {}
                    default_id = evt.get("default_bundle_id")

                    try:
                        current = await store_load(self.middleware.redis)
                    except Exception as e:
                        logger.error(f"Failed to load registry from Redis: {e}")
                        current = BundlesRegistry()

                    try:
                        reg = apply_update(current, op, bundles_patch, default_id)
                    except Exception as e:
                        logger.error(f"Ignoring invalid bundles.update: {e}")
                        continue

                    try:
                        await store_save(self.middleware.redis, reg)
                        await store_publish(self.middleware.redis, reg, op=op, actor=evt.get("updated_by") or None)
                    except Exception as e:
                        logger.error(f"Failed to persist/broadcast bundles: {e}")

                    await set_registry_async(
                        {bid: be.model_dump() for bid, be in reg.bundles.items()},
                        reg.default_bundle_id
                    )
                    new_env = serialize_to_env(get_all(), get_default_id())
                    try:
                        clear_agentic_caches()
                    except Exception:
                        pass

                    logger.info(f"Applied bundles COMMAND (op={op}); now have {len(get_all())} bundles. New env = {new_env}")
                    continue

                if evt.get("type") == "bundles.cleanup":
                    from kdcube_ai_app.infra.plugin.agentic_loader import evict_inactive_specs, AgenticBundleSpec
                    from kdcube_ai_app.infra.plugin.git_bundle import (
                        cleanup_old_git_bundles_async,
                        resolve_bundles_root,
                        bundle_dir_for_git,
                    )
                    from kdcube_ai_app.infra.plugin.bundle_refs import get_active_paths

                    active_specs = []
                    for _bid, entry in (get_all() or {}).items():
                        try:
                            active_specs.append(AgenticBundleSpec(
                                path=entry.get("path"),
                                module=entry.get("module"),
                                singleton=bool(entry.get("singleton")),
                            ))
                        except Exception:
                            continue
                    drop_sys_modules = bool(evt.get("drop_sys_modules", True))
                    result = evict_inactive_specs(
                        active_specs=active_specs,
                        drop_sys_modules=drop_sys_modules,
                    )
                    # Git bundle cleanup (skip active refs from Redis)
                    try:
                        active_paths = await get_active_paths(
                            self.middleware.redis,
                            tenant=tenant,
                            project=project,
                        )
                        bundles = get_all() or {}
                        for _bid, entry in bundles.items():
                            git_url = entry.get("git_url") or entry.get("git_repo")
                            if not git_url:
                                continue
                            base_dir = bundle_dir_for_git(_bid, entry.get("git_ref"))
                            await cleanup_old_git_bundles_async(
                                bundle_id=base_dir,
                                bundles_root=resolve_bundles_root(),
                                active_paths=active_paths,
                            )
                    except Exception as e:
                        logger.warning(f"Git bundle cleanup failed: {e}")
                    logger.info(
                        "Applied bundles cleanup. "
                        f"evicted_modules={result.get('evicted_modules')}; "
                        f"evicted_singletons={result.get('evicted_singletons')}; "
                        f"sys_modules_deleted={result.get('sys_modules_deleted')}"
                    )
                    continue

                logger.debug("Ignoring unrelated pub/sub message on bundles channel")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Config listener error: {e}")
            await asyncio.sleep(1.0)

    # ---------------- Per-task execution ----------------

    @asynccontextmanager
    async def _lock_renewer(self, lock_key: str):
        async def renewer():
            try:
                while not self._stop_event.is_set():
                    await asyncio.sleep(self.lock_renew_sec)
                    ttl = await self.middleware.redis.ttl(lock_key)
                    if ttl is None or ttl < 0:
                        break
                    await self.middleware.redis.expire(lock_key, self.lock_ttl_sec)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(renewer(), name=f"lock-renewer:{lock_key}")
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _process_task(self, task_data: Dict[str, Any]):
        lock_key = task_data.get("_lock_key")

        # 1) Normalize payload
        try:
            payload = ChatTaskPayload.model_validate(task_data)
        except Exception as e:
            logger.error(f"Cannot normalize legacy task: {e}")
            logger.error(traceback.format_exc())
            try:
                if lock_key:
                    await self.middleware.redis.delete(lock_key)
            finally:
                # Ensure load is released even for invalid payloads
                self._current_load = max(0, self._current_load - 1)
                return

        assert payload is not None

        # 2) Build contexts
        session_id = payload.routing.session_id
        socket_id = payload.routing.socket_id
        task_id = payload.meta.task_id
        request_id = (payload.accounting.envelope or {}).get("request_id", task_id)
        svc = ServiceCtx(
            request_id=request_id,
            tenant=payload.actor.tenant_id,
            project=payload.actor.project_id,
            user=payload.user.user_id or payload.user.fingerprint,
            user_obj=payload.user
        )
        conv = ConversationCtx(
            session_id=session_id,
            conversation_id=(payload.routing.conversation_id or session_id),
            turn_id=payload.routing.turn_id,
        )
        # 3) ChatCommunicator (async) over the relay
        comm = ChatCommunicator(
            emitter=self._relay,
            service=svc.model_dump(),
            conversation=conv.model_dump(),
            room=session_id,
            target_sid=socket_id,
            tenant=payload.actor.tenant_id,
            project=payload.actor.project_id,
            user_id=payload.user.user_id,
            user_type=payload.user.user_type,
        )
        # 4) accounting + storage
        from kdcube_ai_app.infra.accounting.envelope import AccountingEnvelope, bind_accounting
        from kdcube_ai_app.infra.accounting import with_accounting

        envelope = AccountingEnvelope.from_dict(payload.accounting.envelope)
        _settings = get_settings()
        storage_backend = create_storage_backend(_settings.STORAGE_PATH, **{})

        # 5) Announce start (async)
        created_at = None
        try:
            created_at = float(getattr(payload.meta, "created_at", None))
        except Exception:
            created_at = None
        if created_at:
            queue_wait_ms = int((time.time() - created_at) * 1000)
            logger.info(
                f"Starting task {task_id} queue_wait_ms={queue_wait_ms} current_load={self._current_load}"
            )
        msg = (
            (payload.request.message[:100] + "...")
            if payload.request.message and len(payload.request.message) > 100
            else (payload.request.message or f"operation={payload.request.operation}")
        )
        await comm.start(message=msg, queue_stats={})
        await comm.step(
            step="workflow_start",
            status="started",
            title="Workflow Start",
            data={ "default_model": (payload.config.values or {}).get("selected_model"), "task_id": task_id},
        )

        # 6) Execute with lock renew + timeout
        success = False
        try:
            async with bind_accounting(envelope, storage_backend, enabled=True):
                async with with_accounting("chat.orchestrator",
                                           app_bundle_id=payload.routing.bundle_id,
                                           conversation_id=payload.routing.conversation_id,
                                           turn_id=payload.routing.turn_id,
                                           metadata={
                    "task_id": task_id,
                    "conversation_id": payload.routing.conversation_id,
                    "turn_id": payload.routing.turn_id,
                }):
                    async with self._lock_renewer(lock_key=lock_key):
                        result = await asyncio.wait_for(
                            self.chat_handler(
                                payload,

                            ),
                            timeout=self.task_timeout_sec,
                        )

            result = result or {}
            success = True
            await comm.complete(data=result)

        except asyncio.TimeoutError:
            tb = "Task timed out"
            await comm.error(message=tb, data={"task_id": task_id})
            success = False
        except Exception:
            tb = traceback.format_exc()
            await comm.error(message=tb, data={"task_id": task_id})
            success = False
        finally:
            try:
                if lock_key:
                    await self.middleware.redis.delete(lock_key)
            finally:
                self._current_load = max(0, self._current_load - 1)
                try:
                    res = await self.conversation_ctx.set_conversation_state(
                        tenant=payload.actor.tenant_id, project=payload.actor.project_id, user_id=payload.user.user_id, conversation_id=payload.routing.conversation_id,
                        new_state=("idle" if success else "error"),
                        by_instance=f"{self.middleware.instance_id}:{self.process_id}",
                        request_id=request_id,
                        last_turn_id=payload.routing.turn_id,
                        require_not_in_progress=False,
                        user_type=payload.user.user_type,
                        bundle_id=payload.routing.bundle_id,
                    )
                    # broadcast to session
                    await self._relay.emit_conv_status(svc, conv,
                                                     routing=payload.routing,
                                                     state=("idle" if success else "error"),
                                                     updated_at=res["updated_at"],
                                                     current_turn_id=res.get("current_turn_id"),
                                                     completion="success" if success else "error",
                                                     target_sid=None)
                except Exception as ex:
                    logger.error(traceback.format_exc())
