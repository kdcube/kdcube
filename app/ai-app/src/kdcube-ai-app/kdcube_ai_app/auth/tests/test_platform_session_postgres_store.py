from __future__ import annotations

import json
import os
import time
import uuid
from collections import deque
from typing import Any, Mapping

import pytest

from kdcube_ai_app.auth.platform_session_schema import (
    TABLE_PLATFORM_SESSIONS,
    platform_session_schema_sql,
)
from kdcube_ai_app.auth.platform_session_store import (
    PlatformSessionStoreResult,
    PostgresPlatformSessionStore,
)
from kdcube_ai_app.auth.sessions import (
    RequestContext,
    SessionManager,
    UserSession,
    UserType,
)


class _Transaction:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection

    async def __aenter__(self) -> None:
        self.connection.transaction_depth += 1
        self.connection.transaction_enters += 1

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.connection.transaction_depth -= 1
        self.connection.transaction_exits += 1


class _Connection:
    def __init__(self, *, rows: list[dict[str, Any] | None] | None = None) -> None:
        self.rows = deque(rows or [])
        self.calls: list[tuple[str, str, tuple[Any, ...], int]] = []
        self.transaction_depth = 0
        self.transaction_enters = 0
        self.transaction_exits = 0

    def transaction(self) -> _Transaction:
        return _Transaction(self)

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append(("execute", sql, args, self.transaction_depth))
        if sql.lstrip().startswith("UPDATE"):
            return "UPDATE 1"
        return "INSERT 0 1"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.calls.append(("fetchrow", sql, args, self.transaction_depth))
        return self.rows.popleft() if self.rows else None


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


def _store(connection: _Connection) -> PostgresPlatformSessionStore:
    return PostgresPlatformSessionStore(
        pg_pool=_Pool(connection),
        tenant="demo-tenant",
        project="demo-project",
    )


def _candidate(*, session_id: str = "session-1") -> dict[str, Any]:
    return {
        "session_id": session_id,
        "user_type": "registered",
        "fingerprint": "fingerprint-1",
        "user_id": "user-1",
        "username": "alice",
        "roles": ["reader"],
        "permissions": [],
        "created_at": 100.0,
        "last_seen": 100.0,
        "timezone": "UTC",
        "request_context": {
            "client_ip": "",
            "user_agent": "",
            "user_timezone": "UTC",
            "user_utc_offset_min": 0,
        },
    }


def test_platform_session_schema_has_relational_identity_and_lifecycle() -> None:
    sql = platform_session_schema_sql("kdcube_demo_tenant_demo_project")

    assert "authority_key" in sql
    assert "session_id" in sql
    assert "revision            BIGINT NOT NULL DEFAULT 1" in sql
    assert "state IN ('active', 'expired', 'revoked')" in sql
    assert "authorization_header" not in sql
    assert "id_token" not in sql


def test_session_serialization_excludes_request_bearers_and_preserves_time() -> None:
    session = UserSession(
        session_id="session-1",
        user_type=UserType.REGISTERED,
        user_id="user-1",
        created_at=10.0,
        last_seen=20.0,
        request_context=RequestContext(
            client_ip="192.0.2.10",
            user_agent="browser-agent",
            authorization_header="Bearer access-secret",
            id_token="id-secret",
            user_timezone="Europe/Berlin",
            user_utc_offset_min=120,
        ),
    )

    record = session.serialize_to_dict()

    assert record["created_at"] == 10.0
    assert record["last_seen"] == 20.0
    assert record["request_context"] == {
        "client_ip": "",
        "user_agent": "",
        "user_timezone": "Europe/Berlin",
        "user_utc_offset_min": 120,
    }
    assert "access-secret" not in json.dumps(record)
    assert "id-secret" not in json.dumps(record)


@pytest.mark.asyncio
async def test_get_or_create_serializes_principal_under_advisory_lock() -> None:
    connection = _Connection(rows=[None])

    result = await _store(connection).get_or_create(
        authority_key="registered:user-1",
        candidate=_candidate(),
        user_data={"user_id": "user-1", "roles": ["writer"]},
        request_context={
            "client_ip": "",
            "user_agent": "",
            "user_timezone": "UTC",
            "user_utc_offset_min": 0,
        },
        user_type="registered",
        ttl_seconds=3600,
        now=200.0,
    )

    assert result.created is True
    assert result.record["roles"] == ["writer"]
    assert result.record["created_at"] == 200.0
    assert result.record["last_seen"] == 200.0
    assert result.revision == 1
    assert connection.transaction_enters == 1
    assert connection.transaction_exits == 1
    assert all(depth == 1 for _kind, _sql, _args, depth in connection.calls)
    assert "pg_advisory_xact_lock" in connection.calls[0][1]
    assert "FOR UPDATE" in connection.calls[1][1]


