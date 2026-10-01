# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/gateway/rate_limiter.py

import hashlib
import time
import uuid
import logging
from dataclasses import dataclass

from kdcube_ai_app.auth.sessions import UserSession, UserType, RequestContext
from kdcube_ai_app.infra.gateway.config import GatewayConfiguration
from kdcube_ai_app.infra.gateway.definitions import GatewayError
from kdcube_ai_app.infra.gateway.thorttling import ThrottlingMonitor, ThrottlingReason
from kdcube_ai_app.infra.namespaces import REDIS, ns_key
from kdcube_ai_app.infra.redis.client import get_async_redis_client

logger = logging.getLogger(__name__)


# Counts and records one request atomically (W435). Returns
# {admitted, burst_count, hour_count}, where the counts include this request
# when admitted and are the counts that refused it otherwise. A limit of -1 is
# unlimited. Nothing is written for a refused request.
_ADMIT_SCRIPT = """
local burst_key = KEYS[1]
local hour_key = KEYS[2]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local burst_limit = tonumber(ARGV[3])
local hour_limit = tonumber(ARGV[4])
local hour_ttl = tonumber(ARGV[5])
local member = ARGV[6]
redis.call('ZREMRANGEBYSCORE', burst_key, 0, now - window)
local burst = redis.call('ZCARD', burst_key) + 1
local hour = tonumber(redis.call('GET', hour_key) or '0') + 1
if (burst_limit ~= -1 and burst > burst_limit) or (hour_limit ~= -1 and hour > hour_limit) then
  return {0, burst, hour}
end
redis.call('ZADD', burst_key, now, member)
redis.call('EXPIRE', burst_key, window)
redis.call('INCR', hour_key)
redis.call('EXPIRE', hour_key, hour_ttl)
return {1, burst, hour}
"""


def rate_limit_subject(session: UserSession) -> str:
    """Return the stable subject that owns this request budget."""

    subject = str(getattr(session, "rate_limit_subject", None) or "").strip()
    return subject or str(session.session_id)


def _subject_digest(subject: str) -> str:
    """A short, stable digest of a budget subject for logs, never the raw id."""

    return hashlib.sha256(str(subject).encode("utf-8")).hexdigest()[:12]


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

    def __init__(self, redis_url: str,
                 gateway_config: GatewayConfiguration,
                 monitor: ThrottlingMonitor):

        self.redis_url = redis_url
        self.redis = None
        self.gateway_config = gateway_config
        self.monitor = monitor
        # Rate-limit keys are tenant/project namespaced
        self.RATE_LIMIT_PREFIX = self.ns(REDIS.SYSTEM.RATE_LIMIT)

        self.limits = {}
        self._refresh_limits()

    def _refresh_limits(self):
        roles = self.gateway_config.rate_limits.roles
        def _rl(role: str, default: RateLimitConfig) -> RateLimitConfig:
            cfg = roles.get(role)
            if cfg:
                return RateLimitConfig(
                    requests_per_hour=cfg.hourly,
                    burst_limit=cfg.burst,
                    burst_window=cfg.burst_window
                )
            return default
        anonymous_limit = _rl("anonymous", RateLimitConfig(50, 5, 60))
        self.limits = {
            UserType.ANONYMOUS: anonymous_limit,
            UserType.EXTERNAL: _rl("external", anonymous_limit),
            UserType.REGISTERED: _rl("registered", RateLimitConfig(500, 20, 60)),
            UserType.PAID: _rl("paid", RateLimitConfig(1000, 50, 60)),
            UserType.PRIVILEGED: _rl("privileged", RateLimitConfig(-1, 100, 60)),
        }

    def ns(self, base: str) -> str:
        return ns_key(base, tenant=self.gateway_config.tenant_id, project=self.gateway_config.project_id)

    async def init_redis(self):
        if not self.redis:
            self.redis = get_async_redis_client(self.redis_url)

    async def check_and_record(self, session: UserSession, context: RequestContext, endpoint: str) -> None:
        """Your existing check_and_record with monitoring integration"""
        await self.init_redis()

        config = self.limits.get(session.user_type)
        if not config:
            return  # No limits configured

        rate_key = f"{self.RATE_LIMIT_PREFIX}:{rate_limit_subject(session)}"
        current_time = time.time()

        # One atomic step (W435): prune the burst window, count it and the
        # hourly bucket, and record this request only when both limits admit
        # it. A refused request is never written, so a client retrying on 429
        # does not keep its own window full; members are unique, so two
        # requests in the same instant are both counted.
        burst_key = f"{rate_key}:burst"
        hour_key = f"{rate_key}:hour:{int(current_time // 3600)}"
        member = f"{current_time:.6f}:{uuid.uuid4().hex}"
        admitted, burst_count, hour_count = await self.redis.eval(
            _ADMIT_SCRIPT,
            2,
            burst_key,
            hour_key,
            current_time,
            config.burst_window,
            config.burst_limit,
            config.requests_per_hour,
            self.gateway_config.redis.rate_limit_key_ttl,
            member,
        )
        burst_count = int(burst_count)
        hour_count = int(hour_count)
        if not int(admitted):
            # W435: one line per refusal, so a burst can be traced to its
            # session and path without reading tokens, cookies or bodies.
            logger.warning(
                "rate limit refused subject=%s user_type=%s endpoint=%s burst=%s/%s hour=%s/%s",
                _subject_digest(rate_limit_subject(session)),
                session.user_type.value,
                endpoint,
                burst_count,
                config.burst_limit,
                hour_count,
                config.requests_per_hour,
            )

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
