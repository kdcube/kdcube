from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client_tools import (
    set_denied_named_service_namespaces,
)


def _bundle_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_agents_main_module():
    _mod_name, module = load_dynamic_module_for_path(_bundle_root() / "agents" / "main.py")
    return module


def test_telegram_request_payload_selects_telegram_ui_topology():
    module = _load_agents_main_module()
    ctx = SimpleNamespace(
        event=SimpleNamespace(source="", event_source_id=""),
        meta=SimpleNamespace(instance_id=""),
        request=SimpleNamespace(
            payload={
                "source": "telegram",
                "telegram": {"chat_id": "100200300", "turn_id": "turn-test"},
            },
            external_events=[],
        ),
    )

    instructions = module._resolve_react_ui_instructions(ctx)

    assert "UI topology for this chat (Telegram):" in instructions
    assert "no tabs" in instructions
    assert "Artifacts tab" not in instructions
    assert "Files tab" not in instructions


def test_telegram_external_event_selects_telegram_ui_topology():
    module = _load_agents_main_module()
    ctx = SimpleNamespace(
        event=SimpleNamespace(source="", event_source_id=""),
        meta=SimpleNamespace(instance_id=""),
        request=SimpleNamespace(
            payload={},
            external_events=[
                {
                    "type": "event.user.prompt",
                    "event_source_id": "telegram.user.prompt",
                }
            ],
        ),
    )

    instructions = module._resolve_react_ui_instructions(ctx)

    assert "UI topology for this chat (Telegram):" in instructions
    assert "Artifacts tab" not in instructions


def test_web_context_keeps_web_ui_topology():
    module = _load_agents_main_module()
    ctx = SimpleNamespace(
        event=SimpleNamespace(source="ingress.web", event_source_id="chat.user.prompt"),
        meta=SimpleNamespace(instance_id="web"),
        request=SimpleNamespace(payload={"source": "web"}, external_events=[]),
    )

    instructions = module._resolve_react_ui_instructions(ctx)

    assert "UI topology for this chat (web interface):" in instructions
    assert "Artifacts tab" in instructions


def test_additional_instructions_omit_disabled_memory_and_canvas_namespaces():
    module = _load_agents_main_module()
    module.resolve_memory_react_additional_instructions = lambda *_args, **_kwargs: "[MEMORY CONTEXT]"
    module.CANVAS_REACT_ADDITIONAL_INSTRUCTIONS = "[CANVAS CONTEXT]"
    ctx = SimpleNamespace(
        event=SimpleNamespace(source="ingress.web", event_source_id="chat.user.prompt"),
        meta=SimpleNamespace(instance_id="web"),
        request=SimpleNamespace(payload={"source": "web"}, external_events=[]),
    )

    set_denied_named_service_namespaces({"mem", "cnv"})
    try:
        instructions = module._resolve_react_additional_instructions(ctx)
    finally:
        set_denied_named_service_namespaces(None)

    assert "UI topology for this chat (web interface):" in instructions
    assert "[MEMORY CONTEXT]" not in instructions
    assert "[CANVAS CONTEXT]" not in instructions


def test_additional_instructions_keep_enabled_memory_and_canvas_namespaces():
    module = _load_agents_main_module()
    module.resolve_memory_react_additional_instructions = lambda *_args, **_kwargs: "[MEMORY CONTEXT]"
    module.CANVAS_REACT_ADDITIONAL_INSTRUCTIONS = "[CANVAS CONTEXT]"
    ctx = SimpleNamespace(
        event=SimpleNamespace(source="ingress.web", event_source_id="chat.user.prompt"),
        meta=SimpleNamespace(instance_id="web"),
        request=SimpleNamespace(payload={"source": "web"}, external_events=[]),
    )

    set_denied_named_service_namespaces(None)
    instructions = module._resolve_react_additional_instructions(ctx)

    assert "[MEMORY CONTEXT]" in instructions
    assert "[CANVAS CONTEXT]" in instructions
