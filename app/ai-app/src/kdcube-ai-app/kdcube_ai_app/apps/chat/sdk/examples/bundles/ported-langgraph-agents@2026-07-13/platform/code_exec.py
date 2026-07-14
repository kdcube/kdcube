# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── code_exec.py ── the code-execution seam ──
#
# Gives BOTH ported agents a way to RUN PYTHON, CREATE FILES, and EXPLORE files in
# an isolated runtime, with the produced files hosted into KDCube's conversation
# storage EXACTLY like a user attachment — the same `ApplicationHostingService`
# path the React agent's `host_files` uses (`host_files_to_conversation` +
# `emit_solver_artifacts`).
#
# This module owns the WIRING (the seam); it is model-agnostic and framework-thin:
#
#   build_code_exec_context(ep, state, agent_id)  — construct the per-turn tool
#       subsystem + hosting service + sandbox for one turn, from the entrypoint.
#   code_exec_scope(ctx)                          — async CM that BINDS the scope
#       (tool subsystem via bind_integrations, OUTDIR_CV/WORKDIR_CV, comm
#       conversation ids) and publishes a per-turn handle on a ContextVar, so a
#       tool running inside it (a model-called `run_python`) resolves.
#   run_code_and_host(code, timeout_s=…)          — run freeform Python in the
#       sandbox (side-effects / no-contract exec) and host every produced file.
#
# ISOLATION MODEL (mirrors the React harness): the code runs in the SDK's isolated
# runtime reached through `run_exec_tool_side_effects`. The runtime is the ON-BOARD
# one — the deployment's `execution.runtime` (in-memory / subprocess / docker /
# fargate), resolved from `runtime_ctx.exec_runtime` via `resolve_exec_runtime_profile`
# exactly as `base_workflow.resolve_exec_runtime` does. So it runs wherever the
# platform is configured to run code, with NO separate docker requirement. Everything
# here is FAIL-OPEN: no runtime / no hosting → a clean error result, never an
# exception into the turn, so the agent still answers.

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import pathlib
import shutil
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from kdcube_ai_app.apps.chat.sdk.runtime.exec_runtime_config import (
    normalize_exec_runtime_config,
    resolve_exec_runtime_profile,
)
from kdcube_ai_app.apps.chat.sdk.runtime.run_ctx import OUTDIR_CV, WORKDIR_CV
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import create_tool_subsystem_with_mcp
from kdcube_ai_app.apps.chat.sdk.runtime.workspace import (
    artifact_outdir_for,
    build_items_from_diff,
)
from kdcube_ai_app.apps.chat.sdk.solutions.infra import get_exec_workspace_root
from kdcube_ai_app.apps.chat.sdk.tools import bundle_tool_context

LOGGER = logging.getLogger("kdcube.ported_langgraph_agents.code_exec")

# ── config defaults (documented in config/bundles.template.yaml) ─────────────
# `tools.code_exec.enabled`  — off by default (additive, config-gated).
# `tools.code_exec.runtime`  — OPTIONAL. Omit to use the deployment's on-board exec
#                              runtime (`execution.runtime`), the same one the React
#                              harness uses. A string selects a named profile from it
#                              (e.g. "fargate_default"); a dict overrides the spec.
# `tools.code_exec.timeout_s`— per-run timeout (default 120s).
CODE_EXEC_ENABLED_DEFAULT = False
CODE_EXEC_TIMEOUT_DEFAULT = 120

# A model-call inside the sandbox may write anywhere under OUTPUT_DIR; only files
# under `turn_<id>/files/` become durable hosted artifacts (the conversation-store
# namespace). We chdir the run into that directory so a bare `open("x.txt","w")`
# lands in the hosted namespace with no path ceremony from the model.
_FILES_NAMESPACE = "files"


@dataclass
class CodeExecContext:
    """Everything one turn needs to run + host code. Built by
    `build_code_exec_context` (production) or hand-assembled (tests)."""

    enabled: bool
    comm: Any = None
    hosting_service: Any = None
    tool_subsystem: Any = None
    outdir: Optional[pathlib.Path] = None
    workdir: Optional[pathlib.Path] = None
    sandbox_root: Optional[pathlib.Path] = None
    turn_id: str = ""
    conversation_id: str = ""
    tenant: str = ""
    project: str = ""
    user_id: str = ""
    user_type: str = ""
    request_id: str = ""
    exec_runtime: Dict[str, Any] = field(default_factory=dict)
    timeout_s: int = CODE_EXEC_TIMEOUT_DEFAULT
    # Injectable exec runner (tests provide a fake; production uses the SDK
    # side-effects exec). Signature mirrors run_exec_tool_side_effects kwargs.
    exec_runner: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None
    logger: Any = LOGGER
    # Every file hosted this turn (compact refs). execute_core copies these onto
    # `state["hosted_files"]` after the run so the turn recorder persists file refs
    # for reload + later pull.
    hosted_files: List[Dict[str, Any]] = field(default_factory=list)


