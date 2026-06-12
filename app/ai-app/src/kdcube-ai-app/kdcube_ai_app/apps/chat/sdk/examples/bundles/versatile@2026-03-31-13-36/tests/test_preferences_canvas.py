from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path
from kdcube_ai_app.apps.chat.sdk.storage.ai_bundle_storage import AIBundleStorage


def _bundle_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_preferences_store_module():
    module_path = _bundle_root() / "preferences_store.py"
    spec = importlib.util.spec_from_file_location("versatile_preferences_store_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_preference_tools_module():
    _mod_name, module = load_dynamic_module_for_path(_bundle_root() / "tools" / "preference_tools.py")
    return module


def _load_entrypoint_module():
    _mod_name, module = load_dynamic_module_for_path(_bundle_root() / "entrypoint.py")
    return module


def _discover_bundle_interface_manifest(workflow, *, bundle_id: str):
    bundle_loader = pytest.importorskip(
        "kdcube_ai_app.infra.plugin.bundle_loader",
        reason=(
            "KDCube platform source is not importable. Run with "
            "PYTHONPATH=<kdcube-ai-app>/app/ai-app/src/kdcube-ai-app or export it before pytest."
        ),
    )
    return bundle_loader.discover_bundle_interface_manifest(workflow, bundle_id=bundle_id)


def _make_storage(tmp_path: Path) -> AIBundleStorage:
    return AIBundleStorage(
        tenant="demo-tenant",
        project="demo-project",
        ai_bundle_id="versatile@2026-03-31-13-36",
        storage_uri=tmp_path.resolve().as_uri(),
    )


class _Relay:
    async def emit(self, *, event: str, data: dict, **kwargs) -> None:
        return None


class _Logger:
    def __init__(self):
        self.lines = []

    def log(self, message, level=None):
        self.lines.append((level, message))


def _comm() -> ChatCommunicator:
    return ChatCommunicator(
        emitter=_Relay(),
        tenant="tenant-a",
        project="project-a",
        user_id="user-1",
        user_type="registered",
        service={
            "request_id": "req-1",
            "tenant": "tenant-a",
            "project": "project-a",
            "user": "user-1",
            "bundle_id": "versatile@2026-03-31-13-36",
        },
        conversation={
            "session_id": "session-1",
            "conversation_id": "conversation-1",
            "turn_id": "turn-1",
        },
    )


def _bind_tool_subsystem(tools_mod, *, user_id: str | None = None, service_user: str | None = None):
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
                            "user_id": user_id,
                            "service": {"user": service_user} if service_user is not None else {},
                        },
                    )(),
                    "bundle_spec": type("_BundleSpec", (), {"id": "versatile@2026-03-31-13-36"})(),
                },
            )()
        }
    )


def test_telegram_bot_transport_manifest_and_defaults():
    module = _load_entrypoint_module()
    workflow = module.VersatileEntrypoint.__new__(module.VersatileEntrypoint)
    bundle_loader = pytest.importorskip(
        "kdcube_ai_app.infra.plugin.bundle_loader",
        reason=(
            "KDCube platform source is not importable. Run with "
            "PYTHONPATH=<kdcube-ai-app>/app/ai-app/src/kdcube-ai-app or export it before pytest."
        ),
    )

    manifest = _discover_bundle_interface_manifest(workflow, bundle_id="versatile@2026-03-31-13-36")
    assert manifest.allowed_roles_config == "visibility.bundle.allowed_roles"

    webhook = next(item for item in manifest.api_endpoints if item.alias == "telegram_webhook")
    assert webhook.http_method == "POST"
    assert webhook.route == "public"
    assert bundle_loader.canonical_enabled_path(
        "api",
        alias=webhook.alias,
        http_method=webhook.http_method,
        route=webhook.route,
    ) == "enabled.api.public.telegram_webhook.POST"
    assert webhook.public_auth is not None
    assert webhook.public_auth.mode == "header_secret"
    assert webhook.public_auth.header == "X-Telegram-Bot-Api-Secret-Token"
    assert webhook.public_auth.secret_key == "integrations.telegram.webhook_secret"

    conversation_list_enabled_paths = {
        item.route: bundle_loader.canonical_enabled_path(
            "api",
            alias=item.alias,
            http_method=item.http_method,
            route=item.route,
        )
        for item in manifest.api_endpoints
        if item.alias == "conversations_list" and item.http_method == "GET"
    }
    assert conversation_list_enabled_paths == {
        "operations": "enabled.api.operations.conversations_list.GET",
        "public": "enabled.api.public.conversations_list.GET",
    }

    admin_data = next(
        item
        for item in manifest.api_endpoints
        if item.alias == "telegram_user_admin_data" and item.route == "operations"
    )
    assert admin_data.roles == ("kdcube:role:super-admin",)
    assert admin_data.roles_config == "visibility.api.telegram_user_admin_data.roles"
    assert admin_data.user_types_config == "visibility.api.telegram_user_admin_data.user_types"

    defaults = module.VersatileEntrypoint.configuration_defaults(workflow)
    enabled_api = defaults.get("enabled", {}).get("api", {})
    assert "telegram_webhook.POST" not in enabled_api
    assert "telegram_versatile_webapp_data.POST" not in enabled_api
    assert "telegram_webapp_user_admin_data.POST" not in enabled_api
    assert "telegram_user_admin_data.POST" not in enabled_api
    assert "versatile_webapp_widget.POST" not in enabled_api
    assert "widget" not in defaults.get("enabled", {})
    assert defaults["visibility"]["bundle"] == {"allowed_roles": []}
    assert defaults["visibility"]["api"]["telegram_user_admin_data"] == {
        "user_types": [],
        "roles": ["kdcube:role:super-admin"],
    }
    assert defaults["visibility"]["widget"]["versatile_webapp"] == {
        "user_types": [],
        "roles": [],
    }
    assert "tools" not in defaults
    assert "named_services" not in defaults
    assert defaults["surfaces"]["as_consumer"]["default_agent"] == "main"
    assert defaults["surfaces"]["as_consumer"]["agents"]["main"]["event_sources"] == []
    assert defaults["surfaces"]["as_consumer"]["ui"]["canvas"]["resolvers"] == []
    assert defaults["telemetry_sink"] == {"endpoint_url": "", "auth_header": ""}
    assert defaults["integrations"]["telegram"] == {
        "enabled": False,
        "webhook_url": "",
        "send_responses": True,
        "stream_activity": True,
        "web_app_auth_max_age_seconds": 86400,
    }
    assert defaults["ui"]["widgets"]["versatile_webapp"]["src_folder"] == "ui/widgets/versatile_webapp"
    assert "enabled" not in defaults["ui"]["widgets"]["versatile_webapp"]


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


