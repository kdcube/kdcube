from __future__ import annotations

import base64
import hashlib
import inspect
import json
import mimetypes
import pathlib
import re
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable, Dict, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.events import (
    artifact_namespace_rehoster,
    event_source_declaration,
    event_source_reader,
)
from kdcube_ai_app.apps.chat.sdk.infra.bundle_urls import bundle_operation_url
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.config import canvas_config_from_props
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.tools_core import DEFAULT_CANVAS_TOOL_EVENT_SOURCE_DESCRIPTIONS
from kdcube_ai_app.apps.chat.sdk.solutions.canvas.tools_core import read_canvas_for_agent
from kdcube_ai_app.apps.chat.sdk.solutions.react.events import default_tool_event_policies

from ..storage import CanvasStore
from .policies import (  # noqa: F401 - imported so event discovery sees canvas read policies
    canvas_announce_policy,
    canvas_read_block_policy,
    canvas_tool_projection_policy,
)


TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = {
    "application/json",
    "application/yaml",
    "application/x-yaml",
    "application/vnd.kdcube.canvas+json",
}

CanvasResolverHandler = Callable[
    [Mapping[str, Any], str, str],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]


def _runtime_value(runtime: Any, name: str, default: Any = "") -> Any:
    return getattr(runtime, name, default) if runtime is not None else default


def _store_from_runtime(runtime: Any) -> CanvasStore:
    cfg = canvas_config_from_props(getattr(runtime, "bundle_props", None))
    return CanvasStore(
        tenant=str(_runtime_value(runtime, "tenant", "") or ""),
        project=str(_runtime_value(runtime, "project", "") or ""),
        bundle_id=str(cfg.get("bundle_id") or _runtime_value(runtime, "bundle_id", "") or ""),
        user_id=str(_runtime_value(runtime, "user_id", "") or "anonymous"),
        storage_root=str(_runtime_value(runtime, "bundle_storage", "") or "."),
        artifact_prefix=str(cfg.get("artifact_prefix") or "canvas"),
        origin_prefix=str(cfg.get("origin_prefix") or "canvas"),
        state_event_source_id=str(cfg.get("state_event_source_id") or "canvas.state"),
        ui_event_type=str(cfg.get("ui_event_type") or "canvas.patch.applied"),
        artifact_resolver_name=str(cfg.get("artifact_resolver_name") or "sdk.canvas.artifact_storage"),
        handoff_resolver_names=dict(cfg.get("handoff_resolver_names") or {}),
        revision_retention=int(cfg.get("revision_retention") or 80),
    )


def _safe_rehost_segment(value: str, *, default: str = "canvas") -> str:
    raw = re.sub(r"[^A-Za-z0-9_.@-]+", "_", str(value or "").strip()).strip("._-")
    return raw[:120] or default


def _looks_like_canvas_storage_key(key: str) -> bool:
    raw = str(key or "").strip().lstrip("/")
    return "/" in raw and ("objects/" in raw or "canvases/" in raw or raw.endswith((".md", ".txt", ".json", ".pdf", ".png", ".jpg", ".jpeg", ".docx", ".xlsx", ".pptx")))


def _canvas_read_policies() -> list[dict[str, Any]]:
    policies = list(default_tool_event_policies())
    policies.append({
        "react_phase": "block_production",
        "event_policy_id": "canvas.block_production.read_result",
    })
    policies.append({
        "react_phase": "timeline_projection",
        "event_policy_id": "canvas.timeline_projection.tool_result",
    })
    policies.append({
        "react_phase": "compaction_projection",
        "event_policy_id": "canvas.compaction_projection.tool_result",
    })
    policies.append({
        "react_phase": "announce_production",
        "event_policy_id": "canvas.announce.board_map",
    })
    return policies


def list_event_sources() -> list[Any]:
    return [
        event_source_declaration(
            event_source_id="{alias}.read",
            policies=_canvas_read_policies(),
            description=DEFAULT_CANVAS_TOOL_EVENT_SOURCE_DESCRIPTIONS["read"],
            kind="react.event_source_reader",
        )
    ]


