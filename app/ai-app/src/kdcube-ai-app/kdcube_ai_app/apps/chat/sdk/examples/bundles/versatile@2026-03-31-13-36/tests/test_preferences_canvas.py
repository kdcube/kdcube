from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.emitters import ChatCommunicator
from kdcube_ai_app.apps.chat.sdk.comm.sink import STATS_COMM_EVENT_SELECTOR
from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path


def _bundle_root() -> Path:
    return Path(__file__).resolve().parents[1]


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
    assert "telegram_webapp_user_admin_data.POST" not in enabled_api
    assert "telegram_user_admin_data.POST" not in enabled_api
    assert "widget" not in defaults.get("enabled", {})
    assert defaults["visibility"]["bundle"] == {"allowed_roles": []}
    assert defaults["visibility"]["api"]["telegram_user_admin_data"] == {
        "user_types": [],
        "roles": ["kdcube:role:super-admin"],
    }
    assert "tools" not in defaults
    assert "named_services" not in defaults
    assert "surfaces" not in defaults
    assert defaults["telemetry_sink"] == {"endpoint_url": "", "auth_header": ""}
    assert defaults["integrations"]["telegram"] == {
        "enabled": False,
        "webhook_url": "",
        "send_responses": True,
        "stream_activity": True,
        "web_app_auth_max_age_seconds": 86400,
    }
    assert defaults["ui"]["widgets"]["telegram_miniapp"]["src_folder"] == "ui/widgets/telegram_miniapp"
    assert "versatile_webapp" not in defaults["ui"]["widgets"]


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
    assert kwargs["filter"] == STATS_COMM_EVENT_SELECTOR
    assert entrypoint.comm.export_recorded_events() == []
