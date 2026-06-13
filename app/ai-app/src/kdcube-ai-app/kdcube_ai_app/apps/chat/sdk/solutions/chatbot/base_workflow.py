# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/solutions/chatbot/base_workflow.py

import asyncio
import base64
import os, time, datetime, json, re
import pathlib
import random
import traceback
import copy
from importlib import import_module
from typing import Dict, Any, List, Optional, Type, Callable, Awaitable, Mapping

from kdcube_ai_app.apps.chat.emitters import (
    ChatCommunicator,
    build_comm_from_comm_context,
    build_relay_from_env,
)
from kdcube_ai_app.apps.chat.external_events import build_conversation_external_event_source
from kdcube_ai_app.apps.chat.sdk.events.event_bus import (
    EventLaneWakePublisher,
    RedisEventLaneWakeEnqueuer,
)
from kdcube_ai_app.apps.chat.sdk.event_identity import DEFAULT_REACT_AGENT_ID, normalize_agent_id
# from kdcube_ai_app.apps.chat.sdk.context.memory.conv_memories import ConvMemoriesStore
from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_index import ConvTicketIndex
from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore, Ticket
from kdcube_ai_app.apps.chat.sdk.infra.economics.limiter import subject_id_of
from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventPayload,
    external_event_attachment_payloads,
    external_events_text,
)
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.turn_reporting import _format_ms_table, _format_ms_table_markdown
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import TurnScratchpad, TurnPhaseError
from kdcube_ai_app.apps.chat.sdk.runtime.exec_runtime_config import (
    normalize_exec_runtime_config,
    resolve_exec_runtime_profile,
)
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.gate.gate_contract import GateOut
from kdcube_ai_app.apps.chat.sdk.solutions.widgets.conversation_turn_work_status import \
    ConversationTurnWorkStatus
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import create_tool_subsystem_with_mcp, ToolSubsystem
from kdcube_ai_app.apps.chat.sdk.runtime.user_inputs import ingest_user_attachments
from kdcube_ai_app.apps.chat.sdk.runtime.scratchpad import CTurnScratchpad
from kdcube_ai_app.apps.chat.sdk.skills.skills_registry import SkillsSubsystem
from kdcube_ai_app.apps.chat.sdk.tools import citations as citations_module
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.util import (truncate_text_by_tokens, _to_jsonable,
                                              ensure_event_markdown, _to_json_safe, _jd,  _now_ms,
                                              _tstart, _tend,
                                              LINE_NUMBERS_LINES, normalize_line_numbers_mode, _shorten)
from kdcube_ai_app.infra.accounting import with_accounting
from kdcube_ai_app.infra.service_hub.errors import ServiceException, ServiceError, is_context_limit_error

from kdcube_ai_app.infra.service_hub.inventory import AgentLogger, ModelServiceBase, Config, _mid
from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore
from kdcube_ai_app.apps.chat.sdk.context.graph.graph_ctx import GraphCtx
from kdcube_ai_app.apps.chat.sdk.runtime.user_inputs import (
    attachment_summary_index_text,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.compaction_memory import extract_note_tags

# ---------- small utilities ----------


def _react_agent_version() -> str:
    try:
        version = str(get_settings().AI_REACT_AGENT_VERSION or "v2").strip().lower()
    except Exception:
        version = "v2"
    return version if version in {"v2", "v3"} else "v2"


def _positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _nonnegative_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed >= 0 else None


def _service_exception_from_chain(exc: BaseException | None) -> ServiceException | None:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ServiceException):
            return cur
        cur = cur.__cause__ or cur.__context__
    return None


def _is_service_connectivity_error(err: ServiceError) -> bool:
    hay = " ".join(
        str(value or "").lower()
        for value in (
            err.code,
            err.error_type,
            err.message,
            err.stage,
            err.provider,
            err.service_name,
        )
    )
    return any(
        token in hay
        for token in (
            "connection error",
            "connectionerror",
            "api_connection_error",
            "connect error",
            "connecterror",
            "network",
            "dns",
            "temporary failure",
            "timeout",
            "timed out",
            "read timeout",
            "write timeout",
            "service unavailable",
            "unavailable",
        )
    )


def _looks_like_traceback_message(message: str) -> bool:
    text = str(message or "")
    return (
        "Traceback (most recent call last)" in text
        or "\n  File " in text
        or text.count("\n") >= 6
    )


def _generic_turn_failure_message(raw_message: str = "") -> str:
    del raw_message
    return "The assistant hit an internal error before it could complete the turn. Please retry."


def _service_connectivity_user_message(err: ServiceError) -> str:
    del err
    return (
        "The AI service connection failed while the assistant was working. "
        "Please retry in a moment; if you just changed networks, wait for connectivity to settle first."
    )


def _chat_input_kind(event_type: Any) -> str:
    text = str(event_type or "").strip().lower()
    if text in {"", "event.user.prompt", "user.prompt", "message", "user", "prompt"}:
        return "message"
    if text in {"event.user.followup", "user.followup", "followup"}:
        return "followup"
    if text in {"event.user.steer", "user.steer", "steer"}:
        return "steer"
    return "message"


def _produced_file_count(blocks: Any, turn_id: str) -> int:
    """Count current-turn files/outputs exposed in timeline blocks."""
    if not isinstance(blocks, list):
        return 0
    turn_text = str(turn_id or "").strip()
    produced: set[str] = set()

    def _add_path(value: Any) -> None:
        path = str(value or "").strip()
        if not path:
            return
        if "/attachments/" in path or ".attachments/" in path:
            return
        if turn_text:
            if path.startswith(f"fi:{turn_text}.files/") or path.startswith(f"fi:{turn_text}.outputs/"):
                produced.add(path)
                return
            if path.startswith(f"{turn_text}/files/") or path.startswith(f"{turn_text}/outputs/"):
                produced.add(path)
                return
        if path.startswith("fi:files/") or path.startswith("fi:outputs/"):
            produced.add(path)

    def _scan_mapping(mapping: Mapping[str, Any]) -> None:
        for key in ("path", "artifact_path", "logical_path", "physical_path", "resolved_ref"):
            _add_path(mapping.get(key))
        for key in ("meta", "data", "value", "payload"):
            child = mapping.get(key)
            if isinstance(child, Mapping):
                _scan_mapping(child)
        text = mapping.get("text")
        if isinstance(text, str) and ("artifact_path" in text or "physical_path" in text):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, Mapping):
                _scan_mapping(parsed)

    for block in blocks:
        if isinstance(block, Mapping):
            _scan_mapping(block)
    return len(produced)


def _get_prop_path(data: Dict[str, Any], path: str, default: Any = None) -> Any:
    if not path:
        return default
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _react_agent_config_keys(agent_id: Any) -> List[str]:
    normalized = normalize_agent_id(agent_id)
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", normalized).strip("_")
    keys: List[str] = []
    for key in (normalized, safe):
        if key and key not in keys:
            keys.append(key)
    for key in ("default_agent", "default"):
        if key not in keys:
            keys.append(key)
    return keys


def _iter_react_config_blocks(
    bundle_props: Dict[str, Any],
    *,
    agent_id: Any = None,
) -> List[tuple[str, Dict[str, Any]]]:
    blocks: List[tuple[str, Dict[str, Any]]] = []
    roots: List[tuple[str, Any]] = [
        ("react", _get_prop_path(bundle_props or {}, "react")),
        ("config.react", _get_prop_path(bundle_props or {}, "config.react")),
    ]
    for root_path, root in roots:
        if not isinstance(root, dict):
            continue
        keys = _react_agent_config_keys(agent_id)
        agents = root.get("agents")
        for key in keys:
            direct = root.get(key)
            if isinstance(direct, dict):
                blocks.append((f"{root_path}.{key}", direct))
            if isinstance(agents, dict):
                nested = agents.get(key)
                if isinstance(nested, dict):
                    blocks.append((f"{root_path}.agents.{key}", nested))
        blocks.append((root_path, root))
    return blocks


def _react_config_lookup(
    bundle_props: Dict[str, Any],
    *keys: str,
    agent_id: Any = None,
    default: Any = None,
) -> tuple[Any, Optional[str]]:
    for block_source, block in _iter_react_config_blocks(bundle_props or {}, agent_id=agent_id):
        for key in keys:
            value = _get_prop_path(block, key)
            if value is not None:
                return value, f"{block_source}.{key}"
    return default, None


def _react_context_max_tokens(config: Any, settings: Any) -> Optional[int]:
    configured = _positive_int(getattr(config, "max_tokens", None))
    if configured is not None:
        return configured
    return _positive_int(getattr(settings, "AI_REACT_CONTEXT_MAX_TOKENS", None))


def _react_max_iterations(bundle_props: Dict[str, Any], settings: Any, *, agent_id: Any = None) -> int:
    raw, _ = _react_config_lookup(bundle_props, "max_iterations", agent_id=agent_id)
    configured = _positive_int(raw)
    if configured is not None:
        return configured
    return _positive_int(getattr(settings, "AI_REACT_MAX_ITERATIONS", None)) or 15


