from __future__ import annotations

import json
import os
import uuid
from collections import deque
from typing import Any, Mapping

import pytest

from kdcube_ai_app.auth.bundle.sessions import (
    BundleSessionAuthority,
    BundleSessionInvalid,
)
from kdcube_ai_app.auth.bundle.session_schema import (
    TABLE_SESSIONS,
    TABLE_USERS,
    bundle_session_schema_sql,
)
from kdcube_ai_app.auth.bundle.session_store import (
    BundleSessionLoginState,
    BundleSessionValidationState,
    PostgresBundleSessionStore,
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
    def __init__(
        self,
        *,
        rows: list[dict[str, Any] | None] | None = None,
        statuses: list[str] | None = None,
    ) -> None:
        self.rows = deque(rows or [])
        self.statuses = deque(statuses or [])
        self.calls: list[tuple[str, str, tuple[Any, ...], int]] = []
        self.transaction_depth = 0
        self.transaction_enters = 0
        self.transaction_exits = 0

    def transaction(self) -> _Transaction:
        return _Transaction(self)

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append(("execute", sql, args, self.transaction_depth))
        if self.statuses:
            return self.statuses.popleft()
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


def _store(connection: _Connection) -> PostgresBundleSessionStore:
    return PostgresBundleSessionStore(
        pg_pool=_Pool(connection),
        tenant="demo-tenant",
        project="demo-project",
    )


def test_bundle_session_schema_stores_only_a_token_hash() -> None:
    sql = bundle_session_schema_sql("kdcube_demo_tenant_demo_project")

    assert "token_sha256" in sql
    assert "session_token" not in sql
    assert "raw_token" not in sql
    assert "kdcube_bundle_session_users" in sql
    assert "kdcube_bundle_sessions" in sql
    assert sql.count("revision            BIGINT NOT NULL DEFAULT 1") == 2


@pytest.mark.asyncio
async def test_register_user_merges_under_one_sql_transaction() -> None:
    connection = _Connection(
        rows=[
            {
                "state": "active",
                "record": {
                    "sub": "user-1",
                    "email": "old@example.test",
                    "roles": ["reader"],
                    "created_at": 10,
                    "updated_at": 10,
                    "disabled": False,
                }
            }
        ]
    )

    record = await _store(connection).register_user(
        sub="user-1",
        updates={"email": "new@example.test", "disabled": False},
        now=20,
    )

    assert record["email"] == "new@example.test"
    assert record["roles"] == ["reader"]
    assert record["created_at"] == 10
    assert record["updated_at"] == 20
    assert connection.transaction_enters == 1
    assert connection.transaction_exits == 1
    assert all(depth == 1 for _kind, _sql, _args, depth in connection.calls)
    assert "FOR UPDATE" in connection.calls[1][1]
    assert "revision = revision + 1" in connection.calls[2][1]


@pytest.mark.asyncio
async def test_register_user_does_not_write_or_advance_an_unchanged_record() -> None:
    existing = {
        "sub": "user-1",
        "email": "same@example.test",
        "roles": ["reader"],
        "created_at": 10,
        "updated_at": 10,
        "disabled": False,
    }
    connection = _Connection(
        rows=[
            {
                "state": "active",
                "disabled": False,
                "revision": 7,
                "record": existing,
            }
        ]
    )

    record = await _store(connection).register_user(
        sub="user-1",
        updates={"email": "same@example.test", "disabled": False},
        now=20,
    )

    assert record == existing
    assert [kind for kind, _sql, _args, _depth in connection.calls] == [
        "execute",
        "fetchrow",
    ]


@pytest.mark.asyncio
async def test_register_user_does_not_revive_deleted_profile_fields() -> None:
    connection = _Connection(
        rows=[
            {
                "state": "deleted",
                "record": {
                    "sub": "user-1",
                    "email": "deleted@example.test",
                    "roles": ["admin"],
                    "permissions": ["records:delete"],
                    "metadata": {"privileged": True},
                    "created_at": 10,
                    "updated_at": 15,
                    "disabled": True,
                },
            }
        ]
    )

    record = await _store(connection).register_user(
        sub="user-1",
        updates={"disabled": False},
        now=20,
    )

    assert record == {
        "sub": "user-1",
        "roles": [],
        "permissions": [],
        "metadata": {},
        "disabled": False,
        "created_at": 20,
        "updated_at": 20,
    }
    assert "revision = revision + 1" in connection.calls[2][1]


@pytest.mark.asyncio
async def test_touch_session_writes_only_after_half_the_idle_window() -> None:
    current = {
        "session_id": "bsn_1",
        "exp": 180,
        "max_exp": 300,
        "last_seen": 100,
    }
    connection = _Connection(rows=[{"record": current}])

    record = await _store(connection).touch_session(
        "bsn_1",
        expires_at=200,
        now=120,
    )

    assert record == current
    assert len(connection.calls) == 1
    kind, sql, arguments, depth = connection.calls[0]
    assert kind == "fetchrow"
    assert arguments == ("bsn_1", 200, 120)
    assert depth == 1
    assert "candidate.idle_expires_at < to_timestamp($3)" in sql
    assert "SELECT candidate.record" in sql
    assert "last_seen_at =" not in sql
    assert "revision = bundle_session.revision + 1" in sql


@pytest.mark.asyncio
async def test_issue_session_locks_user_and_writes_hash_in_one_transaction() -> None:
    token_hash = "a" * 64
    connection = _Connection(
        rows=[{"session_version": 4, "state": "active", "disabled": False}]
    )

    issued = await _store(connection).issue_session(
        {
            "session_id": "bsn_1",
            "sub": "user-1",
            "token_sha256": token_hash,
            "version": 4,
            "iat": 100,
            "exp": 200,
            "max_exp": 300,
            "last_seen": 100,
        },
        expected_version=4,
    )

    assert issued is True
    assert connection.transaction_enters == 1
    assert connection.transaction_exits == 1
    assert "FOR UPDATE" in connection.calls[0][1]
    assert all(depth == 1 for _kind, _sql, _args, depth in connection.calls)
    sql_arguments = [
        argument
        for _kind, _sql, arguments, _depth in connection.calls
        for argument in arguments
    ]
    assert token_hash in sql_arguments


@pytest.mark.asyncio
async def test_issue_session_refuses_a_stale_user_epoch_without_insert() -> None:
    connection = _Connection(
        rows=[{"session_version": 5, "state": "active", "disabled": False}]
    )

    issued = await _store(connection).issue_session(
        {
            "session_id": "bsn_stale",
            "sub": "user-1",
            "token_sha256": "b" * 64,
            "version": 4,
            "iat": 100,
            "exp": 200,
            "max_exp": 300,
            "last_seen": 100,
        },
        expected_version=4,
    )

    assert issued is False
    assert [kind for kind, _sql, _args, _depth in connection.calls] == [
        "fetchrow"
    ]
    assert connection.transaction_enters == 1
    assert connection.transaction_exits == 1


@pytest.mark.asyncio
async def test_invalidate_user_advances_epoch_and_revokes_sessions_atomically() -> None:
    connection = _Connection(
        rows=[{"subject": "user-1"}],
        statuses=["UPDATE 1", "UPDATE 2"],
    )

    removed = await _store(connection).invalidate_user("user-1")

    assert removed == 2
    assert connection.transaction_enters == 1
    assert connection.transaction_exits == 1
    assert all(depth == 1 for _kind, _sql, _args, depth in connection.calls)
    assert "session_version = session_version + 1" in connection.calls[1][1]
    assert "revision = revision + 1" in connection.calls[1][1]
    assert "state = 'revoked'" in connection.calls[2][1]
    assert "revision = revision + 1" in connection.calls[2][1]


@pytest.mark.asyncio
async def test_validation_state_exposes_durable_row_revisions() -> None:
    connection = _Connection(
        rows=[
            {
                "session_record": {"session_id": "bsn_1", "sub": "user-1"},
                "session_state": "active",
                "session_revision": 5,
                "idle_expires_at": 200,
                "hard_expires_at": 300,
                "user_record": {"sub": "user-1"},
                "user_state": "active",
                "user_disabled": False,
                "session_version": 3,
                "user_revision": 9,
            }
        ]
    )

    state = await _store(connection).get_validation_state("bsn_1")

    assert state is not None
    assert state.session_revision == 5
    assert state.user_revision == 9
    assert "bundle_session.revision AS session_revision" in connection.calls[0][1]
    assert "bundle_user.revision AS user_revision" in connection.calls[0][1]


class _MemoryAuthorityStore:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, int] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.session_states: dict[str, str] = {}

    async def register_user(
        self,
        *,
        sub: str,
        updates: Mapping[str, Any],
        now: int,
    ) -> dict[str, Any]:
        existing = self.users.get(sub, {"sub": sub, "created_at": now})
        record = {**existing, **dict(updates), "sub": sub, "updated_at": now}
        self.users[sub] = record
        self.versions.setdefault(sub, 1)
        return dict(record)

    async def get_user(self, sub: str) -> dict[str, Any] | None:
        user = self.users.get(sub)
        return dict(user) if user is not None else None

    async def get_login_state(self, sub: str) -> BundleSessionLoginState | None:
        user = self.users.get(sub)
        if user is None:
            return None
        return BundleSessionLoginState(dict(user), self.versions[sub])

    async def issue_session(
        self,
        record: Mapping[str, Any],
        *,
        expected_version: int,
    ) -> bool:
        payload = dict(record)
        sub = str(payload["sub"])
        if self.versions.get(sub) != expected_version:
            return False
        self.sessions[str(payload["session_id"])] = payload
        self.session_states[str(payload["session_id"])] = "active"
        return True

    async def get_validation_state(
        self,
        session_id: str,
    ) -> BundleSessionValidationState | None:
        record = self.sessions.get(session_id)
        if record is None:
            return None
        sub = str(record["sub"])
        user = self.users[sub]
        return BundleSessionValidationState(
            session=dict(record),
            session_state=self.session_states[session_id],
            idle_expires_at=int(record["exp"]),
            hard_expires_at=int(record["max_exp"]),
            user=dict(user),
            user_state="active",
            user_disabled=bool(user.get("disabled")),
            version=self.versions[sub],
        )

    async def touch_session(
        self,
        session_id: str,
        *,
        expires_at: int,
        now: int,
    ) -> dict[str, Any] | None:
        record = self.sessions.get(session_id)
        if record is None or self.session_states.get(session_id) != "active":
            return None
        record["exp"] = min(
            max(int(record["exp"]), int(expires_at)),
            int(record["max_exp"]),
        )
        record["last_seen"] = now
        return dict(record)

    async def revoke_session(self, session_id: str) -> bool:
        if self.session_states.get(session_id) != "active":
            return False
        self.session_states[session_id] = "revoked"
        return True

    async def invalidate_user(self, sub: str) -> int:
        if sub not in self.users:
            return 0
        self.versions[sub] += 1
        affected = 0
        for session_id, record in self.sessions.items():
            if record["sub"] == sub and self.session_states[session_id] == "active":
                self.session_states[session_id] = "revoked"
                affected += 1
        return affected

    async def delete_user(self, sub: str) -> bool:
        if sub not in self.users:
            return False
        await self.invalidate_user(sub)
        self.users.pop(sub)
        return True


