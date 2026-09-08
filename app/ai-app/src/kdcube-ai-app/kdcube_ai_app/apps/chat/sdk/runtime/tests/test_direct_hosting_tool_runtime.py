from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting import tool_runtime as module
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.tool_runtime import (
    DirectToolRuntime,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.workspace import (
    DirectTurnWorkspace,
)
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import AgentToolConfig


class _ToolSubsystem:
    def __init__(self, **kwargs):
        self.comm = kwargs["comm"]
        self.bundle_root = Path(kwargs["bundle_spec"].path)
        self.prebind_for_in_memory = AsyncMock()
        self.bind_context_rag_client = Mock()


def _runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DirectToolRuntime:
    monkeypatch.setattr(module, "ToolSubsystem", _ToolSubsystem)
    return DirectToolRuntime(
        service=object(),
        comm=SimpleNamespace(),
        workspace=DirectTurnWorkspace(tmp_path, "turn_demo"),
        exec_runtime={"mode": "docker", "image": "exec:test"},
        bundle_id="example@1-0",
        bundle_root=tmp_path,
        bundle_module="agent",
    )


def test_direct_turn_workspace_uses_the_canonical_artifact_layout(
    tmp_path: Path,
) -> None:
    workspace = DirectTurnWorkspace(tmp_path, "turn_demo")

    assert workspace.current_file("research/data.xlsx") == (
        tmp_path
        / "turn_demo"
        / "out"
        / "workdir"
        / "turn_demo"
        / "files"
        / "research"
        / "data.xlsx"
    )
    assert workspace.current_attachment("request.md") == (
        tmp_path
        / "turn_demo"
        / "out"
        / "workdir"
        / "turn_demo"
        / "attachments"
        / "request.md"
    )
    with pytest.raises(ValueError, match="canonical turn_"):
        DirectTurnWorkspace(tmp_path, "turn-demo")


@pytest.mark.asyncio
async def test_runtime_lazily_binds_and_closes_its_conversation_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "ToolSubsystem", _ToolSubsystem)
    client = object()
    conversation_harness = SimpleNamespace(
        open=AsyncMock(),
        close=AsyncMock(),
        conversation_client=client,
    )
    runtime = DirectToolRuntime(
        service=object(),
        comm=SimpleNamespace(),
        workspace=DirectTurnWorkspace(tmp_path, "turn_demo"),
        exec_runtime={"mode": "docker", "image": "exec:test"},
        bundle_id="example@1-0",
        bundle_root=tmp_path,
        bundle_module="agent",
        conversation_harness=conversation_harness,
    )

    await runtime.prepare()
    await runtime.prepare()
    await runtime.close()
    await runtime.close()

    conversation_harness.open.assert_awaited_once()
    runtime.tool_subsystem.bind_context_rag_client.assert_called_once_with(client)
    conversation_harness.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_does_not_close_a_harness_when_client_is_already_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "ToolSubsystem", _ToolSubsystem)
    conversation_harness = SimpleNamespace(
        open=AsyncMock(),
        close=AsyncMock(),
        conversation_client=object(),
    )
    runtime = DirectToolRuntime(
        service=object(),
        comm=SimpleNamespace(),
        workspace=DirectTurnWorkspace(tmp_path, "turn_demo"),
        exec_runtime={"mode": "docker", "image": "exec:test"},
        bundle_id="example@1-0",
        bundle_root=tmp_path,
        bundle_module="agent",
        context_rag_client=object(),
        conversation_harness=conversation_harness,
    )

    await runtime.prepare()
    await runtime.close()

    conversation_harness.open.assert_not_awaited()
    conversation_harness.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_descriptor_selected_module_is_discovered_and_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_module = tmp_path / "market_tools.py"
    tool_module.write_text(
        """
class MarketTools:
    async def latest_prices(self, symbol: str):
        return {"symbol": symbol, "price": 42}

    async def delete_supplier(self, supplier_id: str):
        return {"deleted": supplier_id}

tools = MarketTools()

def list_tools():
    return {
        "latest_prices": {
            "callable": tools.latest_prices,
            "description": "Read current prices.",
        },
        "delete_supplier": {
            "callable": tools.delete_supplier,
            "description": "Delete a supplier.",
        },
    }
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    tool_config = AgentToolConfig(
        tool_specs=[
            {
                "module": "market_tools",
                "alias": "market_tools",
                "use_sk": False,
            }
        ],
        tool_runtime={"market_tools.latest_prices": "local"},
        allowed_plugins=["market_tools"],
        allowed_tool_names_by_alias={"market_tools": ["latest_prices"]},
    )
    runtime = DirectToolRuntime(
        service=object(),
        comm=SimpleNamespace(),
        workspace=DirectTurnWorkspace(tmp_path / "run", "turn_demo"),
        exec_runtime={"mode": "docker", "image": "exec:test"},
        bundle_id="example@1-0",
        bundle_root=tmp_path,
        bundle_module="agent",
        tool_config=tool_config,
    )
    runtime.prepare = AsyncMock()

    async def invoke(*, fn, params, **_kwargs):
        return await fn(**params)

    monkeypatch.setattr(module.agent_io_tools, "tool_call", invoke)

    assert runtime.configured_tool_ids() == ("market_tools.latest_prices",)
    exported = runtime.tool_subsystem.export_runtime_globals()
    assert exported["ALLOWED_TOOL_NAMES_BY_ALIAS"] == {
        "market_tools": ["latest_prices"]
    }
    assert exported["RAW_TOOL_SPECS"] == [
        {
            "module": "market_tools",
            "alias": "market_tools",
            "use_sk": False,
        }
    ]
    assert await runtime.invoke_tool(
        tool_id="market_tools.latest_prices",
        params={"symbol": "KDC"},
        call_reason="Read the current price",
    ) == {"symbol": "KDC", "price": 42}
    with pytest.raises(ValueError, match="not allowed"):
        await runtime.invoke_tool(
            tool_id="market_tools.delete_supplier",
            params={"supplier_id": "supplier-1"},
            call_reason="Delete one supplier",
        )


@pytest.mark.asyncio
async def test_execute_python_normalizes_paths_and_reports_generated_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    execute = AsyncMock(return_value={"ok": True, "items": [], "report_text": "done"})
    monkeypatch.setattr(module, "run_exec_tool", execute)

    result = await runtime.execute_python(
        code="from pathlib import Path\nPath('files/report.txt').write_text('done')",
        artifacts=[
            {
                "filepath": "files/report.txt",
                "description": "Generated report",
                "visibility": "external",
            }
        ],
        program_name="Report program",
    )

    assert result["ok"] is True
    assert result["generated_source"]["archive_path"] == "pkg/user_code.py"
    assert result["path_rewrites"]["contract"] == [
        {
            "original": "files/report.txt",
            "rewritten": "turn_demo/files/report.txt",
        }
    ]
    call = execute.await_args.kwargs
    assert "turn_demo/files/report.txt" in call["code"]
    assert call["contract"][0]["filepath"] == "turn_demo/files/report.txt"
    runtime.tool_subsystem.prebind_for_in_memory.assert_awaited_once()


@pytest.mark.asyncio
async def test_renderer_reads_current_turn_source_and_writes_current_turn_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    source = runtime.workspace.current_file("research/brief.html")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("<html><body><h1>Brief</h1></body></html>", encoding="utf-8")

    from kdcube_ai_app.apps.chat.sdk.runtime.workdir_discovery import resolve_output_dir
    from kdcube_ai_app.apps.chat.sdk.tools import rendering_tools

    async def render_pdf(*, path: str, content: str, **_kwargs):
        assert "<h1>Brief</h1>" in content
        output = resolve_output_dir() / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-direct-runtime")
        return {"ok": True, "error": None}

    monkeypatch.setattr(rendering_tools.tools, "write_pdf", render_pdf)

    result = await runtime.write_pdf(
        source_path="files/research/brief.html",
        output_path="files/research/brief.pdf",
        title="Research brief",
    )

    assert result["ok"] is True
    assert result["source_path"] == "turn_demo/files/research/brief.html"
    assert result["output_path"] == "turn_demo/files/research/brief.pdf"
    assert (
        runtime.workspace.current_file("research/brief.pdf")
        .read_bytes()
        .startswith(b"%PDF-")
    )


@pytest.mark.asyncio
async def test_renderer_enforces_source_format_per_document_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path, monkeypatch)

    result = await runtime.write_docx(
        source_path="files/research/brief.html",
        output_path="files/research/brief.docx",
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_render_path"
    assert "Markdown" not in result["error"]["message"]
    assert ".md" in result["error"]["message"]
