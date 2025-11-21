# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/accounting/turn_cache.py

import json
from typing import Any, List, Dict, Optional

import redis.asyncio as aioredis

class TurnEventCache:
    """
    Redis-backed per-turn accounting event cache.

    Key format:
      {key_prefix}:{tenant}:{project}:{conversation_id}:{turn_id}

    Value:
      Redis LIST of JSON-serialized AccountingEvent.to_dict().

    TTL semantics
    -------------
    - ttl_seconds > 0:
        On every append we refresh the TTL on the key.
        This gives a **sliding TTL**, e.g. "keep this turn alive for 20 minutes
        after the last event was written".
    - ttl_seconds == 0 or None:
        No TTL is applied; caller is responsible for explicit cleanup.
    """

    def __init__(
            self,
            redis_url: str,
            *,
            key_prefix: str = "acct:turn",
            ttl_seconds: int = 3600,
    ):
        self.redis = aioredis.Redis.from_url(redis_url)
        self.key_prefix = key_prefix
        self.ttl_seconds = ttl_seconds

    # ---- key helpers ----

    def _key_for_event(self, event: "AccountingEvent") -> str:
        ctx = event.context or {}

        tenant = event.tenant_id or ctx.get("tenant_id") or "unknown"
        project = event.project_id or ctx.get("project_id") or "unknown"
        conversation_id = ctx.get("conversation_id") or "no-conv"
        turn_id = ctx.get("turn_id") or "no-turn"

        return f"{self.key_prefix}:{tenant}:{project}:{conversation_id}:{turn_id}"

    def _key(
            self,
            *,
            tenant: str,
            project: str,
            conversation_id: str,
            turn_id: str,
    ) -> str:
        return (
            f"{self.key_prefix}:"
            f"{tenant or 'unknown'}:"
            f"{project or 'unknown'}:"
            f"{conversation_id or 'no-conv'}:"
            f"{turn_id or 'no-turn'}"
        )

    # ---- operations ----

    async def append_event(self, event: "AccountingEvent") -> None:
        """
        Append one event to the per-turn list and refresh TTL.

        This implements a sliding TTL:
          - each new event pushes out expiry by `ttl_seconds`.
        """
        key = self._key_for_event(event)
        payload = json.dumps(event.to_dict(), ensure_ascii=False)

        # Use a pipeline so rpush + expire is atomic-ish from the client POV.
        pipe = self.redis.pipeline()
        pipe.rpush(key, payload)
        if self.ttl_seconds:
            pipe.expire(key, self.ttl_seconds)
        await pipe.execute()

    async def get_events_for_turn(
            self,
            *,
            tenant: str,
            project: str,
            conversation_id: str,
            turn_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Return all cached events for a specific turn.

        If the key has expired or never existed, returns [].
        """
        key = self._key(
            tenant=tenant,
            project=project,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        raw = await self.redis.lrange(key, 0, -1)
        if not raw:
            return []
        return [json.loads(item) for item in raw]

    async def clear_turn(
            self,
            *,
            tenant: str,
            project: str,
            conversation_id: str,
            turn_id: str,
    ) -> None:
        """
        Explicitly delete the per-turn cache key.

        This is your "belt" and the TTL is your "suspenders":
        - normal flow: you call clear_turn() at the end of a turn
        - crash / bug: Redis still auto-expires after ttl_seconds
        """
        key = self._key(
            tenant=tenant,
            project=project,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        await self.redis.delete(key)