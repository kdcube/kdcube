# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Small framework-neutral host for running an agent core directly.

The full KDCube runtime normally supplies these boundaries. A direct process
can use this facade to get the same durable conversation record and accounted
turn scope from explicit Postgres, Redis, and object-storage configuration.
The concrete agent loop, tools, and private checkpoint/session remain owned by
the adapter.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import mimetypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Sequence

import asyncpg

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.runtime.harness.timeline.turn_log import TurnLog
from kdcube_ai_app.apps.chat.sdk.runtime.harness.timeline.turn_view import (
    extract_sources_used_from_blocks,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.read import (
    build_conversation_ctx_client,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.record import (
    TURN_LOG_RECORDING_RICH,
    build_minimal_turn_log_payload,
    record_minimal_turn_log_if_absent,
    reset_turn_log_recorded,
)
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.runtime.harness.workspace.hosting import (
    ApplicationHostingService,
)
from kdcube_ai_app.apps.chat.sdk.util import token_count
from kdcube_ai_app.infra.accounting import get_turn_events, with_accounting
from kdcube_ai_app.infra.accounting.envelope import (
    bind_accounting,
    build_envelope_from_session,
)


@dataclass(frozen=True)
class DirectAgentHarnessConfig:
    """Resolved infrastructure and identity for one direct agent host."""

    tenant: str
    project: str
    user_id: str
    user_type: str
    session_id: str
    bundle_id: str
    agent_id: str
    postgres_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    storage_uri: str
    turn_cache_ttl_seconds: int = 3600
    require_accounting_events: bool = True

    def __post_init__(self) -> None:
        required = {
            "tenant": self.tenant,
            "project": self.project,
            "user_id": self.user_id,
            "user_type": self.user_type,
            "session_id": self.session_id,
            "bundle_id": self.bundle_id,
            "agent_id": self.agent_id,
            "postgres_url": self.postgres_url,
            "redis_url": self.redis_url,
            "storage_uri": self.storage_uri,
        }
        missing = [
            name for name, value in required.items() if not str(value or "").strip()
        ]
        if missing:
            raise ValueError(
                f"direct agent harness configuration is missing: {', '.join(missing)}"
            )
        if int(self.turn_cache_ttl_seconds) <= 0:
            raise ValueError("turn_cache_ttl_seconds must be positive")


class DirectAgentTurn:
    """One accounted, durable turn opened by :class:`DirectAgentHarness`."""

    def __init__(
        self,
        *,
        harness: "DirectAgentHarness",
        conversation_id: str,
        turn_id: str,
        communicator: ChatCommunicator,
    ) -> None:
        self.harness = harness
        self.conversation_id = conversation_id
        self.turn_id = turn_id
        self.comm = communicator
        self.accounting_events: list[Any] = []
        self.user_attachments: list[dict[str, Any]] = []
        self.assistant_files: list[dict[str, Any]] = []
        self.execution_snapshots: list[dict[str, Any]] = []
        self.hosting_service = _TrackingHostingService(
            store=harness.storage,
            comm=communicator,
            assistant_files=self.assistant_files,
            execution_snapshots=self.execution_snapshots,
        )
        self.finished = False

    @property
    def conversation_client(self) -> Any:
        return self.harness.conversation_client

    async def add_user_attachment(
        self,
        source: str | Path,
        *,
        filename: str | None = None,
        mime: str | None = None,
        materialize_to: str | Path | None = None,
    ) -> dict[str, Any]:
        """Store one user input and optionally materialize it in the turn workspace."""
        path = Path(source).expanduser().resolve()
        return await self.add_user_attachment_bytes(
            path.read_bytes(),
            filename=filename or path.name,
            mime=mime,
            materialize_to=materialize_to,
        )

    async def add_user_attachment_bytes(
        self,
        data: bytes,
        *,
        filename: str,
        mime: str | None = None,
        materialize_to: str | Path | None = None,
    ) -> dict[str, Any]:
        """Store caller-provided bytes and optionally materialize them for the turn."""
        if not isinstance(data, bytes):
            raise TypeError("direct user attachment content must be bytes")
        name = Path(filename).name
        if not name:
            raise ValueError("direct user attachment filename is required")
        media_type = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
        uri, key, rn = await self.harness.storage.put_attachment(
            tenant=self.harness.config.tenant,
            project=self.harness.config.project,
            user=self.harness.config.user_id,
            fingerprint=None,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            role="user",
            filename=name,
            data=data,
            mime=media_type,
            user_type=self.harness.config.user_type,
            origin="user",
        )
        local_path = ""
        if materialize_to is not None:
            target = Path(materialize_to).expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            local_path = str(target)
        row = {
            "filename": name,
            "mime": media_type,
            "size": len(data),
            "content_sha256": hashlib.sha256(data).hexdigest(),
            "hosted_uri": uri,
            "key": key,
            "rn": rn,
            "physical_path": local_path or name,
            "visibility": "external",
        }
        self.user_attachments.append(row)
        return row

    async def host_files(
        self,
        *,
        files: Sequence[dict[str, Any]],
        outdir: str | Path,
        emit: bool = True,
    ) -> list[dict[str, Any]]:
        """Host current-turn output files and emit only externally visible rows."""
        hosted = await self.hosting_service.host_files_to_conversation(
            rid=f"req-{self.turn_id}",
            files=list(files),
            outdir=str(outdir),
            tenant=self.harness.config.tenant,
            project=self.harness.config.project,
            user=self.harness.config.user_id,
            conversation_id=self.conversation_id,
            user_type=self.harness.config.user_type,
            turn_id=self.turn_id,
        )
        visible = [row for row in hosted if row.get("visibility") != "internal"]
        if emit and visible:
            await self.hosting_service.emit_solver_artifacts(
                files=visible, citations=[]
            )
        return hosted

    async def persist_workspace(
        self,
        *,
        outdir: str | Path,
        workdir: str | Path,
        execution_id: str,
    ) -> dict[str, Any] | None:
        """Archive the turn's runtime output and generated-code workspace."""
        return await self.hosting_service.persist_workspace(
            outdir=str(outdir),
            workdir=str(workdir),
            tenant=self.harness.config.tenant,
            project=self.harness.config.project,
            user=self.harness.config.user_id,
            conversation_id=self.conversation_id,
            user_type=self.harness.config.user_type,
            turn_id=self.turn_id,
            codegen_run_id=execution_id,
        )

    async def complete(
        self,
        *,
        prompt: str,
        final_answer: str,
        conversation_title: str = "",
        rich_blocks: Sequence[dict[str, Any]] | None = None,
        started_at: str = "",
        ended_at: str = "",
    ) -> None:
        """Persist the turn and emit its completion.

        ``rich_blocks`` is for a loop, such as native ReAct, that owns an
        ordered harness timeline. Other adapters receive the canonical minimal
        turn record from their prompt and final answer.
        """
        answer = str(final_answer or "").strip()
        if not answer:
            raise ValueError("a completed direct agent turn requires a final answer")
        cfg = self.harness.config
        if rich_blocks is None:
            await record_minimal_turn_log_if_absent(
                conversation_client=self.conversation_client,
                tenant=cfg.tenant,
                project=cfg.project,
                user=cfg.user_id,
                user_type=cfg.user_type,
                conversation_id=self.conversation_id,
                turn_id=self.turn_id,
                bundle_id=cfg.bundle_id,
                agent_id=cfg.agent_id,
                final_answer=answer,
                conversation_title=conversation_title or None,
                user_prompt_text=str(prompt or ""),
                user_attachments=self.user_attachments,
                assistant_files=self.assistant_files,
            )
        else:
            blocks = [dict(block) for block in rich_blocks if isinstance(block, dict)]
            supplemental = (
                build_minimal_turn_log_payload(
                    final_answer=answer,
                    turn_id=self.turn_id,
                    conversation_id=self.conversation_id,
                    user_attachments=self.user_attachments,
                    assistant_files=self.assistant_files,
                ).get("blocks")
                or []
            )
            existing_paths = {
                str(block.get("path") or "")
                for block in blocks
                if isinstance(block, dict)
            }
            for block in supplemental:
                if not isinstance(block, dict):
                    continue
                if block.get("type") not in {
                    "user.attachment.meta",
                    "react.tool.result",
                }:
                    continue
                if str(block.get("path") or "") in existing_paths:
                    continue
                blocks.append(dict(block))
            payload = TurnLog(
                turn_id=self.turn_id,
                ts=str(started_at or ""),
                end_ts=str(ended_at or ""),
                blocks=blocks,
                sources_used=extract_sources_used_from_blocks(blocks),
                blocks_count=len(blocks),
                tokens=sum(
                    token_count(str(block.get("text") or ""))
                    for block in blocks
                    if block.get("text")
                ),
            ).to_dict()
            await self.conversation_client.save_turn_log_as_artifact(
                tenant=cfg.tenant,
                project=cfg.project,
                user=cfg.user_id,
                user_type=cfg.user_type,
                conversation_id=self.conversation_id,
                turn_id=self.turn_id,
                bundle_id=cfg.bundle_id,
                agent_id=cfg.agent_id,
                payload=payload,
                recording_kind=TURN_LOG_RECORDING_RICH,
                index_transcript=True,
            )
        await self.comm.complete(data={"final_answer": answer, "adapter": cfg.agent_id})
        self.finished = True


class DirectAgentHarness:
    """Bind direct agent turns to configured persistence and accounting."""

    def __init__(
        self,
        *,
        config: DirectAgentHarnessConfig,
        model_service: Any,
        emitter: Any,
    ) -> None:
        self.config = config
        self.model_service = model_service
        self.emitter = emitter
        self._pool: asyncpg.Pool | None = None
        self._store: ConversationStore | None = None
        self._conversation_client: Any = None

    @property
    def conversation_client(self) -> Any:
        if self._conversation_client is None:
            raise RuntimeError("direct agent harness is not open")
        return self._conversation_client

    @property
    def storage(self) -> ConversationStore:
        if self._store is None:
            raise RuntimeError("direct agent harness is not open")
        return self._store

    async def open(self) -> "DirectAgentHarness":
        if self._pool is not None:
            return self
        try:
            from redis.asyncio import from_url

            redis_client = from_url(self.config.redis_url, decode_responses=True)
            try:
                if not await redis_client.ping():
                    raise RuntimeError("Redis PING returned a false value")
            finally:
                await redis_client.aclose()
            self._pool = await asyncpg.create_pool(
                dsn=self.config.postgres_url,
                min_size=1,
                max_size=4,
            )
            self._store = ConversationStore(self.config.storage_uri)
            self._conversation_client = build_conversation_ctx_client(
                pg_pool=self._pool,
                tenant=self.config.tenant,
                project=self.config.project,
                model_service=self.model_service,
                store=self._store,
            )
            await self._conversation_client.idx.ensure_schema()
        except Exception:
            await self.close()
            raise
        return self

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        self._conversation_client = None
        self._store = None
        if pool is not None:
            await pool.close()

    async def __aenter__(self) -> "DirectAgentHarness":
        return await self.open()

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    def communicator(self, *, conversation_id: str, turn_id: str) -> ChatCommunicator:
        cfg = self.config
        return ChatCommunicator(
            emitter=self.emitter,
            tenant=cfg.tenant,
            project=cfg.project,
            user_id=cfg.user_id,
            user_type=cfg.user_type,
            service={
                "request_id": f"req-{turn_id}",
                "tenant": cfg.tenant,
                "project": cfg.project,
                "user": cfg.user_id,
                "user_type": cfg.user_type,
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "bundle_id": cfg.bundle_id,
                "agent_id": cfg.agent_id,
            },
            conversation={
                "session_id": cfg.session_id,
                "conversation_id": conversation_id,
                "turn_id": turn_id,
            },
        )

    @asynccontextmanager
    async def turn(
        self,
        *,
        conversation_id: str,
        turn_id: str,
    ) -> AsyncIterator[DirectAgentTurn]:
        if not str(conversation_id or "").strip() or not str(turn_id or "").strip():
            raise ValueError("conversation_id and turn_id are required")
        reset_turn_log_recorded()
        cfg = self.config
        session = SimpleNamespace(
            user_id=cfg.user_id,
            session_id=cfg.session_id,
            user_type=cfg.user_type,
            timezone="UTC",
        )
        envelope = build_envelope_from_session(
            session=session,
            tenant_id=cfg.tenant,
            project_id=cfg.project,
            request_id=f"req-{turn_id}",
            component="sdk.direct_agent_harness",
            app_bundle_id=cfg.bundle_id,
            metadata={"adapter": cfg.agent_id, "turn_id": turn_id},
        )
        turn = DirectAgentTurn(
            harness=self,
            conversation_id=conversation_id,
            turn_id=turn_id,
            communicator=self.communicator(
                conversation_id=conversation_id,
                turn_id=turn_id,
            ),
        )
        completed_normally = False
        async with bind_accounting(
            envelope,
            self.storage.backend,
            enabled=True,
            redis_turn_cache=True,
            turn_cache_ttl_s=cfg.turn_cache_ttl_seconds,
            redis_url=cfg.redis_url,
        ):
            async with with_accounting(
                cfg.bundle_id,
                agent=cfg.agent_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
            ):
                try:
                    yield turn
                    completed_normally = True
                finally:
                    turn.accounting_events = list(await get_turn_events())
        if completed_normally and not turn.finished:
            raise RuntimeError(
                "direct agent turn exited without calling turn.complete()"
            )
        if (
            completed_normally
            and cfg.require_accounting_events
            and not turn.accounting_events
        ):
            raise RuntimeError(
                "the completed model turn produced no Redis accounting evidence"
            )

    async def verify_conversation(
        self,
        *,
        conversation_id: str,
        expected_turn_ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        """Prove that Postgres rows point to materializable storage payloads."""
        result = await self.conversation_client.recent(
            kinds=("artifact:turn.log",),
            roles=("artifact",),
            limit=max(12, len(expected_turn_ids) * 2),
            days=3650,
            user_id=self.config.user_id,
            conversation_id=conversation_id,
            bundle_id=self.config.bundle_id,
            with_payload=True,
        )
        items = [
            dict(item) for item in (result.get("items") or []) if isinstance(item, dict)
        ]
        found = {str(item.get("turn_id") or "") for item in items}
        missing = [turn_id for turn_id in expected_turn_ids if turn_id not in found]
        if missing:
            raise RuntimeError(
                f"durable conversation is missing turn logs: {', '.join(missing)}"
            )
        for item in items:
            if str(item.get("turn_id") or "") not in expected_turn_ids:
                continue
            if not str(item.get("hosted_uri") or "").strip() or not isinstance(
                item.get("payload"), dict
            ):
                raise RuntimeError(
                    f"turn {item.get('turn_id')!r} has a Postgres row without a materialized storage payload"
                )
        return items


class _TrackingHostingService(ApplicationHostingService):
    """Application hosting plus direct-run evidence collection."""

    def __init__(
        self,
        *,
        assistant_files: list[dict[str, Any]],
        execution_snapshots: list[dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._assistant_files = assistant_files
        self._execution_snapshots = execution_snapshots

    async def host_files_to_conversation(self, **kwargs: Any) -> list[dict[str, Any]]:
        rows = await super().host_files_to_conversation(**kwargs)
        known = {str(row.get("key") or "") for row in self._assistant_files}
        for row in rows:
            key = str(row.get("key") or "")
            if key and key in known:
                continue
            self._assistant_files.append(dict(row))
            if key:
                known.add(key)
        return rows

    async def persist_workspace(self, **kwargs: Any) -> dict[str, Any] | None:
        snapshot = await super().persist_workspace(**kwargs)
        if isinstance(snapshot, dict):
            self._execution_snapshots.append(dict(snapshot))
        return snapshot


__all__ = [
    "DirectAgentHarness",
    "DirectAgentHarnessConfig",
    "DirectAgentTurn",
]
