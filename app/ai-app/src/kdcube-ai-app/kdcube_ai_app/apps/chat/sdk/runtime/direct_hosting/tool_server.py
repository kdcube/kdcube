"""Local stdio MCP bridge for direct Agent Harness conversation and file tools."""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal

from pydantic import Field
from typing_extensions import TypedDict
import yaml

from kdcube_ai_app.apps.chat.sdk.runtime.direct_harness import DirectAgentHarness
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.evidence import ConsoleEmitter
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.infrastructure import (
    activate_platform_descriptors,
    direct_harness_config,
    platform_exec_profile,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.lifecycle import (
    direct_host_process_lifespan,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.configuration import (
    configured_agent_tool_config,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.model_service import (
    build_model_service,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.tool_runtime import (
    DirectToolRuntime,
)
from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.workspace import (
    DirectTurnWorkspace,
)
from kdcube_ai_app.apps.chat.sdk.runtime.mcp.server import KDCubeMCPServer
from kdcube_ai_app.apps.chat.sdk.tools.mcp.mcp_app_transport import run_stdio


class DirectArtifactContract(TypedDict):
    """One output file that isolated execution must preserve."""

    filepath: Annotated[
        str,
        Field(
            description=(
                "OUTPUT_DIR-relative path beginning with files/ or git/projects/"
            )
        ),
    ]
    description: Annotated[str, Field(description="What the output file contains")]
    visibility: Annotated[
        Literal["internal", "external"],
        Field(
            description=(
                "external exposes the file to the user; internal keeps it as turn evidence"
            )
        ),
    ]


@asynccontextmanager
async def tool_server_lifespan(_app: KDCubeMCPServer) -> AsyncIterator[dict[str, Any]]:
    """Release process-wide renderer resources before the MCP event loop closes."""
    runtime = getattr(_app, "_direct_tool_runtime", None)
    try:
        async with direct_host_process_lifespan():
            yield {}
    finally:
        if runtime is not None:
            await runtime.close()


def build_app(runtime: DirectToolRuntime) -> KDCubeMCPServer:
    """Expose the current turn's trusted execution and rendering bridge."""
    app = KDCubeMCPServer("kdcube_harness", lifespan=tool_server_lifespan)
    app._direct_tool_runtime = runtime

    @app.tool(
        name="conversation_search",
        description=(
            "Search this user's current or earlier KDCube conversations. Caller "
            "identity is supplied by the harness, not by tool arguments."
        ),
    )
    async def conversation_search(
        query: str,
        scope: str = "user",
        targets: list[str] | None = None,
        top_k: int = 5,
        days: int = 365,
        include_recovery_sessions: bool = False,
    ) -> str:
        result = await runtime.invoke_tool(
            tool_id="conversation_tools.search",
            params={
                "query": query,
                "scope": scope,
                "targets": targets or ["assistant", "user", "attachment", "summary"],
                "top_k": top_k,
                "days": days,
                "include_recovery_sessions": include_recovery_sessions,
            },
            call_reason="Search this user's conversation history",
        )
        return json.dumps(result, ensure_ascii=False, default=str)

    @app.tool(
        name="execute_python",
        description=(
            "Execute agent-authored Python in KDCube's configured isolated runtime. "
            "The source is an asynchronous module body: use await directly at module "
            "scope and never call asyncio.run() or run_until_complete(). "
            "Use it for computation and file creation. Every kept output must be "
            "declared in artifacts as {filepath, description, visibility}; paths "
            "are current-turn OUTPUT_DIR-relative files/... paths. Write them below "
            "Path(OUTPUT_DIR) and create their parent directories. OUTPUT_DIR is "
            "injected by the runtime; do not replace it with another path."
        ),
    )
    async def execute_python(
        code: Annotated[
            str,
            Field(
                description=(
                    "Complete asynchronous module-body Python source; top-level await "
                    "is supported and event-loop launchers are invalid"
                )
            ),
        ],
        artifacts: Annotated[
            list[DirectArtifactContract],
            Field(
                description=(
                    "Required output files with filepath, description, and visibility"
                )
            ),
        ],
        program_name: Annotated[
            str, Field(description="Short execution label")
        ] = "Agent-generated Python",
        timeout_s: Annotated[
            int | None, Field(description="Optional timeout override")
        ] = None,
    ) -> str:
        result = await runtime.execute_python(
            code=code,
            artifacts=artifacts,
            program_name=program_name,
            timeout_s=timeout_s,
        )
        return runtime.tool_report(result)

    @app.tool(
        name="write_pdf",
        description=(
            "Render a current-turn HTML file into a polished PDF with "
            "KDCube rendering_tools.write_pdf."
        ),
    )
    async def write_pdf(source_path: str, output_path: str, title: str = "") -> str:
        return runtime.tool_report(
            await runtime.write_pdf(
                source_path=source_path,
                output_path=output_path,
                title=title,
            )
        )

    @app.tool(
        name="write_docx",
        description=(
            "Render a current-turn Markdown file into a styled DOCX with "
            "KDCube rendering_tools.write_docx."
        ),
    )
    async def write_docx(source_path: str, output_path: str, title: str = "") -> str:
        return runtime.tool_report(
            await runtime.write_docx(
                source_path=source_path,
                output_path=output_path,
                title=title,
            )
        )

    @app.tool(
        name="write_pptx",
        description=(
            "Render a current-turn section-based HTML file into PPTX with "
            "KDCube rendering_tools.write_pptx."
        ),
    )
    async def write_pptx(source_path: str, output_path: str, title: str = "") -> str:
        return runtime.tool_report(
            await runtime.write_pptx(
                source_path=source_path,
                output_path=output_path,
                title=title,
            )
        )

    return app


async def _build_runtime(args: argparse.Namespace) -> DirectToolRuntime:
    descriptors = Path(args.descriptors).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise ValueError("configuration root must be a mapping")
    settings = activate_platform_descriptors(descriptors)
    service = await build_model_service(role=args.role, check_only=True)
    harness_config = direct_harness_config(
        settings=settings,
        descriptors_dir=descriptors,
        bundle_id=args.bundle_id,
        agent_id=args.agent_id,
        user_id=args.user_id,
        user_type=args.user_type,
        session_id=args.session_id,
        check_only=True,
    )
    emitter = ConsoleEmitter(Path(args.events).expanduser().resolve(), echo=False)
    harness = DirectAgentHarness(
        config=harness_config,
        model_service=service,
        emitter=emitter,
    )
    comm = harness.communicator(
        conversation_id=args.conversation_id,
        turn_id=args.turn_id,
    )
    workspace = DirectTurnWorkspace(
        run_root=Path(args.run_root),
        turn_id=args.turn_id,
    )
    return DirectToolRuntime(
        service=service,
        comm=comm,
        workspace=workspace,
        exec_runtime=platform_exec_profile(settings),
        bundle_id=args.bundle_id,
        bundle_root=Path(args.bundle_root),
        bundle_module=args.bundle_module,
        tool_config=configured_agent_tool_config(
            config,
            agent_id=args.agent_id,
            bundle_root=Path(args.bundle_root),
        ),
        conversation_harness=harness,
        timeout_s=args.timeout_s,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--descriptors", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--conversation-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--user-type", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--turn-id", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--bundle-root", required=True)
    parser.add_argument("--bundle-module", default="agent")
    parser.add_argument("--role", default="standalone.direct.tools")
    parser.add_argument("--timeout-s", type=int, default=600)
    runtime = asyncio.run(_build_runtime(parser.parse_args()))
    run_stdio(build_app(runtime))


if __name__ == "__main__":
    main()


__all__ = [
    "DirectArtifactContract",
    "build_app",
    "main",
    "tool_server_lifespan",
]
