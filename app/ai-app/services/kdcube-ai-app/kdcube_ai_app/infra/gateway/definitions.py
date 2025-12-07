# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/gateway/definitions.py
import time
from dataclasses import dataclass
import os
import logging
import json

from typing import Optional, List, Dict, Any

from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.infra.namespaces import ns_key, REDIS

logger = logging.getLogger(__name__)

@dataclass
class QueueStats:
    """Individual queue statistics"""
    anonymous_queue: int
    registered_queue: int
    privileged_queue: int
    total_queue: int

    # Capacity information
    base_capacity_per_instance: int
    alive_instances: List[str]
    instance_count: int
    weighted_max_capacity: int

    # Pressure information
    pressure_ratio: float
    accepting_anonymous: bool
    accepting_registered: bool
    accepting_privileged: bool

    # Threshold information
    anonymous_threshold: int
    registered_threshold: int
    hard_limit_threshold: int

    # Processing metrics
    avg_wait_times: Dict[str, float]
    throughput_metrics: Dict[str, int]

class GatewayError(Exception):
    """Base gateway error"""
    def __init__(self, message: str, code: int = 500, retry_after: Optional[int] = None, session: UserSession = None):
        self.message = message
        self.code = code
        self.session = session
        self.retry_after = retry_after
        super().__init__(message)

@dataclass
class ActualProcessInfo:
    """Information about actual running processes"""
    instance_id: str
    service_type: str
    service_name: str
    process_count: int
    healthy_processes: int
    total_load: int
    total_capacity: int
    process_details: List[Dict[str, Any]]

