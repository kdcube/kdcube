# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/gateway/config.py
"""
Centralized Gateway Configuration
All gateway-related settings in one place for easy management and monitoring
"""
import os
import logging
import json
import time
import asyncio

from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Optional
from enum import Enum

from kdcube_ai_app.infra.gateway.definitions import ServiceCapacity, CapacityBasedBackpressureConfig
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.namespaces import CONFIG, ns_key
from kdcube_ai_app.infra.service_hub.cache import (
    NamespacedKVCacheConfig,
    create_namespaced_kv_cache_from_config,
)

logger = logging.getLogger(__name__)

def _read_int_env(names, default: int) -> int:
    for name in names:
        value = os.getenv(name)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning("Invalid int for %s=%r; using default %s", name, value, default)
            return default
    return default


def _get_chat_processes_per_instance() -> int:
    return _read_int_env(["CHAT_PROC_PARALLELISM", "CHAT_APP_PARALLELISM"], 1)


def _get_max_concurrent_per_process() -> int:
    return _read_int_env(["MAX_CONCURRENT_CHATS", "MAX_CONCURRENT_CHAT"], 5)


def get_chat_processes_per_instance_env() -> int:
    """Public helper for chat process parallelism (env-derived)."""
    return _get_chat_processes_per_instance()


def get_max_concurrent_per_process_env() -> int:
    """Public helper for per-process concurrency (env-derived)."""
    return _get_max_concurrent_per_process()


def apply_service_capacity_env_overrides(config: "GatewayConfiguration") -> bool:
    """
    Force service_capacity concurrency + process counts to match env values.
    Returns True if any override was applied.
    """
    changed = False
    env_concurrent = _get_max_concurrent_per_process()
    env_processes = _get_chat_processes_per_instance()
    if config.service_capacity.concurrent_requests_per_process != env_concurrent:
        config.service_capacity.concurrent_requests_per_process = env_concurrent
        changed = True
    if config.service_capacity.processes_per_instance != env_processes:
        config.service_capacity.processes_per_instance = env_processes
        changed = True
    if changed:
        config.service_capacity.concurrent_requests_per_instance = (
            config.service_capacity.concurrent_requests_per_process * config.service_capacity.processes_per_instance
        )
    return changed

DEFAULT_GUARDED_REST_PATTERNS = [
    r"^/resources/link-preview$",
    r"^/resources/by-rn$",
    r"^/conversations/[^/]+/[^/]+/[^/]+/fetch$",
    r"^/conversations/[^/]+/[^/]+/turns-with-feedbacks$",
    r"^/conversations/[^/]+/[^/]+/feedback/conversations-in-period$",
    r"^/integrations/bundles/[^/]+/[^/]+/operations/[^/]+$",
]

class GatewayProfile(Enum):
    """Predefined gateway configuration profiles"""
    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"
    LOAD_TEST = "load_test"


@dataclass
class RoleRateLimit:
    hourly: int = 50
    burst: int = 5
    burst_window: int = 60


@dataclass
class RateLimitSettings:
    """Rate limiting settings per role (role -> limits)"""
    roles: Dict[str, RoleRateLimit] = field(default_factory=dict)

    def __post_init__(self):
        if not self.roles:
            # default roles
            self.roles = {
                "anonymous": RoleRateLimit(hourly=120, burst=10, burst_window=60),
                "registered": RoleRateLimit(hourly=600, burst=30, burst_window=60),
                "paid": RoleRateLimit(hourly=2000, burst=60, burst_window=60),
                "privileged": RoleRateLimit(hourly=-1, burst=200, burst_window=60),
            }

    def get(self, role: str) -> RoleRateLimit:
        if role in self.roles:
            return self.roles[role]
        # fallback: registered if present, else any
        if "registered" in self.roles:
            return self.roles["registered"]
        return next(iter(self.roles.values()))

    @staticmethod
    def from_env() -> "RateLimitSettings":
        return RateLimitSettings()


@dataclass
class ServiceCapacitySettings:
    """Service capacity configuration - now process-aware"""
    concurrent_requests_per_process: int = 5  # MAX_CONCURRENT_CHAT(S)
    avg_processing_time_seconds: float = 25.0
    processes_per_instance: int = None  # Auto-detected from CHAT_PROC_PARALLELISM/CHAT_APP_PARALLELISM

    # Computed properties (will be calculated)
    concurrent_requests_per_instance: int = None
    requests_per_hour: Optional[int] = None

    def __post_init__(self):
        if self.processes_per_instance is None:
            self.processes_per_instance = _get_chat_processes_per_instance()
        if self.concurrent_requests_per_process is None:
            self.concurrent_requests_per_process = _get_max_concurrent_per_process()

        if self.concurrent_requests_per_instance is None:
            self.concurrent_requests_per_instance = (
                    self.concurrent_requests_per_process * self.processes_per_instance
            )
    @property
    def total_concurrent_per_instance(self) -> int:
        return self.concurrent_requests_per_process * self.processes_per_instance



@dataclass
class BackpressureSettings:
    """Backpressure policy configuration"""
    capacity_buffer: float = 0.2  # 20% safety buffer
    queue_depth_multiplier: float = 2.0  # 2x processing capacity for queue

    # Pressure thresholds (as ratios of total capacity)
    anonymous_pressure_threshold: float = 0.6  # Block anonymous at 60%
    registered_pressure_threshold: float = 0.8  # Block registered at 80%
    paid_pressure_threshold: float = 0.8  # Block paid at 80% (default same as registered)
    hard_limit_threshold: float = 0.95  # Hard block at 95%


@dataclass
class CircuitBreakerSettings:
    """Circuit breaker configuration"""
    # Authentication circuit breaker
    auth_failure_threshold: int = 15
    auth_recovery_timeout: int = 60
    auth_success_threshold: int = 5
    auth_window_size: int = 120
    auth_half_open_max_calls: int = 10

    # Rate limiter circuit breaker
    rate_limit_failure_threshold: int = 20
    rate_limit_recovery_timeout: int = 30
    rate_limit_success_threshold: int = 3
    rate_limit_window_size: int = 120
    rate_limit_half_open_max_calls: int = 5

    # Backpressure circuit breaker
    backpressure_failure_threshold: int = 10
    backpressure_recovery_timeout: int = 60
    backpressure_success_threshold: int = 5
    backpressure_window_size: int = 120
    backpressure_half_open_max_calls: int = 3
    def to_dict(self) -> Dict[str, Any]:
        """Convert to nested dictionary format expected by frontend"""
        return {
            "authentication": {
                "failure_threshold": self.auth_failure_threshold,
                "recovery_timeout": self.auth_recovery_timeout,
                "success_threshold": self.auth_success_threshold,
                "window_size": self.auth_window_size,
                "half_open_max_calls": self.auth_half_open_max_calls
            },
            "rate_limiter": {
                "failure_threshold": self.rate_limit_failure_threshold,
                "recovery_timeout": self.rate_limit_recovery_timeout,
                "success_threshold": self.rate_limit_success_threshold,
                "window_size": self.rate_limit_window_size,
                "half_open_max_calls": self.rate_limit_half_open_max_calls
            },
            "backpressure": {
                "failure_threshold": self.backpressure_failure_threshold,
                "recovery_timeout": self.backpressure_recovery_timeout,
                "success_threshold": self.backpressure_success_threshold,
                "window_size": self.backpressure_window_size,
                "half_open_max_calls": self.backpressure_half_open_max_calls
            }
        }

