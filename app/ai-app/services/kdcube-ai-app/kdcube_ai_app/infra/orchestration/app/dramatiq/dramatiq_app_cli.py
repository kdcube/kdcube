# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# infra/orchestration/app/dramatiq/dramatiq_app_cli.py
"""
Dramatiq + availability integration
"""
import os
import logging
import asyncio
import threading
from dotenv import load_dotenv, find_dotenv

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AgeLimit, TimeLimit, Retries, Callbacks, AsyncIO, Middleware
from dramatiq.results import Results
from dramatiq.results.backends import RedisBackend

from kdcube_ai_app.infra.orchestration.app.dramatiq.resolver import INSTANCE_ID, REDIS_URL, HEARTBEAT_INTERVAL, \
    TENANT_ID, PROJECT_ID

# Load environment
load_dotenv(find_dotenv())

# Logging setup
import kdcube_ai_app.apps.utils.logging_config as logging_config
logging_config.configure_logging()
logger = logging.getLogger("Orch.Dramatiq")


class HeartbeatMiddleware(Middleware):
    """Dramatiq middleware that tracks job start/end for heartbeat"""

    def __init__(self):
        self.heartbeat_manager = None
        self.active_jobs = 0
        self.lock = threading.Lock()
        self._initialized = False

    def set_heartbeat_manager(self, heartbeat_manager):
        """Set the heartbeat manager reference"""
        self.heartbeat_manager = heartbeat_manager
        self._initialized = True
        logger.info(f"HeartbeatMiddleware connected to manager for PID {os.getpid()}")

    def before_process_message(self, broker, message):
        """Called before a message is processed"""
        with self.lock:
            self.active_jobs += 1
            if self.heartbeat_manager:
                self.heartbeat_manager.set_load(self.active_jobs)
                logger.debug(f"Job started, active jobs: {self.active_jobs}")

    def after_process_message(self, broker, message, *, result=None, exception=None):
        """Called after a message is processed (success or failure)"""
        with self.lock:
            self.active_jobs = max(0, self.active_jobs - 1)
            if self.heartbeat_manager:
                self.heartbeat_manager.set_load(self.active_jobs)

        status = "success" if exception is None else "error"
        logger.debug(f"Job {status}, active jobs: {self.active_jobs}")

    def get_current_load(self):
        """Get current job load"""
        with self.lock:
            return self.active_jobs

# Global middleware instance
heartbeat_middleware = HeartbeatMiddleware()

def setup_worker_broker():
    """Configure your RedisBroker and attach your middleware exactly once."""
    broker = RedisBroker(url=REDIS_URL)

    # Clean up existing middleware
    from dramatiq.middleware.prometheus import Prometheus
    broker.middleware[:] = [
        mw for mw in broker.middleware
        if not isinstance(mw, Prometheus)
    ]

    existing = {type(mw).__name__ for mw in broker.middleware}
    if "AgeLimit" not in existing:
        broker.add_middleware(AgeLimit())
    if "TimeLimit" not in existing:
        broker.add_middleware(TimeLimit())
    if "Retries" not in existing:
        broker.add_middleware(Retries(max_retries=3))
    if "Results" not in existing:
        backend = RedisBackend(url=REDIS_URL)
        broker.add_middleware(Results(backend=backend))
    if "Callbacks" not in existing:
        broker.add_middleware(Callbacks())
    if "AsyncIO" not in existing:
        broker.add_middleware(AsyncIO())

    # Add our heartbeat middleware
    if "HeartbeatMiddleware" not in existing:
        broker.add_middleware(heartbeat_middleware)
        logger.info("Added HeartbeatMiddleware to broker")

    dramatiq.set_broker(broker)
    return broker