@dataclass
class DynamicCapacityMetrics:
    """Capacity metrics based on actual running processes"""
    # Configuration (what we expect)
    configured_concurrent_per_process: int
    configured_processes_per_instance: int
    configured_avg_processing_time: float
    capacity_buffer: float
    queue_depth_multiplier: float

    # Actual runtime values (what we detected)
    actual_processes_per_instance: int
    actual_healthy_processes_per_instance: int
    actual_concurrent_per_instance: int
    actual_effective_concurrent_per_instance: int
    actual_queue_capacity_per_instance: int
    actual_total_capacity_per_instance: int
    actual_theoretical_hourly_per_instance: int

    # Process health info
    process_health_ratio: float  # healthy / total actual
    capacity_utilization_from_load: float  # current load / capacity

    # Thresholds
    anonymous_threshold_ratio: float
    registered_threshold_ratio: float
    hard_limit_threshold_ratio: float

    @classmethod
    def from_config_and_processes(cls, gateway_config, actual_processes: List[ActualProcessInfo]) -> 'DynamicCapacityMetrics':
        """Create metrics from gateway configuration and actual process data"""
        # Extract configuration values
        concurrent_per_process = gateway_config.service_capacity.concurrent_requests_per_process
        configured_processes = gateway_config.service_capacity.processes_per_instance
        avg_processing_time = gateway_config.service_capacity.avg_processing_time_seconds
        capacity_buffer = gateway_config.backpressure.capacity_buffer
        queue_depth_multiplier = gateway_config.backpressure.queue_depth_multiplier

        # Calculate actual process counts - ONLY CHAT REST PROCESSES
        actual_total_processes = 0
        actual_healthy_processes = 0
        total_current_load = 0
        total_reported_capacity = 0

        # Count ONLY chat REST processes for capacity calculation
        for process_info in actual_processes:
            if process_info.service_type == "chat" and process_info.service_name == "rest":
                actual_total_processes += process_info.process_count
                actual_healthy_processes += process_info.healthy_processes
                total_current_load += process_info.total_load
                total_reported_capacity += process_info.total_capacity

        # Use actual CHAT REST process count for calculations
        effective_processes = max(actual_healthy_processes, 1)  # At least 1 to avoid division by zero

        # Compute derived values based on ACTUAL CHAT REST processes only
        actual_concurrent = concurrent_per_process * effective_processes
        actual_effective_concurrent = int(actual_concurrent * (1 - capacity_buffer))
        actual_queue_capacity = int(actual_concurrent * queue_depth_multiplier)
        actual_total_capacity = actual_effective_concurrent + actual_queue_capacity
        actual_theoretical_hourly = int((actual_concurrent * 3600) / avg_processing_time)

        # Calculate health and utilization ratios
        process_health_ratio = (actual_healthy_processes / max(actual_total_processes, 1)) if actual_total_processes > 0 else 0
        capacity_utilization = (total_current_load / max(total_reported_capacity, 1)) if total_reported_capacity > 0 else 0

        return cls(
            # Configuration
            configured_concurrent_per_process=concurrent_per_process,
            configured_processes_per_instance=configured_processes,
            configured_avg_processing_time=avg_processing_time,
            capacity_buffer=capacity_buffer,
            queue_depth_multiplier=queue_depth_multiplier,

        # Actual runtime values - BASED ON CHAT REST PROCESSES ONLY
        actual_processes_per_instance=actual_total_processes,
        actual_healthy_processes_per_instance=actual_healthy_processes,
        actual_concurrent_per_instance=actual_concurrent,
        actual_effective_concurrent_per_instance=actual_effective_concurrent,
        actual_queue_capacity_per_instance=actual_queue_capacity,
        actual_total_capacity_per_instance=actual_total_capacity,
        actual_theoretical_hourly_per_instance=actual_theoretical_hourly,

            # Health metrics
            process_health_ratio=process_health_ratio,
            capacity_utilization_from_load=capacity_utilization,

            # Thresholds
            anonymous_threshold_ratio=gateway_config.backpressure.anonymous_pressure_threshold,
            registered_threshold_ratio=gateway_config.backpressure.registered_pressure_threshold,
            hard_limit_threshold_ratio=gateway_config.backpressure.hard_limit_threshold
        )

    def get_thresholds_for_instances(self, instance_count: int, actual_process_data: Dict[str, List[ActualProcessInfo]]) -> Dict[str, int]:
        """Calculate thresholds based on actual processes across all instances"""
        total_actual_capacity = 0

        for instance_id, processes in actual_process_data.items():
            instance_capacity = 0
            for process_info in processes:
                if process_info.service_type == "chat" and process_info.service_name == "rest":
                    # Use actual healthy processes for capacity calculation
                    instance_actual_concurrent = (
                            self.configured_concurrent_per_process * process_info.healthy_processes
                    )
                    instance_effective = int(instance_actual_concurrent * (1 - self.capacity_buffer))
                    instance_queue = int(instance_actual_concurrent * self.queue_depth_multiplier)
                    instance_capacity += instance_effective + instance_queue

            total_actual_capacity += instance_capacity

        return {
            "anonymous_threshold": int(total_actual_capacity * self.anonymous_threshold_ratio),
            "registered_threshold": int(total_actual_capacity * self.registered_threshold_ratio),
            "hard_limit": int(total_actual_capacity * self.hard_limit_threshold_ratio),
            "total_capacity": total_actual_capacity
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "configuration": {
                "configured_concurrent_per_process": self.configured_concurrent_per_process,
                "configured_processes_per_instance": self.configured_processes_per_instance,
                "configured_avg_processing_time_seconds": self.configured_avg_processing_time,
                "capacity_buffer_percent": round(self.capacity_buffer * 100, 1),
                "queue_depth_multiplier": self.queue_depth_multiplier
            },
            "actual_runtime": {
                "actual_processes_per_instance": self.actual_processes_per_instance,
                "actual_healthy_processes_per_instance": self.actual_healthy_processes_per_instance,
                "actual_concurrent_per_instance": self.actual_concurrent_per_instance,
                "actual_effective_concurrent_per_instance": self.actual_effective_concurrent_per_instance,
                "actual_queue_capacity_per_instance": self.actual_queue_capacity_per_instance,
                "actual_total_capacity_per_instance": self.actual_total_capacity_per_instance,
                "actual_theoretical_hourly_per_instance": self.actual_theoretical_hourly_per_instance
            },
            "health_metrics": {
                "process_health_ratio": round(self.process_health_ratio, 3),
                "capacity_utilization_from_load": round(self.capacity_utilization_from_load, 3),
                "processes_vs_configured": {
                    "configured": self.configured_processes_per_instance,
                    "actual": self.actual_processes_per_instance,
                    "healthy": self.actual_healthy_processes_per_instance,
                    "process_deficit": max(0, self.configured_processes_per_instance - self.actual_healthy_processes_per_instance)
                }
            },
            "threshold_ratios": {
                "anonymous_threshold_ratio": self.anonymous_threshold_ratio,
                "registered_threshold_ratio": self.registered_threshold_ratio,
                "hard_limit_threshold_ratio": self.hard_limit_threshold_ratio
            }
        }

