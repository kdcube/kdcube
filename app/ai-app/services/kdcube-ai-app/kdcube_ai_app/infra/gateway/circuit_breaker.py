# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# # infra/gateway/circuit_breaker.py
"""
Circuit breaker functionality
"""

import time
import json
import uuid
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, Optional
import redis.asyncio as aioredis
import logging

from kdcube_ai_app.infra.gateway.definitions import GatewayError
from kdcube_ai_app.auth.sessions import UserSession, RequestContext

from kdcube_ai_app.infra.gateway.thorttling import ThrottlingMonitor, ThrottlingReason
from kdcube_ai_app.infra.namespaces import REDIS

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation
    OPEN = "open"           # Circuit is open, requests fail fast
    HALF_OPEN = "half_open" # Testing if service recovered

@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5           # Failed requests before opening
    recovery_timeout: int = 60           # Seconds to wait before trying again
    success_threshold: int = 3           # Successful requests needed to close
    timeout: float = 30.0               # Request timeout in seconds
    window_size: int = 60               # Sliding window in seconds
    half_open_max_calls: int = 3        # Max calls allowed in half-open state

@dataclass
class CircuitBreakerStats:
    name: str
    state: CircuitState
    failure_count: int
    success_count: int
    last_failure_time: Optional[float]
    last_success_time: Optional[float]
    total_requests: int
    total_failures: int
    opened_at: Optional[float]
    last_state_change: float
    consecutive_failures: int
    consecutive_successes: int
    current_window_failures: int = 0
    half_open_calls: int = 0

class CircuitBreakerError(GatewayError):
    """Raised when circuit breaker is open"""
    def __init__(self, circuit_name: str, retry_after: int, session: UserSession = None):
        self.circuit_name = circuit_name
        super().__init__(
            f"Circuit breaker '{circuit_name}' is open. Service temporarily unavailable.",
            503,
            retry_after,
            session
        )

