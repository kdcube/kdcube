# SPDX-License-Identifier: MIT
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import quote, urlsplit, urlunsplit

import yaml

from kdcube_cli.control.errors import InvalidDescriptorError
from kdcube_cli.control.models import (
    ApplicationRef,
    ApplicationStatus,
    ApplicationSurface,
    DeploymentTargetRef,
    SurfaceKind,
)


def normalize_endpoint(endpoint: str) -> str:
    raw = str(endpoint or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint must be an absolute http or https URL")
    if parsed.query or parsed.fragment:
        raise ValueError("endpoint must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _segment(value: str) -> str:
    return quote(str(value), safe="@._~-")


def application_surface_route(
    target: DeploymentTargetRef,
    application: ApplicationRef,
    kind: SurfaceKind,
    alias: str,
) -> str:
    if not target.tenant or not target.project:
        raise ValueError("target tenant and project are required for application routes")
    tenant = _segment(target.tenant)
    project = _segment(target.project)
    bundle_id = _segment(application.bundle_id)
    surface_alias = _segment(alias)
    bundle_root = f"/api/integrations/bundles/{tenant}/{project}/{bundle_id}"
    if kind == SurfaceKind.WIDGET:
        return f"{bundle_root}/public/widgets/{surface_alias}"
    if kind == SurfaceKind.MCP:
        return f"{bundle_root}/public/mcp/{surface_alias}"
    if kind == SurfaceKind.OPERATION:
        return f"{bundle_root}/operations/{surface_alias}"
    if kind == SurfaceKind.MAIN_UI:
        return f"/api/integrations/static/{tenant}/{project}/{bundle_id}"
    raise ValueError(f"unsupported surface kind: {kind}")


def build_application_surface(
    target: DeploymentTargetRef,
    application: ApplicationRef,
    kind: SurfaceKind,
    alias: str,
    *,
    endpoint: str,
    declared: bool,
) -> ApplicationSurface:
    route = application_surface_route(target, application, kind, alias)
    base = normalize_endpoint(endpoint)
    return ApplicationSurface(
        application=application,
        kind=kind,
        alias=alias,
        route=route,
        url=f"{base}{route}",
        declared=declared,
        openable=kind in {SurfaceKind.WIDGET, SurfaceKind.MAIN_UI},
    )


def bundle_items_from_descriptor(data: object) -> Tuple[List[Dict[str, object]], Optional[str]]:
    if not isinstance(data, dict):
        return [], None
    # Both shapes have been accepted by the executable's info path: the current
    # wrapped descriptor and the earlier unwrapped catalog.
    raw_bundles = data.get("bundles") if "bundles" in data else data
    if isinstance(raw_bundles, dict):
        default_id = str(raw_bundles.get("default_bundle_id") or "").strip() or None
        raw_items = raw_bundles.get("items")
        if isinstance(raw_items, list):
            return [dict(item) for item in raw_items if isinstance(item, dict)], default_id
        items: List[Dict[str, object]] = []
        for key, value in raw_bundles.items():
            if key in {"items", "version", "default_bundle_id"} or not isinstance(value, dict):
                continue
            item = dict(value)
            item.setdefault("id", str(key))
            items.append(item)
        return items, default_id
    if isinstance(raw_bundles, list):
        return [dict(item) for item in raw_bundles if isinstance(item, dict)], None
    return [], None


def load_descriptor(path: Path) -> Mapping[str, object]:
    descriptor = Path(path).expanduser().resolve()
    if not descriptor.exists():
        raise InvalidDescriptorError(str(descriptor), "file does not exist")
    try:
        data = yaml.safe_load(descriptor.read_text())
    except Exception as exc:
        raise InvalidDescriptorError(str(descriptor), "YAML could not be parsed") from exc
    if not isinstance(data, dict):
        raise InvalidDescriptorError(str(descriptor), "top level must be a mapping")
    return data


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _enabled(value: object) -> bool:
    if isinstance(value, dict) and value.get("enabled") is False:
        return False
    return value is not False


def _provider_aliases(config: Mapping[str, object]) -> Iterable[Tuple[SurfaceKind, str]]:
    surfaces = _mapping(config.get("surfaces"))
    provider = _mapping(surfaces.get("as_provider"))
    for key, kind in (
        ("widget", SurfaceKind.WIDGET),
        ("mcp", SurfaceKind.MCP),
        ("api", SurfaceKind.OPERATION),
        ("operation", SurfaceKind.OPERATION),
    ):
        aliases = _mapping(provider.get(key))
        for alias, spec in aliases.items():
            if str(alias).strip() and _enabled(spec):
                yield kind, str(alias).strip()


def _ui_aliases(config: Mapping[str, object]) -> Iterable[Tuple[SurfaceKind, str]]:
    ui = _mapping(config.get("ui"))
    widgets = _mapping(ui.get("widgets"))
    for alias, spec in widgets.items():
        if str(alias).strip() and _enabled(spec):
            yield SurfaceKind.WIDGET, str(alias).strip()

    main_view = _mapping(ui.get("main_view"))
    if main_view and _enabled(main_view):
        site = _mapping(main_view.get("site"))
        alias = str(site.get("alias") or "main").strip() or "main"
        yield SurfaceKind.MAIN_UI, alias


def application_from_bundle_entry(
    target: DeploymentTargetRef,
    entry: Mapping[str, object],
    *,
    endpoint: str,
) -> Optional[ApplicationStatus]:
    bundle_id = str(entry.get("id") or "").strip()
    if not bundle_id:
        return None
    reference = ApplicationRef(bundle_id=bundle_id)
    config = _mapping(entry.get("config"))
    surfaces: List[ApplicationSurface] = []
    seen = set()
    for kind, alias in list(_provider_aliases(config)) + list(_ui_aliases(config)):
        key = (kind.value, alias)
        if key in seen:
            continue
        seen.add(key)
        surfaces.append(
            build_application_surface(
                target,
                reference,
                kind,
                alias,
                endpoint=endpoint,
                declared=True,
            )
        )

    source_ref = str(entry.get("ref") or "").strip() or None
    return ApplicationStatus(
        reference=reference,
        name=str(entry.get("name") or "").strip() or None,
        installed=True,
        enabled=entry.get("enabled") is not False,
        surfaces=tuple(surfaces),
        source_ref=source_ref,
    )


def application_inventory(
    descriptor: Mapping[str, object],
    target: DeploymentTargetRef,
    *,
    endpoint: str,
) -> Tuple[Tuple[ApplicationStatus, ...], Optional[str]]:
    entries, default_id = bundle_items_from_descriptor(descriptor)
    applications = []
    for entry in entries:
        status = application_from_bundle_entry(target, entry, endpoint=endpoint)
        if status is not None:
            applications.append(status)
    return tuple(applications), default_id
