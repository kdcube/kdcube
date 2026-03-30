# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

import os
import json
import time
from dataclasses import asdict
from datetime import datetime

from fastapi import Depends
from fastapi.responses import JSONResponse

from kdcube_ai_app.apps.chat.ingress.resolvers import auth_without_pressure, INSTANCE_ID, get_fastapi_adapter, require_auth, \
    reset_circuit_breaker, get_circuit_breaker_stats
from kdcube_ai_app.apps.middleware.gateway import CircuitBreakersResponse, CircuitBreakerSummaryResponse, \
    CircuitBreakerStatusResponse
from kdcube_ai_app.auth.AuthManager import RequireUser, RequireRoles
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.infra.availability.health_and_heartbeat import get_expected_services
from kdcube_ai_app.infra.gateway.circuit_breaker import CircuitState
from kdcube_ai_app.infra.gateway.config import GatewayConfigurationManager, get_gateway_config
from kdcube_ai_app.infra.namespaces import REDIS, ns_key
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.metrics.system_monitoring import compute_system_monitoring

"""
Monitoring API

File: api/monitoring/monitoring.py
"""
from fastapi import APIRouter, HTTPException
from typing import Optional, Callable, List, Dict, Any, Union
import logging

logger = logging.getLogger("Monitoring.API")

def _get_router_redis():
    redis = getattr(router.state, "redis_async", None)
    if not redis:
        redis = getattr(router.state, "redis_async_decode", None)
    if not redis:
        middleware = getattr(router.state, "middleware", None)
        redis = getattr(middleware, "redis", None) if middleware else None
    if not redis:
        raise HTTPException(status_code=500, detail="Redis client not initialized")
    return redis


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


async def _scan_delete(redis, pattern: str, batch_size: int = 1000) -> int:
    """Delete keys by pattern using SCAN to avoid blocking Redis."""
    deleted = 0
    batch: List[Any] = []
    async for key in redis.scan_iter(match=pattern, count=batch_size):
        batch.append(key)
        if len(batch) >= batch_size:
            deleted += await redis.delete(*batch)
            batch = []
    if batch:
        deleted += await redis.delete(*batch)
    return deleted


