# SPDX-License-Identifier: MIT

from __future__ import annotations

from dataclasses import dataclass

from kdcube_ai_app.infra.plugin.agentic_loader import (
    APIEndpointSpec as CurrentAPIEndpointSpec,
    CRON_JOB_ATTR,
    CronJobSpec as CurrentCronJobSpec,
    MCP_ENDPOINT_ATTR,
    MCPEndpointSpec as CurrentMCPEndpointSpec,
    ON_JOB_ATTR,
    ON_MESSAGE_ATTR,
    PublicAPIAuthSpec as CurrentPublicAPIAuthSpec,
    UI_MAIN_ATTR,
    UI_WIDGET_ATTR,
    UIWidgetSpec as CurrentUIWidgetSpec,
    API_METHOD_ATTR,
    discover_bundle_interface_manifest,
)


@dataclass(frozen=True)
class APIEndpointSpec:
    method_name: str
    alias: str
    http_method: str = "POST"
    route: str = "operations"
    user_types: tuple[str, ...] = ()
    user_types_config: str | None = None
    roles: tuple[str, ...] = ()
    roles_config: str | None = None
    public_auth: "PublicAPIAuthSpec | None" = None


@dataclass(frozen=True)
class MCPEndpointSpec:
    method_name: str
    alias: str
    route: str = "operations"
    transport: str = "streamable-http"
    transport_config: str | None = None


@dataclass(frozen=True)
class PublicAPIAuthSpec:
    mode: str = "none"
    header: str | None = None
    secret_key: str | None = None


@dataclass(frozen=True)
class UIWidgetSpec:
    method_name: str
    alias: str
    icon: dict[str, str]
    user_types: tuple[str, ...] = ()
    user_types_config: str | None = None
    roles: tuple[str, ...] = ()
    roles_config: str | None = None


@dataclass(frozen=True)
class UIMainSpec:
    method_name: str


@dataclass(frozen=True)
class OnMessageSpec:
    method_name: str


@dataclass(frozen=True)
class OnJobSpec:
    method_name: str


@dataclass(frozen=True)
class CronJobSpec:
    method_name: str
    alias: str = ""
    cron_expression: str | None = None
    expr_config: str | None = None
    timezone: str | None = None
    tz_config: str | None = None
    span: str = "system"


class BundleWithReloadedDecoratorSpecs:
    def api_method(self):
        return None

    def mcp_method(self):
        return None

    def widget_method(self):
        return None

    def ui_main_method(self):
        return None

    def on_message_method(self):
        return None

    def on_job_method(self):
        return None

    def cron_method(self):
        return None


setattr(
    BundleWithReloadedDecoratorSpecs.api_method,
    API_METHOD_ATTR,
    APIEndpointSpec(
        method_name="old_api_method",
        alias="run_now",
        http_method="POST",
        route="operations",
        user_types=("registered",),
        roles=("admin",),
        public_auth=PublicAPIAuthSpec(mode="header", header="x-api-key", secret_key="b:api.key"),
    ),
)
setattr(
    BundleWithReloadedDecoratorSpecs.mcp_method,
    MCP_ENDPOINT_ATTR,
    MCPEndpointSpec(method_name="old_mcp_method", alias="tools", route="mcp", transport="streamable-http"),
)
setattr(
    BundleWithReloadedDecoratorSpecs.widget_method,
    UI_WIDGET_ATTR,
    UIWidgetSpec(
        method_name="old_widget_method",
        alias="versatile_webapp",
        icon={"name": "PanelsTopLeft"},
        user_types=("registered",),
        roles=("operator",),
    ),
)
setattr(BundleWithReloadedDecoratorSpecs.ui_main_method, UI_MAIN_ATTR, UIMainSpec(method_name="old_main"))
setattr(BundleWithReloadedDecoratorSpecs.on_message_method, ON_MESSAGE_ATTR, OnMessageSpec(method_name="old_message"))
setattr(BundleWithReloadedDecoratorSpecs.on_job_method, ON_JOB_ATTR, OnJobSpec(method_name="old_job"))
setattr(
    BundleWithReloadedDecoratorSpecs.cron_method,
    CRON_JOB_ATTR,
    CronJobSpec(method_name="old_cron", alias="daily", cron_expression="0 8 * * *", span="project"),
)


def test_manifest_discovery_accepts_reloaded_decorator_dataclasses():
    manifest = discover_bundle_interface_manifest(BundleWithReloadedDecoratorSpecs, bundle_id="demo")

    assert manifest.bundle_id == "demo"

    assert len(manifest.api_endpoints) == 1
    assert isinstance(manifest.api_endpoints[0], CurrentAPIEndpointSpec)
    assert manifest.api_endpoints[0].method_name == "api_method"
    assert manifest.api_endpoints[0].alias == "run_now"
    assert manifest.api_endpoints[0].user_types == ("registered",)
    assert manifest.api_endpoints[0].roles == ("admin",)
    assert isinstance(manifest.api_endpoints[0].public_auth, CurrentPublicAPIAuthSpec)
    assert manifest.api_endpoints[0].public_auth.header == "x-api-key"

    assert len(manifest.mcp_endpoints) == 1
    assert isinstance(manifest.mcp_endpoints[0], CurrentMCPEndpointSpec)
    assert manifest.mcp_endpoints[0].method_name == "mcp_method"

    assert len(manifest.ui_widgets) == 1
    assert isinstance(manifest.ui_widgets[0], CurrentUIWidgetSpec)
    assert manifest.ui_widgets[0].method_name == "widget_method"
    assert manifest.ui_widgets[0].alias == "versatile_webapp"
    assert manifest.ui_widgets[0].icon == {"name": "PanelsTopLeft"}

    assert manifest.ui_main and manifest.ui_main.method_name == "ui_main_method"
    assert manifest.on_message and manifest.on_message.method_name == "on_message_method"
    assert manifest.on_job and manifest.on_job.method_name == "on_job_method"

    assert len(manifest.scheduled_jobs) == 1
    assert isinstance(manifest.scheduled_jobs[0], CurrentCronJobSpec)
    assert manifest.scheduled_jobs[0].method_name == "cron_method"
    assert manifest.scheduled_jobs[0].alias == "daily"
