"""Isolated code execution and document rendering for direct agent hosts."""

from __future__ import annotations

import json
import mimetypes
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.runtime.direct_hosting.workspace import (
    DirectTurnWorkspace,
)
from kdcube_ai_app.apps.chat.sdk.runtime.harness.workspace.layout import (
    resolve_artifact_path,
)
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.runtime.tool_config import AgentToolConfig
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import (
    ToolSubsystem,
    tool_id_allowed_by_alias_names,
)
from kdcube_ai_app.apps.chat.sdk.tools.io_tools import tools as agent_io_tools
from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import (
    build_exec_output_contract,
    normalize_exec_contract_for_turn,
    rewrite_exec_code_paths,
    run_exec_tool,
)
from kdcube_ai_app.infra.plugin.bundle_registry import BundleSpec
from kdcube_ai_app.infra.service_hub.inventory import AgentLogger


EXEC_TOOL_ID = "execute_python"
RENDER_TOOL_IDS = ("write_pdf", "write_docx", "write_pptx")

#: Per-turn record of the files this turn's tools declared, written into the
#: turn's out directory so the hosting process reads the same declarations the
#: tool server made.
DECLARED_FILES_FILENAME = ".declared_files.json"


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "where": "direct_hosting.tool_runtime",
            "managed": True,
        },
    }


def declared_files_path(workspace: DirectTurnWorkspace) -> Path:
    return workspace.runtime_outdir / DECLARED_FILES_FILENAME