@pytest.mark.asyncio
async def test_authority_uses_durable_store_without_persisting_raw_token() -> None:
    store = _MemoryAuthorityStore()
    authority = BundleSessionAuthority(
        tenant="demo-tenant",
        project="demo-project",
        secret="session-secret",
        authority_store=store,
    )

    await authority.register_user(
        sub="user-1",
        username="alice",
        roles=["registered"],
    )
    grant = await authority.login(sub="user-1", idle_ttl_seconds=60)

    assert grant.token not in json.dumps(store.sessions, sort_keys=True)
    assert store.sessions[grant.session_id]["token_sha256"]
    verified = await authority.validate_token(grant.token)
    assert verified.user.username == "alice"

    assert await authority.logout(token=grant.token) is True
    with pytest.raises(BundleSessionInvalid, match="not active"):
        await authority.validate_token(grant.token)


@pytest.mark.asyncio
async def test_bundle_session_revision_contract_against_real_postgres() -> None:
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set")

    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    store = PostgresBundleSessionStore(
        pg_pool=pool,
        tenant=f"session-authority-test-{uuid.uuid4().hex}",
        project="bundle-sessions",
    )
    try:
        await store.ensure_schema()
        created = await store.register_user(
            sub="user-1",
            updates={"email": "alice@example.test", "roles": ["reader"]},
            now=100,
        )
        unchanged = await store.register_user(
            sub="user-1",
            updates={"email": "alice@example.test", "roles": ["reader"]},
            now=120,
        )
        assert unchanged == created

        async with pool.acquire() as connection:
            revision = await connection.fetchval(
                f"SELECT revision FROM {store.schema}.{TABLE_USERS} "
                "WHERE subject = $1",
                "user-1",
            )
        assert revision == 1

        await store.register_user(
            sub="user-1",
            updates={"roles": ["writer"]},
            now=130,
        )
        login = await store.get_login_state("user-1")
        assert login is not None
        assert login.user_revision == 2

        issued = await store.issue_session(
            {
                "session_id": "bsn_1",
                "sub": "user-1",
                "token_sha256": "a" * 64,
                "version": login.version,
                "iat": 100,
                "exp": 200,
                "max_exp": 400,
                "last_seen": 100,
            },
            expected_version=login.version,
        )
        assert issued is True

        untouched = await store.touch_session(
            "bsn_1",
            expires_at=300,
            now=100,
        )
        assert untouched is not None
        async with pool.acquire() as connection:
            session_revision = await connection.fetchval(
                f"SELECT revision FROM {store.schema}.{TABLE_SESSIONS} "
                "WHERE session_id = $1",
                "bsn_1",
            )
        assert session_revision == 1

        touched = await store.touch_session(
            "bsn_1",
            expires_at=300,
            now=150,
        )
        assert touched is not None
        assert touched["exp"] == 300
        validation = await store.get_validation_state("bsn_1")
        assert validation is not None
        assert validation.session_revision == 2
        assert validation.user_revision == 2
    finally:
        async with pool.acquire() as connection:
            await connection.execute(f"DROP SCHEMA IF EXISTS {store.schema} CASCADE")
        await pool.close()
