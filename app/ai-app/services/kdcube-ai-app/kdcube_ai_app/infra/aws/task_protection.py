from __future__ import annotations

import asyncio
import errno
import json
import math
import os
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import fcntl


class NoopTaskScaleInProtection:
    @property
    def enabled(self) -> bool:
        return False

    @asynccontextmanager
    async def hold(self, *, label: str):
        del label
        yield


class EcsTaskScaleInProtection:
    """
    Best-effort ECS task scale-in protection for busy proc tasks.

    Protection is task-wide, so multiple worker processes in the same ECS task
    coordinate through a small shared state file. Dead process claims are swept
    opportunistically on each acquire/release so a crashed worker does not pin
    task protection forever.
    """

    def __init__(
        self,
        *,
        logger_,
        agent_uri: Optional[str] = None,
        lock_path: Optional[Path | str] = None,
        state_path: Optional[Path | str] = None,
        request_timeout_sec: Optional[float] = None,
        expires_minutes: Optional[int] = None,
        task_timeout_sec: Optional[int] = None,
    ):
        self._logger = logger_
        raw_agent_uri = agent_uri if agent_uri is not None else os.getenv("ECS_AGENT_URI")
        self._agent_uri = (raw_agent_uri or "").rstrip("/")
        self._endpoint = f"{self._agent_uri}/task-protection/v1/state" if self._agent_uri else ""
        self._enabled = bool(self._endpoint)
        self._lock_path = Path(
            lock_path or os.getenv("ECS_TASK_PROTECTION_LOCK_PATH", "/tmp/ecs-task-protection.lock")
        )
        self._state_path = Path(
            state_path or os.getenv("ECS_TASK_PROTECTION_STATE_PATH", "/tmp/ecs-task-protection.json")
        )
        self._request_timeout_sec = max(
            1.0,
            float(request_timeout_sec or os.getenv("ECS_TASK_PROTECTION_REQUEST_TIMEOUT_SEC", "5")),
        )
        effective_task_timeout_sec = max(
            60,
            int(task_timeout_sec or os.getenv("CHAT_TASK_TIMEOUT_SEC", "600")),
        )
        default_expires = max(5, min(120, math.ceil(effective_task_timeout_sec / 60) + 5))
        self._expires_minutes = max(
            1,
            min(
                2880,
                int(expires_minutes or os.getenv("ECS_TASK_PROTECTION_EXPIRES_MINUTES", str(default_expires))),
            ),
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return False
            return True
        return True

    def _load_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"claims": {}}
        try:
            raw = json.loads(self._state_path.read_text())
        except Exception:
            self._logger.warning("Failed to read ECS task protection state; resetting", exc_info=True)
            return {"claims": {}}
        claims = raw.get("claims")
        if not isinstance(claims, dict):
            claims = {}
        norm_claims: dict[str, int] = {}
        for pid, count in claims.items():
            try:
                pid_i = int(pid)
                count_i = int(count)
            except Exception:
                continue
            if pid_i > 0 and count_i > 0:
                norm_claims[str(pid_i)] = count_i
        return {"claims": norm_claims}

    def _save_state(self, state: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._state_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, sort_keys=True))
        tmp_path.replace(self._state_path)

    def _sweep_claims(self, state: dict[str, Any]) -> None:
        claims = state.setdefault("claims", {})
        stale = [pid for pid in list(claims.keys()) if not self._pid_alive(int(pid))]
        for pid in stale:
            claims.pop(pid, None)

    def _active_count(self, state: dict[str, Any]) -> int:
        return sum(max(0, int(v)) for v in (state.get("claims") or {}).values())

    def _format_http_error(self, exc: urllib.error.HTTPError) -> str:
        body_text = ""
        try:
            raw_body = exc.read()
            if raw_body:
                body_text = raw_body.decode("utf-8", errors="ignore").strip()
        except Exception:
            body_text = ""

        detail = body_text
        if body_text:
            try:
                payload = json.loads(body_text)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                failure = payload.get("failure")
                error = payload.get("error")
                if isinstance(failure, dict):
                    reason = failure.get("Reason")
                    arn = failure.get("Arn")
                    extra = failure.get("Detail")
                    parts = [part for part in (f"Reason={reason}" if reason else None, f"Arn={arn}" if arn else None, f"Detail={extra}" if extra else None) if part]
                    detail = ", ".join(parts) or body_text
                elif isinstance(error, dict):
                    code = error.get("Code")
                    arn = error.get("Arn")
                    message = error.get("Message")
                    request_id = payload.get("requestID")
                    parts = [part for part in (f"Code={code}" if code else None, f"Arn={arn}" if arn else None, f"RequestID={request_id}" if request_id else None, f"Message={message}" if message else None) if part]
                    detail = ", ".join(parts) or body_text

        if detail:
            return f"HTTP {exc.code}: {exc.reason}; {detail}"
        return f"HTTP {exc.code}: {exc.reason}"

    def _set_protection(self, enabled: bool) -> None:
        if not self._enabled:
            return
        body = {"ProtectionEnabled": enabled}
        if enabled:
            body["ExpiresInMinutes"] = self._expires_minutes
        req = urllib.request.Request(
            self._endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._request_timeout_sec) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            detail = self._format_http_error(exc)
            raise RuntimeError(f"ECS task protection API request failed: {detail}") from exc

    def _acquire(self, label: str) -> None:
        if not self._enabled:
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+") as lockf:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
            state = self._load_state()
            self._sweep_claims(state)
            active_before = self._active_count(state)
            pid = str(os.getpid())
            state.setdefault("claims", {})[pid] = int(state["claims"].get(pid, 0)) + 1
            if active_before == 0:
                try:
                    self._set_protection(True)
                    self._logger.info(
                        "Enabled ECS task scale-in protection for busy proc task: label=%s expires_minutes=%s",
                        label,
                        self._expires_minutes,
                    )
                except Exception:
                    self._logger.warning(
                        "Failed to enable ECS task scale-in protection for label=%s",
                        label,
                        exc_info=True,
                    )
            self._save_state(state)

    def _release(self, label: str) -> None:
        if not self._enabled:
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+") as lockf:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
            state = self._load_state()
            self._sweep_claims(state)
            claims = state.setdefault("claims", {})
            pid = str(os.getpid())
            current = int(claims.get(pid, 0))
            if current <= 1:
                claims.pop(pid, None)
            else:
                claims[pid] = current - 1
            active_after = self._active_count(state)
            if active_after == 0:
                try:
                    self._set_protection(False)
                    self._logger.info(
                        "Disabled ECS task scale-in protection after proc became idle: label=%s",
                        label,
                    )
                except Exception:
                    self._logger.warning(
                        "Failed to disable ECS task scale-in protection for label=%s",
                        label,
                        exc_info=True,
                    )
            self._save_state(state)

    @asynccontextmanager
    async def hold(self, *, label: str):
        if not self._enabled:
            yield
            return
        await asyncio.to_thread(self._acquire, label)
        try:
            yield
        finally:
            await asyncio.to_thread(self._release, label)


def build_task_scale_in_protection(*, logger_):
    if os.getenv("ECS_AGENT_URI"):
        return EcsTaskScaleInProtection(logger_=logger_)
    return NoopTaskScaleInProtection()
