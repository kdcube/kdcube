# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/gateway/backpressure.py

import json
import time
import os
from typing import Set, Dict, Any, Tuple

from kdcube_ai_app.auth.sessions import UserSession, UserType, RequestContext
from kdcube_ai_app.infra.gateway.config import GatewayConfiguration
from kdcube_ai_app.infra.gateway.definitions import GatewayError, DynamicCapacityCalculator, QueueStats
import logging

from kdcube_ai_app.infra.gateway.thorttling import ThrottlingMonitor, ThrottlingReason
from kdcube_ai_app.infra.namespaces import REDIS, ns_key
from kdcube_ai_app.infra.redis.client import get_async_redis_client

logger = logging.getLogger(__name__)

QUEUE_USER_TYPES = ("anonymous", "registered", "privileged", "paid")


def _queue_key(prefix: str, user_type: str) -> str:
    return f"{prefix}:{user_type}"


def _continuation_count_key(prefix: str, user_type: str) -> str:
    return f"{prefix}:{user_type}"


async def _get_combined_queue_sizes(
    redis,
    queue_prefix: str,
    inflight_queue_prefix: str,
    continuation_count_prefix: str,
) -> Dict[str, int]:
    sizes: Dict[str, int] = {}
    for user_type in QUEUE_USER_TYPES:
        ready = await redis.llen(_queue_key(queue_prefix, user_type))
        inflight = await redis.llen(_queue_key(inflight_queue_prefix, user_type))
        continuation = await redis.get(_continuation_count_key(continuation_count_prefix, user_type))
        if isinstance(continuation, bytes):
            continuation = continuation.decode("utf-8")
        sizes[user_type] = int(ready) + int(inflight) + int(continuation or 0)
    return sizes

class BackpressureError(GatewayError):
    """System under pressure"""
    def __init__(self, message: str, retry_after: int = 60, session: UserSession = None):
        super().__init__(message, 503, retry_after, session)