# The per-turn handle a tool running inside `code_exec_scope` resolves through.
# ContextVar (not a module global) so concurrent turns in one process never see
# each other's handle.
_CODE_EXEC_HANDLE_CV: "contextvars.ContextVar[Optional[CodeExecContext]]" = (
    contextvars.ContextVar("CODE_EXEC_HANDLE_CV", default=None)
)


def current_code_exec_context() -> Optional[CodeExecContext]:
    """The active code-exec context for this turn, or None when not in a scope
    (tool disabled / offline / called outside a turn)."""
    return _CODE_EXEC_HANDLE_CV.get()


# ── config reading ───────────────────────────────────────────────────────────

def read_code_exec_config(ep: Any, agent_id: str) -> Dict[str, Any]:
    """Resolve the ACTIVE agent's code-exec config, with safe defaults. Never raises.

    Tools are declared per agent as a CONNECTION LIST at
    `surfaces.as_consumer.agents.<agent_id>.tools` (the standard KDCube shape). Code
    execution is enabled iff the admin declared the code-exec connection (alias
    `code_exec`, tool `run_python`) — its PRESENCE is the ceiling. That connection's
    optional `code_exec` sub-block carries `timeout_s` / `runtime`. An agent that
    declares no such connection simply has code execution off."""
    from .tool_pick import agent_tool_connections, code_exec_connection

    connection = code_exec_connection(agent_tool_connections(ep, agent_id))
    enabled = connection is not None
    cfg = connection.get("code_exec") if isinstance(connection, dict) else None
    if not isinstance(cfg, dict):
        cfg = {}
    # Runtime: resolve it the SAME way the React harness does. base_workflow's
    # `resolve_exec_runtime` reads `runtime_ctx.exec_runtime` (the deployment's
    # `execution.runtime`, normalized onto runtime_ctx by the platform) and selects
    # a profile via `resolve_exec_runtime_profile`. So the exec tool runs wherever
    # the platform is configured to run code (in-memory / subprocess / docker /
    # fargate per deployment) — it is "on board", no separate docker requirement.
    # The connection's `code_exec.runtime`, when a string, names a PROFILE to select
    # (like the iso-runtime demo's "fargate_default"); a dict overrides the spec.
    runtime_ctx = getattr(ep, "runtime_ctx", None)
    onboard = getattr(runtime_ctx, "exec_runtime", None) if runtime_ctx is not None else None
    runtime_sel = cfg.get("runtime")
    profile = runtime_sel.strip() if isinstance(runtime_sel, str) and runtime_sel.strip() else None
    overrides = runtime_sel if isinstance(runtime_sel, dict) and runtime_sel else None
    try:
        exec_runtime = resolve_exec_runtime_profile(
            runtime=dict(onboard) if isinstance(onboard, dict) else {},
            profile=profile,
            overrides=overrides,
        )
    except Exception:
        exec_runtime = dict(onboard) if isinstance(onboard, dict) else {}
    try:
        timeout_s = int(cfg.get("timeout_s", CODE_EXEC_TIMEOUT_DEFAULT))
    except Exception:
        timeout_s = CODE_EXEC_TIMEOUT_DEFAULT
    return {"enabled": enabled, "exec_runtime": exec_runtime, "timeout_s": max(1, timeout_s)}


def code_exec_enabled(ep: Any, agent_id: str) -> bool:
    return bool(read_code_exec_config(ep, agent_id).get("enabled"))


# ── sandbox bootstrap (per-turn isolated workspace) ──────────────────────────

def _bootstrap_sandbox(*, conversation_id: str, turn_id: str) -> pathlib.Path:
    """Create a fresh per-turn sandbox with `work/` and `out/` subdirectories,
    ROOTED at the platform exec-workspace root (`get_exec_workspace_root`).

    This is the SAME reusable isolated-workspace concept the React path uses
    (`solutions/react/browser.py::_ensure_workspace`) — not React-specific; any
    agent can provision one. The root MATTERS: the docker iso-runtime bind-mounts
    `workdir -> /workspace/work` and `outdir -> /workspace/out`, translating host
    paths against this shared exec-workspace volume. A path under an arbitrary
    `/tmp` dir does NOT translate, so the container sees an empty `/workspace/work`
    and fails with `main.py not found`. The SDK exec tool writes the program into
    `work/` and its outputs under `out/`; we diff `out/` afterwards to discover
    created files. Per-turn (never cached), and cleaned up after the run."""
    base = pathlib.Path(get_exec_workspace_root())
    conv_seg = _safe_seg(conversation_id) or "conv"
    turn_seg = _safe_seg(turn_id) or f"turn_{uuid.uuid4().hex[:8]}"
    root = base / "code-exec" / conv_seg / turn_seg / uuid.uuid4().hex[:8]
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "out").mkdir(parents=True, exist_ok=True)
    return root


