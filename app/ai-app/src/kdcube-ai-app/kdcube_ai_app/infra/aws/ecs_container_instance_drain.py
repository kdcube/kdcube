from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional


class NoopEcsContainerInstanceDrainDetector:
    @property
    def enabled(self) -> bool:
        return False

    async def is_host_draining(self) -> bool:
        return False

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": False,
            "last_checked_at": None,
            "cluster": None,
            "task_arn": None,
            "container_instance_arn": None,
            "container_instance_status": None,
            "last_error": None,
        }


class EcsContainerInstanceDrainDetector:
    """
    Detect whether the current ECS container instance has entered DRAINING.

    This is relevant only for ECS/EC2 tasks where a long-lived proc worker can
    continue to pull queue work even after the underlying container instance has
    started draining. Fargate tasks do not expose a container instance ARN, so
    the detector disables itself there.
    """

    def __init__(
        self,
        *,
        logger_,
        metadata_base_url: Optional[str] = None,
        aws_region: Optional[str] = None,
        request_timeout_sec: Optional[float] = None,
    ):
        self._logger = logger_
        self._metadata_base_url = (
            metadata_base_url
            or os.getenv("ECS_CONTAINER_METADATA_URI_V4")
            or os.getenv("ECS_CONTAINER_METADATA_URI")
            or ""
        ).rstrip("/")
        self._aws_region = (
            aws_region
            or os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or None
        )
        self._request_timeout_sec = max(
            0.5,
            float(
                request_timeout_sec
                or os.getenv("ECS_CONTAINER_INSTANCE_DRAIN_REQUEST_TIMEOUT_SEC", "2")
            ),
        )
        self._enabled = bool(self._metadata_base_url)
        self._cluster: Optional[str] = None
        self._task_arn: Optional[str] = None
        self._container_instance_arn: Optional[str] = None
        self._container_instance_status: Optional[str] = None
        self._last_checked_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_logged_error: Optional[str] = None
        self._ecs_client = None
        self._disabled_reason_logged = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _log_disable(self, message: str) -> None:
        if self._disabled_reason_logged:
            return
        self._disabled_reason_logged = True
        self._logger.info(message)

    def _fetch_task_metadata_sync(self) -> dict[str, Any]:
        url = f"{self._metadata_base_url}/task"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=self._request_timeout_sec) as resp:
            raw = resp.read()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected ECS task metadata payload")
        return payload

    async def _fetch_task_metadata(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_task_metadata_sync)

    def _get_ecs_client(self):
        if self._ecs_client is not None:
            return self._ecs_client
        try:
            import boto3  # type: ignore
        except Exception as exc:
            raise RuntimeError("boto3 is not available for ECS drain detection") from exc
        self._ecs_client = (
            boto3.client("ecs", region_name=self._aws_region)
            if self._aws_region
            else boto3.client("ecs")
        )
        return self._ecs_client

    def _describe_current_task_sync(self, *, cluster: str, task_arn: str) -> dict[str, Any]:
        resp = self._get_ecs_client().describe_tasks(cluster=cluster, tasks=[task_arn])
        tasks = resp.get("tasks") or []
        if not tasks:
            raise RuntimeError("ECS DescribeTasks returned no tasks for current task")
        task = tasks[0]
        if not isinstance(task, dict):
            raise RuntimeError("Unexpected ECS task description payload")
        return task

    async def _describe_current_task(self, *, cluster: str, task_arn: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._describe_current_task_sync,
            cluster=cluster,
            task_arn=task_arn,
        )

    def _describe_container_instance_sync(
        self,
        *,
        cluster: str,
        container_instance_arn: str,
    ) -> dict[str, Any]:
        resp = self._get_ecs_client().describe_container_instances(
            cluster=cluster,
            containerInstances=[container_instance_arn],
        )
        instances = resp.get("containerInstances") or []
        if not instances:
            raise RuntimeError("ECS DescribeContainerInstances returned no instances")
        instance = instances[0]
        if not isinstance(instance, dict):
            raise RuntimeError("Unexpected ECS container instance description payload")
        return instance

    async def _describe_container_instance(
        self,
        *,
        cluster: str,
        container_instance_arn: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._describe_container_instance_sync,
            cluster=cluster,
            container_instance_arn=container_instance_arn,
        )

    async def _resolve_current_task_identity(self) -> None:
        if self._cluster and self._task_arn:
            return
        payload = await self._fetch_task_metadata()
        cluster = payload.get("Cluster")
        task_arn = payload.get("TaskARN")
        if not cluster or not task_arn:
            raise RuntimeError("ECS task metadata did not include Cluster/TaskARN")
        self._cluster = str(cluster)
        self._task_arn = str(task_arn)

    async def _resolve_container_instance_arn(self) -> Optional[str]:
        if self._container_instance_arn:
            return self._container_instance_arn
        await self._resolve_current_task_identity()
        task = await self._describe_current_task(cluster=self._cluster, task_arn=self._task_arn)
        container_instance_arn = task.get("containerInstanceArn")
        if not container_instance_arn:
            self._enabled = False
            self._log_disable(
                "ECS host-drain watcher disabled: current task has no containerInstanceArn"
            )
            return None
        self._container_instance_arn = str(container_instance_arn)
        return self._container_instance_arn

    async def is_host_draining(self) -> bool:
        if not self._enabled:
            return False
        try:
            container_instance_arn = await self._resolve_container_instance_arn()
            if not container_instance_arn:
                return False
            instance = await self._describe_container_instance(
                cluster=self._cluster,
                container_instance_arn=container_instance_arn,
            )
            status = str(instance.get("status") or "").upper() or None
            self._container_instance_status = status
            self._last_checked_at = datetime.now(timezone.utc).isoformat()
            self._last_error = None
            self._last_logged_error = None
            return status == "DRAINING"
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            if self._last_error != self._last_logged_error:
                self._last_logged_error = self._last_error
                self._logger.warning("Failed to check ECS container instance drain status", exc_info=True)
            return False

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "cluster": self._cluster,
            "task_arn": self._task_arn,
            "container_instance_arn": self._container_instance_arn,
            "container_instance_status": self._container_instance_status,
            "last_checked_at": self._last_checked_at,
            "last_error": self._last_error,
        }


def build_ecs_container_instance_drain_detector(*, logger_):
    metadata_base_url = (
        os.getenv("ECS_CONTAINER_METADATA_URI_V4")
        or os.getenv("ECS_CONTAINER_METADATA_URI")
        or ""
    ).strip()
    if not metadata_base_url:
        return NoopEcsContainerInstanceDrainDetector()
    return EcsContainerInstanceDrainDetector(logger_=logger_, metadata_base_url=metadata_base_url)
