from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Dict

import semantic_kernel as sk
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
        scope = _scope()
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
        scope = _scope()
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
        scope = _scope()
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

    @kernel_function(
        name="export_preferences_snapshot",
        description=(
            "Write the current user's preference snapshot to the bundle storage backend "
            "(for example localfs or S3) and return the stored key. Returns an envelope: {ok, error, ret} "
            "where ret contains key, root_uri, current_count, and event_count."
        ),
    )
    async def export_preferences_snapshot(
        self,
        filename: Annotated[str, "Snapshot filename under preferences/exports/."] = "preferences-snapshot.json",
    ) -> Dict[str, Any]:
        scope = _scope()
        key_leaf = Path(filename or "preferences-snapshot.json").name
        snapshot = dict(store_mod.get_preferences_snapshot(scope["storage"], scope["user_id"]))
        snapshot["user_id"] = scope["user_id"]
        key = f"preferences/exports/{scope['user_id']}/{key_leaf}"
        scope["storage"].write(key, json.dumps(snapshot, indent=2), mime="application/json")
        return _ok_ret_result({
            "key": key,
            "root_uri": scope["storage"].root_uri,
            "current_count": len(snapshot["current"]),
            "event_count": len(snapshot["items"]),
        })


kernel = sk.Kernel()
tools = PreferenceTools()
kernel.add_plugin(tools, "preferences")
