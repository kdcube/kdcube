# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Mapping


class ApplicationReadinessMode(str, Enum):
    INDEPENDENT = "independent"
    REQUIRED = "required"


class ApplicationLifecycleState(str, Enum):
    PENDING = "pending"
    PREPARING = "preparing"
    RETRYING = "retrying"
    READY = "ready"
    FAILED = "failed"
    DEPROVISIONING = "deprovisioning"
    DEPROVISION_FAILED = "deprovision_failed"


def normalize_readiness_mode(value: object) -> ApplicationReadinessMode:
    if isinstance(value, ApplicationReadinessMode):
        return value
    normalized = str(value or ApplicationReadinessMode.INDEPENDENT.value).strip().lower()
    try:
        return ApplicationReadinessMode(normalized)
    except ValueError as exc:
        raise ValueError(
            "Application service.readiness must be 'independent' or 'required'"
        ) from exc


@dataclass(frozen=True)
class DesiredApplicationState:
    generation: str
    readiness: ApplicationReadinessMode = ApplicationReadinessMode.INDEPENDENT


@dataclass(frozen=True)
class ApplicationReadinessSnapshot:
    tenant: str
    project: str
    application_id: str
    readiness: ApplicationReadinessMode
    state: ApplicationLifecycleState
    desired_generation: str
    ready_generation: str | None
    attempt: int
    error_code: str | None
    error_message: str | None
    retry_at: str | None
    started_at: str | None
    finished_at: str | None
    updated_at: str

    @property
    def ready(self) -> bool:
        return (
            self.state is ApplicationLifecycleState.READY
            and self.ready_generation == self.desired_generation
        )

    def public_unavailable_payload(self) -> dict[str, object]:
        state = self.state.value
        if self.state is ApplicationLifecycleState.PENDING:
            state = ApplicationLifecycleState.PREPARING.value
        deprovisioning = self.state in {
            ApplicationLifecycleState.DEPROVISIONING,
            ApplicationLifecycleState.DEPROVISION_FAILED,
        }
        return {
            "type": "application_deprovisioning" if deprovisioning else "application_not_ready",
            "application_id": self.application_id,
            "state": state,
            "retryable": not deprovisioning,
        }

    def diagnostic_payload(self) -> dict[str, object]:
        return {
            "application_id": self.application_id,
            "readiness": self.readiness.value,
            "state": self.state.value,
            "ready": self.ready,
            "desired_generation": self.desired_generation,
            "ready_generation": self.ready_generation,
            "attempt": self.attempt,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "retry_at": self.retry_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class AggregateApplicationReadiness:
    tenant: str
    project: str
    ready: bool
    required: tuple[ApplicationReadinessSnapshot, ...]
    blocking: tuple[ApplicationReadinessSnapshot, ...]

    def diagnostic_payload(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "required_applications": [item.application_id for item in self.required],
            "blocking_applications": [item.application_id for item in self.blocking],
        }


@dataclass
class _ApplicationReadinessRecord:
    tenant: str
    project: str
    application_id: str
    readiness: ApplicationReadinessMode
    state: ApplicationLifecycleState
    desired_generation: str
    ready_generation: str | None
    attempt: int
    error_code: str | None
    error_message: str | None
    retry_at: str | None
    started_at: str | None
    finished_at: str | None
    updated_at: str

    def snapshot(self) -> ApplicationReadinessSnapshot:
        return ApplicationReadinessSnapshot(**vars(self))


class ApplicationNotReadyError(RuntimeError):
    """A resolved application exists but its desired state is not ready."""

    def __init__(self, snapshot: ApplicationReadinessSnapshot) -> None:
        self.snapshot = snapshot
        super().__init__(
            f"Application {snapshot.application_id!r} is {snapshot.state.value} "
            f"for the requested runtime state"
        )

    @property
    def public_payload(self) -> dict[str, object]:
        return self.snapshot.public_unavailable_payload()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_error(value: object, *, limit: int = 1000) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 3] + "..."