class BackpressureManager:
    """Simple backpressure management"""

    def __init__(self,
                 redis_url: str,
                 gateway_config: GatewayConfiguration,
                 monitor: ThrottlingMonitor):
        self.redis_url = redis_url
        self.redis = None
        self.gateway_config = gateway_config
        self.config = gateway_config.backpressure_config_obj
        self.monitor = monitor

        # Dynamic capacity calculator (will be injected)
        self.capacity_calculator = None

        self.QUEUE_PREFIX = self.ns(REDIS.CHAT.PROMPT_QUEUE_PREFIX)
        self.QUEUE_INFLIGHT_PREFIX = self.ns(REDIS.CHAT.PROMPT_QUEUE_INFLIGHT_PREFIX)
        self.QUEUE_CONTINUATION_COUNT_PREFIX = self.ns(REDIS.CHAT.CONVERSATION_MAILBOX_COUNT_PREFIX)
        self.PROCESS_HEARTBEAT_PREFIX = self.ns(REDIS.PROCESS.HEARTBEAT_PREFIX)
        self.INSTANCE_STATUS_PREFIX = self.ns(REDIS.INSTANCE.HEARTBEAT_PREFIX)

        # NEW: Atomic capacity tracking
        self.CAPACITY_COUNTER_KEY = self.ns(f"{REDIS.SYSTEM.CAPACITY}:counter")
        self.CAPACITY_LOCK_PREFIX = self.ns(f"{REDIS.SYNCHRONIZATION.LOCK}:capacity")

        # Queue analytics keys
        self.QUEUE_ANALYTICS_PREFIX = self.ns(f"{REDIS.CHAT.PROMPT_QUEUE_PREFIX}:analytics")

        # Cache for performance
        self._last_instance_check = 0
        # self._cached_instances = set()
        # self._instance_cache_ttl = gateway_config.monitoring.instance_cache_ttl_seconds

    def ns(self, base: str) -> str:
        return ns_key(base, tenant=self.gateway_config.tenant_id, project=self.gateway_config.project_id)

    async def init_redis(self):
        if not self.redis:
            self.redis = get_async_redis_client(self.redis_url)

    def set_capacity_calculator(self, calculator: DynamicCapacityCalculator):
        """Inject dynamic capacity calculator"""
        self.capacity_calculator = calculator

    async def get_alive_instances(self) -> Set[str]:
        """Get unique alive instances from heartbeat keys"""
        current_time = time.time()

        # Use cache if still valid
        # if current_time - self._last_instance_check < self._instance_cache_ttl:
        #     return self._cached_instances

        await self.init_redis()
        # Pattern matches: kdcube:heartbeat:instance:instance-id:service-type:service-name
        # or kdcube:heartbeat:process:instance-id:service-type:service-name:process-id
        patterns = [
            f"{self.INSTANCE_STATUS_PREFIX}:*",
            f"{self.PROCESS_HEARTBEAT_PREFIX}:*"
        ]

        alive_instances = set()

        for pattern in patterns:
            keys = await self.redis.keys(pattern)

            for key in keys:
                try:
                    # Extract instance ID from key (position 2 in split)
                    # kdcube:instance:home-instance-1:chat:rest -> home-instance-1
                    # kdcube:process:home-instance-1:kb:rest:12345 -> home-instance-1
                    key_parts = key.decode().split(':')
                    if len(key_parts) >= 4:
                        instance_id = key_parts[3]

                        # Check if the heartbeat is recent (not expired)
                        data = await self.redis.get(key)
                        if data:
                            try:
                                heartbeat_data = json.loads(data)
                                last_heartbeat = heartbeat_data.get('last_heartbeat', 0)

                                # Use configured heartbeat timeout
                                heartbeat_timeout = self.gateway_config.monitoring.heartbeat_timeout_seconds
                                if current_time - last_heartbeat <= heartbeat_timeout:
                                    alive_instances.add(instance_id)
                            except (json.JSONDecodeError, KeyError):
                                # If we can't parse the data, skip this key
                                continue

                except Exception as e:
                    logger.debug(f"Error processing heartbeat key {key}: {e}")
                    continue
        # Update cache
        # self._cached_instances = alive_instances
        # self._last_instance_check = current_time

        return alive_instances

    async def get_individual_queue_sizes(self) -> Dict[str, int]:
        """Get individual queue sizes"""
        await self.init_redis()
        return await _get_combined_queue_sizes(
            self.redis,
            self.QUEUE_PREFIX,
            self.QUEUE_INFLIGHT_PREFIX,
            self.QUEUE_CONTINUATION_COUNT_PREFIX,
        )

    async def get_queue_analytics(self) -> Dict[str, Dict[str, Any]]:
        """Get queue analytics for each user type"""
        if not self.gateway_config.monitoring.queue_analytics_enabled:
            return {}

        await self.init_redis()

        analytics = {}
        for user_type in ["anonymous", "registered", "privileged", "paid"]:
            analytics_key = f"{self.QUEUE_ANALYTICS_PREFIX}:{user_type}"

            # Get or initialize analytics
            data = await self.redis.get(analytics_key)
            if data:
                try:
                    analytics[user_type] = json.loads(data)
                except json.JSONDecodeError:
                    analytics[user_type] = self._default_analytics()
            else:
                analytics[user_type] = self._default_analytics()

        return analytics

    def _default_analytics(self) -> Dict[str, Any]:
        """Default analytics structure"""
        return {
            "avg_wait_time": 0.0,
            "processed_today": 0,
            "peak_size_today": 0,
            "last_updated": time.time(),
            "throughput_last_hour": 0,
            "total_processed": 0
        }

    async def update_queue_analytics(self, user_type: str, wait_time: float = None, processed: bool = False):
        """Update queue analytics"""
        if not self.gateway_config.monitoring.queue_analytics_enabled:
            return

        await self.init_redis()

        analytics_key = f"{self.QUEUE_ANALYTICS_PREFIX}:{user_type}"

        # Get current analytics
        data = await self.redis.get(analytics_key)
        if data:
            try:
                analytics = json.loads(data)
            except json.JSONDecodeError:
                analytics = self._default_analytics()
        else:
            analytics = self._default_analytics()

        # Update analytics
        if wait_time is not None:
            # Running average of wait times
            current_avg = analytics.get("avg_wait_time", 0)
            total_processed = analytics.get("total_processed", 0)
            new_avg = ((current_avg * total_processed) + wait_time) / (total_processed + 1)
            analytics["avg_wait_time"] = new_avg

        if processed:
            analytics["processed_today"] += 1
            analytics["total_processed"] += 1
            analytics["throughput_last_hour"] += 1

        # Update peak size
        current_size = (
            await self.redis.llen(_queue_key(self.QUEUE_PREFIX, user_type))
            + await self.redis.llen(_queue_key(self.QUEUE_INFLIGHT_PREFIX, user_type))
            + int((await self.redis.get(_continuation_count_key(self.QUEUE_CONTINUATION_COUNT_PREFIX, user_type))) or 0)
        )
        analytics["peak_size_today"] = max(analytics.get("peak_size_today", 0), current_size)
        analytics["last_updated"] = time.time()

        # Store updated analytics
        await self.redis.setex(analytics_key, self.gateway_config.redis.analytics_ttl, json.dumps(analytics, default=str, ensure_ascii=False))

    # async def check_capacity(self,
    #                          user_type: UserType,
    #                          session: UserSession,
    #                          context: RequestContext,
    #                          endpoint: str) -> None:
    #     """Check if system can accept new requests"""
    #     await self.init_redis()
    #
    #     # Get current queue sizes
    #     queue_sizes = await self.get_individual_queue_sizes()
    #     total_size = sum(queue_sizes.values())
    #
    #     # Get instance count and calculate thresholds
    #     alive_instances = await self.get_alive_instances()
    #     instance_count = max(len(alive_instances), 1)
    #     thresholds = self.config.get_capacity_thresholds(instance_count)
    #
    #     queue_stats = {
    #         'queue_sizes': queue_sizes,
    #         'total_size': total_size,
    #         'instance_count': instance_count,
    #         'thresholds': thresholds,
    #         'pressure_ratio': total_size / thresholds['total_capacity'],
    #         'gateway_config': {
    #             'profile': self.gateway_config.profile.value,
    #             'instance_id': self.gateway_config.instance_id,
    #             'capacity_settings': self.gateway_config.to_dict()['service_capacity'],
    #             'backpressure_settings': self.gateway_config.to_dict()['backpressure']
    #         }
    #     }
    #
    #     # Record queue analytics
    #     await self.update_queue_analytics(user_type.value.lower())
    #
    #     # Apply backpressure policies
    #     if total_size >= thresholds['hard_limit']:
    #         await self._record_and_raise_backpressure(
    #             ThrottlingReason.SYSTEM_BACKPRESSURE, session, context, endpoint,
    #             f"System at hard limit ({total_size}/{thresholds['hard_limit']})",
    #             queue_stats, 60
    #         )
    #
    #     elif total_size >= thresholds['registered_threshold']:
    #         if user_type != UserType.PRIVILEGED:
    #             await self._record_and_raise_backpressure(
    #                 ThrottlingReason.REGISTERED_BACKPRESSURE, session, context, endpoint,
    #                 f"System under high pressure - privileged users only ({total_size}/{thresholds['registered_threshold']})",
    #                 queue_stats, 45
    #             )
    #
    #     elif total_size >= thresholds['anonymous_threshold']:
    #         if user_type == UserType.ANONYMOUS:
    #             await self._record_and_raise_backpressure(
    #                 ThrottlingReason.SYSTEM_BACKPRESSURE, session, context, endpoint,
    #                 f"System under pressure - registered users only ({total_size}/{thresholds['anonymous_threshold']})",
    #                 queue_stats, 30
    #             )

    async def _record_and_raise_backpressure(self, reason, session, context, endpoint, message, stats, retry_after):
        """Helper to record throttling and raise backpressure error"""
        await self.monitor.record_throttling_event(
            reason=reason,
            session=session,
            context=context,
            endpoint=endpoint,
            retry_after=retry_after,
            additional_data={'queue_stats': stats}
        )

        raise BackpressureError(message, retry_after, session=session)

    async def get_queue_stats(self) -> QueueStats:
        """Get comprehensive queue statistics"""
        await self.init_redis()

        # Get individual queue sizes
        queue_sizes = await self.get_individual_queue_sizes()
        total_size = sum(queue_sizes.values())

        # Get instance information
        alive_instances = await self.get_alive_instances()
        instance_count = max(len(alive_instances), 1)

        # Use dynamic calculator for capacity metrics if available
        if self.capacity_calculator:
            base_capacity = await self.capacity_calculator.get_base_queue_size_per_instance()

            # Get actual process data for all instances
            actual_processes_data = await self.capacity_calculator.get_actual_process_info()
            thresholds = await self.capacity_calculator.get_capacity_thresholds(actual_processes_data)
            weighted_capacity = thresholds["total_capacity"]
        else:
            # Fallback to static calculation
            logger.warning("No dynamic capacity calculator available, using static calculation")
            base_capacity = self.config.get_base_queue_size_per_instance()
            weighted_capacity = base_capacity * instance_count
            thresholds = self.config.get_capacity_thresholds(instance_count)

        # Calculate pressure and acceptance status
        pressure_ratio = total_size / weighted_capacity if weighted_capacity > 0 else 1.0

        accepting_anonymous = total_size < thresholds['anonymous_threshold']
        accepting_registered = total_size < thresholds['registered_threshold']
        accepting_paid = total_size < thresholds['paid_threshold']
        accepting_privileged = total_size < thresholds['hard_limit']

        # Get analytics
        analytics = await self.get_queue_analytics()

        return QueueStats(
            anonymous_queue=queue_sizes['anonymous'],
            registered_queue=queue_sizes['registered'],
            paid_queue=queue_sizes.get('paid', 0),
            privileged_queue=queue_sizes['privileged'],
            total_queue=total_size,
            base_capacity_per_instance=base_capacity,
            alive_instances=list(alive_instances),
            instance_count=instance_count,
            weighted_max_capacity=weighted_capacity,
            pressure_ratio=pressure_ratio,
            accepting_anonymous=accepting_anonymous,
            accepting_registered=accepting_registered,
            accepting_paid=accepting_paid,
            accepting_privileged=accepting_privileged,
            anonymous_threshold=thresholds['anonymous_threshold'],
            registered_threshold=thresholds['registered_threshold'],
            paid_threshold=thresholds['paid_threshold'],
            hard_limit_threshold=thresholds['hard_limit'],
            avg_wait_times={
                user_type: data.get('avg_wait_time', 0)
                for user_type, data in analytics.items()
            },
            throughput_metrics={
                user_type: data.get('throughput_last_hour', 0)
                for user_type, data in analytics.items()
            }
        )

    async def check_capacity_atomic(self,
                                    user_type: UserType,
                                    session: UserSession,
                                    context: RequestContext,
                                    endpoint: str) -> None:
        """Atomic capacity check that prevents race conditions"""
        await self.init_redis()

        # Get current thresholds
        alive_instances = await self.get_alive_instances()
        instance_count = max(len(alive_instances), 1)
        thresholds = self.config.get_capacity_thresholds(instance_count)

        # Determine threshold for this user type
        if user_type == UserType.ANONYMOUS:
            threshold = thresholds['anonymous_threshold']
        elif user_type == UserType.REGISTERED:
            threshold = thresholds['registered_threshold']
        elif user_type == UserType.PAID:
            threshold = thresholds['paid_threshold']
        else:  # PRIVILEGED
            threshold = thresholds['hard_limit']

        # Use Redis atomic operations to check and increment
        lock_key = f"{self.CAPACITY_LOCK_PREFIX}:{user_type.value}"

        # Try to acquire a capacity slot atomically
        success = await self._try_acquire_capacity_slot(user_type, threshold, thresholds)

        if not success:
            # Determine which threshold was hit
            current_total = await self._get_total_queue_size()

            if current_total >= thresholds['hard_limit']:
                reason = ThrottlingReason.SYSTEM_BACKPRESSURE
                message = f"System at hard limit ({current_total}/{thresholds['hard_limit']})"
                retry_after = 60
            elif current_total >= thresholds['paid_threshold'] and user_type != UserType.PRIVILEGED:
                reason = ThrottlingReason.PAID_BACKPRESSURE
                message = f"System under high pressure - privileged users only ({current_total}/{thresholds['paid_threshold']})"
                retry_after = 45
            elif current_total >= thresholds['registered_threshold'] and user_type in (UserType.ANONYMOUS, UserType.REGISTERED):
                reason = ThrottlingReason.REGISTERED_BACKPRESSURE
                message = f"System under pressure - paid users only ({current_total}/{thresholds['registered_threshold']})"
                retry_after = 30
            else:  # Anonymous threshold
                reason = ThrottlingReason.SYSTEM_BACKPRESSURE
                message = f"System under pressure - registered users only ({current_total}/{thresholds['anonymous_threshold']})"
                retry_after = 30

            # Record and raise backpressure error
            await self._record_and_raise_backpressure(
                reason, session, context, endpoint, message,
                {
                    'current_total': current_total,
                    'thresholds': thresholds,
                    'user_type': user_type.value
                },
                retry_after
            )

        # Record queue analytics for successful admission
        await self.update_queue_analytics(user_type.value.lower())

    async def _try_acquire_capacity_slot(self, user_type: UserType, threshold: int, thresholds: Dict[str, int]) -> bool:
        """Atomically try to acquire a capacity slot"""

        # Use Lua script for atomic check-and-increment
        lua_script = """
        local user_queue_key = KEYS[1]
        local total_counter_key = KEYS[2]
        local user_inflight_key = KEYS[3]
        local threshold = tonumber(ARGV[1])
        local hard_limit = tonumber(ARGV[2])
        local user_type = ARGV[3]
        
        -- Get current queue sizes
        local user_queue_size = redis.call('LLEN', user_queue_key) + redis.call('LLEN', user_inflight_key)
        local total_size = 0
        
        -- Calculate total across all queues
        local anonymous_size = redis.call('LLEN', KEYS[4]) + redis.call('LLEN', KEYS[8])
        local registered_size = redis.call('LLEN', KEYS[5]) + redis.call('LLEN', KEYS[9])
        local privileged_size = redis.call('LLEN', KEYS[6]) + redis.call('LLEN', KEYS[10])
        local paid_size = redis.call('LLEN', KEYS[7]) + redis.call('LLEN', KEYS[11])
        total_size = anonymous_size + registered_size + paid_size + privileged_size
        
        -- Check if we can admit this request
        local can_admit = false
        
        if user_type == 'privileged' then
            can_admit = total_size < hard_limit
        elseif user_type == 'registered' or user_type == 'paid' then
            can_admit = total_size < threshold
        else  -- anonymous
            can_admit = total_size < threshold
        end
        
        if can_admit then
            -- Atomically reserve the slot by incrementing a counter
            redis.call('INCR', total_counter_key)
            redis.call('EXPIRE', total_counter_key, 300)  -- 5 minute expiry
            return {1, total_size, user_queue_size}  -- Success
        else
            return {0, total_size, user_queue_size}  -- Rejected
        end
        """

        # Prepare Redis keys
        user_queue_key = f"{self.QUEUE_PREFIX}:{user_type.value.lower()}"
        user_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:{user_type.value.lower()}"
        total_counter_key = f"{self.CAPACITY_COUNTER_KEY}:total"
        anonymous_queue_key = f"{self.QUEUE_PREFIX}:anonymous"
        registered_queue_key = f"{self.QUEUE_PREFIX}:registered"
        privileged_queue_key = f"{self.QUEUE_PREFIX}:privileged"
        paid_queue_key = f"{self.QUEUE_PREFIX}:paid"
        anonymous_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:anonymous"
        registered_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:registered"
        privileged_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:privileged"
        paid_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:paid"

        try:
            # Execute atomic check
            result = await self.redis.eval(
                lua_script,
                11,  # Number of keys
                user_queue_key,
                total_counter_key,
                user_inflight_key,
                anonymous_queue_key,
                registered_queue_key,
                privileged_queue_key,
                paid_queue_key,
                anonymous_inflight_key,
                registered_inflight_key,
                privileged_inflight_key,
                paid_inflight_key,
                threshold,
                thresholds['hard_limit'],
                user_type.value.lower()
            )

            success = bool(result[0])
            total_size = result[1]
            user_queue_size = result[2]

            # Log the decision for debugging
            logger.debug(
                f"Capacity check: user_type={user_type.value}, "
                f"success={success}, total_size={total_size}, "
                f"threshold={threshold}, user_queue_size={user_queue_size}"
            )

            return success

        except Exception as e:
            logger.error(f"Error in atomic capacity check: {e}")
            # Fallback to non-atomic check (safer to allow than block incorrectly)
            return await self._fallback_capacity_check(user_type, threshold, thresholds)

    async def _fallback_capacity_check(self, user_type: UserType, threshold: int, thresholds: Dict[str, int]) -> bool:
        """Fallback non-atomic capacity check"""
        try:
            total_size = await self._get_total_queue_size()

            if user_type == UserType.PRIVILEGED:
                return total_size < thresholds['hard_limit']
            elif user_type in (UserType.REGISTERED, UserType.PAID):
                return total_size < threshold
            else:  # ANONYMOUS
                return total_size < threshold

        except Exception as e:
            logger.error(f"Error in fallback capacity check: {e}")
            # When in doubt, reject to prevent overload
            return False

    async def _get_total_queue_size(self) -> int:
        """Get total queue size across all user types"""
        queue_sizes = await self.get_individual_queue_sizes()
        return sum(queue_sizes.values())

    async def release_capacity_slot(self, user_type: UserType):
        """Release a capacity slot when request processing completes"""
        try:
            total_counter_key = f"{self.CAPACITY_COUNTER_KEY}:total"
            await self.redis.decr(total_counter_key)
        except Exception as e:
            logger.error(f"Error releasing capacity slot: {e}")

    # Update the main check_capacity method to use the atomic version
    async def check_capacity(self,
                             user_type: UserType,
                             session: UserSession,
                             context: RequestContext,
                             endpoint: str) -> None:
        """Check if system can accept new requests (now atomic)"""
        return await self.check_capacity_atomic(user_type, session, context, endpoint)

