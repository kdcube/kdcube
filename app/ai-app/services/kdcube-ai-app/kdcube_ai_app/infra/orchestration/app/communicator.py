# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# kdcube_ai_app/infra/orchestration/app/communicator.py
import time
import json
import logging
import os
import contextlib
from typing import Callable, Iterable, Optional, Union, AsyncIterator, List, Any

import redis
import redis.asyncio as aioredis

from dotenv import load_dotenv, find_dotenv

from kdcube_ai_app.apps.chat.sdk.config import get_settings

# Load environment
load_dotenv(find_dotenv())

# Logging
logger = logging.getLogger("ServiceCommunicator")


# Configuration
# REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
# REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
# REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
# REDIS_URL = os.environ.get("REDIS_URL", f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/0")

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
            ORCHESTRATOR_IDENTITY = os.environ.get("CB_RELAY_IDENTITY", DEFAULT_ORCHESTRATOR_IDENTITY)

            orchestrator_identity = ORCHESTRATOR_IDENTITY

        self.orchestrator_identity = orchestrator_identity

        # sync client for publishing
        self.redis = redis.Redis.from_url(self.redis_url)

        # async client for subscribing
        self._aioredis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        self._listen_task: Optional["asyncio.Task"] = None
        self._subscribed_channels: List[str] = []

        # Support multiple consumer callbacks
        self._listeners: List[Callable[[dict], Any]] = []

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
        # if session_id and channel == "chat.events":
        #     logical_channel = f"{channel}.{session_id}"

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
            self._aioredis = aioredis.Redis.from_url(self.redis_url)

    async def subscribe(self, channels: Union[str, Iterable[str]], *, pattern: bool = False):
        """
        Subscribe (or psubscribe) to one or more channels.
        Call once before listen()/start_listener().
        """
        await self._ensure_async()
        if self._pubsub is None:
            self._pubsub = self._aioredis.pubsub()

        if isinstance(channels, str):
            channels = [channels]

        formatted = [self._fmt_channel(ch) for ch in channels]
        self._subscribed_channels = formatted

        if pattern:
            await self._pubsub.psubscribe(*formatted)
            logger.info(f"Pattern-subscribed to: {formatted}")
        else:
            await self._pubsub.subscribe(*formatted)
            logger.info(f"Subscribed to: {formatted}")

    async def subscribe_add(self, channels: Union[str, Iterable[str]], *, pattern: bool = False):
        await self._ensure_async()
        if self._pubsub is None:
            self._pubsub = self._aioredis.pubsub()

        if isinstance(channels, str):
            channels = [channels]

        formatted = [self._fmt_channel(ch) for ch in channels]

        # Only subscribe to new ones
        new_channels = [ch for ch in formatted if ch not in self._subscribed_channels]
        if not new_channels:
            return

        self._subscribed_channels.extend(new_channels)

        if pattern:
            await self._pubsub.psubscribe(*new_channels)
            logger.info(f"Pattern-subscribed to: {new_channels}")
        else:
            await self._pubsub.subscribe(*new_channels)
            logger.info(f"Subscribed to: {new_channels}")

    async def unsubscribe_some(self, channels: Union[str, Iterable[str]]):
        if not self._pubsub:
            return

        if isinstance(channels, str):
            channels = [channels]

        formatted = [self._fmt_channel(ch) for ch in channels]
        to_remove = [ch for ch in formatted if ch in self._subscribed_channels]
        if not to_remove:
            return

        # Remove from our tracking list
        self._subscribed_channels = [
            ch for ch in self._subscribed_channels if ch not in to_remove
        ]

        # Unsubscribe (works for both sub/psub)
        with contextlib.suppress(Exception):
            await self._pubsub.unsubscribe(*to_remove)
        with contextlib.suppress(Exception):
            await self._pubsub.punsubscribe(*to_remove)

    async def listen(self) -> AsyncIterator[dict]:
        """
        Async iterator yielding decoded payload dicts for 'message'/'pmessage' only.
        Use after subscribe().
        """
        if not self._pubsub:
            raise RuntimeError("Call subscribe() before listen().")

        logger.info(
            "[ServiceCommunicator] listen() started on %r (id=%s), subscribed=%s",
            self, id(self), self._subscribed_channels
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

        logger.info(
            "[ServiceCommunicator] start_listener on %r (id=%s) pubsub=%r",
            self, id(self), self._pubsub
        )

        async def _loop():
            logger.info(
                "[ServiceCommunicator] listener _loop starting on %r (id=%s)",
                self, id(self)
            )
            try:
                async for payload in self.listen():
                    # fan-out payload to all listeners
                    listeners_snapshot = list(self._listeners)
                    for cb in listeners_snapshot:
                        try:
                            res = cb(payload)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception as cb_err:
                            logger.error(f"[ServiceCommunicator] on_message error: {cb_err}")
            except asyncio.CancelledError:
                logger.info("[ServiceCommunicator] listener cancelled")
                raise
            except Exception as e:
                logger.error(f"[ServiceCommunicator] listener error: {e}")

        if self._listen_task and not self._listen_task.done():
            logger.info(
                "[ServiceCommunicator] start_listener called but task already running (id=%s)",
                id(self._listen_task),
            )
            return  # already running

        self._listen_task = asyncio.create_task(_loop(), name="service-communicator-listener")
        logger.info(
            "[ServiceCommunicator] Started listener task %r on channels: %s",
            self._listen_task, self._subscribed_channels
        )

    async def stop_listener(self):
        """Cancel listener task and close pubsub + connection."""
        import asyncio
        task, self._listen_task = self._listen_task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        if self._pubsub:
            with contextlib.suppress(Exception):
                if self._subscribed_channels:
                    # attempt to unsubscribe (works for both sub and psub)
                    await self._pubsub.unsubscribe(*self._subscribed_channels)
                    await self._pubsub.punsubscribe(*self._subscribed_channels)
            with contextlib.suppress(Exception):
                await self._pubsub.close()
            self._pubsub = None
            self._subscribed_channels = []

        if self._aioredis:
            with contextlib.suppress(Exception):
                await self._aioredis.close()
            self._aioredis = None
        logger.info("Stopped listener and closed async Redis.")


    # ==============================================================================
    #                           UTILITY FUNCTIONS
    # ==============================================================================

    def get_queue_stats(self):
        """Get queue statistics - matches what the orchestrator interface expects"""
        redis_client = redis.Redis.from_url(self.redis_url)
        stats = {}

        queues = [
            f"{KDCUBE_ORCHESTRATOR_QUEUES_PREFIX}low_priority",
            f"{KDCUBE_ORCHESTRATOR_QUEUES_PREFIX}high_priority",
            f"{KDCUBE_ORCHESTRATOR_QUEUES_PREFIX}batch",
            "health_check"
        ]

        for queue in queues:
            queue_key = f"dramatiq:default.{queue}"
            length = redis_client.llen(queue_key)
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