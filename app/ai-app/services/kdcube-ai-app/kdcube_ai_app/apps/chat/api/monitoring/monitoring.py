# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

import os
import json
import time
from dataclasses import asdict
from datetime import datetime

from fastapi import Depends
from fastapi.responses import JSONResponse

from kdcube_ai_app.apps.chat.api.resolvers import auth_without_pressure, INSTANCE_ID, get_fastapi_adapter, require_auth, \
    reset_circuit_breaker, get_circuit_breaker_stats
from kdcube_ai_app.apps.middleware.gateway import CircuitBreakersResponse, CircuitBreakerSummaryResponse, \
    CircuitBreakerStatusResponse
from kdcube_ai_app.auth.AuthManager import RequireUser, RequireRoles
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.infra.availability.health_and_heartbeat import get_expected_services
from kdcube_ai_app.infra.gateway.circuit_breaker import CircuitState
from kdcube_ai_app.infra.gateway.config import GatewayConfigurationManager

"""
Monitoring API

File: api/monitoring/monitoring.py
"""
from fastapi import APIRouter, HTTPException
from typing import Optional, Callable, List, Dict, Any, Union
import logging

logger = logging.getLogger("Monitoring.API")

# Create router
router = APIRouter()

# ================================
# CIRCUIT BREAKER ADMIN ENDPOINTS
# ================================

@router.get("/admin/circuit-breakers", response_model=CircuitBreakersResponse)
async def get_circuit_breaker_status(
        session: UserSession = Depends(auth_without_pressure())
):
    """Get circuit breaker status (admin only)"""
    try:
        stats = await get_circuit_breaker_stats()

        # Convert to response format
        circuits = {}
        for name, cb_stats in stats.items():
            circuits[name] = CircuitBreakerStatusResponse(
                name=cb_stats.name,
                state=cb_stats.state.value,
                failure_count=cb_stats.failure_count,
                success_count=cb_stats.success_count,
                total_requests=cb_stats.total_requests,
                total_failures=cb_stats.total_failures,
                consecutive_failures=cb_stats.consecutive_failures,
                current_window_failures=cb_stats.current_window_failures,
                last_failure_time=cb_stats.last_failure_time,
                last_success_time=cb_stats.last_success_time,
                opened_at=cb_stats.opened_at
            )

        summary = CircuitBreakerSummaryResponse(
            total_circuits=len(stats),
            open_circuits=len([s for s in stats.values() if s.state == CircuitState.OPEN]),
            half_open_circuits=len([s for s in stats.values() if s.state == CircuitState.HALF_OPEN]),
            closed_circuits=len([s for s in stats.values() if s.state == CircuitState.CLOSED])
        )

        return CircuitBreakersResponse(summary=summary, circuits=circuits)

    except Exception as e:
        logger.error(f"Error getting circuit breaker status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/admin/circuit-breakers/{circuit_name}/reset")