def _safe_seg(value: str) -> str:
    raw = (value or "").strip()
    return "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in raw)[:120]


# ── context construction (production) ────────────────────────────────────────

def _svc(service: Any, key: str) -> str:
    """A field from the communicator's service dict (the request identity surface
    the React host path reads), or '' when absent."""
    if isinstance(service, dict):
        return str(service.get(key) or "")
    return ""


def build_code_exec_context(ep: Any, state: Dict[str, Any], agent_id: str) -> CodeExecContext:
    """Build the per-turn code-exec context from the entrypoint.

    Reads config, resolves the turn identity from the bound communicator, opens a
    hosting service (ApplicationHostingService over the platform ConversationStore,
    the SAME edge the React agent hosts through), and builds a tool subsystem bound
    to that hosting service. Fail-open: any construction failure yields a DISABLED
    context, so the turn runs unchanged with the tool inert."""
    cfg = read_code_exec_config(ep, agent_id)
    if not cfg["enabled"]:
        return CodeExecContext(enabled=False)

    try:
        comm = ep.comm
    except Exception:
        comm = None
    if comm is None:
        LOGGER.info("[ported-langgraph] code_exec: no comm bound; disabling for this turn")
        return CodeExecContext(enabled=False)

    conversation = getattr(comm, "conversation", None) or {}
    service = getattr(comm, "service", None) or {}
    conversation_id = str(
        (conversation.get("conversation_id") if isinstance(conversation, dict) else "")
        or state.get("conversation_id")
        or state.get("session_id")
        or ""
    ).strip()
    turn_id = str(
        (conversation.get("turn_id") if isinstance(conversation, dict) else "")
        or state.get("turn_id")
        or ""
    ).strip()

    hosting_service = _build_hosting_service(comm)
    if hosting_service is None:
        LOGGER.info("[ported-langgraph] code_exec: hosting service unavailable; disabling")
        return CodeExecContext(enabled=False)

    tool_subsystem = _build_tool_subsystem(ep, comm, hosting_service)
    if tool_subsystem is None:
        LOGGER.info("[ported-langgraph] code_exec: tool subsystem unavailable; disabling")
        return CodeExecContext(enabled=False)

    sandbox_root = _bootstrap_sandbox(conversation_id=conversation_id, turn_id=turn_id)

    LOGGER.info(
        "[ported-langgraph] code_exec: identity (svc-first, like React) tenant=%s project=%s "
        "owner=%s(user_type=%s) conv=%s turn=%s — owner keys the hosted file's download path",
        (_svc(service, "tenant") or getattr(comm, "tenant", None) or ""),
        (_svc(service, "project") or getattr(comm, "project", None) or ""),
        (_svc(service, "user") or getattr(comm, "user_id", None) or ""),
        (_svc(service, "user_type") or getattr(comm, "user_type", None) or ""),
        conversation_id, turn_id,
    )
    return CodeExecContext(
        enabled=True,
        comm=comm,
        hosting_service=hosting_service,
        tool_subsystem=tool_subsystem,
        outdir=sandbox_root / "out",
        workdir=sandbox_root / "work",
        sandbox_root=sandbox_root,
        turn_id=turn_id,
        conversation_id=conversation_id,
        # Identity is sourced from `comm.service` FIRST (with comm-attr fallbacks) —
        # EXACTLY like the React host path (react/tools/common.py, bundle_tool_context).
        # The download-critical field is the OWNER: `service["user"]` is the request's
        # user_id OR its fingerprint; the download resolver reconstructs the storage
        # key with that same owner. Using `comm.user_id` alone stored fingerprint-owned
        # (anonymous) turns under "unknown", so the hosted file failed to download.
        tenant=str(_svc(service, "tenant") or getattr(comm, "tenant", None) or "").strip(),
        project=str(_svc(service, "project") or getattr(comm, "project", None) or "").strip(),
        user_id=str(_svc(service, "user") or getattr(comm, "user_id", None) or "").strip(),
        user_type=str(_svc(service, "user_type") or getattr(comm, "user_type", None) or "").strip(),
        request_id=str(_svc(service, "request_id") or "").strip(),
        exec_runtime=cfg["exec_runtime"],
        timeout_s=cfg["timeout_s"],
        logger=getattr(ep, "logger", None) or LOGGER,
    )


