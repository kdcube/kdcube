from __future__ import annotations

import base64
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from kdcube_ai_app.apps.chat.proc.rest.management import (
    human_approval_oidc,
    human_approval_routes,
    human_approval_webauthn,
)
from kdcube_ai_app.apps.chat.proc.rest.management.config import (
    CognitoFreshAuthenticationProvider,
    GoogleFreshAuthenticationConfig,
    HumanApprovalConfig,
    WebAuthnApprovalConfig,
)
from kdcube_ai_app.apps.chat.proc.rest.management.human_approval import (
    FRESH_AUTHENTICATION,
    USER_VERIFICATION,
    BrowserAdminSession,
    HumanApprovalChallenge,
    HumanApprovalContext,
    HumanApprovalError,
    HumanApprovalEvidence,
    _subject,
)
from kdcube_ai_app.apps.chat.proc.rest.management.human_approval_store import (
    HumanProofRecord,
    PasskeyCredentialRecord,
    RedisHumanApprovalStore,
)
from starlette.requests import Request


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, *, nx=False, ex=None):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def eval(self, script, _keys, key, *args):
        current = self.values.get(key)
        if "ARGV[3]" in script:
            expected, replacement, _ttl = args
            if current is None:
                return 0
            if current != expected:
                return -1
            self.values[key] = replacement
            return 1
        expected, replacement = args
        if expected == "__missing__":
            if current is not None:
                return -1
        elif current is None or current != expected:
            return -1
        self.values[key] = replacement
        return 1


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _admin(*, hint=None, binding="b" * 64) -> BrowserAdminSession:
    return BrowserAdminSession(
        subject="google:subject-a",
        session_id="session-a",
        cookie_binding=binding,
        username="owner-a",
        email="owner@example.test",
        identity_hint=hint or {"provider": "google", "provider_subject": "subject-a"},
    )


def _context(*, assurance=FRESH_AUTHENTICATION) -> HumanApprovalContext:
    return HumanApprovalContext(
        action="kdcube.management.secret.export",
        tenant="tenant-a",
        project="project-a",
        transaction_id="t" * 43,
        request_digest="d" * 64,
        required_assurance=assurance,
        max_evidence_age_seconds=300,
        return_url="/api/integrations/management/v1/secrets/export/authorize?transaction="
        + "t" * 43,
    )


def _config(
    *,
    provider="google",
    cognito=False,
    webauthn_policy="verified_passkey",
) -> HumanApprovalConfig:
    cognito_provider = CognitoFreshAuthenticationProvider(
        alias="primary",
        region="eu-west-1",
        user_pool_id="eu-west-1_POOL",
        app_client_id="cognito-client",
        hosted_ui_domain="https://auth.example.test",
    )
    return HumanApprovalConfig(
        fresh_authentication_provider=provider,
        challenge_ttl_seconds=180,
        http_timeout_seconds=10,
        cognito_managed_login=cognito,
        cognito_providers=(cognito_provider,) if cognito else (),
        google=GoogleFreshAuthenticationConfig(
            client_id="google-client",
            jwks_url="https://www.googleapis.com/oauth2/v3/certs",
        ),
        webauthn=WebAuthnApprovalConfig(
            enabled=True,
            rp_id="example.test",
            rp_name="KDCube",
            allowed_origins=("https://example.test",),
            credential_policy=webauthn_policy,
            trusted_attestation_root_files={},
            timeout_milliseconds=60000,
            max_credentials_per_user=8,
        ),
    )


def _request(*, config=None, store=None, state=None) -> Request:
    app = FastAPI()
    app.state.human_approval_config = config or _config()
    app.state.human_approval_store = store or RedisHumanApprovalStore(
        _Redis(),
        tenant="tenant-a",
        project="project-a",
        ttl_seconds=180,
    )
    if state:
        for name, value in state.items():
            setattr(app.state, name, value)
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("example.test", 443),
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", b"example.test")],
            "app": app,
        }
    )


def test_browser_subject_prefers_verified_platform_identity() -> None:
    session = SimpleNamespace(
        user_id="display-name",
        username="display-name",
        identity_authority={"platform_user_id": "immutable-provider-subject"},
    )

    assert _subject(session) == "immutable-provider-subject"