async def reset_circuit_breaker_endpoint(
        circuit_name: str,
        session: UserSession = Depends(auth_without_pressure())
):
    """Reset a specific circuit breaker (admin only)"""
    try:
        await reset_circuit_breaker(circuit_name)
        return {
            "message": f"Circuit breaker '{circuit_name}' has been reset",
            "circuit_name": circuit_name,
            "reset_by": session.username,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error resetting circuit breaker {circuit_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/monitoring/system")
async def get_system_monitoring(
        session= Depends(auth_without_pressure())):
    """
    Enhanced system monitoring with chat REST vs other process tracking
    """
    try:
        middleware = router.state.middleware
        await middleware.init_redis()
        current_time = time.time()

        # Get expected services configuration
        expected_services = get_expected_services(INSTANCE_ID)

        # Get all process heartbeats from Redis
        process_pattern = f"{middleware.PROCESS_HEARTBEAT_PREFIX}:*"
        process_keys = await middleware.redis.keys(process_pattern)

        # Parse all heartbeats and separate chat REST from others
        all_heartbeats = {}
        chat_rest_heartbeats = {}

        for key in process_keys:
            try:
                data = await middleware.redis.get(key)
                if not data:
                    continue

                heartbeat = json.loads(data)
                instance_id = heartbeat['instance_id']
                service_type = heartbeat['service_type']
                service_name = heartbeat['service_name']
                service_key = f"{service_type}_{service_name}"

                # Initialize instance data
                if instance_id not in all_heartbeats:
                    all_heartbeats[instance_id] = {}
                    chat_rest_heartbeats[instance_id] = {}
                if service_key not in all_heartbeats[instance_id]:
                    all_heartbeats[instance_id][service_key] = []

                # Calculate age and normalize health status
                age_seconds = current_time - heartbeat.get('last_heartbeat', 0)
                raw_health = heartbeat.get('health_status', 'unknown')

                # Normalize health status
                if isinstance(raw_health, str):
                    if 'HEALTHY' in raw_health.upper() or raw_health.lower() == 'healthy':
                        status = 'healthy'
                    elif 'DEGRADED' in raw_health.upper() or raw_health.lower() == 'degraded':
                        status = 'degraded'
                    elif 'UNHEALTHY' in raw_health.upper() or raw_health.lower() == 'unhealthy':
                        status = 'unhealthy'
                    else:
                        status = 'unknown'
                else:
                    status = str(raw_health).lower()

                # Determine if process is stale
                if age_seconds > middleware.PROCESS_TIMEOUT:
                    status = 'stale'

                process_info = {
                    'pid': heartbeat.get('process_id'),
                    'port': heartbeat.get('port'),
                    'load': heartbeat.get('current_load', 0),
                    'capacity': heartbeat.get('max_capacity', 0),
                    'last_heartbeat': heartbeat.get('last_heartbeat', current_time),
                    'status': status,
                    'age_seconds': age_seconds,
                    'service_type': service_type,
                    'service_name': service_name
                }

                all_heartbeats[instance_id][service_key].append(process_info)

                # Separate chat REST processes for capacity calculations
                if service_type == "chat" and service_name == "rest":
                    if service_key not in chat_rest_heartbeats[instance_id]:
                        chat_rest_heartbeats[instance_id][service_key] = []
                    chat_rest_heartbeats[instance_id][service_key].append(process_info)

            except Exception as e:
                logger.error(f"Error parsing process heartbeat {key}: {e}")
                continue

        # Build service health for each instance
        instances_data = {}
        global_stats = {
            'total_expected': 0,
            'total_actual': 0,
            'total_healthy': 0,
            'total_load': 0,
            'total_capacity': 0,
            'healthy_instances': 0,
            'total_instances': 0,
            # Chat REST specific stats
            'chat_rest_expected': 0,
            'chat_rest_actual': 0,
            'chat_rest_healthy': 0,
            'chat_rest_load': 0,
            'chat_rest_capacity': 0
        }

        for instance_id, services_config in expected_services.items():
            instances_data[instance_id] = {}
            instance_healthy = True

            for service_config in services_config:
                service_key = service_config.get_service_key()

                # Get actual processes for this service
                actual_processes = all_heartbeats.get(instance_id, {}).get(service_key, [])

                # Count process states
                healthy_count = len([p for p in actual_processes if p['status'] == 'healthy'])
                degraded_count = len([p for p in actual_processes if p['status'] == 'degraded'])
                unhealthy_count = len([p for p in actual_processes if p['status'] == 'unhealthy'])
                stale_count = len([p for p in actual_processes if p['status'] == 'stale'])

                actual_count = len(actual_processes)
                missing_count = max(0, service_config.expected_processes - actual_count)

                # Calculate service-level health
                responsive_processes = healthy_count + degraded_count
                if responsive_processes == service_config.expected_processes:
                    if healthy_count == service_config.expected_processes:
                        overall_status = 'healthy'
                    else:
                        overall_status = 'degraded'
                elif responsive_processes > 0:
                    overall_status = 'degraded'
                else:
                    overall_status = 'unhealthy'
                    instance_healthy = False

                # Calculate totals
                total_load = sum(p['load'] for p in actual_processes)
                total_capacity = sum(p['capacity'] for p in actual_processes)

                # Store service data
                instances_data[instance_id][service_key] = {
                    'processes': service_config.expected_processes,
                    'actual_processes': actual_count,
                    'load': total_load,
                    'capacity': total_capacity,
                    'health': overall_status,
                    'healthy_processes': responsive_processes,
                    'missing_processes': missing_count,
                    'status_breakdown': {
                        'healthy': healthy_count,
                        'degraded': degraded_count,
                        'unhealthy': unhealthy_count,
                        'stale': stale_count,
                        'missing': missing_count
                    },
                    'pids': [p['pid'] for p in actual_processes],
                    'ports': [p['port'] for p in actual_processes if p['port']],
                    'loads': [p['load'] for p in actual_processes],
                    'heartbeats': [p['last_heartbeat'] for p in actual_processes],
                    'health_statuses': [p['status'] for p in actual_processes],
                    # Mark if this is a chat REST service
                    'is_chat_rest': service_config.service_type == "chat" and service_config.service_name == "rest"
                }

                # Update global stats
                global_stats['total_expected'] += service_config.expected_processes
                global_stats['total_actual'] += actual_count
                global_stats['total_healthy'] += responsive_processes
                global_stats['total_load'] += total_load
                global_stats['total_capacity'] += total_capacity

                # Update chat REST specific stats
                if service_config.service_type == "chat" and service_config.service_name == "rest":
                    global_stats['chat_rest_expected'] += service_config.expected_processes
                    global_stats['chat_rest_actual'] += actual_count
                    global_stats['chat_rest_healthy'] += responsive_processes
                    global_stats['chat_rest_load'] += total_load
                    global_stats['chat_rest_capacity'] += total_capacity

            if instance_healthy:
                global_stats['healthy_instances'] += 1
            global_stats['total_instances'] += 1

        # Get gateway system status with corrected capacity calculations
        gateway_adapter = router.state.gateway_adapter
        gateway_status = await gateway_adapter.gateway.get_system_status()

        # Extract the automatically computed data
        queue_data = gateway_status["queue_stats"]
        capacity_transparency = gateway_status["capacity_transparency"]

        # Get throttling statistics for multiple time periods
        throttling_stats = None
        throttling_events = None
        throttling_by_period = {}

        try:
            # Get throttling data for different time periods
            time_periods = [1, 3, 6, 12, 24]  # hours
            for hours in time_periods:
                period_data = await gateway_adapter.gateway.throttling_monitor.get_throttling_stats_for_period(hours)

                # Calculate throttle rate
                throttle_rate = 0
                total_attempted = 0
                if period_data.total_requests > 0:
                    estimated_successful = max(0, period_data.total_requests - period_data.total_throttled)
                    total_attempted = estimated_successful + period_data.total_throttled
                    throttle_rate = (period_data.total_throttled / total_attempted * 100) if total_attempted > 0 else 0

                throttling_by_period[f"{hours}h"] = {
                    'total_requests': period_data.total_requests,
                    'total_throttled': period_data.total_throttled,
                    'rate_limit_429': period_data.rate_limit_429,
                    'backpressure_503': period_data.backpressure_503,
                    'throttled_by_reason': period_data.throttled_by_reason,
                    'hourly_stats': period_data.hourly_stats,
                    'top_throttled_sessions': period_data.top_throttled_sessions,
                    'throttle_rate': round(throttle_rate, 2),
                    'total_attempted': total_attempted,
                    'events_per_hour': period_data.total_throttled / hours if hours > 0 else 0
                }

            # Use 1 hour data as default for backward compatibility
            throttling_stats = throttling_by_period.get("1h", {
                'total_requests': 0,
                'total_throttled': 0,
                'rate_limit_429': 0,
                'backpressure_503': 0,
                'throttle_rate': 0,
                'throttled_by_reason': {},
                'hourly_stats': {},
                'top_throttled_sessions': [],
                'recent_events_count': 0
            })

            # Get recent events (last hour)
            throttling_events_data = await gateway_adapter.gateway.throttling_monitor.get_recent_events_for_period(1, 20)

            # Convert enum values to strings
            serializable_events = []
            for event in throttling_events_data:
                event_copy = asdict(event)
                if 'reason' in event_copy and hasattr(event_copy['reason'], 'value'):
                    event_copy['reason'] = event_copy['reason'].value
                elif 'reason' in event_copy:
                    event_copy['reason'] = str(event_copy['reason'])
                serializable_events.append(event_copy)

            throttling_events = serializable_events
            throttling_stats['recent_events_count'] = len(throttling_events)

        except Exception as e:
            logger.error(f"Error getting throttling statistics: {e}")
            # Provide fallback data structure
            throttling_stats = {
                'total_requests': 0,
                'total_throttled': 0,
                'rate_limit_429': 0,
                'backpressure_503': 0,
                'throttle_rate': 0,
                'throttled_by_reason': {},
                'hourly_stats': {},
                'top_throttled_sessions': [],
                'recent_events_count': 0
            }
            throttling_events = []
            throttling_by_period = {"1h": throttling_stats}

        # Get circuit breaker status
        circuit_breakers = await get_circuit_breaker_status()

        # Enhanced response with detailed capacity breakdown
        response_data = {
            "instances": instances_data,
            "global_stats": {
                "load": global_stats['total_load'],
                "capacity": global_stats['total_capacity'],
                "healthy": global_stats['total_healthy'],
                "total": global_stats['total_expected'],
                "actual": global_stats['total_actual'],
                "healthy_instances": global_stats['healthy_instances'],
                "total_instances": global_stats['total_instances'],
                "utilization_percent": round((global_stats['total_load'] / global_stats['total_capacity'] * 100) if global_stats['total_capacity'] > 0 else 0, 1),
                # Chat REST specific metrics
                "chat_rest": {
                    "expected": global_stats['chat_rest_expected'],
                    "actual": global_stats['chat_rest_actual'],
                    "healthy": global_stats['chat_rest_healthy'],
                    "load": global_stats['chat_rest_load'],
                    "capacity": global_stats['chat_rest_capacity'],
                    "utilization_percent": round((global_stats['chat_rest_load'] / global_stats['chat_rest_capacity'] * 100) if global_stats['chat_rest_capacity'] > 0 else 0, 1)
                }
            },

            # Queue stats with chat REST capacity context
            "queue_stats": {
                "anonymous": queue_data["anonymous"],
                "registered": queue_data["registered"],
                "privileged": queue_data["privileged"]
            },
            "enhanced_queue_stats": {
                "anonymous_queue": queue_data["anonymous"],
                "registered_queue": queue_data["registered"],
                "privileged_queue": queue_data["privileged"],
                "total_queue": queue_data["total"],
                "base_capacity_per_instance": queue_data["capacity_context"]["base_capacity_per_instance"],
                "alive_instances": queue_data["capacity_context"]["alive_instances"],
                "instance_count": queue_data["capacity_context"]["instance_count"],
                "weighted_max_capacity": queue_data["capacity_context"]["weighted_max_capacity"],
                "pressure_ratio": queue_data["capacity_context"]["pressure_ratio"],
                "accepting_anonymous": queue_data["capacity_context"]["accepting_anonymous"],
                "accepting_registered": queue_data["capacity_context"]["accepting_registered"],
                "accepting_privileged": queue_data["capacity_context"]["accepting_privileged"]
            },

            # Backpressure policy with chat REST context
            "backpressure_policy": {
                "thresholds": queue_data["capacity_context"]["thresholds"],
                "current_effects": {
                    "anonymous_blocked": not queue_data["capacity_context"]["accepting_anonymous"],
                    "registered_blocked": not queue_data["capacity_context"]["accepting_registered"],
                    "all_blocked": not queue_data["capacity_context"]["accepting_privileged"],
                    "pressure_level": (
                        "critical" if queue_data["capacity_context"]["pressure_ratio"] > 0.9 else
                        "high" if queue_data["capacity_context"]["pressure_ratio"] > 0.8 else
                        "medium" if queue_data["capacity_context"]["pressure_ratio"] > 0.6 else
                        "low"
                    )
                },
                "capacity_scaling": {
                    "base_per_instance": capacity_transparency["capacity_metrics"].get("actual_runtime", {}).get("actual_total_capacity_per_instance", 0),
                    "instances_detected": capacity_transparency["instance_scaling"]["detected_instances"],
                    "total_weighted_capacity": capacity_transparency["instance_scaling"]["total_system_capacity"],
                    "utilization_percent": round(queue_data["capacity_context"]["pressure_ratio"] * 100, 1),
                    # Chat REST specific capacity info
                    "chat_rest_capacity": global_stats['chat_rest_capacity'],
                    "chat_rest_utilization": round((global_stats['chat_rest_load'] / global_stats['chat_rest_capacity'] * 100) if global_stats['chat_rest_capacity'] > 0 else 0, 1)
                }
            },

            # Queue analytics
            "queue_analytics": {
                "wait_times": queue_data["analytics"]["avg_wait_times"],
                "throughput": queue_data["analytics"]["throughput_metrics"],
                "individual_queues": {
                    "anonymous": {
                        "size": queue_data["anonymous"],
                        "avg_wait": queue_data["analytics"]["avg_wait_times"].get("anonymous", 0),
                        "throughput": queue_data["analytics"]["throughput_metrics"].get("anonymous", 0),
                        "blocked": not queue_data["capacity_context"]["accepting_anonymous"]
                    },
                    "registered": {
                        "size": queue_data["registered"],
                        "avg_wait": queue_data["analytics"]["avg_wait_times"].get("registered", 0),
                        "throughput": queue_data["analytics"]["throughput_metrics"].get("registered", 0),
                        "blocked": not queue_data["capacity_context"]["accepting_registered"]
                    },
                    "privileged": {
                        "size": queue_data["privileged"],
                        "avg_wait": queue_data["analytics"]["avg_wait_times"].get("privileged", 0),
                        "throughput": queue_data["analytics"]["throughput_metrics"].get("privileged", 0),
                        "blocked": not queue_data["capacity_context"]["accepting_privileged"]
                    }
                }
            },

            # Overall utilization based on chat REST capacity
            "queue_utilization": round((queue_data["total"] / queue_data["capacity_context"]["weighted_max_capacity"] * 100) if queue_data["capacity_context"]["weighted_max_capacity"] > 0 else 0, 1),

            # Capacity info
            "capacity_info": queue_data["capacity_context"],

            # Time-based throttling data (multiple periods)
            "throttling_stats": throttling_stats,  # Default 1h for backward compatibility
            "throttling_by_period": throttling_by_period,  # All time periods
            "recent_throttling_events": throttling_events,

            # Circuit breakers
            "circuit_breakers": circuit_breakers.model_dump(),

            # Gateway configuration
            "gateway_configuration": _extract_gateway_config_for_frontend(gateway_status),

            # Capacity transparency with chat REST focus
            "capacity_transparency": capacity_transparency,

            # Configuration details
            "configuration": _extract_detailed_config_for_frontend(capacity_transparency, gateway_status),

            "timestamp": current_time,
            "redis_info": {
                "process_keys_found": len(process_keys),
                "active_processes": global_stats['total_actual'],
                "chat_rest_processes": global_stats['chat_rest_actual'],
                "max_queue_size": queue_data["capacity_context"]["weighted_max_capacity"]
            }
        }

        return JSONResponse(content=response_data)

    except Exception as e:
        logger.error(f"Enhanced system monitoring endpoint error: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch system monitoring data: {str(e)}"
        )


def _extract_gateway_config_for_frontend(gateway_status: Dict[str, Any]) -> Dict[str, Any]:
    """Extract gateway configuration in format expected by React frontend"""
    config = gateway_status["gateway_configuration"]
    capacity = gateway_status.get("capacity_transparency", {})

    # Get capacity metrics safely
    capacity_metrics = capacity.get("capacity_metrics", {})
    actual_runtime = capacity_metrics.get("actual_runtime", {})
    configuration = capacity_metrics.get("configuration", {})

    rate_limits = config.get("rate_limits", {})
    roles = rate_limits.get("roles") if isinstance(rate_limits, dict) else None
    roles = roles if roles is not None else rate_limits
    return {
        "current_profile": config["profile"],
        "instance_id": config["instance_id"],
        "tenant_id": config["tenant_id"],
        "display_name": config["display_name"],
        "guarded_rest_patterns": config.get("guarded_rest_patterns", []),
        "rate_limits": roles or {},
        "service_capacity": {
            # Use actual runtime data if available, otherwise fall back to config
            "concurrent_requests_per_instance": actual_runtime.get(
                "actual_concurrent_per_instance",
                config["service_capacity"]["concurrent_requests_per_instance"]
            ),
            "avg_processing_time_seconds": configuration.get(
                "configured_avg_processing_time_seconds",
                config["service_capacity"]["avg_processing_time_seconds"]
            ),
            "requests_per_hour": actual_runtime.get(
                "actual_theoretical_hourly_per_instance",
                config.get("computed_metrics", {}).get("theoretical_throughput_per_instance", 0)
            )
        },
        "backpressure_settings": {
            "capacity_buffer": config["backpressure"]["capacity_buffer"],
            "queue_depth_multiplier": config["backpressure"]["queue_depth_multiplier"],
            "anonymous_pressure_threshold": config["backpressure"]["anonymous_pressure_threshold"],
            "registered_pressure_threshold": config["backpressure"]["registered_pressure_threshold"],
            "hard_limit_threshold": config["backpressure"]["hard_limit_threshold"]
        },
        "circuit_breaker_settings": config["circuit_breakers"],
        "monitoring_settings": config["monitoring"]
    }

def _extract_detailed_config_for_frontend(capacity_transparency: Dict[str, Any], gateway_status: Dict[str, Any]) -> Dict[str, Any]:
    """Extract detailed configuration for React frontend"""
    capacity = capacity_transparency.get("capacity_metrics", {})
    actual_runtime = capacity.get("actual_runtime", {})
    configuration = capacity.get("configuration", {})

    return {
        "expected_services": {
            INSTANCE_ID: [
                {
                    "service_key": svc.get_service_key(),
                    "expected_processes": svc.expected_processes,
                    "ports": svc.ports
                } for svc in get_expected_services(INSTANCE_ID)[INSTANCE_ID]
            ]
        },
        "queue_policy": {
            "service_capacity": {
                "concurrent_requests_per_instance": actual_runtime.get("actual_concurrent_per_instance", 0),
                "avg_processing_time_seconds": configuration.get("configured_avg_processing_time_seconds", 25.0),
                "requests_per_hour": actual_runtime.get("actual_theoretical_hourly_per_instance", 0)
            },
            "backpressure_thresholds": {
                "anonymous_pressure_threshold": capacity.get("threshold_ratios", {}).get("anonymous_threshold_ratio", 0.6),
                "registered_pressure_threshold": capacity.get("threshold_ratios", {}).get("registered_threshold_ratio", 0.8),
                "hard_limit_threshold": capacity.get("threshold_ratios", {}).get("hard_limit_threshold_ratio", 0.95),
                "capacity_buffer": configuration.get("capacity_buffer_percent", 20.0) / 100,
                "queue_depth_multiplier": configuration.get("queue_depth_multiplier", 2.0)
            }
        },
        "computed_metrics": actual_runtime
    }

@router.post("/admin/gateway/validate-config")
async def validate_config_changes(
        changes: Dict[str, Any],
        session: UserSession = Depends(auth_without_pressure())
):
    """Validate proposed configuration changes (admin only)"""
    gateway_adapter = get_fastapi_adapter()
    config_manager = GatewayConfigurationManager(gateway_adapter)

    validation = await config_manager.validate_proposed_changes(**changes)

    return {
        "validation_result": validation,
        "proposed_changes": changes,
        "recommendation": "safe to apply" if validation["is_valid"] else "fix errors before applying"
    }

@router.post("/admin/gateway/update-config")
async def update_gateway_config(
        changes: Dict[str, Any],
        session: UserSession = Depends(require_auth(
            RequireUser(),
            RequireRoles("kdcube:role:super-admin")
        ))
):
    """Update gateway configuration (admin only)"""
    gateway_adapter = get_fastapi_adapter()
    config_manager = GatewayConfigurationManager(gateway_adapter)

    # Validate first
    validation = await config_manager.validate_proposed_changes(**changes)
    if not validation["is_valid"]:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Configuration validation failed",
                "issues": validation["issues_by_severity"]["error"]
            }
        )

    # Apply changes
    updated_config = await config_manager.update_capacity_settings(**changes)
    new_metrics = await config_manager.get_current_metrics()

    return {
        "success": True,
        "message": "Configuration updated successfully",
        "applied_changes": changes,
        "updated_metrics": new_metrics,
        "validation": validation
    }