def _build_hosting_service(comm: Any) -> Any:
    """Open an ApplicationHostingService over the platform ConversationStore — the
    same construction the React path uses (`store, comm, logger`). None offline."""
    try:
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
        from kdcube_ai_app.apps.chat.sdk.solutions.react.solution_workspace import (
            ApplicationHostingService,
        )

        store = ConversationStore(get_settings().STORAGE_PATH)
        return ApplicationHostingService(store=store, comm=comm, logger=LOGGER)
    except Exception:
        LOGGER.warning("[ported-langgraph] code_exec: failed to open hosting service", exc_info=True)
        return None


def _build_tool_subsystem(ep: Any, comm: Any, hosting_service: Any) -> Any:
    """Build the bundle's tool subsystem bound to the hosting service — the object
    the SDK exec tool runs through (`tool_manager`) and that a bundle tool calling
    `host_files` resolves via `bind_integrations`. None if it cannot be built."""
    try:
        tool_subsystem, _ = create_tool_subsystem_with_mcp(
            service=getattr(ep, "models_service", None),
            comm=comm,
            logger=getattr(ep, "logger", None) or LOGGER,
            bundle_spec=ep.config.ai_bundle_spec,
            context_rag_client=getattr(ep, "ctx_client", None),
            registry={
                "bundle_props": getattr(ep, "bundle_props", None) or {},
                "pg_pool": getattr(ep, "pg_pool", None),
                "redis": getattr(ep, "redis", None),
                "config": getattr(ep, "config", None),
            },
            raw_tool_specs=[],
            tool_runtime=None,
            mcp_tool_specs=[],
            mcp_env_json=os.environ.get("MCP_SERVICES") or "",
            hosting_service=hosting_service,
        )
        return tool_subsystem
    except Exception:
        LOGGER.warning("[ported-langgraph] code_exec: failed to build tool subsystem", exc_info=True)
        return None


# ── the scope (binds everything so a tool inside it resolves) ────────────────

@contextlib.asynccontextmanager
async def code_exec_scope(ctx: Optional[CodeExecContext]):
    """Bind the code-exec scope for the duration of a graph run.

    On enter (when `ctx.enabled`): bind the tool subsystem globally
    (`bind_integrations`, so a bundle tool calling `host_files` resolves), point
    OUTDIR_CV/WORKDIR_CV at the sandbox, ensure the communicator's conversation
    carries conversation_id/turn_id, and publish the per-turn handle on the
    ContextVar so `run_code_and_host` finds it. On exit: restore every binding.

    Disabled / None ctx is a clean no-op: the graph runs unchanged and any
    `run_python` call fails open (the tool is inert)."""
    if ctx is None or not ctx.enabled:
        LOGGER.info(
            "[ported-langgraph] code_exec: scope INERT (ctx=%s enabled=%s) — run_python will fail open",
            "none" if ctx is None else "present",
            None if ctx is None else ctx.enabled,
        )
        yield ctx
        return

    LOGGER.info(
        "[ported-langgraph] code_exec: scope ENTER conv=%s turn=%s outdir=%s workdir=%s runtime=%s timeout_s=%s",
        ctx.conversation_id, ctx.turn_id,
        ctx.outdir, ctx.workdir,
        (ctx.exec_runtime or {}).get("type") or (ctx.exec_runtime or {}).get("runtime") or "?",
        ctx.timeout_s,
    )

    # 1) publish the per-turn handle
    handle_token = _CODE_EXEC_HANDLE_CV.set(ctx)

    # 2) bind the tool subsystem globally (best-effort; restored on exit)
    prev_tool_subsystem = getattr(bundle_tool_context, "_TOOL_SUBSYSTEM", None)
    try:
        bundle_tool_context.bind_integrations({"tool_subsystem": ctx.tool_subsystem})
    except Exception:
        prev_tool_subsystem = None

    # 3) point the runtime context vars at the sandbox
    out_token = OUTDIR_CV.set(str(ctx.outdir) if ctx.outdir else "")
    work_token = WORKDIR_CV.set(str(ctx.workdir) if ctx.workdir else "")

    # 4) ensure comm.conversation carries the ids scope()/host_files read
    restore_conversation = _ensure_comm_conversation(ctx)

    try:
        yield ctx
    finally:
        LOGGER.info(
            "[ported-langgraph] code_exec: scope EXIT conv=%s turn=%s (unbinding + cleanup)",
            ctx.conversation_id, ctx.turn_id,
        )
        with contextlib.suppress(Exception):
            _CODE_EXEC_HANDLE_CV.reset(handle_token)
        with contextlib.suppress(Exception):
            OUTDIR_CV.reset(out_token)
        with contextlib.suppress(Exception):
            WORKDIR_CV.reset(work_token)
        with contextlib.suppress(Exception):
            bundle_tool_context.bind_integrations({"tool_subsystem": prev_tool_subsystem})
        with contextlib.suppress(Exception):
            restore_conversation()
        _cleanup_sandbox(ctx)


