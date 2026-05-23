from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Annotated, Any, Callable, Dict, Optional

import semantic_kernel as sk
from kdcube_ai_app.apps.chat.sdk.config import get_secret
from .. import preferences_store as store_mod

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

_TOOL_SUBSYSTEM: Any = None


def bind_integrations(integrations: Dict[str, Any]) -> None:
    global _TOOL_SUBSYSTEM
    _TOOL_SUBSYSTEM = integrations.get("tool_subsystem")


def _ok_ret_result(ret: Any) -> dict[str, Any]:
    return {"ok": True, "error": None, "ret": ret}


def _error_result(*, code: str, message: str, where: str, managed: bool, ret: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "where": where,
            "managed": managed,
        },
        "ret": ret,
    }


def _bundle_secret_path(scope: Dict[str, Any], *parts: str) -> str:
    suffix = ".".join(str(part).strip(".") for part in parts if str(part).strip("."))
    base = f"bundles.{scope['bundle_id']}.secrets"
    return f"{base}.{suffix}" if suffix else base


def _scope() -> Dict[str, Any]:
    if _TOOL_SUBSYSTEM is None:
        raise RuntimeError("preferences tools are not bound to the current tool subsystem")
    comm = _TOOL_SUBSYSTEM.comm
    spec = _TOOL_SUBSYSTEM.bundle_spec
    service = getattr(comm, "service", None)
    service_user = service.get("user") if isinstance(service, dict) else None
    effective_user_id = (
        getattr(comm, "user_id", None)
        or service_user
        or "anonymous"
    )
    storage = store_mod.build_preferences_storage(
        tenant=getattr(comm, "tenant", None) or "unknown",
        project=getattr(comm, "project", None) or "unknown",
        bundle_id=getattr(spec, "id", None) or "versatile",
    )
    return {
        "tenant": getattr(comm, "tenant", None),
        "project": getattr(comm, "project", None),
        "user_id": effective_user_id,
        "bundle_id": getattr(spec, "id", None) or "versatile",
        "storage": storage,
    }


def _with_user(scope: Dict[str, Any], *, user_id: str | None = None) -> Dict[str, Any]:
    resolved = dict(scope)
    if user_id is not None and str(user_id).strip():
        resolved["user_id"] = str(user_id).strip()
    return resolved


async def _get_preferences_result(
    scope: Dict[str, Any],
    *,
    recency: int = 10,
    kwords: str = "",
) -> Dict[str, Any]:
    view = store_mod.get_preferences_view(
        scope["storage"],
        scope["user_id"],
        recency=max(1, int(recency or 10)),
        kwords=kwords,
    )
    current = view.get("current") or {}
    items = view.get("items") or []
    summary_lines = []
    if current:
        summary_lines.append("Current preferences:")
        for key, value in sorted(current.items()):
            summary_lines.append(f"- {key}: {value.get('value')}")
    else:
        if view.get("has_any_preferences"):
            summary_lines.append("No stored preferences matched the provided keywords.")
        else:
            summary_lines.append("No stored preferences yet.")
    if items:
        summary_lines.append("")
        summary_lines.append("Recent observations:")
        for item in items:
            summary_lines.append(
                f"- {item.get('captured_at')}: {item.get('key')} = {item.get('value')}"
            )
    return _ok_ret_result({
        "user_id": scope["user_id"],
        "current": current,
        "items": items,
        "keywords": view.get("keywords") or [],
        "matched_count": view.get("matched_count") or 0,
        "summary": "\n".join(summary_lines),
    })


async def _capture_preferences_result(
    scope: Dict[str, Any],
    *,
    text: str,
    source: str = "agent_memory",
) -> Dict[str, Any]:
    raw_text = str(text or "").strip()
    if not raw_text:
        return _error_result(
            code="invalid_argument",
            message="capture_preferences requires non-empty text",
            where="preferences.capture_preferences",
            managed=True,
            ret={
                "user_id": scope["user_id"],
                "captured": [],
                "captured_count": 0,
                "summary": "No durable preference patterns were captured from the provided text.",
                "current": store_mod.load_current_preferences(scope["storage"], scope["user_id"]),
            },
        )

    captured = list(
        store_mod.auto_capture_preferences(
            scope["storage"],
            scope["user_id"],
            text=raw_text,
            source=str(source or "agent_memory").strip() or "agent_memory",
        )
    )

    if not captured:
        for clause in raw_text.replace("\n", ";").split(";"):
            clause = clause.strip()
            if not clause:
                continue
            if ":" in clause:
                key, value = clause.split(":", 1)
            elif "=" in clause:
                key, value = clause.split("=", 1)
            else:
                continue
            key = key.strip().lower().replace(" ", "_")
            value = value.strip()
            if not key or not value:
                continue
            captured.append(
                store_mod.append_preference_event(
                    scope["storage"],
                    scope["user_id"],
                    key=key,
                    value=value,
                    source=str(source or "agent_memory").strip() or "agent_memory",
                    origin="tool_capture",
                    evidence=raw_text,
                )
            )

    return _ok_ret_result({
        "user_id": scope["user_id"],
        "captured": captured,
        "captured_count": len(captured),
        "current": store_mod.load_current_preferences(scope["storage"], scope["user_id"]),
        "summary": (
            f"Captured {len(captured)} durable preference/item(s)."
            if captured
            else "No durable preference patterns were captured from the provided text."
        ),
    })


