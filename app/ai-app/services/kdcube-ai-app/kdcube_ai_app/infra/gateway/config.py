# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/gateway/config.py
"""
Centralized Gateway Configuration
All gateway-related settings in one place for easy management and monitoring
"""
import os
import logging

from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional
from enum import Enum

from kdcube_ai_app.infra.gateway.definitions import ServiceCapacity, CapacityBasedBackpressureConfig

logger = logging.getLogger(__name__)

class GatewayProfile(Enum):
    """Predefined gateway configuration profiles"""
    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"
    LOAD_TEST = "load_test"


@dataclass
class RateLimitSettings:
    """Rate limiting settings per user type"""
    anonymous_hourly: int = 50
    anonymous_burst: int = 5
    anonymous_burst_window: int = 60

    registered_hourly: int = 500
    registered_burst: int = 20
    registered_burst_window: int = 60

    privileged_hourly: int = -1  # -1 means unlimited
    privileged_burst: int = 100
    privileged_burst_window: int = 60


@dataclass
class ServiceCapacitySettings:
    """Service capacity configuration - now process-aware"""
    concurrent_requests_per_process: int = 5  # MAX_CONCURRENT_CHAT
    avg_processing_time_seconds: float = 25.0
    processes_per_instance: int = None  # Auto-detected from CHAT_APP_PARALLELISM

    # Computed properties (will be calculated)
    concurrent_requests_per_instance: int = None
    requests_per_hour: Optional[int] = None

    def __post_init__(self):
        if self.processes_per_instance is None:
            self.processes_per_instance = int(os.getenv("CHAT_APP_PARALLELISM", "1"))

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
            "hard_limit": int(actual_system_capacity * self.backpressure.hard_limit_threshold),
            "total_capacity": actual_system_capacity
        }

