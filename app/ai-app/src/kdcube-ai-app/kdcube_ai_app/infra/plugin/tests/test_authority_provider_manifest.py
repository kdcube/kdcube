# SPDX-License-Identifier: MIT

from __future__ import annotations

from kdcube_ai_app.infra.plugin.bundle_loader import (
    authority_provider,
    discover_bundle_interface_manifest,
)


def test_authority_provider_is_discovered_in_bundle_manifest():
    class AuthorityBundle:
        @authority_provider(
            authority_id="yay.identity",
            authenticator_id="yay.identity.oauth",
            credential_kinds=["authority_access"],
            audiences=["bundle:navigator-tg-bot@1-0"],
            label="Yay Identity",
        )
        async def yay_identity_provider(self):
            return None

    manifest = discover_bundle_interface_manifest(AuthorityBundle, bundle_id="navigator-tg-bot@1-0")

    assert len(manifest.authority_providers) == 1
    spec = manifest.authority_providers[0]
    assert spec.method_name == "yay_identity_provider"
    assert spec.authority_id == "yay.identity"
    assert spec.authenticator_id == "yay.identity.oauth"
    assert spec.credential_kinds == ("authority_access",)
    assert spec.audiences == ("bundle:navigator-tg-bot@1-0",)
    assert spec.label == "Yay Identity"
    assert spec.transports == ("local",)
