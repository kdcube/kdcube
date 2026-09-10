from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml


REPO_ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "AGENTS.md").is_file() and (parent / "agents").is_dir()
)
AGENTS_ROOT = REPO_ROOT / "agents"
ROOT_README = REPO_ROOT / "README.md"
QUICK_START = REPO_ROOT / "app" / "ai-app" / "docs" / "quick-start-README.md"
MCP_CATALOG = REPO_ROOT / "mcp" / "README.md"
WEB_SEARCH_QUICK_START = REPO_ROOT / "mcp" / "web-search" / "README.md"
DIRECT_RECIPE = (
    REPO_ROOT
    / "app"
    / "ai-app"
    / "docs"
    / "recipes"
    / "quickstart"
    / "run-agent-harness-from-python-README.md"
)
ADAPTERS = ("native", "langgraph", "claude")
README_SECTIONS = (
    "## What it is",
    "## Run it",
    "## What the demo shows",
    "## Change the demo",
)


def test_primary_agents_are_visible_at_the_agents_root() -> None:
    top_level_directories = {
        path.name
        for path in AGENTS_ROOT.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert top_level_directories == set(ADAPTERS)


def test_agents_root_explains_the_demonstration_and_why_to_run_it() -> None:
    source = (AGENTS_ROOT / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "# Build and Run On-Premises Agents with the KDCube Harness" in source
    assert len(source.splitlines()) <= 200
    assert "without starting a KDCube server" in normalized
    assert "normal Python processes" in normalized
    assert "[Native agent](native/README.md)" in source
    assert "[LangGraph](langgraph/README.md)" in source
    assert "[Claude Code](claude/README.md)" in source
    assert "**Your agent**" in source
    assert "an isolated turn workspace and isolated code-execution sandbox" in normalized
    assert "durable conversations and search across earlier conversations" in normalized
    assert "local or S3 storage" in normalized
    assert "streamed activity, model/tool usage, cost" in normalized
    assert "infrastructure you control" in source
    assert "A running KDCube deployment is not a prerequisite" in normalized
    assert "## Try the Native agent" in source
    assert "setup_local.py --provider anthropic" in source
    assert "docker compose --env-file .env" in source
    assert ".venv/bin/python -m playwright install chromium" in source
    assert "py-code-exec:latest" in source
    assert ".venv/bin/python agent.py --infra-check" in source
    assert "Web Search tool" in source
    assert "isolated code-execution tool creates XLSX + HTML" in source
    assert "PDF rendering tool" in source
    assert "another conversation searches and recovers" in source
    assert "## Run the automatic demonstration" in source
    assert "This command needs no input" in source
    assert "prints `demonstration: PASS`, and exits" in normalized
    assert "research-data.xlsx" in source
    assert "evidence.json" in source
    assert "## Talk to the agent" in source
    assert "This command does not run the automatic demonstration" in normalized
    assert "Each Telegram chat becomes a durable conversation" in normalized
    assert "sends its answer and declared external files back" in normalized
    assert "keeps listening until `Ctrl-C`" in normalized
    assert "config.local.yaml" in source
    assert "descriptors.local/assembly.yaml" in source
    assert "--conversation-id first-research" in source
    assert "step-by-step executable recipe" in normalized
    assert "DirectAgentHarness" not in source
    assert "ToolSubsystem" not in source


def test_conversation_search_doc_leads_with_user_value() -> None:
    source = (
        REPO_ROOT
        / "app"
        / "ai-app"
        / "docs"
        / "sdk"
        / "solutions"
        / "conversation"
        / "search-README.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(source.split())
    body = source.split("---", 2)[2]

    assert "recover useful work" in normalized
    assert "continue last week's supplier comparison" in normalized
    assert "an agent can resume work from another conversation" in normalized
    assert "run_conversation_search" in source
    assert body.index("recover useful work") < body.index("run_conversation_search")


def test_repo_entry_points_name_the_agent_harness_value() -> None:
    root = ROOT_README.read_text(encoding="utf-8")
    quick_start = QUICK_START.read_text(encoding="utf-8")

    for source in (root, quick_start):
        normalized = " ".join(source.split())
        assert "Build and run on-premises agents with the KDCube Harness" in normalized
        assert "durable conversations" in normalized
        assert "isolated code" in normalized
        assert "rendering" in normalized
        assert "accounting" in normalized


def test_primary_onboarding_links_agents_and_web_search_directly() -> None:
    root = ROOT_README.read_text(encoding="utf-8")
    quick_start = QUICK_START.read_text(encoding="utf-8")
    agents = (AGENTS_ROOT / "README.md").read_text(encoding="utf-8")
    mcp_catalog = MCP_CATALOG.read_text(encoding="utf-8")

    assert "(agents/README.md)" in root
    assert (
        "(app/ai-app/docs/recipes/quickstart/run-agent-harness-from-python-README.md)"
        in root
    )
    assert "(mcp/web-search/README.md)" in root
    assert "(../../../agents/README.md)" in quick_start
    assert "(recipes/quickstart/run-agent-harness-from-python-README.md)" in quick_start
    assert "(../../../mcp/web-search/README.md)" in quick_start
    assert "(../mcp/web-search/README.md)" in agents
    assert "(web-search/README.md)" in mcp_catalog
    assert DIRECT_RECIPE.is_file()
    assert WEB_SEARCH_QUICK_START.is_file()


def test_every_agents_readme_answers_the_four_first_use_questions() -> None:
    readmes = sorted(AGENTS_ROOT / adapter / "README.md" for adapter in ADAPTERS)
    assert readmes
    for path in readmes:
        source = path.read_text(encoding="utf-8")
        positions = [source.find(heading) for heading in README_SECTIONS]
        assert all(position >= 0 for position in positions), path
        assert positions == sorted(positions), path
        assert "python3.11 -m venv .venv" in source, path
        assert ".venv/bin/python -m pip install -r requirements.txt" in source, path
        assert ".venv/bin/python -m playwright install chromium" in source, path
        assert "Dockerfile_Exec" in source, path
        assert ".venv/bin/python agent.py --infra-check" in source, path
        assert ".venv/bin/python agent.py --interactive" in source, path
        assert ".venv/bin/python agent.py --telegram-local" in source, path


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_each_example_owns_its_runnable_contract(adapter: str) -> None:
    root = AGENTS_ROOT / adapter
    expected = {
        "README.md",
        "agent.py",
        "compose.yaml",
        "config.template.yaml",
        "setup_local.py",
        "descriptors.template",
        "requirements.txt",
        "skills",
    }
    assert expected.issubset({path.name for path in root.iterdir()})
    assert not (root / "web-search.yaml").exists()
    if adapter == "native":
        assert not (root / "tool_plan.py").exists()
        assert not (root / "configuration.py").exists()

    config = yaml.safe_load((root / "config.template.yaml").read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    assert "output" not in config
    assert "web_search" not in config
    assert "infra" not in config
    agent = config["agent"]
    agent_input = agent["input"]
    assert agent_input["user_id"] == "demo-user"
    assert agent_input["user_type"] == "regular"
    assert agent_input["session_id"] == "local-session"
    assert agent_input["conversation_id"] == f"{adapter}-demo"
    assert agent_input["recall_conversation_id"] == f"{adapter}-recall-demo"
    if adapter == "native":
        assert agent["max_tokens"] == 80000
    assert agent["instructions"]["profile"] == (
        "lite:core" if adapter == "native" else "workspace-files"
    )
    assert str(agent.get("additional_instructions") or "").strip()
    assert agent["run_directory"] == "./output"
    telegram = agent["ingress"]["telegram"]
    assert telegram == {
        "host": "127.0.0.1",
        "port": 8787,
        "path": "/telegram/webhook",
        "bot_token_ref": "platform.services.telegram.bot_token",
        "webhook_secret_ref": "platform.services.telegram.webhook_secret",
    }
    assert isinstance(agent.get("tools"), list)
    assert agent["tools"]
    assert all(str(item.get("id") or "").strip() for item in agent["tools"])
    configured_by_id = {item["id"]: item for item in agent["tools"]}
    expected_sources = {"web", "code", "documents"}
    if adapter != "native":
        expected_sources.add("conversations")
    assert set(configured_by_id) == expected_sources
    assert configured_by_id["web"]["module"].endswith(".web_tools")
    assert configured_by_id["web"]["alias"] == "web_tools"
    assert configured_by_id["web"]["allowed"] == ["web_search", "web_fetch"]
    assert configured_by_id["web"]["runtime"] == {
        "web_search": "local",
        "web_fetch": "local",
    }
    assert configured_by_id["code"]["module"].endswith(".exec_tools")
    assert configured_by_id["code"]["allowed"] == ["execute_code_python"]
    assert configured_by_id["code"]["runtime"] == {"execute_code_python": "docker"}
    if adapter != "native":
        assert configured_by_id["conversations"]["module"].endswith(
            ".conversation_tools"
        )
        assert configured_by_id["conversations"]["alias"] == "conversation_tools"
        assert configured_by_id["conversations"]["allowed"] == ["search"]
        assert configured_by_id["conversations"]["runtime"] == {"search": "local"}
    assert configured_by_id["documents"]["module"].endswith(".rendering_tools")
    assert set(configured_by_id["documents"]["allowed"]) == {
        "write_pdf",
        "write_docx",
        "write_pptx",
    }
    assert agent.get("skills", {}).get("enabled") == ["demo.research-brief"]
    web_policy = configured_by_id["web"]["settings"]
    assert web_policy["filter"]["allowlist"] == ["python.org"]
    assert web_policy["filter"]["blocklist"] == []
    assert web_policy["filter"]["ssrf_guard"] is True
    if adapter == "claude":
        assert "model" not in agent["adapter"]
        assert agent["adapter"]["allowed_tools"] == [
            "Read",
            "Write",
            "Edit",
            "Grep",
            "Glob",
        ]
    else:
        assert "adapter" not in agent

    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    assert "../../app/ai-app/src/kdcube-ai-app" in requirements
    assert "sdk/tools/mcp/web_search/requirements.txt" in requirements
    assert "playwright" in requirements
    assert "fastapi" in requirements
    assert "uvicorn" in requirements
    assert "python-docx" in requirements
    assert "python-pptx" in requirements

    secrets = yaml.safe_load(
        (root / "descriptors.template" / "secrets.yaml").read_text(encoding="utf-8")
    )
    assert secrets["platform"]["services"]["brave"] == {"api_key": None}
    assert secrets["platform"]["services"]["telegram"] == {
        "bot_token": None,
        "webhook_secret": None,
    }
    assembly = yaml.safe_load(
        (root / "descriptors.template" / "assembly.yaml").read_text(encoding="utf-8")
    )
    web_backend = assembly["platform"]["services"]["proc"]["tools"]["web_search"]
    assert web_backend == {
        "web_search_primary_backend": "duckduckgo",
        "web_search_backend": "duckduckgo",
    }
    assert assembly["paths"]["host_bundle_storage_path"] == (
        "../output/bundle-storage"
    )
    assert assembly["platform"]["services"]["proc"]["bundles"] == {
        "bundle_storage_root": "../output/bundle-storage"
    }
    assert (
        assembly["platform"]["services"]["proc"]["exec"][
            "py_code_exec_network_mode"
        ]
        == "auto"
    )


def test_every_adapter_uses_kdcube_web_search_and_fetch() -> None:
    native_source = (AGENTS_ROOT / "native" / "agent.py").read_text(encoding="utf-8")
    langgraph_tools = (AGENTS_ROOT / "langgraph" / "tools.py").read_text(
        encoding="utf-8"
    )
    langgraph_source = (AGENTS_ROOT / "langgraph" / "agent.py").read_text(
        encoding="utf-8"
    )
    claude_config = yaml.safe_load(
        (AGENTS_ROOT / "claude" / "config.template.yaml").read_text(encoding="utf-8")
    )
    claude_source = (AGENTS_ROOT / "claude" / "agent.py").read_text(encoding="utf-8")

    assert not (AGENTS_ROOT / "native" / "tools.py").exists()
    assert "web_search_server.web_search(" not in langgraph_tools
    assert "web_search_server.web_fetch(" not in langgraph_tools
    assert "require_runtime().invoke_tool(" in langgraph_tools

    claude_sources = {item["id"]: item for item in claude_config["agent"]["tools"]}
    assert claude_sources["web"]["module"].endswith(".web_tools")
    assert claude_sources["web"]["allowed"] == ["web_search", "web_fetch"]
    assert "WebSearch" not in claude_config["agent"]["adapter"]["allowed_tools"]
    assert "WebFetch" not in claude_config["agent"]["adapter"]["allowed_tools"]
    assert "Bash" not in claude_config["agent"]["adapter"]["allowed_tools"]
    assert 'REPO_ROOT / "mcp" / "web-search" / "server.py"' in claude_source
    assert 'denied_tools=("WebSearch", "WebFetch", "Bash")' in claude_source
    for source in (native_source, langgraph_source, claude_source):
        assert "agent_io_tools.tool_call(fn=web_tools.web_search" in source
        assert "Do not import `web_tools`" in source
        assert "The result is an envelope with `ok` and `ret`" in source
        assert "xlsx_contains_tool_evidence" in source


def test_every_adapter_exposes_cross_conversation_search() -> None:
    native = (AGENTS_ROOT / "native" / "agent.py").read_text(encoding="utf-8")
    langgraph = (AGENTS_ROOT / "langgraph" / "agent.py").read_text(
        encoding="utf-8"
    )
    langgraph_tools = (AGENTS_ROOT / "langgraph" / "tools.py").read_text(
        encoding="utf-8"
    )
    claude = (AGENTS_ROOT / "claude" / "agent.py").read_text(encoding="utf-8")

    assert '"react.memsearch"' in native
    assert '"conversation_tools.search"' in langgraph_tools
    assert '"conversation_tools.search"' in claude
    for source in (langgraph, claude):
        assert "conversation_search_tool=" in source
        assert "recall_conversation_id" in source

    for adapter in ("langgraph", "claude"):
        config = yaml.safe_load(
            (AGENTS_ROOT / adapter / "config.template.yaml").read_text(
                encoding="utf-8"
            )
        )
        tools = {row["id"]: row for row in config["agent"]["tools"]}
        assert tools["conversations"] == {
            "id": "conversations",
            "kind": "python",
            "module": "kdcube_ai_app.apps.chat.sdk.tools.conversation_tools",
            "alias": "conversation_tools",
            "discovery": "semantic_kernel",
            "allowed": ["search"],
            "runtime": {"search": "local"},
        }


@pytest.mark.asyncio
async def test_langgraph_web_search_adapter_calls_kdcube_tool() -> None:
    from agents.langgraph import tools as langgraph_tools

    runtime = SimpleNamespace(
        invoke_tool=AsyncMock(
            return_value={
                "ok": True,
                "ret": [
                    {
                        "title": "Python",
                        "text": "Current release",
                        "url": "https://python.org/",
                    }
                ],
            }
        )
    )
    tools = langgraph_tools.build_tools(
        runtime,
        configured_ids={"web_tools.web_search"},
    )

    result = json.loads(
        await tools[0].ainvoke(
            {
                "queries": "current Python release",
                "objective": "verify the current release",
                "n": 2,
                "fetch_content": False,
                "use_llm": False,
            }
        )
    )

    assert result["ok"] is True
    assert result["results"][0]["url"] == "https://python.org/"
    runtime.invoke_tool.assert_awaited_once_with(
        tool_id="web_tools.web_search",
        params={
            "queries": "current Python release",
            "objective": "verify the current release",
            "n": 2,
            "fetch_content": False,
            "use_llm": False,
        },
        call_reason="Search the public web",
    )


@pytest.mark.asyncio
async def test_langgraph_web_fetch_adapter_calls_kdcube_tool() -> None:
    from agents.langgraph import tools as langgraph_tools

    runtime = SimpleNamespace(
        invoke_tool=AsyncMock(
            return_value={
                "ok": True,
                "ret": [{"url": "https://python.org/", "text": "Python"}],
            }
        )
    )
    tools = langgraph_tools.build_tools(
        runtime,
        configured_ids={"web_tools.web_fetch"},
    )

    result = json.loads(
        await tools[0].ainvoke(
            {
                "urls": ["https://python.org/"],
                "objective": "verify release",
                "refinement": "none",
            }
        )
    )

    assert result["ok"] is True
    assert result["ret"][0]["text"] == "Python"
    runtime.invoke_tool.assert_awaited_once_with(
        tool_id="web_tools.web_fetch",
        params={
            "urls": ["https://python.org/"],
            "objective": "verify release",
            "refinement": "none",
        },
        call_reason="Fetch a selected web source",
    )


@pytest.mark.asyncio
async def test_langgraph_conversation_search_adapter_calls_kdcube_tool() -> None:
    from agents.langgraph import tools as langgraph_tools

    runtime = SimpleNamespace(
        invoke_tool=AsyncMock(
            return_value={
                "ok": True,
                "error": None,
                "ret": {
                    "items": [
                        {
                            "ref": "conv:turn:turn-earlier",
                            "body": {"conversation_id": "conversation-earlier"},
                        }
                    ]
                },
            }
        )
    )
    tools = langgraph_tools.build_tools(
        runtime,
        configured_ids={"conversation_tools.search"},
    )

    result = json.loads(
        await tools[0].ainvoke(
            {
                "query": "earlier source",
                "scope": "user",
                "targets": ["assistant", "summary"],
                "top_k": 3,
                "days": 30,
                "include_recovery_sessions": False,
            }
        )
    )

    assert result["ok"] is True
    assert result["ret"]["items"][0]["body"]["conversation_id"] == (
        "conversation-earlier"
    )
    runtime.invoke_tool.assert_awaited_once_with(
        tool_id="conversation_tools.search",
        params={
            "query": "earlier source",
            "scope": "user",
            "targets": ["assistant", "summary"],
            "top_k": 3,
            "days": 30,
            "include_recovery_sessions": False,
        },
        call_reason="Search this user's conversation history",
    )


@pytest.mark.asyncio
async def test_langgraph_stream_records_search_and_fetch_calls() -> None:
    from agents.langgraph.agent import stream_turn

    class Graph:
        async def astream_events(self, *_args, **_kwargs):
            for name in ("web_search", "web_fetch"):
                yield {
                    "event": "on_tool_start",
                    "name": name,
                    "run_id": f"{name}-run",
                    "data": {"input": {}},
                }

        async def aget_state(self, _config):
            return SimpleNamespace(
                values={"messages": [SimpleNamespace(content="research complete")]}
            )

    comm = SimpleNamespace(
        start=AsyncMock(),
        step=AsyncMock(),
        delta=AsyncMock(),
    )

    answer, called_tools = await stream_turn(Graph(), "research", {}, comm)

    assert answer == "research complete"
    assert called_tools == {"web_search", "web_fetch"}


@pytest.mark.asyncio
async def test_public_web_search_mcp_launcher_starts_with_operator_policy() -> None:
    from kdcube_ai_app.apps.chat.sdk.runtime.mcp.client import (
        normalize_mcp_tool_result,
        open_mcp_client,
    )

    launcher = REPO_ROOT / "mcp" / "web-search" / "server.py"
    config = (AGENTS_ROOT / "claude" / "config.template.yaml").resolve()
    async with open_mcp_client(
        transport="stdio",
        command=sys.executable,
        args=[
            str(launcher),
            "--transport",
            "stdio",
            "--config",
            str(config),
            "--tool-id",
            "web",
        ],
        env=os.environ.copy(),
        read_timeout_seconds=20,
    ) as client:
        listed = await client.list_tools()
        status = normalize_mcp_tool_result(
            await client.call_tool("allowlist_status", {})
        )["result"]

    assert {tool.name for tool in listed.tools} == {
        "allowlist_status",
        "web_fetch",
        "web_search",
    }
    assert status["allowlist_entries"] == ["python.org"]
    assert status["ssrf_guard"] is True
    assert status["enforced"] is True


def test_claude_demo_recognizes_search_and_fetch_calls() -> None:
    from agents.claude.agent import _called_tool_names

    events = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "search-1",
                            "name": "mcp__kdcube_web_search__web_search",
                            "input": {"queries": "Python release"},
                        },
                        {
                            "type": "tool_use",
                            "id": "fetch-1",
                            "name": "mcp__kdcube_web_search__web_fetch",
                            "input": {"urls": "https://python.org/"},
                        },
                    ]
                },
            }
        ),
        "not-json",
    ]

    assert _called_tool_names(events) == {
        "mcp__kdcube_web_search__web_search",
        "mcp__kdcube_web_search__web_fetch",
    }