def test_preferences_canvas_document_is_simplified_for_human_editing(tmp_path, monkeypatch):
    prefs = _load_preferences_store_module()
    storage = _make_storage(tmp_path)
    times = iter([
        "2026-04-02T09:00:00+00:00",
        "2026-04-02T09:01:00+00:00",
    ])
    monkeypatch.setattr(prefs, "_utc_now", lambda: next(times))

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
    assert doc["document_format"] == "entries"
    assert doc["entries"][0]["label"] == "preferred_name"
    assert doc["entries"][0]["author"] == "assistant"
    assert doc["entries"][1]["label"] == "timezone"
    parsed = json.loads(doc["document_text"])
    assert parsed["preferred_name"]["value"] == "Elena"
    assert parsed["preferred_name"]["updated_at"]
    assert parsed["preferred_name"]["origin"] == "auto_capture"
    assert parsed["timezone"]["value"] == "Europe/Berlin"
    assert parsed["timezone"]["updated_at"]
    assert doc["path"].endswith("preferences/users/user-1/current.json")


def test_preferences_canvas_entries_present_notebook_friendly_rows(tmp_path, monkeypatch):
    prefs = _load_preferences_store_module()
    storage = _make_storage(tmp_path)
    times = iter([
        "2026-04-02T09:00:00+00:00",
        "2026-04-02T09:02:00+00:00",
    ])
    monkeypatch.setattr(prefs, "_utc_now", lambda: next(times))

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
        key="fish_preference",
        value={"style": "fresh", "priority": "high"},
        source="preferences_canvas",
        origin="user",
        evidence="Edited in collaborative preferences notebook",
    )

    entries = prefs.build_preferences_canvas_entries(storage, "user-1")

    assert [entry["label"] for entry in entries] == ["preferred_name", "fish_preference"]
    assert entries[0]["author"] == "assistant"
    assert entries[0]["text"] == "Elena"
    assert entries[1]["author"] == "user"
    assert '"priority": "high"' in entries[1]["text"]


def test_preferences_canvas_entries_edit_rewrites_timestamp_and_origin(tmp_path, monkeypatch):
    prefs = _load_preferences_store_module()
    storage = _make_storage(tmp_path)

    prefs.append_preference_event(
        storage,
        "user-1",
        key="location",
        value="Berlin",
        source="chat",
        origin="auto_capture",
        evidence="I am in Berlin",
    )

    monkeypatch.setattr(prefs, "_utc_now", lambda: "2026-04-02T10:15:00+00:00")

    result = prefs.save_preferences_canvas_entries(
        storage,
        "user-1",
        entries=[
            {
                "key": "location",
                "label": "city",
                "text": "Wuppertal",
            }
        ],
    )

    current = prefs.load_current_preferences(storage, "user-1")
    assert "location" not in current
    assert current["city"]["value"] == "Wuppertal"
    assert current["city"]["updated_at"] == "2026-04-02T10:15:00+00:00"
    assert current["city"]["origin"] == "user"
    assert current["city"]["source"] == "preferences_canvas"
    assert result["changed_keys"] == ["city"]
    assert result["removed_keys"] == ["location"]
    assert result["entries"][0]["label"] == "city"
    assert result["entries"][0]["author"] == "user"
    assert result["entries"][0]["updated_at"] == "2026-04-02T10:15:00+00:00"

    events = prefs.load_preference_events(storage, "user-1")
    assert events[-2]["key"] == "city"
    assert events[-2]["origin"] == "user"
    assert events[-1]["key"] == "location"
    assert events[-1]["origin"] == "user_remove"


