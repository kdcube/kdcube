from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Annotated, Any, Dict

import semantic_kernel as sk

from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage
from kdcube_ai_app.infra.plugin.bundle_storage import storage_for_spec

try:
    from semantic_kernel.functions import kernel_function
except Exception:
    from semantic_kernel.utils.function_decorator import kernel_function

_TOOL_SUBSYSTEM: Any = None


def bind_integrations(integrations: Dict[str, Any]) -> None:
    global _TOOL_SUBSYSTEM
    _TOOL_SUBSYSTEM = integrations.get("tool_subsystem")


def _load_store_module():
    module_name = "_kdcube_versatile_preferences_store"
    if module_name in sys.modules:
        return sys.modules[module_name]

    bundle_root = Path(__file__).resolve().parent.parent
    module_path = bundle_root / "preferences_store.py"
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load preferences store: {module_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def _scope() -> Dict[str, Any]:
    if _TOOL_SUBSYSTEM is None:
        raise RuntimeError("preferences tools are not bound to the current tool subsystem")
    comm = _TOOL_SUBSYSTEM.comm
    spec = _TOOL_SUBSYSTEM.bundle_spec
    storage_root = storage_for_spec(
        spec=spec,
        tenant=getattr(comm, "tenant", None),
        project=getattr(comm, "project", None),
        ensure=True,
    )
    if storage_root is None:
        raise RuntimeError("bundle storage root is unavailable")
    return {
        "tenant": getattr(comm, "tenant", None),
        "project": getattr(comm, "project", None),
        "user_id": getattr(comm, "user_id", None) or "anonymous",
        "bundle_id": getattr(spec, "id", None) or "versatile",
        "storage_root": storage_root,
    }


class PreferenceTools:
    @kernel_function(
        name="get_preferences",
        description=(
            "Return stored user preferences for the current user. "
            "Use this before personalizing advice, suggestions, or follow-up actions."
        ),
    )
    async def get_preferences(
        self,
        recency: Annotated[int, "How many recent preference observations to return."] = 10,
        kwords: Annotated[str, "Optional comma-separated keywords used to filter preference matches."] = "",
    ) -> Dict[str, Any]:
        scope = _scope()
        store_mod = _load_store_module()
        view = store_mod.get_preferences_view(
            scope["storage_root"],
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
        return {
            "ok": True,
            "user_id": scope["user_id"],
            "current": current,
            "items": items,
            "keywords": view.get("keywords") or [],
            "matched_count": view.get("matched_count") or 0,
            "summary": "\n".join(summary_lines),
        }

    @kernel_function(
        name="set_preference",
        description="Store or update an explicit preference for the current user.",
    )
    async def set_preference(
        self,
        key: Annotated[str, "Preference key to update, for example preferred_name or output_style."],
        value: Annotated[str, "Preference value to store."],
        source: Annotated[str, "Short reason or source label for this preference change."] = "agent",
    ) -> Dict[str, Any]:
        scope = _scope()
        store_mod = _load_store_module()
        event = store_mod.append_preference_event(
            scope["storage_root"],
            scope["user_id"],
            key=str(key).strip(),
            value=str(value).strip(),
            source=str(source).strip() or "agent",
            origin="tool",
            evidence="explicit tool call",
        )
        return {
            "ok": True,
            "user_id": scope["user_id"],
            "event": event,
            "current": store_mod.load_current_preferences(scope["storage_root"], scope["user_id"]),
        }

    @kernel_function(
        name="export_preferences_snapshot",
        description=(
            "Write the current user's preference snapshot to the bundle storage backend "
            "(for example localfs or S3) and return the stored key."
        ),
    )
    async def export_preferences_snapshot(
        self,
        filename: Annotated[str, "Snapshot filename under preferences/exports/."] = "preferences-snapshot.json",
    ) -> Dict[str, Any]:
        scope = _scope()
        store_mod = _load_store_module()
        key_leaf = Path(filename or "preferences-snapshot.json").name
        snapshot = {
            "user_id": scope["user_id"],
            "current": store_mod.load_current_preferences(scope["storage_root"], scope["user_id"]),
            "items": store_mod.load_preference_events(scope["storage_root"], scope["user_id"]),
        }
        storage = AIBundleStorage(
            tenant=scope["tenant"],
            project=scope["project"],
            ai_bundle_id=scope["bundle_id"],
            storage_uri=None,
        )
        key = f"preferences/exports/{scope['user_id']}/{key_leaf}"
        storage.write(key, json.dumps(snapshot, indent=2), mime="application/json")
        return {
            "ok": True,
            "key": key,
            "root_uri": storage.root_uri,
            "current_count": len(snapshot["current"]),
            "event_count": len(snapshot["items"]),
        }


kernel = sk.Kernel()
tools = PreferenceTools()
kernel.add_plugin(tools, "preferences")
