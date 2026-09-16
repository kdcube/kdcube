"""Operations-route authorization contract.

Bundle operation dispatch (``/bundles/.../operations/{operation}``) admits any
authenticated registered user at the HTTP layer. Role requirements come only
from the operation's declared visibility:

- visibility not declared -> no role restriction (any registered user);
- visibility declared -> enforced exactly as declared.

Bundle administration endpoints (props, secrets, registry management) keep the
super-admin requirement.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers

from kdcube_ai_app.apps.chat.proc.rest.integrations import integrations
from kdcube_ai_app.apps.chat.proc.rest.integrations import operation_csrf
from kdcube_ai_app.auth.AuthManager import RequirementBase, RequireRoles
from kdcube_ai_app.infra.plugin.bundle_loader import APIEndpointSpec, api

SUPER_ADMIN_ROLE = "kdcube:role:super-admin"

OPERATIONS_DISPATCH_ENDPOINTS = (
    integrations.call_bundle_op,
    integrations.call_bundle_op_default,
    integrations.call_bundle_op_get,
)

ADMIN_ENDPOINTS = (
    integrations.set_bundle_props,
    integrations.set_bundle_secrets,
    integrations.admin_set_bundles,
)


def _dependency_requirements(endpoint) -> list[RequirementBase]:
    """Extract the auth requirements captured by the endpoint's session dependency."""
    dep = inspect.signature(endpoint).parameters["session"].default.dependency
    for cell in dep.__closure__ or ():
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        if (
            isinstance(value, list)
            and value
            and all(isinstance(item, RequirementBase) for item in value)
        ):
            return value
    return []


def _user(roles: list[str]) -> SimpleNamespace:
    return SimpleNamespace(user_type="registered", roles=list(roles), permissions=[])


# ---------------------------------------------------------------------------
# HTTP dependency layer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", OPERATIONS_DISPATCH_ENDPOINTS)
def test_operations_dispatch_admits_registered_non_admin(endpoint):
    reqs = _dependency_requirements(endpoint)
    assert reqs, "operations dispatch must declare explicit auth requirements"

    # No injected role requirement: undeclared op visibility means no
    # restriction, so the dependency must not manufacture one.
    assert not any(
        isinstance(req, RequireRoles) and SUPER_ADMIN_ROLE in req.roles
        for req in reqs
    ), "operations dispatch must not require super-admin"

    member = _user(["kdcube:role:member"])
    for req in reqs:
        assert req.validate_requirement(member) is None


@pytest.mark.parametrize("endpoint", OPERATIONS_DISPATCH_ENDPOINTS)
def test_operations_dispatch_rejects_anonymous(endpoint):
    reqs = _dependency_requirements(endpoint)
    anonymous = SimpleNamespace(user_type="anonymous", roles=[], permissions=[])
    assert any(req.validate_requirement(anonymous) is not None for req in reqs)


@pytest.mark.parametrize("endpoint", ADMIN_ENDPOINTS)
def test_bundle_admin_endpoints_stay_super_admin_gated(endpoint):
    reqs = _dependency_requirements(endpoint)
    assert any(
        isinstance(req, RequireRoles) and SUPER_ADMIN_ROLE in req.roles
        for req in reqs
    ), "bundle administration endpoints must keep the super-admin requirement"

    member = _user(["kdcube:role:member"])
    assert any(req.validate_requirement(member) is not None for req in reqs)


def test_api_dashboard_descriptor_exposes_effective_csrf_override():
    spec = APIEndpointSpec(
        method_name="account_disconnect",
        alias="account_disconnect",
        http_method="POST",
        route="operations",
        csrf=True,
    )
    props = {
        "surfaces": {
            "as_provider": {
                "api": {
                    "operations": {
                        "account_disconnect": {
                            "POST": {"csrf": False},
                        },
                    },
                },
            },
        },
    }

    descriptor = integrations._api_spec_descriptor(
        spec,
        props,
        bundle_id="example@1-0",
    )

    assert descriptor["csrf"] is False
    assert descriptor["csrf_default"] is True
    assert descriptor["csrf_overridden"] is True
    assert descriptor["csrf_path"] == (
        "surfaces.as_provider.api.operations.account_disconnect.POST.csrf"
    )


def test_api_dashboard_descriptor_reports_explicit_default_value_as_override():
    spec = APIEndpointSpec(
        method_name="account_disconnect",
        alias="account_disconnect",
        http_method="POST",
        route="operations",
        csrf=True,
    )
    props = {
        "surfaces": {
            "as_provider": {
                "api": {
                    "operations": {
                        "account_disconnect": {
                            "POST": {"csrf": True},
                        },
                    },
                },
            },
        },
    }

    descriptor = integrations._api_spec_descriptor(
        spec,
        props,
        bundle_id="example@1-0",
    )

    assert descriptor["csrf"] is True
    assert descriptor["csrf_default"] is True
    assert descriptor["csrf_overridden"] is True