def test_preferences_canvas_excel_export_and_import_roundtrip(tmp_path, monkeypatch):
    prefs = _load_preferences_store_module()
    storage = _make_storage(tmp_path)
    times = iter([
        "2026-04-02T09:00:00+00:00",
        "2026-04-02T09:02:00+00:00",
        "2026-04-02T10:15:00+00:00",
    ])
    monkeypatch.setattr(prefs, "_utc_now", lambda: next(times))

    prefs.append_preference_event(
        storage,
        "user-1",
        key="location",
        value="Wuppertal",
        source="chat",
        origin="auto_capture",
        evidence="I am in Wuppertal",
    )
    prefs.append_preference_event(
        storage,
        "user-1",
        key="food_preference",
        value="fresh fish",
        source="preferences_canvas",
        origin="user",
        evidence="Edited in collaborative preferences notebook",
    )

    workbook = prefs.export_preferences_canvas_xlsx(storage, "user-1")
    imported = prefs.import_preferences_canvas_xlsx(workbook)

    assert [entry["label"] for entry in imported] == ["location", "food_preference"]
    assert imported[0]["text"] == "Wuppertal"
    assert imported[1]["text"] == "fresh fish"

    result = prefs.save_preferences_canvas_entries(storage, "user-1", entries=imported)
    current = prefs.load_current_preferences(storage, "user-1")

    assert {entry["label"] for entry in result["entries"]} == {"location", "food_preference"}
    assert current["location"]["value"] == "Wuppertal"
    assert current["location"]["origin"] == "user"
    assert current["location"]["updated_at"] == "2026-04-02T10:15:00+00:00"
    assert current["food_preference"]["value"] == "fresh fish"


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
    _bind_tool_subsystem(tools_mod, user_id=None, service_user="fp-user-1")

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
    _bind_tool_subsystem(tools_mod, user_id=None, service_user="fp-user-1")

    result = await tools_mod.PreferenceTools().get_preferences(recency=10, kwords="location")

    assert result["ok"] is True
    assert result["error"] is None
    assert result["ret"]["user_id"] == "fp-user-1"
    assert result["ret"]["current"]["location"]["value"] == "Wuppertal"


@pytest.mark.asyncio
async def test_preference_tools_get_preferences_treats_comma_keywords_as_alternatives(tmp_path, monkeypatch):
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
    _bind_tool_subsystem(tools_mod, user_id=None, service_user="fp-user-1")

    result = await tools_mod.PreferenceTools().get_preferences(
        recency=10,
        kwords="city, location, timezone",
    )

    assert result["ok"] is True
    assert result["ret"]["matched_count"] == 1
    assert result["ret"]["current"]["location"]["value"] == "Wuppertal"
    assert "No stored preferences yet." not in result["ret"]["summary"]


@pytest.mark.asyncio
async def test_export_preferences_snapshot_uses_bundle_secret_for_signature(tmp_path, monkeypatch):
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
        get_preferences_snapshot = staticmethod(prefs.get_preferences_snapshot)

    monkeypatch.setattr(tools_mod, "store_mod", _StoreModule)
    async def _get_secret(key, default=None):
        return "snapshot-secret" if key.endswith(".preferences.snapshot_hmac_key") else default

    monkeypatch.setattr(tools_mod, "get_secret", _get_secret)
    _bind_tool_subsystem(tools_mod, user_id=None, service_user="fp-user-1")

    result = await tools_mod.PreferenceTools().export_preferences_snapshot(filename="user-preferences.json")

    assert result["ok"] is True
    assert result["ret"]["signed"] is True
    assert result["ret"]["signature_algorithm"] == "hmac-sha256"
    assert result["ret"]["signature_secret_key"] == (
        "bundles.versatile@2026-03-31-13-36.secrets.preferences.snapshot_hmac_key"
    )
    assert result["ret"]["signature_key"] == "preferences/exports/fp-user-1/user-preferences.json.sig.json"

    signature_doc = json.loads(storage.read(result["ret"]["signature_key"], as_text=True))
    assert signature_doc["algorithm"] == "hmac-sha256"
    assert signature_doc["secret_key"] == result["ret"]["signature_secret_key"]
    assert signature_doc["signed_key"] == result["ret"]["key"]
    assert signature_doc["signature"]


