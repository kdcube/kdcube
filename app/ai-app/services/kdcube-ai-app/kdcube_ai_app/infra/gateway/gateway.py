# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/gateway/gateway.py
"""
Simplified, framework-agnostic request gateway
"""
import time
from dataclasses import asdict
from typing import Dict, Any, Optional, List, Tuple
import logging

from kdcube_ai_app.auth.AuthManager import AuthManager, AuthenticationError, RequirementBase, PRIVILEGED_ROLES, \
    PAYED_ROLES
from kdcube_ai_app.infra.gateway.backpressure import BackpressureError, BackpressureManager, \
    create_atomic_backpressure_manager
from kdcube_ai_app.infra.gateway.circuit_breaker import CircuitBreakerError, CircuitState, \
    QueueAwareCircuitBreakerManager
from kdcube_ai_app.infra.gateway.definitions import DynamicCapacityCalculator
from kdcube_ai_app.infra.gateway.rate_limiter import RateLimitError, RateLimiter
from kdcube_ai_app.infra.gateway.thorttling import ThrottlingMonitor
from kdcube_ai_app.infra.gateway.config import GatewayConfiguration, validate_gateway_config
from kdcube_ai_app.auth.sessions import SessionManager, UserType, UserSession, RequestContext

logger = logging.getLogger(__name__)


