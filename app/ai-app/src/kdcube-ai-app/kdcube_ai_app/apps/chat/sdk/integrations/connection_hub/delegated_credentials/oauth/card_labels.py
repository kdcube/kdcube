# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Owner-visible delegated Card labels shared by consent and issuance hosts."""
from __future__ import annotations

from typing import Any, Mapping

_LABEL_SEPARATOR = " · "


def _asserted_client_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    asserted = metadata.get("client_metadata")
    return asserted if isinstance(asserted, Mapping) else {}


def _asserted_agent_id(asserted: Mapping[str, Any]) -> str:
    return str(
        asserted.get("kdcube_agent_id") or asserted.get("kdcube_worker_id") or ""
    ).strip()


def _asserted_worker_identity(asserted: Mapping[str, Any]) -> str:
    """``<provider>:<alias>:<session>`` for a client asserting the full worker shape, else "".

    The shape is what a KDCube host worker registers (``kdcube_worker_id``,
    ``kdcube_worker_alias``, ``kdcube_agent_provider`` and
    ``kdcube_agent_session_id``), for example every Problem Board worker. A
    client asserting only some of these keeps the general naming rule.
    """

    provider = str(asserted.get("kdcube_agent_provider") or "").strip()
    alias = str(asserted.get("kdcube_worker_alias") or "").strip()
    session = str(asserted.get("kdcube_agent_session_id") or "").strip()
    worker_id = str(asserted.get("kdcube_worker_id") or "").strip()
    if provider and alias and session and worker_id:
        return f"{provider}:{alias}:{session}"
    return ""


def _identity_forms(asserted: Mapping[str, Any]) -> frozenset[str]:
    """Every spelling of the asserted agent identity, lowercased for segment comparison."""

    forms = {_asserted_agent_id(asserted)}
    provider = str(asserted.get("kdcube_agent_provider") or "").strip()
    session = str(asserted.get("kdcube_agent_session_id") or "").strip()
    alias = str(asserted.get("kdcube_worker_alias") or "").strip()
    if provider and session:
        forms.add(f"{provider}:{session}")
        if alias:
            forms.add(f"{provider}:{alias}:{session}")
    return frozenset(form.lower() for form in forms if form)


def _label_segments(label: str) -> list[str]:
    """The label's segments with byte-identical repeats removed, order kept."""

    seen: set[str] = set()
    segments: list[str] = []
    for segment in (part.strip() for part in str(label or "").split(_LABEL_SEPARATOR.strip())):
        if segment and segment.lower() not in seen:
            seen.add(segment.lower())
            segments.append(segment)
    return segments


def _registered_name(metadata: Mapping[str, Any]) -> str:
    return str(
        metadata.get("client_name")
        or metadata.get("name")
        or metadata.get("client_uri")
        or ""
    ).strip()


def _door_alias(resource: str) -> str:
    door_path = str(resource or "").split("?", 1)[0].rstrip("*").rstrip("/")
    return door_path.rsplit("/mcp/", 1)[-1].strip("/") if "/mcp/" in door_path else ""


def oauth_card_label(
    client_metadata: Mapping[str, Any] | None,
    *,
    resource: str,
    explicit: str = "",
) -> str:
    """The owner-visible card name: product, entry door and agent identity, each once.

    A client asserting the full KDCube worker shape is named
    ``<client product> · <entry door> · <provider>:<alias>:<session>``, so a
    first connect and a reconnect give the same name whatever its registered
    name spelled. Any other client keeps its registered name and gains the
    entry door and its asserted agent id only when no segment already equals
    them. On 2026-09-21 the alias and plain forms of one worker identity were
    compared as substrings, missed each other, and every worker Card carried
    its identity twice.
    """

    if str(explicit or "").strip():
        return str(explicit).strip()
    metadata = dict(client_metadata or {})
    asserted = _asserted_client_metadata(metadata)
    door = _door_alias(resource)
    segments = _label_segments(_registered_name(metadata))
    forms = _identity_forms(asserted)

    worker_identity = _asserted_worker_identity(asserted)
    if worker_identity:
        product = segments[0] if segments else ""
        if product.lower() in forms or (door and product.lower() == door.lower()):
            product = ""
        return _LABEL_SEPARATOR.join(part for part in (product, door, worker_identity) if part)

    present = {segment.lower() for segment in segments}
    if door and door.lower() not in present:
        segments.append(door)
        present.add(door.lower())
    agent_id = _asserted_agent_id(asserted)
    if agent_id and not (present & forms):
        segments.append(agent_id)
    label = _LABEL_SEPARATOR.join(segments)
    return label or str(metadata.get("client_id") or "Connected client")


def _legacy_card_label(metadata: Mapping[str, Any], door: str) -> str:
    """The name the rule before 2026-09-21 generated for this client at ``door``."""

    label = _registered_name(metadata)
    if door and door.lower() not in label.lower():
        label = f"{label}{_LABEL_SEPARATOR}{door}" if label else door
    agent_id = _asserted_agent_id(_asserted_client_metadata(metadata))
    if agent_id and agent_id.lower() not in label.lower():
        label = f"{label}{_LABEL_SEPARATOR}{agent_id}" if label else agent_id
    return label or str(metadata.get("client_id") or "Connected client")


def _generated_card_labels(metadata: Mapping[str, Any], *, resource: str) -> frozenset[str]:
    """Every name this service could have generated for this client, current or earlier rule.

    A Card may have been named at another entry door, so the earlier rule is
    replayed at the current door, at every door the registered name spells, and
    with no door.
    """

    doors = {_door_alias(resource), ""}
    doors.update(_label_segments(_registered_name(metadata)))
    labels = {_legacy_card_label(metadata, door) for door in doors}
    labels.add(_registered_name(metadata))
    labels.add(oauth_card_label(metadata, resource=resource))
    return frozenset(label for label in labels if label)


def consent_label(
    existing_label: str,
    derived_label: str,
    client_metadata: Mapping[str, Any] | None,
    *,
    resource: str,
) -> str:
    """The name the consent page proposes: the existing Card's, unless this service generated it.

    A reconnect edits an existing Card, and a name a person chose is theirs to
    keep. Only an exact match with a name this service generates, under the
    current rule or the earlier one, is replaced by the current derivation.
    """

    existing = str(existing_label or "").strip()
    if not existing:
        return derived_label
    if existing in _generated_card_labels(dict(client_metadata or {}), resource=resource):
        return derived_label
    return existing


__all__ = ["oauth_card_label", "consent_label"]

