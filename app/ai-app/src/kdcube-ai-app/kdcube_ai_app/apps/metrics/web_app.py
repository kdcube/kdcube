# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Metrics service (FastAPI).

Purpose:
- Aggregate metrics from ingress + processor services.
- Provide a single endpoint for autoscalers (ECS/HPA) to scrape.

This is intentionally minimal; we can extend it with richer aggregation
and push-to-CloudWatch/Prometheus exporters later.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import contextlib
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional, Iterable, Tuple

from dotenv import load_dotenv, find_dotenv

_ENV_DIR = Path(__file__).resolve().parent
load_dotenv(_ENV_DIR / ".env.metrics", override=True)
load_dotenv(find_dotenv(usecwd=False))
from kdcube_ai_app.apps.chat.sdk.config import get_settings
get_settings.cache_clear()

from kdcube_ai_app.apps.utils.cors import configure_cors
from kdcube_ai_app.apps.metrics.rest.events import mount_events_routers
from kdcube_ai_app.apps.chat.ingress.resolvers import (
    get_fastapi_adapter,
    get_pg_pool,
    get_redis_clients,
    get_heartbeats_mgr_and_middleware,
    REDIS_URL,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.gateway.config import (
    apply_gateway_config_from_cache,
    apply_gateway_config_from_env,
    should_force_gateway_config_from_env,
    subscribe_gateway_config_updates,
    gateway_config_cache_key,
)
from kdcube_ai_app.apps.metrics.export_metrics import extract_metrics, parse_json_dict
from kdcube_ai_app.infra.metrics.system_monitoring import compute_system_monitoring

import httpx
from prometheus_client import CollectorRegistry, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
# Ensure per-replica instance id is set (do not override explicit env)
os.environ.setdefault("INSTANCE_ID", f"metrics-{uuid.uuid4().hex[:8]}")

import kdcube_ai_app.apps.utils.logging_config as logging_config
logging_config.configure_logging()
logger = logging.getLogger("Metrics.WebApp")

# Metrics server should use processor capacity for queue/backpressure calculations.
os.environ.setdefault("GATEWAY_COMPONENT", "proc")


METRICS_PORT = int(os.getenv("METRICS_PORT", "8090"))
INGRESS_BASE_URL = os.getenv("METRICS_INGRESS_BASE_URL", "").strip()
PROC_BASE_URL = os.getenv("METRICS_PROC_BASE_URL", "").strip()
REQUEST_TIMEOUT = float(os.getenv("METRICS_REQUEST_TIMEOUT_SEC", "5.0"))
METRICS_MODE = os.getenv("METRICS_MODE", "redis").strip().lower()
METRICS_ENABLE_PG_POOL = os.getenv("METRICS_ENABLE_PG_POOL", "0").lower() in {"1", "true", "yes", "on"}

# Scheduled exporter
SCHEDULER_ENABLED = os.getenv("METRICS_SCHEDULER_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
EXPORT_INTERVAL_SEC = float(os.getenv("METRICS_EXPORT_INTERVAL_SEC", "30"))
EXPORT_ON_START = os.getenv("METRICS_EXPORT_ON_START", "1").lower() in {"1", "true", "yes", "on"}

# CloudWatch export
CLOUDWATCH_ENABLED = os.getenv("METRICS_EXPORT_CLOUDWATCH", "0").lower() in {"1", "true", "yes", "on"}
CLOUDWATCH_NAMESPACE = os.getenv("METRICS_CLOUDWATCH_NAMESPACE", "KDCube/Metrics")
CLOUDWATCH_REGION = os.getenv("METRICS_CLOUDWATCH_REGION") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
CLOUDWATCH_DIMENSIONS_JSON = os.getenv("METRICS_CLOUDWATCH_DIMENSIONS_JSON", "").strip()

# Prometheus pushgateway export
PROM_PUSH_ENABLED = os.getenv("METRICS_EXPORT_PROMETHEUS_PUSH", "0").lower() in {"1", "true", "yes", "on"}
PROM_PUSHGATEWAY_URL = os.getenv("METRICS_PROM_PUSHGATEWAY_URL", "").strip()
PROM_JOB_NAME = os.getenv("METRICS_PROM_JOB_NAME", "kdcube_metrics").strip()
PROM_GROUPING_LABELS_JSON = os.getenv("METRICS_PROM_GROUPING_LABELS_JSON", "").strip()
PROM_SCRAPE_CACHE_TTL_SEC = float(os.getenv("METRICS_PROM_SCRAPE_TTL_SEC", "10"))

# One-shot mode (for scheduled tasks)
RUN_ONCE = os.getenv("METRICS_RUN_ONCE", "0").lower() in {"1", "true", "yes", "on"}

# Metric mapping (optional)
METRICS_MAPPING_JSON = os.getenv("METRICS_MAPPING_JSON", "").strip()


def _build_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    name = os.getenv("METRICS_AUTH_HEADER_NAME", "").strip()
    value = os.getenv("METRICS_AUTH_HEADER_VALUE", "").strip()
    if name and value:
        headers[name] = value

    extra_raw = os.getenv("METRICS_HEADERS_JSON", "").strip()
    if extra_raw:
        try:
            extra = json.loads(extra_raw)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    if v is None:
                        continue
                    headers[str(k)] = str(v)
        except Exception:
            logger.warning("Failed to parse METRICS_HEADERS_JSON")
    return headers


def _load_metric_mapping() -> Dict[str, Dict[str, str]]:
    if not METRICS_MAPPING_JSON:
        return {}
    try:
        raw = json.loads(METRICS_MAPPING_JSON)
    except Exception:
        logger.warning("Failed to parse METRICS_MAPPING_JSON")
        return {}
    mapping: Dict[str, Dict[str, str]] = {}
    if isinstance(raw, dict):
        for key, val in raw.items():
            if isinstance(val, str):
                mapping[str(key)] = {"name": val}
            elif isinstance(val, dict):
                name = val.get("name") or val.get("metric") or val.get("cloudwatch_name")
                unit = val.get("unit")
                if name:
                    mapping[str(key)] = {"name": str(name), "unit": str(unit) if unit else None}
    return mapping


def _apply_mapping(metric_name: str, mapping: Dict[str, Dict[str, str]]) -> Tuple[str, Optional[str]]:
    cfg = mapping.get(metric_name)
    if cfg:
        return cfg.get("name", metric_name), cfg.get("unit")
    return metric_name, None


def _sanitize_prom_name(name: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in name.replace(".", "_").replace("/", "_"))
    if not safe or safe[0].isdigit():
        safe = f"m_{safe}"
    return safe


def _chunk(items: Iterable[Tuple[str, float]], size: int = 20) -> Iterable[list[Tuple[str, float]]]:
    chunk: list[Tuple[str, float]] = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


async def _fetch_json(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    if not url:
        raise HTTPException(status_code=400, detail="Base URL is not configured")
    try:
        resp = await client.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Metrics upstream error: {e}")


async def _fetch_system(client: httpx.AsyncClient, base_url: str) -> Dict[str, Any]:
    if not base_url:
        raise HTTPException(status_code=400, detail="Base URL is not configured")
    url = f"{base_url.rstrip('/')}/monitoring/system"
    return await _fetch_json(client, url)


async def _export_cloudwatch(metrics: Dict[str, float]) -> None:
    if not CLOUDWATCH_ENABLED or not metrics:
        return
    try:
        import boto3
    except Exception:
        logger.exception("boto3 is not installed; CloudWatch export disabled")
        return

    dims = parse_json_dict(CLOUDWATCH_DIMENSIONS_JSON)
    dimensions = [{"Name": k, "Value": v} for k, v in dims.items()]
    namespace = CLOUDWATCH_NAMESPACE
    region = CLOUDWATCH_REGION
    mapping = _load_metric_mapping()

    def _send() -> None:
        client = boto3.client("cloudwatch", region_name=region) if region else boto3.client("cloudwatch")
        for chunk in _chunk(metrics.items(), size=20):
            data = []
            for name, value in chunk:
                mapped_name, unit_override = _apply_mapping(name, mapping)
                unit = unit_override or "Count"
                if unit_override is None:
                    if name.endswith("_percent"):
                        unit = "Percent"
                    elif name.endswith("_seconds"):
                        unit = "Seconds"
                data.append({
                    "MetricName": mapped_name.replace(".", "/"),
                    "Dimensions": dimensions,
                    "Value": float(value),
                    "Unit": unit,
                })
            client.put_metric_data(Namespace=namespace, MetricData=data)

    await asyncio.to_thread(_send)


async def _export_prometheus(metrics: Dict[str, float]) -> None:
    if not PROM_PUSH_ENABLED or not metrics or not PROM_PUSHGATEWAY_URL:
        return
    try:
        from prometheus_client import push_to_gateway
    except Exception:
        logger.exception("prometheus_client is not installed; Prometheus export disabled")
        return

    grouping = parse_json_dict(PROM_GROUPING_LABELS_JSON)
    mapping = _load_metric_mapping()

    def _push() -> None:
        registry = CollectorRegistry()
        for name, value in metrics.items():
            mapped_name, _ = _apply_mapping(name, mapping)
            safe = "kdcube_" + _sanitize_prom_name(mapped_name)
            g = Gauge(safe, "kdcube metric", registry=registry)
            g.set(value)
        push_to_gateway(PROM_PUSHGATEWAY_URL, job=PROM_JOB_NAME, registry=registry, grouping_key=grouping)

    await asyncio.to_thread(_push)


async def _scheduler_loop(app: FastAPI) -> None:
    if EXPORT_ON_START:
        await _export_once(app)
    while not app.state.metrics_stop.is_set():
        try:
            await asyncio.sleep(EXPORT_INTERVAL_SEC)
        except asyncio.CancelledError:
            break
        await _export_once(app)


async def _export_once(app: FastAPI) -> None:
    metrics = await _collect_metrics(app)
    if not metrics:
        return

    await _export_cloudwatch(metrics)
    await _export_prometheus(metrics)


async def _collect_metrics(app: FastAPI) -> Dict[str, float]:
    if METRICS_MODE == "redis":
        system_data = await _collect_system_redis(app)
        return extract_metrics(system_data, system_data)
    ingress_data = None
    proc_data = None
    if INGRESS_BASE_URL:
        try:
            ingress_data = await _fetch_system(app.state.http, INGRESS_BASE_URL)
        except Exception as e:
            logger.warning("Ingress metrics fetch failed: %s", e)
    if PROC_BASE_URL:
        try:
            proc_data = await _fetch_system(app.state.http, PROC_BASE_URL)
        except Exception as e:
            logger.warning("Processor metrics fetch failed: %s", e)
    return extract_metrics(ingress_data, proc_data)


async def _collect_system_redis(app: FastAPI) -> Dict[str, Any]:
    if not app.state.gateway_adapter or not app.state.middleware:
        raise HTTPException(status_code=500, detail="Metrics service not initialized (redis mode)")
    redis_clients = {
        "async": app.state.redis_async,
        "async_decode": app.state.redis_async_decode,
        "sync": app.state.redis_sync,
    }
    data = await compute_system_monitoring(
        redis=app.state.redis_async,
        gateway_adapter=app.state.gateway_adapter,
        middleware=app.state.middleware,
        instance_id=os.getenv("INSTANCE_ID", "metrics"),
        expected_services=None,
        sse_hub=None,
        pg_pool=app.state.pg_pool,
        redis_clients=redis_clients,
    )
    data.pop("_gateway_status", None)
    return data


async def _run_once() -> int:
    if METRICS_MODE == "redis":
        # Initialize minimal state for redis mode
        headers = _build_headers()
        async with httpx.AsyncClient(headers=headers) as client:
            # Build app-like context
            class _Tmp:
                pass
            tmp = _Tmp()
            tmp.http = client
            tmp.redis_async, tmp.redis_async_decode, tmp.redis_sync = await get_redis_clients()
            tmp.gateway_adapter = get_fastapi_adapter()
            settings = get_settings()
            if should_force_gateway_config_from_env():
                await apply_gateway_config_from_env(
                    gateway_adapter=tmp.gateway_adapter,
                    tenant=settings.TENANT,
                    project=settings.PROJECT,
                    redis_url=REDIS_URL,
                    publish=False,
                )
            else:
                await apply_gateway_config_from_cache(
                    gateway_adapter=tmp.gateway_adapter,
                    tenant=settings.TENANT,
                    project=settings.PROJECT,
                    redis_url=REDIS_URL,
                )
            tmp.middleware, _ = get_heartbeats_mgr_and_middleware(
                service_type="metrics",
                service_name="metrics",
                port=METRICS_PORT,
                redis_client=tmp.redis_async,
            )
            tmp.pg_pool = await get_pg_pool() if METRICS_ENABLE_PG_POOL else None
            system_data = await _collect_system_redis(tmp)
            metrics = extract_metrics(system_data, system_data)
            if not metrics:
                logger.warning("No metrics collected (redis mode)")
                return 2
            await _export_cloudwatch(metrics)
            await _export_prometheus(metrics)
        return 0
    headers = _build_headers()
    async with httpx.AsyncClient(headers=headers) as client:
        ingress_data = None
        proc_data = None
        if INGRESS_BASE_URL:
            try:
                ingress_data = await _fetch_system(client, INGRESS_BASE_URL)
            except Exception as e:
                logger.warning("Ingress metrics fetch failed: %s", e)
        if PROC_BASE_URL:
            try:
                proc_data = await _fetch_system(client, PROC_BASE_URL)
            except Exception as e:
                logger.warning("Processor metrics fetch failed: %s", e)
        metrics = extract_metrics(ingress_data, proc_data)
        if not metrics:
            logger.warning("No metrics collected (check base URLs / auth)")
            return 2
        await _export_cloudwatch(metrics)
        await _export_prometheus(metrics)
    return 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.headers = _build_headers()
    app.state.http = httpx.AsyncClient(headers=app.state.headers)
    app.state.metrics_stop = asyncio.Event()
    app.state.metrics_task = None
    app.state.prom_registry = CollectorRegistry()
    app.state.prom_gauges = {}
    app.state.metric_mapping = _load_metric_mapping()
    app.state.last_metrics = {}
    app.state.last_metrics_ts = 0.0
    app.state.gateway_adapter = None
    app.state.redis_async = None
    app.state.redis_async_decode = None
    app.state.redis_sync = None
    app.state.middleware = None
    app.state.pg_pool = None
    app.state.gateway_config_task = None
    app.state.gateway_config_stop = None
    logger.info(
        "Metrics service ready: port=%s mode=%s ingress_base=%s proc_base=%s",
        METRICS_PORT,
        METRICS_MODE,
        INGRESS_BASE_URL or "<unset>",
        PROC_BASE_URL or "<unset>",
    )
    if METRICS_MODE == "redis":
        # Shared Redis pools
        app.state.redis_async, app.state.redis_async_decode, app.state.redis_sync = await get_redis_clients()
        # Gateway adapter + config cache
        app.state.gateway_adapter = get_fastapi_adapter()
        settings = get_settings()
        if should_force_gateway_config_from_env():
            await apply_gateway_config_from_env(
                gateway_adapter=app.state.gateway_adapter,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                redis_url=REDIS_URL,
                publish=False,
            )
            app.state.gateway_config_source = "env (forced)"
            logger.info(
                "Gateway config source: env (forced) tenant=%s project=%s",
                settings.TENANT,
                settings.PROJECT,
            )
        else:
            cache_applied = await apply_gateway_config_from_cache(
                gateway_adapter=app.state.gateway_adapter,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                redis_url=REDIS_URL,
            )
            if cache_applied:
                app.state.gateway_config_source = "redis-cache"
                logger.info(
                    "Gateway config source: redis-cache tenant=%s project=%s key=%s",
                    settings.TENANT,
                    settings.PROJECT,
                    gateway_config_cache_key(tenant=settings.TENANT, project=settings.PROJECT),
                )
            else:
                source = "env"
                if os.getenv("GATEWAY_CONFIG_JSON"):
                    source = "env (GATEWAY_CONFIG_JSON)"
                app.state.gateway_config_source = source
                logger.info(
                    "Gateway config source: %s tenant=%s project=%s",
                    source,
                    settings.TENANT,
                    settings.PROJECT,
                )
        app.state.gateway_config_stop = asyncio.Event()
        app.state.gateway_config_task = asyncio.create_task(
            subscribe_gateway_config_updates(
                gateway_adapter=app.state.gateway_adapter,
                tenant=settings.TENANT,
                project=settings.PROJECT,
                redis_url=REDIS_URL,
                stop_event=app.state.gateway_config_stop,
            )
        )
        # Heartbeat middleware (no heartbeat started; read-only)
        app.state.middleware, _ = get_heartbeats_mgr_and_middleware(
            service_type="metrics",
            service_name="metrics",
            port=METRICS_PORT,
            redis_client=app.state.redis_async,
        )
        if METRICS_ENABLE_PG_POOL:
            app.state.pg_pool = await get_pg_pool()
    if SCHEDULER_ENABLED:
        logger.info(
            "Metrics scheduler enabled interval=%ss cloudwatch=%s prometheus_push=%s",
            EXPORT_INTERVAL_SEC,
            CLOUDWATCH_ENABLED,
            PROM_PUSH_ENABLED,
        )
        app.state.metrics_task = asyncio.create_task(_scheduler_loop(app))
    yield
    try:
        if app.state.gateway_config_task:
            app.state.gateway_config_stop.set()
            app.state.gateway_config_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await app.state.gateway_config_task
        if app.state.metrics_task:
            app.state.metrics_stop.set()
            app.state.metrics_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await app.state.metrics_task
        await app.state.http.aclose()
    except Exception:
        logger.exception("Failed to close httpx client")


app = FastAPI(title="Metrics API", description="Metrics aggregation service", lifespan=lifespan)
configure_cors(app)
mount_events_routers(app)


@app.get("/health")
async def health():
    return {"status": "ok", "ts": time.time()}


@app.get("/metrics/ingress/system")
async def metrics_ingress_system():
    if METRICS_MODE == "redis":
        return await _collect_system_redis(app)
    return await _fetch_system(app.state.http, INGRESS_BASE_URL)


@app.get("/metrics/proc/system")
async def metrics_proc_system():
    if METRICS_MODE == "redis":
        return await _collect_system_redis(app)
    return await _fetch_system(app.state.http, PROC_BASE_URL)


@app.get("/metrics/combined")
async def metrics_combined():
    tasks = []
    ingress_missing = None
    proc_missing = None
    if METRICS_MODE == "redis":
        data = await _collect_system_redis(app)
        return JSONResponse(content={"timestamp": time.time(), "mode": "redis", "system": data})
    if INGRESS_BASE_URL:
        tasks.append(_fetch_json(app.state.http, f"{INGRESS_BASE_URL.rstrip('/')}/monitoring/system"))
    else:
        ingress_missing = "METRICS_INGRESS_BASE_URL is not set"
        tasks.append(asyncio.sleep(0, result=None))
    if PROC_BASE_URL:
        tasks.append(_fetch_json(app.state.http, f"{PROC_BASE_URL.rstrip('/')}/monitoring/system"))
    else:
        proc_missing = "METRICS_PROC_BASE_URL is not set"
        tasks.append(asyncio.sleep(0, result=None))

    ingress_data, proc_data = await asyncio.gather(*tasks, return_exceptions=True)

    def _wrap(value: Any) -> Dict[str, Any]:
        if isinstance(value, Exception):
            return {"error": str(value)}
        return value

    payload = {
        "timestamp": time.time(),
        "ingress": {"error": ingress_missing} if ingress_missing else _wrap(ingress_data),
        "proc": {"error": proc_missing} if proc_missing else _wrap(proc_data),
    }
    return JSONResponse(content=payload)


@app.get("/metrics/export")
async def metrics_export_now():
    await _export_once(app)
    return {"status": "ok", "ts": time.time()}


@app.get("/metrics")
async def metrics_prometheus():
    # Cache to avoid overloading upstreams on fast scrapes
    now = time.time()
    cached = app.state.last_metrics
    if cached and (now - app.state.last_metrics_ts) < PROM_SCRAPE_CACHE_TTL_SEC:
        metrics = cached
    else:
        metrics = await _collect_metrics(app)
        if metrics:
            app.state.last_metrics = metrics
            app.state.last_metrics_ts = now
        else:
            metrics = cached or {}

    # Update registry
    mapping = app.state.metric_mapping or {}
    for name, value in metrics.items():
        mapped_name, _ = _apply_mapping(name, mapping)
        prom_name = "kdcube_" + _sanitize_prom_name(mapped_name)
        gauge = app.state.prom_gauges.get(prom_name)
        if gauge is None:
            gauge = Gauge(prom_name, "kdcube metric", registry=app.state.prom_registry)
            app.state.prom_gauges[prom_name] = gauge
        gauge.set(float(value))

    payload = generate_latest(app.state.prom_registry)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn
    if RUN_ONCE:
        raise SystemExit(asyncio.run(_run_once()))
    else:
        uvicorn.run(
            "kdcube_ai_app.apps.metrics.web_app:app",
            host="0.0.0.0",
            port=METRICS_PORT,
            log_config=None,
            log_level=None,
        )
