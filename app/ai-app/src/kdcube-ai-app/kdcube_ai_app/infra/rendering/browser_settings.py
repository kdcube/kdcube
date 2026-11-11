# SPDX-License-Identifier: MIT

# infra/rendering/browser_settings.py

"""Descriptor-owned settings for the Chromium that rendering and browser tools use.

``assembly.yaml``::

    platform:
      rendering:
        browser:
          ws_endpoint: ""          # remote Playwright server; empty = launch locally
          idle_disconnect_s: 0     # > 0: drop an idle browser after this many seconds
          local_chromium: true     # false: never launch or install Chromium here

Every field is optional; an absent section keeps the local-launch behaviour.
``local_chromium: false`` also builds the processor and executor images
without Chromium (the CLI passes ``INSTALL_CHROMIUM=0``).
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
    local_chromium: bool = True


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


def _local_chromium(raw: Any) -> bool:
    if raw is None or raw == "":
        return True
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"0", "false", "no", "off"}:
        return False
    if text not in {"1", "true", "yes", "on"}:
        logger.warning("assembly %s.local_chromium=%r is not a boolean; keeping true", BROWSER_SETTINGS_PATH, raw)
    return True


def browser_settings() -> BrowserSettings:
    """Current browser settings; re-read on each call so a descriptor edit applies."""
    return BrowserSettings(
        ws_endpoint=_ws_endpoint(_read("ws_endpoint")),
        idle_disconnect_s=_idle_disconnect_s(_read("idle_disconnect_s")),
        local_chromium=_local_chromium(_read("local_chromium")),
    )


def no_local_browser_error(ws_endpoint: Optional[str]) -> RuntimeError:
    """The error for a render that needs a browser this process may not launch."""
    where = f"the remote Playwright server at {ws_endpoint} is unreachable" if ws_endpoint else (
        f"no remote Playwright server is configured ({BROWSER_SETTINGS_PATH}.ws_endpoint)"
    )
    return RuntimeError(
        f"No browser available for rendering: {BROWSER_SETTINGS_PATH}.local_chromium is false "
        f"and {where}."
    )
