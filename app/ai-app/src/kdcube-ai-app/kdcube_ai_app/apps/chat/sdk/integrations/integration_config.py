from __future__ import annotations

import re
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.config import get_secret


_RESERVED_KEYS = {
    "id",
    "integration_id",
    "integrationId",
    "provider",
    "where",
    "enabled",
    "definition",
    "secret_ref",
    "secret_refs",
}


def _str(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def stable_secret_key(value: str) -> str:
    """Return a dot-path-safe key segment for descriptor secret refs."""

    key = re.sub(r"[^A-Za-z0-9_]+", "_", _str(value)).strip("_")
    return key or "default"


def entrypoint_bundle_id(entrypoint: Any, default: str = "") -> str:
    spec = getattr(getattr(entrypoint, "config", None), "ai_bundle_spec", None)
    return (
        _str(getattr(spec, "id", None))
        or _str(getattr(spec, "bundle_id", None))
        or _str(getattr(entrypoint, "bundle_id", None))
        or default
    )


def _bundle_prop(entrypoint: Any, path: str, default: Any = None) -> Any:
    fn = getattr(entrypoint, "bundle_prop", None)
    return fn(path, default) if callable(fn) else default


def _normalize_row(
    row: Mapping[str, Any],
    *,
    integration_id: str = "",
    provider: str = "",
    source: str = "config",
) -> dict[str, Any]:
    definition = _mapping(row.get("definition"))
    if not definition:
        definition = {key: value for key, value in dict(row).items() if key not in _RESERVED_KEYS}
    item_id = (
        _str(integration_id)
        or _str(row.get("id"))
        or _str(row.get("integration_id"))
        or _str(row.get("integrationId"))
    )
    if not item_id:
        return {}
    inferred_provider = item_id.split(".", 1)[0] if "." in item_id else item_id
    provider_value = _str(row.get("provider") or provider or inferred_provider).lower()
    if not provider_value:
        provider_value = "integration"
    return {
        **dict(row),
        "id": item_id,
        "integration_id": item_id,
        "provider": provider_value,
        "where": _str(row.get("where")) or "built-in",
        "enabled": row.get("enabled") is not False,
        "definition": definition,
        "secret_ref": _str(row.get("secret_ref")),
        "secret_refs": _mapping(row.get("secret_refs")),
        "source": _str(row.get("source") or source) or source,
    }


def configured_integrations(entrypoint: Any, *, provider: str = "") -> list[dict[str, Any]]:
    """Return normalized app integration descriptors.

    Canonical config shape is a map keyed by integration id:

      integrations:
        telegram.kdcube_ref:
          provider: telegram
          where: built-in|connection-hub
          enabled: true
          definition: {...}

    App code selects a row by integration_id because that id is the reason the
    app needs the integration for a specific surface or operation.
    """

    raw = _bundle_prop(entrypoint, "integrations", None)
    rows: list[dict[str, Any]] = []
    if isinstance(raw, Mapping):
        for key, item in raw.items():
            if isinstance(item, Mapping):
                row = _normalize_row(item, integration_id=_str(key))
                if row:
                    rows.append(row)

    provider_value = _str(provider).lower()
    if provider_value:
        rows = [row for row in rows if _str(row.get("provider")).lower() == provider_value]

    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = _str(row.get("id"))
        if not row_id:
            continue
        deduped[row_id] = row
    return list(deduped.values())


def select_integration(
    entrypoint: Any,
    *,
    provider: str,
    integration_id: str = "",
) -> dict[str, Any]:
    rows = [row for row in configured_integrations(entrypoint, provider=provider) if row.get("enabled") is not False]
    wanted_id = _str(integration_id)
    if wanted_id:
        for row in rows:
            if _str(row.get("id")) == wanted_id:
                return row
        return {}

    if len(rows) == 1:
        return rows[0]
    return {}


def integration_definition_value(
    entrypoint: Any,
    *,
    provider: str,
    key: str,
    default: Any = None,
    integration_id: str = "",
) -> Any:
    row = select_integration(entrypoint, provider=provider, integration_id=integration_id)
    definition = _mapping(row.get("definition"))
    if key in definition:
        return definition.get(key)
    if "." in key:
        cur: Any = definition
        for part in key.split("."):
            if not isinstance(cur, Mapping) or part not in cur:
                cur = None
                break
            cur = cur[part]
        if cur is not None:
            return cur
    if provider == "telegram":
        webhook = _mapping(definition.get("webhook"))
        if key in webhook:
            return webhook.get(key)
    return default


def integration_secret_ref_candidates(
    entrypoint: Any,
    *,
    provider: str,
    field: str,
    integration_id: str = "",
) -> list[str]:
    row = select_integration(entrypoint, provider=provider, integration_id=integration_id)
    row_id = _str(row.get("id")) or f"{provider}.default"
    definition = _mapping(row.get("definition"))
    refs = _mapping(row.get("secret_refs"))

    out: list[str] = []
    for candidate in (
        refs.get(field),
        definition.get(f"{field}_secret_ref"),
        row.get(f"{field}_secret_ref"),
    ):
        value = _str(candidate)
        if value:
            out.append(value)

    if field == "bot_token" and _str(row.get("secret_ref")):
        out.append(_str(row.get("secret_ref")))

    seen: set[str] = set()
    deduped: list[str] = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


async def integration_secret_value(
    entrypoint: Any,
    *,
    provider: str,
    field: str,
    integration_id: str = "",
) -> str:
    bundle_id = entrypoint_bundle_id(entrypoint)
    for ref in integration_secret_ref_candidates(
        entrypoint,
        provider=provider,
        field=field,
        integration_id=integration_id,
    ):
        if ref.startswith("bundles."):
            value = await get_secret(ref)
        elif ref.startswith("b:"):
            value = await get_secret(ref, bundle_id=bundle_id)
        else:
            value = await get_secret(f"b:{ref}", bundle_id=bundle_id)
            if not value and bundle_id:
                value = await get_secret(f"bundles.{bundle_id}.secrets.{ref}")
        if value:
            return _str(value)
    return ""
