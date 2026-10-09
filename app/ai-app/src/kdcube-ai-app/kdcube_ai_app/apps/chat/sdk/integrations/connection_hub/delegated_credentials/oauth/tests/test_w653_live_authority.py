"""W653: the delegated session's authority is the live Card's, call by call (W502 live authority).

Each test keeps ONE delivered session (the same bearer and its stored grant record) and changes only the
live Card between calls, through the real managed MCP guard. identity_scope (whose identities' data the
session reaches) and the record's top-level resource_grants must follow the live Card like every other Card
fact; a record without a registry pointer (legacy) keeps its stored values. The grantor's consent-time IdP
ceiling (grantor_authority) is pinned as deny-only; the live ceiling is the grantor's Control Card.
"""
from __future__ import annotations

import dataclasses
import json

from fastapi import Request
from fastapi.responses import JSONResponse

from connection_hub.delegated_credentials.cards.model import (
    CARD_KIND_CONTROL,
    CardAuthority,
    ControlCardBinding,
    NamedServiceSelection,
)
from connection_hub.delegated_credentials.controls.snapshot import materialize_control_snapshot
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.automation_access import (
    card_authority_from_record,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth import surface_guard
from connection_hub.hub.resolver import IDENTITY_SCOPE_GRANTOR, IDENTITY_SCOPE_GRANTOR_FAMILY
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.tests.test_surface_guard import (  # noqa: F401
    _projections_swept,  # the same autouse precondition: this run's Card projections are swept
    GUARD_RESOURCE,
    _Redis,
    _authority,
    _client,
    _live_card,
    _rpc_tool_call,
    _store_card_authority,
    _store_live_card,
)

OTHER_RESOURCE = "https://other.example.test/mcp"
BEARER = {"Authorization": "Bearer reader"}


def _session(monkeypatch, redis, grant_record):
    """One delivered session through the real managed MCP guard at the granted resource. The route answers the
    runtime projection and the grant record the guard resolved for this call."""
    client = _client(monkeypatch, grant_record=grant_record, redis=redis)
    client.app.router.routes[:] = [r for r in client.app.router.routes if getattr(r, "path", "") != "/guard"]

    @client.app.post("/guard")
    async def guard(request: Request):
        denial = await surface_guard.authorize_delegated_mcp_request(
            request=request, body=await request.body(), auth={
                "mode": "managed", "authority_id": "delegated_client",
                "tools": {"records_export": {"grants": ["records:read"]}}, "selected_tool_grants": True})
        if denial is not None:
            return denial
        resolved = request.state.delegated_credential["grant_record"]
        return JSONResponse({"ok": True, "projection": surface_guard.delegated_mcp_runtime_projection(request),
                             "record": {"resource_grants": resolved.get("resource_grants"),
                                        "resource": resolved.get("resource"),
                                        "credential_attrs": resolved["credential"]["attrs"]}})

    return client


def _projection(client):
    response = client.post("/guard", json=_rpc_tool_call(), headers=BEARER)
    assert response.status_code == 200, response.text
    return response.json()


def _put(redis, card):
    _store_card_authority(redis, card_authority_from_record(card))


def _stored(identity_scope="grantor", **extra):
    """The stored grant record of the delivered session: a registry pointer plus the issuance-time facts."""
    credential = _authority(identity_scope=identity_scope)
    return {"registry_access_id": "oauth-access-1", "operations": ["records_export"], "credential": credential,
            **extra}


def _refused(response) -> bool:
    """The guard refused this call: an HTTP refusal, or a tool-level error with no projection."""
    if response.status_code in (401, 403):
        return True
    body = response.json()
    return response.status_code == 200 and "projection" not in body and body.get("ok") is not True


# identity_scope: live Card authority -------------------------------------------------------------------------

def test_a_narrowed_identity_scope_applies_to_the_same_sessions_next_call(monkeypatch):
    redis = _Redis()
    _put(redis, dataclasses.replace(_live_card(), identity_scope="grantor_family"))
    client = _session(monkeypatch, redis, _stored(identity_scope="grantor_family"))
    assert _projection(client)["projection"]["identity_scope"] == IDENTITY_SCOPE_GRANTOR_FAMILY

    _put(redis, dataclasses.replace(_live_card(), identity_scope="grantor"))  # the Card narrowed
    projection = _projection(client)["projection"]
    # Both the runtime field and the identity_authority the proc bridge hands to the session (and every nested
    # app/named-service call) carry the narrowed scope: the family is no longer reachable from this session.
    assert projection["identity_scope"] == IDENTITY_SCOPE_GRANTOR
    assert projection["identity_authority"]["identity_scope"] == IDENTITY_SCOPE_GRANTOR


def test_an_unchanged_identity_scope_is_kept(monkeypatch):
    redis = _Redis()
    _put(redis, dataclasses.replace(_live_card(), identity_scope="grantor_family"))
    client = _session(monkeypatch, redis, _stored(identity_scope="grantor_family"))
    assert _projection(client)["projection"]["identity_scope"] == IDENTITY_SCOPE_GRANTOR_FAMILY
    assert _projection(client)["projection"]["identity_scope"] == IDENTITY_SCOPE_GRANTOR_FAMILY


def test_a_live_card_without_identity_scope_never_keeps_the_stored_wider_value(monkeypatch):
    redis = _Redis()
    _put(redis, dataclasses.replace(_live_card(), identity_scope=""))
    client = _session(monkeypatch, redis, _stored(identity_scope="grantor_family"))
    assert _projection(client)["projection"]["identity_scope"] == IDENTITY_SCOPE_GRANTOR


def test_a_legacy_record_without_a_registry_pointer_keeps_its_stored_identity_scope(monkeypatch):
    redis = _Redis()
    legacy = {"operations": ["records_export"], "credential": _authority(identity_scope="grantor_family")}
    client = _session(monkeypatch, redis, legacy)
    assert _projection(client)["projection"]["identity_scope"] == IDENTITY_SCOPE_GRANTOR_FAMILY


# The record's top-level resource_grants / resource (W652 review: one source for every reader) ------------------

def test_the_resolved_record_carries_the_live_resource_map_not_the_issuance_time_one(monkeypatch):
    redis = _Redis()
    _put(redis, _live_card(resource_grants={GUARD_RESOURCE: ("records:read",), OTHER_RESOURCE: ("records:read",)}))
    stored = _stored(resource_grants={GUARD_RESOURCE: ["records:read"], OTHER_RESOURCE: ["records:read"]},
                     resource=OTHER_RESOURCE)
    stored["credential"]["attrs"]["resource"] = OTHER_RESOURCE  # the single issuance-time resource
    client = _session(monkeypatch, redis, stored)
    first = _projection(client)["record"]
    assert set(first["resource_grants"]) == {GUARD_RESOURCE, OTHER_RESOURCE}

    _put(redis, _live_card(resource_grants={GUARD_RESOURCE: ("records:read",)}))  # OTHER removed from the Card
    second = _projection(client)["record"]
    assert second["resource_grants"] == {GUARD_RESOURCE: ["records:read"]}
    assert second["resource"] is None and "resource" not in second["credential_attrs"]


def test_a_live_card_with_an_empty_map_resolves_to_no_resource(monkeypatch):
    redis = _Redis()
    _put(redis, _live_card(resource_grants={}))
    stored = _stored(resource_grants={GUARD_RESOURCE: ["records:read"]}, resource=GUARD_RESOURCE)
    stored["credential"]["attrs"]["resource"] = GUARD_RESOURCE
    client = _session(monkeypatch, redis, stored)
    assert _refused(client.post("/guard", json=_rpc_tool_call(), headers=BEARER))


def test_a_legacy_record_keeps_its_stored_resource(monkeypatch):
    redis = _Redis()
    legacy = {"operations": ["records_export"], "credential": _authority(), "resource": GUARD_RESOURCE}
    client = _session(monkeypatch, redis, legacy)
    record = _projection(client)["record"]
    assert record["resource"] == GUARD_RESOURCE


# Same session, Card or Control narrowed or revoked: pins of the existing live enforcement --------------------------

def test_the_same_session_is_admitted_while_its_card_grants(monkeypatch):
    redis = _Redis()
    _put(redis, _live_card())
    client = _session(monkeypatch, redis, _stored())
    assert "projection" in _projection(client)


def test_the_same_session_is_denied_once_its_card_narrows(monkeypatch):
    redis = _Redis()
    _put(redis, _live_card())
    client = _session(monkeypatch, redis, _stored())
    assert "projection" in _projection(client)
    _put(redis, _live_card(resource_grants={GUARD_RESOURCE: ("records:write",)}))
    assert _refused(client.post("/guard", json=_rpc_tool_call(), headers=BEARER))


def test_the_same_session_is_denied_once_its_card_is_revoked(monkeypatch):
    redis = _Redis()
    card = _live_card()
    _put(redis, card)
    client = _session(monkeypatch, redis, _stored())
    assert "projection" in _projection(client)
    redis.values.clear()  # the Card is gone (no durable store configured: absent means revoked)
    response = client.post("/guard", json=_rpc_tool_call(), headers=BEARER)
    assert response.status_code != 200 or _refused(response)


def _composed(redis, control_grants, *, resource=GUARD_RESOURCE, caller_card=None, mode="and"):
    caller = card_authority_from_record(caller_card or _live_card(resource_operations={GUARD_RESOURCE: ("records_export",)}))
    control = CardAuthority(
        access_id="control-card-1", client_id="", grantor_subject=caller.grantor_subject, delegate_subject="",
        source="control", card_kind=CARD_KIND_CONTROL, label="Grantor control", card_revision=8,
        resource_grants={resource: tuple(control_grants)},
        resource_operations={resource: ("records_export",) if resource == GUARD_RESOURCE else ()},
        named_service_operations=NamedServiceSelection.none(), identity_scope=caller.identity_scope,
        issuer_ref="work:project:demo", issuer_kind="application", issuer_label="Demo project",
        composition_mode=mode)
    control = materialize_control_snapshot(control, basis_catalog_version="catalog-test-v1", origin="test")
    caller = dataclasses.replace(caller, control_card=ControlCardBinding(
        control_id=control.access_id, issuer_ref=control.issuer_ref, issuer_kind=control.issuer_kind,
        issuer_label=control.issuer_label, control_revision=control.card_revision))
    _store_card_authority(redis, caller)
    _store_card_authority(redis, control)


def test_the_same_session_is_denied_once_the_grantors_control_narrows(monkeypatch):
    redis = _Redis()
    _composed(redis, ["records:read"])
    client = _session(monkeypatch, redis, _stored())
    assert "projection" in _projection(client)
    _composed(redis, ["records:write"])  # the grantor's Control no longer grants the read
    assert _refused(client.post("/guard", json=_rpc_tool_call(), headers=BEARER))


# The configured composition is preserved: under "or" the Control contributes, it does not narrow (AE effective.py).

def test_under_or_a_narrowed_control_does_not_remove_the_callers_own_grant(monkeypatch):
    redis = _Redis()
    _composed(redis, ["records:read"], mode="or")
    client = _session(monkeypatch, redis, _stored())
    assert "projection" in _projection(client)
    _composed(redis, ["records:write"], mode="or")  # the Control no longer grants the read; the caller still does
    assert "projection" in _projection(client)


def test_under_or_a_grant_only_the_control_contributed_ends_when_the_control_drops_it(monkeypatch):
    redis = _Redis()
    caller = _live_card(resource_grants={GUARD_RESOURCE: ("records:write",)},
                        resource_operations={GUARD_RESOURCE: ("records_export",)})
    _composed(redis, ["records:read"], mode="or", caller_card=caller)
    client = _session(monkeypatch, redis, _stored())
    assert "projection" in _projection(client)
    _composed(redis, ["records:write"], mode="or", caller_card=caller)
    assert _refused(client.post("/guard", json=_rpc_tool_call(), headers=BEARER))


def test_under_or_a_revoked_control_refuses_the_same_session(monkeypatch):
    redis = _Redis()
    _composed(redis, ["records:read"], mode="or")
    client = _session(monkeypatch, redis, _stored())
    assert "projection" in _projection(client)
    redis.values.pop(next(key for key in redis.values if "control-card-1" in str(key)))  # the Control is gone
    response = client.post("/guard", json=_rpc_tool_call(), headers=BEARER)
    assert response.status_code != 200 or _refused(response)


# The consent itself: its documented revocation and expiry end the same session -------------------------------------

def test_a_revoked_consent_ends_the_same_session(monkeypatch):
    redis = _Redis()
    _put(redis, _live_card())
    client = _session(monkeypatch, redis, _stored())
    assert "projection" in _projection(client)
    client.app.state.oauth_grant_store.record = None  # the grant (consent) is revoked: the store has no record
    response = client.post("/guard", json=_rpc_tool_call(), headers=BEARER)
    assert response.status_code != 200 or _refused(response)


def test_an_expired_card_ends_the_same_session(monkeypatch):
    redis = _Redis()
    _put(redis, _live_card())
    client = _session(monkeypatch, redis, _stored())
    assert "projection" in _projection(client)
    _put(redis, _live_card(expires_at=1))  # the Card's life has ended
    response = client.post("/guard", json=_rpc_tool_call(), headers=BEARER)
    assert response.status_code != 200 or _refused(response)


# grantor_authority: the consent-time IdP ceiling only ever denies ----------------------------------------------

def test_a_consent_time_grantor_admin_role_does_not_enlarge_a_card_selected_role():
    """Pinned (no change): on the MCP/REST projection, a Card-selected role never inherits grantor roles."""
    credential = _authority(scopes=["kdcube:role:registered", "records:read"], resource="*")
    request = Request({"type": "http", "method": "POST", "path": "/guard", "query_string": b"", "headers": [],
                       "scheme": "http", "server": ("testserver", 80)})
    request.state.delegated_credential = {
        "credential": credential,
        "grant_record": {"credential": credential,
                         "grantor_authority": {"grantor_roles": ["kdcube:role:super-admin"],
                                               "grantor_permissions": ["kdcube:*"]}},
        "user": {"sub": credential["subject"], "roles": [], "permissions": []},
    }
    projection = surface_guard.delegated_rest_runtime_projection(request, request_resource="*")
    assert "kdcube:role:super-admin" not in projection["roles"]
    assert "kdcube:*" not in projection["permissions"]


# The platform-admin entrance (all resources): the same live enforcement, plus the deny-only grantor ceiling ----------

ADMIN = "kdcube:role:super-admin"


def _admin_card(grants=(ADMIN,)):
    return _live_card(resource_grants={"*": tuple(grants)}, operations=())


def _admin_session(monkeypatch, redis, *, grantor_roles=(ADMIN,)):
    record = {"registry_access_id": "oauth-access-1",
              "credential": _authority(scopes=[ADMIN], resource="*"),
              "grantor_authority": {"grantor_roles": list(grantor_roles)}}
    client = _client(monkeypatch, grant_record=record, redis=redis, user={
        "sub": "integration:claude:a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
        "roles": ["kdcube:role:delegated-client"], "permissions": [ADMIN]})
    client.app.state.oauth_delegated_config = {"tenant": "home", "project": "demo", "enabled": True, "resources": [
        {"resource": "*", "label": "All platform and application APIs", "admin_only": True, "grants": [ADMIN]}]}

    @client.app.get("/api/admin")
    async def admin(request: Request):
        return JSONResponse(await surface_guard.delegated_platform_admin_runtime_projection(
            request, authority_id="delegated_client"))

    return client


def _entered(client) -> bool:
    response = client.get("/api/admin", headers=BEARER)
    assert response.status_code == 200, response.text
    projection = response.json()
    return bool(projection) and ADMIN in projection.get("roles", [])


def test_the_admin_entrance_admits_the_session_while_card_and_grantor_hold_admin(monkeypatch):
    redis = _Redis()
    _store_live_card(redis, _admin_card())
    assert _entered(_admin_session(monkeypatch, redis))


def test_the_admin_entrance_refuses_the_same_session_once_its_card_narrows(monkeypatch):
    redis = _Redis()
    _store_live_card(redis, _admin_card())
    client = _admin_session(monkeypatch, redis)
    assert _entered(client)
    _store_live_card(redis, _admin_card(grants=("kdcube:role:registered",)))
    assert not _entered(client)


def test_the_admin_entrance_refuses_the_same_session_once_its_card_is_revoked(monkeypatch):
    redis = _Redis()
    _store_live_card(redis, _admin_card())
    client = _admin_session(monkeypatch, redis)
    assert _entered(client)
    redis.values.clear()
    assert not _entered(client)


def test_the_admin_entrance_refuses_the_same_session_once_the_grantors_control_narrows(monkeypatch):
    redis = _Redis()
    _composed(redis, [ADMIN], resource="*", caller_card=_admin_card())
    client = _admin_session(monkeypatch, redis)
    assert _entered(client)
    _composed(redis, ["kdcube:role:registered"], resource="*", caller_card=_admin_card())
    assert not _entered(client)


def test_the_consent_time_grantor_ceiling_only_denies(monkeypatch):
    """Pinned (no change): a Card granting admin does not admit a grantor whose consent-time roles lack it.
    The opposite window (a grantor demoted in the IdP after consent) is a named limitation, Root's decision."""
    redis = _Redis()
    _store_live_card(redis, _admin_card())
    assert not _entered(_admin_session(monkeypatch, redis, grantor_roles=("kdcube:role:registered",)))


def test_a_card_that_stops_selecting_a_role_falls_back_to_the_consent_time_grantor_roles(monkeypatch):
    """PINNED, NOT ENDORSED (W653 finding 2, for Main/Root): delegated_roles.delegated_role_projection's contract,
    "Use Card-selected roles when present; otherwise preserve legacy roles", projects the grantor's consent-time
    roles for endpoint checks once a live Card selects no platform role, with the actor kept external. This test
    records today's behaviour on one session; changing it is a contract decision, not part of this fix."""
    redis = _Redis()
    _put(redis, _live_card(resource_grants={GUARD_RESOURCE: (ADMIN, "records:read")}))
    record = {**_stored(), "grantor_authority": {"grantor_roles": [ADMIN], "grantor_permissions": ["kdcube:*"]}}
    client = _session(monkeypatch, redis, record)
    before = _projection(client)["projection"]
    assert (before["roles"], before["user_type"], before["delegated_roles_selected"]) == ([ADMIN], "privileged", True)
    _put(redis, _live_card(resource_grants={GUARD_RESOURCE: ("records:read",)}))  # the Card drops the role
    after = _projection(client)["projection"]
    assert (after["roles"], after["permissions"], after["user_type"], after["delegated_roles_selected"]) == (
        [ADMIN], ["kdcube:*"], "external", False)
