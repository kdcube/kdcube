# SPDX-License-Identifier: MIT

"""
Tests that Stripe service classes resolve their API keys through
get_service_secret (bundle-first, global-fallback) rather than from
a hard-wired global settings object.

Only the constructor key-resolution logic is tested here; no DB or
real Stripe calls are made.
"""

import pytest
from unittest.mock import MagicMock

import kdcube_ai_app.apps.chat.sdk.infra.economics.stripe as stripe_mod


# ---------------------------------------------------------------------------
# StripeSubscriptionService
# ---------------------------------------------------------------------------

class TestStripeSubscriptionServiceKeyResolution:
    def test_explicit_key_bypasses_service_secret(self, monkeypatch):
        secret_calls = []
        monkeypatch.setattr(stripe_mod, "get_service_secret",
                            lambda k, default=None: secret_calls.append(k) or "should-not-use")

        svc = stripe_mod.StripeSubscriptionService(
            pg_pool=MagicMock(),
            subscription_mgr=MagicMock(),
            default_tenant="t",
            default_project="p",
            stripe_api_key="sk-explicit",
        )

        assert svc.stripe_api_key == "sk-explicit"
        assert secret_calls == []

    def test_bundle_key_used_when_no_explicit_key(self, monkeypatch):
        monkeypatch.setattr(stripe_mod, "get_service_secret",
                            lambda k, default=None: "sk-bundle-stripe" if k == "stripe.secret_key" else None)

        svc = stripe_mod.StripeSubscriptionService(
            pg_pool=MagicMock(),
            subscription_mgr=MagicMock(),
            default_tenant="t",
            default_project="p",
        )

        assert svc.stripe_api_key == "sk-bundle-stripe"

    def test_global_key_used_as_fallback(self, monkeypatch):
        """When get_service_secret returns the global value (no bundle override)."""
        monkeypatch.setattr(stripe_mod, "get_service_secret",
                            lambda k, default=None: "sk-global-stripe" if k == "stripe.secret_key" else None)

        svc = stripe_mod.StripeSubscriptionService(
            pg_pool=MagicMock(),
            subscription_mgr=MagicMock(),
            default_tenant="t",
            default_project="p",
        )

        assert svc.stripe_api_key == "sk-global-stripe"

    def test_none_when_no_key_configured(self, monkeypatch):
        monkeypatch.setattr(stripe_mod, "get_service_secret", lambda k, default=None: None)

        svc = stripe_mod.StripeSubscriptionService(
            pg_pool=MagicMock(),
            subscription_mgr=MagicMock(),
            default_tenant="t",
            default_project="p",
        )

        assert svc.stripe_api_key is None


# ---------------------------------------------------------------------------
# StripeEconomicsWebhookHandler
# ---------------------------------------------------------------------------

class TestStripeEconomicsWebhookHandlerKeyResolution:
    def _make_handler(self, monkeypatch, service_secret_fn, *, webhook_secret=None):
        monkeypatch.setattr(stripe_mod, "get_service_secret", service_secret_fn)
        return stripe_mod.StripeEconomicsWebhookHandler(
            pg_pool=MagicMock(),
            user_credits_mgr=MagicMock(),
            subscription_budget_factory=MagicMock(),
            subscription_mgr=MagicMock(),
            default_tenant="t",
            default_project="p",
            stripe_webhook_secret=webhook_secret,
        )

    def test_bundle_secret_key_used(self, monkeypatch):
        def _secrets(k, default=None):
            return {"stripe.secret_key": "sk-bundle", "stripe.webhook_secret": "wh-bundle"}.get(k)

        handler = self._make_handler(monkeypatch, _secrets)
        assert handler.stripe_api_key == "sk-bundle"

    def test_bundle_webhook_secret_used(self, monkeypatch):
        def _secrets(k, default=None):
            return {"stripe.secret_key": "sk-bundle", "stripe.webhook_secret": "wh-bundle"}.get(k)

        handler = self._make_handler(monkeypatch, _secrets)
        assert handler.webhook_secret == "wh-bundle"

    def test_explicit_webhook_secret_takes_priority(self, monkeypatch):
        def _secrets(k, default=None):
            return "should-not-use"

        handler = self._make_handler(monkeypatch, _secrets, webhook_secret="wh-explicit")
        assert handler.webhook_secret == "wh-explicit"

    def test_none_webhook_secret_when_no_key(self, monkeypatch):
        handler = self._make_handler(monkeypatch, lambda k, default=None: None)
        assert handler.webhook_secret is None

    def test_verify_raises_when_webhook_secret_missing(self, monkeypatch):
        handler = self._make_handler(monkeypatch, lambda k, default=None: None)
        with pytest.raises(RuntimeError, match="webhook_secret"):
            handler._verify_and_parse(body=b"payload", stripe_signature="sig")


# ---------------------------------------------------------------------------
# StripeEconomicsAdminService
# ---------------------------------------------------------------------------

class TestStripeEconomicsAdminServiceKeyResolution:
    def test_bundle_key_used(self, monkeypatch):
        monkeypatch.setattr(stripe_mod, "get_service_secret",
                            lambda k, default=None: "sk-bundle-admin" if k == "stripe.secret_key" else None)

        svc = stripe_mod.StripeEconomicsAdminService(
            pg_pool=MagicMock(),
            user_credits_mgr=MagicMock(),
            subscription_mgr=MagicMock(),
        )

        assert svc.stripe_api_key == "sk-bundle-admin"

    def test_explicit_key_bypasses_service_secret(self, monkeypatch):
        secret_calls = []
        monkeypatch.setattr(stripe_mod, "get_service_secret",
                            lambda k, default=None: secret_calls.append(k) or "should-not-use")

        svc = stripe_mod.StripeEconomicsAdminService(
            pg_pool=MagicMock(),
            user_credits_mgr=MagicMock(),
            subscription_mgr=MagicMock(),
            stripe_api_key="sk-explicit-admin",
        )

        assert svc.stripe_api_key == "sk-explicit-admin"
        assert secret_calls == []

    def test_raises_when_stripe_called_without_key(self, monkeypatch):
        monkeypatch.setattr(stripe_mod, "get_service_secret", lambda k, default=None: None)

        svc = stripe_mod.StripeEconomicsAdminService(
            pg_pool=MagicMock(),
            user_credits_mgr=MagicMock(),
            subscription_mgr=MagicMock(),
        )

        with pytest.raises(RuntimeError, match="Stripe API key not configured"):
            svc._stripe()
