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

    @api(method="POST", alias="public_ping", route="public", public_auth="none")
    async def public_ping(self, **kwargs):
        return kwargs

    @api(method="POST", alias="preferences_widget", route="operations", roles=("registered",))
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
    assert {
        (item.alias, item.http_method, item.route)
        for item in manifest.api_endpoints
    } == {
        ("preferences_widget", "POST", "operations"),
        ("prefs_save", "POST", "operations"),
        ("prefs_view", "GET", "operations"),
        ("public_ping", "POST", "public"),
    }
    public_ping = next(item for item in manifest.api_endpoints if item.alias == "public_ping")
    assert public_ping.public_auth and public_ping.public_auth.mode == "none"
    assert manifest.ui_main and manifest.ui_main.method_name == "main_ui"
    assert manifest.on_message and manifest.on_message.method_name == "handle_message"


def test_resolve_bundle_api_endpoint_prefers_decorated_alias_and_method():
    workflow = _DecoratedWorkflow()

    get_spec, allowed = resolve_bundle_api_endpoint(
        workflow,
        alias="prefs_view",
        http_method="GET",
        route="operations",
        bundle_id="bundle.demo",
    )
    assert get_spec and get_spec.method_name == "preferences_view"
    assert allowed == ("GET",)

    missing_post, allowed = resolve_bundle_api_endpoint(
        workflow,
        alias="prefs_view",
        http_method="POST",
        route="operations",
        bundle_id="bundle.demo",
    )
    assert missing_post is None
    assert allowed == ("GET",)

    public_spec, allowed = resolve_bundle_api_endpoint(
        workflow,
        alias="public_ping",
        http_method="POST",
        route="public",
        bundle_id="bundle.demo",
    )
    assert public_spec and public_spec.method_name == "public_ping"
    assert allowed == ("POST",)
    assert public_spec.public_auth and public_spec.public_auth.mode == "none"

    wrong_route, allowed = resolve_bundle_api_endpoint(
        workflow,
        alias="public_ping",
        http_method="POST",
        route="operations",
        bundle_id="bundle.demo",
    )
    assert wrong_route is None
    assert allowed == ()

    widget = resolve_bundle_widget(workflow, alias="preferences", bundle_id="bundle.demo")
    assert widget and widget.method_name == "preferences_widget"


def test_string_widget_icon_is_normalized_to_tailwind_provider():
    class _LegacyWidgetWorkflow:
        @api(alias="legacy_widget", route="operations")
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
    assert {
        (item["alias"], item["http_method"], item["route"])
        for item in manifest["api_endpoints"]
    } == {
        ("preferences_widget", "POST", "operations"),
        ("prefs_save", "POST", "operations"),
        ("prefs_view", "GET", "operations"),
        ("public_ping", "POST", "public"),
    }
    public_ping = next(item for item in manifest["api_endpoints"] if item["alias"] == "public_ping")
    assert public_ping["public_auth_mode"] == "none"

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
        route="operations",
        session=_session(),
    )

    assert captured["kwargs"]["value"] == "Wuppertal"
    assert result["prefs_view"] == {"ok": True, "value": "Wuppertal"}


@pytest.mark.asyncio
async def test_call_bundle_op_inner_supports_widget_compat_api(monkeypatch):
    async def _load_bundle_workflow(**kwargs):
        del kwargs
        return _DecoratedWorkflow(), SimpleNamespace(id="bundle.demo"), "tenant-a", "project-a"

    monkeypatch.setattr(integrations, "_load_bundle_workflow", _load_bundle_workflow)

    result = await integrations._call_bundle_op_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        payload=integrations.BundleSuggestionsRequest(),
        request=_request(
            method="POST",
            path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/operations/preferences_widget",
        ),
        operation="preferences_widget",
        route="operations",
        session=_session(),
    )

    assert result["preferences_widget"] == ["<p>fp-1</p>"]


@pytest.mark.asyncio
async def test_call_bundle_op_inner_rejects_undeclared_method_without_api(monkeypatch):
    class _Workflow:
        async def legacy_callable(self, **kwargs):
            return kwargs

    async def _load_bundle_workflow(**kwargs):
        del kwargs
        return _Workflow(), SimpleNamespace(id="bundle.demo"), "tenant-a", "project-a"

    monkeypatch.setattr(integrations, "_load_bundle_workflow", _load_bundle_workflow)

    with pytest.raises(integrations.HTTPException) as exc:
        await integrations._call_bundle_op_inner(
            tenant="tenant-a",
            project="project-a",
            bundle_id="bundle.demo",
            payload=integrations.BundleSuggestionsRequest(),
            request=_request(
                method="POST",
                path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/operations/legacy_callable",
            ),
            operation="legacy_callable",
            route="operations",
            session=_session(),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_call_bundle_op_inner_enforces_public_vs_operations_route(monkeypatch):
    async def _load_bundle_workflow(**kwargs):
        del kwargs
        return _DecoratedWorkflow(), SimpleNamespace(id="bundle.demo"), "tenant-a", "project-a"

    monkeypatch.setattr(integrations, "_load_bundle_workflow", _load_bundle_workflow)

    public_result = await integrations._call_bundle_op_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        payload=integrations.BundleSuggestionsRequest(),
        request=_request(
            method="POST",
            path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/public/public_ping",
        ),
        operation="public_ping",
        route="public",
        session=_session(),
    )
    assert public_result["public_ping"] == {"user_id": "user-1", "fingerprint": "fp-1"}

    with pytest.raises(integrations.HTTPException) as exc:
        await integrations._call_bundle_op_inner(
            tenant="tenant-a",
            project="project-a",
            bundle_id="bundle.demo",
            payload=integrations.BundleSuggestionsRequest(),
            request=_request(
                method="POST",
                path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/operations/public_ping",
            ),
            operation="public_ping",
            route="operations",
            session=_session(),
        )

    assert exc.value.status_code == 404