class DynamicCapacityCalculator:
    """Capacity calculator that uses actual running processes from heartbeats"""

    def __init__(self, gateway_config, redis_client=None):
        self.gateway_config = gateway_config
        self.redis_client = redis_client
        self._cached_metrics = None
        self._last_update = 0
        self._cache_ttl = 10  # Cache for 10 seconds

        # Redis keys for heartbeat data
        self.PROCESS_HEARTBEAT_PREFIX = self.ns(REDIS.PROCESS.HEARTBEAT_PREFIX)
        self.INSTANCE_STATUS_PREFIX = self.ns(REDIS.INSTANCE.HEARTBEAT_PREFIX)

    def ns(self, base: str) -> str:
        return ns_key(base, tenant=self.gateway_config.tenant_id, project=self.gateway_config.project_id)

    async def get_actual_process_info(self) -> Dict[str, List[ActualProcessInfo]]:
        """Get actual process information from Redis heartbeats"""
        if not self.redis_client:
            logger.warning("No Redis client available, using configured process counts")
            return self._get_fallback_process_info()

        try:
            # Get all process heartbeats
            pattern = f"{self.PROCESS_HEARTBEAT_PREFIX}:*"
            keys = await self.redis_client.keys(pattern)

            process_data = {}
            current_time = time.time()

            for key in keys:
                try:
                    data = await self.redis_client.get(key)
                    if not data:
                        continue

                    heartbeat = json.loads(data)
                    instance_id = heartbeat['instance_id']
                    service_type = heartbeat['service_type']
                    service_name = heartbeat['service_name']

                    # Check if heartbeat is recent (not stale)
                    age_seconds = current_time - heartbeat.get('last_heartbeat', 0)
                    if age_seconds > 60:  # Consider stale after 60 seconds
                        continue

                    # Determine health
                    raw_health = heartbeat.get('health_status', 'unknown')
                    is_healthy = ('HEALTHY' in str(raw_health).upper() or
                                  str(raw_health).lower() == 'healthy')

                    # Group by instance and service
                    service_key = f"{service_type}_{service_name}"
                    if instance_id not in process_data:
                        process_data[instance_id] = {}
                    if service_key not in process_data[instance_id]:
                        process_data[instance_id][service_key] = {
                            'processes': [],
                            'healthy_count': 0,
                            'total_load': 0,
                            'total_capacity': 0
                        }

                    # Add process info
                    process_info = {
                        'pid': heartbeat.get('process_id'),
                        'load': heartbeat.get('current_load', 0),
                        'capacity': heartbeat.get('max_capacity', 0),
                        'healthy': is_healthy,
                        'last_heartbeat': heartbeat.get('last_heartbeat', current_time)
                    }

                    process_data[instance_id][service_key]['processes'].append(process_info)
                    process_data[instance_id][service_key]['total_load'] += process_info['load']
                    process_data[instance_id][service_key]['total_capacity'] += process_info['capacity']

                    if is_healthy:
                        process_data[instance_id][service_key]['healthy_count'] += 1

                except Exception as e:
                    logger.error(f"Error parsing heartbeat {key}: {e}")
                    continue

            # Convert to ActualProcessInfo objects
            result = {}
            for instance_id, services in process_data.items():
                result[instance_id] = []
                for service_key, service_data in services.items():
                    service_type, service_name = service_key.split('_', 1)

                    actual_process_info = ActualProcessInfo(
                        instance_id=instance_id,
                        service_type=service_type,
                        service_name=service_name,
                        process_count=len(service_data['processes']),
                        healthy_processes=service_data['healthy_count'],
                        total_load=service_data['total_load'],
                        total_capacity=service_data['total_capacity'],
                        process_details=service_data['processes']
                    )
                    result[instance_id].append(actual_process_info)

            return result

        except Exception as e:
            logger.error(f"Error getting actual process info from Redis: {e}")
            return self._get_fallback_process_info()

    def _get_fallback_process_info(self) -> Dict[str, List[ActualProcessInfo]]:
        """Fallback to configured process counts when Redis is unavailable"""
        instance_id = self.gateway_config.instance_id
        configured_processes = self.gateway_config.service_capacity.processes_per_instance
        configured_capacity = self.gateway_config.service_capacity.concurrent_requests_per_process

        # Create fallback data assuming all configured processes are healthy
        fallback_info = ActualProcessInfo(
            instance_id=instance_id,
            service_type="chat",
            service_name="rest",
            process_count=configured_processes,
            healthy_processes=configured_processes,
            total_load=0,  # Unknown
            total_capacity=configured_capacity * configured_processes,
            process_details=[]
        )

        return {instance_id: [fallback_info]}

    async def get_dynamic_metrics(self) -> DynamicCapacityMetrics:
        """Get capacity metrics based on actual running processes"""
        current_time = time.time()

        # Use cache if still valid
        if (self._cached_metrics and
                current_time - self._last_update < self._cache_ttl):
            return self._cached_metrics

        # Get actual process information
        actual_processes_data = await self.get_actual_process_info()

        # Extract processes for this instance
        instance_id = self.gateway_config.instance_id
        instance_processes = actual_processes_data.get(instance_id, [])

        # Create metrics
        self._cached_metrics = DynamicCapacityMetrics.from_config_and_processes(
            self.gateway_config, instance_processes
        )
        self._last_update = current_time

        return self._cached_metrics

    async def get_base_queue_size_per_instance(self) -> int:
        """Get base queue size based on actual processes"""
        metrics = await self.get_dynamic_metrics()
        return metrics.actual_total_capacity_per_instance

    async def get_capacity_thresholds(self, actual_instances_data: Dict[str, List[ActualProcessInfo]]) -> Dict[str, int]:
        """Get capacity thresholds based on actual processes across all instances"""
        metrics = await self.get_dynamic_metrics()
        return metrics.get_thresholds_for_instances(
            len(actual_instances_data),
            actual_instances_data
        )

    async def get_monitoring_data(self) -> Dict[str, Any]:
        """Get comprehensive monitoring data with actual vs configured comparison"""
        metrics = await self.get_dynamic_metrics()
        actual_processes_data = await self.get_actual_process_info()

        # Calculate system-wide metrics - ONLY CHAT REST PROCESSES
        total_instances = len(actual_processes_data)
        total_configured_processes = metrics.configured_processes_per_instance * total_instances

        # Count all processes (for general health)
        total_actual_processes = sum(
            sum(p.process_count for p in processes)
            for processes in actual_processes_data.values()
        )
        total_healthy_processes = sum(
            sum(p.healthy_processes for p in processes)
            for processes in actual_processes_data.values()
        )

        # Count ONLY chat REST processes for capacity calculations
        chat_rest_actual_processes = sum(
            sum(p.process_count for p in processes
                if p.service_type == "chat" and p.service_name == "rest")
            for processes in actual_processes_data.values()
        )
        chat_rest_healthy_processes = sum(
            sum(p.healthy_processes for p in processes
                if p.service_type == "chat" and p.service_name == "rest")
            for processes in actual_processes_data.values()
        )

        thresholds = await self.get_capacity_thresholds(actual_processes_data)

        return {
            "capacity_metrics": metrics.to_dict(),
            "instance_scaling": {
                "detected_instances": total_instances,
                "total_configured_processes": total_configured_processes,
                "total_actual_processes": total_actual_processes,
                "total_healthy_processes": total_healthy_processes,
                "process_health_ratio": total_healthy_processes / max(total_actual_processes, 1),

                # Capacity metrics based ONLY on chat REST processes
                "chat_rest_configured_processes": metrics.configured_processes_per_instance * total_instances,
                "chat_rest_actual_processes": chat_rest_actual_processes,
                "chat_rest_healthy_processes": chat_rest_healthy_processes,
                "chat_rest_health_ratio": chat_rest_healthy_processes / max(chat_rest_actual_processes, 1) if chat_rest_actual_processes > 0 else 0,

            # Capacity based on actual healthy CHAT REST processes only
            "total_concurrent_capacity": metrics.configured_concurrent_per_process * chat_rest_healthy_processes,
            "total_effective_capacity": int(metrics.configured_concurrent_per_process * chat_rest_healthy_processes * (1 - metrics.capacity_buffer)),
            "total_queue_capacity": int(metrics.configured_concurrent_per_process * chat_rest_healthy_processes * metrics.queue_depth_multiplier),
            "total_system_capacity": thresholds["total_capacity"],
            "theoretical_system_hourly": int((metrics.configured_concurrent_per_process * chat_rest_healthy_processes * 3600) / metrics.configured_avg_processing_time)
        },
        "current_thresholds": thresholds,
        "threshold_breakdown": {
            "anonymous_blocks_at": thresholds["anonymous_threshold"],
            "registered_blocks_at": thresholds["registered_threshold"],
            "hard_limit_at": thresholds["hard_limit"],
            "anonymous_percentage": round(metrics.anonymous_threshold_ratio * 100, 1),
            "registered_percentage": round(metrics.registered_threshold_ratio * 100, 1),
            "hard_limit_percentage": round(metrics.hard_limit_threshold_ratio * 100, 1)
        },
        "capacity_warnings": self._generate_capacity_warnings(metrics, actual_processes_data)
    }

    def _generate_capacity_warnings(self, metrics: DynamicCapacityMetrics,
                                    actual_processes_data: Dict[str, List[ActualProcessInfo]]) -> List[str]:
        """Generate warnings about capacity issues"""
        warnings = []

        # Check for process deficits
        if metrics.actual_healthy_processes_per_instance < metrics.configured_processes_per_instance:
            deficit = metrics.configured_processes_per_instance - metrics.actual_healthy_processes_per_instance
            warnings.append(f"Process deficit: {deficit} processes missing or unhealthy")

        # Check for low process health ratio
        if metrics.process_health_ratio < 0.8:
            warnings.append(f"Low process health: only {metrics.process_health_ratio:.1%} of processes are healthy")

        # Check for instances with no processes
        for instance_id, processes in actual_processes_data.items():
            chat_processes = [p for p in processes if p.service_type == "chat" and p.service_name == "rest"]
            if not chat_processes or all(p.healthy_processes == 0 for p in chat_processes):
                warnings.append(f"Instance {instance_id} has no healthy chat processes")

        return warnings

    def refresh_cache(self):
        """Force refresh of cached metrics"""
        self._cached_metrics = None
        self._last_update = 0