class WorkerHeartbeatManager:
    """Manages heartbeat for Dramatiq worker in separate thread"""

    def __init__(self, heartbeat_manager, middleware_ref):
        self.heartbeat_manager = heartbeat_manager
        self.middleware_ref = middleware_ref
        self.running = False
        self.thread = None

    def start(self):
        """Start heartbeat thread"""
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.thread.start()
        logger.info(f"Started heartbeat thread for worker PID {os.getpid()}")

    def stop(self):
        """Stop heartbeat thread"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

    def _heartbeat_loop(self):
        """Heartbeat loop that runs in separate thread"""
        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._async_heartbeat_loop())
        except Exception as e:
            logger.error(f"Heartbeat loop error: {e}")
        finally:
            loop.close()

    async def _async_heartbeat_loop(self):
        """Async heartbeat loop"""
        from kdcube_ai_app.infra.availability.health_and_heartbeat import MultiprocessDistributedMiddleware

        # Initialize Redis connection in this thread
        middleware = MultiprocessDistributedMiddleware(REDIS_URL, instance_id=INSTANCE_ID, tenant=TENANT_ID, project=PROJECT_ID)
        await middleware.init_redis()
        await self.heartbeat_manager.start_heartbeat(interval=HEARTBEAT_INTERVAL)

        logger.info(f"Dramatiq worker heartbeat started for PID {os.getpid()}")

        while self.running:
            try:
                # Update load from middleware
                current_load = self.middleware_ref.get_current_load()
                self.heartbeat_manager.set_load(current_load)

                await asyncio.sleep(10)  # Heartbeat every 10 seconds

            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                await asyncio.sleep(10)

def worker_main(worker_id: int):

    import os, signal
    from dramatiq.worker import Worker

    worker_logger = logging.getLogger(f"Worker-{worker_id}")
    worker_logger.info(f"Starting Dramatiq worker {worker_id}, PID: {os.getpid()}")

    # Import availability middleware
    from kdcube_ai_app.infra.availability.health_and_heartbeat import MultiprocessDistributedMiddleware, ProcessHeartbeatManager

    # Create heartbeat manager
    middleware = MultiprocessDistributedMiddleware(REDIS_URL, instance_id=INSTANCE_ID, tenant=TENANT_ID, project=PROJECT_ID)
    pid = os.getpid()

    # Use "chat" service type since these are orchestrator workers for chat processing
    heartbeat_manager = ProcessHeartbeatManager(
        middleware,
        "chat",  # Changed from "kb" to "chat" since these are chat orchestrator workers
        "orchestrator",
        pid
    )

    # Set metadata to identify as Dramatiq worker
    heartbeat_manager.set_metadata(
        worker_type="dramatiq",
        worker_id=worker_id,
        queues=["kdcube_orch_low_priority", "health_check"]
    )

    # Connect heartbeat manager to the dramatiq middleware
    heartbeat_middleware.set_heartbeat_manager(heartbeat_manager)

    # # Setup broker with middleware
    # broker = setup_worker_broker()

    # Start dedicated heartbeat manager
    worker_heartbeat = WorkerHeartbeatManager(heartbeat_manager, heartbeat_middleware)
    worker_heartbeat.start()

    # Setup broker with middleware
    broker = setup_worker_broker()

    # Import your actors
    import kdcube_ai_app.apps.orchestration.tasks.kb_actors

    # Declare queues
    queues = ["kdcube_orch_low_priority", "health_check"]
    for q in queues:
        broker.declare_queue(q)

    # Create and start worker
    threads = int(os.environ.get("DRAMATIQ_THREADS", 1))
    worker = Worker(broker, worker_threads=threads, queues=queues)

    # Signal handlers
    def _shutdown(signum, frame):
        worker_logger.info(f"Received signal {signum}, shutting down worker {worker_id}")
        worker_heartbeat.stop()
        worker.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Start worker
    worker.start()
    worker_logger.info(f"Worker {worker_id} actors: {list(broker.actors.keys())}")

    # Wait for worker threads
    try:
        for t in worker.workers:
            t.join()
    finally:
        worker_heartbeat.stop()
        worker_logger.info(f"Worker {worker_id} stopped")

def start_multiprocess_workers():
    import multiprocessing

    procs = []
    n = int(os.environ.get("DRAMATIQ_PROCESSES", multiprocessing.cpu_count()))

    logger.info(f"Starting {n} Dramatiq worker processes")

    for i in range(n):
        p = multiprocessing.Process(target=worker_main, args=(i,))
        p.start()
        procs.append(p)
        logger.info(f"Started worker process {i} with PID {p.pid}")

    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        logger.info("Shutting down all worker processes...")
        for p in procs:
            p.terminate()
        for p in procs:
            p.join()

if __name__ == "__main__":
    start_multiprocess_workers()