@pytest.mark.asyncio
async def test_claude_cli_credentials_keep_descriptor_fields_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.claude import agent as claude_agent

    values = {
        "platform.services.anthropic.api_key": "anthropic-api-key",
        "platform.services.anthropic.claude_code_key": "claude-code-key",
    }

    async def get_secret(ref: str) -> str | None:
        return values.get(ref)

    monkeypatch.setattr(claude_agent, "get_secret", get_secret)

    assert await claude_agent.descriptor_claude_cli_credentials() == {
        "ANTHROPIC_API_KEY": "anthropic-api-key",
        "CLAUDE_CODE_KEY": "claude-code-key",
    }


@pytest.mark.asyncio
async def test_claude_harness_mcp_exposes_conversation_execution_and_rendering_tools(
    tmp_path: Path,
) -> None:
    from kdcube_ai_app.apps.chat.sdk.runtime.mcp.client import open_mcp_client

    module = "kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.tool_server"
    descriptors = (AGENTS_ROOT / "claude" / "descriptors.template").resolve()
    async with open_mcp_client(
        transport="stdio",
        command=sys.executable,
        args=[
            "-m",
            module,
            "--config",
            str(AGENTS_ROOT / "claude" / "config.template.yaml"),
            "--descriptors",
            str(descriptors),
            "--run-root",
            str(tmp_path / "run"),
            "--events",
            str(tmp_path / "mcp-events.jsonl"),
            "--conversation-id",
            "conversation-demo",
            "--user-id",
            "demo-user",
            "--user-type",
            "regular",
            "--session-id",
            "local-session",
            "--turn-id",
            "turn_demo",
            "--bundle-id",
            "standalone-claude-demo@1-0",
            "--agent-id",
            "claude",
            "--bundle-root",
            str(AGENTS_ROOT / "claude"),
            "--bundle-module",
            "agent",
        ],
        env=os.environ.copy(),
        read_timeout_seconds=20,
    ) as client:
        listed = await client.list_tools()

    assert {tool.name for tool in listed.tools} == {
        "conversation_search",
        "execute_python",
        "write_pdf",
        "write_docx",
        "write_pptx",
    }


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_each_example_owns_a_standard_app_agnostic_descriptor_set(adapter: str) -> None:
    descriptors = AGENTS_ROOT / adapter / "descriptors.template"
    assert {path.name for path in descriptors.iterdir()} == {
        "assembly.yaml",
        "economics.yaml",
        "gateway.yaml",
        "secrets.yaml",
    }
    assembly = yaml.safe_load(
        (descriptors / "assembly.yaml").read_text(encoding="utf-8")
    )
    secrets = yaml.safe_load((descriptors / "secrets.yaml").read_text(encoding="utf-8"))
    economics = yaml.safe_load(
        (descriptors / "economics.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(assembly.get("infra", {}).get("redis"), dict)
    assert isinstance(assembly.get("infra", {}).get("postgres"), dict)
    assert assembly.get("storage", {}).get("kdcube") == "../output/kdcube-storage"
    assert assembly["models"]["default_llm_provider"] == "anthropic"
    assert assembly["models"]["default_llm_model_id"] == "claude-haiku-4-5-20251001"
    assert (
        assembly["platform"]["services"]["proc"]["exec"]["py_code_exec_image"]
        == "py-code-exec:latest"
    )
    assert assembly.get("secrets", {}).get("provider") == "secrets-file"
    assert secrets["platform"]["infra"]["redis"]["password"] is None
    assert secrets["platform"]["infra"]["postgres"]["password"] is None
    if adapter == "claude":
        assert assembly["storage"]["claude_code_session"]["type"] == "git"
        assert str(assembly["storage"]["claude_code_session"]["repo"] or "")
        assert "http_token" in secrets["platform"]["services"]["git"]
    else:
        assert "claude_code_session" not in assembly["storage"]
        assert "git" not in secrets["platform"]["services"]
        custom = assembly["services"]["llm"]["custom"]
        assert custom == {
            "endpoint": "http://127.0.0.1:11500/generate",
            "num_ctx": 32768,
        }
        assert "model_overrides" not in custom
        assert secrets["platform"]["services"]["llm"]["custom"]["api_key"] is None
    prices = economics.get("price_tables", {}).get("llm")
    assert prices
    assert prices[0]["model"] == "claude-haiku-4-5-20251001"
    assert prices[0]["provider"] == "anthropic"
    assert (
        yaml.safe_load((descriptors / "gateway.yaml").read_text(encoding="utf-8")) == {}
    )


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_primary_examples_do_not_use_hosted_runtime_client(adapter: str) -> None:
    source = (AGENTS_ROOT / adapter / "agent.py").read_text(encoding="utf-8")
    forbidden = (
        "/sse/chat",
        "RuntimeChatClient",
        "--workdir",
        "KDCUBE_RUNTIME_WORKDIR",
        "agents.integration",
    )
    assert not [marker for marker in forbidden if marker in source]


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_primary_examples_use_sdk_direct_harness(adapter: str) -> None:
    source = (AGENTS_ROOT / adapter / "agent.py").read_text(encoding="utf-8")
    assert "DirectAgentHarness" in source
    assert "harness.turn(" in source
    assert "verify_conversation(" in source


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_primary_examples_expose_the_same_direct_conversation_modes(
    adapter: str,
) -> None:
    source = (AGENTS_ROOT / adapter / "agent.py").read_text(encoding="utf-8")

    assert "DirectTurnRequest" in source
    assert "completed_direct_turn_result(" in source
    assert "run_terminal_chat(" in source
    assert "serve_direct_telegram(" in source
    assert '"--interactive"' in source
    assert '"--telegram-local"' in source
    assert "except KeyboardInterrupt:" in source


def test_provider_native_examples_name_materialized_telegram_attachments() -> None:
    langgraph = (AGENTS_ROOT / "langgraph" / "agent.py").read_text(encoding="utf-8")
    claude = (AGENTS_ROOT / "claude" / "agent.py").read_text(encoding="utf-8")

    assert "prompt_with_attachment_manifest(prompt, attachments)" in langgraph
    assert "prompt_with_attachment_manifest(prompt, attachments)" in claude
    assert 'mirror_to=workspace / "attachments"' in claude


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_direct_conversation_modes_are_exclusive_with_checks(adapter: str) -> None:
    completed = subprocess.run(
        [sys.executable, "agent.py", "--check", "--interactive"],
        cwd=AGENTS_ROOT / adapter,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 2
    assert "not allowed with argument" in completed.stderr


def test_each_adapter_uses_the_full_agent_private_state_scope() -> None:
    native = (AGENTS_ROOT / "native" / "agent.py").read_text(encoding="utf-8")
    langgraph = (AGENTS_ROOT / "langgraph" / "agent.py").read_text(encoding="utf-8")
    claude = (AGENTS_ROOT / "claude" / "agent.py").read_text(encoding="utf-8")

    for source in (native, langgraph, claude):
        assert ".continuity_key(" in source
        assert 'user_id="demo-user"' not in source
        assert 'session_id="local-session"' not in source
    assert '"thread_id": private_state_key' in langgraph
    assert 'f"kdcube-agent:{private_state_key}"' in claude


def test_claude_uses_sdk_git_session_store_for_its_transcript() -> None:
    source = (AGENTS_ROOT / "claude" / "agent.py").read_text(encoding="utf-8")
    assembly = yaml.safe_load(
        (AGENTS_ROOT / "claude" / "descriptors.template" / "assembly.yaml").read_text(
            encoding="utf-8"
        )
    )
    secrets = yaml.safe_load(
        (AGENTS_ROOT / "claude" / "descriptors.template" / "secrets.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert "run_claude_code_turn(" in source
    assert "ClaudeCodeSessionStoreConfig(" in source
    assert source.count("agent_name=agent_id") == 2
    assert assembly["storage"]["claude_code_session"]["type"] == "git"
    assert str(assembly["storage"]["claude_code_session"]["repo"] or "")
    assert "http_token" in secrets["platform"]["services"]["git"]


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_offline_adapter_construction(adapter: str) -> None:
    root = AGENTS_ROOT / adapter
    env = os.environ.copy()
    env.pop("KDCUBE_RUNTIME_WORKDIR", None)
    completed = subprocess.run(
        [
            sys.executable,
            "agent.py",
            "--config",
            "config.template.yaml",
            "--descriptors",
            "descriptors.template",
            "--check",
        ],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "mode: standalone SDK process" in completed.stdout
    assert "tools:" in completed.stdout
    assert "skills: demo.research-brief" in completed.stdout
    assert "instruction profile:" in completed.stdout
    assert "custom instructions: configured" in completed.stdout
    assert "model: anthropic/claude-haiku-4-5-20251001" in completed.stdout
    assert f"conversation: {adapter}-demo" in completed.stdout
    assert (
        f"agent state scope: standalone/agent-harness-{adapter}/"
        f"demo-user/{adapter}-demo/{adapter}"
    ) in completed.stdout
    assert "check: PASS" in completed.stdout


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_offline_adapter_construction_accepts_explicit_caller_overrides(
    adapter: str,
) -> None:
    root = AGENTS_ROOT / adapter
    completed = subprocess.run(
        [
            sys.executable,
            "agent.py",
            "--config",
            "config.template.yaml",
            "--descriptors",
            "descriptors.template",
            "--check",
            "--user-id",
            "alice",
            "--conversation-id",
            "research-42",
            "--session-id",
            "terminal-7",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "user: alice (regular)" in completed.stdout
    assert "session: terminal-7" in completed.stdout
    assert "conversation: research-42" in completed.stdout
    assert (
        f"agent state scope: standalone/agent-harness-{adapter}/"
        f"alice/research-42/{adapter}"
    ) in completed.stdout


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_offline_adapter_construction_rejects_unknown_instruction_profile(
    adapter: str,
    tmp_path: Path,
) -> None:
    root = AGENTS_ROOT / adapter
    config = yaml.safe_load((root / "config.template.yaml").read_text(encoding="utf-8"))
    config["agent"]["instructions"]["profile"] = "unknown-profile"
    config["agent"]["run_directory"] = str(tmp_path / "output")
    config["agent"]["skills"]["root"] = str(root / "skills")
    config_path = tmp_path / f"{adapter}.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "agent.py",
            "--config",
            str(config_path),
            "--descriptors",
            "descriptors.template",
            "--check",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "unknown" in completed.stderr.lower()
    assert "instruction profile" in completed.stderr.lower()


def test_native_check_rejects_configured_callable_missing_from_source(
    tmp_path: Path,
) -> None:
    root = AGENTS_ROOT / "native"
    config = yaml.safe_load((root / "config.template.yaml").read_text(encoding="utf-8"))
    config["agent"]["tools"][0]["allowed"].append("missing_callable")
    config["agent"]["run_directory"] = str(tmp_path / "output")
    config["agent"]["skills"]["root"] = str(root / "skills")
    config_path = tmp_path / "native-missing-tool.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "agent.py",
            "--config",
            str(config_path),
            "--descriptors",
            "descriptors.template",
            "--check",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "native adapter construction incomplete" in completed.stderr
    assert "web_tools.missing_callable" in completed.stderr


def test_native_model_selection_comes_from_assembly_descriptor(tmp_path: Path) -> None:
    descriptors = tmp_path / "descriptors"
    shutil.copytree(AGENTS_ROOT / "native" / "descriptors.template", descriptors)
    assembly_path = descriptors / "assembly.yaml"
    assembly = yaml.safe_load(assembly_path.read_text(encoding="utf-8"))
    assembly["models"]["default_llm_provider"] = "openai"
    assembly["models"]["default_llm_model_id"] = "gpt-4o"
    assembly_path.write_text(
        yaml.safe_dump(assembly, sort_keys=False), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable,
            "agent.py",
            "--descriptors",
            str(descriptors),
            "--check",
        ],
        cwd=AGENTS_ROOT / "native",
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "model: openai/gpt-4o" in completed.stdout


@pytest.mark.parametrize("adapter", ("native", "langgraph"))
def test_model_service_agents_accept_arbitrary_on_host_model_from_assembly(
    adapter: str,
    tmp_path: Path,
) -> None:
    descriptors = tmp_path / f"{adapter}-descriptors"
    root = AGENTS_ROOT / adapter
    shutil.copytree(root / "descriptors.template", descriptors)
    assembly_path = descriptors / "assembly.yaml"
    assembly = yaml.safe_load(assembly_path.read_text(encoding="utf-8"))
    assembly["models"] = {
        "default_llm_provider": "custom",
        "default_llm_model_id": "operator-selected:model-tag",
    }
    assembly["services"]["llm"]["custom"] = {
        "endpoint": "http://127.0.0.1:11500/generate",
        "num_ctx": 24576,
    }
    assembly_path.write_text(
        yaml.safe_dump(assembly, sort_keys=False), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable,
            "agent.py",
            "--config",
            "config.template.yaml",
            "--descriptors",
            str(descriptors),
            "--check",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "model: custom/operator-selected:model-tag" in completed.stdout
    assert "model endpoint: http://127.0.0.1:11500/generate" in completed.stdout
    assert "model context: 24576" in completed.stdout
    assert "check: PASS" in completed.stdout


def test_custom_model_selection_requires_gateway_endpoint(tmp_path: Path) -> None:
    descriptors = tmp_path / "descriptors"
    root = AGENTS_ROOT / "native"
    shutil.copytree(root / "descriptors.template", descriptors)
    assembly_path = descriptors / "assembly.yaml"
    assembly = yaml.safe_load(assembly_path.read_text(encoding="utf-8"))
    assembly["models"] = {
        "default_llm_provider": "custom",
        "default_llm_model_id": "operator-selected:model-tag",
    }
    del assembly["services"]["llm"]["custom"]["endpoint"]
    assembly_path.write_text(
        yaml.safe_dump(assembly, sort_keys=False), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable,
            "agent.py",
            "--config",
            "config.template.yaml",
            "--descriptors",
            str(descriptors),
            "--check",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert "provider 'custom' requires services.llm.custom.endpoint" in completed.stderr


def test_claude_model_selection_is_descriptor_owned_and_provider_constrained(
    tmp_path: Path,
) -> None:
    descriptors = tmp_path / "descriptors"
    root = AGENTS_ROOT / "claude"
    shutil.copytree(root / "descriptors.template", descriptors)
    assembly_path = descriptors / "assembly.yaml"
    assembly = yaml.safe_load(assembly_path.read_text(encoding="utf-8"))
    assembly["models"] = {
        "default_llm_provider": "custom",
        "default_llm_model_id": "operator-selected:model-tag",
    }
    assembly_path.write_text(
        yaml.safe_dump(assembly, sort_keys=False), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable,
            "agent.py",
            "--config",
            "config.template.yaml",
            "--descriptors",
            str(descriptors),
            "--check",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert (
        "Claude Code adapter requires models.default_llm_provider" in completed.stderr
    )


def test_each_example_owns_its_selected_research_skill() -> None:
    for adapter in ADAPTERS:
        skill = (
            AGENTS_ROOT / adapter / "skills" / "demo" / "research-brief" / "SKILL.md"
        )
        source = skill.read_text(encoding="utf-8")
        assert "namespace: demo" in source
        assert "name: research-brief" in source
        assert "id: research-brief" in source
        config = yaml.safe_load(
            (AGENTS_ROOT / adapter / "config.template.yaml").read_text(encoding="utf-8")
        )
        assert config["agent"]["skills"]["root"] == "./skills"


def test_native_yaml_can_select_the_isolated_exec_tool() -> None:
    from agents.native.agent import EXEC_TOOL_ID, RENDER_TOOL_IDS
    from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.configuration import (
        configured_agent_tool_config,
        configured_tool_ids,
    )

    config = yaml.safe_load(
        (AGENTS_ROOT / "native" / "config.template.yaml").read_text(encoding="utf-8")
    )
    resolved = configured_agent_tool_config(
        config,
        agent_id="native",
        bundle_root=AGENTS_ROOT / "native",
    )

    assert configured_tool_ids(resolved) == (
        "web_tools.web_search",
        "web_tools.web_fetch",
        EXEC_TOOL_ID,
        *RENDER_TOOL_IDS,
    )
    assert resolved.tool_runtime[EXEC_TOOL_ID] == "docker"
    assert resolved.allowed_tool_names_by_alias["exec_tools"] == ["execute_code_python"]
    assert any(spec.get("alias") == "exec_tools" for spec in resolved.tool_specs)
    assert resolved.allowed_tool_names_by_alias["rendering_tools"] == [
        "write_pdf",
        "write_docx",
        "write_pptx",
    ]


def test_direct_tool_selection_changes_only_through_canonical_allowed_names() -> None:
    from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.configuration import (
        configured_agent_tool_config,
        configured_tool_ids,
    )

    config = yaml.safe_load(
        (AGENTS_ROOT / "native" / "config.template.yaml").read_text(encoding="utf-8")
    )
    config["agent"]["tools"][0]["allowed"] = ["web_search"]
    resolved = configured_agent_tool_config(
        config,
        agent_id="native",
        bundle_root=AGENTS_ROOT / "native",
    )

    assert "web_tools.web_search" in configured_tool_ids(resolved)
    assert "web_tools.web_fetch" not in configured_tool_ids(resolved)


def test_native_event_evidence_resolves_json_tool_calls_and_results() -> None:
    from agents.native.agent import event_source_ids

    blocks = [
        {
            "type": "react.tool.call",
            "mime": "application/json",
            "text": '{"tool_id":"react.memsearch","tool_call_id":"call-1"}',
        },
        {
            "type": "react.tool.result",
            "meta": {"tool_call_id": "call-1"},
        },
    ]

    assert event_source_ids(blocks) == {"react.memsearch"}


def test_claude_evidence_uses_the_canonical_supervisor_tool_identity() -> None:
    from agents.claude.agent import WEB_SEARCH_TOOL_ID, canonical_tool_id

    assert canonical_tool_id(WEB_SEARCH_TOOL_ID) == "web_tools.web_search"
    with pytest.raises(ValueError, match="one canonical SDK identity"):
        canonical_tool_id("mcp__unknown__tool")


def test_missing_isolated_exec_image_fails_before_agent_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting import configuration

    calls: list[list[str]] = []
    monkeypatch.setattr(configuration.shutil, "which", lambda _name: "/usr/bin/docker")

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(configuration.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="build it before enabling code execution"):
        configuration.verify_docker_image({"image": "missing-exec:latest"})
    assert calls == [["/usr/bin/docker", "image", "inspect", "missing-exec:latest"]]


def test_direct_recipe_is_an_executable_configuration_contract() -> None:
    source = DIRECT_RECIPE.read_text(encoding="utf-8")
    required = (
        ".venv/bin/python setup_local.py --provider anthropic",
        ".venv/bin/python setup_local.py --provider none",
        "docker compose --env-file .env -f compose.yaml up -d --wait",
        "descriptors.local/",
        "storage:\n  kdcube:",
        "storage.claude_code_session.repo",
        "platform.services.git.http_token",
        "default_llm_provider: custom",
        "default_llm_model_id: <model-tag-loaded-by-your-local-runtime>",
        "endpoint: http://127.0.0.1:11500/generate",
        "kdcube_ai_app.apps.models_gateway.app:app",
        "The Native agent does not require provider-native tool calling",
        "Playwright/Chromium render",
        ".venv/bin/python agent.py --check",
        "agent:\n  input:",
        "user_id: demo-user",
        "conversation_id: native-demo",
        "tenant / project / user_id / conversation_id / agent_id",
        'topic: "the current stable Python release and its release date"',
        "instructions:\n    profile: lite:core",
        "additional_instructions: |",
        "id: web",
        "module: kdcube_ai_app.apps.chat.sdk.tools.web_tools",
        "allowed: [web_search, web_fetch]",
        "id: code",
        "allowed: [execute_code_python]",
        "id: documents",
        "allowed: [write_pdf, write_docx, write_pptx]",
        "skills:\n    root: ./skills",
        "settings:\n        filter:",
        "SDK `ToolSubsystem` introspects those sources",
        "Dockerfile_Exec",
        "docker image inspect py-code-exec:latest",
        "demonstration: PASS",
        ".venv/bin/python agent.py --interactive",
        ".venv/bin/python agent.py --telegram-local",
        "bot_token_ref: platform.services.telegram.bot_token",
        "webhook_secret_ref: platform.services.telegram.webhook_secret",
        "user_id         = telegram_<sender-id>",
        "the app and chat runtime provide",
        "build a durable queue around the direct callback",
        "output/runs/<user>/<conversation>/<run>/evidence.json",
        "Serve the same agent to users",
    )
    assert not [fragment for fragment in required if fragment not in source]
    assert "api_key_ref" not in source
    assert "password_ref" not in source
    assert "export OPENAI_API_KEY" not in source
    readmes = (
        AGENTS_ROOT / "README.md",
        *(AGENTS_ROOT / name / "README.md" for name in ADAPTERS),
    )
    for readme in readmes:
        assert "run-agent-harness-from-python-README.md" in readme.read_text(
            encoding="utf-8"
        )


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_each_example_owns_independent_infrastructure(adapter: str) -> None:
    compose = yaml.safe_load(
        (AGENTS_ROOT / adapter / "compose.yaml").read_text(encoding="utf-8")
    )
    assert set(compose["services"]) == {"postgres", "redis"}
    assert compose["services"]["postgres"]["image"] == "pgvector/pgvector:pg16"
    assert "redis:7" in compose["services"]["redis"]["image"]


def test_configure_materializes_standard_descriptors_without_printing_secrets(
    tmp_path: Path,
) -> None:
    from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.local_setup import configure

    shutil.copytree(
        AGENTS_ROOT / "claude" / "descriptors.template",
        tmp_path / "descriptors.template",
    )
    descriptors, compose_env, claude_repo = configure(
        tmp_path,
        provider="openai",
        provider_key="provider-secret",
    )

    secret_document = json.loads(
        (descriptors / "secrets.yaml").read_text(encoding="utf-8")
    )
    compose_source = compose_env.read_text(encoding="utf-8")
    platform = secret_document["platform"]
    assert "services" not in secret_document
    assert "infra" not in secret_document
    assert platform["services"]["openai"]["api_key"] == "provider-secret"
    assert platform["infra"]["postgres"]["password"] in compose_source
    assert platform["infra"]["redis"]["password"] in compose_source
    assert (descriptors / "assembly.yaml").is_file()
    assert (descriptors / "economics.yaml").is_file()
    assert (descriptors / "gateway.yaml").is_file()
    assert (descriptors / "secrets.yaml").stat().st_mode & 0o777 == 0o600
    assert compose_env.stat().st_mode & 0o777 == 0o600
    assert (claude_repo / "HEAD").is_file()
    with pytest.raises(FileExistsError, match="local configuration already exists"):
        configure(tmp_path, provider="none", provider_key=None)


def test_infrastructure_projects_standard_descriptor_settings(tmp_path: Path) -> None:
    from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.infrastructure import (
        postgres_url,
        redis_url,
        storage_uri,
    )

    settings = SimpleNamespace(
        REDIS_PASSWORD="r:e/d@is",
        REDIS_HOST="localhost",
        REDIS_PORT=56379,
        REDIS_DB=3,
        PGPASSWORD="p:o/s@t",
        PGUSER="demo user",
        PGHOST="localhost",
        PGPORT=55432,
        PGDATABASE="demo/db",
        PGSSL=False,
        STORAGE_PATH="./output/conversation-store",
    )

    assert redis_url(settings) == "redis://:r%3Ae%2Fd%40is@localhost:56379/3"
    assert postgres_url(settings) == (
        "postgresql://demo%20user:p%3Ao%2Fs%40t@localhost:55432/demo%2Fdb?sslmode=disable"
    )
    assert (
        storage_uri(settings, descriptors_dir=tmp_path)
        == (tmp_path / "output" / "conversation-store").resolve().as_uri()
    )

    settings.STORAGE_PATH = "s3://agent-records/tenant/project"
    assert (
        storage_uri(settings, descriptors_dir=tmp_path)
        == "s3://agent-records/tenant/project"
    )


def test_descriptor_activation_normalizes_storage_for_sdk_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting import infrastructure

    for name in infrastructure.DESCRIPTOR_FILENAMES:
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    for env_name in (
        "PLATFORM_DESCRIPTORS_DIR",
        "ASSEMBLY_YAML_DESCRIPTOR_PATH",
        "GLOBAL_SECRETS_YAML",
        "ECONOMICS_YAML_DESCRIPTOR_PATH",
        "GATEWAY_YAML_PATH",
        "GATEWAY_COMPONENT",
    ):
        monkeypatch.setenv(env_name, "")
    settings = SimpleNamespace(
        STORAGE_PATH="../output/kdcube-storage",
        HOST_BUNDLE_STORAGE_PATH="../output/bundle-storage",
        PLATFORM=SimpleNamespace(
            APPLICATIONS=SimpleNamespace(
                BUNDLE_STORAGE_ROOT="../output/bundle-storage"
            )
        ),
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.config.get_settings",
        Mock(return_value=settings),
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.config_cache.clear_config_cache",
        Mock(),
    )
    monkeypatch.setattr(
        "kdcube_ai_app.infra.secrets.reset_secrets_manager_cache",
        Mock(),
    )

    activated = infrastructure.activate_platform_descriptors(tmp_path)

    assert activated is settings
    assert os.environ["GATEWAY_COMPONENT"] == "proc"
    assert settings.STORAGE_PATH == str(
        (tmp_path / "../output/kdcube-storage").resolve()
    )
    assert settings.HOST_BUNDLE_STORAGE_PATH == str(
        (tmp_path / "../output/bundle-storage").resolve()
    )
    assert settings.PLATFORM.APPLICATIONS.BUNDLE_STORAGE_ROOT == str(
        (tmp_path / "../output/bundle-storage").resolve()
    )


@pytest.mark.asyncio
async def test_langgraph_postgres_checkpointer_runs_idempotent_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from agents.langgraph.agent import open_postgres_checkpointer

    checkpointer = object()
    setup = AsyncMock()
    checkpointer = type("FakeCheckpointer", (), {"setup": setup})()

    class FakeContextManager:
        async def __aenter__(self):
            return checkpointer

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(
        AsyncPostgresSaver,
        "from_conn_string",
        staticmethod(lambda _url: FakeContextManager()),
    )
    settings = SimpleNamespace(
        PGHOST="localhost", PGPORT=55432, PGDATABASE="kdcube_agents"
    )

    async with open_postgres_checkpointer(settings, "postgresql://unused") as opened:
        assert opened is checkpointer
    setup.assert_awaited_once_with()


def test_examples_require_model_authored_code_and_kdcube_rendering() -> None:
    for adapter in ADAPTERS:
        source = (AGENTS_ROOT / adapter / "agent.py").read_text(encoding="utf-8")
        assert "openpyxl" in source
        assert "execute_python" in source or "execute_code_python" in source
        assert "write_pdf" in source
        assert "construct pdf bytes" in source.lower()
        assert "in python" in source.lower()

    helper_sources = (
        (AGENTS_ROOT / "langgraph" / "tools.py").read_text(encoding="utf-8"),
    )
    for source in helper_sources:
        assert "create_briefing" not in source
        assert "from openpyxl" not in source
        assert "from reportlab" not in source


@pytest.mark.asyncio
async def test_langgraph_file_tools_delegate_to_the_turn_runtime() -> None:
    from agents.langgraph.tools import build_tools

    execute = AsyncMock(return_value={"ok": True, "items": []})
    render = AsyncMock(return_value={"ok": True, "items": []})
    runtime = SimpleNamespace(
        execute_python=execute,
        write_pdf=render,
        write_docx=AsyncMock(return_value={"ok": True, "items": []}),
        write_pptx=AsyncMock(return_value={"ok": True, "items": []}),
        tool_report=lambda result: json.dumps(result),
    )
    tools = {
        item.name: item
        for item in build_tools(
            runtime,
            configured_ids={
                "exec_tools.execute_code_python",
                "rendering_tools.write_pdf",
            },
        )
    }

    execute_result = json.loads(
        await tools["execute_python"].ainvoke(
            {
                "code": "print('agent authored')",
                "artifacts": [
                    {
                        "filepath": "files/research/data.xlsx",
                        "description": "Evidence workbook",
                        "visibility": "external",
                    }
                ],
                "program_name": "Research workbook",
                "timeout_s": 300,
            }
        )
    )
    render_result = json.loads(
        await tools["write_pdf"].ainvoke(
            {
                "source_path": "files/research/brief.html",
                "output_path": "files/research/brief.pdf",
                "title": "Brief",
            }
        )
    )

    assert execute_result["ok"] is True
    assert render_result["ok"] is True
    execute.assert_awaited_once()
    render.assert_awaited_once_with(
        source_path="files/research/brief.html",
        output_path="files/research/brief.pdf",
        title="Brief",
    )


def test_claude_runner_names_a_missing_claude_credential(tmp_path: Path) -> None:
    """The git transcript store points the CLI at its own config directory, so a
    login in the operator's ~/.claude is reachable only when the runner links it
    in. With no credential at all the runner must name the remedies instead of
    letting the CLI answer `Not logged in` with no cause."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "claude_runner_under_test", AGENTS_ROOT / "claude" / "agent.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module._require_claude_credential({"ANTHROPIC_API_KEY": "sk-configured"})
    module._require_claude_credential({"CLAUDE_CODE_KEY": "sk-configured"})

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    original = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        module._require_claude_credential({})
        os.environ["HOME"] = str(tmp_path / "no-login")
        with pytest.raises(RuntimeError) as failure:
            module._require_claude_credential({})
    finally:
        if original is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = original

    message = str(failure.value)
    assert "no Claude credential available" in message
    assert "platform.services.anthropic.api_key" in message
    assert "claude_code_session.type: local" in message