# Update ServiceCapacity and CapacityBasedBackpressureConfig to use calculator
@dataclass
class ServiceCapacity:
    """Simplified service capacity that delegates to calculator"""
    concurrent_requests_per_process: int = 5
    avg_processing_time_seconds: float = 25.0
    processes_per_instance: int = None

    def __post_init__(self):
        if self.processes_per_instance is None:
            self.processes_per_instance = int(os.getenv("CHAT_APP_PARALLELISM", "1"))

    @property
    def concurrent_requests_per_instance(self) -> int:
        return self.concurrent_requests_per_process * self.processes_per_instance

    @property
    def requests_per_hour_per_instance(self) -> int:
        return int((self.concurrent_requests_per_instance * 3600) / self.avg_processing_time_seconds)

@dataclass
class CapacityBasedBackpressureConfig:
    """Simplified backpressure config that uses capacity calculator"""
    service_capacity: ServiceCapacity
    capacity_buffer: float = 0.2
    queue_depth_multiplier: float = 2.0
    anonymous_pressure_threshold: float = 0.6
    registered_pressure_threshold: float = 0.8
    hard_limit_threshold: float = 0.95

    def __post_init__(self):
        # Create calculator for this config
        from kdcube_ai_app.infra.gateway.config import GatewayConfiguration
        # We'll inject the calculator from the gateway
        self._calculator = None

    def set_calculator(self, calculator: DynamicCapacityCalculator):
        """Inject calculator (called by gateway)"""
        self._calculator = calculator

    def get_base_queue_size_per_instance(self) -> int:
        """Delegate to calculator"""
        if self._calculator:
            return self._calculator.get_base_queue_size_per_instance()

        # Fallback calculation (for compatibility)
        processing_capacity = self.service_capacity.concurrent_requests_per_instance
        effective_capacity = int(processing_capacity * (1 - self.capacity_buffer))
        queue_capacity = int(processing_capacity * self.queue_depth_multiplier)
        return effective_capacity + queue_capacity

    def get_capacity_thresholds(self, instance_count: int) -> Dict[str, int]:
        """Delegate to calculator"""
        if self._calculator:
            return self._calculator.get_capacity_thresholds(instance_count)

        # Fallback calculation
        base_capacity = self.get_base_queue_size_per_instance()
        total_capacity = base_capacity * instance_count
        return {
            "anonymous_threshold": int(total_capacity * self.anonymous_pressure_threshold),
            "registered_threshold": int(total_capacity * self.registered_pressure_threshold),
            "hard_limit": int(total_capacity * self.hard_limit_threshold),
            "total_capacity": total_capacity
        }

def get_config_for_chat_service():
    """Configuration for AI chat service with proper multi-process calculation"""
    return ServiceCapacity(
        concurrent_requests_per_process=int(os.getenv("MAX_CONCURRENT_CHAT", "5")),
        avg_processing_time_seconds=float(os.getenv("AVG_PROCESSING_TIME_SECONDS", "25.0")),
        processes_per_instance=int(os.getenv("CHAT_APP_PARALLELISM", "1"))
    )

def get_config_for_api_service():
    """Configuration for fast API service"""
    return ServiceCapacity(
        concurrent_requests_per_process=50,     # 50 simultaneous API calls
        avg_processing_time_seconds=2.0,    # 2 seconds average response time
        # This gives ~90,000 requests/hour per instance
    )

def get_config_for_heavy_processing():
    """Configuration for heavy processing service"""
    return ServiceCapacity(
        concurrent_requests_per_process=3,      # Only 3 simultaneous heavy jobs
        avg_processing_time_seconds=120.0,  # 2 minutes average processing time
        # This gives ~90 requests/hour per instance
    )