class AtomicBackpressureManager:
    """
    Drop-in replacement for BackpressureManager that uses atomic operations
    Integrates with existing throttling monitor and circuit breaker machinery
    """

    def __init__(self, redis_url: str, gateway_config: GatewayConfiguration, monitor):
        self.redis_url = redis_url
        self.redis = None
        self.gateway_config = gateway_config
        self.config = gateway_config.backpressure_config_obj  # Keep compatibility
        self.monitor = monitor

        # Dynamic capacity calculator (will be injected like in original)
        self.capacity_calculator = None

        # Redis keys (keep same as existing)
        self.QUEUE_PREFIX = self.ns(REDIS.CHAT.PROMPT_QUEUE_PREFIX)
        self.QUEUE_INFLIGHT_PREFIX = self.ns(REDIS.CHAT.PROMPT_QUEUE_INFLIGHT_PREFIX)
        self.QUEUE_CONTINUATION_COUNT_PREFIX = self.ns(REDIS.CHAT.CONVERSATION_MAILBOX_COUNT_PREFIX)
        self.PROCESS_HEARTBEAT_PREFIX = self.ns(REDIS.PROCESS.HEARTBEAT_PREFIX)
        self.INSTANCE_STATUS_PREFIX = self.ns(REDIS.INSTANCE.HEARTBEAT_PREFIX)
        self.CAPACITY_COUNTER_KEY = self.ns(f"{REDIS.SYSTEM.CAPACITY}:counter")

        # Queue analytics keys
        self.QUEUE_ANALYTICS_PREFIX = self.ns(f"{REDIS.CHAT.PROMPT_QUEUE_PREFIX}:analytics")

        # Lua script for atomic capacity check (without enqueueing)
        self.ATOMIC_CAPACITY_CHECK_SCRIPT = """
        local anon_queue_key = KEYS[1]
        local reg_queue_key = KEYS[2]
        local priv_queue_key = KEYS[3]
        local paid_queue_key = KEYS[4]
        local anon_inflight_key = KEYS[5]
        local reg_inflight_key = KEYS[6]
        local priv_inflight_key = KEYS[7]
        local paid_inflight_key = KEYS[8]
        local anon_cont_key = KEYS[9]
        local reg_cont_key = KEYS[10]
        local priv_cont_key = KEYS[11]
        local paid_cont_key = KEYS[12]
        
        local user_type = ARGV[1]
        local anonymous_ratio = tonumber(ARGV[2])
        local registered_ratio = tonumber(ARGV[3])
        local paid_ratio = tonumber(ARGV[4])
        local hard_ratio = tonumber(ARGV[5])
        local capacity_buffer = tonumber(ARGV[6])
        local queue_depth_multiplier = tonumber(ARGV[7])
        local heartbeat_timeout = tonumber(ARGV[8])
        local current_time = tonumber(ARGV[9])
        local heartbeat_pattern = ARGV[10]
        local target_service_type = ARGV[11]
        local target_service_name = ARGV[12]
        
        -- Count healthy capacity-source processes and aggregate capacity
        local heartbeat_keys = redis.call('KEYS', heartbeat_pattern)
        local healthy_processes = 0
        local actual_capacity = 0
        
        for i, key in ipairs(heartbeat_keys) do
            local heartbeat_data = redis.call('GET', key)
            if heartbeat_data then
                local success, heartbeat = pcall(cjson.decode, heartbeat_data)
                if success and heartbeat then
                    if heartbeat.service_type == target_service_type and heartbeat.service_name == target_service_name then
                        local age = current_time - (heartbeat.last_heartbeat or 0)
                        local is_healthy = (heartbeat.health_status == "healthy" or 
                                          heartbeat.health_status == "HEALTHY" or
                                          string.find(tostring(heartbeat.health_status), "HEALTHY"))
                        if age <= heartbeat_timeout and is_healthy then
                            healthy_processes = healthy_processes + 1
                            local max_cap = tonumber(heartbeat.max_capacity) or 0
                            local effective = math.floor(max_cap * (1 - capacity_buffer))
                            local queue_cap = math.floor(max_cap * queue_depth_multiplier)
                            actual_capacity = actual_capacity + effective + queue_cap
                        end
                    end
                end
            end
        end
        
        -- Get current queue sizes
        local anon_queue = redis.call('LLEN', anon_queue_key) + redis.call('LLEN', anon_inflight_key) + tonumber(redis.call('GET', anon_cont_key) or '0')
        local reg_queue = redis.call('LLEN', reg_queue_key) + redis.call('LLEN', reg_inflight_key) + tonumber(redis.call('GET', reg_cont_key) or '0')
        local priv_queue = redis.call('LLEN', priv_queue_key) + redis.call('LLEN', priv_inflight_key) + tonumber(redis.call('GET', priv_cont_key) or '0')
        local paid_queue = redis.call('LLEN', paid_queue_key) + redis.call('LLEN', paid_inflight_key) + tonumber(redis.call('GET', paid_cont_key) or '0')
        local total_queue = anon_queue + reg_queue + paid_queue + priv_queue
        
        if actual_capacity <= 0 then
            return {0, "no_healthy_processes", total_queue, 0, healthy_processes}
        end
        
        -- Calculate dynamic thresholds based on actual capacity
        local anon_threshold = math.floor(actual_capacity * anonymous_ratio)
        local reg_threshold = math.floor(actual_capacity * registered_ratio)
        local paid_threshold_val = math.floor(actual_capacity * paid_ratio)
        local hard_threshold = math.floor(actual_capacity * hard_ratio)
        
        -- Check if request can be admitted (without enqueueing)
        local can_admit = false
        local rejection_reason = ""
        
        if user_type == "privileged" then
            can_admit = total_queue < hard_threshold
            rejection_reason = total_queue >= hard_threshold and "hard_limit_exceeded" or ""
        elseif user_type == "registered" then
            can_admit = total_queue < reg_threshold  
            rejection_reason = total_queue >= reg_threshold and "registered_threshold_exceeded" or ""
        elseif user_type == "paid" then
            can_admit = total_queue < paid_threshold_val
            rejection_reason = total_queue >= paid_threshold_val and "paid_threshold_exceeded" or ""
        else -- anonymous
            can_admit = total_queue < anon_threshold
            rejection_reason = total_queue >= anon_threshold and "anonymous_threshold_exceeded" or ""
        end
        
        if can_admit then
            return {1, "admitted", total_queue, actual_capacity, healthy_processes}
        else
            return {0, rejection_reason, total_queue, actual_capacity, healthy_processes}
        end
        """

    def ns(self, base: str) -> str:
        return ns_key(base, tenant=self.gateway_config.tenant_id, project=self.gateway_config.project_id)

    async def init_redis(self):
        if not self.redis:
            self.redis = get_async_redis_client(self.redis_url)

    def set_capacity_calculator(self, calculator):
        """Inject dynamic capacity calculator (same interface as original)"""
        self.capacity_calculator = calculator

    async def check_capacity(self,
                             user_type: UserType,
                             session: UserSession,
                             context: RequestContext,
                             endpoint: str) -> None:
        """
        Atomic capacity check (gateway-level check) - DOES NOT enqueue tasks
        This is the immediate backpressure check in the gateway
        """
        await self.init_redis()

        # Perform atomic capacity check (without enqueueing)
        success, reason, stats = await self._atomic_capacity_check(user_type)

        if not success:
            # Record throttling event and raise error
            await self._record_and_raise_backpressure(
                reason, session, context, endpoint, stats
            )

        # Record queue analytics for successful admission
        await self.update_queue_analytics(user_type.value.lower())

    async def _atomic_capacity_check(self, user_type: UserType) -> Tuple[bool, str, Dict[str, Any]]:
        """Perform atomic capacity check without enqueueing"""
        anon_queue_key = f"{self.QUEUE_PREFIX}:anonymous"
        reg_queue_key = f"{self.QUEUE_PREFIX}:registered"
        priv_queue_key = f"{self.QUEUE_PREFIX}:privileged"
        paid_queue_key = f"{self.QUEUE_PREFIX}:paid"
        anon_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:anonymous"
        reg_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:registered"
        priv_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:privileged"
        paid_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:paid"

        heartbeat_pattern = f"{self.PROCESS_HEARTBEAT_PREFIX}:*"
        service_type, service_name = self.gateway_config.capacity_source_selector()
        bp = self.gateway_config.backpressure
        try:
            result = await self.redis.eval(
                self.ATOMIC_CAPACITY_CHECK_SCRIPT,
                12,  # Number of keys
                anon_queue_key,
                reg_queue_key,
                priv_queue_key,
                paid_queue_key,
                anon_inflight_key,
                reg_inflight_key,
                priv_inflight_key,
                paid_inflight_key,
                f"{self.QUEUE_CONTINUATION_COUNT_PREFIX}:anonymous",
                f"{self.QUEUE_CONTINUATION_COUNT_PREFIX}:registered",
                f"{self.QUEUE_CONTINUATION_COUNT_PREFIX}:privileged",
                f"{self.QUEUE_CONTINUATION_COUNT_PREFIX}:paid",
                # Arguments
                user_type.value,
                str(bp.anonymous_pressure_threshold),
                str(bp.registered_pressure_threshold),
                str(bp.paid_pressure_threshold),
                str(bp.hard_limit_threshold),
                str(bp.capacity_buffer),
                str(bp.queue_depth_multiplier),
                str(self.gateway_config.monitoring.heartbeat_timeout_seconds),
                str(time.time()),
                heartbeat_pattern,
                service_type,
                service_name,
            )

            success = bool(result[0])
            reason = result[1]
            reason = reason.decode('utf-8') if reason and isinstance(reason, bytes) else reason
            current_queue_size = result[2]
            actual_capacity = int(result[3])
            healthy_processes = result[4] if len(result) > 4 else 0
            theoretical_thresholds = self.gateway_config.get_thresholds_for_actual_capacity(actual_capacity)

            stats = {
                "current_queue_size": current_queue_size,
                "actual_capacity": actual_capacity,
                "healthy_processes": healthy_processes,
                "configured_capacity": self.gateway_config.total_capacity_per_instance,
                "theoretical_thresholds": theoretical_thresholds,
                "user_type": user_type.value,
                "check_type": "gateway_immediate",
                "gateway_config": {
                    "profile": self.gateway_config.profile.value,
                    "instance_id": self.gateway_config.instance_id
                }
            }

            if success:
                logger.info(f"Gateway capacity check passed: {user_type.value}, queue={current_queue_size}/{actual_capacity}")
            else:
                logger.warning(f"Gateway capacity check failed: {user_type.value}, reason={reason}, queue={current_queue_size}/{actual_capacity}")

            return success, reason, stats

        except Exception as e:
            logger.error(f"Atomic capacity check failed: {e}")
            return False, f"capacity_check_error: {str(e)}", {}

    async def _record_and_raise_backpressure(self, reason: str, session: UserSession,
                                             context: RequestContext, endpoint: str,
                                             stats: Dict[str, Any]):
        """Record throttling event and raise appropriate error"""
        from kdcube_ai_app.infra.gateway.thorttling import ThrottlingReason

        # Determine throttling reason and retry time
        if "hard_limit" in reason:
            throttling_reason = ThrottlingReason.SYSTEM_BACKPRESSURE
            retry_after = 60
            message = f"System at hard limit ({stats.get('current_queue_size', 0)}/{stats.get('actual_capacity', 0)})"
        elif "paid_threshold" in reason:
            throttling_reason = ThrottlingReason.PAID_BACKPRESSURE
            retry_after = 45
            message = (
                "System under high pressure - privileged users only "
                f"({stats.get('current_queue_size', 0)}/{stats.get('actual_capacity', 0)})"
            )
        elif "registered_threshold" in reason:
            throttling_reason = ThrottlingReason.REGISTERED_BACKPRESSURE
            retry_after = 45
            message = (
                "System under pressure - paid users only "
                f"({stats.get('current_queue_size', 0)}/{stats.get('actual_capacity', 0)})"
            )
        elif "anonymous_threshold" in reason:
            throttling_reason = ThrottlingReason.ANONYMOUS_BACKPRESSURE
            retry_after = 30
            message = f"System under pressure - registered users only ({stats.get('current_queue_size', 0)}/{stats.get('actual_capacity', 0)})"
        else:
            throttling_reason = ThrottlingReason.SYSTEM_BACKPRESSURE
            retry_after = 30
            message = f"System capacity exceeded: {reason}"

        # Record throttling event
        await self.monitor.record_throttling_event(
            reason=throttling_reason,
            session=session,
            context=context,
            endpoint=endpoint,
            retry_after=retry_after,
            additional_data={'atomic_backpressure_stats': stats}
        )

        raise BackpressureError(message, retry_after, session=session)

    # Keep all existing methods for compatibility with monitoring and capacity calculator
    async def get_alive_instances(self):
        """Keep existing implementation"""
        current_time = time.time()
        await self.init_redis()

        patterns = [
            f"{self.INSTANCE_STATUS_PREFIX}:*",
            f"{self.PROCESS_HEARTBEAT_PREFIX}:*"
        ]

        alive_instances = set()

        for pattern in patterns:
            keys = await self.redis.keys(pattern)

            for key in keys:
                try:
                    key_parts = key.decode().split(':')
                    if len(key_parts) >= 4:
                        instance_id = key_parts[3]

                        data = await self.redis.get(key)
                        if data:
                            try:
                                heartbeat_data = json.loads(data)
                                last_heartbeat = heartbeat_data.get('last_heartbeat', 0)
                                heartbeat_timeout = self.gateway_config.monitoring.heartbeat_timeout_seconds
                                if current_time - last_heartbeat <= heartbeat_timeout:
                                    alive_instances.add(instance_id)
                            except (json.JSONDecodeError, KeyError):
                                continue
                except Exception as e:
                    logger.debug(f"Error processing heartbeat key {key}: {e}")
                    continue

        return alive_instances

    async def get_individual_queue_sizes(self) -> Dict[str, int]:
        """Get individual queue sizes"""
        await self.init_redis()
        return await _get_combined_queue_sizes(
            self.redis,
            self.QUEUE_PREFIX,
            self.QUEUE_INFLIGHT_PREFIX,
            self.QUEUE_CONTINUATION_COUNT_PREFIX,
        )

    async def get_queue_analytics(self) -> Dict[str, Dict[str, Any]]:
        """Get queue analytics for each user type"""
        if not self.gateway_config.monitoring.queue_analytics_enabled:
            return {}

        await self.init_redis()

        analytics = {}
        for user_type in ["anonymous", "registered", "privileged", "paid"]:
            analytics_key = f"{self.QUEUE_ANALYTICS_PREFIX}:{user_type}"

            data = await self.redis.get(analytics_key)
            if data:
                try:
                    analytics[user_type] = json.loads(data)
                except json.JSONDecodeError:
                    analytics[user_type] = self._default_analytics()
            else:
                analytics[user_type] = self._default_analytics()

        return analytics

    def _default_analytics(self) -> Dict[str, Any]:
        """Default analytics structure"""
        return {
            "avg_wait_time": 0.0,
            "processed_today": 0,
            "peak_size_today": 0,
            "last_updated": time.time(),
            "throughput_last_hour": 0,
            "total_processed": 0
        }

    async def update_queue_analytics(self, user_type: str, wait_time: float = None, processed: bool = False):
        """Update queue analytics"""
        if not self.gateway_config.monitoring.queue_analytics_enabled:
            return

        await self.init_redis()

        analytics_key = f"{self.QUEUE_ANALYTICS_PREFIX}:{user_type}"

        data = await self.redis.get(analytics_key)
        if data:
            try:
                analytics = json.loads(data)
            except json.JSONDecodeError:
                analytics = self._default_analytics()
        else:
            analytics = self._default_analytics()

        # Update analytics
        if wait_time is not None:
            current_avg = analytics.get("avg_wait_time", 0)
            total_processed = analytics.get("total_processed", 0)
            new_avg = ((current_avg * total_processed) + wait_time) / (total_processed + 1)
            analytics["avg_wait_time"] = new_avg

        if processed:
            analytics["processed_today"] += 1
            analytics["total_processed"] += 1
            analytics["throughput_last_hour"] += 1

        current_size = (
            await self.redis.llen(_queue_key(self.QUEUE_PREFIX, user_type))
            + await self.redis.llen(_queue_key(self.QUEUE_INFLIGHT_PREFIX, user_type))
        )
        analytics["peak_size_today"] = max(analytics.get("peak_size_today", 0), current_size)
        analytics["last_updated"] = time.time()

        await self.redis.setex(analytics_key, self.gateway_config.redis.analytics_ttl, json.dumps(analytics, default=str, ensure_ascii=False))

    async def get_queue_stats(self):
        """Enhanced queue stats - uses capacity calculator if available, otherwise atomic calculation"""
        await self.init_redis()

        if self.capacity_calculator:
            # Use existing capacity calculator for full compatibility
            queue_sizes = await self.get_individual_queue_sizes()
            total_size = sum(queue_sizes.values())

            alive_instances = await self.get_alive_instances()
            instance_count = max(len(alive_instances), 1)

            base_capacity = await self.capacity_calculator.get_base_queue_size_per_instance()
            actual_processes_data = await self.capacity_calculator.get_actual_process_info()
            thresholds = await self.capacity_calculator.get_capacity_thresholds(actual_processes_data)
            weighted_capacity = thresholds["total_capacity"]

            pressure_ratio = total_size / weighted_capacity if weighted_capacity > 0 else 1.0

            accepting_anonymous = total_size < thresholds['anonymous_threshold']
            accepting_registered = total_size < thresholds['registered_threshold']
            accepting_paid = total_size < thresholds['paid_threshold']
            accepting_privileged = total_size < thresholds['hard_limit']

            analytics = await self.get_queue_analytics()

            from kdcube_ai_app.infra.gateway.definitions import QueueStats

            return QueueStats(
                anonymous_queue=queue_sizes['anonymous'],
                registered_queue=queue_sizes['registered'],
                paid_queue=queue_sizes.get('paid', 0),
                privileged_queue=queue_sizes['privileged'],
                total_queue=total_size,
                base_capacity_per_instance=base_capacity,
                alive_instances=list(alive_instances),
                instance_count=instance_count,
                weighted_max_capacity=weighted_capacity,
                pressure_ratio=pressure_ratio,
                accepting_anonymous=accepting_anonymous,
                accepting_registered=accepting_registered,
                accepting_paid=accepting_paid,
                accepting_privileged=accepting_privileged,
                anonymous_threshold=thresholds['anonymous_threshold'],
                registered_threshold=thresholds['registered_threshold'],
                paid_threshold=thresholds['paid_threshold'],
                hard_limit_threshold=thresholds['hard_limit'],
                avg_wait_times={
                    user_type: data.get('avg_wait_time', 0)
                    for user_type, data in analytics.items()
                } if analytics else {},
                throughput_metrics={
                    user_type: data.get('throughput_last_hour', 0)
                    for user_type, data in analytics.items()
                } if analytics else {}
            )
        else:
            # Fallback to atomic calculation
            return await self._get_queue_stats_atomic()

    async def _get_queue_stats_atomic(self):
        """Fallback queue stats using atomic calculation"""
        queue_sizes = await self.get_individual_queue_sizes()
        total_size = sum(queue_sizes.values())

        alive_instances = await self.get_alive_instances()
        instance_count = max(len(alive_instances), 1)

        healthy_processes, actual_capacity = await self._get_capacity_from_heartbeats()
        actual_thresholds = self.gateway_config.get_thresholds_for_actual_capacity(actual_capacity)

        pressure_ratio = total_size / actual_capacity if actual_capacity > 0 else 1.0

        accepting_anonymous = total_size < actual_thresholds['anonymous_threshold']
        accepting_registered = total_size < actual_thresholds['registered_threshold']
        accepting_paid = total_size < actual_thresholds['paid_threshold']
        accepting_privileged = total_size < actual_thresholds['hard_limit']

        analytics = await self.get_queue_analytics()

        from kdcube_ai_app.infra.gateway.definitions import QueueStats

        return QueueStats(
            anonymous_queue=queue_sizes['anonymous'],
            registered_queue=queue_sizes['registered'],
            paid_queue=queue_sizes.get('paid', 0),
            privileged_queue=queue_sizes['privileged'],
            total_queue=total_size,
            base_capacity_per_instance=self.gateway_config.total_capacity_per_instance,
            alive_instances=list(alive_instances),
            instance_count=instance_count,
            weighted_max_capacity=actual_capacity,
            pressure_ratio=pressure_ratio,
            accepting_anonymous=accepting_anonymous,
            accepting_registered=accepting_registered,
            accepting_paid=accepting_paid,
            accepting_privileged=accepting_privileged,
            anonymous_threshold=actual_thresholds['anonymous_threshold'],
            registered_threshold=actual_thresholds['registered_threshold'],
            paid_threshold=actual_thresholds['paid_threshold'],
            hard_limit_threshold=actual_thresholds['hard_limit'],
            avg_wait_times={
                user_type: data.get('avg_wait_time', 0)
                for user_type, data in analytics.items()
            } if analytics else {},
            throughput_metrics={
                user_type: data.get('throughput_last_hour', 0)
                for user_type, data in analytics.items()
            } if analytics else {}
        )

    async def _get_capacity_from_heartbeats(self) -> Tuple[int, int]:
        """Aggregate healthy process count and capacity from heartbeats."""
        try:
            pattern = f"{self.PROCESS_HEARTBEAT_PREFIX}:*"
            keys = await self.redis.keys(pattern)
            healthy_count = 0
            current_time = time.time()
            actual_capacity = 0
            service_type, service_name = self.gateway_config.capacity_source_selector()

            for key in keys:
                try:
                    data = await self.redis.get(key)
                    if not data:
                        continue

                    heartbeat = json.loads(data)
                    if (heartbeat.get("service_type") == service_type and
                            heartbeat.get("service_name") == service_name):

                        age = current_time - heartbeat.get("last_heartbeat", 0)
                        health_status = str(heartbeat.get("health_status", "")).upper()
                        is_healthy = "HEALTHY" in health_status

                        if age <= self.gateway_config.monitoring.heartbeat_timeout_seconds and is_healthy:
                            healthy_count += 1
                            max_cap = int(heartbeat.get("max_capacity") or 0)
                            effective = int(max_cap * (1 - self.gateway_config.backpressure.capacity_buffer))
                            queue_cap = int(max_cap * self.gateway_config.backpressure.queue_depth_multiplier)
                            actual_capacity += effective + queue_cap

                except Exception as e:
                    logger.debug(f"Error parsing heartbeat {key}: {e}")
                    continue

            if actual_capacity <= 0:
                actual_capacity = self.gateway_config.total_capacity_per_instance * max(healthy_count, 1)

            return max(healthy_count, 1), actual_capacity

        except Exception as e:
            logger.error(f"Error counting healthy processes: {e}")
            return 1, self.gateway_config.total_capacity_per_instance  # Fallback

    async def release_capacity_slot(self):
        """Release a capacity slot when request processing completes"""
        try:
            await self.redis.decr(self.CAPACITY_COUNTER_KEY)
        except Exception as e:
            logger.error(f"Error releasing capacity slot: {e}")

