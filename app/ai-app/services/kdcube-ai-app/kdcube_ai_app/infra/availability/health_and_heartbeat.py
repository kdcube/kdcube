# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/availability/health_and_heartbeat.py
"""
Multiprocess-aware distributed system for distributed app with the single point of "map" (the chatbot and "prompt" api)
Handles N replicas per service type on same instance with proper health tracking
"""
import asyncio
import json
import time
import uuid
import os
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
import redis.asyncio as aioredis
import logging
from enum import Enum

from kdcube_ai_app.infra.namespaces import REDIS

logger = logging.getLogger(__name__)

SERVICES_PORTS = {
    "chat": 8010,
    "kb": 8000,
    "monitoring": 8080
}
class ServiceHealth(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"

@dataclass
class ProcessHeartbeat:
    instance_id: str
    service_type: str  # 'chat', 'kb'
    service_name: str  # 'rest', 'socketio', 'orchestrator'
    process_id: int    # PID or worker ID
    port: Optional[int]  # For web services
    current_load: int
    max_capacity: int
    last_heartbeat: float
    health_status: ServiceHealth
    metadata: Dict[str, Any] = None

@dataclass
class InstanceServiceStatus:
    instance_id: str
    service_type: str
    service_name: str
    total_processes: int
    healthy_processes: int
    total_load: int
    total_capacity: int
    overall_health: ServiceHealth
    process_details: List[ProcessHeartbeat]
    last_updated: float

class MultiprocessDistributedMiddleware:
    """Enhanced middleware for multiprocess deployments"""

    def __init__(self, redis_url: str, instance_id: str = None):
        self.redis_url = redis_url
        self.instance_id = instance_id or str(uuid.uuid4())
        self.redis = None

        # Redis namespaces
        self.PROCESS_HEARTBEAT_PREFIX = REDIS.PROCESS.HEARTBEAT_PREFIX
        self.INSTANCE_STATUS_PREFIX = REDIS.INSTANCE.HEARTBEAT_PREFIX
        self.QUEUE_PREFIX = REDIS.CHAT.PROMPT_QUEUE_PREFIX
        self.CAPACITY_PREFIX = REDIS.SYSTEM.CAPACITY
        self.LOCK_PREFIX = REDIS.SYNCHRONIZATION.LOCK
        self.SERVICE_REGISTRY_PREFIX = REDIS.DISCOVERY.REGISTRY

        self.HEARTBEAT_TTL = 30
        self.PROCESS_TIMEOUT = 45  # Consider process dead after 45s

    async def init_redis(self):
        if not self.redis:
            self.redis = aioredis.from_url(self.redis_url)

    async def send_process_heartbeat(
            self,
            service_type: str,
            service_name: str,
            process_id: int,
            current_load: int,
            max_capacity: int,
            port: Optional[int] = None,
            health_status: ServiceHealth = ServiceHealth.HEALTHY,
            metadata: Dict = None
    ):
        """Send heartbeat for a specific process"""
        await self.init_redis()

        heartbeat = ProcessHeartbeat(
            instance_id=self.instance_id,
            service_type=service_type,
            service_name=service_name,
            process_id=process_id,
            port=port,
            current_load=current_load,
            max_capacity=max_capacity,
            last_heartbeat=time.time(),
            health_status=health_status,
            metadata=metadata or {}
        )
        if current_load > 0:
            print(f"Process {process_id} heartbeat: {heartbeat}")

        key = f"{self.PROCESS_HEARTBEAT_PREFIX}:{self.instance_id}:{service_type}:{service_name}:{process_id}"
        await self.redis.setex(key, self.HEARTBEAT_TTL, json.dumps(asdict(heartbeat), default=str, ensure_ascii=False))

        # Also register service endpoint for local discovery
        if port:
            registry_key = f"{self.SERVICE_REGISTRY_PREFIX}:{self.instance_id}:{service_type}"
            await self.redis.sadd(registry_key, f"localhost:{port}")
            await self.redis.expire(registry_key, self.HEARTBEAT_TTL)

    async def get_local_service_endpoints(self, service_type: str) -> List[str]:
        """Get local service endpoints for this instance"""
        await self.init_redis()

        registry_key = f"{self.SERVICE_REGISTRY_PREFIX}:{self.instance_id}:{service_type}"
        endpoints = await self.redis.smembers(registry_key)
        return [endpoint.decode() for endpoint in endpoints] if endpoints else []

    async def update_instance_service_status(self, service_type: str, service_name: str):
        """Aggregate process heartbeats into instance-level service status"""
        await self.init_redis()

        # Get all process heartbeats for this service on this instance
        pattern = f"{self.PROCESS_HEARTBEAT_PREFIX}:{self.instance_id}:{service_type}:{service_name}:*"
        keys = await self.redis.keys(pattern)

        process_heartbeats = []
        healthy_count = 0
        total_load = 0
        total_capacity = 0

        for key in keys:
            data = await self.redis.get(key)
            if data:
                try:
                    heartbeat = ProcessHeartbeat(**json.loads(data))

                    # Check if process is still alive
                    if time.time() - heartbeat.last_heartbeat > self.PROCESS_TIMEOUT:
                        continue  # Skip dead processes

                    process_heartbeats.append(heartbeat)
                    total_load += heartbeat.current_load
                    total_capacity += heartbeat.max_capacity

                    if heartbeat.health_status == ServiceHealth.HEALTHY:
                        healthy_count += 1

                except Exception as e:
                    logger.error(f"Error parsing process heartbeat {key}: {e}")

        # Determine overall health
        if not process_heartbeats:
            overall_health = ServiceHealth.UNHEALTHY
        elif healthy_count == 0:
            overall_health = ServiceHealth.UNHEALTHY
        elif healthy_count < len(process_heartbeats) * 0.5:  # Less than 50% healthy
            overall_health = ServiceHealth.DEGRADED
        else:
            overall_health = ServiceHealth.HEALTHY

        # Create instance service status
        status = InstanceServiceStatus(
            instance_id=self.instance_id,
            service_type=service_type,
            service_name=service_name,
            total_processes=len(process_heartbeats),
            healthy_processes=healthy_count,
            total_load=total_load,
            total_capacity=total_capacity,
            overall_health=overall_health,
            process_details=process_heartbeats,
            last_updated=time.time()
        )

        # Store aggregated status
        status_key = f"{self.INSTANCE_STATUS_PREFIX}:{self.instance_id}:{service_type}:{service_name}"
        await self.redis.setex(status_key, self.HEARTBEAT_TTL, json.dumps(asdict(status), default=str, ensure_ascii=False))

        return status

    async def get_instance_service_status(self, service_type: str, service_name: str) -> Optional[InstanceServiceStatus]:
        """Get aggregated service status for this instance"""
        await self.init_redis()

        status_key = f"{self.INSTANCE_STATUS_PREFIX}:{self.instance_id}:{service_type}:{service_name}"
        data = await self.redis.get(status_key)

        if data:
            try:
                return InstanceServiceStatus(**json.loads(data))
            except Exception as e:
                logger.error(f"Error parsing instance service status: {e}")

        return None

    async def get_all_instance_statuses(self) -> Dict[str, Dict[str, InstanceServiceStatus]]:
        """Get all instance service statuses across the cluster"""
        await self.init_redis()

        pattern = f"{self.INSTANCE_STATUS_PREFIX}:*"
        keys = await self.redis.keys(pattern)

        instances = {}

        for key in keys:
            data = await self.redis.get(key)
            if data:
                try:
                    status = InstanceServiceStatus(**json.loads(data))
                    instance_id = status.instance_id
                    service_key = f"{status.service_type}_{status.service_name}"

                    if instance_id not in instances:
                        instances[instance_id] = {}

                    instances[instance_id][service_key] = status

                except Exception as e:
                    logger.error(f"Error parsing instance status {key}: {e}")

        return instances

class ProcessHeartbeatManager:
    """Manages heartbeats for a specific process"""

    def __init__(
            self,
            middleware: MultiprocessDistributedMiddleware,
            service_type: str,
            service_name: str,
            process_id: int = None,
            port: Optional[int] = None
    ):
        self.middleware = middleware
        self.service_type = service_type
        self.service_name = service_name
        self.process_id = process_id or os.getpid()
        self.port = port
        self.current_load = 0
        self.max_capacity = int(os.getenv(f"MAX_CONCURRENT_{service_type.upper()}", "5"))
        self.health_status = ServiceHealth.HEALTHY
        self.metadata = {}
        self.heartbeat_task = None

    def set_load(self, current_load: int):
        """Update current load"""
        self.current_load = current_load

        # Update health based on load
        if current_load >= self.max_capacity:
            self.health_status = ServiceHealth.UNHEALTHY
        elif current_load >= self.max_capacity * 0.8:
            self.health_status = ServiceHealth.DEGRADED
        else:
            self.health_status = ServiceHealth.HEALTHY

    def set_health(self, health: ServiceHealth):
        """Manually set health status"""
        self.health_status = health

    def set_metadata(self, **kwargs):
        """Set additional metadata"""
        self.metadata.update(kwargs)

    async def start_heartbeat(self, interval: int = 10):
        """Start sending periodic heartbeats"""
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval))

    async def stop_heartbeat(self):
        """Stop sending heartbeats"""
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self, interval: int):
        """Heartbeat loop"""
        while True:
            try:
                await self.middleware.send_process_heartbeat(
                    self.service_type,
                    self.service_name,
                    self.process_id,
                    self.current_load,
                    self.max_capacity,
                    self.port,
                    self.health_status,
                    self.metadata
                )

                # Update instance-level status
                await self.middleware.update_instance_service_status(
                    self.service_type,
                    self.service_name
                )

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Process heartbeat error: {e}")
                self.health_status = ServiceHealth.UNHEALTHY
                await asyncio.sleep(interval)

