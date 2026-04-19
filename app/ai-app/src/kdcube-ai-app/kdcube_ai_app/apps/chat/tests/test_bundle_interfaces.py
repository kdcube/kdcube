from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.auth.sessions import UserType
from kdcube_ai_app.apps.chat.sdk.runtime.http_ops import BundleBinaryResponse
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint import BaseEntrypoint
from kdcube_ai_app.apps.chat.sdk.solutions.chatbot.entrypoint_with_economic import BaseEntrypointWithEconomics
from kdcube_ai_app.infra.plugin.agentic_loader import (
    _BUNDLE_VENV_EXEC_ENV,
    _BUNDLE_VENV_STAMP_FILE,
    _bundle_venv_base_python,
    _bundle_venv_build_env,
    _load_module_from_dir,
    _load_from_sys_with_path_on_syspath,
    BUNDLE_VENV_ATTR,
    api,
    discover_bundle_interface_manifest,
    mcp,
    on_message,
    resolve_bundle_message_method,
    resolve_bundle_api_endpoint,
    resolve_bundle_mcp_endpoint,
    resolve_bundle_widget,
    ui_main,
    ui_widget,
    venv,
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


def _request(
        *,
        method: str = "GET",
        path: str = "/api/integrations/test",
        query_string: bytes = b"",
        body: bytes = b"",
        headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    sent = False

    async def _receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query_string,
            "headers": headers or [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "http_version": "1.1",
        },
        receive=_receive,
    )


class _RecordingMCPProvider:
    def streamable_http_app(self):
        async def _app(scope, receive, send):
            assert scope["type"] == "http"
            body = b""
            while True:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break

            payload = json.dumps(
                {
                    "path": scope.get("path"),
                    "method": scope.get("method"),
                    "body": body.decode("utf-8"),
                    "authorization": dict(scope.get("headers") or {}).get(b"authorization", b"").decode("utf-8"),
                    "cookie": dict(scope.get("headers") or {}).get(b"cookie", b"").decode("utf-8"),
                }
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": payload,
                    "more_body": False,
                }
            )

        return _app