@artifact_namespace_rehoster(
    namespace="cnv",
    description="Materialize a cnv: canvas board or canvas-owned object ref into the current ReAct artifact workspace.",
)
async def rehost_canvas_ref(
    *,
    ref: str,
    namespace: str = "cnv",
    key: str = "",
    ctx_browser: Any = None,
    outdir: pathlib.Path | None = None,
    **_context: Any,
) -> Dict[str, Any]:
    uri = str(ref or (f"{namespace}:{key}" if key else "")).strip()
    runtime = getattr(ctx_browser, "runtime_ctx", None)
    turn_id = str(_runtime_value(runtime, "turn_id", "") or "").strip()
    if not uri or not turn_id or outdir is None:
        return {"missing": [{"object_ref": uri, "reason": "missing_ref_or_runtime"}]}

    from kdcube_ai_app.apps.chat.sdk.runtime.workspace import resolve_artifact_path
    from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
        ARTIFACT_NAMESPACE_ATTACHMENTS,
        ARTIFACT_NAMESPACE_SNAPSHOTS,
        build_physical_artifact_path,
        physical_path_to_logical_path,
    )

    try:
        store = _store_from_runtime(runtime)
    except Exception as exc:
        return {"missing": [{"object_ref": uri, "reason": f"canvas_runtime_scope_unavailable:{exc}"}]}

    storage_key = str(key or (uri.split(":", 1)[1] if ":" in uri else "")).strip().lstrip("/")
    if _looks_like_canvas_storage_key(storage_key):
        mime, _ = mimetypes.guess_type(pathlib.PurePosixPath(storage_key).name)
        mime = mime or "application/octet-stream"
        try:
            payload, _meta = CanvasArtifactResolver(store).download_bytes(f"{namespace}:{storage_key}", mime=mime)
        except Exception as exc:
            return {"missing": [{"object_ref": uri, "reason": f"canvas_object_not_found:{exc}"}]}
        relpath = f"cnv/{storage_key}"
        namespace_name = ARTIFACT_NAMESPACE_ATTACHMENTS
    else:
        try:
            result = read_canvas_for_agent(store=store, uri=uri)
        except Exception as exc:
            return {"missing": [{"object_ref": uri, "reason": f"canvas_read_failed:{exc}"}]}
        if not result.get("ok"):
            return {"missing": [{"object_ref": uri, "reason": str(result.get("error") or "canvas_read_failed")}]}
        payload = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
        mime = "application/json"
        digest = hashlib.sha1(uri.encode("utf-8")).hexdigest()[:12]
        relpath = f"cnv/{_safe_rehost_segment(storage_key or uri.split(':', 1)[-1])}-{digest}.json"
        namespace_name = ARTIFACT_NAMESPACE_SNAPSHOTS

    physical_path = build_physical_artifact_path(
        turn_id=turn_id,
        namespace=namespace_name,
        relpath=relpath,
    )
    logical_path = physical_path_to_logical_path(physical_path)
    target = resolve_artifact_path(pathlib.Path(outdir), physical_path, prefer_existing=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return {
        "materialized": [{
            "object_ref": uri,
            "logical_path": logical_path,
            "physical_path": physical_path,
            "scope": namespace_name,
            "mime": mime,
            "size_bytes": len(payload),
            "file_count": 1,
        }],
    }


@event_source_reader(
    namespace="cnv",
    event_source_id="{alias}.read",
    description="Resolve a cnv:<name>@<revision> board ref into the canvas.read event-source payload.",
)
async def read_canvas_event_ref(
    *,
    ref: str,
    namespace: str = "cnv",
    key: str = "",
    ctx_browser: Any = None,
    **_context: Any,
) -> Dict[str, Any]:
    uri = ref or (f"{namespace}:{key}" if key else "")
    runtime = getattr(ctx_browser, "runtime_ctx", None)
    try:
        result = read_canvas_for_agent(
            store=_store_from_runtime(runtime),
            uri=uri,
        )
        if not result.get("ok"):
            result = {"ok": False, "error": result.get("error") or "canvas_read_failed", **result}
        return result
    except Exception as exc:
        return {"ok": False, "ref": uri, "object_ref": uri, "error": "canvas_read_failed", "message": str(exc)}


def namespace_for_ref(ref: str) -> str:
    value = str(ref or "").strip()
    if not value or ":" not in value:
        return ""
    return value.split(":", 1)[0].strip().lower()


def _bundle_operation_url(store: CanvasStore, operation: str, query: Mapping[str, Any]) -> str:
    return bundle_operation_url(
        tenant=getattr(store, "tenant", ""),
        project=getattr(store, "project", ""),
        bundle_id=getattr(store, "bundle_id", ""),
        operation=operation,
        query=query,
    )


def _looks_textual(raw: bytes, *, mime: str = "") -> bool:
    lowered = str(mime or "").split(";", 1)[0].strip().lower()
    if lowered.startswith(TEXT_MIME_PREFIXES) or lowered in TEXT_MIME_TYPES or lowered.endswith("+json"):
        return True
    if not raw:
        return True
    try:
        raw[:4096].decode("utf-8")
        return True
    except Exception:
        return False


class CanvasObjectResolver:
    """Namespace-owned object resolver used by the canvas registry.

    Canvas owns board state and dispatch. The resolver implementation owns the
    semantics of the underlying object namespace (`fi:`, `mem:`, provider-owned
    refs, etc.).
    See repo:kdcube-ai-app/app/ai-app/docs/sdk/bundle/bundle-subsystem-integration-README.md
    and repo:kdcube-ai-app/app/ai-app/docs/sdk/solutions/canvas/pin-integration-README.md.
    """

    namespace = ""
    resolver = "unknown"
    resolver_status = "unknown"

    def capabilities_for_ref(self, ref: str) -> Dict[str, bool]:
        return {"preview": False, "open": False, "download": False, "rehost": False}

    def default_open_effect_action_for_ref(self, ref: str) -> str:
        """Action to run when a UI surface opens/clicks this object handle.

        Namespace owners define this per concrete ref/object kind. A host
        component must not infer that `open` or `download` is the right effect
        from the namespace alone.
        """
        del ref
        return ""

    def base_response(self, *, ref: str, action: str) -> Dict[str, Any]:
        namespace = namespace_for_ref(ref)
        response: Dict[str, Any] = {
            "ok": True,
            "action": action,
            "ref": ref,
            "object_ref": ref,
            "namespace": namespace,
            "resolver": self.resolver,
            "resolver_status": self.resolver_status,
            "capabilities": self.capabilities_for_ref(ref),
        }
        default_open_effect_action = self.default_open_effect_action_for_ref(ref)
        if default_open_effect_action:
            response["default_open_effect_action"] = default_open_effect_action
        return response

    async def object_action(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: str,
        action: str,
    ) -> Dict[str, Any]:
        ref = object_ref_from_payload(payload)
        if action in {"capabilities", "describe"}:
            return self.base_response(ref=ref, action=action)
        return {
            **self.base_response(ref=ref, action=action),
            "ok": False,
            "error": "resolver_action_not_implemented",
            "status": 400,
        }


class CallableCanvasObjectResolver(CanvasObjectResolver):
    """Adapter for namespace resolvers owned outside the canvas package."""

    def __init__(
        self,
        *,
        namespace: str,
        resolver: str,
        resolver_status: str,
        capabilities: Mapping[str, bool],
        handler: CanvasResolverHandler,
    ) -> None:
        self.namespace = str(namespace or "").strip().lower()
        self.resolver = str(resolver or self.namespace or "external")
        self.resolver_status = str(resolver_status or "registered")
        self._capabilities = {str(key): bool(value) for key, value in dict(capabilities or {}).items()}
        self._handler = handler

    def capabilities_for_ref(self, ref: str) -> Dict[str, bool]:
        return {
            "preview": bool(self._capabilities.get("preview")),
            "open": bool(self._capabilities.get("open")),
            "download": bool(self._capabilities.get("download")),
            "rehost": bool(self._capabilities.get("rehost")),
        }

    async def object_action(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: str,
        action: str,
    ) -> Dict[str, Any]:
        result = self._handler(payload, user_id, action)
        if inspect.isawaitable(result):
            result = await result
        merged = {**self.base_response(ref=object_ref_from_payload(payload), action=action), **dict(result or {})}
        merged.setdefault("capabilities", self.capabilities_for_ref(str(merged.get("object_ref") or merged.get("ref") or "")))
        return merged


class CanvasArtifactResolver(CanvasObjectResolver):
    """Resolver for storage-backed artifact refs."""

    namespace = "cnv"
    resolver = "sdk.canvas.artifact_storage"
    resolver_status = "implemented"

    def __init__(
        self,
        store: CanvasStore,
        *,
        namespace: str = "cnv",
        resolver: str = "",
        read_behavior: str = "",
    ) -> None:
        self.store = store
        self.namespace = str(namespace or self.namespace).strip().lower()
        self.resolver = str(resolver or getattr(store, "artifact_resolver_name", None) or self.resolver)
        self._read_behavior = str(read_behavior or "Storage-backed refs are previewed here and can be imported into ReAct with react.pull.").strip()

    def capabilities_for_ref(self, ref: str) -> Dict[str, bool]:
        return {"preview": True, "open": False, "download": True, "rehost": False}

    def default_open_effect_action_for_ref(self, ref: str) -> str:
        del ref
        return "download"

    async def object_action(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: str,
        action: str,
    ) -> Dict[str, Any]:
        ref = object_ref_from_payload(payload)
        mime = str(payload.get("mime") or "").strip()
        if action in {"capabilities", "describe"}:
            return self.base_response(ref=ref, action=action)
        if action == "preview":
            return self.read_ref(ref, mime=mime, max_text_chars=4000)
        if action == "download":
            return self.download_ref(ref, mime=mime)
        return {
            **self.base_response(ref=ref, action=action),
            "ok": False,
            "error": "unsupported_canvas_object_action",
            "status": 400,
        }

    def read_ref(self, ref: str, *, mime: str = "", max_text_chars: int = 20000) -> Dict[str, Any]:
        key = ref.split(":", 1)[1].strip()
        if not key:
            return {"ok": False, "error": f"empty_{self.namespace}_ref", "ref": ref, "namespace": self.namespace}
        try:
            raw = self.store.artifacts.read(key)
        except Exception as exc:
            return {
                "ok": False,
                "resolved": False,
                "ref": ref,
                "object_ref": ref,
                "namespace": self.namespace,
                "key": key,
                "error": f"{self.namespace}_ref_not_found",
                "message": str(exc),
            }
        data = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        textual = _looks_textual(data, mime=mime)
        out: Dict[str, Any] = {
            **self.base_response(ref=ref, action="preview"),
            "resolved": True,
            "read_behavior": self._read_behavior,
            "key": key,
            "size": len(data),
            "mime": mime or "",
        }
        if textual:
            text = data.decode("utf-8", errors="replace")
            out["text"] = text[:max_text_chars]
            out["truncated"] = len(text) > max_text_chars
            if key.endswith(".json") or str(mime or "").endswith("+json") or str(mime or "").split(";", 1)[0] == "application/json":
                try:
                    out["json"] = json.loads(text)
                except Exception:
                    pass
        else:
            out["base64"] = base64.b64encode(data).decode("ascii")
            out["encoding"] = "base64"
        return out

    def download_ref(self, ref: str, *, mime: str = "") -> Dict[str, Any]:
        try:
            data, meta = self.download_bytes(ref, mime=mime)
        except Exception as exc:
            return {
                "ok": False,
                "resolved": False,
                "ref": ref,
                "object_ref": ref,
                "namespace": self.namespace,
                "key": ref.split(":", 1)[1].strip() if ":" in ref else "",
                "error": f"{self.namespace}_ref_not_found",
                "message": str(exc),
            }
        result = {
            **self.base_response(ref=ref, action="download"),
            **meta,
            "resolved": True,
            "size": len(data),
        }
        download_url = _bundle_operation_url(
            self.store,
            "canvas_object_download",
            {
                "object_ref": ref,
                "mime": meta.get("mime") or mime,
                "filename": meta.get("filename") or "",
            },
        )
        if download_url:
            result["download_url"] = download_url
        return result

    def download_bytes(self, ref: str, *, mime: str = "") -> tuple[bytes, Dict[str, Any]]:
        key = ref.split(":", 1)[1].strip()
        if not key:
            raise ValueError(f"empty_{self.namespace}_ref")
        try:
            raw = self.store.artifacts.read(key)
        except Exception as exc:
            raise FileNotFoundError(str(exc)) from exc
        data = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        filename = PurePosixPath(key).name or "canvas-artifact"
        return data, {
            "key": key,
            "ref": ref,
            "object_ref": ref,
            "namespace": self.namespace,
            "filename": filename,
            "mime": mime or "",
        }


class NamespaceHandoffResolver(CanvasObjectResolver):
    """Known namespace with no local resolver implementation in this bundle."""

    def __init__(
        self,
        *,
        namespace: str,
        resolver: str,
        resolver_status: str,
        capabilities: Mapping[str, bool] | None = None,
        read_behavior: str = "",
    ) -> None:
        self.namespace = str(namespace or "").strip().lower()
        self.resolver = str(resolver or self.namespace or "unknown")
        self.resolver_status = str(resolver_status or "handoff")
        self._capabilities = {
            "preview": False,
            "open": False,
            "download": False,
            "rehost": False,
            **{str(key): bool(value) for key, value in dict(capabilities or {}).items()},
        }
        self._read_behavior = str(read_behavior or "").strip()

    def capabilities_for_ref(self, ref: str) -> Dict[str, bool]:
        return dict(self._capabilities)

    async def object_action(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: str,
        action: str,
    ) -> Dict[str, Any]:
        ref = object_ref_from_payload(payload)
        base = self.base_response(ref=ref, action=action)
        if action in {"capabilities", "describe"}:
            if self._read_behavior:
                base["read_behavior"] = self._read_behavior
            return base
        return {
            **base,
            "ok": False,
            "resolved": False,
            "read_behavior": self._read_behavior,
            "error": "namespace_resolver_not_registered_here",
            "message": f"{self.namespace}: is owned by {self.resolver}; register that subsystem resolver to enable {action}.",
            "status": 404,
        }

    def read_ref(self, ref: str) -> Dict[str, Any]:
        return {
            **self.base_response(ref=ref, action="preview"),
            "resolved": False,
            "read_behavior": self._read_behavior,
            "message": f"{self.namespace}: refs are resolved by the named resolver, not canvas bundle artifact storage.",
        }


class CanvasObjectResolverRegistry:
    """Small dispatch registry; namespace behavior stays in each owner system."""

    def __init__(self, resolvers: Sequence[CanvasObjectResolver] | None = None) -> None:
        self._resolvers: Dict[str, CanvasObjectResolver] = {}
        for resolver in resolvers or ():
            self.register(resolver)

    def register(self, resolver: CanvasObjectResolver) -> None:
        namespace = str(getattr(resolver, "namespace", "") or "").strip().lower()
        if not namespace:
            raise ValueError("canvas object resolver namespace is required")
        self._resolvers[namespace] = resolver

    def resolver_for_ref(self, ref: str) -> CanvasObjectResolver | None:
        return self._resolvers.get(namespace_for_ref(ref))

    def capabilities_for_ref(self, ref: str) -> Dict[str, Any]:
        raw_ref = str(ref or "").strip()
        namespace = namespace_for_ref(raw_ref)
        if not raw_ref:
            return {"ok": False, "error": "ref_required"}
        resolver = self.resolver_for_ref(raw_ref)
        if resolver is None:
            return {
                "ok": True,
                "ref": raw_ref,
                "object_ref": raw_ref,
                "namespace": namespace,
                "resolver": "unknown",
                "resolver_status": "not_registered",
                "capabilities": {"preview": False, "open": False, "download": False, "rehost": False},
            }
        return resolver.base_response(ref=raw_ref, action="capabilities")

    async def object_action(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: str,
    ) -> Dict[str, Any]:
        ref = object_ref_from_payload(payload)
        action = str(payload.get("action") or "capabilities").strip().lower()
        if not ref:
            return {"ok": False, "action": action, "error": "ref_required", "status": 400}
        resolver = self.resolver_for_ref(ref)
        if resolver is None:
            return {
                "ok": False,
                "action": action,
                "ref": ref,
                "object_ref": ref,
                "namespace": namespace_for_ref(ref),
                "resolver": "unknown",
                "resolver_status": "not_registered",
                "capabilities": {"preview": False, "open": False, "download": False, "rehost": False},
                "error": "canvas_object_resolver_not_registered",
                "status": 404,
            }
        return await resolver.object_action(payload, user_id=user_id, action=action)


def object_ref_from_payload(payload: Mapping[str, Any]) -> str:
    return str(payload.get("object_ref") or payload.get("ref") or payload.get("logical_path") or "").strip()


def default_handoff_resolvers(*, resolver_names: Mapping[str, str] | None = None) -> list[CanvasObjectResolver]:
    resolver_names = dict(resolver_names or {})
    resolvers: list[CanvasObjectResolver] = [
        NamespaceHandoffResolver(
            namespace="fi",
            resolver="react.event_ref",
            resolver_status="registered_elsewhere",
            read_behavior="Use the React event/artifact resolver for fi: refs; durable canvas refs must include conv_<conversation_id>.",
        ),
        NamespaceHandoffResolver(
            namespace="mem",
            resolver="sdk.memory",
            resolver_status="registered_elsewhere",
            read_behavior="Use the memory module resolver/tools; mem: is not a filesystem artifact.",
        ),
        NamespaceHandoffResolver(
            namespace="so",
            resolver="platform.sources_pool",
            resolver_status="registered_elsewhere",
            read_behavior="Use the source/search resolver registered by the owning subsystem.",
        ),
        NamespaceHandoffResolver(
            namespace="su",
            resolver="subsystem.search",
            resolver_status="reserved",
            read_behavior="Reserved for subsystem-specific search rows when so: is not enough.",
        ),
        NamespaceHandoffResolver(
            namespace="ev",
            resolver="platform.timeline_event",
            resolver_status="provenance_handoff",
            read_behavior="Use only for provenance/event inspection, not as normal card content.",
        ),
    ]
    for namespace, resolver_name in sorted(resolver_names.items()):
        namespace_value = str(namespace or "").strip().lower()
        resolver_value = str(resolver_name or "").strip()
        if not namespace_value or namespace_value in {"fi", "mem", "so", "su", "ev", "cnv"}:
            continue
        resolvers.append(NamespaceHandoffResolver(
            namespace=namespace_value,
            resolver=resolver_value or f"{namespace_value}.resolver",
            resolver_status="registered_elsewhere",
            capabilities={"preview": True, "open": True, "download": False, "rehost": False},
            read_behavior=(
                f"{namespace_value}: refs are owned by {resolver_value or 'the configured namespace resolver'}; "
                "use that provider's resolver/tools for object reads, opens, and mutations."
            ),
        ))
    return resolvers


def build_default_canvas_resolver_registry(store: CanvasStore) -> CanvasObjectResolverRegistry:
    return CanvasObjectResolverRegistry([
        CanvasArtifactResolver(store),
        CanvasArtifactResolver(
            store,
            namespace="ext",
            read_behavior="Bundle-owned external refs are previewed here and can be imported into ReAct with react.pull ext:<path>.",
        ),
        *default_handoff_resolvers(resolver_names=getattr(store, "handoff_resolver_names", None)),
    ])


class CanvasPinResolver:
    """Facade for card action callers that need registry-backed resolution."""

    def __init__(self, store: CanvasStore) -> None:
        self.store = store
        self.registry = build_default_canvas_resolver_registry(store)

    def capabilities_for_ref(self, ref: str) -> Dict[str, Any]:
        return self.registry.capabilities_for_ref(ref)

    def read_ref(self, ref: str, *, mime: str = "", max_text_chars: int = 20000) -> Dict[str, Any]:
        raw_ref = str(ref or "").strip()
        namespace = namespace_for_ref(raw_ref)
        if not raw_ref:
            return {"ok": False, "error": "ref_required"}
        resolver = self.registry.resolver_for_ref(raw_ref)
        if isinstance(resolver, CanvasArtifactResolver):
            return resolver.read_ref(raw_ref, mime=mime, max_text_chars=max_text_chars)
        if isinstance(resolver, NamespaceHandoffResolver):
            return resolver.read_ref(raw_ref)
        return {
            "ok": False,
            "resolved": False,
            "ref": raw_ref,
            "object_ref": raw_ref,
            "namespace": namespace,
            "error": "unsupported_ref_namespace",
        }

    def download_ref(self, ref: str, *, mime: str = "") -> Dict[str, Any]:
        raw_ref = str(ref or "").strip()
        if not raw_ref:
            return {"ok": False, "error": "ref_required"}
        resolver = self.registry.resolver_for_ref(raw_ref)
        if isinstance(resolver, CanvasArtifactResolver):
            return resolver.download_ref(raw_ref, mime=mime)
        return {
            **self.registry.capabilities_for_ref(raw_ref),
            "ok": False,
            "error": "download_not_supported_by_this_resolver",
        }


def search_canvas_cards(
    canvas: Mapping[str, Any],
    *,
    query: str,
    namespaces: Sequence[str] | None = None,
    limit: int = 20,
) -> Dict[str, Any]:
    terms = [part.lower() for part in str(query or "").split() if part.strip()]
    allowed = {str(ns).strip().lower().rstrip(":") for ns in (namespaces or []) if str(ns).strip()}
    rows: list[Dict[str, Any]] = []
    for card in canvas.get("cards") or []:
        if not isinstance(card, Mapping):
            continue
        ref = str(card.get("logical_path") or card.get("storage_ref") or "").strip()
        namespace = namespace_for_ref(ref)
        if allowed and namespace not in allowed:
            continue
        haystack = " ".join(
            str(card.get(key) or "")
            for key in ("id", "kind", "title", "mime", "logical_path", "content_preview")
        ).lower()
        if terms and not all(term in haystack for term in terms):
            continue
        rows.append({
            "card_id": str(card.get("id") or ""),
            "kind": str(card.get("kind") or ""),
            "title": str(card.get("title") or ""),
            "mime": str(card.get("mime") or ""),
            "logical_path": ref,
            "namespace": namespace,
            "selected": bool(card.get("selected")),
            "placement": str(card.get("placement") or "floating"),
        })
        if len(rows) >= max(1, int(limit or 20)):
            break
    return {
        "ok": True,
        "query": query,
        "namespaces": sorted(allowed),
        "count": len(rows),
        "items": rows,
        "note": "This searches canvas card metadata. Namespace content search is handled by namespace-specific resolvers/indexes.",
    }


__all__ = [
    "CallableCanvasObjectResolver",
    "CanvasArtifactResolver",
    "CanvasObjectResolver",
    "CanvasObjectResolverRegistry",
    "CanvasPinResolver",
    "NamespaceHandoffResolver",
    "build_default_canvas_resolver_registry",
    "list_event_sources",
    "namespace_for_ref",
    "object_ref_from_payload",
    "read_canvas_event_ref",
    "rehost_canvas_ref",
    "search_canvas_cards",
]
