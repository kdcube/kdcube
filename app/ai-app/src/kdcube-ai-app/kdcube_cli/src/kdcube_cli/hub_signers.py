# SPDX-License-Identifier: MIT
"""Prepare non-secret Connection Hub signer references for local bootstrap."""
from __future__ import annotations

from pathlib import Path
import re

import yaml

from kdcube_cli.descriptor_files import write_descriptor_text

HUB_ID = "connection-hub@1-0"
SIGNER_REFS = {
    "connections.delegated_credentials.oauth.original_exchange.refresh_signing_secret_ref":
        "oauth_original.refresh_signing_secret",
    "connections.delegated_credentials.admission.services.kdcube-management.secret_ref":
        "connections.delegated_credentials.admission.services.kdcube-management.signing_secret",
}
_PUBLIC_BOOTSTRAP_REASONS = frozenset({
    "duplicate_hub_descriptor", "invalid_management_service_id", "invalid_or_missing_signing_reference",
    "signing_references_must_be_distinct", "management_references_must_match", "provider_read_unavailable",
    "invalid_signing_seed_length", "provider_write_lease_lost", "provider_create_unavailable",
    "signing_secret_not_verified", "descriptor_unreadable", "management_hub_application_mismatch",
    "deployment_configuration_unavailable",
})


def public_bootstrap_failure(output: str) -> str:
    """Accept only the SDK's finite reason and a validated bundle key name."""
    for line in reversed(output.splitlines()):
        match = re.fullmatch(
            r"Connection Hub signing secret bootstrap refused: ([a-z_]+)"
            r"(?: \[(bundles\.connection-hub@1-0\.secrets\.[A-Za-z0-9_.-]+)\])?", line,
        )
        if match and match[1] in _PUBLIC_BOOTSTRAP_REASONS:
            return f" Reason: {match[1]}." + (f" Key: {match[2]}." if match[2] else "")
    return ""


def public_bootstrap_created_lines(output: str) -> tuple[str, ...]:
    """Select the bootstrap's name-only creation receipts from captured output."""
    return tuple(
        line for line in output.splitlines()
        if re.fullmatch(
            r"Created missing signing secret: bundles\.connection-hub@1-0\.secrets\."
            r"[\w-]+(?:\.[\w-]+)*", line,
        )
    )


def hub_signer_bootstrap_command(base_command: list[str], *, build: bool) -> list[str]:
    return [
        *base_command, "run", "--rm", "-T", *(["--build"] if build else []),
        "chat-proc", "python", "-m", "kdcube_ai_app.infra.secrets.deployment_bootstrap",
    ]


def _child(node, key, *, require_block=True):
    if not isinstance(node, yaml.MappingNode) or (require_block and node.flow_style):
        raise ValueError("Add the missing Connection Hub signing references using block mappings")
    matches = [value for name, value in node.value if name.value == key]
    if len(matches) > 1:
        raise ValueError("Duplicate Connection Hub configuration key")
    return matches[0] if matches else None


def _content_end(node):
    # Container end marks can skip comments before the next sibling. Anchor
    # insertion to the last actual value instead, before ownership markers or
    # comments belonging to another application.
    if not getattr(node, "flow_style", False):
        if isinstance(node, yaml.MappingNode) and node.value:
            return _content_end(node.value[-1][1])
        if isinstance(node, yaml.SequenceNode) and node.value:
            return _content_end(node.value[-1])
    return node.end_mark


def _insert_reference(text: str, dotted: str, default: str) -> str:
    # YAML node marks locate an insertion; the original bytes are never
    # serialized again. Alias/flow/null forms refuse rather than modifying a
    # shared node or replacing operator-owned syntax.
    if any(isinstance(token, yaml.AliasToken) for token in yaml.scan(text)):
        raise ValueError("Add the missing Connection Hub signing references explicitly when using YAML aliases")
    root = yaml.compose(text)
    items = _child(_child(root, "bundles"), "items")
    if not isinstance(items, yaml.SequenceNode) or items.flow_style:
        raise ValueError("Add the missing Connection Hub signing references using block mappings")
    hubs = [node for node in items.value if getattr(_child(node, "id", require_block=False), "value", None) == HUB_ID]
    if len(hubs) != 1:
        raise ValueError("Connection Hub descriptor is ambiguous")
    node = hubs[0]
    parts = ["config", *dotted.split(".")]
    for index, part in enumerate(parts):
        child = _child(node, part)
        if child is None:
            addition = default
            for key in reversed(parts[index:]):
                addition = {key: addition}
            indent = node.value[0][0].start_mark.column
            newline = "\r\n" if "\r\n" in text else "\n"
            inserted = "".join(
                " " * indent + line + newline
                for line in yaml.safe_dump(addition, sort_keys=False).splitlines()
            )
            lines = text.splitlines(keepends=True)
            end = _content_end(node)
            offset = sum(map(len, lines[:end.line + (1 if end.column else 0)]))
            if offset and not text[:offset].endswith(("\n", "\r")):
                inserted = newline + inserted
            return text[:offset] + inserted + text[offset:]
        node = child
    raise ValueError("Connection Hub signing reference is already configured")


def prepare_hub_signing_refs(config_dir: Path) -> bool:
    """Fill omitted reference properties; keep every configured reference.

    Return whether this deployment contains Connection Hub. No secret values
    are generated or written in this host-side preparation.
    """
    path = config_dir / "bundles.yaml"
    if not path.exists():
        return False
    try:
        text = path.read_bytes().decode("utf-8")
        data = yaml.safe_load(text)
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
    missing = []
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
            missing.append((dotted, default))
    if changed:
        for dotted, default in missing:
            text = _insert_reference(text, dotted, default)
        if yaml.safe_load(text) != data:
            raise ValueError("Connection Hub signing reference insertion could not be verified")
        write_descriptor_text(path, text)
    return True
