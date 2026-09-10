from __future__ import annotations

import pytest

from kdcube_cli.compose_profiles import (
    ComposeProfileConfigurationError,
    compose_profile_args,
    proxy_login_enabled,
)


@pytest.mark.parametrize("auth_type", [None, "simple", "cognito", "bundle"])
def test_proxy_login_defaults_to_disabled(auth_type):
    assembly = {} if auth_type is None else {"auth": {"type": auth_type}}

    assert proxy_login_enabled(assembly) is False
    assert compose_profile_args(assembly) == ()


def test_legacy_delegated_descriptor_defaults_to_enabled():
    assembly = {"auth": {"type": "delegated"}}

    assert proxy_login_enabled(assembly) is True
    assert compose_profile_args(assembly) == ("--profile", "proxylogin")


@pytest.mark.parametrize(
    ("assembly", "message"),
    [
        (
            {"auth": {"type": "delegated", "proxy_login": {"enabled": False}}},
            "auth.type=delegated requires",
        ),
        (
            {"auth": {"type": "bundle", "proxy_login": {"enabled": True}}},
            "requires auth.type=delegated",
        ),
        (
            {"auth": {"type": "bundle", "proxy_login": {"enabled": "sometimes"}}},
            "must be a boolean",
        ),
        (
            {"auth": {"type": "delegated", "proxy_login": {"enabled": "yes"}}},
            "must be a boolean",
        ),
    ],
)
def test_proxy_login_rejects_inconsistent_configuration(assembly, message):
    with pytest.raises(ComposeProfileConfigurationError, match=message):
        proxy_login_enabled(assembly)
