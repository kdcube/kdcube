# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Per-tenant notification bus for live platform-setting consumers.

Descriptors remain the source of configuration. An update message says which
section changed so each service can reread only the state it owns. Values are
deliberately absent from the message: Redis is a wake-up path, not another
configuration store.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

from kdcube_ai_app.infra.namespaces import CONFIG

logger = logging.getLogger(__name__)

PLATFORM_SETTINGS_UPDATE_VERSION = 1
PlatformSettingsHandler = Callable[["PlatformSettingsUpdate"], Awaitable[None] | None]


def platform_settings_update_channel(*, tenant: str, project: str) -> str:
    return CONFIG.PLATFORM_SETTINGS.UPDATE_CHANNEL.format(
        tenant=str(tenant or "").strip(),
        project=str(project or "").strip(),
    )


@dataclass(frozen=True)
class PlatformSettingsUpdate:
    section: str
    scope: str
    changed: tuple[str, ...]
    reason: str
    actor: str
    tenant: str
    project: str
    event_id: str
    published_at: str
    version: int = PLATFORM_SETTINGS_UPDATE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "event_id": self.event_id,
            "published_at": self.published_at,
            "tenant": self.tenant,
            "project": self.project,
            "section": self.section,
            "scope": self.scope,
            "changed": list(self.changed),
            "reason": self.reason,
            "actor": self.actor,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def create(
        cls,
        *,
        tenant: str,
        project: str,
        section: str,
        scope: str,
        changed: Sequence[str] = (),
        reason: str = "",
        actor: str = "",
    ) -> "PlatformSettingsUpdate":
        normalized_section = str(section or "").strip().lower()
        normalized_scope = str(scope or "").strip().lower()
        normalized_tenant = str(tenant or "").strip()
        normalized_project = str(project or "").strip()
        if not normalized_section or not normalized_scope:
            raise ValueError("Platform settings updates require section and scope")
        if not normalized_tenant or not normalized_project:
            raise ValueError("Platform settings updates require tenant and project")
        return cls(
            section=normalized_section,
            scope=normalized_scope,
            changed=tuple(dict.fromkeys(str(item).strip() for item in changed if str(item).strip())),
            reason=str(reason or "").strip(),
            actor=str(actor or "").strip(),
            tenant=normalized_tenant,
            project=normalized_project,
            event_id=uuid.uuid4().hex,
            published_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "PlatformSettingsUpdate":
        try:
            version = int(payload.get("version") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Platform settings update has an invalid version") from exc
        if version != PLATFORM_SETTINGS_UPDATE_VERSION:
            raise ValueError(f"Unsupported platform settings update version: {version}")
        event = cls(
            version=version,
            event_id=str(payload.get("event_id") or "").strip(),
            published_at=str(payload.get("published_at") or "").strip(),
            tenant=str(payload.get("tenant") or "").strip(),
            project=str(payload.get("project") or "").strip(),
            section=str(payload.get("section") or "").strip().lower(),
            scope=str(payload.get("scope") or "").strip().lower(),
            changed=tuple(
                dict.fromkeys(
                    str(item).strip()
                    for item in (payload.get("changed") or [])
                    if str(item).strip()
                )
            ),
            reason=str(payload.get("reason") or "").strip(),
            actor=str(payload.get("actor") or "").strip(),
        )
        if not all((event.event_id, event.tenant, event.project, event.section, event.scope)):
            raise ValueError("Platform settings update is missing required fields")
        return event

    @classmethod
    def from_message(cls, data: Any) -> "PlatformSettingsUpdate":
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        payload = json.loads(str(data or ""))
        if not isinstance(payload, Mapping):
            raise ValueError("Platform settings update must be a JSON object")
        return cls.from_payload(payload)


async def publish_platform_settings_update(
    redis: Any,
    *,
    tenant: str,
    project: str,
    section: str,
    scope: str,
    changed: Sequence[str] = (),
    reason: str = "",
    actor: str = "",
) -> PlatformSettingsUpdate:
    event = PlatformSettingsUpdate.create(
        tenant=tenant,
        project=project,
        section=section,
        scope=scope,
        changed=changed,
        reason=reason,
        actor=actor,
    )
    await redis.publish(
        platform_settings_update_channel(tenant=event.tenant, project=event.project),
        event.to_json(),
    )
    return event


class PlatformSettingsUpdateListener:
    """Dispatch one platform-settings channel to section-specific handlers."""

    def __init__(
        self,
        redis: Any,
        *,
        tenant: str,
        project: str,
        handlers: Mapping[str, PlatformSettingsHandler],
        stop_event: asyncio.Event | None = None,
        poll_seconds: float = 1.0,
        reconnect_max_seconds: float = 10.0,
    ) -> None:
        self.redis = redis
        self.tenant = str(tenant or "").strip()
        self.project = str(project or "").strip()
        self.handlers = {
            str(section or "").strip().lower(): handler
            for section, handler in handlers.items()
            if str(section or "").strip() and handler is not None
        }
        self.stop_event = stop_event or asyncio.Event()
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.reconnect_max_seconds = max(0.1, float(reconnect_max_seconds))
        self.channel = platform_settings_update_channel(tenant=self.tenant, project=self.project)

    async def _call(self, handler: PlatformSettingsHandler, event: PlatformSettingsUpdate) -> None:
        result = handler(event)
        if inspect.isawaitable(result):
            await result

    async def dispatch(self, event: PlatformSettingsUpdate) -> bool:
        if event.tenant != self.tenant or event.project != self.project:
            logger.warning(
                "Ignored platform settings update for another runtime: tenant=%s project=%s section=%s",
                event.tenant,
                event.project,
                event.section,
            )
            return False
        handler = self.handlers.get(event.section)
        if handler is None:
            logger.info(
                "No platform settings handler in this service: section=%s scope=%s event_id=%s",
                event.section,
                event.scope,
                event.event_id,
            )
            return False
        await self._call(handler, event)
        return True

    async def catch_up(self) -> None:
        # Redis Pub/Sub does not buffer updates. Each handler rereads its own
        # descriptor state after a listener reconnects.
        for section, handler in self.handlers.items():
            event = PlatformSettingsUpdate.create(
                tenant=self.tenant,
                project=self.project,
                section=section,
                scope="catch_up",
                reason="platform-settings-listener.subscribe",
                actor="runtime",
            )
            await self._call(handler, event)

    async def run(self) -> None:
        backoff = 0.5
        while not self.stop_event.is_set():
            pubsub = None
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(self.channel)
                logger.info(
                    "Subscribed to platform settings updates: channel=%s sections=%s",
                    self.channel,
                    sorted(self.handlers),
                )
                await self.catch_up()
                backoff = 0.5
                while not self.stop_event.is_set():
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=self.poll_seconds,
                    )
                    if not message or message.get("type") not in {"message", b"message"}:
                        await asyncio.sleep(0)
                        continue
                    try:
                        event = PlatformSettingsUpdate.from_message(message.get("data"))
                    except Exception as exc:
                        logger.warning("Ignored invalid platform settings update: %s", exc)
                        continue
                    try:
                        await self.dispatch(event)
                    except Exception:
                        logger.exception(
                            "Platform settings handler failed: section=%s scope=%s event_id=%s",
                            event.section,
                            event.scope,
                            event.event_id,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Platform settings listener disconnected; retrying: channel=%s delay=%ss",
                    self.channel,
                    backoff,
                )
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(self.reconnect_max_seconds, backoff * 2)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(self.channel)
                    except Exception:
                        pass
                    try:
                        close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
                        if close is not None:
                            result = close()
                            if inspect.isawaitable(result):
                                await result
                    except Exception:
                        pass


__all__ = [
    "PLATFORM_SETTINGS_UPDATE_VERSION",
    "PlatformSettingsHandler",
    "PlatformSettingsUpdate",
    "PlatformSettingsUpdateListener",
    "platform_settings_update_channel",
    "publish_platform_settings_update",
]