class GatewayConfigFactory:
    """Factory for creating gateway configurations"""

    @staticmethod
    def create_from_env(profile: GatewayProfile = None) -> GatewayConfiguration:
        """Create configuration from environment variables"""

        # Auto-detect profile if not specified
        if profile is None:
            env_profile = os.getenv("GATEWAY_PROFILE", "development").lower()
            profile = GatewayProfile(env_profile)

        # Environment variables with defaults
        redis_password = os.getenv("REDIS_PASSWORD", "")
        redis_host = os.getenv("REDIS_HOST", "localhost")
        redis_port = os.getenv("REDIS_PORT", "6379")
        redis_url = f"redis://:{redis_password}@{redis_host}:{redis_port}/0"

        instance_id = os.getenv("INSTANCE_ID", "default-instance")
        tenant_id = os.getenv("TENANT_ID", "default-tenant")
        project_id = os.getenv("DEFAULT_PROJECT_NAME", "default-tenant")

        # Rate limiting from environment
        rate_limits = RateLimitSettings(
            anonymous_hourly=int(os.getenv("ANON_RATE_LIMIT", "50")),
            anonymous_burst=int(os.getenv("ANON_BURST_LIMIT", "5")),
            registered_hourly=int(os.getenv("REG_RATE_LIMIT", "500")),
            registered_burst=int(os.getenv("REG_BURST_LIMIT", "20")),
            privileged_hourly=int(os.getenv("PRIV_RATE_LIMIT", "-1")),
            privileged_burst=int(os.getenv("PRIV_BURST_LIMIT", "100"))
        )

        # Service capacity from environment
        service_capacity = ServiceCapacitySettings(
            concurrent_requests_per_process=int(os.getenv("MAX_CONCURRENT_CHAT", "5")), # CONCURRENT_REQUESTS_PER_PROCESS
            avg_processing_time_seconds=float(os.getenv("AVG_PROCESSING_TIME_SECONDS", "25.0")),
            processes_per_instance=int(os.getenv("CHAT_APP_PARALLELISM", "1"))
        )

        # Apply profile-specific overrides
        rate_limits, service_capacity, backpressure, circuit_breakers = GatewayConfigFactory._apply_profile_overrides(
            profile, rate_limits, service_capacity
        )

        return GatewayConfiguration(
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
            project_id=project_id
        )

    @staticmethod
    def _apply_profile_overrides(profile: GatewayProfile,
                                 rate_limits: RateLimitSettings,
                                 service_capacity: ServiceCapacitySettings) -> tuple:
        """Apply profile-specific configuration overrides"""

        backpressure = BackpressureSettings()
        circuit_breakers = CircuitBreakerSettings()

        if profile == GatewayProfile.DEVELOPMENT:
            # Development: More permissive settings
            rate_limits.anonymous_hourly = 100
            rate_limits.registered_hourly = 1000
            backpressure.anonymous_pressure_threshold = 0.8
            backpressure.registered_pressure_threshold = 0.9
            circuit_breakers.auth_failure_threshold = 20

        elif profile == GatewayProfile.TESTING:
            # Testing: Moderate settings
            rate_limits.anonymous_hourly = 200
            rate_limits.registered_hourly = 2000
            service_capacity.concurrent_requests_per_instance = 10
            backpressure.capacity_buffer = 0.15

        elif profile == GatewayProfile.PRODUCTION:
            # Production: Conservative settings
            rate_limits.anonymous_hourly = 50
            rate_limits.registered_hourly = 500
            backpressure.capacity_buffer = 0.25
            backpressure.anonymous_pressure_threshold = 0.5
            backpressure.registered_pressure_threshold = 0.7
            circuit_breakers.auth_failure_threshold = 10

        elif profile == GatewayProfile.LOAD_TEST:
            # Load testing: High capacity, detailed monitoring
            rate_limits.anonymous_hourly = 5000
            rate_limits.registered_hourly = 10000
            service_capacity.concurrent_requests_per_instance = 20
            service_capacity.avg_processing_time_seconds = 15.0
            backpressure.capacity_buffer = 0.1
            backpressure.queue_depth_multiplier = 3.0

        return rate_limits, service_capacity, backpressure, circuit_breakers

    @staticmethod
    def create_for_chat_service() -> GatewayConfiguration:
        """Create optimized configuration for chat service"""
        config = GatewayConfigFactory.create_from_env()

        # Chat-specific optimizations with process awareness
        max_concurrent_per_process = int(os.getenv("MAX_CONCURRENT_CHAT", "5"))
        processes = int(os.getenv("CHAT_APP_PARALLELISM", "1"))

        config.service_capacity.concurrent_requests_per_process = max_concurrent_per_process
        config.service_capacity.processes_per_instance = processes
        config.service_capacity.concurrent_requests_per_instance = max_concurrent_per_process * processes
        config.service_capacity.avg_processing_time_seconds = 25.0
        config.backpressure.queue_depth_multiplier = 2.0
        config.backpressure.anonymous_pressure_threshold = 0.6

        return config

    @staticmethod
    def create_for_api_service() -> GatewayConfiguration:
        """Create optimized configuration for fast API service"""
        config = GatewayConfigFactory.create_from_env()

        # API-specific optimizations
        config.service_capacity.concurrent_requests_per_instance = 50
        config.service_capacity.avg_processing_time_seconds = 2.0
        config.rate_limits.anonymous_hourly = 1000
        config.rate_limits.registered_hourly = 10000
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
    if config.rate_limits.anonymous_hourly <= 0 and config.rate_limits.anonymous_hourly != -1:
        issues.append("Anonymous hourly rate limit must be positive or -1 for unlimited")

    if config.rate_limits.anonymous_burst <= 0:
        issues.append("Anonymous burst limit must be positive")

    if config.rate_limits.registered_hourly <= 0 and config.rate_limits.registered_hourly != -1:
        issues.append("Registered hourly rate limit must be positive or -1 for unlimited")

    if config.rate_limits.registered_burst <= 0:
        issues.append("Registered burst limit must be positive")

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

    if not (0 < config.backpressure.hard_limit_threshold <= 1):
        issues.append("Hard limit threshold must be between 0 and 1")

    # Threshold ordering validation
    if config.backpressure.anonymous_pressure_threshold >= config.backpressure.registered_pressure_threshold:
        issues.append(
            f"Anonymous pressure threshold ({config.backpressure.anonymous_pressure_threshold}) "
            f"must be less than registered threshold ({config.backpressure.registered_pressure_threshold})"
        )

    if config.backpressure.registered_pressure_threshold >= config.backpressure.hard_limit_threshold:
        issues.append(
            f"Registered pressure threshold ({config.backpressure.registered_pressure_threshold}) "
            f"must be less than hard limit threshold ({config.backpressure.hard_limit_threshold})"
        )

    # Performance validation - updated for new capacity structure
    try:
        service_capacity_obj = config.service_capacity_obj
        theoretical_throughput_per_instance = service_capacity_obj.requests_per_hour_per_instance

        # Validate anonymous rate limits against single instance throughput
        if (config.rate_limits.anonymous_hourly > theoretical_throughput_per_instance and
                config.rate_limits.anonymous_hourly != -1):
            issues.append(
                f"Anonymous rate limit ({config.rate_limits.anonymous_hourly}/hour) exceeds "
                f"theoretical throughput per instance ({theoretical_throughput_per_instance}/hour)"
            )

        # Validate registered rate limits against single instance throughput
        if (config.rate_limits.registered_hourly > theoretical_throughput_per_instance and
                config.rate_limits.registered_hourly != -1):
            issues.append(
                f"Registered rate limit ({config.rate_limits.registered_hourly}/hour) exceeds "
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

    # Environment consistency validation
    try:
        import os
        env_max_concurrent = int(os.getenv("MAX_CONCURRENT_CHAT", "5"))
        env_parallelism = int(os.getenv("CHAT_APP_PARALLELISM", "1"))

        if config.service_capacity.concurrent_requests_per_process != env_max_concurrent:
            issues.append(
                f"Config concurrent_requests_per_process ({config.service_capacity.concurrent_requests_per_process}) "
                f"doesn't match MAX_CONCURRENT_CHAT env var ({env_max_concurrent})"
            )

        if config.service_capacity.processes_per_instance != env_parallelism:
            issues.append(
                f"Config processes_per_instance ({config.service_capacity.processes_per_instance}) "
                f"doesn't match CHAT_APP_PARALLELISM env var ({env_parallelism})"
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
                "hard_limit_at": thresholds["hard_limit"],
                "anonymous_percentage": config.backpressure.anonymous_pressure_threshold * 100,
                "registered_percentage": config.backpressure.registered_pressure_threshold * 100,
                "hard_limit_percentage": config.backpressure.hard_limit_threshold * 100
            },
            "rate_limits": {
                "anonymous_hourly": config.rate_limits.anonymous_hourly,
                "registered_hourly": config.rate_limits.registered_hourly,
                "privileged_hourly": config.rate_limits.privileged_hourly
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
        rate_limits=RateLimitSettings(anonymous_hourly=30, registered_hourly=300),
        service_capacity=ServiceCapacitySettings(concurrent_requests_per_instance=6, avg_processing_time_seconds=30.0),
        backpressure=BackpressureSettings(capacity_buffer=0.3, anonymous_pressure_threshold=0.5),
        circuit_breakers=CircuitBreakerSettings(),
        monitoring=MonitoringSettings(),
        redis=RedisSettings(),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        instance_id=os.getenv("INSTANCE_ID", "chat-prod"),
        tenant_id=os.getenv("TENANT_ID", "production"),
        project_id=os.getenv("DEFAULT_PROJECT_NAME", "demo"),
    ),
    "api_high_throughput": lambda: GatewayConfigFactory.create_for_api_service(),
    "load_test_heavy": lambda: GatewayConfiguration(
        profile=GatewayProfile.LOAD_TEST,
        rate_limits=RateLimitSettings(anonymous_hourly=10000, registered_hourly=50000, privileged_hourly=-1),
        service_capacity=ServiceCapacitySettings(concurrent_requests_per_instance=25, avg_processing_time_seconds=10.0),
        backpressure=BackpressureSettings(capacity_buffer=0.05, queue_depth_multiplier=4.0),
        circuit_breakers=CircuitBreakerSettings(),
        monitoring=MonitoringSettings(),
        redis=RedisSettings(),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        instance_id=os.getenv("INSTANCE_ID", "load-test"),
        tenant_id=os.getenv("TENANT_ID", "testing"),
        project_id=os.getenv("DEFAULT_PROJECT_NAME", "demo"),
    )
}

