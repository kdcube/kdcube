# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/gateway/throttling.py
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional, Dict, List

from redis import asyncio as aioredis
import logging
from kdcube_ai_app.auth.sessions import UserSession, RequestContext
from kdcube_ai_app.infra.namespaces import REDIS

logger = logging.getLogger(__name__)

class ThrottlingReason(Enum):
    SESSION_RATE_LIMIT = "session_rate_limit"
    HOURLY_RATE_LIMIT = "hourly_rate_limit"
    BURST_RATE_LIMIT = "burst_rate_limit"
    SYSTEM_BACKPRESSURE = "system_backpressure"
    ANONYMOUS_BACKPRESSURE = "anonymous_backpressure"
    REGISTERED_BACKPRESSURE = "registered_backpressure"


@dataclass
class ThrottlingEvent:
    timestamp: float
    event_id: str
    reason: ThrottlingReason
    http_status: int  # 429 or 503
    session_id: str
    user_type: str
    fingerprint: str
    endpoint: str
    retry_after: int
    queue_stats: Optional[Dict] = None
    rate_limit_stats: Optional[Dict] = None


@dataclass
class ThrottlingStats:
    total_requests: int = 0
    total_throttled: int = 0
    throttled_by_reason: Dict[str, int] = None
    rate_limit_429: int = 0
    backpressure_503: int = 0
    hourly_stats: Dict[str, int] = None
    top_throttled_sessions: List[tuple] = None

    def __post_init__(self):
        if self.throttled_by_reason is None:
            self.throttled_by_reason = {}
        if self.hourly_stats is None:
            self.hourly_stats = {}
        if self.top_throttled_sessions is None:
            self.top_throttled_sessions = []


