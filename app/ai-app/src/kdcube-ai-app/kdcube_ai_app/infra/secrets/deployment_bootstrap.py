# SPDX-License-Identifier: MIT
"""Trusted install/refresh bootstrap for durable Connection Hub signers.

Run in the selected deployment's service image, before application processes
start. Values remain in-process and in the selected secret provider.
"""
from __future__ import annotations

import asyncio
import secrets
from collections.abc import Mapping
from pathlib import Path

import yaml

from kdcube_ai_app.infra.secrets.manager import ISecretsManager, SecretsManagerError

HUB_ID = "connection-hub@1-0"
REFRESH_SIGNER = "oauth_original.refresh_signing_secret"
MANAGEMENT_SIGNER = "connections.delegated_credentials.admission.services.kdcube-management.signing_secret"
GRANTOR_SIGNER = "connections.delegated_credentials.grantor_authority_secret"


def _at(data: object, path: str) -> object:
    for part in path.split("."):
        if not isinstance(data, Mapping):
            return None
        data = data.get(part)
    return data


def _hub(data: object) -> Mapping | None:
    items = _at(data, "bundles.items")
    if not isinstance(items, list):
        return None
    matches = [item for item in items if isinstance(item, Mapping) and item.get("id") == HUB_ID]
    if len(matches) > 1:
        raise SecretsManagerError("Duplicate Connection Hub descriptor")
    return matches[0] if matches else None


def signing_paths(
    config: object, *, service_id: str = "kdcube-management", management_secret_ref: str | None = None,
) -> tuple[str, ...]:
    if not service_id or "." in service_id:
        raise SecretsManagerError("Connection Hub management service id is invalid")
    paths = (
        _at(config, "connections.delegated_credentials.oauth.original_exchange.refresh_signing_secret_ref"),
        _at(config, f"connections.delegated_credentials.admission.services.{service_id}.secret_ref"),
        GRANTOR_SIGNER,
    )
    for path in paths:
        if (not isinstance(path, str) or not path or path != path.strip()
                or any(not part or not all(c.isalnum() or c in "_-" for c in part)
                       for part in path.split("."))):
            raise SecretsManagerError("Connection Hub signing secret reference is invalid or missing")
    if len(set(paths)) != len(paths):
        raise SecretsManagerError("Connection Hub signing secret references must be distinct")
    if management_secret_ref is not None and paths[1] != management_secret_ref:
        raise SecretsManagerError("Management and Connection Hub signing references must match")
    return paths


async def ensure_hub_signers(
    manager: ISecretsManager, *, bundles: object, seed_bundles: object = None,
    service_id: str = "kdcube-management", management_secret_ref: str | None = None,
) -> tuple[str, ...]:
    hub = _hub(bundles)
    if hub is None:
        return ()
    paths = signing_paths(hub.get("config"), service_id=service_id,
                          management_secret_ref=management_secret_ref)
    keys = {path: f"bundles.{HUB_ID}.secrets.{path}" for path in paths}
    # Read the whole set strictly before creating anything. An unavailable
    # provider must never look like an empty deployment.
    current = {path: await manager.get_secret_strict(key) for path, key in keys.items()}
    seed = _hub(seed_bundles)
    seed = seed.get("secrets") if seed else None
    created = []
    for path, key in keys.items():
        if current[path] is not None:
            continue
        value = _at(seed, path)
        if value is None or (isinstance(value, str) and (
            not value.strip() or (value.strip().startswith("<") and value.strip().endswith(">"))
        )):
            value = secrets.token_hex(32)
        if not isinstance(value, str):
            raise SecretsManagerError("Connection Hub signing secret seed is invalid")
        wrote = await manager.create_bundle_secret(key, value)
        stored = await manager.get_secret_strict(key)
        if stored is None or (wrote and not secrets.compare_digest(stored, value)):
            raise SecretsManagerError("Connection Hub signing secret creation was not verified")
        if wrote:
            created.append(key)
    return tuple(created)


def _load(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text()) if path.exists() else {}
    except Exception:
        # YAML exceptions may include source lines containing secret material.
        raise SecretsManagerError("Deployment secret bootstrap descriptor is unreadable") from None


async def _run() -> tuple[str, ...]:
    from kdcube_ai_app.apps.chat.sdk.config import get_settings
    from kdcube_ai_app.apps.chat.sdk.config_scopes import _descriptor_path
    from kdcube_ai_app.apps.chat.proc.rest.management.config import DelegatedManagementConfig
    from kdcube_ai_app.infra.secrets import get_secrets_manager

    bundles = Path(_descriptor_path(env_name="BUNDLES_YAML_DESCRIPTOR_PATH",
                                   filename="bundles.yaml", default="/config/bundles.yaml"))
    seed = Path(_descriptor_path(env_name="BUNDLE_SECRETS_YAML",
                                filename="bundles.secrets.yaml", default="/config/bundles.secrets.yaml"))
    settings = get_settings()
    management = DelegatedManagementConfig.from_settings(settings)
    if management.connection_hub_app_id != HUB_ID:
        raise SecretsManagerError("Management Connection Hub application must match the deployment bootstrap")
    return await ensure_hub_signers(
        get_secrets_manager(settings), bundles=_load(bundles), seed_bundles=_load(seed),
        service_id=management.service_id, management_secret_ref=management.service_secret_ref,
    )


def main() -> int:
    try:
        created = asyncio.run(_run())
    except Exception:
        print("Connection Hub signing secret bootstrap failed; application startup refused.")
        return 1
    for key in created:
        print(f"Created missing signing secret: {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
