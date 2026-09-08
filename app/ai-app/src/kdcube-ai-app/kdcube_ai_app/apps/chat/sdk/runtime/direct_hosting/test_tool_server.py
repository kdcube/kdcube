from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting import tool_server
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.tool_server import build_app


@pytest.mark.asyncio
async def test_execute_python_schema_teaches_async_module_contract() -> None:
    app = build_app(SimpleNamespace())

    tools = {tool.name: tool for tool in await app.list_tools()}
    execute = tools["execute_python"]

    assert "asynchronous module body" in execute.description
    assert "never call asyncio.run()" in execute.description
    assert "Path(OUTPUT_DIR)" in execute.description
    assert "OUTPUT_DIR is injected by the runtime" in execute.description
    code_schema = execute.input_schema["properties"]["code"]
    assert "top-level await is supported" in code_schema["description"]
    artifact_schema = execute.input_schema["$defs"]["DirectArtifactContract"]
    assert artifact_schema["required"] == [
        "filepath",
        "description",
        "visibility",
    ]
    assert set(artifact_schema["properties"]) == {
        "filepath",
        "description",
        "visibility",
    }
    assert artifact_schema["properties"]["visibility"]["enum"] == [
        "internal",
        "external",
    ]


@pytest.mark.asyncio
async def test_tool_server_lifespan_delegates_process_cleanup(monkeypatch) -> None:
    entered = Mock()

    @asynccontextmanager
    async def process_lifespan():
        entered()
        yield

    monkeypatch.setattr(tool_server, "direct_host_process_lifespan", process_lifespan)

    async with tool_server.tool_server_lifespan(None):
        entered.assert_called_once_with()

    entered.assert_called_once_with()