def test_api_rejects_public_auth_on_operations_route():
    with pytest.raises(ValueError):
        class _Workflow:
            @api(alias="bad", route="operations", public_auth="none")
            async def bad(self, **kwargs):
                return kwargs


@pytest.mark.asyncio
async def test_call_bundle_op_inner_rejects_public_endpoint_without_public_auth(monkeypatch):
    class _Workflow:
        @api(method="POST", alias="public_ping", route="public")
        async def public_ping(self, **kwargs):
            return kwargs

    async def _load_bundle_workflow(**kwargs):
        del kwargs
        return _Workflow(), SimpleNamespace(id="bundle.demo"), "tenant-a", "project-a"

    monkeypatch.setattr(integrations, "_load_bundle_workflow", _load_bundle_workflow)

    with pytest.raises(integrations.HTTPException) as exc:
        await integrations._call_bundle_op_inner(
            tenant="tenant-a",
            project="project-a",
            bundle_id="bundle.demo",
            payload=integrations.BundleSuggestionsRequest(),
            request=_request(
                method="POST",
                path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/public/public_ping",
            ),
            operation="public_ping",
            route="public",
            session=_session(),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_call_bundle_op_inner_enforces_public_header_secret(monkeypatch):
    class _Workflow:
        @api(
            method="POST",
            alias="telegram_webhook",
            route="public",
            public_auth={
                "mode": "header_secret",
                "header": "X-Telegram-Bot-Api-Secret-Token",
                "secret_key": "telegram.webhook_secret",
            },
        )
        async def telegram_webhook(self, **kwargs):
            return {"ok": True, "fingerprint": kwargs.get("fingerprint")}

    async def _load_bundle_workflow(**kwargs):
        del kwargs
        return _Workflow(), SimpleNamespace(id="bundle.demo"), "tenant-a", "project-a"

    monkeypatch.setattr(integrations, "_load_bundle_workflow", _load_bundle_workflow)
    monkeypatch.setattr(
        integrations,
        "get_secret",
        lambda key, default=None: "telegram-secret"
        if key == "bundles.bundle.demo.secrets.telegram.webhook_secret"
        else default,
    )

    with pytest.raises(integrations.HTTPException) as exc:
        await integrations._call_bundle_op_inner(
            tenant="tenant-a",
            project="project-a",
            bundle_id="bundle.demo",
            payload=integrations.BundleSuggestionsRequest(),
            request=_request(
                method="POST",
                path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/public/telegram_webhook",
            ),
            operation="telegram_webhook",
            route="public",
            session=_session(),
        )

    assert exc.value.status_code == 401

    result = await integrations._call_bundle_op_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        payload=integrations.BundleSuggestionsRequest(),
        request=Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/integrations/bundles/tenant-a/project-a/bundle.demo/public/telegram_webhook",
                "query_string": b"",
                "headers": [(b"x-telegram-bot-api-secret-token", b"telegram-secret")],
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
                "http_version": "1.1",
            }
        ),
        operation="telegram_webhook",
        route="public",
        session=_session(),
    )

    assert result["telegram_webhook"] == {"ok": True, "fingerprint": "fp-1"}


@pytest.mark.asyncio
async def test_load_bundle_workflow_rejects_config_scope_override(monkeypatch):
    captured: dict[str, object] = {}

    async def _resolve_bundle_async(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(id="bundle.demo", path="bundle.py", module=None, singleton=False)

    def _create_workflow_config(cfg_req):
        captured["cfg_req"] = cfg_req
        return SimpleNamespace(ai_bundle_spec=None)

    def _get_workflow_instance(spec, config, *, comm_context, redis):
        captured["spec"] = spec
        captured["config"] = config
        captured["comm_context"] = comm_context
        captured["redis"] = redis
        return _DecoratedWorkflow(), SimpleNamespace()

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(
            TENANT="tenant-a",
            PROJECT="project-a",
            OPENAI_API_KEY="openai-key",
            ANTHROPIC_API_KEY="claude-key",
        ),
    )
    monkeypatch.setattr(integrations, "resolve_bundle_async", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "create_workflow_config", _create_workflow_config)
    monkeypatch.setattr(integrations, "get_workflow_instance", _get_workflow_instance)
    monkeypatch.setattr(integrations, "_get_app_redis", lambda request: "redis-client")

    with pytest.raises(integrations.HTTPException) as exc:
        await integrations._load_bundle_workflow(
            tenant="tenant-a",
            project="project-a",
            bundle_id="bundle.demo",
            payload=integrations.BundleSuggestionsRequest(
                config_request=integrations.ConfigRequest(tenant="tenant-b", project="project-a")
            ),
            request=_request(),
            session=_session(),
        )

    assert exc.value.status_code == 400
    assert "config_request.tenant" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_load_bundle_workflow_rejects_scope_not_served_by_proc(monkeypatch):
    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(
            TENANT="tenant-a",
            PROJECT="project-a",
            OPENAI_API_KEY="openai-key",
            ANTHROPIC_API_KEY="claude-key",
        ),
    )

    with pytest.raises(integrations.HTTPException) as exc:
        await integrations._load_bundle_workflow(
            tenant="tenant-b",
            project="project-a",
            bundle_id="bundle.demo",
            payload=integrations.BundleSuggestionsRequest(),
            request=_request(),
            session=_session(),
        )

    assert exc.value.status_code == 403
    assert "tenant" in str(exc.value.detail).lower()
