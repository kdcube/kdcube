# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Shared system monitoring computation.

Used by:
- Chat monitoring endpoint (/monitoring/system)
- Metrics service (redis mode)

This module keeps the heavy logic in one place.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from typing import Optional, Dict, Any, List

import logging

from kdcube_ai_app.apps.chat.ingress.resolvers import get_pg_pool
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.availability.health_and_heartbeat import get_expected_services, ServiceConfig
from kdcube_ai_app.infra.gateway.config import load_gateway_config_raw, parse_gateway_config_for_component
from kdcube_ai_app.infra.gateway.definitions import (
    ActualProcessInfo,
    DynamicCapacityMetrics,
    DynamicCapacityCalculator,
    _matches_capacity_source,
)
from kdcube_ai_app.infra.metrics.rolling_stats import (
    record_metric,
    read_metric_windows,
    read_metric_percentiles,
)
from kdcube_ai_app.infra.namespaces import REDIS, ns_key

logger = logging.getLogger("Monitoring.Core")

_DB_MAX_CONNECTIONS_CACHE: dict[str, Any] = {"value": None, "ts": 0.0, "source": None}
_DB_MAX_CONNECTIONS_TTL_SEC = 60
_POOL_UTIL_RETENTION_SEC = 3600


async def _resolve_db_max_connections(pg_pool=None, gateway_config=None) -> tuple[Optional[int], Optional[str]]:
    try:
        if gateway_config and getattr(gateway_config, "pools", None):
            cfg_val = getattr(gateway_config.pools, "pg_max_connections", None)
            if cfg_val is not None:
                return int(cfg_val), "config"
    except Exception:
        pass
    now = time.time()
    if _DB_MAX_CONNECTIONS_CACHE["value"] is not None and now - _DB_MAX_CONNECTIONS_CACHE["ts"] < _DB_MAX_CONNECTIONS_TTL_SEC:
        return _DB_MAX_CONNECTIONS_CACHE["value"], _DB_MAX_CONNECTIONS_CACHE["source"]
    try:
        pool = pg_pool or await get_pg_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SHOW max_connections;")
            if val is not None:
                value = int(val)
                _DB_MAX_CONNECTIONS_CACHE.update({"value": value, "ts": now, "source": "query"})
                return value, "query"
    except Exception as e:
        logger.warning("Failed to fetch DB max_connections: %s", e)
    return None, None


def _sum_numeric(values: List[Any]) -> tuple[Optional[float], int]:
    total = 0.0
    count = 0
    for value in values:
        if isinstance(value, (int, float)):
            total += float(value)
            count += 1
    return (total if count else None), count


def _pool_util_key(tenant: str, project: str, component: str, pool_name: str) -> str:
    base = f"{REDIS.METRICS.POOL_UTILIZATION}:{component}:{pool_name}"
    return ns_key(base, tenant=tenant, project=project)




async def _record_pool_utilization(redis, *, tenant: str, project: str, component: str, pool_name: str, value: float) -> None:
    if redis is None:
        return
    ts = time.time()
    key = _pool_util_key(tenant, project, component, pool_name)
    member = f"{ts}:{value}"
    try:
        await redis.zadd(key, {member: ts})
        await redis.zremrangebyscore(key, 0, ts - _POOL_UTIL_RETENTION_SEC)
        await redis.expire(key, _POOL_UTIL_RETENTION_SEC + 60)
    except Exception:
        return


async def _read_pool_utilization_windows(redis, *, tenant: str, project: str, component: str, pool_name: str) -> Dict[str, Optional[float]]:
    if redis is None:
        return {"1m": None, "15m": None, "1h": None, "max": None}
    key = _pool_util_key(tenant, project, component, pool_name)
    now = time.time()

    async def _avg_since(seconds: int) -> Optional[float]:
        try:
            members = await redis.zrangebyscore(key, now - seconds, now)
            if not members:
                return None
            vals = []
            for m in members:
                raw = m.decode() if isinstance(m, (bytes, bytearray)) else str(m)
                parts = raw.split(":", 1)
                if len(parts) == 2:
                    try:
                        vals.append(float(parts[1]))
                    except Exception:
                        continue
            if not vals:
                return None
            return round(sum(vals) / len(vals), 1)
        except Exception:
            return None

    async def _max_since(seconds: int) -> Optional[float]:
        try:
            members = await redis.zrangebyscore(key, now - seconds, now)
            if not members:
                return None
            vals = []
            for m in members:
                raw = m.decode() if isinstance(m, (bytes, bytearray)) else str(m)
                parts = raw.split(":", 1)
                if len(parts) == 2:
                    try:
                        vals.append(float(parts[1]))
                    except Exception:
                        continue
            if not vals:
                return None
            return round(max(vals), 1)
        except Exception:
            return None

    return {
        "1m": await _avg_since(60),
        "15m": await _avg_since(900),
        "1h": await _avg_since(3600),
        "max": await _max_since(3600),
    }


