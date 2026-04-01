from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import BaseEntrypointWithEconomics
from kdcube_ai_app.infra.plugin.agentic_loader import (
    api,
    discover_bundle_interface_manifest,
    on_message,
    resolve_bundle_message_method,
    resolve_bundle_api_endpoint,
    resolve_bundle_widget,
    ui_main,
    ui_widget,
)


def _session(*, user_type: str = "registered", roles: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        session_id="session-1",
        user_type=SimpleNamespace(value=user_type),
        user_id="user-1",
        username="elena",
        fingerprint="fp-1",
        roles=roles or [],
        permissions=["chat.use"],
        request_context=SimpleNamespace(user_timezone="Europe/Berlin", user_utc_offset_min=120),
    )


def _request(*, method: str = "GET", path: str = "/api/integrations/test", query_string: bytes = b"") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query_string,
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "http_version": "1.1",
        }
    )


class _DecoratedWorkflow:
    @api(method="GET", alias="prefs_view", roles=("registered",))
    async def preferences_view(self, **kwargs):
        return kwargs

    @api(method="POST", alias="prefs_save")
    async def save_preferences(self, **kwargs):
        return kwargs

    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:adjustments-horizontal",
            "lucide": "SlidersHorizontal",
        },
        alias="preferences",
        roles=("registered",),
    )
    def preferences_widget(self, **kwargs):
        return [f"<p>{kwargs.get('fingerprint')}</p>"]

    @ui_main
    def main_ui(self):
        return ["<html></html>"]

    @on_message
    async def handle_message(self, **kwargs):
        return kwargs


def test_discover_bundle_interface_manifest_returns_declarative_specs():
    manifest = discover_bundle_interface_manifest(_DecoratedWorkflow(), bundle_id="bundle.demo")

    assert manifest.bundle_id == "bundle.demo"
    assert [item.alias for item in manifest.ui_widgets] == ["preferences"]
    assert manifest.ui_widgets[0].icon == {
        "tailwind": "heroicons-outline:adjustments-horizontal",
        "lucide": "SlidersHorizontal",
    }
    assert [(item.alias, item.http_method) for item in manifest.api_endpoints] == [
        ("prefs_save", "POST"),
        ("prefs_view", "GET"),
    ]
    assert manifest.ui_main and manifest.ui_main.method_name == "main_ui"
    assert manifest.on_message and manifest.on_message.method_name == "handle_message"


def test_resolve_bundle_api_endpoint_prefers_decorated_alias_and_method():
    workflow = _DecoratedWorkflow()

    get_spec, allowed = resolve_bundle_api_endpoint(workflow, alias="prefs_view", http_method="GET", bundle_id="bundle.demo")
    assert get_spec and get_spec.method_name == "preferences_view"
    assert allowed == ("GET",)

    missing_post, allowed = resolve_bundle_api_endpoint(workflow, alias="prefs_view", http_method="POST", bundle_id="bundle.demo")
    assert missing_post is None
    assert allowed == ("GET",)

    widget = resolve_bundle_widget(workflow, alias="preferences", bundle_id="bundle.demo")
    assert widget and widget.method_name == "preferences_widget"


def test_string_widget_icon_is_normalized_to_tailwind_provider():
    class _LegacyWidgetWorkflow:
        @ui_widget(icon="heroicons-outline:swatch", alias="legacy")
        def legacy_widget(self, **kwargs):
            return kwargs

    manifest = discover_bundle_interface_manifest(_LegacyWidgetWorkflow(), bundle_id="bundle.demo")
    assert manifest.ui_widgets[0].icon == {"tailwind": "heroicons-outline:swatch"}


def test_base_entrypoints_expose_run_as_on_message():
    base = resolve_bundle_message_method(BaseEntrypoint, bundle_id="base")
    econ = resolve_bundle_message_method(BaseEntrypointWithEconomics, bundle_id="base.econ")

    assert base and base.method_name == "run"
    assert econ and econ.method_name == "run"


@pytest.mark.asyncio
async def test_get_bundle_interface_and_widgets_use_decorators(monkeypatch):
    async def _load_bundle_workflow(**kwargs):
        del kwargs
        return _DecoratedWorkflow(), SimpleNamespace(id="bundle.demo"), "tenant-a", "project-a"

    monkeypatch.setattr(integrations, "_load_bundle_workflow", _load_bundle_workflow)

    request = _request()
    session = _session()

    manifest = await integrations.get_bundle_interface(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        request=request,
        session=session,
    )
    assert manifest["bundle_id"] == "bundle.demo"
    assert manifest["ui_widgets"][0]["alias"] == "preferences"
    assert manifest["ui_widgets"][0]["icon"]["lucide"] == "SlidersHorizontal"
    assert manifest["api_endpoints"][0]["alias"] == "prefs_save"

    widgets = await integrations.list_bundle_widgets(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        request=request,
        session=session,
    )
    assert widgets["ui_widgets"][0]["alias"] == "preferences"

    widget_payload = await integrations.fetch_bundle_widget(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        widget_alias="preferences",
        request=request,
        session=session,
    )
    assert widget_payload["widget"]["alias"] == "preferences"
    assert widget_payload["preferences"] == ["<p>fp-1</p>"]


@pytest.mark.asyncio
async def test_call_bundle_op_inner_supports_decorated_get_api(monkeypatch):
    captured: dict[str, object] = {}

    class _Workflow(_DecoratedWorkflow):
        @api(method="GET", alias="prefs_view", roles=("registered",))
        async def preferences_view(self, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return {"ok": True, "value": kwargs.get("value")}

    async def _load_bundle_workflow(**kwargs):
        del kwargs
        return _Workflow(), SimpleNamespace(id="bundle.demo"), "tenant-a", "project-a"

    monkeypatch.setattr(integrations, "_load_bundle_workflow", _load_bundle_workflow)

    result = await integrations._call_bundle_op_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        payload=integrations.BundleSuggestionsRequest(),
        request=_request(
            method="GET",
            path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/operations/prefs_view",
            query_string=b"value=Wuppertal",
        ),
        operation="prefs_view",
        session=_session(),
    )

    assert captured["kwargs"]["value"] == "Wuppertal"
    assert result["prefs_view"] == {"ok": True, "value": "Wuppertal"}
