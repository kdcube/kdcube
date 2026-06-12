# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import logging
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.canvas.events.resolver import (
    CanvasObjectResolver,
    object_ref_from_payload,
)

from .client_tools import (
    named_service_namespace_client_resolver_config,
    named_service_namespace_provider_configs_from_config,
)
from .transports.api_client import NamedServiceEndpoint, call_named_service_endpoint
from .types import OBJECT_ACTION, OBJECT_RESOLVE, NamedServiceRequest

LOGGER = logging.getLogger("kdcube.sdk.named_services.canvas_resolver")


class NamedServiceCanvasObjectResolver(CanvasObjectResolver):
    """Canvas/chat object resolver backed by a configured named-service provider."""

    resolver_status = "configured"

    def __init__(
        self,
        *,
        namespace: str,
        endpoint: NamedServiceEndpoint,
        resolver: str | None = None,
        allowed_operations: Any = None,
    ) -> None:
        self.namespace = str(namespace or endpoint.namespace or "").strip().lower()
        self.endpoint = endpoint
        self.resolver = str(resolver or f"named_service.{endpoint.provider or self.namespace}").strip()
        self._capabilities: dict[str, bool] = {}
        self.allowed_operations = {
            str(item or "").strip()
            for item in (allowed_operations or ())
            if str(item or "").strip()
        }

    def _operation_allowed(self, operation: str) -> bool:
        return not self.allowed_operations or operation in self.allowed_operations

    def capabilities_for_ref(self, ref: str) -> dict[str, bool]:
        del ref
        return dict(self._capabilities)

    async def object_action(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: str,
        story_id: str,
        action: str,
    ) -> dict[str, Any]:
        object_ref = object_ref_from_payload(payload)
        normalized_action = str(action or "capabilities").strip().lower() or "capabilities"
        operation = OBJECT_RESOLVE if normalized_action in {"capabilities", "describe"} else OBJECT_ACTION
        base = self.base_response(ref=object_ref, action=normalized_action)
        if not self._operation_allowed(operation):
            return {
                **base,
                "ok": False,
                "status": 403,
                "error": "canvas_named_service_operation_not_allowed",
                "message": f"Canvas resolver for namespace {self.namespace!r} is not configured to call {operation!r}.",
                "operation": operation,
            }
        LOGGER.info(
            "Named-service canvas resolver call start: namespace=%s provider=%s bundle=%s operation=%s action=%s object_ref=%s user_id=%s story_id=%s",
            self.namespace,
            self.endpoint.provider or "",
            self.endpoint.bundle_id,
            operation,
            action,
            object_ref,
            user_id,
            story_id,
        )
        response = await call_named_service_endpoint(
            self.endpoint,
            NamedServiceRequest(
                operation=operation,
                provider=self.endpoint.provider,
                namespace=self.namespace,
                object_ref=object_ref,
                action=normalized_action,
                context={
                    "source": "canvas.object_resolve" if operation == OBJECT_RESOLVE else "canvas.object_action",
                    "user_id": user_id,
                    "story_id": story_id,
                },
                payload=dict(payload or {}),
            ),
        )
        if not response.ok:
            message = response.error.message if response.error else "Named service resolver failed"
            code = response.error.code if response.error else "named_service_resolver_failed"
            LOGGER.warning(
                "Named-service canvas resolver call failed: namespace=%s provider=%s bundle=%s operation=%s action=%s object_ref=%s status=%s error=%s",
                self.namespace,
                self.endpoint.provider or "",
                self.endpoint.bundle_id,
                operation,
                action,
                object_ref,
                response.status,
                code,
            )
            return {
                **base,
                "ok": False,
                "status": response.status,
                "error": code,
                "message": message,
                "provider": dict(response.provider or {}),
                "extra": dict(response.extra or {}),
            }

        result: dict[str, Any] = {
            **base,
            "ok": True,
            "operation": operation,
            "provider": dict(response.provider or {}),
            "object": dict(response.object or {}),
            "items": list(response.items or []),
            "extra": dict(response.extra or {}),
        }
        for key in (
            "content_base64",
            "download_url",
            "filename",
            "mime",
            "size_bytes",
            "text",
            "summary",
            "message",
            "json",
            "actions",
            "parent",
        ):
            if key in response.extra:
                result[key] = response.extra[key]
        if response.capabilities:
            result["capabilities"] = dict(response.capabilities)
        if response.ui_event is not None:
            result["ui_event"] = dict(response.ui_event or {})
        if response.object_ref:
            result["object_ref"] = response.object_ref
            result["ref"] = response.object_ref
        object_payload = dict(response.object or {})
        identity = object_payload.get("identity") if isinstance(object_payload.get("identity"), Mapping) else {}
        meta = object_payload.get("meta") if isinstance(object_payload.get("meta"), Mapping) else {}
        body = object_payload.get("body") if isinstance(object_payload.get("body"), Mapping) else {}
        derived = {
            "title": body.get("title") or body.get("filename") or object_payload.get("title"),
            "description": body.get("description") or object_payload.get("description"),
            "mime": meta.get("mime") or object_payload.get("mime"),
            "object_kind": identity.get("object_kind") or object_payload.get("object_kind"),
        }
        for key, value in derived.items():
            if value is not None:
                result[key] = value
        LOGGER.info(
            "Named-service canvas resolver call complete: namespace=%s provider=%s bundle=%s operation=%s action=%s object_ref=%s status=%s ui_event=%s",
            self.namespace,
            self.endpoint.provider or "",
            self.endpoint.bundle_id,
            operation,
            action,
            object_ref,
            response.status,
            bool(response.ui_event),
        )
        return result


