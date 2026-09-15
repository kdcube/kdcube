from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Optional, Sequence


_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SITE_AUTH_PUBLIC = "public"
SITE_AUTH_PLATFORM_SESSION = "platform_session"
_SITE_AUTH_MODES = frozenset({SITE_AUTH_PUBLIC, SITE_AUTH_PLATFORM_SESSION})


class SiteRegistryError(ValueError):
    """Raised when enabled application-site declarations are ambiguous."""


@dataclass(frozen=True)
class ApplicationSiteTarget:
    path: str
    module: Optional[str]
    singleton: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "module": self.module,
            "singleton": self.singleton,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ApplicationSiteTarget":
        return cls(
            path=str(value.get("path") or "").strip(),
            module=str(value.get("module") or "").strip() or None,
            singleton=_enabled(value.get("singleton")),
        )


@dataclass(frozen=True)
class ApplicationSite:
    application_id: str
    alias: str
    default: bool
    hosts: tuple[str, ...]
    target: Optional[ApplicationSiteTarget] = None
    auth_mode: str = SITE_AUTH_PUBLIC

    def to_dict(self) -> dict[str, Any]:
        value = {
            "application_id": self.application_id,
            "alias": self.alias,
            "default": self.default,
            "hosts": list(self.hosts),
            "target": self.target.to_dict() if self.target is not None else None,
        }
        # Omitting the default preserves revisions for catalogs written before
        # site authentication became descriptor-owned.
        if self.auth_mode != SITE_AUTH_PUBLIC:
            value["auth"] = {"mode": self.auth_mode}
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ApplicationSite":
        raw_hosts = value.get("hosts") or []
        if isinstance(raw_hosts, str):
            raw_hosts = [raw_hosts]
        raw_target = value.get("target")
        return cls(
            application_id=str(value.get("application_id") or "").strip(),
            alias=str(value.get("alias") or "").strip().lower(),
            default=_enabled(value.get("default")),
            hosts=tuple(
                host
                for host in (_normalize_host(item) for item in raw_hosts)
                if host
            ),
            target=(
                ApplicationSiteTarget.from_dict(raw_target)
                if isinstance(raw_target, Mapping)
                else None
            ),
            auth_mode=_site_auth_mode(value.get("auth")),
        )