async def _set_preference_result(
    scope: Dict[str, Any],
    *,
    key: str,
    value: str,
    source: str = "agent",
) -> Dict[str, Any]:
    event = store_mod.append_preference_event(
        scope["storage"],
        scope["user_id"],
        key=str(key).strip(),
        value=str(value).strip(),
        source=str(source).strip() or "agent",
        origin="tool",
        evidence="explicit tool call",
    )
    return _ok_ret_result({
        "user_id": scope["user_id"],
        "event": event,
        "current": store_mod.load_current_preferences(scope["storage"], scope["user_id"]),
    })


async def _delete_preference_result(
    scope: Dict[str, Any],
    *,
    key: str,
    source: str = "agent",
) -> Dict[str, Any]:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return _error_result(
            code="invalid_argument",
            message="delete_preference requires a non-empty key",
            where="preferences.delete_preference",
            managed=True,
            ret={
                "user_id": scope["user_id"],
                "current": store_mod.load_current_preferences(scope["storage"], scope["user_id"]),
            },
        )
    event = store_mod.append_preference_event(
        scope["storage"],
        scope["user_id"],
        key=normalized_key,
        value=None,
        source=str(source).strip() or "agent",
        origin="tool_delete",
        evidence="explicit tool delete",
    )
    return _ok_ret_result({
        "user_id": scope["user_id"],
        "event": event,
        "current": store_mod.load_current_preferences(scope["storage"], scope["user_id"]),
    })


