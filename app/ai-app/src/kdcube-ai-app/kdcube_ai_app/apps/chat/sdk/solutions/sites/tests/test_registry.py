from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.sites.registry import (
    ApplicationSiteCatalog,
    SITE_AUTH_PLATFORM_SESSION,
    SITE_AUTH_PUBLIC,
    SiteRegistryError,
    application_site_from_props,
    build_application_site_catalog,
    compile_application_site_catalog,
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


def test_site_auth_mode_is_descriptor_owned() -> None:
    public_site = _site("public@1", "public")
    protected_site = application_site_from_props(
        application_id="protected@1",
        props={
            "ui": {
                "main_view": {
                    "site": {
                        "enabled": True,
                        "alias": "protected",
                        "auth": {"mode": "platform_session"},
                    }
                }
            }
        },
    )

    assert public_site.auth_mode == SITE_AUTH_PUBLIC
    assert protected_site.auth_mode == SITE_AUTH_PLATFORM_SESSION
    assert "auth" not in public_site.to_dict()
    assert protected_site.to_dict()["auth"] == {"mode": "platform_session"}
    assert ApplicationSiteCatalog.from_dict(
        compile_application_site_catalog(
            tenant="tenant-a",
            project="project-a",
            sites=[public_site, protected_site],
        ).to_dict()
    ).resolve(alias="protected").auth_mode == SITE_AUTH_PLATFORM_SESSION


def test_site_rejects_unknown_auth_mode() -> None:
    with pytest.raises(SiteRegistryError, match="auth.mode"):
        application_site_from_props(
            application_id="site@1",
            props={
                "ui": {
                    "main_view": {
                        "site": {
                            "enabled": True,
                            "alias": "site",
                            "auth": {"mode": "implicit"},
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
    with pytest.raises(SiteRegistryError, match="overlapping application site host patterns"):
        resolve_application_site(
            [
                _site("a@1", "a", hosts=["site.example.com"]),
                _site("b@1", "b", hosts=["site.example.com"]),
            ],
            host="site.example.com",
        )


def test_rejects_wildcard_host_overlap() -> None:
    with pytest.raises(SiteRegistryError, match="overlapping application site host patterns"):
        resolve_application_site(
            [
                _site("a@1", "a", hosts=["*.example.com"]),
                _site("b@1", "b", hosts=["*.preview.example.com"]),
            ],
            host="a.preview.example.com",
        )


def test_enabled_site_requires_explicit_alias() -> None:
    with pytest.raises(SiteRegistryError, match="requires a valid"):
        _site("site@1", "")


def test_enabled_site_requires_application_id() -> None:
    with pytest.raises(SiteRegistryError, match="requires an application id"):
        _site("", "site")


def test_catalog_revision_is_deterministic_and_round_trips() -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[
            _site("z@1", "z", hosts=["z.example.com"]),
            _site("a@1", "a", default=True),
        ],
    )
    same = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=list(reversed(catalog.sites)),
    )

    restored = ApplicationSiteCatalog.from_dict(catalog.to_dict())

    assert catalog.revision == same.revision
    assert restored == catalog
    assert restored.resolve(host="z.example.com").application_id == "z@1"


def test_catalog_rejects_tampered_revision() -> None:
    catalog = compile_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        sites=[_site("site@1", "site")],
    )
    payload = catalog.to_dict()
    payload["revision"] = "not-the-content-revision"

    with pytest.raises(SiteRegistryError, match="revision"):
        ApplicationSiteCatalog.from_dict(payload)


def test_disabled_application_is_not_projected() -> None:
    catalog = build_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        application_props={
            "disabled@1": {
                "enabled": {"bundle": False},
                "ui": {
                    "main_view": {
                        "site": {"enabled": True, "alias": "disabled"},
                    },
                },
            },
            "enabled@1": {
                "ui": {
                    "main_view": {
                        "site": {"enabled": True, "alias": "enabled"},
                    },
                },
            },
        },
    )

    assert [site.alias for site in catalog.sites] == ["enabled"]


def test_root_route_prefix_rejects_enabled_application_sites() -> None:
    with pytest.raises(SiteRegistryError, match="proxy.route_prefix is '/'"):
        build_application_site_catalog(
            tenant="tenant-a",
            project="project-a",
            route_prefix="/",
            application_props={
                "site@1": {
                    "ui": {
                        "main_view": {
                            "site": {"enabled": True, "alias": "site", "default": True},
                        },
                    },
                },
            },
        )


def test_root_route_prefix_without_sites_is_allowed() -> None:
    catalog = build_application_site_catalog(
        tenant="tenant-a",
        project="project-a",
        route_prefix="/",
        application_props={},
    )

    assert catalog.sites == ()


def test_non_root_route_prefix_allows_enabled_application_sites() -> None:
    for prefix in ("/platform", "/control/ui"):
        catalog = build_application_site_catalog(
            tenant="tenant-a",
            project="project-a",
            route_prefix=prefix,
            application_props={
                "site@1": {
                    "ui": {
                        "main_view": {
                            "site": {"enabled": True, "alias": "site", "default": True},
                        },
                    },
                },
            },
        )

        assert [site.alias for site in catalog.sites] == ["site"]