@pytest.mark.asyncio
async def test_get_or_create_merges_live_session_in_same_transaction() -> None:
    connection = _Connection(
        rows=[
            {
                "session_id": "session-existing",
                "record": _candidate(session_id="session-existing"),
                "revision": 4,
                "expires_at": 500.0,
            }
        ]
    )

    result = await _store(connection).get_or_create(
        authority_key="registered:user-1",
        candidate=_candidate(session_id="session-candidate"),
        user_data={"roles": ["admin"], "permissions": ["records:read"]},
        request_context={
            "client_ip": "",
            "user_agent": "",
            "user_timezone": "Europe/Berlin",
            "user_utc_offset_min": 120,
        },
        user_type="privileged",
        ttl_seconds=3600,
        now=200.0,
    )

    assert result.created is False
    assert result.record["session_id"] == "session-existing"
    assert result.record["roles"] == ["admin"]
    assert result.record["permissions"] == ["records:read"]
    assert result.record["user_type"] == "privileged"
    assert result.record["timezone"] == "Europe/Berlin"
    assert result.revision == 5
    assert all(depth == 1 for _kind, _sql, _args, depth in connection.calls)
    assert "revision = revision + 1" in connection.calls[2][1]


@pytest.mark.asyncio
async def test_get_or_create_does_not_write_an_unchanged_live_session() -> None:
    existing = _candidate(session_id="session-existing")
    connection = _Connection(
        rows=[
            {
                "session_id": "session-existing",
                "record": existing,
                "revision": 7,
                "expires_at": 1_000.0,
            }
        ]
    )

    result = await _store(connection).get_or_create(
        authority_key="registered:user-1",
        candidate=_candidate(session_id="session-candidate"),
        user_data={
            "user_id": "user-1",
            "username": "alice",
            "roles": ["reader"],
            "permissions": [],
        },
        request_context={
            "client_ip": "",
            "user_agent": "",
            "user_timezone": "UTC",
            "user_utc_offset_min": 0,
        },
        user_type="registered",
        ttl_seconds=600,
        now=200.0,
    )

    assert result == PlatformSessionStoreResult(
        existing,
        False,
        7,
        "registered:user-1",
        1_000.0,
    )
    assert [kind for kind, _sql, _args, _depth in connection.calls] == [
        "execute",
        "fetchrow",
    ]
    assert "pg_advisory_xact_lock" in connection.calls[0][1]
    assert "FOR UPDATE" in connection.calls[1][1]


class _MemoryStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.authority_keys: dict[str, str] = {}
        self.last_candidate: dict[str, Any] = {}
        self.last_context: dict[str, Any] = {}

    async def get_or_create(
        self,
        *,
        authority_key: str,
        candidate: Mapping[str, Any],
        user_data: Mapping[str, Any],
        request_context: Mapping[str, Any],
        user_type: str,
        ttl_seconds: int,
        now: float,
    ) -> PlatformSessionStoreResult:
        del user_data, user_type, ttl_seconds, now
        self.last_candidate = dict(candidate)
        self.last_context = dict(request_context)
        existing_id = self.authority_keys.get(authority_key)
        if existing_id:
            return PlatformSessionStoreResult(
                dict(self.records[existing_id]),
                False,
            )
        record = dict(candidate)
        session_id = str(record["session_id"])
        self.records[session_id] = record
        self.authority_keys[authority_key] = session_id
        return PlatformSessionStoreResult(dict(record), True)

    async def update_session(
        self,
        record: Mapping[str, Any],
        *,
        ttl_seconds: int,
        now: float,
    ) -> bool:
        del ttl_seconds, now
        session_id = str(record["session_id"])
        if session_id not in self.records:
            return False
        self.records[session_id] = dict(record)
        return True

    async def get_session_by_id(self, session_id: str) -> dict[str, Any] | None:
        record = self.records.get(session_id)
        return dict(record) if record else None

    async def get_session_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        for record in self.records.values():
            if record.get("user_id") == user_id:
                return dict(record)
        return None


