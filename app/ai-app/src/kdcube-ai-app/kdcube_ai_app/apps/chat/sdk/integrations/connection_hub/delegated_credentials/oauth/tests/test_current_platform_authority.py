"""Native target-role proof and request-local typed delegated casts."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from connection_hub.authority_registry import CredentialEnvelope
from kdcube_ai_app.apps.chat.sdk import identity_authority
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth import current_grantor, surface_guard

REGISTERED = "kdcube:role:registered"
PAID = "kdcube:role:paid"
ADMIN = "kdcube:role:super-admin"
TARGET = "native-user-1"


@pytest.fixture
def native_source(monkeypatch):
    from kdcube_ai_app.apps.middleware import platform_auth
    from kdcube_ai_app.auth import bundle

    state = SimpleNamespace(
        provider="bundle", reads=[], failure=None,
        user=SimpleNamespace(
            sub=TARGET, roles=[PAID], permissions=["records:read"],
            provider="cognito", metadata={}, disabled=False, updated_at=100,
        ),
    )

    async def get_user(subject):
        state.reads.append(subject)
        if state.failure is not None:
            raise state.failure
        return state.user

    monkeypatch.setattr(identity_authority, "get_settings", lambda: object())
    monkeypatch.setattr(platform_auth, "platform_authenticator_descriptor", lambda *_: {
        "provider": state.provider, "authority_id": "kdcube.platform",
    })
    monkeypatch.setattr(bundle, "get_bundle_session_authority", lambda **_: SimpleNamespace(get_user=get_user))
    return state


def native(**kwargs):
    return asyncio.run(identity_authority.resolve_native_platform_identity(
        tenant="test", project="test", platform_user_id=TARGET, **kwargs,
    ))


def cast_inputs(**attrs):
    return CredentialEnvelope.coerce({
        "credential_kind": "delegated_client_access",
        "issuer_authority_id": "delegated_client",
        "subject": "integration:client:native-user-1",
        "attrs": {"grantor_subject": TARGET, "client_id": "client", **attrs},
    })


def request():
    return SimpleNamespace(state=SimpleNamespace())


def cast(req, credential=None, record=None):
    return current_grantor.resolve_current_grantor_authority(
        req, tenant="test", project="test", credential=credential or cast_inputs(),
        grant_record=record or {"registry_access_id": "card-1", "card_revision": 7},
    )


def test_native_record_not_the_latest_derived_session_is_read(native_source, monkeypatch):
    def derived_session_must_not_be_read(*_a, **_kw):
        raise AssertionError("A derived session cannot supply target roles")

    monkeypatch.setattr(identity_authority.SessionManager, "get_session_by_user_id", derived_session_must_not_be_read)
    resolved = native()
    assert resolved.roles == (PAID,)
    assert resolved.source == "bundle_session_native_user"
    assert resolved.freshness == "last_native_sign_in_or_admin_write"
    assert resolved.updated_at == 100
    assert native_source.reads == [TARGET]


@pytest.mark.parametrize("provider", ["cognito", "multi-cognito", "simple"])
def test_unwired_provider_is_unresolved_without_store_fallback(native_source, provider):
    native_source.provider = provider
    with pytest.raises(identity_authority.PlatformAuthorityResolutionError) as error:
        native()
    assert (error.value.status_code, error.value.reason) == (403, "target_authority_identity_unresolved")
    assert native_source.reads == []


def test_missing_target_is_unresolved_not_registered(native_source):
    native_source.user = None
    with pytest.raises(identity_authority.PlatformAuthorityResolutionError) as error:
        native()
    assert (error.value.status_code, error.value.reason) == (403, "target_authority_identity_unresolved")


def test_unavailable_store_is_not_a_missing_identity(native_source):
    native_source.failure = RuntimeError("synthetic backend failure")
    with pytest.raises(identity_authority.PlatformAuthorityResolutionError) as error:
        native()
    assert (error.value.status_code, error.value.reason) == (503, "target_authority_unavailable")


def test_disabled_target_denies(native_source):
    native_source.user.disabled = True
    with pytest.raises(identity_authority.PlatformAuthorityResolutionError) as error:
        native()
    assert error.value.reason == "target_authority_identity_disabled"


def test_empty_native_roles_do_not_manufacture_a_tier(native_source):
    native_source.user.roles = []
    assert native().roles == ()


@pytest.mark.parametrize("derived_by", ["provider", "credential"])
def test_integration_rows_are_not_native_role_proof(native_source, derived_by):
    if derived_by == "provider":
        native_source.user.provider = "integration"
    else:
        native_source.user.metadata = {"credential": {"credential_kind": "delegated_client_access"}}
    with pytest.raises(identity_authority.PlatformAuthorityResolutionError) as error:
        native()
    assert error.value.reason == "target_authority_identity_unresolved"


def test_wrong_authority_never_reads_platform_user(native_source):
    with pytest.raises(identity_authority.PlatformAuthorityResolutionError) as error:
        native(target_authority_id="other-authority")
    assert error.value.reason == "required_authority_not_linked"
    assert native_source.reads == []


def test_one_resolution_per_request_ignores_captured_entitlements(native_source):
    async def invoke():
        req = request()
        record = {
            "registry_access_id": "card-1", "card_revision": 7,
            "grantor_authority": {"grantor_roles": [ADMIN], "economics_budget_bypass": True},
        }
        first = await cast(req, record=record)
        assert await cast(req, record=record) is first
        assert first.roles == (PAID,)
        assert first.projection_input()["economics_budget_bypass"] is False
        assert first.provenance()["card_revision"] == 7
        assert first.provenance()["actor_identity"].startswith("integration:")
    asyncio.run(invoke())
    assert native_source.reads == [TARGET]


def test_next_request_observes_native_demotion_after_delegated_cast(native_source):
    assert asyncio.run(cast(request())).roles == (PAID,)
    native_source.user.roles = [REGISTERED]
    native_source.user.updated_at = 101
    resolved = asyncio.run(cast(request()))
    assert resolved.roles == (REGISTERED,)
    assert resolved.updated_at == 101
    with pytest.raises(current_grantor.GrantorAuthorityFailure, match="grantor_role_changed"):
        resolved.require_roles((PAID,))
    assert native_source.reads == [TARGET, TARGET]


def test_platform_tier_does_not_imply_custom_role(native_source):
    native_source.user.roles = [ADMIN]
    resolved = asyncio.run(cast(request()))
    resolved.require_roles((PAID,))
    with pytest.raises(current_grantor.GrantorAuthorityFailure, match="grantor_role_changed"):
        resolved.require_roles(("kdcube:role:custom",))


def test_missing_target_never_falls_back_to_delegate(native_source):
    with pytest.raises(current_grantor.GrantorAuthorityFailure, match="required_authority_not_linked"):
        asyncio.run(cast(request(), credential=cast_inputs(grantor_subject="")))
    assert native_source.reads == []


def test_mismatched_explicit_target_denies_before_lookup(native_source):
    record = {"grantor_authority": {"delegation_edges": [{
        "authority_id": "platform", "user_id": "other-user", "grants": [PAID],
    }]}}
    with pytest.raises(current_grantor.GrantorAuthorityFailure, match="required_authority_not_linked"):
        asyncio.run(cast(request(), record=record))
    assert native_source.reads == []


def test_dict_runtime_is_not_a_typed_admission_result():
    req = request()
    req.state._delegated_admitted_runtime = {"identity_authority": {"roles": [ADMIN], "budget_bypass": True}}
    assert surface_guard.delegated_economics_authority(req) == {}