def _build_actual_processes_data(
    all_heartbeats: Dict[str, Dict[str, List[Dict[str, Any]]]]
) -> Dict[str, List[ActualProcessInfo]]:
    result: Dict[str, List[ActualProcessInfo]] = {}
    for instance_id, services in all_heartbeats.items():
        result[instance_id] = []
        for service_key, procs in services.items():
            if not procs:
                continue
            if "_" in service_key:
                service_type, service_name = service_key.split("_", 1)
            else:
                service_type, service_name = "chat", service_key
            healthy_count = sum(1 for p in procs if p.get("status") == "healthy")
            total_load = sum(int(p.get("load") or 0) for p in procs)
            total_capacity = sum(int(p.get("capacity") or 0) for p in procs)
            result[instance_id].append(
                ActualProcessInfo(
                    instance_id=instance_id,
                    service_type=service_type,
                    service_name=service_name,
                    process_count=len(procs),
                    healthy_processes=healthy_count,
                    total_load=total_load,
                    total_capacity=total_capacity,
                    process_details=procs,
                )
            )
    return result


def _compute_capacity_transparency_from_heartbeats(
    gateway_config,
    actual_processes_data: Dict[str, List[ActualProcessInfo]],
) -> Dict[str, Any]:
    all_processes: List[ActualProcessInfo] = [
        p for processes in actual_processes_data.values() for p in processes
    ]
    metrics = DynamicCapacityMetrics.from_config_and_processes(gateway_config, all_processes)
    cap_service_type, cap_service_name = gateway_config.capacity_source_selector()

    total_instances = len(actual_processes_data)
    total_configured_processes = metrics.configured_processes_per_instance * total_instances

    total_actual_processes = sum(
        sum(p.process_count for p in processes)
        for processes in actual_processes_data.values()
    )
    total_healthy_processes = sum(
        sum(p.healthy_processes for p in processes)
        for processes in actual_processes_data.values()
    )

    capacity_source_actual_processes = sum(
        sum(p.process_count for p in processes if _matches_capacity_source(p, gateway_config))
        for processes in actual_processes_data.values()
    )
    capacity_source_healthy_processes = sum(
        sum(p.healthy_processes for p in processes if _matches_capacity_source(p, gateway_config))
        for processes in actual_processes_data.values()
    )

    thresholds = metrics.get_thresholds_for_instances(total_instances, actual_processes_data, gateway_config)

    calc = DynamicCapacityCalculator(gateway_config, redis_client=None)
    warnings = calc._generate_capacity_warnings(metrics, actual_processes_data)

    return {
        "capacity_metrics": metrics.to_dict(),
        "instance_scaling": {
            "detected_instances": total_instances,
            "total_configured_processes": total_configured_processes,
            "total_actual_processes": total_actual_processes,
            "total_healthy_processes": total_healthy_processes,
            "process_health_ratio": total_healthy_processes / max(total_actual_processes, 1),
            "capacity_source": f"{cap_service_type}:{cap_service_name}",
            "capacity_source_configured_processes": metrics.configured_processes_per_instance * total_instances,
            "capacity_source_actual_processes": capacity_source_actual_processes,
            "capacity_source_healthy_processes": capacity_source_healthy_processes,
            "capacity_source_health_ratio": capacity_source_healthy_processes / max(capacity_source_actual_processes, 1)
            if capacity_source_actual_processes > 0 else 0,
            "chat_rest_configured_processes": metrics.configured_processes_per_instance * total_instances,
            "chat_rest_actual_processes": capacity_source_actual_processes,
            "chat_rest_healthy_processes": capacity_source_healthy_processes,
            "chat_rest_health_ratio": capacity_source_healthy_processes / max(capacity_source_actual_processes, 1)
            if capacity_source_actual_processes > 0 else 0,
            "total_concurrent_capacity": metrics.configured_concurrent_per_process * capacity_source_healthy_processes,
            "total_effective_capacity": int(
                metrics.configured_concurrent_per_process * capacity_source_healthy_processes * (1 - metrics.capacity_buffer)
            ),
            "total_queue_capacity": int(
                metrics.configured_concurrent_per_process * capacity_source_healthy_processes * metrics.queue_depth_multiplier
            ),
            "total_system_capacity": thresholds["total_capacity"],
            "theoretical_system_hourly": int(
                (metrics.configured_concurrent_per_process * capacity_source_healthy_processes * 3600)
                / metrics.configured_avg_processing_time
            ),
        },
        "current_thresholds": thresholds,
        "threshold_breakdown": {
            "anonymous_blocks_at": thresholds["anonymous_threshold"],
            "registered_blocks_at": thresholds["registered_threshold"],
            "paid_blocks_at": thresholds["paid_threshold"],
            "hard_limit_at": thresholds["hard_limit"],
            "anonymous_percentage": round(metrics.anonymous_threshold_ratio * 100, 1),
            "registered_percentage": round(metrics.registered_threshold_ratio * 100, 1),
            "paid_percentage": round(metrics.paid_threshold_ratio * 100, 1),
            "hard_limit_percentage": round(metrics.hard_limit_threshold_ratio * 100, 1),
        },
        "capacity_warnings": warnings,
    }