def test_human_approval_config_inherits_platform_authorities() -> None:
    values = {
        "management.human_approval.fresh_authentication_provider": "auto",
        "management.human_approval.challenge_ttl_seconds": 180,
        "management.human_approval.http_timeout_seconds": 10,
        "management.human_approval.cognito": {
            "managed_login": True,
            "hosted_ui_domain": "",
        },
        "management.human_approval.google": {},
        "management.human_approval.webauthn": {
            "enabled": True,
            "rp_id": "example.test",
            "rp_name": "KDCube",
            "allowed_origins": ["https://example.test/"],
            "credential_policy": "verified_passkey",
            "trusted_attestation_root_files": {},
            "timeout_milliseconds": 60000,
            "max_credentials_per_user": 8,
        },
    }
    cognito_provider = SimpleNamespace(
        alias="primary",
        region="eu-west-1",
        user_pool_id="eu-west-1_POOL",
        app_client_id="cognito-client",
        hosted_ui_domain="https://auth.example.test",
    )
    settings = SimpleNamespace(
        plain=lambda path, default=None: values.get(path, default),
        AUTH=SimpleNamespace(COGNITO_TRUSTED_PROVIDERS=[cognito_provider]),
        connection_hub_platform_auth_config=lambda: {
            "login_authenticator": {
                "provider": {
                    "authenticator": {
                        "client_id": "google-client",
                    }
                }
            }
        },
    )

    config = HumanApprovalConfig.from_settings(settings)

    assert config.cognito_providers[0].hosted_ui_domain == ("https://auth.example.test")
    assert config.google.client_id == "google-client"
    assert config.webauthn.allowed_origins == ("https://example.test",)


def test_attested_hardware_requires_explicit_trust_roots() -> None:
    with pytest.raises(ValueError, match="attestation trust roots"):
        _config(webauthn_policy="attested_hardware").validate()


def _patch_admin(monkeypatch, admin: BrowserAdminSession) -> None:
    async def resolve(_request):
        return admin

    monkeypatch.setattr(human_approval_oidc, "resolve_browser_admin_session", resolve)
    monkeypatch.setattr(
        human_approval_webauthn,
        "resolve_browser_admin_session",
        resolve,
    )


@pytest.mark.asyncio
async def test_google_fresh_auth_uses_signed_auth_time_and_consumes_once(
    monkeypatch,
) -> None:
    now = int(time.time())
    admin = _admin()
    _patch_admin(monkeypatch, admin)
    store = RedisHumanApprovalStore(
        _Redis(), tenant="tenant-a", project="project-a", ttl_seconds=180
    )

    async def verify(*, token, config):
        assert token == "signed-google-id-token"
        assert config.client_id == "google-client"
        return {
            "sub": "subject-a",
            "nonce": nonce,
            "auth_time": now,
            "iat": now + 1000,
        }

    request = _request(
        store=store,
        state={"human_approval_google_token_verifier": verify},
    )
    context = _context()
    verifier = human_approval_oidc.OidcFreshAuthenticationVerifier()
    outcome = await verifier.evaluate(request, context=context, phase="present")
    assert isinstance(outcome, HumanApprovalChallenge)
    params = parse_qs(urlsplit(outcome.authorization_url).query)
    state = params["state"][0]
    nonce = params["nonce"][0]
    assert params["response_type"] == ["id_token"]
    assert params["response_mode"] == ["form_post"]
    assert params["prompt"] == ["select_account"]
    assert json.loads(params["claims"][0]) == {
        "id_token": {"auth_time": {"essential": True}}
    }

    returned = await human_approval_oidc.complete_oidc_callback(
        request,
        state=state,
        id_token="signed-google-id-token",
        response_issuer="https://accounts.google.com",
    )
    assert returned == context.return_url

    presented = await verifier.evaluate(request, context=context, phase="present")
    assert presented.assurance == FRESH_AUTHENTICATION
    assert presented.verified_at == now
    committed = await verifier.evaluate(request, context=context, phase="commit")
    assert committed == presented
    with pytest.raises(HumanApprovalError) as replay:
        await verifier.evaluate(request, context=context, phase="commit")
    assert replay.value.code == "human_approval_restart_required"
    assert "signed-google-id-token" not in "\n".join(store._redis.values.values())