@router.post("/admin/gateway/reset-config")
async def reset_gateway_config(
        payload: Dict[str, Any],
        session: UserSession = Depends(require_auth(
            RequireUser(),
            RequireRoles("kdcube:role:super-admin")
        ))
):
    """
    Reset gateway config to env defaults.
    Optional payload: {"tenant": "...", "project": "..."}.
    """
    gateway_adapter = get_fastapi_adapter()
    config_manager = GatewayConfigurationManager(gateway_adapter)
    payload = payload or {}
    dry_run = bool(payload.get("dry_run", False))
    applied = await config_manager.reset_to_env(**payload)
    new_metrics = await config_manager.get_current_metrics()
    return {
        "success": True,
        "message": "Configuration reset to env defaults" if not dry_run else "Dry run: env defaults computed",
        "applied_config": applied.to_dict(),
        "updated_metrics": new_metrics
    }

@router.get("/debug/capacity-calculation")
async def debug_capacity_calculation(
        session: UserSession = Depends(auth_without_pressure())
):
    """Debug endpoint to understand capacity calculations"""
    try:
        gateway_adapter = router.state.gateway_adapter
        gateway = gateway_adapter.gateway

        # Get the gateway configuration
        config = gateway.gateway_config

        # Get the dynamic capacity calculator
        calculator = gateway.capacity_calculator
        if not calculator:
            await gateway.backpressure_manager.init_redis()
            calculator = gateway.capacity_calculator

        # Get actual process data
        actual_processes_data = await calculator.get_actual_process_info()

        # Get dynamic metrics
        metrics = await calculator.get_dynamic_metrics()

        # Get current queue stats
        queue_stats = await gateway.backpressure_manager.get_queue_stats()

        debug_info = {
            "configuration": {
                "concurrent_per_process": config.service_capacity.concurrent_requests_per_process,
                "processes_per_instance": config.service_capacity.processes_per_instance,
                "total_concurrent_per_instance": config.service_capacity.concurrent_requests_per_instance,
                "capacity_buffer": config.backpressure.capacity_buffer,
                "queue_depth_multiplier": config.backpressure.queue_depth_multiplier,
                "anonymous_threshold": config.backpressure.anonymous_pressure_threshold,
                "registered_threshold": config.backpressure.registered_pressure_threshold,
                "hard_limit_threshold": config.backpressure.hard_limit_threshold
            },

            "actual_processes": {
                "instances_found": list(actual_processes_data.keys()),
                "instance_count": len(actual_processes_data),
                "processes_per_instance": {
                    instance_id: [
                        {
                            "service_type": p.service_type,
                            "service_name": p.service_name,
                            "process_count": p.process_count,
                            "healthy_processes": p.healthy_processes,
                            "is_chat_rest": p.service_type == "chat" and p.service_name == "rest"
                        }
                        for p in processes
                    ]
                    for instance_id, processes in actual_processes_data.items()
                }
            },

            "dynamic_metrics": {
                "actual_processes_per_instance": metrics.actual_processes_per_instance,
                "actual_healthy_processes_per_instance": metrics.actual_healthy_processes_per_instance,
                "actual_concurrent_per_instance": metrics.actual_concurrent_per_instance,
                "actual_effective_concurrent_per_instance": metrics.actual_effective_concurrent_per_instance,
                "actual_queue_capacity_per_instance": metrics.actual_queue_capacity_per_instance,
                "actual_total_capacity_per_instance": metrics.actual_total_capacity_per_instance,
                "process_health_ratio": metrics.process_health_ratio
            },

            "system_capacity_calculation": {
                "base_capacity_per_instance": queue_stats.base_capacity_per_instance,
                "alive_instances": queue_stats.alive_instances,
                "instance_count": queue_stats.instance_count,
                "weighted_max_capacity": queue_stats.weighted_max_capacity,
                "thresholds": {
                    "anonymous_threshold": queue_stats.anonymous_threshold,
                    "registered_threshold": queue_stats.registered_threshold,
                    "hard_limit_threshold": queue_stats.hard_limit_threshold
                }
            },

            "current_queue_state": {
                "anonymous_queue": queue_stats.anonymous_queue,
                "registered_queue": queue_stats.registered_queue,
                "privileged_queue": queue_stats.privileged_queue,
                "total_queue": queue_stats.total_queue,
                "pressure_ratio": queue_stats.pressure_ratio,
                "accepting_anonymous": queue_stats.accepting_anonymous,
                "accepting_registered": queue_stats.accepting_registered,
                "accepting_privileged": queue_stats.accepting_privileged
            },

            "calculations_breakdown": {
                "step_1_config": f"{config.service_capacity.concurrent_requests_per_process} concurrent per process",
                "step_2_processes": f"{metrics.actual_healthy_processes_per_instance} healthy chat REST processes",
                "step_3_total_concurrent": f"{metrics.actual_concurrent_per_instance} total concurrent capacity",
                "step_4_effective": f"{metrics.actual_effective_concurrent_per_instance} effective (after {config.backpressure.capacity_buffer*100}% buffer)",
                "step_5_queue": f"{metrics.actual_queue_capacity_per_instance} queue capacity ({config.backpressure.queue_depth_multiplier}x multiplier)",
                "step_6_total_per_instance": f"{metrics.actual_total_capacity_per_instance} total capacity per instance",
                "step_7_instances": f"{queue_stats.instance_count} instances detected",
                "step_8_system_total": f"{queue_stats.weighted_max_capacity} total system capacity",
                "step_9_thresholds": {
                    "anonymous": f"{queue_stats.anonymous_threshold} = {queue_stats.weighted_max_capacity} * {config.backpressure.anonymous_pressure_threshold}",
                    "registered": f"{queue_stats.registered_threshold} = {queue_stats.weighted_max_capacity} * {config.backpressure.registered_pressure_threshold}",
                    "hard_limit": f"{queue_stats.hard_limit_threshold} = {queue_stats.weighted_max_capacity} * {config.backpressure.hard_limit_threshold}"
                }
            }
        }

        return debug_info

    except Exception as e:
        import traceback
        return {
            "error": str(e),
            "traceback": traceback.format_exc()
        }

# Also add environment variables debug
@router.get("/debug/environment")
async def debug_environment(session: UserSession = Depends(auth_without_pressure())):
    """Debug environment variables affecting capacity"""
    return {
        "MAX_CONCURRENT_CHAT": os.getenv("MAX_CONCURRENT_CHAT", "5"),
        "CHAT_APP_PARALLELISM": os.getenv("CHAT_APP_PARALLELISM", "1"),
        "AVG_PROCESSING_TIME_SECONDS": os.getenv("AVG_PROCESSING_TIME_SECONDS", "25.0"),
        "GATEWAY_PROFILE": os.getenv("GATEWAY_PROFILE", "development"),
        "INSTANCE_ID": os.getenv("INSTANCE_ID", "default-instance"),
        "all_env_vars": {k: v for k, v in os.environ.items() if any(keyword in k.upper() for keyword in ["CHAT", "CONCURRENT", "PARALLEL", "GATEWAY", "CAPACITY"])}
    }