def _ensure_comm_conversation(ctx: CodeExecContext) -> Callable[[], None]:
    """Best-effort: make sure comm.conversation has conversation_id/turn_id so the
    SDK `scope()` resolves. Returns a restore callable."""
    comm = ctx.comm
    if comm is None:
        return lambda: None
    conversation = getattr(comm, "conversation", None)
    if not isinstance(conversation, dict):
        return lambda: None
    prev = dict(conversation)

    def _restore() -> None:
        conversation.clear()
        conversation.update(prev)

    if ctx.conversation_id and not conversation.get("conversation_id"):
        conversation["conversation_id"] = ctx.conversation_id
    if ctx.turn_id and not conversation.get("turn_id"):
        conversation["turn_id"] = ctx.turn_id
    return _restore


def _cleanup_sandbox(ctx: CodeExecContext) -> None:
    if ctx.sandbox_root is None:
        return
    with contextlib.suppress(Exception):
        shutil.rmtree(ctx.sandbox_root, ignore_errors=True)


# ── run + host ───────────────────────────────────────────────────────────────

def _wrap_code(user_code: str, *, turn_id: str) -> str:
    """Wrap the model's freeform code so a bare relative write lands in the hosted
    `turn_<id>/files/` namespace under OUTPUT_DIR (the exec runtime sets OUTPUT_DIR
    to the artifact root). Exposes OUTPUT_DIR + FILES_DIR and chdir's into
    FILES_DIR, so `open("out.csv","w")` is hosted with no path ceremony."""
    turn_seg = _safe_seg(turn_id) or "turn_local"
    preamble = (
        "import os as _kd_os\n"
        "from pathlib import Path as _kd_Path\n"
        "OUTPUT_DIR = _kd_os.environ.get('OUTPUT_DIR') or _kd_os.getcwd()\n"
        f"FILES_DIR = _kd_Path(OUTPUT_DIR) / {turn_seg!r} / {_FILES_NAMESPACE!r}\n"
        "FILES_DIR.mkdir(parents=True, exist_ok=True)\n"
        "_kd_os.chdir(str(FILES_DIR))\n"
    )
    return preamble + "\n" + (user_code or "")


async def _default_exec_runner(
    *,
    tool_manager: Any,
    code: str,
    timeout_s: int,
    workdir: pathlib.Path,
    outdir: pathlib.Path,
    exec_id: str,
    exec_runtime: Dict[str, Any],
    logger: Any,
) -> Dict[str, Any]:
    """Production exec: the SDK side-effects / no-contract runner (diffs out/ to
    discover created files). Isolated in the SDK's docker runtime — LIVE-ONLY."""
    from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import run_exec_tool_side_effects

    return await run_exec_tool_side_effects(
        tool_manager=tool_manager,
        code=code,
        timeout_s=timeout_s,
        workdir=workdir,
        outdir=outdir,
        logger=logger,
        exec_id=exec_id,
        exec_runtime=exec_runtime or None,
    )


def _artifacts_from_side_effects(envelope: Dict[str, Any], *, outdir: pathlib.Path) -> List[Dict[str, Any]]:
    """Turn a side-effects envelope's created/modified files into host artifacts in
    the shape `ApplicationHostingService.host_files_to_conversation` consumes.

    Prefers the envelope's own `items` (built from the out/ diff); recomputes from
    the diff when absent, so this works whichever exec runner produced it."""
    items = envelope.get("items")
    if not isinstance(items, list) or not items:
        diff = envelope.get("workspace_diff") or {}
        if isinstance(diff, dict) and diff:
            items = build_items_from_diff(artifact_outdir_for(outdir, create=False), diff)
        else:
            items = []
    artifacts: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out = item.get("output") or {}
        if not isinstance(out, dict) or out.get("type") != "file":
            continue
        path = str(out.get("path") or "").strip()
        if not path:
            continue
        artifacts.append(
            {
                "type": "file",
                "output": {
                    "type": "file",
                    "path": path,
                    "filename": out.get("filename") or pathlib.PurePosixPath(path).name,
                    "mime": out.get("mime") or "application/octet-stream",
                    "text": out.get("text") if isinstance(out.get("text"), str) else "",
                },
                "mime": out.get("mime") or "application/octet-stream",
                "description": out.get("description") or item.get("summary") or "",
                "resource_id": item.get("artifact_id") or pathlib.PurePosixPath(path).name,
                "slot": item.get("artifact_id") or pathlib.PurePosixPath(path).name,
                "tool_id": "code_exec.run_python",
            }
        )
    return artifacts