@pytest.mark.asyncio
async def test_auto_provider_follows_google_session_in_mixed_deployment(
    monkeypatch,
) -> None:
    admin = _admin(
        hint={
            "provider": "google",
            "provider_subject": "subject-a",
            "iss": "https://accounts.google.com",
        }
    )
    _patch_admin(monkeypatch, admin)
    request = _request(config=_config(provider="auto", cognito=True))

    outcome = await human_approval_oidc.OidcFreshAuthenticationVerifier().evaluate(
        request,
        context=_context(),
        phase="present",
    )

    assert isinstance(outcome, HumanApprovalChallenge)
    assert urlsplit(outcome.authorization_url).netloc == "accounts.google.com"


@pytest.mark.asyncio
async def test_cognito_classic_hosted_ui_is_not_treated_as_fresh_login(
    monkeypatch,
) -> None:
    admin = BrowserAdminSession(
        subject="cognito-subject",
        session_id="session-c",
        cookie_binding="c" * 64,
        identity_hint={
            "iss": "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_POOL",
            "aud": "cognito-client",
        },
    )
    _patch_admin(monkeypatch, admin)
    config = replace(
        _config(provider="cognito", cognito=True),
        cognito_managed_login=False,
    )

    with pytest.raises(HumanApprovalError) as unavailable:
        await human_approval_oidc.OidcFreshAuthenticationVerifier().evaluate(
            _request(config=config),
            context=_context(),
            phase="present",
        )

    assert unavailable.value.code == "human_approval_cognito_managed_login_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claims", "code"),
    [
        (
            {"sub": "subject-a", "nonce": "wrong", "auth_time": int(time.time())},
            "human_approval_oidc_nonce_invalid",
        ),
        (
            {"sub": "subject-b", "nonce": "placeholder", "auth_time": int(time.time())},
            "human_approval_oidc_subject_changed",
        ),
        (
            {"sub": "subject-a", "nonce": "placeholder", "iat": int(time.time())},
            "human_approval_auth_time_required",
        ),
        (
            {
                "sub": "subject-a",
                "nonce": "placeholder",
                "auth_time": int(time.time()) - 301,
            },
            "human_approval_authentication_not_fresh",
        ),
    ],
)
async def test_google_fresh_auth_rejects_untrusted_freshness(
    monkeypatch,
    claims,
    code,
) -> None:
    admin = _admin()
    _patch_admin(monkeypatch, admin)
    store = RedisHumanApprovalStore(
        _Redis(), tenant="tenant-a", project="project-a", ttl_seconds=180
    )
    captured = {}

    async def verify(**_kwargs):
        return {
            **claims,
            "nonce": captured["nonce"]
            if claims.get("nonce") == "placeholder"
            else claims.get("nonce"),
        }

    request = _request(
        store=store,
        state={"human_approval_google_token_verifier": verify},
    )
    outcome = await human_approval_oidc.OidcFreshAuthenticationVerifier().evaluate(
        request,
        context=_context(),
        phase="present",
    )
    params = parse_qs(urlsplit(outcome.authorization_url).query)
    captured["nonce"] = params["nonce"][0]
    with pytest.raises(HumanApprovalError) as invalid:
        await human_approval_oidc.complete_oidc_callback(
            request,
            state=params["state"][0],
            id_token="signed-token",
            response_issuer="https://accounts.google.com",
        )
    assert invalid.value.code == code


@pytest.mark.asyncio
async def test_google_callback_requires_authorization_response_issuer(
    monkeypatch,
) -> None:
    admin = _admin()
    _patch_admin(monkeypatch, admin)
    request = _request()
    outcome = await human_approval_oidc.OidcFreshAuthenticationVerifier().evaluate(
        request,
        context=_context(),
        phase="present",
    )
    state = parse_qs(urlsplit(outcome.authorization_url).query)["state"][0]

    with pytest.raises(HumanApprovalError) as invalid:
        await human_approval_oidc.complete_oidc_callback(
            request,
            state=state,
            id_token="unused",
            response_issuer="https://attacker.example",
        )

    assert invalid.value.code == "human_approval_oidc_issuer_mismatch"