@router.post("/admin/throttling/reset")
async def reset_throttling_state(
        payload: Dict[str, Any],
        session: UserSession = Depends(require_auth(
            RequireUser(),
            RequireRoles("kdcube:role:super-admin")
        ))
):
    """
    Reset throttling/backpressure state.
    Payload:
      - reset_rate_limits: bool (default True)
      - reset_backpressure: bool (default True)
      - reset_throttling_stats: bool (default False)
      - session_id: str (optional, defaults to current session)
      - all_sessions: bool (default False, deletes all rate-limit keys)
      - tenant, project: optional override for namespaced keys
    """
    payload = payload or {}
    reset_rate_limits = bool(payload.get("reset_rate_limits", True))
    reset_backpressure = bool(payload.get("reset_backpressure", True))
    reset_stats = bool(payload.get("reset_throttling_stats", False))
    purge_chat_queues = bool(payload.get("purge_chat_queues", False))
    all_sessions = bool(payload.get("all_sessions", False))
    session_id = (payload.get("session_id") or "").strip() or (session.session_id if not all_sessions else None)

    settings = get_settings()
    tenant = payload.get("tenant") or settings.TENANT
    project = payload.get("project") or settings.PROJECT

    middleware = router.state.middleware
    redis = _get_router_redis()

    results: Dict[str, Any] = {"deleted": {}, "tenant": tenant, "project": project}

    # Rate limit keys (tenant/project namespaced)
    if reset_rate_limits:
        rate_prefix = ns_key(REDIS.SYSTEM.RATE_LIMIT, tenant=tenant, project=project)
        if all_sessions:
            deleted_burst = await _scan_delete(redis, f"{rate_prefix}:*:burst")
            deleted_hour = await _scan_delete(redis, f"{rate_prefix}:*:hour:*")
            results["deleted"]["rate_limits_all_sessions"] = deleted_burst + deleted_hour
        else:
            if not session_id:
                raise HTTPException(status_code=400, detail="session_id is required unless all_sessions=true")
            deleted = 0
            deleted += await redis.delete(f"{rate_prefix}:{session_id}:burst")
            deleted += await _scan_delete(redis, f"{rate_prefix}:{session_id}:hour:*")
            results["deleted"]["rate_limits_session"] = deleted

    # Backpressure counters (namespaced)
    if reset_backpressure:
        capacity_base = ns_key(f"{REDIS.SYSTEM.CAPACITY}:counter", tenant=tenant, project=project)
        deleted = await redis.delete(capacity_base, f"{capacity_base}:total")
        results["deleted"]["backpressure_capacity_counters"] = deleted

    # Chat queues (danger: drops pending tasks)
    if purge_chat_queues:
        queue_prefix = ns_key(REDIS.CHAT.PROMPT_QUEUE_PREFIX, tenant=tenant, project=project)
        inflight_queue_prefix = ns_key(REDIS.CHAT.PROMPT_QUEUE_INFLIGHT_PREFIX, tenant=tenant, project=project)
        keys = [
            f"{queue_prefix}:anonymous",
            f"{queue_prefix}:registered",
            f"{queue_prefix}:privileged",
            f"{queue_prefix}:paid",
            f"{inflight_queue_prefix}:anonymous",
            f"{inflight_queue_prefix}:registered",
            f"{inflight_queue_prefix}:privileged",
            f"{inflight_queue_prefix}:paid",
        ]
        deleted = await redis.delete(*keys)
        results["deleted"]["chat_queues"] = deleted
        # If queues are purged, clear capacity counters to avoid stale pressure.
        capacity_base = ns_key(f"{REDIS.SYSTEM.CAPACITY}:counter", tenant=tenant, project=project)
        deleted = await redis.delete(capacity_base, f"{capacity_base}:total")
        results["deleted"]["backpressure_capacity_counters_from_purge"] = deleted

    # Throttling stats (namespaced, dashboard-only)
    if reset_stats:
        keys = [
            ns_key(REDIS.THROTTLING.EVENTS_KEY, tenant=tenant, project=project),
            ns_key(REDIS.THROTTLING.STATS_KEY, tenant=tenant, project=project),
            ns_key(REDIS.THROTTLING.SESSION_COUNTERS_KEY, tenant=tenant, project=project),
            ns_key(REDIS.THROTTLING.TOTAL_REQUESTS_KEY, tenant=tenant, project=project),
            ns_key(REDIS.THROTTLING.TOTAL_REQUESTS_HOURLY, tenant=tenant, project=project),
            ns_key(REDIS.THROTTLING.TOTAL_THROTTLED_REQUESTS_KEY, tenant=tenant, project=project),
            ns_key(REDIS.THROTTLING.RATE_LIMIT_429, tenant=tenant, project=project),
            ns_key(REDIS.THROTTLING.BACKPRESSURE_503, tenant=tenant, project=project),
            ns_key(REDIS.THROTTLING.HOURLY, tenant=tenant, project=project),
            ns_key(REDIS.THROTTLING.BY_REASON, tenant=tenant, project=project),
        ]
        deleted = await redis.delete(*keys)
        results["deleted"]["throttling_stats"] = deleted

    return {
        "success": True,
        "message": "Throttling/backpressure state reset",
        "details": results,
    }


def _burst_sim_enabled() -> bool:
    return os.getenv("MONITORING_BURST_ENABLE", "0").lower() in {"1", "true", "yes", "on"}


@router.get("/admin/burst/users")
async def get_burst_users(session: UserSession = Depends(auth_without_pressure())):
    """
    Dev-only helper: expose SimpleIDP tokens for burst simulation in the monitoring UI.
    Controlled by MONITORING_BURST_ENABLE=1 and AUTH_PROVIDER=simple.
    """
    if not _burst_sim_enabled():
        raise HTTPException(status_code=404, detail="Burst simulator is disabled")

    if os.getenv("AUTH_PROVIDER", "simple").lower() != "simple":
        raise HTTPException(status_code=400, detail="Burst simulator requires AUTH_PROVIDER=simple")

    try:
        from kdcube_ai_app.apps.middleware.simple_idp import IDP_DB_PATH
        with open(IDP_DB_PATH, "r") as f:
            users = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load SimpleIDP users: {e}")

    groups = {"admin": [], "registered": [], "paid": []}
    for token, user in (users or {}).items():
        roles = set(user.get("roles") or [])
        entry = {
            "token": token,
            "user_id": user.get("sub") or user.get("user_id"),
            "username": user.get("username"),
            "roles": list(roles),
        }
        if "kdcube:role:super-admin" in roles:
            groups["admin"].append(entry)
        elif "kdcube:role:paid" in roles:
            groups["paid"].append(entry)
        else:
            groups["registered"].append(entry)

    return {
        "enabled": True,
        "counts": {k: len(v) for k, v in groups.items()},
        "users": groups,
    }