def _aggregate_pool_stats_from_processes(processes: List[Dict[str, Any]]) -> Dict[str, Any]:
    pg_stats: List[Dict[str, Any]] = []
    redis_stats: Dict[str, List[Dict[str, Any]]] = {"async": [], "async_decode": [], "sync": []}

    for proc in processes:
        metadata = proc.get("metadata") or {}
        pools = metadata.get("pools") or {}
        pg = pools.get("postgres")
        if isinstance(pg, dict):
            pg_stats.append(pg)
        redis = pools.get("redis") or {}
        for key in ("async", "async_decode", "sync"):
            item = redis.get(key)
            if isinstance(item, dict):
                redis_stats[key].append(item)

    pg_max_total, _ = _sum_numeric([p.get("max_size") for p in pg_stats])
    pg_size_total, _ = _sum_numeric([p.get("size") for p in pg_stats])
    pg_available_total, _ = _sum_numeric([p.get("available") for p in pg_stats])
    pg_in_use_total, _ = _sum_numeric([p.get("in_use") for p in pg_stats])
    pg_capacity = pg_max_total if pg_max_total is not None else pg_size_total
    pg_util = None
    if pg_capacity and pg_in_use_total is not None and pg_capacity > 0:
        pg_util = round((pg_in_use_total / pg_capacity) * 100, 1)

    redis_agg: Dict[str, Any] = {}
    for key, items in redis_stats.items():
        max_total, _ = _sum_numeric([p.get("max_connections") for p in items])
        in_use_total, _ = _sum_numeric([p.get("in_use") for p in items])
        available_total, _ = _sum_numeric([p.get("available") for p in items])
        total_total, _ = _sum_numeric([p.get("total") for p in items])
        capacity = max_total if max_total is not None else total_total
        util = None
        if capacity and in_use_total is not None and capacity > 0:
            util = round((in_use_total / capacity) * 100, 1)
        redis_agg[key] = {
            "max_total": max_total,
            "in_use_total": in_use_total,
            "available_total": available_total,
            "total_total": total_total,
            "utilization_percent": util,
            "reported_processes": len(items),
        }

    return {
        "postgres": {
            "max_total": pg_max_total,
            "size_total": pg_size_total,
            "available_total": pg_available_total,
            "in_use_total": pg_in_use_total,
            "utilization_percent": pg_util,
            "reported_processes": len(pg_stats),
        },
        "redis": redis_agg,
    }


async def _throttling_window_stats(redis, events_key: str, seconds: int) -> Dict[str, Any]:
    if redis is None:
        return {"total_throttled": 0, "rate_limit_429": 0, "backpressure_503": 0, "events_per_min": 0.0}
    now = time.time()
    start = now - seconds
    try:
        events = await redis.zrangebyscore(events_key, start, now)
    except Exception:
        events = []

    rate_limit_429 = 0
    backpressure_503 = 0
    for raw in events:
        try:
            payload = json.loads(raw)
        except Exception:
            try:
                payload = json.loads(raw.decode())
            except Exception:
                continue
        status = payload.get("http_status")
        if status == 429:
            rate_limit_429 += 1
        elif status == 503:
            backpressure_503 += 1

    total = len(events)
    events_per_min = round(total / (seconds / 60), 2) if seconds > 0 else 0.0
    return {
        "total_throttled": total,
        "rate_limit_429": rate_limit_429,
        "backpressure_503": backpressure_503,
        "events_per_min": events_per_min,
    }


async def _aggregate_sse_stats_from_redis(
    redis,
    tenant: str,
    project: str,
    *,
    max_age_sec: int,
) -> Optional[Dict[str, Any]]:
    if not redis:
        return None
    pattern = ns_key(f"{REDIS.CHAT.SSE_CONNECTIONS_PREFIX}:*", tenant=tenant, project=project)
    total = 0
    max_total = 0
    sessions_total = 0
    processes = 0
    now = time.time()
    async for key in redis.scan_iter(match=pattern, count=500):
        try:
            raw = await redis.get(key)
            if not raw:
                continue
            data = json.loads(raw)
            ts = data.get("ts")
            if isinstance(ts, (int, float)) and (now - ts) > max_age_sec:
                continue
            total += int(data.get("count") or 0)
            max_total += int(data.get("max") or 0)
            sessions_total += int(data.get("sessions") or 0)
            processes += 1
        except Exception:
            continue
    if processes == 0:
        return None
    return {
        "total_connections": total,
        "max_connections": max_total,
        "sessions": sessions_total,
        "processes": processes,
        "source": "redis",
    }


