from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from kdcube_ai_app.apps.chat.sdk.context.memory.events.resolver import (
    memory_ref_capabilities,
    resolve_memory_ref_action,
)
from kdcube_ai_app.apps.chat.sdk.solutions.canvas import api as canvas_api
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.resolver import (
    CallableCanvasObjectResolver,
    build_default_canvas_resolver_registry,
)
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.storage import CanvasStore
from kdcube_ai_app.apps.chat.sdk.solutions.chat.events.resolver import (
    conversation_ref_capabilities,
    resolve_conversation_ref_action,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    named_service_canvas_resolver_namespaces,
    register_configured_named_service_canvas_resolvers,
)
from kdcube_ai_app.apps.chat.sdk.solutions.react.events.resolver import resolve_event_ref_action
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import DataBusResult


@dataclass(frozen=True)
class CanvasRuntimeConfig:
    bundle_id: str
    artifact_prefix: str
    origin_prefix: str
    state_event_source_id: str
    ui_event_type: str
    artifact_resolver_name: str


def payload_from_call(data: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    if isinstance(data, Mapping):
        return {str(k): v for k, v in data.items()}
    return {
        str(k): v
        for k, v in kwargs.items()
        if k not in {"request", "alias", "route", "endpoint_alias"} and v is not None
    }


def protocol_string(payload: Mapping[str, Any], field: str, default: str = "") -> str:
    value = payload.get(field)
    if value is None:
        return default
    text = str(value).strip()
    if text:
        return text
    return default


class VersatileCanvasService:
    def __init__(
        self,
        entrypoint: Any,
        *,
        config: CanvasRuntimeConfig,
        logger: logging.Logger,
    ) -> None:
        self.entrypoint = entrypoint
        self.config = config
        self.logger = logger

    def resolve_user_id(self, payload: Mapping[str, Any]) -> str:
        value = payload.get("user_id")
        if value is not None and str(value).strip():
            return str(value).strip()
        comm_user_id = getattr(getattr(self.entrypoint, "comm", None), "user_id", None)
        if comm_user_id is not None and str(comm_user_id).strip():
            return str(comm_user_id).strip()
        return "anonymous"

    def storage_root_or_error(self):
        storage_root = self.entrypoint.bundle_storage_root()
        if not storage_root:
            raise RuntimeError("Bundle storage backend is not configured for this bundle.")
        return storage_root

    def log_failure(self, alias: str, payload: Mapping[str, Any], result: Mapping[str, Any]) -> Mapping[str, Any]:
        if result.get("ok") is not False:
            return result
        error_text = result.get("error")
        if error_text is None:
            error_text = result.get("detail")
        if error_text is None:
            error_text = result.get("message")
        if error_text is None:
            error_text = "operation failed"
        self.logger.warning(
            "[canvas] operation failed alias=%s error=%s context=%s",
            alias,
            str(error_text)[:500],
            {
                "canvas_id": result.get("canvas_id") if result.get("canvas_id") is not None else payload.get("canvas_id"),
                "canvas_name": result.get("canvas_name") if result.get("canvas_name") is not None else payload.get("canvas_name"),
                "story_id": result.get("story_id") if result.get("story_id") is not None else payload.get("story_id"),
                "revision": result.get("revision"),
                "expected_revision": result.get("expected_revision"),
                "current_revision": result.get("current_revision"),
            },
        )
        return result

    def store(self, payload: Mapping[str, Any], *, user_id: str | None = None) -> CanvasStore:
        ident = self.entrypoint.runtime_identity()
        tenant = protocol_string(payload, "tenant", protocol_string(ident, "tenant", "default"))
        project = protocol_string(payload, "project", protocol_string(ident, "project", "default"))
        revision_retention = self.entrypoint.bundle_prop("canvas.revision_retention", 80)
        if revision_retention is None:
            revision_retention = 80
        resolved_user_id = user_id
        if resolved_user_id is None or not str(resolved_user_id).strip():
            resolved_user_id = self.resolve_user_id(payload)
        return CanvasStore(
            tenant=tenant,
            project=project,
            bundle_id=self.config.bundle_id,
            user_id=resolved_user_id,
            storage_root=self.storage_root_or_error(),
            revision_retention=int(revision_retention),
            artifact_prefix=self.config.artifact_prefix,
            origin_prefix=self.config.origin_prefix,
            state_event_source_id=self.config.state_event_source_id,
            ui_event_type=self.config.ui_event_type,
            artifact_resolver_name=self.config.artifact_resolver_name,
        )

    def target(self) -> Dict[str, str]:
        return {
            "agent_id": "canvas",
            "surface": "canvas",
            "story_kind": "canvas",
            "conversation_role": "canvas",
        }

    def object_resolvers(self, payload: Mapping[str, Any], *, user_id: str):
        store = self.store(payload, user_id=user_id)
        registry = build_default_canvas_resolver_registry(store)
        ident = self.entrypoint.runtime_identity()
        tenant = protocol_string(payload, "tenant", protocol_string(ident, "tenant", "default"))
        project = protocol_string(payload, "project", protocol_string(ident, "project", "default"))

        async def _resolve_fi(
            action_payload: Mapping[str, Any],
            resolver_user_id: str,
            resolver_story_id: str,
            action: str,
        ) -> Mapping[str, Any]:
            return await resolve_event_ref_action(
                {**dict(action_payload if action_payload is not None else {}), "action": action},
                tenant=tenant,
                project=project,
                user_id=resolver_user_id,
                storage_path=str(getattr(self.entrypoint.settings, "STORAGE_PATH", "")),
                story_id=resolver_story_id,
                require_embedded_conversation=True,
            )

        registry.register(
            CallableCanvasObjectResolver(
                namespace="fi",
                resolver="react.event_ref",
                resolver_status="implemented",
                capabilities={"preview": False, "open": False, "download": True, "rehost": False},
                handler=_resolve_fi,
            )
        )

        async def _resolve_mem(
            action_payload: Mapping[str, Any],
            resolver_user_id: str,
            resolver_story_id: str,
            action: str,
        ) -> Mapping[str, Any]:
            del resolver_user_id, resolver_story_id
            return await resolve_memory_ref_action(
                {**dict(action_payload if action_payload is not None else {}), "action": action},
                store=self.entrypoint._memory_store(),
                scope=self.entrypoint._memory_scope(),
                scope_filter=self.entrypoint._memory_scope_filter("current_bundle"),
            )

        registry.register(
            CallableCanvasObjectResolver(
                namespace="mem",
                resolver="sdk.memory",
                resolver_status="implemented",
                capabilities=memory_ref_capabilities(),
                handler=_resolve_mem,
            )
        )

        async def _fetch_conversation_details(
            fetch_user_id: str,
            conversation_id: str,
            bundle_id: Optional[str],
        ) -> Optional[Mapping[str, Any]]:
            from kdcube_ai_app.apps.chat.sdk.context.retrieval.ctx_rag import ContextRAGClient
            from kdcube_ai_app.apps.chat.sdk.context.vector.conv_index import ConvIndex
            from kdcube_ai_app.apps.chat.sdk.storage.conversation_store import ConversationStore

            conv_idx = ConvIndex(pool=self.entrypoint.pg_pool)
            await conv_idx.init()
            ctx_client = ContextRAGClient(
                conv_idx=conv_idx,
                store=ConversationStore(self.entrypoint.settings.STORAGE_PATH),
                model_service=self.entrypoint.models_service,
            )
            return await ctx_client.get_conversation_details(
                user_id=fetch_user_id,
                conversation_id=conversation_id,
                bundle_id=bundle_id,
            )

        async def _resolve_conv(
            action_payload: Mapping[str, Any],
            resolver_user_id: str,
            resolver_story_id: str,
            action: str,
        ) -> Mapping[str, Any]:
            del resolver_story_id
            return await resolve_conversation_ref_action(
                {**dict(action_payload if action_payload is not None else {}), "action": action},
                user_id=resolver_user_id,
                fetch_details=_fetch_conversation_details,
            )

        registry.register(
            CallableCanvasObjectResolver(
                namespace="conv",
                resolver="sdk.chat.conversation",
                resolver_status="implemented",
                capabilities=conversation_ref_capabilities(),
                handler=_resolve_conv,
            )
        )

        register_configured_named_service_canvas_resolvers(
            registry,
            namespaces=named_service_canvas_resolver_namespaces(self.entrypoint.bundle_props),
            tenant=tenant,
            project=project,
            logger=self.logger,
        )
        return registry

    def apply_patch_payload(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        user_id = self.resolve_user_id(payload)
        story_id = protocol_string(payload, "story_id")
        try:
            result = canvas_api.patch(
                payload=payload,
                store=self.store(payload, user_id=user_id),
                user_id=user_id,
                story_id=story_id,
                target=self.target(),
            )
        except Exception as exc:
            result = {"ok": False, "user_id": user_id, "story_id": story_id, "error": str(exc)}
        self.log_failure("canvas_patch", payload, result)
        return result

    async def attachment_upload(
        self,
        payload: Mapping[str, Any],
        *,
        uploaded_files: list[Any],
    ) -> Dict[str, Any]:
        user_id = self.resolve_user_id(payload)
        story_id = protocol_string(payload, "story_id")
        try:
            result = canvas_api.upload_attachments(
                payload=payload,
                uploaded_files=uploaded_files,
                store=self.store(payload, user_id=user_id),
                user_id=user_id,
                story_id=story_id,
            )
        except Exception as exc:
            self.logger.exception("[canvas.attachment_upload] failed story_id=%s", story_id)
            result = {"ok": False, "user_id": user_id, "story_id": story_id, "error": str(exc)}
        self.log_failure("canvas_attachment_upload", payload, result)
        return result

    async def object_action(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        user_id = self.resolve_user_id(payload)
        story_id = protocol_string(payload, "story_id")
        try:
            result = await canvas_api.object_action(
                payload=payload,
                registry=self.object_resolvers(payload, user_id=user_id),
                user_id=user_id,
                story_id=story_id,
            )
        except Exception as exc:
            result = {"ok": False, "user_id": user_id, "story_id": story_id, "error": str(exc)}
        self.log_failure("canvas_object_action", payload, result)
        return result

    def operation(self, alias: str, payload: Mapping[str, Any], operation: Any) -> Dict[str, Any]:
        user_id = self.resolve_user_id(payload)
        story_id = protocol_string(payload, "story_id")
        try:
            result = operation(user_id=user_id, story_id=story_id)
        except Exception as exc:
            result = {"ok": False, "user_id": user_id, "story_id": story_id, "error": str(exc)}
        self.log_failure(alias, payload, result)
        return result

    def data_bus_patch_result(self, ctx: Any, message: Any) -> DataBusResult:
        payload = dict(message.payload if message.payload is not None else {})
        actor = dict(message.actor if message.actor is not None else {})
        if ctx.tenant and "tenant" not in payload:
            payload["tenant"] = ctx.tenant
        if ctx.project and "project" not in payload:
            payload["project"] = ctx.project
        if actor.get("user_id") and "user_id" not in payload:
            payload["user_id"] = actor["user_id"]
        if actor.get("fingerprint") and "fingerprint" not in payload:
            payload["fingerprint"] = actor["fingerprint"]
        if actor.get("roles") and "roles" not in payload:
            payload["roles"] = list(actor.get("roles") if actor.get("roles") is not None else [])
        if actor.get("user_id") and "actor" not in payload:
            payload["actor"] = actor["user_id"]
        if message.object_ref and "object_ref" not in payload:
            payload["object_ref"] = message.object_ref

        result = self.apply_patch_payload(payload)
        if result.get("ok") is False and result.get("current_revision") is not None:
            return DataBusResult.conflict(message, result)
        return DataBusResult.ok(message, result)
