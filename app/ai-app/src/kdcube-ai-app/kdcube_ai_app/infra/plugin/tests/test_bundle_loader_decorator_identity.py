# SPDX-License-Identifier: MIT

from __future__ import annotations

from dataclasses import dataclass

import pytest

from kdcube_ai_app.infra.plugin.bundle_loader import (
    APIEndpointSpec as CurrentAPIEndpointSpec,
    AUTHORITY_PROVIDER_ATTR,
    CRON_JOB_ATTR,
    AuthorityProviderDeclarationSpec as CurrentAuthorityProviderDeclarationSpec,
    CronJobSpec as CurrentCronJobSpec,
    MCP_ENDPOINT_ATTR,
    MCPEndpointSpec as CurrentMCPEndpointSpec,
    ON_JOB_ATTR,
    ON_MESSAGE_ATTR,
    UI_MAIN_ATTR,
    UI_WIDGET_ATTR,
    UIWidgetSpec as CurrentUIWidgetSpec,
    API_METHOD_ATTR,
    api,
    data_bus_handler,
    discover_bundle_interface_manifest,
)
from kdcube_ai_app.apps.chat.sdk.application_operations import (
    APPLICATION_OPERATION_POLICY_PROPERTY,
    APPLICATION_OPERATION_POLICY_SCHEMA_V2,
    ApplicationOperationPolicyError,
    api_application_operation_id,
    api_application_operation_ref,
    application_operation_policy,
    application_operation_policy_enabled,
    application_operation_ref,
    data_bus_application_operation_ref,
    delegated_application_operation_role,
    delegated_application_operation_selection,
    parse_application_operation_ref,
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


@dataclass(frozen=True)
class MCPEndpointSpec:
    method_name: str
    alias: str
    route: str = "operations"
    transport: str = "streamable-http"
    transport_config: str | None = None


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


@dataclass(frozen=True)
class AuthorityProviderDeclarationSpec:
    method_name: str
    authority_id: str
    authenticator_id: str = ""
    credential_kinds: tuple[str, ...] = ()
    audiences: tuple[str, ...] = ()
    label: str = ""
    transports: tuple[str, ...] = ("local",)


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

    def authority_provider_method(self):
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
        alias="workspace_webapp",
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
setattr(
    BundleWithReloadedDecoratorSpecs.authority_provider_method,
    AUTHORITY_PROVIDER_ATTR,
    AuthorityProviderDeclarationSpec(
        method_name="old_authority",
        authority_id="custom.identity",
        authenticator_id="custom.identity.oauth",
        credential_kinds=("authority_access",),
        audiences=("bundle:custom-app@1-0",),
        label="Custom Identity",
    ),
)


def test_manifest_discovery_accepts_reloaded_decorator_dataclasses():
    manifest = discover_bundle_interface_manifest(BundleWithReloadedDecoratorSpecs, bundle_id="demo")

    assert manifest.bundle_id == "demo"

    assert len(manifest.api_endpoints) == 1
    assert isinstance(manifest.api_endpoints[0], CurrentAPIEndpointSpec)
    assert manifest.api_endpoints[0].method_name == "api_method"
    assert manifest.api_endpoints[0].alias == "run_now"
    assert manifest.api_endpoints[0].csrf is False
    assert manifest.api_endpoints[0].user_types == ("registered",)
    assert manifest.api_endpoints[0].roles == ("admin",)

    assert len(manifest.mcp_endpoints) == 1
    assert isinstance(manifest.mcp_endpoints[0], CurrentMCPEndpointSpec)
    assert manifest.mcp_endpoints[0].method_name == "mcp_method"

    assert len(manifest.ui_widgets) == 1
    assert isinstance(manifest.ui_widgets[0], CurrentUIWidgetSpec)
    assert manifest.ui_widgets[0].method_name == "widget_method"
    assert manifest.ui_widgets[0].alias == "workspace_webapp"
    assert manifest.ui_widgets[0].icon == {"name": "PanelsTopLeft"}

    assert manifest.ui_main and manifest.ui_main.method_name == "ui_main_method"
    assert manifest.on_message and manifest.on_message.method_name == "on_message_method"
    assert manifest.on_job and manifest.on_job.method_name == "on_job_method"

    assert len(manifest.scheduled_jobs) == 1
    assert isinstance(manifest.scheduled_jobs[0], CurrentCronJobSpec)
    assert manifest.scheduled_jobs[0].method_name == "cron_method"
    assert manifest.scheduled_jobs[0].alias == "daily"

    assert len(manifest.authority_providers) == 1
    assert isinstance(manifest.authority_providers[0], CurrentAuthorityProviderDeclarationSpec)
    assert manifest.authority_providers[0].method_name == "authority_provider_method"
    assert manifest.authority_providers[0].authority_id == "custom.identity"
    assert manifest.authority_providers[0].authenticator_id == "custom.identity.oauth"


def test_api_decorator_preserves_csrf_metadata_and_limits_it_to_operations_post():
    @api(alias="grant_create", csrf=True)
    async def grant_create():
        return None

    spec = getattr(grant_create, API_METHOD_ATTR)
    assert spec.csrf is True
    assert spec.http_method == "POST"
    assert spec.route == "operations"

    with pytest.raises(ValueError, match="POST on the operations route"):
        api(alias="bad_get", method="GET", csrf=True)
    with pytest.raises(ValueError, match="POST on the operations route"):
        api(alias="bad_public", route="public", csrf=True)


def test_application_operation_reference_is_app_scoped_and_round_trips():
    operation_id, explicit = api_application_operation_id(
        alias="same_alias",
        method="POST",
        route="operations",
    )
    assert operation_id == "api.operations.post.same_alias"
    assert explicit is False

    first = application_operation_ref(
        application_id="first@1-0",
        operation_id=operation_id,
    )
    second = application_operation_ref(
        application_id="second@1-0",
        operation_id=operation_id,
    )
    assert first != second
    assert parse_application_operation_ref(first) == (
        "first@1-0",
        operation_id,
    )


def test_explicit_operation_id_can_join_api_and_data_bus_exposures():
    class SharedOperationBundle:
        @api(alias="publish_report", operation_id="report.publish")
        async def publish_report(self):
            return None

        @data_bus_handler(
            subject="report.publish.requested",
            operation_id="report.publish",
        )
        async def publish_report_from_bus(self):
            return None

    manifest = discover_bundle_interface_manifest(
        SharedOperationBundle,
        bundle_id="reports@1-0",
    )
    api_spec = manifest.api_endpoints[0]
    handler_spec = manifest.data_bus_handlers[0]

    assert api_spec.operation_id == "report.publish"
    assert api_spec.operation_id_explicit is True
    assert handler_spec.operation_id == "report.publish"
    assert handler_spec.operation_id_explicit is True
    assert application_operation_ref(
        application_id=manifest.bundle_id,
        operation_id=api_spec.operation_id,
    ) == application_operation_ref(
        application_id=manifest.bundle_id,
        operation_id=handler_spec.operation_id,
    )
    assert api_application_operation_ref(
        application_id=manifest.bundle_id,
        alias=api_spec.alias,
        method=api_spec.http_method,
        route=api_spec.route,
        operation_id=api_spec.operation_id,
    ) == data_bus_application_operation_ref(
        application_id=manifest.bundle_id,
        subject=handler_spec.subject,
        operation_id=handler_spec.operation_id,
    )


def test_implicit_operation_ids_keep_api_exposures_distinct():
    class DistinctExposuresBundle:
        @api(alias="status", method="GET", route="public")
        async def public_status(self):
            return None

        @api(alias="status", method="POST", route="operations")
        async def private_status(self):
            return None

    manifest = discover_bundle_interface_manifest(
        DistinctExposuresBundle,
        bundle_id="status@1-0",
    )
    operation_ids = {spec.operation_id for spec in manifest.api_endpoints}
    assert operation_ids == {
        "api.operations.post.status",
        "api.public.get.status",
    }


def test_application_operation_selection_distinguishes_absent_and_empty_rows():
    binding = {"access_id": "card-a"}
    policy = {
        APPLICATION_OPERATION_POLICY_PROPERTY: application_operation_policy(),
    }

    assert delegated_application_operation_selection({}) is None
    assert delegated_application_operation_selection(
        {"delegated_card_binding": binding, "resource_operations": {}}
    ) is None
    assert delegated_application_operation_selection(
        {
            "delegated_card_binding": binding,
            "resource_operations": {"*": []},
        }
    ) is None
    assert delegated_application_operation_selection(
        {
            **policy,
            "delegated_card_binding": binding,
            "resource_grants": {"*": ["kdcube:role:registered"]},
            "resource_operations": {"*": []},
        }
    ) == frozenset()
    assert delegated_application_operation_selection(
        {
            **policy,
            "delegated_card_binding": binding,
            "resource_grants": {"*": ["kdcube:role:registered"]},
            "resource_operations": {
                "*": [
                    "urn:kdcube:application-operation:"
                    "reports%401-0:report.publish"
                ]
            },
        }
    ) == frozenset(
        {
            "urn:kdcube:application-operation:"
            "reports%401-0:report.publish"
        }
    )
    assert application_operation_policy_enabled(policy) is True
    assert application_operation_policy_enabled({}) is False


def test_application_operation_role_is_resolved_per_selected_operation():
    publish = application_operation_ref(
        application_id="reports@1-0",
        operation_id="report.publish",
    )
    read = application_operation_ref(
        application_id="reports@1-0",
        operation_id="report.read",
    )
    authority = {
        APPLICATION_OPERATION_POLICY_PROPERTY: {
            "schema": APPLICATION_OPERATION_POLICY_SCHEMA_V2,
            "mode": "selected",
            "default_role": "kdcube:role:registered",
            "operation_roles": {publish: "kdcube:role:super-admin"},
        },
        "delegated_card_binding": {"access_id": "card-a"},
        "resource_grants": {"*": ["kdcube:role:registered"]},
        "resource_operations": {"*": [publish, read]},
    }

    assert delegated_application_operation_role(
        authority,
        operation_ref=publish,
    ) == "kdcube:role:super-admin"
    assert delegated_application_operation_role(
        authority,
        operation_ref=read,
    ) == "kdcube:role:registered"


def test_application_operation_role_policy_rejects_stale_overrides():
    selected = application_operation_ref(
        application_id="reports@1-0",
        operation_id="report.read",
    )
    removed = application_operation_ref(
        application_id="reports@1-0",
        operation_id="report.removed",
    )
    authority = {
        APPLICATION_OPERATION_POLICY_PROPERTY: {
            "schema": APPLICATION_OPERATION_POLICY_SCHEMA_V2,
            "mode": "selected",
            "default_role": "kdcube:role:registered",
            "operation_roles": {removed: "kdcube:role:super-admin"},
        },
        "delegated_card_binding": {"access_id": "card-a"},
        "resource_grants": {"*": ["kdcube:role:registered"]},
        "resource_operations": {"*": [selected]},
    }

    with pytest.raises(ApplicationOperationPolicyError) as exc_info:
        delegated_application_operation_selection(authority)

    assert exc_info.value.reason == "application_operation_override_not_selected"