def _derive_expected_services_from_heartbeats(all_heartbeats: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> Dict[str, List[ServiceConfig]]:
    expected: Dict[str, List[ServiceConfig]] = {}
    for instance_id, services in all_heartbeats.items():
        expected[instance_id] = []
        for service_key, processes in services.items():
            service_type = None
            service_name = None
            if processes:
                service_type = processes[0].get("service_type")
                service_name = processes[0].get("service_name")
            if not service_type or not service_name:
                if "_" in service_key:
                    service_type, service_name = service_key.split("_", 1)
                else:
                    service_type, service_name = service_key, "rest"
            expected[instance_id].append(
                ServiceConfig(
                    service_type=service_type,
                    service_name=service_name,
                    expected_processes=len(processes),
                    ports=[],
                )
            )
    return expected


def _collect_component_processes(
    all_heartbeats: Dict[str, Dict[str, List[Dict[str, Any]]]],
    service_type: str,
    service_name: str,
) -> tuple[Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    per_instance: Dict[str, List[Dict[str, Any]]] = {}
    flat: List[Dict[str, Any]] = []
    service_key = f"{service_type}_{service_name}"
    for instance_id, services in all_heartbeats.items():
        processes = services.get(service_key, [])
        if processes:
            per_instance[instance_id] = processes
            flat.extend(processes)
    return per_instance, flat


def _autoscaler_decision_proc(component_stats: Dict[str, Any], queue_stats: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    scale_up = False
    scale_down = False

    pressure_ratio = queue_stats.get("capacity_context", {}).get("pressure_ratio") or 0.0
    total_queue = queue_stats.get("total") or 0
    utilization = component_stats.get("utilization_percent") or 0.0
    instance_count = component_stats.get("instance_count") or 0

    if pressure_ratio >= 0.85:
        scale_up = True
        reasons.append(f"queue pressure {pressure_ratio:.2f} >= 0.85")
    if total_queue > 0 and utilization >= 85:
        scale_up = True
        reasons.append(f"utilization {utilization:.1f}% with non-empty queue")

    if total_queue == 0 and pressure_ratio < 0.3 and utilization < 30 and instance_count > 1:
        scale_down = True
        reasons.append("low queue + low utilization")

    decision = "scale_up" if scale_up and not scale_down else "scale_down" if scale_down else "hold"
    return {
        "decision": decision,
        "scale_up": scale_up,
        "scale_down": scale_down,
        "reasons": reasons,
        "signals": {
            "queue_pressure_ratio": round(float(pressure_ratio), 3),
            "queue_depth": int(total_queue),
            "utilization_percent": round(float(utilization), 1),
            "instance_count": int(instance_count),
        },
    }


def _autoscaler_decision_ingress(component_stats: Dict[str, Any], sse_stats: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    reasons: List[str] = []
    scale_up = False
    scale_down = False

    instance_count = component_stats.get("instance_count") or 0
    utilization = component_stats.get("utilization_percent") or 0.0

    sse_util = None
    if sse_stats and sse_stats.get("max_connections"):
        try:
            sse_util = (float(sse_stats.get("total_connections") or 0) / float(sse_stats.get("max_connections") or 1)) * 100
        except Exception:
            sse_util = None

    if sse_util is not None and sse_util >= 80:
        scale_up = True
        reasons.append(f"sse utilization {sse_util:.1f}% >= 80%")

    if (sse_util is not None and sse_util < 30 and instance_count > 1) and utilization < 30:
        scale_down = True
        reasons.append("low sse utilization + low load")

    decision = "scale_up" if scale_up and not scale_down else "scale_down" if scale_down else "hold"
    return {
        "decision": decision,
        "scale_up": scale_up,
        "scale_down": scale_down,
        "reasons": reasons,
        "signals": {
            "sse_utilization_percent": round(float(sse_util), 1) if sse_util is not None else None,
            "utilization_percent": round(float(utilization), 1),
            "instance_count": int(instance_count),
        },
    }


async def compute_system_monitoring(
    *,
    redis,
    gateway_adapter,
    middleware,
    instance_id: str,
    expected_services: Optional[Dict[str, List[ServiceConfig]]] = None,
    sse_hub=None,
    pg_pool=None,
    redis_clients: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute system monitoring response.

    This mirrors the /monitoring/system endpoint but accepts dependencies explicitly.
    """
    current_time = time.time()
    settings = get_settings()

    # Get all process heartbeats from Redis
    process_pattern = f"{middleware.PROCESS_HEARTBEAT_PREFIX}:*"
    process_keys = await redis.keys(process_pattern)

    # Parse all heartbeats and separate chat REST from others
    all_heartbeats: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    chat_rest_heartbeats: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    for key in process_keys:
        try:
            data = await redis.get(key)
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

            metadata = heartbeat.get("metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}

            process_info = {
                'pid': heartbeat.get('process_id'),
                'port': heartbeat.get('port'),
                'load': heartbeat.get('current_load', 0),
                'capacity': heartbeat.get('max_capacity', 0),
                'last_heartbeat': heartbeat.get('last_heartbeat', current_time),
                'status': status,
                'age_seconds': age_seconds,
                'service_type': service_type,
                'service_name': service_name,
                'metadata': metadata,
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

    # Derive expected services if not provided
    if expected_services is None:
        if all_heartbeats:
            expected_services = _derive_expected_services_from_heartbeats(all_heartbeats)
        else:
            expected_services = get_expected_services(instance_id)

    # Build service health for each instance
    instances_data: Dict[str, Any] = {}
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
    gateway_status = await gateway_adapter.gateway.get_system_status()
    gateway_config = getattr(gateway_adapter.gateway, "gateway_config", None) or getattr(gateway_adapter.gateway, "config", None)

    # Extract the automatically computed data
    queue_data = gateway_status["queue_stats"]
    capacity_transparency = gateway_status["capacity_transparency"]

    # Recompute capacity transparency from the same heartbeat data used by monitoring
    try:
        if gateway_config:
            actual_processes_data = _build_actual_processes_data(all_heartbeats)
            capacity_transparency = _compute_capacity_transparency_from_heartbeats(
                gateway_config, actual_processes_data
            )
    except Exception as e:
        logger.warning("Failed to recompute capacity transparency from heartbeats: %s", e)

    # SSE stats (local + optional global from Redis)
    sse_stats = None
    if sse_hub:
        try:
            sse_stats = await sse_hub.get_stats()
            if isinstance(sse_stats, dict):
                sse_stats.setdefault("source", "process")
        except Exception:
            sse_stats = None
    try:
        max_age = 120
        if gateway_config and getattr(gateway_config, "redis", None):
            max_age = int(getattr(gateway_config.redis, "sse_stats_max_age_seconds", max_age))
        global_sse = await _aggregate_sse_stats_from_redis(
            redis,
            settings.TENANT,
            settings.PROJECT,
            max_age_sec=max_age,
        )
    except Exception:
        global_sse = None
    if global_sse:
        if sse_stats:
            sse_stats["global_total_connections"] = global_sse["total_connections"]
            sse_stats["global_max_connections"] = global_sse["max_connections"]
            sse_stats["global_sessions"] = global_sse.get("sessions")
            sse_stats["global_processes"] = global_sse.get("processes")
            sse_stats["global_source"] = global_sse.get("source")
        else:
            sse_stats = global_sse

    # Rolling SSE connection windows (ingress)
    sse_windows = None
    if sse_stats:
        total_conn = sse_stats.get("global_total_connections", sse_stats.get("total_connections"))
        if total_conn is not None:
            await record_metric(
                redis,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component="ingress",
                base=REDIS.METRICS.SSE_CONNECTIONS,
                value=float(total_conn),
            )
            sse_windows = await read_metric_windows(
                redis,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component="ingress",
                base=REDIS.METRICS.SSE_CONNECTIONS,
            )

    # Component-aware config (ingress + proc)
    raw_config = None
    component_configs: Dict[str, Any] = {}
    try:
        raw_config = await load_gateway_config_raw(
            tenant=settings.TENANT,
            project=settings.PROJECT,
            redis_url=settings.REDIS_URL,
        )
        for comp in ("ingress", "proc"):
            try:
                component_configs[comp] = parse_gateway_config_for_component(raw_config, comp)
            except Exception:
                continue
    except Exception:
        raw_config = None
        component_configs = {}

    # Component stats + autoscaler hints
    components: Dict[str, Any] = {}
    autoscaler: Dict[str, Any] = {}
    heartbeat_timeout = (
        gateway_status.get("gateway_configuration", {})
        .get("monitoring", {})
        .get("heartbeat_timeout_seconds", 45)
    )
    grace_seconds = max((heartbeat_timeout or 45) * 2, (heartbeat_timeout or 45) + 10)
    component_defs = {
        "ingress": ("chat", "rest"),
        "proc": ("chat", "proc"),
    }
    for comp, (service_type, service_name) in component_defs.items():
        per_instance, flat = _collect_component_processes(all_heartbeats, service_type, service_name)
        instance_ids = list(per_instance.keys())
        instance_count = len(instance_ids)
        actual = len(flat)
        healthy = len([p for p in flat if p['status'] in ('healthy', 'degraded')])
        load_total = sum(p.get("load", 0) for p in flat)
        capacity_total = sum(p.get("capacity", 0) for p in flat)
        utilization = (load_total / capacity_total * 100) if capacity_total > 0 else 0.0

        cfg = component_configs.get(comp)
        expected_per_instance = None
        configured_concurrent = None
        if cfg is not None:
            expected_per_instance = int(getattr(cfg.service_capacity, "processes_per_instance", 0) or 0)
            configured_concurrent = int(getattr(cfg.service_capacity, "concurrent_requests_per_process", 0) or 0)
        expected_total = expected_per_instance * instance_count if expected_per_instance and instance_count else None
        missing = (expected_total - actual) if expected_total is not None else None

        pools_cfg = getattr(cfg, "pools", None) if cfg is not None else None
        pool_pg_max = getattr(pools_cfg, "pg_pool_max_size", None) if pools_cfg else None
        pool_redis_max = getattr(pools_cfg, "redis_max_connections", None) if pools_cfg else None
        if pool_pg_max is None and configured_concurrent:
            pool_pg_max = configured_concurrent

        est_pg_per_instance = (pool_pg_max * expected_per_instance) if (pool_pg_max and expected_per_instance) else None
        est_pg_total = (est_pg_per_instance * instance_count) if (est_pg_per_instance and instance_count) else None

        instances_detail = []
        for inst_id, procs in per_instance.items():
            inst_health = len([p for p in procs if p['status'] in ('healthy', 'degraded')])
            inst_capacity = sum(p.get("capacity", 0) for p in procs)
            inst_load = sum(p.get("load", 0) for p in procs)
            inst_statuses = [p.get("status") for p in procs]
            max_age = max([p.get("age_seconds", 0) for p in procs], default=0)
            draining = bool(inst_statuses) and all(s == "stale" for s in inst_statuses) and max_age <= grace_seconds
            instances_detail.append({
                "instance_id": inst_id,
                "processes": len(procs),
                "healthy_processes": inst_health,
                "capacity": inst_capacity,
                "load": inst_load,
                "utilization_percent": round((inst_load / inst_capacity * 100) if inst_capacity else 0, 1),
                "statuses": inst_statuses,
                "max_age_seconds": max_age,
                "draining": draining,
            })

        component_entry = {
            "service_type": service_type,
            "service_name": service_name,
            "instance_count": instance_count,
            "expected_processes": expected_total,
            "actual_processes": actual,
            "healthy_processes": healthy,
            "missing_processes": missing,
            "load": load_total,
            "capacity": capacity_total,
            "utilization_percent": round(utilization, 1),
            "configured": {
                "processes_per_instance": expected_per_instance,
                "concurrent_requests_per_process": configured_concurrent,
            },
            "pools": {
                "pg_pool_max_size": pool_pg_max,
                "redis_max_connections": pool_redis_max,
                "estimated_pg_per_instance": est_pg_per_instance,
                "estimated_pg_total": est_pg_total,
            },
            "pools_aggregate": _aggregate_pool_stats_from_processes(flat),
            "instances": instances_detail,
        }

        if comp == "ingress":
            if sse_stats:
                total_conn = sse_stats.get("global_total_connections", sse_stats.get("total_connections"))
                max_conn = sse_stats.get("global_max_connections", sse_stats.get("max_connections"))
                util = None
                if max_conn:
                    util = round((float(total_conn or 0) / float(max_conn)) * 100, 1)
                component_entry["sse"] = {
                    "total_connections": total_conn,
                    "max_connections": max_conn,
                    "sessions": sse_stats.get("global_sessions", sse_stats.get("sessions")),
                    "utilization_percent": util,
                    "windows": sse_windows,
                }
        if comp == "proc":
            component_entry["queue"] = {
                "total": queue_data.get("total"),
                "pressure_ratio": queue_data.get("capacity_context", {}).get("pressure_ratio"),
                "thresholds": queue_data.get("capacity_context", {}).get("thresholds"),
                "accepting": {
                    "anonymous": queue_data.get("capacity_context", {}).get("accepting_anonymous"),
                    "registered": queue_data.get("capacity_context", {}).get("accepting_registered"),
                    "paid": queue_data.get("capacity_context", {}).get("accepting_paid"),
                    "privileged": queue_data.get("capacity_context", {}).get("accepting_privileged"),
                },
            }

        components[comp] = component_entry

    # Attach rolling utilization windows for pool stats
    for comp, entry in components.items():
        pools_agg = entry.get("pools_aggregate") or {}
        windows: Dict[str, Dict[str, Optional[float]]] = {}
        in_use_windows: Dict[str, Dict[str, Optional[float]]] = {}

        pool_util_map = {
            "postgres": (pools_agg.get("postgres") or {}).get("utilization_percent"),
            "redis_async": (pools_agg.get("redis", {}).get("async") or {}).get("utilization_percent"),
            "redis_async_decode": (pools_agg.get("redis", {}).get("async_decode") or {}).get("utilization_percent"),
            "redis_sync": (pools_agg.get("redis", {}).get("sync") or {}).get("utilization_percent"),
        }
        pool_in_use_map = {
            "postgres": (pools_agg.get("postgres") or {}).get("in_use_total"),
            "redis_async": (pools_agg.get("redis", {}).get("async") or {}).get("in_use_total"),
            "redis_async_decode": (pools_agg.get("redis", {}).get("async_decode") or {}).get("in_use_total"),
            "redis_sync": (pools_agg.get("redis", {}).get("sync") or {}).get("in_use_total"),
        }

        for pool_name, util in pool_util_map.items():
            if util is None:
                continue
            await _record_pool_utilization(
                redis,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component=comp,
                pool_name=pool_name,
                value=float(util),
            )
            windows[pool_name] = await _read_pool_utilization_windows(
                redis,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component=comp,
                pool_name=pool_name,
            )

        for pool_name, in_use in pool_in_use_map.items():
            if in_use is None:
                continue
            await record_metric(
                redis,
                base=f"{REDIS.METRICS.POOL_IN_USE}:{pool_name}",
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component=comp,
                value=float(in_use),
            )
            in_use_windows[pool_name] = await read_metric_windows(
                redis,
                base=f"{REDIS.METRICS.POOL_IN_USE}:{pool_name}",
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component=comp,
            )

        pools_agg["utilization_windows"] = windows
        pools_agg["in_use_windows"] = in_use_windows

    if components.get("proc"):
        autoscaler["proc"] = _autoscaler_decision_proc(components["proc"], queue_data)
    if components.get("ingress"):
        autoscaler["ingress"] = _autoscaler_decision_ingress(components["ingress"], sse_stats)

    # Rolling queue stats windows (proc)
    queue_pressure_windows = None
    queue_depth_windows = None
    try:
        pressure_ratio = queue_data.get("capacity_context", {}).get("pressure_ratio")
        total_depth = queue_data.get("total")
        if pressure_ratio is not None:
            await record_metric(
                redis,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component="proc",
                base=REDIS.METRICS.QUEUE_PRESSURE,
                value=float(pressure_ratio),
            )
            queue_pressure_windows = await read_metric_windows(
                redis,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component="proc",
                base=REDIS.METRICS.QUEUE_PRESSURE,
            )
        if total_depth is not None:
            await record_metric(
                redis,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component="proc",
                base=REDIS.METRICS.QUEUE_DEPTH,
                value=float(total_depth),
            )
            queue_depth_windows = await read_metric_windows(
                redis,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component="proc",
                base=REDIS.METRICS.QUEUE_DEPTH,
            )
    except Exception:
        queue_pressure_windows = None
        queue_depth_windows = None

    if components.get("proc") and components["proc"].get("queue") is not None:
        components["proc"]["queue"]["windows"] = {
            "pressure_ratio": queue_pressure_windows,
            "depth": queue_depth_windows,
        }

    # Task latency percentiles (proc)
    try:
        if components.get("proc") is not None:
            queue_wait_p = await read_metric_percentiles(
                redis,
                base=REDIS.METRICS.TASK_QUEUE_WAIT_MS,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component="proc",
            )
            exec_p = await read_metric_percentiles(
                redis,
                base=REDIS.METRICS.TASK_EXEC_MS,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component="proc",
            )
            components["proc"]["latency"] = {
                "queue_wait_ms": queue_wait_p,
                "exec_ms": exec_p,
            }
    except Exception:
        pass

    # Ingress REST latency percentiles
    try:
        if components.get("ingress") is not None:
            rest_p = await read_metric_percentiles(
                redis,
                base=REDIS.METRICS.INGRESS_REST_MS,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                component="ingress",
            )
            components["ingress"]["latency"] = {
                "rest_ms": rest_p,
            }
    except Exception:
        pass

    # Get throttling statistics for multiple time periods
    throttling_stats = None
    throttling_events = None
    throttling_by_period = {}
    throttling_windows = {}

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

        # Short windows for autoscaler view
        events_key = gateway_adapter.gateway.throttling_monitor.events_key
        throttling_windows = {
            "1m": await _throttling_window_stats(redis, events_key, 60),
            "15m": await _throttling_window_stats(redis, events_key, 900),
            "1h": await _throttling_window_stats(redis, events_key, 3600),
        }

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
        throttling_windows = {}

    # Get circuit breaker status
    circuit_breakers = await gateway_adapter.gateway.circuit_manager.get_all_stats()

    # DB connection capacity warnings (optional)
    db_max_connections, db_max_source = await _resolve_db_max_connections(pg_pool, gateway_config)
    cfg_capacity = gateway_status["gateway_configuration"]["service_capacity"]
    pool_max_per_worker = 0
    try:
        pools_cfg = getattr(gateway_config, "pools", None) if gateway_config else None
        if pools_cfg and pools_cfg.pg_pool_max_size is not None:
            pool_max_per_worker = int(pools_cfg.pg_pool_max_size)
        else:
            pool_max_per_worker = int(cfg_capacity.get("concurrent_requests_per_process") or 0)
    except Exception:
        pool_max_per_worker = int(cfg_capacity.get("concurrent_requests_per_process") or 0)
    processes_per_instance = int(cfg_capacity.get("processes_per_instance") or 1)
    estimated_per_instance = pool_max_per_worker * processes_per_instance
    instance_count = queue_data["capacity_context"]["instance_count"] or 1
    estimated_total = estimated_per_instance * instance_count
    db_warning = False
    db_warning_reason = None
    db_warning_level = None
    db_percent_of_max = None
    if db_max_connections:
        db_percent_of_max = round((estimated_total / db_max_connections) * 100, 1)
        if estimated_total > db_max_connections:
            db_warning = True
            db_warning_level = "exceeds"
            db_warning_reason = (
                f"Estimated DB connections ({estimated_total}) exceed max_connections ({db_max_connections})."
            )
        elif estimated_total == db_max_connections:
            db_warning = True
            db_warning_level = "max"
            db_warning_reason = (
                f"Estimated DB connections ({estimated_total}) are at max_connections ({db_max_connections})."
            )
        elif estimated_total >= int(db_max_connections * 0.8):
            db_warning = True
            db_warning_level = "high"
            db_warning_reason = (
                f"Estimated DB connections ({estimated_total}) are >=80% of max_connections ({db_max_connections})."
            )

    response_data = {
        "components": components,
        "autoscaler": autoscaler,
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

        # Queue stats (full payload from gateway)
        "queue_stats": queue_data,
        "enhanced_queue_stats": {
            "anonymous_queue": queue_data["anonymous"],
            "registered_queue": queue_data["registered"],
            "paid_queue": queue_data.get("paid", 0),
            "privileged_queue": queue_data["privileged"],
            "total_queue": queue_data["total"],
            "base_capacity_per_instance": queue_data["capacity_context"]["base_capacity_per_instance"],
            "alive_instances": queue_data["capacity_context"]["alive_instances"],
            "instance_count": queue_data["capacity_context"]["instance_count"],
            "weighted_max_capacity": queue_data["capacity_context"]["weighted_max_capacity"],
            "pressure_ratio": queue_data["capacity_context"]["pressure_ratio"],
            "accepting_anonymous": queue_data["capacity_context"]["accepting_anonymous"],
            "accepting_registered": queue_data["capacity_context"]["accepting_registered"],
            "accepting_paid": queue_data["capacity_context"].get("accepting_paid"),
            "accepting_privileged": queue_data["capacity_context"]["accepting_privileged"]
        },

        # Backpressure policy with chat REST context
        "backpressure_policy": {
            "thresholds": queue_data["capacity_context"]["thresholds"],
            "current_effects": {
                "anonymous_blocked": not queue_data["capacity_context"]["accepting_anonymous"],
                "registered_blocked": not queue_data["capacity_context"]["accepting_registered"],
                "paid_blocked": not queue_data["capacity_context"].get("accepting_paid", True),
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
                "paid": {
                    "size": queue_data.get("paid", 0),
                    "avg_wait": queue_data["analytics"]["avg_wait_times"].get("paid", 0),
                    "throughput": queue_data["analytics"]["throughput_metrics"].get("paid", 0),
                    "blocked": not queue_data["capacity_context"].get("accepting_paid", True)
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
        "throttling_windows": throttling_windows,
        "recent_throttling_events": throttling_events,

        # Circuit breakers
        "circuit_breakers": circuit_breakers,

        # Gateway configuration (raw; formatted in chat monitoring endpoint)
        "gateway_configuration": gateway_status["gateway_configuration"],
        "gateway_config_raw": raw_config,
        "gateway_config_components": {k: v.to_dict() for k, v in component_configs.items()} if component_configs else None,
        "_gateway_status": gateway_status,

        # Capacity transparency with chat REST focus
        "capacity_transparency": capacity_transparency,

        "db_connections": {
            "max_connections": db_max_connections,
            "source": db_max_source,
            "pool_max_per_worker": pool_max_per_worker,
            "processes_per_instance": processes_per_instance,
            "estimated_per_instance": estimated_per_instance,
            "instance_count": instance_count,
            "estimated_total": estimated_total,
            "percent_of_max": db_percent_of_max,
            "warning": db_warning,
            "warning_reason": db_warning_reason,
            "warning_level": db_warning_level,
        },
        "sse_connections": sse_stats,
        "connection_pools": {
            "components": {comp: data.get("pools_aggregate") for comp, data in components.items()},
        },

        "timestamp": current_time,
        "redis_info": {
            "process_keys_found": len(process_keys),
            "active_processes": global_stats['total_actual'],
            "chat_rest_processes": global_stats['chat_rest_actual'],
            "max_queue_size": queue_data["capacity_context"]["weighted_max_capacity"]
        }
    }

    return response_data