@pytest.mark.asyncio
async def test_preference_tools_delete_preference_removes_current_value(tmp_path, monkeypatch):
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
        load_current_preferences = staticmethod(prefs.load_current_preferences)
        append_preference_event = staticmethod(prefs.append_preference_event)

    monkeypatch.setattr(tools_mod, "store_mod", _StoreModule)
    _bind_tool_subsystem(tools_mod, user_id=None, service_user="fp-user-1")

    result = await tools_mod.PreferenceTools().delete_preference(key="location")

    assert result["ok"] is True
    assert "location" not in result["ret"]["current"]
    assert result["ret"]["event"]["value"] is None
    assert result["ret"]["event"]["origin"] == "tool_delete"


def test_build_preferences_mcp_app_returns_streamable_http_app(tmp_path):
    tools_mod = _load_preference_tools_module()
    storage = _make_storage(tmp_path)
    app = tools_mod.build_preferences_mcp_app(
        name="versatile.preferences_tools",
        scope_provider=lambda user_id=None: {
            "tenant": "demo-tenant",
            "project": "demo-project",
            "bundle_id": "versatile@2026-03-31-13-36",
            "user_id": user_id or "demo-user",
            "storage": storage,
        },
    )

    assert hasattr(app, "streamable_http_app")
    assert callable(app.streamable_http_app)
    assert app.streamable_http_app() is not None


@pytest.mark.asyncio
async def test_event_recording_configures_react_scope_from_endpoint_and_secret(monkeypatch):
    entrypoint_mod = _load_entrypoint_module()
    entrypoint = object.__new__(entrypoint_mod.VersatileEntrypoint)
    entrypoint._comm = _comm()
    entrypoint.logger = _Logger()
    entrypoint.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="versatile@2026-03-31-13-36"))
    entrypoint.bundle_props = {"events": {"record": {"telemetry": {"enabled": True}}}}
    entrypoint.bundle_prop = lambda key, default=None: (
        "http://stats.local/public/ingest" if key == "telemetry_sink.endpoint_url" else default
    )

    async def _get_secret(key, **kwargs):
        return "telemetry-token" if key == entrypoint_mod.TELEMETRY_SINK_TOKEN_SECRET else None

    monkeypatch.setattr(entrypoint_mod, "get_secret", _get_secret)

    await entrypoint._configure_event_recording()

    recording = entrypoint.comm.recording_config()
    assert entrypoint.comm.event_sink is not None
    assert recording["enabled"] is True
    assert recording["scopes"][0]["scope"] == {
        "owner": "react",
        "bundle": "versatile@2026-03-31-13-36",
        "runtime": "on_message",
    }


@pytest.mark.asyncio
async def test_event_recording_sends_chat_message_and_turn_metrics(monkeypatch):
    entrypoint_mod = _load_entrypoint_module()
    entrypoint = object.__new__(entrypoint_mod.VersatileEntrypoint)
    entrypoint._comm = _comm()
    entrypoint.logger = _Logger()
    entrypoint.config = SimpleNamespace(ai_bundle_spec=SimpleNamespace(id="versatile@2026-03-31-13-36"))
    entrypoint.bundle_props = {"events": {"record": {"telemetry": {"enabled": True}}}}
    sent_batches = []

    async def sink(batch, **kwargs):
        sent_batches.append((batch, kwargs))
        return {"accepted": len(batch)}

    async def _make_event_sink():
        return sink

    entrypoint._make_event_sink = _make_event_sink
    await entrypoint._configure_event_recording()

    await entrypoint.comm.event(
        agent="user",
        type="chat.conversation.accepted",
        route="chat.step",
        title="User Message",
        step="chat.user.message",
        status="completed",
        data={
            "text": "private user text",
            "message_len": 17,
            "input_kind": "message",
            "attachment_count": 1,
        },
    )
    await entrypoint.comm.event(
        agent="planner",
        type="chat.conversation.turn.completed",
        route="chat.step",
        title="Plan Completed",
        step="plan.done",
        status="completed",
        data={
            "produced_file_count": 2,
            "citation_count": 3,
        },
    )

    result = await entrypoint._send_recorded_events()

    assert result["ok"] is True
    assert len(sent_batches) == 1
    batch, kwargs = sent_batches[0]
    assert [item["type"] for item in batch] == [
        "chat.conversation.accepted",
        "chat.conversation.turn.completed",
    ]
    assert kwargs["filter"] == entrypoint_mod.STATS_COMM_EVENT_SELECTOR
    assert entrypoint.comm.export_recorded_events() == []