class GatewayConfigurationManager:
    """Manages gateway configuration changes and automatically updates all components"""

    def __init__(self, gateway_adapter):
        self.gateway_adapter = gateway_adapter
        self.gateway = gateway_adapter.gateway

    async def update_capacity_settings(self, **kwargs):
        """Update capacity settings and refresh all dependent calculations"""
        config = get_gateway_config()

        # Update capacity settings
        if 'concurrent_per_process' in kwargs:
            config.service_capacity.concurrent_requests_per_process = kwargs['concurrent_per_process']
        if 'processes_per_instance' in kwargs:
            config.service_capacity.processes_per_instance = kwargs['processes_per_instance']
        if 'avg_processing_time' in kwargs:
            config.service_capacity.avg_processing_time_seconds = kwargs['avg_processing_time']
        if 'capacity_buffer' in kwargs:
            config.backpressure.capacity_buffer = kwargs['capacity_buffer']
        if 'queue_depth_multiplier' in kwargs:
            config.backpressure.queue_depth_multiplier = kwargs['queue_depth_multiplier']

        # Update thresholds
        if 'anonymous_threshold' in kwargs:
            config.backpressure.anonymous_pressure_threshold = kwargs['anonymous_threshold']
        if 'registered_threshold' in kwargs:
            config.backpressure.registered_pressure_threshold = kwargs['registered_threshold']
        if 'hard_limit_threshold' in kwargs:
            config.backpressure.hard_limit_threshold = kwargs['hard_limit_threshold']

        # Automatically refresh all dependent calculations
        self.gateway.refresh_capacity_calculation()

        # Update global config
        set_gateway_config(config)

        logger.info("Gateway configuration updated - all capacity calculations refreshed automatically")

        return config

    async def get_current_metrics(self):
        """Get current capacity metrics"""
        return self.gateway.capacity_calculator.get_monitoring_data()

    async def validate_proposed_changes(self, **kwargs):
        """Validate proposed configuration changes before applying"""
        # Create a temporary config with proposed changes
        temp_config = get_gateway_config()

        # Apply proposed changes to temp config
        for key, value in kwargs.items():
            if hasattr(temp_config.service_capacity, key):
                setattr(temp_config.service_capacity, key, value)
            elif hasattr(temp_config.backpressure, key):
                setattr(temp_config.backpressure, key, value)

        # Validate the temporary config
        validation = get_validation_summary(temp_config)

        return validation