class AtomicChatQueueManager:
    """
    Atomic chat queue manager for the actual chat endpoint
    This is the "by fact" backpressure that happens during actual enqueueing
    """

    def __init__(self, redis_url: str, gateway_config: GatewayConfiguration, monitor):
        self.redis_url = redis_url
        self.redis = None
        self.gateway_config = gateway_config
        self.monitor = monitor
        self.max_queue_size = int(getattr(gateway_config.limits, "max_queue_size", 0) or 0)

        # Redis keys
        self.QUEUE_PREFIX = self.ns(REDIS.CHAT.PROMPT_QUEUE_PREFIX)
        self.QUEUE_INFLIGHT_PREFIX = self.ns(REDIS.CHAT.PROMPT_QUEUE_INFLIGHT_PREFIX)
        self.PROCESS_HEARTBEAT_PREFIX = self.ns(REDIS.PROCESS.HEARTBEAT_PREFIX)
        self.CAPACITY_COUNTER_KEY = self.ns(f"{REDIS.SYSTEM.CAPACITY}:counter")

        # Lua script for atomic capacity check and task enqueue
        self.ATOMIC_CHAT_ENQUEUE_SCRIPT = """
        local queue_key = KEYS[1]
        local capacity_counter_key = KEYS[2]
        local anon_queue_key = KEYS[3] 
        local reg_queue_key = KEYS[4]
        local priv_queue_key = KEYS[5]
        local paid_queue_key = KEYS[6]
        local anon_inflight_key = KEYS[7]
        local reg_inflight_key = KEYS[8]
        local priv_inflight_key = KEYS[9]
        local paid_inflight_key = KEYS[10]
        local anon_cont_key = KEYS[11]
        local reg_cont_key = KEYS[12]
        local priv_cont_key = KEYS[13]
        local paid_cont_key = KEYS[14]
        
        local user_type = ARGV[1]
        local chat_task_json = ARGV[2]  -- Actual chat task
        local anonymous_ratio = tonumber(ARGV[3])
        local registered_ratio = tonumber(ARGV[4])
        local paid_ratio = tonumber(ARGV[5])
        local hard_ratio = tonumber(ARGV[6])
        local capacity_buffer = tonumber(ARGV[7])
        local queue_depth_multiplier = tonumber(ARGV[8])
        local heartbeat_timeout = tonumber(ARGV[9])
        local current_time = tonumber(ARGV[10])
        local heartbeat_pattern = ARGV[11]
        local max_queue_size = tonumber(ARGV[12])
        local target_service_type = ARGV[13]
        local target_service_name = ARGV[14]
        
        -- Count healthy capacity-source processes
        local heartbeat_keys = redis.call('KEYS', heartbeat_pattern)
        local healthy_processes = 0
        local actual_capacity = 0
        
        for i, key in ipairs(heartbeat_keys) do
            local heartbeat_data = redis.call('GET', key)
            if heartbeat_data then
                local success, heartbeat = pcall(cjson.decode, heartbeat_data)
                if success and heartbeat then
                    if heartbeat.service_type == target_service_type and heartbeat.service_name == target_service_name then
                        local age = current_time - (heartbeat.last_heartbeat or 0)
                        local is_healthy = (heartbeat.health_status == "healthy" or 
                                          heartbeat.health_status == "HEALTHY" or
                                          string.find(tostring(heartbeat.health_status), "HEALTHY"))
                        if age <= heartbeat_timeout and is_healthy then
                            healthy_processes = healthy_processes + 1
                            local max_cap = tonumber(heartbeat.max_capacity) or 0
                            local effective = math.floor(max_cap * (1 - capacity_buffer))
                            local queue_cap = math.floor(max_cap * queue_depth_multiplier)
                            actual_capacity = actual_capacity + effective + queue_cap
                        end
                    end
                end
            end
        end
        
        -- Get current queue sizes
        local anon_queue = redis.call('LLEN', anon_queue_key) + redis.call('LLEN', anon_inflight_key) + tonumber(redis.call('GET', anon_cont_key) or '0')
        local reg_queue = redis.call('LLEN', reg_queue_key) + redis.call('LLEN', reg_inflight_key) + tonumber(redis.call('GET', reg_cont_key) or '0')
        local priv_queue = redis.call('LLEN', priv_queue_key) + redis.call('LLEN', priv_inflight_key) + tonumber(redis.call('GET', priv_cont_key) or '0')
        local paid_queue = redis.call('LLEN', paid_queue_key) + redis.call('LLEN', paid_inflight_key) + tonumber(redis.call('GET', paid_cont_key) or '0')
        local total_queue = anon_queue + reg_queue + paid_queue + priv_queue

        if actual_capacity <= 0 then
            return {0, "no_healthy_processes", total_queue, 0, healthy_processes}
        end

        if max_queue_size and max_queue_size > 0 then
            if total_queue >= max_queue_size then
                return {0, "queue_size_exceeded", total_queue, actual_capacity, healthy_processes}
            end
        end
        
        -- Calculate dynamic thresholds
        local anon_threshold = math.floor(actual_capacity * anonymous_ratio)
        local reg_threshold = math.floor(actual_capacity * registered_ratio)
        local paid_threshold_val = math.floor(actual_capacity * paid_ratio)
        local hard_threshold = math.floor(actual_capacity * hard_ratio)
        
        -- Check if chat request can be admitted
        local can_admit = false
        local rejection_reason = ""
        
        if user_type == "privileged" then
            can_admit = total_queue < hard_threshold
            rejection_reason = total_queue >= hard_threshold and "hard_limit_exceeded" or ""
        elseif user_type == "registered" then
            can_admit = total_queue < reg_threshold
            rejection_reason = total_queue >= reg_threshold and "registered_threshold_exceeded" or ""
        elseif user_type == "paid" then
            can_admit = total_queue < paid_threshold_val
            rejection_reason = total_queue >= paid_threshold_val and "paid_threshold_exceeded" or ""
        else -- anonymous
            can_admit = total_queue < anon_threshold
            rejection_reason = total_queue >= anon_threshold and "anonymous_threshold_exceeded" or ""
        end
        
        if can_admit then
            -- Atomically add ACTUAL chat task to queue
            redis.call('LPUSH', queue_key, chat_task_json)
            redis.call('INCR', capacity_counter_key)
            redis.call('EXPIRE', capacity_counter_key, 300)
            
            return {1, "admitted", total_queue + 1, actual_capacity, healthy_processes}
        else
            return {0, rejection_reason, total_queue, actual_capacity, healthy_processes}
        end
        """

    def ns(self, base: str) -> str:
        return ns_key(base, tenant=self.gateway_config.tenant_id, project=self.gateway_config.project_id)

    async def init_redis(self):
        if not self.redis:
            self.redis = get_async_redis_client(self.redis_url)

    async def enqueue_chat_task_atomic(self,
                                       user_type: UserType,
                                       chat_task_data: Dict[str, Any],
                                       session: UserSession,
                                       context: RequestContext,
                                       endpoint: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Atomically check capacity and enqueue ACTUAL chat task
        This is the "by fact" backpressure check that counts for circuit breakers
        """

        await self.init_redis()

        queue_key = f"{self.QUEUE_PREFIX}:{user_type.value}"
        capacity_counter_key = self.CAPACITY_COUNTER_KEY
        anon_queue_key = f"{self.QUEUE_PREFIX}:anonymous"
        reg_queue_key = f"{self.QUEUE_PREFIX}:registered"
        priv_queue_key = f"{self.QUEUE_PREFIX}:privileged"
        paid_queue_key = f"{self.QUEUE_PREFIX}:paid"
        anon_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:anonymous"
        reg_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:registered"
        priv_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:privileged"
        paid_inflight_key = f"{self.QUEUE_INFLIGHT_PREFIX}:paid"

        heartbeat_pattern = f"{self.PROCESS_HEARTBEAT_PREFIX}:*"
        service_type, service_name = self.gateway_config.capacity_source_selector()
        bp = self.gateway_config.backpressure
        try:
            result = await self.redis.eval(
                self.ATOMIC_CHAT_ENQUEUE_SCRIPT,
                14,  # Number of keys
                queue_key,
                capacity_counter_key,
                anon_queue_key,
                reg_queue_key,
                priv_queue_key,
                paid_queue_key,
                anon_inflight_key,
                reg_inflight_key,
                priv_inflight_key,
                paid_inflight_key,
                f"{self.QUEUE_CONTINUATION_COUNT_PREFIX}:anonymous",
                f"{self.QUEUE_CONTINUATION_COUNT_PREFIX}:registered",
                f"{self.QUEUE_CONTINUATION_COUNT_PREFIX}:privileged",
                f"{self.QUEUE_CONTINUATION_COUNT_PREFIX}:paid",
                # Arguments
                user_type.value,
                json.dumps(chat_task_data, ensure_ascii=False),  # Your actual chat task
                str(bp.anonymous_pressure_threshold),
                str(bp.registered_pressure_threshold),
                str(bp.paid_pressure_threshold),
                str(bp.hard_limit_threshold),
                str(bp.capacity_buffer),
                str(bp.queue_depth_multiplier),
                str(self.gateway_config.monitoring.heartbeat_timeout_seconds),
                str(time.time()),
                heartbeat_pattern,
                str(self.max_queue_size),
                service_type,
                service_name,
            )

            success = bool(result[0])
            reason = result[1]
            current_queue_size = result[2]
            actual_capacity = int(result[3])
            healthy_processes = result[4] if len(result) > 4 else 0
            theoretical_thresholds = self.gateway_config.get_thresholds_for_actual_capacity(actual_capacity)

            stats = {
                "current_queue_size": current_queue_size,
                "actual_capacity": actual_capacity,
                "healthy_processes": healthy_processes,
                "configured_capacity": self.gateway_config.total_capacity_per_instance,
                "theoretical_thresholds": theoretical_thresholds,
                "user_type": user_type.value,
                "task_id": chat_task_data.get("task_id"),
                "check_type": "chat_enqueue_by_fact",
                "gateway_config": {
                    "profile": self.gateway_config.profile.value,
                    "instance_id": self.gateway_config.instance_id
                }
            }

            if success:
                logger.info(f"Chat task admitted atomically: {chat_task_data.get('task_id')} "
                            f"({user_type.value}), queue={current_queue_size}/{actual_capacity}")
            else:
                logger.warning(f"Chat task rejected atomically: {chat_task_data.get('task_id')} "
                               f"({user_type.value}), reason={reason}, queue={current_queue_size}/{actual_capacity}")

                # Record this rejection for circuit breaker machinery
                await self._record_chat_backpressure_rejection(reason, session, context, endpoint, stats)

            return success, reason, stats

        except Exception as e:
            logger.error(f"Atomic chat enqueue failed: {e}")
            return False, f"atomic_enqueue_error: {str(e)}", {}

    async def _record_chat_backpressure_rejection(self, reason: str, session: UserSession,
                                                  context: RequestContext, endpoint: str,
                                                  stats: Dict[str, Any]):
        """Record chat-level backpressure rejection for circuit breaker machinery"""
        from kdcube_ai_app.infra.gateway.thorttling import ThrottlingReason

        # Determine throttling reason and retry time
        if "hard_limit" in reason:
            throttling_reason = ThrottlingReason.SYSTEM_BACKPRESSURE
            retry_after = 60
        elif "paid_threshold" in reason:
            throttling_reason = ThrottlingReason.PAID_BACKPRESSURE
            retry_after = 45
        elif "registered_threshold" in reason:
            throttling_reason = ThrottlingReason.REGISTERED_BACKPRESSURE
            retry_after = 45
        elif "anonymous_threshold" in reason:
            throttling_reason = ThrottlingReason.ANONYMOUS_BACKPRESSURE
            retry_after = 30
        else:
            throttling_reason = ThrottlingReason.SYSTEM_BACKPRESSURE
            retry_after = 30

        # Record throttling event for circuit breaker machinery
        await self.monitor.record_throttling_event(
            reason=throttling_reason,
            session=session,
            context=context,
            endpoint=endpoint,
            retry_after=retry_after,
            additional_data={'chat_enqueue_backpressure_stats': stats}
        )

def create_atomic_backpressure_manager(redis_url: str, gateway_config: GatewayConfiguration, monitor) -> AtomicBackpressureManager:
    """Create atomic backpressure manager as drop-in replacement"""
    return AtomicBackpressureManager(redis_url, gateway_config, monitor)

def create_atomic_chat_queue_manager(redis_url: str, gateway_config: GatewayConfiguration, monitor) -> AtomicChatQueueManager:
    """Create atomic chat queue manager for chat endpoint"""
    return AtomicChatQueueManager(redis_url, gateway_config, monitor)
