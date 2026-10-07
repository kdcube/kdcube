# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Cleanup of portable credentials withheld after a known Card refusal."""
from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger("kdcube.connection_hub.oauth")


async def revoke_withheld_oauth_credentials(
    store: Any, *, access_token: str, refresh_token: str | None, client_id: str,
) -> None:
    """Attempt both revocations independently, preserving the original refusal.

    A backing-store failure is logged without credential or exception text.
    Cancellation propagates; no successful cleanup is claimed for a failed call.
    This revokes portable grants/families, not a platform-login session.
    """
    for kind, method, token in (
        ("access", "revoke_access_grant", access_token),
        ("refresh", "revoke_refresh_token", refresh_token),
    ):
        if not token:
            continue
        try:
            await getattr(store, method)(token)
        except Exception:
            LOGGER.error(
                "[connection-hub.oauth] withheld credential cleanup unavailable "
                "kind=%s client=%s", kind, client_id,
            )
