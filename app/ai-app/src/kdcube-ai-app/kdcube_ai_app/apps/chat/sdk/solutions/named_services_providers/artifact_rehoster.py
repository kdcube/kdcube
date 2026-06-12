# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import pathlib
import re
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.runtime.workspace import resolve_artifact_path
from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
    ARTIFACT_NAMESPACE_ATTACHMENTS,
    build_physical_artifact_path,
    physical_path_to_logical_path,
)

from .client_tools import named_service_namespace_provider_configs_from_config
from .transports.api_client import NamedServiceEndpoint, call_named_service_endpoint_stream
from .types import OBJECT_ACTION, NamedServiceRequest

LOGGER = logging.getLogger("kdcube.sdk.named_services.artifact_rehoster")


def _safe_filename(value: Any, *, default: str = "object.bin") -> str:
    name = pathlib.PurePosixPath(str(value or "").strip().strip("/")).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or default


def _safe_rel_segment(value: Any, *, default: str = "object") -> str:
    segment = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return segment or default


def _runtime_value(runtime: Any, name: str) -> str:
    return str(getattr(runtime, name, "") or "").strip()


class NamedServiceArtifactNamespaceRehoster:
    """ReAct artifact rehoster backed by a configured named-service namespace."""

    def __init__(
        self,
        *,
        namespace: str,
        provider_config: Mapping[str, Any],
        tenant: str = "",
        project: str = "",
    ) -> None:
        self.namespace = str(namespace or "").strip().lower().rstrip(":")
        self.provider_config = dict(provider_config or {})
        self.tenant = str(tenant or "").strip()
        self.project = str(project or "").strip()

    def _endpoint(self, runtime: Any) -> NamedServiceEndpoint | None:
        provider_config = dict(self.provider_config)
        provider_config.setdefault("tenant", self.tenant or _runtime_value(runtime, "tenant"))
        provider_config.setdefault("project", self.project or _runtime_value(runtime, "project"))
        provider_configs = named_service_namespace_provider_configs_from_config(provider_config)
        if provider_configs:
            return NamedServiceEndpoint.from_provider_configs(provider_configs, namespace=self.namespace)
        return NamedServiceEndpoint(namespace=self.namespace, tenant=provider_config.get("tenant"), project=provider_config.get("project"))

    async def __call__(
        self,
        *,
        ref: str,
        key: str,
        ctx_browser: Any,
        outdir: pathlib.Path,
        **context: Any,
    ) -> dict[str, Any]:
        runtime = getattr(ctx_browser, "runtime_ctx", None)
        turn_id = _runtime_value(runtime, "turn_id")
        endpoint = self._endpoint(runtime)
        if not turn_id:
            return {"missing": [{"source_ref": ref, "reason": "missing_turn_id"}]}

        LOGGER.info(
            "Named-service artifact rehost start: namespace=%s provider=%s bundle=%s source_ref=%s",
            self.namespace,
            endpoint.provider or "",
            endpoint.bundle_id,
            ref,
        )
        stream = await call_named_service_endpoint_stream(
            endpoint,
            NamedServiceRequest(
                operation=OBJECT_ACTION,
                provider=endpoint.provider,
                namespace=self.namespace,
                object_ref=str(ref or "").strip() or None,
                action="rehost",
                context={
                    "source": "react.pull",
                    "namespace": self.namespace,
                    "turn_id": turn_id,
                    "tool_id": context.get("tool_id"),
                    "tool_call_id": context.get("tool_call_id"),
                },
                payload={"key": str(key or "").strip()},
            ),
        )
        filename = _safe_filename(
            stream.filename
            or pathlib.PurePosixPath(str(key or "").strip()).name
            or pathlib.PurePosixPath(str(ref or "").strip()).name,
            default="object.bin",
        )
        mime = str(stream.media_type or "").strip()
        if not mime:
            guessed, _ = mimetypes.guess_type(filename)
            mime = guessed or "application/octet-stream"
        digest = hashlib.sha1(str(ref or "").encode("utf-8")).hexdigest()[:16]
        relpath = f"named_services/{_safe_rel_segment(self.namespace)}/{digest}/{filename}"
        physical_path = build_physical_artifact_path(
            turn_id=turn_id,
            namespace=ARTIFACT_NAMESPACE_ATTACHMENTS,
            relpath=relpath,
        )
        target = resolve_artifact_path(pathlib.Path(outdir), physical_path, prefer_existing=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        size_bytes = 0
        with target.open("wb") as fh:
            async for chunk in stream.chunks:
                payload = bytes(chunk or b"")
                if not payload:
                    continue
                size_bytes += len(payload)
                await asyncio.to_thread(fh.write, payload)
        logical_path = physical_path_to_logical_path(physical_path)
        LOGGER.info(
            "Named-service artifact rehost complete: namespace=%s provider=%s bundle=%s source_ref=%s logical_path=%s bytes=%s",
            self.namespace,
            endpoint.provider or "",
            endpoint.bundle_id,
            ref,
            logical_path,
            size_bytes,
        )
        return {
            "materialized": [{
                "source_ref": ref,
                "logical_path": logical_path,
                "physical_path": physical_path,
                "namespace": ARTIFACT_NAMESPACE_ATTACHMENTS,
                "artifact_kind": "attachment",
                "mime": mime,
                "size_bytes": size_bytes,
                "file_count": 1,
            }]
        }


def register_configured_named_service_artifact_rehosters(
    event_sources: Any,
    *,
    namespaces: Mapping[str, Any] | None,
    tenant: str = "",
    project: str = "",
    logger: logging.Logger | None = None,
) -> int:
    """Register `react.pull` rehosters for configured named-service namespaces."""

    log = logger or LOGGER
    if namespaces is None:
        return 0
    if not isinstance(namespaces, Mapping):
        log.warning(
            "[react.pull] named_services.namespaces must be an object; got %s",
            type(namespaces).__name__,
        )
        return 0
    register = getattr(event_sources, "register_namespace_rehoster", None)
    if not callable(register):
        log.warning("[react.pull] event_sources does not support dynamic namespace rehoster registration")
        return 0

    registered = 0
    for raw_namespace, raw_config in namespaces.items():
        namespace = str(raw_namespace or "").strip().lower().rstrip(":")
        if not namespace:
            continue
        if not isinstance(raw_config, Mapping):
            log.warning("[react.pull] named service namespace=%s config must be an object", namespace)
            continue
        provider_configs = named_service_namespace_provider_configs_from_config(raw_config)
        endpoint = (
            NamedServiceEndpoint.from_provider_configs(provider_configs, namespace=namespace, tenant=tenant, project=project)
            if provider_configs
            else NamedServiceEndpoint(namespace=namespace, tenant=tenant, project=project)
        )
        register(
            namespace,
            NamedServiceArtifactNamespaceRehoster(
                namespace=namespace,
                provider_config={"providers": list(provider_configs)},
                tenant=tenant,
                project=project,
            ),
            description=f"Named-service object rehoster for namespace {namespace!r}.",
            module=__name__,
            object_name=f"named_service_{namespace}_artifact_rehoster",
        )
        log.info(
            "[react.pull] registered named service artifact rehoster namespace=%s provider=%s bundle=%s",
            namespace,
            endpoint.provider or "<discovery>",
            endpoint.bundle_id or endpoint.module or "<discovery>",
        )
        registered += 1
    return registered


__all__ = [
    "NamedServiceArtifactNamespaceRehoster",
    "register_configured_named_service_artifact_rehosters",
]