def test_api_dashboard_csrf_reset_marker_restores_decorator_default():
    spec = APIEndpointSpec(
        method_name="account_disconnect",
        alias="account_disconnect",
        http_method="POST",
        route="operations",
        csrf=False,
    )
    props = {
        "surfaces": {
            "as_provider": {
                "api": {
                    "operations": {
                        "account_disconnect": {
                            "csrf": True,
                            "POST": {"csrf": None},
                        },
                    },
                },
            },
        },
    }

    descriptor = integrations._api_spec_descriptor(
        spec,
        props,
        bundle_id="example@1-0",
    )

    assert descriptor["csrf"] is False
    assert descriptor["csrf_default"] is False
    assert descriptor["csrf_overridden"] is False


# ---------------------------------------------------------------------------
# Dispatch layer: declared visibility is the only source of role requirements
# ---------------------------------------------------------------------------


def _session(roles: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        session_id="session-1",
        user_type=SimpleNamespace(value="registered"),
        user_id="user-1",
        username="user",
        email=None,
        fingerprint="fp-1",
        roles=list(roles),
        permissions=["chat.use"],
        request_context=SimpleNamespace(user_timezone="UTC", user_utc_offset_min=0),
    )


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def setex(self, key: str, _ttl: int, value: str) -> bool:
        self.values[key] = value
        return True

    async def eval(self, _script: str, _numkeys: int, key: str):
        return self.values.pop(key, None)


def _request(
    *,
    redis=None,
    cookie: bool = False,
    csrf_token: str = "",
    authorization: str = "",
) -> SimpleNamespace:
    headers: dict[str, str] = {}
    if csrf_token:
        headers[operation_csrf.OPERATION_CSRF_HEADER] = csrf_token
    if authorization:
        headers["Authorization"] = authorization
    return SimpleNamespace(
        method="POST",
        headers=Headers(headers),
        cookies={"__Secure-LATC": "ambient-session"} if cookie else {},
        state=SimpleNamespace(),
        app=SimpleNamespace(
            state=SimpleNamespace(
                redis_async=redis or _Redis(),
                pg_pool=object(),
            )
        ),
    )


class _Workflow:
    """Per-user ops (visibility undeclared) plus one declared-admin op."""

    @api(alias="canvas_read")
    async def canvas_read(self, **kwargs):
        return {"ok": "canvas_read"}

    @api(alias="memories_widget_data")
    async def memories_widget_data(self, **kwargs):
        return {"ok": "memories_widget_data"}

    @api(alias="telegram_user_admin_data", roles=(SUPER_ADMIN_ROLE,))
    async def telegram_user_admin_data(self, **kwargs):
        return {"ok": "telegram_user_admin_data"}

    @api(alias="delegated_access_create", csrf=True)
    async def delegated_access_create(self, **kwargs):
        return {"ok": "delegated_access_create"}

    @api(alias="agent_selection_update")
    async def agent_selection_update(self, **kwargs):
        return {"ok": "agent_selection_update"}


def _patch_bundle_runtime(monkeypatch):
    async def _resolve_bundle_async(*args, **kwargs):
        bundle_id = kwargs.get("bundle_id") or (args[0] if args else None)
        return SimpleNamespace(id=bundle_id, path="/tmp/demo", module="entrypoint", singleton=False)

    def _create_workflow_config(_cfg_req):
        return SimpleNamespace(ai_bundle_spec=None)

    async def _get_workflow_instance(spec, wf_config, comm_context=None, redis=None, pg_pool=None):
        del spec, wf_config, comm_context, redis, pg_pool
        return _Workflow(), None

    monkeypatch.setattr(
        integrations,
        "get_settings",
        lambda: SimpleNamespace(
            OPENAI_API_KEY=None,
            ANTHROPIC_API_KEY=None,
            TENANT="tenant-a",
            PROJECT="project-a",
        ),
    )
    monkeypatch.setattr(
        operation_csrf,
        "get_settings",
        lambda: SimpleNamespace(
            AUTH=SimpleNamespace(
                ID_TOKEN_HEADER_NAME="X-ID-Token",
                AUTH_TOKEN_COOKIE_NAME="__Secure-LATC",
                ID_TOKEN_COOKIE_NAME="__Secure-LITC",
                MASQUERADED_TOKEN_COOKIE_NAME="__Secure-LMTC",
            )
        ),
    )
    monkeypatch.setattr(integrations, "_resolve_bundle_spec_from_runtime", _resolve_bundle_async)
    monkeypatch.setattr(integrations, "create_workflow_config", _create_workflow_config)
    monkeypatch.setattr(integrations, "get_workflow_instance_async", _get_workflow_instance)
    monkeypatch.setattr(
        integrations, "store_get_bundle_props_from_authority", lambda **kwargs: {}
    )


