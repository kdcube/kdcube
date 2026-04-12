import logging

import pytest

from kdcube_ai_app.infra.aws.ecs_container_instance_drain import (
    EcsContainerInstanceDrainDetector,
    NoopEcsContainerInstanceDrainDetector,
    build_ecs_container_instance_drain_detector,
)


def test_build_ecs_container_instance_drain_detector_returns_noop_without_metadata_uri(monkeypatch):
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI", raising=False)

    detector = build_ecs_container_instance_drain_detector(logger_=logging.getLogger("test"))

    assert isinstance(detector, NoopEcsContainerInstanceDrainDetector)
    assert detector.enabled is False


@pytest.mark.asyncio
async def test_ecs_container_instance_drain_detector_reports_draining(monkeypatch):
    detector = EcsContainerInstanceDrainDetector(
        logger_=logging.getLogger("test"),
        metadata_base_url="http://127.0.0.1:51679/v4",
    )

    async def _fetch_task_metadata():
        return {
            "Cluster": "cluster-a",
            "TaskARN": "arn:aws:ecs:eu-west-1:123456789012:task/cluster-a/task-123",
        }

    async def _describe_current_task(*, cluster: str, task_arn: str):
        assert cluster == "cluster-a"
        assert task_arn.endswith("task-123")
        return {"containerInstanceArn": "arn:aws:ecs:eu-west-1:123456789012:container-instance/abc"}

    async def _describe_container_instance(*, cluster: str, container_instance_arn: str):
        assert cluster == "cluster-a"
        assert container_instance_arn.endswith("/abc")
        return {"status": "DRAINING"}

    monkeypatch.setattr(detector, "_fetch_task_metadata", _fetch_task_metadata)
    monkeypatch.setattr(detector, "_describe_current_task", _describe_current_task)
    monkeypatch.setattr(detector, "_describe_container_instance", _describe_container_instance)

    assert await detector.is_host_draining() is True
    snapshot = detector.snapshot()
    assert snapshot["enabled"] is True
    assert snapshot["cluster"] == "cluster-a"
    assert snapshot["container_instance_arn"].endswith("/abc")
    assert snapshot["container_instance_status"] == "DRAINING"


@pytest.mark.asyncio
async def test_ecs_container_instance_drain_detector_disables_without_container_instance(monkeypatch):
    detector = EcsContainerInstanceDrainDetector(
        logger_=logging.getLogger("test"),
        metadata_base_url="http://127.0.0.1:51679/v4",
    )

    async def _fetch_task_metadata():
        return {
            "Cluster": "cluster-a",
            "TaskARN": "arn:aws:ecs:eu-west-1:123456789012:task/cluster-a/task-123",
        }

    async def _describe_current_task(*, cluster: str, task_arn: str):
        del cluster, task_arn
        return {}

    monkeypatch.setattr(detector, "_fetch_task_metadata", _fetch_task_metadata)
    monkeypatch.setattr(detector, "_describe_current_task", _describe_current_task)

    assert await detector.is_host_draining() is False
    assert detector.enabled is False
