from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.sites.registry import (
    SiteRegistryError,
    application_site_from_props,
    resolve_application_site,
)


def _site(application_id: str, alias: str, *, default: bool = False, hosts=None):
    return application_site_from_props(
        application_id=application_id,
        props={
            "ui": {
                "main_view": {
                    "site": {
                        "enabled": True,
                        "alias": alias,
                        "default": default,
                        "hosts": hosts or [],
                    }
                }
            }
        },
    )


def test_disabled_site_is_not_registered() -> None:
    assert application_site_from_props(
        application_id="site@1",
        props={"ui": {"main_view": {"site": {"enabled": False}}}},
    ) is None


def test_resolves_alias_host_and_default() -> None:
    primary = _site("primary@1", "primary", default=True)
    docs = _site("docs@1", "docs", hosts=["docs.example.com", "*.preview.example.com"])
    assert resolve_application_site([primary, docs], alias="docs") == docs
    assert resolve_application_site([primary, docs], host="docs.example.com:443") == docs
    assert resolve_application_site([primary, docs], host="a.preview.example.com") == docs
    assert resolve_application_site([primary, docs], host="unknown.example.com") == primary


def test_rejects_ambiguous_registry() -> None:
    with pytest.raises(SiteRegistryError, match="duplicate application site alias"):
        resolve_application_site([_site("a@1", "same"), _site("b@1", "same")], alias="same")
    with pytest.raises(SiteRegistryError, match="multiple default"):
        resolve_application_site(
            [_site("a@1", "a", default=True), _site("b@1", "b", default=True)]
        )
    with pytest.raises(SiteRegistryError, match="multiple application sites match host"):
        resolve_application_site(
            [
                _site("a@1", "a", hosts=["site.example.com"]),
                _site("b@1", "b", hosts=["site.example.com"]),
            ],
            host="site.example.com",
        )


def test_enabled_site_requires_explicit_alias() -> None:
    with pytest.raises(SiteRegistryError, match="requires a valid"):
        _site("site@1", "")


def test_enabled_site_requires_application_id() -> None:
    with pytest.raises(SiteRegistryError, match="requires an application id"):
        _site("", "site")