def register_configured_named_service_canvas_resolvers(
    registry: Any,
    *,
    namespaces: Mapping[str, Any] | None,
    tenant: str = "",
    project: str = "",
    logger: logging.Logger | None = None,
) -> int:
    """Register configured named-service resolvers into a canvas registry.

    Composition bundles use this helper when their canvas/chat surface should
    resolve object refs owned by another bundle, for example `task:` refs owned
    by a task tracker bundle. The helper is SDK-level so every scene bundle can
    reuse the same config shape.
    """

    log = logger or logging.getLogger(__name__)
    if namespaces is None:
        return 0
        if not isinstance(namespaces, Mapping):
            log.warning(
                "[canvas.named_service_resolver] named service resolver config must be an object; got %s",
                type(namespaces).__name__,
            )
            return 0

    registered = 0
    for raw_namespace, raw_config in namespaces.items():
        namespace = str(raw_namespace or "").strip().lower().rstrip(":")
        if not namespace:
            continue
        if not isinstance(raw_config, Mapping):
            log.warning(
                "[canvas.named_service_resolver] named service resolver config for namespace=%s must be an object",
                namespace,
            )
            continue
        resolver_cfg = named_service_namespace_client_resolver_config(
            {"named_services": {"namespaces": {namespace: raw_config}}},
            namespace=namespace,
            client_id="canvas",
        )
        if resolver_cfg.get("enabled") is not True:
            log.info(
                "[canvas.named_service_resolver] skipping namespace=%s because canvas resolver is not enabled",
                namespace,
            )
            continue
        provider_configs = named_service_namespace_provider_configs_from_config(raw_config)
        endpoint = (
            NamedServiceEndpoint.from_provider_configs(provider_configs, namespace=namespace, tenant=tenant, project=project)
            if provider_configs
            else NamedServiceEndpoint(namespace=namespace, tenant=tenant, project=project)
        )

        registry.register(
            NamedServiceCanvasObjectResolver(
                namespace=namespace,
                endpoint=endpoint,
                resolver=str(raw_config.get("resolver") or "").strip() or None,
                allowed_operations=resolver_cfg.get("allowed_operations"),
            )
        )
        log.info(
            "[canvas.named_service_resolver] registered named service resolver namespace=%s provider=%s bundle=%s operations=%s",
            namespace,
            endpoint.provider or "<discovery>",
            endpoint.bundle_id or endpoint.module or "<discovery>",
            ",".join(str(item) for item in resolver_cfg.get("allowed_operations") or ()) or "<default>",
        )
        registered += 1
    return registered


__all__ = [
    "NamedServiceCanvasObjectResolver",
    "register_configured_named_service_canvas_resolvers",
]