@router.get("/monitoring/system")
async def get_system_monitoring(
        session= Depends(auth_without_pressure())):
    """
    Enhanced system monitoring with chat REST vs other process tracking
    """
    try:
        redis = _get_router_redis()
        middleware = getattr(router.state, "middleware", None)
        if middleware is None:
            from kdcube_ai_app.infra.availability.health_and_heartbeat import MultiprocessDistributedMiddleware
            settings = get_settings()
            middleware = MultiprocessDistributedMiddleware(
                settings.REDIS_URL,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                instance_id=INSTANCE_ID,
                redis=redis,
            )
            router.state.middleware = middleware
        gateway_adapter = router.state.gateway_adapter
        expected_services = get_expected_services(INSTANCE_ID)

        response_data = await compute_system_monitoring(
            redis=redis,
            gateway_adapter=gateway_adapter,
            middleware=middleware,
            instance_id=INSTANCE_ID,
            expected_services=expected_services,
            sse_hub=getattr(router.state, "sse_hub", None),
            pg_pool=getattr(router.state, "pg_pool", None),
            redis_clients={
                "async": getattr(router.state, "redis_async", None),
                "async_decode": getattr(router.state, "redis_async_decode", None),
                "sync": getattr(router.state, "redis_sync", None),
            },
        )
        response_data["gateway_config_source"] = getattr(router.state, "gateway_config_source", None)
        gateway_status = response_data.pop("_gateway_status", None)
        if gateway_status:
            response_data["gateway_configuration"] = _extract_gateway_config_for_frontend(gateway_status)
            response_data["configuration"] = _extract_detailed_config_for_frontend(
                response_data.get("capacity_transparency", {}),
                gateway_status,
            )
        try:
            cb = await get_circuit_breaker_status()
            response_data["circuit_breakers"] = cb.model_dump()
        except Exception:
            pass
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
        "bypass_throttling_patterns": config.get("bypass_throttling_patterns", []),
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
            "paid_pressure_threshold": config["backpressure"].get("paid_pressure_threshold"),
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
                "paid_pressure_threshold": capacity.get("threshold_ratios", {}).get("paid_threshold_ratio", 0.8),
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


@router.post("/admin/gateway/clear-cache")
async def clear_gateway_config_cache_endpoint(
        payload: Dict[str, Any],
        session: UserSession = Depends(require_auth(
            RequireUser(),
            RequireRoles("kdcube:role:super-admin")
        ))
):
    """
    Clear cached gateway config for a tenant/project.
    Optional payload: {"tenant": "...", "project": "..."}.
    """
    gateway_adapter = get_fastapi_adapter()
    config_manager = GatewayConfigurationManager(gateway_adapter)
    payload = payload or {}
    result = await config_manager.clear_cached_config(**payload)
    return {
        "success": True,
        "message": "Gateway config cache cleared (next restart falls back to env/GATEWAY_CONFIG_JSON)",
        "result": result,
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
                "paid_threshold": config.backpressure.paid_pressure_threshold,
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
                    "paid_threshold": queue_stats.paid_threshold,
                    "hard_limit_threshold": queue_stats.hard_limit_threshold
                }
            },

            "current_queue_state": {
                "anonymous_queue": queue_stats.anonymous_queue,
                "registered_queue": queue_stats.registered_queue,
                "paid_queue": queue_stats.paid_queue,
                "privileged_queue": queue_stats.privileged_queue,
                "total_queue": queue_stats.total_queue,
                "pressure_ratio": queue_stats.pressure_ratio,
                "accepting_anonymous": queue_stats.accepting_anonymous,
                "accepting_registered": queue_stats.accepting_registered,
                "accepting_paid": queue_stats.accepting_paid,
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
                    "paid": f"{queue_stats.paid_threshold} = {queue_stats.weighted_max_capacity} * {config.backpressure.paid_pressure_threshold}",
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
    config = get_gateway_config()
    return {
        "service_capacity": {
            "concurrent_requests_per_process": config.service_capacity.concurrent_requests_per_process,
            "processes_per_instance": config.service_capacity.processes_per_instance,
            "concurrent_requests_per_instance": config.service_capacity.concurrent_requests_per_instance,
            "avg_processing_time_seconds": config.service_capacity.avg_processing_time_seconds,
        },
        "AVG_PROCESSING_TIME_SECONDS": os.getenv("AVG_PROCESSING_TIME_SECONDS", "25.0"),
        "GATEWAY_PROFILE": os.getenv("GATEWAY_PROFILE", "development"),
        "INSTANCE_ID": os.getenv("INSTANCE_ID", "default-instance"),
        "GATEWAY_CONFIG_JSON_SET": bool(os.getenv("GATEWAY_CONFIG_JSON")),
        "all_env_vars": {k: v for k, v in os.environ.items() if any(keyword in k.upper() for keyword in ["CHAT", "CONCURRENT", "PARALLEL", "GATEWAY", "CAPACITY"])}
    }
