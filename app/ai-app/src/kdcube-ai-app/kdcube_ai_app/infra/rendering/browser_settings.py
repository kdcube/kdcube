# SPDX-License-Identifier: MIT

# infra/rendering/browser_settings.py

"""Descriptor-owned settings for the Chromium that rendering and browser tools use.

``assembly.yaml``::

    platform:
      rendering:
        browser:
          ws_endpoint: ""          # remote Playwright server; empty = launch locally
          idle_disconnect_s: 0     # > 0: drop an idle browser after this many seconds

Every field is optional; an absent section keeps the local-launch behaviour.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

BROWSER_SETTINGS_PATH = "platform.rendering.browser"


@dataclass(frozen=True)
class BrowserSettings:
    ws_endpoint: Optional[str] = None
    idle_disconnect_s: Optional[float] = None


def _read(field: str) -> Any:
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_plain

        return get_plain(f"a:{BROWSER_SETTINGS_PATH}.{field}", None)
    except Exception:
        # A process without platform settings (an isolated executor, a bare
        # script) has no assembly descriptor: the defaults apply.
        logger.debug("assembly %s.%s unavailable", BROWSER_SETTINGS_PATH, field, exc_info=True)
        return None


def _ws_endpoint(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    if not text.lower().startswith(("ws://", "wss://")):
        logger.warning("assembly %s.ws_endpoint=%r is not a ws:// or wss:// URL; ignored", BROWSER_SETTINGS_PATH, raw)
        return None
    return text


def _idle_disconnect_s(raw: Any) -> Optional[float]:
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        logger.warning("assembly %s.idle_disconnect_s=%r is not a number; ignored", BROWSER_SETTINGS_PATH, raw)
        return None
    return seconds if seconds > 0 else None


def browser_settings() -> BrowserSettings:
    """Current browser settings; re-read on each call so a descriptor edit applies."""
    return BrowserSettings(
        ws_endpoint=_ws_endpoint(_read("ws_endpoint")),
        idle_disconnect_s=_idle_disconnect_s(_read("idle_disconnect_s")),
    )