async def _export_preferences_snapshot_result(
    scope: Dict[str, Any],
    *,
    filename: str = "preferences-snapshot.json",
) -> Dict[str, Any]:
    key_leaf = Path(filename or "preferences-snapshot.json").name
    snapshot = dict(store_mod.get_preferences_snapshot(scope["storage"], scope["user_id"]))
    snapshot["user_id"] = scope["user_id"]
    snapshot["bundle_id"] = scope["bundle_id"]
    key = f"preferences/exports/{scope['user_id']}/{key_leaf}"
    snapshot_text = json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    scope["storage"].write(key, snapshot_text, mime="application/json")

    signature_secret_key = _bundle_secret_path(scope, "preferences", "snapshot_hmac_key")
    signature_value = await get_secret(signature_secret_key)
    signature_key = f"{key}.sig.json"
    signed = False
    if signature_value:
        signature = hmac.new(
            signature_value.encode("utf-8"),
            snapshot_text.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        scope["storage"].write(
            signature_key,
            json.dumps(
                {
                    "algorithm": "hmac-sha256",
                    "signed_key": key,
                    "secret_key": signature_secret_key,
                    "signature": signature,
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ) + "\n",
            mime="application/json",
        )
        signed = True
    else:
        signature_key = None

    return _ok_ret_result({
        "key": key,
        "root_uri": scope["storage"].root_uri,
        "current_count": len(snapshot["current"]),
        "event_count": len(snapshot["items"]),
        "signed": signed,
        "signature_algorithm": "hmac-sha256" if signed else None,
        "signature_key": signature_key,
        "signature_secret_key": signature_secret_key,
    })


def build_preferences_mcp_app(
    *,
    name: str,
    scope_provider: Callable[[str | None], Dict[str, Any]],
):
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as e:  # pragma: no cover - runtime dependency
        raise ImportError("mcp server SDK is not installed") from e

    mcp = FastMCP(name)

    @mcp.tool(
        name="get_preferences",
        description="Read one user's current preferences and recent observations from the versatile reference bundle storage.",
    )
    async def _mcp_get_preferences(
        user_id: Optional[str] = None,
        recency: int = 10,
        kwords: str = "",
    ) -> Dict[str, Any]:
        return await _get_preferences_result(
            scope_provider(user_id),
            recency=recency,
            kwords=kwords,
        )

    @mcp.tool(
        name="capture_preferences",
        description="Extract durable preferences from free-form text and store them for the selected user.",
    )
    async def _mcp_capture_preferences(
        text: str,
        user_id: Optional[str] = None,
        source: str = "mcp",
    ) -> Dict[str, Any]:
        return await _capture_preferences_result(
            scope_provider(user_id),
            text=text,
            source=source,
        )

    @mcp.tool(
        name="set_preference",
        description="Create or update one explicit user preference in the versatile reference bundle storage.",
    )
    async def _mcp_set_preference(
        key: str,
        value: str,
        user_id: Optional[str] = None,
        source: str = "mcp",
    ) -> Dict[str, Any]:
        return await _set_preference_result(
            scope_provider(user_id),
            key=key,
            value=value,
            source=source,
        )

    @mcp.tool(
        name="delete_preference",
        description="Delete one stored user preference by key in the versatile reference bundle storage.",
    )
    async def _mcp_delete_preference(
        key: str,
        user_id: Optional[str] = None,
        source: str = "mcp",
    ) -> Dict[str, Any]:
        return await _delete_preference_result(
            scope_provider(user_id),
            key=key,
            source=source,
        )

    @mcp.tool(
        name="export_preferences_snapshot",
        description="Export one user's preference snapshot from the versatile reference bundle storage.",
    )
    async def _mcp_export_preferences_snapshot(
        filename: str = "preferences-snapshot.json",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await _export_preferences_snapshot_result(
            scope_provider(user_id),
            filename=filename,
        )

    return mcp


class PreferenceTools:
    @kernel_function(
        name="get_preferences",
        description=(
            "Return stored user preferences, choices, interests, and profile facts for the current user. "
            "Use this early on preference-sensitive turns before personalizing advice, recommendations, formatting, "
            "follow-up actions, or repeated task behavior. Also use this first when the user asks what you remember "
            "about them or asks for remembered facts like city, location, timezone, preferred name, answer style, "
            "diet, dislikes, or interests. Returns an envelope: {ok, error, ret} where ret contains "
            "user_id, current, items, keywords, matched_count, and summary."
        ),
    )
    async def get_preferences(
        self,
        recency: Annotated[int, "How many recent preference observations to return."] = 10,
        kwords: Annotated[str, "Optional comma-separated keywords used to filter preference matches."] = "",
    ) -> Dict[str, Any]:
        return await _get_preferences_result(
            _scope(),
            recency=recency,
            kwords=kwords,
        )

    @kernel_function(
        name="capture_preferences",
        description=(
            "Capture one or more durable user preferences, choices, interests, constraints, or profile facts from "
            "a short natural-language note or quoted user text. Use this when the user reveals information that "
            "should influence future turns, especially profile facts like city, location, timezone, preferred name, "
            "tastes, constraints, answer style, or recurring choices. Returns an envelope: {ok, error, ret} "
            "where ret contains captured, captured_count, current, user_id, and summary."
        ),
    )
    async def capture_preferences(
        self,
        text: Annotated[
            str,
            "Short natural-language note, quoted user statement, or semicolon-separated key:value pairs to store.",
        ],
        source: Annotated[str, "Short source label for this capture, for example agent_memory or chat."] = "agent_memory",
    ) -> Dict[str, Any]:
        return await _capture_preferences_result(
            _scope(),
            text=text,
            source=source,
        )

    @kernel_function(
        name="set_preference",
        description=(
            "Store or update a single explicit durable preference or user fact for the current user. "
            "Use this for structured corrections or when you already know the exact key/value to save, especially "
            "when the user is correcting remembered facts or explicitly telling you what should persist. "
            "Returns an envelope: {ok, error, ret} where ret contains event, current, and user_id."
        ),
    )
    async def set_preference(
        self,
        key: Annotated[str, "Preference key to update, for example preferred_name or output_style."],
        value: Annotated[str, "Preference value to store."],
        source: Annotated[str, "Short reason or source label for this preference change."] = "agent",
    ) -> Dict[str, Any]:
        return await _set_preference_result(
            _scope(),
            key=key,
            value=value,
            source=source,
        )

    @kernel_function(
        name="delete_preference",
        description=(
            "Delete one explicit durable preference or user fact for the current user by key. "
            "Use this when remembered data must be removed or corrected rather than overwritten. "
            "Returns an envelope: {ok, error, ret} where ret contains event, current, and user_id."
        ),
    )
    async def delete_preference(
        self,
        key: Annotated[str, "Preference key to delete, for example preferred_name or output_style."],
        source: Annotated[str, "Short reason or source label for this preference deletion."] = "agent",
    ) -> Dict[str, Any]:
        return await _delete_preference_result(
            _scope(),
            key=key,
            source=source,
        )

    @kernel_function(
        name="export_preferences_snapshot",
        description=(
            "Write the current user's preference snapshot to the bundle storage backend "
            "(for example localfs or S3) and return the stored key. Returns an envelope: {ok, error, ret} "
            "where ret contains key, root_uri, current_count, event_count, and signature metadata. "
            "If bundle secret bundles.<bundle_id>.secrets.preferences.snapshot_hmac_key is configured, "
            "the exported snapshot is signed with HMAC-SHA256 and a .sig.json sidecar is written."
        ),
    )
    async def export_preferences_snapshot(
        self,
        filename: Annotated[str, "Snapshot filename under preferences/exports/."] = "preferences-snapshot.json",
    ) -> Dict[str, Any]:
        return await _export_preferences_snapshot_result(
            _scope(),
            filename=filename,
        )


kernel = sk.Kernel()
tools = PreferenceTools()
kernel.add_plugin(tools, "preferences")