async def _host_artifacts(
    ctx: CodeExecContext, artifacts: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Host produced files into conversation storage and emit them to the chat —
    the SAME two-step (`host_files_to_conversation` + `emit_solver_artifacts`) the
    React `host_files` helper performs. Returns compact refs for the model."""
    if not artifacts:
        return []
    hosting = ctx.hosting_service
    hosted = await hosting.host_files_to_conversation(
        rid=ctx.request_id,
        files=artifacts,
        outdir=str(ctx.outdir) if ctx.outdir else None,
        tenant=ctx.tenant,
        project=ctx.project,
        user=ctx.user_id,
        conversation_id=ctx.conversation_id,
        user_type=ctx.user_type,
        turn_id=ctx.turn_id,
    )
    hosted = hosted or []
    # Deliver them to the chat surface as attachments (best-effort).
    try:
        await hosting.emit_solver_artifacts(files=list(hosted), citations=[])
        LOGGER.info(
            "[ported-langgraph] code_exec: emit_solver_artifacts OK — %d file(s) sent to chat as a user event",
            len(hosted),
        )
    except Exception:
        LOGGER.warning(
            "[ported-langgraph] code_exec: emit_solver_artifacts FAILED (files hosted, not surfaced in chat)",
            exc_info=True,
        )
    refs: List[Dict[str, Any]] = []
    for row in hosted:
        if not isinstance(row, dict):
            continue
        # `logical_path` is the React-style `fi:conv_…` link the chat UI downloads
        # by and the model can cite back to the user (built by host_files_to_conversation
        # from the SAME conversation_id the bytes were stored under).
        refs.append(
            {
                "rn": row.get("rn") or "",
                "hosted_uri": row.get("hosted_uri") or "",
                "logical_path": row.get("logical_path") or "",
                "mime": row.get("mime") or "application/octet-stream",
                "filename": row.get("filename") or "",
            }
        )
        LOGGER.info(
            "[ported-langgraph] code_exec: hosted file filename=%s owner=%s conv=%s turn=%s "
            "physical_path=%s logical_path=%s rn=%s",
            row.get("filename"), ctx.user_id, ctx.conversation_id, ctx.turn_id,
            row.get("physical_path"), row.get("logical_path"), row.get("rn"),
        )
    return refs


def _error_result(message: str, *, code: str = "code_exec_unavailable", kind: str = "runtime") -> Dict[str, Any]:
    # `error` is the CODE (kept for back-compat); `error_kind` classifies the failure
    # so the model can react correctly — "runtime" = a PLATFORM/sandbox failure (not a
    # defect in the model's code, usually transient/retryable), "program" = the model's
    # own code raised. `error_message` is the human-readable line. Mirrors the SDK exec
    # contract (docs/exec/exec-logging-error-propagation-README.md).
    return {
        "ok": False, "stdout": "", "stderr": message, "files": [],
        "error": code, "error_kind": kind, "error_message": message,
    }


# ── live code-exec widget ─────────────────────────────────────────────────────
# The SAME streaming widget the React decision loop drives (solutions/widgets/exec.py):
# it emits `code_exec.*` subsystem deltas (program name, the code, status gen→exec→
# done/error) keyed by an execution_id, and the reusable chat component renders the
# live exec panel from them. React feeds it from a `<channel:code>` stream; a
# create_agent tool call carries the code as an argument, so we drive the widget
# directly around the run. Best-effort: a widget failure never affects the exec.

def _program_name_from_code(code: str) -> str:
    """A short label for the exec panel. Prefer a leading `# name` comment; else a
    generic name (the tool takes no explicit prog_name)."""
    for line in (code or "").splitlines():
        s = line.strip()
        if s.startswith("#"):
            name = s.lstrip("#").strip()
            if name:
                return name[:80]
        elif s:
            break
    return "Python program"


async def _begin_exec_widget(
    ctx: "CodeExecContext",
    exec_id: str,
    code: str,
    *,
    prog_name: Optional[str] = None,
    output_contract: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Any], float]:
    """Create + prime the live code-exec widget BEFORE the run — program name, the
    code, and (when the model declared one) the CONTRACT of files it will produce,
    then the gen→exec transition. The widget is built for exactly these three inputs.
    Returns (widget|None, t0). Fail-open."""
    import time as _time
    t0 = _time.perf_counter()
    comm = getattr(ctx, "comm", None)
    emit_delta = getattr(comm, "delta", None) if comm is not None else None
    if not callable(emit_delta):
        return None, t0
    try:
        from kdcube_ai_app.apps.chat.sdk.solutions.widgets.exec import DecisionExecCodeStreamer
        widget = DecisionExecCodeStreamer(
            emit_delta=emit_delta,
            agent="lg-react.code_exec",
            artifact_name=f"exec.{exec_id}",
            execution_id=exec_id,
            turn_id=str(getattr(ctx, "turn_id", "") or ""),
        )
        await widget.emit_program_name((prog_name or "").strip() or _program_name_from_code(code))
        await widget.feed_code(code, completed=True)   # streams `code_exec.code` (activates, emits gen)
        if isinstance(output_contract, dict) and output_contract:
            await widget.emit_contract(output_contract)   # `code_exec.contract` + auto status "exec"
        else:
            await widget.emit_status(status="exec")
        return widget, t0
    except Exception:
        LOGGER.info("[ported-langgraph] code_exec: exec widget begin failed (non-fatal)", exc_info=True)
        return None, t0


async def _end_exec_widget(widget: Optional[Any], t0: float, *, ok: bool, error: Optional[Dict[str, Any]]) -> None:
    """Close the widget: exec timing + the terminal done/error status. Fail-open."""
    if widget is None:
        return
    import time as _time
    try:
        widget.set_timings(exec_ms=int((_time.perf_counter() - t0) * 1000))
        await widget.emit_status(
            status=("done" if ok else "error"),
            error=None if ok else (error or {"message": "execution failed", "where": "exec_execution"}),
        )
    except Exception:
        LOGGER.info("[ported-langgraph] code_exec: exec widget end failed (non-fatal)", exc_info=True)


async def run_code_and_host(
    code: str,
    *,
    contract: Optional[Any] = None,
    prog_name: Optional[str] = None,
    timeout_s: Optional[int] = None,
    ctx: Optional[CodeExecContext] = None,
) -> Dict[str, Any]:
    """Run Python in the isolated sandbox and host produced files.

    Two modes, chosen by whether the model declared a `contract`:
    - **contract mode** (a non-empty `contract` of output-file specs): the model
      names the files it will produce (like the React exec tool); the code runs
      VERBATIM via the platform contract runner (`run_exec_tool`) and only the
      contracted files are hosted. The widget shows the declared contract.
    - **side-effects mode** (no contract): freeform Python, wrapped so plain relative
      paths land in the hosted namespace; every produced file is hosted.

    Returns `{"ok", "stdout", "stderr", "files":[...], + error_kind/error on failure}`.
    The file BYTES are never in the result — only refs. FAIL-OPEN: no active scope /
    offline / a sandbox-harness failure yields a clean error result, never an
    exception into the turn."""
    ctx = ctx or current_code_exec_context()
    LOGGER.info(
        "[ported-langgraph] code_exec: run_python CALLED ctx=%s enabled=%s code_len=%d",
        "none" if ctx is None else "present",
        None if ctx is None else ctx.enabled,
        len(code or ""),
    )
    if ctx is None or not ctx.enabled:
        LOGGER.info("[ported-langgraph] code_exec: run_python INERT (no active scope) — returning fail-open")
        return _error_result(
            "Code execution is not available for this turn (disabled or offline).",
            code="code_exec_disabled",
        )
    if not str(code or "").strip():
        return _error_result("No code was provided to run.", code="code_exec_empty")
    if ctx.hosting_service is None or ctx.tool_subsystem is None or ctx.outdir is None:
        LOGGER.warning(
            "[ported-langgraph] code_exec: run_python NOT WIRED hosting=%s tool_subsystem=%s outdir=%s",
            ctx.hosting_service is not None, ctx.tool_subsystem is not None, ctx.outdir is not None,
        )
        return _error_result("Code execution runtime is not fully wired for this turn.")

    exec_id = f"code-exec-{uuid.uuid4().hex[:12]}"
    workdir = ctx.workdir or (ctx.outdir.parent / "work")
    workdir.mkdir(parents=True, exist_ok=True)
    ctx.outdir.mkdir(parents=True, exist_ok=True)

    # The model MAY declare a contract of the files it plans to produce. It is ADVISORY:
    # it drives the exec panel + the model's own planning, but it NEVER gates the run or
    # hosting. Execution is always side-effects — the code is wrapped so plain relative
    # paths land in the hosted namespace and EVERY produced file is hosted. This is the
    # robust choice for a small model: the strict "write to the exact OUTPUT_DIR path or
    # the file is lost" contract runner sends it into a retry loop when it saves to a
    # plain path. Best-effort: a bad/unparseable contract just means no contract panel.
    output_contract: Optional[Dict[str, Any]] = None
    if contract is not None and (not isinstance(contract, (list, str)) or contract):
        try:
            from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import (
                normalize_exec_contract_for_turn, build_exec_output_contract,
            )
            _normalized, _rewrites, c_err = normalize_exec_contract_for_turn(contract, turn_id=ctx.turn_id)
            if c_err is None:
                output_contract, _n2, c_err2 = build_exec_output_contract(_normalized)
                if c_err2 is not None:
                    output_contract = None
        except Exception:
            LOGGER.info("[ported-langgraph] code_exec: contract parse failed (advisory — ignored)", exc_info=True)
            output_contract = None

    wrapped = _wrap_code(code, turn_id=ctx.turn_id)
    runner = ctx.exec_runner or _default_exec_runner

    # Drive the live code-exec widget (same one React streams) around the run — it shows
    # the program name, the (advisory) contract, and the code.
    exec_widget, _widget_t0 = await _begin_exec_widget(
        ctx, exec_id, code, prog_name=prog_name, output_contract=output_contract,
    )

    LOGGER.info(
        "[ported-langgraph] code_exec: EXEC start exec_id=%s runtime=%s timeout_s=%s workdir=%s outdir=%s",
        exec_id,
        (ctx.exec_runtime or {}).get("type") or (ctx.exec_runtime or {}).get("runtime") or "?",
        int(timeout_s or ctx.timeout_s), workdir, ctx.outdir,
    )
    try:
        envelope = await runner(
            tool_manager=ctx.tool_subsystem,
            code=wrapped,
            timeout_s=int(timeout_s or ctx.timeout_s),
            workdir=workdir,
            outdir=ctx.outdir,
            exec_id=exec_id,
            exec_runtime=ctx.exec_runtime,
            logger=ctx.logger,
        )
    except Exception as exc:  # never let a sandbox-harness failure crash the turn
        LOGGER.warning("[ported-langgraph] code_exec: exec runner failed", exc_info=True)
        await _end_exec_widget(
            exec_widget, _widget_t0, ok=False,
            error={"code": "sandbox_execution_failed", "message": f"{type(exc).__name__}: {exc}", "where": "exec_runner"},
        )
        return _error_result(
            f"The code sandbox failed to run your program (a platform error, not a defect "
            f"in your code; usually transient — you may retry): {type(exc).__name__}: {exc}",
            code="sandbox_execution_failed", kind="runtime",
        )

    if not isinstance(envelope, dict):
        await _end_exec_widget(exec_widget, _widget_t0, ok=False, error={"code": "sandbox_execution_failed", "message": "no result", "where": "exec_execution"})
        return _error_result(
            "The code sandbox returned no result (a platform error; usually transient — you may retry).",
            code="sandbox_execution_failed", kind="runtime",
        )

    stdout = str(envelope.get("user_out_tail") or "").strip()
    stderr_parts = [
        str(envelope.get("user_error_lines") or "").strip(),
        str(envelope.get("user_tracebacks") or "").strip(),
    ]
    err = envelope.get("error")
    if isinstance(err, dict):
        stderr_parts.append(str(err.get("description") or err.get("message") or "").strip())
    stderr = "\n".join(p for p in stderr_parts if p).strip()
    ok = bool(envelope.get("ok"))
    LOGGER.info(
        "[ported-langgraph] code_exec: EXEC done exec_id=%s ok=%s stdout_len=%d stderr_len=%d",
        exec_id, ok, len(stdout), len(stderr),
    )
    await _end_exec_widget(
        exec_widget, _widget_t0, ok=ok,
        error=(err if isinstance(err, dict) else ({"message": stderr[:400], "where": "exec_execution"} if not ok else None)),
    )

    files: List[Dict[str, Any]] = []
    try:
        artifacts = _artifacts_from_side_effects(envelope, outdir=ctx.outdir)
        LOGGER.info(
            "[ported-langgraph] code_exec: HOST %d produced file(s) exec_id=%s",
            len(artifacts), exec_id,
        )
        files = await _host_artifacts(ctx, artifacts)
        # Remember every hosted file for the turn so execute_core can persist the
        # refs (reload + later pull); the model still only sees the compact refs.
        ctx.hosted_files.extend(files)
        LOGGER.info(
            "[ported-langgraph] code_exec: HOSTED %d file(s) → conversation storage + chat event exec_id=%s rns=%s",
            len(files), exec_id, [f.get("rn") for f in files],
        )
    except Exception:
        LOGGER.warning("[ported-langgraph] code_exec: hosting produced files failed", exc_info=True)

    result: Dict[str, Any] = {"ok": ok, "stdout": stdout, "stderr": stderr, "files": files}
    if not ok:
        # Classify so the model reacts correctly (retry a platform failure; fix its own
        # code on a program error). A managed error dict from the runtime = a runtime/
        # platform failure; otherwise a non-zero exit is the model's program failing.
        if isinstance(err, dict) and (err.get("code") or err.get("managed")):
            result["error"] = str(err.get("code") or "sandbox_execution_failed")
            result["error_kind"] = "runtime"
            result["error_message"] = str(
                err.get("message") or err.get("description") or (stderr[:400] or "runtime error")
            ).strip()
        else:
            result["error"] = "program_error"
            result["error_kind"] = "program"
            result["error_message"] = (stderr[:800] or "your program exited with an error").strip()
    return result