@pytest.mark.asyncio
async def test_cognito_managed_login_uses_prompt_login_and_pkce(monkeypatch) -> None:
    now = int(time.time())
    admin = BrowserAdminSession(
        subject="cognito-subject",
        session_id="session-c",
        cookie_binding="c" * 64,
        identity_hint={
            "iss": "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_POOL",
            "aud": "cognito-client",
        },
    )
    _patch_admin(monkeypatch, admin)
    store = RedisHumanApprovalStore(
        _Redis(), tenant="tenant-a", project="project-a", ttl_seconds=180
    )
    captured = {}

    async def exchange(*, provider, challenge, code):
        assert provider.alias == "primary"
        assert code == "one-use-code"
        assert len(challenge.code_verifier) >= 43
        captured["nonce"] = challenge.nonce
        return {"id_token": "cognito-id-token"}

    async def verify(*, provider, token):
        assert provider.app_client_id == "cognito-client"
        assert token == "cognito-id-token"
        return {
            "sub": "cognito-subject",
            "nonce": captured["nonce"],
            "auth_time": now,
        }

    request = _request(
        config=_config(provider="cognito", cognito=True),
        store=store,
        state={
            "human_approval_cognito_exchange": exchange,
            "human_approval_cognito_token_verifier": verify,
        },
    )
    outcome = await human_approval_oidc.OidcFreshAuthenticationVerifier().evaluate(
        request,
        context=_context(),
        phase="present",
    )
    params = parse_qs(urlsplit(outcome.authorization_url).query)
    assert params["prompt"] == ["login"]
    assert params["code_challenge_method"] == ["S256"]
    await human_approval_oidc.complete_oidc_callback(
        request,
        state=params["state"][0],
        code="one-use-code",
        response_issuer=("https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_POOL"),
    )


@pytest.mark.asyncio
async def test_fresh_proof_is_bound_to_the_original_browser_cookie(
    monkeypatch,
) -> None:
    now = int(time.time())
    original = _admin(binding="a" * 64)
    _patch_admin(monkeypatch, original)
    store = RedisHumanApprovalStore(
        _Redis(), tenant="tenant-a", project="project-a", ttl_seconds=180
    )
    captured = {}

    async def verify(**_kwargs):
        return {
            "sub": "subject-a",
            "nonce": captured["nonce"],
            "auth_time": now,
        }

    request = _request(
        store=store,
        state={"human_approval_google_token_verifier": verify},
    )
    context = _context()
    adapter = human_approval_oidc.OidcFreshAuthenticationVerifier()
    outcome = await adapter.evaluate(request, context=context, phase="present")
    params = parse_qs(urlsplit(outcome.authorization_url).query)
    captured["nonce"] = params["nonce"][0]
    await human_approval_oidc.complete_oidc_callback(
        request,
        state=params["state"][0],
        id_token="signed-token",
        response_issuer="https://accounts.google.com",
    )

    changed = _admin(binding="b" * 64)
    _patch_admin(monkeypatch, changed)
    with pytest.raises(HumanApprovalError) as invalid:
        await adapter.evaluate(request, context=context, phase="commit")

    assert invalid.value.code == "human_approval_restart_required"


@pytest.mark.asyncio
async def test_passkey_assertion_is_operation_bound_and_replay_safe(
    monkeypatch,
) -> None:
    admin = _admin()
    _patch_admin(monkeypatch, admin)
    store = RedisHumanApprovalStore(
        _Redis(), tenant="tenant-a", project="project-a", ttl_seconds=180
    )
    credential_id = _b64(b"credential-a")
    await store.add_passkey(
        subject=admin.subject,
        rp_id="example.test",
        credential=PasskeyCredentialRecord(
            credential_id=credential_id,
            public_key=_b64(b"public-key"),
            sign_count=4,
            aaguid="aaguid",
            attestation_format="none",
            device_type="multi_device",
            backed_up=True,
            policy="verified_passkey",
            label="Passkey",
            created_at=int(time.time()),
        ),
        maximum=8,
    )

    def verify(**kwargs):
        assert kwargs["credential_current_sign_count"] == 4
        assert kwargs["require_user_verification"] is True
        return SimpleNamespace(
            credential_device_type=SimpleNamespace(value="multi_device"),
            credential_backed_up=True,
            user_verified=True,
            new_sign_count=5,
        )

    request = _request(
        config=_config(webauthn_policy="verified_passkey"),
        store=store,
        state={"human_approval_webauthn_authentication_verifier": verify},
    )
    context = _context(assurance=USER_VERIFICATION)
    adapter = human_approval_webauthn.WebAuthnHumanApprovalVerifier()
    outcome = await adapter.evaluate(request, context=context, phase="present")
    assert isinstance(outcome, HumanApprovalChallenge)
    state = parse_qs(urlsplit(outcome.authorization_url).query)["state"][0]
    options = await human_approval_webauthn.authentication_options(
        request,
        state=state,
    )
    assert options["options"]["userVerification"] == "required"
    returned = await human_approval_webauthn.complete_authentication(
        request,
        state=state,
        credential_payload={"id": credential_id, "rawId": credential_id},
    )
    assert returned == context.return_url
    assert (
        await adapter.evaluate(request, context=context, phase="commit")
    ).assurance == USER_VERIFICATION
    with pytest.raises(HumanApprovalError) as replay:
        await human_approval_webauthn.complete_authentication(
            request,
            state=state,
            credential_payload={"id": credential_id, "rawId": credential_id},
        )
    assert replay.value.code == "human_approval_passkey_challenge_invalid"


