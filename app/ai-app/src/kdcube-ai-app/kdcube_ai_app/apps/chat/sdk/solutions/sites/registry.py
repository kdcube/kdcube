from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Optional


_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class SiteRegistryError(ValueError):
    """Raised when enabled application-site declarations are ambiguous."""


@dataclass(frozen=True)
class ApplicationSite:
    application_id: str
    alias: str
    default: bool
    hosts: tuple[str, ...]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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
) -> Optional[ApplicationSite]:
    normalized_application_id = str(application_id or "").strip()
    if not normalized_application_id:
        raise SiteRegistryError("application site requires an application id")

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
    )


def _host_matches(pattern: str, host: str) -> bool:
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and host != suffix[1:]
    return pattern == host


def resolve_application_site(
    sites: Iterable[ApplicationSite],
    *,
    alias: str = "",
    host: str = "",
) -> Optional[ApplicationSite]:
    catalog = tuple(sites)
    aliases: dict[str, ApplicationSite] = {}
    for site in catalog:
        if site.alias in aliases:
            raise SiteRegistryError(f"duplicate application site alias: {site.alias}")
        aliases[site.alias] = site

    requested_alias = str(alias or "").strip().lower()
    if requested_alias:
        return aliases.get(requested_alias)

    normalized_host = _normalize_host(host)
    host_matches = [
        site
        for site in catalog
        if normalized_host and any(_host_matches(pattern, normalized_host) for pattern in site.hosts)
    ]
    if len(host_matches) > 1:
        raise SiteRegistryError(f"multiple application sites match host: {normalized_host}")
    if host_matches:
        return host_matches[0]

    defaults = [site for site in catalog if site.default]
    if len(defaults) > 1:
        raise SiteRegistryError("multiple default application sites are configured")
    return defaults[0] if defaults else None


__all__ = [
    "ApplicationSite",
    "SiteRegistryError",
    "application_site_from_props",
    "resolve_application_site",
]