@dataclass
class MonitoringSettings:
    """Monitoring and analytics configuration"""
    throttling_events_retention_hours: int = 24
    session_analytics_enabled: bool = True
    circuit_breaker_stats_retention_hours: int = 24
    queue_analytics_enabled: bool = True
    heartbeat_timeout_seconds: int = 45
    instance_cache_ttl_seconds: int = 10


@dataclass
class RedisSettings:
    """Redis configuration for gateway components"""
    rate_limit_key_ttl: int = 3600
    session_ttl: int = 86400
    analytics_ttl: int = 86400
    circuit_breaker_stats_ttl: int = 3600
    heartbeat_ttl: int = 30


@dataclass
class GatewayConfiguration:
    """Complete gateway configuration"""
    profile: GatewayProfile
    instance_id: str
    project_id: str
    tenant_id: str

    # Core settings
    rate_limits: RateLimitSettings
    service_capacity: ServiceCapacitySettings
    backpressure: BackpressureSettings
    circuit_breakers: CircuitBreakerSettings
    monitoring: MonitoringSettings
    redis: RedisSettings

    # Environment-specific
    redis_url: str
    guarded_rest_patterns: list[str] = field(default_factory=list)

    # Computed properties
    @property
    def display_name(self) -> str:
        return f"Gateway-{self.profile.value}-{self.instance_id}"

    @property
    def service_capacity_obj(self) -> ServiceCapacity:
        """Get ServiceCapacity object for gateway creation"""
        return ServiceCapacity(
            concurrent_requests_per_process=self.service_capacity.concurrent_requests_per_process,
            avg_processing_time_seconds=self.service_capacity.avg_processing_time_seconds,
            processes_per_instance=self.service_capacity.processes_per_instance
        )

    @property
    def backpressure_config_obj(self) -> CapacityBasedBackpressureConfig:
        """Get CapacityBasedBackpressureConfig object for gateway creation"""
        return CapacityBasedBackpressureConfig(
            service_capacity=self.service_capacity_obj,
            capacity_buffer=self.backpressure.capacity_buffer,
            queue_depth_multiplier=self.backpressure.queue_depth_multiplier,
            anonymous_pressure_threshold=self.backpressure.anonymous_pressure_threshold,
            registered_pressure_threshold=self.backpressure.registered_pressure_threshold,
            paid_pressure_threshold=self.backpressure.paid_pressure_threshold,
            hard_limit_threshold=self.backpressure.hard_limit_threshold
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for monitoring/API exposure"""
        return {
            "profile": self.profile.value,
            "display_name": self.display_name,
            "instance_id": self.instance_id,
            "tenant_id": self.tenant_id,
            "rate_limits": asdict(self.rate_limits),
            "service_capacity": asdict(self.service_capacity),
            "backpressure": asdict(self.backpressure),
            "circuit_breakers": self.circuit_breakers.to_dict(),  # Use custom method here
            "monitoring": asdict(self.monitoring),
            "redis": asdict(self.redis),
            "guarded_rest_patterns": list(self.guarded_rest_patterns or []),
            "computed_metrics": {
                "base_queue_size_per_instance": self.backpressure_config_obj.get_base_queue_size_per_instance(),
                "theoretical_throughput_per_instance": self.service_capacity_obj.requests_per_hour_per_instance,
                "effective_concurrent_capacity": int(
                    self.service_capacity.concurrent_requests_per_instance * (1 - self.backpressure.capacity_buffer)
                ),
                "queue_capacity_per_instance": int(
                    self.service_capacity.concurrent_requests_per_instance * self.backpressure.queue_depth_multiplier
                )
            }
        }

    @property
    def total_concurrent_per_instance(self) -> int:
        """Total concurrent requests per instance (processes × concurrent_per_process)"""
        return (self.service_capacity.concurrent_requests_per_process *
                self.service_capacity.processes_per_instance)

    @property
    def effective_concurrent_per_instance(self) -> int:
        """Effective concurrent capacity after buffer"""
        total = self.total_concurrent_per_instance
        return int(total * (1 - self.backpressure.capacity_buffer))

    @property
    def queue_capacity_per_instance(self) -> int:
        """Queue capacity per instance"""
        total = self.total_concurrent_per_instance
        return int(total * self.backpressure.queue_depth_multiplier)

    @property
    def total_capacity_per_instance(self) -> int:
        """Total capacity per instance (effective + queue)"""
        return self.effective_concurrent_per_instance + self.queue_capacity_per_instance

    def get_thresholds_for_actual_capacity(self, actual_system_capacity: int) -> Dict[str, int]:
        """Calculate thresholds based on actual system capacity"""
        return {
            "anonymous_threshold": int(actual_system_capacity * self.backpressure.anonymous_pressure_threshold),
            "registered_threshold": int(actual_system_capacity * self.backpressure.registered_pressure_threshold),
            "paid_threshold": int(actual_system_capacity * self.backpressure.paid_pressure_threshold),
            "hard_limit": int(actual_system_capacity * self.backpressure.hard_limit_threshold),
            "total_capacity": actual_system_capacity
        }

class GatewayConfigFactory:
    """Factory for creating gateway configurations"""

    @staticmethod
    def create_from_env(profile: GatewayProfile = None) -> GatewayConfiguration:
        """Create configuration from environment variables"""
        # JSON override (full config)
        cfg_json = os.getenv("GATEWAY_CONFIG_JSON")
        if cfg_json:
            try:
                data = json.loads(cfg_json)
                cfg = _config_from_dict(data)
                settings = get_settings()
                if not cfg.redis_url:
                    cfg.redis_url = settings.REDIS_URL or ""
                if not cfg.tenant_id:
                    cfg.tenant_id = os.getenv("TENANT_ID", "default-tenant")
                if not cfg.project_id:
                    cfg.project_id = os.getenv("DEFAULT_PROJECT_NAME", "default-tenant")
                if not cfg.instance_id:
                    cfg.instance_id = os.getenv("INSTANCE_ID", "default-instance")
                apply_service_capacity_env_overrides(cfg)
                return cfg
            except Exception as e:
                logger.warning(f"Failed to parse GATEWAY_CONFIG_JSON: {e}. Falling back to env defaults.")

        # Auto-detect profile if not specified
        if profile is None:
            env_profile = os.getenv("GATEWAY_PROFILE", "development").lower()
            profile = GatewayProfile(env_profile)

        # Environment variables with defaults
        settings = get_settings()
        redis_url = settings.REDIS_URL or ""

        instance_id = os.getenv("INSTANCE_ID", "default-instance")
        tenant_id = os.getenv("TENANT_ID", "default-tenant")
        project_id = os.getenv("DEFAULT_PROJECT_NAME", "default-tenant")

        # Rate limiting from environment
        rate_limits = RateLimitSettings.from_env()

        # Service capacity from environment
        service_capacity = ServiceCapacitySettings(
            concurrent_requests_per_process=_get_max_concurrent_per_process(), # CONCURRENT_REQUESTS_PER_PROCESS
            avg_processing_time_seconds=float(os.getenv("AVG_PROCESSING_TIME_SECONDS", "25.0")),
            processes_per_instance=_get_chat_processes_per_instance()
        )

        # Apply profile-specific overrides
        rate_limits, service_capacity, backpressure, circuit_breakers = GatewayConfigFactory._apply_profile_overrides(
            profile, rate_limits, service_capacity
        )

        cfg = GatewayConfiguration(
            profile=profile,
            rate_limits=rate_limits,
            service_capacity=service_capacity,
            backpressure=backpressure,
            circuit_breakers=circuit_breakers,
            monitoring=MonitoringSettings(),
            redis=RedisSettings(),
            redis_url=redis_url,
            instance_id=instance_id,
            tenant_id=tenant_id,
            project_id=project_id,
            guarded_rest_patterns=list(DEFAULT_GUARDED_REST_PATTERNS),
        )
        apply_service_capacity_env_overrides(cfg)
        return cfg

    @staticmethod
    def _apply_profile_overrides(profile: GatewayProfile,
                                 rate_limits: RateLimitSettings,
                                 service_capacity: ServiceCapacitySettings) -> tuple:
        """Apply profile-specific configuration overrides"""

        backpressure = BackpressureSettings()
        circuit_breakers = CircuitBreakerSettings()
        roles = rate_limits.roles
        if "anonymous" not in roles:
            roles["anonymous"] = RoleRateLimit()
        if "registered" not in roles:
            roles["registered"] = RoleRateLimit(hourly=500, burst=20, burst_window=60)
        if "paid" not in roles:
            roles["paid"] = RoleRateLimit(hourly=1000, burst=50, burst_window=60)
        if "privileged" not in roles:
            roles["privileged"] = RoleRateLimit(hourly=-1, burst=100, burst_window=60)

        if profile == GatewayProfile.DEVELOPMENT:
            # Development: More permissive settings
            roles["anonymous"].hourly = 100
            roles["registered"].hourly = 1000
            backpressure.anonymous_pressure_threshold = 0.8
            backpressure.registered_pressure_threshold = 0.9
            backpressure.paid_pressure_threshold = backpressure.registered_pressure_threshold
            circuit_breakers.auth_failure_threshold = 20

        elif profile == GatewayProfile.TESTING:
            # Testing: Moderate settings
            roles["anonymous"].hourly = 200
            roles["registered"].hourly = 2000
            service_capacity.concurrent_requests_per_instance = 10
            backpressure.capacity_buffer = 0.15

        elif profile == GatewayProfile.PRODUCTION:
            # Production: Conservative settings
            roles["anonymous"].hourly = 50
            roles["registered"].hourly = 500
            backpressure.capacity_buffer = 0.25
            backpressure.anonymous_pressure_threshold = 0.5
            backpressure.registered_pressure_threshold = 0.7
            backpressure.paid_pressure_threshold = backpressure.registered_pressure_threshold
            circuit_breakers.auth_failure_threshold = 10

        elif profile == GatewayProfile.LOAD_TEST:
            # Load testing: High capacity, detailed monitoring
            roles["anonymous"].hourly = 5000
            roles["registered"].hourly = 10000
            service_capacity.concurrent_requests_per_instance = 20
            service_capacity.avg_processing_time_seconds = 15.0
            backpressure.capacity_buffer = 0.1
            backpressure.queue_depth_multiplier = 3.0
            backpressure.paid_pressure_threshold = backpressure.registered_pressure_threshold

        return rate_limits, service_capacity, backpressure, circuit_breakers

    @staticmethod
    def create_for_chat_service() -> GatewayConfiguration:
        """Create optimized configuration for chat service"""
        config = GatewayConfigFactory.create_from_env()

        # Chat-specific optimizations with process awareness
        max_concurrent_per_process = _get_max_concurrent_per_process()
        processes = _get_chat_processes_per_instance()

        config.service_capacity.concurrent_requests_per_process = max_concurrent_per_process
        config.service_capacity.processes_per_instance = processes
        config.service_capacity.concurrent_requests_per_instance = max_concurrent_per_process * processes
        config.service_capacity.avg_processing_time_seconds = 25.0
        config.backpressure.queue_depth_multiplier = 2.0
        config.backpressure.anonymous_pressure_threshold = 0.6
        if not getattr(config.backpressure, "paid_pressure_threshold", None):
            config.backpressure.paid_pressure_threshold = config.backpressure.registered_pressure_threshold

        return config

    @staticmethod
    def create_for_api_service() -> GatewayConfiguration:
        """Create optimized configuration for fast API service"""
        config = GatewayConfigFactory.create_from_env()

        # API-specific optimizations
        config.service_capacity.concurrent_requests_per_instance = 50
        config.service_capacity.avg_processing_time_seconds = 2.0
        roles = config.rate_limits.roles
        if "anonymous" in roles:
            roles["anonymous"].hourly = 1000
        if "registered" in roles:
            roles["registered"].hourly = 10000
        config.backpressure.capacity_buffer = 0.1

        return config


# Global configuration instance
_gateway_config: Optional[GatewayConfiguration] = None


def get_gateway_config() -> GatewayConfiguration:
    """Get the global gateway configuration"""
    global _gateway_config
    if _gateway_config is None:
        _gateway_config = GatewayConfigFactory.create_from_env()
    return _gateway_config


def set_gateway_config(config: GatewayConfiguration):
    """Set the global gateway configuration"""
    global _gateway_config
    _gateway_config = config


def reset_gateway_config():
    """Reset the global gateway configuration"""
    global _gateway_config
    _gateway_config = None


# Configuration validation
def validate_gateway_config(config: GatewayConfiguration) -> list[str]:
    """Validate gateway configuration and return list of issues"""
    issues = []

    # Rate limit validation
    required_roles = ["anonymous", "registered", "paid", "privileged"]
    for role in required_roles:
        if role not in config.rate_limits.roles:
            issues.append(f"Missing rate limit role config: {role}")
    for role, rl in config.rate_limits.roles.items():
        if rl.hourly <= 0 and rl.hourly != -1:
            issues.append(f"{role} hourly rate limit must be positive or -1 for unlimited")
        if rl.burst <= 0:
            issues.append(f"{role} burst limit must be positive")

    # Service capacity validation - updated for multi-process
    if config.service_capacity.concurrent_requests_per_process <= 0:
        issues.append("Concurrent requests per process must be positive")

    if config.service_capacity.processes_per_instance <= 0:
        issues.append("Processes per instance must be positive")

    if config.service_capacity.concurrent_requests_per_instance <= 0:
        issues.append("Concurrent requests per instance must be positive")

    if config.service_capacity.avg_processing_time_seconds <= 0:
        issues.append("Average processing time must be positive")

    # Validate that instance capacity = per_process * processes
    expected_instance_capacity = (
            config.service_capacity.concurrent_requests_per_process *
            config.service_capacity.processes_per_instance
    )
    if config.service_capacity.concurrent_requests_per_instance != expected_instance_capacity:
        issues.append(
            f"Instance capacity mismatch: expected {expected_instance_capacity} "
            f"({config.service_capacity.concurrent_requests_per_process} × {config.service_capacity.processes_per_instance}), "
            f"got {config.service_capacity.concurrent_requests_per_instance}"
        )

    # Backpressure validation
    if not (0 < config.backpressure.capacity_buffer < 1):
        issues.append("Capacity buffer must be between 0 and 1")

    if config.backpressure.queue_depth_multiplier <= 0:
        issues.append("Queue depth multiplier must be positive")

    if not (0 < config.backpressure.anonymous_pressure_threshold <= 1):
        issues.append("Anonymous pressure threshold must be between 0 and 1")

    if not (0 < config.backpressure.registered_pressure_threshold <= 1):
        issues.append("Registered pressure threshold must be between 0 and 1")

    if not (0 < config.backpressure.paid_pressure_threshold <= 1):
        issues.append("Paid pressure threshold must be between 0 and 1")

    if not (0 < config.backpressure.hard_limit_threshold <= 1):
        issues.append("Hard limit threshold must be between 0 and 1")

    # Threshold ordering validation
    if config.backpressure.anonymous_pressure_threshold >= config.backpressure.registered_pressure_threshold:
        issues.append(
            f"Anonymous pressure threshold ({config.backpressure.anonymous_pressure_threshold}) "
            f"must be less than registered threshold ({config.backpressure.registered_pressure_threshold})"
        )

    if config.backpressure.registered_pressure_threshold > config.backpressure.paid_pressure_threshold:
        issues.append(
            f"Registered pressure threshold ({config.backpressure.registered_pressure_threshold}) "
            f"must be less than or equal to paid threshold ({config.backpressure.paid_pressure_threshold})"
        )

    if config.backpressure.paid_pressure_threshold >= config.backpressure.hard_limit_threshold:
        issues.append(
            f"Paid pressure threshold ({config.backpressure.paid_pressure_threshold}) "
            f"must be less than hard limit threshold ({config.backpressure.hard_limit_threshold})"
        )

    # Performance validation - updated for new capacity structure
    try:
        service_capacity_obj = config.service_capacity_obj
        theoretical_throughput_per_instance = service_capacity_obj.requests_per_hour_per_instance

        # Validate anonymous/registered rate limits against single instance throughput
        anon = config.rate_limits.roles.get("anonymous")
        reg = config.rate_limits.roles.get("registered")
        if anon and anon.hourly != -1 and anon.hourly > theoretical_throughput_per_instance:
            issues.append(
                f"Anonymous rate limit ({anon.hourly}/hour) exceeds "
                f"theoretical throughput per instance ({theoretical_throughput_per_instance}/hour)"
            )
        if reg and reg.hourly != -1 and reg.hourly > theoretical_throughput_per_instance:
            issues.append(
                f"Registered rate limit ({reg.hourly}/hour) exceeds "
                f"theoretical throughput per instance ({theoretical_throughput_per_instance}/hour)"
            )

        # Validate capacity calculations
        base_queue_size = config.backpressure_config_obj.get_base_queue_size_per_instance()
        if base_queue_size <= 0:
            issues.append("Calculated base queue size per instance must be positive")

        # Warn about potentially problematic configurations
        processing_capacity = service_capacity_obj.concurrent_requests_per_instance
        effective_capacity = int(processing_capacity * (1 - config.backpressure.capacity_buffer))

        if effective_capacity < service_capacity_obj.concurrent_requests_per_process:
            issues.append(
                f"Warning: Effective capacity ({effective_capacity}) is less than single process capacity "
                f"({service_capacity_obj.concurrent_requests_per_process}) due to high capacity buffer"
            )

        # Validate that queue can handle reasonable bursts
        queue_capacity = int(processing_capacity * config.backpressure.queue_depth_multiplier)
        if queue_capacity < processing_capacity:
            issues.append(
                f"Warning: Queue capacity ({queue_capacity}) is less than processing capacity "
                f"({processing_capacity}). Consider increasing queue_depth_multiplier."
            )

    except Exception as e:
        issues.append(f"Error calculating service capacity metrics: {str(e)}")

    # Circuit breaker validation
    if config.circuit_breakers.auth_failure_threshold <= 0:
        issues.append("Auth circuit breaker failure threshold must be positive")

    if config.circuit_breakers.auth_recovery_timeout <= 0:
        issues.append("Auth circuit breaker recovery timeout must be positive")

    if config.circuit_breakers.rate_limit_failure_threshold <= 0:
        issues.append("Rate limit circuit breaker failure threshold must be positive")

    if config.circuit_breakers.backpressure_failure_threshold <= 0:
        issues.append("Backpressure circuit breaker failure threshold must be positive")

    # Environment consistency validation (skip when JSON config is supplied)
    if not os.getenv("GATEWAY_CONFIG_JSON"):
        try:
            env_max_concurrent = _get_max_concurrent_per_process()
            env_parallelism = _get_chat_processes_per_instance()

            if config.service_capacity.concurrent_requests_per_process != env_max_concurrent:
                issues.append(
                    f"Config concurrent_requests_per_process ({config.service_capacity.concurrent_requests_per_process}) "
                    f"doesn't match MAX_CONCURRENT_CHAT env var ({env_max_concurrent})"
                )

            if config.service_capacity.processes_per_instance != env_parallelism:
                issues.append(
                    f"Config processes_per_instance ({config.service_capacity.processes_per_instance}) "
                    f"doesn't match CHAT_PROC_PARALLELISM/CHAT_APP_PARALLELISM env var ({env_parallelism})"
                )

        except (ValueError, TypeError) as e:
            issues.append(f"Error validating environment variables: {str(e)}")

    # Realistic capacity warnings
    total_instance_capacity = base_queue_size if 'base_queue_size' in locals() else 0
    if total_instance_capacity > 1000:
        issues.append(
            f"Warning: Very high total capacity per instance ({total_instance_capacity}). "
            f"This may cause memory or performance issues."
        )

    if config.service_capacity.processes_per_instance > 16:
        issues.append(
            f"Warning: High number of processes per instance ({config.service_capacity.processes_per_instance}). "
            f"Consider if this exceeds available CPU cores."
        )

    return issues

def analyze_gateway_capacity(config: GatewayConfiguration) -> Dict[str, Any]:
    """Analyze gateway capacity configuration and return detailed metrics"""
    try:
        service_capacity = config.service_capacity_obj
        backpressure_config = config.backpressure_config_obj

        # Calculate key metrics
        processing_capacity = service_capacity.concurrent_requests_per_instance
        effective_capacity = int(processing_capacity * (1 - config.backpressure.capacity_buffer))
        queue_capacity = int(processing_capacity * config.backpressure.queue_depth_multiplier)
        total_capacity = effective_capacity + queue_capacity

        # Calculate thresholds (assume single instance for analysis)
        thresholds = backpressure_config.get_capacity_thresholds(1)

        return {
            "per_process": {
                "concurrent_requests": service_capacity.concurrent_requests_per_process,
                "avg_processing_time": service_capacity.avg_processing_time_seconds,
                "theoretical_hourly": service_capacity.concurrent_requests_per_process * 3600 / service_capacity.avg_processing_time_seconds
            },
            "per_instance": {
                "processes": service_capacity.processes_per_instance,
                "processing_capacity": processing_capacity,
                "effective_capacity": effective_capacity,
                "queue_capacity": queue_capacity,
                "total_capacity": total_capacity,
                "theoretical_hourly": service_capacity.requests_per_hour_per_instance
            },
            "thresholds": {
                "anonymous_blocks_at": thresholds["anonymous_threshold"],
                "registered_blocks_at": thresholds["registered_threshold"],
                "paid_blocks_at": thresholds["paid_threshold"],
                "hard_limit_at": thresholds["hard_limit"],
                "anonymous_percentage": config.backpressure.anonymous_pressure_threshold * 100,
                "registered_percentage": config.backpressure.registered_pressure_threshold * 100,
                "paid_percentage": config.backpressure.paid_pressure_threshold * 100,
                "hard_limit_percentage": config.backpressure.hard_limit_threshold * 100
            },
            "rate_limits": {
                "roles": {
                    role: {
                        "hourly": rl.hourly,
                        "burst": rl.burst,
                        "burst_window": rl.burst_window
                    }
                    for role, rl in config.rate_limits.roles.items()
                }
            },
            "efficiency": {
                "capacity_buffer_percentage": config.backpressure.capacity_buffer * 100,
                "queue_depth_multiplier": config.backpressure.queue_depth_multiplier,
                "processing_to_queue_ratio": processing_capacity / queue_capacity if queue_capacity > 0 else float('inf')
            }
        }
    except Exception as e:
        return {"error": f"Failed to analyze capacity: {str(e)}"}


# Add validation summary function
def get_validation_summary(config: GatewayConfiguration) -> Dict[str, Any]:
    """Get comprehensive validation summary"""
    issues = validate_gateway_config(config)
    capacity_analysis = analyze_gateway_capacity(config)

    severity_levels = {
        "error": [issue for issue in issues if "must be" in issue or "Error" in issue],
        "warning": [issue for issue in issues if "Warning:" in issue],
        "info": [issue for issue in issues if issue not in
                 [i for i in issues if "must be" in i or "Error" in i or "Warning:" in i]]
    }

    return {
        "is_valid": len(severity_levels["error"]) == 0,
        "total_issues": len(issues),
        "issues_by_severity": severity_levels,
        "capacity_analysis": capacity_analysis,
        "config_summary": {
            "profile": config.profile.value,
            "per_process_capacity": config.service_capacity.concurrent_requests_per_process,
            "processes": config.service_capacity.processes_per_instance,
            "total_instance_capacity": config.service_capacity.concurrent_requests_per_instance,
            "queue_multiplier": config.backpressure.queue_depth_multiplier,
            "capacity_buffer": f"{config.backpressure.capacity_buffer * 100:.1f}%"
        }
    }

# Configuration presets for common scenarios
PRESET_CONFIGURATIONS = {
    "chat_development": lambda: GatewayConfigFactory.create_for_chat_service(),
    "chat_production": lambda: GatewayConfiguration(
        profile=GatewayProfile.PRODUCTION,
        rate_limits=RateLimitSettings(roles={
            "anonymous": RoleRateLimit(hourly=30, burst=5, burst_window=60),
            "registered": RoleRateLimit(hourly=300, burst=20, burst_window=60),
            "paid": RoleRateLimit(hourly=500, burst=20, burst_window=60),
            "privileged": RoleRateLimit(hourly=-1, burst=100, burst_window=60),
        }),
        service_capacity=ServiceCapacitySettings(concurrent_requests_per_instance=6, avg_processing_time_seconds=30.0),
        backpressure=BackpressureSettings(capacity_buffer=0.3, anonymous_pressure_threshold=0.5),
        circuit_breakers=CircuitBreakerSettings(),
        monitoring=MonitoringSettings(),
        redis=RedisSettings(),
        redis_url=get_settings().REDIS_URL,
        instance_id=os.getenv("INSTANCE_ID", "chat-prod"),
        tenant_id=os.getenv("TENANT_ID", "production"),
        project_id=os.getenv("DEFAULT_PROJECT_NAME", "demo"),
    ),
    "api_high_throughput": lambda: GatewayConfigFactory.create_for_api_service(),
    "load_test_heavy": lambda: GatewayConfiguration(
        profile=GatewayProfile.LOAD_TEST,
        rate_limits=RateLimitSettings(roles={
            "anonymous": RoleRateLimit(hourly=10000, burst=200, burst_window=60),
            "registered": RoleRateLimit(hourly=50000, burst=500, burst_window=60),
            "paid": RoleRateLimit(hourly=80000, burst=800, burst_window=60),
            "privileged": RoleRateLimit(hourly=-1, burst=1000, burst_window=60),
        }),
        service_capacity=ServiceCapacitySettings(concurrent_requests_per_instance=25, avg_processing_time_seconds=10.0),
        backpressure=BackpressureSettings(capacity_buffer=0.05, queue_depth_multiplier=4.0),
        circuit_breakers=CircuitBreakerSettings(),
        monitoring=MonitoringSettings(),
        redis=RedisSettings(),
        redis_url=get_settings().REDIS_URL,
        instance_id=os.getenv("INSTANCE_ID", "load-test"),
        tenant_id=os.getenv("TENANT_ID", "testing"),
        project_id=os.getenv("DEFAULT_PROJECT_NAME", "demo"),
    )
}

# -----------------------------
# Gateway config cache (Redis)
# -----------------------------

def _serialize_gateway_config(config: GatewayConfiguration) -> Dict[str, Any]:
    return {
        "profile": config.profile.value,
        "instance_id": config.instance_id,
        "tenant_id": config.tenant_id,
        "project_id": config.project_id,
        "redis_url": config.redis_url,
        "rate_limits": asdict(config.rate_limits),
        "service_capacity": asdict(config.service_capacity),
        "backpressure": asdict(config.backpressure),
        "circuit_breakers": asdict(config.circuit_breakers),
        "monitoring": asdict(config.monitoring),
        "redis": asdict(config.redis),
        "guarded_rest_patterns": list(config.guarded_rest_patterns or []),
    }


def _config_from_dict(data: Dict[str, Any]) -> GatewayConfiguration:
    def _pick(src: Dict[str, Any], keys: list[str]) -> Dict[str, Any]:
        return {k: src[k] for k in keys if k in src}

    rate_limits_data = data.get("rate_limits", {})
    roles_data = rate_limits_data.get("roles") if isinstance(rate_limits_data, dict) else None
    roles_data = roles_data if roles_data is not None else rate_limits_data
    roles: Dict[str, RoleRateLimit] = {}
    if roles_data and isinstance(roles_data, dict):
        for role, cfg in roles_data.items():
            if not isinstance(cfg, dict):
                continue
            roles[str(role)] = RoleRateLimit(
                hourly=int(cfg.get("hourly", 50)),
                burst=int(cfg.get("burst", 5)),
                burst_window=int(cfg.get("burst_window", 60)),
            )
    rate_limits = RateLimitSettings(roles=roles)
    service_capacity = ServiceCapacitySettings(**_pick(data.get("service_capacity", {}), [
        "concurrent_requests_per_process",
        "avg_processing_time_seconds",
        "processes_per_instance",
        "concurrent_requests_per_instance",
        "requests_per_hour",
    ]))
    backpressure = BackpressureSettings(**_pick(data.get("backpressure", {}), [
        "capacity_buffer",
        "queue_depth_multiplier",
        "anonymous_pressure_threshold",
        "registered_pressure_threshold",
        "paid_pressure_threshold",
        "hard_limit_threshold",
    ]))
    circuit_breakers = CircuitBreakerSettings(**_pick(data.get("circuit_breakers", {}), [
        "auth_failure_threshold", "auth_recovery_timeout", "auth_success_threshold",
        "auth_window_size", "auth_half_open_max_calls",
        "rate_limit_failure_threshold", "rate_limit_recovery_timeout",
        "rate_limit_success_threshold", "rate_limit_window_size",
        "rate_limit_half_open_max_calls",
        "backpressure_failure_threshold", "backpressure_recovery_timeout",
        "backpressure_success_threshold", "backpressure_window_size",
        "backpressure_half_open_max_calls",
    ]))
    monitoring = MonitoringSettings(**_pick(data.get("monitoring", {}), [
        "throttling_events_retention_hours",
        "session_analytics_enabled",
        "circuit_breaker_stats_retention_hours",
        "queue_analytics_enabled",
        "heartbeat_timeout_seconds",
        "instance_cache_ttl_seconds",
    ]))
    redis = RedisSettings(**_pick(data.get("redis", {}), [
        "rate_limit_key_ttl",
        "session_ttl",
        "analytics_ttl",
        "circuit_breaker_stats_ttl",
        "heartbeat_ttl",
    ]))

    profile_raw = str(data.get("profile") or GatewayProfile.DEVELOPMENT.value)
    profile = GatewayProfile(profile_raw) if profile_raw in GatewayProfile._value2member_map_ else GatewayProfile.DEVELOPMENT

    guarded_rest_patterns = data.get("guarded_rest_patterns")
    if not isinstance(guarded_rest_patterns, list):
        guarded_rest_patterns = list(DEFAULT_GUARDED_REST_PATTERNS)
    else:
        guarded_rest_patterns = [str(p) for p in guarded_rest_patterns if p]
        if not guarded_rest_patterns:
            guarded_rest_patterns = list(DEFAULT_GUARDED_REST_PATTERNS)

    cfg = GatewayConfiguration(
        profile=profile,
        instance_id=str(data.get("instance_id") or os.getenv("INSTANCE_ID", "default-instance")),
        tenant_id=str(data.get("tenant_id") or os.getenv("TENANT_ID", "default-tenant")),
        project_id=str(data.get("project_id") or os.getenv("DEFAULT_PROJECT_NAME", "default-tenant")),
        rate_limits=rate_limits,
        service_capacity=service_capacity,
        backpressure=backpressure,
        circuit_breakers=circuit_breakers,
        monitoring=monitoring,
        redis=redis,
        redis_url=str(data.get("redis_url") or get_settings().REDIS_URL),
        guarded_rest_patterns=guarded_rest_patterns,
    )
    apply_service_capacity_env_overrides(cfg)
    return cfg


def _build_gateway_config_cache(*, tenant: str, project: str, redis_url: Optional[str]) -> Optional[Any]:
    if not redis_url:
        return None
    cfg = NamespacedKVCacheConfig(
        redis_url=redis_url,
        namespace=CONFIG.GATEWAY.NAMESPACE,
        tenant=tenant,
        project=project,
        default_ttl_seconds=0,
        decode_responses=True,
        use_tp_prefix=True,
    )
    return create_namespaced_kv_cache_from_config(cfg)


async def load_gateway_config_from_cache(
        *,
        tenant: str,
        project: str,
        redis_url: Optional[str] = None,
) -> Optional[GatewayConfiguration]:
    cache = _build_gateway_config_cache(tenant=tenant, project=project, redis_url=redis_url)
    if not cache:
        return None
    data = await cache.get_json(CONFIG.GATEWAY.CURRENT_KEY)
    if not data:
        return None
    return _config_from_dict(data)


async def save_gateway_config_to_cache(config: GatewayConfiguration) -> bool:
    cache = _build_gateway_config_cache(
        tenant=config.tenant_id,
        project=config.project_id,
        redis_url=config.redis_url,
    )
    if not cache:
        return False
    payload = _serialize_gateway_config(config)
    return await cache.set_json(CONFIG.GATEWAY.CURRENT_KEY, payload, ttl_seconds=0)


async def publish_gateway_config_update(config: GatewayConfiguration, *, actor: Optional[str] = None) -> None:
    if not config.redis_url:
        return
    channel = ns_key(CONFIG.GATEWAY.UPDATE_CHANNEL, tenant=config.tenant_id, project=config.project_id)
    try:
        from redis import asyncio as aioredis
        redis = aioredis.from_url(config.redis_url, decode_responses=True)
        payload = {
            "tenant": config.tenant_id,
            "project": config.project_id,
            "ts": time.time(),
            "config": _serialize_gateway_config(config),
        }
        if actor:
            payload["actor"] = actor
        await redis.publish(channel, json.dumps(payload, ensure_ascii=False))
        await redis.close()
    except Exception:
        return


def apply_gateway_config_snapshot(gateway, new_config: GatewayConfiguration) -> None:
    """
    Apply a full config snapshot to an existing gateway instance.
    Updates dependent component configs and refreshes capacity.
    """
    # update core config object
    gateway.gateway_config.profile = new_config.profile
    gateway.gateway_config.instance_id = new_config.instance_id
    gateway.gateway_config.tenant_id = new_config.tenant_id
    gateway.gateway_config.project_id = new_config.project_id
    gateway.gateway_config.redis_url = new_config.redis_url
    gateway.gateway_config.rate_limits = new_config.rate_limits
    gateway.gateway_config.service_capacity = new_config.service_capacity
    gateway.gateway_config.backpressure = new_config.backpressure
    gateway.gateway_config.circuit_breakers = new_config.circuit_breakers
    gateway.gateway_config.monitoring = new_config.monitoring
    gateway.gateway_config.redis = new_config.redis

    # update rate limiter limits
    gateway.rate_limiter.gateway_config = gateway.gateway_config
    if hasattr(gateway.rate_limiter, "_refresh_limits"):
        gateway.rate_limiter._refresh_limits()

    # update backpressure manager config
    gateway.backpressure_manager.gateway_config = gateway.gateway_config
    gateway.backpressure_manager.config = gateway.gateway_config.backpressure_config_obj

    # update throttling monitor config (namespacing)
    gateway.throttling_monitor.gateway_config = gateway.gateway_config

    # update circuit breaker configs
    from kdcube_ai_app.infra.gateway.circuit_breaker import CircuitBreakerConfig
    cb_cfg = gateway.gateway_config.circuit_breakers
    cb_map = {
        "authentication": CircuitBreakerConfig(
            failure_threshold=cb_cfg.auth_failure_threshold,
            recovery_timeout=cb_cfg.auth_recovery_timeout,
            success_threshold=cb_cfg.auth_success_threshold,
            window_size=cb_cfg.auth_window_size,
            half_open_max_calls=cb_cfg.auth_half_open_max_calls,
        ),
        "rate_limiter": CircuitBreakerConfig(
            failure_threshold=cb_cfg.rate_limit_failure_threshold,
            recovery_timeout=cb_cfg.rate_limit_recovery_timeout,
            success_threshold=cb_cfg.rate_limit_success_threshold,
            window_size=cb_cfg.rate_limit_window_size,
            half_open_max_calls=cb_cfg.rate_limit_half_open_max_calls,
        ),
        "backpressure": CircuitBreakerConfig(
            failure_threshold=cb_cfg.backpressure_failure_threshold,
            recovery_timeout=cb_cfg.backpressure_recovery_timeout,
            success_threshold=cb_cfg.backpressure_success_threshold,
            window_size=cb_cfg.backpressure_window_size,
            half_open_max_calls=cb_cfg.backpressure_half_open_max_calls,
        ),
    }
    for name, cfg in cb_map.items():
        cb = gateway.circuit_manager.get_circuit_breaker(name, cfg)
        cb.config = cfg
        cb.gateway_config = gateway.gateway_config
    gateway.circuit_manager.gateway_config = gateway.gateway_config

    # refresh capacity calculator
    gateway.refresh_capacity_calculation()


async def apply_gateway_config_from_cache(
        *,
        gateway_adapter,
        tenant: str,
        project: str,
        redis_url: Optional[str] = None,
) -> bool:
    cfg = await load_gateway_config_from_cache(tenant=tenant, project=project, redis_url=redis_url)
    if not cfg:
        return False
    apply_gateway_config_snapshot(gateway_adapter.gateway, cfg)
    set_gateway_config(gateway_adapter.gateway.gateway_config)
    if hasattr(gateway_adapter, "policy") and getattr(gateway_adapter.policy, "set_guarded_patterns", None):
        gateway_adapter.policy.set_guarded_patterns(gateway_adapter.gateway.gateway_config.guarded_rest_patterns)
    return True


async def subscribe_gateway_config_updates(
        *,
        gateway_adapter,
        tenant: str,
        project: str,
        redis_url: Optional[str] = None,
        stop_event: Optional[asyncio.Event] = None,
) -> None:
    if not redis_url:
        return
    channel = ns_key(CONFIG.GATEWAY.UPDATE_CHANNEL, tenant=tenant, project=project)
    from redis import asyncio as aioredis
    redis = aioredis.from_url(redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        while True:
            if stop_event and stop_event.is_set():
                break
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not msg:
                await asyncio.sleep(0.1)
                continue
            if msg.get("type") != "message":
                continue
            try:
                payload = json.loads(msg.get("data") or "{}")
                cfg_data = payload.get("config")
                if not cfg_data:
                    continue
                cfg = _config_from_dict(cfg_data)
                apply_gateway_config_snapshot(gateway_adapter.gateway, cfg)
                set_gateway_config(gateway_adapter.gateway.gateway_config)
                if hasattr(gateway_adapter, "policy") and getattr(gateway_adapter.policy, "set_guarded_patterns", None):
                    gateway_adapter.policy.set_guarded_patterns(gateway_adapter.gateway.gateway_config.guarded_rest_patterns)
            except Exception:
                continue
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
        except Exception:
            pass
        try:
            await redis.close()
        except Exception:
            pass

class GatewayConfigurationManager:
    """Manages gateway configuration changes and automatically updates all components"""

    def __init__(self, gateway_adapter):
        self.gateway_adapter = gateway_adapter
        self.gateway = gateway_adapter.gateway

    async def update_capacity_settings(self, **kwargs):
        """Update capacity settings and refresh all dependent calculations"""
        target_tenant = kwargs.pop("tenant", None) or kwargs.pop("tenant_id", None)
        target_project = kwargs.pop("project", None) or kwargs.pop("project_id", None)
        service_capacity_payload = kwargs.pop("service_capacity", None) or {}
        backpressure_payload = kwargs.pop("backpressure", None) or {}
        rate_limits_payload = kwargs.pop("rate_limits", None) or {}
        guarded_rest_patterns = kwargs.pop("guarded_rest_patterns", None)
        base_config = get_gateway_config()
        config = _config_from_dict(_serialize_gateway_config(base_config))
        if target_tenant:
            config.tenant_id = target_tenant
        if target_project:
            config.project_id = target_project

        # Update capacity settings
        merged_service_capacity = {**service_capacity_payload, **kwargs}
        if 'concurrent_per_process' in merged_service_capacity:
            config.service_capacity.concurrent_requests_per_process = merged_service_capacity['concurrent_per_process']
        if 'processes_per_instance' in merged_service_capacity:
            config.service_capacity.processes_per_instance = merged_service_capacity['processes_per_instance']
        if 'avg_processing_time' in merged_service_capacity:
            config.service_capacity.avg_processing_time_seconds = merged_service_capacity['avg_processing_time']
        if 'avg_processing_time_seconds' in merged_service_capacity:
            config.service_capacity.avg_processing_time_seconds = merged_service_capacity['avg_processing_time_seconds']

        # Update thresholds
        merged_backpressure = {**backpressure_payload, **kwargs}
        if 'capacity_buffer' in merged_backpressure:
            config.backpressure.capacity_buffer = merged_backpressure['capacity_buffer']
        if 'queue_depth_multiplier' in merged_backpressure:
            config.backpressure.queue_depth_multiplier = merged_backpressure['queue_depth_multiplier']
        if 'anonymous_pressure_threshold' in merged_backpressure:
            config.backpressure.anonymous_pressure_threshold = merged_backpressure['anonymous_pressure_threshold']
        if 'anonymous_threshold' in merged_backpressure:
            config.backpressure.anonymous_pressure_threshold = merged_backpressure['anonymous_threshold']
        if 'registered_pressure_threshold' in merged_backpressure:
            config.backpressure.registered_pressure_threshold = merged_backpressure['registered_pressure_threshold']
        if 'registered_threshold' in merged_backpressure:
            config.backpressure.registered_pressure_threshold = merged_backpressure['registered_threshold']
        if 'paid_pressure_threshold' in merged_backpressure:
            config.backpressure.paid_pressure_threshold = merged_backpressure['paid_pressure_threshold']
        if 'paid_threshold' in merged_backpressure:
            config.backpressure.paid_pressure_threshold = merged_backpressure['paid_threshold']
        if 'hard_limit_threshold' in merged_backpressure:
            config.backpressure.hard_limit_threshold = merged_backpressure['hard_limit_threshold']

        # Role-based rate limits
        roles_payload = rate_limits_payload.get('roles') if isinstance(rate_limits_payload, dict) else None
        roles_payload = roles_payload if roles_payload is not None else rate_limits_payload
        if isinstance(roles_payload, dict):
            for role, cfg in roles_payload.items():
                if not isinstance(cfg, dict):
                    continue
                config.rate_limits.roles[str(role)] = RoleRateLimit(
                    hourly=int(cfg.get("hourly", 50)),
                    burst=int(cfg.get("burst", 5)),
                    burst_window=int(cfg.get("burst_window", 60)),
                )

        if isinstance(guarded_rest_patterns, list):
            config.guarded_rest_patterns = [str(p) for p in guarded_rest_patterns if p]
            if not config.guarded_rest_patterns:
                config.guarded_rest_patterns = list(DEFAULT_GUARDED_REST_PATTERNS)

        # Enforce env-derived service capacity for this instance
        apply_service_capacity_env_overrides(config)

        is_local_target = (
            (config.tenant_id == base_config.tenant_id) and
            (config.project_id == base_config.project_id)
        )

        if is_local_target:
            # Apply config snapshot to gateway + refresh
            apply_gateway_config_snapshot(self.gateway, config)
            # Update global config
            set_gateway_config(self.gateway.gateway_config)
            applied = self.gateway.gateway_config
        else:
            applied = config

        # Persist + publish (best-effort) for the target tenant/project
        try:
            await save_gateway_config_to_cache(applied)
            await publish_gateway_config_update(applied, actor="admin")
        except Exception:
            pass

        logger.info("Gateway configuration updated - persisted for tenant/project")

        return applied

    async def get_current_metrics(self):
        """Get current capacity metrics"""
        return await self.gateway.capacity_calculator.get_monitoring_data()

    async def reset_to_env(self, **kwargs):
        """
        Reset gateway config to env defaults and persist to Redis.
        If tenant/project provided, resets that target; local instance only applies its own.
        """
        target_tenant = kwargs.pop("tenant", None) or kwargs.pop("tenant_id", None)
        target_project = kwargs.pop("project", None) or kwargs.pop("project_id", None)
        dry_run = bool(kwargs.pop("dry_run", False))

        base_config = GatewayConfigFactory.create_from_env()
        if target_tenant:
            base_config.tenant_id = target_tenant
        if target_project:
            base_config.project_id = target_project

        is_local_target = (
            (base_config.tenant_id == self.gateway.gateway_config.tenant_id) and
            (base_config.project_id == self.gateway.gateway_config.project_id)
        )

        if is_local_target:
            apply_gateway_config_snapshot(self.gateway, base_config)
            set_gateway_config(self.gateway.gateway_config)
            applied = self.gateway.gateway_config
        else:
            applied = base_config

        if not dry_run:
            try:
                await save_gateway_config_to_cache(applied)
                await publish_gateway_config_update(applied, actor="admin.reset")
            except Exception:
                pass

        return applied

    async def validate_proposed_changes(self, **kwargs):
        """Validate proposed configuration changes before applying"""
        target_tenant = kwargs.pop("tenant", None) or kwargs.pop("tenant_id", None)
        target_project = kwargs.pop("project", None) or kwargs.pop("project_id", None)
        service_capacity_payload = kwargs.pop("service_capacity", None) or {}
        backpressure_payload = kwargs.pop("backpressure", None) or {}
        rate_limits_payload = kwargs.pop("rate_limits", None) or {}
        guarded_rest_patterns = kwargs.pop("guarded_rest_patterns", None)
        # Create a temporary config with proposed changes (do not mutate global)
        temp_config = _config_from_dict(_serialize_gateway_config(get_gateway_config()))
        if target_tenant:
            temp_config.tenant_id = target_tenant
        if target_project:
            temp_config.project_id = target_project

        # Apply proposed changes to temp config
        for key, value in kwargs.items():
            if hasattr(temp_config.service_capacity, key):
                setattr(temp_config.service_capacity, key, value)
            elif hasattr(temp_config.backpressure, key):
                setattr(temp_config.backpressure, key, value)

        merged_service_capacity = {**service_capacity_payload}
        for key, value in merged_service_capacity.items():
            if hasattr(temp_config.service_capacity, key):
                setattr(temp_config.service_capacity, key, value)

        merged_backpressure = {**backpressure_payload}
        for key, value in merged_backpressure.items():
            if hasattr(temp_config.backpressure, key):
                setattr(temp_config.backpressure, key, value)

        roles_payload = rate_limits_payload.get("roles") if isinstance(rate_limits_payload, dict) else None
        roles_payload = roles_payload if roles_payload is not None else rate_limits_payload
        if isinstance(roles_payload, dict):
            for role, cfg in roles_payload.items():
                if not isinstance(cfg, dict):
                    continue
                temp_config.rate_limits.roles[str(role)] = RoleRateLimit(
                    hourly=int(cfg.get("hourly", 50)),
                    burst=int(cfg.get("burst", 5)),
                    burst_window=int(cfg.get("burst_window", 60)),
                )

        if isinstance(guarded_rest_patterns, list):
            temp_config.guarded_rest_patterns = [str(p) for p in guarded_rest_patterns if p]
            if not temp_config.guarded_rest_patterns:
                temp_config.guarded_rest_patterns = list(DEFAULT_GUARDED_REST_PATTERNS)

        # Validate the temporary config
        validation = get_validation_summary(temp_config)

        return validation
