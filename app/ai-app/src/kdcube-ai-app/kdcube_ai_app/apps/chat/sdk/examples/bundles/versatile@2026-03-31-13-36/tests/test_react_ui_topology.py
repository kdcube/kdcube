from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.runtime.dynamic_module_loader import load_dynamic_module_for_path


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
