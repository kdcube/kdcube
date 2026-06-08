from __future__ import annotations

import base64
import inspect
import json
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable, Dict, Mapping, Sequence

from ..storage import CanvasStore


TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = {
    "application/json",
    "application/yaml",
    "application/x-yaml",
    "application/vnd.kdcube.canvas+json",
}

CanvasResolverHandler = Callable[
    [Mapping[str, Any], str, str, str],
    Mapping[str, Any] | Awaitable[Mapping[str, Any]],
]


def namespace_for_ref(ref: str) -> str:
    value = str(ref or "").strip()
    if not value or ":" not in value:
        return ""
    return value.split(":", 1)[0].strip().lower()


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
    semantics of the underlying object namespace (`task:`, `fi:`, `mem:`, etc.).
    """

    namespace = ""
    resolver = "unknown"
    resolver_status = "unknown"

    def capabilities_for_ref(self, ref: str) -> Dict[str, bool]:
        return {"preview": False, "open": False, "download": False, "rehost": False}

    def base_response(self, *, ref: str, action: str) -> Dict[str, Any]:
        namespace = namespace_for_ref(ref)
        return {
            "ok": True,
            "action": action,
            "ref": ref,
            "object_ref": ref,
            "namespace": namespace,
            "resolver": self.resolver,
            "resolver_status": self.resolver_status,
            "capabilities": self.capabilities_for_ref(ref),
        }

    async def object_action(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: str,
        story_id: str,
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
        story_id: str,
        action: str,
    ) -> Dict[str, Any]:
        result = self._handler(payload, user_id, story_id, action)
        if inspect.isawaitable(result):
            result = await result
        merged = {**self.base_response(ref=object_ref_from_payload(payload), action=action), **dict(result or {})}
        merged.setdefault("capabilities", self.capabilities_for_ref(str(merged.get("object_ref") or merged.get("ref") or "")))
        return merged


class BundleExtArtifactResolver(CanvasObjectResolver):
    """Resolver for canvas/bundle-owned `ext:` artifact refs."""

    namespace = "ext"
    resolver = "sdk.canvas.bundle_artifact_storage"
    resolver_status = "implemented"

    def __init__(self, store: CanvasStore) -> None:
        self.store = store
        self.resolver = str(getattr(store, "artifact_resolver_name", None) or self.resolver)

    def capabilities_for_ref(self, ref: str) -> Dict[str, bool]:
        return {"preview": True, "open": False, "download": True, "rehost": False}

    async def object_action(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: str,
        story_id: str,
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
            "error": "unsupported_ext_object_action",
            "status": 400,
        }

    def read_ref(self, ref: str, *, mime: str = "", max_text_chars: int = 20000) -> Dict[str, Any]:
        key = ref.split(":", 1)[1].strip()
        if not key:
            return {"ok": False, "error": "empty_ext_ref", "ref": ref, "namespace": "ext"}
        try:
            raw = self.store.artifacts.read(key)
        except Exception as exc:
            return {
                "ok": False,
                "resolved": False,
                "ref": ref,
                "object_ref": ref,
                "namespace": "ext",
                "key": key,
                "error": "ext_ref_not_found",
                "message": str(exc),
            }
        data = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        textual = _looks_textual(data, mime=mime)
        out: Dict[str, Any] = {
            **self.base_response(ref=ref, action="preview"),
            "resolved": True,
            "read_behavior": "bundle reads ext: bytes directly; ReAct can also react.pull ext: through the registered rehoster.",
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
        key = ref.split(":", 1)[1].strip()
        if not key:
            return {"ok": False, "error": "empty_ext_ref", "ref": ref, "namespace": "ext"}
        try:
            raw = self.store.artifacts.read(key)
        except Exception as exc:
            return {
                "ok": False,
                "resolved": False,
                "ref": ref,
                "object_ref": ref,
                "namespace": "ext",
                "key": key,
                "error": "ext_ref_not_found",
                "message": str(exc),
            }
        data = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        filename = PurePosixPath(key).name or "canvas-artifact"
        return {
            **self.base_response(ref=ref, action="download"),
            "resolved": True,
            "filename": filename,
            "mime": mime or "",
            "size": len(data),
            "content_base64": base64.b64encode(data).decode("ascii"),
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
        story_id: str,
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
        story_id: str,
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
        return await resolver.object_action(payload, user_id=user_id, story_id=story_id, action=action)


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
        NamespaceHandoffResolver(
            namespace="cnv",
            resolver="sdk.canvas_owned",
            resolver_status="reserved",
            read_behavior="Reserved for SDK canvas-owned objects. Current canvas-owned objects may also use ext: refs for compatibility.",
        ),
    ]
    task_resolver = str(resolver_names.get("task") or "").strip()
    if task_resolver:
        resolvers.append(NamespaceHandoffResolver(
            namespace="task",
            resolver=task_resolver,
            resolver_status="registered_elsewhere",
            capabilities={"preview": True, "open": True, "download": False, "rehost": False},
            read_behavior="Use the task resolver or task.read/task.patch for task objects; task-owned attachment refs are handled by task tools.",
        ))
    return resolvers


def build_default_canvas_resolver_registry(store: CanvasStore) -> CanvasObjectResolverRegistry:
    return CanvasObjectResolverRegistry([
        BundleExtArtifactResolver(store),
        *default_handoff_resolvers(resolver_names=getattr(store, "handoff_resolver_names", None)),
    ])


class CanvasPinResolver:
    """Compatibility wrapper for existing pin read/capability callers."""

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
        if isinstance(resolver, BundleExtArtifactResolver):
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
        if isinstance(resolver, BundleExtArtifactResolver):
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
    "BundleExtArtifactResolver",
    "CallableCanvasObjectResolver",
    "CanvasObjectResolver",
    "CanvasObjectResolverRegistry",
    "CanvasPinResolver",
    "NamespaceHandoffResolver",
    "build_default_canvas_resolver_registry",
    "namespace_for_ref",
    "object_ref_from_payload",
    "search_canvas_cards",
]