class LocalServiceClient:
    """Client that only talks to localhost services"""

    def __init__(self, middleware: MultiprocessDistributedMiddleware):
        self.middleware = middleware
        self.http_client = None

    async def _get_http_client(self):
        if not self.http_client:
            import httpx
            self.http_client = httpx.AsyncClient(timeout=30.0)
        return self.http_client

    async def search_kb_local(self, search_params: Dict, retry_count: int = 3) -> Optional[Dict]:
        """Search KB using only local instances"""

        endpoints = await self.middleware.get_local_service_endpoints("kb")
        if not endpoints:
            logger.warning("No local KB service endpoints available")
            return None

        client = await self._get_http_client()

        # Try endpoints in order
        for endpoint in endpoints:
            try:
                url = f"http://{endpoint}/api/search"
                response = await client.post(url, json=search_params)

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 503:
                    # This KB instance is overloaded, try next
                    continue
                else:
                    logger.warning(f"KB search failed on {endpoint}: {response.status_code}")

            except Exception as e:
                logger.error(f"KB search request failed on {endpoint}: {e}")
                continue

        return None

    async def close(self):
        """Close HTTP client"""
        if self.http_client:
            await self.http_client.aclose()

# Service health checker
class ServiceHealthChecker:
    """Monitors and reports on service health across all processes"""

    def __init__(self, middleware: MultiprocessDistributedMiddleware):
        self.middleware = middleware
        self.checker_task = None

    async def start_monitoring(self, interval: int = 30):
        """Start health monitoring"""
        self.checker_task = asyncio.create_task(self._monitoring_loop(interval))

    async def stop_monitoring(self):
        """Stop health monitoring"""
        if self.checker_task:
            self.checker_task.cancel()
            try:
                await self.checker_task
            except asyncio.CancelledError:
                pass

    async def _monitoring_loop(self, interval: int):
        """Monitor service health and log issues"""
        while True:
            try:
                await self._check_service_health()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitoring error: {e}")
                await asyncio.sleep(interval)

    async def _check_service_health(self):
        """Check health of all services on this instance"""

        # Define expected services
        expected_services = [
            ("chat", "rest"),
            ("chat", "socketio"),
            ("kb", "rest"),
            ("chat", "orchestrator")  # Dramatiq workers
        ]

        for service_type, service_name in expected_services:
            status = await self.middleware.get_instance_service_status(service_type, service_name)

            if not status:
                # logger.debug(f"No status found for {service_type}:{service_name}")
                continue

            if status.overall_health == ServiceHealth.UNHEALTHY:
                logger.error(
                    f"SERVICE UNHEALTHY: {service_type}:{service_name} - "
                    f"{status.healthy_processes}/{status.total_processes} processes healthy"
                )
            elif status.overall_health == ServiceHealth.DEGRADED:
                logger.warning(
                    f"SERVICE DEGRADED: {service_type}:{service_name} - "
                    f"{status.healthy_processes}/{status.total_processes} processes healthy"
                )

