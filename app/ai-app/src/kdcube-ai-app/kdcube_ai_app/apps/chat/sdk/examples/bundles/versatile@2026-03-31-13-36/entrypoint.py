from __future__ import annotations

import base64
import json
import os
import tempfile
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph

from kdcube_ai_app.apps.chat.sdk.context.vector.conv_ticket_store import ConvTicketStore
from kdcube_ai_app.apps.chat.sdk.protocol import ChatTaskPayload
from kdcube_ai_app.apps.chat.sdk.runtime.exec_runtime_config import normalize_exec_runtime_config
from kdcube_ai_app.apps.chat.sdk.runtime.tool_subsystem import create_tool_subsystem_with_mcp
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import (
    BaseEntrypointWithEconomics,
)
from kdcube_ai_app.apps.chat.sdk.tools.exec_tools import (
    build_exec_output_contract,
    run_exec_tool,
)
from kdcube_ai_app.apps.chat.sdk.viz.patch_platform_dashboard import patch_dashboard
from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage
from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, api, ui_widget
from kdcube_ai_app.infra.service_hub.inventory import BundleState, Config

from . import tools_descriptor
from .event_filter import BundleEventFilter
from .orchestrator.workflow import VersatileWorkflow
from .preferences_store import (
    build_preferences_storage,
    build_preferences_canvas_document,
    export_preferences_canvas_xlsx,
    get_preferences_snapshot,
    import_preferences_canvas_xlsx,
    build_widget_payload,
    save_preferences_canvas_entries,
    save_preferences_canvas_document,
)

BUNDLE_ID = "versatile"


