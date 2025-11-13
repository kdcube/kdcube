# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/gateway/backpressure.py

import json
import time
import uuid
from typing import Set, Dict, Any, Tuple

from redis import asyncio as aioredis

from kdcube_ai_app.auth.sessions import UserSession, UserType, RequestContext
from kdcube_ai_app.infra.gateway.config import GatewayConfiguration
from kdcube_ai_app.infra.gateway.definitions import GatewayError, DynamicCapacityCalculator, QueueStats
import logging

from kdcube_ai_app.infra.gateway.thorttling import ThrottlingMonitor, ThrottlingReason
from kdcube_ai_app.infra.namespaces import REDIS

logger = logging.getLogger(__name__)

class BackpressureError(GatewayError):
    """System under pressure"""
    def __init__(self, message: str, retry_after: int = 60, session: UserSession = None):
        super().__init__(message, 503, retry_after, session)


class BackpressureManager:
    """Simple backpressure management"""

    def __init__(self, redis_url: str, gateway_config: GatewayConfiguration, monitor: ThrottlingMonitor):
        self.redis_url = redis_url
        self.redis = None
        self.gateway_config = gateway_config
        self.config = gateway_config.backpressure_config_obj
        self.monitor = monitor

        # Dynamic capacity calculator (will be injected)
        self.capacity_calculator = None

        self.QUEUE_PREFIX = REDIS.CHAT.PROMPT_QUEUE_PREFIX
        self.PROCESS_HEARTBEAT_PREFIX = REDIS.PROCESS.HEARTBEAT_PREFIX
        self.INSTANCE_STATUS_PREFIX = REDIS.INSTANCE.HEARTBEAT_PREFIX
        # For compatibility with both patterns
        self.PROCESS_HEARTBEAT_PREFIX = REDIS.PROCESS.HEARTBEAT_PREFIX

        # NEW: Atomic capacity tracking
        self.CAPACITY_COUNTER_KEY = f"{REDIS.SYSTEM.CAPACITY}:counter"
        self.CAPACITY_LOCK_PREFIX = f"{REDIS.SYNCHRONIZATION.LOCK}:capacity"

        # Queue analytics keys
        self.QUEUE_ANALYTICS_PREFIX = f"{REDIS.CHAT.PROMPT_QUEUE_PREFIX}:analytics"

        # Cache for performance
        self._last_instance_check = 0
        # self._cached_instances = set()
        # self._instance_cache_ttl = gateway_config.monitoring.instance_cache_ttl_seconds

    async def init_redis(self):
        if not self.redis:
            self.redis = aioredis.from_url(self.redis_url)

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

        queues = {
            "anonymous": f"{self.QUEUE_PREFIX}:anonymous",
            "registered": f"{self.QUEUE_PREFIX}:registered",
            "privileged": f"{self.QUEUE_PREFIX}:privileged"
        }

        sizes = {}
        for user_type, queue_key in queues.items():
            sizes[user_type] = await self.redis.llen(queue_key)

        return sizes

    async def get_queue_analytics(self) -> Dict[str, Dict[str, Any]]:
        """Get queue analytics for each user type"""
        if not self.gateway_config.monitoring.queue_analytics_enabled:
            return {}

        await self.init_redis()

        analytics = {}
        for user_type in ["anonymous", "registered", "privileged"]:
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
        current_size = await self.redis.llen(f"{self.QUEUE_PREFIX}:{user_type}")
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
        accepting_privileged = total_size < thresholds['hard_limit']

        # Get analytics
        analytics = await self.get_queue_analytics()

        return QueueStats(
            anonymous_queue=queue_sizes['anonymous'],
            registered_queue=queue_sizes['registered'],
            privileged_queue=queue_sizes['privileged'],
            total_queue=total_size,
            base_capacity_per_instance=base_capacity,
            alive_instances=list(alive_instances),
            instance_count=instance_count,
            weighted_max_capacity=weighted_capacity,
            pressure_ratio=pressure_ratio,
            accepting_anonymous=accepting_anonymous,
            accepting_registered=accepting_registered,
            accepting_privileged=accepting_privileged,
            anonymous_threshold=thresholds['anonymous_threshold'],
            registered_threshold=thresholds['registered_threshold'],
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
            elif current_total >= thresholds['registered_threshold'] and user_type != UserType.PRIVILEGED:
                reason = ThrottlingReason.REGISTERED_BACKPRESSURE
                message = f"System under high pressure - privileged users only ({current_total}/{thresholds['registered_threshold']})"
                retry_after = 45
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
        local threshold = tonumber(ARGV[1])
        local hard_limit = tonumber(ARGV[2])
        local user_type = ARGV[3]
        
        -- Get current queue sizes
        local user_queue_size = redis.call('LLEN', user_queue_key)
        local total_size = 0
        
        -- Calculate total across all queues
        local anonymous_size = redis.call('LLEN', KEYS[3])
        local registered_size = redis.call('LLEN', KEYS[4])
        local privileged_size = redis.call('LLEN', KEYS[5])
        total_size = anonymous_size + registered_size + privileged_size
        
        -- Check if we can admit this request
        local can_admit = false
        
        if user_type == 'privileged' then
            can_admit = total_size < hard_limit
        elseif user_type == 'registered' then
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
        total_counter_key = f"{self.CAPACITY_COUNTER_KEY}:total"
        anonymous_queue_key = f"{self.QUEUE_PREFIX}:anonymous"
        registered_queue_key = f"{self.QUEUE_PREFIX}:registered"
        privileged_queue_key = f"{self.QUEUE_PREFIX}:privileged"

        try:
            # Execute atomic check
            result = await self.redis.eval(
                lua_script,
                6,  # Number of keys
                user_queue_key,
                total_counter_key,
                anonymous_queue_key,
                registered_queue_key,
                privileged_queue_key,
                "",  # Placeholder for 6th key
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
            elif user_type == UserType.REGISTERED:
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
        self.QUEUE_PREFIX = REDIS.CHAT.PROMPT_QUEUE_PREFIX
        self.PROCESS_HEARTBEAT_PREFIX = REDIS.PROCESS.HEARTBEAT_PREFIX
        self.INSTANCE_STATUS_PREFIX = REDIS.INSTANCE.HEARTBEAT_PREFIX
        self.CAPACITY_COUNTER_KEY = f"{REDIS.SYSTEM.CAPACITY}:counter"

        # Queue analytics keys
        self.QUEUE_ANALYTICS_PREFIX = f"{REDIS.CHAT.PROMPT_QUEUE_PREFIX}:analytics"

        # Lua script for atomic capacity check (without enqueueing)
        self.ATOMIC_CAPACITY_CHECK_SCRIPT = """
        local anon_queue_key = KEYS[1]
        local reg_queue_key = KEYS[2]
        local priv_queue_key = KEYS[3]
        
        local user_type = ARGV[1]
        local anonymous_threshold = tonumber(ARGV[2])
        local registered_threshold = tonumber(ARGV[3])
        local hard_limit = tonumber(ARGV[4])
        local capacity_per_healthy_process = tonumber(ARGV[5])
        local heartbeat_timeout = tonumber(ARGV[6])
        local current_time = tonumber(ARGV[7])
        local heartbeat_pattern = ARGV[8]
        
        -- Count healthy chat REST processes
        local heartbeat_keys = redis.call('KEYS', heartbeat_pattern)
        local healthy_processes = 0
        
        for i, key in ipairs(heartbeat_keys) do
            local heartbeat_data = redis.call('GET', key)
            if heartbeat_data then
                local success, heartbeat = pcall(cjson.decode, heartbeat_data)
                if success and heartbeat then
                    if heartbeat.service_type == "chat" and heartbeat.service_name == "rest" then
                        local age = current_time - (heartbeat.last_heartbeat or 0)
                        local is_healthy = (heartbeat.health_status == "healthy" or 
                                          heartbeat.health_status == "HEALTHY" or
                                          string.find(tostring(heartbeat.health_status), "HEALTHY"))
                        if age <= heartbeat_timeout and is_healthy then
                            healthy_processes = healthy_processes + 1
                        end
                    end
                end
            end
        end
        
        -- Calculate actual system capacity
        local actual_capacity = healthy_processes * capacity_per_healthy_process
        if actual_capacity <= 0 then
            return {0, "no_healthy_processes", 0, 0, 0}
        end
        
        -- Get current queue sizes
        local anon_queue = redis.call('LLEN', anon_queue_key)
        local reg_queue = redis.call('LLEN', reg_queue_key) 
        local priv_queue = redis.call('LLEN', priv_queue_key)
        local total_queue = anon_queue + reg_queue + priv_queue
        
        -- Calculate dynamic thresholds based on actual capacity
        local anon_threshold = math.floor(actual_capacity * (anonymous_threshold / hard_limit))
        local reg_threshold = math.floor(actual_capacity * (registered_threshold / hard_limit))
        local hard_threshold = math.floor(actual_capacity * 1.0)
        
        -- Check if request can be admitted (without enqueueing)
        local can_admit = false
        local rejection_reason = ""
        
        if user_type == "privileged" then
            can_admit = total_queue < hard_threshold
            rejection_reason = total_queue >= hard_threshold and "hard_limit_exceeded" or ""
        elseif user_type == "registered" then
            can_admit = total_queue < reg_threshold  
            rejection_reason = total_queue >= reg_threshold and "registered_threshold_exceeded" or ""
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

    async def init_redis(self):
        if not self.redis:
            self.redis = aioredis.from_url(self.redis_url)

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

        # Get theoretical thresholds from configuration
        total_instance_capacity = self.gateway_config.total_capacity_per_instance
        theoretical_thresholds = self.gateway_config.get_thresholds_for_actual_capacity(total_instance_capacity)

        heartbeat_pattern = f"{self.PROCESS_HEARTBEAT_PREFIX}:*"

        total_per_single_process = (
                int(self.gateway_config.service_capacity.concurrent_requests_per_process * (1 - self.gateway_config.backpressure.capacity_buffer)) +
                int(self.gateway_config.service_capacity.concurrent_requests_per_process * self.gateway_config.backpressure.queue_depth_multiplier)
        )
        try:
            result = await self.redis.eval(
                self.ATOMIC_CAPACITY_CHECK_SCRIPT,
                3,  # Number of keys
                anon_queue_key,
                reg_queue_key,
                priv_queue_key,
                # Arguments
                user_type.value,
                str(theoretical_thresholds["anonymous_threshold"]),
                str(theoretical_thresholds["registered_threshold"]),
                str(theoretical_thresholds["hard_limit"]),
                # str(self.gateway_config.total_capacity_per_instance),
                str(total_per_single_process),
                str(self.gateway_config.monitoring.heartbeat_timeout_seconds),
                str(time.time()),
                heartbeat_pattern
            )

            success = bool(result[0])
            reason = result[1]
            reason = reason.decode('utf-8') if reason and isinstance(reason, bytes) else reason
            current_queue_size = result[2]
            actual_capacity = result[3]
            healthy_processes = result[4] if len(result) > 4 else 0

            stats = {
                "current_queue_size": current_queue_size,
                "actual_capacity": actual_capacity,
                "healthy_processes": healthy_processes,
                "configured_capacity": total_instance_capacity,
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
        elif "registered_threshold" in reason:
            throttling_reason = ThrottlingReason.REGISTERED_BACKPRESSURE
            retry_after = 45
            message = f"System under high pressure - privileged users only ({stats.get('current_queue_size', 0)}/{stats.get('actual_capacity', 0)})"
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

        queues = {
            "anonymous": f"{self.QUEUE_PREFIX}:anonymous",
            "registered": f"{self.QUEUE_PREFIX}:registered",
            "privileged": f"{self.QUEUE_PREFIX}:privileged"
        }

        sizes = {}
        for user_type, queue_key in queues.items():
            sizes[user_type] = await self.redis.llen(queue_key)

        return sizes

    async def get_queue_analytics(self) -> Dict[str, Dict[str, Any]]:
        """Get queue analytics for each user type"""
        if not self.gateway_config.monitoring.queue_analytics_enabled:
            return {}

        await self.init_redis()

        analytics = {}
        for user_type in ["anonymous", "registered", "privileged"]:
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

        current_size = await self.redis.llen(f"{self.QUEUE_PREFIX}:{user_type}")
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
            accepting_privileged = total_size < thresholds['hard_limit']

            analytics = await self.get_queue_analytics()

            from kdcube_ai_app.infra.gateway.definitions import QueueStats

            return QueueStats(
                anonymous_queue=queue_sizes['anonymous'],
                registered_queue=queue_sizes['registered'],
                privileged_queue=queue_sizes['privileged'],
                total_queue=total_size,
                base_capacity_per_instance=base_capacity,
                alive_instances=list(alive_instances),
                instance_count=instance_count,
                weighted_max_capacity=weighted_capacity,
                pressure_ratio=pressure_ratio,
                accepting_anonymous=accepting_anonymous,
                accepting_registered=accepting_registered,
                accepting_privileged=accepting_privileged,
                anonymous_threshold=thresholds['anonymous_threshold'],
                registered_threshold=thresholds['registered_threshold'],
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

        healthy_processes = await self._count_healthy_chat_processes()
        actual_capacity = self.gateway_config.total_capacity_per_instance * healthy_processes
        actual_thresholds = self.gateway_config.get_thresholds_for_actual_capacity(actual_capacity)

        pressure_ratio = total_size / actual_capacity if actual_capacity > 0 else 1.0

        accepting_anonymous = total_size < actual_thresholds['anonymous_threshold']
        accepting_registered = total_size < actual_thresholds['registered_threshold']
        accepting_privileged = total_size < actual_thresholds['hard_limit']

        analytics = await self.get_queue_analytics()

        from kdcube_ai_app.infra.gateway.definitions import QueueStats

        return QueueStats(
            anonymous_queue=queue_sizes['anonymous'],
            registered_queue=queue_sizes['registered'],
            privileged_queue=queue_sizes['privileged'],
            total_queue=total_size,
            base_capacity_per_instance=self.gateway_config.total_capacity_per_instance,
            alive_instances=list(alive_instances),
            instance_count=instance_count,
            weighted_max_capacity=actual_capacity,
            pressure_ratio=pressure_ratio,
            accepting_anonymous=accepting_anonymous,
            accepting_registered=accepting_registered,
            accepting_privileged=accepting_privileged,
            anonymous_threshold=actual_thresholds['anonymous_threshold'],
            registered_threshold=actual_thresholds['registered_threshold'],
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

    async def _count_healthy_chat_processes(self) -> int:
        """Count healthy chat REST processes from heartbeats"""
        try:
            pattern = f"{self.PROCESS_HEARTBEAT_PREFIX}:*"
            keys = await self.redis.keys(pattern)
            healthy_count = 0
            current_time = time.time()

            for key in keys:
                try:
                    data = await self.redis.get(key)
                    if not data:
                        continue

                    heartbeat = json.loads(data)
                    if (heartbeat.get("service_type") == "chat" and
                            heartbeat.get("service_name") == "rest"):

                        age = current_time - heartbeat.get("last_heartbeat", 0)
                        health_status = str(heartbeat.get("health_status", "")).upper()
                        is_healthy = "HEALTHY" in health_status

                        if age <= self.gateway_config.monitoring.heartbeat_timeout_seconds and is_healthy:
                            healthy_count += 1

                except Exception as e:
                    logger.debug(f"Error parsing heartbeat {key}: {e}")
                    continue

            return max(healthy_count, 1)  # Assume at least 1 process

        except Exception as e:
            logger.error(f"Error counting healthy processes: {e}")
            return 1  # Fallback

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

        # Redis keys
        self.QUEUE_PREFIX = REDIS.CHAT.PROMPT_QUEUE_PREFIX
        self.PROCESS_HEARTBEAT_PREFIX = REDIS.PROCESS.HEARTBEAT_PREFIX
        self.CAPACITY_COUNTER_KEY = f"{REDIS.SYSTEM.CAPACITY}:counter"

        # Lua script for atomic capacity check and task enqueue
        self.ATOMIC_CHAT_ENQUEUE_SCRIPT = """
        local queue_key = KEYS[1]
        local capacity_counter_key = KEYS[2]
        local anon_queue_key = KEYS[3] 
        local reg_queue_key = KEYS[4]
        local priv_queue_key = KEYS[5]
        
        local user_type = ARGV[1]
        local chat_task_json = ARGV[2]  -- Actual chat task
        local anonymous_threshold = tonumber(ARGV[3])
        local registered_threshold = tonumber(ARGV[4])
        local hard_limit = tonumber(ARGV[5])
        local capacity_per_healthy_process = tonumber(ARGV[6])
        local heartbeat_timeout = tonumber(ARGV[7])
        local current_time = tonumber(ARGV[8])
        local heartbeat_pattern = ARGV[9]
        
        -- Count healthy chat REST processes
        local heartbeat_keys = redis.call('KEYS', heartbeat_pattern)
        local healthy_processes = 0
        
        for i, key in ipairs(heartbeat_keys) do
            local heartbeat_data = redis.call('GET', key)
            if heartbeat_data then
                local success, heartbeat = pcall(cjson.decode, heartbeat_data)
                if success and heartbeat then
                    if heartbeat.service_type == "chat" and heartbeat.service_name == "rest" then
                        local age = current_time - (heartbeat.last_heartbeat or 0)
                        local is_healthy = (heartbeat.health_status == "healthy" or 
                                          heartbeat.health_status == "HEALTHY" or
                                          string.find(tostring(heartbeat.health_status), "HEALTHY"))
                        if age <= heartbeat_timeout and is_healthy then
                            healthy_processes = healthy_processes + 1
                        end
                    end
                end
            end
        end
        
        -- Calculate actual system capacity
        local actual_capacity = healthy_processes * capacity_per_healthy_process
        if actual_capacity <= 0 then
            return {0, "no_healthy_processes", 0, 0, 0}
        end
        
        -- Get current queue sizes
        local anon_queue = redis.call('LLEN', anon_queue_key)
        local reg_queue = redis.call('LLEN', reg_queue_key) 
        local priv_queue = redis.call('LLEN', priv_queue_key)
        local total_queue = anon_queue + reg_queue + priv_queue
        
        -- Calculate dynamic thresholds
        local anon_threshold = math.floor(actual_capacity * (anonymous_threshold / hard_limit))
        local reg_threshold = math.floor(actual_capacity * (registered_threshold / hard_limit))
        local hard_threshold = math.floor(actual_capacity * 1.0)
        
        -- Check if chat request can be admitted
        local can_admit = false
        local rejection_reason = ""
        
        if user_type == "privileged" then
            can_admit = total_queue < hard_threshold
            rejection_reason = total_queue >= hard_threshold and "hard_limit_exceeded" or ""
        elseif user_type == "registered" then
            can_admit = total_queue < reg_threshold  
            rejection_reason = total_queue >= reg_threshold and "registered_threshold_exceeded" or ""
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

    async def init_redis(self):
        if not self.redis:
            self.redis = aioredis.from_url(self.redis_url)

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

        # Get theoretical thresholds from configuration
        total_instance_capacity = self.gateway_config.total_capacity_per_instance
        theoretical_thresholds = self.gateway_config.get_thresholds_for_actual_capacity(total_instance_capacity)

        heartbeat_pattern = f"{self.PROCESS_HEARTBEAT_PREFIX}:*"

        total_per_single_process = (
                int(self.gateway_config.service_capacity.concurrent_requests_per_process * (1 - self.gateway_config.backpressure.capacity_buffer)) +
                int(self.gateway_config.service_capacity.concurrent_requests_per_process * self.gateway_config.backpressure.queue_depth_multiplier)
        )
        try:
            result = await self.redis.eval(
                self.ATOMIC_CHAT_ENQUEUE_SCRIPT,
                5,  # Number of keys
                queue_key,
                capacity_counter_key,
                anon_queue_key,
                reg_queue_key,
                priv_queue_key,
                # Arguments
                user_type.value,
                json.dumps(chat_task_data, ensure_ascii=False),  # Your actual chat task
                str(theoretical_thresholds["anonymous_threshold"]),
                str(theoretical_thresholds["registered_threshold"]),
                str(theoretical_thresholds["hard_limit"]),
                # str(self.gateway_config.total_capacity_per_instance),
                str(total_per_single_process),
                str(self.gateway_config.monitoring.heartbeat_timeout_seconds),
                str(time.time()),
                heartbeat_pattern
            )

            success = bool(result[0])
            reason = result[1]
            current_queue_size = result[2]
            actual_capacity = result[3]
            healthy_processes = result[4] if len(result) > 4 else 0

            stats = {
                "current_queue_size": current_queue_size,
                "actual_capacity": actual_capacity,
                "healthy_processes": healthy_processes,
                "configured_capacity": total_instance_capacity,
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