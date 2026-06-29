# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
#
# ── entrypoint.py ──
# user-memories — a memory-only app.
#
# Goal:
#   Expose the user's memories ONCE, as a standalone surface (the SDK memories
#   widget + the `mem` named service), so other apps/scenes embed it by iframe
#   instead of each republishing the memory module. This is the cleaner paradigm
#   than the current "embedded module" approach (e.g. in `versatile`).
#
# How:
#   The entrypoint derives BaseEntrypointWithEconomicsAndMemory, which already
#   wires the memory subsystem (widget operations + the `mem` named-service
#   provider) and the economics guard. This app only ENABLES the memory widget
#   and points the build at the SDK widget source — it ships no UI of its own.
#
# Served surfaces (provided by the mixin once memory is enabled):
#   - widget  `memories`  -> /api/integrations/bundles/{tenant}/{project}/user-memories@2026-06-26/widgets/memories
#   - named service `mem` -> registered for cross-app consumption (discovery via Redis)

from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import StateGraph, START, END

from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload, external_events_text
from kdcube_ai_app.infra.service_hub.inventory import Config, BundleState
from kdcube_ai_app.infra.plugin.bundle_loader import bundle_entrypoint, bundle_id, mcp
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_memory import (
    BaseEntrypointWithEconomicsAndMemory,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry import CredentialEnvelope
from kdcube_ai_app.apps.chat.sdk.solutions.connections.hub import delegated_primary_user_id

try:
    from . import memory_mcp_tools
except Exception:  # pragma: no cover - bundle loader may import entrypoint as a loose module
    import memory_mcp_tools  # type: ignore

BUNDLE_ID = "user-memories@2026-06-26"
WORKFLOW_NAME = "user_memories"

# The memories widget is built from the SDK source (shared, single copy). This
# app does NOT keep its own ui/ folder — the platform materializes the widget at
# build time from this src_folder.
MEMORY_WIDGET_SRC = "sdk://context/memory/ui/widget/memories"
WIDGET_BUILD_COMMAND = (
    "npm install --no-package-lock && OUTDIR=<VI_BUILD_DEST_ABSOLUTE_PATH> npm run build"
)


@bundle_entrypoint(
    name=WORKFLOW_NAME,
    version="1.0.0",
    priority=100,
    allowed_roles_config="surfaces.as_provider.bundle.visibility.allowed_roles",
)
@bundle_id(id=BUNDLE_ID)
class UserMemoriesEntrypoint(BaseEntrypointWithEconomicsAndMemory):
    """Memory-only app: serves the SDK user-memories widget and the `mem` named
    service. No chat product surface, no embedded memory copy — other apps embed
    this widget by iframe and consume `mem` as a named service."""

    def __init__(
        self,
        config: Config,
        pg_pool: Any = None,
        redis: Any = None,
        comm_context: ExternalEventPayload = None,
    ):
        super().__init__(
            config=config,
            pg_pool=pg_pool,
            redis=redis,
            comm_context=comm_context,
        )
        # The base entrypoint expects a workflow graph; this app has no chat
        # product, so the graph is a no-op that points callers at the widget.
        self.graph = self._build_graph()

    def configuration_defaults(self) -> Dict[str, Any]:
        defaults = {
            "surfaces": {
                "as_provider": {
                    "bundle": {"visibility": {"allowed_roles": []}},
                    "mcp": {
                        "memories": {
                            "auth": {
                                "mode": "managed",
                                "authority_id": "delegated_client",
                                "tools": {
                                    "memory_search": {"grants": ["memories:read"]},
                                    "memory_get": {"grants": ["memories:read"]},
                                },
                                "selected_tool_grants": True,
                            },
                        },
                    },
                    "widget": {
                        "memories": {"visibility": {"user_types": [], "roles": []}},
                    },
                },
            },
            # Enable the memory subsystem. The mixin's memory_configuration_defaults()
            # fill in the rest (reconciliation, snapshots, schema). Announce/tools
            # stay off — this app has no chat agent to inject memories into; the
            # WIDGET (and the `mem` named service) are the product.
            "memory": {
                "enabled": True,
                "announce": {"enabled": False},
                "tools": {"enabled": False},
                "widget": {
                    "enabled": True,
                    "allow_write": True,
                    # This is the dedicated memory surface, so default to the
                    # user's whole memory set across apps, not one app's slice.
                    "default_scope_filter": "all_user_memories",
                    "allow_all_user_memories": True,
                    "limit": 30,
                },
            },
            "ui": {
                "widgets": {
                    "memories": {
                        "enabled": True,
                        "src_folder": MEMORY_WIDGET_SRC,
                        "build_command": WIDGET_BUILD_COMMAND,
                    },
                },
            },
        }
        return self._deep_merge_props(super().configuration_defaults(), defaults)

    def _memory_mcp_scope(self, request=None):
        from kdcube_ai_app.apps.chat.sdk.context.memory import MemoryScope

        current = self._memory_scope()
        user_id = ""
        envelope = self._memory_mcp_credential(request)
        if envelope is not None:
            user_id = delegated_primary_user_id(envelope)
        bundle_spec = getattr(getattr(self, "config", None), "ai_bundle_spec", None)
        return MemoryScope(
            tenant=current.tenant,
            project=current.project,
            user_id=user_id or current.user_id,
            bundle_id=str(getattr(bundle_spec, "id", None) or current.bundle_id or "").strip(),
        ).normalized()

    def _memory_mcp_credential(self, request=None):
        delegated = getattr(getattr(request, "state", None), "delegated_credential", None) if request is not None else None
        if not isinstance(delegated, dict):
            return None
        envelope = CredentialEnvelope.coerce(delegated.get("authority"))
        return envelope if (envelope.credential_kind or envelope.subject) else None

    def _memory_mcp_grantor_authority(self, request=None) -> Dict[str, Any]:
        delegated = getattr(getattr(request, "state", None), "delegated_credential", None) if request is not None else None
        if not isinstance(delegated, dict):
            return {}
        grant_record = delegated.get("grant_record")
        if not isinstance(grant_record, dict):
            return {}
        authority = grant_record.get("grantor_authority")
        return dict(authority) if isinstance(authority, dict) else {}

    async def _memory_mcp_projection(self, scope, request=None) -> Dict[str, Any]:
        state = getattr(request, "state", None) if request is not None else None
        cached = getattr(state, "_kdcube_memory_mcp_projection", None) if state is not None else None
        if isinstance(cached, dict):
            return cached
        envelope = self._memory_mcp_credential(request)
        if envelope is not None:
            try:
                from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_operation

                result = await call_bundle_operation(
                    bundle_id=self._memory_identity_family_bundle_id(),
                    operation="delegated_identity_scope_resolve",
                    data={
                        "credential": envelope.to_dict(),
                        "grantor_authority": self._memory_mcp_grantor_authority(request),
                    },
                    tenant=scope.tenant,
                    project=scope.project,
                    route="operations",
                )
                if isinstance(result, dict) and result.get("ok", True):
                    if state is not None:
                        try:
                            setattr(state, "_kdcube_memory_mcp_projection", result)
                        except Exception:
                            pass
                    return result
            except Exception as exc:
                try:
                    self.logger.log(f"[memory.delegated_identity_scope] resolve failed, single-grantor fallback: {exc}", "WARNING")
                except Exception:
                    pass
        return {}

    async def _memory_mcp_read_user_ids(self, scope, request=None):
        projection = await self._memory_mcp_projection(scope, request=request)
        raw = projection.get("memory_user_ids") if isinstance(projection, dict) else None
        if isinstance(raw, (list, tuple)):
            user_ids = [str(uid or "").strip() for uid in raw if str(uid or "").strip()]
            if user_ids:
                return user_ids
        return await self._memory_read_user_ids(scope=scope)

    async def _memory_mcp_economics_subject(self, scope, request=None):
        from kdcube_ai_app.apps.chat.sdk.infra.economics.enforcement import (
            economics_subject_from_authority_context,
        )

        projection = await self._memory_mcp_projection(scope, request=request)
        economics = projection.get("economics") if isinstance(projection, dict) else None
        if isinstance(economics, dict) and str(economics.get("user_id") or "").strip():
            return economics_subject_from_authority_context(
                tenant=scope.tenant,
                project=scope.project,
                identity_authority=economics,
                actor_user_id=str((projection.get("provenance") or {}).get("actor_identity") or ""),
                fallback_user_id=str(economics.get("user_id") or "").strip(),
                fallback_roles=tuple(economics.get("roles") or ()),
                fallback_permissions=tuple(economics.get("permissions") or ()),
                provenance=dict(economics.get("provenance") or projection.get("provenance") or {}),
            )
        return self._memory_search_econ_subject()

    async def _memory_mcp_query_embedding(self, scope, query: str, request=None):
        normalized = str(query or "").strip()
        if not normalized or not self._memory_economics_enabled():
            return None
        try:
            from kdcube_ai_app.apps.chat.sdk.infra.economics.policy import EconomicsLimitException
            from kdcube_ai_app.apps.chat.sdk.solutions.search_service import make_search_model_service

            subject = await self._memory_mcp_economics_subject(scope, request=request)
            if not (subject.tenant and subject.project and subject.user_id and subject.user_id != "anonymous"):
                return None
            model_service = make_search_model_service(self, flow="memory.search", subject=subject)
            embed_query = getattr(model_service, "embed_search_query", None)
            if callable(embed_query):
                return await embed_query(normalized, flow="memory.search")
        except EconomicsLimitException as exc:
            try:
                self.logger.log(
                    f"[memory.mcp.search] economics limit; lexical fallback: user={scope.user_id} code={getattr(exc, 'code', 'rate_limited')}",
                    "INFO",
                )
            except Exception:
                pass
            return None
        except Exception as exc:
            try:
                self.logger.log(f"[memory.mcp.search] metered embed failed; lexical fallback: {exc}", "WARNING")
            except Exception:
                pass
            return None
        return None

    @mcp(alias="memories", route="public", transport="streamable-http", auth_config="surfaces.as_provider.mcp.memories.auth")
    def memories_mcp(self, request=None, **kwargs):
        return memory_mcp_tools.build_user_memories_mcp_app(
            name="KDCube user memories",
            store_factory=self._memory_store,
            scope_factory=lambda: self._memory_mcp_scope(request=request),
            read_user_ids_factory=lambda scope: self._memory_mcp_read_user_ids(scope, request=request),
            search_embedding_factory=lambda scope, query: self._memory_mcp_query_embedding(scope, query, request=request),
        )

    def _build_graph(self) -> StateGraph:
        g = StateGraph(BundleState)

        async def note(state: BundleState) -> BundleState:
            # No chat product here. If a message ever reaches this app, point the
            # caller at the widget rather than failing.
            _ = external_events_text(state.get("external_events") or [])
            state["final_answer"] = (
                "This app serves your memories. Open the Memories widget to search, "
                "add, and manage them."
            )
            return state

        g.add_node("note", note)
        g.add_edge(START, "note")
        g.add_edge("note", END)
        return g.compile()

    async def execute_core(
        self,
        *,
        state: Dict[str, Any],
        thread_id: str,
        params: Dict[str, Any],
    ):
        return await self.graph.ainvoke(state)