@agentic_workflow(name=BUNDLE_ID, version="1.0.0", priority=100)
class VersatileEntrypoint(BaseEntrypointWithEconomics):
    """All-features reference bundle for bundle builders."""

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ChatTaskPayload = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
            event_filter=BundleEventFilter(),
        )
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        g = StateGraph(BundleState)

        async def orchestrate(state: BundleState) -> BundleState:
            from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
            from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
            from kdcube_ai_app.apps.chat.sdk.retrieval.kb_client import KBClient
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

            conv_idx = ConvIndex(pool=self.pg_pool)
            kb = KBClient(pool=self.pg_pool)
            store = ConversationStore(self.settings.STORAGE_PATH)
            conv_ticket_store = ConvTicketStore(pool=self.pg_pool)

            await conv_idx.init()
            await kb.init()
            await conv_ticket_store.init()

            ctx_client = ContextRAGClient(
                conv_idx=conv_idx,
                store=store,
                model_service=self.models_service,
            )

            try:
                orch = VersatileWorkflow(
                    conv_idx=conv_idx,
                    kb=kb,
                    store=store,
                    comm=self.comm,
                    model_service=self.models_service,
                    conv_ticket_store=conv_ticket_store,
                    config=self.config,
                    comm_context=self.comm_context,
                    ctx_client=ctx_client,
                    bundle_props=self.bundle_props,
                )
                res = await orch.process(
                    {
                        "request_id": state["request_id"],
                        "tenant": state["tenant"],
                        "project": state["project"],
                        "user": state["user"],
                        "user_type": state["user_type"] or "anonymous",
                        "session_id": state["session_id"],
                        "conversation_id": state["conversation_id"],
                        "turn_id": state["turn_id"],
                        "text": state["text"],
                        "attachments": state.get("attachments") or [],
                    }
                )
                if not isinstance(res, dict):
                    res = {}
                state["final_answer"] = res.get("answer") or ""
                state["followups"] = res.get("followups") or []
            except Exception as exc:
                await self.report_turn_error(state=state, exc=exc, title="Turn Error")

            return state

        g.add_node("orchestrate", orchestrate)
        g.add_edge(START, "orchestrate")
        g.add_edge("orchestrate", END)
        return g.compile()

    def on_bundle_load(self, **kwargs) -> None:
        storage_root = self.bundle_storage_root()
        if storage_root:
            (storage_root / "_ops").mkdir(parents=True, exist_ok=True)
        return None

    def _preferences_storage(self) -> Optional[AIBundleStorage]:
        actor = getattr(self.comm_context, "actor", None)
        bundle_spec = getattr(getattr(self, "config", None), "ai_bundle_spec", None)
        tenant = getattr(actor, "tenant_id", None) or getattr(self.comm, "tenant", None) or self.settings.TENANT
        project = getattr(actor, "project_id", None) or getattr(self.comm, "project", None) or self.settings.PROJECT
        bundle_id = getattr(bundle_spec, "id", None) or BUNDLE_ID
        if not tenant or not project or not bundle_id:
            return None
        return build_preferences_storage(
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )

    def _preferences_ops_root(self) -> Path:
        storage_root = self.bundle_storage_root()
        if storage_root:
            root = storage_root / "_ops"
            root.mkdir(parents=True, exist_ok=True)
            return root
        root = Path(tempfile.gettempdir()) / "kdcube-versatile-ops"
        root.mkdir(parents=True, exist_ok=True)
        return root

    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:adjustments-horizontal",
            "lucide": "SlidersHorizontal",
        },
        alias="preferences",
        roles=("registered", "privileged", "free", "payasyougo", "admin"),
    )
    def preferences_widget(self, user_id: Optional[str] = None, **kwargs):
        storage = self._preferences_storage()
        if not storage:
            return ["<p>Bundle storage backend is not configured for this bundle.</p>"]

        bundle_root = self._bundle_root()
        if not bundle_root:
            return ["<p>Bundle root is unavailable.</p>"]

        try:
            target_user = user_id or getattr(self.comm, "user_id", None) or "anonymous"
            payload = build_widget_payload(storage=storage, user_id=target_user)
            tsx_path = Path(bundle_root) / "ui" / "PreferencesBrowser.tsx"
            content = tsx_path.read_text(encoding="utf-8")
            rendered = content.replace("__PREFERENCES_JSON__", json.dumps(payload))
            actor = getattr(self.comm_context, "actor", None)
            bundle_spec = getattr(getattr(self, "config", None), "ai_bundle_spec", None)
            rendered = patch_dashboard(
                input_content=rendered,
                base_url=f"http://localhost:{os.environ.get('CHAT_APP_PORT') or '8010'}",
                default_tenant=getattr(actor, "tenant_id", None) or self.settings.TENANT,
                default_project=getattr(actor, "project_id", None) or self.settings.PROJECT,
                default_app_bundle_id=getattr(bundle_spec, "id", None) or BUNDLE_ID,
                access_token=None,
                id_token=None,
                id_token_header="X-ID-Token",
            )
            return [self._render_dashboard_html(content=rendered, title="Preferences Browser")]
        except Exception:
            self.logger.log(traceback.format_exc(), "ERROR")
            return ["<p>Unable to render the preferences widget right now.</p>"]

    @api(alias="preferences_widget_data")
    def preferences_widget_data(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ):
        storage = self._preferences_storage()
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": user_id or fingerprint or "anonymous",
                "current": {},
                "recent": [],
                "matched_count": 0,
            }

        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        payload = build_widget_payload(storage=storage, user_id=target_user)
        payload["ok"] = True
        return payload

    @api(alias="preferences_canvas_data")
    def preferences_canvas_data(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        **kwargs,
    ):
        storage = self._preferences_storage()
        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": target_user,
                "path": None,
                "document_format": "entries",
                "entries": [],
                "document_text": "{}\n",
                "changed_keys": [],
                "removed_keys": [],
            }

        payload = build_preferences_canvas_document(storage=storage, user_id=target_user)
        payload["ok"] = True
        return payload

    @api(alias="preferences_canvas_save")
    def preferences_canvas_save(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        document_text: Optional[str] = None,
        entries: Optional[list[dict[str, Any]]] = None,
        content: Optional[str] = None,
        text: Optional[str] = None,
        **kwargs,
    ):
        storage = self._preferences_storage()
        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": target_user,
            }

        try:
            if entries is not None:
                payload = save_preferences_canvas_entries(
                    storage=storage,
                    user_id=target_user,
                    entries=entries,
                )
            else:
                raw_document = document_text
                if raw_document is None:
                    raw_document = content
                if raw_document is None:
                    raw_document = text
                if raw_document is None and "document" in kwargs:
                    raw_document = kwargs.get("document")
                if raw_document is None:
                    return {
                        "ok": False,
                        "error": "entries or document_text is required.",
                        "user_id": target_user,
                    }

                payload = save_preferences_canvas_document(
                    storage=storage,
                    user_id=target_user,
                    document_text=str(raw_document),
                )
        except ValueError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "user_id": target_user,
            }

        payload["ok"] = True
        return payload

    @api(alias="preferences_canvas_export_excel")
    def preferences_canvas_export_excel(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        filename: Optional[str] = None,
        **kwargs,
    ):
        storage = self._preferences_storage()
        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": target_user,
            }

        try:
            raw = export_preferences_canvas_xlsx(storage=storage, user_id=target_user)
        except RuntimeError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "user_id": target_user,
            }

        safe_name = Path(filename or f"{target_user}-preferences.xlsx").name or "preferences.xlsx"
        return {
            "ok": True,
            "user_id": target_user,
            "filename": safe_name,
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "content_b64": base64.b64encode(raw).decode("ascii"),
        }

    @api(alias="preferences_canvas_import_excel")
    def preferences_canvas_import_excel(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        content_b64: Optional[str] = None,
        **kwargs,
    ):
        storage = self._preferences_storage()
        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
                "user_id": target_user,
            }

        raw_content = content_b64 or kwargs.get("file_b64") or kwargs.get("content")
        if not raw_content:
            return {
                "ok": False,
                "error": "content_b64 is required.",
                "user_id": target_user,
            }

        try:
            binary = base64.b64decode(str(raw_content), validate=True)
            entries = import_preferences_canvas_xlsx(binary)
            payload = save_preferences_canvas_entries(
                storage=storage,
                user_id=target_user,
                entries=entries,
            )
        except (ValueError, RuntimeError) as exc:
            return {
                "ok": False,
                "error": str(exc),
                "user_id": target_user,
            }

        payload["ok"] = True
        return payload

    @api(alias="preferences_exec_report")
    async def preferences_exec_report(
        self,
        user_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        recency: int = 10,
        kwords: str = "",
        **kwargs,
    ):
        storage = self._preferences_storage()
        if not storage:
            return {
                "ok": False,
                "error": "Bundle storage backend is not configured for this bundle.",
            }

        tool_subsystem, _ = create_tool_subsystem_with_mcp(
            service=self.models_service,
            comm=self.comm,
            logger=self.logger,
            bundle_spec=self.config.ai_bundle_spec,
            context_rag_client=self.ctx_client,
            registry={},
            raw_tool_specs=tools_descriptor.TOOLS_SPECS,
            tool_runtime=getattr(tools_descriptor, "TOOL_RUNTIME", None),
            mcp_tool_specs=getattr(tools_descriptor, "MCP_TOOL_SPECS", None) or [],
            mcp_services_config=self.bundle_prop("mcp.services"),
            mcp_env_json=os.environ.get("MCP_SERVICES") or "",
        )

        op_root = self._preferences_ops_root() / "preferences_exec_report"
        workdir = op_root / "work"
        outdir = op_root / "out"
        exec_id = f"preferences-{uuid.uuid4().hex[:10]}"
        target_user = user_id or fingerprint or getattr(self.comm, "user_id", None) or "anonymous"
        try:
            report_recency = max(1, min(100, int(recency)))
        except Exception:
            report_recency = 10
        report_keywords = str(kwords or "").strip()
        snapshot = get_preferences_snapshot(storage=storage, user_id=target_user)
        current = snapshot.get("current") or {}
        events = snapshot.get("items") or []
        contract_items = [
            {
                "filename": "turn_preferences_exec/files/preferences_exec_report.md",
                "description": "Markdown report generated from shared bundle preference history.",
            }
        ]
        output_contract, contract, err = build_exec_output_contract(contract_items)
        if err or not output_contract or not contract:
            return {
                "ok": False,
                "error": err or {"message": "Failed to build exec output contract."},
            }

        code = f"""
from pathlib import Path
import json

current = json.loads({json.dumps(json.dumps(current, ensure_ascii=False))})
events = json.loads({json.dumps(json.dumps(events, ensure_ascii=False))})

report_keywords = {json.dumps(report_keywords)}
keyword_tokens = [token.lower() for token in report_keywords.split() if token.strip()]
if keyword_tokens:
    filtered_current = {{}}
    for key, item in current.items():
        haystack = f"{{key}} {{item.get('value', '')}} {{item.get('origin', '')}}".lower()
        if all(token in haystack for token in keyword_tokens):
            filtered_current[key] = item
    current = filtered_current

    filtered_events = []
    for event in events:
        haystack = (
            f"{{event.get('key', '')}} {{event.get('value', '')}} "
            f"{{event.get('origin', '')}} {{event.get('evidence', '')}}"
        ).lower()
        if all(token in haystack for token in keyword_tokens):
            filtered_events.append(event)
    events = filtered_events

lines = [
    "# Preferences Exec Report",
    "",
    f"User: {target_user}",
    f"Recency: {report_recency}",
    f"Keywords: {{report_keywords or '(none)'}}",
    "",
    "## Current preferences",
]

if current:
    for key, item in sorted(current.items()):
        if keyword_tokens:
            haystack = f"{{key}} {{item.get('value', '')}}".lower()
            if not all(keyword in haystack for keyword in keyword_tokens):
                continue
        lines.append(f"- {{key}}: {{item.get('value')}}")
else:
    lines.append("- No stored preferences yet.")

lines.extend(["", "## Recent observations"])
if events:
    for event in events[-{report_recency}:]:
        lines.append(
            f"- {{event.get('captured_at')}} | {{event.get('key')}} = {{event.get('value')}} "
            f"(origin={{event.get('origin')}})"
        )
else:
    lines.append("- No captured observations yet.")

report_path = Path(OUTPUT_DIR) / "turn_preferences_exec/files/preferences_exec_report.md"
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
print(f"wrote {{report_path}}")
"""

        envelope = await run_exec_tool(
            tool_manager=tool_subsystem,
            output_contract=output_contract,
            code=code,
            contract=contract,
            timeout_s=60,
            workdir=workdir,
            outdir=outdir,
            logger=self.logger,
            exec_id=exec_id,
            exec_runtime=normalize_exec_runtime_config(self.bundle_prop("execution.runtime")),
            bundle_storage_dir=str(self.bundle_storage_root()) if self.bundle_storage_root() else None,
        )
        report_relpath = "turn_preferences_exec/files/preferences_exec_report.md"
        report_abspath = outdir / report_relpath
        report_content_b64 = None
        if report_abspath.exists():
            try:
                report_content_b64 = base64.b64encode(report_abspath.read_bytes()).decode("ascii")
            except Exception:
                report_content_b64 = None
        return {
            "ok": bool(envelope.get("ok")),
            "report_text": envelope.get("report_text"),
            "items": envelope.get("items") or [],
            "out_dyn": envelope.get("out_dyn") or {},
            "error": envelope.get("error"),
            "recency": report_recency,
            "keywords": report_keywords,
            "report_filename": "preferences_exec_report.md",
            "report_mime": "text/markdown",
            "report_content_b64": report_content_b64,
        }

    @property
    def configuration(self) -> Dict[str, Any]:
        sonnet_45 = "claude-sonnet-4-5-20250929"
        haiku_4 = "claude-haiku-4-5-20251001"

        config = dict(super().configuration)
        role_models = dict(config.get("role_models") or {})
        for key, value in {
            "gate.simple": {"provider": "anthropic", "model": haiku_4},
            "answer.generator.simple": {"provider": "anthropic", "model": sonnet_45},
            "solver.coordinator.v2": {"provider": "anthropic", "model": sonnet_45},
            "solver.react.v2.decision.v2.strong": {"provider": "anthropic", "model": sonnet_45},
            "solver.react.v2.decision.v2.regular": {"provider": "anthropic", "model": haiku_4},
        }.items():
            role_models.setdefault(key, value)
        config["role_models"] = role_models

        preferences_cfg = dict(config.get("preferences") or {})
        preferences_cfg.setdefault("auto_capture", True)
        preferences_cfg.setdefault("max_recent_events", 25)
        preferences_cfg.setdefault("widget_max_events", 15)
        config["preferences"] = preferences_cfg

        execution_cfg = dict(config.get("execution") or {})
        execution_cfg.setdefault("runtime", {"mode": "docker"})
        config["execution"] = execution_cfg

        subsystems = dict(config.get("subsystems") or {})
        subsystems.setdefault("preferences_browser", {"dashboard": "ui/PreferencesBrowser.tsx"})
        config["subsystems"] = subsystems

        mcp = dict(config.get("mcp") or {})
        mcp.setdefault("services", {})
        config["mcp"] = mcp
        return config

    @property
    def app_quota_policies(self):
        from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import QuotaPolicy

        return {
            "anonymous": QuotaPolicy(
                max_concurrent=1,
                requests_per_day=5,
                requests_per_month=120,
                total_requests=None,
                tokens_per_hour=200_000,
                tokens_per_day=1_500_000,
                tokens_per_month=20_000_000,
            ),
            "free": QuotaPolicy(
                max_concurrent=2,
                requests_per_day=100,
                requests_per_month=30_000,
                total_requests=None,
                tokens_per_hour=500_000,
                tokens_per_day=2_000_000,
                tokens_per_month=30_000_000,
            ),
            "payasyougo": QuotaPolicy(
                max_concurrent=3,
                requests_per_day=500,
                requests_per_month=100_000,
                total_requests=None,
                tokens_per_hour=2_000_000,
                tokens_per_day=6_000_000,
                tokens_per_month=80_000_000,
            ),
            "admin": QuotaPolicy(max_concurrent=10),
        }

    async def execute_core(self, *, state: Dict[str, Any], thread_id: str, params: Dict[str, Any]):
        return await self.graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
