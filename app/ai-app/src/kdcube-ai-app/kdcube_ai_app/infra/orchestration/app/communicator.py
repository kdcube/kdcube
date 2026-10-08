# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/infra/orchestration/app/communicator.py
import asyncio
import time
import json
import logging
import os
import contextlib
from typing import Callable, Iterable, Optional, Union, AsyncIterator, List, Any

import redis.asyncio as aioredis

from dotenv import load_dotenv, find_dotenv
from kdcube_ai_app.apps.chat.sdk.config import get_settings

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.redis.client import get_async_redis_client

# Load environment
# load_dotenv(find_dotenv())

# Logging
logger = logging.getLogger("ServiceCommunicator")

# Queue prefix to match web server expectations
KDCUBE_ORCHESTRATOR_QUEUES_PREFIX = "kdcube_orch_"

class ServiceCommunicator:
    """
    Unified Redis Pub/Sub helper.
    - Sync publish API (used by actors/workers).
    - Async subscribe/listen API (used by Socket.IO service).

    Message schema (JSON):
        {
          "target_sid": str,
          "event": str,
          "data": dict,
          "timestamp": float
        }

    Channels are auto-prefixed with "<ORCHESTRATOR_IDENTITY>." to keep producers/consumers aligned.
    """

    # ---------- construction ----------

    def __init__(
            self,
            redis_url: str = None,
            orchestrator_identity: str = None,
    ):
        settings = get_settings()
        if not redis_url:
            redis_url = settings.REDIS_URL

        self.redis_url = redis_url
        if not orchestrator_identity:
            ORCHESTRATOR_TYPE = os.environ.get("CB_ORCHESTRATOR_TYPE", "chatbot")
            DEFAULT_ORCHESTRATOR_IDENTITY = f"kdcube.relay.{ORCHESTRATOR_TYPE}"
            orchestrator_identity = (
                get_settings().PLATFORM.SERVICE.CB_RELAY_IDENTITY
                or DEFAULT_ORCHESTRATOR_IDENTITY
            )

        self.orchestrator_identity = orchestrator_identity

        # async client for subscribing
        self._aioredis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        self._listen_task: Optional["asyncio.Task"] = None
        self._last_message_ts = 0.0

        self._subscribed_channels: List[str] = []
        self._subscribed_patterns: List[str] = []
        # Logical subscriptions survive a transport failure. Serialize their
        # changes with transport replacement so recovery cannot restore stale refs.
        self._subscription_lock = asyncio.Lock()

        # Support multiple consumer callbacks
        self._listeners: List[Callable[[dict], Any]] = []

    # ---------- resiliency helpers ----------
    def _has_active_subscriptions(self) -> bool:
        return bool(self._subscribed_channels or self._subscribed_patterns)

    def listener_alive(self) -> bool:
        return self._listen_task is not None and not self._listen_task.done()

    def debug_state(self) -> dict:
        t = self._listen_task
        return {
            "self_id": id(self),
            "pubsub_id": id(self._pubsub) if self._pubsub else None,
            "task_id": id(t) if t else None,
            "listener_alive": self.listener_alive(),
            "subscribed_channels": list(self._subscribed_channels),
            "subscribed_patterns": list(self._subscribed_patterns),
            "last_message_ts": self._last_message_ts,
        }
    # ---------- channel helpers ----------

    def _fmt_channel(self, channel: str) -> str:
        """Apply identity prefix."""
        if channel.startswith(self.orchestrator_identity + "."):
            return channel
        return f"{self.orchestrator_identity}.{channel}"

    def add_listener(self, callback: Callable[[dict], Any]):
        """Register a callback to receive each pubsub payload."""
        if callback and callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[dict], Any]):
        """Unregister a callback."""
        if not callback:
            return
        self._listeners = [cb for cb in self._listeners if cb is not callback]

    # ---------- publisher API (sync) ----------

    async def _pub_async(
            self,
            event: str,
            target_sid: str | None,
            data: dict,
            channel: str = "kb.process_resource_out",
            session_id: str | None = None,
    ):
        message = {
            "target_sid": target_sid,
            "session_id": session_id,
            "event": event,
            "data": data,
            "timestamp": time.time(),
        }

        # Shard chat events by session
        logical_channel = channel
        full_channel = self._fmt_channel(logical_channel)
        await self._ensure_async()

        payload = json.dumps(message, ensure_ascii=False)
        logger.debug(
            "Publishing event '%s' to '%s' (sid=%s, session=%s): %s",
            event,
            full_channel,
            target_sid,
            session_id,
            data,
        )
        try:
            await self._aioredis.publish(full_channel, payload)
        except Exception as e:
            logger.error(
                "[ServiceCommunicator] async publish failed to %s: %s",
                full_channel,
                e,
            )

    async def pub(
            self,
            event: str,
            target_sid: str | None,
            data: dict,
            channel: str = "kb.process_resource_out",
            session_id: str | None = None,
    ):
        await self._pub_async(event, target_sid, data, channel=channel, session_id=session_id)

    # ---------- subscriber API (async) ----------

    async def _ensure_async(self):
        if self._aioredis is None:
            self._aioredis = get_async_redis_client(self.redis_url)
            logger.info("[ServiceCommunicator] Lazy Redis async client initialized")

    async def _discard_pubsub_locked(self):
        pubsub, self._pubsub = self._pubsub, None
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.close()

    async def _create_pubsub_locked(self):
        """Publish a replacement only after restoring every logical subscription."""
        await self._ensure_async()
        pubsub = self._aioredis.pubsub()
        try:
            if self._subscribed_channels:
                await pubsub.subscribe(*self._subscribed_channels)
            if self._subscribed_patterns:
                await pubsub.psubscribe(*self._subscribed_patterns)
        except BaseException:
            # Cancellation also releases a candidate's pooled connection.
            with contextlib.suppress(Exception):
                await pubsub.close()
            raise
        self._pubsub = pubsub

    async def _subscribe_locked(self, channels: List[str], *, pattern: bool):
        if self._pubsub is None:
            # Even a duplicate add must restore the old channels on a new transport.
            await self._create_pubsub_locked()
        elif channels:
            try:
                if pattern:
                    await self._pubsub.psubscribe(*channels)
                else:
                    await self._pubsub.subscribe(*channels)
            except BaseException:
                # A failed command leaves physical subscription state uncertain.
                # Keep logical intent, discard this connection and retry the full set.
                await self._discard_pubsub_locked()
                raise

    async def subscribe(self, channels: Union[str, Iterable[str]], *, pattern: bool = False):
        """
        Subscribe (or psubscribe) to one or more channels.
        Call once before listen()/start_listener().
        """
        if isinstance(channels, str):
            channels = [channels]
        formatted = list(dict.fromkeys(self._fmt_channel(ch) for ch in channels))
        async with self._subscription_lock:
            if pattern:
                self._subscribed_patterns = formatted
            else:
                self._subscribed_channels = formatted
            await self._subscribe_locked(formatted, pattern=pattern)

    async def subscribe_add(self, channels: Union[str, Iterable[str]], *, pattern: bool = False):
        if isinstance(channels, str):
            channels = [channels]
        formatted = list(dict.fromkeys(self._fmt_channel(ch) for ch in channels))
        async with self._subscription_lock:
            target_list = self._subscribed_patterns if pattern else self._subscribed_channels
            new_channels = [ch for ch in formatted if ch not in target_list]
            target_list.extend(new_channels)
            await self._subscribe_locked(new_channels, pattern=pattern)
            logger.info(
                "[ServiceCommunicator] subscriptions updated self_id=%s pubsub_id=%s channels=%s patterns=%s",
                id(self), id(self._pubsub), len(self._subscribed_channels), len(self._subscribed_patterns)
            )


    async def unsubscribe_some(self, channels: Union[str, Iterable[str]]):
        if isinstance(channels, str):
            channels = [channels]
        formatted = [self._fmt_channel(ch) for ch in channels]
        async with self._subscription_lock:
            to_remove = [ch for ch in formatted if ch in self._subscribed_channels]
            patterns = [ch for ch in formatted if ch in self._subscribed_patterns]
            # Release logical intent even while Redis is unavailable.
            self._subscribed_channels = [ch for ch in self._subscribed_channels if ch not in to_remove]
            self._subscribed_patterns = [ch for ch in self._subscribed_patterns if ch not in patterns]
            if self._pubsub is not None:
                try:
                    if to_remove:
                        await self._pubsub.unsubscribe(*to_remove)
                    if patterns:
                        await self._pubsub.punsubscribe(*patterns)
                except asyncio.CancelledError:
                    await self._discard_pubsub_locked()
                    raise
                except Exception as unsubscribe_err:
                    # Logical release is complete. Discard uncertain physical
                    # state, but do not prevent the relay's final idle cleanup.
                    await self._discard_pubsub_locked()
                    logger.debug("[ServiceCommunicator] release transport error self_id=%s error_type=%s",
                                 id(self), type(unsubscribe_err).__name__)


    async def listen(self) -> AsyncIterator[dict]:
        """
        Async iterator yielding decoded payload dicts for 'message'/'pmessage' only.
        Use after subscribe().
        """
        if not self._pubsub:
            raise RuntimeError("Call subscribe() before listen().")

        log = logger.info if self._has_active_subscriptions() else logger.debug
        log(
            "[ServiceCommunicator] listen() started on %r (id=%s), channels=%s patterns=%s",
            self, id(self), self._subscribed_channels, self._subscribed_patterns
        )
        async for msg in self._pubsub.listen():
            mtype = msg.get("type")
            if mtype not in ("message", "pmessage"):
                # ignore 'subscribe', 'psubscribe', etc.
                continue
            data = msg.get("data")
            if isinstance(data, (bytes, bytearray)):
                try:
                    payload = json.loads(data)
                except Exception as e:
                    logger.error(f"[ServiceCommunicator] JSON decode error: {e} ({data[:200]!r})")
                    continue
            elif isinstance(data, dict):
                payload = data
            else:
                # unexpected
                continue
            yield payload


    async def start_listener(self, on_message: Callable[[dict], "asyncio.Future | None | Any"]):
        """
        Start a background task that invokes on_message(payload) for every message.
        Requires subscribe() called beforehand.
        """
        import asyncio

        if on_message is not None:
            self.add_listener(on_message)

        if not self._pubsub:
            raise RuntimeError("Call subscribe() before start_listener().")

        async def _loop():
            backoff = 0.5
            logger.info(
                "[ServiceCommunicator] listener _loop starting self_id=%s pubsub_id=%s",
                id(self), id(self._pubsub)
            )
            while True:
                try:
                    async for payload in self.listen():
                        # fan-out payload to all listeners
                        backoff = 0.5
                        self._last_message_ts = time.time()
                        listeners_snapshot = list(self._listeners)
                        for cb in listeners_snapshot:
                            try:
                                res = cb(payload)
                                if asyncio.iscoroutine(res):
                                    await res
                            except Exception as cb_err:
                                logger.error("[ServiceCommunicator] on_message error: %s", cb_err)

                    if not self._has_active_subscriptions():
                        logger.debug(
                            "[ServiceCommunicator] listen() ended with no active subscriptions "
                            "self_id=%s pubsub_id=%s",
                            id(self), id(self._pubsub)
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 10.0)
                        continue

                    logger.warning(
                        "[ServiceCommunicator] listen() ended WITHOUT exception "
                        "self_id=%s pubsub_id=%s channels=%s patterns=%s",
                        id(self), id(self._pubsub), self._subscribed_channels, self._subscribed_patterns
                    )
                    raise RuntimeError("pubsub listen ended without exception")
                except asyncio.CancelledError:
                    logger.info("[ServiceCommunicator] listener cancelled self_id=%s", id(self))
                    raise
                except Exception as e:
                    log = logger.warning if self._has_active_subscriptions() else logger.debug
                    log("[ServiceCommunicator] listener error self_id=%s error_type=%s", id(self), type(e).__name__)
                    # Recovery failure is still a transport outage, not a reason
                    # to terminate the sole listener for connected sessions.
                    try:
                        await self._reconnect_pubsub()
                    except asyncio.CancelledError:
                        raise
                    except Exception as reconnect_err:
                        log("[ServiceCommunicator] recovery failed self_id=%s error_type=%s",
                            id(self), type(reconnect_err).__name__)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10.0)

        if self._listen_task and not self._listen_task.done():
            logger.info(
                "[ServiceCommunicator] start_listener called but task already running (id=%s)",
                id(self._listen_task),
            )
            return  # already running

        self._listen_task = asyncio.create_task(_loop(), name="service-communicator-listener")
        logger.info(
            "[ServiceCommunicator] Started listener task %r on channels: %s",
            self._listen_task, self._subscribed_channels + self._subscribed_patterns
        )

    async def stop_listener(self):
        """Cancel listener task and close pubsub + connection."""
        import asyncio
        task, self._listen_task = self._listen_task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        # Cancel before taking the lock: recovery may hold it across a Redis call.
        async with self._subscription_lock:
            await self._discard_pubsub_locked()
            self._subscribed_channels = []
            self._subscribed_patterns = []
            if self._aioredis:
                with contextlib.suppress(Exception):
                    if not getattr(self._aioredis, "_kdcube_shared", False):
                        await self._aioredis.close()
                self._aioredis = None
        logger.info("Stopped listener and closed async Redis.")

    async def _reconnect_pubsub(self):
        """Recreate pubsub connection and resubscribe after Redis restart."""
        async with self._subscription_lock:
            await self._discard_pubsub_locked()
            if self._has_active_subscriptions():
                await self._create_pubsub_locked()
            logger.info(
                "[ServiceCommunicator] Reconnected pubsub self_id=%s pubsub_id=%s channels=%s patterns=%s",
                id(self), id(self._pubsub) if self._pubsub else None,
                len(self._subscribed_channels), len(self._subscribed_patterns)
            )


    # ==============================================================================
    #                           UTILITY FUNCTIONS
    # ==============================================================================

    async def get_queue_stats(self):
        """Get queue statistics (async)."""
        await self._ensure_async()
        stats = {}
        queues = [
            f"{KDCUBE_ORCHESTRATOR_QUEUES_PREFIX}low_priority",
            f"{KDCUBE_ORCHESTRATOR_QUEUES_PREFIX}high_priority",
            f"{KDCUBE_ORCHESTRATOR_QUEUES_PREFIX}batch",
            "health_check"
        ]
        for queue in queues:
            queue_key = f"dramatiq:default.{queue}"
            try:
                length = await self._aioredis.llen(queue_key)
            except Exception:
                length = 0
            stats[queue] = length
        return stats

    def get_task_result(self,
                        message_id: str):
        """Get result of a completed task"""
        from dramatiq.results.backends import RedisBackend

        try:
            result_backend = RedisBackend(url=self.redis_url)
            return result_backend.get_result(message_id, block=False)
        except Exception as e:
            logger.error(f"Failed to get task result for {message_id}: {e}")
            return None
