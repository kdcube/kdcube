# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/solutions/chatbot/entrypoint.py

from __future__ import annotations

import importlib
import json
import os
import pathlib
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from kdcube_ai_app.apps.chat.emitters import (
    ChatCommunicator,
    build_comm_from_comm_context,
    build_relay_from_env,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.viz.patch_platform_dashboard import patch_dashboard
from kdcube_ai_app.infra.service_hub.inventory import (
    APP_STATE_KEYS,
    AgentLogger,
    Config,
    ModelServiceBase,
    _mid,
)
from kdcube_ai_app.tools.serialization import json_safe
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.turn_reporting import (
    _format_cost_table_markdown,
    _format_cost_summary_compact,
    _format_agent_breakdown_markdown,
)
from kdcube_ai_app.storage.storage import create_storage_backend
from kdcube_ai_app.infra.accounting.calculator import RateCalculator
from kdcube_ai_app.apps.chat.sdk.viz.tsx_transpiler import ClientSideTSXTranspiler
from kdcube_ai_app.infra.service_hub.cache import create_kv_cache_from_env


class BaseEntrypoint:
    """
    Minimal, reusable bundle entrypoint base.
    Intended to be subclassed by bundle-specific workflows.
    """

    BUNDLE_ID = "kdcube.bundle.base"

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ChatTaskPayload = None,
        event_filter: Optional[Any] = None,
        ctx_client: Optional[Any] = None,
    ):
        self.config = config
        self.settings = get_settings()
        self.pg_pool = pg_pool
        self.redis = redis
        self.comm_context = comm_context
        self._event_filter = event_filter

        self._comm: Optional[ChatCommunicator] = None
        self._conv_idx = None
        self._kb = None
        self._store = None
        self.ctx_client = ctx_client
        self.bundle_props: Dict[str, Any] = dict(self.bundle_props_defaults or {})
        self.kv_cache = create_kv_cache_from_env()

        self.logger = AgentLogger(f"{self.BUNDLE_ID}.Workflow", config.log_level)
        self.models_service = ModelServiceBase(self.config)

        if getattr(self, "ctx_client", None) is None:
            self.ctx_client = None

        self._apply_configuration_overrides()

    # ---------- Common helpers ----------

    def _apply_configuration_overrides(self) -> None:
        configuration = getattr(self, "configuration", None) or {}

        wf_roles = configuration.get("role_models") or {}
        if wf_roles:
            self.config.set_role_models({**(self.config.role_models or {}), **wf_roles})

        wf_embedding = configuration.get("embedding") or {}
        if wf_embedding:
            self.config.set_embedding(wf_embedding)

        if getattr(self.config, "ai_bundle_spec", None):
            try:
                from kdcube_ai_app.apps.chat.sdk.runtime.external.distributed_snapshot import compute_dir_sha256
                spec = self.config.ai_bundle_spec
                root = pathlib.Path(spec.path)
                if spec.module:
                    module_root = spec.module.split(".")[0]
                    candidate = root / module_root
                    if candidate.exists():
                        root = candidate
                if root.exists():
                    sha = compute_dir_sha256(root, skip_files=set())
                    # Always use content hash as authoritative version
                    self.config.ai_bundle_spec.version = sha[:12]
            except Exception:
                pass

    @property
    def bundle_props_defaults(self) -> Dict[str, Any]:
        return {}

    async def refresh_bundle_props(self, *, state: Dict[str, Any]) -> Dict[str, Any]:
        defaults = dict(self.bundle_props_defaults or {})
        if not self.kv_cache and not self.redis:
            self.bundle_props = defaults
            return self.bundle_props

        tenant = state.get("tenant") or getattr(getattr(self.comm_context, "actor", None), "tenant_id", None)
        project = state.get("project") or getattr(getattr(self.comm_context, "actor", None), "project_id", None)
        if not tenant or not project:
            self.bundle_props = defaults
            return self.bundle_props

        bundle_id = getattr(getattr(self.config, "ai_bundle_spec", None), "id", None)
        if not bundle_id:
            self.bundle_props = defaults
            return self.bundle_props

        from kdcube_ai_app.infra import namespaces

        key = namespaces.CONFIG.BUNDLES.PROPS_KEY_FMT.format(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )
        overrides: Dict[str, Any] = {}
        if self.kv_cache:
            overrides = await self.kv_cache.get_json(key) or {}
        else:
            raw = await self.redis.get(key)
            if raw:
                try:
                    overrides = json.loads(raw)
                except Exception:
                    overrides = {}
        if overrides:
            defaults.update(overrides)

        self.bundle_props = defaults
        return self.bundle_props

    @property
    def comm(self) -> ChatCommunicator:
        if self._comm:
            return self._comm
        if not self.comm_context:
            raise RuntimeError("Workflow cannot build communicator: task missing")
        self._comm = build_comm_from_comm_context(
            self.comm_context,
            relay=build_relay_from_env(),
            event_filter=self._event_filter,
        )
        return self._comm

    @classmethod
    def project_app_state(cls, state: Dict[str, Any]) -> Dict[str, Any]:
        out = {"context": {"bundle": cls.BUNDLE_ID}}
        for k in APP_STATE_KEYS:
            out[k] = json_safe(state.get(k))
        return out

    @staticmethod
    def user_type_from_comm_ctx(comm: ChatCommunicator) -> str:
        user_obj = (comm.service or {}).get("user_obj") or {}
        return user_obj.get("user_type") or "anonymous"

    @staticmethod
    def create_initial_state(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "request_id": payload.get("request_id") or _mid("req"),
            "tenant": payload.get("tenant"),
            "project": payload.get("project"),
            "user": payload.get("user"),
            "user_type": payload.get("user_type"),
            "session_id": payload.get("session_id"),
            "conversation_id": payload.get("conversation_id"),
            "text": (payload.get("text") or "").strip(),
            "attachments": payload.get("attachments") or (payload.get("payload") or {}).get("attachments") or [],
            "step_logs": [],
            "start_time": time.time(),
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        self._app_state = dict(state or {})
        self._turn_id = self._app_state.get("turn_id")

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        raise NotImplementedError("execute_core() must be implemented by subclasses")

    async def pre_run_hook(self, *, state: Dict[str, Any]) -> None:
        return None

    async def post_run_hook(self, *, state: Dict[str, Any], result: Dict[str, Any]) -> None:
        return None

    async def run(self, **params) -> Dict[str, Any]:
        state = dict(getattr(self, "_app_state", {}) or {})
        self._turn_id = self._turn_id or _mid("turn")
        state["turn_id"] = self._turn_id
        if params.get("text"):
            state["text"] = params["text"]
        if "attachments" in params:
            state["attachments"] = params.get("attachments") or []

        tenant = state.get("tenant")
        project = state.get("project")
        user_id = state.get("user") or state.get("fingerprint")
        user_type = state.get("user_type") or "anonymous"
        thread_id = state.get("conversation_id") or state.get("session_id") or "default"
        turn_id = state.get("turn_id")

        await self.refresh_bundle_props(state=state)
        await self.pre_run_hook(state=state)

        result = await self.execute_core(state=state, thread_id=thread_id, params=params)
        result = result or {}

        usage_from = datetime.utcnow().date().isoformat()
        await self.run_accounting(
            tenant=tenant,
            project=project,
            user_id=user_id,
            user_type=user_type,
            thread_id=thread_id,
            turn_id=turn_id,
            usage_from=usage_from,
        )

        await self.post_run_hook(state=state, result=result)
        return self.project_app_state(result)

    def _bundle_root(self) -> Optional[str]:
        spec = getattr(self.config, "ai_bundle_spec", None)
        if spec and spec.module and spec.path:
            return os.path.join(spec.path, "/".join(spec.module.split(".")[:-1]))
        return None

    def _ensure_privileged(self, *, user_id: Optional[str], feature: str) -> Optional[str]:
        user_type = self.user_type_from_comm_ctx(self.comm)
        if user_type not in ("privileged",):
            self.logger.log(
                f"[{feature}]. User {user_id} with type [{user_type}] has no permission to access {feature}",
                "WARN",
            )
            return None
        return user_type

    def _render_dashboard_html(
        self,
        *,
        content: str,
        title: str,
    ) -> str:
        transpiler = ClientSideTSXTranspiler()
        return transpiler.tsx_to_html(content, title=title)

    def opex(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="opex")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[opex]. Generating Opex Report for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No opex available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                cp_mod = importlib.import_module("kdcube_ai_app.apps.chat.api.opex")
                fallback_path = Path(cp_mod.__file__).parent / "OpexDashboard.tsx"
                content = fallback_path.read_text(encoding="utf-8")
                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{os.environ.get('CHAT_APP_PORT') or '8010'}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="OPEX")
                return [html]
            except Exception:
                self.logger.log(f"Error loading opex by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    def control_plane(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="control_plane")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[control_plane]. Generating Control Plane Admin Dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No control plane interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                cp_mod = importlib.import_module("kdcube_ai_app.apps.chat.api.control_plane")
                fallback_path = Path(cp_mod.__file__).parent / "EconomicsDashboard.tsx"
                content = fallback_path.read_text(encoding="utf-8")
                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{os.environ.get('CHAT_APP_PORT') or '8010'}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Control Plane")
                return [html]
            except Exception:
                self.logger.log(f"Error loading control_plane by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    def conversation_browser(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="conversation_browser")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(
            f"[conversation_browser]. Generating Conversation Browser Admin Dashboard for user {user_id} ({user_type})"
        )

        bundle_root = self._bundle_root()
        default_content = "<p>No conversation browser interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                cp_mod = importlib.import_module("kdcube_ai_app.apps.chat.api.control_plane")
                fallback_path = Path(cp_mod.__file__).parent / "ConversationBrowser.tsx"
                content = fallback_path.read_text(encoding="utf-8")

                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{os.environ.get('CHAT_APP_PORT') or '8010'}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Control Plane")
                return [html]
            except Exception:
                self.logger.log(
                    f"Error loading conversation browser by user {user_id}: {traceback.format_exc()}",
                    "ERROR",
                )
        return [default_html]

    def svc_gateway(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="svc_gateway")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[svc_gateway]. Generating Gateway Monitoring Dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No gateway monitoring interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                monitoring_mod = importlib.import_module("kdcube_ai_app.apps.chat.api.monitoring")
                fallback_path = Path(monitoring_mod.__file__).parent / "ControlPlaneMonitoringDashboard.tsx"
                content = fallback_path.read_text(encoding="utf-8")

                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{os.environ.get('CHAT_APP_PORT') or '8010'}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Gateway Monitoring")
                return [html]
            except Exception:
                self.logger.log(f"Error loading svc_gateway by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    def redis_browser(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="redis_browser")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[redis_browser]. Generating Redis Browser Admin Dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No redis browser interface available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        if bundle_root:
            try:
                cp_mod = importlib.import_module("kdcube_ai_app.apps.chat.api.control_plane")
                fallback_path = Path(cp_mod.__file__).parent / "RedisBrowser.tsx"
                content = fallback_path.read_text(encoding="utf-8")
                output_content = patch_dashboard(
                    input_content=content,
                    base_url=f"http://localhost:{os.environ.get('CHAT_APP_PORT') or '8010'}",
                    access_token=None,
                    default_tenant=self.settings.TENANT,
                    default_project=self.settings.PROJECT,
                    default_app_bundle_id=self.config.ai_bundle_spec.id,
                )
                html = self._render_dashboard_html(content=output_content, title="Redis Browser")
                return [html]
            except Exception:
                self.logger.log(f"Error loading redis browser by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    def ai_bundles(self, user_id: Optional[str] = None, **kwargs):
        user_type = self._ensure_privileged(user_id=user_id, feature="ai_bundles")
        if not user_type:
            return ["<p>No permission.</p>"]
        self.logger.log(f"[ai_bundles]. Generating AI Bundles Admin Dashboard for user {user_id} ({user_type})")

        bundle_root = self._bundle_root()
        default_content = "<p>No AI bundles dashboard available.</p>"
        default_html = f"<div style='margin: 0; position: absolute'>{default_content}</div>"

        try:
            integrations_mod = importlib.import_module("kdcube_ai_app.apps.chat.proc.rest.integrations")
            fallback_path = Path(integrations_mod.__file__).parent / "AIBundleDashboard.tsx"
            content = fallback_path.read_text(encoding="utf-8")

            output_content = patch_dashboard(
                input_content=content,
                base_url=f"http://localhost:{os.environ.get('CHAT_APP_PORT') or '8010'}",
                access_token=None,
                default_tenant=self.settings.TENANT,
                default_project=self.settings.PROJECT,
                default_app_bundle_id=self.config.ai_bundle_spec.id,
                host_bundles_path=os.environ.get("HOST_BUNDLES_PATH"),
                agentic_bundles_root=os.environ.get("AGENTIC_BUNDLES_ROOT"),
            )
            html = self._render_dashboard_html(content=output_content, title="AI Bundles")
            return [html]
        except Exception:
            self.logger.log(f"Error loading ai_bundles by user {user_id}: {traceback.format_exc()}", "ERROR")
        return [default_html]

    @property
    def configuration(self) -> Dict[str, Any]:
        sonnet_45 = "claude-sonnet-4-5-20250929"
        haiku_3 = "claude-3-5-haiku-20241022"
        haiku_4 = "claude-haiku-4-5-20251001"


        return {
            "role_models": {
                "ctx.reconciler":  {"provider": "anthropic", "model": haiku_4},
                "turn.summary": {"provider": "anthropic", "model": haiku_4},
                "attachment.summary": {"provider": "anthropic", "model": haiku_4},
                "format_fixer": {"provider": "anthropic", "model": haiku_3},

                "solver.tool_router": {"provider": "anthropic", "model": haiku_4},
                "solver.solvability": {"provider": "anthropic", "model": haiku_4},
                "solver.codegen": {"provider": "anthropic", "model": sonnet_45},
                "solver.coordinator": {"provider": "anthropic", "model": sonnet_45},
                "solver.unified-planner": {"provider": "anthropic", "model": sonnet_45},
                "solver.react.decision": {"provider": "anthropic", "model": sonnet_45},
                "solver.react.decision.strong": {"provider": "anthropic", "model": sonnet_45},
                "solver.react.decision.regular": {"provider": "anthropic", "model": haiku_4},
                "solver.react.summary": {"provider": "anthropic", "model": haiku_4},

                "tool.generator": {"provider": "anthropic", "model": sonnet_45},
                "tool.generator.strong": {"provider": "anthropic", "model": sonnet_45},
                "tool.generator.regular": {"provider": "anthropic", "model": haiku_4},

                "tool.source.reconciler": {"provider": "anthropic", "model": haiku_4},
                "tool.sources.filter.by.content": {"provider": "anthropic", "model": haiku_4},
                "tool.sources.filter.by.content.and.segment": {"provider": "anthropic", "model": haiku_4},
            },
            "embedding": {
                "provider": "openai",
                "model": "text-embedding-3-small",
            },
        }

    async def apply_accounting(
        self,
        tenant: str,
        project: str,
        user_id: str,
        user_type: str,
        thread_id: str,
        turn_id: str,
        usage_from: str,
    ):
        """Calculate and report turn costs using calculator."""
        settings = get_settings()
        kdcube_path = settings.STORAGE_PATH
        backend = create_storage_backend(kdcube_path)
        calc = RateCalculator(backend, base_path="accounting")
        bundle_id = self.config.ai_bundle_spec.id

        from datetime import datetime
        try:
            date_to = datetime.utcnow().date().isoformat()
        except Exception:
            date_to = usage_from

        self.logger.log(
            f"[apply_accounting]. tenant={tenant};project={project};user_id={user_id};"
            f"conversation_id={thread_id};turn_id={turn_id};usage_from={usage_from};"
            f"date_to={date_to};bundle_id={bundle_id};"
        )
        ref_provider = "anthropic"
        ref_model = "claude-sonnet-4-5-20250929"

        result = await calc.calculate_turn_costs(
            tenant_id=tenant,
            project_id=project,
            conversation_id=thread_id,
            turn_id=turn_id,
            app_bundle_id=bundle_id,
            date_from=usage_from,
            date_to=date_to,
            service_types=["llm", "embedding", "web_search"],
            use_memory_cache=True,
            ref_provider=ref_provider,
            ref_model=ref_model,
        )

        cost_total_usd = result["cost_total_usd"]
        cost_breakdown = result["cost_breakdown"]
        agent_costs = result["agent_costs"]
        token_summary = result["token_summary"]

        weighted_tokens = token_summary["weighted_tokens"]
        ranked_tokens = token_summary.get("llm_equivalent_tokens") or token_summary["weighted_tokens"]

        self.logger.log(
            f"[Conversation id: {thread_id}; Turn id: {turn_id}] "
            f"Token breakdown - Uncached: {token_summary['llm_input_sum']}, "
            f"Cache write: {token_summary['llm_cache_creation_sum']}, "
            f"Cache read: {token_summary['llm_cache_read_sum']}, "
            f"Output: {token_summary['llm_output_sum']}, "
            f"Total input: {token_summary['total_input_tokens']}"
        )
        self.logger.log(
            f"[Conversation id: {thread_id}; Turn id: {turn_id}] "
            f"Weighted tokens (LLM only): {weighted_tokens}; Equivalent tokens: {ranked_tokens}"
        )
        self.logger.log(
            f"[Conversation id: {thread_id}; Turn id: {turn_id}] "
            f"Estimated spend (with cache): {cost_total_usd:.6f} USD; "
            f"breakdown: {json.dumps(cost_breakdown, ensure_ascii=False)}"
        )
        self.logger.log(
            f"[Conversation id: {thread_id}; Turn id: {turn_id}] "
            f"Cost by agent: {json.dumps({k: v['total_cost_usd'] for k, v in agent_costs.items()}, ensure_ascii=False)}"
        )

        cost_markdown = _format_cost_table_markdown(
            cost_breakdown=cost_breakdown,
            total_cost=cost_total_usd,
            show_detailed=True,
        )
        agent_markdown = _format_agent_breakdown_markdown(agent_costs, cost_total_usd)
        full_markdown = cost_markdown + "\n\n" + agent_markdown

        compact_summary = _format_cost_summary_compact(
            cost_breakdown=cost_breakdown,
            total_cost=cost_total_usd,
            weighted_tokens=weighted_tokens,
            total_input_tokens=token_summary["total_input_tokens"],
            llm_output_sum=token_summary["llm_output_sum"],
        )

        await self.comm.event(
            agent="accounting",
            type="accounting.usage",
            title=f"💰 Turn Cost: ${cost_total_usd:.6f}",
            step="accounting",
            data={
                "breakdown": cost_breakdown,
                "cost_total_usd": cost_total_usd,
                "weighted_tokens": weighted_tokens,
                "ranked_tokens": ranked_tokens,
                "total_input_tokens": token_summary["total_input_tokens"],
                "llm_output_sum": token_summary["llm_output_sum"],
                "summary": compact_summary,
                "agent_costs": agent_costs,
            },
            markdown=full_markdown,
            status="completed",
        )

        await self.comm.service_event(
            type="accounting.usage",
            step="accounting",
            status="completed",
            title=f"💰 Turn Cost: ${cost_total_usd:.6f}",
            data={
                "breakdown": cost_breakdown,
                "cost_total_usd": cost_total_usd,
                "weighted_tokens": weighted_tokens,
                "ranked_tokens": ranked_tokens,
                "total_input_tokens": token_summary["total_input_tokens"],
                "llm_output_sum": token_summary["llm_output_sum"],
                "summary": compact_summary,
                "agent_costs": agent_costs,
            },
            agent="accounting",
            markdown=full_markdown,
        )

        return ranked_tokens, result

    async def run_accounting(
        self,
        tenant: str,
        project: str,
        user_id: str,
        user_type: str,
        thread_id: str,
        turn_id: str,
        usage_from: str,
    ):
        return await self.apply_accounting(
            tenant=tenant,
            project=project,
            user_id=user_id,
            user_type=user_type,
            thread_id=thread_id,
            turn_id=turn_id,
            usage_from=usage_from,
        )

    # ---------- Optional SDK services ----------

    async def get_conv_index(self):
        if self._conv_idx is not None:
            return self._conv_idx
        if not self.pg_pool:
            return None
        from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex

        self._conv_idx = ConvIndex(pool=self.pg_pool)
        await self._conv_idx.init()
        return self._conv_idx

    async def get_kb_client(self):
        if self._kb is not None:
            return self._kb
        if not self.pg_pool:
            return None
        from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient

        self._kb = KBClient(pool=self.pg_pool)
        await self._kb.init()
        return self._kb

    async def get_ctx_client(self):
        if self.ctx_client is not None:
            return self.ctx_client
        conv_idx = await self.get_conv_index()
        if conv_idx is None:
            return None
        if self._store is None:
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

            self._store = ConversationStore(self.settings.STORAGE_PATH)
        from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient

        self.ctx_client = ContextRAGClient(
            conv_idx=conv_idx,
            store=self._store,
            model_service=self.models_service,
        )
        return self.ctx_client

    async def close_optional_services(self) -> None:
        if self._kb:
            try:
                await self._kb.close()
            except Exception:
                pass
        if self._conv_idx:
            try:
                await self._conv_idx.close()
            except Exception:
                pass
        self._store = None

        self._kb = None
        self._conv_idx = None