class CircuitBreaker:
    """Individual circuit breaker for a specific service/operation"""

    def __init__(self, name: str, config: CircuitBreakerConfig, redis_url: str):
        self.name = name
        self.config = config
        self.redis_url = redis_url
        self.redis = None

        # Redis keys using your namespace pattern
        self.state_key = f"kdcube:circuit_breaker:{name}:state"
        self.stats_key = f"kdcube:circuit_breaker:{name}:stats"
        self.window_key = f"kdcube:circuit_breaker:{name}:window"
        self.half_open_key = f"kdcube:circuit_breaker:{name}:half_open_calls"

        # In-memory state (cached from Redis)
        self._cached_state = CircuitState.CLOSED
        self._last_cache_update = 0
        self._cache_ttl = 5  # seconds

    async def init_redis(self):
        if not self.redis:
            self.redis = aioredis.from_url(self.redis_url)

    async def check_request_allowed(self, session: UserSession) -> bool:
        """Check if request is allowed through circuit breaker"""
        state = await self._get_current_state()

        if state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            stats = await self._get_stats()
            if stats.opened_at and (time.time() - stats.opened_at) >= self.config.recovery_timeout:
                await self._transition_to_half_open()
                return True
            else:
                retry_after = int(self.config.recovery_timeout - (time.time() - (stats.opened_at or time.time())))
                raise CircuitBreakerError(self.name, max(1, retry_after), session)

        elif state == CircuitState.HALF_OPEN:
            # Check if we're at the limit for half-open calls
            current_calls = await self._get_half_open_calls()
            if current_calls >= self.config.half_open_max_calls:
                logger.warning(f"Circuit Breaker [{self.name}]: Half open call limit exceeded")
                raise CircuitBreakerError(self.name, self.config.recovery_timeout // 2, session)
            await self._increment_half_open_calls()

        return True

    async def record_success(self):
        """Record a successful request"""
        stats = await self._get_stats()
        current_time = time.time()

        stats.success_count += 1
        stats.total_requests += 1
        stats.last_success_time = current_time
        stats.consecutive_successes += 1
        stats.consecutive_failures = 0  # Reset failure counter

        current_state = await self._get_current_state()

        if current_state == CircuitState.HALF_OPEN:
            # Check if we have enough successes to close the circuit
            if stats.consecutive_successes >= self.config.success_threshold:
                await self._transition_to_closed()
                logger.info(f"Circuit Breaker [{self.name}]: Transitioning to CLOSED")
                stats.state = CircuitState.CLOSED

        await self._update_stats(stats)
        logger.debug(f"Circuit breaker '{self.name}': Success recorded")

    async def record_failure(self, error_type: str = "general"):
        """Record a failed request"""
        stats = await self._get_stats()
        current_time = time.time()

        stats.failure_count += 1
        stats.total_requests += 1
        stats.total_failures += 1
        stats.last_failure_time = current_time
        stats.consecutive_failures += 1
        stats.consecutive_successes = 0  # Reset success counter

        # Update sliding window
        await self._update_failure_window(current_time)
        stats.current_window_failures = await self._get_window_failures()

        current_state = await self._get_current_state()

        # Check if we should open the circuit based on window failures
        if (current_state == CircuitState.CLOSED and
                stats.current_window_failures >= self.config.failure_threshold):
            await self._transition_to_open()
            stats.state = CircuitState.OPEN
            stats.opened_at = current_time

        elif current_state == CircuitState.HALF_OPEN:
            # Any failure in half-open state reopens the circuit
            await self._transition_to_open()
            stats.state = CircuitState.OPEN
            stats.opened_at = current_time

        await self._update_stats(stats)
        logger.warning(f"Circuit breaker '{self.name}': {error_type} failure recorded (window: {stats.current_window_failures}/{self.config.failure_threshold})")

    async def _get_current_state(self) -> CircuitState:
        """Get current circuit breaker state with caching"""
        current_time = time.time()

        # Use cached state if still valid
        if (current_time - self._last_cache_update) < self._cache_ttl:
            return self._cached_state

        await self.init_redis()
        state_data = await self.redis.get(self.state_key)

        if state_data:
            self._cached_state = CircuitState(state_data.decode())
        else:
            self._cached_state = CircuitState.CLOSED
            await self.redis.set(self.state_key, self._cached_state.value, ex=3600)

        self._last_cache_update = current_time
        return self._cached_state

    async def _get_stats(self) -> CircuitBreakerStats:
        """Get circuit breaker statistics"""
        await self.init_redis()
        stats_data = await self.redis.get(self.stats_key)

        if stats_data:
            try:
                data = json.loads(stats_data)
                data['state'] = CircuitState(data['state'])
                return CircuitBreakerStats(**data)
            except Exception as e:
                logger.error(f"Error parsing circuit breaker stats: {e}")

        # Return default stats
        return CircuitBreakerStats(
            name=self.name,
            state=CircuitState.CLOSED,
            failure_count=0,
            success_count=0,
            last_failure_time=None,
            last_success_time=None,
            total_requests=0,
            total_failures=0,
            opened_at=None,
            last_state_change=time.time(),
            consecutive_failures=0,
            consecutive_successes=0,
            current_window_failures=0,
            half_open_calls=0
        )

    async def _update_stats(self, stats: CircuitBreakerStats):
        """Update circuit breaker statistics"""
        await self.init_redis()

        # Convert to dict and handle enum
        stats_dict = asdict(stats)
        stats_dict['state'] = stats.state.value

        await self.redis.set(self.stats_key, json.dumps(stats_dict, default=str, ensure_ascii=False), ex=3600)

        # Invalidate cache
        self._last_cache_update = 0

    async def _update_failure_window(self, timestamp: float):
        """Update sliding window of failures"""
        await self.init_redis()

        # Add current failure to window
        await self.redis.zadd(self.window_key, {str(uuid.uuid4()): timestamp})

        # Remove old failures outside window
        cutoff_time = timestamp - self.config.window_size
        await self.redis.zremrangebyscore(self.window_key, 0, cutoff_time)

        # Set expiry
        await self.redis.expire(self.window_key, self.config.window_size * 2)

    async def _get_window_failures(self) -> int:
        """Get number of failures in current window"""
        await self.init_redis()
        return await self.redis.zcard(self.window_key)

    async def _transition_to_open(self):
        """Transition circuit breaker to OPEN state"""
        await self.init_redis()
        await self.redis.set(self.state_key, CircuitState.OPEN.value, ex=3600)
        await self.redis.delete(self.half_open_key)
        self._cached_state = CircuitState.OPEN
        logger.warning(f"Circuit breaker '{self.name}' opened")

    async def _transition_to_half_open(self):
        """Transition circuit breaker to HALF_OPEN state"""
        await self.init_redis()
        await self.redis.set(self.state_key, CircuitState.HALF_OPEN.value, ex=3600)
        await self.redis.delete(self.half_open_key)
        self._cached_state = CircuitState.HALF_OPEN
        logger.info(f"Circuit breaker '{self.name}' half-opened")

    async def _transition_to_closed(self):
        """Transition circuit breaker to CLOSED state"""
        await self.init_redis()
        await self.redis.set(self.state_key, CircuitState.CLOSED.value, ex=3600)
        await self.redis.delete(self.half_open_key)
        # Also clear failure window on successful close
        await self.redis.delete(self.window_key)
        self._cached_state = CircuitState.CLOSED
        logger.info(f"Circuit breaker '{self.name}' closed")

    async def _get_half_open_calls(self) -> int:
        """Get number of calls made in half-open state"""
        await self.init_redis()
        calls = await self.redis.get(self.half_open_key)
        return int(calls) if calls else 0

    async def _increment_half_open_calls(self):
        """Increment half-open call counter"""
        await self.init_redis()
        await self.redis.incr(self.half_open_key)
        await self.redis.expire(self.half_open_key, self.config.recovery_timeout)

class QueueAwareCircuitBreaker(CircuitBreaker):
    """Circuit breaker that considers queue pressure for backpressure decisions"""

    def __init__(self, name: str, config: CircuitBreakerConfig, redis_url: str, backpressure_manager=None):
        super().__init__(name, config, redis_url)
        self.backpressure_manager = backpressure_manager

        # Queue-specific settings
        self.queue_check_interval = 5  # Check queue every 5 seconds
        self.last_queue_check = 0
        self.cached_queue_pressure = 0.0

    async def check_request_allowed(self, session: UserSession) -> bool:
        """Enhanced check that considers both failures and queue pressure"""
        current_time = time.time()
        state = await self._get_current_state()

        # For backpressure circuit breaker, also check queue levels
        if self.name == "backpressure" and self.backpressure_manager:
            await self._update_queue_pressure_cache(current_time)

        if state == CircuitState.OPEN:
            # For backpressure CB: also check if queue pressure has decreased
            if self.name == "backpressure" and self._should_attempt_recovery_due_to_queue():
                await self._transition_to_half_open()
                return True

            # Standard timeout-based recovery
            stats = await self._get_stats()
            if stats.opened_at and (current_time - stats.opened_at) >= self.config.recovery_timeout:
                await self._transition_to_half_open()
                return True
            else:
                retry_after = int(self.config.recovery_timeout - (current_time - (stats.opened_at or current_time)))
                raise CircuitBreakerError(self.name, max(1, retry_after), session)

        elif state == CircuitState.HALF_OPEN:
            # Standard half-open logic
            current_calls = await self._get_half_open_calls()
            if current_calls >= self.config.half_open_max_calls:
                logger.warning(f"Circuit Breaker [{self.name}]: Half open call limit exceeded")
                raise CircuitBreakerError(self.name, self.config.recovery_timeout // 2, session)
            await self._increment_half_open_calls()

        return True

    async def _update_queue_pressure_cache(self, current_time: float):
        """Update cached queue pressure if needed"""
        if current_time - self.last_queue_check > self.queue_check_interval:
            try:
                queue_stats = await self.backpressure_manager.get_queue_stats()
                self.cached_queue_pressure = queue_stats.pressure_ratio
                self.last_queue_check = current_time
            except Exception as e:
                logger.error(f"Error updating queue pressure cache: {e}")

    def _should_attempt_recovery_due_to_queue(self) -> bool:
        """Check if we should attempt recovery due to decreased queue pressure"""
        # If queue pressure is below 50% of registered threshold, attempt recovery
        recovery_threshold = 0.5 * 0.8  # 50% of registered pressure threshold (80%)
        return self.cached_queue_pressure < recovery_threshold

    async def record_success(self):
        """Enhanced success recording for backpressure CB"""
        await super().record_success()

        # For backpressure CB: if we're in half-open and queue pressure is low, close faster
        if self.name == "backpressure":
            current_state = await self._get_current_state()
            if current_state == CircuitState.HALF_OPEN and self.cached_queue_pressure < 0.3:
                # Queue pressure is very low, close the circuit faster
                stats = await self._get_stats()
                if stats.consecutive_successes >= max(1, self.config.success_threshold // 2):
                    await self._transition_to_closed()
                    logger.info(f"Circuit Breaker [{self.name}]: Fast close due to low queue pressure")

    async def record_failure(self, error_type: str = "general"):
        """
        Enhanced failure recording that considers queue pressure.

        For backpressure_exceeded when queue pressure is very low,
        we ignore the failure (likely config/false positive).
        """
        if self.name == "backpressure" and error_type == "backpressure_exceeded":
            current_time = time.time()
            await self._update_queue_pressure_cache(current_time)

            if self.cached_queue_pressure < 0.1:
                logger.warning(
                    f"Backpressure error with low queue pressure "
                    f"({self.cached_queue_pressure:.2f}); ignoring failure"
                )
                # TODO: "soft record" option
                # We may want to *count lightly* instead of ignoring completely:
                #   - increment total_requests / total_failures for observability
                #   - maybe track a separate "soft_backpressure_failures" metric
                #   - BUT do NOT update sliding window
                #   - AND do NOT trigger OPEN transition
                # This would preserve monitoring signal without destabilizing the CB logic.
                return

        await super().record_failure(error_type)


class CircuitBreakerManager:
    """Manages multiple circuit breakers and integrates with your throttling monitor"""

    def __init__(self, redis_url: str, throttling_monitor: ThrottlingMonitor):
        self.redis_url = redis_url
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.throttling_monitor = throttling_monitor
        self.default_config = CircuitBreakerConfig()

    def setup_default_circuits(self):
        """Setup circuit breakers for different gateway operations"""

        # Rate limiter circuit breaker (prevents retry storms)
        rate_limit_config = CircuitBreakerConfig(
            failure_threshold=20,      # Higher threshold - only for system failures
            recovery_timeout=30,       # 0.5 minute recovery time
            success_threshold=3,       # Testing time: need 3 successes to close
            half_open_max_calls=5,     # testing time: test with 5 requests
            window_size=120            # 2 minute window
        )
        self.get_circuit_breaker("rate_limiter", rate_limit_config)

        # Backpressure circuit breaker
        backpressure_config = CircuitBreakerConfig(
            failure_threshold=10,      # Open after backpressure hits
            recovery_timeout=60,       # 1 minute recovery
            success_threshold=5,       # Need some successes
            half_open_max_calls=3,     # Test with only 3 requests
            window_size=120            # 2 minute window
        )
        self.get_circuit_breaker("backpressure", backpressure_config)

        # Authentication circuit breaker
        auth_config = CircuitBreakerConfig(
            failure_threshold=15,       # Allow more auth service failures
            recovery_timeout=60,        # 1 minute recovery
            success_threshold=5,        # Quick recovery
            half_open_max_calls=10,     # Allow reasonable testing
            window_size=120            # 2 minute window
        )
        self.get_circuit_breaker("authentication", auth_config)

    def get_circuit_breaker(self, name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        """Get or create a circuit breaker"""
        if name not in self.circuit_breakers:
            circuit_config = config or self.default_config
            self.circuit_breakers[name] = CircuitBreaker(name, circuit_config, self.redis_url)

        return self.circuit_breakers[name]

    async def record_circuit_breaker_event(self,
                                           circuit_name: str,
                                           session: UserSession,
                                           context: RequestContext,
                                           endpoint: str,
                                           retry_after: int):
        """Record circuit breaker event in throttling monitor"""
        await self.throttling_monitor.record_throttling_event(
            reason=ThrottlingReason.SYSTEM_BACKPRESSURE,  # Reuse existing reason or add new one
            session=session,
            context=context,
            endpoint=endpoint,
            retry_after=retry_after,
            additional_data={
                'circuit_breaker': circuit_name,
                'circuit_state': 'open'
            }
        )

    async def get_all_stats(self) -> Dict[str, CircuitBreakerStats]:
        """Get statistics for all circuit breakers"""
        stats = {}
        for name, cb in self.circuit_breakers.items():
            cb_stats = await cb._get_stats()
            # Add current window failures
            cb_stats.current_window_failures = await cb._get_window_failures()
            cb_stats.half_open_calls = await cb._get_half_open_calls()
            stats[name] = cb_stats
        return stats

    async def reset_circuit_breaker(self, name: str):
        """Manually reset a circuit breaker to CLOSED state"""
        if name in self.circuit_breakers:
            cb = self.circuit_breakers[name]
            await cb._transition_to_closed()

            # Reset stats
            stats = CircuitBreakerStats(
                name=name,
                state=CircuitState.CLOSED,
                failure_count=0,
                success_count=0,
                last_failure_time=None,
                last_success_time=None,
                total_requests=0,
                total_failures=0,
                opened_at=None,
                last_state_change=time.time(),
                consecutive_failures=0,
                consecutive_successes=0,
                current_window_failures=0,
                half_open_calls=0
            )
            await cb._update_stats(stats)
            logger.info(f"Circuit breaker '{name}' manually reset")

class QueueAwareCircuitBreakerManager(CircuitBreakerManager):
    """Circuit breaker manager with queue-aware backpressure CB"""

    def __init__(self, redis_url: str, throttling_monitor, backpressure_manager=None):
        super().__init__(redis_url, throttling_monitor)
        self.backpressure_manager = backpressure_manager

    def get_circuit_breaker(self, name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        """Get or create a circuit breaker (queue-aware for backpressure)"""
        if name not in self.circuit_breakers:
            circuit_config = config or self.default_config

            if name == "backpressure" and self.backpressure_manager:
                # Use queue-aware circuit breaker for backpressure
                self.circuit_breakers[name] = QueueAwareCircuitBreaker(
                    name, circuit_config, self.redis_url, self.backpressure_manager
                )
            else:
                # Standard circuit breaker for others
                self.circuit_breakers[name] = CircuitBreaker(name, circuit_config, self.redis_url)

        return self.circuit_breakers[name]