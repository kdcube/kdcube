import io
import asyncio
import logging
import urllib.error

import pytest

from kdcube_ai_app.infra.aws.task_protection import (
    EcsTaskScaleInProtection,
    NoopTaskScaleInProtection,
    build_task_scale_in_protection,
)


def test_build_task_scale_in_protection_returns_noop_without_ecs_agent_uri(monkeypatch):
    monkeypatch.delenv("ECS_AGENT_URI", raising=False)

    protection = build_task_scale_in_protection(logger_=logging.getLogger("test"))

    assert isinstance(protection, NoopTaskScaleInProtection)
    assert protection.enabled is False
    asyncio.run(_exercise_hold(protection))


async def _exercise_hold(protection):
    async with protection.hold(label="test"):
        return None


def test_ecs_task_scale_in_protection_toggles_on_first_and_last_claim(tmp_path, monkeypatch):
    protection = EcsTaskScaleInProtection(
        logger_=logging.getLogger("test"),
        agent_uri="http://127.0.0.1:51678",
        lock_path=tmp_path / "ecs-task-protection.lock",
        state_path=tmp_path / "ecs-task-protection.json",
        expires_minutes=15,
        task_timeout_sec=600,
    )

    calls = []
    monkeypatch.setattr(protection, "_set_protection", lambda enabled: calls.append(enabled))

    protection._acquire("task-a")
    protection._acquire("task-b")
    protection._release("task-b")
    protection._release("task-a")

    assert calls == [True, False]


def test_ecs_task_scale_in_protection_surfaces_http_error_details(tmp_path, monkeypatch):
    protection = EcsTaskScaleInProtection(
        logger_=logging.getLogger("test"),
        agent_uri="http://127.0.0.1:51678",
        lock_path=tmp_path / "ecs-task-protection.lock",
        state_path=tmp_path / "ecs-task-protection.json",
        expires_minutes=15,
        task_timeout_sec=600,
    )

    payload = (
        '{"error":{"Code":"AccessDeniedException","Arn":"arn:aws:ecs:eu-west-1:123456789012:task/test/abc",'
        '"Message":"missing ecs:UpdateTaskProtection permission"},"requestID":"req-123"}'
    ).encode("utf-8")

    def _raise_http_error(*args, **kwargs):
        raise urllib.error.HTTPError(
            protection._endpoint,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(payload),
        )

    monkeypatch.setattr("urllib.request.urlopen", _raise_http_error)

    with pytest.raises(RuntimeError) as exc_info:
        protection._set_protection(True)
    message = str(exc_info.value)
    assert "HTTP 400: Bad Request" in message
    assert "AccessDeniedException" in message
    assert "missing ecs:UpdateTaskProtection permission" in message