def read_declared_files(
    workspace: DirectTurnWorkspace,
) -> dict[str, dict[str, Any]]:
    """Declarations this turn's tools recorded, keyed by relative path."""
    try:
        stored = json.loads(
            declared_files_path(workspace).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {
        str(key): dict(value)
        for key, value in stored.items()
        if isinstance(value, dict)
    }


def _existing_declared_rows(
    workspace: DirectTurnWorkspace,
    declared: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relative, row in declared.items():
        if resolve_artifact_path(workspace.runtime_outdir, relative).is_file():
            rows.append(dict(row))
    return rows


@contextmanager
def _workspace_scope(workspace: DirectTurnWorkspace) -> Iterator[None]:
    out_token = OUTDIR_CV.set(str(workspace.runtime_outdir))
    work_token = WORKDIR_CV.set(str(workspace.workdir))
    try:
        yield
    finally:
        WORKDIR_CV.reset(work_token)
        OUTDIR_CV.reset(out_token)


class DirectToolRuntime:
    """Framework-neutral tool bridge bound to one direct Agent Harness turn.

    The caller supplies model-authored Python. KDCube executes it in the
    configured isolated runtime and keeps the generated source in the turn's
    execution workspace. Document renderers run in the trusted host process
    against files under the same current-turn artifact root.
    """

    def __init__(
        self,
        *,
        service: Any,
        comm: Any,
        workspace: DirectTurnWorkspace,
        exec_runtime: Mapping[str, Any],
        bundle_id: str,
        bundle_root: Path,
        bundle_module: str,
        tool_config: AgentToolConfig | None = None,
        context_rag_client: Any = None,
        conversation_harness: Any = None,
        timeout_s: int = 600,
        logger: Any = None,
    ) -> None:
        self.service = service
        self.comm = comm
        self.workspace = workspace
        self.exec_runtime = dict(exec_runtime or {})
        self.timeout_s = max(1, int(timeout_s or 600))
        self.logger = logger or AgentLogger("direct_hosting.tools")
        self.tool_config = tool_config or AgentToolConfig()
        self._conversation_harness = conversation_harness
        self._conversation_client_bound = context_rag_client is not None
        self._conversation_harness_opened = False
        self._tool_policy = (
            None
            if tool_config is None
            else self.tool_config.allowed_tool_names_by_alias
        )
        # Files this turn's tools declared, keyed by artifact-root-relative
        # path. A rewrite of the same path replaces the earlier declaration.
        # Kept in the turn's own out directory because a foreign agent loop can
        # reach these tools through the stdio tool server, which is a different
        # process than the one that hosts the turn.
        self._declared_files: dict[str, dict[str, Any]] = {}
        self.tool_subsystem = ToolSubsystem(
            service=service,
            comm=comm,
            logger=self.logger,
            bundle_spec=BundleSpec(
                id=bundle_id,
                path=str(Path(bundle_root).resolve()),
                module=bundle_module,
            ),
            context_rag_client=context_rag_client,
            raw_tool_specs=list(self.tool_config.tool_specs),
            tool_runtime=dict(self.tool_config.tool_runtime),
            tool_traits=dict(self.tool_config.tool_traits),
            allowed_tool_names_by_alias=self._tool_policy,
        )

    async def prepare(self) -> None:
        if not self._conversation_client_bound and self._conversation_harness is not None:
            await self._conversation_harness.open()
            self._conversation_harness_opened = True
            self.tool_subsystem.bind_context_rag_client(
                self._conversation_harness.conversation_client
            )
            self._conversation_client_bound = True
        await self.tool_subsystem.prebind_for_in_memory(
            workdir=self.workspace.workdir,
            outdir=self.workspace.runtime_outdir,
            logger=self.logger,
        )

    async def close(self) -> None:
        """Close resources this direct tool runtime owns."""
        if self._conversation_harness_opened and self._conversation_harness is not None:
            self._conversation_harness_opened = False
            await self._conversation_harness.close()
            self._conversation_client_bound = False

    def configured_tool_ids(self) -> tuple[str, ...]:
        """Return the finite, discovered catalog selected by descriptor policy."""
        entries = self.tool_subsystem.react_tools_cached(
            allowed_plugins=self.tool_config.allowed_plugins,
            allowed_tool_names_by_alias=(self.tool_config.allowed_tool_names_by_alias),
        )
        return tuple(str(entry["id"]) for entry in entries)

    def _require_configured_tool(self, tool_id: str) -> None:
        if not tool_id_allowed_by_alias_names(tool_id, self._tool_policy):
            raise ValueError(f"tool is not allowed by agent configuration: {tool_id}")

    async def invoke_tool(
        self,
        *,
        tool_id: str,
        params: Mapping[str, Any],
        call_reason: str,
    ) -> Any:
        """Invoke one configured Python tool through the ordinary tool wrapper."""
        self._require_configured_tool(tool_id)
        fn = self.tool_subsystem.resolve_callable(tool_id)
        if fn is None:
            raise ValueError(f"configured Python tool is not available: {tool_id}")
        await self.prepare()
        with _workspace_scope(self.workspace):
            return await agent_io_tools.tool_call(
                fn=fn,
                params=dict(params),
                call_reason=call_reason,
                tool_id=tool_id,
            )

    async def execute_python(
        self,
        *,
        code: str,
        artifacts: Sequence[Mapping[str, Any]] | str,
        program_name: str = "Agent-generated Python",
        timeout_s: int | None = None,
    ) -> dict[str, Any]:
        """Execute Python supplied by the agent under the configured contract."""
        self._require_configured_tool("exec_tools.execute_code_python")
        source = str(code or "")
        if not source.strip():
            return _error("missing_code", "code is required")
        normalized, contract_rewrites, contract_error = (
            normalize_exec_contract_for_turn(
                artifacts,
                turn_id=self.workspace.turn_id,
            )
        )
        if contract_error or not normalized:
            return _error(
                str((contract_error or {}).get("code") or "invalid_contract"),
                str(
                    (contract_error or {}).get("message")
                    or "artifact contract is invalid"
                ),
            )
        output_contract, contract, output_error = build_exec_output_contract(normalized)
        if output_error or output_contract is None or contract is None:
            return _error(
                str((output_error or {}).get("code") or "invalid_contract"),
                str(
                    (output_error or {}).get("message")
                    or "artifact contract is invalid"
                ),
            )
        rewritten_code, code_rewrites = rewrite_exec_code_paths(
            source,
            turn_id=self.workspace.turn_id,
        )
        exec_id = f"direct_{uuid.uuid4().hex[:12]}"
        await self.prepare()
        with _workspace_scope(self.workspace):
            result = await run_exec_tool(
                tool_manager=self.tool_subsystem,
                output_contract=output_contract,
                code=rewritten_code,
                contract=contract,
                timeout_s=max(1, int(timeout_s or self.timeout_s)),
                workdir=self.workspace.workdir,
                outdir=self.workspace.runtime_outdir,
                logger=self.logger,
                exec_id=exec_id,
                exec_runtime=self.exec_runtime,
            )
        result["execution_id"] = exec_id
        result["program_name"] = str(program_name or "Agent-generated Python")
        result["generated_source"] = {
            "path": str(self.workspace.workdir / "user_code.py"),
            "archive_path": "pkg/user_code.py",
        }
        result["path_rewrites"] = {
            "contract": contract_rewrites,
            "code": code_rewrites,
        }
        self._record_declared_files(result, tool_id=EXEC_TOOL_ID)
        return result

    def _record_declared_files(
        self, result: Mapping[str, Any], *, tool_id: str
    ) -> None:
        """Remember the files a tool declared for this turn.

        ``visibility`` comes from the declaration, not from the caller: the
        docs make the declaring side the authority on whether a file is
        emitted to the user (``external``) or kept for later agent use
        (``internal``).
        """
        if not isinstance(result, Mapping) or not result.get("ok"):
            return
        for item in result.get("items") or []:
            if not isinstance(item, Mapping) or item.get("error"):
                continue
            output = item.get("output")
            if not isinstance(output, Mapping):
                continue
            if str(output.get("type") or "").strip() != "file":
                continue
            relative = str(output.get("path") or "").strip()
            if not relative:
                continue
            filename = str(output.get("filename") or "").strip() or Path(relative).name
            mime = str(output.get("mime") or "").strip() or (
                mimetypes.guess_type(filename)[0] or "application/octet-stream"
            )
            visibility = (
                str(output.get("visibility") or item.get("visibility") or "external")
                .strip()
                .lower()
                or "external"
            )
            description = (
                str(output.get("description") or item.get("summary") or "").strip()
                or f"Agent output: {filename}"
            )
            self._declared_files[relative] = {
                "type": "file",
                "output": {
                    "type": "file",
                    "path": relative,
                    "filename": filename,
                    "mime": mime,
                    "visibility": visibility,
                },
                "mime": mime,
                "visibility": visibility,
                "description": description,
                "resource_id": str(item.get("artifact_id") or "").strip()
                or Path(filename).stem,
                "tool_id": tool_id,
            }
        self._write_declared_files()

    @property
    def _declared_files_path(self) -> Path:
        return declared_files_path(self.workspace)

    def _write_declared_files(self) -> None:
        path = self._declared_files_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._declared_files, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            self.logger.log(
                f"[direct_hosting.tools] could not record declared files: {exc}",
                level="WARNING",
            )

    def declared_files(self) -> list[dict[str, Any]]:
        """Rows for ``DirectAgentTurn.host_files`` covering every file this
        turn's tools declared and that exists in the turn workspace."""
        merged = dict(read_declared_files(self.workspace))
        merged.update(self._declared_files)
        return _existing_declared_rows(self.workspace, merged)

    @staticmethod
    def declared_files_for_workspace(
        workspace: DirectTurnWorkspace,
    ) -> list[dict[str, Any]]:
        """The same rows for a host whose tools ran in the stdio tool server."""
        return _existing_declared_rows(workspace, read_declared_files(workspace))

    def _current_artifact(self, path: str, *, extension: str) -> tuple[str, Path]:
        normalized, _rewrites, error = normalize_exec_contract_for_turn(
            [
                {
                    "filepath": path,
                    "description": "Document rendering artifact",
                    "visibility": "external",
                }
            ],
            turn_id=self.workspace.turn_id,
        )
        if error or not normalized:
            raise ValueError(
                str((error or {}).get("message") or "invalid artifact path")
            )
        relative = str(normalized[0]["filepath"])
        if Path(relative).suffix.lower() != extension:
            raise ValueError(f"artifact path must end with {extension}")
        return relative, resolve_artifact_path(self.workspace.runtime_outdir, relative)

    async def _render(
        self,
        *,
        operation: str,
        source_path: str,
        output_path: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        from kdcube_ai_app.apps.chat.sdk.tools import rendering_tools

        operation = str(operation or "").strip()
        self._require_configured_tool(f"rendering_tools.{operation}")
        extension = {
            "write_pdf": ".pdf",
            "write_docx": ".docx",
            "write_pptx": ".pptx",
        }.get(operation)
        source_extensions = {
            "write_pdf": {".html", ".htm"},
            "write_docx": {".md", ".markdown"},
            "write_pptx": {".html", ".htm"},
        }.get(operation, set())
        if extension is None:
            return _error("unsupported_renderer", f"unsupported renderer: {operation}")
        try:
            source_suffix = Path(source_path).suffix.lower()
            if source_suffix not in source_extensions:
                allowed = ", ".join(sorted(source_extensions))
                raise ValueError(f"{operation} source must end with one of: {allowed}")
            source_rel, source = self._current_artifact(
                source_path,
                extension=source_suffix,
            )
            output_rel, output = self._current_artifact(
                output_path, extension=extension
            )
            source.resolve().relative_to(self.workspace.artifact_root.resolve())
        except Exception as exc:
            return _error("invalid_render_path", str(exc))
        if not source.is_file():
            return _error(
                "render_source_missing", f"render source does not exist: {source_rel}"
            )
        if source.stat().st_size > 4_000_000:
            return _error("render_source_too_large", "render source exceeds 4 MB")
        content = source.read_text(encoding="utf-8")
        renderer = getattr(rendering_tools.tools, operation)
        kwargs: dict[str, Any] = {
            "path": output_rel,
            "content": content,
            "title": title,
        }
        if operation == "write_pdf":
            kwargs["format"] = "html"
            kwargs["include_sources_section"] = False
        elif operation == "write_pptx":
            kwargs["format"] = "html"
        with _workspace_scope(self.workspace):
            rendered = await renderer(**kwargs)
        if not isinstance(rendered, dict) or not rendered.get("ok"):
            return dict(rendered or _error("render_failed", f"{operation} failed"))
        if not output.is_file() or output.stat().st_size <= 0:
            return _error(
                "render_output_missing", f"renderer did not create {output_rel}"
            )
        mime = mimetypes.guess_type(output.name)[0] or "application/octet-stream"
        rendered_result = {
            "ok": True,
            "operation": operation,
            "source_path": source_rel,
            "output_path": output_rel,
            "items": [
                {
                    "artifact_id": output.stem,
                    "artifact_kind": "file",
                    "summary": f"Created by rendering_tools.{operation}",
                    "visibility": "external",
                    "output": {
                        "type": "file",
                        "path": output_rel,
                        "filename": output.name,
                        "mime": mime,
                        "description": f"Rendered {extension[1:].upper()} document",
                        "visibility": "external",
                        "size_bytes": output.stat().st_size,
                    },
                }
            ],
        }
        self._record_declared_files(rendered_result, tool_id=operation)
        return rendered_result

    async def write_pdf(
        self, *, source_path: str, output_path: str, title: str = ""
    ) -> dict[str, Any]:
        """Render current-turn HTML to PDF with ``rendering_tools.write_pdf``."""
        return await self._render(
            operation="write_pdf",
            source_path=source_path,
            output_path=output_path,
            title=title or None,
        )

    async def write_docx(
        self, *, source_path: str, output_path: str, title: str = ""
    ) -> dict[str, Any]:
        """Render current-turn Markdown to DOCX with ``rendering_tools.write_docx``."""
        return await self._render(
            operation="write_docx",
            source_path=source_path,
            output_path=output_path,
            title=title or None,
        )

    async def write_pptx(
        self, *, source_path: str, output_path: str, title: str = ""
    ) -> dict[str, Any]:
        """Render current-turn slide HTML to PPTX with ``rendering_tools.write_pptx``."""
        return await self._render(
            operation="write_pptx",
            source_path=source_path,
            output_path=output_path,
            title=title or None,
        )

    @staticmethod
    def tool_report(result: Mapping[str, Any]) -> str:
        """Compact JSON returned to foreign agent frameworks."""
        payload = {
            "ok": bool(result.get("ok")),
            "report": result.get("report_text") or result.get("error"),
            "execution_id": result.get("execution_id"),
            "generated_source": result.get("generated_source"),
            "files": [
                {
                    "path": (item.get("output") or {}).get("path"),
                    "mime": (item.get("output") or {}).get("mime"),
                    "visibility": item.get("visibility")
                    or (item.get("output") or {}).get("visibility"),
                }
                for item in (result.get("items") or [])
                if isinstance(item, Mapping) and isinstance(item.get("output"), Mapping)
            ],
        }
        return json.dumps(payload, ensure_ascii=False, default=str)


__all__ = ["DirectToolRuntime", "EXEC_TOOL_ID", "RENDER_TOOL_IDS"]
