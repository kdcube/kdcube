# SPDX-License-Identifier: MIT

"""Finish conversation deletions that were recorded but did not complete.

A deletion that fails after its ledger row is written leaves the row open:
the conversation stays hidden and frozen until the deletion completes. This
runs those deletions again with their recorded scope. It never waits for the
retention lock (a deletion a person is waiting for goes first) and stops the
run when the lock is held; it holds the conversation's lock while it deletes,
so no turn of that conversation runs meanwhile.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_retention import (
    ConversationDeleteBusy,
    ConversationDeleteIncomplete,
    ConversationDeleteUnavailable,
)
from kdcube_ai_app.apps.chat.sdk.runtime.conversation_lock import (
    ConversationLockHeld,
    conversation_lock_key,
    hold_conversation_lock,
)

logger = logging.getLogger(__name__)

RESUME_ACTOR = "system:conversation-deletion-resume"
WARN_AFTER_ATTEMPTS = 5


async def finish_unfinished_conversation_deletions(
    *,
    ctx_client: Any,
    redis: Any,
    tenant: str,
    project: str,
    limit: int = 20,
    warn_after: int = WARN_AFTER_ATTEMPTS,
) -> Dict[str, int]:
    """Finish up to ``limit`` unfinished conversation deletions, oldest first; returns counts."""
    summary = {"found": 0, "finished": 0, "incomplete": 0, "turn_running": 0, "stopped": 0}
    retention = ctx_client.idx.retention(store=ctx_client.store)
    if retention is None:
        logger.warning("[conversation-deletion-resume] cold tier unavailable; nothing finished")
        summary["stopped"] = 1
        return summary
    pending = await retention.unfinished_conversation_deletions(limit=limit)
    summary["found"] = len(pending)
    for item in pending:
        user_id, conversation_id = item["user_id"], item["conversation_id"]
        try:
            async with hold_conversation_lock(
                redis,
                conversation_lock_key(tenant=tenant, project=project, user_id=user_id,
                                      conversation_id=conversation_id),
                owner="conversation-deletion-resume",
            ):
                await ctx_client.delete_conversation(
                    tenant=tenant, project=project, user_id=user_id, conversation_id=conversation_id,
                    # The stored files are found under every user type.
                    user_type="registered",
                    bundle_id=item.get("bundle_id"), bundle_ids=item.get("bundle_ids"),
                    actor=RESUME_ACTOR, reason="finish an unfinished deletion", lock_wait_s=0,
                )
            summary["finished"] += 1
        except ConversationLockHeld:
            # A turn runs, or a person is deleting it right now.
            summary["turn_running"] += 1
        except ConversationDeleteIncomplete as exc:
            summary["incomplete"] += 1
            attempts = int(item.get("attempts") or 0) + 1
            log = logger.warning if attempts >= warn_after else logger.info
            log("[conversation-deletion-resume] deletion of conversation=%s user=%s did not finish "
                "(attempt %s, deletion %s): %s", conversation_id, user_id, attempts, exc.deletion_id, exc)
        except (ConversationDeleteBusy, ConversationDeleteUnavailable) as exc:
            # The retention lock is held (a deletion or an archive step) or the
            # cold tier is gone: stop, the next run continues.
            logger.info("[conversation-deletion-resume] stopped: %s", exc)
            summary["stopped"] = 1
            break
    return summary


__all__ = ["RESUME_ACTOR", "finish_unfinished_conversation_deletions"]