@dataclass
class ServiceConfig:
    """Configuration for expected services on an instance"""
    service_type: str
    service_name: str
    expected_processes: int
    ports: List[int] = None

    def get_service_key(self) -> str:
        return f"{self.service_type}_{self.service_name}"

def get_expected_services(INSTANCE_ID) -> Dict[str, List[ServiceConfig]]:
    """
    Define expected services per instance based on environment variables
    """
    # Get configuration from environment
    chat_workers = int(os.getenv("CHAT_APP_PARALLELISM", "1"))
    kb_workers = int(os.getenv("KB_PARALLELISM", "1"))
    orchestrator_workers = int(os.getenv("DRAMATIQ_WORKERS", "4"))

    # Get port configurations
    chat_port = int(os.getenv("CHAT_APP_PORT", "8010"))
    kb_port = int(os.getenv("KB_PORT", "8000"))

    # Define expected services per instance
    expected_services = {
        INSTANCE_ID: [
            ServiceConfig("chat", "rest", chat_workers, [chat_port + i for i in range(chat_workers)]),
            ServiceConfig("kb", "rest", kb_workers, [kb_port + i for i in range(kb_workers)]),
            ServiceConfig("chat", "orchestrator", orchestrator_workers, [])  # No ports for workers
        ]
    }

    return expected_services