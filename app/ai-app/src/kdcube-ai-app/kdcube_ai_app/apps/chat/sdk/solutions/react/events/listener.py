# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

from kdcube_ai_app.apps.chat.sdk.solutions.react.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.events.event_bus.exceptions import ExternalEventLaneTurnSuperseded


@dataclass(frozen=True)
class LiveExternalEventOwnerLease:
    """Owner lease held by a live ReAct turn while it drains its event lane."""

    lease_token: str
    lease_epoch: int = 0


async def acquire_live_external_event_owner(
    *,
    source: Any,
    runtime_ctx: RuntimeCtx,
    listener_id: str,
    log: Any,
) -> LiveExternalEventOwnerLease | None:
    """Acquire the Redis owner lease for the live ReAct event listener."""

    try:
        lease = await source.acquire_owner(
            turn_id=str(runtime_ctx.turn_id or ""),
            bundle_id=str(runtime_ctx.bundle_id or ""),
            listener_id=listener_id,
        )
        token = str(getattr(lease, "lease_token", "") or "")
        if not token:
            return None
        return LiveExternalEventOwnerLease(
            lease_token=token,
            lease_epoch=int(getattr(lease, "lease_epoch", 0) or 0),
        )
    except Exception:
        try:
            log.log("[timeline.external] failed to acquire owner lease\n" + traceback.format_exc(), "ERROR")
        except Exception:
            pass
        return None


async def release_live_external_event_owner(
    *,
    source: Any,
    listener_id: str,
    lease_token: str,
) -> None:
    """Release the Redis owner lease held by a live ReAct event listener."""

    if source is None or not listener_id:
        return
    try:
        await source.release_owner(listener_id=listener_id, lease_token=lease_token)
    except Exception:
        pass


async def run_live_external_event_listener_loop(
    *,
    source_getter: Callable[[], Any],
    runtime_ctx: RuntimeCtx,
    stop_event: asyncio.Event,
    listener_id: str,
    lease_token_getter: Callable[[], str],
    last_cursor_getter: Callable[[], str],
    apply_events: Callable[[Sequence[Any]], Awaitable[int]],
    log: Any,
    acknowledge: Optional[Callable[[], Awaitable[None]]] = None,
    on_owner_lost: Optional[Callable[[str, Any], None]] = None,
) -> None:
    """
    Drain the live ReAct external-event lane while the current turn owns it.

    This loop owns transport mechanics only: refresh the owner lease, read the
    Redis lane in order, and hand events to the supplied materializer. Event to
    block conversion remains outside this transport loop.
    """

    source = source_getter()
    if source is None:
        return
    while not stop_event.is_set():
        try:
            lease_token = str(lease_token_getter() or "")
            refreshed = await source.refresh_owner(
                listener_id=listener_id,
                turn_id=str(runtime_ctx.turn_id or ""),
                bundle_id=str(runtime_ctx.bundle_id or ""),
                lease_token=lease_token,
            )
            if refreshed is None:
                current_owner = None
                try:
                    current_owner = await source.get_owner()
                except Exception:
                    pass
                if on_owner_lost is not None:
                    on_owner_lost("owner_lease_refresh_rejected", current_owner)
                log.log("[timeline.external]: owner lease refresh rejected; stopping listener", "INFO")
                break
            current_owner = await source.get_owner()
            if current_owner is None or str(getattr(current_owner, "lease_token", "") or "") != lease_token:
                if on_owner_lost is not None:
                    on_owner_lost("owner_lease_lost", current_owner)
                log.log("[timeline.external]: owner lease lost; stopping listener", "INFO")
                break
            if acknowledge is not None:
                try:
                    await acknowledge()
                except ExternalEventLaneTurnSuperseded:
                    raise
                except Exception:
                    log.log(f"[timeline.external]: consumer acknowledgement callback failure {traceback.format_exc()}", "ERROR")
            last_cursor = ""
            try:
                last_cursor = str(last_cursor_getter() or "")
            except Exception:
                last_cursor = ""
            events = await source.wait_for_events_after(last_cursor, block_ms=3000, limit=100)
            if events:
                log.log(
                    f"[timeline.external]: listener received conversation={runtime_ctx.conversation_id} "
                    f"turn_id={runtime_ctx.turn_id} count={len(events)} last_cursor={last_cursor}",
                    "INFO",
                )
            await apply_events(events)
        except asyncio.CancelledError:
            raise
        except ExternalEventLaneTurnSuperseded:
            log.log("[timeline.external]: listener stopped because turn was superseded", "INFO")
            break
        except Exception:
            log.log(f"[timeline.external]: listener loop failure {traceback.format_exc()}", "ERROR")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