@pytest.mark.asyncio
async def test_incompatible_passkey_requires_new_enrollment(monkeypatch) -> None:
    admin = _admin()
    _patch_admin(monkeypatch, admin)
    store = RedisHumanApprovalStore(
        _Redis(), tenant="tenant-a", project="project-a", ttl_seconds=180
    )
    await store.add_passkey(
        subject=admin.subject,
        rp_id="example.test",
        credential=PasskeyCredentialRecord(
            credential_id=_b64(b"synced-credential"),
            public_key=_b64(b"public-key"),
            sign_count=0,
            aaguid="aaguid",
            attestation_format="none",
            device_type="multi_device",
            backed_up=True,
            policy="verified_passkey",
            label="Passkey",
            created_at=int(time.time()),
        ),
        maximum=8,
    )
    request = _request(
        config=_config(webauthn_policy="single_device"),
        store=store,
    )

    outcome = await human_approval_webauthn.WebAuthnHumanApprovalVerifier().evaluate(
        request,
        context=_context(assurance=USER_VERIFICATION),
        phase="present",
    )

    assert isinstance(outcome, HumanApprovalChallenge)
    assert outcome.method == "webauthn_enrollment_required"
    assert urlsplit(outcome.authorization_url).path.endswith("/passkeys/register")


@pytest.mark.asyncio
async def test_passkey_registration_requires_and_consumes_fresh_proof(
    monkeypatch,
) -> None:
    admin = _admin()
    _patch_admin(monkeypatch, admin)
    store = RedisHumanApprovalStore(
        _Redis(), tenant="tenant-a", project="project-a", ttl_seconds=180
    )
    context = _context()
    enrollment_id = "e" * 43
    await store.create_passkey_enrollment(
        enrollment_id=enrollment_id,
        context=context,
        admin=admin,
        final_return_url=context.return_url,
    )
    await store.put_proof(
        HumanProofRecord(
            evidence=HumanApprovalEvidence(
                subject=admin.subject,
                assurance=FRESH_AUTHENTICATION,
                method="google_oidc_auth_time",
                request_digest=context.request_digest,
                verified_at=int(time.time()),
            ),
            context=context,
            admin=admin,
        )
    )

    def verify_registration(**kwargs):
        assert kwargs["require_user_verification"] is True
        return SimpleNamespace(
            credential_id=b"registered-credential",
            credential_public_key=b"registered-public-key",
            sign_count=0,
            aaguid="aaguid",
            fmt=SimpleNamespace(value="none"),
            credential_device_type=SimpleNamespace(value="multi_device"),
            credential_backed_up=True,
            user_verified=True,
            attestation_object=b"unused-for-none",
        )

    request = _request(
        store=store,
        state={"human_approval_webauthn_registration_verifier": verify_registration},
    )
    options = await human_approval_webauthn.registration_options(
        request,
        enrollment_id=enrollment_id,
    )
    assert isinstance(options, dict)
    state = options["state"]
    returned = await human_approval_webauthn.complete_registration(
        request,
        state=state,
        credential_payload={"id": "browser-credential"},
    )

    assert returned == context.return_url
    credentials = await store.passkeys(
        subject=admin.subject,
        rp_id="example.test",
    )
    assert [item.credential_id for item in credentials] == [
        _b64(b"registered-credential")
    ]
    assert await store.proof(context=context, admin=admin, consume=False) is None


