from unittest.mock import AsyncMock

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting import lifecycle


@pytest.mark.asyncio
async def test_direct_host_process_lifespan_closes_resources(monkeypatch) -> None:
    close = AsyncMock()
    monkeypatch.setattr(lifecycle, "close_shared_browser", close)

    async with lifecycle.direct_host_process_lifespan():
        pass

    close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_direct_host_process_lifespan_closes_resources_after_error(
    monkeypatch,
) -> None:
    close = AsyncMock()
    monkeypatch.setattr(lifecycle, "close_shared_browser", close)

    with pytest.raises(RuntimeError, match="turn failed"):
        async with lifecycle.direct_host_process_lifespan():
            raise RuntimeError("turn failed")

    close.assert_awaited_once_with()
