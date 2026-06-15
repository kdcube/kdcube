from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote, urlencode


def bundle_operation_url(
    *,
    tenant: str | None,
    project: str | None,
    bundle_id: str | None,
    operation: str | None,
    route: str = "operations",
    query: Mapping[str, Any] | None = None,
    base_url: str = "",
    strict: bool = False,
) -> str:
    """Build a browser-callable KDCube bundle operation URL.

    The returned path is relative unless `base_url` is provided. Missing route
    parts return an empty string by default so optional download affordances can
    be omitted gracefully; use `strict=True` for provider paths where URL
    construction is required.
    """

    tenant_value = str(tenant or "").strip()
    project_value = str(project or "").strip()
    bundle_value = str(bundle_id or "").strip()
    operation_value = str(operation or "").strip()
    route_value = str(route or "operations").strip().strip("/") or "operations"
    missing = [
        name
        for name, value in (
            ("tenant", tenant_value),
            ("project", project_value),
            ("bundle_id", bundle_value),
            ("operation", operation_value),
        )
        if not value
    ]
    if missing:
        if strict:
            raise ValueError(f"Cannot build bundle operation URL; missing {', '.join(missing)}")
        return ""

    path_parts = [
        "api",
        "integrations",
        "bundles",
        tenant_value,
        project_value,
        bundle_value,
        *[part for part in route_value.split("/") if part],
        operation_value,
    ]
    path = "/" + "/".join(quote(part, safe="") for part in path_parts)
    query_string = urlencode(
        {
            str(key): value
            for key, value in dict(query or {}).items()
            if value not in (None, "")
        },
        doseq=True,
    )
    if query_string:
        path = f"{path}?{query_string}"
    if base_url:
        return f"{str(base_url).rstrip('/')}{path}"
    return path


__all__ = ["bundle_operation_url"]