@pytest.mark.asyncio
async def test_invalid_registration_does_not_consume_fresh_proof(
    monkeypatch,
) -> None:
    admin = _admin()
    _patch_admin(monkeypatch, admin)
    store = RedisHumanApprovalStore(
        _Redis(), tenant="tenant-a", project="project-a", ttl_seconds=180
    )
    context = _context()
    enrollment_id = "f" * 43
    await store.create_passkey_enrollment(
        enrollment_id=enrollment_id,
        context=context,
        admin=admin,
        final_return_url=context.return_url,
    )
    evidence = HumanApprovalEvidence(
        subject=admin.subject,
        assurance=FRESH_AUTHENTICATION,
        method="google_oidc_auth_time",
        request_digest=context.request_digest,
        verified_at=int(time.time()),
    )
    await store.put_proof(
        HumanProofRecord(evidence=evidence, context=context, admin=admin)
    )

    def reject_registration(**_kwargs):
        raise ValueError("malformed credential")

    request = _request(
        store=store,
        state={"human_approval_webauthn_registration_verifier": reject_registration},
    )
    options = await human_approval_webauthn.registration_options(
        request,
        enrollment_id=enrollment_id,
    )
    with pytest.raises(HumanApprovalError) as invalid:
        await human_approval_webauthn.complete_registration(
            request,
            state=options["state"],
            credential_payload={"id": "malformed"},
        )

    assert invalid.value.code == "human_approval_passkey_registration_invalid"
    assert await store.proof(context=context, admin=admin, consume=False) == evidence


@pytest.mark.asyncio
async def test_stale_fresh_proof_cannot_bootstrap_passkey(monkeypatch) -> None:
    admin = _admin()
    _patch_admin(monkeypatch, admin)
    store = RedisHumanApprovalStore(
        _Redis(), tenant="tenant-a", project="project-a", ttl_seconds=180
    )
    context = _context()
    enrollment_id = "g" * 43
    await store.create_passkey_enrollment(
        enrollment_id=enrollment_id,
        context=context,
        admin=admin,
        final_return_url=context.return_url,
    )
    await store.put_proof(
        HumanProofRecord(
            evidence=HumanApprovalEvidence(
                subject=admin.subject,
                assurance=FRESH_AUTHENTICATION,
                method="google_oidc_auth_time",
                request_digest=context.request_digest,
                verified_at=int(time.time()) - 301,
            ),
            context=context,
            admin=admin,
        )
    )

    with pytest.raises(HumanApprovalError) as stale:
        await human_approval_webauthn.registration_options(
            _request(store=store),
            enrollment_id=enrollment_id,
        )

    assert stale.value.code == "human_approval_evidence_invalid"


def test_passkey_policy_does_not_equate_synced_and_attested_credentials() -> None:
    credential = PasskeyCredentialRecord(
        credential_id=_b64(b"credential"),
        public_key=_b64(b"public"),
        sign_count=0,
        aaguid="aaguid",
        attestation_format="none",
        device_type="multi_device",
        backed_up=True,
        policy="verified_passkey",
        label="Passkey",
        created_at=int(time.time()),
    )
    assert (
        human_approval_webauthn._enforce_credential_policy(
            configured_policy="verified_passkey",
            credential=credential,
            device_type="multi_device",
            user_verified=True,
        )
        == "webauthn_uv"
    )
    with pytest.raises(HumanApprovalError) as device:
        human_approval_webauthn._enforce_credential_policy(
            configured_policy="single_device",
            credential=credential,
            device_type="multi_device",
            user_verified=True,
        )
    assert device.value.code == "human_approval_single_device_required"
    with pytest.raises(HumanApprovalError) as attested:
        human_approval_webauthn._enforce_credential_policy(
            configured_policy="attested_hardware",
            credential=credential,
            device_type="single_device",
            user_verified=True,
        )
    assert attested.value.code == "human_approval_attested_hardware_required"


def test_sign_in_redirect_preserves_exact_passkey_challenge_query() -> None:
    app = FastAPI()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("example.test", 443),
            "path": "/api/integrations/management/v1/human-approval/webauthn",
            "query_string": b"state=opaque-state",
            "headers": [(b"host", b"example.test")],
            "app": app,
        }
    )

    response = human_approval_routes._sign_in_redirect(request)
    query = parse_qs(urlsplit(response.headers["location"]).query)

    assert query["next"] == [
        "/api/integrations/management/v1/human-approval/webauthn?state=opaque-state"
    ]