class RequestGateway:
    """Main request gateway - orchestrates all components"""

    def __init__(self, gateway_config: GatewayConfiguration, auth_manager: AuthManager):
        # Validate configuration
        config_issues = validate_gateway_config(gateway_config)
        if config_issues:
            logger.warning(f"Gateway configuration issues: {config_issues}")

        self.gateway_config = gateway_config
        self.auth_manager = auth_manager

        # Initialize components with centralized config
        self.throttling_monitor = ThrottlingMonitor(gateway_config.redis_url,
                                                    gateway_config=gateway_config)
        self.session_manager = SessionManager(
            gateway_config.redis_url,
            tenant=gateway_config.tenant_id,
            project=gateway_config.project_id,
            session_ttl=gateway_config.redis.session_ttl
        )
        self.rate_limiter = RateLimiter(
            gateway_config.redis_url,
            gateway_config,
            self.throttling_monitor
        )
        self.backpressure_manager = create_atomic_backpressure_manager(gateway_config.redis_url,
                                                                       gateway_config,
                                                                       self.throttling_monitor)

        # Create dynamic capacity calculator (will be injected into backpressure manager)
        self.capacity_calculator = None  # Will be set after Redis is initialized

        # Circuit breaker manager with config
        self.circuit_manager = QueueAwareCircuitBreakerManager(
            gateway_config=gateway_config,
            throttling_monitor=self.throttling_monitor,
            backpressure_manager=self.backpressure_manager  # Pass backpressure manager
        )
        self._setup_circuit_breakers()

    async def _ensure_capacity_calculator(self):
        """Ensure capacity calculator is initialized with Redis client"""
        if self.capacity_calculator is None:
            await self.backpressure_manager.init_redis()
            self.capacity_calculator = DynamicCapacityCalculator(
                self.gateway_config,
                self.backpressure_manager.redis
            )
            # Inject into backpressure manager
            self.backpressure_manager.set_capacity_calculator(self.capacity_calculator)

    def _setup_circuit_breakers(self):
        """Setup circuit breakers with configuration"""
        from kdcube_ai_app.infra.gateway.circuit_breaker import CircuitBreakerConfig

        cb_config = self.gateway_config.circuit_breakers

        # Authentication circuit breaker
        auth_config = CircuitBreakerConfig(
            failure_threshold=cb_config.auth_failure_threshold,
            recovery_timeout=cb_config.auth_recovery_timeout,
            success_threshold=cb_config.auth_success_threshold,
            window_size=cb_config.auth_window_size,
            half_open_max_calls=cb_config.auth_half_open_max_calls
        )
        self.circuit_manager.get_circuit_breaker("authentication", auth_config)

        # Rate limiter circuit breaker
        rate_config = CircuitBreakerConfig(
            failure_threshold=cb_config.rate_limit_failure_threshold,
            recovery_timeout=cb_config.rate_limit_recovery_timeout,
            success_threshold=cb_config.rate_limit_success_threshold,
            window_size=cb_config.rate_limit_window_size,
            half_open_max_calls=cb_config.rate_limit_half_open_max_calls
        )
        self.circuit_manager.get_circuit_breaker("rate_limiter", rate_config)

        # Backpressure circuit breaker
        bp_config = CircuitBreakerConfig(
            failure_threshold=cb_config.backpressure_failure_threshold,
            recovery_timeout=cb_config.backpressure_recovery_timeout,
            success_threshold=cb_config.backpressure_success_threshold,
            window_size=cb_config.backpressure_window_size,
            half_open_max_calls=cb_config.backpressure_half_open_max_calls
        )
        self.circuit_manager.get_circuit_breaker("backpressure", bp_config)

    async def process_request(self,
                              context: RequestContext,
                              requirements: List[RequirementBase] = None,
                              endpoint: str = "/api/chat",
                              bypass_throttling: bool = False,
                              bypass_gate: bool = False) -> UserSession:
        """Process request through all gateway layers with optional bypass"""

        # Check if this is a privileged admin/monitoring endpoint
        is_admin_endpoint = any(endpoint.startswith(path) for path in [
            "/admin", "/monitoring", "/health", "/debug"
        ])

        # Get circuit breakers
        auth_circuit = self.circuit_manager.get_circuit_breaker("authentication")
        rate_limit_circuit = self.circuit_manager.get_circuit_breaker("rate_limiter")
        backpressure_circuit = self.circuit_manager.get_circuit_breaker("backpressure")

        session = None

        try:
            # Step 1: Authentication with circuit breaker
            try:
                user_type, user_data = await self._authenticate(context)
                session = await self.session_manager.get_or_create_session(context, user_type, user_data)

                # Check auth circuit breaker
                await auth_circuit.check_request_allowed(session)
                await auth_circuit.record_success()

            except Exception as e:
                await auth_circuit.record_failure("authentication_error")
                raise

            # Step 2: Authorization (if requirements specified)
            if requirements:
                user = session.to_user()
                for requirement in requirements:
                    validation_error = requirement.validate_requirement(user)
                    if validation_error:
                        from kdcube_ai_app.auth.AuthManager import AuthorizationError
                        raise AuthorizationError(validation_error.message, validation_error.code)

            if bypass_gate:
                return session

            # Step 3: Check if privileged user on admin endpoint (bypass throttling)
            if (is_admin_endpoint and
                session.user_type == UserType.PRIVILEGED and
                bypass_throttling):

                # logger.info(f"Bypassing throttling for privileged user on admin endpoint: {endpoint}")
                await self.throttling_monitor.record_request(session)
                return session

            # Step 4: Rate Limiting (skip for privileged on admin endpoints)
            if not (is_admin_endpoint and session.user_type == UserType.PRIVILEGED):
                try:
                    await rate_limit_circuit.check_request_allowed(session)
                    await self.rate_limiter.check_and_record(session, context, endpoint)
                    await rate_limit_circuit.record_success()

                except (RateLimitError, CircuitBreakerError) as e:
                    # if isinstance(e, RateLimitError):
                    #     await rate_limit_circuit.record_failure("rate_limit_exceeded")
                    raise
                except Exception as e:
                    await rate_limit_circuit.record_failure("rate_limit_error")
                    raise

            # Step 5: Backpressure (skip for privileged on admin endpoints)
            if not (is_admin_endpoint and session.user_type == UserType.PRIVILEGED):
                try:
                    await backpressure_circuit.check_request_allowed(session)
                    await self.backpressure_manager.check_capacity(session.user_type, session, context, endpoint)
                    await backpressure_circuit.record_success()

                except (BackpressureError, CircuitBreakerError) as e:
                    if isinstance(e, BackpressureError):
                        await backpressure_circuit.record_failure("backpressure_exceeded")
                    raise
                except Exception as e:
                    await backpressure_circuit.record_failure("backpressure_error")
                    raise

            # Step 6: Record successful request
            await self.throttling_monitor.record_request(session)
            return session

        except CircuitBreakerError as e:
            # Record circuit breaker event in throttling monitor
            if session:
                await self.circuit_manager.record_circuit_breaker_event(
                    e.circuit_name, session, context, endpoint, e.retry_after
                )
            raise

    async def get_throttling_stats(self) -> Dict[str, Any]:
        """Get throttling statistics"""
        stats = await self.throttling_monitor.get_throttling_stats()
        system_status = await self.get_system_status()

        return {
            "throttling": asdict(stats),
            "system": system_status
        }

    async def get_throttling_events(self, limit: int = 50) -> List[Dict]:
        """Get recent throttling events"""
        events = await self.throttling_monitor.get_recent_events(limit)
        return [asdict(event) for event in events]

    async def _authenticate(self, context: RequestContext) -> Tuple[UserType, Optional[Dict]]:
        """Authenticate request"""
        if not context.authorization_header or not self.auth_manager:
            return UserType.ANONYMOUS, None

        try:
            # Extract token
            parts = context.authorization_header.split(" ", 1)
            if len(parts) != 2 or parts[0].lower() != "bearer":
                return UserType.ANONYMOUS, None
            id_token = context.id_token

            token = parts[1]
            user = await self.auth_manager.authenticate_with_both(token, id_token)

            if PRIVILEGED_ROLES & set(user.roles):
                user_type = UserType.PRIVILEGED
            elif PAYED_ROLES & set(user.roles):
                user_type = UserType.PAYED
            else:
                user_type = UserType.REGISTERED
            return user_type, {
                "user_id": getattr(user, 'sub', None) or user.username,
                "username": user.username,
                "email": user.email,
                "roles": user.roles,
                "permissions": user.permissions
            }

        except (AuthenticationError, Exception) as ex:
            return UserType.ANONYMOUS, None

    async def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status with automatic capacity transparency"""
        await self._ensure_capacity_calculator()

        queue_stats = await self.backpressure_manager.get_queue_stats()

        # Get dynamic capacity data (automatically uses actual processes)
        capacity_data = await self.capacity_calculator.get_monitoring_data()

        base_status = {
            "timestamp": time.time(),

            # Automatically computed capacity info
            "capacity_transparency": capacity_data,

            # Gateway configuration (as before)
            "gateway_configuration": self.gateway_config.to_dict(),

            # Queue stats with enhanced capacity context
            "queue_stats": {
                "anonymous": queue_stats.anonymous_queue,
                "registered": queue_stats.registered_queue,
                "privileged": queue_stats.privileged_queue,
                "total": queue_stats.total_queue,
                "capacity_context": {
                    "base_capacity_per_instance": queue_stats.base_capacity_per_instance,
                    "alive_instances": queue_stats.alive_instances,
                    "instance_count": queue_stats.instance_count,
                    "weighted_max_capacity": queue_stats.weighted_max_capacity,
                    "pressure_ratio": queue_stats.pressure_ratio,
                    "accepting_anonymous": queue_stats.accepting_anonymous,
                    "accepting_registered": queue_stats.accepting_registered,
                    "accepting_privileged": queue_stats.accepting_privileged,
                    "thresholds": {
                        "anonymous_threshold": queue_stats.anonymous_threshold,
                        "registered_threshold": queue_stats.registered_threshold,
                        "hard_limit_threshold": queue_stats.hard_limit_threshold
                    }
                },
                "analytics": {
                    "avg_wait_times": queue_stats.avg_wait_times,
                    "throughput_metrics": queue_stats.throughput_metrics
                }
            },

            "rate_limits": {
                user_type.value: {
                    "requests_per_hour": config.requests_per_hour,
                    "burst_limit": config.burst_limit,
                    "burst_window": config.burst_window
                }
                for user_type, config in self.rate_limiter.limits.items()
            }
        }

        # Add circuit breaker stats
        circuit_stats = await self.circuit_manager.get_all_stats()
        circuit_summary = {
            "total_circuits": len(circuit_stats),
            "open_circuits": len([s for s in circuit_stats.values() if s.state == CircuitState.OPEN]),
            "half_open_circuits": len([s for s in circuit_stats.values() if s.state == CircuitState.HALF_OPEN]),
            "closed_circuits": len([s for s in circuit_stats.values() if s.state == CircuitState.CLOSED]),
        }

        # Convert stats to serializable format
        serializable_circuit_stats = {}
        for name, stats in circuit_stats.items():
            stats_dict = asdict(stats)
            stats_dict['state'] = stats.state.value
            serializable_circuit_stats[name] = stats_dict

        base_status.update({
            "circuit_breakers": {
                "summary": circuit_summary,
                "circuits": serializable_circuit_stats
            }
        })

        return base_status

    # Add method to refresh capacity if config changes
    def refresh_capacity_calculation(self):
        """Refresh capacity calculation (call when config changes)"""
        if self.capacity_calculator:
            self.capacity_calculator.refresh_cache()

# Factory function for easy setup with centralized config
def create_gateway_from_config(gateway_config: GatewayConfiguration, auth_manager: AuthManager) -> RequestGateway:
    """Create gateway from centralized configuration"""

    # Log configuration details
    logger.info(f"Creating gateway with profile: {gateway_config.profile.value}")
    logger.info(f"Service capacity: {gateway_config.service_capacity.concurrent_requests_per_instance} concurrent, "
                f"{gateway_config.service_capacity.avg_processing_time_seconds}s avg processing")
    logger.info(f"Rate limits - Anonymous: {gateway_config.rate_limits.anonymous_hourly}/hr, "
                f"Registered: {gateway_config.rate_limits.registered_hourly}/hr")

    # Validate configuration
    issues = validate_gateway_config(gateway_config)
    if issues:
        logger.warning(f"Configuration validation issues: {issues}")

    return RequestGateway(gateway_config, auth_manager)