@pytest.mark.asyncio
async def test_session_manager_uses_postgres_store_without_redis_or_bearers() -> None:
    store = _MemoryStore()
    manager = SessionManager(
        redis_url="",
        tenant="demo-tenant",
        project="demo-project",
        authority_store=store,
    )
    context = RequestContext(
        client_ip="192.0.2.10",
        user_agent="browser-agent",
        authorization_header="Bearer access-secret",
        id_token="id-secret",
        user_timezone="Europe/Berlin",
        user_utc_offset_min=120,
    )

    session = await manager.get_or_create_session(
        context,
        UserType.REGISTERED,
        {"user_id": "user-1", "username": "alice", "roles": ["reader"]},
    )

    assert session.is_new_session is True
    assert session.request_context is context
    serialized = json.dumps(store.last_candidate, sort_keys=True)
    assert "access-secret" not in serialized
    assert "id-secret" not in serialized
    assert store.last_context["user_timezone"] == "Europe/Berlin"
    assert await manager.get_session_by_id(session.session_id) is not None
    assert await manager.get_session_by_user_id("user-1") is not None


class _CaptureRedis:
    def __init__(self) -> None:
        self.arguments: tuple[Any, ...] = ()

    async def eval(
        self,
        script: str,
        num_keys: int,
        session_key: str,
        *arguments: Any,
    ) -> list[Any]:
        del script, num_keys, session_key
        self.arguments = arguments
        return [1, arguments[0]]


@pytest.mark.asyncio
async def test_legacy_redis_session_record_never_contains_request_bearers() -> None:
    redis = _CaptureRedis()
    manager = SessionManager(
        redis_url="redis://unused",
        tenant="demo-tenant",
        project="demo-project",
    )
    manager.redis = redis
    context = RequestContext(
        client_ip="192.0.2.10",
        user_agent="browser-agent",
        authorization_header="Bearer access-secret",
        id_token="id-secret",
        user_timezone="Europe/Berlin",
        user_utc_offset_min=120,
    )

    session = await manager.get_or_create_session(
        context,
        UserType.REGISTERED,
        {"user_id": "user-1", "username": "alice", "roles": ["reader"]},
    )

    assert session.request_context is context
    persisted = json.dumps(redis.arguments, sort_keys=True)
    assert "access-secret" not in persisted
    assert "id-secret" not in persisted
    assert "192.0.2.10" not in persisted
    assert "browser-agent" not in persisted


@pytest.mark.asyncio
async def test_platform_session_revision_contract_against_real_postgres() -> None:
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set")

    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    store = PostgresPlatformSessionStore(
        pg_pool=pool,
        tenant=f"session-authority-test-{uuid.uuid4().hex}",
        project="platform-sessions",
    )
    context = {
        "client_ip": "",
        "user_agent": "",
        "user_timezone": "UTC",
        "user_utc_offset_min": 0,
    }
    now = time.time()
    try:
        await store.ensure_schema()
        created = await store.get_or_create(
            authority_key="registered:user-1",
            candidate=_candidate(),
            user_data={
                "user_id": "user-1",
                "username": "alice",
                "roles": ["reader"],
                "permissions": [],
            },
            request_context=context,
            user_type="registered",
            ttl_seconds=600,
            now=now,
        )
        assert created.revision == 1

        unchanged = await store.get_or_create(
            authority_key="registered:user-1",
            candidate=_candidate(session_id="session-candidate"),
            user_data={
                "user_id": "user-1",
                "username": "alice",
                "roles": ["reader"],
                "permissions": [],
            },
            request_context=context,
            user_type="registered",
            ttl_seconds=600,
            now=now + 100,
        )
        assert unchanged.created is False
        assert unchanged.revision == 1

        changed = await store.get_or_create(
            authority_key="registered:user-1",
            candidate=_candidate(session_id="session-candidate"),
            user_data={
                "user_id": "user-1",
                "username": "alice",
                "roles": ["writer"],
                "permissions": [],
            },
            request_context=context,
            user_type="registered",
            ttl_seconds=600,
            now=now + 110,
        )
        assert changed.revision == 2
        assert changed.record["roles"] == ["writer"]
        assert await store.get_session_by_id("session-1") is not None
        assert await store.get_session_by_user_id("user-1") is not None

        async with pool.acquire() as connection:
            revision = await connection.fetchval(
                f"SELECT revision FROM {store.schema}.{TABLE_PLATFORM_SESSIONS} "
                "WHERE session_id = $1",
                "session-1",
            )
        assert revision == 2
    finally:
        async with pool.acquire() as connection:
            await connection.execute(f"DROP SCHEMA IF EXISTS {store.schema} CASCADE")
        await pool.close()
