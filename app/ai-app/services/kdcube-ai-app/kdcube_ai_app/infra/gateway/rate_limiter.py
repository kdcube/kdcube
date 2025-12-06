# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/gateway/rate_limiter.py

import time
import logging
from dataclasses import dataclass

from redis import asyncio as aioredis

from kdcube_ai_app.auth.sessions import UserSession, UserType, RequestContext
from kdcube_ai_app.infra.gateway.config import GatewayConfiguration
from kdcube_ai_app.infra.gateway.definitions import GatewayError
from kdcube_ai_app.infra.gateway.thorttling import ThrottlingMonitor, ThrottlingReason
from kdcube_ai_app.infra.namespaces import REDIS

logger = logging.getLogger(__name__)

class RateLimitError(GatewayError):
    """Rate limit exceeded"""
    def __init__(self, message: str, retry_after: int = 3600, session: UserSession = None):
        super().__init__(message, 429, retry_after, session)


@dataclass
class RateLimitConfig:
    """Rate limit configuration"""
    requests_per_hour: int
    burst_limit: int
    burst_window: int = 60  # seconds


class RateLimiter:
    """Simple rate limiter"""

    def __init__(self, redis_url: str, gateway_config: GatewayConfiguration, monitor: ThrottlingMonitor):
        self.redis_url = redis_url
        self.redis = None
        self.gateway_config = gateway_config
        self.monitor = monitor
        self.RATE_LIMIT_PREFIX = REDIS.SYSTEM.RATE_LIMIT

        # Create rate limit configs from centralized configuration
        self.limits = {
            UserType.ANONYMOUS: RateLimitConfig(
                requests_per_hour=gateway_config.rate_limits.anonymous_hourly,
                burst_limit=gateway_config.rate_limits.anonymous_burst,
                burst_window=gateway_config.rate_limits.anonymous_burst_window
            ),
            UserType.REGISTERED: RateLimitConfig(
                requests_per_hour=gateway_config.rate_limits.registered_hourly,
                burst_limit=gateway_config.rate_limits.registered_burst,
                burst_window=gateway_config.rate_limits.registered_burst_window
            ),
            UserType.PRIVILEGED: RateLimitConfig(
                requests_per_hour=gateway_config.rate_limits.privileged_hourly,
                burst_limit=gateway_config.rate_limits.privileged_burst,
                burst_window=gateway_config.rate_limits.privileged_burst_window
            )
        }

    async def init_redis(self):
        if not self.redis:
            self.redis = aioredis.from_url(self.redis_url)

    async def check_and_record(self, session: UserSession, context: RequestContext, endpoint: str) -> None:
        """Your existing check_and_record with monitoring integration"""
        await self.init_redis()

        config = self.limits.get(session.user_type)
        if not config:
            return  # No limits configured

        rate_key = f"{self.RATE_LIMIT_PREFIX}:{session.session_id}"
        current_time = time.time()

        # Use Redis pipeline for atomic operations (your existing code)
        pipe = self.redis.pipeline()

        # Check burst limit (sliding window)
        burst_key = f"{rate_key}:burst"
        pipe.zremrangebyscore(burst_key, 0, current_time - config.burst_window)
        pipe.zcard(burst_key)
        pipe.zadd(burst_key, {str(current_time): current_time})
        pipe.expire(burst_key, config.burst_window)

        # Check hourly limit
        hour_key = f"{rate_key}:hour:{int(current_time // 3600)}"
        pipe.incr(hour_key)
        pipe.expire(hour_key, self.gateway_config.redis.rate_limit_key_ttl)

        results = await pipe.execute()

        burst_count = results[1]
        hour_count = results[4]

        # Check limits (your existing logic with monitoring)
        if config.burst_limit != -1 and burst_count > config.burst_limit:
            # Record throttling event before raising error
            await self.monitor.record_throttling_event(
                reason=ThrottlingReason.BURST_RATE_LIMIT,
                session=session,
                context=context,
                endpoint=endpoint,
                retry_after=config.burst_window,
                additional_data={
                    'rate_limit_stats': {
                        'burst_count': burst_count,
                        'burst_limit': config.burst_limit,
                        'hour_count': hour_count,
                        'hour_limit': config.requests_per_hour
                    },
                    'gateway_config': {
                        'profile': self.gateway_config.profile.value,
                        'user_type': session.user_type.value
                    }
                }
            )
            raise RateLimitError(
                f"Burst limit exceeded ({burst_count}/{config.burst_limit})",
                config.burst_window,
                session=session
            )

        if config.requests_per_hour != -1 and hour_count > config.requests_per_hour:
            # Record throttling event before raising error
            await self.monitor.record_throttling_event(
                reason=ThrottlingReason.HOURLY_RATE_LIMIT,
                session=session,
                context=context,
                endpoint=endpoint,
                retry_after=3600,
                additional_data={
                    'rate_limit_stats': {
                        'hour_count': hour_count,
                        'hour_limit': config.requests_per_hour,
                        'burst_count': burst_count,
                        'burst_limit': config.burst_limit
                    },
                    'gateway_config': {
                        'profile': self.gateway_config.profile.value,
                        'user_type': session.user_type.value
                    }
                }
            )

            raise RateLimitError(
                f"Hourly limit exceeded ({hour_count}/{config.requests_per_hour})",
                3600,
                session=session
            )