def _bool_or_none(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _react_render_thinking(bundle_props: Dict[str, Any], settings: Any, *, agent_id: Any = None) -> bool:
    raw, _ = _react_config_lookup(bundle_props, "render_thinking", agent_id=agent_id)
    configured = _bool_or_none(raw)
    if configured is not None:
        return configured
    return bool(getattr(settings, "AI_REACT_RENDER_THINKING", True))


def _react_line_numbers_mode(bundle_props: Dict[str, Any], settings: Any, *, agent_id: Any = None) -> str:
    configured, _ = _react_config_lookup(bundle_props, "line_numbers_mode", agent_id=agent_id)
    if configured is None:
        configured = getattr(settings, "AI_REACT_LINE_NUMBERS_MODE", LINE_NUMBERS_LINES)
    return normalize_line_numbers_mode(configured, default=LINE_NUMBERS_LINES)


def _react_story_snapshots_enabled(bundle_props: Dict[str, Any], *, agent_id: Any = None) -> bool:
    raw, _ = _react_config_lookup(bundle_props, "story_snapshots.enabled", agent_id=agent_id)
    configured = _bool_or_none(raw)
    return bool(configured) if configured is not None else False


def _react_role_models(bundle_props: Dict[str, Any], *, agent_id: Any = None) -> Dict[str, Any]:
    raw, _ = _react_config_lookup(bundle_props, "role_models", agent_id=agent_id)
    return dict(raw) if isinstance(raw, dict) else {}


def _react_event_source_pipeline_enabled(bundle_props: Dict[str, Any], settings: Any, *, agent_id: Any = None) -> bool:
    raw, _ = _react_config_lookup(
        bundle_props,
        "event_source_pipeline.enabled",
        "event_source_pipeline_enabled",
        agent_id=agent_id,
    )
    configured = _bool_or_none(raw)
    if configured is not None:
        return configured
    return bool(getattr(settings, "AI_REACT_EVENT_SOURCE_PIPELINE_ENABLED", False))


def _react_event_source_pipeline_config_report(
    bundle_props: Dict[str, Any],
    settings: Any,
    *,
    agent_id: Any = None,
) -> Dict[str, Any]:
    raw, source = _react_config_lookup(
        bundle_props,
        "event_source_pipeline.enabled",
        "event_source_pipeline_enabled",
        agent_id=agent_id,
    )
    configured = _bool_or_none(raw)
    if configured is not None:
        return {
            "source": f"bundle_props.{source}",
            "raw": raw,
            "effective": configured,
        }
    raw = getattr(settings, "AI_REACT_EVENT_SOURCE_PIPELINE_ENABLED", False)
    return {
        "source": "settings.AI_REACT_EVENT_SOURCE_PIPELINE_ENABLED",
        "raw": raw,
        "effective": bool(raw),
    }


def _react_debug_timeline_enabled(
    bundle_props: Dict[str, Any],
    settings: Any,
    *,
    default: bool = False,
    agent_id: Any = None,
) -> bool:
    raw, _ = _react_config_lookup(bundle_props, "debug_timeline", agent_id=agent_id)
    configured = _bool_or_none(raw)
    if configured is not None:
        return configured
    configured = _bool_or_none(getattr(settings, "AI_REACT_DEBUG_TIMELINE", None))
    if configured is not None:
        return configured
    return bool(default)


def _react_debug_timeline_root(settings: Any) -> Optional[str]:
    platform = getattr(settings, "PLATFORM", None)
    react_debug = getattr(platform, "REACT_DEBUG", None)
    root = getattr(react_debug, "REACT_DEBUG_ROOT", None)
    if not root:
        host_root = getattr(settings, "HOST_REACT_DEBUG_PATH", None)
        host_text = str(host_root or "").strip()
        if host_text and pathlib.Path(host_text).expanduser().exists():
            root = host_text
    text = str(root or "").strip()
    return text or None


def _react_debug_timeline_keep_files(settings: Any) -> int:
    platform = getattr(settings, "PLATFORM", None)
    react_debug = getattr(platform, "REACT_DEBUG", None)
    return _positive_int(getattr(react_debug, "REACT_DEBUG_KEEP_FILES", None)) or 100


def _apply_react_session_settings(runtime_ctx: Any, settings: Any) -> None:
    session = getattr(runtime_ctx, "session", None)
    if session is None:
        return
    keep_recent = _nonnegative_int(getattr(settings, "AI_REACT_CACHE_KEEP_RECENT_TURNS", None))
    if keep_recent is not None:
        session.keep_recent_turns = keep_recent
    keep_intact = _nonnegative_int(getattr(settings, "AI_REACT_CACHE_KEEP_RECENT_INTACT_TURNS", None))
    if keep_intact is not None:
        session.keep_recent_intact_turns = keep_intact
    try:
        session.working_summary_enabled = bool(getattr(settings, "AI_REACT_WORKING_SUMMARY_ENABLED", True))
    except Exception:
        session.working_summary_enabled = True
    mode = str(getattr(settings, "AI_REACT_PRUNED_TURN_SUMMARY_MODE", "working_summary") or "working_summary").strip()
    if mode:
        session.pruned_turn_summary_mode = mode


def _effective_runtime_ctx_log_payload(runtime_ctx: Any, bundle_props: Dict[str, Any], settings: Any) -> Dict[str, Any]:
    session = getattr(runtime_ctx, "session", None)
    cache = getattr(runtime_ctx, "cache", None)
    exec_runtime = getattr(runtime_ctx, "exec_runtime", None)
    exec_runtime_keys = sorted(exec_runtime.keys()) if isinstance(exec_runtime, dict) else []
    return {
        "tenant": getattr(runtime_ctx, "tenant", None),
        "project": getattr(runtime_ctx, "project", None),
        "user_id": getattr(runtime_ctx, "user_id", None),
        "user_type": getattr(runtime_ctx, "user_type", None),
        "conversation_id": getattr(runtime_ctx, "conversation_id", None),
        "turn_id": getattr(runtime_ctx, "turn_id", None),
        "bundle_id": getattr(runtime_ctx, "bundle_id", None),
        "agent_id": getattr(runtime_ctx, "agent_id", None),
        "react_agent_version": _react_agent_version(),
        "max_tokens": getattr(runtime_ctx, "max_tokens", None),
        "max_iterations": getattr(runtime_ctx, "max_iterations", None),
        "render_thinking": bool(getattr(runtime_ctx, "render_thinking", True)),
        "line_numbers_mode": getattr(runtime_ctx, "line_numbers_mode", None),
        "story_snapshots_enabled": bool(getattr(runtime_ctx, "story_snapshots_enabled", False)),
        "agent_role_models": dict(getattr(runtime_ctx, "agent_role_models", None) or {}),
        "event_source_pipeline_enabled": bool(getattr(runtime_ctx, "event_source_pipeline_enabled", False)),
        "event_source_pipeline_config": _react_event_source_pipeline_config_report(
            bundle_props,
            settings,
            agent_id=getattr(runtime_ctx, "agent_id", None),
        ),
        "debug_timeline": bool(getattr(runtime_ctx, "debug_timeline", False)),
        "debug_timeline_root": getattr(runtime_ctx, "debug_timeline_root", None),
        "debug_timeline_keep_files": getattr(runtime_ctx, "debug_timeline_keep_files", None),
        "workspace_implementation": getattr(runtime_ctx, "workspace_implementation", None),
        "workspace_git_repo": getattr(runtime_ctx, "workspace_git_repo", None),
        "bundle_storage": getattr(runtime_ctx, "bundle_storage", None),
        "multi_action_mode": getattr(runtime_ctx, "multi_action_mode", None),
        "memory_enabled": bool(getattr(runtime_ctx, "memory_enabled", False)),
        "memory_announce_enabled": bool(getattr(runtime_ctx, "memory_announce_enabled", False)),
        "session": session.to_dict() if hasattr(session, "to_dict") else {},
        "cache": cache.to_dict() if hasattr(cache, "to_dict") else {},
        "exec_runtime_keys": exec_runtime_keys,
    }


def _react_module(module_suffix: str):
    version = _react_agent_version()
    return import_module(
        f"kdcube_ai_app.apps.chat.sdk.solutions.react.{version}.{module_suffix}"
    )


def _react_symbol(module_suffix: str, name: str):
    return getattr(_react_module(module_suffix), name)


def _react_shared_symbol(module_suffix: str, name: str):
    return getattr(
        import_module(f"kdcube_ai_app.apps.chat.sdk.solutions.react.{module_suffix}"),
        name,
    )


def _cleanup_turn_workspace(runtime_ctx: Any, logger: Any) -> None:
    import shutil

    parents: dict[pathlib.Path, list[pathlib.Path]] = {}
    for attr in ("workdir", "outdir"):
        path_str = getattr(runtime_ctx, attr, None) if runtime_ctx else None
        if not path_str:
            continue
        p = pathlib.Path(path_str)
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            logger.log(f"[workflow] cleaned up turn workspace: {p}", level="INFO")
        if p.name in {"work", "out"}:
            parents.setdefault(p.parent, []).append(p)

    for parent, paths in parents.items():
        if not parent.name.startswith(("ctx_v2_", "exec_")):
            continue
        cache = parent / ".react_workspace_git"
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
            logger.log(f"[workflow] cleaned up turn workspace git cache: {cache}", level="INFO")
        try:
            parent.rmdir()
            logger.log(f"[workflow] cleaned up empty turn workspace root: {parent}", level="INFO")
        except OSError:
            if any(path.exists() for path in paths):
                logger.log(f"[workflow] turn workspace root retained after partial cleanup: {parent}", level="WARNING")


def _ttl_for(user_type: str, requested: int) -> int:
    ttl_map = {
        "anonymous": 1,
        "registered": 7,
        "privileged": 90,
        "paid": 365
    }
    hard = ttl_map.get((user_type or "anonymous").lower(), 7)
    return min(requested, hard)

def _norm_topic(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")

# ---------- Orchestrator ----------

class BaseWorkflow():

    def __init__(self,
                 conv_idx: ConvIndex,
                 kb: KBClient,
                 store: ConversationStore,
                 comm: ChatCommunicator,
                 model_service: ModelServiceBase,
                 conv_ticket_store: ConvTicketStore,
                 config: Config,
                 comm_context: ExternalEventPayload,
                 ctx_client: Any = None,
                 message_resources_fn: Optional[Callable[[str, bool], str]] = None,
                 gate_out_class: Optional[Type] = None,
                 answer_system_prompt: Optional[str] = None,
                 graph: GraphCtx = None,
                 pg_pool: Any = None,
                 redis: Any = None,
                 bundle_props: Optional[Dict[str, Any]] = None):

        self.graph = graph
        self.kb = kb
        self.comm = comm
        self.comm_context = comm_context

        self.model_service = model_service
        self.store = store
        self.conv_idx = conv_idx
        self.pg_pool = pg_pool
        self.redis = redis

        self.conv_ticket_store = conv_ticket_store
        self.ticket_index = ConvTicketIndex(conv_ticket_store)
        self.logger = AgentLogger("base.workflow")

        self._ctx = {}

        # do not reorder these initializations below
        self.config = config
        self.bundle_props = dict(bundle_props or {})
        self.ctx_client = ctx_client or ContextRAGClient(conv_idx=self.conv_idx,
                                                        store=self.store,
                                                        model_service=self.model_service,)

        self.gate_out_class = gate_out_class or GateOut

        ApplicationHostingService = _react_symbol("solution_workspace", "ApplicationHostingService")
        RuntimeCtx = _react_shared_symbol("proto", "RuntimeCtx")
        ContextBrowser = _react_symbol("browser", "ContextBrowser")

        self.hosting_service = ApplicationHostingService(
            store=self.store,
            comm=self.comm,
            logger=self.logger,
        )

        # self.conv_memories = ConvMemoriesStore(self.graph)
        # if self.ctx_client:
        #     self.conv_memories.bind_ctx_client(self.ctx_client)
        self.turn_status = ConversationTurnWorkStatus(
            emit_delta=self.comm.delta,
            agent="orchestrator",
        )
        self._thinking_delta_idx: Dict[str, int] = {}
        self._answer_delta_idx: int = 0

        self.message_resources_fn = message_resources_fn or (lambda err_code, fallback=None: None)
        self.answer_system_prompt = answer_system_prompt
        # Runtime context + context browser are constructed once per workflow instance
        settings = get_settings()
        runtime_max_tokens = _react_context_max_tokens(self.config, settings)
        runtime_agent_id = normalize_agent_id(getattr(getattr(self.comm_context, "event", None), "agent_id", None))
        runtime_max_iterations = _react_max_iterations(self.bundle_props, settings, agent_id=runtime_agent_id)
        runtime_render_thinking = _react_render_thinking(self.bundle_props, settings, agent_id=runtime_agent_id)
        runtime_line_numbers_mode = _react_line_numbers_mode(self.bundle_props, settings, agent_id=runtime_agent_id)
        runtime_story_snapshots_enabled = _react_story_snapshots_enabled(self.bundle_props, agent_id=runtime_agent_id)
        runtime_event_source_pipeline_enabled = _react_event_source_pipeline_enabled(
            self.bundle_props,
            settings,
            agent_id=runtime_agent_id,
        )
        runtime_debug_timeline_enabled = _react_debug_timeline_enabled(
            self.bundle_props,
            settings,
            agent_id=runtime_agent_id,
        )
        runtime_debug_timeline_root = _react_debug_timeline_root(settings)
        runtime_debug_timeline_keep_files = _react_debug_timeline_keep_files(settings)
        try:
            self.runtime_ctx = RuntimeCtx(
                tenant=self.comm_context.actor.tenant_id,
                project=self.comm_context.actor.project_id,
                user_id=self.comm_context.user.user_id,
                user_type=self.comm_context.user.user_type,
                timezone=self.comm_context.user.timezone,
                conversation_id=self.comm_context.routing.conversation_id,
                turn_id=self.comm_context.routing.turn_id,
                bundle_id=self.config.ai_bundle_spec.id,
                agent_id=runtime_agent_id,
                max_tokens=runtime_max_tokens,
                max_iterations=runtime_max_iterations,
                read_visible_max_text_symbols=_positive_int(getattr(settings, "AI_REACT_READ_VISIBLE_MAX_TEXT_SYMBOLS", None)),
                read_visible_max_tokens=_positive_int(getattr(settings, "AI_REACT_READ_VISIBLE_MAX_TOKENS", None)),
                read_visible_max_bytes=_positive_int(getattr(settings, "AI_REACT_READ_VISIBLE_MAX_BYTES", None)),
                read_visible_context_fraction=getattr(settings, "AI_REACT_READ_VISIBLE_CONTEXT_FRACTION", None),
                knowledge_read_visible_max_text_symbols=_positive_int(getattr(settings, "AI_REACT_KNOWLEDGE_READ_VISIBLE_MAX_TEXT_SYMBOLS", None)),
                knowledge_read_visible_max_tokens=_positive_int(getattr(settings, "AI_REACT_KNOWLEDGE_READ_VISIBLE_MAX_TOKENS", None)),
                knowledge_read_visible_max_bytes=_positive_int(getattr(settings, "AI_REACT_KNOWLEDGE_READ_VISIBLE_MAX_BYTES", None)),
                exec_text_preview_max_symbols=_positive_int(getattr(settings, "AI_REACT_EXEC_TEXT_PREVIEW_MAX_SYMBOLS", None)),
                tool_result_preview_max_text_symbols=_positive_int(getattr(settings, "AI_REACT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS", None)),
                render_thinking=runtime_render_thinking,
                line_numbers_mode=runtime_line_numbers_mode,
                story_snapshots_enabled=runtime_story_snapshots_enabled,
                event_source_pipeline_enabled=runtime_event_source_pipeline_enabled,
                debug_timeline=runtime_debug_timeline_enabled,
                debug_timeline_root=runtime_debug_timeline_root,
                debug_timeline_keep_files=runtime_debug_timeline_keep_files,
                bundle_storage=self._resolve_runtime_ctx_bundle_storage(),
                workspace_implementation=settings.REACT_WORKSPACE_IMPLEMENTATION,
                workspace_git_repo=settings.REACT_WORKSPACE_GIT_REPO,
                external_event_source=None,
                multi_action_mode=settings.AI_REACT_AGENT_MULTI_ACTION,
            )
            _apply_react_session_settings(self.runtime_ctx, settings)
            self._sync_runtime_external_event_bus(self.runtime_ctx)
            self.ctx_browser = ContextBrowser(
                ctx_client=self.ctx_client,
                logger=self.logger,
                model_service=self.model_service,
                runtime_ctx=self.runtime_ctx,
            )
            self._register_workflow_external_event_hook()
            self._sync_runtime_ctx_bundle_props()
            try:
                self.logger.log(
                    "[react.runtime_ctx.effective] "
                    + json.dumps(
                        _effective_runtime_ctx_log_payload(self.runtime_ctx, self.bundle_props, settings),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    level="INFO",
                )
            except Exception:
                pass
        except Exception:
            self.runtime_ctx = RuntimeCtx(
                agent_id=runtime_agent_id,
                max_tokens=runtime_max_tokens,
                max_iterations=runtime_max_iterations,
                read_visible_max_text_symbols=_positive_int(getattr(settings, "AI_REACT_READ_VISIBLE_MAX_TEXT_SYMBOLS", None)),
                read_visible_max_tokens=_positive_int(getattr(settings, "AI_REACT_READ_VISIBLE_MAX_TOKENS", None)),
                read_visible_max_bytes=_positive_int(getattr(settings, "AI_REACT_READ_VISIBLE_MAX_BYTES", None)),
                read_visible_context_fraction=getattr(settings, "AI_REACT_READ_VISIBLE_CONTEXT_FRACTION", None),
                knowledge_read_visible_max_text_symbols=_positive_int(getattr(settings, "AI_REACT_KNOWLEDGE_READ_VISIBLE_MAX_TEXT_SYMBOLS", None)),
                knowledge_read_visible_max_tokens=_positive_int(getattr(settings, "AI_REACT_KNOWLEDGE_READ_VISIBLE_MAX_TOKENS", None)),
                knowledge_read_visible_max_bytes=_positive_int(getattr(settings, "AI_REACT_KNOWLEDGE_READ_VISIBLE_MAX_BYTES", None)),
                exec_text_preview_max_symbols=_positive_int(getattr(settings, "AI_REACT_EXEC_TEXT_PREVIEW_MAX_SYMBOLS", None)),
                tool_result_preview_max_text_symbols=_positive_int(getattr(settings, "AI_REACT_TOOL_RESULT_PREVIEW_MAX_TEXT_SYMBOLS", None)),
                render_thinking=runtime_render_thinking,
                line_numbers_mode=runtime_line_numbers_mode,
                story_snapshots_enabled=runtime_story_snapshots_enabled,
                event_source_pipeline_enabled=runtime_event_source_pipeline_enabled,
                debug_timeline=runtime_debug_timeline_enabled,
                debug_timeline_root=runtime_debug_timeline_root,
                debug_timeline_keep_files=runtime_debug_timeline_keep_files,
                workspace_implementation=settings.REACT_WORKSPACE_IMPLEMENTATION,
                workspace_git_repo=settings.REACT_WORKSPACE_GIT_REPO,
                multi_action_mode=settings.AI_REACT_AGENT_MULTI_ACTION,
            )
            _apply_react_session_settings(self.runtime_ctx, settings)
            self._sync_runtime_external_event_bus(self.runtime_ctx)
            self.ctx_browser = ContextBrowser(
                ctx_client=self.ctx_client,
                logger=self.logger,
                model_service=self.model_service,
                runtime_ctx=self.runtime_ctx,
            )
            self._register_workflow_external_event_hook()
            self._sync_runtime_ctx_bundle_props()
            try:
                self.logger.log(
                    "[react.runtime_ctx.effective] "
                    + json.dumps(
                        _effective_runtime_ctx_log_payload(self.runtime_ctx, self.bundle_props, settings),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    level="INFO",
                )
            except Exception:
                pass

    @staticmethod
    def get_prop_path(data: Dict[str, Any], path: str, default: Any = None) -> Any:
        if not path:
            return default
        cur: Any = data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def bundle_prop(self, path: str, default: Any = None) -> Any:
        return self.get_prop_path(self.bundle_props or {}, path, default)

    def _resolve_mcp_services_config(self) -> Any:
        props = self.bundle_props or {}

        raw = self.get_prop_path(props, "mcp.services", default=None)
        if isinstance(raw, dict) and raw:
            return copy.deepcopy(raw)
        if isinstance(raw, str) and raw.strip():
            return raw

        mcp_block = self.get_prop_path(props, "mcp", default=None)
        if isinstance(mcp_block, dict):
            if isinstance(mcp_block.get("mcpServers"), dict) and mcp_block.get("mcpServers"):
                return {"mcpServers": copy.deepcopy(mcp_block["mcpServers"])}
            if isinstance(mcp_block.get("servers"), dict) and mcp_block.get("servers"):
                return {"servers": copy.deepcopy(mcp_block["servers"])}

        raw = self.get_prop_path(props, "mcp_services", default=None)
        if isinstance(raw, dict) and raw:
            return copy.deepcopy(raw)
        if isinstance(raw, str) and raw.strip():
            return raw

        env_json = os.environ.get("MCP_SERVICES") or ""
        return env_json or None

    def _sync_runtime_ctx_bundle_props(self) -> None:
        runtime_ctx = getattr(self, "runtime_ctx", None)
        if runtime_ctx is None:
            return
        raw = self.get_prop_path(self.bundle_props or {}, "execution.runtime", default=None)
        if raw is None:
            raw = self.get_prop_path(self.bundle_props or {}, "exec_runtime")
        runtime_ctx.bundle_props = copy.deepcopy(self.bundle_props or {})
        runtime_ctx.exec_runtime = copy.deepcopy(normalize_exec_runtime_config(raw))
        try:
            settings = get_settings()
            agent_id = getattr(runtime_ctx, "agent_id", None)
            runtime_ctx.max_iterations = _react_max_iterations(self.bundle_props, settings, agent_id=agent_id)
            runtime_ctx.render_thinking = _react_render_thinking(self.bundle_props, settings, agent_id=agent_id)
            runtime_ctx.line_numbers_mode = _react_line_numbers_mode(self.bundle_props, settings, agent_id=agent_id)
            runtime_ctx.story_snapshots_enabled = _react_story_snapshots_enabled(self.bundle_props, agent_id=agent_id)
            runtime_ctx.event_source_pipeline_enabled = _react_event_source_pipeline_enabled(
                self.bundle_props,
                settings,
                agent_id=agent_id,
            )
            runtime_ctx.debug_timeline = _react_debug_timeline_enabled(
                self.bundle_props,
                settings,
                agent_id=agent_id,
            )
            runtime_ctx.debug_timeline_root = _react_debug_timeline_root(settings)
            runtime_ctx.debug_timeline_keep_files = _react_debug_timeline_keep_files(settings)
            runtime_ctx.agent_role_models = _react_role_models(self.bundle_props, agent_id=agent_id)
        except Exception:
            pass
        memory_cfg = self.get_prop_path(self.bundle_props or {}, "memory", default={}) or {}
        if not isinstance(memory_cfg, dict):
            memory_cfg = {}
        memory_enabled_raw = _bool_or_none(memory_cfg.get("enabled"))
        memory_enabled = bool(memory_enabled_raw) if memory_enabled_raw is not None else False
        announce_cfg = memory_cfg.get("announce") if isinstance(memory_cfg.get("announce"), dict) else {}
        announce_enabled_raw = _bool_or_none(announce_cfg.get("enabled"))
        announce_enabled = bool(announce_enabled_raw) if announce_enabled_raw is not None else False
        runtime_ctx.memory_enabled = memory_enabled
        runtime_ctx.memory_announce_enabled = bool(memory_enabled and announce_enabled)
        runtime_ctx.memory_scope_filter = str(announce_cfg.get("scope_filter") or "current_bundle").strip() or "current_bundle"
        runtime_ctx.memory_hotset_limit = _positive_int(announce_cfg.get("limit")) or 8
        try:
            timeout = float(announce_cfg.get("timeout_seconds") or 1.5)
        except Exception:
            timeout = 1.5
        runtime_ctx.memory_announce_timeout_seconds = max(0.1, min(timeout, 10.0))
        if not runtime_ctx.memory_announce_enabled:
            runtime_ctx.memory_hotset = []
            runtime_ctx.memory_hotset_error = None

    def _register_workflow_external_event_hook(self) -> None:
        ctx_browser = getattr(self, "ctx_browser", None)
        if ctx_browser is None:
            return
        try:
            ctx_browser.add_external_event_hook(self.on_external_event_received, start_listener=False)
        except TypeError:
            try:
                ctx_browser.add_external_event_hook(self.on_external_event_received)
            except Exception:
                pass
        except Exception:
            pass

    async def on_external_event_received(self, *, type: str, event: Any, blocks: List[Dict[str, Any]], **kwargs: Any) -> None:
        return None

    async def _refresh_user_memory_hotset_for_announce(self) -> None:
        runtime_ctx = getattr(self, "runtime_ctx", None)
        if runtime_ctx is None or not bool(getattr(runtime_ctx, "memory_announce_enabled", False)):
            return
        runtime_ctx.memory_hotset = []
        runtime_ctx.memory_hotset_error = None
        if getattr(self, "pg_pool", None) is None:
            runtime_ctx.memory_hotset_error = "pg_pool unavailable"
            return

        try:
            from kdcube_ai_app.apps.chat.sdk.context.memory import (
                MemoryScope,
                MemorySearchRequest,
                UserMemoryStore,
                normalize_scope_filter,
            )

            scope = MemoryScope(
                tenant=str(getattr(runtime_ctx, "tenant", "") or "").strip(),
                project=str(getattr(runtime_ctx, "project", "") or "").strip(),
                user_id=str(getattr(runtime_ctx, "user_id", "") or "").strip(),
                bundle_id=str(getattr(runtime_ctx, "bundle_id", "") or "").strip(),
            ).normalized()
            scope_filter = normalize_scope_filter(str(getattr(runtime_ctx, "memory_scope_filter", "") or "current_bundle"))
            runtime_ctx.memory_scope_filter = scope_filter
            limit = _positive_int(getattr(runtime_ctx, "memory_hotset_limit", None)) or 8
            store = UserMemoryStore(
                pg_pool=self.pg_pool,
                tenant=scope.tenant,
                project=scope.project,
            )
            try:
                prefs = await store.get_user_preferences(scope=scope)
                if prefs.get("memory_enabled") is False:
                    runtime_ctx.memory_hotset = []
                    runtime_ctx.memory_hotset_error = "disabled by user"
                    return
            except Exception:
                # Preference table may not exist until the memory schema
                # migration runs; keep legacy behavior in that case.
                pass
            timeout = float(getattr(runtime_ctx, "memory_announce_timeout_seconds", 1.5) or 1.5)
            rows = await asyncio.wait_for(
                store.search(
                    MemorySearchRequest(
                        scope=scope,
                        mode="hotset",
                        status="active",
                        visible_to_user=True,
                        include_private=False,
                        scope_filter=scope_filter,
                        limit=limit,
                    )
                ),
                timeout=max(0.1, timeout),
            )

            compact: List[Dict[str, Any]] = []
            for row in rows or []:
                memory = getattr(row, "memory", row)
                mem_scope = getattr(memory, "scope", scope)
                updated_at = getattr(memory, "updated_at", None)
                last_event_at = getattr(memory, "last_event_at", None)
                compact.append({
                    "id": str(getattr(memory, "id", "") or ""),
                    "bundle_id": str(getattr(mem_scope, "bundle_id", "") or ""),
                    "memory": str(getattr(memory, "memory", "") or ""),
                    "context": str(getattr(memory, "context", "") or ""),
                    "kind": str(getattr(memory, "kind", "") or ""),
                    "labels": list(getattr(memory, "labels", []) or []),
                    "keywords": list(getattr(memory, "keywords", []) or []),
                    "tier": getattr(memory, "tier", None),
                    "pinned": bool(getattr(memory, "pinned", False)),
                    "confidence_score": getattr(memory, "confidence_score", None),
                    "importance_score": getattr(memory, "importance_score", None),
                    "freshness_score": getattr(memory, "freshness_score", None),
                    "salience_score": getattr(memory, "salience_score", None),
                    "evidence_count": getattr(memory, "evidence_count", None),
                    "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at or ""),
                    "last_event_at": last_event_at.isoformat() if hasattr(last_event_at, "isoformat") else str(last_event_at or ""),
                    "score": getattr(row, "score", None),
                })
            runtime_ctx.memory_hotset = compact
        except Exception as e:
            runtime_ctx.memory_hotset = []
            runtime_ctx.memory_hotset_error = f"{type(e).__name__}: {e}"
            try:
                self.logger.log(f"[memory.announce] hotset unavailable: {traceback.format_exc()}", "WARNING")
            except Exception:
                pass

    def _resolve_runtime_ctx_bundle_storage(self) -> Optional[str]:
        try:
            bundle_storage_root = getattr(self, "bundle_storage_root", None)
            if callable(bundle_storage_root):
                resolved = bundle_storage_root()
                if resolved:
                    return str(resolved)
            from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec

            bundle_ws = storage_for_spec(
                spec=getattr(self.config, "ai_bundle_spec", None),
                tenant=getattr(getattr(self.comm_context, "actor", None), "tenant_id", None),
                project=getattr(getattr(self.comm_context, "actor", None), "project_id", None),
                ensure=True,
            )
            return str(bundle_ws) if bundle_ws else None
        except Exception:
            return None

    def rebind_request_context(
        self,
        *,
        comm_context: Optional[ExternalEventPayload] = None,
        pg_pool: Any = None,
        redis: Any = None,
    ) -> None:
        """
        Refresh request-bound state on cached singleton workflows.
        """
        if pg_pool is not None:
            self.pg_pool = pg_pool
        if redis is not None:
            self.redis = redis

        if comm_context is not None:
            self.comm_context = comm_context
            self.comm = build_comm_from_comm_context(
                comm_context,
                relay=build_relay_from_env(),
            )

            if getattr(self, "hosting_service", None) is not None:
                self.hosting_service.comm = self.comm
            if getattr(self, "turn_status", None) is not None:
                self.turn_status.emit_delta = self.comm.delta

            runtime_ctx = getattr(self, "runtime_ctx", None)
            if runtime_ctx is not None:
                runtime_ctx.tenant = comm_context.actor.tenant_id
                runtime_ctx.project = comm_context.actor.project_id
                runtime_ctx.user_id = comm_context.user.user_id
                runtime_ctx.user_type = comm_context.user.user_type
                runtime_ctx.timezone = comm_context.user.timezone
                runtime_ctx.conversation_id = comm_context.routing.conversation_id
                runtime_ctx.turn_id = comm_context.routing.turn_id
                runtime_ctx.agent_id = normalize_agent_id(
                    getattr(getattr(comm_context, "event", None), "agent_id", None)
                )
                runtime_ctx.bundle_storage = self._resolve_runtime_ctx_bundle_storage()
                self._sync_runtime_external_event_bus(runtime_ctx)
                self._sync_runtime_ctx_bundle_props()
        else:
            runtime_ctx = getattr(self, "runtime_ctx", None)
            if runtime_ctx is not None:
                self._sync_runtime_external_event_bus(runtime_ctx)
        self._sync_runtime_ctx_bundle_props()

    def _sync_runtime_external_event_bus(self, runtime_ctx: Any) -> None:
        if runtime_ctx is None:
            return
        runtime_ctx.external_event_source = self._external_event_source_for_runtime()
        runtime_ctx.external_event_wake_publisher = self._external_event_wake_publisher_for_runtime()

    def _external_event_source_for_runtime(self) -> Optional[Any]:
        redis = getattr(self, "redis", None)
        ctx = getattr(self, "comm_context", None)
        if redis is None or ctx is None:
            return None
        try:
            tenant = ctx.actor.tenant_id
            project = ctx.actor.project_id
            conversation_id = ctx.routing.conversation_id or ctx.routing.session_id
            event_ctx = getattr(ctx, "event", None)
            if event_ctx is None:
                user_id = ""
                agent_id = DEFAULT_REACT_AGENT_ID
            else:
                user_id = ctx.user.user_id or getattr(ctx.user, "fingerprint", None) or ""
                runtime_ctx = getattr(self, "runtime_ctx", None)
                agent_id = normalize_agent_id(getattr(runtime_ctx, "agent_id", None))
        except Exception:
            return None
        if not tenant or not project or not conversation_id:
            return None
        try:
            return build_conversation_external_event_source(
                redis=redis,
                tenant=tenant,
                project=project,
                conversation_id=conversation_id,
                user_id=user_id,
                agent_id=agent_id,
            )
        except Exception:
            return None

    def _external_event_wake_publisher_for_runtime(self) -> Optional[Any]:
        redis = getattr(self, "redis", None)
        ctx = getattr(self, "comm_context", None)
        if redis is None or ctx is None:
            return None
        try:
            tenant = ctx.actor.tenant_id
            project = ctx.actor.project_id
        except Exception:
            return None
        if not tenant or not project:
            return None
        return EventLaneWakePublisher(
            RedisEventLaneWakeEnqueuer(
                redis=redis,
                tenant=tenant,
                project=project,
            )
        )

    def react_debug_timeline_enabled(self, *, default: bool = False) -> bool:
        return _react_debug_timeline_enabled(
            self.bundle_props,
            get_settings(),
            default=default,
            agent_id=getattr(getattr(self, "runtime_ctx", None), "agent_id", None),
        )

    def resolve_exec_runtime(
        self,
        *,
        profile: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Resolve a runtime profile from RuntimeCtx.exec_runtime only.

        This helper does not read proc service env vars directly. Backend env
        fallback, where supported, happens later inside the selected execution
        runtime if the chosen profile leaves some keys unset.
        """
        runtime_ctx = getattr(self, "runtime_ctx", None)
        if runtime_ctx is None:
            return dict(overrides or {})
        return resolve_exec_runtime_profile(
            runtime=dict(getattr(runtime_ctx, "exec_runtime", {}) or {}),
            profile=profile,
            overrides=overrides,
        )

    # ---------- Comm ----------

    async def _emit(self, evt: Dict[str, Any]):
        raw = evt.get("data") or {}
        data = _to_jsonable(raw)
        await self.comm.event(
            agent=evt.get("agent"),
            type=evt.get("type","chat.step"),
            route=evt.get("route") or "chat.step",
            title=evt.get("title"),
            step=evt.get("step","event"),
            data=data,
            markdown=evt.get("markdown"),
            status=evt.get("status","update"),
            broadcast=evt.get("broadcast", False),
        )

    async def _emit_compaction_event(self, *, status: str, payload: Dict[str, Any] | None = None) -> None:
        payload_dict = _to_jsonable(dict(payload or {}))
        status_norm = str(status or payload_dict.get("status") or "update").strip().lower()
        title_by_status = {
            "started": "Context Compaction Started",
            "completed": "Context Compaction Completed",
            "skipped": "Context Compaction Skipped",
            "error": "Context Compaction Failed",
        }
        title = title_by_status.get(status_norm, "Context Compaction")
        payload_dict.setdefault("status", status_norm)
        await self._emit({
            "type": "chat.compaction",
            "route": "chat.compaction",
            "agent": "context.compaction",
            "step": "context.compaction",
            "status": status_norm,
            "title": title,
            "data": payload_dict,
            "broadcast": False,
        })

    def _envelope(self, evt: Dict[str, Any]) -> Dict[str, Any]:
        et = (evt.get("type") or "chat.step").strip()
        # ensure markdown for non-deltas
        if et != "chat.assistant.delta":
            try:
                ensure_event_markdown(evt)  # populates evt["markdown"] if missing
            except Exception:
                pass

        env: Dict[str, Any] = {
            "type": et,
            "ts": evt.get("ts") or _now_ms(),
            "service": dict(self._ctx.get("service") or {}),
            "conversation": dict(self._ctx.get("conversation") or {}),
            "event": {
                "agent": evt.get("agent"),
                "step": evt.get("step"),
                "status": evt.get("status") or "update",
                "title": evt.get("title"),
                "markdown": evt.get("markdown"),
                "timing": evt.get("timing") or {},
            },
            "data": evt.get("data") or {}
        }

        # delta-specific block
        if et in ("chat.assistant.delta", "chat.delta"):
            txt = (evt.get("text") or "").rstrip("\0")
            marker = evt.get("marker") or "answer"
            env["delta"] = {"text": txt, "marker": marker}
            # back-compat mirrors (client may still read these)
            env["text"] = txt

        # keep a safe version
        return _to_json_safe(env)

    async def emit_conversation_title(self, conversation_id: str, turn_id: str, title: str) -> None:
        """
        Emits a chat event for conversation title update.
        """
        if title:
            await self._emit({
                "type": "chat.conversation.title",
                "agent": "system",
                "step": "conversation_title",
                "status": "completed",
                "title": "Conversation Title Updated",
                "data": {
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "title": title
                },
                "broadcast": True
            })

    async def _emit_agent_error(self, *, origin: str, err: Exception, step: str, extra: Optional[dict] = None):
        """
        Emit a chat.error event to the client with JSON-serializable payload.
        `origin` is the logical agent name (e.g. "gate", "ctx.reconciler", "answer_generator").
        `step` must be unique within the workflow (e.g. "gate.service_error").
        """
        err_info = {
            "origin": origin,
            "type": err.__class__.__name__,
            "message": str(err),
        }

        if extra:
            err_info["extra"] = extra

        await self._emit({
            "type": "chat.error",
            "agent": origin,
            "step": step,
            "status": "error",
            "title": f"{origin} failed",
            "data": err_info,
        })

    async def emit_suggested_followups(self, suggested_followups: Optional[list[str]] = None):
        if not suggested_followups:
            return
        await self._emit({"type": "chat.followups", "agent": "answer.generator", "step": "followups",
                          "status": "completed", "title": "Follow-ups: User Shortcuts", "data": {"items": suggested_followups}})

    async def _persist_attachment_summaries(self, scratchpad) -> None:
        attachments = getattr(scratchpad, "user_attachments", None) or []
        if not attachments:
            return
        for a in attachments:
            if not isinstance(a, dict):
                continue
            if a.get("summary_persisted"):
                continue
            summary = (a.get("summary") or "").strip()
            if not summary:
                continue
            filename = (a.get("filename") or "attachment").strip()
            artifact_name = (a.get("artifact_name") or "").strip()
            payload = {
                "summary": summary,
                "text": (a.get("text") or "").strip(),
                "filename": filename,
                "artifact_name": artifact_name,
                "mime": (a.get("mime") or a.get("mime_type") or "").strip(),
                "size": a.get("size") or a.get("size_bytes"),
                "rn": a.get("rn"),
                "hosted_uri": a.get("hosted_uri") or a.get("source_path") or a.get("path"),
                "key": a.get("key"),
            }
            if self.ctx_client:
                content_str = attachment_summary_index_text(payload) if attachment_summary_index_text else str(payload)[:1000]
                embedding = None
                if self.model_service:
                    try:
                        [embedding] = await self.model_service.embed_texts([summary])
                    except Exception:
                        embedding = None
                await self.ctx_browser.save_artifact(
                    kind="user.attachment",
                    tenant=self.runtime_ctx.tenant,
                    project=self.runtime_ctx.project,
                    user_id=self.runtime_ctx.user_id,
                    conversation_id=self.runtime_ctx.conversation_id,
                    user_type=self.runtime_ctx.user_type,
                    turn_id=self.runtime_ctx.turn_id,
                    content=payload,
                    content_str=content_str,
                    embedding=embedding,
                    ttl_days=_ttl_for(self.runtime_ctx.user_type, 365),
                    meta={
                        "title": f"User Attachment Summary: {filename}",
                        "kind": "user.attachment",
                        "request_id": self._ctx["service"]["request_id"],
                    },
                    bundle_id=self.config.ai_bundle_spec.id,
                )
            a["summary_persisted"] = True

    def _topics_from_summary(self, summary: dict) -> List[str]:
        domain = (summary.get("domain") or "").strip()
        if not domain:
            return []
        return [d.strip() for d in domain.split(";") if d.strip()]

    def _merge_topics(self, primary: List[str], secondary: List[str]) -> List[str]:
        out = []
        for t in (primary or []) + (secondary or []):
            t = (t or "").strip()
            if t and t not in out:
                out.append(t)
        return out

    def _prefs_from_summary(self, summary: dict) -> tuple[List[dict], List[dict]]:
        prefs = summary.get("prefs") or {}
        assertions = []
        for a in (prefs.get("assertions") or []):
            key = a.get("key")
            if not key:
                continue
            entry = {
                "key": key,
                "value": a.get("value"),
                "severity": a.get("severity") or "prefer",
            }
            if a.get("scope"):
                entry["scope"] = a.get("scope")
            if a.get("applies_to"):
                entry["applies_to"] = a.get("applies_to")
            assertions.append(entry)
        exceptions = []
        for e in (prefs.get("exceptions") or []):
            key = e.get("key")
            if not key:
                continue
            entry = {
                "key": key,
                "value": e.get("value"),
                "severity": e.get("severity") or "avoid",
            }
            if e.get("scope"):
                entry["scope"] = e.get("scope")
            if e.get("applies_to"):
                entry["applies_to"] = e.get("applies_to")
            exceptions.append(entry)
        return assertions, exceptions

    def _assistant_signals_from_summary(self, summary: dict) -> List[dict]:
        signals = []
        for s in (summary.get("assistant_signals") or []):
            key = (s.get("key") or "").strip()
            if not key:
                continue
            entry = {
                "key": key,
                "value": s.get("value"),
            }
            if s.get("severity"):
                entry["severity"] = s.get("severity")
            if s.get("scope"):
                entry["scope"] = s.get("scope")
            if s.get("applies_to"):
                entry["applies_to"] = s.get("applies_to")
            signals.append(entry)
        return signals

    async def _persist_stream_artifacts(self) -> None:
        if not self.ctx_client:
            return
        try:
            tenant = self.runtime_ctx.tenant
            project = self.runtime_ctx.project
            user_id = self.runtime_ctx.user_id
            user_type = self.runtime_ctx.user_type
            conversation_id = self.runtime_ctx.conversation_id
            turn_id = self.runtime_ctx.turn_id
        except Exception:
            return

        all_deltas = self.comm.get_delta_aggregates(
            conversation_id=conversation_id, turn_id=turn_id, merge_text=True
        )
        canvas_and_tools_blocks = [d for d in all_deltas if d.get("marker") in ["canvas", "tool", "subsystem"] and (d.get("text") or d.get("chunks"))]

        subsystem_blocks = [d for d in all_deltas if d.get("marker") in ["subsystem"] and (d.get("text") or d.get("chunks"))]

        canvas_full = [
            {**{k: v for k, v in item.items() if k != "chunks"},
             "chunks_num": len(item.get("chunks") or [])}
            for item in canvas_and_tools_blocks
        ]
        canvas_idx = [
            {**{k: v for k, v in item.items() if k not in ("text", "chunks")},
             "text_size": len(item.get("text") or ""),
             "chunks_num": len(item.get("chunks") or [])}
            for item in canvas_and_tools_blocks
        ]

        if canvas_and_tools_blocks:
            await self.ctx_browser.save_artifact(
                kind="conv.artifacts.stream",
                tenant=tenant, project=project,
                turn_id=turn_id,
                user_id=user_id,
                conversation_id=conversation_id,
                bundle_id=self.config.ai_bundle_spec.id,
                user_type=user_type,
                content={"version": "v1", "items": canvas_full},
                content_str=json.dumps(canvas_idx),
                extra_tags=["conversation", "stream", "canvas"],
            )

        self.comm.clear_delta_aggregates(conversation_id=conversation_id, turn_id=turn_id)

    async def _snapshot_execution_tree(
            self,
            *,
            outdir: Optional[str],
            workdir: Optional[str],
            tenant: str, project: str, user: str, conversation_id: str,
            user_type: str, turn_id: str, codegen_run_id: str
    ):
        snap = await self.store.put_execution_snapshot(
            tenant=tenant, project=project, user=user, fingerprint=None,
            conversation_id=conversation_id, turn_id=turn_id,
            out_dir=outdir, pkg_dir=workdir,
            codegen_run_id=codegen_run_id,
            user_type=user_type
        )
        return snap

    def _current_turn_user_input_materialized_from_event_lane(self) -> bool:
        ctx_browser = getattr(self, "ctx_browser", None)
        if ctx_browser is None:
            return False
        try:
            reader_result_fn = getattr(ctx_browser, "last_external_event_reader_result", None)
            reader_result = reader_result_fn() if callable(reader_result_fn) else {}
        except Exception:
            return False
        if not isinstance(reader_result, dict):
            return False
        return bool(reader_result.get("current_turn_user_input_materialized"))

    async def persist_user_message(self, scratch: CTurnScratchpad):

        if getattr(scratch, "user_message_persisted", False):
            return
        turn_id = self._ctx["conversation"]["turn_id"]
        if self._current_turn_user_input_materialized_from_event_lane():
            scratch.user_message_persisted = True
            return
        ts = self._ctx["conversation"]["ts"]
        path = f"ar:{turn_id}.user.prompt"
        await self._persist_user_conversation_entry(
            scratchpad=scratch,
            text=scratch.user_text or scratch.short_text,
            ts=ts,
            turn_id=turn_id,
            path=path,
            user_event_type="event.user.prompt",
        )
        scratch.user_message_persisted = True
        scratch.persisted_turn_entry_paths.add(path)

    def _current_turn_blocks(self, *, turn_id: str) -> List[Dict[str, Any]]:
        if not self.ctx_browser or not getattr(self.ctx_browser, "timeline", None):
            return []
        try:
            return [
                b for b in (self.ctx_browser.timeline.blocks or [])
                if isinstance(b, dict) and str(b.get("turn_id") or "").strip() == turn_id
            ]
        except Exception:
            return []

    def _iter_turn_prompt_entries(self, *, turn_id: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for blk in self._current_turn_blocks(turn_id=turn_id):
            btype = str(blk.get("type") or "").strip()
            text = str(blk.get("text") or "").strip()
            path = str(blk.get("path") or "").strip()
            ts = str(blk.get("ts") or "").strip()
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            user_event_type: Optional[str] = None
            if btype == "user.prompt":
                user_event_type = str(meta.get("event_type") or "event.user.prompt").strip() or "event.user.prompt"
            elif btype in {"user.followup", "user.followup.preserved"}:
                user_event_type = "event.user.followup"
            elif btype in {"user.steer", "user.steer.preserved"}:
                user_event_type = "event.user.steer"
            else:
                continue
            if not text:
                continue
            entries.append({
                "text": text,
                "ts": ts,
                "path": path,
                "user_event_type": user_event_type,
            })
        return entries

    def _iter_turn_assistant_completion_entries(self, *, turn_id: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for blk in self._current_turn_blocks(turn_id=turn_id):
            if str(blk.get("type") or "").strip() != "assistant.completion":
                continue
            text = str(blk.get("text") or "").strip()
            path = str(blk.get("path") or "").strip()
            ts = str(blk.get("ts") or "").strip()
            if not text or not path:
                continue
            entries.append({
                "text": text,
                "ts": ts,
                "path": path,
            })
        return entries

    def _iter_turn_working_summary_entries(self, *, turn_id: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for blk in self._current_turn_blocks(turn_id=turn_id):
            if str(blk.get("type") or "").strip() != "conv.working.summary":
                continue
            text = str(blk.get("text") or "").strip()
            path = str(blk.get("path") or "").strip()
            ts = str(blk.get("ts") or "").strip()
            if not text or not path:
                continue
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            entries.append({
                "text": text,
                "ts": ts,
                "path": path,
                "meta": meta,
            })
        return entries

    # Tools that don't produce new substantive content — they only retrieve
    # or inspect what already exists. A turn whose only tool calls came from
    # this set is a "recovery session" (the agent was looking things up, not
    # producing). We tag its working summary so future memsearches can skip
    # the agent's own recovery activity by default, instead of recursively
    # surfacing it for every future query about the same topic.
    _RECOVERY_TOOL_IDS = frozenset({
        "react.memsearch",
        "react.read",
        "react.rg",
        "react.pull",
        "react.checkout",
        "react.plan",
        "react.hide",
    })

    def _is_recovery_turn(self, *, turn_id: str) -> bool:
        """
        Return True iff the turn called `react.memsearch` at least once and
        every tool it called was in the recovery/read-only set. Such a turn
        produced no new artifact; its working summary is metadata about the
        agent's lookup activity, not substantive content the user authored.
        """
        called_memsearch = False
        for blk in self._current_turn_blocks(turn_id=turn_id):
            if str(blk.get("type") or "").strip() != "react.tool.call":
                continue
            tid = ""
            payload_text = blk.get("text")
            if isinstance(payload_text, str) and payload_text:
                try:
                    payload = json.loads(payload_text)
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    tid = str(payload.get("tool_id") or "").strip()
            if not tid:
                meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
                tid = str(meta.get("tool_id") or blk.get("tool_id") or "").strip()
            if not tid:
                continue
            if tid not in self._RECOVERY_TOOL_IDS:
                # A non-recovery tool was called — this turn produced or
                # acted on something new.
                return False
            if tid == "react.memsearch":
                called_memsearch = True
        return called_memsearch

    def _iter_turn_internal_note_entries(self, *, turn_id: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for blk in self._current_turn_blocks(turn_id=turn_id):
            btype = str(blk.get("type") or "").strip()
            if btype not in {"react.note", "react.note.preserved"}:
                continue
            text = str(blk.get("text") or "").strip()
            path = str(blk.get("path") or "").strip()
            ts = str(blk.get("ts") or "").strip()
            if not text or not path:
                continue
            meta = blk.get("meta") if isinstance(blk.get("meta"), dict) else {}
            entries.append({
                "text": text,
                "ts": ts,
                "path": path,
                "meta": meta,
                "preserved": btype == "react.note.preserved",
                "note_tags": extract_note_tags(text),
            })
        return entries

    async def _persist_user_conversation_entry(
        self,
        *,
        scratchpad: CTurnScratchpad,
        text: str,
        ts: str,
        turn_id: str,
        path: str,
        user_event_type: Optional[str],
    ) -> Optional[str]:
        tenant, project, user, user_type = (
            self._ctx["service"]["tenant"],
            self._ctx["service"]["project"],
            self._ctx["service"]["user"],
            self._ctx["service"]["user_type"],
        )
        conversation_id = self._ctx["conversation"]["conversation_id"]
        text = str(text or "").strip()
        if not text or not path:
            return None
        truncated_text = truncate_text_by_tokens(text)
        [uvec] = await self.model_service.embed_texts([truncated_text])
        scratchpad.uvec = uvec
        msg_ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
        safe_path = re.sub(r"[^a-zA-Z0-9._-]+", "_", path).strip("_") or "prompt"
        msgid_u = f"{_mid('user', msg_ts)}-{safe_path}"
        tags = ["chat:user", f"turn:{turn_id}"] + [f"topic:{t}" for t in scratchpad.turn_topics_plain or []]
        if user_event_type:
            tags.append(f"event_type:{user_event_type}")
        await self.conv_idx.add_message(
            user_id=user,
            conversation_id=conversation_id,
            bundle_id=self.config.ai_bundle_spec.id,
            turn_id=turn_id,
            role="user",
            text=text,
            hosted_uri="index_only",
            ts=ts,
            tags=tags,
            ttl_days=_ttl_for(user_type, 365),
            user_type=user_type,
            embedding=uvec,
            message_id=msgid_u,
        )
        scratchpad.persisted_turn_entry_paths.add(path)
        return msgid_u

    async def persist_turn_prompt_entries(self, scratchpad: CTurnScratchpad) -> int:
        turn_id = self._ctx["conversation"]["turn_id"]
        persisted = 0
        for entry in self._iter_turn_prompt_entries(turn_id=turn_id):
            path = str(entry.get("path") or "").strip()
            if not path or path in scratchpad.persisted_turn_entry_paths:
                continue
            msgid = await self._persist_user_conversation_entry(
                scratchpad=scratchpad,
                text=str(entry.get("text") or ""),
                ts=str(entry.get("ts") or self._ctx["conversation"]["ts"]),
                turn_id=turn_id,
                path=path,
                user_event_type=entry.get("user_event_type"),
            )
            if msgid:
                persisted += 1
        return persisted

    async def persist_assistant(self, scratchpad: TurnScratchpad):

        tenant, project, user, user_type = (
            self._ctx["service"]["tenant"],
            self._ctx["service"]["project"],
            self._ctx["service"]["user"],
            self._ctx["service"]["user_type"],
        )
        conversation_id, turn_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"]

        entries = self._iter_turn_assistant_completion_entries(turn_id=turn_id)
        if not entries and (scratchpad.answer_raw or scratchpad.answer):
            entries = [{
                "text": scratchpad.answer_raw or scratchpad.answer or "",
                "ts": getattr(scratchpad, "ended_at", None) or datetime.datetime.utcnow().isoformat() + "Z",
                "path": f"ar:{turn_id}.assistant.completion",
            }]

        persisted = 0
        persisted_completions = 0
        persisted_summaries = 0
        persisted_notes = 0
        t14, ms14 = _tstart()
        for entry in entries:
            path = str(entry.get("path") or "").strip()
            if not path or path in scratchpad.persisted_turn_entry_paths:
                continue
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            [avec] = await self.model_service.embed_texts([text])
            scratchpad.avec = avec
            msg_ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
            safe_path = re.sub(r"[^a-zA-Z0-9._-]+", "_", path).strip("_") or "assistant"
            msgid_a = f"{_mid('assistant', msg_ts)}-{safe_path}"
            await self.conv_idx.add_message(
                user_id=user,
                conversation_id=conversation_id,
                bundle_id=self.config.ai_bundle_spec.id,
                turn_id=turn_id,
                role="assistant",
                text=text,
                hosted_uri="index_only",
                ts=str(entry.get("ts") or datetime.datetime.utcnow().isoformat() + "Z"),
                tags=["chat:assistant", f"turn:{turn_id}"] + [f"topic:{t}" for t in scratchpad.turn_topics_plain or []],
                ttl_days=_ttl_for(user_type, 365),
                user_type=user_type,
                embedding=avec,
                message_id=msgid_a,
            )
            scratchpad.persisted_turn_entry_paths.add(path)
            persisted += 1
            persisted_completions += 1
        summary_entries = self._iter_turn_working_summary_entries(turn_id=turn_id)
        is_recovery_turn = self._is_recovery_turn(turn_id=turn_id) if summary_entries else False
        for entry in summary_entries:
            path = str(entry.get("path") or "").strip()
            if not path or path in scratchpad.persisted_turn_entry_paths:
                continue
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            [avec] = await self.model_service.embed_texts([text])
            scratchpad.avec = avec
            msg_ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
            safe_path = re.sub(r"[^a-zA-Z0-9._-]+", "_", path).strip("_") or "working_summary"
            msgid_a = f"{_mid('assistant', msg_ts)}-{safe_path}"
            meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
            tags = [
                "chat:assistant",
                "chat:summary",
                "kind:working.summary",
                f"turn:{turn_id}",
            ] + [f"topic:{t}" for t in scratchpad.turn_topics_plain or []]
            if is_recovery_turn:
                # The agent only called memsearch / read / pull / etc. — no new
                # artifact was produced. Tag the summary so memsearch can
                # exclude these by default. Otherwise the agent's own
                # search-about-X summaries dominate future searches for X.
                tags.append("kind:react.recovery.session")
            scope = str(meta.get("summary_scope") or "").strip()
            if scope:
                tags.append(f"summary_scope:{scope}")
            if meta.get("assistant_completion_attempt_index") is not None:
                tags.append(f"summary_attempt:{meta.get('assistant_completion_attempt_index')}")
            anchors_text = ""
            try:
                from kdcube_ai_app.apps.chat.sdk.context.vector.anchors import parse_retrieval_anchors
                anchors_text = parse_retrieval_anchors(text)
            except Exception:
                anchors_text = ""
            await self.conv_idx.add_message(
                user_id=user,
                conversation_id=conversation_id,
                bundle_id=self.config.ai_bundle_spec.id,
                turn_id=turn_id,
                role="assistant",
                text=text,
                hosted_uri="index_only",
                ts=str(entry.get("ts") or datetime.datetime.utcnow().isoformat() + "Z"),
                tags=tags,
                ttl_days=_ttl_for(user_type, 365),
                user_type=user_type,
                embedding=avec,
                message_id=msgid_a,
                anchors_text=anchors_text,
            )
            scratchpad.persisted_turn_entry_paths.add(path)
            persisted += 1
            persisted_summaries += 1
        note_entries = self._iter_turn_internal_note_entries(turn_id=turn_id)
        for entry in note_entries:
            path = str(entry.get("path") or "").strip()
            if not path or path in scratchpad.persisted_turn_entry_paths:
                continue
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            [avec] = await self.model_service.embed_texts([text])
            scratchpad.avec = avec
            msg_ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
            safe_path = re.sub(r"[^a-zA-Z0-9._-]+", "_", path).strip("_") or "react_note"
            msgid_a = f"{_mid('artifact', msg_ts)}-{safe_path}"
            tags = [
                "chat:internal_note",
                "kind:react.note",
                "visibility:internal",
                f"turn:{turn_id}",
            ] + [f"topic:{t}" for t in scratchpad.turn_topics_plain or []]
            if entry.get("preserved"):
                tags.append("kind:react.note.preserved")
            for tag in entry.get("note_tags") or []:
                tag_text = str(tag or "").strip().upper()
                if tag_text:
                    tags.append(f"note_tag:{tag_text}")
            await self.conv_idx.add_message(
                user_id=user,
                conversation_id=conversation_id,
                bundle_id=self.config.ai_bundle_spec.id,
                turn_id=turn_id,
                role="artifact",
                text=text,
                hosted_uri="index_only",
                ts=str(entry.get("ts") or datetime.datetime.utcnow().isoformat() + "Z"),
                tags=tags,
                ttl_days=_ttl_for(user_type, 365),
                user_type=user_type,
                embedding=avec,
                message_id=msgid_a,
            )
            scratchpad.persisted_turn_entry_paths.add(path)
            persisted += 1
            persisted_notes += 1
        if persisted <= 0:
            return
        timing_assist_persist = _tend(t14, ms14)
        step_title = "Assistant Messages Persisted"
        await self._emit({"type": "chat.step", "agent": "store", "step": "conversation.persist.assistant_message",
                          "status": "completed", "title": step_title,
                          "data": {
                              "count": persisted,
                              "assistant_completion_count": persisted_completions,
                              "working_summary_count": persisted_summaries,
                              "internal_note_count": persisted_notes,
                          },
                          "timing": timing_assist_persist})
        scratchpad.timings.append({
            "title": step_title,
            "elapsed_ms": timing_assist_persist["elapsed_ms"]
        })

    async def _publish_git_workspace_if_needed(self) -> Optional[Dict[str, Any]]:
        runtime_ctx = getattr(self, "runtime_ctx", None)
        if runtime_ctx is None:
            return None
        impl = str(getattr(runtime_ctx, "workspace_implementation", "custom") or "custom").strip().lower()
        if impl != "git":
            return None
        outdir_raw = str(getattr(runtime_ctx, "outdir", "") or "").strip()
        if not outdir_raw:
            return None
        turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
        publish_current_turn_git_workspace = _react_symbol(
            "git_workspace",
            "publish_current_turn_git_workspace",
        )
        try:
            result = await publish_current_turn_git_workspace(
                runtime_ctx=runtime_ctx,
                outdir=pathlib.Path(outdir_raw),
                logger=self.logger,
            )
            self._contribute_workspace_publish_event(
                status="succeeded",
                payload=result,
            )
            return result
        except Exception as exc:
            self.logger.log(traceback.format_exc(), level="ERROR")
            detail = str(exc).strip()
            self._contribute_workspace_publish_event(
                status="failed",
                payload={
                    "turn_id": turn_id,
                    "workspace_implementation": "git",
                    "message": detail,
                    "error": exc.__class__.__name__,
                },
            )
            raise TurnPhaseError(
                f"Failed to save git workspace progress: {detail}" if detail else "Failed to save git workspace progress.",
                code="workspace_publish_failed",
                data={
                    "workspace_implementation": "git",
                    "turn_id": turn_id,
                    "error": exc.__class__.__name__,
                    "cause": detail,
                },
            ) from exc

    def _contribute_workspace_publish_event(
        self,
        *,
        status: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.ctx_browser or not getattr(self.ctx_browser, "timeline", None):
            return
        runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
        turn_id = str(getattr(runtime_ctx, "turn_id", "") or "").strip()
        if not turn_id:
            return
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        body = {
            "status": status,
            "turn_id": turn_id,
            "workspace_implementation": str(getattr(runtime_ctx, "workspace_implementation", "") or ""),
            **dict(payload or {}),
        }
        block = self.ctx_browser.timeline.block(
            type="react.workspace.publish",
            author="react.workspace",
            turn_id=turn_id,
            ts=ts,
            mime="application/json",
            path=f"ar:{turn_id}.react.workspace.publish",
            text=json.dumps(body, ensure_ascii=False, indent=2),
            meta={"status": status},
        )
        self.ctx_browser.contribute(blocks=[block])

    async def _summarize_user_attachments(self, scratchpad: CTurnScratchpad) -> None:
        if not (scratchpad.user_attachments or []):
            return
        try:
            max_ctx_chars = int(os.getenv("ATTACHMENT_SUMMARY_MAX_CONTEXT_CHARS", "12000"))
        except Exception:
            max_ctx_chars = 12000
        try:
            max_tokens = int(os.getenv("ATTACHMENT_SUMMARY_MAX_TOKENS", "600"))
        except Exception:
            max_tokens = 600

        async with with_accounting(
                self.config.ai_bundle_spec.id,
                agent="attachment.summarizer",
                metadata={"agent": "attachment.summarizer"},
        ):
            from kdcube_ai_app.apps.chat.sdk.tools.backends.summary.attachment_summary import (
                summarize_user_attachments_for_turn_log,
            )
            items = await summarize_user_attachments_for_turn_log(
                svc=self.model_service,
                user_text=scratchpad.user_text or "",
                user_attachments=list(scratchpad.user_attachments or []),
                max_ctx_chars=max_ctx_chars,
                max_tokens=max_tokens,
            )
            scratchpad.user_attachments = items

    async def handle_conversation_title(self, *, scratchpad: CTurnScratchpad, pre_out: dict):
        conversation_id = self.runtime_ctx.conversation_id

        # Conversation title now stored in timeline
        if scratchpad.is_new_conversation:
            conversation_title = (pre_out.get("conversation_title") or "").strip()
            scratchpad.conversation_title = conversation_title
            try:
                if self.ctx_browser and self.ctx_browser.timeline:
                    self.ctx_browser.timeline.set_conversation_title(conversation_title)
                    if not self.ctx_browser.timeline.conversation_started_at:
                        self.ctx_browser.timeline.set_conversation_started_at(self.runtime_ctx.started_at or "")
                    self.ctx_browser.timeline.write_local()
            except Exception:
                pass
        await self.emit_conversation_title(conversation_id=conversation_id, turn_id=self._ctx["conversation"]["turn_id"], title=scratchpad.conversation_title)

    async def handle_feedback(self, scratchpad: TurnScratchpad, gate):
        tenant, project, user, user_type = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"]
        conversation_id = self._ctx["conversation"]["conversation_id"]
        current_turn_id = scratchpad.turn_id  # Add this - the turn where feedback was given

        fb = gate.get("feedback") or {}
        self.logger.log(f"Feedback {fb}; conversation_id={conversation_id};")

        FEEDBACK_MIN_CONF = 0.70
        feedback_text = (fb.get("text") or "").strip()
        feedback_confidence = float(fb.get("confidence") or 0.0)
        reaction = fb.get("reaction")

        target_tid = fb.get("turn_id")
        match_targets = list((gate.get("feedback_match_targets") or []))


        if feedback_text and feedback_confidence >= FEEDBACK_MIN_CONF:
            try:
                # Custom scoring function that prioritizes similarity
                def feedback_scoring(sim: float, rec: float, ts: str) -> float:
                    # Prioritize similarity heavily, add small recency bias
                    return 0.85 * sim + 0.15 * rec

                target_tid, hits = await self.ctx_browser.search(
                    targets=match_targets,
                    user=user,
                    conv=conversation_id,
                    scoring_mode="custom",
                    custom_score_fn=feedback_scoring,
                    top_k=5,
                    days=365,
                    with_payload=True
                )
                hits = [{**h, "log_payload": h.get("payload") or {}} for h in hits]

                target_turn = next(iter([h for h in hits or [] if h["turn_id"] == target_tid]), None)

                # emit feedback block regardless of search success
                reaction_payload = {
                    "origin": "user",
                    "reaction": reaction,
                    "confidence": feedback_confidence,
                    "text": feedback_text,
                    "from_turn_id": target_tid,
                    "ts": datetime.datetime.utcnow().isoformat() + "Z",
                }
                self.ctx_browser.contribute_feedback(
                    reaction=reaction_payload,
                )

                if target_tid:
                    try:
                        self.logger.log(f"Feedback target turn: {target_turn or target_tid}; conversation_id={conversation_id};")
                        feedback_ts = datetime.datetime.utcnow().isoformat() + "Z"

                        # Build machine-inferred feedback (no reaction field for machine feedback)
                        scratchpad.detected_feedback = {
                            "turn_id": target_tid,
                            "text": feedback_text,
                            "confidence": feedback_confidence,
                            "reaction": reaction,
                            "ts": feedback_ts,
                            "origin": "machine"  # mark as machine-inferred
                        }

                        # 1) Log to current turn (where feedback was given) with origin="machine"
                        await self.ctx_client.append_reaction_to_turn_log(
                            turn_id=target_tid,
                            bundle_id=self.config.ai_bundle_spec.id,
                            reaction=scratchpad.detected_feedback,
                            tenant=tenant, project=project, user=user,
                            fingerprint=None, user_type=user_type,
                            conversation_id=conversation_id,
                            origin="machine",
                        )

                        # 2) Apply feedback to target turn log (update the actual turn)
                        await self.ctx_client.apply_feedback_to_turn_log(
                            tenant=tenant,
                            project=project,
                            user=user,
                            user_type=user_type,
                            conversation_id=conversation_id,
                            turn_id=target_tid,  # The turn being commented on
                            bundle_id=self.config.ai_bundle_spec.id,
                            feedback={
                                "text": feedback_text,
                                "confidence": feedback_confidence,
                                "ts": feedback_ts,
                                "from_turn_id": current_turn_id,  # Where the feedback came from
                                "origin": "machine",  # mark as machine-inferred
                                "reaction": reaction
                            }
                        )

                        # 3) Format details for logging
                        target_turn_details = ""
                        if target_turn:
                            ts = target_turn.get('ts')
                            # Handle both datetime objects and ISO strings
                            if isinstance(ts, str):
                                ts_str = ts[:16]
                            elif hasattr(ts, 'isoformat'):
                                ts_str = ts.isoformat()[:16]
                            else:
                                ts_str = str(ts)[:16] if ts else ""

                            if ts_str:
                                target_turn_details = f" originated on {ts_str}"
                        trace_ = (
                            f"{feedback_text} (confidence={feedback_confidence}; "
                            f"to turn {target_tid}{target_turn_details}; origin=machine)"
                        )
                        self.logger.log(f"Feedback applied. {trace_}; conversation_id={conversation_id};")

                    except Exception:
                        self.logger.log(traceback.format_exc(), "ERROR")
            except Exception:
                self.logger.log(traceback.format_exc(), "ERROR")

    # ------ streaming ---------
    async def _emit_turn_work_status(self, choices: List[str]) -> None:
        if not choices:
            return
        await self.turn_status.send(random.choice(choices))

    async def _emit_thinking_delta(self, *, agent: str, text: str, completed: bool = False) -> None:
        if not text and not completed:
            return
        idx = self._thinking_delta_idx.get(agent, 0)
        if text:
            self._thinking_delta_idx[agent] = idx + 1
        await self.comm.delta(
            text=text,
            index=idx,
            marker="thinking",
            agent=agent,
            completed=completed,
            format="text",
        )

    def mk_streamer(self, agent: str):
        async def _emit(text: str, completed: bool = False, **_):
            await self._emit_thinking_delta(agent=agent, text=text, completed=completed)
        return _emit

    def mk_thinking_streamer(self, agent: str):
        return self.mk_streamer(agent)

    async def _emit_answer_delta(self, *, text: str, completed: bool = False, agent: str = "answer.generator") -> None:
        if not text and not completed:
            return
        idx = self._answer_delta_idx
        if text:
            self._answer_delta_idx = idx + 1
        await self.comm.delta(
            text=text,
            index=idx,
            marker="answer",
            agent=agent,
            completed=completed,
            format="markdown",
        )

    async def _emit_committed_answer_once(self, scratchpad: TurnScratchpad, *, agent: str = "assistant.completion") -> None:
        answer_text = str(getattr(scratchpad, "answer", "") or "")
        if not answer_text:
            return
        if bool(getattr(scratchpad, "_final_answer_delta_emitted", False)):
            return
        rendered_answer = answer_text
        try:
            sources_pool = list(getattr(getattr(self, "ctx_browser", None), "sources_pool", None) or [])
            citation_map = citations_module.build_citation_map_from_sources(sources_pool)
            if citation_map:
                rendered_answer = citations_module.replace_citation_tokens_batch(answer_text, citation_map)
        except Exception:
            rendered_answer = answer_text
        await self._emit_answer_delta(text=rendered_answer, completed=False, agent=agent)
        await self._emit_answer_delta(text="", completed=True, agent=agent)
        scratchpad._final_answer_delta_emitted = True
    # ------ end of streaming ---------

    def bundle_root(self):
        spec = self.config.ai_bundle_spec
        if spec and spec.module and spec.path:
            from kdcube_ai_app.infra.plugin.bundle_registry import resolve_bundle_root
            bundle_root = resolve_bundle_root(spec.path, spec.module)
        else:
            # Fallback: directory above orchestrator/ (the bundle root)
            bundle_root = pathlib.Path(__file__).resolve().parents[1]
        return bundle_root

    def build_react(self,
                    scratchpad: TurnScratchpad,
                    mod_tools_spec: Optional[List[Dict[str, Any]]] = None,
                    mcp_tools_spec: Optional[List[Dict[str, Any]]] = None,
                    tools_runtime: Optional[Dict[str, str]] = None,
                    custom_skills_root: Optional[str] = None,
                    skills_visibility_agents_config: Optional[Dict[str, Dict[str, Any]]] = None,
                    additional_instructions: Optional[str] = None,
                    instruction_body: Optional[str] = None,
                    instruction_blocks: Optional[List[str]] = None,
                    event_source_specs: Optional[List[Dict[str, Any]]] = None,
                    story_snapshots_enabled: Optional[bool] = None,
                    include_tool_catalog: Optional[bool] = None,
                    include_skill_gallery: Optional[bool] = None) -> Any:

        bundle_root = self.bundle_root()
        react_version = _react_agent_version()

        async def _kb_proxy(query: str, top_n: int = 8, providers: Optional[List[str]] = None):
            vec = (await self.model_service.embed_texts([query]))[0]
            return await self.kb.hybrid_search(
                query=query, embedding=vec, top_n=top_n,
                include_expired=False, providers=(providers or None)
            )
        # self.conv_memories.bind_ctx_client(self.ctx_client)
        if custom_skills_root is None:
            candidate = bundle_root / "skills"
            if candidate.exists():
                custom_skills_root = candidate

        tool_subsystem, mcp_subsystem = create_tool_subsystem_with_mcp(
            service=self.model_service,
            comm=self.comm,
            logger=self.logger,
            bundle_spec=self.config.ai_bundle_spec,
            context_rag_client=self.ctx_client,
            registry={
                "kb_client": self.kb,
                "pg_pool": self.pg_pool,
                "redis": self.redis,
                "bundle_props": self.bundle_props,
                "comm_context": self.comm_context,
                "config": self.config,
                "client_id": getattr(getattr(self, "runtime_ctx", None), "agent_id", None),
            },
            raw_tool_specs=mod_tools_spec,
            tool_runtime=tools_runtime,
            event_specs=event_source_specs,
            mcp_tool_specs=mcp_tools_spec or [],
            mcp_services_config=self._resolve_mcp_services_config(),
            mcp_env_json=os.environ.get("MCP_SERVICES") or "",
            hosting_service=self.hosting_service,
        )

        tools = tool_subsystem or ToolSubsystem(
            service=self.model_service,
            comm=self.comm,
            bundle_spec=self.config.ai_bundle_spec,
            logger=self.logger,
            context_rag_client=self.ctx_client,
            registry={
                "kb_client": self.kb,
                "pg_pool": self.pg_pool,
                "redis": self.redis,
                "bundle_props": self.bundle_props,
                "comm_context": self.comm_context,
                "config": self.config,
                "client_id": getattr(getattr(self, "runtime_ctx", None), "agent_id", None),
            },
            mcp_subsystem=mcp_subsystem,
            tool_runtime=tools_runtime,
            event_specs=event_source_specs,
            hosting_service=self.hosting_service,
        )
        try:
            self.runtime_ctx.event_sources = getattr(tools, "event_sources", None)
        except Exception:
            pass
        skills = SkillsSubsystem(
            descriptor={
                "custom_skills_root": str(custom_skills_root) if custom_skills_root else None,
                "agents_config": skills_visibility_agents_config,
            },
            bundle_root=bundle_root,
        )
        def _first_react_prop(*keys: str, default: Any = None) -> Any:
            value, _source = _react_config_lookup(
                self.bundle_props or {},
                *keys,
                agent_id=getattr(getattr(self, "runtime_ctx", None), "agent_id", None),
                default=default,
            )
            return value

        def _str_list(value: Any) -> List[str]:
            if value is None:
                return []
            if isinstance(value, str):
                return [value] if value.strip() else []
            if isinstance(value, (list, tuple)):
                return [str(item) for item in value if str(item or "").strip()]
            return [str(value)]

        if additional_instructions is None:
            additional_instructions = _first_react_prop("additional_instructions")
        raw_instructions = _first_react_prop("instructions")
        if instruction_body is None:
            if isinstance(raw_instructions, str):
                instruction_body = raw_instructions
            else:
                instruction_body = _first_react_prop(
                    "instructions.body",
                    "instruction_body",
                )
        if instruction_blocks is None:
            if isinstance(raw_instructions, (list, tuple)):
                instruction_blocks = _str_list(raw_instructions)
            else:
                instruction_blocks = _str_list(_first_react_prop(
                    "instructions.blocks",
                    "instruction_blocks",
                ))
        if include_tool_catalog is None:
            include_tool_catalog = _bool_or_none(_first_react_prop(
                "instructions.include_tool_catalog",
            ))
        if include_skill_gallery is None:
            include_skill_gallery = _bool_or_none(_first_react_prop(
                "instructions.include_skill_gallery",
            ))
        if include_tool_catalog is None:
            include_tool_catalog = True
        if include_skill_gallery is None:
            include_skill_gallery = True

        extra_instructions = str(additional_instructions or "").strip()
        custom_instruction_body = str(instruction_body or "").strip()
        custom_instruction_blocks = _str_list(instruction_blocks)
        effective_story_snapshots_enabled = (
            bool(story_snapshots_enabled)
            if story_snapshots_enabled is not None
            else bool(getattr(self.runtime_ctx, "story_snapshots_enabled", False))
        )
        if effective_story_snapshots_enabled:
            try:
                from kdcube_ai_app.apps.chat.sdk.skills.instructions.shared_instructions_lite import (
                    get_lite_instruction_block,
                )

                story_snapshot_block = get_lite_instruction_block("REACT_LITE_STORY_SNAPSHOTS")
            except Exception:
                story_snapshot_block = ""
            already_present = (
                "[STORY SNAPSHOTS]" in extra_instructions
                or "[STORY SNAPSHOTS]" in custom_instruction_body
                or any(
                    str(item or "").strip() == "REACT_LITE_STORY_SNAPSHOTS"
                    or "[STORY SNAPSHOTS]" in str(item or "")
                    for item in custom_instruction_blocks
                )
            )
            if story_snapshot_block and not already_present:
                extra_instructions = f"{extra_instructions}\n\n{story_snapshot_block}".strip()
        try:
            if extra_instructions:
                compact = re.sub(r"\s+", " ", extra_instructions)
                head = compact[:220]
                tail = compact[-220:]
                self.logger.log(
                    f"[react.{react_version}] agent admin customization provided "
                    f"len={len(extra_instructions)} head={head!r} tail={tail!r}",
                    level="INFO",
                )
            else:
                self.logger.log(
                    f"[react.{react_version}] agent admin customization not provided",
                    level="INFO",
                )
            if custom_instruction_body or custom_instruction_blocks:
                self.logger.log(
                    f"[react.{react_version}] custom instruction composition "
                    f"body={'yes' if custom_instruction_body else 'no'} "
                    f"blocks={len(custom_instruction_blocks)} "
                    f"include_tool_catalog={bool(include_tool_catalog)} "
                    f"include_skill_gallery={bool(include_skill_gallery)} "
                    f"story_snapshots_enabled={effective_story_snapshots_enabled}",
                    level="INFO",
                )
        except Exception:
            pass
        ReactSolver = _react_symbol("runtime", "ReactSolverV2")
        react = ReactSolver(
            service=self.model_service,
            logger=self.logger,
            tools_subsystem=tools,     # exposes .tools to React
            skills_subsystem=skills,
            comm=self.comm,
            comm_context=self.comm_context,
            hosting_service=self.hosting_service,
            ctx_browser=self.ctx_browser,
            scratchpad=scratchpad,
            additional_instructions=extra_instructions or None,
            instruction_body=custom_instruction_body or None,
            instruction_blocks=custom_instruction_blocks or None,
            include_tool_catalog=bool(include_tool_catalog),
            include_skill_gallery=bool(include_skill_gallery),
        )
        try:
            self.logger.log(
                f"[react.{react_version}] build_react version={react_version} "
                f"multi_action_mode={getattr(self.runtime_ctx, 'multi_action_mode', None)} "
                f"story_snapshots_enabled={effective_story_snapshots_enabled}",
                level="INFO",
            )
        except Exception:
            pass
        return react

    # -------------------- Create solver --------------------
    async def report_timings(self, scratchpad: CTurnScratchpad, ms0u, total_ms):

        timings_list = [t for t in scratchpad.timings if isinstance(t.get("elapsed_ms"), int)]
        agg = {}
        order = []
        for t in timings_list:
            title_i = (t.get("title") or t.get("step") or "").strip() or "(untitled)"
            if title_i not in agg:
                agg[title_i] = 0
                order.append(title_i)
            agg[title_i] += int(t.get("elapsed_ms") or 0)

        rows = [(title_i, agg[title_i]) for title_i in order]
        rows.append(("TOTAL", total_ms))

        ms_pretty_table = _format_ms_table(rows)
        ms_markdown = _format_ms_table_markdown(scratchpad.timings)
        step_title = "Turn Summary (Timings)"
        await self._emit({"type": "chat.turn.summary", "agent": "turn_controller", "step": "turn.summary",
                          "status": "completed",
                          "markdown": f"{ms_markdown}",
                          "title": step_title, "data": {"elapsed_ms": total_ms},
                          "timing": {"started_ms": ms0u, "ended_ms": _now_ms(), "elapsed_ms": total_ms}})

        # Put it right in your face in the console and also via logger
        self.logger.log("\n" + ms_pretty_table + "\n")
        return ms_pretty_table, ms_markdown, timings_list

    # -------------------- Create turn --------------------
    async def construct_turn_and_scratchpad(self, payload: dict) -> CTurnScratchpad:

        rid = payload["request_id"]
        tenant, project, user = payload["tenant"], payload["project"], payload["user"]
        session_id = payload.get("session_id")
        conversation_id = payload.get("conversation_id") or session_id
        user_type = payload.get("user_type") or "anonymous"
        turn_id = payload.get("turn_id")
        external_events = payload.get("external_events") if isinstance(payload.get("external_events"), list) else []
        text = external_events_text(external_events)
        attachments = external_event_attachment_payloads(external_events)

        # bind for envelope composition
        self._ctx["service"] = {"request_id": rid, "tenant": tenant, "project": project,
                                "user": user, "user_type": user_type, "session_id": session_id}
        self._ctx["conversation"] = {"conversation_id": conversation_id,
                                     "turn_id": turn_id,
                                     "ts": datetime.datetime.utcnow().isoformat() + "Z"}
        scratchpad = CTurnScratchpad(user=user,
                                     conversation_id=conversation_id,
                                     turn_id=turn_id,
                                     text=text,
                                     attachments=attachments,
                                     gate_out_class=self.gate_out_class)
        scratchpad.user_ts = self._ctx["conversation"].get("ts")
        return scratchpad

    async def _ingest_user_attachments(self, *, attachments: list) -> list:
        return await ingest_user_attachments(attachments=attachments, store=self.store)

    async def _materialize_current_turn_user_attachments(self, scratchpad: CTurnScratchpad) -> None:
        items = getattr(scratchpad, "user_attachments", None) or []
        if not items:
            return
        turn_id = (getattr(scratchpad, "turn_id", "") or getattr(self.runtime_ctx, "turn_id", "") or "").strip()
        if not turn_id:
            return
        outdir_raw = (getattr(self.runtime_ctx, "outdir", "") or "").strip()
        if not outdir_raw and self.ctx_browser:
            try:
                _workdir, outdir = await self.ctx_browser._ensure_workspace()
                outdir_raw = str(outdir)
            except Exception:
                outdir_raw = ""
        if not outdir_raw:
            return

        attachments_dir = pathlib.Path(outdir_raw) / turn_id / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)
        used: Dict[str, int] = {}

        def _safe_name(raw: str, fallback: str) -> str:
            name = pathlib.PurePath(str(raw or "").strip()).name
            if not name or name in {".", ".."}:
                name = fallback
            count = used.get(name, 0) + 1
            used[name] = count
            if count <= 1:
                return name
            stem = pathlib.PurePath(name).stem or "attachment"
            suffix = pathlib.PurePath(name).suffix
            return f"{stem}_{count}{suffix}"

        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            filename = _safe_name(item.get("filename") or item.get("name"), f"attachment_{idx + 1}.bin")
            data: Optional[bytes] = None
            b64 = item.get("base64")
            if isinstance(b64, str) and b64:
                try:
                    data = base64.b64decode(b64, validate=False)
                except Exception as exc:
                    item["local_materialize_error"] = f"base64_decode_failed: {exc}"
            if data is None and self.store:
                src = (
                    item.get("hosted_uri")
                    or item.get("source_path")
                    or item.get("key")
                    or item.get("rn")
                    or ""
                )
                if src:
                    try:
                        data = await self.store.get_blob_bytes(src)
                    except Exception as exc:
                        item["local_materialize_error"] = f"read_failed: {exc}"
            if data is None:
                item.setdefault("local_materialized", False)
                item.setdefault("local_materialize_reason", "no_bytes")
                continue
            target = attachments_dir / filename
            try:
                target.write_bytes(data)
            except Exception as exc:
                item["local_materialized"] = False
                item["local_materialize_error"] = f"write_failed: {exc}"
                continue
            rel_path = f"{turn_id}/attachments/{filename}"
            item["filename"] = filename
            item["physical_path"] = rel_path
            item["local_path"] = rel_path
            item["local_materialized"] = True
            item["file_exists"] = True
            item["size"] = item.get("size") or len(data)
            item["size_bytes"] = item.get("size_bytes") or len(data)

    def _attachments_summary_text(self, scratchpad: CTurnScratchpad) -> str:
        items = getattr(scratchpad, "user_attachments", None) or []
        lines: List[str] = []
        for a in items:
            if not isinstance(a, dict):
                continue
            name = (a.get("artifact_name") or a.get("filename") or "attachment").strip()
            summary = (a.get("summary") or "").strip()
            if summary:
                lines.append(f"- {name}: {summary}")
            else:
                lines.append(f"- {name}")
        return "\n".join(lines).strip()

    async def start_turn(self,
                         scratchpad: CTurnScratchpad,
                         summarize_attachments: bool = False):

        tenant, project, user, user_type, request_id, session_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"], self._ctx["service"]["session_id"]
        conversation_id, turn_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"]

        # (0) ensure User ↔ Conversation link (cheap + idempotent)
        t_turn0 = time.perf_counter()
        t0u, ms0u = _tstart()
        self._ctx["turn"] = {
            "t_turn0": t_turn0,
            "t0u": t0u,
            "ms0u": ms0u,
        }
        timing_uconv = _tend(t0u, ms0u)
        step_title = "User↔Conversation Linked"
        status = "completed"
        await self._emit({"type": "chat.step", "agent": "graph", "step": "context.graph",
                          "status": status, "title": step_title,
                          "data": {"user": user, "conversation": conversation_id},
                          "timing": timing_uconv})
        scratchpad.timings.append({
            "title": step_title,
            "elapsed_ms": timing_uconv["elapsed_ms"]
        })

        # --- 1) Load context bundle + timeline blocks
        t1, ms1 = _tstart()
        event_reader_materialized_input = False
        try:
            await self._emit_turn_work_status(
                [
                    "loading",
                    "preparing context",
                    "setting up the thread",
                ]
            )
            # Bundles can override gate_out_class after construction if they use a custom gate contract.
            async def _before_compaction(payload: dict) -> None:
                await self._emit_compaction_event(status="started", payload=payload)
                await self._emit_turn_work_status(
                    [
                        "compacting",
                        "organizing the thread",
                        "distilling context",
                    ]
                )
            async def _after_compaction(payload: dict) -> None:
                status = "completed"
                if isinstance(payload, dict) and payload.get("status"):
                    status = str(payload.get("status") or "completed")
                await self._emit_compaction_event(status=status, payload=payload)
                await self._emit_turn_work_status(
                    [
                        "back to work",
                        "continuing",
                        "progressing",
                    ]
                )
            async def _save_summary(payload: dict) -> None:
                if not isinstance(payload, dict):
                    return
                summary = (payload.get("summary") or "").strip()
                if not summary:
                    return
                if not self.ctx_browser:
                    return
                user_type = self.runtime_ctx.user_type or "anonymous"
                embedding = None
                if self.model_service:
                    try:
                        [embedding] = await self.model_service.embed_texts([summary])
                    except Exception:
                        embedding = None
                try:
                    await self.ctx_browser.save_artifact(
                        kind="conv.range.summary",
                        tenant=self.runtime_ctx.tenant,
                        project=self.runtime_ctx.project,
                        user_id=self.runtime_ctx.user_id,
                        conversation_id=self.runtime_ctx.conversation_id,
                        user_type=user_type,
                        turn_id=self.runtime_ctx.turn_id,
                        content=dict(payload),
                        content_str=summary,
                        embedding=embedding,
                        ttl_days=_ttl_for(user_type, 365),
                        bundle_id=self.config.ai_bundle_spec.id,
                        index_only=True,
                    )
                except Exception:
                    pass
            self.runtime_ctx.on_before_compaction = _before_compaction
            self.runtime_ctx.on_after_compaction = _after_compaction
            self.runtime_ctx.save_summary = _save_summary
            self.runtime_ctx.started_at = scratchpad.started_at
            # refresh per-turn ids
            self.runtime_ctx.turn_id = scratchpad.turn_id
            self.runtime_ctx.conversation_id = scratchpad.conversation_id
            self.runtime_ctx.user_id = scratchpad.user
            try:
                open_external_event_handler = getattr(self.ctx_browser, "open_external_event_handler", None)
                if callable(open_external_event_handler):
                    await open_external_event_handler()
            except Exception:
                self.logger.log(traceback.format_exc(), "ERROR")
            try:
                await self.ctx_browser.load_timeline(
                    days=365,
                )
            except Exception:
                try:
                    self.logger.log(
                        f"[react.timeline.load] failed turn_id={scratchpad.turn_id} conversation_id={scratchpad.conversation_id}: "
                        f"{traceback.format_exc()}",
                        level="ERROR",
                    )
                except Exception:
                    pass
            try:
                event_reader_result = self.ctx_browser.last_external_event_reader_result()
            except Exception:
                event_reader_result = {}
            try:
                self.logger.log(
                    "[react.external.initial_fold] "
                    + json.dumps(
                        {
                            "turn_id": scratchpad.turn_id,
                            "conversation_id": scratchpad.conversation_id,
                            "agent_id": getattr(getattr(self, "runtime_ctx", None), "agent_id", None),
                            "event_source_pipeline_enabled": bool(getattr(getattr(self, "runtime_ctx", None), "event_source_pipeline_enabled", False)),
                            "external_event_source": bool(getattr(getattr(self, "runtime_ctx", None), "external_event_source", None)),
                            **(event_reader_result if isinstance(event_reader_result, dict) else {}),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    level="INFO",
                )
            except Exception:
                pass
            event_reader_materialized_input = bool(event_reader_result.get("current_turn_user_input_materialized"))
            prompt_text = str(event_reader_result.get("current_turn_prompt_text") or "").strip()
            if prompt_text:
                scratchpad.user_text = prompt_text
                scratchpad.short_text = _shorten(prompt_text, 1000)
            if summarize_attachments and not event_reader_materialized_input:
                await self._summarize_user_attachments(scratchpad)
                await self._persist_attachment_summaries(scratchpad)
            try:
                await self._refresh_user_memory_hotset_for_announce()
            except Exception:
                pass
            # Set new-conversation flag and seed title from timeline
            try:
                tl = self.ctx_browser.timeline
                scratchpad.is_new_conversation = len(tl.get_history_blocks()) == 0
                if tl.conversation_title:
                    scratchpad.conversation_title = tl.conversation_title
            except Exception:
                pass

        except Exception as e:
            self.logger.log(traceback.format_exc(), "ERROR")
            try:
                from kdcube_ai_app.apps.chat.sdk.solutions.infra import ExecWorkspaceError
                if isinstance(e, ExecWorkspaceError):
                    # Fail fast: workspace is required for turn-level execution.
                    raise
            except Exception:
                pass

            timing_ctx = _tend(t1, ms1)
            scratchpad.timings.append({"title": "context.load", "elapsed_ms": timing_ctx["elapsed_ms"]})

        _event_ctx = self.comm_context.event if hasattr(self.comm_context, "event") else None
        _user_event_type = (getattr(_event_ctx, "type", None) or "") or ""
        _chat_input = _chat_input_kind(_user_event_type)
        _attachment_count = len([att for att in (scratchpad.user_attachments or []) if isinstance(att, dict)])

        # (1) user message
        await self._emit({"type": "chat.conversation.accepted", "agent": "user", "step": "chat.user.message", "status": "completed",
                          "title": "User Message",
                          "data": {
                              "text": scratchpad.short_text,
                              "chars": len(scratchpad.short_text),
                              "message_len": len(scratchpad.short_text),
                              "input_kind": _chat_input,
                              "attachment_count": _attachment_count,
                          }})
        self.logger.log_step("recv_user_message", {"len": len(scratchpad.user_text)})

        if not event_reader_materialized_input:
            await self._materialize_current_turn_user_attachments(scratchpad)
            try:
                build_user_input_blocks = _react_symbol("layout", "build_user_input_blocks")
                self.ctx_browser.contribute(
                    blocks=build_user_input_blocks(
                        runtime=self.ctx_browser.runtime_ctx,
                        user_text=scratchpad.user_text or "",
                        user_attachments=list(scratchpad.user_attachments or []),
                        block_factory=self.ctx_browser.timeline.block,
                        event_type=_user_event_type or None,
                    ),
                )
                # Add attachments to sources_pool so local attachment paths are citable.
                try:
                    merge_sources_pool_for_attachment_rows = _react_symbol(
                        "sources",
                        "merge_sources_pool_for_attachment_rows",
                    )
                    turn_id = self.ctx_browser.runtime_ctx.turn_id if self.ctx_browser and self.ctx_browser.runtime_ctx else ""
                    new_rows = []
                    for att in (scratchpad.user_attachments or []):
                        if not isinstance(att, dict):
                            continue
                        filename = (att.get("filename") or att.get("name") or "").strip()
                        if not filename or not turn_id:
                            continue
                        physical_path = f"{turn_id}/attachments/{filename}"
                        hosted_uri = (att.get("hosted_uri") or att.get("source_path") or att.get("path") or att.get("key") or "").strip()
                        row = {
                            "url": hosted_uri or physical_path,
                            "title": filename,
                            "text": "",
                            "source_type": "attachment",
                            "mime": (att.get("mime") or att.get("mime_type") or "").strip(),
                            "size_bytes": att.get("size") or att.get("size_bytes"),
                            "physical_path": physical_path,
                            "artifact_path": f"fi:{turn_id}.user.attachments/{filename}",
                            "turn_id": turn_id,
                        }
                        if hosted_uri:
                            row["hosted_uri"] = hosted_uri
                        if att.get("rn"):
                            row["rn"] = att.get("rn")
                        if att.get("key"):
                            row["key"] = att.get("key")
                        new_rows.append(row)
                    if new_rows:
                        merge_sources_pool_for_attachment_rows(ctx_browser=self.ctx_browser, rows=new_rows)
                except Exception:
                    pass
            except Exception:
                pass

        self.logger.start_operation(
            "orchestrator.process",
            request_id=request_id, tenant=tenant, project=project, user=user,
            session=session_id, conversation=conversation_id, text_preview=scratchpad.short_text,
        )

    async def finish_turn(self,
                          scratchpad: TurnScratchpad,
                          ok: bool = True,
                          result_summary: str | None = None,
                          on_flush_completed_hook: Optional[Callable[[CTurnScratchpad], Awaitable[None]]] = None):
        # prevent double-finish from multiple branches / nested handlers
        if getattr(scratchpad, "_turn_finished", False):
            return
        scratchpad._turn_finished = True

        tenant, project, user, user_type, request_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"]
        conversation_id, turn_id = self._ctx["conversation"]["conversation_id"], self._ctx["conversation"]["turn_id"]
        t_turn0, ms0u = self._ctx["turn"]["t_turn0"], self._ctx["turn"]["ms0u"]

        if scratchpad.answer:
            try:
                await self._emit_committed_answer_once(scratchpad, agent="assistant.completion")
            except Exception:
                pass
            # Contribute pre-answer blocks (e.g., final ANNOUNCE)
            try:
                runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
                pre_hook = getattr(runtime_ctx, "on_before_completion_contribution", None) if runtime_ctx else None
                pre_blocks = pre_hook() if callable(pre_hook) else None
                if callable(pre_hook):
                    try:
                        runtime_ctx.on_before_completion_contribution = None
                    except Exception:
                        pass
                if pre_blocks:
                    try:
                        types = [b.get("type") for b in pre_blocks if isinstance(b, dict)]
                        self.logger.log(f"[workflow] pre_completion_blocks: {types}", level="INFO")
                    except Exception:
                        pass
                    self.ctx_browser.contribute(blocks=list(pre_blocks))
            except Exception:
                pass
            # Contribute assistant completion to current turn log
            try:
                build_assistant_completion_blocks = _react_symbol(
                    "layout",
                    "build_assistant_completion_blocks",
                )
                self.ctx_browser.contribute(
                    blocks=build_assistant_completion_blocks(
                        runtime=self.ctx_browser.runtime_ctx,
                        completion_entries=list(getattr(scratchpad, "assistant_completion_attempts", []) or []),
                        final_answer_text=scratchpad.answer or "",
                        ended_at=getattr(scratchpad, "ended_at", None),
                        block_factory=self.ctx_browser.timeline.block,
                    ),
                )
                # set suggested followups on scratchpad to add them on timeline. This will render the timeline properly
                if scratchpad.suggested_followups:
                    # in order to later retrieve with fetch and in order to contribute to assistant answer block during rendering
                    self.ctx_browser.contribute_suggested_followups(suggested_followups=scratchpad.suggested_followups)

            except Exception:
                pass
            try:
                await self.persist_turn_prompt_entries(scratchpad)
            except Exception:
                self.logger.log(traceback.format_exc(), "ERROR")
            await self.persist_assistant(scratchpad)
            # Post-answer blocks (react.state / react.exit)
            try:
                runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
                post_hook = getattr(runtime_ctx, "on_after_completion_contribution", None) if runtime_ctx else None
                post_blocks = post_hook() if callable(post_hook) else None
                if callable(post_hook):
                    try:
                        runtime_ctx.on_after_completion_contribution = None
                    except Exception:
                        pass
                if post_blocks:
                    try:
                        types = [b.get("type") for b in post_blocks if isinstance(b, dict)]
                        self.logger.log(f"[workflow] post_completion_blocks: {types}", level="INFO")
                    except Exception:
                        pass
                    self.ctx_browser.contribute(blocks=list(post_blocks))
            except Exception:
                pass
        else:
            # No assistant answer; still emit pre/post blocks if present
            try:
                runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
                pre_hook = getattr(runtime_ctx, "on_before_completion_contribution", None) if runtime_ctx else None
                pre_blocks = pre_hook() if callable(pre_hook) else None
                if callable(pre_hook):
                    try:
                        runtime_ctx.on_before_completion_contribution = None
                    except Exception:
                        pass
                if pre_blocks:
                    try:
                        types = [b.get("type") for b in pre_blocks if isinstance(b, dict)]
                        self.logger.log(f"[workflow] pre_completion_blocks: {types}", level="INFO")
                    except Exception:
                        pass
                    self.ctx_browser.contribute(blocks=list(pre_blocks))
            except Exception:
                pass
            try:
                runtime_ctx = getattr(self.ctx_browser, "runtime_ctx", None)
                post_hook = getattr(runtime_ctx, "on_after_completion_contribution", None) if runtime_ctx else None
                post_blocks = post_hook() if callable(post_hook) else None
                if callable(post_hook):
                    try:
                        runtime_ctx.on_after_completion_contribution = None
                    except Exception:
                        pass
                if post_blocks:
                    try:
                        types = [b.get("type") for b in post_blocks if isinstance(b, dict)]
                        self.logger.log(f"[workflow] post_completion_blocks: {types}", level="INFO")
                    except Exception:
                        pass
                    self.ctx_browser.contribute(blocks=list(post_blocks))
            except Exception:
                pass
            try:
                await self.persist_turn_prompt_entries(scratchpad)
            except Exception:
                self.logger.log(traceback.format_exc(), "ERROR")
        if ok:
            if on_flush_completed_hook:
                await on_flush_completed_hook(scratchpad)
            try:
                await self._publish_git_workspace_if_needed()
            except TurnPhaseError:
                self.logger.log(
                    "[workflow] git workspace publish failed; continuing with turn persistence\n" + traceback.format_exc(),
                    level="ERROR",
                )

        try:
            if self.ctx_browser:
                await self.ctx_browser.stop_external_event_listener()
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")

        # Save turn log (always) - v2
        try:
            contrib_log = []
            used_sids = []
            try:
                if self.ctx_browser:
                    contrib_log = list(self.ctx_browser.current_turn_blocks() or [])
            except Exception:
                contrib_log = []
            if not contrib_log and self.ctx_browser and getattr(self.ctx_browser, "timeline", None):
                # Fallback: if current_turn_offset is missing or incorrect, filter by turn_id.
                try:
                    blocks = [
                        b for b in (self.ctx_browser.timeline.blocks or [])
                        if isinstance(b, dict) and b.get("turn_id") == turn_id
                    ]
                    if blocks:
                        contrib_log = blocks
                        self.logger.log(
                            f"[workflow] turn_log fallback: collected blocks by turn_id={turn_id} count={len(blocks)}",
                            level="WARNING",
                        )
                except Exception:
                    pass
            end_ts = datetime.datetime.utcnow().isoformat() + "Z"
            total_tokens = 0
            try:
                extract_sources_used_from_blocks = _react_symbol(
                    "timeline",
                    "extract_sources_used_from_blocks",
                )
                used_sids = extract_sources_used_from_blocks(contrib_log)
            except Exception:
                used_sids = []
            try:
                from kdcube_ai_app.apps.chat.sdk.util import token_count

                def _block_tokens(block: dict) -> int:
                    total = 0
                    if not isinstance(block, dict):
                        return 0
                    text_val = block.get("text")
                    if isinstance(text_val, str) and text_val:
                        try:
                            total += token_count(text_val)
                        except Exception:
                            pass
                    b64_val = block.get("base64")
                    if isinstance(b64_val, str) and b64_val:
                        try:
                            total += token_count(b64_val)
                        except Exception:
                            pass
                    return total

                total_tokens = sum(_block_tokens(b) for b in (contrib_log or []))
            except Exception:
                total_tokens = 0
            TurnLog = _react_symbol("turn_log", "TurnLog")
            tlog = TurnLog(
                turn_id=turn_id,
                ts=(scratchpad.started_at or ""),
                blocks=contrib_log,
                end_ts=end_ts,
                sources_used=used_sids,
                blocks_count=len(contrib_log),
                tokens=total_tokens,
            )
            payload = tlog.to_dict()
            # sources_pool is stored in timeline artifact, not in turn log
        except Exception:
            payload = {"turn_id": turn_id, "ts": (scratchpad.started_at or ""), "blocks": []}
        blocks_count = len(payload.get("blocks") or []) if isinstance(payload, dict) else 0
        self.logger.log(
            f"[workflow] persist turn_log start: turn_id={turn_id} blocks={blocks_count}",
            level="INFO",
        )
        await self.ctx_client.save_turn_log_as_artifact(
            tenant=tenant, project=project, user=user,
            conversation_id=conversation_id, user_type=user_type,
            turn_id=turn_id,
            bundle_id=self.config.ai_bundle_spec.id,
            payload=payload,
            extra_tags=[],
        )
        self.logger.log(
            f"[workflow] persist turn_log done: turn_id={turn_id}",
            level="INFO",
        )

        tl_blocks = 0
        sp_len = 0
        if self.ctx_browser and self.ctx_browser.timeline:
            try:
                tl_blocks = len(list(self.ctx_browser.timeline.blocks or []))
            except Exception:
                tl_blocks = 0
        if self.ctx_browser:
            try:
                sp_len = len(list(self.ctx_browser.sources_pool or []))
            except Exception:
                sp_len = 0
        self.logger.log(
            f"[workflow] persist timeline start: turn_id={turn_id} blocks={tl_blocks} sources_pool={sp_len}",
            level="INFO",
        )
        try:
            await self.ctx_browser.persist_timeline()
            self.logger.log(
                f"[workflow] persist timeline done: turn_id={turn_id}",
                level="INFO",
            )
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")
        # (19) done

        total_ms = int((time.perf_counter() - t_turn0) * 1000)
        step_title = "Plan Completed" if ok else "Plan Failed"
        completion_metrics = {
            "elapsed_ms": total_ms,
            "active_seconds": round(total_ms / 1000.0, 3),
            "duration_ms": total_ms,
            "produced_file_count": _produced_file_count(payload.get("blocks") or [], turn_id) if isinstance(payload, dict) else 0,
            "citation_count": len(used_sids or []) if isinstance(used_sids, list) else 0,
        }
        await self._emit({"type": "chat.conversation.turn.completed", "agent": "planner", "step": "plan.done", "status": "completed",
                          "title": step_title, "data": completion_metrics,
                          "timing": {"started_ms": ms0u, "ended_ms": _now_ms(), "elapsed_ms": total_ms}})
        scratchpad.timings.append({
            "title": step_title,
            "elapsed_ms": total_ms
        })

        def_status = "ok" if ok else "failed"
        self.logger.finish_operation(ok, result_summary=(result_summary or f"{def_status} • elapsed={total_ms}ms"))
        await self.report_timings(scratchpad, ms0u, total_ms)

        try:
            await self._persist_stream_artifacts()
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")

        try:
            if self.ctx_browser:
                close_external_event_handler = getattr(self.ctx_browser, "close_external_event_handler", None)
                if callable(close_external_event_handler):
                    await close_external_event_handler()
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")

        try:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings
            if not get_settings().SOLUTION_RETAIN_TURN_WORKSPACE:
                _cleanup_turn_workspace(getattr(self, "runtime_ctx", None), self.logger)
                browser_ctx = getattr(getattr(self, "ctx_browser", None), "runtime_ctx", None)
                if browser_ctx is not getattr(self, "runtime_ctx", None):
                    _cleanup_turn_workspace(browser_ctx, self.logger)
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")

    async def _handle_turn_exception(self,
                                     exc: Exception,
                                     scratchpad: CTurnScratchpad) -> None:

        # ---- phase info ----
        phase = getattr(scratchpad, "current_phase", None)
        agent = (phase.agent if phase else None) or "workflow"
        stage = (phase.name if phase else None) or "workflow"
        meta = (phase.meta if phase else {}) if phase else {}

        t_turn0, ms0u = self._ctx["turn"]["t_turn0"], self._ctx["turn"]["ms0u"]
        tenant, project, user, user_type, request_id = self._ctx["service"]["tenant"], self._ctx["service"]["project"], self._ctx["service"]["user"], self._ctx["service"]["user_type"], self._ctx["service"]["request_id"]

        total_ms = int((time.perf_counter() - t_turn0) * 1000)
        ms_pretty_table, ms_markdown, timings = await self.report_timings(scratchpad, ms0u, total_ms)

        extra_data: dict = {}
        managed_exception: Exception | None = None
        show_error_in_timeline = True
        service_exception = _service_exception_from_chain(exc)

        # Defaults for generic path
        message = str(exc) or repr(exc)
        error_type = exc.__class__.__name__
        if not isinstance(exc, ServiceException):
            try:
                safe_msg = self.message_resources_fn("server_error") if self.message_resources_fn else None
            except Exception:
                safe_msg = None
            message = safe_msg or _generic_turn_failure_message(str(exc))
            try:
                extra_data["raw_error"] = str(exc)
                extra_data["traceback"] = traceback.format_exc()
            except Exception:
                pass

        # ---- unwrap ServiceException / TurnPhaseError vs generic errors ----
        if service_exception is not None and _is_service_connectivity_error(service_exception.err):
            se: ServiceError = service_exception.err
            agent = se.service_name or agent
            stage = se.stage or stage
            try:
                resource_msg = self.message_resources_fn("service_connection_error") if self.message_resources_fn else None
            except Exception:
                resource_msg = None
            message = resource_msg or _service_connectivity_user_message(se)
            error_type = se.code or se.error_type or "service_connection_error"
            extra_data = {
                "service_error": {
                    "kind": getattr(se.kind, "value", se.kind),
                    "service_name": se.service_name,
                    "provider": se.provider,
                    "model_name": se.model_name,
                    "error_type": se.error_type,
                    "stage": se.stage,
                    "http_status": se.http_status,
                    "code": se.code,
                    "retryable": se.retryable,
                },
                "service_kind": getattr(se.kind, "value", se.kind),
                "service_name": se.service_name,
                "provider": se.provider,
                "model_name": se.model_name,
                "http_status": se.http_status,
                "retryable": se.retryable,
                "service_stage": se.stage,
            }
            show_error_in_timeline = True

        elif isinstance(exc, ServiceException):
            se: ServiceError = exc.err

            # Optional: prefer service payload for "agent"/"stage" if present
            agent = se.service_name or agent
            stage = se.stage or stage

            # message = se.message
            service_message = se.message
            # user-facing message (no internals)
            message = self.message_resources_fn("usage_limit")
            # prefer canonical codes if provided
            error_type = se.code or se.error_type or "ServiceError"

            extra_data = {
                "service_error": se.model_dump(),
                "service_kind": getattr(se.kind, "value", se.kind),
                "service_name": se.service_name,
                "provider": se.provider,
                "model_name": se.model_name,
                "http_status": se.http_status,
                "retryable": se.retryable,
                "service_stage": se.stage,
            }
            show_error_in_timeline = False

            # ---- build economics payload (entrypoint-style) ----
            bundle_id = self.config.ai_bundle_spec.id
            subj = subject_id_of(tenant, project, user)

            # "derived from service error"
            code = (se.code or se.error_type or (f"http_{int(se.http_status)}" if se.http_status else None) or "services_quota_exceeded")

            econ_payload = {
                "message": message,
                "reason": "services_quota_exceeded",
                "bundle_id": bundle_id,
                "subject_id": subj,
                "user_type": user_type,
                "code": code,
                "show_in_timeline": False,
                "service_error": se.model_dump(),  # <-- required nesting
            }

            # Emit service event so client can handle it
            try:
                await self.comm.service_event(
                    type="rate_limit.ai_services_quota",
                    step="rate_limit",
                    status="error",
                    title="Services quota exceeded",
                    agent="bundle.rate_limiter",
                    data=econ_payload,
                )
            except Exception:
                # best-effort; don't mask the main flow
                pass

            managed_exception = EconomicsLimitException(message, code=code, data=econ_payload)

        elif isinstance(exc, TurnPhaseError):
            message = str(exc)
            error_type = exc.code or "TurnPhaseError"
            extra_data = dict(exc.data or {})
        else:
            if not message:
                message = str(exc) or repr(exc)
            error_type = exc.__class__.__name__

        # ---- log ----
        self.logger.log(
            f"Turn failed at phase={stage} agent={agent}: {message}\n"
            f"Timings:\n{ms_pretty_table}\n"
            f"error_type={error_type};phase_meta={meta}",
            level="ERROR",
        )

        # ---- build message payload ----
        data = {
            "agent": agent,
            "stage": stage,
            "phase_meta": meta,
            "error_type": error_type,
            "timings": timings,
            "timings_markdown": ms_markdown,
            **extra_data,
        }
        safe_data = data
        try:
            safe_data = json.loads(json.dumps(data, ensure_ascii=False, default=str))
        except Exception:
            safe_data = {
                "agent": agent,
                "stage": stage,
                "error_type": error_type,
            }
        data_for_user = dict(safe_data)
        # keep internals out of timeline-facing error payloads
        for k in ("raw_error", "traceback"):
            if k in data_for_user:
                data_for_user.pop(k, None)
        if show_error_in_timeline:
            # pass
            # Emit error event for telemetry and an answer bubble for the user.
            await self.comm.error(message=message, agent="turn.error", data=data_for_user)
            # await self.comm.delta(text=message, index=0, marker="answer", agent="turn_exception", completed=True)
        else:
            # Keep it out of timeline; still produce an "answer" bubble
            await self.comm.delta(text=message, index=0, marker="answer", agent="turn_exception", completed=True)

        # no-op (kept for alignment with prior error handling)

        # ---- rollback ----
        try:
            await self.ctx_client.delete_turn(
                tenant=tenant,
                project=project,
                user_id=user,
                conversation_id=scratchpad.conversation_id,
                turn_id=scratchpad.turn_id,
                user_type=user_type,
                bundle_id=self.config.ai_bundle_spec.id,
                where="index_only",   # important: keep blobs for monitoring, just rollback index
            )
        except Exception as e:
            self.logger.log(f"Rollback delete_turn(index_only) failed: {traceback.format_exc()}")

        try:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings
            if not get_settings().SOLUTION_RETAIN_TURN_WORKSPACE:
                _cleanup_turn_workspace(getattr(self, "runtime_ctx", None), self.logger)
                browser_ctx = getattr(getattr(self, "ctx_browser", None), "runtime_ctx", None)
                if browser_ctx is not getattr(self, "runtime_ctx", None):
                    _cleanup_turn_workspace(browser_ctx, self.logger)
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")

        # ---- bubble ----
        if managed_exception is not None:
            raise managed_exception from exc

        # preserve original traceback as much as possible
        raise exc.with_traceback(exc.__traceback__)
