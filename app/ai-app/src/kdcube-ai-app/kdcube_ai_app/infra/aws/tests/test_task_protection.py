import io
import asyncio
import logging
import json
import os
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


def test_ecs_task_scale_in_protection_reconcile_disables_stale_idle_state(tmp_path, monkeypatch):
    protection = EcsTaskScaleInProtection(
        logger_=logging.getLogger("test"),
        agent_uri="http://127.0.0.1:51678",
        lock_path=tmp_path / "ecs-task-protection.lock",
        state_path=tmp_path / "ecs-task-protection.json",
        expires_minutes=15,
        task_timeout_sec=600,
    )

    protection._state_path.write_text(json.dumps({"claims": {}, "protection_enabled": True}))
    calls = []
    monkeypatch.setattr(protection, "_set_protection", lambda enabled: calls.append(enabled))

    asyncio.run(protection.reconcile(label="startup", force=True))

    assert calls == [False]
    state = json.loads(protection._state_path.read_text())
    assert state["claims"] == {}
    assert state["protection_enabled"] is False


def test_ecs_task_scale_in_protection_reconcile_sweeps_dead_claims_and_disables(tmp_path, monkeypatch):
    protection = EcsTaskScaleInProtection(
        logger_=logging.getLogger("test"),
        agent_uri="http://127.0.0.1:51678",
        lock_path=tmp_path / "ecs-task-protection.lock",
        state_path=tmp_path / "ecs-task-protection.json",
        expires_minutes=15,
        task_timeout_sec=600,
    )

    protection._state_path.write_text(
        json.dumps({"claims": {"999999": 1}, "protection_enabled": True})
    )
    monkeypatch.setattr(protection, "_pid_alive", lambda pid: False)
    calls = []
    monkeypatch.setattr(protection, "_set_protection", lambda enabled: calls.append(enabled))

    asyncio.run(protection.reconcile(label="periodic", force=False))

    assert calls == [False]
    state = json.loads(protection._state_path.read_text())
    assert state["claims"] == {}
    assert state["protection_enabled"] is False


def test_ecs_task_scale_in_protection_reconcile_refreshes_busy_claims_when_sync_is_old(tmp_path, monkeypatch):
    protection = EcsTaskScaleInProtection(
        logger_=logging.getLogger("test"),
        agent_uri="http://127.0.0.1:51678",
        lock_path=tmp_path / "ecs-task-protection.lock",
        state_path=tmp_path / "ecs-task-protection.json",
        expires_minutes=15,
        task_timeout_sec=600,
    )

    protection._state_path.write_text(
        json.dumps(
            {
                "claims": {str(os.getpid()): 1},
                "protection_enabled": True,
                "last_protection_sync_at": 0,
            }
        )
    )
    calls = []
    monkeypatch.setattr(protection, "_set_protection", lambda enabled: calls.append(enabled))

    asyncio.run(protection.reconcile(label="periodic", force=False))

    assert calls == [True]
    state = json.loads(protection._state_path.read_text())
    assert state["claims"][str(os.getpid())] == 1
    assert state["protection_enabled"] is True


def test_ecs_task_scale_in_protection_prefers_max_wall_time_env_for_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_TASK_TIMEOUT_SEC", "600")
    monkeypatch.setenv("CHAT_TASK_MAX_WALL_TIME_SEC", "3600")

    protection = EcsTaskScaleInProtection(
        logger_=logging.getLogger("test"),
        agent_uri="http://127.0.0.1:51678",
        lock_path=tmp_path / "ecs-task-protection.lock",
        state_path=tmp_path / "ecs-task-protection.json",
    )

    assert protection._expires_minutes == 65