async def _dispatch(
    operation: str,
    session: SimpleNamespace,
    *,
    request=None,
    internal_peer_call: bool = False,
):
    return await integrations._call_bundle_op_inner(
        tenant="tenant-a",
        project="project-a",
        bundle_id=None,
        payload=integrations.BundleSuggestionsRequest(bundle_id="bundle.demo", data={}),
        request=request or _request(),
        operation=operation,
        route="operations",
        session=session,
        _internal_peer_call=internal_peer_call,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["canvas_read", "memories_widget_data"])
async def test_undeclared_visibility_op_open_to_registered_user(monkeypatch, operation):
    _patch_bundle_runtime(monkeypatch)
    result = await _dispatch(operation, _session(["kdcube:role:member"]))
    assert result[operation] == {"ok": operation}


@pytest.mark.asyncio
async def test_declared_super_admin_op_denies_non_admin(monkeypatch):
    _patch_bundle_runtime(monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        await _dispatch("telegram_user_admin_data", _session(["kdcube:role:member"]))
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_declared_super_admin_op_admits_super_admin(monkeypatch):
    _patch_bundle_runtime(monkeypatch)
    result = await _dispatch("telegram_user_admin_data", _session([SUPER_ADMIN_ROLE]))
    assert result["telegram_user_admin_data"] == {"ok": "telegram_user_admin_data"}


@pytest.mark.asyncio
async def test_cookie_authenticated_protected_operation_requires_single_use_csrf(monkeypatch):
    _patch_bundle_runtime(monkeypatch)
    redis = _Redis()
    session = _session(["kdcube:role:member"])
    token_response = await integrations._bundle_operation_csrf_token(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        operation="delegated_access_create",
        request=_request(redis=redis, cookie=True),
        session=session,
    )
    token_payload = json.loads(token_response.body)
    token = token_payload["csrf_token"]
    request = _request(redis=redis, cookie=True, csrf_token=token)

    result = await _dispatch(
        "delegated_access_create",
        session,
        request=request,
    )
    assert result["delegated_access_create"] == {"ok": "delegated_access_create"}

    with pytest.raises(HTTPException) as replay:
        await _dispatch(
            "delegated_access_create",
            session,
            request=request,
        )
    assert replay.value.status_code == 403


@pytest.mark.asyncio
async def test_protected_operation_rejects_missing_csrf_for_cookie_auth(monkeypatch):
    _patch_bundle_runtime(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        await _dispatch(
            "delegated_access_create",
            _session(["kdcube:role:member"]),
            request=_request(cookie=True),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_cookie_authenticated_unprotected_post_does_not_require_csrf(monkeypatch):
    """Regression: generic bundle POSTs remain unchanged unless they opt in."""
    _patch_bundle_runtime(monkeypatch)
    session = _session(["kdcube:role:member"])
    request = _request(cookie=True)

    token_response = await integrations._bundle_operation_csrf_token(
        tenant="tenant-a",
        project="project-a",
        bundle_id="bundle.demo",
        operation="agent_selection_update",
        request=request,
        session=session,
    )
    token_payload = json.loads(token_response.body)
    result = await _dispatch(
        "agent_selection_update",
        session,
        request=request,
    )

    assert token_payload == {
        "csrf_required": False,
        "bundle_id": "bundle.demo",
        "operation": "agent_selection_update",
    }
    assert result["agent_selection_update"] == {"ok": "agent_selection_update"}


@pytest.mark.asyncio
async def test_protected_operation_keeps_non_ambient_and_internal_callers_compatible(monkeypatch):
    _patch_bundle_runtime(monkeypatch)
    session = _session(["kdcube:role:member"])

    bearer_result = await _dispatch(
        "delegated_access_create",
        session,
        request=_request(cookie=True, authorization="Bearer explicit"),
    )
    peer_result = await _dispatch(
        "delegated_access_create",
        session,
        request=_request(cookie=True),
        internal_peer_call=True,
    )

    assert bearer_result["delegated_access_create"]["ok"] == "delegated_access_create"
    assert peer_result["delegated_access_create"]["ok"] == "delegated_access_create"
