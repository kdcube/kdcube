# SPDX-License-Identifier: MIT
"""Prepare non-secret Connection Hub signer references for local bootstrap."""
from __future__ import annotations

from pathlib import Path

import yaml

from kdcube_cli.descriptor_files import write_descriptor_text

HUB_ID = "connection-hub@1-0"
SIGNER_REFS = {
    "connections.delegated_credentials.oauth.original_exchange.refresh_signing_secret_ref":
        "oauth_original.refresh_signing_secret",
    "connections.delegated_credentials.admission.services.kdcube-management.secret_ref":
        "connections.delegated_credentials.admission.services.kdcube-management.signing_secret",
}


def hub_signer_bootstrap_command(base_command: list[str], *, build: bool) -> list[str]:
    return [
        *base_command, "run", "--rm", "-T", *(["--build"] if build else []),
        "chat-proc", "python", "-m", "kdcube_ai_app.infra.secrets.deployment_bootstrap",
    ]


def prepare_hub_signing_refs(config_dir: Path) -> bool:
    """Fill omitted reference properties; keep every configured reference.

    Return whether this deployment contains Connection Hub. No secret values
    are generated or written in this host-side preparation.
    """
    path = config_dir / "bundles.yaml"
    if not path.exists():
        return False
    try:
        data = yaml.safe_load(path.read_text())
    except Exception:
        raise ValueError("Connection Hub descriptor is unreadable") from None
    if not isinstance(data, dict):
        raise ValueError("Bundle descriptor must be a mapping")
    bundles = data.get("bundles") or {}
    items = bundles.get("items") if isinstance(bundles, dict) else None
    if not isinstance(items, list):
        raise ValueError("Bundle descriptor items must be a list")
    hubs = [item for item in items if isinstance(item, dict) and item.get("id") == HUB_ID]
    if not hubs:
        return False
    if len(hubs) != 1:
        raise ValueError("Duplicate Connection Hub descriptor")
    changed = False
    if hubs[0].get("config") is None:
        hubs[0]["config"] = {}
        changed = True
    config = hubs[0]["config"]
    for dotted, default in SIGNER_REFS.items():
        node = config
        parts = dotted.split(".")
        for part in parts[:-1]:
            if not isinstance(node, dict):
                raise ValueError("Connection Hub signing reference configuration must be a mapping")
            if node.get(part) is None:
                node[part] = {}
                changed = True
            node = node[part]
        if not isinstance(node, dict):
            raise ValueError("Connection Hub signing reference configuration must be a mapping")
        if node.get(parts[-1]) is None:
            node[parts[-1]] = default
            changed = True
    if changed:
        write_descriptor_text(path, yaml.safe_dump(data, sort_keys=False))
    return True