class _DecoratedWorkflow:
    @api(method="GET", alias="prefs_view", user_types=("registered",))
    async def preferences_view(self, **kwargs):
        return kwargs

    @api(method="POST", alias="prefs_save")
    async def save_preferences(self, **kwargs):
        return kwargs

    @api(method="POST", alias="public_ping", route="public", public_auth="none")
    async def public_ping(self, **kwargs):
        return kwargs

    @mcp(alias="tools", route="operations")
    def tools_mcp(self, **kwargs):
        return _RecordingMCPProvider()

    @mcp(alias="public_tools", route="public")
    def public_tools_mcp(self, **kwargs):
        return _RecordingMCPProvider()

    @api(method="POST", alias="preferences_widget", route="operations", user_types=("registered",))
    @ui_widget(
        icon={
            "tailwind": "heroicons-outline:adjustments-horizontal",
            "lucide": "SlidersHorizontal",
        },
        alias="preferences",
        user_types=("registered",),
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
    tools_mcp = next(item for item in manifest.mcp_endpoints if item.alias == "tools")
    assert tools_mcp.route == "operations"
    assert tools_mcp.transport == "streamable-http"
    public_tools_mcp = next(item for item in manifest.mcp_endpoints if item.alias == "public_tools")
    assert public_tools_mcp.route == "public"
    prefs_view = next(item for item in manifest.api_endpoints if item.alias == "prefs_view")
    assert prefs_view.user_types == ("registered",)
    assert prefs_view.roles == ()
    assert manifest.ui_widgets[0].user_types == ("registered",)
    assert manifest.ui_main and manifest.ui_main.method_name == "main_ui"
    assert manifest.on_message and manifest.on_message.method_name == "handle_message"


def test_discover_bundle_interface_manifest_normalizes_legacy_roles_to_user_types_and_raw_roles():
    class _LegacyWorkflow:
        @api(method="GET", alias="legacy_user_type", roles=("registered",))
        async def legacy_user_type(self, **kwargs):
            return kwargs

        @api(method="POST", alias="legacy_admin", roles=("super-admin",))
        async def legacy_admin(self, **kwargs):
            return kwargs

    manifest = discover_bundle_interface_manifest(_LegacyWorkflow(), bundle_id="bundle.demo")
    legacy_user_type = next(item for item in manifest.api_endpoints if item.alias == "legacy_user_type")
    legacy_admin = next(item for item in manifest.api_endpoints if item.alias == "legacy_admin")

    assert legacy_user_type.user_types == ("registered",)
    assert legacy_user_type.roles == ()
    assert legacy_admin.user_types == ()
    assert legacy_admin.roles == ("kdcube:role:super-admin",)


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
    mcp_spec = resolve_bundle_mcp_endpoint(workflow, alias="tools", route="operations", bundle_id="bundle.demo")
    assert mcp_spec and mcp_spec.method_name == "tools_mcp"
    assert resolve_bundle_mcp_endpoint(workflow, alias="missing", route="operations", bundle_id="bundle.demo") is None


def test_string_widget_icon_is_normalized_to_tailwind_provider():
    class _LegacyWidgetWorkflow:
        @api(alias="legacy_widget", route="operations")
        @ui_widget(icon="heroicons-outline:swatch", alias="legacy")
        def legacy_widget(self, **kwargs):
            return kwargs

    manifest = discover_bundle_interface_manifest(_LegacyWidgetWorkflow(), bundle_id="bundle.demo")
    assert manifest.ui_widgets[0].icon == {"tailwind": "heroicons-outline:swatch"}


def test_venv_decorator_records_metadata_without_changing_function_behavior(monkeypatch):
    monkeypatch.setenv(_BUNDLE_VENV_EXEC_ENV, "1")

    @venv(requirements="requirements.txt", python="python3.11", timeout_seconds=30)
    def _job(payload: dict[str, object]) -> dict[str, object]:
        return payload

    assert getattr(_job, BUNDLE_VENV_ATTR) == {
        "requirements": "requirements.txt",
        "python": "python3.11",
        "timeout_seconds": 30,
    }
    assert _job({"ok": True}) == {"ok": True}


def test_venv_decorator_executes_in_cached_bundle_subprocess(monkeypatch, tmp_path):
    storage_root = tmp_path / "bundle-storage"
    monkeypatch.setenv("BUNDLE_STORAGE_ROOT", str(storage_root))

    bundle_dir = tmp_path / "demo-bundle"
    bundle_dir.mkdir()
    (bundle_dir / "requirements.txt").write_text("", encoding="utf-8")
    (bundle_dir / "entrypoint.py").write_text(
        "\n".join(
            [
                "from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, bundle_id",
                "",
                "@agentic_workflow(name='Demo Bundle')",
                "@bundle_id('bundle.demo')",
                "class DemoBundle:",
                "    pass",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (bundle_dir / "service.py").write_text(
        "\n".join(
            [
                "import os",
                "import sys",
                "from kdcube_ai_app.infra.plugin.agentic_loader import venv",
                "",
                "@venv(requirements='requirements.txt')",
                "def run_job(payload):",
                "    return {",
                "        'pid': os.getpid(),",
                "        'python': sys.executable,",
                "        'payload': payload,",
                "    }",
                "",
            ]
        ),
        encoding="utf-8",
    )

    mod = _load_module_from_dir(bundle_dir, "service")
    first = mod.run_job({"ok": True})
    assert first["payload"] == {"ok": True}
    assert first["pid"] != os.getpid()
    assert str(storage_root.resolve()) in first["python"]

    venv_dir = storage_root / "_bundle_venvs" / "bundle.demo"
    stamp_path = venv_dir / _BUNDLE_VENV_STAMP_FILE
    stamp_1 = json.loads(stamp_path.read_text(encoding="utf-8"))
    assert stamp_1["bundle_id"] == "bundle.demo"

    second = mod.run_job({"ok": False})
    assert second["payload"] == {"ok": False}
    stamp_2 = json.loads(stamp_path.read_text(encoding="utf-8"))
    assert stamp_2["build_id"] == stamp_1["build_id"]

    (bundle_dir / "requirements.txt").write_text("# change\n", encoding="utf-8")
    third = mod.run_job({"changed": True})
    assert third["payload"] == {"changed": True}
    stamp_3 = json.loads(stamp_path.read_text(encoding="utf-8"))
    assert stamp_3["build_id"] != stamp_2["build_id"]


def test_venv_decorator_supports_bundle_local_dataclass_arguments(monkeypatch, tmp_path):
    storage_root = tmp_path / "bundle-storage"
    monkeypatch.setenv("BUNDLE_STORAGE_ROOT", str(storage_root))

    bundle_dir = tmp_path / "dataclass-bundle"
    bundle_dir.mkdir()
    (bundle_dir / "requirements.txt").write_text("", encoding="utf-8")
    (bundle_dir / "entrypoint.py").write_text(
        "\n".join(
            [
                "from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, bundle_id",
                "",
                "@agentic_workflow(name='Dataclass Bundle')",
                "@bundle_id('bundle.dataclass')",
                "class DataclassBundle:",
                "    pass",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (bundle_dir / "service.py").write_text(
        "\n".join(
            [
                "from dataclasses import dataclass",
                "from kdcube_ai_app.infra.plugin.agentic_loader import venv",
                "",
                "@dataclass",
                "class SheetUser:",
                "    email: str",
                "    status: str",
                "",
                "@venv(requirements='requirements.txt')",
                "def echo_user(user: SheetUser) -> SheetUser:",
                "    return SheetUser(email=user.email.upper(), status=user.status)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    mod = _load_module_from_dir(bundle_dir, "service")
    result = mod.echo_user(mod.SheetUser(email="alpha@example.com", status="new"))

    assert isinstance(result, mod.SheetUser)
    assert result.email == "ALPHA@EXAMPLE.COM"
    assert result.status == "new"


def test_venv_decorator_supports_bundle_local_dataclass_arguments_when_module_spec_contains_bundle_dir(
    monkeypatch, tmp_path
):
    storage_root = tmp_path / "bundle-storage"
    monkeypatch.setenv("BUNDLE_STORAGE_ROOT", str(storage_root))

    container_dir = tmp_path / "bundle-container"
    bundle_dir = container_dir / "user-mgmt@1-0"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "requirements.txt").write_text("", encoding="utf-8")
    (bundle_dir / "entrypoint.py").write_text(
        "\n".join(
            [
                "from kdcube_ai_app.infra.plugin.agentic_loader import agentic_workflow, bundle_id",
                "",
                "@agentic_workflow(name='User Mgmt Bundle')",
                "@bundle_id('user-mgmt@1-0')",
                "class UserMgmtBundle:",
                "    pass",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (bundle_dir / "service.py").write_text(
        "\n".join(
            [
                "from dataclasses import dataclass",
                "from kdcube_ai_app.infra.plugin.agentic_loader import venv",
                "",
                "@dataclass",
                "class Payload:",
                "    email: str",
                "",
                "@venv(requirements='requirements.txt')",
                "def normalize(payload: Payload) -> Payload:",
                "    return Payload(email=payload.email.upper())",
                "",
            ]
        ),
        encoding="utf-8",
    )

    mod = _load_from_sys_with_path_on_syspath(container_dir, "user-mgmt@1-0.service")
    result = mod.normalize(mod.Payload(email="alpha@example.com"))

    assert isinstance(result, mod.Payload)
    assert result.email == "ALPHA@EXAMPLE.COM"


def test_bundle_venv_build_env_strips_nested_runtime_and_debugger_vars(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/current-venv")
    monkeypatch.setenv("PYTHONPATH", "/tmp/pythonpath")
    monkeypatch.setenv("PYCHARM_HOSTED", "1")
    monkeypatch.setenv("PYDEVD_USE_CYTHON", "YES")
    monkeypatch.setenv("IDE_PROJECT_ROOTS", "/tmp/project")

    env = _bundle_venv_build_env()

    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert "PYCHARM_HOSTED" not in env
    assert "PYDEVD_USE_CYTHON" not in env
    assert "IDE_PROJECT_ROOTS" not in env


def test_bundle_venv_base_python_prefers_base_executable(monkeypatch):
    monkeypatch.setattr("sys._base_executable", "/tmp/base-python", raising=False)
    monkeypatch.setattr("sys.executable", "/tmp/runtime-python")

    assert _bundle_venv_base_python({}) == "/tmp/base-python"
    assert _bundle_venv_base_python({"python": "/tmp/requested-python"}) == "/tmp/requested-python"


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
    prefs_view = next(item for item in manifest["api_endpoints"] if item["alias"] == "prefs_view")
    assert prefs_view["user_types"] == ["registered"]
    assert prefs_view["roles"] == []
    assert manifest["ui_widgets"][0]["user_types"] == ["registered"]
    assert manifest["ui_widgets"][0]["roles"] == []
    assert {
        (item["alias"], item["route"], item["transport"])
        for item in manifest["mcp_endpoints"]
    } == {
        ("tools", "operations", "streamable-http"),
        ("public_tools", "public", "streamable-http"),
    }

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
    assert widget_payload["widget"]["user_types"] == ["registered"]
    assert widget_payload["widget"]["roles"] == []
    assert widget_payload["preferences"] == ["<p>fp-1</p>"]


@pytest.mark.asyncio
async def test_call_bundle_op_inner_supports_decorated_get_api(monkeypatch):
    captured: dict[str, object] = {}

    class _Workflow(_DecoratedWorkflow):
        @api(method="GET", alias="prefs_view", user_types=("registered",))
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
async def test_call_bundle_mcp_inner_dispatches_into_bundle_mcp_app(monkeypatch):
    async def _load_bundle_workflow(**kwargs):
        del kwargs
        return _DecoratedWorkflow(), SimpleNamespace(id="bundle.demo"), "tenant-a", "project-a"

    monkeypatch.setattr(integrations, "_load_bundle_workflow", _load_bundle_workflow)

    response = await integrations._call_bundle_mcp_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        request=_request(
            method="POST",
            path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/mcp/tools/list",
            body=b'{"jsonrpc":"2.0","id":"1","method":"tools/list"}',
            headers=[
                (b"content-type", b"application/json"),
                (b"authorization", b"Bearer mcp-token"),
                (b"cookie", b"bundle-auth=cookie-token"),
            ],
        ),
        endpoint_alias="tools",
        route="operations",
        mcp_path="list",
    )

    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["path"] == "/mcp/list"
    assert payload["method"] == "POST"
    assert payload["body"] == '{"jsonrpc":"2.0","id":"1","method":"tools/list"}'
    assert payload["authorization"] == "Bearer mcp-token"
    assert payload["cookie"] == "bundle-auth=cookie-token"


@pytest.mark.asyncio
async def test_call_bundle_mcp_inner_supports_public_mcp_endpoint(monkeypatch):
    async def _load_bundle_workflow(**kwargs):
        del kwargs
        return _DecoratedWorkflow(), SimpleNamespace(id="bundle.demo"), "tenant-a", "project-a"

    monkeypatch.setattr(integrations, "_load_bundle_workflow", _load_bundle_workflow)

    response = await integrations._call_bundle_mcp_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        request=_request(
            method="GET",
            path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/public/mcp/public_tools",
        ),
        endpoint_alias="public_tools",
        route="public",
        mcp_path="",
    )

    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["path"] == "/mcp"
    assert payload["method"] == "GET"


@pytest.mark.asyncio
async def test_call_bundle_mcp_route_delegates_without_proc_session(monkeypatch):
    captured: dict[str, object] = {}

    async def _call_bundle_mcp_limited(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(integrations, "_call_bundle_mcp_limited", _call_bundle_mcp_limited)

    request = _request(
        method="POST",
        path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/mcp/tools/list",
    )

    result = await integrations.call_bundle_mcp(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        endpoint_alias="tools",
        request=request,
        mcp_path="list",
    )

    assert result == {"ok": True}
    assert captured == {
        "tenant": "tenant-a",
        "project": "project-a",
        "bundle_id": "bundle.demo",
        "request": request,
        "endpoint_alias": "tools",
        "route": "operations",
        "mcp_path": "list",
    }


def test_build_mcp_request_session_is_anonymous_and_keeps_raw_request_headers():
    session = integrations._build_mcp_request_session(
        _request(
            method="POST",
            path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/mcp/tools",
            headers=[
                (b"authorization", b"Bearer external-mcp-token"),
                (b"x-id-token", b"opaque-id-token"),
                (b"x-user-timezone", b"Europe/Berlin"),
                (b"x-user-utc-offset", b"120"),
            ],
        )
    )

    assert session.user_type == UserType.ANONYMOUS
    assert session.request_context is not None
    assert session.request_context.authorization_header == "Bearer external-mcp-token"
    assert session.request_context.id_token == "opaque-id-token"
    assert session.request_context.user_timezone == "Europe/Berlin"
    assert session.request_context.user_utc_offset_min == 120


def test_visible_specs_require_intersection_of_user_types_and_raw_roles():
    class _VisibilityWorkflow:
        @api(alias="by_user_type", user_types=("registered",))
        async def by_user_type(self, **kwargs):
            return kwargs

        @api(alias="by_role", roles=("kdcube:role:super-admin",))
        async def by_role(self, **kwargs):
            return kwargs

        @api(alias="by_both", user_types=("privileged",), roles=("kdcube:role:super-admin",))
        async def by_both(self, **kwargs):
            return kwargs

        @ui_widget(
            icon="heroicons-outline:shield-check",
            alias="admin_widget",
            user_types=("privileged",),
            roles=("kdcube:role:super-admin",),
        )
        def admin_widget(self, **kwargs):
            return kwargs

        @mcp(
            alias="admin_mcp",
        )
        def admin_mcp(self, **kwargs):
            return _RecordingMCPProvider()

    manifest = discover_bundle_interface_manifest(_VisibilityWorkflow(), bundle_id="bundle.demo")

    visible_registered = integrations._visible_api_specs(manifest, _session(user_type="registered"))
    assert {spec.alias for spec in visible_registered} == {"by_user_type"}

    visible_admin = integrations._visible_api_specs(
        manifest,
        _session(user_type="privileged", roles=["kdcube:role:super-admin"]),
    )
    assert {spec.alias for spec in visible_admin} == {"by_user_type", "by_role", "by_both"}

    visible_privileged_no_role = integrations._visible_api_specs(
        manifest,
        _session(user_type="privileged"),
    )
    assert {spec.alias for spec in visible_privileged_no_role} == {"by_user_type"}

    visible_widget = integrations._visible_widget_specs(
        manifest,
        _session(user_type="privileged", roles=["kdcube:role:super-admin"]),
    )
    assert [spec.alias for spec in visible_widget] == ["admin_widget"]

    visible_mcp = integrations._visible_mcp_specs(
        manifest,
        _session(user_type="registered"),
    )
    assert [spec.alias for spec in visible_mcp] == ["admin_mcp"]


def test_mcp_rejects_proc_side_visibility_and_public_auth():
    with pytest.raises(ValueError):
        class _WorkflowWithUserTypes:
            @mcp(alias="bad", user_types=("registered",))
            def bad(self, **kwargs):
                return kwargs

    with pytest.raises(ValueError):
        class _WorkflowWithRoles:
            @mcp(alias="bad", roles=("kdcube:role:super-admin",))
            def bad(self, **kwargs):
                return kwargs

    with pytest.raises(ValueError):
        class _WorkflowWithPublicAuth:
            @mcp(alias="bad", route="public", public_auth="none")
            def bad(self, **kwargs):
                return kwargs


def test_user_type_visibility_uses_minimum_threshold_order():
    assert integrations._user_types_visible(("registered",), _session(user_type="registered")) is True
    assert integrations._user_types_visible(("registered",), _session(user_type="paid")) is True
    assert integrations._user_types_visible(("registered",), _session(user_type="privileged")) is True
    assert integrations._user_types_visible(("paid",), _session(user_type="registered")) is False
    assert integrations._user_types_visible(("paid",), _session(user_type="paid")) is True
    assert integrations._user_types_visible(("paid",), _session(user_type="privileged")) is True
    assert integrations._user_types_visible(("privileged",), _session(user_type="paid")) is False
    assert integrations._user_types_visible(("anonymous",), _session(user_type="registered")) is True
    assert integrations._user_types_visible(("registered", "privileged"), _session(user_type="paid")) is True


def test_visible_specs_apply_threshold_user_types():
    class _VisibilityWorkflow:
        @api(alias="reg_only", user_types=("registered",))
        async def reg_only(self, **kwargs):
            return kwargs

        @api(alias="paid_only", user_types=("paid",))
        async def paid_only(self, **kwargs):
            return kwargs

        @ui_widget(
            icon="heroicons-outline:shield-check",
            alias="paid_widget",
            user_types=("paid",),
        )
        def paid_widget(self, **kwargs):
            return kwargs

    manifest = discover_bundle_interface_manifest(_VisibilityWorkflow(), bundle_id="bundle.demo")

    visible_paid = integrations._visible_api_specs(manifest, _session(user_type="paid"))
    assert {spec.alias for spec in visible_paid} == {"reg_only", "paid_only"}

    visible_privileged = integrations._visible_api_specs(manifest, _session(user_type="privileged"))
    assert {spec.alias for spec in visible_privileged} == {"reg_only", "paid_only"}

    visible_registered = integrations._visible_api_specs(manifest, _session(user_type="registered"))
    assert {spec.alias for spec in visible_registered} == {"reg_only"}

    visible_widgets_paid = integrations._visible_widget_specs(manifest, _session(user_type="paid"))
    assert [spec.alias for spec in visible_widgets_paid] == ["paid_widget"]


def test_parse_bundle_request_payload_supports_multipart_files():
    captured: dict[str, object] = {}
    app = FastAPI()

    @app.post("/upload")
    async def upload(request: Request):
        payload, uploaded_files = await integrations._parse_bundle_request_payload(request)
        captured["payload"] = payload
        captured["uploaded_files"] = uploaded_files
        return {"ok": True}

    client = TestClient(app)
    response = client.post(
        "/upload",
        data={
            "payload": '{"conversation_id":"conv-1","data":{"project_code":"PRJ"}}',
        },
        files={
            "file": ("notes.txt", b"hello from rms", "text/plain"),
        },
    )

    assert response.status_code == 200
    payload = captured["payload"]
    uploaded_files = captured["uploaded_files"]
    assert isinstance(payload, integrations.BundleSuggestionsRequest)
    assert payload.conversation_id == "conv-1"
    assert payload.data == {"project_code": "PRJ"}
    assert len(uploaded_files) == 1
    assert uploaded_files[0].filename == "notes.txt"
    assert uploaded_files[0].content_type == "text/plain"
    assert uploaded_files[0].content == b"hello from rms"


def test_parse_bundle_request_payload_supports_raw_json_object():
    captured: dict[str, object] = {}
    app = FastAPI()

    @app.post("/bundle-op")
    async def bundle_op(request: Request):
        payload, uploaded_files = await integrations._parse_bundle_request_payload(request)
        captured["payload"] = payload
        captured["uploaded_files"] = uploaded_files
        return {"ok": True}

    client = TestClient(app)
    response = client.post(
        "/bundle-op",
        json={
            "project_code": "PRJ",
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    payload = captured["payload"]
    uploaded_files = captured["uploaded_files"]
    assert isinstance(payload, integrations.BundleSuggestionsRequest)
    assert payload.data == {"project_code": "PRJ", "dry_run": True}
    assert uploaded_files == []


def test_parse_bundle_request_payload_merges_reserved_fields_with_raw_json_object():
    captured: dict[str, object] = {}
    app = FastAPI()

    @app.post("/bundle-op")
    async def bundle_op(request: Request):
        payload, _uploaded_files = await integrations._parse_bundle_request_payload(request)
        captured["payload"] = payload
        return {"ok": True}

    client = TestClient(app)
    response = client.post(
        "/bundle-op",
        json={
            "conversation_id": "conv-1",
            "bundle_id": "bundle.demo",
            "project_code": "PRJ",
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    payload = captured["payload"]
    assert isinstance(payload, integrations.BundleSuggestionsRequest)
    assert payload.conversation_id == "conv-1"
    assert payload.bundle_id == "bundle.demo"
    assert payload.data == {"project_code": "PRJ", "dry_run": True}


@pytest.mark.asyncio
async def test_call_bundle_op_inner_passes_uploaded_files_and_returns_binary_response(monkeypatch):
    captured: dict[str, object] = {}

    class _Workflow:
        @api(method="POST", alias="download_bundle", route="operations")
        async def download_bundle(self, **kwargs):
            captured["kwargs"] = dict(kwargs)
            return BundleBinaryResponse(
                content=b"zip-bytes",
                filename="bundle.zip",
                media_type="application/zip",
            )

    async def _load_bundle_workflow(**kwargs):
        del kwargs
        return _Workflow(), SimpleNamespace(id="bundle.demo"), "tenant-a", "project-a"

    monkeypatch.setattr(integrations, "_load_bundle_workflow", _load_bundle_workflow)

    response = await integrations._call_bundle_op_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        payload=integrations.BundleSuggestionsRequest(data={"project_code": "PRJ"}),
        uploaded_files=[
            integrations.BundleUploadedFile(
                filename="archive.zip",
                content_type="application/zip",
                content=b"abc",
                field_name="file",
            )
        ],
        request=_request(
            method="POST",
            path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/operations/download_bundle",
        ),
        operation="download_bundle",
        route="operations",
        session=_session(),
    )

    assert response.status_code == 200
    assert response.media_type == "application/zip"
    assert response.body == b"zip-bytes"
    assert response.headers["content-disposition"] == 'attachment; filename="bundle.zip"'
    kwargs = captured["kwargs"]
    assert kwargs["project_code"] == "PRJ"
    assert len(kwargs["uploaded_files"]) == 1
    assert kwargs["uploaded_files"][0].filename == "archive.zip"


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
async def test_call_bundle_op_inner_logs_bundle_api_exceptions(monkeypatch):
    captured: dict[str, object] = {}

    class _Workflow:
        @api(method="POST", alias="explode", route="operations")
        async def explode(self, **kwargs):
            del kwargs
            raise ValueError("boom")

    async def _load_bundle_workflow(**kwargs):
        del kwargs
        return _Workflow(), SimpleNamespace(id="bundle.demo"), "tenant-a", "project-a"

    def _logger_exception(msg, *args, **kwargs):
        captured["msg"] = msg
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(integrations, "_load_bundle_workflow", _load_bundle_workflow)
    monkeypatch.setattr(integrations.logger, "exception", _logger_exception)

    with pytest.raises(integrations.HTTPException) as exc:
        await integrations._call_bundle_op_inner(
            tenant="tenant-a",
            project="project-a",
            bundle_id="bundle.demo",
            payload=integrations.BundleSuggestionsRequest(),
            request=_request(
                method="POST",
                path="/api/integrations/bundles/tenant-a/project-a/bundle.demo/operations/explode",
            ),
            operation="explode",
            route="operations",
            session=_session(),
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == "explode() failed: boom"
    assert captured["msg"] == (
        "Bundle operation failed tenant=%s project=%s bundle=%s route=%s method=%s operation=%s endpoint=%s"
    )
    assert captured["args"] == (
        "tenant-a",
        "project-a",
        "bundle.demo",
        "operations",
        "POST",
        "explode",
        "explode",
    )


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
