"""Process-lifetime cleanup for directly hosted agents."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from kdcube_ai_app.infra.rendering.shared_browser import close_shared_browser


@asynccontextmanager
async def direct_host_process_lifespan() -> AsyncIterator[None]:
    """Close process-wide SDK resources before the owning event loop exits."""
    try:
        yield
    finally:
        await close_shared_browser()


__all__ = ["direct_host_process_lifespan"]
