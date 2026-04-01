from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage


def _load_preferences_store_module():
    root = Path(__file__).resolve().parents[1]
    module_path = (
        root
        / "sdk"
        / "examples"
        / "bundles"
        / "versatile@2026-03-31-13-36"
        / "preferences_store.py"
    )
    spec = importlib.util.spec_from_file_location("versatile_preferences_store_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_preference_tools_module():
    root = Path(__file__).resolve().parents[1]
    bundle_root = (
        root
        / "sdk"
        / "examples"
        / "bundles"
        / "versatile@2026-03-31-13-36"
    )
    module_path = bundle_root / "tools" / "preference_tools.py"
    pkg_name = "_test_bundle_versatile"
    if pkg_name not in sys.modules:
        pkg_spec = importlib.machinery.ModuleSpec(pkg_name, loader=None, is_package=True)
        pkg_mod = importlib.util.module_from_spec(pkg_spec)
        pkg_mod.__path__ = [str(bundle_root)]  # type: ignore[attr-defined]
        pkg_mod.__package__ = pkg_name
        sys.modules[pkg_name] = pkg_mod
    tools_pkg_name = f"{pkg_name}.tools"
    if tools_pkg_name not in sys.modules:
        tools_pkg_spec = importlib.machinery.ModuleSpec(tools_pkg_name, loader=None, is_package=True)
        tools_pkg_mod = importlib.util.module_from_spec(tools_pkg_spec)
        tools_pkg_mod.__path__ = [str(bundle_root / 'tools')]  # type: ignore[attr-defined]
        tools_pkg_mod.__package__ = tools_pkg_name
        sys.modules[tools_pkg_name] = tools_pkg_mod
    spec = importlib.util.spec_from_file_location(f"{tools_pkg_name}.preference_tools", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    module.__package__ = tools_pkg_name
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_storage(tmp_path: Path) -> AIBundleStorage:
    return AIBundleStorage(
        tenant="demo-tenant",
        project="demo-project",
        ai_bundle_id="versatile@2026-03-31-13-36",
        storage_uri=tmp_path.resolve().as_uri(),
    )


def test_preferences_canvas_save_normalizes_document_and_appends_events(tmp_path):
    prefs = _load_preferences_store_module()
    storage = _make_storage(tmp_path)

    result = prefs.save_preferences_canvas_document(
        storage,
        "user-1",
        document_text=json.dumps(
            {
                "[p]_preferred_name": "Elena",
                "theme": "light",
                "profile": {"likes": ["tea", "books"]},
            }
        ),
    )

    current = prefs.load_current_preferences(storage, "user-1")
    assert result["changed_keys"] == ["preferred_name", "theme", "profile"]
    assert result["removed_keys"] == []
    assert current["preferred_name"]["value"] == "Elena"
    assert current["preferred_name"]["source"] == "preferences_canvas"
    assert current["preferred_name"]["origin"] == "user_canvas"
    assert current["preferred_name"]["updated_at"]
    assert current["theme"]["value"] == "light"
    assert current["profile"]["value"] == {"likes": ["tea", "books"]}

    events = prefs.load_preference_events(storage, "user-1")
    assert [event["key"] for event in events] == ["preferred_name", "theme", "profile"]
    assert all(event["origin"] == "user_canvas" for event in events)
    assert result["path"].endswith("preferences/users/user-1/current.json")


def test_preferences_canvas_document_is_simplified_for_human_editing(tmp_path):
    prefs = _load_preferences_store_module()
    storage = _make_storage(tmp_path)

    prefs.append_preference_event(
        storage,
        "user-1",
        key="[p]_preferred_name",
        value="Elena",
        source="chat",
        origin="auto_capture",
        evidence="call me Elena",
    )
    prefs.append_preference_event(
        storage,
        "user-1",
        key="timezone",
        value="Europe/Berlin",
        source="chat",
        origin="auto_capture",
        evidence="I am in Berlin",
    )

    doc = prefs.build_preferences_canvas_document(storage, "user-1")
    parsed = json.loads(doc["document_text"])
    assert parsed["preferred_name"]["value"] == "Elena"
    assert parsed["preferred_name"]["updated_at"]
    assert parsed["preferred_name"]["origin"] == "auto_capture"
    assert parsed["timezone"]["value"] == "Europe/Berlin"
    assert parsed["timezone"]["updated_at"]
    assert doc["path"].endswith("preferences/users/user-1/current.json")


def test_preference_tools_scope_falls_back_to_service_user(tmp_path, monkeypatch):
    tools_mod = _load_preference_tools_module()

    storage = _make_storage(tmp_path)

    class _StoreModule:
        @staticmethod
        def build_preferences_storage(*, tenant, project, bundle_id):
            assert tenant == "demo-tenant"
            assert project == "demo-project"
            assert bundle_id == "versatile@2026-03-31-13-36"
            return storage

    monkeypatch.setattr(tools_mod, "store_mod", _StoreModule)
    tools_mod.bind_integrations(
        {
            "tool_subsystem": type(
                "_ToolSubsystem",
                (),
                {
                    "comm": type(
                        "_Comm",
                        (),
                        {
                            "tenant": "demo-tenant",
                            "project": "demo-project",
                            "user_id": None,
                            "service": {"user": "fp-user-1"},
                        },
                    )(),
                    "bundle_spec": type("_BundleSpec", (), {"id": "versatile@2026-03-31-13-36"})(),
                },
            )()
        }
    )

    scope = tools_mod._scope()
    assert scope["user_id"] == "fp-user-1"


@pytest.mark.asyncio
async def test_preference_tools_get_preferences_returns_canonical_envelope(tmp_path, monkeypatch):
    prefs = _load_preferences_store_module()
    tools_mod = _load_preference_tools_module()
    storage = _make_storage(tmp_path)

    prefs.append_preference_event(
        storage,
        "fp-user-1",
        key="location",
        value="Wuppertal",
        source="chat",
        origin="auto_capture",
        evidence="I am in Wuppertal",
    )

    class _StoreModule:
        build_preferences_storage = staticmethod(lambda **_: storage)
        get_preferences_view = staticmethod(prefs.get_preferences_view)
        load_current_preferences = staticmethod(prefs.load_current_preferences)
        auto_capture_preferences = staticmethod(prefs.auto_capture_preferences)
        append_preference_event = staticmethod(prefs.append_preference_event)
        get_preferences_snapshot = staticmethod(prefs.get_preferences_snapshot)

    monkeypatch.setattr(tools_mod, "store_mod", _StoreModule)
    tools_mod.bind_integrations(
        {
            "tool_subsystem": type(
                "_ToolSubsystem",
                (),
                {
                    "comm": type(
                        "_Comm",
                        (),
                        {
                            "tenant": "demo-tenant",
                            "project": "demo-project",
                            "user_id": None,
                            "service": {"user": "fp-user-1"},
                        },
                    )(),
                    "bundle_spec": type("_BundleSpec", (), {"id": "versatile@2026-03-31-13-36"})(),
                },
            )()
        }
    )

    result = await tools_mod.PreferenceTools().get_preferences(recency=10, kwords="location")

    assert result["ok"] is True
    assert result["error"] is None
    assert result["ret"]["user_id"] == "fp-user-1"
    assert result["ret"]["current"]["location"]["value"] == "Wuppertal"
