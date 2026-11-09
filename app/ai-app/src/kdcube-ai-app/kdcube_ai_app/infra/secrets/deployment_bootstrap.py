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

from kdcube_ai_app.infra.secrets.manager import (
    ISecretsManager, SecretsManagerError, SecretsManagerLeaseLost,
)

HUB_ID = "connection-hub@1-0"
REFRESH_SIGNER = "oauth_original.refresh_signing_secret"
MANAGEMENT_SIGNER = "connections.delegated_credentials.admission.services.kdcube-management.signing_secret"
GRANTOR_SIGNER = "connections.delegated_credentials.grantor_authority_secret"
_PUBLIC_REASONS = frozenset({
    "duplicate_hub_descriptor", "invalid_management_service_id", "invalid_or_missing_signing_reference",
    "signing_references_must_be_distinct", "management_references_must_match", "provider_read_unavailable",
    "invalid_signing_seed_length", "provider_write_lease_lost", "provider_create_unavailable",
    "signing_secret_not_verified", "descriptor_unreadable", "management_hub_application_mismatch",
    "deployment_configuration_unavailable",
})


class DeploymentBootstrapError(SecretsManagerError):
    """Finite public reason, never a raw provider exception."""

    def __init__(self, code: str, key: str = ""):
        if code not in _PUBLIC_REASONS:
            code, key = "deployment_configuration_unavailable", ""
        self.code, self.key = code, key
        super().__init__(code)


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
        raise DeploymentBootstrapError("duplicate_hub_descriptor")
    return matches[0] if matches else None


def signing_paths(
    config: object, *, service_id: str = "kdcube-management", management_secret_ref: str | None = None,
) -> tuple[str, ...]:
    if not service_id or "." in service_id:
        raise DeploymentBootstrapError("invalid_management_service_id")
    paths = (
        _at(config, "connections.delegated_credentials.oauth.original_exchange.refresh_signing_secret_ref"),
        _at(config, f"connections.delegated_credentials.admission.services.{service_id}.secret_ref"),
        GRANTOR_SIGNER,
    )
    for path in paths:
        if (not isinstance(path, str) or not path or path != path.strip()
                or any(not part or not all(c.isalnum() or c in "_-" for c in part)
                       for part in path.split("."))):
            raise DeploymentBootstrapError("invalid_or_missing_signing_reference")
    if len(set(paths)) != len(paths):
        raise DeploymentBootstrapError("signing_references_must_be_distinct")
    if management_secret_ref is not None and paths[1] != management_secret_ref:
        raise DeploymentBootstrapError("management_references_must_match")
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
    try:
        current = {path: await manager.get_secret_strict(key) for path, key in keys.items()}
    except Exception:
        raise DeploymentBootstrapError("provider_read_unavailable") from None
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
        try:
            size = len(value.encode("utf-8")) if isinstance(value, str) else 0
        except UnicodeError:
            size = 0
        if size < 32 or (path == paths[0] and size > 4096):
            raise DeploymentBootstrapError("invalid_signing_seed_length", key)
        try:
            wrote = await manager.create_bundle_secret(key, value)
            stored = await manager.get_secret_strict(key)
        except SecretsManagerLeaseLost:
            raise DeploymentBootstrapError("provider_write_lease_lost", key) from None
        except Exception:
            raise DeploymentBootstrapError("provider_create_unavailable", key) from None
        try:
            verified = stored is not None and (
                not wrote or secrets.compare_digest(stored.encode("utf-8"), value.encode("utf-8"))
            )
        except (AttributeError, UnicodeError):
            verified = False
        if not verified:
            raise DeploymentBootstrapError("signing_secret_not_verified", key)
        if wrote:
            created.append(key)
    return tuple(created)


def _load(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text()) if path.exists() else {}
    except Exception:
        # YAML exceptions may include source lines containing secret material.
        raise DeploymentBootstrapError("descriptor_unreadable") from None


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
        raise DeploymentBootstrapError("management_hub_application_mismatch")
    return await ensure_hub_signers(
        get_secrets_manager(settings), bundles=_load(bundles), seed_bundles=_load(seed),
        service_id=management.service_id, management_secret_ref=management.service_secret_ref,
    )


def main() -> int:
    try:
        created = asyncio.run(_run())
    except DeploymentBootstrapError as exc:
        key = f" [{exc.key}]" if exc.key else ""
        print(f"Connection Hub signing secret bootstrap refused: {exc.code}{key}")
        return 1
    except Exception:
        print("Connection Hub signing secret bootstrap refused: deployment_configuration_unavailable")
        return 1
    for key in created:
        print(f"Created missing signing secret: {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