class ThrottlingMonitor:
    """Lightweight throttling monitor that integrates with your existing gateway"""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.redis = None
        self.events_key = REDIS.THROTTLING.EVENTS_KEY
        self.stats_key = REDIS.THROTTLING.STATS_KEY
        self.session_counters_key = REDIS.THROTTLING.SESSION_COUNTERS_KEY

        # In-memory tracking for performance
        self.recent_events = deque(maxlen=100)
        self.request_counter = 0

    async def init_redis(self):
        if not self.redis:
            self.redis = aioredis.from_url(self.redis_url)

    async def record_request(self, session: UserSession):
        """Record a successful request (for statistics)"""
        await self.init_redis()
        self.request_counter += 1

        # Increment total request counter
        await self.redis.incr(REDIS.THROTTLING.TOTAL_REQUESTS_KEY)

    async def record_throttling_event(self,
                                      reason: ThrottlingReason,
                                      session: UserSession,
                                      context: RequestContext,
                                      endpoint: str,
                                      retry_after: int,
                                      additional_data: Optional[Dict] = None):
        """Record a throttling event"""
        await self.init_redis()

        event = ThrottlingEvent(
            timestamp=time.time(),
            event_id=str(uuid.uuid4()),
            reason=reason,
            http_status=429 if reason.value.endswith('rate_limit') else 503,
            session_id=session.session_id,
            user_type=session.user_type.value,
            fingerprint=session.fingerprint,
            endpoint=endpoint,
            retry_after=retry_after,
            queue_stats=additional_data.get('queue_stats') if additional_data else None,
            rate_limit_stats=additional_data.get('rate_limit_stats') if additional_data else None
        )

        # Store event in Redis (with TTL)
        event_data = asdict(event)
        event_data['reason'] = event.reason.value  # Convert enum to string

        await self.redis.zadd(
            self.events_key,
            {json.dumps(event_data, default=str, ensure_ascii=False): event.timestamp}
        )

        # Keep only last 24 hours
        cutoff_time = time.time() - 86400
        await self.redis.zremrangebyscore(self.events_key, 0, cutoff_time)

        # Update session counter
        await self.redis.hincrby(self.session_counters_key, session.session_id, 1)
        await self.redis.expire(self.session_counters_key, 86400)  # 24 hour TTL

        # Update stats
        await self._update_stats(event)

        # Add to in-memory cache
        self.recent_events.append(event)

        logger.info(f"Throttling event: {reason.value} for session {session.session_id[:8]} ({session.user_type.value})")

    async def _update_stats(self, event: ThrottlingEvent):
        """Update aggregated statistics"""

        # Increment counters
        pipe = self.redis.pipeline()
        pipe.incr(REDIS.THROTTLING.TOTAL_THROTTLED_REQUESTS_KEY)
        pipe.hincrby(REDIS.THROTTLING.BY_REASON, event.reason.value, 1)

        if event.http_status == 429:
            pipe.incr(REDIS.THROTTLING.RATE_LIMIT_429)
        else:
            pipe.incr(REDIS.THROTTLING.BACKPRESSURE_503)

        # Hourly breakdown
        hour_key = time.strftime("%Y-%m-%d_%H", time.localtime(event.timestamp))
        pipe.hincrby(REDIS.THROTTLING.HOURLY, hour_key, 1)

        await pipe.execute()

    async def get_throttling_stats_for_period(self, hours_back: int = 1) -> ThrottlingStats:
        """Get throttling statistics for a specific time period"""
        await self.init_redis()

        # Calculate time window
        current_time = time.time()
        start_time = current_time - (hours_back * 3600)

        # Get events within time window
        events_in_period = await self.redis.zrangebyscore(
            self.events_key, start_time, current_time, withscores=True
        )

        # Count events by reason
        throttled_by_reason = {}
        rate_limit_429 = 0
        backpressure_503 = 0
        total_throttled = len(events_in_period)

        session_counters = {}

        for event_json, timestamp in events_in_period:
            try:
                event_dict = json.loads(event_json)
                reason = event_dict.get('reason', 'unknown')
                throttled_by_reason[reason] = throttled_by_reason.get(reason, 0) + 1

                if event_dict.get('http_status') == 429:
                    rate_limit_429 += 1
                elif event_dict.get('http_status') == 503:
                    backpressure_503 += 1

                # Count by session
                session_id = event_dict.get('session_id', 'unknown')
                session_counters[session_id] = session_counters.get(session_id, 0) + 1

            except json.JSONDecodeError:
                continue

        # Get total requests in period (approximate)
        # We'll need to track this better, for now use throttled + estimated successful
        total_requests = total_throttled  # This is a lower bound

        # Top throttled sessions
        top_sessions = sorted(session_counters.items(), key=lambda x: x[1], reverse=True)[:10]

        # Calculate hourly breakdown for the period
        hourly_stats = {}
        for event_json, timestamp in events_in_period:
            hour_key = time.strftime("%Y-%m-%d_%H", time.localtime(timestamp))
            hourly_stats[hour_key] = hourly_stats.get(hour_key, 0) + 1

        return ThrottlingStats(
            total_requests=total_requests,
            total_throttled=total_throttled,
            throttled_by_reason=throttled_by_reason,
            rate_limit_429=rate_limit_429,
            backpressure_503=backpressure_503,
            hourly_stats=hourly_stats,
            top_throttled_sessions=top_sessions
        )


    async def get_throttling_stats(self) -> ThrottlingStats:
        """Get current throttling statistics (all time - for backward compatibility)"""
        return await self.get_throttling_stats_for_period(hours_back=24)  # Default to last 24 hours

    async def get_recent_events_for_period(self, hours_back: int = 1, limit: int = 50) -> List[ThrottlingEvent]:
        """Get recent throttling events for a specific time period"""
        await self.init_redis()

        # Calculate time window
        current_time = time.time()
        start_time = current_time - (hours_back * 3600)

        # Get events within time window, most recent first
        event_data = await self.redis.zrevrangebyscore(
            self.events_key, current_time, start_time, start=0, num=limit, withscores=True
        )

        events = []
        for event_json, timestamp in event_data:
            try:
                event_dict = json.loads(event_json)
                event_dict['reason'] = ThrottlingReason(event_dict['reason'])
                events.append(ThrottlingEvent(**event_dict))
            except Exception as e:
                logger.error(f"Error parsing throttling event: {e}")

        return events