class ApplicationReadinessRegistry:
    """Process-local desired/ready state used by preparation and admission."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._active_scopes: set[tuple[str, str]] = set()
        self._records: dict[tuple[str, str, str], _ApplicationReadinessRecord] = {}

    @staticmethod
    def _scope(tenant: str, project: str) -> tuple[str, str]:
        return str(tenant).strip(), str(project).strip()

    @classmethod
    def _key(cls, tenant: str, project: str, application_id: str) -> tuple[str, str, str]:
        scope = cls._scope(tenant, project)
        return scope[0], scope[1], str(application_id).strip()

    def replace_desired(
        self,
        *,
        tenant: str,
        project: str,
        applications: Mapping[str, DesiredApplicationState],
    ) -> dict[str, ApplicationReadinessSnapshot]:
        scope = self._scope(tenant, project)
        now = _utc_iso()
        desired_ids = {str(application_id).strip() for application_id in applications}
        with self._lock:
            self._active_scopes.add(scope)
            for key in tuple(self._records):
                if key[:2] == scope and key[2] not in desired_ids:
                    self._records.pop(key, None)

            for application_id, desired in applications.items():
                normalized_id = str(application_id).strip()
                generation = str(desired.generation or "").strip()
                if not normalized_id or not generation:
                    raise ValueError("Application id and desired generation must be non-empty")
                key = (*scope, normalized_id)
                readiness = normalize_readiness_mode(desired.readiness)
                current = self._records.get(key)
                if current is None:
                    self._records[key] = _ApplicationReadinessRecord(
                        tenant=scope[0],
                        project=scope[1],
                        application_id=normalized_id,
                        readiness=readiness,
                        state=ApplicationLifecycleState.PENDING,
                        desired_generation=generation,
                        ready_generation=None,
                        attempt=0,
                        error_code=None,
                        error_message=None,
                        retry_at=None,
                        started_at=None,
                        finished_at=None,
                        updated_at=now,
                    )
                    continue
                current.readiness = readiness
                if current.desired_generation != generation:
                    current.desired_generation = generation
                    current.state = ApplicationLifecycleState.PENDING
                    current.attempt = 0
                    current.error_code = None
                    current.error_message = None
                    current.retry_at = None
                    current.started_at = None
                    current.finished_at = None
                current.updated_at = now
            return self._scope_snapshots_locked(scope)

    def transition(
        self,
        *,
        tenant: str,
        project: str,
        application_id: str,
        generation: str,
        state: ApplicationLifecycleState,
        attempt: int | None = None,
        error: BaseException | str | None = None,
        retry_at: str | None = None,
    ) -> bool:
        key = self._key(tenant, project, application_id)
        now = _utc_iso()
        with self._lock:
            current = self._records.get(key)
            if current is None or current.desired_generation != generation:
                return False
            current.state = ApplicationLifecycleState(state)
            if attempt is not None:
                current.attempt = max(0, int(attempt))
            current.retry_at = retry_at
            current.updated_at = now
            if state is ApplicationLifecycleState.PREPARING:
                current.started_at = now
                current.finished_at = None
                current.error_code = None
                current.error_message = None
            elif state is ApplicationLifecycleState.PENDING:
                current.attempt = 0 if attempt is None else current.attempt
                current.retry_at = None
                current.started_at = None
                current.finished_at = None
                current.error_code = None
                current.error_message = None
            elif state is ApplicationLifecycleState.READY:
                current.ready_generation = generation
                current.finished_at = now
                current.error_code = None
                current.error_message = None
                current.retry_at = None
            elif state is ApplicationLifecycleState.DEPROVISIONING:
                current.started_at = now
                current.finished_at = None
                current.error_code = None
                current.error_message = None
                current.retry_at = None
            elif state in (
                ApplicationLifecycleState.FAILED,
                ApplicationLifecycleState.RETRYING,
                ApplicationLifecycleState.DEPROVISION_FAILED,
            ):
                current.finished_at = (
                    now
                    if state in {
                        ApplicationLifecycleState.FAILED,
                        ApplicationLifecycleState.DEPROVISION_FAILED,
                    }
                    else None
                )
                current.error_code = type(error).__name__ if isinstance(error, BaseException) else None
                current.error_message = _bounded_error(error)
            return True

    def snapshot(
        self,
        *,
        tenant: str,
        project: str,
        application_id: str,
    ) -> ApplicationReadinessSnapshot | None:
        with self._lock:
            current = self._records.get(self._key(tenant, project, application_id))
            return current.snapshot() if current is not None else None

    def scope_snapshots(
        self,
        *,
        tenant: str,
        project: str,
    ) -> dict[str, ApplicationReadinessSnapshot]:
        with self._lock:
            return self._scope_snapshots_locked(self._scope(tenant, project))

    def remove_application(
        self,
        *,
        tenant: str,
        project: str,
        application_id: str,
    ) -> None:
        with self._lock:
            self._records.pop(self._key(tenant, project, application_id), None)

    def _scope_snapshots_locked(
        self,
        scope: tuple[str, str],
    ) -> dict[str, ApplicationReadinessSnapshot]:
        return {
            key[2]: record.snapshot()
            for key, record in sorted(self._records.items())
            if key[:2] == scope
        }

    def aggregate(
        self,
        *,
        tenant: str,
        project: str,
    ) -> AggregateApplicationReadiness:
        snapshots = self.scope_snapshots(tenant=tenant, project=project)
        required = tuple(
            item for item in snapshots.values()
            if item.readiness is ApplicationReadinessMode.REQUIRED
        )
        blocking = tuple(item for item in required if not item.ready)
        return AggregateApplicationReadiness(
            tenant=str(tenant).strip(),
            project=str(project).strip(),
            ready=not blocking,
            required=required,
            blocking=blocking,
        )

    def require_ready(
        self,
        *,
        tenant: str,
        project: str,
        application_id: str,
    ) -> ApplicationReadinessSnapshot | None:
        scope = self._scope(tenant, project)
        snapshot = self.snapshot(
            tenant=scope[0],
            project=scope[1],
            application_id=application_id,
        )
        with self._lock:
            scope_active = scope in self._active_scopes
        if snapshot is None:
            if not scope_active:
                return None
            now = _utc_iso()
            snapshot = ApplicationReadinessSnapshot(
                tenant=scope[0],
                project=scope[1],
                application_id=str(application_id).strip(),
                readiness=ApplicationReadinessMode.INDEPENDENT,
                state=ApplicationLifecycleState.PENDING,
                desired_generation="unregistered",
                ready_generation=None,
                attempt=0,
                error_code=None,
                error_message=None,
                retry_at=None,
                started_at=None,
                finished_at=None,
                updated_at=now,
            )
        if not snapshot.ready:
            raise ApplicationNotReadyError(snapshot)
        return snapshot

    def deactivate_scope(self, *, tenant: str, project: str, clear: bool = True) -> None:
        scope = self._scope(tenant, project)
        with self._lock:
            self._active_scopes.discard(scope)
            if clear:
                for key in tuple(self._records):
                    if key[:2] == scope:
                        self._records.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._active_scopes.clear()
            self._records.clear()


application_readiness_registry = ApplicationReadinessRegistry()