@dataclass(frozen=True)
class ApplicationSiteCatalog:
    """Validated, immutable site-routing snapshot used by request handlers."""

    tenant: str
    project: str
    generation: int
    revision: str
    sites: tuple[ApplicationSite, ...]
    schema_version: int = 1

    def resolve(self, *, alias: str = "", host: str = "") -> Optional[ApplicationSite]:
        requested_alias = str(alias or "").strip().lower()
        if requested_alias:
            return next((site for site in self.sites if site.alias == requested_alias), None)

        normalized_host = _normalize_host(host)
        host_matches = [
            site
            for site in self.sites
            if normalized_host and any(_host_matches(pattern, normalized_host) for pattern in site.hosts)
        ]
        if len(host_matches) > 1:
            raise SiteRegistryError(f"multiple application sites match host: {normalized_host}")
        if host_matches:
            return host_matches[0]
        return next((site for site in self.sites if site.default), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tenant": self.tenant,
            "project": self.project,
            "generation": self.generation,
            "revision": self.revision,
            "sites": [site.to_dict() for site in self.sites],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ApplicationSiteCatalog":
        schema_version = int(value.get("schema_version") or 0)
        if schema_version != 1:
            raise SiteRegistryError(f"unsupported application site catalog schema: {schema_version}")
        tenant = str(value.get("tenant") or "").strip()
        project = str(value.get("project") or "").strip()
        generation = int(value.get("generation") or 0)
        if generation < 0:
            raise SiteRegistryError("application site catalog generation cannot be negative")
        sites = tuple(
            ApplicationSite.from_dict(item)
            for item in (value.get("sites") or [])
            if isinstance(item, Mapping)
        )
        catalog = compile_application_site_catalog(
            tenant=tenant,
            project=project,
            sites=sites,
            generation=generation,
        )
        declared_revision = str(value.get("revision") or "").strip()
        if declared_revision and declared_revision != catalog.revision:
            raise SiteRegistryError("application site catalog revision does not match its content")
        return catalog


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _site_auth_mode(value: Any) -> str:
    raw_mode = value.get("mode") if isinstance(value, Mapping) else value
    mode = str(raw_mode or SITE_AUTH_PUBLIC).strip().lower()
    if mode not in _SITE_AUTH_MODES:
        allowed = ", ".join(sorted(_SITE_AUTH_MODES))
        raise SiteRegistryError(
            f"application site auth.mode must be one of: {allowed}"
        )
    return mode


def _normalize_host(value: Any) -> str:
    host = str(value or "").strip().lower().rstrip(".")
    if not host:
        return ""
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end >= 0 else host
    return host.split(":", 1)[0]


def application_site_from_props(
    *,
    application_id: str,
    props: Mapping[str, Any] | None,
    application_spec: Mapping[str, Any] | None = None,
) -> Optional[ApplicationSite]:
    normalized_application_id = str(application_id or "").strip()
    if not normalized_application_id:
        raise SiteRegistryError("application site requires an application id")

    enabled = _mapping(_mapping(props).get("enabled"))
    if "bundle" in enabled and not _enabled(enabled.get("bundle")):
        return None

    main_view = _mapping(_mapping(_mapping(props).get("ui")).get("main_view"))
    site = _mapping(main_view.get("site"))
    if not _enabled(site.get("enabled")):
        return None

    alias = str(site.get("alias") or "").strip().lower()
    if not _ALIAS_RE.fullmatch(alias) or alias == "_root":
        raise SiteRegistryError(
            f"enabled application site {normalized_application_id!r} requires a valid, non-reserved alias"
        )
    raw_hosts = site.get("hosts") or []
    if isinstance(raw_hosts, str):
        raw_hosts = [raw_hosts]
    hosts = tuple(
        dict.fromkeys(
            host
            for host in (_normalize_host(value) for value in raw_hosts if value is not None)
            if host
        )
    )
    return ApplicationSite(
        application_id=normalized_application_id,
        alias=alias,
        default=_enabled(site.get("default")),
        hosts=hosts,
        target=(
            ApplicationSiteTarget.from_dict(application_spec)
            if isinstance(application_spec, Mapping)
            else None
        ),
        auth_mode=_site_auth_mode(site.get("auth")),
    )


def _host_matches(pattern: str, host: str) -> bool:
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and host != suffix[1:]
    return pattern == host


def _host_patterns_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    left_wildcard = left.startswith("*.")
    right_wildcard = right.startswith("*.")
    if left_wildcard and right_wildcard:
        left_suffix = left[1:]
        right_suffix = right[1:]
        return left_suffix.endswith(right_suffix) or right_suffix.endswith(left_suffix)
    if left_wildcard:
        return _host_matches(left, right)
    if right_wildcard:
        return _host_matches(right, left)
    return False


def _catalog_revision(*, tenant: str, project: str, sites: Sequence[ApplicationSite]) -> str:
    canonical = {
        "schema_version": 1,
        "tenant": tenant,
        "project": project,
        "sites": [site.to_dict() for site in sites],
    }
    encoded = json.dumps(canonical, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compile_application_site_catalog(
    *,
    tenant: str,
    project: str,
    sites: Iterable[ApplicationSite],
    generation: int = 0,
    route_prefix: Any = None,
) -> ApplicationSiteCatalog:
    normalized_tenant = str(tenant or "").strip()
    normalized_project = str(project or "").strip()
    if not normalized_tenant or not normalized_project:
        raise SiteRegistryError("application site catalog requires tenant and project")
    normalized_generation = int(generation)
    if normalized_generation < 0:
        raise SiteRegistryError("application site catalog generation cannot be negative")

    catalog = tuple(sorted(sites, key=lambda item: (item.alias, item.application_id)))
    aliases: dict[str, ApplicationSite] = {}
    host_patterns: dict[str, ApplicationSite] = {}
    defaults: list[ApplicationSite] = []
    for site in catalog:
        if not site.application_id or not _ALIAS_RE.fullmatch(site.alias) or site.alias == "_root":
            raise SiteRegistryError("application site catalog contains an invalid site declaration")
        if site.auth_mode not in _SITE_AUTH_MODES:
            raise SiteRegistryError(
                f"application site {site.alias!r} has unsupported auth mode: {site.auth_mode}"
            )
        if site.alias in aliases:
            raise SiteRegistryError(f"duplicate application site alias: {site.alias}")
        aliases[site.alias] = site
        if site.default:
            defaults.append(site)
        for pattern in site.hosts:
            for existing_pattern, owner in host_patterns.items():
                if (
                    owner.application_id != site.application_id
                    and _host_patterns_overlap(existing_pattern, pattern)
                ):
                    raise SiteRegistryError(
                        "overlapping application site host patterns: "
                        f"{existing_pattern}, {pattern}"
                    )
            host_patterns[pattern] = site

    if len(defaults) > 1:
        raise SiteRegistryError("multiple default application sites are configured")
    if catalog and str(route_prefix or "").strip() == "/":
        raise SiteRegistryError(
            "application-hosted sites cannot be enabled when proxy.route_prefix is '/'; "
            "set proxy.route_prefix to a non-root mount such as '/platform' or disable root sites"
        )

    return ApplicationSiteCatalog(
        tenant=normalized_tenant,
        project=normalized_project,
        generation=normalized_generation,
        revision=_catalog_revision(
            tenant=normalized_tenant,
            project=normalized_project,
            sites=catalog,
        ),
        sites=catalog,
    )


def build_application_site_catalog(
    *,
    tenant: str,
    project: str,
    application_props: Mapping[str, Mapping[str, Any] | None],
    application_specs: Mapping[str, Mapping[str, Any] | None] | None = None,
    route_prefix: Any = None,
) -> ApplicationSiteCatalog:
    sites: list[ApplicationSite] = []
    for application_id in sorted(application_props):
        site = application_site_from_props(
            application_id=application_id,
            props=application_props.get(application_id),
            application_spec=(application_specs or {}).get(application_id),
        )
        if site is not None:
            sites.append(site)
    return compile_application_site_catalog(
        tenant=tenant,
        project=project,
        sites=sites,
        route_prefix=route_prefix,
    )


def resolve_application_site(
    sites: Iterable[ApplicationSite],
    *,
    alias: str = "",
    host: str = "",
) -> Optional[ApplicationSite]:
    catalog = compile_application_site_catalog(
        tenant="_compat",
        project="_compat",
        sites=sites,
    )
    return catalog.resolve(alias=alias, host=host)


__all__ = [
    "ApplicationSite",
    "ApplicationSiteCatalog",
    "ApplicationSiteTarget",
    "SITE_AUTH_PLATFORM_SESSION",
    "SITE_AUTH_PUBLIC",
    "SiteRegistryError",
    "application_site_from_props",
    "build_application_site_catalog",
    "compile_application_site_catalog",
    "resolve_application_site",
]
